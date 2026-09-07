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

from . import files as file_processor
from . import limits
from . import models as model_registry
from . import storage
from . import service
from .providers import get_provider

ai_api = Blueprint("ai_api", __name__, url_prefix="/api/ai")

#: Providers the "connect your own account" flow accepts, keyed to the
#: registry's provider ids (see ai/models.py) plus the display label used in UI.
CONNECTABLE_PROVIDERS = {"anthropic": "Claude", "openai": "ChatGPT", "google": "Gemini"}

UPLOAD_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "Database",
    "ai_uploads",
)


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
    """Return stored material text (and user's own upload) or None."""
    if not material_id:
        return None
    safe = os.path.basename(str(material_id))
    path = os.path.join(UPLOAD_ROOT, user_id, safe + ".txt")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _server_provider_keys_available():
    """True when any real provider key is configured at the server level."""
    return any(
        os.getenv(k) for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_AI_API_KEY")
    )


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
    server_keys = [
        pid for pid, env in (
            ("openai", "OPENAI_API_KEY"),
            ("anthropic", "ANTHROPIC_API_KEY"),
            ("google", "GOOGLE_AI_API_KEY"),
        ) if os.getenv(env)
    ]
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
    """Validate a BYOK key against the provider, then store it (obfuscated).

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
    model = body.get("model") or "claude"
    mode = body.get("mode") or "ask"
    if not model_registry.get_model(model):
        model = "claude"
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
        if not model_registry.get_model(body["model"]):
            return jsonify({"error": "unknown model"}), 400
        ok = storage.set_conversation_model(uid, conversation_id, body["model"])
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
    model_id = body.get("model") or conv.get("model") or "claude"
    model = model_registry.get_model(model_id) or model_registry.get_model("claude")

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
    storage.add_message(uid, conversation_id, "user", question, model=model_id, metadata={"mode": mode, "materialId": material_id})

    history = conv.get("messages", [])

    request_dict = service.build_provider_request(
        mode=mode,
        question=question,
        material_text=file_processor.truncate_for_context(material_text),
        user=_load_users().get(uid, {}),
        conversation_messages=history[:-1],  # exclude the just-added user message
        model_id=model_id,
        subject_title=body.get("subject"),
    )

    # Use the user's own BYOK key when connected; otherwise the provider falls
    # back to the server-level key (or the mock provider when neither exists).
    personal_key = db.get_ai_connection_key(uid, model["provider"])

    streaming = request.args.get("stream") in ("1", "true")

    if streaming:
        def generate():
            accumulated = []

            def chunks():
                for ch in service.stream_reply(request_dict, api_key=personal_key):
                    accumulated.append(ch)
                    yield ch

            try:
                for ch in chunks():
                    yield ch
            except Exception as exc:  # noqa: BLE001
                msg = service.handle_error(exc)
                yield "[[AI_ERROR]]" + msg
                return

            full = "".join(accumulated)
            storage.add_message(uid, conversation_id, "assistant", full, model=model_id, metadata={"mode": mode})
            storage.record_usage(uid, model=model_id, mode=mode)
            db.log_audit(uid, "ai_reply", f"mode={mode} model={model_id}")
            # Note: tokens unknown in streaming (mock) — usage recorded without token counts.

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
        result = service.generate_reply(request_dict, api_key=personal_key)
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": service.handle_error(exc)}), 502

    content = result["content"]
    usage = result.get("usage", {})
    storage.add_message(uid, conversation_id, "assistant", content, model=model_id, metadata={"mode": mode})
    storage.record_usage(
        uid,
        model=model_id,
        mode=mode,
        input_tokens=usage.get("inputTokens", 0),
        output_tokens=usage.get("outputTokens", 0),
    )
    db.log_audit(uid, "ai_reply", f"mode={mode} model={model_id}")
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
    folder = os.path.join(UPLOAD_ROOT, uid)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, material_id + ".txt"), "w", encoding="utf-8") as out:
        out.write(result["text"])

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
