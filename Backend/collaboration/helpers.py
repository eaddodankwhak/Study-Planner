"""Shared view helpers for collaboration routes."""

import random
import string

from flask import session, url_for


def current_user():
    """Resolve the logged-in user dict (for templates), or None."""
    import db
    return db.get_user(session.get("user_id")) if session.get("user_id") else None


def redirect_to_workspace(workspace_id, tab="overview", open_task=None):
    from flask import redirect
    if open_task is None:
        return redirect(url_for("collab.workspace_detail", workspace_id=workspace_id, tab=tab))
    return redirect(
        url_for("collab.workspace_detail", workspace_id=workspace_id, tab=tab, open=open_task)
    )


def new_invite_code():
    """Generate a 'WS-XXXX' join code, retrying until it is not already used."""
    while True:
        suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=4))
        code = f"WS-{suffix}"
        if not _invite_exists(code):
            return code


def _invite_exists(code):
    import db
    return db.collab_find_workspace_by_invite(code) is not None