"""Tests for "Sign in with Google" (OAuth 2.0 + PKCE) and password methods.

The two real Google endpoints (token exchange and id_token verification) are
patched out so the suite runs offline; PKCE helpers are tested on their own
against the spec, and the route-level flows are verified end to end with
mocked claims.
"""

import os
import sys
import unittest
from unittest import mock

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import db
import google_auth

db.init_db()

UID = "google-auth-user"
GOOGLE_EMAIL = "google-user@example.com"
GOOGLE_SUB = "gsub-1234567890"
CLIENT_ID = "1234567890-test.apps.googleusercontent.com"


def _reset():
    for email in (GOOGLE_EMAIL, "manual@example.com", "linked@example.com", "cfg@example.com"):
        db._execute("DELETE FROM users WHERE email = ?", (email,))
    db._execute("DELETE FROM app_config WHERE key = 'google_client_id'")


class PkceHelpersTest(unittest.TestCase):
    def test_verifier_shape(self):
        v = google_auth.new_code_verifier()
        alphabet = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        self.assertLessEqual(set(v), alphabet)
        self.assertGreaterEqual(len(v), 43)
        self.assertLessEqual(len(v), 128)

    def test_challenge_is_sha256_b64url(self):
        v = "aB3x-" * 14
        expected = google_auth._b64url(__import__("hashlib").sha256(v.encode("ascii")).digest())
        self.assertEqual(google_auth.code_challenge(v), expected)

    def test_authorize_url_carries_pkce_and_redirect(self):
        url = google_auth.authorize_url(CLIENT_ID, "http://localhost/oauth/google/callback", "st", "ch")
        self.assertIn("accounts.google.com/o/oauth2/v2/auth", url)
        self.assertIn("client_id=" + CLIENT_ID, url)
        self.assertIn("redirect_uri=http%3A%2F%2Flocalhost%2Foauth%2Fgoogle%2Fcallback", url)
        self.assertIn("code_challenge=ch", url)
        self.assertIn("code_challenge_method=S256", url)
        self.assertIn("state=st", url)

    def test_verify_id_token_rejects_wrong_audience(self):
        with mock.patch.object(google_auth, "_get_json", return_value={"aud": "someone-else"}):
            with self.assertRaises(google_auth.GoogleAuthError):
                google_auth.verify_id_token("tok", CLIENT_ID)

    def test_verify_id_token_accepts_verified_claims(self):
        claims = {
            "aud": CLIENT_ID,
            "iss": "https://accounts.google.com",
            "sub": GOOGLE_SUB,
            "email": GOOGLE_EMAIL,
            "email_verified": True,
            "name": "Google User",
        }
        with mock.patch.object(google_auth, "_get_json", return_value=claims):
            self.assertEqual(google_auth.verify_id_token("tok", CLIENT_ID)["email"], GOOGLE_EMAIL)


class GoogleFlowRouteTest(unittest.TestCase):
    def setUp(self):
        _reset()
        self.client = google_test_client()

    def tearDown(self):
        _reset()

    def _start(self, **kwargs):
        return self.client.get("/oauth/google/start", **kwargs)

    def _start_with_id(self):
        db.set_app_config("google_client_id", CLIENT_ID)
        return self._start()

    def test_start_without_config_hints_were_to_enable(self):
        r = self._start(follow_redirects=True)
        self.assertIn("set up yet", r.get_data(as_text=True))

    def test_start_redirects_to_google_and_stores_pkce_state(self):
        r = self._start_with_id()
        self.assertEqual(r.status_code, 302)
        self.assertIn("accounts.google.com", r.headers["Location"])
        with self.client.session_transaction() as s:
            self.assertTrue(s.get("google_state"))
            self.assertTrue(s.get("google_verifier"))

    def test_callback_rejects_bad_state(self):
        self._start_with_id()
        r = self.client.get("/oauth/google/callback?state=wrong&code=abc", follow_redirects=True)
        self.assertIn("invalid or expired", r.get_data(as_text=True))
        self.assertIsNone(db.get_user_by_email(GOOGLE_EMAIL))

    def _claims(self):
        return {
            "aud": CLIENT_ID,
            "iss": "https://accounts.google.com",
            "sub": GOOGLE_SUB,
            "email": GOOGLE_EMAIL,
            "email_verified": True,
            "name": "Google User",
        }

    def test_callback_creates_account_for_new_email(self):
        self._start_with_id()
        with self.client.session_transaction() as s:
            state = s["google_state"]
        with mock.patch.object(google_auth, "exchange_code", return_value={"id_token": "tok"}), \
             mock.patch.object(google_auth, "verify_id_token", return_value=self._claims()):
            r = self.client.get(f"/oauth/google/callback?state={state}&code=abc")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/onboarding"))
        user = db.get_user_by_email(GOOGLE_EMAIL)
        self.assertIsNotNone(user)
        self.assertEqual(user.get("google_sub"), GOOGLE_SUB)
        with self.client.session_transaction() as s:
            self.assertEqual(s["user_id"], user["id"])

    def test_callback_links_existing_manual_account(self):
        db.create_user("manual-1", "Manual", "linked@example.com", "hash")
        self._start_with_id()
        with self.client.session_transaction() as s:
            state = s["google_state"]
        claims = self._claims()
        claims["email"] = "linked@example.com"
        with mock.patch.object(google_auth, "exchange_code", return_value={"id_token": "tok"}), \
             mock.patch.object(google_auth, "verify_id_token", return_value=claims):
            r = self.client.get(f"/oauth/google/callback?state={state}&code=abc")
        self.assertIn(r.headers["Location"], ("/", "/dashboard"))
        self.assertEqual(db.get_user("manual-1").get("google_sub"), GOOGLE_SUB)

    def test_callback_recognizes_existing_google_account(self):
        db.create_user("google-1", "Existing", GOOGLE_EMAIL, "", google_sub=GOOGLE_SUB)
        self._start_with_id()
        with self.client.session_transaction() as s:
            state = s["google_state"]
        with mock.patch.object(google_auth, "exchange_code", return_value={"id_token": "tok"}), \
             mock.patch.object(google_auth, "verify_id_token", return_value=self._claims()):
            r = self.client.get(f"/oauth/google/callback?state={state}&code=abc")
        self.assertIn(r.headers["Location"], ("/", "/dashboard"))
        with self.client.session_transaction() as s:
            self.assertEqual(s["user_id"], "google-1")


