"""Shared fixtures for collaboration tests.

Uses per-row cleanup (like Backend/tests/test_db.py) so the development
database is left untouched.
"""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import db
from app import app


class CollabTestCase(unittest.TestCase):
    """Creates owner/member/outsider users and a workspace per test."""

    WS_NAME = "Group 3 Project"

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

        self.owner_id = "collab-owner"
        self.member_id = "collab-member"
        self.outsider_id = "collab-outsider"
        for uid, name, email in (
            (self.owner_id, "Alice Owner", "alice@example.com"),
            (self.member_id, "Bob Member", "bob@example.com"),
            (self.outsider_id, "Cara Outsider", "cara@example.com"),
        ):
            db._execute("DELETE FROM users WHERE id = ?", (uid,))
            db.create_user(uid, name, email, "hash")

        self.ws_id = None
        # Safety: clear any leftover workspace owned by this user from a prior run.
        db._execute("DELETE FROM workspaces WHERE owner_id = ?", (self.owner_id,))
        self._start_session(self.owner_id)
        self._create_workspace()

    def tearDown(self):
        if self.ws_id:
            db._execute("DELETE FROM workspaces WHERE id = ?", (self.ws_id,))
        for uid in (self.owner_id, self.member_id, self.outsider_id):
            db._execute("DELETE FROM users WHERE id = ?", (uid,))

    # ------------------------------------------------------------- helpers
    def _start_session(self, user_id):
        with self.client.session_transaction() as s:
            s["user_id"] = user_id
            s["user_name"] = user_id

    def _create_workspace(self, name=None):
        r = self.client.post("/workspaces/", data={"name": name or self.WS_NAME})
        self.assertEqual(r.status_code, 302)
        workspaces = db.collab_workspaces_for(self.owner_id)
        self.ws_id = workspaces[0]["id"]
        self.code = workspaces[0]["invite_code"]
        return self.ws_id

    def _add_member(self, user_id):
        db._execute(
            "INSERT OR IGNORE INTO workspace_members (workspace_id, user_id, role) "
            "VALUES (?, ?, 'member')",
            (self.ws_id, user_id),
        )
        return user_id

    def _add_task(self, title="Collect data", assignee_id=None, **extra):
        with self.client.session_transaction() as s:
            s["user_id"] = self.owner_id
        before = {t["id"] for t in db.collab_work_items(self.ws_id)}
        data = {"title": title}
        if assignee_id:
            data["assignee_id"] = assignee_id
        data.update(extra)
        r = self.client.post(f"/workspaces/{self.ws_id}/tasks/create", data=data)
        self.assertEqual(r.status_code, 302)
        items = [t for t in db.collab_work_items(self.ws_id) if t["id"] not in before]
        return items[0]["id"]

    def _post_status(self, task_id, user_id, payload):
        self._start_session(user_id)
        return self.client.post(
            f"/workspaces/{self.ws_id}/tasks/{task_id}/status",
            json=payload,
            headers={"Content-Type": "application/json"},
        )

    def _activity_events(self):
        return [a["event_type"] for a in db.collab_activity(self.ws_id)]

    def _assert_activity(self, event_type):
        self.assertIn(event_type, self._activity_events(), "expected activity_log entry")


if __name__ == "__main__":
    unittest.main()