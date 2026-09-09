"""Tests for the merged Schedule page (Calendar + Practice in one tab).

One nav entry ("Schedule") renders one page that weaves the academic
calendar events, deadlines, tasks, and a focus-session CTA together.
The old /calendar and /task GET pages redirect into it, while the
POST handlers (add/toggle/delete) keep their URLs and route back to
/schedule after acting.
"""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import db
import planner

db.init_db()

UID = "schedule-page-user"


def _reset_ud():
    db._execute("DELETE FROM courses WHERE user_id = ?", (UID,))
    db._execute("DELETE FROM users WHERE id = ?", (UID,))
    data = planner.load_all()
    data.pop(UID, None)
    planner.save_all(data)
    db.create_user(UID, "Schedule Test", "schedule@example.com", "hash")


def _client():
    from app import app

    app.config["TESTING"] = True
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = UID
        s["user_name"] = "Schedule Test"
    return c


class SchedulePageTest(unittest.TestCase):
    def setUp(self):
        _reset_ud()
        self.client = _client()

    def tearDown(self):
        db._execute("DELETE FROM courses WHERE user_id = ?", (UID,))
        db._execute("DELETE FROM users WHERE id = ?", (UID,))
        data = planner.load_all()
        data.pop(UID, None)
        planner.save_all(data)

    def _page(self):
        r = self.client.get("/schedule")
        self.assertEqual(r.status_code, 200)
        return r.data.decode()

    def test_schedule_weaves_calendar_deadlines_tasks_and_focus_cta(self):
        html = self._page()
        self.assertIn(">Schedule<", html)
        self.assertNotIn(">Calendar<", html)
        self.assertNotIn(">Practice<", html)
        # Focus-session CTA with a live open-task count.
        self.assertIn(">Start a focus session<", html)
        self.assertIn("Ready to focus?", html)
        # All three data sections on one page.
        self.assertIn("Upcoming Events", html)
        self.assertIn(">Tasks<", html)
        self.assertIn("Deadlines &amp; Assessments", html)

    def test_all_three_add_forms_render_once_with_unique_field_ids(self):
        html = self._page()
        self.assertEqual(html.count("action=\"/calendar\""), 1)
        self.assertEqual(html.count("action=\"/task\""), 1)
        self.assertEqual(html.count("action=\"/deadlines\""), 1)
        # IDs are prefixed per form so labels stay uniquely bound.
        for prefix in ("ev", "tk", "dl"):
            self.assertIn(f'id="{prefix}_title"', html)
        self.assertNotIn('id="title"', html)

    def test_calendar_and_task_get_pages_redirect_to_schedule(self):
        for old in ("/calendar", "/task"):
            r = self.client.get(old)
            self.assertEqual(r.status_code, 302, old)
            self.assertTrue(r.headers["Location"].endswith("/schedule"), (old, r.headers["Location"]))

    def test_event_add_creates_and_returns_to_schedule(self):
        r = self.client.post(
            "/calendar",
            data={
                "title": "DCIT 204 Lecture",
                "type": "lecture",
                "course_id": "",
                "start": "2026-09-08T10:00",
                "duration_minutes": "60",
                "recurrence": "none",
                "weekday": "",
            },
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/schedule"))
        html = self._page()
        self.assertIn("DCIT 204 Lecture", html)

    def test_task_add_creates_and_returns_to_schedule(self):
        r = self.client.post(
            "/task",
            data={
                "title": "Review Bayes theorem",
                "course_id": "",
                "due_date": "2026-09-10",
                "estimated_minutes": "45",
                "priority": "high",
            },
        )
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/schedule"))
        html = self._page()
        self.assertIn("Review Bayes theorem", html)
        self.assertIn("1 open task", html)

    def test_deadline_add_still_returns_to_deadlines_page(self):
        r = self.client.post(
            "/deadlines",
            data={
                "title": "Assignment 2",
                "course_id": "",
                "type": "assignment",
                "due_date": "2026-10-01",
                "weight": "10",
                "estimated_hours": "4",
            },
        )
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/deadlines"))
        html = self.client.get("/deadlines").data.decode()
        self.assertIn("Assignment 2", html)

    def test_deadline_delete_removes_deadline_its_steps_and_redirects(self):
        d = planner.create_deadline(
            UID,
            title="Dropped module",
            type="exam",
            due_date="2026-11-01",
            weight="50",
            estimated_hours="8",
        )
        task = planner.add_task(UID, deadline_id=d["id"], title="Study session", due_date="2026-10-20")
        self.assertEqual(len(planner.list_tasks(UID)), 1)
        r = self.client.post(
            "/deadlines/" + d["id"] + "/delete",
            data={"back": "/deadlines"},
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 302)
        self.assertIsNone(planner.get_deadline(UID, d["id"]))
        self.assertIsNone(planner.get_task(UID, task["id"]))

    def test_deadline_delete_redirects_back_to_schedule_when_requested(self):
        d = planner.create_deadline(UID, title="Mini test", type="assignment", due_date="2026-09-20")
        r = self.client.post(
            "/deadlines/" + d["id"] + "/delete",
            data={"back": "/schedule"},
            follow_redirects=False,
        )
        self.assertTrue(r.headers["Location"].endswith("/schedule"))

    def test_unlink_course_detaches_records_without_deleting_them(self):
        from courses import upsert_course

        course = upsert_course(UID, "DCIT 205")
        ev = planner.create_event(UID, title="Lecture", type="lecture", course_id=course["id"],
                                  recurrence="none", start="2026-09-08T10:00")
        d = planner.create_deadline(UID, title="Coursework", type="assignment", due_date="2026-10-01",
                                    course_id=course["id"], weight="20", estimated_hours="3")
        t = planner.add_task(UID, title="Prep", due_date="2026-09-30", course_id=course["id"],
                             deadline_id=d["id"])

        planner.unlink_course(UID, course["id"])

        events = planner.list_events(UID)
        self.assertEqual(events[0]["course_id"], None)
        self.assertEqual(planner.get_deadline(UID, d["id"])["course_id"], None)
        self.assertEqual(planner.get_task(UID, t["id"])["course_id"], None)


if __name__ == "__main__":
    unittest.main()