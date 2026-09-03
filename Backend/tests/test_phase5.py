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


if __name__ == "__main__":
    unittest.main()