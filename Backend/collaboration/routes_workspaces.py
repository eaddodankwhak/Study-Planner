"""Workspace list/detail routes: creation, joining, membership."""

from flask import (flash, redirect, render_template, request, session, url_for)

import db

from .bp import collab_bp
from .helpers import current_user, new_invite_code, redirect_to_workspace
from .models import activity_summary
from .services import activity, permissions

TABS = ("overview", "tasks", "chat", "requests", "log", "activity", "members")


def _today():
    import datetime
    return datetime.date.today().isoformat()


def _load_context(ws_id, uid):
    """Load every piece of data the tabbed detail page needs."""
    ws = db.collab_workspace(ws_id)
    if not ws:
        return None
    members = db.collab_members(ws_id)
    is_owner = permissions.is_owner(ws_id, uid)

    tasks = db.collab_work_items(ws_id)
    milestones = db.collab_milestones(ws_id, with_counts=True)
    labels = db.collab_labels(ws_id)
    labels_map = db.collab_ws_labels_map(ws_id)
    comment_counts = db.collab_ws_comment_counts(ws_id)
    dep_pairs = db.collab_ws_dependency_pairs(ws_id)

    items_by_id = {t["id"]: t for t in tasks}
    for t in tasks:
        t["can_edit"] = permissions.can_edit_item(ws_id, uid, t)
        t.setdefault("labels", labels_map.get(t["id"], []))
        t["comments_count"] = comment_counts.get(t["id"], 0)
        t.setdefault("dependencies", [])
        t.setdefault("dependents", [])
        t.setdefault("children", [])

    roots = []
    for t in tasks:
        if t["parent_id"] and t["parent_id"] in items_by_id:
            items_by_id[t["parent_id"]]["children"].append(t)
        else:
            roots.append(t)

    for item_id, dep_id in dep_pairs:
        if item_id in items_by_id and dep_id in items_by_id:
            dep = items_by_id[dep_id]
            items_by_id[item_id]["dependencies"].append(
                {"id": dep["id"], "title": dep["title"], "status": dep["status"]}
            )
            items_by_id[dep_id]["dependents"].append(
                {"id": items_by_id[item_id]["id"], "title": items_by_id[item_id]["title"],
                 "status": items_by_id[item_id]["status"]}
            )

    return {
        "workspace": ws,
        "members": members,
        "member_ids": [m["user_id"] for m in members],
        "is_owner": is_owner,
        "admin_perms": is_owner,
        "milestones": milestones,
        "labels": labels,
        "tasks": tasks,
        "roots": roots,
        "chat": db.collab_comments(ws_id),
        "requests": db.collab_requests(ws_id),
        "daily_logs": db.collab_daily_logs(ws_id),
        "today_log": db.collab_daily_log_for(ws_id, uid, _today()),
        "activity": [_with_summary(a) for a in db.collab_activity(ws_id)],
        "tabs": TABS,
        "today": _today(),
    }


def _with_summary(activity_row):
    activity_row["summary"] = activity_summary(activity_row)
    return activity_row


def _redirect_to_list():
    return redirect(url_for("collab.workspace_list"))


TODO_STATUS = "todo"


@collab_bp.get("/")
@permissions.login_required
def workspace_list():
    user_id = session["user_id"]
    workspaces = db.collab_workspaces_for(user_id)
    return render_template(
        "collaboration/workspace_list.html",
        user=current_user(),
        workspaces=workspaces,
        today=_today(),
    )


@collab_bp.post("/")
@permissions.login_required
def workspace_create():
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("Workspace name is required.", "error")
        return _redirect_to_list()
    course_code = (request.form.get("course_code") or "").strip() or None
    description = (request.form.get("description") or "").strip() or None
    deadline = (request.form.get("deadline") or "").strip() or None
    owner_id = session["user_id"]

    conn = db.connect()
    try:
        ws_id = db.collab_create_workspace(
            conn, name, course_code, description, deadline,
            owner_id, new_invite_code(),
        )
        activity.log_activity(conn, ws_id, owner_id, "workspace_created", name=name)
        conn.commit()
    finally:
        conn.close()
    flash("Workspace created. Share the invite code with your group.", "success")
    return redirect_to_workspace(ws_id)


@collab_bp.post("/join")
@permissions.login_required
def workspace_join():
    code = (request.form.get("invite_code") or "").strip().upper()
    ws = db.collab_find_workspace_by_invite(code)
    if not ws:
        flash("That invite code did not match any workspace.", "error")
        return _redirect_to_list()
    uid = session["user_id"]
    if db.collab_role(ws["id"], uid) is None:
        conn = db.connect()
        try:
            db.collab_add_member(conn, ws["id"], uid)
            activity.log_activity(conn, ws["id"], uid, "member_joined")
            conn.commit()
        finally:
            conn.close()
    return redirect_to_workspace(ws["id"])


@collab_bp.get("/<int:workspace_id>")
@permissions.workspace_member_required
def workspace_detail(workspace_id):
    uid = session["user_id"]
    ctx = _load_context(workspace_id, uid)
    if ctx is None:
        from flask import abort
        abort(404)
    tab = request.args.get("tab", "overview")
    if tab not in TABS:
        tab = "overview"
    ctx["tab"] = tab
    ctx["open_task"] = request.args.get("open", type=int)
    ctx["user"] = current_user()
    return render_template("collaboration/workspace_detail.html", **ctx)


# --------------------------------------------------------------- membership

@collab_bp.post("/<int:workspace_id>/members/add")
@permissions.workspace_owner_required
def workspace_member_add(workspace_id):
    email = (request.form.get("email") or "").strip().lower()
    target = db.get_user_by_email(email) if email else None
    if not target:
        flash(f"No account found for {email or 'that email'}.", "error")
        return redirect_to_workspace(workspace_id, "members")
    conn = db.connect()
    try:
        added = db.collab_add_member(conn, workspace_id, target["id"])
        activity.log_activity(
            conn, workspace_id, session["user_id"], "member_added",
            target_id=target["id"],
        )
        conn.commit()
    finally:
        conn.close()
    if added:
        flash(f"Added {target['name']} to the workspace.", "success")
    else:
        flash(f"{target['name']} is already a member.", "info")
    return redirect_to_workspace(workspace_id, "members")


@collab_bp.post("/<int:workspace_id>/members/remove")
@permissions.workspace_owner_required
def workspace_member_remove(workspace_id):
    target_id = (request.form.get("user_id") or "").strip()
    if not target_id:
        return redirect_to_workspace(workspace_id, "members")
    if permissions.is_owner(workspace_id, target_id):
        flash("You cannot remove the workspace owner.", "error")
        return redirect_to_workspace(workspace_id, "members")
    conn = db.connect()
    try:
        removed = db.collab_remove_member(conn, workspace_id, target_id)
        activity.log_activity(
            conn, workspace_id, session["user_id"], "member_removed", target_id=target_id
        )
        conn.commit()
    finally:
        conn.close()
    if removed:
        flash("Member removed.", "success")
    return redirect_to_workspace(workspace_id, "members")
