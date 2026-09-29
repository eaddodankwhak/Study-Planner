"""Circuit breaker + provider health for the AI Gateway.

State per provider lives in ``ai_provider_health``:
  * closed    — healthy, calls pass through.
  * open      — tripped after N consecutive failures; calls are short-circuited
                (the gateway skips this provider) until ``circuit_open_seconds``
                elapses.
  * half_open — exactly one trial call is claimed; success closes the circuit,
                failure re-opens it.

This keeps one flaky/down provider from stalling every student request: the
gateway's fallback chain moves to the next healthy model instead.

Concurrency matters more than it looks. Every student request in a busy period
hits this table at once, so the two things that must not be approximate are:

  * the trial call — if ``is_available()`` merely said "yes, the cooldown
    elapsed", every in-flight request would stampede a provider we already know
    is down. :func:`try_claim` instead performs a conditional UPDATE whose
    ``WHERE state = 'open'`` predicate makes exactly one caller the winner.
  * the failure counter — a read-then-write loses increments when two failures
    land together, which delays tripping the breaker. The increment is done as
    a single ``failures = failures + 1`` UPDATE and the row is re-read after.
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


def _row(provider_id):
    conn = db._conn_context()
    try:
        return conn.execute(
            "SELECT state, failures, opened_at FROM ai_provider_health "
            "WHERE provider_id = ?",
            (provider_id,),
        ).fetchone()
    finally:
        conn.close()


def get_state(provider_id):
    """Return {state, failures} for a provider (defaults to closed)."""
    row = _row(provider_id)
    if not row:
        return {"state": _STATE_CLOSED, "failures": 0}
    return {"state": row["state"], "failures": row["failures"]}


def is_available(provider_id):
    """True when a call *could* be attempted on this provider right now.

    This is the read-only predicate used when ranking models, so it must never
    mutate state and must not be treated as a reservation — call
    :func:`try_claim` immediately before the real call to actually win a slot.
    """
    row = _row(provider_id)
    if not row or row["state"] == _STATE_CLOSED:
        # No row yet means the provider has never failed: treat as closed.
        return True
    if row["state"] == _STATE_HALF_OPEN:
        # A trial is in flight. Reporting it as available would let the rest of
        # the fleet queue up behind a single probe.
        return False
    opened = _parse(row["opened_at"])
    if opened is None:
        return True
    cooled = (datetime.now(timezone.utc) - opened).total_seconds()
    return cooled >= GatewayConfig.circuit_open_seconds


def try_claim(provider_id):
    """Atomically reserve the right to make one call to this provider.

    Returns True for the single caller that wins the half_open trial and for
    any caller while the circuit is simply closed. Returns False when the
    circuit is open-and-cooling, or when another request already holds the
    trial, so at most one request probes a recovered provider at a time.
    """
    row = _row(provider_id)
    if not row or row["state"] == _STATE_CLOSED:
        return True
    if row["state"] == _STATE_HALF_OPEN:
        return False  # a trial is already in flight

    opened = _parse(row["opened_at"])
    if opened is not None:
        cooled = (datetime.now(timezone.utc) - opened).total_seconds()
        if cooled < GatewayConfig.circuit_open_seconds:
            return False

    # The cooldown has elapsed: transition open -> half_open. The `state` in the
    # WHERE clause is what makes this a compare-and-swap — the first writer
    # moves the row off 'open', so every competing UPDATE matches zero rows and
    # knows it lost the race.
    conn = db._conn_context()
    try:
        cursor = conn.execute(
            "UPDATE ai_provider_health SET state = ?, updated_at = ? "
            "WHERE provider_id = ? AND state = ?",
            (_STATE_HALF_OPEN, _now(), provider_id, _STATE_OPEN),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def record_success(provider_id):
    """Reset the breaker to closed after a successful call.

    ``opened_at`` and ``last_error`` are cleared as well: leaving them behind
    would make an admin looking at the health table think a provider is still
    tripped, and would leave a stale timestamp to be read by is_available().
    """
    conn = db._conn_context()
    try:
        conn.execute(
            "INSERT INTO ai_provider_health "
            "(provider_id, state, failures, updated_at) VALUES (?, ?, 0, ?) "
            "ON CONFLICT(provider_id) DO UPDATE SET state = excluded.state, "
            "failures = 0, opened_at = NULL, last_error = NULL, "
            "updated_at = excluded.updated_at",
            (provider_id, _STATE_CLOSED, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def record_failure(provider_id, error=""):
    """Count a failure and trip the breaker once the threshold is reached.

    Written as three statements rather than one read-modify-write:

    1. ``failures = failures + 1`` is an atomic increment, so simultaneous
       failures cannot overwrite each other and delay the trip. If the row does
       not exist yet, rowcount is 0 and it is inserted with a count of 1.
    2. Re-read to decide the state. A half_open trial that failed re-opens
       immediately — the probe just failed, so the provider is still down.
    3. Write the state *only* when opening. There is deliberately no
       "keep closed" write, so a slow reader can never clobber an open circuit
       that another thread has already tripped.
    """
    conn = db._conn_context()
    try:
        now = _now()
        cursor = conn.execute(
            "UPDATE ai_provider_health SET failures = failures + 1, updated_at = ? "
            "WHERE provider_id = ?",
            (now, provider_id),
        )
        if cursor.rowcount == 0:
            conn.execute(
                "INSERT INTO ai_provider_health "
                "(provider_id, state, failures, updated_at) VALUES (?, ?, 1, ?)",
                (provider_id, _STATE_CLOSED, now),
            )
        row = conn.execute(
            "SELECT state, failures FROM ai_provider_health WHERE provider_id = ?",
            (provider_id,),
        ).fetchone()
        if row["state"] == _STATE_HALF_OPEN or (
            row["failures"] >= GatewayConfig.circuit_failure_threshold
        ):
            conn.execute(
                "UPDATE ai_provider_health SET state = ?, opened_at = ?, "
                "last_error = ?, updated_at = ? WHERE provider_id = ?",
                (_STATE_OPEN, now, error[:500], now, provider_id),
            )
        conn.commit()
    finally:
        conn.close()
