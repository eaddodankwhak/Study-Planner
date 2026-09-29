"""Admin console API for the AI Gateway (Phase 6).

Reads and edits the provider/model registry, the daily quota rules and the
circuit-breaker health state. The whole surface is gated by an email
allow-list (``AI_GATEWAY_ADMIN_EMAILS``): it is empty by default, so no account
is an admin until a deployment explicitly names one, and a misconfigured
deployment fails closed rather than open.

Nothing here is reachable from the student surface; the student API
(``ai_gateway/api.py``) deliberately reports friendly names only. This module
is the one place an operator can see an env var name or flip a safety flag, and
it says so with the ``warnings`` it attaches to a training provider.
"""

import db
from flask import Blueprint, jsonify, request, session

from . import health, quotas, registry
from .config import is_admin_email

admin_api = Blueprint("ai_gateway_admin", __name__, url_prefix="/api/ai-gateway/admin")


@admin_api.before_request
def _require_admin():
    """Fail closed: no session -> 401, no allow-listed email -> 403."""
    if not session.get("user_id"):
        return jsonify({"error": "unauthorized"}), 401
    user = db.get_user(session["user_id"]) or {}
    if not is_admin_email(user.get("email")):
        return jsonify({"error": "forbidden"}), 403


# --------------------------------------------------------------- serialisers

def _provider_payload(provider):
    """Operator view of one provider, including its health and safety warnings."""
    state = health.get_state(provider["slug"])
    warnings = []
    if provider.get("may_train_on_data"):
        warnings.append("This provider may train on the content students send it.")
        if provider.get("allowed_for_minors"):
            warnings.append("It may train on data and is currently allowed for minors.")
    return {
        "slug": provider["slug"],
        "name": provider["display_name"],
        "adapter": provider["adapter"],
        "baseUrl": provider.get("base_url"),
        "envKeyName": provider.get("env_key_name"),
        "hasServerKey": bool(provider.get("has_server_key")),
        "isEnabled": bool(provider.get("is_enabled")),
        "isFreeTier": bool(provider.get("is_free_tier")),
        "mayTrainOnData": bool(provider.get("may_train_on_data")),
        "allowedForMinors": bool(provider.get("allowed_for_minors")),
        "sortOrder": provider.get("sort_order"),
        "modelCount": provider.get("model_count", 0),
        "health": state,
        "warnings": warnings,
    }


def _model_payload(model):
    return {
        "key": registry.model_key(model["provider_slug"], model["model_id"]),
        "provider": model["provider_slug"],
        "providerName": model.get("provider_name"),
        "modelId": model["model_id"],
        "name": model["display_name"],
        "tier": model["tier"],
        "bestFor": model.get("best_for"),
        "contextWindow": model.get("context_window"),
        "maxOutput": model.get("max_output"),
        "supportsJsonMode": bool(model.get("supports_json_mode")),
        "speed": model.get("speed"),
        "isEnabled": bool(model.get("is_enabled")),
    }


def _health_payload(provider):
    state = health.get_state(provider["slug"])
    return {
        "provider": provider["slug"],
        "name": provider["display_name"],
        "isEnabled": bool(provider.get("is_enabled")),
        "hasServerKey": bool(provider.get("has_server_key")),
        "state": state["state"],
        "failures": state["failures"],
    }


# ---------------------------------------------------------------- validation

def _provider_fields(payload):
    """Translate a provider PATCH body to DB columns, or None if invalid."""
    fields = {}
    for key, column in (
        ("isEnabled", "is_enabled"),
        ("mayTrainOnData", "may_train_on_data"),
        ("allowedForMinors", "allowed_for_minors"),
    ):
        if key in payload:
            value = payload[key]
            if not isinstance(value, bool):
                return None
            fields[column] = 1 if value else 0
    if "displayName" in payload:
        name = (payload["displayName"] or "").strip()
        if not name:
            return None
        fields["display_name"] = name
    if "baseUrl" in payload:
        url = (payload["baseUrl"] or "").strip()
        if url and not url.lower().startswith(("http://", "https://")):
            return None
        fields["base_url"] = url or None
    return fields


