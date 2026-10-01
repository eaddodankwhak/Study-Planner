"""Per-user daily quota enforcement (server-funded requests only).

Rules live in the ``ai_quota_rules`` table keyed by (tier, user_group), so an
admin can tune limits later without a code change. ``seed_quota_rules()``
installs safe defaults idempotently. A personal (BYOK) key never consumes the
server's budget — only server-funded requests count, mirroring the Stash rule.
"""

import uuid
from datetime import datetime, timezone

import db

from .config import GatewayConfig
from .errors import QuotaExceededError

#: Default per-day limits used when no explicit rule row exists. Mirrors the
#: env-configurable GatewayConfig values so a fresh DB is sensible.
_DEFAULTS = {
    "free": (GatewayConfig.free_daily_requests, None, 200_000),
    "standard": (GatewayConfig.standard_daily_requests, None, 200_000),
    "premium": (GatewayConfig.premium_daily_requests, None, 400_000),
}


def _day():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def seed_quota_rules():
    """Insert the default quota rules for students, once.

    Every column here is an admin-controlled limit, so the conflict action is
    deliberately ``DO NOTHING``: tuning a limit in the admin panel must not be
    undone by the next restart or by the usage meter polling this function.
    """
    conn = db._conn_context()
    try:
        for tier, (reqs, docs, tokens) in _DEFAULTS.items():
            conn.execute(
                "INSERT INTO ai_quota_rules "
                "(id, tier, user_group, daily_requests, daily_documents, daily_tokens) "
                "VALUES (?, ?, 'students', ?, ?, ?) "
                "ON CONFLICT(tier, user_group) DO NOTHING",
                (uuid.uuid4().hex, tier, reqs, docs or 0, tokens),
            )
        conn.commit()
    finally:
        conn.close()


def _rule_for(tier, user_group):
    conn = db._conn_context()
    try:
        row = conn.execute(
            "SELECT daily_requests, daily_tokens FROM ai_quota_rules "
            "WHERE tier = ? AND user_group = ?",
            (tier, user_group),
        ).fetchone()
    finally:
        conn.close()
    if row:
        return row["daily_requests"], row["daily_tokens"]
    reqs, _docs, tokens = _DEFAULTS.get(tier, _DEFAULTS["free"])
    return reqs, tokens


def _user_group(user_id):
    try:
        user = db.get_user(user_id) or {}
    except Exception:  # noqa: BLE001 - unknown user -> students bucket
        return "students"
    return user.get("user_group") or "students"


def usage_today(user_id):
    """Return {requests, input_tokens, output_tokens} for today (server key)."""
    conn = db._conn_context()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(requests),0) AS reqs, "
            "COALESCE(SUM(input_tokens),0) AS it, "
            "COALESCE(SUM(output_tokens),0) AS ot "
            "FROM ai_gateway_usage WHERE user_id = ? AND day = ? AND used_own_key = 0",
            (user_id, _day()),
        ).fetchone()
    finally:
        conn.close()
    return {
        "requests": int(row["reqs"]),
        "input_tokens": int(row["it"]),
        "output_tokens": int(row["ot"]),
        "total_tokens": int(row["it"]) + int(row["ot"]),
    }


def check(user_id, tier, paid_by, estimated_tokens=0):
    """Raise QuotaExceededError when the server-funded budget is spent.

    paid_by == "personal" (BYOK) is always allowed. Otherwise compare today's
    server-funded usage against the (tier, group) rule with a small headroom
    allowance for the in-flight request.
    """
    if paid_by == "personal":
        return
    group = _user_group(user_id)
    max_requests, max_tokens = _rule_for(tier, group)
    used = usage_today(user_id)

    if max_requests and used["requests"] >= max_requests:
        raise QuotaExceededError(
            "You have used today's AI request limit. New requests resume "
            "tomorrow, or you can add your own AI key in Settings → AI "
            "Providers."
        )
    if max_tokens and (used["total_tokens"] + estimated_tokens) > max_tokens:
        raise QuotaExceededError(
            "You have used today's AI token limit. New requests resume "
            "tomorrow, or you can add your own AI key in Settings → AI "
            "Providers."
        )


def remaining(user_id, tier):
    """Return a small dict for the usage meter UI (Phase 4)."""
    group = _user_group(user_id)
    max_requests, max_tokens = _rule_for(tier, group)
    used = usage_today(user_id)
    return {
        "requests_used": used["requests"],
        "requests_limit": max_requests,
        "tokens_used": used["total_tokens"],
        "tokens_limit": max_tokens,
    }


# ---------------------------------------------------------------- admin rules

#: Tiers a rule may target. Guards against a typo creating a rule for a tier
#: the gateway never looks up, which would look saved but change nothing.
VALID_TIERS = ("free", "standard", "premium")


def _rule_row(tier, user_group):
    conn = db._conn_context()
    try:
        row = conn.execute(
            "SELECT tier, user_group, daily_requests, daily_documents, daily_tokens "
            "FROM ai_quota_rules WHERE tier = ? AND user_group = ?",
            (tier, user_group),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def list_rules():
    """Every quota rule as a list of dicts (admin console)."""
    seed_quota_rules()
    conn = db._conn_context()
    try:
        rows = conn.execute(
            "SELECT tier, user_group, daily_requests, daily_documents, daily_tokens "
            "FROM ai_quota_rules ORDER BY user_group, tier"
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def set_rule(tier, user_group, daily_requests=None, daily_documents=None,
             daily_tokens=None):
    """Upsert one (tier, group) rule; None when the tier/group is invalid.

    Omitted limits keep their current value so a partial edit cannot wipe a
    limit the admin did not touch. Unlike ``seed_quota_rules`` this is an
    explicit admin write, so the conflict action is DO UPDATE.
    """
    if tier not in VALID_TIERS or not user_group:
        return None
    seed_quota_rules()
    existing = _rule_row(tier, user_group)
    if existing is None:
        defaults = _DEFAULTS.get(tier, _DEFAULTS["free"])
        existing = {
            "daily_requests": defaults[0],
            "daily_documents": defaults[1] or 0,
            "daily_tokens": defaults[2],
        }
    reqs = existing["daily_requests"] if daily_requests is None else daily_requests
    docs = existing["daily_documents"] if daily_documents is None else daily_documents
    toks = existing["daily_tokens"] if daily_tokens is None else daily_tokens
    conn = db._conn_context()
    try:
        conn.execute(
            "INSERT INTO ai_quota_rules "
            "(id, tier, user_group, daily_requests, daily_documents, daily_tokens) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(tier, user_group) DO UPDATE SET "
            "daily_requests = excluded.daily_requests, "
            "daily_documents = excluded.daily_documents, "
            "daily_tokens = excluded.daily_tokens",
            (uuid.uuid4().hex, tier, user_group, reqs, docs, toks),
        )
        conn.commit()
    finally:
        conn.close()
    return _rule_row(tier, user_group)