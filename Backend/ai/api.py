"""API blueprint for the AI Learning Hub.

All /api/ai/* endpoints require an authenticated session and authorize against
the logged-in user, so users can never read or write another user's
conversations or files. Responses are JSON; message-send supports Server-Sent
Events for streaming.
"""

import json
import os
import sys
import time
import uuid

from flask import Blueprint, Response, current_app, jsonify, request, session

# Make the Backend package importable so `db` (at the Backend root) resolves.
if os.path.dirname(os.path.dirname(os.path.abspath(__file__))) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db

# The durable file store at the Backend root. Aliased because ``.storage``
# below is the AI Hub's conversation store, and both are needed here.
import storage as file_store

from ai_gateway import errors as gw_errors
from ai_gateway import gateway as gw
from ai_gateway import registry as gw_registry

from . import files as file_processor
from . import limits
from . import models as model_registry
from . import storage
from . import service
from .providers import get_provider

ai_api = Blueprint("ai_api", __name__, url_prefix="/api/ai")

#: Providers the "connect your own account" flow accepts, keyed to the
#: registry's provider ids (see ai/models.py) plus the display label used in UI.
CONNECTABLE_PROVIDERS = {
    "anthropic": "Claude",
    "openai": "ChatGPT",
    "google": "Gemini",
    "deepseek": "DeepSeek",
    "copilot": "Copilot",
}

#: Server-level env key -> provider id used for the /meta serverKeys list.
#: A provider may list several env names (aliases); any one set enables it.
_SERVER_KEY_ENVS = (
    ("openai", ("OPENAI_API_KEY",)),
    ("anthropic", ("ANTHROPIC_API_KEY",)),
    ("google", ("GOOGLE_AI_API_KEY", "GEMINI_API_KEY")),
    ("deepseek", ("DEEPSEEK_API_KEY",)),
    ("copilot", ("COPILOT_GITHUB_TOKEN", "GH_TOKEN")),
)

#: Legacy AI Hub model ids -> gateway composite keys, for requests that still
#: arrive with the old names (Phase 5 bridge). New clients send the composite
#: key ("openai/gpt-5-mini") or "auto", which the picker already uses.
_LEGACY_MODEL_KEYS = {
    "claude": "anthropic/claude-sonnet-4-5",
    "gpt": "openai/gpt-5-mini",
    "gemini": "google/gemini-3.8-flash",
    "deepseek": "deepseek/deepseek-v4-flash",
    "copilot": "copilot/gpt-5-mini",
}

#: AI Hub modes -> gateway ranking feature used to order and pick a model.
_FEATURE_BY_MODE = {
    "quiz": "quiz",
    "flashcards": "cards",
    "explain": "explain",
    "summarize": "summarize",
    "simplify": "simplify",
    "solve": "solve",
}


def _gateway_feature(mode):
    """Map an AI Hub mode to a gateway ranking feature (default: chat)."""
    return _FEATURE_BY_MODE.get(mode, "chat")


def _is_valid_gateway_key(value):
    """True when `value` is a registry-known composite key ("openai/gpt-5-mini")."""
    return isinstance(value, str) and "/" in value and bool(gw_registry.get_model_by_key(value))


def _normalize_model_choice(model):
    """Legacy id, gateway composite key, or none -> the stored conversation label."""
    value = (model or "").strip()
    if not value or value in ("auto", ""):
        return "auto"
    if model_registry.get_model(value) or _is_valid_gateway_key(value):
        return value
    return "claude"


def _soft_model_key(user_id, requested):
    """Turn an AI Hub model field into a *soft* gateway preference (or None).

    Accepts a Phase 4 composite key ("openai/gpt-5-mini") or a legacy id
    ("claude"); empty/"auto" falls through to the account preference. The
    requested model is honored only while it is usable, otherwise the gateway
    auto-selects — the same behavior students see in the picker.
    """
    pick = (requested or "").strip()
    if not pick or pick == "auto":
        return None
    key = pick if "/" in pick else _LEGACY_MODEL_KEYS.get(pick)
    if not key:
        return None
    model = gw_registry.get_model_by_key(key)
    if model is None or not gw._model_usable(user_id, model):
        return None
    return key


def _persisted_model(resolved_model, requested_model):
    """Gateway model row -> storage-safe model key (requested key as fallback)."""
    if isinstance(resolved_model, dict) and resolved_model.get("provider_slug") and resolved_model.get("model_id"):
        return gw_registry.model_key(resolved_model["provider_slug"], resolved_model["model_id"])
    return requested_model or ""


def _server_env_set(envs):
    """True when any of the given server env names holds a value."""
    return any(os.getenv(env) for env in envs)

