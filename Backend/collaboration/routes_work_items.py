"""Work-item routes: create, edit, status transitions, dependencies, labels."""

from flask import (abort, flash, jsonify, render_template, request, session)

import db

from .bp import collab_bp
from .helpers import redirect_to_workspace
from .models import PRIORITIES, STATUSES, STATUS_LABEL
from .services import activity, permissions


def _get_item_or_404(workspace_id, task_id):
    item = db.collab_work_item(task_id)
    if not item or item["workspace_id"] != workspace_id:
        abort(404)
    return item


@collab_bp.post("/<int:workspace_id>/tasks/create")
@permissions.workspace_member_required
def task_create(workspace_id):
    uid = session["user_id"]
    title = (request.form.get("title") or "").strip()
    if not title:
        flash("A title is required.", "error")
        return redirect_to_workspace(workspace_id, "tasks")
    priority = request.form.get("priority", "normal")
    if priority not in PRIORITIES:
        priority = "normal"
    assignee = (request.form.get("assignee_id") or "").strip() or None
    milestone = (request.form.get("milestone_id") or "").strip() or None
    parent = (request.form.get("parent_id") or "").strip() or None
    due = (request.form.get("due_date") or "").strip() or None
    description = (request.form.get("description") or "").strip() or None

    # Members can only create tasks for themselves; owners may assign anyone.
    if assignee and not permissions.is_owner(workspace_id, uid) and assignee != uid:
        flash("Only the workspace owner can assign tasks to others.", "error")
        return redirect_to_workspace(workspace_id, "tasks")

    milestone_id = int(milestone) if milestone and milestone.isdigit() else None
    parent_id = int(parent) if parent and parent.isdigit() else None
    known = {t["id"] for t in db.collab_work_items(workspace_id)}
    if parent_id is not None and parent_id not in known:
        parent_id = None

    conn = db.connect()
    try:
        task_id = db.collab_create_work_item(
            conn, workspace_id, title, description,
            assignee or None, priority, due, uid,
            milestone_id=milestone_id,
            parent_id=parent_id,
        )
        activity.log_activity(conn, workspace_id, uid, "task_created", task_id=task_id, title=title)
        conn.commit()
    finally:
        conn.close()
    return redirect_to_workspace(workspace_id, "tasks")


@collab_bp.get("/<int:workspace_id>/tasks/<int:task_id>/panel")
@permissions.workspace_member_required
def task_panel(workspace_id, task_id):
    uid = session["user_id"]
    item = _get_item_or_404(workspace_id, task_id)
    ctx = _panel_context(workspace_id, uid, item)
    return render_template("collaboration/_task_panel.html", **ctx)


def _panel_context(workspace_id, uid, item):
    from .models import name_of
    members = db.collab_members(workspace_id)
    users = {m["user_id"]: {"name": m["name"]} for m in members}
    peers = db.collab_work_items(workspace_id)
    other_tasks = [p for p in peers if p["id"] != item["id"]]
    ctx = {
        "task": item,
        "workspace_id": workspace_id,
        "members": members,
        "user_id": uid,
        "current_user_id": uid,
        "milestones": db.collab_milestones(workspace_id),
        "priorities": PRIORITIES,
        "labels": db.collab_labels(workspace_id),
        "item_labels": db.collab_item_labels(item["id"]),
        "dependencies": db.collab_item_dependencies(item["id"]),
        "other_tasks": other_tasks,
        "comments": db.collab_comments(workspace_id, item["id"]),
        "is_owner": permissions.is_owner(workspace_id, uid),
        "can_edit": permissions.can_edit_item(workspace_id, uid, item),
        "statuses": STATUSES,
        "status_labels": STATUS_LABEL,
        "name_of": name_of,
        "users": users,
    }
    return ctx


