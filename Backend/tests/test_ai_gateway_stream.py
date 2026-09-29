"""Tests for gateway streaming and the Copilot adapter.

Streaming is the reason these live in their own module: the AI Hub has
always streamed, so the gateway has to too, and a stream has sharp edges
(non-blocking fallback, usage reported only at the end) that a plain
generate() test never exercises.
"""

import os
import sys
import unittest
from unittest import mock

os.environ.setdefault(
    "DATABASE_PATH", os.path.join(os.path.expanduser("~"), "ai-gw-stream-test.db")
)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import db  # noqa: E402

db.init_db()

import ai_gateway as gw  # noqa: E402
from ai_gateway import gateway, health, models_seed, quotas, registry  # noqa: E402
from ai_gateway.adapters import (  # noqa: E402
    AnthropicAdapter,
    CopilotAdapter,
    OpenAICompatAdapter,
    build_adapter,
)
from ai_gateway.config import GatewayConfig  # noqa: E402
from ai_gateway.errors import AllProvidersFailedError, AIDisabledError  # noqa: E402
from ai.providers._http import ProviderHTTPError  # noqa: E402

UID = "gateway-stream-test-user"


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
    db._execute("DELETE FROM ai_cache")
    db._execute("DELETE FROM ai_provider_health")
    registry._seeded = False
    registry.ensure_seeded()
    quotas.seed_quota_rules()
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


class _FakeStreamAdapter:
    """Adapter that streams `deltas`, optionally failing after n tokens."""

    provider_role = "fake"

    def __init__(self, deltas, fail_after=None, error=None):
        self.deltas = list(deltas)
        self.fail_after = fail_after
        self.error = error or ProviderHTTPError(503, "boom")
        self.last_usage = {"inputTokens": 7, "outputTokens": 3}

    def stream(self, request):
        for i, text in enumerate(self.deltas):
            if self.fail_after is not None and i >= self.fail_after:
                raise self.error
            yield text
        # A generator only runs while being drained, so a failure scheduled
        # past the last delta has to be raised after the loop to be observed.
        if self.fail_after is not None and len(self.deltas) <= self.fail_after:
            raise self.error


