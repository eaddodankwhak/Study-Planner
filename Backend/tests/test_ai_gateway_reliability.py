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
from ai_gateway import cache, gateway, health, models_seed, quotas, registry  # noqa: E402
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
        "DELETE FROM ai_user_prefs WHERE user_id = ?",
        "DELETE FROM users WHERE id = ?",
    ):
        db._execute(sql, (UID,))
    # Global tables (no per-user binding).
    db._execute("DELETE FROM ai_cache")
    db._execute("DELETE FROM ai_provider_health")
    registry._seeded = False
    registry.ensure_seeded()
    quotas.seed_quota_rules()
    _reset_registry_to_seed()


def _reset_registry_to_seed():
    """Put admin-controlled columns back to their seed values.

    Needed because the seed deliberately does NOT overwrite these columns any
    more (that is the whole point: admin decisions must survive a restart), so
    a test that flips is_enabled or may_train_on_data would otherwise leak into
    every later test in this module.
    """
    for p in models_seed.PROVIDERS:
        db._execute(
            "UPDATE ai_providers SET is_enabled = ?, is_free_tier = ?, "
            "may_train_on_data = ?, allowed_for_minors = ? WHERE slug = ?",
            (
                p["is_enabled"],
                p["is_free_tier"],
                p["may_train_on_data"],
                p["allowed_for_minors"],
                p["slug"],
            ),
        )
    for m in models_seed.MODELS:
        db._execute(
            "UPDATE ai_models SET is_enabled = 1 WHERE provider_id = ? AND model_id = ?",
            (m["provider_id"], m["model_id"]),
        )


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


class BreakerClaimTest(unittest.TestCase):
    """The half_open trial must be won by exactly one caller.

    A model that is merely "cooled down" is a provider we already believe is
    down. If every in-flight request re-probed it at once, the fallback chain
    would stop working exactly when it is needed.
    """

    def setUp(self):
        _clean()

    def tearDown(self):
        _clean()

    def _trip(self):
        with mock.patch.object(GatewayConfig, "circuit_failure_threshold", 1):
            health.record_failure("groq", "down")

    def test_claim_allowed_while_closed(self):
        self.assertTrue(health.try_claim("groq"))
        # A closed circuit is not a one-shot resource: everyone may call.
        self.assertTrue(health.try_claim("groq"))

    def test_claim_denied_while_open_and_cooling(self):
        self._trip()
        with mock.patch.object(GatewayConfig, "circuit_open_seconds", 300):
            self.assertFalse(health.try_claim("groq"))
            self.assertFalse(health.is_available("groq"))

    def test_only_one_caller_wins_the_half_open_trial(self):
        self._trip()
        with mock.patch.object(GatewayConfig, "circuit_open_seconds", -1):
            winners = [health.try_claim("groq") for _ in range(5)]
        self.assertEqual(winners.count(True), 1)
        # The loser must not see the provider as available either.
        self.assertFalse(health.is_available("groq"))
        self.assertEqual(health.get_state("groq")["state"], "half_open")

    def test_failed_trial_reopens_immediately(self):
        self._trip()
        with mock.patch.object(GatewayConfig, "circuit_open_seconds", -1):
            self.assertTrue(health.try_claim("groq"))
        # Threshold is 1 but the row is already at 1 failure; what matters is
        # that a failed half_open probe does not wait for another N failures.
        with mock.patch.object(GatewayConfig, "circuit_failure_threshold", 99):
            health.record_failure("groq", "still down")
        self.assertEqual(health.get_state("groq")["state"], "open")

    def test_success_clears_open_marker_and_error(self):
        self._trip()
        health.record_success("groq")
        conn = db._conn_context()
        try:
            row = conn.execute(
                "SELECT state, failures, opened_at, last_error FROM ai_provider_health "
                "WHERE provider_id = ?",
                ("groq",),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["state"], "closed")
        self.assertEqual(row["failures"], 0)
        self.assertIsNone(row["opened_at"])
        self.assertIsNone(row["last_error"])

    def test_failure_counter_does_not_lose_concurrent_increments(self):
        with mock.patch.object(GatewayConfig, "circuit_failure_threshold", 99):
            for _ in range(4):
                health.record_failure("groq", "boom")
        self.assertEqual(health.get_state("groq")["failures"], 4)


