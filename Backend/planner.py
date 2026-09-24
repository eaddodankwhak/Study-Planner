"""Data persistence for the Study Planner planning features.

Stores each student's calendar events, deadlines, and tasks in the database
(planner_events / planner_deadlines / planner_tasks tables) so every backend
keeps the data in one place. In a local SQLite file the rows land in
instance/study_planner.db; with DATABASE_URL set they live in Postgres.
Courses live in the shared courses table via Backend/courses.py.

Entity shapes (returned by the read helpers):

    Event    id,type,title,course_id,start,duration_minutes,recurrence,weekday
    Deadline id,course_id,title,type,due_date,weight,estimated_hours,steps,status
    Task     id,deadline_id,course_id,title,due_date,estimated_minutes,
             priority,status,auto
"""

import json
import uuid
from datetime import datetime, timedelta

import db
import courses

DEADLINE_TYPES = [
    "assignment",
    "quiz",
    "midterm",
    "final",
    "project",
    "presentation",
    "report",
]

EVENT_TYPES = [
    "lecture",
    "tutorial",
    "lab",
    "exam",
    "meeting",
    "other",
]

TASK_PRIORITIES = ["high", "medium", "low"]


# ---------------------------------------------------------------------------
# Whole-document helpers (compat surface used by tests and /export)
# ---------------------------------------------------------------------------

def load_all():
    """Reconstruct the full planner document as a user_id -> block dict.

    Kept for compatibility with callers that still think in terms of the old
    planner.json document (tests, export). By default (empty data) this is {}.
    """
    rows = db._query_all(
        "SELECT DISTINCT user_id FROM planner_events "
        "UNION SELECT DISTINCT user_id FROM planner_deadlines "
        "UNION SELECT DISTINCT user_id FROM planner_tasks"
    )
    doc = {}
    for row in rows:
        uid = row["user_id"]
        events = [e for e in list_events(uid)]
        deadlines = {d["id"]: d for d in list_deadlines(uid)}
        tasks = [t for t in list_tasks(uid)]
        doc[uid] = {
            "courses": {},
            "events": events,
            "deadlines": deadlines,
            "tasks": tasks,
        }
    return doc


def save_all(data):
    """Persist a whole planner document into the planner tables.

    For every user block in ``data`` the planner rows are replaced, so passing
    back a pruned dict removes the dropped users' rows.
    """
    conn = db._conn_context()
    try:
        for user_id, block in (data or {}).items():
            if not isinstance(block, dict):
                continue
            conn.execute("DELETE FROM planner_events WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM planner_deadlines WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM planner_tasks WHERE user_id = ?", (user_id,))
            for ev in block.get("events") or []:
                conn.execute(
                    "INSERT INTO planner_events "
                    "(id, user_id, type, title, course_id, start, "
                    " duration_minutes, recurrence, weekday) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        ev.get("id") or uuid.uuid4().hex,
                        user_id,
                        ev.get("type", "other"),
                        (ev.get("title") or "").strip(),
                        ev.get("course_id"),
                        ev.get("start", ""),
                        int(ev.get("duration_minutes") or 60),
                        ev.get("recurrence", "none"),
                        ev.get("weekday", ""),
                    ),
                )
            for d in (block.get("deadlines") or {}).values():
                conn.execute(
                    "INSERT INTO planner_deadlines "
                    "(id, user_id, course_id, title, type, due_date, weight, "
                    " estimated_hours, steps_json, status) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        d.get("id") or uuid.uuid4().hex,
                        user_id,
                        d.get("course_id"),
                        (d.get("title") or "").strip(),
                        d.get("type", "assignment"),
                        d.get("due_date", ""),
                        float(d.get("weight") or 0),
                        float(d.get("estimated_hours") or 0),
                        json.dumps(list(d.get("steps") or [])),
                        d.get("status", "open"),
                    ),
                )
            for t in block.get("tasks") or []:
                conn.execute(
                    "INSERT INTO planner_tasks "
                    "(id, user_id, deadline_id, course_id, title, due_date, "
                    " estimated_minutes, priority, status, auto) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        t.get("id") or uuid.uuid4().hex,
                        user_id,
                        t.get("deadline_id"),
                        t.get("course_id"),
                        (t.get("title") or "").strip(),
                        t.get("due_date", ""),
                        int(t.get("estimated_minutes") or 60),
                        t.get("priority", "medium"),
                        t.get("status", "todo"),
                        1 if t.get("auto") else 0,
                    ),
                )
        present = set((data or {}).keys())
        for table in ("planner_events", "planner_deadlines", "planner_tasks"):
            uids = conn.execute(
                f"SELECT DISTINCT user_id FROM {table}"
            ).fetchall()
            for row in uids:
                if row["user_id"] not in present:
                    conn.execute(
                        f"DELETE FROM {table} WHERE user_id = ?",
                        (row["user_id"],),
                    )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Courses
