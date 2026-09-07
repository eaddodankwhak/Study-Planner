"""Tests for the single-source-of-truth courses layer (Backend/courses.py).

The two screenshots this fix targets — dashboard "My Subjects" vs. the
/courses page disagreeing — are impossible once both read get_user_courses,
and the onboarding-stub vs. Add-Course duplicate is impossible once both write
through upsert_course (UNIQUE(user_id, course_code)).
"""

import json
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import db
import courses

db.init_db()


class CoursesTest(unittest.TestCase):
    def setUp(self):
        self.uid = "courses-test-user"
        self.other = "courses-test-other"
        for uid in (self.uid, self.other):
            db._execute("DELETE FROM courses WHERE user_id = ?", (uid,))
            db._execute("DELETE FROM users WHERE id = ?", (uid,))
        db.create_user(self.uid, "Courses Test", "courses@example.com", "hash")
        db.create_user(self.other, "Other User", "other@example.com", "hash")

    def tearDown(self):
        for uid in (self.uid, self.other):
            db._execute("DELETE FROM courses WHERE user_id = ?", (uid,))
            db._execute("DELETE FROM users WHERE id = ?", (uid,))

    def test_normalize_course_code(self):
        self.assertEqual(db.normalize_course_code("dcit  204"), "DCIT 204")
        self.assertEqual(db.normalize_course_code("DCIT 204"), "DCIT 204")
        self.assertEqual(db.normalize_course_code("  stat   222\n"), "STAT 222")
        self.assertEqual(db.normalize_course_code(None), "")
        self.assertEqual(db.normalize_course_code("  "), "")

    def test_onboarding_stub_then_manual_add_same_course_dont_duplicate(self):
        # Onboarding-style: a code-only stub.
        courses.upsert_course(self.uid, "dcit 204")
        # The /courses form later completes the details.
        full = courses.upsert_course(
            self.uid,
            "DCIT 204",
            title="Data Structures & Algorithms",
            lecturer="Dr. Mensah",
            credits="3",
            schedule="Mon & Wed 10:00",
        )
        rows = courses.get_user_courses(self.uid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], full["id"])
        self.assertEqual(rows[0]["title"], "Data Structures & Algorithms")
        self.assertEqual(rows[0]["lecturer"], "Dr. Mensah")
        self.assertEqual(rows[0]["credits"], 3)

    def test_manual_add_then_stub_rerun_does_not_clobber(self):
        first = courses.upsert_course(
            self.uid, "DCIT 204", title="Real Title", credits="4"
        )
        # A later onboarding/rerun upsert with no details must not wipe them.
        courses.upsert_course(self.uid, "Dcit  204")
        rows = courses.get_user_courses(self.uid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], first["id"])
        self.assertEqual(rows[0]["title"], "Real Title")
        self.assertEqual(rows[0]["credits"], 4)

    def test_stub_renders_to_be_assigned_without_fake_id(self):
        courses.upsert_course(self.uid, "DCIT 204")
        row = courses.get_user_courses(self.uid)[0]
        self.assertEqual(row["code"], "DCIT 204")
        self.assertEqual(row["title"], "To be assigned")
        self.assertEqual(row["instructor"], "To be assigned")
        self.assertFalse(row["id"].startswith("CRS"))
        self.assertNotIn("CRS", row["code"] + row["title"])

    def test_uppercase_enforced_at_write_time(self):
        courses.upsert_course(self.uid, "uGRC 227")
        self.assertEqual(courses.get_user_courses(self.uid)[0]["code"], "UGRC 227")

    def test_auto_color_resolved_and_stable(self):
        a = courses.upsert_course(self.uid, "DCIT 204")
        b = courses.upsert_course(self.uid, "STAT 222")
        for c in (a, b):
            self.assertIn(c["color"], courses.COURSE_COLORS)
        # Same code -> same stored hue on every read (no per-render flutter).
        self.assertEqual(
            a["color"],
            courses.get_user_courses(self.uid)[0]["color"],
        )

    def test_courses_are_scoped_per_user(self):
        courses.upsert_course(self.uid, "DCIT 204")
        courses.upsert_course(self.other, "BIO 101")
        self.assertEqual(len(courses.get_user_courses(self.uid)), 1)
        self.assertEqual(courses.get_user_courses(self.uid)[0]["code"], "DCIT 204")
        self.assertEqual(len(courses.get_user_courses(self.other)), 1)
        self.assertIsNone(courses.get_course(self.uid, "nope"))

    def test_legacy_json_codes_become_stub_rows_once(self):
        def run_migration():
            conn = db._conn_context()
            try:
                db._migrate_legacy_courses(conn)
                conn.commit()
            finally:
                conn.close()

        db.update_user(self.uid, {"courses_json": json.dumps(["dcit 204", "Stat 222"])})
        run_migration()
        rows = courses.get_user_courses(self.uid)
        self.assertEqual([r["code"] for r in rows], ["DCIT 204", "STAT 222"])
        self.assertEqual(rows[0]["title"], "To be assigned")
        # Re-running the migration (every boot) must not duplicate anything.
        run_migration()
        self.assertEqual(len(courses.get_user_courses(self.uid)), 2)


