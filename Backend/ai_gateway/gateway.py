"""Gateway core: the single entry point for server-side AI calls.

Any feature (AI Hub chat, Stash cards, list features) calls:

    gateway.generate_text(user_id, prompt, ...)
    gateway.generate_json(user_id, schema, prompt, ...)

The gateway owns:
  * model auto-selection when the caller gives none (or a preferred id);
  * key resolution — server key first, then the user's own BYOK key;
  * provider/model eligibility (is_free_tier, allowed_for_minors, enabled,
    has_server_key / has BYOK, health state, reliability overrides);
  * JSON mode: adapters use a provider-native JSON constraint when one exists,
    and the gateway *also* parses + validates the JSON against the caller's
    schema with one self-correction retry;
  * usage accounting (ai_gateway_usage) so quota meters have real numbers.

Phases later add: quotas (Phase 3), caching (Phase 3), fallback ordering
(Phase 3), circuit breaker (Phase 3), admin overrides (Phase 6).

The privacy gate (users.settings_json privacy.ai_activity == False) raises
AIDisabledError and is enforced here so every gateway caller gets it for free.
"""

import uuid

from . import cache, health, quotas, registry
from .config import GatewayConfig
from .adapters import build_adapter
from .errors import (
    AIDisabledError,
    AllProvidersFailedError,
    NoProviderAvailableError,
    PreferenceError,
    ProviderCallError,
)
from .validation import ParseError, extract_json, validate_schema

_HINT = (
    "AI is disabled (no server key is configured and you have not added a "
    "personal key in Settings → AI Providers)."
)


def gateway_enabled(user_id):
    """True when AI activity is allowed for this user (privacy switch)."""
    import db

    try:
        settings = db.get_settings(user_id)
        return bool(settings["privacy"]["ai_activity"])
    except Exception:  # noqa: BLE001 - a privacy read must never block AI
        return True


def _now_day():
    """UTC day string used as the usage/quota window key."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ------------------------------------------------------- key resolution

def server_key_for(provider_id):
    """Return the server's key for a provider, or None."""
    import db

    provider = registry.get_provider(provider_id)
    if not provider:
        return None
    if provider["has_server_key"]:
        key = GatewayConfig.server_keys.get(provider["env_key_name"])
        if key:
            return key
        # Legacy alias (GOOGLE_AI_API_KEY) for google.
        if provider_id == "google":
            return GatewayConfig.server_keys.get("GOOGLE_AI_API_KEY")
    return None


def _resolve_key(user_id, provider):
    """Return (api_key, paid_by) — server key first, then user's BYOK.

    paid_by is "server" or "personal"; personal keys never burn the daily
    server quota (mirrors the Stash convention).
    """
    key = server_key_for(provider["slug"])
    if key:
        return key, "server"
    try:
        import db

        personal = db.get_ai_connection_key(user_id, provider["slug"])
    except Exception:  # noqa: BLE001
        personal = None
    if personal:
        return personal, "personal"
    return None, None


# ----------------------------------------------------- model selection

def _eligible_providers(user_id):
    """Providers this user may use: enabled, and with a usable key."""
    usable = []
    for p in registry.list_providers(enabled_only=True):
        if p["has_server_key"]:
            usable.append(p)
            continue
        try:
            import db

            if db.get_ai_connection_key(user_id, p["slug"]):
                usable.append(p)
        except Exception:  # noqa: BLE001
            continue
    return usable


def _eligible_models(user_id):
    """Models the user may use, in picker order, each with a usable key."""
    providers = {p["slug"]: p for p in _eligible_providers(user_id)}
    out = []
    for m in registry.list_models(enabled_models=True):
        if m["provider_slug"] not in providers:
            continue
        m = dict(m)
        m["has_server_key"] = providers[m["provider_slug"]]["has_server_key"]
        out.append(m)
    return out


def select_model(user_id, preferred=None, feature=None):
    """Auto-select a real model for a user; raises NoProviderAvailableError.

    preferred: a model dict from the registry (the caller may pass the user's
    stored preference); when None the model list is filtered by feature fit
    (best_for) and the cheapest/fastest free one wins. This keeps callers
    (Stash, AI Hub, list features) able to ask for "any model" and get a real
    one without coupling to provider internals.
    """
    if preferred is not None:
        m = registry.get_model(preferred.get("provider_id"), preferred.get("model_id"))
        if m and _model_usable(user_id, m):
            return m
        raise PreferenceError(
            "Your preferred AI model is no longer available. Pick another in "
            "Settings → AI Providers."
        )

    models = _ranked_models(user_id, feature)
    if not models:
        raise NoProviderAvailableError(_HINT)
    return models[0]


