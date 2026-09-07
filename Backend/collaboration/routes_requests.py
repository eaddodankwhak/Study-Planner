"""Information-request routes."""

from flask import jsonify, request, session

import db

from .bp import collab_bp
from .helpers import redirect_to_workspace
from .services import activity, permissions

REQUEST_STATUSES = ("pending", "provided", "unavailable", "reassigned")


@collab_bp.post("/<int:workspace_id>/requests/create")
@permissions.workspace_member_required
def request_create(workspace_id):
    uid = session["user_id"]
    what = (request.form.get("what_needed") or "").strip()
    if not what:
        flash("Tell people what you need.", "error")
        return redirect_to_workspace(workspace_id, "requests")
    recipient = (request.form.get("recipient_id") or "").strip()
    task = (request.form.get("work_item_id") or "").strip()
    conn = db.connect()
    try:
        req_id = db.collab_create_request(
            conn, workspace_id,
            int(task) if task and task.isdigit() else None,
            uid,
            recipient,
            what,
            (request.form.get("why_needed") or "").strip() or None,
            (request.form.get("needed_by") or "").strip() or None,
        )
        activity.log_activity(
            conn, workspace_id, uid, "request_created",
            request_id=req_id, what_needed=what, recipient_id=recipient,
        )
        conn.commit()
    finally:
        conn.close()
    return redirect_to_workspace(workspace_id, "requests")


@collab_bp.post("/<int:workspace_id>/requests/<int:request_id>/status")
@permissions.workspace_member_required
def request_update_status(workspace_id, request_id):
    """Resolve/acknowledge an info request. The requester or recipient (or owner) may act."""
    uid = session["user_id"]
    req = db.collab_requests(workspace_id)
    target = next((r for r in req if r["id"] == request_id), None)
    if not target:
        return jsonify(error="Request not found."), 404
    if not (
        permissions.is_owner(workspace_id, uid)
        or target["requester_id"] == uid
        or target["recipient_id"] == uid
    ):
        return jsonify(error="Only the requester, recipient, or owner can update this."), 403
    status = (request.form.get("status") or (request.get_json(silent=True) or {}).get("status") or "").strip()
    if status not in REQUEST_STATUSES:
        return jsonify(error="Invalid status."), 400
    conn = db.connect()
    try:
        db.collab_update_request_status(conn, request_id, status)
        activity.log_activity(
            conn, workspace_id, uid, "request_status", request_id=request_id, status=status
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(ok=True, status=status)