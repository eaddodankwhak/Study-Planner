"""Activity log writer.

Every mutating collaboration path must call log_activity() in the SAME
transaction as the underlying change (pass the shared connection). This is the
"I did my part" accountability feature: an immutable, append-only audit trail.
"""

import json

import db


def log_activity(conn, workspace_id, actor_id, event_type, **payload):
    """Record an activity event inside the caller's transaction (shared conn)."""
    return db.collab_log_activity(
        conn,
        workspace_id,
        actor_id,
        event_type,
        json.dumps(payload) if payload else None,
    )