def _model_usable(user_id, m):
    provider = registry.get_provider(m["provider_slug"])
    if not provider or not provider["is_enabled"]:
        return False
    # Skip providers whose circuit breaker is open (flaky/down).
    if not health.is_available(m["provider_slug"]):
        return False
    # Providers that may train on prompts are skipped for minors unless the
    # admin explicitly allowed them (build-prompt default).
    if not _is_minor(user_id):
        return True
    return not provider["may_train_on_data"] and provider["allowed_for_minors"]


def _fallback_chain(user_id, preferred=None, feature=None):
    """Ordered list of usable models to try: preferred (if any) first, then
    the auto-ranked alternatives. The gateway walks this chain on provider
    failure (up to fallback_max_tries) and records breaker state per provider.
    """
    if preferred is not None:
        m = registry.get_model(preferred.get("provider_id"), preferred.get("model_id"))
        if m and _model_usable(user_id, m):
            chain = [m]
        else:
            raise PreferenceError(
                "Your preferred AI model is no longer available. Pick another in "
                "Settings → AI Providers."
            )
    else:
        chain = []

    for m in _ranked_models(user_id, feature):
        if not any(
            m["model_id"] == c["model_id"] and m["provider_slug"] == c["provider_slug"]
            for c in chain
        ):
            chain.append(m)

    if not chain:
        raise NoProviderAvailableError(_HINT)
    return chain


def _ranked_models(user_id, feature=None):
    """Auto-ranked usable models (same scoring as select_model) for fallback."""
    models = _eligible_models(user_id)
    if not feature:
        return models
    scored = []
    for m in models:
        if not _model_usable(user_id, m):
            continue
        features = (m.get("best_for") or "").split(",")
        in_feature = 1 if (feature in features or "*" in features) else 0
        free = 1 if m.get("tier") == "free" else 0
        speed = {"fast": 3, "medium": 2, "slow": 1}.get(m.get("speed"), 0)
        cost = m.get("cost_in_per_million") or 0
        scored.append((in_feature, free, speed, -cost, m["sort_order"], m))
    scored.sort(key=lambda t: t[:-1], reverse=True)
    return [t[-1] for t in scored]


def _is_minor(user_id):
    """True when the user is a minor account (data-training providers gated)."""
    import db

    try:
        user = db.get_user(user_id) or {}
    except Exception:  # noqa: BLE001
        return False
    group = (user.get("user_group") or "").lower()
    role = (user.get("role") or "").lower()
    return group == "minors" or role in ("minor", "child", "student_minor")


# ------------------------------------------------------------- generation