# ---------------------------------------------------------------------------

def list_courses(user_id):
    """This user's courses, straight from the shared courses table.

    Courses moved out of planner.json into Backend/courses.py: the dashboard,
    /courses, and onboarding all read the one table, so the views stay in
    agreement.
    """
    return courses.get_user_courses(user_id)


def get_course(user_id, course_id):
    return courses.get_course(user_id, course_id)


def create_course(user_id, **fields):
    """Legacy create-call now goes through the shared upsert path."""
    return courses.upsert_course(user_id, fields.get("code", ""), **fields)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def _event_dict(row):
    d = dict(row)
    d.pop("user_id", None)
    return d


def list_events(user_id):
    rows = db._query_all(
        "SELECT id, type, title, course_id, start, duration_minutes, "
        "recurrence, weekday FROM planner_events WHERE user_id = ? "
        "ORDER BY start",
        (user_id,),
    )
    return [_event_dict(r) for r in rows]


def create_event(user_id, **fields):
    event_id = uuid.uuid4().hex
    event = {
        "id": event_id,
        "type": fields.get("type", "other"),
        "title": fields.get("title", "").strip(),
        "course_id": fields.get("course_id"),
        "start": fields.get("start", ""),
        "duration_minutes": int(fields.get("duration_minutes") or 60),
        "recurrence": fields.get("recurrence", "none"),  # none|weekly
        "weekday": fields.get("weekday", ""),
    }
    db._execute(
        "INSERT INTO planner_events "
        "(id, user_id, type, title, course_id, start, duration_minutes, "
        " recurrence, weekday) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            event_id,
            user_id,
            event["type"],
            event["title"],
            event["course_id"],
            event["start"],
            event["duration_minutes"],
            event["recurrence"],
            event["weekday"],
        ),
    )
    return event


def delete_event(user_id, event_id):
    db._execute(
        "DELETE FROM planner_events WHERE user_id = ? AND id = ?",
        (user_id, event_id),
    )


# ---------------------------------------------------------------------------
# Deadlines
# ---------------------------------------------------------------------------

def _deadline_dict(row):
    d = dict(row)
    d.pop("user_id", None)
    try:
        d["steps"] = json.loads(d.pop("steps_json") or "[]")
    except (TypeError, ValueError):
        d["steps"] = []
    return d


def list_deadlines(user_id):
    rows = db._query_all(
        "SELECT id, user_id, course_id, title, type, due_date, weight, "
        "estimated_hours, steps_json, status FROM planner_deadlines "
        "WHERE user_id = ? ORDER BY due_date",
        (user_id,),
    )
    return [_deadline_dict(r) for r in rows]


def get_deadline(user_id, deadline_id):
    row = db._query_one(
        "SELECT id, user_id, course_id, title, type, due_date, weight, "
        "estimated_hours, steps_json, status FROM planner_deadlines "
        "WHERE user_id = ? AND id = ?",
        (user_id, deadline_id),
    )
    return _deadline_dict(row) if row else None


def create_deadline(user_id, **fields):
    deadline_id = uuid.uuid4().hex
    deadline = {
        "id": deadline_id,
        "course_id": fields.get("course_id"),
        "title": fields.get("title", "").strip(),
        "type": fields.get("type", "assignment"),
        "due_date": fields.get("due_date", ""),
        "weight": float(fields.get("weight") or 0),
        "estimated_hours": float(fields.get("estimated_hours") or 0),
        "steps": [],
        "status": "open",
    }
    db._execute(
        "INSERT INTO planner_deadlines "
        "(id, user_id, course_id, title, type, due_date, weight, "
        " estimated_hours, steps_json, status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            deadline_id,
            user_id,
            deadline["course_id"],
            deadline["title"],
            deadline["type"],
            deadline["due_date"],
            deadline["weight"],
            deadline["estimated_hours"],
            "[]",
            deadline["status"],
        ),
    )
    return deadline


def _deadline_steps(conn, user_id, deadline_id):
    rows = conn.execute(
        "SELECT id FROM planner_tasks WHERE user_id = ? AND deadline_id = ?",
        (user_id, deadline_id),
    ).fetchall()
    return [r["id"] for r in rows]


def _apply_deadline_steps(conn, user_id, deadline_id):
    steps = _deadline_steps(conn, user_id, deadline_id)
    conn.execute(
        "UPDATE planner_deadlines SET steps_json = ? "
        "WHERE user_id = ? AND id = ?",
        (json.dumps(steps), user_id, deadline_id),
    )