class AdminOverrideTest(unittest.TestCase):
    """Seeding must never undo a decision an admin made in the admin panel."""

    def setUp(self):
        _clean()

    def tearDown(self):
        _clean()

    def test_reseed_preserves_disabled_provider(self):
        conn = db._conn_context()
        try:
            conn.execute("UPDATE ai_providers SET is_enabled = 0 WHERE slug = 'openai'")
            conn.commit()
        finally:
            conn.close()
        # Force the seed to re-run, as it would on the next app start.
        registry._seeded = False
        registry.ensure_seeded()
        self.assertEqual(registry.get_provider("openai")["is_enabled"], 0)

    def test_reseed_preserves_minor_safety_decision(self):
        conn = db._conn_context()
        try:
            conn.execute(
                "UPDATE ai_providers SET may_train_on_data = 1, allowed_for_minors = 0 "
                "WHERE slug = 'groq'"
            )
            conn.commit()
        finally:
            conn.close()
        registry._seeded = False
        registry.ensure_seeded()
        provider = registry.get_provider("groq")
        self.assertEqual(provider["may_train_on_data"], 1)
        self.assertEqual(provider["allowed_for_minors"], 0)

    def test_reseed_supplies_primary_keys(self):
        """ai_providers.id / ai_models.id are NOT NULL: Postgres would reject
        a NULL there even though SQLite quietly tolerates one."""
        conn = db._conn_context()
        try:
            nulls = conn.execute(
                "SELECT COUNT(*) AS n FROM ai_providers WHERE id IS NULL"
            ).fetchone()["n"] + conn.execute(
                "SELECT COUNT(*) AS n FROM ai_models WHERE id IS NULL"
            ).fetchone()["n"]
        finally:
            conn.close()
        self.assertEqual(nulls, 0)

    def test_reseed_preserves_tuned_quota_limits(self):
        conn = db._conn_context()
        try:
            conn.execute(
                "UPDATE ai_quota_rules SET daily_requests = 7 "
                "WHERE tier = 'free' AND user_group = 'students'"
            )
            conn.commit()
        finally:
            conn.close()
        quotas.seed_quota_rules()
        conn = db._conn_context()
        try:
            row = conn.execute(
                "SELECT daily_requests FROM ai_quota_rules "
                "WHERE tier = 'free' AND user_group = 'students'"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row["daily_requests"], 7)


class ModelPreferenceTest(unittest.TestCase):
    """The picker writes ai_user_prefs and the gateway actually reads it."""

    def setUp(self):
        _clean()
        db.create_user(UID, "Pref", UID + "@example.com", "hash")

    def tearDown(self):
        _clean()

    def test_defaults_to_auto_mode(self):
        self.assertEqual(gateway.get_preference(UID), {"preferred_model_id": None, "auto_mode": 1})
        self.assertIsNone(gateway.preferred_model(UID))

    def test_pinned_model_round_trips(self):
        gateway.set_preference(UID, "openai/gpt-5-mini", auto_mode=False)
        saved = gateway.get_preference(UID)
        self.assertEqual(saved["preferred_model_id"], "openai/gpt-5-mini")
        self.assertEqual(saved["auto_mode"], 0)
        # A pin only resolves while the model is genuinely usable.
        with _server_keys({"OPENAI_API_KEY": "k1"}):
            pinned = gateway.preferred_model(UID)
        self.assertIsNotNone(pinned)
        self.assertEqual(pinned["model_id"], "gpt-5-mini")

    def test_auto_mode_clears_a_stale_pin(self):
        gateway.set_preference(UID, "openai/gpt-5-mini", auto_mode=False)
        gateway.set_preference(UID, None, auto_mode=True)
        self.assertIsNone(gateway.get_preference(UID)["preferred_model_id"])
        self.assertIsNone(gateway.preferred_model(UID))

    def test_pinned_model_that_disappears_falls_back_to_auto(self):
        gateway.set_preference(UID, "openai/gpt-5-mini", auto_mode=False)
        conn = db._conn_context()
        try:
            conn.execute("UPDATE ai_models SET is_enabled = 0 WHERE model_id = 'gpt-5-mini'")
            conn.commit()
        finally:
            conn.close()
        # Not an error: the student's choice is stale, so the gateway quietly
        # auto-selects and the picker shows the model as unavailable.
        self.assertIsNone(gateway.preferred_model(UID))

    def test_pinned_model_with_a_dead_provider_falls_back_to_auto(self):
        gateway.set_preference(UID, "openai/gpt-5-mini", auto_mode=False)
        # Enabled, but nobody holds a key for the provider right now.
        with _server_keys({}):
            self.assertIsNone(gateway.preferred_model(UID))

    def test_pinned_model_behind_an_open_breaker_falls_back_to_auto(self):
        gateway.set_preference(UID, "openai/gpt-5-mini", auto_mode=False)
        for _ in range(GatewayConfig.circuit_failure_threshold):
            health.record_failure("openai", "boom")
        with _server_keys({"OPENAI_API_KEY": "k1", "ANTHROPIC_API_KEY": "k2"}):
            # A transient outage must not hard-fail the request; the whole point
            # of the fallback chain is to keep the student moving.
            self.assertIsNone(gateway.preferred_model(UID))
            chain = gateway._fallback_chain(UID)
        self.assertNotIn(
            "gpt-5-mini", [m["model_id"] for m in chain]
        )

    def test_explicit_per_request_model_still_raises_when_unusable(self):
        # A pin is a soft preference, but a model named in the request itself
        # is a hard requirement: substituting a different model silently would
        # send the student's data somewhere they did not ask for.
        with _server_keys({}):
            with self.assertRaises(gw.PreferenceError):
                gateway._fallback_chain(UID, preferred="openai/gpt-5-mini")

    def test_generation_uses_the_pinned_model(self):
        gateway.set_preference(UID, "anthropic/claude-sonnet-4-5", auto_mode=False)
        seen = {}

        def builder(provider, key):
            seen["provider"] = provider["slug"]
            return _fake(result={"content": "ok", "usage": {"inputTokens": 1, "outputTokens": 1}})

        with mock.patch("ai_gateway.gateway.build_adapter", side_effect=builder):
            with _server_keys({"OPENAI_API_KEY": "k1", "ANTHROPIC_API_KEY": "k2"}):
                out = gw.generate_text(UID, "hi", feature="chat")
        self.assertEqual(seen["provider"], "anthropic")
        self.assertEqual(out["model"]["model_id"], "claude-sonnet-4-5")