def _record_usage(user_id, model, provider, feature, usage, paid_by):
    """Upsert one row in ai_gateway_usage for the request."""
    import db

    day = _now_day()
    input_tokens = int(usage.get("inputTokens") or 0)
    output_tokens = int(usage.get("outputTokens") or 0)
    est_cost = input_tokens / 1_000_000.0 * (model.get("cost_in_per_million") or 0)
    est_cost += output_tokens / 1_000_000.0 * (model.get("cost_out_per_million") or 0)
    with db._conn_context() as conn:
        row = conn.execute(
            "SELECT id, requests, input_tokens, output_tokens, est_cost "
            "FROM ai_gateway_usage WHERE user_id = ? AND day = ? "
            "AND model_id = ? AND feature = ? AND used_own_key = ?",
            (user_id, day, model["model_id"], feature, 1 if paid_by == "personal" else 0),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE ai_gateway_usage SET requests = requests + 1, "
                "input_tokens = input_tokens + ?, output_tokens = output_tokens + ?, "
                "est_cost = est_cost + ? WHERE id = ?",
                (input_tokens, output_tokens, est_cost, row["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO ai_gateway_usage "
                "(id, user_id, day, model_id, feature, requests, input_tokens, "
                " output_tokens, est_cost, used_own_key) "
                "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
                (
                    uuid.uuid4().hex,
                    user_id,
                    day,
                    model["model_id"],
                    feature,
                    input_tokens,
                    output_tokens,
                    est_cost,
                    1 if paid_by == "personal" else 0,
                ),
            )
        conn.commit()


def _call(adapter, request, describe):
    """Wrap adapter errors into GatewayErrors with user-safe messages."""
    from ai.providers._http import ProviderHTTPError

    try:
        return adapter.generate(request)
    except ProviderHTTPError as exc:
        if exc.status in (401, 403):
            raise ProviderCallError(
                f"The AI provider rejected the server key for {describe} (HTTP {exc.status})."
            ) from exc
        if exc.status == 429:
            raise ProviderCallError(
                f"The AI provider is rate-limiting us right now ({describe}). Try again shortly."
            ) from exc
        raise ProviderCallError(
            f"The AI provider returned HTTP {exc.status} while {describe}. Try again shortly."
        ) from exc
    except (ConnectionError, TimeoutError, OSError) as exc:
        raise ProviderCallError(
            f"Could not reach the AI provider while {describe}. Try again shortly."
        ) from exc
    except Exception as exc:  # noqa: BLE001 - adapter contract violations
        raise ProviderCallError(
            f"The AI provider call failed while {describe}."
        ) from exc


def _call_json(adapter, request, schema, describe):
    """Like _call but uses the adapter's JSON mode (native when available)."""
    from ai.providers._http import ProviderHTTPError

    try:
        if schema is not None and hasattr(adapter, "generate_json"):
            return adapter.generate_json(request, schema=schema)
        return adapter.generate(request)
    except ProviderHTTPError as exc:
        if exc.status in (401, 403):
            raise ProviderCallError(
                f"The AI provider rejected the server key for {describe} (HTTP {exc.status})."
            ) from exc
        if exc.status == 429:
            raise ProviderCallError(
                f"The AI provider is rate-limiting us right now ({describe}). Try again shortly."
            ) from exc
        raise ProviderCallError(
            f"The AI provider returned HTTP {exc.status} while {describe}. Try again shortly."
        ) from exc
    except (ConnectionError, TimeoutError, OSError) as exc:
        raise ProviderCallError(
            f"Could not reach the AI provider while {describe}. Try again shortly."
        ) from exc
    except Exception as exc:  # noqa: BLE001 - adapter contract violations
        raise ProviderCallError(
            f"The AI provider call failed while {describe}."
        ) from exc


def _attempt_chain(user_id, chain, describe, runner):
    """Walk the fallback chain, returning the first success.

    runner(adapter, model, provider, paid_by, request) performs one call and
    raises ProviderCallError on failure. Each failure is recorded on the
    provider's circuit breaker; once the breaker opens, that provider is
    skipped. Raises AllProvidersFailedError when the whole chain is exhausted.
    """
    last_error = None
    tried = 0
    for model in chain:
        if tried >= GatewayConfig.fallback_max_tries:
            break
        provider = registry.get_provider(model["provider_slug"])
        if not provider:
            continue
        if not health.is_available(provider["slug"]):
            continue  # breaker open; move to the next model
        api_key, paid_by = _resolve_key(user_id, provider)
        if not api_key:
            continue
        tried += 1
        try:
            return runner(build_adapter(provider, api_key), model, provider, paid_by)
        except ProviderCallError as exc:
            health.record_failure(provider["slug"], str(exc))
            last_error = exc
    if last_error is not None:
        raise AllProvidersFailedError(
            "AI is temporarily unavailable across all providers. Try again in a "
            "moment."
        ) from last_error
    raise NoProviderAvailableError(_HINT)


def generate_text(user_id, prompt, *, system=None, messages=None,
                  model=None, feature="chat", max_tokens=None,
                  temperature=None, require_privacy=True, cache_key=None):
    """Generate free text through the gateway.

    Returns {"content", "model": model dict, "provider": provider dict,
    "paid_by": "server"|"personal"}. `cache_key` opts this call into the
    shared-content cache (document generation only — never personal text).
    """
    if require_privacy and not gateway_enabled(user_id):
        raise AIDisabledError(
            "AI activity is turned off in your privacy settings. Turn it on in "
            "Settings → Privacy to use AI features."
        )
    chain = _fallback_chain(user_id, preferred=model, feature=feature)

    if cache_key:
        cached = cache.get(cache_key)
        if cached is not None:
            chosen = chain[0]
            provider = registry.get_provider(chosen["provider_slug"])
            return {"content": cached, "model": chosen, "provider": provider,
                    "paid_by": "server", "cached": True}

    def runner(adapter, chosen, provider, paid_by):
        built = list(messages or [])
        if system and not any(m.get("role") == "system" for m in built):
            built.insert(0, {"role": "system", "content": system})
        if not any(m.get("role") == "user" for m in built):
            built.append({"role": "user", "content": prompt})
        request = {"model": chosen["model_id"], "messages": built}
        if max_tokens is not None:
            request["max_tokens"] = max_tokens
        if temperature is not None:
            request["temperature"] = temperature
        quotas.check(user_id, chosen.get("tier", "free"), paid_by,
                     estimated_tokens=len(prompt) // 3)
        result = _call(adapter, request, feature)
        _record_usage(user_id, chosen, provider, feature, result["usage"], paid_by)
        health.record_success(provider["slug"])
        return {"content": result["content"], "model": chosen, "provider": provider,
                "paid_by": paid_by, "cached": False}

    out = _attempt_chain(user_id, chain, feature, runner)
    if cache_key:
        cache.set(cache_key, out["content"], model_id=out["model"]["model_id"])
    return out


def generate_json(user_id, schema, prompt, *, model=None, feature="generate",
                  max_tokens=None, temperature=None, require_privacy=True,
                  max_retries=1, cache_key=None, with_meta=False):
    """Generate a validated JSON object through the gateway.

    Returns the parsed JSON value. Pass with_meta=True to receive
    {"data", "model", "provider", "paid_by", "cached"} instead (callers that
    need the model/tokens for display, e.g. Stash).

    On provider failure the gateway falls back to the next healthy model. On
    invalid JSON/schema it retries once with a corrective message before
    raising JSONValidationError.
    """
    if require_privacy and not gateway_enabled(user_id):
        raise AIDisabledError(
            "AI activity is turned off in your privacy settings. Turn it on in "
            "Settings → Privacy to use AI features."
        )
    chain = _fallback_chain(user_id, preferred=model, feature=feature)

    if cache_key:
        cached = cache.get(cache_key)
        if cached is not None:
            chosen = chain[0]
            provider = registry.get_provider(chosen["provider_slug"])
            envelope = {"data": cached, "model": chosen, "provider": provider,
                        "paid_by": "server", "cached": True}
            return envelope if with_meta else cached

    def runner(adapter, chosen, provider, paid_by):
        last_error = None
        for _attempt in range(max_retries + 1):
            messages = [
                {"role": "system",
                 "content": (
                     "You are helping a student. Respond with ONLY a single JSON "
                     "object matching the requested schema. No markdown fences, no "
                     "commentary."
                 )},
                {"role": "user", "content": prompt},
            ]
            if last_error:
                messages.append({
                    "role": "user",
                    "content": (
                        f"Your previous reply was not valid JSON. Fix these issues "
                        f"and return the corrected object only:\n{last_error}"
                    ),
                })
            request = {"model": chosen["model_id"], "messages": messages}
            if max_tokens is not None:
                request["max_tokens"] = max_tokens
            if temperature is not None:
                request["temperature"] = temperature
            quotas.check(user_id, chosen.get("tier", "free"), paid_by,
                         estimated_tokens=len(prompt) // 3)
            result = _call_json(adapter, request, schema, feature)
            _record_usage(user_id, chosen, provider, feature, result["usage"], paid_by)
            health.record_success(provider["slug"])
            try:
                parsed = extract_json(result["content"])
            except ParseError as exc:
                last_error = f"Could not parse the reply as JSON ({exc})."
                continue
            problems = validate_schema(parsed, schema) if schema else []
            if not problems:
                return {"data": parsed, "model": chosen, "provider": provider,
                        "paid_by": paid_by, "cached": False}
            last_error = "Validation problems: " + "; ".join(problems)

        from .errors import JSONValidationError

        raise JSONValidationError(
            "The AI returned content that did not match the expected format after "
            f"{max_retries + 1} attempts.",
            raw_content=last_error,
        )

    out = _attempt_chain(user_id, chain, feature, runner)
    if cache_key:
        cache.set(cache_key, out["data"], model_id=out["model"]["model_id"])
    return out if with_meta else out["data"]