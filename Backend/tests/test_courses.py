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

    def test_update_course_edits_fields_but_never_the_code(self):
        c = courses.upsert_course(
            self.uid, "DCIT 204",
            title="Old Title", lecturer="Dr. A", credits="3", schedule="Mon", color="navy",
        )
        updated = courses.update_course(
            self.uid, c["id"],
            title="New Title",
            lecturer="Dr. B",
            credits="4",
            schedule="Tue",
            description="Now with details",
            color="teal",
        )
        self.assertEqual(updated["code"], "DCIT 204")
        self.assertEqual(updated["title"], "New Title")
        self.assertEqual(updated["lecturer"], "Dr. B")
        self.assertEqual(updated["credits"], 4)
        self.assertEqual(updated["schedule"], "Tue")
        self.assertEqual(updated["description"], "Now with details")
        self.assertEqual(updated["color"], "teal")
        # Still exactly one row — the edit never spawns a sibling via upsert.
        self.assertEqual(len(courses.get_user_courses(self.uid)), 1)

    def test_update_course_is_scoped_to_owner(self):
        c = courses.upsert_course(self.uid, "DCIT 204", title="Mine")
        # Trying to edit someone else's row via the foreign id returns None.
        self.assertIsNone(courses.update_course(self.other, c["id"], title="Hijacked"))
        self.assertEqual(courses.get_course(self.uid, c["id"])["title"], "Mine")

    def test_update_course_clears_blank_fields_back_to_assigned(self):
        c = courses.upsert_course(self.uid, "DCIT 204", title="Titled", lecturer="Lecturer")
        courses.update_course(self.uid, c["id"], lecturer="")
        rows = courses.get_user_courses(self.uid)
        self.assertEqual(rows[0]["title"], "Titled")
        self.assertEqual(rows[0]["lecturer"], "To be assigned")

    def test_delete_course_removes_only_the_owned_row(self):
        a = courses.upsert_course(self.uid, "DCIT 204")
        courses.upsert_course(self.uid, "STAT 222")
        courses.upsert_course(self.other, "BIO 101")
        self.assertTrue(courses.delete_course(self.uid, a["id"]))
        rows = courses.get_user_courses(self.uid)
        self.assertEqual([r["code"] for r in rows], ["STAT 222"])
        # The other user's course is untouched.
        self.assertEqual(courses.get_user_courses(self.other)[0]["code"], "BIO 101")
        # Deleting again (or someone else's id) is a no-op.
        self.assertFalse(courses.delete_course(self.uid, a["id"]))


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

    def test_courses_update_route_edits_row_and_redirects(self):
        c = courses.upsert_course(self.uid, "DCIT 204", title="Old", color="navy")
        r = self.client.post(
            "/courses/" + c["id"] + "/update",
            data={"title": "Renamed", "lecturer": "Dr. X", "credits": "2",
                  "schedule": "", "description": "", "color": "teal"},
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 302)
        row = courses.get_course(self.uid, c["id"])
        self.assertEqual(row["title"], "Renamed")
        self.assertEqual(row["color"], "teal")

    def test_courses_delete_route_removes_row_and_redirects(self):
        c = courses.upsert_course(self.uid, "DCIT 204")
        r = self.client.post(
            "/courses/" + c["id"] + "/delete",
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 302)
        self.assertIsNone(courses.get_course(self.uid, c["id"]))


