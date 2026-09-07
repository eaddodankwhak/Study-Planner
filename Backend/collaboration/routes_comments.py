"""Comment routes: workspace chat messages and per-task comments.

Both endpoints work as plain HTML form posts (flash + redirect) OR return JSON
when called with `ajax=1` (used by task-detail.js for live comment threads).
"""

from flask import jsonify, render_template, request, session

import db

from .bp import collab_bp
from .helpers import current_user, redirect_to_workspace
from .models import name_of
from .services import activity, permissions


def _comment_author(uid, members):
    return name_of(uid, {m["user_id"]: {"name": m["name"]} for m in members})


def _comment_html(body, author, is_json):
    return render_template(
        "collaboration/_comment_item.html",
        comment={"body": body, "author_name": author, "created_at": ""},
    )


@collab_bp.post("/<int:workspace_id>/chat")
@permissions.workspace_member_required
def chat_post(workspace_id):
    uid = session["user_id"]
    data = request.get_json(silent=True) or {}
    body = (request.form.get("body") or data.get("body") or "").strip()
    is_json = request.is_json or request.form.get("ajax") == "1"
    if not body:
        if is_json:
            return jsonify(error="Message cannot be empty."), 400
        flash("Message cannot be empty.", "error")
        return redirect_to_workspace(workspace_id, "chat")
    if len(body) > 2000:
        if is_json:
            return jsonify(error="Message is too long."), 400
        flash("Message is too long.", "error")
        return redirect_to_workspace(workspace_id, "chat")
    conn = db.connect()
    try:
        comment_id = db.collab_add_comment(conn, workspace_id, uid, body)
        activity.log_activity(
            conn, workspace_id, uid, "comment", comment_id=comment_id, kind="chat"
        )
        conn.commit()
    finally:
        conn.close()
    if is_json:
        author = _comment_author(uid, db.collab_members(workspace_id))
        return jsonify(ok=True, comment_id=comment_id, html=_comment_html(body, author, True))
    return redirect_to_workspace(workspace_id, "chat")


@collab_bp.post("/<int:workspace_id>/tasks/<int:task_id>/comments")
@permissions.workspace_member_required
def task_comment(workspace_id, task_id):
    """Appends a comment to a task's discussion thread."""
    uid = session["user_id"]
    item = db.collab_work_item(task_id)
    if not item or item["workspace_id"] != workspace_id:
        if request.form.get("ajax") or request.is_json:
            return jsonify(error="Task not found."), 404
        flash("Task not found.", "error")
        return redirect_to_workspace(workspace_id, "tasks")
    data = request.get_json(silent=True) or {}
    body = (request.form.get("body") or data.get("body") or "").strip()
    is_json = request.is_json or request.form.get("ajax") == "1"
    if not body:
        if is_json:
            return jsonify(error="Comment cannot be empty."), 400
        flash("Comment cannot be empty.", "error")
        return redirect_to_workspace(workspace_id, "tasks", open_task=str(task_id))
    if len(body) > 2000:
        if is_json:
            return jsonify(error="Comment is too long."), 400
        flash("Comment is too long.", "error")
        return redirect_to_workspace(workspace_id, "tasks", open_task=str(task_id))
    conn = db.connect()
    try:
        comment_id = db.collab_add_comment(conn, workspace_id, uid, body, work_item_id=task_id)
        activity.log_activity(
            conn, workspace_id, uid, "comment",
            comment_id=comment_id, task_id=task_id, title=item["title"], kind="task",
        )
        conn.commit()
    finally:
        conn.close()
    if is_json:
        author = _comment_author(uid, db.collab_members(workspace_id))
        return jsonify(ok=True, comment_id=comment_id, html=_comment_html(body, author, True))
    return redirect_to_workspace(workspace_id, "tasks", open_task=str(task_id))