def _require_user_id():
    uid = session.get("user_id")
    if not uid:
        return None
    return uid


def _user_subjects_titles():
    # Light helper: none strictly needed here; context builder reads the user.
    return None


def _load_users():
    return db.load_users()


def _sample_usage():
    uid = session.get("user_id")
    if not uid:
        return (0, limits.MAX_REQUESTS_PER_DAY)
    return limits.remaining_requests(uid)


def _write_sse(events):
    """Wrap a list/iterable as a text/event-stream Flask response."""
    def gen():
        for event in events:
            yield f"data: {json.dumps(event)}\n\n"
        yield "data: [DONE]\n\n"
    return Response(gen(), mimetype="text/event-stream")


def _event_stream_from(callback):
    """Return an SSE Generator that runs callback(emit) for streaming."""
    def emit(d):
        return f"data: {json.dumps(d)}\n\n"

    def gen():
        done_text = []

        def on_chunk(text):
            done_text.append(text)
            yield emit({"type": "chunk", "text": text})

        def on_done(payload):
            yield emit({"type": "done", **payload})

        try:
            for chunk in callback():
                yield emit({"type": "chunk", "text": chunk})
                # (accumulate handled in closure below via callback's own store)
        except Exception as exc:  # noqa: BLE001
            yield emit({"type": "error", "message": service.handle_error(exc)})
        yield emit({"type": "done"} if not done_text else {"type": "done", "note": "streamed"})
        yield "data: [DONE]\n\n"

    return Response(gen(), mimetype="text/event-stream")


def _material_text(material_id, user_id):
    """Return stored material text (and user's own upload) or None.

    Read through the durable store so a material uploaded before a redeploy is
    still usable afterwards. Keys are namespaced by ``user_id``, so one student
    cannot read another's material even by guessing an id.
    """
    if not material_id:
        return None
    safe = os.path.basename(str(material_id))
    if not safe:
        return None
    data = file_store.load(file_store.ai_material_key(user_id, safe))
    if data is None:
        return None
    return data.decode("utf-8", errors="replace")


def _server_provider_keys_available():
    """True when any real provider key is configured at the server level."""
    return any(_server_env_set(env) for _pid, env in _SERVER_KEY_ENVS)


def _verify_provider_key(provider, api_key):
    """Ask the provider adapter to validate a candidate key.

    Module-level so hermetic tests can monkeypatch it instead of hitting the
    network. Returns (ok: bool, note: str).
    """
    try:
        provider_obj = get_provider(provider, api_key=api_key)
    except Exception as exc:  # noqa: BLE001 - adapter construction errors surface as-is
        return False, f"Could not reach {provider}: {exc}"
    try:
        return provider_obj.verify(api_key=api_key)
    except Exception as exc:  # noqa: BLE001
        return False, f"Could not reach {provider}: {exc}"


@ai_api.before_request
def _auth():
    if not session.get("user_id"):
        return jsonify({"error": "unauthorized"}), 401


# --------------------------------------------------------------- meta

@ai_api.get("/meta")
def meta():
    uid = session["user_id"]
    users = _load_users()
    user = users.get(uid, {})
    used, limit = _sample_usage()
    connections = db.get_ai_connections(uid)
    connected_providers = {c["provider"] for c in connections}
    server_keys = [pid for pid, env in _SERVER_KEY_ENVS if _server_env_set(env)]
    mock_mode = not _server_provider_keys_available() and not connected_providers
    return jsonify({
        "models": model_registry.models_available(),
        "modes": model_registry.MODES,
        "preferences": {
            "model": user.get("ai_model", "claude"),
            "level": user.get("ai_level", "intermediate"),
        },
        "usage": {"used": used, "limit": limit},
        "mockMode": mock_mode,
        "connections": connections,
        "serverKeys": server_keys,
    })


# ------------------------------------------------------------ connections

@ai_api.get("/connections")
def list_connections():
    uid = session["user_id"]
    return jsonify({"connections": db.get_ai_connections(uid)})


@ai_api.post("/connections")
def add_connection():
    """Validate a BYOK key against the provider, then store it encrypted.

    Keys are verified live before saving so invalid keys never reach the db.
    """
    uid = session["user_id"]
    body = request.get_json(silent=True) or {}
    provider = (body.get("provider") or "").strip().lower()
    api_key = (body.get("apiKey") or "").strip()
    if provider not in CONNECTABLE_PROVIDERS:
        return jsonify({"error": "Unknown provider."}), 400
    if not api_key:
        return jsonify({"error": "API key cannot be empty."}), 400

    ok, note = _verify_provider_key(provider, api_key)
    if not ok:
        return jsonify({"error": note}), 400

    connection = db.set_ai_connection(
        uid, provider, api_key, label=CONNECTABLE_PROVIDERS[provider]
    )
    if not connection:
        return jsonify({"error": "Could not save connection."}), 500
    db.log_audit(uid, "ai_connect", provider)
    return jsonify({"connection": connection}), 201


