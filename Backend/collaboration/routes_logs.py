"""Daily log routes (the 'what did you do today' accountability log)."""

from flask import request, session

import db

from .bp import collab_bp
from .helpers import redirect_to_workspace
from .services import activity, permissions


@collab_bp.post("/<int:workspace_id>/log")
@permissions.workspace_member_required
def log_save(workspace_id):
    uid = session["user_id"]
    summary = (request.form.get("summary") or "").strip()
    if not summary:
        flash("Write one or two lines about what you got done today.", "error")
        return redirect_to_workspace(workspace_id, "log")
    log_date = (request.form.get("log_date") or "").strip() or None
    if not log_date:
        import datetime
        log_date = datetime.date.today().isoformat()
    minutes = (request.form.get("minutes_spent") or "").strip()
    minutes = int(minutes) if minutes.isdigit() else None
    conn = db.connect()
    try:
        db.collab_upsert_daily_log(
            conn, workspace_id, uid, log_date, summary, minutes,
            (request.form.get("blocked_by") or "").strip() or None,
            (request.form.get("plan_for_tomorrow") or "").strip() or None,
        )
        activity.log_activity(
            conn, workspace_id, uid, "daily_log", log_date=log_date,
        )
        conn.commit()
    finally:
        conn.close()
    return redirect_to_workspace(workspace_id, "log")