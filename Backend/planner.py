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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_DIR = os.path.join(BASE_DIR, "..", "Database")
PLANNER_FILE = os.path.join(DATABASE_DIR, "planner.json")

COURSE_COLORS = ["navy", "teal", "orange", "green", "purple", "red"]

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
    data = load_all()
    ud = _user_data(data, user_id)
    return list(ud.get("courses", {}).values())


def get_course(user_id, course_id):
    data = load_all()
    ud = _user_data(data, user_id)
    return ud.get("courses", {}).get(course_id)


def create_course(user_id, **fields):
    data = load_all()
    ud = _user_data(data, user_id)
    courses = ud.setdefault("courses", {})
    course_id = uuid.uuid4().hex
    color = fields.get("color") or COURSE_COLORS[len(courses) % len(COURSE_COLORS)]
    courses[course_id] = {
        "id": course_id,
        "code": fields.get("code", "").strip(),
        "title": fields.get("title", "").strip(),
        "lecturer": fields.get("lecturer", "").strip(),
        "credits": int(fields.get("credits") or 0),
        "description": fields.get("description", "").strip(),
        "schedule": fields.get("schedule", "").strip(),
        "color": color,
    }
    save_all(data)
    return courses[course_id]


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
