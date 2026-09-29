"""Registry access for providers and models behind the AI Gateway.

Two sources of truth feed this module:
  * ``models_seed.PROVIDERS`` / ``models_seed.MODELS`` (defaults, applied by the
    idempotent ``ensure_seeded()``);
  * the ``ai_providers`` / ``ai_models`` tables (admin edits override seeds).

``ensure_seeded()`` runs lazily on first access so a brand-new database works
immediately, and on every app start the schema migration hook guarantees the
tables exist. Seed rows are UPSERTs, so admin edits are preserved across
restarts.
"""

from . import models_seed
from .config import GatewayConfig

#: Cached copy of the seed rows keyed by a registry signature is unnecessary —
#: the DB is small and reads go through one connection helper. We keep a
#: ``_seeded`` flag purely to avoid re-running the UPSERTs on every call.
_seeded = False


def ensure_seeded():
    """Idempotently apply the default provider/model registry."""
    global _seeded
    if _seeded:
        return
    import db

    with db._conn_context() as conn:
        for p in models_seed.PROVIDERS:
            # Values shared between INSERT and the conflict update are put in
            # a single params dict and referenced by name, so SQLite and
            # Postgres both accept the UPSERT.
            conn.execute(
                """
                INSERT INTO ai_providers
                (slug, display_name, adapter, base_url, env_key_name,
                 is_enabled, is_free_tier, may_train_on_data, allowed_for_minors,
                 sort_order)
                VALUES (:slug, :display_name, :adapter, :base_url, :env_key_name,
                        :is_enabled, :is_free_tier, :may_train_on_data,
                        :allowed_for_minors, :sort_order)
                ON CONFLICT(slug) DO UPDATE SET
                    display_name = excluded.display_name,
                    adapter = excluded.adapter,
                    base_url = excluded.base_url,
                    env_key_name = excluded.env_key_name,
                    is_enabled = excluded.is_enabled,
                    is_free_tier = excluded.is_free_tier,
                    may_train_on_data = excluded.may_train_on_data,
                    allowed_for_minors = excluded.allowed_for_minors,
                    sort_order = excluded.sort_order
                """,
                p,
            )
        for m in models_seed.MODELS:
            conn.execute(
                """
                INSERT INTO ai_models
                (provider_id, model_id, display_name, tier, context_window,
                 max_output, supports_json_mode, speed, cost_in_per_million,
                 cost_out_per_million, best_for, sort_order)
                VALUES (:provider_id, :model_id, :display_name, :tier,
                        :context_window, :max_output, :supports_json_mode,
                        :speed, :cost_in_per_million, :cost_out_per_million,
                        :best_for, :sort_order)
                ON CONFLICT(provider_id, model_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    tier = excluded.tier,
                    context_window = excluded.context_window,
                    max_output = excluded.max_output,
                    supports_json_mode = excluded.supports_json_mode,
                    speed = excluded.speed,
                    cost_in_per_million = excluded.cost_in_per_million,
                    cost_out_per_million = excluded.cost_out_per_million,
                    best_for = excluded.best_for,
                    sort_order = excluded.sort_order
                """,
                m,
            )
        conn.commit()
    _seeded = True


def _rows(sql, params=()):
    import db

    with db._conn_context() as conn:
        return conn.execute(sql, params).fetchall()


def _row(sql, params=()):
    import db

    with db._conn_context() as conn:
        return conn.execute(sql, params).fetchone()


# --------------------------------------------------------------- providers

def list_providers(enabled_only=True):
    """Return provider rows as dicts, newest last, with key availability."""
    ensure_seeded()
    where = "WHERE 1=1" if not enabled_only else "WHERE is_enabled = 1"
    rows = _rows(
        "SELECT p.*, "
        " (SELECT COUNT(*) FROM ai_models m WHERE m.provider_id = p.slug) AS model_count "
        f"FROM ai_providers p {where} ORDER BY p.sort_order, p.display_name"
    )
    out = []
    for r in rows:
        d = dict(r)
        d["has_server_key"] = _provider_has_server_key(d)
        out.append(d)
    return out


def list_models(enabled_models=True):
    """Return model rows joined with provider info for pickers."""
    ensure_seeded()
    where = "WHERE m.is_enabled = 1" if enabled_models else ""
    rows = _rows(
        "SELECT m.*, p.slug AS provider_slug, p.display_name AS provider_name, "
        "p.adapter AS adapter, p.base_url AS base_url, "
        "p.is_free_tier AS provider_is_free_tier, "
        "p.allowed_for_minors AS provider_allowed_for_minors "
        f"FROM ai_models m JOIN ai_providers p ON p.slug = m.provider_id "
        f"{where} ORDER BY p.sort_order, m.sort_order, m.model_id"
    )
    return [dict(r) for r in rows]


def get_provider(slug):
    """Return a single provider row (dict) or None."""
    ensure_seeded()
    r = _row("SELECT * FROM ai_providers WHERE slug = ?", (slug,))
    if r is None:
        return None
    d = dict(r)
    d["has_server_key"] = _provider_has_server_key(d)
    return d


def get_model(provider_slug, model_id):
    """Return a single model row joined with provider info, or None."""
    ensure_seeded()
    r = _row(
        "SELECT m.*, p.slug AS provider_slug, p.display_name AS provider_name, "
        "p.adapter AS adapter, p.base_url AS base_url, "
        "p.is_free_tier AS provider_is_free_tier, "
        "p.env_key_name AS provider_env_key_name, "
        "p.allowed_for_minors AS provider_allowed_for_minors "
        "FROM ai_models m JOIN ai_providers p ON p.slug = m.provider_id "
        "WHERE m.provider_id = ? AND m.model_id = ?",
        (provider_slug, model_id),
    )
    return dict(r) if r else None


# -------------------------------------------------------------- key checks

def _provider_has_server_key(provider):
    """True when the server holds an API key for this provider."""
    name = provider.get("env_key_name") or ""
    if not name:
        return False
    # Legacy alias support (the old `GOOGLE_AI_API_KEY` name still works).
    if name in models_seed.ENV_ALIASES:
        if getattr(GatewayConfig, "server_keys", {}).get(name):
            return True
        return bool(getattr(GatewayConfig, "server_keys", {}).get(models_seed.ENV_ALIASES[name]))
    return bool(getattr(GatewayConfig, "server_keys", {}).get(name))


def provider_key_slug(provider):
    """Return the canonical env var name holding the server key (incl. aliases)."""
    name = provider.get("env_key_name") or ""
    if name and name in models_seed.ENV_ALIASES:
        alias = models_seed.ENV_ALIASES[name]
        if getattr(GatewayConfig, "server_keys", {}).get(alias) and not getattr(
            GatewayConfig, "server_keys", {}
        ).get(name):
            return alias
    return name