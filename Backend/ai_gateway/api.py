"""Student-facing AI Gateway endpoints.

The whole point of the gateway is that a student never has to think about
providers or API keys. These endpoints are the only gateway surface the browser
touches, and they are deliberately dumb: they report friendly model names,
whether the server can serve them, the student's own remaining quota, and which
model the student last picked. No API key, base URL, env var name or internal
provider id is ever serialised here.

Three routes:
    GET  /api/ai-gateway/models       model picker + availability + quota
    PUT  /api/ai-gateway/models       remember the student's model choice
    GET  /api/ai-gateway/usage        the usage-meter badge on its own
"""

from flask import Blueprint, jsonify, request, session

from . import gateway, health, quotas, registry

gateway_api = Blueprint("ai_gateway_api", __name__, url_prefix="/api/ai-gateway")

# Human labels for the cost tiers. The numbers are provider list prices and
# change often, so students get a word, not a price they could be misled by.
TIER_LABELS = {
    "free": "Free",
    "standard": "Standard",
    "premium": "Premium",
}

# Why a model is not offered, in language a student can act on.
_UNAVAILABLE_COPY = {
    "disabled": "Not available right now",
    "no_key": "Temporarily unavailable",
    "circuit": "Temporarily unavailable",
    "minors": "Not available for your account type",
}


@gateway_api.before_request
def _require_login():
    if not session.get("user_id"):
        return jsonify({"error": "unauthorized"}), 401


def _tier_label(tier):
    return TIER_LABELS.get(tier, "Standard")


def _serialize_model(user_id, model, minor):
    """Build the browser-safe view of one catalog model.

    The id is the public "provider/model" key rather than the row uuid: it is
    what the student stores as a preference, and it survives a reseed. Every
    other field is display-only. Nothing here reveals a key, a base URL or an
    environment variable name.
    """
    provider = registry.get_provider(model["provider_slug"]) or {}
    usable, reason = _availability(user_id, model, provider, minor)
    # Whether a request on this model bills the student's own key or the
    # server's included quota. Reports only which, never the key itself, so the
    # picker can badge personal-key models.
    _key, paid_by = gateway._resolve_key(user_id, provider)
    return {
        "id": registry.model_key(model["provider_slug"], model["model_id"]),
        "name": model["display_name"],
        "provider": provider.get("display_name") or "AI",
        "tier": model["tier"],
        "tierLabel": _tier_label(model["tier"]),
        "bestFor": model["best_for"] or "chat",
        "contextWindow": model.get("context_window") or 0,
        "available": usable,
        "unavailableReason": _UNAVAILABLE_COPY.get(reason),
        "paidBy": "personal" if paid_by == "personal" else "server",
    }


def _availability(user_id, model, provider, minor):
    """Return (usable, reason) without ever raising, for UI rendering."""
    if not model.get("is_enabled") or not provider.get("is_enabled"):
        return False, "disabled"
    # A model the student cannot reach is shown as unavailable rather than
    # hidden, so the picker does not silently reshuffle itself.
    if not gateway._provider_reachable(user_id, provider):
        return False, "no_key"
    if not gateway._model_allowed_for_account(model, provider, minor):
        return False, "minors"
    if not health.is_available(model["provider_slug"]):
        return False, "circuit"
    return True, None


@gateway_api.get("/models")
def list_models():
    """Model picker data: the catalog, what is usable, and the quota left."""
    user_id = session["user_id"]
    registry.ensure_seeded()
    minor = gateway._is_minor(user_id)
    models = [
        _serialize_model(user_id, m, minor)
        for m in registry.list_models(enabled_models=False)
    ]
    saved = gateway.get_preference(user_id)
    return jsonify({
        "models": models,
        "preferredModelId": saved.get("preferred_model_id"),
        "autoMode": saved.get("auto_mode", 1) == 1,
        "usage": _usage_payload(user_id),
    })


@gateway_api.put("/models")
def save_model():
    """Remember the student's pick so the next message uses the same model.

    Setting autoMode clears the pinned model: auto means "let the gateway
    choose", and a stale pin would silently override it.
    """
    user_id = session["user_id"]
    payload = request.get_json(silent=True) or {}
    model_id = payload.get("modelId") or None
    auto = bool(payload.get("autoMode", model_id is None))

    if model_id:
        # The catalog is the allow-list: a model id that is not in it is a
        # client bug, not a preference. Disabled models are still accepted,
        # because an admin may switch one off after a student picked it, and
        # the gateway already falls back to auto when a pin is unusable. Auto
        # mode sends no modelId at all, so a stale pin can always be cleared.
        if not isinstance(model_id, str) or not registry.get_model_by_key(model_id):
            return jsonify({"error": "unknown_model"}), 400
    if auto:
        model_id = None

    gateway.set_preference(user_id, model_id, auto_mode=auto)
    return jsonify({
        "preferredModelId": model_id,
        "autoMode": auto,
        "usage": _usage_payload(user_id),
    })


@gateway_api.get("/usage")
def usage():
    """The usage-meter badge, refreshed on its own after a long session."""
    return jsonify(_usage_payload(session["user_id"]))


def _usage_payload(user_id):
    """Quota snapshot for the meter, tolerant of an unseeded rules table.

    The meter reports the *free* tier allowance on purpose. Students have no
    plan tier of their own — the tier belongs to the model they happen to be
    using — and the free tier is the allowance every student is guaranteed
    regardless of which model auto-selection picks for them. Individual calls
    are still checked against their own model's tier limit at request time, so
    the meter never over-promises.
    """
    try:
        quotas.seed_quota_rules()
        snapshot = quotas.remaining(user_id, "free")
    except Exception:  # noqa: BLE001 - a meter must never break the picker
        snapshot = {
            "requests_used": 0,
            "requests_limit": None,
            "tokens_used": 0,
            "tokens_limit": None,
        }
    requests_limit = snapshot.get("requests_limit")
    requests_used = snapshot.get("requests_used") or 0
    return {
        "requestsUsed": requests_used,
        "requestsLimit": requests_limit,
        "tokensUsed": snapshot.get("tokens_used") or 0,
        "tokensLimit": snapshot.get("tokens_limit"),
        # Percentage is computed once here so the JS never divides by null.
        "percent": (
            round(min(100, (requests_used / requests_limit) * 100))
            if requests_limit else None
        ),
    }