def _model_fields(payload):
    """Translate a model PATCH body to DB columns, or None if invalid."""
    fields = {}
    if "isEnabled" in payload:
        value = payload["isEnabled"]
        if not isinstance(value, bool):
            return None
        fields["is_enabled"] = 1 if value else 0
    if "supportsJsonMode" in payload:
        value = payload["supportsJsonMode"]
        if not isinstance(value, bool):
            return None
        fields["supports_json_mode"] = 1 if value else 0
    if "displayName" in payload:
        name = (payload["displayName"] or "").strip()
        if not name:
            return None
        fields["display_name"] = name
    if "tier" in payload:
        tier = (payload["tier"] or "").strip()
        if tier not in quotas.VALID_TIERS:
            return None
        fields["tier"] = tier
    return fields


def _int_or_none(value):
    """Parse an optional non-negative integer; raises ValueError if malformed."""
    if value is None or value == "":
        return None
    parsed = int(value)
    if parsed < 0:
        raise ValueError("negative")
    return parsed


# -------------------------------------------------------------------- routes

@admin_api.get("/providers")
def list_providers():
    """Every provider (enabled or not) with health and safety warnings."""
    registry.ensure_seeded()
    providers = registry.list_providers(enabled_only=False)
    return jsonify({"providers": [_provider_payload(p) for p in providers]})


@admin_api.patch("/providers/<slug>")
def update_provider(slug):
    payload = request.get_json(silent=True) or {}
    fields = _provider_fields(payload)
    if fields is None:
        return jsonify({"error": "invalid_field"}), 400
    row = registry.update_provider(slug, fields)
    if row is None:
        return jsonify({"error": "unknown_provider"}), 404
    if fields:
        db.log_audit(
            session["user_id"], "ai_gateway_provider_update",
            f"{slug}: {', '.join(sorted(fields))}",
        )
    return jsonify({"provider": _provider_payload(row)})


@admin_api.get("/models")
def list_models():
    """Every model (enabled or not) for the admin table."""
    registry.ensure_seeded()
    models = registry.list_models(enabled_models=False)
    return jsonify({"models": [_model_payload(m) for m in models]})


@admin_api.patch("/models/<provider_slug>/<path:model_id>")
def update_model(provider_slug, model_id):
    payload = request.get_json(silent=True) or {}
    fields = _model_fields(payload)
    if fields is None:
        return jsonify({"error": "invalid_field"}), 400
    row = registry.update_model(provider_slug, model_id, fields)
    if row is None:
        return jsonify({"error": "unknown_model"}), 404
    if fields:
        db.log_audit(
            session["user_id"], "ai_gateway_model_update",
            f"{provider_slug}/{model_id}: {', '.join(sorted(fields))}",
        )
    return jsonify({"model": _model_payload(row)})


@admin_api.get("/quotas")
def list_quotas():
    """The daily request/token rules that gate server-funded requests."""
    return jsonify({"rules": quotas.list_rules(), "tiers": list(quotas.VALID_TIERS)})


@admin_api.put("/quotas/<tier>/<user_group>")
def update_quota(tier, user_group):
    payload = request.get_json(silent=True) or {}
    try:
        reqs = _int_or_none(payload.get("dailyRequests"))
        docs = _int_or_none(payload.get("dailyDocuments"))
        toks = _int_or_none(payload.get("dailyTokens"))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid_number"}), 400
    row = quotas.set_rule(tier, user_group, reqs, docs, toks)
    if row is None:
        return jsonify({"error": "invalid_rule"}), 400
    db.log_audit(
        session["user_id"], "ai_gateway_quota_update",
        f"{tier}/{user_group}: requests={row['daily_requests']} "
        f"tokens={row['daily_tokens']}",
    )
    return jsonify({"rule": row})


@admin_api.get("/health")
def list_health():
    """Circuit-breaker state per provider, for the health panel."""
    registry.ensure_seeded()
    providers = registry.list_providers(enabled_only=False)
    return jsonify({"health": [_health_payload(p) for p in providers]})


@admin_api.post("/health/<slug>/reset")
def reset_health(slug):
    """Close a tripped circuit manually after a provider has been fixed."""
    if registry.get_provider(slug) is None:
        return jsonify({"error": "unknown_provider"}), 404
    health.record_success(slug)
    db.log_audit(session["user_id"], "ai_gateway_health_reset", slug)
    return jsonify({"provider": _health_payload(registry.get_provider(slug))})
