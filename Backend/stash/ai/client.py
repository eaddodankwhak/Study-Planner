"""AI generation client for Stash.

Picks a real provider for generation — the user's BYOK connection first, then
the server's configured keys — never the mock provider, and routes every model
call through the AI Gateway so Stash never talks to a provider directly. The
gateway owns key resolution (server key first, then BYOK), fallback, circuit
breaking, native JSON/structured-output mode and usage accounting; Stash keeps
its own privacy check, per-user daily token cap (only when the server key pays)
and card-shape validation with one error-feedback retry per section.
"""

import json
import os
import sys

# Backend root must be importable so the shared `ai`, `ai_gateway` and `db`
# packages resolve (the app runs from Backend/; the test worker may import from
# elsewhere).
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import ai.models as model_registry  # noqa: E402
from ai.providers import get_provider  # noqa: E402
from ai_gateway import errors as gw_errors  # noqa: E402
from ai_gateway import gateway as gw  # noqa: E402
from ai_gateway import registry as gw_registry  # noqa: E402
import db  # noqa: E402

from . import prompts as prompt_builders  # noqa: E402
from .. import repository  # noqa: E402
from .. import schemas  # noqa: E402
from ..config import StashConfig  # noqa: E402
from . import validator  # noqa: E402


class StashAIError(Exception):
    """Generation failed after retries; message is user-safe."""


class NoProviderError(StashAIError):
    """No usable AI key exists (server env or user BYOK)."""


class DailyLimitError(StashAIError):
    """The user's per-day generation budget is spent."""


class PrivacyBlockedError(StashAIError):
    """AI activity is turned off in the user's privacy settings."""


HINT = (
    "Stash needs an AI key to generate cards. Connect your own key in the AI "
    "Hub (Claude, OpenAI, Gemini and more), or ask the admin to add one to the "
    "server."
)

#: Human-friendly provider labels for pickers and banners.
_PROVIDER_LABELS = {
    "anthropic": "Claude",
    "openai": "GPT",
    "google": "Gemini",
    "deepseek": "DeepSeek",
    "copilot": "Copilot",
}

#: Server key fallback order for generation when the user has no BYOK/priority
#: pick. Each provider lists its accepted env names; any one set enables it.
_SERVER_KEYS = (
    ("anthropic", ("ANTHROPIC_API_KEY",)),
    ("openai", ("OPENAI_API_KEY",)),
    ("google", ("GOOGLE_AI_API_KEY", "GEMINI_API_KEY")),
    ("deepseek", ("DEEPSEEK_API_KEY",)),
    ("copilot", ("COPILOT_GITHUB_TOKEN", "GH_TOKEN")),
)


def privacy_allows(uid):
    """True when the user's privacy settings permit AI activity."""
    try:
        settings = db.get_settings(uid)
        return bool(settings["privacy"]["ai_activity"])
    except Exception:  # noqa: BLE001 - a settings read failure must not block
        return True


def _build_generation(provider_id, model_id, uid):
    """Return a Generation dict for (provider, model) or None when unusable.

    A Generation is {"provider": adapter, "provider_id", "model": registry
    descriptor, "model_id": API model id, "paid_by": "personal"|"server"}. The
    adapter is built fresh so the picker can show which providers are usable;
    the gateway performs the actual call (key resolution is server-first there).
    The mock provider is never accepted — happily inventing card content would
    be worse than failing.
    """
    model = model_registry.get_model(model_id)
    if not model or model["provider"] != provider_id:
        return None
    personal_key = db.get_ai_connection_key(uid, provider_id)
    provider = get_provider(provider_id, api_key=personal_key)
    if getattr(provider, "is_mock", False):
        return None
    # The gateway resolves keys server-first, so report who actually pays the
    # same way: a configured server key wins over the student's own key.
    return {
        "provider": provider,
        "provider_id": provider_id,
        "model": model,
        "model_id": model["model_id"],
        "paid_by": "server" if _server_key_set(provider_id) else "personal",
    }


def _server_envs(provider_id):
    for pid, envs in _SERVER_KEYS:
        if pid == provider_id:
            return envs
    return ()


def _server_key_set(provider_id):
    return any(os.getenv(e) for e in _server_envs(provider_id))


def _connected_providers(uid):
    return {c["provider"] for c in db.get_ai_connections(uid)}