@ai_api.delete("/connections/<provider>")
def remove_connection(provider):
    uid = session["user_id"]
    if provider not in CONNECTABLE_PROVIDERS:
        return jsonify({"error": "Unknown provider."}), 400
    ok = db.delete_ai_connection(uid, provider)
    if not ok:
        return jsonify({"error": "Not connected."}), 404
    db.log_audit(uid, "ai_disconnect", provider)
    return jsonify({"ok": True})


# ---------------------------------------------------------- conversations

@ai_api.get("/conversations")
def list_conversations():
    uid = session["user_id"]
    return jsonify({"conversations": storage.list_conversations(uid)})


@ai_api.post("/conversations")
def create_conversation():
    uid = session["user_id"]
    body = request.get_json(silent=True) or {}
    model = _normalize_model_choice(body.get("model"))
    mode = body.get("mode") or "ask"
    conv = storage.create_conversation(uid, model=model, mode=mode)
    return jsonify({"conversation": conv}), 201


@ai_api.get("/conversations/<conversation_id>")
def get_conversation(conversation_id):
    uid = session["user_id"]
    conv = storage.get_conversation(uid, conversation_id)
    if not conv:
        return jsonify({"error": "not found"}), 404
    return jsonify({"conversation": conv})


@ai_api.patch("/conversations/<conversation_id>")
def update_conversation(conversation_id):
    uid = session["user_id"]
    body = request.get_json(silent=True) or {}
    if "title" in body:
        ok = storage.rename_conversation(uid, conversation_id, str(body["title"])[:80])
    elif "model" in body:
        raw = body["model"]
        if raw and raw != "auto" and not model_registry.get_model(raw) and not _is_valid_gateway_key(raw):
            return jsonify({"error": "unknown model"}), 400
        ok = storage.set_conversation_model(uid, conversation_id, _normalize_model_choice(raw))
    else:
        return jsonify({"error": "nothing to update"}), 400
    if not ok:
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


@ai_api.delete("/conversations/<conversation_id>")
def delete_conversation(conversation_id):
    uid = session["user_id"]
    ok = storage.delete_conversation(uid, conversation_id)
    if not ok:
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


# ---------------------------------------------------------------- messages

@ai_api.get("/conversations/<conversation_id>/messages")
def list_messages(conversation_id):
    uid = session["user_id"]
    conv = storage.get_conversation(uid, conversation_id)
    if not conv:
        return jsonify({"error": "not found"}), 404
    return jsonify({"messages": conv.get("messages", []), "conversation": conv})