@collab_bp.post("/<int:workspace_id>/tasks/<int:task_id>/status")
@permissions.workspace_member_required
def task_status(workspace_id, task_id):
    """JSON endpoint for optimistic status updates.

    Enforces: status must be valid; 'blocked' requires a reason; members may
    only change status on tasks assigned to (or created by) them; transitioning
    to 'in_progress' with open dependencies returns a warning (not a hard block).
    """
    uid = session["user_id"]
    item = _get_item_or_404(workspace_id, task_id)
    if not permissions.can_edit_item(workspace_id, uid, item):
        return jsonify(error="You can only change tasks assigned to you."), 403

    data = request.get_json(silent=True) or {}
    status = data.get("status")
    if status not in STATUSES:
        return jsonify(error="Invalid status."), 400

    blocked_reason = None
    if status == "blocked":
        blocked_reason = (data.get("blocked_reason") or "").strip()
        if not blocked_reason:
            return jsonify(error="Blocking requires a reason explaining what you're stuck on."), 400

    warning = None
    if status == "in_progress":
        deps = db.collab_item_dependencies(task_id)
        open_deps = [d for d in deps if d["status"] not in ("completed", "cancelled")]
        if open_deps:
            names = ", ".join(f"'{d['title']}'" for d in open_deps[:3])
            warning = f"This task depends on {names} which {'is' if len(open_deps) == 1 else 'are'} not completed yet."

    conn = db.connect()
    try:
        db.collab_set_status(conn, task_id, status, blocked_reason)
        log_payload = {"task_id": task_id, "title": item["title"], "status": status}
        if blocked_reason:
            log_payload["blocked_reason"] = blocked_reason
        activity.log_activity(conn, workspace_id, uid, "task_status", **log_payload)

        # blocked -> offer to raise an information request in the same transaction
        if status == "blocked":
            recipient = (data.get("recipient_id") or "").strip() or None
            what = (data.get("what_needed") or "").strip()
            if recipient and what:
                req_id = db.collab_create_request(
                    conn, workspace_id, task_id, uid, recipient, what,
                    (data.get("why_needed") or "").strip() or None,
                    (data.get("needed_by") or "").strip() or None,
                )
                activity.log_activity(
                    conn, workspace_id, uid, "request_created",
                    task_id=task_id, title=item["title"], request_id=req_id,
                )
        conn.commit()
    finally:
        conn.close()
    return jsonify(ok=True, status=status, warning=warning)