# ---------------------------------------------------------------------------
# Tasks (manual lead-up steps tied to a deadline)
# ---------------------------------------------------------------------------

def _task_dict(row):
    d = dict(row)
    d.pop("user_id", None)
    d["auto"] = bool(d["auto"])
    return d


def list_tasks(user_id):
    rows = db._query_all(
        "SELECT id, user_id, deadline_id, course_id, title, due_date, "
        "estimated_minutes, priority, status, auto FROM planner_tasks "
        "WHERE user_id = ? ORDER BY due_date",
        (user_id,),
    )
    return [_task_dict(r) for r in rows]


def get_task(user_id, task_id):
    row = db._query_one(
        "SELECT id, user_id, deadline_id, course_id, title, due_date, "
        "estimated_minutes, priority, status, auto FROM planner_tasks "
        "WHERE user_id = ? AND id = ?",
        (user_id, task_id),
    )
    return _task_dict(row) if row else None


def add_task(user_id, **fields):
    task_id = uuid.uuid4().hex
    task = {
        "id": task_id,
        "deadline_id": fields.get("deadline_id"),
        "course_id": fields.get("course_id"),
        "title": fields.get("title", "").strip(),
        "due_date": fields.get("due_date", ""),
        "estimated_minutes": int(fields.get("estimated_minutes") or 60),
        "priority": fields.get("priority", "medium"),
        "status": "todo",
        "auto": False,
    }
    conn = db._conn_context()
    try:
        conn.execute(
            "INSERT INTO planner_tasks "
            "(id, user_id, deadline_id, course_id, title, due_date, "
            " estimated_minutes, priority, status, auto) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                task_id,
                user_id,
                task["deadline_id"],
                task["course_id"],
                task["title"],
                task["due_date"],
                task["estimated_minutes"],
                task["priority"],
                task["status"],
                0,
            ),
        )
        if task["deadline_id"]:
            _apply_deadline_steps(conn, user_id, task["deadline_id"])
        conn.commit()
    finally:
        conn.close()
    return task


def toggle_task(user_id, task_id):
    """Flip a task between todo and done."""
    row = db._query_one(
        "SELECT status FROM planner_tasks WHERE user_id = ? AND id = ?",
        (user_id, task_id),
    )
    if not row:
        return False
    new_status = "done" if row["status"] != "done" else "todo"
    db._execute(
        "UPDATE planner_tasks SET status = ? WHERE user_id = ? AND id = ?",
        (new_status, user_id, task_id),
    )
    return True


def complete_deadline(user_id, deadline_id):
    row = db._query_one(
        "SELECT 1 FROM planner_deadlines WHERE user_id = ? AND id = ?",
        (user_id, deadline_id),
    )
    if not row:
        return False
    db._execute(
        "UPDATE planner_deadlines SET status = 'done' "
        "WHERE user_id = ? AND id = ?",
        (user_id, deadline_id),
    )
    return True


def delete_deadline(user_id, deadline_id):
    """Remove a deadline and the lead-up steps planned for it."""
    row = db._query_one(
        "SELECT 1 FROM planner_deadlines WHERE user_id = ? AND id = ?",
        (user_id, deadline_id),
    )
    if not row:
        return False
    conn = db._conn_context()
    try:
        conn.execute(
            "DELETE FROM planner_tasks WHERE user_id = ? AND deadline_id = ?",
            (user_id, deadline_id),
        )
        conn.execute(
            "DELETE FROM planner_deadlines WHERE user_id = ? AND id = ?",
            (user_id, deadline_id),
        )
        conn.commit()
    finally:
        conn.close()
    return True


def unlink_course(user_id, course_id):
    """Detach every planner record from a course without deleting it.

    A deleted course must never leak id references into events, deadlines, or
    tasks (which would cascade into broken 'owned by ghost course' rows). The
    records themselves are worth keeping — the student still owes the work.
    """
    conn = db._conn_context()
    try:
        conn.execute(
            "UPDATE planner_events SET course_id = NULL "
            "WHERE user_id = ? AND course_id = ?",
            (user_id, course_id),
        )
        conn.execute(
            "UPDATE planner_deadlines SET course_id = NULL "
            "WHERE user_id = ? AND course_id = ?",
            (user_id, course_id),
        )
        conn.execute(
            "UPDATE planner_tasks SET course_id = NULL "
            "WHERE user_id = ? AND course_id = ?",
            (user_id, course_id),
        )
        conn.commit()
    finally:
        conn.close()


