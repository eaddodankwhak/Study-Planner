"""Tests for AI Gateway reliability (Phase 3): quotas, cache, fallback,
circuit breaker.

Suites:
- GatewayQuotaTest   : ai_quota_rules seed, per-day enforcement, personal keys
                        never burn the server budget.
- GatewayCacheTest   : shared-content key derivation, TTL, hit counting, prune.
- GatewayHealthTest  : breaker closes/opens/half-opens on failures.
- GatewayFallbackTest: fallback to the next healthy model when the first
                        provider errors; open breaker is skipped.

The provider HTTP layer is never reached: the gateway adapter builder is
monkeypatched with fake adapters.
"""

import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("DATABASE_PATH", os.path.join(os.path.expanduser("~"), "ai-gw-rel-test.db"))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import db  # noqa: E402

db.init_db()

import ai_gateway as gw  # noqa: E402
from ai_gateway import cache, health, quotas, registry  # noqa: E402
from ai_gateway.config import GatewayConfig  # noqa: E402
from ai_gateway.errors import (  # noqa: E402
    AllProvidersFailedError,
    QuotaExceededError,
)

UID = "gateway-rel-test-user"


def _server_keys(keys):
    return mock.patch.object(GatewayConfig, "server_keys", dict(keys))


def _clean():
    for sql in (
        "DELETE FROM ai_gateway_usage WHERE user_id = ?",
        "DELETE FROM ai_connections WHERE user_id = ?",
        "DELETE FROM users WHERE id = ?",
    ):
        db._execute(sql, (UID,))
    # Global tables (no per-user binding).
    db._execute("DELETE FROM ai_cache")
    db._execute("DELETE FROM ai_provider_health")
    registry._seeded = False
    registry.ensure_seeded()
    quotas.seed_quota_rules()


class QuotaSeedTest(unittest.TestCase):
    def test_seed_quota_rules_idempotent(self):
        quotas.seed_quota_rules()
        quotas.seed_quota_rules()
        conn = db._conn_context()
        try:
            n = conn.execute("SELECT COUNT(*) AS n FROM ai_quota_rules").fetchone()["n"]
        finally:
            conn.close()
        self.assertEqual(n, 3)  # free/standard/premium for students


class GatewayQuotaTest(unittest.TestCase):
    def setUp(self):
        _clean()
        db.create_user(UID, "Rel Test", UID + "@example.com", "hash")

    def tearDown(self):
        _clean()

    def test_usage_today_zero_initially(self):
        self.assertEqual(quotas.usage_today(UID)["requests"], 0)

    def test_check_raises_when_requests_spent(self):
        # Insert a server-funded usage row at today's quota boundary.
        with db._conn_context() as conn:
            conn.execute(
                "INSERT INTO ai_gateway_usage "
                "(id, user_id, day, model_id, feature, requests, input_tokens, "
                "output_tokens, est_cost, used_own_key) "
                "VALUES ('q1', ?, date('now'), 'm', 'chat', 100, 0, 0, 0, 0)",
                (UID,),
            )
            conn.commit()
        with self.assertRaises(QuotaExceededError):
            quotas.check(UID, "free", "server")

    def test_personal_key_never_blocked(self):
        with db._conn_context() as conn:
            conn.execute(
                "INSERT INTO ai_gateway_usage "
                "(id, user_id, day, model_id, feature, requests, input_tokens, "
                "output_tokens, est_cost, used_own_key) "
                "VALUES ('q2', ?, date('now'), 'm', 'chat', 100, 0, 0, 0, 0)",
                (UID,),
            )
            conn.commit()
        # personal => no raise
        quotas.check(UID, "free", "personal")

    def test_remaining_reports_limits(self):
        rem = quotas.remaining(UID, "free")
        self.assertEqual(rem["requests_used"], 0)
        self.assertGreater(rem["requests_limit"], 0)


class GatewayCacheTest(unittest.TestCase):
    def setUp(self):
        _clean()

    def tearDown(self):
        _clean()

    def test_make_key_deterministic_and_content_sensitive(self):
        a = cache.make_key("doc1", "v1")
        b = cache.make_key("doc1", "v1")
        c = cache.make_key("doc2", "v1")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_set_get_roundtrip(self):
        key = cache.make_key("shared-doc", "prompt")
        self.assertTrue(cache.set(key, {"cards": [1]}, model_id="m"))
        got = cache.get(key)
        self.assertEqual(got, {"cards": [1]})

    def test_hit_counter_increments(self):
        key = cache.make_key("hit-doc")
        cache.set(key, {"v": 1})
        cache.get(key)
        cache.get(key)
        conn = db._conn_context()
        try:
            row = conn.execute(
                "SELECT hits FROM ai_cache WHERE cache_key = ?", (key,)
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["hits"], 2)

    def test_ttl_expiry(self):
        key = cache.make_key("ttl-doc")
        cache.set(key, {"v": 1})
        with mock.patch.object(GatewayConfig, "cache_ttl_seconds", -1):
            self.assertIsNone(cache.get(key))

    def test_disabled_cache_is_noop(self):
        key = cache.make_key("off-doc")
        with mock.patch.object(GatewayConfig, "cache_enabled", False):
            self.assertFalse(cache.set(key, {"v": 1}))
            self.assertIsNone(cache.get(key))


