"""Progress / overview statistics for the Study Planner.

Combines planning data (from :mod:`planner`) and activity data (from
:mod:`db`) into the summary shown on the Progress page and Home dashboard.
"""

from datetime import date as _date
from datetime import timedelta as _timedelta


def _active_days(rows):
    """Build the set of 'YYYY-MM-DD' days with study or quiz activity."""
    days = set()
    for s in rows.get("sessions", []):
        local = s.get("started_at")
        if local:
            days.add(_date.fromtimestamp(local).isoformat())
    for a in rows.get("attempts", []):
        d = a.get("date")
        if d:
            days.add(d.split()[0])
    return days


def overview(user_id, db, planner):
    """Compute the Progress overview dict for a user."""
    activity = db.minutes_by_day(user_id)
    active = _active_days(activity)

    total_minutes = sum(int(s.get("duration_minutes") or 0) for s in activity.get("sessions", []))
    total_sessions = len(activity.get("sessions", []))
    attempts = activity.get("attempts", [])

    tasks = planner.list_tasks(user_id)
    done_tasks = [t for t in tasks if t.get("status") == "done"]
    deadlines = planner.list_deadlines(user_id)
    done_deadlines = [d for d in deadlines if d.get("status") == "done"]

    quiz_best = {}
    for a in attempts:
        qid = a.get("quiz_id")
        pct = ((a.get("score") or 0) / float(a.get("total") or 1)) * 100
        quiz_best[qid] = max(quiz_best.get(qid, 0.0), pct)

    return {
        "total_minutes": total_minutes,
        "total_sessions": total_sessions,
        "total_attempts": len(attempts),
        "streak": db.compute_streak(active),
        "active_days": len(active),
        "tasks_total": len(tasks),
        "tasks_done": len(done_tasks),
        "tasks_pct": round((len(done_tasks) / len(tasks) * 100), 1) if tasks else 0,
        "deadlines_total": len(deadlines),
        "deadlines_done": len(done_deadlines),
        "low_confidence_sessions": [
            s for s in db.list_sessions(user_id)
            if s.get("confidence") is not None and s.get("confidence") <= 2
        ],
        "quiz_best": quiz_best,
    }


def weekly_minutes(user_id, db, days=7):
    """Minute-by-minute breakdown for the last N days (oldest first).

    Real activity, one row per day — the Progress page's "weekly study
    minutes" chart is backed by this instead of being a static heading.
    """
    activity = db.minutes_by_day(user_id, days=days)
    per_day = {}
    today = _date.today()
    for s in activity.get("sessions", []):
        local = s.get("started_at")
        if not local:
            continue
        try:
            day = _date.fromtimestamp(local)
        except (TypeError, ValueError, OSError):
            continue
        if day < today - _timedelta(days=days):
            continue
        per_day[day] = per_day.get(day, 0) + int(s.get("duration_minutes") or 0)

    max_minutes = max(per_day.values(), default=0)
    rows = []
    for i in range(days - 1, -1, -1):
        day = today - _timedelta(days=i)
        minutes = per_day.get(day, 0)
        rows.append({
            "date": day.isoformat(),
            "weekday": day.strftime("%a"),
            "minutes": minutes,
            "pct": round((minutes / max_minutes) * 100) if max_minutes else 0,
        })
    return rows