class StreamTest(unittest.TestCase):
    def setUp(self):
        _clean()
        db.create_user(UID, "Stream", UID + "@example.com", "hash")

    def tearDown(self):
        _clean()

    def _drain(self, gen):
        """Split the stream into (text parts, final meta dict)."""
        text, meta = [], None
        for item in gen:
            if isinstance(item, dict):
                meta = item
            else:
                text.append(item)
        return "".join(text), meta

    # --------------------------------------------------------- happy path

    def test_stream_yields_deltas_then_meta(self):
        adapter = _FakeStreamAdapter(["Hel", "lo", " there"])
        with mock.patch("ai_gateway.gateway.build_adapter", return_value=adapter):
            with _server_keys({"OPENAI_API_KEY": "k1"}):
                text, meta = self._drain(gw.stream_text(UID, "hi"))
        self.assertEqual(text, "Hello there")
        self.assertIsNotNone(meta)
        self.assertEqual(meta["model"]["model_id"], "gpt-5-mini")
        self.assertEqual(meta["paid_by"], "server")

    def test_stream_records_usage(self):
        adapter = _FakeStreamAdapter(["a", "b"])
        with mock.patch("ai_gateway.gateway.build_adapter", return_value=adapter):
            with _server_keys({"OPENAI_API_KEY": "k1"}):
                self._drain(gw.stream_text(UID, "hi"))
        row = db._query_one(
            "SELECT requests, input_tokens, output_tokens FROM ai_gateway_usage "
            "WHERE user_id = ?",
            (UID,),
        )
        self.assertIsNotNone(row, "a streamed turn must still be billed")
        self.assertEqual(row["requests"], 1)
        self.assertEqual(row["input_tokens"], 7)
        self.assertEqual(row["output_tokens"], 3)

    def test_stream_uses_the_saved_pin_first(self):
        gateway.set_preference(UID, "anthropic/claude-sonnet-4-5", auto_mode=False)
        adapter = _FakeStreamAdapter(["ok"])
        with mock.patch("ai_gateway.gateway.build_adapter", return_value=adapter):
            with _server_keys({"OPENAI_API_KEY": "k1", "ANTHROPIC_API_KEY": "k2"}):
                _text, meta = self._drain(gw.stream_text(UID, "hi"))
        self.assertEqual(meta["model"]["model_id"], "claude-sonnet-4-5")

    def test_stream_respects_privacy_switch(self):
        import json

        user = db.get_user(UID) or {}
        settings = json.loads(user.get("settings_json") or "{}")
        privacy = dict(settings.get("privacy") or {})
        privacy["ai_activity"] = False
        settings["privacy"] = privacy
        db._execute(
            "UPDATE users SET settings_json = ? WHERE id = ?",
            (json.dumps(settings), UID),
        )
        adapter = _FakeStreamAdapter(["nope"])
        with mock.patch("ai_gateway.gateway.build_adapter", return_value=adapter):
            with _server_keys({"OPENAI_API_KEY": "k1"}):
                with self.assertRaises(AIDisabledError):
                    self._drain(gw.stream_text(UID, "hi"))

    # ----------------------------------------------------------- fallback

    def test_fallback_before_first_token_switches_provider(self):
        """A provider that dies before emitting must not cost the student."""
        dead = _FakeStreamAdapter([], fail_after=0, error=ProviderHTTPError(503, "down"))
        live = _FakeStreamAdapter(["re", "covered"])
        adapters = [dead, live]
        with mock.patch("ai_gateway.gateway.build_adapter", side_effect=lambda p, k: adapters.pop(0)):
            with _server_keys({"OPENAI_API_KEY": "k1", "ANTHROPIC_API_KEY": "k2"}):
                text, meta = self._drain(gw.stream_text(UID, "hi"))
        self.assertEqual(text, "recovered")
        self.assertEqual(meta["provider"]["slug"], "anthropic")

    def test_failure_after_first_token_propagates(self):
        """Half a reply is already on screen; splicing two answers is worse."""
        adapter = _FakeStreamAdapter(["partial", "more"], fail_after=1)
        with mock.patch("ai_gateway.gateway.build_adapter", return_value=adapter):
            with _server_keys({"OPENAI_API_KEY": "k1", "ANTHROPIC_API_KEY": "k2"}):
                gen = gw.stream_text(UID, "hi")
                self.assertEqual(next(gen), "partial")
                with self.assertRaises(ProviderHTTPError):
                    for _ in gen:
                        pass

    def test_all_providers_failing_raises_all_providers_failed(self):
        dead = _FakeStreamAdapter([], fail_after=0)
        live = _FakeStreamAdapter([], fail_after=0)
        adapters = [dead, live]
        with mock.patch("ai_gateway.gateway.build_adapter", side_effect=lambda p, k: adapters.pop(0)):
            with _server_keys({"OPENAI_API_KEY": "k1", "ANTHROPIC_API_KEY": "k2"}):
                with self.assertRaises(AllProvidersFailedError):
                    self._drain(gw.stream_text(UID, "hi"))

    def test_stream_failure_opens_the_breaker(self):
        adapter = _FakeStreamAdapter([], fail_after=0)
        with mock.patch.object(GatewayConfig, "circuit_failure_threshold", 1):
            with mock.patch("ai_gateway.gateway.build_adapter", return_value=adapter):
                with _server_keys({"OPENAI_API_KEY": "k1"}):
                    with self.assertRaises(AllProvidersFailedError):
                        self._drain(gw.stream_text(UID, "hi"))
            self.assertFalse(health.is_available("openai"))


