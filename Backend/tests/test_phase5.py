"""Tests for the ICS export, report, and Settings notification preference."""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import db
from app import app


class Phase5RoutesTest(unittest.TestCase):
    def setUp(self):
        self.uid = "phase5-test-user"
        db._execute("DELETE FROM users WHERE id = ?", (self.uid,))
        db.create_user(self.uid, "Phase5", "phase5@example.com", "hash")
        app.config["TESTING"] = True
        self.client = app.test_client()
        with self.client.session_transaction() as s:
            s["user_id"] = self.uid
            s["user_name"] = "Phase5"

    def tearDown(self):
        db._execute("DELETE FROM users WHERE id = ?", (self.uid,))

    def test_report_route_ok(self):
        r = self.client.get("/report")
        self.assertEqual(r.status_code, 200)

    def test_calendar_export_is_ics(self):
        r = self.client.get("/calendar/export")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "text/calendar")
        self.assertIn(b"BEGIN:VCALENDAR", r.data)
        self.assertIn(b"END:VCALENDAR", r.data)

    def test_settings_preference_toggle_and_preview(self):
        r = self.client.get("/settings")
        self.assertEqual(r.status_code, 200)
        self.assertIn("notify_digest".encode(), r.data)
        self.assertFalse(db.get_user(self.uid).get("notify_digest"))

        r = self.client.post("/settings/notifications", data={"notify_digest": "on"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(db.get_user(self.uid).get("notify_digest"))


class OnboardingWizardTest(unittest.TestCase):
    """The five-step onboarding wizard: draft persistence, validation, and finish."""

    def setUp(self):
        self.uid = "onboarding-test-user"
        db._execute("DELETE FROM courses WHERE user_id = ?", (self.uid,))
        db._execute("DELETE FROM users WHERE id = ?", (self.uid,))
        db.create_user(self.uid, "Sam Test", "onboard@example.com", "hash")
        app.config["TESTING"] = True
        self.client = app.test_client()
        with self.client.session_transaction() as s:
            s["user_id"] = self.uid
            s["user_name"] = "Sam Test"

    def tearDown(self):
        # The wizard now writes courses rows; clear them before the user (FK).
        db._execute("DELETE FROM courses WHERE user_id = ?", (self.uid,))
        db._execute("DELETE FROM users WHERE id = ?", (self.uid,))

    def post_step(self, step, school="", program="", courses="", goals="", hours="4", action="continue"):
        return self.client.post("/onboarding", data={
            "step": str(step), "action": action,
            "school": school, "program": program,
            "courses": courses, "goals": goals, "available_hours": hours,
        })

    def test_stepper_markup_and_no_duplicate_greeting(self):
        r = self.client.get("/onboarding")
        self.assertEqual(r.status_code, 200)
        # No native fieldset border, no green "Welcome…" alert box on this page.
        self.assertNotIn(b"<legend", r.data)
        self.assertNotIn(b"flashes", r.data)
        self.assertIn(b"aria-current", r.data)
        self.assertIn(b"onboarding-stepper__list", r.data)
        self.assertEqual(r.data.count(b"class=\"onboarding-stepper__item"), 5)
        # Step 1 greeting comes from the header, not a flash.
        self.assertIn("Sam".encode(), r.data)

    def test_step_argument_clamped(self):
        for bad, expected in (("0", "Step 1 of 5"), ("9", "Step 5 of 5"), ("abc", "Step 1 of 5")):
            r = self.client.get(f"/onboarding?step={bad}")
            self.assertEqual(r.status_code, 200)
            self.assertIn(expected.encode(), r.data)

    def test_full_wizard_completes_and_drafts_accumulate(self):
        # Step 1 advances and saves a draft without completing onboarding.
        r = self.post_step(1, school="UG")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/onboarding?step=2"))
        user = db.get_user(self.uid)
        self.assertEqual(user["school"], "UG")
        self.assertIs(user["onboarded"], False)

        r = self.post_step(2, school="UG", program="CS")
        self.assertTrue(r.headers["Location"].endswith("/onboarding?step=3"))
        self.assertIs(db.get_user(self.uid)["onboarded"], False)

        r = self.post_step(3, school="UG", program="CS", courses="DCIT 204, STAT 222")
        self.assertTrue(r.headers["Location"].endswith("/onboarding?step=4"))
        self.assertEqual(db.get_user(self.uid)["courses"], ["DCIT 204", "STAT 222"])

        r = self.post_step(4, school="UG", program="CS", courses="DCIT 204, STAT 222", goals="pass")
        self.assertTrue(r.headers["Location"].endswith("/onboarding?step=5"))

        # Final step: still a plain continue, but this one completes onboarding.
        r = self.post_step(5, school="UG", program="CS", courses="DCIT 204, STAT 222", goals="pass")
        user = db.get_user(self.uid)
        self.assertTrue(user["onboarded"])
        self.assertEqual(user["goals"], "pass")
        self.assertEqual(user["available_hours"], 4)

    def test_blank_required_field_stays_on_step(self):
        r = self.post_step(2, program="")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/onboarding?step=2"))
        # Error flash set but profile not flagged as complete.
        self.assertIs(db.get_user(self.uid)["onboarded"], False)

    def test_finish_later_keeps_draft_and_bails(self):
        r = self.post_step(2, school="UG", program="CS", action="finish-later")
        self.assertEqual(r.status_code, 302)
        user = db.get_user(self.uid)
        self.assertEqual(user["program"], "CS")
        self.assertIs(user["onboarded"], False)

    def test_available_hours_clamped(self):
        r = self.post_step(1, school="UG", hours="99")
        user = db.get_user(self.uid)
        self.assertEqual(user["available_hours"], 12)
        r = self.post_step(1, school="UG", hours="abc")
        self.assertEqual(db.get_user(self.uid)["available_hours"], 4)


if __name__ == "__main__":
    unittest.main()