class CoursesRoutesTest(unittest.TestCase):
    def setUp(self):
        self.uid = "courses-route-user"
        db._execute("DELETE FROM courses WHERE user_id = ?", (self.uid,))
        db._execute("DELETE FROM users WHERE id = ?", (self.uid,))
        db.create_user(self.uid, "Route Test", "route@example.com", "hash")

        from app import app
        app.config["TESTING"] = True
        self.client = app.test_client()
        with self.client.session_transaction() as s:
            s["user_id"] = self.uid
            s["user_name"] = "Route Test"

    def tearDown(self):
        db._execute("DELETE FROM courses WHERE user_id = ?", (self.uid,))
        db._execute("DELETE FROM users WHERE id = ?", (self.uid,))

    def test_dashboard_and_courses_page_agree(self):
        # Onboarding-style stubs, then a full Add-Course entry.
        courses.upsert_course(self.uid, "dcit 204")
        courses.upsert_course(self.uid, "STAT 222")
        self.client.post(
            "/courses",
            data={
                "code": "Dcit  208",
                "title": "Systems Programming",
                "lecturer": "Dr. Osei", "credits": "3",
                "description": "", "schedule": "", "color": "",
            },
        )

        dash = self.client.get("/dashboard").data.decode()
        page = self.client.get("/courses").data.decode()

        # Same canonical codes on both pages, uppercase, no fabricated "CRS".
        for code in ("DCIT 204", "STAT 222", "DCIT 208"):
            self.assertIn(code, dash)
            self.assertIn(code, page)
        self.assertNotIn("CRS ", dash)

        # Dashboard cards point at the real course detail rows.
        self.assertIn("<span class=\"course-card__title\">To be assigned</span>", dash)
        self.assertIn("Systems Programming", dash)

        rows = courses.get_user_courses(self.uid)
        self.assertEqual([r["code"] for r in rows], ["DCIT 204", "DCIT 208", "STAT 222"])

    def test_courses_post_creates_normalized_upserted_row(self):
        r = self.client.post(
            "/courses",
            data={
                "code": "dcit  204",
                "title": "Data Structures", "lecturer": "", "credits": "3",
                "description": "", "schedule": "", "color": "",
            },
        )
        self.assertEqual(r.status_code, 302)
        rows = courses.get_user_courses(self.uid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "DCIT 204")

    def test_courses_post_accepts_template_course_code_field(self):
        r = self.client.post(
            "/courses",
            data={
                "course_code": "math 101",
                "title": "Calculus", "lecturer": "", "credits": "3",
                "description": "", "schedule": "", "color": "",
            },
        )
        self.assertEqual(r.status_code, 302)
        rows = courses.get_user_courses(self.uid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["code"], "MATH 101")


if __name__ == "__main__":
    unittest.main()