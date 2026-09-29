"""Registry access for providers and models behind the AI Gateway.

Two sources of truth feed this module:
  * ``models_seed.PROVIDERS`` / ``models_seed.MODELS`` (defaults, applied by the
    idempotent ``ensure_seeded()``);
  * the ``ai_providers`` / ``ai_models`` tables (admin edits override seeds).

``ensure_seeded()`` runs lazily on first access so a brand-new database works
immediately, and on every app start the schema migration hook guarantees the
tables exist. Seed rows are UPSERTs that only refresh display metadata, so
admin edits to the safety and enablement columns survive restarts.
"""

import uuid

from . import models_seed
from .config import GatewayConfig

#: Cached copy of the seed rows keyed by a registry signature is unnecessary —
#: the DB is small and reads go through one connection helper. We keep a
#: ``_seeded`` flag purely to avoid re-running the UPSERTs on every call.
_seeded = False


def ensure_seeded():
    """Idempotently apply the default provider/model registry.

    Seed rows are inserted once and never fight the admin panel afterwards:
    the UPSERT refreshes only display metadata (names, sort order, prices) and
    deliberately leaves the admin-controlled columns alone, so flipping
    ``is_enabled``, ``may_train_on_data``, ``allowed_for_minors`` or repointing
    a ``base_url`` in the admin UI sticks across restarts.
    """
    global _seeded
    if _seeded:
        return
    import db

    with db._conn_context() as conn:
        for p in models_seed.PROVIDERS:
            # Values shared between INSERT and the conflict update are put in
            # a single params dict and referenced by name, so SQLite and
            # Postgres both accept the UPSERT. `id` is generated per insert:
            # both tables have a TEXT primary key, and Postgres (unlike SQLite)
            # rejects a NULL there, so the seed must always supply one.
            params = dict(p, id=uuid.uuid4().hex)
            conn.execute(
                """
                INSERT INTO ai_providers
                (id, slug, display_name, adapter, base_url, env_key_name,
                 is_enabled, is_free_tier, may_train_on_data, allowed_for_minors,
                 sort_order)
                VALUES (:id, :slug, :display_name, :adapter, :base_url, :env_key_name,
                        :is_enabled, :is_free_tier, :may_train_on_data,
                        :allowed_for_minors, :sort_order)
                ON CONFLICT(slug) DO UPDATE SET
                    display_name = excluded.display_name,
                    sort_order = excluded.sort_order
                """,
                params,
            )
        for m in models_seed.MODELS:
            params = dict(m, id=uuid.uuid4().hex)
            conn.execute(
                """
                INSERT INTO ai_models
                (id, provider_id, model_id, display_name, tier, context_window,
                 max_output, supports_json_mode, speed, cost_in_per_million,
                 cost_out_per_million, best_for, sort_order)
                VALUES (:id, :provider_id, :model_id, :display_name, :tier,
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
                params,
            )
        _backfill_missing_ids(conn)
        conn.commit()
    _seeded = True


def _backfill_missing_ids(conn):
    """Give any NULL-id row a primary key.

    An earlier version of the seed omitted ``id``. SQLite quietly accepts a
    NULL in a TEXT PRIMARY KEY (a historical quirk) so those rows exist in
    existing dev databases, and the UPSERT above will not repair them because
    it only touches the display columns. Postgres would have rejected the
    INSERT outright, so this is an upgrade path rather than a hotfix.

    Rows are addressed by their natural key rather than a synthetic rowid so
    this works on Postgres too, and each row gets its own fresh uuid.
    """
    for table, keys in (
        ("ai_providers", ("slug",)),
        ("ai_models", ("provider_id", "model_id")),
    ):
        where = " AND ".join(f"{k} = ?" for k in keys)
        rows = conn.execute(
            f"SELECT {', '.join(keys)} FROM {table} WHERE id IS NULL"
        ).fetchall()
        for row in rows:
            conn.execute(
                f"UPDATE {table} SET id = ? WHERE {where}",
                (uuid.uuid4().hex, *[row[k] for k in keys]),
            )


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


def model_key(provider_slug, model_id):
    """Stable public identifier for a model, e.g. "openai/gpt-4o-mini".

    This is what the browser stores as the student's preference. The row uuid
    is deliberately not used: a public key survives a reseed, and provider
    slugs never contain "/" while model ids often do, so the split is
    unambiguous.
    """
    return f"{provider_slug}/{model_id}"


def get_model_by_key(key):
    """Resolve a model_key() back to a model row, or None if it is gone."""
    if not key or "/" not in key:
        return None
    provider_slug, _, model_id = key.partition("/")
    return get_model(provider_slug, model_id)


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


# ---------------------------------------------------------------- admin writes

#: Columns an admin may edit on a provider. Everything else (slug, adapter,
#: env_key_name) is structural and owned by the seed, so the admin console
#: cannot repoint a provider at an unexpected adapter or key name.
PROVIDER_EDITABLE = {
    "display_name", "base_url", "is_enabled", "may_train_on_data",
    "allowed_for_minors", "sort_order",
}

#: Columns an admin may edit on a model.
MODEL_EDITABLE = {
    "display_name", "tier", "context_window", "max_output", "supports_json_mode",
    "speed", "cost_in_per_million", "cost_out_per_million", "best_for",
    "is_enabled", "sort_order",
}

#: Columns the app compares as truthy/falsey; coerced to INTEGER 0/1 so the
#: same SQL runs on SQLite and Postgres.
_BOOL_COLUMNS = {
    "is_enabled", "may_train_on_data", "allowed_for_minors", "supports_json_mode",
}


def _coerce(column, value):
    if column in _BOOL_COLUMNS:
        return 1 if value in (True, 1, "1", "true", "True") else 0
    return value


def update_provider(slug, fields):
    """Apply whitelisted admin edits to a provider.

    Returns the refreshed row, or None when the slug is unknown so the caller
    can 404 rather than silently creating a provider. A call with no recognised
    fields returns the current row unchanged.
    """
    import db

    ensure_seeded()
    if get_provider(slug) is None:
        return None
    columns = [k for k in fields if k in PROVIDER_EDITABLE]
    if columns:
        assignments = ", ".join(f"{k} = ?" for k in columns)
        values = [_coerce(k, fields[k]) for k in columns]
        conn = db._conn_context()
        try:
            conn.execute(
                f"UPDATE ai_providers SET {assignments}, updated_at = datetime('now') "
                "WHERE slug = ?",
                (*values, slug),
            )
            conn.commit()
        finally:
            conn.close()
    return get_provider(slug)


def update_model(provider_slug, model_id, fields):
    """Apply whitelisted admin edits to a model; None when it does not exist."""
    import db

    ensure_seeded()
    if get_model(provider_slug, model_id) is None:
        return None
    columns = [k for k in fields if k in MODEL_EDITABLE]
    if columns:
        assignments = ", ".join(f"{k} = ?" for k in columns)
        values = [_coerce(k, fields[k]) for k in columns]
        conn = db._conn_context()
        try:
            conn.execute(
                f"UPDATE ai_models SET {assignments} "
                "WHERE provider_id = ? AND model_id = ?",
                (*values, provider_slug, model_id),
            )
            conn.commit()
        finally:
            conn.close()
    return get_model(provider_slug, model_id)