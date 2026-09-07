"""Tests for the single course-detail destination (sidebar workspace).

The tabbed /courses/<id> page is retired: that URL now 301-redirects to the
canonical /subject/<slug> workspace, every link in the UI goes through
courses.course_url(), and the sidebar sub-nav owns Notes and Focus session —
so nothing the old hub delivered is lost when the template is deleted.
"""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import courses
import db
import planner

db.init_db()

UID = "course-links-user"


def _reset_ud():
    db._execute("DELETE FROM courses WHERE user_id = ?", (UID,))
    db._execute("DELETE FROM notes WHERE user_id = ?", (UID,))
    db._execute("DELETE FROM users WHERE id = ?", (UID,))
    data = planner.load_all()
    data.pop(UID, None)
    planner.save_all(data)
    db.create_user(UID, "Links Test", "links@example.com", "hash")


def _client():
    from app import app

    app.config["TESTING"] = True
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = UID
        s["user_name"] = "Links Test"
    return c


class CourseDetailLinksTest(unittest.TestCase):
    def setUp(self):
        _reset_ud()
        self.client = _client()
        self.course = courses.upsert_course(
            UID, "DCIT 204", title="Systems Programming", color="teal"
        )

    def tearDown(self):
        db._execute("DELETE FROM courses WHERE user_id = ?", (UID,))
        db._execute("DELETE FROM notes WHERE user_id = ?", (UID,))
        db._execute("DELETE FROM users WHERE id = ?", (UID,))
        data = planner.load_all()
        data.pop(UID, None)
        planner.save_all(data)

    def test_course_detail_301_redirects_to_subject_workspace(self):
        r = self.client.get(f'/courses/{self.course["id"]}')
        self.assertEqual(r.status_code, 301)
        self.assertEqual(r.headers["Location"], "/subject/dcit-204?tool=home")
        followed = self.client.get(r.headers["Location"])
        self.assertEqual(followed.status_code, 200)
        self.assertIn("Systems Programming", followed.data.decode())

    def test_course_detail_unknown_course_redirects_back_to_courses(self):
        r = self.client.get("/courses/does-not-exist")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/courses"))

    def test_course_url_builder(self):
        from app import app

        with app.test_request_context("/"):
            self.assertEqual(
                courses.course_url({"code": "DCIT 204"}),
                "/subject/dcit-204?tool=home",
            )
            self.assertEqual(
                courses.course_url({"code": "BIO 101"}, tool="resources"),
                "/subject/bio-101?tool=resources",
            )
            self.assertEqual(
                courses.course_url({"slug": "custom-slug", "code": "X 1"}),
                "/subject/custom-slug?tool=home",
            )

    def test_sidebar_owns_notes_and_focus_sub_nav(self):
        html = self.client.get("/subject/dcit-204?tool=notes").data.decode()
        self.assertIn(">Notes<", html)
        self.assertIn("Focus session", html)
        self.assertNotIn("course_detail", html)

    def test_notes_tool_lists_course_notes(self):
        db.create_note(
            UID,
            title="Vitamin Notes",
            body="B12 is essential for nerves",
            course_id=self.course["id"],
        )
        html = self.client.get("/subject/dcit-204?tool=notes").data.decode()
        self.assertIn("Vitamin Notes", html)
        self.assertIn("B12 is essential for nerves", html)

    def test_note_form_posts_back_to_notes_tool(self):
        r = self.client.post(
            "/notes",
            data={
                "title": "From the workspace",
                "body": "Saved inline",
                "course_id": self.course["id"],
                "back": f"/subject/dcit-204?tool=notes",
            },
        )
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/subject/dcit-204?tool=notes"))
        html = self.client.get("/subject/dcit-204?tool=notes").data.decode()
        self.assertIn("From the workspace", html)

    def test_focus_tool_links_into_session_page(self):
        html = self.client.get("/subject/dcit-204?tool=focus").data.decode()
        self.assertIn("Start focus session", html)
        self.assertIn(f'href="/session?course_id={self.course["id"]}"', html)

    def test_assignments_tool_shows_course_deadlines_and_tasks(self):
        planner.create_deadline(
            UID,
            course_id=self.course["id"],
            title="Assignment 2",
            type="assignment",
            due_date="2026-10-01",
            weight="10",
        )
        planner.add_task(
            UID,
            course_id=self.course["id"],
            title="Read chapter 4",
            due_date="2026-09-20",
            estimated_minutes="45",
            priority="high",
        )
        html = self.client.get("/subject/dcit-204?tool=assignments").data.decode()
        self.assertIn("Assignment 2", html)
        self.assertIn("Read chapter 4", html)

    def test_unrelated_course_data_stays_out_of_workspace(self):
        other = courses.upsert_course(UID, "PHY 240", title="Physics II")
        planner.create_deadline(
            UID,
            course_id=other["id"],
            title="Physics Lab Report",
            type="assignment",
            due_date="2026-10-05",
            weight="5",
        )
        html = self.client.get("/subject/dcit-204?tool=assignments").data.decode()
        self.assertNotIn("Physics Lab Report", html)


if __name__ == "__main__":
    unittest.main()