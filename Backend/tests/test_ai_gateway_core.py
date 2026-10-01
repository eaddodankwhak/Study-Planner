"""Tests for the AI Gateway adapters and core (Phase 2).

Suites:
- GatewayValidationTest : JSON extraction + schema validation.
- GatewayAdapterTest    : adapter build + generate happy path (mocked HTTP),
                          error mapping, unknown adapter rejection.
- GatewayCoreTest       : key resolution (server first, then BYOK),
                          model selection, AIDisabledError, usage accounting,
                          generate_text / generate_json with a fake adapter.

The provider HTTP layer is monkeypatched so no test touches the network.
"""

import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("DATABASE_PATH", os.path.join(os.path.expanduser("~"), "ai-gw-core-test.db"))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import db  # noqa: E402

db.init_db()

from app import app  # noqa: E402  F401
import ai_gateway as gw  # noqa: E402
from ai_gateway import adapters, registry, validation  # noqa: E402
from ai_gateway.config import GatewayConfig  # noqa: E402
from ai_gateway.errors import (  # noqa: E402
    AIDisabledError,
    NoProviderAvailableError,
    JSONValidationError,
)
from ai.providers._http import ProviderHTTPError  # noqa: E402

UID = "gateway-core-test-user"


def _clean():
    for sql in (
        "DELETE FROM ai_gateway_usage WHERE user_id = ?",
        "DELETE FROM ai_connections WHERE user_id = ?",
        "DELETE FROM users WHERE id = ?",
    ):
        db._execute(sql, (UID,))
    # Reset the seed flag so env-key changes reflect.
    registry._seeded = False
    registry.ensure_seeded()


def _server_keys(keys):
    """Temporarily set the gateway's server keys (shared singleton)."""
    return mock.patch.object(GatewayConfig, "server_keys", dict(keys))


class GatewayValidationTest(unittest.TestCase):
    def test_extract_json_handles_fences_and_prose(self):
        self.assertEqual(
            validation.extract_json('here: ```json\n{"a": 1}\n``` nice'),
            {"a": 1},
        )
        self.assertEqual(
            validation.extract_json('leading prose {"b": 2} trailing'),
            {"b": 2},
        )
        self.assertIsNone(validation.extract_json('```json\nnull\n```'))

    def test_extract_json_rejects_garbage(self):
        with self.assertRaises(validation.ParseError):
            validation.extract_json("not json at all")

    def test_validate_schema_basic(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
            "required": ["name"],
            "additionalProperties": False,
        }
        self.assertEqual(validation.validate_schema({"name": "x", "age": 3}, schema), [])
        self.assertTrue(validation.validate_schema({"name": 1}, schema))
        self.assertTrue(validation.validate_schema({"name": "x", "extra": 1}, schema))
        self.assertTrue(validation.validate_schema({"age": 3}, schema))

    def test_validate_schema_array_items(self):
        schema = {
            "type": "object",
            "properties": {"cards": {"type": "array", "items": {"type": "object"}}},
            "required": ["cards"],
        }
        self.assertEqual(
            validation.validate_schema({"cards": [{"type": "concept"}]}, schema), []
        )
        self.assertTrue(validation.validate_schema({"cards": [1, 2]}, schema))

    def test_validate_schema_enum(self):
        schema = {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": ["a", "b"]}},
            "required": ["kind"],
        }
        self.assertEqual(validation.validate_schema({"kind": "a"}, schema), [])
        self.assertTrue(validation.validate_schema({"kind": "z"}, schema))


class FakeAdapter:
    """Drop-in adapter returning canned content; used to keep gateway tests
    hermetic and assert the requested payload."""

    provider_role = "fake"

    def __init__(self, result=None, error=None, api_key=None):
        self.result = result or {"content": "{}", "usage": {"inputTokens": 5, "outputTokens": 3}}
        self.error = error
        self.api_key = api_key
        self.calls = []

    def generate(self, request):
        self.calls.append(request)
        if self.error:
            raise self.error
        return self.result

    def generate_json(self, request, schema=None):
        self.calls.append((request, schema))
        return self.result