class AdapterStreamParsingTest(unittest.TestCase):
    """The adapters must translate each provider's SSE dialect to plain text."""

    def test_openai_stream_reads_deltas_and_final_usage(self):
        frames = [
            {"choices": [{"delta": {"content": "He"}}]},
            {"choices": [{"delta": {"content": "llo"}}]},
            {"choices": [{"delta": {}}], "usage": {"prompt_tokens": 11, "completion_tokens": 4}},
        ]
        adapter = OpenAICompatAdapter("k", "https://x/v1")
        with mock.patch(
            "ai_gateway.adapters.stream_json_lines", return_value=iter(frames)
        ):
            out = list(adapter.stream({"model": "m", "messages": []}))
        self.assertEqual(out, ["He", "llo"])
        self.assertEqual(adapter.last_usage, {"inputTokens": 11, "outputTokens": 4})

    def test_openai_stream_asks_for_usage(self):
        """Without include_usage every streamed request would look free."""
        captured = {}

        def fake(url, headers, payload, timeout=120):
            captured.update(payload)
            return iter([])

        adapter = OpenAICompatAdapter("k", "https://x/v1")
        with mock.patch("ai_gateway.adapters.stream_json_lines", side_effect=fake):
            list(adapter.stream({"model": "m", "messages": []}))
        self.assertEqual(captured["stream"], True)
        self.assertEqual(captured["stream_options"], {"include_usage": True})

    def test_anthropic_stream_reads_content_block_deltas(self):
        frames = [
            {"type": "message_start", "message": {"usage": {"input_tokens": 9}}},
            {"type": "content_block_delta", "delta": {"text": "Hi"}},
            {"type": "content_block_delta", "delta": {"text": "!"}},
            {"type": "message_delta", "usage": {"output_tokens": 2}},
        ]
        adapter = AnthropicAdapter("k", "https://x")
        with mock.patch(
            "ai_gateway.adapters.stream_json_lines", return_value=iter(frames)
        ):
            out = list(adapter.stream({"model": "m", "messages": [{"role": "user", "content": "x"}]}))
        self.assertEqual(out, ["Hi", "!"])
        self.assertEqual(adapter.last_usage, {"inputTokens": 9, "outputTokens": 2})


class CopilotAdapterTest(unittest.TestCase):
    def test_registry_builds_the_copilot_adapter(self):
        provider = registry.get_provider("copilot")
        self.assertIsNotNone(provider, "copilot must be seeded into the registry")
        self.assertEqual(provider["adapter"], "copilot")
        self.assertIsInstance(build_adapter(provider, "gh-token"), CopilotAdapter)

    def test_copilot_sends_the_editor_identity_headers(self):
        adapter = build_adapter(registry.get_provider("copilot"), "gh-token")
        headers = adapter._headers()
        self.assertEqual(headers["Authorization"], "Bearer gh-token")
        self.assertIn("Editor-Version", headers)
        self.assertIn("Copilot-Integration-Id", headers)

    def test_copilot_is_excluded_for_minors(self):
        """Copilot trains on prompts, so it must not be offered to minors."""
        db.create_user(UID + "-minor", "Minor", UID + "-minor@example.com", "hash")
        db._execute("UPDATE users SET user_group = 'minors' WHERE id = ?", (UID + "-minor",))
        try:
            minor = registry.get_provider("copilot")
            self.assertFalse(
                gateway._model_allowed_for_account({}, minor, minor=True),
                "a training-on-data provider must stay hidden from minors",
            )
        finally:
            db._execute("DELETE FROM users WHERE id = ?", (UID + "-minor",))

    def test_copilot_verify_falls_back_to_the_individual_host(self):
        adapter = build_adapter(registry.get_provider("copilot"), "gh-token")
        tried = []

        def fake_post(url, headers, payload, timeout=60):
            tried.append(url)
            if "individual" not in url:
                raise ProviderHTTPError(401, "nope")
            return {"choices": [{"message": {"content": "ok"}}]}

        with mock.patch("ai_gateway.adapters.post_json", side_effect=fake_post):
            ok, message = adapter.verify()
        self.assertTrue(ok, message)
        self.assertEqual(len(tried), 2)
        self.assertIn("individual", tried[1])

    def test_copilot_verify_reports_a_rejected_token(self):
        adapter = build_adapter(registry.get_provider("copilot"), "gh-token")

        def fake_post(url, headers, payload, timeout=60):
            raise ProviderHTTPError(401, "nope")

        with mock.patch("ai_gateway.adapters.post_json", side_effect=fake_post):
            ok, message = adapter.verify()
        self.assertFalse(ok)
        self.assertIn("401", message)


if __name__ == "__main__":
    unittest.main()
