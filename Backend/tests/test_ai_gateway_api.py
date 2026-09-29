"""Tests for the AI Gateway's student-facing HTTP surface (Phase 4).

The browser may only see friendly names, availability and quota numbers. These
tests pin that contract down, especially the parts that would be a privacy leak
if they regressed: no API key, no base URL, no env var name and no internal
provider id may appear in a response body.
"""

import json
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("DATABASE_PATH", os.path.join(os.path.expanduser("~"), "ai-gw-api-test.db"))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import db  # noqa: E402

db.init_db()

from ai_gateway import gateway, models_seed, quotas, registry  # noqa: E402
from ai_gateway.api import gateway_api  # noqa: E402
from ai_gateway.config import GatewayConfig  # noqa: E402

UID = "gateway-api-test-user"
MINOR_UID = "gateway-api-minor-user"


def _flask_app():
    from flask import Flask

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.secret_key = "test"
    app.register_blueprint(gateway_api)
    return app


def _clean():
    for uid in (UID, MINOR_UID):
        for sql in (
            "DELETE FROM ai_gateway_usage WHERE user_id = ?",
            "DELETE FROM ai_connections WHERE user_id = ?",
            "DELETE FROM ai_user_prefs WHERE user_id = ?",
            "DELETE FROM users WHERE id = ?",
        ):
            db._execute(sql, (uid,))
    db._execute("DELETE FROM ai_provider_health")
    registry._seeded = False
    registry.ensure_seeded()
    quotas.seed_quota_rules()
    # seed_quota_rules() is deliberately DO NOTHING so admin limits survive a
    # restart, which means it will not undo a limit a test edited. Restore the
    # defaults explicitly, otherwise one test's edit leaks into every later one.
    for tier, (reqs, docs, tokens) in quotas._DEFAULTS.items():
        db._execute(
            "UPDATE ai_quota_rules SET daily_requests = ?, daily_documents = ?, "
            "daily_tokens = ? WHERE tier = ? AND user_group = 'students'",
            (reqs, docs or 0, tokens, tier),
        )
    for p in models_seed.PROVIDERS:
        db._execute(
            "UPDATE ai_providers SET is_enabled = ?, may_train_on_data = ?, "
            "allowed_for_minors = ? WHERE slug = ?",
            (p["is_enabled"], p["may_train_on_data"], p["allowed_for_minors"], p["slug"]),
        )


def _login(app, uid):
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["user_id"] = uid
        yield c


class _Base(unittest.TestCase):
    def setUp(self):
        _clean()
        db.create_user(UID, "Api", UID + "@example.com", "hash")
        self.app = _flask_app()

    def tearDown(self):
        _clean()

    def get_json(self, url, uid=UID):
        with self.app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user_id"] = uid
            resp = c.get(url)
            self.assertEqual(resp.status_code, 200, resp.data[:400])
            return json.loads(resp.data)

    def put_json(self, url, payload, uid=UID, expect=200):
        with self.app.test_client() as c:
            with c.session_transaction() as sess:
                sess["user_id"] = uid
            resp = c.put(url, json=payload)
            self.assertEqual(resp.status_code, expect, resp.data[:400])
            return json.loads(resp.data) if resp.data else {}


class AuthTest(_Base):
    def test_models_requires_login(self):
        with self.app.test_client() as c:
            self.assertEqual(c.get("/api/ai-gateway/models").status_code, 401)

    def test_usage_requires_login(self):
        with self.app.test_client() as c:
            self.assertEqual(c.get("/api/ai-gateway/usage").status_code, 401)

    def test_saving_a_model_requires_login(self):
        with self.app.test_client() as c:
            resp = c.put("/api/ai-gateway/models", json={"modelId": "openai/gpt-5-mini"})
        self.assertEqual(resp.status_code, 401)


