"""Tests for the AI model registry and capability system."""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)

from ai import models as models  # noqa: E402


class ModelRegistryTest(unittest.TestCase):
    def test_models_available_returns_all(self):
        available = models.models_available()
        ids = {m["id"] for m in available}
        self.assertIn("claude", ids)
        self.assertIn("gpt", ids)
        self.assertIn("gemini", ids)

    def test_get_model_known(self):
        m = models.get_model("claude")
        self.assertIsNotNone(m)
        self.assertEqual(m["provider"], "anthropic")
        self.assertEqual(m["id"], "claude")

    def test_get_model_unknown_returns_none(self):
        self.assertIsNone(models.get_model("nonexistent-model"))

    def test_mode_listing(self):
        ids = {m["id"] for m in models.MODES}
        for expected in ("explain", "summarize", "solve", "quiz", "flashcards",
                        "study_plan", "simplify", "exam_prep", "ask"):
            self.assertIn(expected, ids)

    def test_capability_support(self):
        self.assertTrue(models.model_supports("claude", "TEXT"))
        self.assertTrue(models.model_supports("gemini", "IMAGE"))
        self.assertFalse(models.model_supports("unknown-model", "TEXT"))

    def test_model_descriptor_shape(self):
        m = models.get_model("gpt")
        self.assertIn("displayName", m)
        self.assertIn("context_window", m)
        self.assertIn("supports_streaming", m)
        self.assertIn("capabilities", m)


if __name__ == "__main__":
    unittest.main()