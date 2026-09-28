"""AI generation client for Stash.

Resolves a real provider for generation (server env key or the user's BYOK
connection — never the mock provider), enforces the privacy toggle and the
per-user daily token cap, then generates + validates cards with retry/backoff
and one error-feedback retry per chunk.
"""

import os
import sys
import time
import urllib.error

# Backend root must be importable so the shared `ai` and `db` packages resolve
# (the app runs from Backend/; the test worker may import from elsewhere).
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import ai.models as model_registry  # noqa: E402
import ai.providers  # noqa: E402
from ai.providers import get_provider  # noqa: E402
from ai.providers._http import ProviderHTTPError  # noqa: E402
import db  # noqa: E402

from . import prompts as prompt_builders  # noqa: E402
from .. import repository  # noqa: E402
from .. import schemas  # noqa: E402
from ..config import StashConfig  # noqa: E402
from . import validator  # noqa: E402


class StashAIError(Exception):
    """Generation failed after retries; message is user-safe."""


class NoProviderError(StashAIError):
    """No usable Anthropic key exists (server env or user BYOK)."""


class DailyLimitError(StashAIError):
    """The user's per-day generation budget is spent."""


class PrivacyBlockedError(StashAIError):
    """AI activity is turned off in the user's privacy settings."""


HINT = (
    "Stash needs a real Claude key. Add ANTHROPIC_API_KEY to the server or "
    "connect your own Claude account in Settings → AI Settings, then retry."
)

_RETRY_STATUSES = {429, 500, 502, 503, 504}


def privacy_allows(uid):
    """True when the user's privacy settings permit AI activity."""
    try:
        settings = db.get_settings(uid)
        return bool(settings["privacy"]["ai_activity"])
    except Exception:  # noqa: BLE001 - a settings read failure must not block
        return True


def resolve_provider(uid):
    """Return (provider, model_api_id) using server key or user BYOK key.

    Explicitly rejects the mock provider: Silently returning invented content
    for a paid feature would be worse than failing loudly.
    """
    model = model_registry.get_model(StashConfig.model) or model_registry.get_model("claude")
    provider_id = model["provider"]
    personal_key = db.get_ai_connection_key(uid, provider_id)
    provider = get_provider(provider_id, api_key=personal_key)
    if getattr(provider, "is_mock", False):
        raise NoProviderError(HINT)
    return provider, model["model_id"]


def generate_chunk_cards(uid, document, section, chunk):
    """Generate validated cards for one chunk; returns (cards, usage_tokens)."""
    if not privacy_allows(uid):
        raise PrivacyBlockedError(
            "AI activity is turned off in your privacy settings. Turn it on in "
            "Settings → Privacy to use Stash."
        )
    provider, model_id = resolve_provider(uid)

    estimate = estimate_tokens(chunk["text"])
    if repository.daily_token_total(uid) + estimate > StashConfig.daily_token_cap:
        raise DailyLimitError(
            "Your daily Stash AI limit is reached. New requests resume tomorrow."
        )

    user_prompt = prompt_builders.build_chunk_prompt(
        document["title"], section["title"], chunk["text"]
    )
    tokens, content = _request(
        provider, model_id, user_prompt, describe="cards for a section"
    )
    cards = _validate_with_retry(
        provider, model_id, user_prompt, content,
        default_page=_lookup_page(document.get("page_count"), chunk),
    )
    return cards, tokens


def generate_recap_card(uid, document, section, section_text):
    """Generate the single recap card for a section; returns (card, usage)."""
    provider, model_id = resolve_provider(uid)
    estimate = estimate_tokens(section_text) + 200
    if repository.daily_token_total(uid) + estimate > StashConfig.daily_token_cap:
        raise DailyLimitError(
            "Your daily Stash AI limit is reached. New requests resume tomorrow."
        )
    user_prompt = prompt_builders.build_recap_prompt(
        document["title"], section["title"], section_text
    )
    tokens, content = _request(
        provider, model_id, user_prompt, describe=f'recap for "{section["title"]}"'
    )
    cards = _validate_with_retry(
        provider, model_id, user_prompt, content, default_page=None
    )
    recap = [c for c in cards if c["card_type"] == "recap"]
    if not recap:
        return None, tokens
    return recap[0], tokens


# ---------------------------------------------------------------- internals

def _validate_with_retry(provider, model_id, user_prompt, content,
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
                _, content = _request(provider, model_id, user_prompt, describe="fixed cards")
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
                _, content = _request(provider, model_id, user_prompt, describe="fixed cards")
                continue
    raise StashAIError(
        "The generator could not produce valid cards for this section. "
        "The section was skipped; you can regenerate it later."
    )


def _request(provider, model_id, user_prompt, describe=""):
    """Run one provider request with retry/backoff; returns (tokens, content)."""
    request = {
        "messages": [
            {"role": "system", "content": prompt_builders.SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "model": model_id,
        "max_tokens": StashConfig.max_output_tokens,
        "temperature": StashConfig.temperature,
    }
    delay = 1.0
    for attempt in range(3):
        try:
            result = provider.generate(request)
            usage = result.get("usage") or {}
            tokens = (
                int(usage.get("inputTokens", 0) or 0),
                int(usage.get("outputTokens", 0) or 0),
            )
            content = (result.get("content") or "").strip()
            if not content:
                raise StashAIError(
                    f"The generator returned nothing for {describe or 'this section'}."
                )
            return tokens, content
        except ProviderHTTPError as exc:
            if exc.status in _RETRY_STATUSES and attempt < 2:
                time.sleep(delay)
                delay *= 2
                continue
            detail = {"429": "rate limit", "401": "bad key", "403": "access denied",
                      "404": "model unavailable"}.get(str(exc.status), f"HTTP {exc.status}")
            raise StashAIError(
                f"The AI provider failed ({detail}) while generating {describe or 'cards'}."
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt < 2:
                time.sleep(delay)
                delay *= 2
                continue
            raise StashAIError(
                "Could not reach the AI provider while generating cards."
            ) from exc
    raise StashAIError("Generation failed after repeated retries.")


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
    """User-facing note about which provider/key Stash will use."""
    try:
        provider, model_id = resolve_provider(uid)
        return {"configured": True, "provider": provider.name, "model": model_id}
    except NoProviderError:
        return {"configured": False, "hint": HINT}