class CoursesPageModernizationTest(unittest.TestCase):
    """Invariants for the modernized courses page.

    The grid renders first (courses are the content); Add-a-course lives behind
    a triggered modal, the colour picker is see-and-pick swatches rather than
    a native name dropdown, and the live preview reflects the typed values.
    """

    def setUp(self):
        self.uid = "courses-page-user"
        db._execute("DELETE FROM courses WHERE user_id = ?", (self.uid,))
        db._execute("DELETE FROM users WHERE id = ?", (self.uid,))
        db.create_user(self.uid, "Page Test", "page@example.com", "hash")

        from app import app
        app.config["TESTING"] = True
        self.client = app.test_client()
        with self.client.session_transaction() as s:
            s["user_id"] = self.uid
            s["user_name"] = "Page Test"

    def tearDown(self):
        db._execute("DELETE FROM courses WHERE user_id = ?", (self.uid,))
        db._execute("DELETE FROM users WHERE id = ?", (self.uid,))

    def _page(self):
        r = self.client.get("/courses")
        self.assertEqual(r.status_code, 200)
        return r.data.decode()

    def test_courses_render_first_add_form_is_triggered_not_permanent(self):
        courses.upsert_course(self.uid, "DCIT 204")
        html = self._page()
        # The grid of existing courses comes first; the add form is inside a
        # hidden modal after it — not permanent page real estate above the fold.
        self.assertLess(html.index('class="courses-grid"'), html.index('id="add-course-modal"'))
        self.assertIn("course-card course-card--", html)
        self.assertIn('id="add-course-trigger"', html)
        # The old inline "Add a Course" card is gone.
        self.assertNotIn(">Add a Course<", html)

    def test_modal_is_a_hidden_dialog(self):
        html = self._page()
        self.assertIn('id="add-course-modal"', html)
        self.assertIn('class="modal"', html)
        self.assertIn('aria-hidden="true"', html)
        self.assertIn('role="dialog"', html)
        self.assertIn('aria-modal="true"', html)
        self.assertIn('aria-labelledby="add-course-title"', html)
        self.assertIn('class="modal__backdrop"', html)
        self.assertIn('id="cancel-add-course"', html)
        self.assertNotIn('class="dashboard-heading"', html)

    def test_color_is_swatch_picker_not_native_select(self):
        html = self._page()
        self.assertIn('role="radiogroup"', html)
        # Auto pill + six palette swatches, each aligning to the stored hues.
        self.assertEqual(html.count('class="color-swatch"'), 6)
        self.assertIn('class="color-swatch color-swatch--auto"', html)
        for hue in ("navy", "teal", "orange", "green", "purple", "red"):
            self.assertIn('data-color="%s"' % hue, html, hue)
        # No native <select> for the card colour (or anywhere on the page).
        self.assertNotIn("<select", html)

    def test_live_preview_updates_with_typed_values(self):
        html = self._page()
        self.assertIn('id="course-preview-card"', html)
        self.assertIn('id="preview-code"', html)
        self.assertIn('id="preview-title"', html)
        # Placeholder text matches what a stub row card renders.
        self.assertIn(">DCIT 204<", html)
        self.assertIn(">To be assigned<", html)
        # The hidden colour input keeps the no-JS fallback working.
        self.assertIn('name="color"', html)
        self.assertIn('type="hidden"', html)

    def test_field_grid_uses_consistent_responsive_layout(self):
        html = self._page()
        self.assertIn('class="course-form__grid"', html)
        self.assertIn('name="course_code"', html)
        self.assertIn('class="field"', html)
        self.assertIn("field--narrow", html)
        self.assertIn('name="credits"', html)
        self.assertIn('type="number"', html)
        self.assertIn('min="0"', html)
        self.assertIn('max="12"', html)
        self.assertIn('name="description"', html)
        self.assertIn('class="course-form__footer"', html)

    def test_post_accepts_new_course_code_field(self):
        r = self.client.post(
            "/courses",
            data={
                "course_code": "dcit  204",
                "title": "Picked from the new form", "credits": "3",
                "color": "purple",
            },
        )
        self.assertEqual(r.status_code, 302)
        row = courses.get_user_courses(self.uid)[0]
        self.assertEqual(row["code"], "DCIT 204")
        self.assertEqual(row["title"], "Picked from the new form")
        self.assertEqual(row["color"], "purple")


if __name__ == "__main__":
    unittest.main()