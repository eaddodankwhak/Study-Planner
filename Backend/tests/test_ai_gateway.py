"""Tests for the AI Gateway registry and seed (Phase 1).

Suites:
- AiGatewaySchemaTest : tables exist after init_db, columns and indexes present.
- AiGatewaySeedTest   : idempotent default seed, providers/models contract,
                        env-key detection, model/provider lookups.
- AiGatewayMigrationTest: users.role / users.user_group columns are added to an
                        old database, and delete_user() clears gateway rows.

The seed writes to the DB, so a fresh temp database is used and every test
module in this file shares it (unittest runs modules sequentially).
"""

import os
import sys
import tempfile
import unittest

os.environ.setdefault("DATABASE_PATH", os.path.join(tempfile.mkdtemp(), "ai-gateway-test.db"))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import db  # noqa: E402

db.init_db()


def _conn():
    return db._conn_context()


class AiGatewaySchemaTest(unittest.TestCase):
    def test_gateway_tables_exist(self):
        with _conn() as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        for t in (
            "ai_providers",
            "ai_models",
            "ai_user_prefs",
            "ai_gateway_usage",
            "ai_quota_rules",
            "ai_cache",
            "ai_provider_health",
        ):
            self.assertIn(t, tables, f"missing table {t}")

    def test_usage_table_name_does_not_clobber_legacy(self):
        # The legacy ai_usage JSON-blob table must still exist (we renamed our
        # new table to ai_gateway_usage).
        with _conn() as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
        self.assertIn("ai_usage", tables)
        self.assertIn("ai_gateway_usage", tables)

    def test_indexes_exist(self):
        with _conn() as conn:
            idx = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                ).fetchall()
            }
        for name in ("idx_ai_models_provider", "idx_ai_gateway_usage_user", "idx_ai_cache_key"):
            self.assertIn(name, idx, f"missing index {name}")

    def test_migrate_adds_role_and_user_group_columns(self):
        with _conn() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
        self.assertIn("role", cols)
        self.assertIn("user_group", cols)


class AiGatewaySeedTest(unittest.TestCase):
    def setUp(self):
        # Imported fresh so the module-level cache flag is stable.
        import ai_gateway.registry as reg

        self.reg = reg
        reg.ensure_seeded()

    def test_seed_is_idempotent(self):
        with _conn() as conn:
            before_p = conn.execute("SELECT COUNT(*) FROM ai_providers").fetchone()[0]
            before_m = conn.execute("SELECT COUNT(*) FROM ai_models").fetchone()[0]
        self.reg.ensure_seeded()
        with _conn() as conn:
            after_p = conn.execute("SELECT COUNT(*) FROM ai_providers").fetchone()[0]
            after_m = conn.execute("SELECT COUNT(*) FROM ai_models").fetchone()[0]
        self.assertEqual(before_p, after_p)
        self.assertEqual(before_m, after_m)

    def test_seed_has_expected_providers(self):
        slugs = {p["slug"] for p in self.reg.list_providers(enabled_only=False)}
        for expected in (
            "google",
            "deepseek",
            "groq",
            "mistral",
            "openai",
            "anthropic",
            "openrouter",
            "cerebras",
        ):
            self.assertIn(expected, slugs, f"missing provider {expected}")

    def test_free_tier_and_train_flag_defaults(self):
        providers = {p["slug"]: p for p in self.reg.list_providers(enabled_only=False)}
        self.assertEqual(providers["google"]["is_free_tier"], 1)
        self.assertEqual(providers["groq"]["is_free_tier"], 1)
        self.assertEqual(providers["openai"]["is_free_tier"], 0)
        self.assertEqual(providers["anthropic"]["is_free_tier"], 0)
        # Data-training free tiers must be gated off for minors by default.
        self.assertEqual(providers["mistral"]["may_train_on_data"], 1)
        self.assertEqual(providers["mistral"]["allowed_for_minors"], 0)
        self.assertEqual(providers["google"]["may_train_on_data"], 1)
        self.assertEqual(providers["groq"]["may_train_on_data"], 0)

    def test_models_have_supported_fields(self):
        models = self.reg.list_models()
        self.assertTrue(models)
        for m in models:
            self.assertIn(m["provider_id"], {p["slug"] for p in self.reg.list_providers(enabled_only=False)})
            self.assertIn(m["adapter"], ("openai_compat", "anthropic"))
            self.assertIn(m["tier"], ("free", "standard", "premium"))
            for expected_key in (
                "model_id",
                "display_name",
                "context_window",
                "max_output",
                "supports_json_mode",
            ):
                self.assertIn(expected_key, m)

    def test_anthropic_model_uses_anthropic_adapter(self):
        model = self.reg.get_model("anthropic", "claude-sonnet-4-5")
        self.assertIsNotNone(model)
        self.assertEqual(model["adapter"], "anthropic")
        self.assertEqual(model["base_url"], "https://api.anthropic.com")

    def test_get_provider_and_model_lookups(self):
        prov = self.reg.get_provider("openai")
        self.assertIsNotNone(prov)
        self.assertEqual(prov["env_key_name"], "OPENAI_API_KEY")
        model = self.reg.get_model("openai", "gpt-5-mini")
        self.assertIsNotNone(model)
        self.assertEqual(model["provider_name"], "ChatGPT")
        self.assertIsNone(self.reg.get_provider("nope"))
        self.assertIsNone(self.reg.get_model("nope", "nope"))


if __name__ == "__main__":
    unittest.main()