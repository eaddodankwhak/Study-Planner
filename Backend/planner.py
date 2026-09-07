"""Data persistence for the Study Planner planning features.

Stores each student's courses, calendar events, deadlines, and tasks in a
single JSON file (Database/planner.json), keyed by user id.

Entities:
    Course  - code, title, lecturer, credits, description, schedule, color
    Event   - calendar event (lecture/tutorial/lab/exam/other) with optional
              weekly recurrence rule
    Deadline- graded deliverable: type, due date, weight, estimated hours,
              plus manually entered lead-up steps (tasks)
    Task    - a study/lead-up task tied to a course and optionally a deadline
"""

import json
import os
import uuid
from datetime import datetime, timedelta

import courses

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_DIR = os.path.join(BASE_DIR, "..", "Database")
PLANNER_FILE = os.path.join(DATABASE_DIR, "planner.json")

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


def _empty():
    return {"courses": {}, "events": [], "deadlines": {}, "tasks": []}


def load_all():
    """Load the whole planner document (a map of user_id -> user planner block)."""
    if not os.path.exists(PLANNER_FILE):
        return {}
    with open(PLANNER_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_all(data):
    """Persist the whole planner document."""
    os.makedirs(DATABASE_DIR, exist_ok=True)
    with open(PLANNER_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _user_data(data, user_id):
    """Return (and lazily create) the planner data block for one user."""
    if user_id not in data:
        data[user_id] = {"courses": {}, "events": [], "deadlines": {}, "tasks": []}
    return data[user_id]


# ---------------------------------------------------------------------------
# Courses
# ---------------------------------------------------------------------------

def list_courses(user_id):
    """This user's courses, straight from the shared courses table.

    Courses moved out of planner.json into Backend/courses.py: the dashboard,
    /courses, and onboarding all read the one table, so the views stay in
    agreement. planner.json still owns events, deadlines, and tasks.
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

def list_events(user_id):
    data = load_all()
    ud = _user_data(data, user_id)
    return sorted(ud.get("events", []), key=lambda e: e.get("start", ""))


def create_event(user_id, **fields):
    data = load_all()
    ud = _user_data(data, user_id)
    event = {
        "id": uuid.uuid4().hex,
        "type": fields.get("type", "other"),
        "title": fields.get("title", "").strip(),
        "course_id": fields.get("course_id"),
        "start": fields.get("start", ""),
        "duration_minutes": int(fields.get("duration_minutes") or 60),
        "recurrence": fields.get("recurrence", "none"),  # none|weekly
        "weekday": fields.get("weekday", ""),
    }
    ud.setdefault("events", []).append(event)
    save_all(data)
    return event


def delete_event(user_id, event_id):
    data = load_all()
    ud = _user_data(data, user_id)
    ud["events"] = [e for e in ud.get("events", []) if e.get("id") != event_id]
    save_all(data)


# ---------------------------------------------------------------------------
# Deadlines
# ---------------------------------------------------------------------------

def list_deadlines(user_id):
    data = load_all()
    ud = _user_data(data, user_id)
    return list(ud.get("deadlines", {}).values())


def get_deadline(user_id, deadline_id):
    data = load_all()
    ud = _user_data(data, user_id)
    return ud.get("deadlines", {}).get(deadline_id)


def create_deadline(user_id, **fields):
    data = load_all()
    ud = _user_data(data, user_id)
    deadlines = ud.setdefault("deadlines", {})
    deadline_id = uuid.uuid4().hex
    deadlines[deadline_id] = {
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
    save_all(data)
    return deadlines[deadline_id]


# ---------------------------------------------------------------------------
# Tasks (manual lead-up steps tied to a deadline)
# ---------------------------------------------------------------------------

def list_tasks(user_id):
    data = load_all()
    ud = _user_data(data, user_id)
    return sorted(ud.get("tasks", []), key=lambda t: t.get("due_date", ""))


def get_task(user_id, task_id):
    data = load_all()
    ud = _user_data(data, user_id)
    for t in ud.get("tasks", []):
        if t.get("id") == task_id:
            return t
    return None


def add_task(user_id, **fields):
    data = load_all()
    ud = _user_data(data, user_id)
    task = {
        "id": uuid.uuid4().hex,
        "deadline_id": fields.get("deadline_id"),
        "course_id": fields.get("course_id"),
        "title": fields.get("title", "").strip(),
        "due_date": fields.get("due_date", ""),
        "estimated_minutes": int(fields.get("estimated_minutes") or 60),
        "priority": fields.get("priority", "medium"),
        "status": "todo",
    }
    ud.setdefault("tasks", []).append(task)
    deadline_id = fields.get("deadline_id")
    if deadline_id and deadline_id in ud.get("deadlines", {}):
        ud["deadlines"][deadline_id].setdefault("steps", []).append(task["id"])
    save_all(data)
    return task


def toggle_task(user_id, task_id):
    """Flip a task between todo and done."""
    data = load_all()
    ud = _user_data(data, user_id)
    for t in ud.get("tasks", []):
        if t.get("id") == task_id:
            t["status"] = "done" if t["status"] != "done" else "todo"
            save_all(data)
            return True
    return False


def complete_deadline(user_id, deadline_id):
    data = load_all()
    ud = _user_data(data, user_id)
    d = ud.get("deadlines", {}).get(deadline_id)
    if d:
        d["status"] = "done"
        save_all(data)
        return True
    return False


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

    Returns a list of the generated task ids. Idempotent options:
      - replace=True  remove any existing steps for the deadline first.
    """
    data = load_all()
    ud = _user_data(data, user_id)
    d = ud.get("deadlines", {}).get(deadline_id)
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

    if replace:
        old_ids = set(ud.get("deadlines", {}).get(deadline_id, {}).get("steps", []))
        ud.setdefault("tasks", [])[:] = [
            t for t in ud.get("tasks", []) if t.get("id") not in old_ids
        ]
        d["steps"] = []

    # Spread work over the days_to_use days before the due date.
    remaining_minutes = int(round(total_hours * 60))
    created = []
    for i in range(days_to_use):
        day = due - timedelta(days=(days_to_use - i))
        chunk = min(remaining_minutes, minutes_per_day)
        if chunk <= 0:
            break
        task = {
            "id": uuid.uuid4().hex,
            "deadline_id": deadline_id,
            "course_id": d.get("course_id"),
            "title": f"Work on {d.get('title', 'deadline')}",
            "due_date": day.isoformat(),
            "estimated_minutes": chunk,
            "priority": "high",
            "status": "todo",
            "auto": True,
        }
        ud.setdefault("tasks", []).append(task)
        d.setdefault("steps", []).append(task["id"])
        created.append(task)
        remaining_minutes -= chunk
    save_all(data)
    return created


def db_available_hours(user_id):
    """Read a user's daily available-hours (from the accounts DB)."""
    try:
        import db as _db
        u = _db.get_user(user_id)
        if u:
            return float(u.get("available_hours") or 4)
    except Exception:
        pass
    return 4


def deadline_feasible(user_id, deadline_id):
    """Return a short feasibility report for a deadline:
    days left, sessions remaining vs. needed, and whether it fits.
    """
    data = load_all()
    ud = _user_data(data, user_id)
    d = ud.get("deadlines", {}).get(deadline_id)
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