class GatewayAdapterTest(unittest.TestCase):
    def test_build_adapter_openai(self):
        p = registry.get_provider("openai")
        a = adapters.build_adapter(p, "sk-test")
        self.assertIsInstance(a, adapters.OpenAICompatAdapter)

    def test_build_adapter_anthropic(self):
        p = registry.get_provider("anthropic")
        a = adapters.build_adapter(p, "sk-test")
        self.assertIsInstance(a, adapters.AnthropicAdapter)

    def test_build_adapter_unknown(self):
        with self.assertRaises(ValueError):
            adapters.build_adapter({"adapter": "nope", "base_url": None}, "k")

    @mock.patch("ai_gateway.adapters.post_json")
    def test_openai_generate_maps_responses(self, post_json):
        post_json.return_value = {
            "choices": [{"message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 6},
        }
        a = adapters.OpenAICompatAdapter("sk", "https://api.openai.com/v1")
        result = a.generate({"model": "gpt-5-mini", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(result["content"], "hello")
        self.assertEqual(result["usage"]["inputTokens"], 4)
        self.assertEqual(result["usage"]["outputTokens"], 6)

    @mock.patch("ai_gateway.adapters.post_json")
    def test_openai_json_mode_sets_response_format(self, post_json):
        post_json.return_value = {
            "choices": [{"message": {"content": "{\"ok\": true}"}}],
            "usage": {},
        }
        a = adapters.OpenAICompatAdapter("sk", "https://api.openai.com/v1")
        a.generate_json(
            {"model": "gpt-5-mini", "messages": [{"role": "user", "content": "go"}]},
            schema={"type": "object"},
        )
        payload = post_json.call_args[0][2]
        self.assertEqual(payload["response_format"]["type"], "json_object")

    @mock.patch("ai_gateway.adapters.post_json")
    def test_anthropic_generate(self, post_json):
        post_json.return_value = {
            "content": [{"type": "text", "text": "ciao"}],
            "usage": {"input_tokens": 2, "output_tokens": 4},
        }
        a = adapters.AnthropicAdapter("sk", "https://api.anthropic.com")
        result = a.generate({"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(result["content"], "ciao")
        self.assertEqual(result["usage"]["inputTokens"], 2)
        self.assertEqual(result["usage"]["outputTokens"], 4)
        headers = post_json.call_args[0][1]
        self.assertEqual(headers["x-api-key"], "sk")


class GatewayCoreTest(unittest.TestCase):
    def setUp(self):
        _clean()
        db.create_user(UID, "Gateway Core", UID + "@example.com", "hash")
        db.update_settings(UID, {})  # ensure settings row materializes

    def tearDown(self):
        _clean()

    def test_server_key_resolution(self):
        with _server_keys({
            "OPENAI_API_KEY": "sk-server",
            "ANTHROPIC_API_KEY": "sk-server-2",
        }):
            key = gw.server_key_for("openai")
            self.assertEqual(key, "sk-server")
            key2 = gw.server_key_for("anthropic")
            self.assertEqual(key2, "sk-server-2")

    @mock.patch("ai_gateway.gateway.build_adapter")
    def test_generate_text_uses_server_key_and_records_usage(self, build_adapter):
        fake = FakeAdapter(
            result={"content": "lesson text", "usage": {"inputTokens": 10, "outputTokens": 20}}
        )
        build_adapter.return_value = fake
        with _server_keys({"OPENAI_API_KEY": "sk-server"}):
            out = gw.generate_text(UID, "Explain photosynthesis", feature="chat")
        self.assertEqual(out["content"], "lesson text")
        self.assertEqual(out["paid_by"], "server")
        # usage row was written
        with db._conn_context() as conn:
            row = conn.execute(
                "SELECT requests, input_tokens, output_tokens FROM ai_gateway_usage "
                "WHERE user_id = ? AND feature = 'chat' AND used_own_key = 0",
                (UID,),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["requests"], 1)
        self.assertEqual(row["input_tokens"], 10)
        self.assertEqual(row["output_tokens"], 20)

    @mock.patch("ai_gateway.gateway.build_adapter")
    def test_generate_text_uses_byok_when_no_server_key(self, build_adapter):
        fake = FakeAdapter(result={"content": "x", "usage": {"inputTokens": 1, "outputTokens": 1}})
        build_adapter.return_value = fake
        # Connect the user's personal OpenAI key.
        db.set_ai_connection(UID, "openai", "sk-personal", label="Mine")
        with _server_keys({}):
            out = gw.generate_text(UID, "Hi", feature="chat")
        self.assertEqual(out["paid_by"], "personal")
        # The BYOK key (not a server key) was handed to the adapter builder.
        self.assertEqual(build_adapter.call_args[0][1], "sk-personal")

    def test_privacy_disabled_raises(self):
        db.update_settings(UID, {"privacy": {"ai_activity": False}})
        with self.assertRaises(AIDisabledError):
            gw.generate_text(UID, "Hi", feature="chat")

    @mock.patch("ai_gateway.gateway.build_adapter")
    def test_generate_json_parses_and_validates(self, build_adapter):
        schema = {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        }
        fake = FakeAdapter(result={"content": '{"title": "T"}' , "usage": {"inputTokens": 1, "outputTokens": 1}})
        build_adapter.return_value = fake
        with _server_keys({"OPENAI_API_KEY": "sk-server"}):
            parsed = gw.generate_json(UID, schema, "Make a title", feature="quiz")
        self.assertEqual(parsed, {"title": "T"})

    @mock.patch("ai_gateway.gateway.build_adapter")
    def test_generate_json_retries_then_raises_on_bad_json(self, build_adapter):
        schema = {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        }

        class Flaky(FakeAdapter):
            def __init__(self):
                super().__init__()
                self.n = 0

            def generate_json(self, request, schema=None):
                self.n += 1
                self.calls.append((request, schema))
                if self.n == 1:
                    return {"content": "not json", "usage": {}}
                return {"content": '{"wrong": "shape"}', "usage": {}}

        fake = Flaky()
        build_adapter.return_value = fake
        with _server_keys({"OPENAI_API_KEY": "sk-server"}):
            with self.assertRaises(JSONValidationError):
                gw.generate_json(UID, schema, "Make a title", feature="quiz")
        self.assertGreaterEqual(fake.n, 2)  # at least one retry happened

    def test_no_provider_raises_friendly(self):
        with _server_keys({}):  # no server keys, no BYOK
            with self.assertRaises(NoProviderAvailableError):
                gw.select_model(UID, feature="cards")

    def test_execute_via_rows(self):
        # ensure new tables are queryable for real
        with db._conn_context() as conn:
            n = conn.execute("SELECT COUNT(*) AS n FROM ai_providers").fetchone()["n"]
        self.assertGreaterEqual(n, 8)


if __name__ == "__main__":
    unittest.main()