class ModelListTest(_Base):
    def test_returns_friendly_names_and_tier_labels(self):
        with mock.patch.object(GatewayConfig, "server_keys", {"OPENAI_API_KEY": "sk-x"}):
            data = self.get_json("/api/ai-gateway/models")
        models = {m["id"]: m for m in data["models"]}
        self.assertIn("openai/gpt-5-mini", models)
        row = models["openai/gpt-5-mini"]
        # Student-facing labels, not vendor slugs or model ids.
        self.assertEqual(row["name"], "ChatGPT Mini")
        self.assertEqual(row["provider"], "ChatGPT")
        self.assertEqual(row["tierLabel"], "Premium")
        self.assertIs(row["available"], True)

    def test_tier_label_is_derived_from_the_tier(self):
        with mock.patch.object(GatewayConfig, "server_keys", {"OPENAI_API_KEY": "sk-x"}):
            data = self.get_json("/api/ai-gateway/models")
        expected = {"free": "Free", "standard": "Standard", "premium": "Premium"}
        for row in data["models"]:
            self.assertEqual(row["tierLabel"], expected[row["tier"]])

    def test_never_leaks_secrets_or_internals(self):
        with mock.patch.object(
            GatewayConfig,
            "server_keys",
            {"OPENAI_API_KEY": "sk-super-secret", "ANTHROPIC_API_KEY": "sk-ant-secret"},
        ):
            raw = self.app.test_client()
            with raw.session_transaction() as sess:
                sess["user_id"] = UID
            body = raw.get("/api/ai-gateway/models").data.decode()
        for leak in (
            "sk-super-secret",
            "sk-ant-secret",
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "api.openai.com",
            "api.anthropic.com",
            "base_url",
            "env_key_name",
        ):
            self.assertNotIn(leak, body, f"{leak!r} leaked to the browser")

    def test_unavailable_model_is_shown_not_hidden(self):
        with mock.patch.object(GatewayConfig, "server_keys", {"OPENAI_API_KEY": "sk-x"}):
            data = self.get_json("/api/ai-gateway/models")
        models = {m["id"]: m for m in data["models"]}
        # No anthropic key configured, so Claude cannot be reached.
        claude = models["anthropic/claude-sonnet-4-5"]
        self.assertIs(claude["available"], False)
        self.assertEqual(claude["unavailableReason"], "Temporarily unavailable")

    def test_open_breaker_is_reported_as_unavailable(self):
        with mock.patch.object(GatewayConfig, "server_keys", {"OPENAI_API_KEY": "sk-x"}):
            with mock.patch.object(GatewayConfig, "circuit_failure_threshold", 1):
                gateway.health.record_failure("openai", "down")
            data = self.get_json("/api/ai-gateway/models")
        models = {m["id"]: m for m in data["models"]}
        self.assertIs(models["openai/gpt-5-mini"]["available"], False)

    def test_personal_key_makes_a_model_reachable(self):
        """A student with only their own BYOK key still gets a working picker."""
        db.set_ai_connection(UID, "openai", "sk-personal")
        with mock.patch.object(GatewayConfig, "server_keys", {}):
            data = self.get_json("/api/ai-gateway/models")
        models = {m["id"]: m for m in data["models"]}
        self.assertIs(models["openai/gpt-5-mini"]["available"], True)

    def test_models_report_which_key_pays_without_leaking_it(self):
        """The picker badges personal-key models so a surprise bill is visible."""
        db.set_ai_connection(UID, "openai", "sk-personal")
        keys = {"ANTHROPIC_API_KEY": "sk-server"}
        with mock.patch.object(GatewayConfig, "server_keys", keys):
            data = self.get_json("/api/ai-gateway/models")
        models = {m["id"]: m for m in data["models"]}
        self.assertEqual(models["openai/gpt-5-mini"]["paidBy"], "personal")
        self.assertEqual(models["anthropic/claude-sonnet-4-5"]["paidBy"], "server")
        # Only which key pays, never the key itself.
        body = json.dumps(data)
        self.assertNotIn("sk-personal", body)
        self.assertNotIn("sk-server", body)

    def test_server_key_wins_over_a_personal_one(self):
        db.set_ai_connection(UID, "openai", "sk-personal")
        with mock.patch.object(GatewayConfig, "server_keys", {"OPENAI_API_KEY": "sk-server"}):
            data = self.get_json("/api/ai-gateway/models")
        models = {m["id"]: m for m in data["models"]}
        self.assertEqual(models["openai/gpt-5-mini"]["paidBy"], "server")

    def test_minor_does_not_see_training_providers(self):
        db.create_user(MINOR_UID, "Minor", MINOR_UID + "@example.com", "hash")
        db._execute(
            "UPDATE users SET user_group = 'minors' WHERE id = ?", (MINOR_UID,)
        )
        db._execute(
            "UPDATE ai_providers SET may_train_on_data = 1, allowed_for_minors = 0 "
            "WHERE slug = 'openai'"
        )
        keys = {"OPENAI_API_KEY": "sk-x", "ANTHROPIC_API_KEY": "sk-y"}
        with mock.patch.object(GatewayConfig, "server_keys", keys):
            data = self.get_json("/api/ai-gateway/models", uid=MINOR_UID)
        models = {m["id"]: m for m in data["models"]}
        self.assertIs(models["openai/gpt-5-mini"]["available"], False)
        self.assertIs(models["anthropic/claude-sonnet-4-5"]["available"], True)


