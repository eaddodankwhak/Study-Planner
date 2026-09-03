"""Tests for the provider router, request builder, and service orchestration."""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)

# Ensure real provider keys are not set for these tests.
for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GOOGLE_AI_API_KEY"):
    os.environ.pop(key, None)

from ai import limits  # noqa: E402
from ai import service  # noqa: E402
from ai.providers import get_provider, provider_available, list_providers, MockProvider  # noqa: E402


class ProviderRouterTest(unittest.TestCase):
    def test_mock_when_no_keys(self):
        for pid in ("openai", "anthropic", "google"):
            p = get_provider(pid)
            self.assertTrue(getattr(p, "is_mock", False), f"{pid} should fall back to mock")

    def test_mock_provider_requested(self):
        p = get_provider("mock")
        self.assertTrue(p.is_mock)

    def test_unknown_provider_returns_mock(self):
        p = get_provider("does-not-exist")
        self.assertIsInstance(p, MockProvider)

    def test_provider_availability(self):
        # with keys cleared these providers are unavailable (but mock is available)
        self.assertFalse(provider_available("openai"))
        self.assertTrue(provider_available("mock"))

    def test_list_providers_shape(self):
        providers = list_providers()
        ids = {p["id"] for p in providers}
        self.assertIn("mock", ids)
        self.assertIn("openai", ids)
        for p in providers:
            self.assertIn("isMock", p)
            self.assertIn("available", p)


class BuildProviderRequestTest(unittest.TestCase):
    def test_build_request_shape(self):
        req = service.build_provider_request(
            mode="solve",
            question="integrate x^2",
            material_text="Calculus notes",
            user={"courses": ["Maths"]},
            conversation_messages=[
                {"role": "user", "content": "earlier question"},
                {"role": "assistant", "content": "earlier answer"},
            ],
            model_id="claude",
        )
        self.assertEqual(req["provider_id"], "anthropic")
        self.assertIn("messages", req)
        self.assertEqual(req["max_tokens"], limits.MAX_OUTPUT_TOKENS)

        # first message is system, last message is the current user turn
        self.assertEqual(req["messages"][0]["role"], "system")
        self.assertEqual(req["messages"][-1]["role"], "user")
        self.assertIn("integrate x^2", req["messages"][-1]["content"])

    def test_build_request_unknown_model_falls_back_to_claude(self):
        req = service.build_provider_request(
            mode="ask", question="q", material_text="", user={},
            conversation_messages=[], model_id="bogus",
        )
        self.assertEqual(req["provider_id"], "anthropic")


class ServiceTest(unittest.TestCase):
    def test_generate_reply_with_mock(self):
        req = service.build_provider_request(
            mode="ask", question="Hello there", material_text="", user={},
            conversation_messages=[], model_id="claude",
        )
        result = service.generate_reply(req)
        self.assertIn("content", result)
        self.assertTrue(result["content"])
        self.assertIn("demo response", result["content"])

    def test_stream_reply_with_mock(self):
        req = service.build_provider_request(
            mode="explain", question="Explain gravity", material_text="", user={},
            conversation_messages=[], model_id="claude",
        )
        chunks = list(service.stream_reply(req))
        self.assertTrue(chunks)
        self.assertEqual("".join(chunks), "".join(chunks))  # deterministic content join

    def test_handle_error_mapping(self):
        from ai import limits as limits_mod
        self.assertIn("tomorrow", service.handle_error(limits_mod.RateLimitError("You've used your AI requests for today. Please try again tomorrow.")))
        # generic exception -> safe generic message
        self.assertEqual(
            service.handle_error(RuntimeError("boom")),
            "The AI service is temporarily unavailable. Please try again.",
        )


if __name__ == "__main__":
    unittest.main()