def google_test_client():
    import app as app_mod

    app_mod.app.config["TESTING"] = True
    c = app_mod.app.test_client()
    with c.session_transaction() as s:
        s.clear()
    return c


class PasswordMethodTest(unittest.TestCase):
    def setUp(self):
        _reset()
        import app as app_mod

        app_mod._login_track.clear()
        self.client = google_test_client()

    def tearDown(self):
        _reset()
        import app as app_mod

        app_mod._login_track.clear()

    def _login_client(self, uid="google-1"):
        with self.client.session_transaction() as s:
            s["user_id"] = uid
            s["user_name"] = "Google User"

    def test_google_only_account_cannot_use_password(self):
        db.create_user("google-1", "Google User", GOOGLE_EMAIL, "", google_sub=GOOGLE_SUB)
        r = self.client.post(
            "/login",
            data={"email": GOOGLE_EMAIL, "password": "whatever123"},
            follow_redirects=True,
        )
        body = r.get_data(as_text=True)
        self.assertIn("signs in with Google", body)
        self.assertNotIn("attempts remaining", body)

    def test_set_password_then_manual_login_works(self):
        db.create_user("google-1", "Google User", GOOGLE_EMAIL, "", google_sub=GOOGLE_SUB)
        self._login_client()
        r = self.client.post(
            "/settings/password",
            data={"new_password": "new-secure-pass", "confirm_password": "new-secure-pass"},
            follow_redirects=True,
        )
        self.assertIn("Password saved", r.get_data(as_text=True))
        # Manual login now works for the same account.
        self.client.post("/logout")
        r = self.client.post(
            "/login",
            data={"email": GOOGLE_EMAIL, "password": "new-secure-pass"},
            follow_redirects=True,
        )
        self.assertIn("Welcome back", r.get_data(as_text=True))

    def test_short_password_rejected(self):
        db.create_user("google-1", "Google User", GOOGLE_EMAIL, "hash", google_sub=GOOGLE_SUB)
        self._login_client()
        r = self.client.post(
            "/settings/password",
            data={"current_password": "hash", "new_password": "short", "confirm_password": "short"},
            follow_redirects=True,
        )
        self.assertIn("at least 8 characters", r.get_data(as_text=True))

    def test_change_password_requires_current(self):
        db.create_user("manual-1", "Manual", "manual@example.com", make_hash("old-pass-1"))
        self._login_client("manual-1")
        r = self.client.post(
            "/settings/password",
            data={"current_password": "wrong", "new_password": "new-secure-pass", "confirm_password": "new-secure-pass"},
            follow_redirects=True,
        )
        self.assertIn("Current password is incorrect", r.get_data(as_text=True))


def make_hash(value):
    from werkzeug.security import generate_password_hash

    return generate_password_hash(value)


class SigninConfigTest(unittest.TestCase):
    def setUp(self):
        _reset()
        db.create_user("cfg-user", "Cfg", "cfg@example.com", "hash")
        self.client = google_test_client()
        with self.client.session_transaction() as s:
            s["user_id"] = "cfg-user"
            s["user_name"] = "Cfg"

    def tearDown(self):
        _reset()

    def test_invalid_client_id_rejected(self):
        r = self.client.post(
            "/settings/signin",
            data={"google_client_id": "not a valid id!"},
            follow_redirects=True,
        )
        self.assertIn("Client ID (they end in", r.get_data(as_text=True))

    def test_valid_client_id_saved_and_removed(self):
        r = self.client.post(
            "/settings/signin",
            data={"google_client_id": CLIENT_ID},
            follow_redirects=True,
        )
        self.assertIn("Google sign-in is set up", r.get_data(as_text=True))
        self.assertEqual(db.get_app_config("google_client_id"), CLIENT_ID)
        r = self.client.post("/settings/signin", data={"remove": "1"}, follow_redirects=True)
        self.assertIn("Google sign-in removed", r.get_data(as_text=True))
        self.assertEqual(db.get_app_config("google_client_id"), "")


if __name__ == "__main__":
    unittest.main()