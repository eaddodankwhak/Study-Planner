"""Tests for the AI Gateway admin console API (Phase 6).

The admin surface is the only place an operator can see provider internals or
flip safety flags, so these tests pin the two things that would be dangerous to
regress: it must fail closed when the caller is not an allow-listed admin, and
it must warn when a provider that may train on student data is enabled or
allowed for minors.
"""

import os
import sys
import unittest

os.environ.setdefault(
    "DATABASE_PATH", os.path.join(os.path.expanduser("~"), "ai-gw-admin-test.db")
)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import db  # noqa: E402

db.init_db()

from ai_gateway import health, models_seed, quotas, registry  # noqa: E402
from ai_gateway.admin import admin_api  # noqa: E402
from ai_gateway.config import GatewayConfig  # noqa: E402

ADMIN_UID = "gateway-admin-user"
ADMIN_EMAIL = "gateway-admin@example.com"
STUDENT_UID = "gateway-admin-student"
STUDENT_EMAIL = "gateway-admin-student@example.com"

_ORIGINAL_ADMIN_EMAILS = set(GatewayConfig.admin_emails)

PROVIDERS_URL = "/api/ai-gateway/admin/providers"
MODELS_URL = "/api/ai-gateway/admin/models"
QUOTAS_URL = "/api/ai-gateway/admin/quotas"
HEALTH_URL = "/api/ai-gateway/admin/health"


def _flask_app():
    from flask import Flask

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.secret_key = "test"
    app.register_blueprint(admin_api)
    return app


def _clean():
    for uid in (ADMIN_UID, STUDENT_UID):
        db._execute("DELETE FROM ai_user_prefs WHERE user_id = ?", (uid,))
        db._execute("DELETE FROM users WHERE id = ?", (uid,))
    db._execute("DELETE FROM ai_provider_health")
    registry._seeded = False
    registry.ensure_seeded()
    quotas.seed_quota_rules()
    # seed_quota_rules() is deliberately DO NOTHING so admin edits survive a
    # restart; restore the defaults explicitly so one test cannot leak a limit
    # into the next.
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
    for m in models_seed.MODELS:
        db._execute(
            "UPDATE ai_models SET is_enabled = 1, tier = ? "
            "WHERE provider_id = ? AND model_id = ?",
            (m["tier"], m["provider_id"], m["model_id"]),
        )


class _Base(unittest.TestCase):
    def setUp(self):
        _clean()
        db.create_user(ADMIN_UID, "Admin", ADMIN_EMAIL, "hash")
        db.create_user(STUDENT_UID, "Student", STUDENT_EMAIL, "hash")
        GatewayConfig.admin_emails = {ADMIN_EMAIL}
        self.app = _flask_app()

    def tearDown(self):
        GatewayConfig.admin_emails = set(_ORIGINAL_ADMIN_EMAILS)
        _clean()

    def client(self, uid):
        """A test client whose session is signed in as ``uid`` (None = anonymous)."""
        c = self.app.test_client()
        if uid is not None:
            with c.session_transaction() as sess:
                sess["user_id"] = uid
        return c


class AdminAuthTest(_Base):
    def test_anonymous_is_unauthorized(self):
        resp = self.client(None).get(PROVIDERS_URL)
        self.assertEqual(resp.status_code, 401)

    def test_non_admin_is_forbidden(self):
        resp = self.client(STUDENT_UID).get(PROVIDERS_URL)
        self.assertEqual(resp.status_code, 403)

    def test_empty_allow_list_forbids_everyone(self):
        GatewayConfig.admin_emails = set()
        resp = self.client(ADMIN_UID).get(PROVIDERS_URL)
        self.assertEqual(resp.status_code, 403)

    def test_admin_is_allowed(self):
        resp = self.client(ADMIN_UID).get(PROVIDERS_URL)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("providers", resp.get_json())