def usable_providers(uid):
    """Return the distinct (provider, model) options Stash can generate with.

    Each entry: {"provider", "providerName", "model", "modelName", "modelApiId",
    "source": "personal"|"server", "contextWindow", "maxOutput"}. Personal
    (BYOK) options come first; a provider the user is connected to is listed
    once, using their key. Server options follow in the fallback order.
    """
    options = []
    seen = set()
    personal = _connected_providers(uid)

    for m in model_registry.MODELS:
        pid, mid = m["provider"], m["id"]
        if (pid, mid) in seen:
            continue
        if pid in personal and db.get_ai_connection_key(uid, pid):
            seen.add((pid, mid))
            options.append(_option_entry(uid, pid, mid, "personal"))
    for pid, _envs in _SERVER_KEYS:
        first = next(iter(model_registry.get_models_for_provider(pid)), None)
        if not first or (pid, first["id"]) in seen:
            continue
        if _server_key_set(pid):
            seen.add((pid, first["id"]))
            options.append(_option_entry(uid, pid, first["id"], "server"))
    return options


def _option_entry(uid, provider_id, model_id, source):
    model = model_registry.get_model(model_id)
    return {
        "provider": provider_id,
        "providerName": _PROVIDER_LABELS.get(provider_id, provider_id),
        "model": model["id"],
        "modelName": model["displayName"],
        "modelApiId": model["model_id"],
        "source": source,
        "contextWindow": model["context_window"],
        "maxOutput": model["max_output"],
    }


def resolve_generation(uid, preferred=None):
    """Resolve a usable Generation for a user, in priority order.

    preferred: {"provider": registry provider id, "model": registry model id}
    — normally the document's stored picks.

    Priority: explicit preferred pick → the user's AI Hub preference model →
    any BYOK-connected provider (registry order) → server env keys in the fixed
    fallback order (ANTHROPIC → OPENAI → GEMINI → DEEPSEEK → COPILOT). Raises
    NoProviderError when nothing usable exists.
    """
    if preferred:
        gen = _build_generation(
            preferred.get("provider"), preferred.get("model"), uid
        )
        if gen:
            return gen

    try:
        hub_model = (db.get_user(uid) or {}).get("ai_model") or StashConfig.model
    except Exception:  # noqa: BLE001 - preference read failures fall through
        hub_model = StashConfig.model
    model = model_registry.get_model(hub_model)
    if model:
        gen = _build_generation(model["provider"], model["id"], uid)
        if gen:
            return gen

    for m in model_registry.MODELS:
        if m["provider"] not in _connected_providers(uid):
            continue
        gen = _build_generation(m["provider"], m["id"], uid)
        if gen:
            return gen

    for pid, _envs in _SERVER_KEYS:
        first = next(iter(model_registry.get_models_for_provider(pid)), None)
        if not first or not _server_key_set(pid):
            continue
        gen = _build_generation(pid, first["id"], uid)
        if gen:
            return gen

    raise NoProviderError(HINT)


def _check_privacy(uid):
    if not privacy_allows(uid):
        raise PrivacyBlockedError(
            "AI activity is turned off in your privacy settings. Turn it on in "
            "Settings → Privacy to use Stash."
        )


def _check_cap(uid, generation, estimate):
    """Enforce the daily per-user cap only when the server key pays."""
    if generation.get("paid_by") == "server":
        if repository.daily_token_total(uid) + estimate > StashConfig.daily_token_cap:
            raise DailyLimitError(
                "Your daily Stash AI limit is reached. New requests resume tomorrow."
            )


def generate_chunk_cards(uid, generation, document, section, chunk):
    """Generate validated cards for one chunk; returns (cards, usage_tokens)."""
    _check_privacy(uid)
    _check_cap(uid, generation, estimate_tokens(chunk["text"]))

    user_prompt = prompt_builders.build_chunk_prompt(
        document["title"], section["title"], chunk["text"]
    )
    tokens, content = _generate_json(
        uid, generation, user_prompt, describe="cards for a section",
    )
    cards = _validate_with_retry(
        uid, generation, user_prompt, content,
        default_page=_lookup_page(document.get("page_count"), chunk),
    )
    return cards, tokens


def generate_recap_card(uid, generation, document, section, section_text):
    """Generate the single recap card for a section; returns (card, usage)."""
    _check_privacy(uid)
    _check_cap(uid, generation, estimate_tokens(section_text) + 200)

    user_prompt = prompt_builders.build_recap_prompt(
        document["title"], section["title"], section_text
    )
    tokens, content = _generate_json(
        uid, generation, user_prompt,
        describe=f'recap for "{section["title"]}"',
    )
    cards = _validate_with_retry(
        uid, generation, user_prompt, content,
        default_page=None,
    )
    recap = [c for c in cards if c["card_type"] == "recap"]
    if not recap:
        return None, tokens
    return recap[0], tokens