class GatewayHealthTest(unittest.TestCase):
    def setUp(self):
        _clean()

    def tearDown(self):
        _clean()

    def test_starts_closed_available(self):
        self.assertEqual(health.get_state("groq")["state"], "closed")
        self.assertTrue(health.is_available("groq"))

    def test_trips_open_after_threshold(self):
        with mock.patch.object(GatewayConfig, "circuit_failure_threshold", 2):
            health.record_failure("groq", "boom")
            self.assertTrue(health.is_available("groq"))  # below threshold
            health.record_failure("groq", "boom")
            self.assertEqual(health.get_state("groq")["state"], "open")
            self.assertFalse(health.is_available("groq"))

    def test_success_resets_to_closed(self):
        health.record_failure("groq", "boom")
        health.record_success("groq")
        self.assertEqual(health.get_state("groq")["state"], "closed")

    def test_half_open_after_cooldown(self):
        with mock.patch.object(GatewayConfig, "circuit_failure_threshold", 1):
            health.record_failure("groq", "boom")
            self.assertFalse(health.is_available("groq"))
        with mock.patch.object(GatewayConfig, "circuit_open_seconds", -1):
            self.assertTrue(health.is_available("groq"))  # cooldown elapsed


def _fake(result=None, error=None):
    class Fake:
        provider_role = "fake"

        def __init__(self):
            self.n = 0

        def generate(self, request):
            self.n += 1
            if error:
                raise error
            return result or {"content": "ok", "usage": {"inputTokens": 1, "outputTokens": 1}}

        def generate_json(self, request, schema=None):
            return self.generate(request)

    return Fake()


class GatewayFallbackTest(unittest.TestCase):
    def setUp(self):
        _clean()
        db.create_user(UID, "Fallback", UID + "@example.com", "hash")

    def tearDown(self):
        _clean()

    def test_falls_back_to_next_model_on_failure(self):
        # Both openai + anthropic keyed so the chain has >= 2 entries.
        from ai.providers._http import ProviderHTTPError

        calls = {"openai": 0, "anthropic": 0}

        def builder(provider, key):
            if provider["slug"] == "openai":
                calls["openai"] += 1
                return _fake(error=ProviderHTTPError(500, "down"))
            calls["anthropic"] += 1
            return _fake(result={"content": "second", "usage": {"inputTokens": 1, "outputTokens": 1}})

        with mock.patch("ai_gateway.gateway.build_adapter", side_effect=builder):
            with _server_keys({"OPENAI_API_KEY": "k1", "ANTHROPIC_API_KEY": "k2"}):
                out = gw.generate_text(UID, "hi", feature="chat")
        self.assertEqual(out["content"], "second")
        self.assertEqual(calls["openai"], 1)   # first tried
        self.assertEqual(calls["anthropic"], 1)  # fallback used

    def test_all_fail_raises_all_providers_failed(self):
        from ai.providers._http import ProviderHTTPError

        def builder(provider, key):
            return _fake(error=ProviderHTTPError(500, "down"))

        with mock.patch("ai_gateway.gateway.build_adapter", side_effect=builder):
            with _server_keys({"OPENAI_API_KEY": "k1", "ANTHROPIC_API_KEY": "k2"}):
                with self.assertRaises(AllProvidersFailedError):
                    gw.generate_text(UID, "hi", feature="chat")

    def test_open_breaker_is_skipped(self):
        # Open openai's breaker; it should not be attempted.
        with mock.patch.object(GatewayConfig, "circuit_failure_threshold", 1):
            health.record_failure("openai", "down")
        calls = {"openai": 0}

        def builder(provider, key):
            if provider["slug"] == "openai":
                calls["openai"] += 1
            return _fake(result={"content": "fallback", "usage": {"inputTokens": 1, "outputTokens": 1}})

        with mock.patch("ai_gateway.gateway.build_adapter", side_effect=builder):
            with _server_keys({"OPENAI_API_KEY": "k1", "ANTHROPIC_API_KEY": "k2"}):
                out = gw.generate_text(UID, "hi", feature="chat")
        self.assertEqual(out["content"], "fallback")
        self.assertEqual(calls["openai"], 0)  # breaker open, skipped

    def test_cache_hit_skips_provider(self):
        key = cache.make_key("shared-textbook")
        cache.set(key, "cached content", model_id="m")
        def builder(provider, key_):  # should not be called on a cache hit
            raise AssertionError("adapter should not be built on cache hit")
        with mock.patch("ai_gateway.gateway.build_adapter", side_effect=builder):
            with _server_keys({"OPENAI_API_KEY": "k1"}):
                out = gw.generate_text(UID, "hi", feature="cards", cache_key=key)
        self.assertEqual(out["content"], "cached content")
        self.assertTrue(out["cached"])

    def test_json_cache_hit(self):
        key = cache.make_key("shared-quiz")
        cache.set(key, {"q": "cached"}, model_id="m")
        def builder(provider, key_):
            raise AssertionError("adapter should not be built on cache hit")
        with mock.patch("ai_gateway.gateway.build_adapter", side_effect=builder):
            with _server_keys({"OPENAI_API_KEY": "k1"}):
                data = gw.generate_json(UID, None, "make quiz", feature="quiz", cache_key=key)
        self.assertEqual(data, {"q": "cached"})


if __name__ == "__main__":
    unittest.main()