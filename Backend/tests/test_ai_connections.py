"""Tests for the per-user BYOK AI connections feature.

Covers the storage layer (obfuscated keys, per-user isolation, upsert, delete),
the /api/ai/connections endpoints (verification is monkeypatched so tests stay
hermetic — no network), the meta payload, per-user key routing on send_message,
and the Settings page panel.
"""

import os
import sys
import unittest
from unittest import mock

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

# Ensure real provider keys are not set for these tests.
for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_AI_API_KEY"):
    os.environ.pop(key, None)

import db
from app import app


def _clean(uid):
    for sql in (
        "DELETE FROM ai_messages WHERE user_id = ?",
        "DELETE FROM ai_conversations WHERE user_id = ?",
        "DELETE FROM ai_connections WHERE user_id = ?",
        "DELETE FROM ai_usage WHERE user_id = ?",
        "DELETE FROM courses WHERE user_id = ?",
        "DELETE FROM users WHERE id = ?",
    ):
        db._execute(sql, (uid,))


class AIConnectionsDBTest(unittest.TestCase):
    UID = "ai-conn-db-test-user"

    def setUp(self):
        _clean(self.UID)
        db.create_user(self.UID, "Conn Db Test", self.UID + "@example.com", "hash")

    def tearDown(self):
        _clean(self.UID)

    def test_set_get_and_masked_list(self):
        conn = db.set_ai_connection(self.UID, "openai", "sk-abcdef123456789", label="ChatGPT")
        self.assertEqual(conn["provider"], "openai")
        self.assertNotIn("sk-abcdef123456789", str(conn))
        # Key round-trips for routing.
        self.assertEqual(db.get_ai_connection_key(self.UID, "openai"), "sk-abcdef123456789")
        # Listed rows never expose the plaintext key.
        listed = db.get_ai_connections(self.UID)
        self.assertEqual(len(listed), 1)
        self.assertNotIn("sk-abcdef123456789", str(listed))
        self.assertEqual(db.get_ai_connection(self.UID, "openai")["provider"], "openai")

    def test_plaintext_never_stored(self):
        db.set_ai_connection(self.UID, "openai", "sk-super-secret-value")
        blob = db._query_one(
            "SELECT api_key_enc FROM ai_connections WHERE user_id = ? AND provider = ?",
            (self.UID, "openai"),
        )
        self.assertNotIn("sk-super-secret-value", (blob or {}).get("api_key_enc", ""))

    def test_per_user_isolation(self):
        db.set_ai_connection(self.UID, "anthropic", "sk-userA")
        db.set_ai_connection("ai-conn-other-user", "anthropic", "sk-userB")
        self.assertEqual(db.get_ai_connection_key(self.UID, "anthropic"), "sk-userA")
        self.assertEqual(db.get_ai_connection_key("ai-conn-other-user", "anthropic"), "sk-userB")
        self.assertIsNone(db.get_ai_connection_key(self.UID, "google"))
        self.assertEqual(len(db.get_ai_connections(self.UID)), 1)
        db._execute("DELETE FROM ai_connections WHERE user_id = ?", ("ai-conn-other-user",))

    def test_upsert_replaces_not_duplicates(self):
        db.set_ai_connection(self.UID, "openai", "sk-first", label="ChatGPT")
        db.set_ai_connection(self.UID, "openai", "sk-second", label="ChatGPT")
        self.assertEqual(len(db.get_ai_connections(self.UID)), 1)
        self.assertEqual(db.get_ai_connection_key(self.UID, "openai"), "sk-second")

    def test_delete(self):
        db.set_ai_connection(self.UID, "google", "ai-newkey")
        self.assertTrue(db.delete_ai_connection(self.UID, "google"))
        self.assertFalse(db.delete_ai_connection(self.UID, "google"))
        self.assertEqual(db.get_ai_connections(self.UID), [])

    def test_delete_user_removes_connections(self):
        db.set_ai_connection(self.UID, "openai", "sk-delete-me")
        db.delete_user(self.UID)
        self.assertIsNone(db.get_ai_connection_key(self.UID, "openai"))
        db.create_user(self.UID, "Conn Db Test", self.UID + "@example.com", "hash")