@ai_api.post("/conversations/<conversation_id>/messages")
def send_message(conversation_id):
    """Send a message. Supports non-streaming (JSON) and streaming (SSE).

    Use ?stream=1 for Server-Sent Events; otherwise a JSON response with the
    assistant reply is returned.
    """
    uid = session["user_id"]
    conv = storage.get_conversation(uid, conversation_id)
    if not conv:
        return jsonify({"error": "not found"}), 404

    body = request.get_json(silent=True) or {}
    question = (body.get("message") or "").strip()
    if not question:
        return jsonify({"error": "Message cannot be empty."}), 400
    if len(question) > limits.MAX_QUESTION_CHARS:
        return jsonify({"error": "Message is too long."}), 400

    mode = body.get("mode") or conv.get("mode") or "ask"
    requested_model = body.get("model") or conv.get("model") or ""
    model_key = _soft_model_key(uid, requested_model)
    gw_feature = _gateway_feature(mode)

    # Optional study material reference.
    material_text = None
    material_id = body.get("materialId")
    if material_id:
        material_text = _material_text(material_id, uid)

    # Enforce usage limits before doing work.
    try:
        limits.check_limit(uid)
    except limits.RateLimitError as exc:
        return jsonify({"error": str(exc)}), 429

    # Persist the user message first.
    storage.add_message(uid, conversation_id, "user", question, model=requested_model or "", metadata={"mode": mode, "materialId": material_id})

    history = conv.get("messages", [])

    # Build the same system/user prompts the legacy router used, then hand the
    # turn to the gateway: server key first, the student's own key as fallback,
    # and the student's saved pick auto-applied when no explicit model is sent.
    system_prompt, user_prompt = service.prepare_messages(
        mode=mode,
        question=question,
        material_text=file_processor.truncate_for_context(material_text),
        user=_load_users().get(uid, {}),
        conversation_messages=history[:-1],  # exclude the just-added user message
        subject_title=body.get("subject"),
    )
    gateway_messages = [
        {"role": m["role"], "content": m["content"]}
        for m in history[:-1]
        if m["role"] in ("user", "assistant")
    ]
    gateway_messages.append({"role": "user", "content": user_prompt})

    streaming = request.args.get("stream") in ("1", "true")

    if streaming:
        def generate():
            accumulated = []
            meta = None
            try:
                for item in gw.stream_text(
                    uid,
                    question,
                    system=system_prompt,
                    messages=gateway_messages,
                    model=model_key,
                    feature=gw_feature,
                    max_tokens=limits.MAX_OUTPUT_TOKENS,
                ):
                    if isinstance(item, dict):
                        # Final metadata: the model/usage actually billed.
                        meta = item
                        continue
                    accumulated.append(item)
                    yield item
            except Exception as exc:  # noqa: BLE001
                yield "[[AI_ERROR]]" + service.handle_error(exc)
                return

            full = "".join(accumulated)
            used_model = _persisted_model((meta or {}).get("model"), model_key or requested_model)
            usage = (meta or {}).get("usage") or {}
            storage.add_message(uid, conversation_id, "assistant", full, model=used_model, metadata={"mode": mode})
            storage.record_usage(
                uid,
                model=used_model,
                mode=mode,
                input_tokens=usage.get("inputTokens", 0),
                output_tokens=usage.get("outputTokens", 0),
            )
            db.log_audit(uid, "ai_reply", f"mode={mode} model={used_model}")

        def sse_gen():
            started = False
            for ch in generate():
                if ch.startswith("[[AI_ERROR]]"):
                    yield f"data: {json.dumps({'type': 'error', 'message': ch[len('[[AI_ERROR]]'):]})}\n\n"
                    yield "data: [DONE]\n\n"
                    return
                yield f"data: {json.dumps({'type': 'chunk', 'text': ch})}\n\n"
                started = True
            yield "data: [DONE]\n\n"

        return Response(sse_gen(), mimetype="text/event-stream")

    # Non-streaming path.
    try:
        result = gw.generate_text(
            uid,
            question,
            system=system_prompt,
            messages=gateway_messages,
            model=model_key,
            feature=gw_feature,
            max_tokens=limits.MAX_OUTPUT_TOKENS,
        )
    except gw_errors.QuotaExceededError as exc:
        return jsonify({"error": str(exc)}), 429
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": service.handle_error(exc)}), 502

    content = result["content"]
    used_model = _persisted_model(result.get("model"), model_key or requested_model)
    storage.add_message(uid, conversation_id, "assistant", content, model=used_model, metadata={"mode": mode})
    storage.record_usage(uid, model=used_model, mode=mode)
    db.log_audit(uid, "ai_reply", f"mode={mode} model={used_model}")
    return jsonify({"reply": content, "conversation": storage.get_conversation(uid, conversation_id)})


# ---------------------------------------------------------------- uploads

@ai_api.post("/upload")
def upload_material():
    uid = session["user_id"]
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "No file selected."}), 400

    content = f.read()
    if len(content) > limits.MAX_FILE_BYTES:
        return jsonify({"error": "File too large."}), 400

    result = file_processor.extract_text(f.filename, content, f.filename)

    # Persist extracted text for later attachment to a message.
    material_id = uuid.uuid4().hex
    file_store.save(
        file_store.ai_material_key(uid, material_id),
        result["text"].encode("utf-8"),
        user_id=uid,
        filename=f"{material_id}.txt",
        content_type="text/plain; charset=utf-8",
    )

    return jsonify({
        "materialId": material_id,
        "filename": os.path.basename(f.filename),
        "kind": result["kind"],
        "textLength": len(result["text"]),
        "note": result["note"],
    })


# ------------------------------------------------------------ preferences

@ai_api.put("/preferences")
def set_preferences():
    uid = session["user_id"]
    body = request.get_json(silent=True) or {}
    user = db.get_user(uid)
    if not user:
        return jsonify({"error": "user not found"}), 404

    model = body.get("model")
    level = body.get("level")
    if model is not None and not model_registry.get_model(model):
        return jsonify({"error": "unknown model"}), 400
    if level is not None and level not in ("beginner", "intermediate", "advanced"):
        return jsonify({"error": "unknown level"}), 400
    db.set_ai_preferences(uid, model=model, level=level)
    return jsonify({"ok": True})