# ---------------------------------------------------------------- internals

def _validate_with_retry(uid, generation, user_prompt, content,
                         default_page=None, default_slide=None):
    """Parse + shape-validate; retry once feeding validation errors back."""
    for attempt in range(2):
        try:
            cards = validator.parse_cards(
                content, default_page=default_page, default_slide=default_slide
            )
            problems = [p for c in cards for p in c.get("problems", [])]
            if not problems:
                for card in cards:
                    card["content_hash"] = validator.content_hash(card)
                return cards
            if attempt == 0:
                user_prompt = prompt_builders.build_fix_prompt(problems, content)
                _, content = _generate_json(uid, generation, user_prompt, describe="fixed cards")
                continue
            # Second attempt still imperfect: keep flagged-but-usable cards.
            usable = [c for c in cards if not c.get("problems")]
            if usable:
                for card in usable:
                    card["content_hash"] = validator.content_hash(card)
                return usable
        except validator.GenerationError:
            if attempt == 0:
                user_prompt = prompt_builders.build_fix_prompt(
                    ["Response was not valid JSON in the required shape."], content
                )
                _, content = _generate_json(uid, generation, user_prompt, describe="fixed cards")
                continue
    raise StashAIError(
        "The generator could not produce valid cards for this section. "
        "The section was skipped; you can regenerate it later."
    )


def _generate_json(uid, generation, user_prompt, describe=""):
    """Run one structured request through the AI Gateway; (tokens, content).

    The gateway owns provider/model fallback, key resolution (server key first,
    then the student's BYOK), circuit-breaking and usage accounting, so Stash
    never calls a provider directly. The pick from resolve_generation() rides
    along as the gateway's soft preference; the model/provider/paid_by actually
    used are read back and reflected on ``generation`` so Stash's own daily cap
    and accounting stay accurate. The cards JSON Schema is passed through so
    providers with a native JSON/structured-output mode constrain the reply;
    Stash still does its own card-shape validation. Content is raw JSON text.
    """
    key = gw_registry.model_key(
        generation["provider_id"], generation["model"]["model_id"]
    )
    try:
        envelope = gw.generate_json(
            uid,
            schemas.CARDS_SCHEMA,
            user_prompt,
            model=key,
            feature="cards",
            max_tokens=StashConfig.max_output_tokens,
            temperature=StashConfig.temperature,
            max_retries=1,
            with_meta=True,
        )
    except gw_errors.AIDisabledError as exc:
        raise PrivacyBlockedError(str(exc)) from exc
    except gw_errors.NoProviderAvailableError as exc:
        raise NoProviderError(HINT) from exc
    except gw_errors.QuotaExceededError as exc:
        raise DailyLimitError(str(exc)) from exc
    except gw_errors.GatewayError as exc:
        raise StashAIError(
            f"The AI provider failed while generating {describe or 'cards'}. {exc}"
        ) from exc

    # The gateway resolves keys server-first, so trust what it reports over the
    # estimate made at resolve time.
    generation["paid_by"] = envelope.get("paid_by") or generation.get("paid_by")
    usage = envelope.get("usage") or {}
    tokens = (
        int(usage.get("inputTokens", 0) or 0),
        int(usage.get("outputTokens", 0) or 0),
    )
    content = json.dumps(envelope["data"], ensure_ascii=False).strip()
    if not content or content in ("{}", "[]", "null"):
        raise StashAIError(
            f"The generator returned nothing for {describe or 'this section'}."
        )
    return tokens, content


def estimate_tokens(text):
    import math

    return max(1, math.ceil(len(text or "") / 4))


def _lookup_page(page_count, chunk):
    """Return the anchor page for a chunk (first page unless it's beyond PDF)."""
    first = chunk.get("page_start")
    if page_count and first and first <= page_count:
        return int(first)
    return first


def describe_provider_availability(uid):
    """User-facing note about which provider/key Stash uses, plus picker data."""
    providers = usable_providers(uid)
    try:
        gen = resolve_generation(uid)
        return {
            "configured": True,
            "provider": _PROVIDER_LABELS.get(gen["provider_id"], gen["provider_id"]),
            "model": gen["model"]["model_id"],
            "providerId": gen["provider_id"],
            "modelId": gen["model"]["id"],
            "paidBy": gen["paid_by"],
            "providers": providers,
            "hint": "",
        }
    except NoProviderError:
        return {"configured": False, "providers": providers, "hint": HINT}