class AIConnectionsAPITest(unittest.TestCase):
    UID = "ai-conn-api-test-user"

    def setUp(self):
        _clean(self.UID)
        db.create_user(self.UID, "Conn Api Test", self.UID + "@example.com", "hash")
        app.config["TESTING"] = True
        self.client = app.test_client()
        with self.client.session_transaction() as s:
            s["user_id"] = self.UID
            s["user_name"] = "Conn Api Test"

    def tearDown(self):
        _clean(self.UID)

    def test_meta_includes_connections_and_mock_mode(self):
        r = self.client.get("/api/ai/meta")
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertIn("connections", data)
        self.assertIn("serverKeys", data)
        self.assertEqual(data["connections"], [])
        self.assertTrue(data["mockMode"])  # no server keys, no connections

    def test_connect_validates_and_persists(self):
        with mock.patch("ai.api._verify_provider_key", return_value=(True, "Works")):
            r = self.client.post(
                "/api/ai/connections",
                json={"provider": "openai", "apiKey": "sk-test-live-integration"},
            )
        self.assertEqual(r.status_code, 201)
        conn = r.get_json()["connection"]
        self.assertEqual(conn["provider"], "openai")
        self.assertNotIn("sk-test-live-integration", str(conn))
        listed = self.client.get("/api/ai/connections").get_json()["connections"]
        self.assertEqual([c["provider"] for c in listed], ["openai"])

    def test_connect_rejects_bad_provider_or_empty_key(self):
        r = self.client.post("/api/ai/connections", json={"provider": "nope", "apiKey": "x"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/api/ai/connections", json={"provider": "openai", "apiKey": "   "})
        self.assertEqual(r.status_code, 400)

    def test_connect_rejects_key_verification_failure(self):
        with mock.patch("ai.api._verify_provider_key", return_value=(False, "Key rejected by OpenAI (401).")):
            r = self.client.post(
                "/api/ai/connections",
                json={"provider": "openai", "apiKey": "sk-bad"},
            )
        self.assertEqual(r.status_code, 400)
        self.assertIn("rejected", r.get_json()["error"])
        self.assertEqual(db.get_ai_connections(self.UID), [])

    def test_disconnect(self):
        db.set_ai_connection(self.UID, "anthropic", "sk-claude-key")
        r = self.client.delete("/api/ai/connections/anthropic")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(db.get_ai_connections(self.UID), [])
        r = self.client.delete("/api/ai/connections/anthropic")
        self.assertEqual(r.status_code, 404)

    def test_meta_mock_mode_clears_when_connected(self):
        db.set_ai_connection(self.UID, "anthropic", "sk-claude-key")
        data = self.client.get("/api/ai/meta").get_json()
        self.assertFalse(data["mockMode"])

    def test_send_message_uses_personal_key_for_routing(self):
        db.set_ai_connection(self.UID, "anthropic", "sk-personal-claude")
        captured = {}
        from ai.providers import MockProvider

        def fake_get_provider(provider_id, api_key=None):
            captured["provider"] = provider_id
            captured["api_key"] = api_key
            return MockProvider()

        conv = self.client.post(
            "/api/ai/conversations", json={"model": "claude", "mode": "ask"}
        ).get_json()["conversation"]
        with mock.patch("ai.service.get_provider", side_effect=fake_get_provider):
            r = self.client.post(
                f"/api/ai/conversations/{conv['id']}/messages",
                json={"message": "Explain recursion please", "model": "claude"},
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(captured["provider"], "anthropic")
        self.assertEqual(captured["api_key"], "sk-personal-claude")


class AIConnectionsSettingsTest(unittest.TestCase):
    UID = "ai-conn-settings-test-user"

    def setUp(self):
        _clean(self.UID)
        db.create_user(self.UID, "Conn Settings Test", self.UID + "@example.com", "hash")
        app.config["TESTING"] = True
        self.client = app.test_client()
        with self.client.session_transaction() as s:
            s["user_id"] = self.UID
            s["user_name"] = "Conn Settings Test"

    def tearDown(self):
        _clean(self.UID)

    def test_settings_page_renders_ai_accounts_panel(self):
        r = self.client.get("/settings")
        self.assertEqual(r.status_code, 200)
        html = r.data.decode()
        self.assertIn('id="ai-accounts-heading"', html)
        expected = {
            "anthropic": "https://platform.claude.com/settings/keys",
            "openai": "https://platform.openai.com/api-keys",
            "google": "https://aistudio.google.com/apikey",
            "deepseek": "https://platform.deepseek.com/api_keys",
            "copilot": "https://github.com/settings/personal-access-tokens",
        }
        for provider, label in (("anthropic", "Claude"), ("openai", "ChatGPT"),
                                ("google", "Gemini"), ("deepseek", "DeepSeek"),
                                ("copilot", "Copilot")):
            self.assertIn(f'data-ai-connect="{provider}"', html)
            self.assertIn(f'data-ai-key-url="{expected[provider]}"', html)
            self.assertIn(label, html)
        self.assertIn('id="ai-mock-hint"', html)


if __name__ == "__main__":
    unittest.main()