class UsageTest(_Base):
    def test_usage_starts_at_zero_with_a_limit(self):
        with mock.patch.object(GatewayConfig, "server_keys", {"OPENAI_API_KEY": "sk-x"}):
            data = self.get_json("/api/ai-gateway/models")
        usage = data["usage"]
        self.assertEqual(usage["requestsUsed"], 0)
        self.assertIsInstance(usage["requestsLimit"], int)
        self.assertEqual(usage["percent"], 0)

    def test_usage_endpoint_standalone(self):
        data = self.get_json("/api/ai-gateway/usage")
        self.assertIn("requestsUsed", data)
        self.assertIn("requestsLimit", data)

    def test_percent_is_capped_and_computed_server_side(self):
        db._execute(
            "UPDATE ai_quota_rules SET daily_requests = 10 "
            "WHERE tier = 'free' AND user_group = 'students'"
        )
        db._execute(
            "INSERT INTO ai_gateway_usage "
            "(id, user_id, day, model_id, feature, requests, used_own_key) "
            "VALUES ('u1', ?, ?, 'm', 'chat', 5, 0)",
            (UID, quotas._day()),
        )
        data = self.get_json("/api/ai-gateway/usage")
        self.assertEqual(data["requestsUsed"], 5)
        self.assertEqual(data["requestsLimit"], 10)
        self.assertEqual(data["percent"], 50)

    def test_personal_key_usage_does_not_consume_the_meter(self):
        db._execute(
            "INSERT INTO ai_gateway_usage "
            "(id, user_id, day, model_id, feature, requests, used_own_key) "
            "VALUES ('u2', ?, ?, 'm', 'chat', 9, 1)",
            (UID, quotas._day()),
        )
        self.assertEqual(self.get_json("/api/ai-gateway/usage")["requestsUsed"], 0)


class PreferenceTest(_Base):
    def test_defaults_to_auto(self):
        with mock.patch.object(GatewayConfig, "server_keys", {"OPENAI_API_KEY": "sk-x"}):
            data = self.get_json("/api/ai-gateway/models")
        self.assertTrue(data["autoMode"])
        self.assertIsNone(data["preferredModelId"])

    def test_saving_a_model_pins_it(self):
        out = self.put_json(
            "/api/ai-gateway/models",
            {"modelId": "openai/gpt-5-mini", "autoMode": False},
        )
        self.assertEqual(out["preferredModelId"], "openai/gpt-5-mini")
        self.assertFalse(out["autoMode"])
        with mock.patch.object(GatewayConfig, "server_keys", {"OPENAI_API_KEY": "sk-x"}):
            data = self.get_json("/api/ai-gateway/models")
        self.assertEqual(data["preferredModelId"], "openai/gpt-5-mini")

    def test_turning_auto_back_on_clears_the_pin(self):
        self.put_json("/api/ai-gateway/models", {"modelId": "openai/gpt-5-mini", "autoMode": False})
        out = self.put_json("/api/ai-gateway/models", {"autoMode": True})
        self.assertTrue(out["autoMode"])
        self.assertIsNone(out["preferredModelId"])

    def test_unknown_model_is_rejected(self):
        out = self.put_json(
            "/api/ai-gateway/models",
            {"modelId": "evil/does-not-exist", "autoMode": False},
            expect=400,
        )
        self.assertEqual(out["error"], "unknown_model")

    def test_a_disabled_model_can_still_be_cleared(self):
        """Rejecting an unknown id must not lock a student out of auto mode."""
        self.put_json("/api/ai-gateway/models", {"modelId": "openai/gpt-5-mini", "autoMode": False})
        db._execute("DELETE FROM ai_models WHERE model_id = 'gpt-5-mini'")
        out = self.put_json("/api/ai-gateway/models", {"autoMode": True})
        self.assertTrue(out["autoMode"])

    def test_saving_a_pinned_model_uses_the_public_key_not_the_row_id(self):
        self.put_json("/api/ai-gateway/models", {"modelId": "openai/gpt-5-mini", "autoMode": False})
        with mock.patch.object(GatewayConfig, "server_keys", {"OPENAI_API_KEY": "sk-x"}):
            pinned = gateway.preferred_model(UID)
        self.assertIsNotNone(pinned)
        self.assertEqual(pinned["model_id"], "gpt-5-mini")


if __name__ == "__main__":
    unittest.main()
