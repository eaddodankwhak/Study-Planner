"""Shared-content response cache (document card generation only).

The cache is keyed by a hash of *shared* document content + the prompt version
so two students uploading the same textbook get the second card set for free.
Per the build prompt it MUST NOT be used for personal text (notes, chat
messages, saved cards) — callers pass a key they compute only from document
text; the module never builds a key from a user id or a user-supplied field on
its own. It is TTL'd and per-user row-capped via a global prune.
"""

import hashlib
import json
import uuid
from datetime import datetime, timezone

import db

from .config import GatewayConfig


def make_key(*parts):
    """Stable cache key from shared content parts (never user-identifying)."""
    joined = "\x1f".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def get(cache_key):
    """Return the cached payload dict, or None when absent/expired."""
    if not GatewayConfig.cache_enabled or not cache_key:
        return None
    conn = db._conn_context()
    try:
        row = conn.execute(
            "SELECT payload, created_at FROM ai_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if not row:
            return None
        # Simple TTL check against created_at (SQLite/PG string timestamps).
        from datetime import datetime as _dt

        try:
            created = _dt.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            created = None
        if created is not None:
            age = (datetime.now(timezone.utc) - created.replace(tzinfo=timezone.utc)).total_seconds()
            if age > GatewayConfig.cache_ttl_seconds:
                conn.execute("DELETE FROM ai_cache WHERE cache_key = ?", (cache_key,))
                conn.commit()
                return None
        conn.execute(
            "UPDATE ai_cache SET hits = hits + 1 WHERE cache_key = ?", (cache_key,)
        )
        conn.commit()
        return json.loads(row["payload"])
    finally:
        conn.close()


def set(cache_key, payload, model_id=None):  # noqa: A001 - mirrors dict API
    """Store a payload for a shared-content key; returns True on success."""
    if not GatewayConfig.cache_enabled or not cache_key:
        return False
    conn = db._conn_context()
    try:
        conn.execute(
            "INSERT INTO ai_cache (id, cache_key, payload, model_id) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(cache_key) DO UPDATE SET payload = excluded.payload, "
            "model_id = excluded.model_id",
            (uuid.uuid4().hex, cache_key, json.dumps(payload), model_id),
        )
        conn.commit()
    except Exception:  # noqa: BLE001 - cache is best-effort, never fatal
        return False
    finally:
        conn.close()
    _prune()
    return True


def _prune():
    """Keep the cache from growing without bound (oldest-first eviction)."""
    cap = GatewayConfig.cache_max_rows_per_user
    if cap <= 0:
        return
    conn = db._conn_context()
    try:
        total = conn.execute("SELECT COUNT(*) AS n FROM ai_cache").fetchone()["n"]
        if total > cap:
            conn.execute(
                "DELETE FROM ai_cache WHERE cache_key IN ("
                "  SELECT cache_key FROM ai_cache ORDER BY created_at ASC LIMIT ?"
                ")",
                (total - cap,),
            )
            conn.commit()
    except Exception:  # noqa: BLE001 - best effort
        return
    finally:
        conn.close()