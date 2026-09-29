"""Circuit breaker + provider health for the AI Gateway.

State per provider lives in ``ai_provider_health``:
  * closed    — healthy, calls pass through.
  * open      — tripped after N consecutive failures; calls are short-circuited
                (the gateway skips this provider) until ``circuit_open_seconds``
                elapses.
  * half_open — one trial call is allowed through; success closes the circuit,
                failure re-opens it.

This keeps one flaky/down provider from stalling every student request: the
gateway's fallback chain moves to the next healthy model instead.
"""

from datetime import datetime, timezone

import db

from .config import GatewayConfig

_STATE_CLOSED = "closed"
_STATE_OPEN = "open"
_STATE_HALF_OPEN = "half_open"


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse(ts):
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def get_state(provider_id):
    """Return {state, failures} for a provider (defaults to closed)."""
    conn = db._conn_context()
    try:
        row = conn.execute(
            "SELECT state, failures, opened_at FROM ai_provider_health "
            "WHERE provider_id = ?",
            (provider_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {"state": _STATE_CLOSED, "failures": 0}
    return {"state": row["state"], "failures": row["failures"]}


def is_available(provider_id):
    """True when a call may be attempted now (closed, or open-but-cooled-down)."""
    conn = db._conn_context()
    try:
        row = conn.execute(
            "SELECT state, opened_at FROM ai_provider_health WHERE provider_id = ?",
            (provider_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row or row["state"] != _STATE_OPEN:
        return True
    opened = _parse(row["opened_at"])
    if opened is None:
        return True
    cooled = (datetime.now(timezone.utc) - opened).total_seconds()
    return cooled >= GatewayConfig.circuit_open_seconds


def record_success(provider_id):
    """Reset the breaker to closed after a successful call."""
    _upsert_state(provider_id, _STATE_CLOSED, 0)


def record_failure(provider_id, error=""):
    """Count a failure and trip the breaker once the threshold is reached."""
    conn = db._conn_context()
    try:
        row = conn.execute(
            "SELECT state, failures FROM ai_provider_health WHERE provider_id = ?",
            (provider_id,),
        ).fetchone()
        failures = (row["failures"] + 1) if row else 1
        state = row["state"] if row else _STATE_CLOSED
        # A failure in half_open re-opens immediately; otherwise only trip once
        # the consecutive-failure threshold is met.
        if state == _STATE_HALF_OPEN or failures >= GatewayConfig.circuit_failure_threshold:
            state = _STATE_OPEN
        conn.execute(
            "INSERT INTO ai_provider_health "
            "(provider_id, state, failures, opened_at, last_error) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(provider_id) DO UPDATE SET state = excluded.state, "
            "failures = excluded.failures, opened_at = excluded.opened_at, "
            "last_error = excluded.last_error",
            (provider_id, state, failures, _now() if state == _STATE_OPEN else None,
             error[:500]),
        )
        conn.commit()
    finally:
        conn.close()


def _upsert_state(provider_id, state, failures):
    conn = db._conn_context()
    try:
        conn.execute(
            "INSERT INTO ai_provider_health "
            "(provider_id, state, failures, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(provider_id) DO UPDATE SET state = excluded.state, "
            "failures = excluded.failures, updated_at = excluded.updated_at",
            (provider_id, state, failures, _now()),
        )
        conn.commit()
    finally:
        conn.close()