"""Thin row -> dict mappers for the collaborative workspace.

No ORM: functions read through the shared SQLite layer (db.py) and return
plain dicts / lists of dicts shaped for the templates and JS.
"""

STATUSES = ["todo", "in_progress", "in_review", "blocked", "completed", "cancelled"]
PRIORITIES = ["low", "normal", "high", "urgent"]

STATUS_LABEL = {
    "todo": "To do",
    "in_progress": "In progress",
    "in_review": "In review",
    "blocked": "Blocked",
    "completed": "Completed",
    "cancelled": "Cancelled",
}

PRIORITY_LABEL = {
    "low": "Low",
    "normal": "Normal",
    "high": "High",
    "urgent": "Urgent",
}

DEFAULT_LABEL_COLORS = ["#ef4444", "#f59e0b", "#10b981", "#3b82f6", "#8b5cf6", "#64748b"]


def is_valid_status(value):
    return value in STATUSES


def is_valid_priority(value):
    return value in PRIORITIES


def workspace_row_to_dict(row, members=None, num_tasks=0, num_completed=0):
    d = dict(row)
    d.setdefault("members", members or [])
    d.setdefault("num_tasks", num_tasks)
    d.setdefault("num_completed", num_completed)
    d["progress_pct"] = round(num_completed / num_tasks * 100) if num_tasks else 0
    return d


def name_of(user_id, users):
    """Return a display name for a user id, or a fallback."""
    if not user_id:
        return None
    u = users.get(user_id)
    return u["name"] if u else user_id


def activity_summary(activity):
    """Turn an activity_log row into a short human-readable summary."""
    import json
    p = {}
    if activity.get("payload"):
        try:
            p = json.loads(activity["payload"])
        except (ValueError, TypeError):
            p = {}
    event = activity.get("event_type", "")
    bits = []
    if p.get("title"):
        bits.append(f"'{p['title']}'")
    if p.get("status"):
        bits.append(STATUS_LABEL.get(p["status"], p["status"]))
    if p.get("blocked_reason"):
        bits.append("blocked")
    if p.get("assignee_id") is not None:
        bits.append("reassigned")
    if p.get("depends_on"):
        bits.append(f"depends on '{p['depends_on']}'")
    if event == "member_added" and p.get("target_id"):
        bits.append(p["target_id"])
    if p.get("name"):
        bits.append(p["name"])
    if p.get("what_needed"):
        bits.append(p["what_needed"])
    if event == "request_status" and p.get("status"):
        bits.append(p["status"])
    return " ".join(bits)