class AdminProviderTest(_Base):
    def test_list_reports_health_and_training_warnings(self):
        data = self.client(ADMIN_UID).get(PROVIDERS_URL).get_json()
        by_slug = {p["slug"]: p for p in data["providers"]}
        self.assertIn("openai", by_slug)
        self.assertIn("health", by_slug["openai"])
        # google ships with may_train_on_data=1 in the seed.
        self.assertTrue(by_slug["google"]["mayTrainOnData"])
        self.assertTrue(any("train" in w.lower() for w in by_slug["google"]["warnings"]))

    def test_disable_provider_persists(self):
        resp = self.client(ADMIN_UID).patch(
            PROVIDERS_URL + "/openai", json={"isEnabled": False}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.get_json()["provider"]["isEnabled"])
        self.assertEqual(registry.get_provider("openai")["is_enabled"], 0)

    def test_training_provider_for_minors_surfaces_warning(self):
        resp = self.client(ADMIN_UID).patch(
            PROVIDERS_URL + "/openai",
            json={"mayTrainOnData": True, "allowedForMinors": True},
        )
        self.assertEqual(resp.status_code, 200)
        warnings = resp.get_json()["provider"]["warnings"]
        self.assertTrue(any("minors" in w.lower() for w in warnings))

    def test_non_boolean_is_rejected(self):
        resp = self.client(ADMIN_UID).patch(
            PROVIDERS_URL + "/openai", json={"isEnabled": "yes"}
        )
        self.assertEqual(resp.status_code, 400)

    def test_unknown_provider_is_404(self):
        resp = self.client(ADMIN_UID).patch(
            PROVIDERS_URL + "/nope", json={"isEnabled": False}
        )
        self.assertEqual(resp.status_code, 404)


class AdminModelTest(_Base):
    def test_toggle_model(self):
        resp = self.client(ADMIN_UID).patch(
            MODELS_URL + "/openai/gpt-5-mini", json={"isEnabled": False}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.get_json()["model"]["isEnabled"])

    def test_invalid_tier_is_rejected(self):
        resp = self.client(ADMIN_UID).patch(
            MODELS_URL + "/openai/gpt-5-mini", json={"tier": "gold"}
        )
        self.assertEqual(resp.status_code, 400)

    def test_unknown_model_is_404(self):
        resp = self.client(ADMIN_UID).patch(
            MODELS_URL + "/openai/nonexistent", json={"isEnabled": False}
        )
        self.assertEqual(resp.status_code, 404)


class AdminQuotaTest(_Base):
    def test_update_and_list_quota(self):
        c = self.client(ADMIN_UID)
        resp = c.put(
            QUOTAS_URL + "/free/students",
            json={"dailyRequests": 7, "dailyTokens": 1234},
        )
        self.assertEqual(resp.status_code, 200)
        rule = resp.get_json()["rule"]
        self.assertEqual(rule["daily_requests"], 7)
        self.assertEqual(rule["daily_tokens"], 1234)
        listed = {
            (r["tier"], r["user_group"]): r for r in c.get(QUOTAS_URL).get_json()["rules"]
        }
        self.assertEqual(listed[("free", "students")]["daily_requests"], 7)

    def test_partial_update_keeps_other_limits(self):
        c = self.client(ADMIN_UID)
        before = {
            (r["tier"], r["user_group"]): r for r in c.get(QUOTAS_URL).get_json()["rules"]
        }[("free", "students")]
        after = c.put(
            QUOTAS_URL + "/free/students", json={"dailyRequests": 9}
        ).get_json()["rule"]
        self.assertEqual(after["daily_requests"], 9)
        self.assertEqual(after["daily_tokens"], before["daily_tokens"])

    def test_negative_is_rejected(self):
        resp = self.client(ADMIN_UID).put(
            QUOTAS_URL + "/free/students", json={"dailyRequests": -1}
        )
        self.assertEqual(resp.status_code, 400)

    def test_invalid_tier_is_rejected(self):
        resp = self.client(ADMIN_UID).put(
            QUOTAS_URL + "/gold/students", json={"dailyRequests": 1}
        )
        self.assertEqual(resp.status_code, 400)


class AdminHealthTest(_Base):
    def test_reset_closes_tripped_circuit(self):
        for _ in range(GatewayConfig.circuit_failure_threshold):
            health.record_failure("openai", "boom")
        c = self.client(ADMIN_UID)
        states = {h["provider"]: h for h in c.get(HEALTH_URL).get_json()["health"]}
        self.assertEqual(states["openai"]["state"], "open")

        resp = c.post(HEALTH_URL + "/openai/reset")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["provider"]["state"], "closed")
        self.assertEqual(health.get_state("openai")["state"], "closed")

    def test_reset_unknown_provider_is_404(self):
        resp = self.client(ADMIN_UID).post(HEALTH_URL + "/nope/reset")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