class MinorSafetyTest(unittest.TestCase):
    """Providers that may train on prompts are withheld from minor accounts."""

    def setUp(self):
        _clean()
        db.create_user(UID, "Minor", UID + "@example.com", "hash")

    def tearDown(self):
        _clean()

    def _set_group(self, group):
        db._execute(
            "UPDATE users SET user_group = ?, role = 'student' WHERE id = ?",
            (group, UID),
        )

    def test_training_provider_is_hidden_from_minors(self):
        self._set_group("minors")
        conn = db._conn_context()
        try:
            conn.execute(
                "UPDATE ai_providers SET may_train_on_data = 1, allowed_for_minors = 0 "
                "WHERE slug = 'openai'"
            )
            conn.commit()
        finally:
            conn.close()
        with _server_keys({"OPENAI_API_KEY": "k1", "ANTHROPIC_API_KEY": "k2"}):
            usable = {m["provider_slug"] for m in gateway._ranked_models(UID, "chat")}
        self.assertNotIn("openai", usable)
        self.assertIn("anthropic", usable)

    def test_admin_can_explicitly_opt_a_training_provider_in(self):
        """allowed_for_minors=1 is the documented escape hatch, so it has to
        actually open the gate rather than being ANDed away."""
        self._set_group("minors")
        conn = db._conn_context()
        try:
            conn.execute(
                "UPDATE ai_providers SET may_train_on_data = 1, allowed_for_minors = 1 "
                "WHERE slug = 'openai'"
            )
            conn.commit()
        finally:
            conn.close()
        with _server_keys({"OPENAI_API_KEY": "k1", "ANTHROPIC_API_KEY": "k2"}):
            usable = {m["provider_slug"] for m in gateway._ranked_models(UID, "chat")}
        self.assertIn("openai", usable)

    def test_non_minor_sees_training_providers(self):
        self._set_group("students")
        with _server_keys({"OPENAI_API_KEY": "k1", "ANTHROPIC_API_KEY": "k2"}):
            usable = {m["provider_slug"] for m in gateway._ranked_models(UID, "chat")}
        self.assertIn("openai", usable)

    def test_unkeyed_provider_is_never_usable(self):
        self._set_group("students")
        with _server_keys({"OPENAI_API_KEY": "k1"}):
            usable = {m["provider_slug"] for m in gateway._ranked_models(UID, "chat")}
        self.assertIn("openai", usable)
        self.assertNotIn("anthropic", usable)


if __name__ == "__main__":
    unittest.main()
