"""Workspace membership + authorization boundary tests."""

import unittest

import db

from collaboration.tests.base import CollabTestCase


class WorkspaceAuthTest(CollabTestCase):
    def test_create_sets_owner_and_membership(self):
        role = db.collab_role(self.ws_id, self.owner_id)
        self.assertEqual(role, "owner")

    def test_create_logs_workspace_created(self):
        self._assert_activity("workspace_created")

    def test_anonymous_user_redirected_to_welcome(self):
        with self.client.session_transaction() as s:
            s.clear()
        r = self.client.get("/workspaces/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/welcome", r.headers["Location"])
        r = self.client.get("/workspaces/" + str(self.ws_id))
        self.assertEqual(r.status_code, 302)

    def test_join_by_invite_code_adds_member_and_logs(self):
        self._start_session(self.member_id)
        r = self.client.post("/workspaces/join", data={"invite_code": self.code})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(db.collab_role(self.ws_id, self.member_id), "member")
        self._assert_activity("member_joined")

    def test_join_bad_code_flashes_error(self):
        self._start_session(self.member_id)
        r = self.client.post("/workspaces/join", data={"invite_code": "WS-NOPE"})
        self.assertEqual(r.status_code, 302)
        self.assertIsNone(db.collab_role(self.ws_id, self.member_id))

    def test_outside_member_gets_404_page_for_workspace(self):
        self._start_session(self.outsider_id)
        r = self.client.get(f"/workspaces/{self.ws_id}")
        self.assertEqual(r.status_code, 403)

    def test_member_can_view_workspace(self):
        self._add_member(self.member_id)
        self._start_session(self.member_id)
        r = self.client.get(f"/workspaces/{self.ws_id}")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Group 3 Project".encode(), r.data)

    def test_workspace_list_only_shows_my_workspaces(self):
        self._start_session(self.member_id)
        self.assertEqual(self.client.get("/workspaces/").status_code, 200)
        self.assertEqual(db.collab_workspaces_for(self.member_id), [])


class MembershipManagementTest(CollabTestCase):
    def test_owner_adds_member_by_email(self):
        r = self.client.post(
            f"/workspaces/{self.ws_id}/members/add",
            data={"email": "bob@example.com"},
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(db.collab_role(self.ws_id, self.member_id), "member")
        self._assert_activity("member_added")

    def test_add_unknown_email_does_not_create_member(self):
        r = self.client.post(
            f"/workspaces/{self.ws_id}/members/add",
            data={"email": "ghost@example.com"},
        )
        self.assertEqual(r.status_code, 302)
        self.assertIsNone(db.collab_role(self.ws_id, "ghost@example.com"))

    def test_member_cannot_add_members(self):
        self._add_member(self.member_id)
        self._start_session(self.member_id)
        r = self.client.post(
            f"/workspaces/{self.ws_id}/members/add",
            data={"email": "cara@example.com"},
        )
        self.assertEqual(r.status_code, 403)

    def test_owner_removes_member(self):
        self._add_member(self.member_id)
        r = self.client.post(
            f"/workspaces/{self.ws_id}/members/remove",
            data={"user_id": self.member_id},
        )
        self.assertEqual(r.status_code, 302)
        self.assertIsNone(db.collab_role(self.ws_id, self.member_id))
        self._assert_activity("member_removed")

    def test_member_cannot_remove_members(self):
        self._add_member(self.member_id)
        self._start_session(self.member_id)
        r = self.client.post(
            f"/workspaces/{self.ws_id}/members/remove",
            data={"user_id": self.owner_id},
        )
        self.assertEqual(r.status_code, 403)

    def test_owner_cannot_remove_owner(self):
        r = self.client.post(
            f"/workspaces/{self.ws_id}/members/remove",
            data={"user_id": self.owner_id},
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(db.collab_role(self.ws_id, self.owner_id), "owner")


if __name__ == "__main__":
    unittest.main()