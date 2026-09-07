"""Permission invariants: owners may, members may not, outsiders 403."""

import unittest

import db

from collaboration.tests.base import CollabTestCase


class PermissionInvariantsTest(CollabTestCase):
    def test_milestones_are_owner_only(self):
        r = self.client.post(
            f"/workspaces/{self.ws_id}/milestones/create",
            data={"title": "Draft"},
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(db.collab_milestones(self.ws_id)), 1)

        self._add_member(self.member_id)
        self._start_session(self.member_id)
        r = self.client.post(
            f"/workspaces/{self.ws_id}/milestones/create",
            data={"title": "Nope"},
        )
        self.assertEqual(r.status_code, 403)
        self.assertEqual(len(db.collab_milestones(self.ws_id)), 1)

    def test_labels_are_available_to_members(self):
        self._add_member(self.member_id)
        self._start_session(self.member_id)
        r = self.client.post(
            f"/workspaces/{self.ws_id}/labels/create",
            data={"name": "reading"},
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(db.collab_labels(self.ws_id)), 1)

    def test_label_delete_is_owner_only(self):
        conn = db.connect()
        try:
            label_id = db.collab_create_label(conn, self.ws_id, "reads")
            conn.commit()
        finally:
            conn.close()
        self._add_member(self.member_id)
        self._start_session(self.member_id)
        r = self.client.post(
            f"/workspaces/{self.ws_id}/labels/{label_id}/delete",
        )
        self.assertEqual(r.status_code, 403)
        self.assertEqual(len(db.collab_labels(self.ws_id)), 1)

    def test_outsider_cannot_access_any_mutation(self):
        task_id = self._add_task("T")
        self._start_session(self.outsider_id)
        for url, data in (
            (f"/workspaces/{self.ws_id}/tasks/{task_id}/status", {"status": "completed"}),
            (f"/workspaces/{self.ws_id}/tasks/create", {"title": "x"}),
            (f"/workspaces/{self.ws_id}/chat", {"body": "hi"}),
            (f"/workspaces/{self.ws_id}/log", {"summary": "s"}),
            (f"/workspaces/{self.ws_id}/requests/create", {"what_needed": "x"}),
        ):
            r = self.client.post(url, json=data)
            self.assertEqual(r.status_code, 403, f"{url} should be 403 for outsiders")
        self.assertEqual(db.collab_work_item(task_id)["status"], "todo")


if __name__ == "__main__":
    unittest.main()