@collab_bp.post("/<int:workspace_id>/tasks/<int:task_id>/assign")
@permissions.workspace_member_required
def task_assign(workspace_id, task_id):
    """JSON endpoint. Members may only (un)assign on tasks they can edit, and only to themselves."""
    uid = session["user_id"]
    item = _get_item_or_404(workspace_id, task_id)
    data = request.get_json(silent=True) or {}
    assignee = (data.get("assignee_id") or "").strip() or None

    if not permissions.can_edit_item(workspace_id, uid, item):
        return jsonify(error="You can only change tasks assigned to you."), 403
    if assignee and not permissions.is_owner(workspace_id, uid) and assignee != uid:
        return jsonify(error="Only the workspace owner can assign tasks to others."), 403
    member_ids = {m["user_id"] for m in db.collab_members(workspace_id)}
    if assignee and assignee not in member_ids:
        return jsonify(error="Assignee must be a workspace member."), 400

    conn = db.connect()
    try:
        db.collab_update_work_item(conn, task_id, assignee_id=assignee)
        activity.log_activity(
            conn, workspace_id, uid, "task_assign", task_id=task_id, title=item["title"],
            assignee_id=assignee,
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(ok=True, assignee_id=assignee)


@collab_bp.post("/<int:workspace_id>/tasks/<int:task_id>/update")
@permissions.workspace_member_required
def task_update(workspace_id, task_id):
    uid = session["user_id"]
    item = _get_item_or_404(workspace_id, task_id)
    if not permissions.can_edit_item(workspace_id, uid, item):
        flash("Only the owner or assignee can edit this task.", "error")
        return redirect_to_workspace(workspace_id, "tasks", open_task=str(task_id))

    fields = {}
    if request.form.get("unnest"):
        fields["parent_id"] = None
    title = (request.form.get("title") or "").strip()
    if title:
        fields["title"] = title
    desc = (request.form.get("description") or "").strip()
    fields["description"] = desc or None
    due = (request.form.get("due_date") or "").strip()
    fields["due_date"] = due or None
    priority = request.form.get("priority", "")
    if priority in PRIORITIES:
        fields["priority"] = priority
    milestone = (request.form.get("milestone_id") or "").strip()
    fields["milestone_id"] = int(milestone) if milestone and milestone.isdigit() else None
    assignee = (request.form.get("assignee_id") or "").strip()
    if assignee:
        if not permissions.is_owner(workspace_id, uid) and assignee != uid:
            flash("Only the workspace owner can assign tasks to others.", "error")
            return redirect_to_workspace(workspace_id, "tasks", open_task=str(task_id))
        fields["assignee_id"] = assignee
    elif "assignee_id" in request.form:
        fields["assignee_id"] = None

    conn = db.connect()
    try:
        db.collab_update_work_item(conn, task_id, **fields)
        activity.log_activity(
            conn, workspace_id, uid, "task_updated", task_id=task_id,
            title=fields.get("title") or item["title"],
        )
        conn.commit()
    finally:
        conn.close()
    return redirect_to_workspace(workspace_id, "tasks", open_task=str(task_id))


@collab_bp.post("/<int:workspace_id>/tasks/<int:task_id>/delete")
@permissions.workspace_member_required
def task_delete(workspace_id, task_id):
    uid = session["user_id"]
    item = _get_item_or_404(workspace_id, task_id)
    if not permissions.can_edit_item(workspace_id, uid, item):
        flash("You do not have permission to delete this task.", "error")
        return redirect_to_workspace(workspace_id, "tasks")
    conn = db.connect()
    try:
        # dependencies and comments cascade via the schema; activity entry remains.
        cur = conn.execute("DELETE FROM work_items WHERE id = ?", (task_id,))
        if cur.rowcount:
            activity.log_activity(
                conn, workspace_id, uid, "task_deleted", task_id=task_id, title=item["title"]
            )
        conn.commit()
    finally:
        conn.close()
    return redirect_to_workspace(workspace_id, "tasks")


# ------------------------------------------------------------------ labels

@collab_bp.post("/<int:workspace_id>/tasks/<int:task_id>/labels/toggle")
@permissions.workspace_member_required
def task_toggle_label(workspace_id, task_id):
    uid = session["user_id"]
    item = _get_item_or_404(workspace_id, task_id)
    if not permissions.can_edit_item(workspace_id, uid, item):
        return jsonify(error="You can only change tasks assigned to you."), 403
    data = request.get_json(silent=True) or {}
    label_id = data.get("label_id")
    if not label_id:
        return jsonify(error="label_id required"), 400
    conn = db.connect()
    try:
        attached = db.collab_toggle_label(conn, task_id, int(label_id))
        activity.log_activity(
            conn, workspace_id, uid, "task_label" if attached else "task_label_removed",
            task_id=task_id, title=item["title"], label_id=int(label_id),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify(ok=True, attached=attached)


# -------------------------------------------------------------- dependencies

@collab_bp.post("/<int:workspace_id>/tasks/<int:task_id>/deps/add")
@permissions.workspace_member_required
def task_add_dependency(workspace_id, task_id):
    uid = session["user_id"]
    item = _get_item_or_404(workspace_id, task_id)
    if not permissions.can_edit_item(workspace_id, uid, item):
        flash("Only the owner or assignee can edit this task.", "error")
        return redirect_to_workspace(workspace_id, "tasks", open_task=str(task_id))
    dep_id = (request.form.get("depends_on_id") or "").strip()
    if not dep_id or not dep_id.isdigit():
        flash("Choose a task to depend on.", "error")
        return redirect_to_workspace(workspace_id, "tasks", open_task=str(task_id))
    dep = db.collab_work_item(int(dep_id))
    if not dep or dep["workspace_id"] != workspace_id:
        flash("That dependency is not valid here.", "error")
        return redirect_to_workspace(workspace_id, "tasks", open_task=str(task_id))
    conn = db.connect()
    try:
        try:
            added = db.collab_add_dependency(conn, task_id, int(dep_id))
        except ValueError as exc:
            flash(str(exc), "error")
            return redirect_to_workspace(workspace_id, "tasks", open_task=str(task_id))
        if added:
            activity.log_activity(
                conn, workspace_id, uid, "task_dependency",
                task_id=task_id, title=item["title"], depends_on=dep["title"],
            )
        conn.commit()
    finally:
        conn.close()
    return redirect_to_workspace(workspace_id, "tasks", open_task=str(task_id))


@collab_bp.post("/<int:workspace_id>/tasks/<int:task_id>/deps/remove")
@permissions.workspace_member_required
def task_remove_dependency(workspace_id, task_id):
    uid = session["user_id"]
    item = _get_item_or_404(workspace_id, task_id)
    if not permissions.can_edit_item(workspace_id, uid, item):
        flash("Only the owner or assignee can edit this task.", "error")
        return redirect_to_workspace(workspace_id, "tasks", open_task=str(task_id))
    dep_id = (request.form.get("depends_on_id") or "").strip()
    if not dep_id or not dep_id.isdigit():
        return redirect_to_workspace(workspace_id, "tasks", open_task=str(task_id))
    conn = db.connect()
    try:
        removed = db.collab_remove_dependency(conn, task_id, int(dep_id))
        if removed:
            activity.log_activity(
                conn, workspace_id, uid, "task_dependency_removed",
                task_id=task_id, title=item["title"],
            )
        conn.commit()
    finally:
        conn.close()
    return redirect_to_workspace(workspace_id, "tasks", open_task=str(task_id))


# --------------------------------------------------------------- milestones

@collab_bp.post("/<int:workspace_id>/milestones/create")
@permissions.workspace_owner_required
def milestone_create(workspace_id):
    title = (request.form.get("title") or "").strip()
    if not title:
        flash("Milestone title required.", "error")
        return redirect_to_workspace(workspace_id, "tasks")
    due = (request.form.get("due_date") or "").strip() or None
    conn = db.connect()
    try:
        milestone_id = db.collab_create_milestone(conn, workspace_id, title, due)
        activity.log_activity(
            conn, workspace_id, session["user_id"], "milestone_created",
            milestone_id=milestone_id, title=title,
        )
        conn.commit()
    finally:
        conn.close()
    return redirect_to_workspace(workspace_id, "tasks")


@collab_bp.post("/<int:workspace_id>/milestones/<int:milestone_id>/delete")
@permissions.workspace_owner_required
def milestone_delete(workspace_id, milestone_id):
    conn = db.connect()
    try:
        db.collab_delete_milestone(conn, milestone_id)
        activity.log_activity(
            conn, workspace_id, session["user_id"], "milestone_deleted", milestone_id=milestone_id
        )
        conn.commit()
    finally:
        conn.close()
    return redirect_to_workspace(workspace_id, "tasks")


# -------------------------------------------------------------------- labels

@collab_bp.post("/<int:workspace_id>/labels/create")
@permissions.workspace_member_required
def label_create(workspace_id):
    name = (request.form.get("name") or "").strip()
    color = (request.form.get("color") or "").strip() or "#64748b"
    if not name:
        flash("Label name required.", "error")
        return redirect_to_workspace(workspace_id, "tasks")
    conn = db.connect()
    try:
        label_id = db.collab_create_label(conn, workspace_id, name, color)
        activity.log_activity(
            conn, workspace_id, session["user_id"], "label_created",
            label_id=label_id, name=name,
        )
        conn.commit()
    finally:
        conn.close()
    return redirect_to_workspace(workspace_id, "tasks")


@collab_bp.post("/<int:workspace_id>/labels/<int:label_id>/delete")
@permissions.workspace_owner_required
def label_delete(workspace_id, label_id):
    conn = db.connect()
    try:
        db.collab_delete_label(conn, label_id)
        activity.log_activity(
            conn, workspace_id, session["user_id"], "label_deleted", label_id=label_id
        )
        conn.commit()
    finally:
        conn.close()
    return redirect_to_workspace(workspace_id, "tasks")
