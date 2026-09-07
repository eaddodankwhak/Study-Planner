"""Workspace authorization helpers and route decorators.

Membership is checked against workspace_members. Unauthenticated requests are
redirected to the welcome page (matching the app's login_required pattern);
authenticated but unauthorized requests get a 403.
"""

import db


def user_in_workspace(workspace_id, user_id):
    return db.collab_role(workspace_id, user_id) is not None


def is_owner(workspace_id, user_id):
    return db.collab_role(workspace_id, user_id) == "owner"


def members(workspace_id):
    return db.collab_members(workspace_id)


def workspace_owner(workspace_id):
    return db.collab_workspace(workspace_id)["owner_id"]


def can_edit_item(workspace_id, user_id, item):
    """An owner always can; a member only on items they own or created."""
    if is_owner(workspace_id, user_id):
        return True
    return bool(item and (item.get("assignee_id") == user_id or item.get("created_by") == user_id))


def login_required(view):
    """Redirect unauthenticated users to the welcome page (app pattern)."""
    from functools import wraps
    from flask import redirect, session, url_for

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("welcome"))
        return view(*args, **kwargs)

    return wrapped


def _workspace_id(args, kwargs):
    ws_id = kwargs.get("workspace_id")
    if ws_id is None and args:
        ws_id = args[0]
    kwargs.pop("workspace_id", None)
    return ws_id


def workspace_member_required(view):
    """Require the current session user to be a member of the workspace."""
    from functools import wraps
    from flask import abort, g, redirect, session, url_for

    @wraps(view)
    def wrapped(*args, **kwargs):
        uid = session.get("user_id")
        if not uid:
            return redirect(url_for("welcome"))
        ws_id = _workspace_id(args, kwargs)
        if not ws_id or not user_in_workspace(ws_id, uid):
            abort(403)
        g.workspace_member = uid
        return view(ws_id, *args, **kwargs)

    return wrapped


def workspace_owner_required(view):
    """Require the current session user to be the owner (supervisor) of the workspace."""
    from functools import wraps
    from flask import abort, g, redirect, session, url_for

    @wraps(view)
    def wrapped(*args, **kwargs):
        uid = session.get("user_id")
        if not uid:
            return redirect(url_for("welcome"))
        ws_id = _workspace_id(args, kwargs)
        if not ws_id or not is_owner(ws_id, uid):
            abort(403)
        g.workspace_owner = uid
        return view(ws_id, *args, **kwargs)

    return wrapped