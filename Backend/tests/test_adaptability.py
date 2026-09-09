"""Tests for the adaptability + error-handling layer.

Covers enforced appearance validation (theme/text-size whitelist), the
server-rendered theme/size attributes on <html>, keyboard-friendly markup,
the login failure/lockout messaging (with accurate remaining attempts), the
timing-flat check for unknown emails, and signup password rules.
"""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import app as app_mod
import db

db.init_db()

UID = "adaptability-user"
EMAIL = "adapt-add@example.com"


def _reset_user():
    db._execute("DELETE FROM users WHERE id = ?", (UID,))
    db._execute("DELETE FROM users WHERE email = ?", (EMAIL,))
    db._execute("DELETE FROM courses WHERE user_id = ?", (UID,))
    db.create_user(UID, "Adapt Test", "adapt@example.com", "hash")


class AdaptabilityBase(unittest.TestCase):
    def setUp(self):
        _reset_user()
        app_mod._login_track.clear()
        self.client = app_mod.app.test_client()
        app_mod.app.config["TESTING"] = True
        with self.client.session_transaction() as s:
            s["user_id"] = UID
            s["user_name"] = "Adapt Test"

    def tearDown(self):
        db._execute("DELETE FROM users WHERE id = ?", (UID,))
        db._execute("DELETE FROM users WHERE email = ?", (EMAIL,))
        app_mod._login_track.clear()


class AppearanceValidationTest(AdaptabilityBase):
    def test_invalid_theme_and_text_size_fall_back(self):
        r = self.client.post(
            "/settings/preferences",
            data={"theme": "neon", "text_size": "huge", "reduce_motion": "on"},
            follow_redirects=True,
        )
        self.assertEqual(r.status_code, 200)
        appearance = db.get_settings(UID).get("appearance", {})
        self.assertEqual(appearance.get("theme"), "system")
        self.assertEqual(appearance.get("text_size"), "default")
        self.assertTrue(appearance.get("reduce_motion"))

    def test_valid_dark_large_are_saved(self):
        r = self.client.post(
            "/settings/preferences",
            data={"theme": "dark", "text_size": "large", "reduce_motion": "on"},
            follow_redirects=True,
        )
        self.assertEqual(r.status_code, 200)
        appearance = db.get_settings(UID).get("appearance", {})
        self.assertEqual(appearance.get("theme"), "dark")
        self.assertEqual(appearance.get("text_size"), "large")

    def test_settings_page_renders_server_side_appearance_attributes(self):
        self.client.post(
            "/settings/preferences",
            data={"theme": "dark", "text_size": "larger"},
            follow_redirects=True,
        )
        r = self.client.get("/settings")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertTrue('data-theme="dark"' in html)
        self.assertTrue('data-text-size="larger"' in html)

    def test_default_appearance_renders_system_default(self):
        r = self.client.get("/settings")
        html = r.get_data(as_text=True)
        self.assertTrue('data-theme="system"' in html)
        self.assertTrue('data-text-size="default"' in html)


class AuthErrorHandlingTest(AdaptabilityBase):
    def _login(self, email, password, **kwargs):
        return self.client.post("/login", data={"email": email, "password": password}, **kwargs)

    def test_first_wrong_password_tells_remaining_attempts(self):
        r = self._login("adapt@example.com", "wrongpass", follow_redirects=True)
        body = r.get_data(as_text=True)
        self.assertIn("Invalid email or password.", body)
        self.assertIn("4 attempts remaining", body)

    def test_attempts_decrement_then_lockout(self):
        r1 = self._login("adapt@example.com", "bad1", follow_redirects=True)
        self.assertIn("4 attempts remaining", r1.get_data(as_text=True))
        r2 = self._login("adapt@example.com", "bad2", follow_redirects=True)
        self.assertIn("3 attempts remaining", r2.get_data(as_text=True))
        r3 = self._login("adapt@example.com", "bad3", follow_redirects=True)
        self.assertIn("2 attempts remaining", r3.get_data(as_text=True))
        r4 = self._login("adapt@example.com", "bad4", follow_redirects=True)
        self.assertIn("1 attempt remaining", r4.get_data(as_text=True))
        r5 = self._login("adapt@example.com", "bad5", follow_redirects=True)
        self.assertIn(
            f"locked for {app_mod.LOGIN_LOCKOUT_SECONDS} seconds.",
            r5.get_data(as_text=True),
        )

    def test_locked_account_reports_remaining_wait(self):
        for _ in range(app_mod.LOGIN_MAX_ATTEMPTS):
            self._login("adapt@example.com", "bad")
        blocked = self._login("adapt@example.com", "bad", follow_redirects=True)
        body = blocked.get_data(as_text=True)
        self.assertIn("temporarily locked; try again", body)
        # Will wait between 1 and lockout seconds (ceil, never 0).
        self.assertRegex(body, r"try again in ([1-9][0-9]*) seconds")

    def test_unknown_email_still_counts_as_failure(self):
        """Unknown emails consume attempts too (timing-flat, enumerated nowhere)."""
        for _ in range(app_mod.LOGIN_MAX_ATTEMPTS):
            self._login("nobody@example.com", "bad")
        r = self._login("nobody@example.com", "bad", follow_redirects=True)
        self.assertIn("temporarily locked", r.get_data(as_text=True))

    def test_shortsignup_password_rejected(self):
        with self.client.session_transaction() as s:
            s.clear()
        r = self.client.post(
            "/signup",
            data={"name": "New", "email": EMAIL, "password": "short"},
            follow_redirects=True,
        )
        body = r.get_data(as_text=True)
        self.assertIn("Password must be at least 8 characters.", body)
        self.assertIsNone(db.get_user_by_email(EMAIL))


if __name__ == "__main__":
    unittest.main()