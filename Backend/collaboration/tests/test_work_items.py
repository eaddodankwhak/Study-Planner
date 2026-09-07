"""Work-item status transitions, activity log, and info-request tests."""

import unittest

import db

from collaboration.tests.base import CollabTestCase


class TaskStatusTest(CollabTestCase):
    def test_create_task_is_logged_and_scoped(self):
        task_id = self._add_task("Read chapter 3")
        item = db.collab_work_item(task_id)
        self.assertEqual(item["workspace_id"], self.ws_id)
        self._assert_activity("task_created")

    def test_owner_can_change_status(self):
        task_id = self._add_task()
        r = self._post_status(task_id, self.owner_id, {"status": "in_progress"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(db.collab_work_item(task_id)["status"], "in_progress")
        self._assert_activity("task_status")

    def test_assignee_can_change_status(self):
        self._add_member(self.member_id)
        task_id = self._add_task("T", assignee_id=self.member_id)
        r = self._post_status(task_id, self.member_id, {"status": "completed"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(db.collab_work_item(task_id)["status"], "completed")

    def test_member_cannot_change_others_task(self):
        self._add_member(self.member_id)
        task_id = self._add_task("T")  # assigned to nobody, created by owner
        r = self._post_status(task_id, self.member_id, {"status": "in_progress"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(db.collab_work_item(task_id)["status"], "todo")

    def test_invalid_status_rejected(self):
        task_id = self._add_task()
        r = self._post_status(task_id, self.owner_id, {"status": "halfway"})
        self.assertEqual(r.status_code, 400)

    def test_blocked_requires_reason(self):
        task_id = self._add_task()
        r = self._post_status(task_id, self.owner_id, {"status": "blocked"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(db.collab_work_item(task_id)["status"], "todo")

    def test_blocked_sets_reason(self):
        task_id = self._add_task()
        r = self._post_status(task_id, self.owner_id, {"status": "blocked", "blocked_reason": "waiting on data"})
        self.assertEqual(r.status_code, 200)
        item = db.collab_work_item(task_id)
        self.assertEqual(item["status"], "blocked")
        self.assertEqual(item["blocked_reason"], "waiting on data")

    def test_in_progress_with_open_dependency_warns_but_allows(self):
        dep_id = self._add_task("Prereq")
        task_id = self._add_task("Depends")
        conn = db.connect()
        try:
            db.collab_add_dependency(conn, task_id, dep_id)
            conn.commit()
        finally:
            conn.close()
        r = self._post_status(task_id, self.owner_id, {"status": "in_progress"})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["status"], "in_progress")
        self.assertTrue(body["warning"])
        self.assertEqual(db.collab_work_item(task_id)["status"], "in_progress")

    def test_block_can_raise_info_request_in_same_action(self):
        self._add_member(self.member_id)
        task_id = self._add_task("Needs help")
        r = self._post_status(task_id, self.owner_id, {
            "status": "blocked",
            "blocked_reason": "stuck on the spreadsheet",
            "recipient_id": self.member_id,
            "what_needed": "the raw results file",
        })
        self.assertEqual(r.status_code, 200)
        reqs = db.collab_requests(self.ws_id)
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0]["what_needed"], "the raw results file")
        self._assert_activity("request_created")


class TaskAssignmentTest(CollabTestCase):
    def test_member_cannot_assign_to_others_via_assign_endpoint(self):
        self._add_member(self.member_id)
        task_id = self._add_task("T", assignee_id=self.member_id)
        self._start_session(self.member_id)
        r = self.client.post(
            f"/workspaces/{self.ws_id}/tasks/{task_id}/assign",
            json={"assignee_id": self.owner_id},
        )
        self.assertEqual(r.status_code, 403)

    def test_member_can_assign_a_task_they_created_to_self(self):
        self._add_member(self.member_id)
        self._start_session(self.member_id)
        self.client.post(
            f"/workspaces/{self.ws_id}/tasks/create",
            data={"title": "T"},
        )
        task_id = db.collab_work_items(self.ws_id)[0]["id"]
        r = self.client.post(
            f"/workspaces/{self.ws_id}/tasks/{task_id}/assign",
            json={"assignee_id": self.member_id},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(db.collab_work_item(task_id)["assignee_id"], self.member_id)
        self._assert_activity("task_assign")

    def test_owner_can_assign_to_member(self):
        self._add_member(self.member_id)
        task_id = self._add_task("T")
        r = self.client.post(
            f"/workspaces/{self.ws_id}/tasks/{task_id}/assign",
            json={"assignee_id": self.member_id},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(db.collab_work_item(task_id)["assignee_id"], self.member_id)

    def test_member_create_only_assigns_self(self):
        self._add_member(self.member_id)
        self._start_session(self.member_id)
        r = self.client.post(
            f"/workspaces/{self.ws_id}/tasks/create",
            data={"title": "Mine", "assignee_id": self.owner_id},
        )
        self.assertEqual(r.status_code, 302)
        # rejected at creation: no task was created for a member assigning to others
        self.assertEqual(db.collab_work_items(self.ws_id), [])


class LabelsDependenciesCommentsTest(CollabTestCase):
    def test_label_toggle_and_log(self):
        task_id = self._add_task("T")
        conn = db.connect()
        try:
            label_id = db.collab_create_label(conn, self.ws_id, "reads")
            conn.commit()
        finally:
            conn.close()
        r = self.client.post(
            f"/workspaces/{self.ws_id}/tasks/{task_id}/labels/toggle",
            json={"label_id": label_id},
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["attached"])
        self.assertEqual(len(db.collab_item_labels(task_id)), 1)
        self._assert_activity("task_label")

    def test_outsider_cannot_toggle_labels(self):
        task_id = self._add_task("T")
        conn = db.connect()
        try:
            label_id = db.collab_create_label(conn, self.ws_id, "reads")
            conn.commit()
        finally:
            conn.close()
        self._start_session(self.outsider_id)
        r = self.client.post(
            f"/workspaces/{self.ws_id}/tasks/{task_id}/labels/toggle",
            json={"label_id": label_id},
        )
        self.assertEqual(r.status_code, 403)

    def test_add_and_remove_dependency(self):
        a = self._add_task("A")
        b = self._add_task("B")
        r = self.client.post(
            f"/workspaces/{self.ws_id}/tasks/{b}/deps/add",
            data={"depends_on_id": str(a)},
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(db.collab_item_dependencies(b)), 1)
        self._assert_activity("task_dependency")

        r = self.client.post(
            f"/workspaces/{self.ws_id}/tasks/{b}/deps/remove",
            data={"depends_on_id": str(a)},
        )
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(db.collab_item_dependencies(b)), 0)

    def test_task_comment_is_logged(self):
        task_id = self._add_task("T")
        r = self.client.post(
            f"/workspaces/{self.ws_id}/tasks/{task_id}/comments",
            data={"ajax": "1", "body": "I started on this"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])
        comments = db.collab_comments(self.ws_id, task_id)
        self.assertEqual(comments[0]["body"], "I started on this")
        self._assert_activity("comment")

    def test_chat_message_is_logged(self):
        r = self.client.post(
            f"/workspaces/{self.ws_id}/chat",
            data={"ajax": "1", "body": "hello group"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(db.collab_comments(self.ws_id)[0]["body"], "hello group")
        self._assert_activity("comment")


class RequestsAndLogsTest(CollabTestCase):
    def test_create_and_update_info_request(self):
        self._add_member(self.member_id)
        r = self.client.post(
            f"/workspaces/{self.ws_id}/requests/create",
            data={"what_needed": "the data file", "recipient_id": self.member_id},
        )
        self.assertEqual(r.status_code, 302)
        reqs = db.collab_requests(self.ws_id)
        self.assertEqual(len(reqs), 1)
        self._assert_activity("request_created")

        r = self.client.post(
            f"/workspaces/{self.ws_id}/requests/{reqs[0]['id']}/status",
            data={"status": "provided"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(db.collab_requests(self.ws_id)[0]["status"], "provided")
        self._assert_activity("request_status")

    def test_daily_log_upserts_with_activity(self):
        import datetime
        today = datetime.date.today().isoformat()
        r = self.client.post(
            f"/workspaces/{self.ws_id}/log",
            data={"log_date": today, "summary": "did the intro", "minutes_spent": "45"},
        )
        self.assertEqual(r.status_code, 302)
        log = db.collab_daily_log_for(self.ws_id, self.owner_id, today)
        self.assertEqual(log["summary"], "did the intro")
        self._assert_activity("daily_log")

        r = self.client.post(
            f"/workspaces/{self.ws_id}/log",
            data={"log_date": today, "summary": "updated", "minutes_spent": "50"},
        )
        self.assertEqual(r.status_code, 302)
        log = db.collab_daily_log_for(self.ws_id, self.owner_id, today)
        self.assertEqual(log["summary"], "updated")
        logs = [l for l in db.collab_daily_logs(self.ws_id) if l["log_date"] == today]
        self.assertEqual(len(logs), 1)

    def test_request_status_limited_to_participants(self):
        self._add_member(self.member_id)
        r = self.client.post(
            f"/workspaces/{self.ws_id}/requests/create",
            data={"what_needed": "x", "recipient_id": self.member_id},
        )
        self.assertEqual(r.status_code, 302)
        req_id = db.collab_requests(self.ws_id)[0]["id"]

        self._start_session(self.outsider_id)
        r = self.client.post(
            f"/workspaces/{self.ws_id}/requests/{req_id}/status",
            data={"status": "provided"},
        )
        self.assertEqual(r.status_code, 403)


if __name__ == "__main__":
    unittest.main()