def parse_date(value):
    """Best-effort parse of a YYYY-MM-DD string into a date."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def remaining_hours(days_left, available_hours):
    """Suggest a daily cap for lead-up steps given days until due."""
    if days_left <= 0:
        return 0
    cap = available_hours or 4
    return round(min(cap, max(1, cap / days_left)), 1)


# ---------------------------------------------------------------------------
# Backward auto-planning + recovery (Home checkpoints / "I'm behind")
# ---------------------------------------------------------------------------

def _today():
    return datetime.now().date()


def _days_until(due_date_str):
    d = parse_date(due_date_str)
    if not d:
        return None
    return (d - _today()).days


def auto_plan_deadline(user_id, deadline_id, hours_per_day=None, replace=True):
    """Break a deadline's estimated work into lead-up tasks spaced backward
    from its due date, at most ``hours_per_day`` (defaults to the studio's
    available-hours cap).

    Returns a list of the generated task dicts. Idempotent options:
      - replace=True  remove any existing steps for the deadline first.
    """
    d = get_deadline(user_id, deadline_id)
    if not d:
        return []

    due = parse_date(d.get("due_date", ""))
    if not due:
        return []
    total_hours = float(d.get("estimated_hours") or 0) or 1.0
    if hours_per_day is None:
        available = float(db_available_hours(user_id))
        hours_per_day = max(1.0, round(available, 2)) if available else None
    if hours_per_day is None:
        hours_per_day = 4.0

    days_left = (due - _today()).days
    if days_left < 1:
        days_left = 1
    days_needed = max(1, round(total_hours / hours_per_day))
    days_to_use = min(days_left, days_needed)

    minutes_per_day = int(round(60 * hours_per_day))

    conn = db._conn_context()
    try:
        if replace:
            conn.execute(
                "DELETE FROM planner_tasks "
                "WHERE user_id = ? AND deadline_id = ?",
                (user_id, deadline_id),
            )
            conn.execute(
                "UPDATE planner_deadlines SET steps_json = '[]' "
                "WHERE user_id = ? AND id = ?",
                (user_id, deadline_id),
            )

        # Spread work over the days_to_use days before the due date.
        remaining_minutes = int(round(total_hours * 60))
        created = []
        for i in range(days_to_use):
            day = due - timedelta(days=(days_to_use - i))
            chunk = min(remaining_minutes, minutes_per_day)
            if chunk <= 0:
                break
            task_id = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO planner_tasks "
                "(id, user_id, deadline_id, course_id, title, due_date, "
                " estimated_minutes, priority, status, auto) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    user_id,
                    deadline_id,
                    d.get("course_id"),
                    f"Work on {d.get('title', 'deadline')}",
                    day.isoformat(),
                    chunk,
                    "high",
                    "todo",
                    1,
                ),
            )
            created.append(
                {
                    "id": task_id,
                    "deadline_id": deadline_id,
                    "course_id": d.get("course_id"),
                    "title": f"Work on {d.get('title', 'deadline')}",
                    "due_date": day.isoformat(),
                    "estimated_minutes": chunk,
                    "priority": "high",
                    "status": "todo",
                    "auto": True,
                }
            )
            remaining_minutes -= chunk
        _apply_deadline_steps(conn, user_id, deadline_id)
        conn.commit()
    finally:
        conn.close()
    return created


def db_available_hours(user_id):
    """Read a user's daily available-hours (from the accounts DB)."""
    try:
        u = db.get_user(user_id)
        if u:
            return float(u.get("available_hours") or 4)
    except Exception:
        pass
    return 4


def deadline_feasible(user_id, deadline_id):
    """Return a short feasibility report for a deadline:
    days left, sessions remaining vs. needed, and whether it fits.
    """
    d = get_deadline(user_id, deadline_id)
    if not d:
        return None
    due = parse_date(d.get("due_date", ""))
    if not due:
        return None
    days_left = (due - _today()).days
    total_hours = float(d.get("estimated_hours") or 0) or 1.0
    available = db_available_hours(user_id)
    capacity_left = max(0, days_left) * available
    feasible = capacity_left >= total_hours
    return {
        "days_left": days_left,
        "needed_hours": round(total_hours, 1),
        "available_per_day": available,
        "capacity_left": round(capacity_left, 1),
        "feasible": feasible,
    }


def recovery_plan(user_id, deadline_id, hours_per_day=None):
    """If a deadline is no longer feasible (behind), rebuild the lead-up plan
    squeezed into the time that remains. Returns the recovery report.
    """
    report = deadline_feasible(user_id, deadline_id)
    if not report:
        return None
    if report["feasible"]:
        return {**report, "recovery": False, "steps": list_tasks(user_id)}

    created = auto_plan_deadline(user_id, deadline_id, hours_per_day=hours_per_day,
                                 replace=True)
    new_report = deadline_feasible(user_id, deadline_id)
    return {
        **new_report,
        "recovery": True,
        "steps": created,
    }