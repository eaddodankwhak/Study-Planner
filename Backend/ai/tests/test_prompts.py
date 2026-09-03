"""Tests for prompt building and student context."""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BASE)

from ai import prompts  # noqa: E402
from ai import context  # noqa: E402


class PromptBuilderTest(unittest.TestCase):
    def test_base_system(self):
        prompt = prompts.base_system_prompt()
        self.assertIn("study assistant", prompt)
        self.assertIn("academic", prompt)

    def test_mode_instruction_defaults_to_ask(self):
        self.assertEqual(
            prompts.mode_instruction("ask"),
            prompts.MODE_PROMPTS["ask"],
        )
        # unknown mode falls back to ask
        self.assertEqual(prompts.mode_instruction("bogus"), prompts.MODE_PROMPTS["ask"])

    def test_known_mode_instruction(self):
        self.assertIn("QUIZ ME", prompts.mode_instruction("quiz"))

    def test_build_system_prompt_includes_mode_and_context(self):
        system = prompts.build_system_prompt("simplify", context_text="Courses: Maths")
        self.assertIn("SIMPLIFY", system)
        self.assertIn("Courses: Maths", system)
        self.assertIn("AI_MODE=simplify", system)

    def test_build_user_prompt_includes_material(self):
        user_prompt = prompts.build_user_prompt(
            "solve", "integrate x^2", material_text="Notes about calculus"
        )
        self.assertIn("STUDY MATERIAL", user_prompt)
        self.assertIn("integrate x^2", user_prompt)
        self.assertIn("(solve):", user_prompt)

    def test_build_user_prompt_no_material(self):
        user_prompt = prompts.build_user_prompt("ask", "hello")
        self.assertNotIn("STUDY MATERIAL", user_prompt)
        self.assertIn("hello", user_prompt)


class ContextBuilderTest(unittest.TestCase):
    def test_empty_user_returns_none(self):
        self.assertIsNone(context.build_context(None))
        self.assertIsNone(context.build_context({}))

    def test_builds_context_string(self):
        user = {
            "courses": ["Biology", "Chemistry", ""],
            "program": "BSc Biology",
            "goals": "Pass exams",
        }
        ctx = context.build_context(user, subject_title="Genetics")
        self.assertIn("Biology, Chemistry", ctx)
        self.assertIn("Genetics", ctx)
        self.assertIn("BSc Biology", ctx)
        self.assertIn("Pass exams", ctx)

    def test_ignores_blank_courses(self):
        user = {"courses": ["  ", ""]}
        ctx = context.build_context(user)
        self.assertIsNone(ctx)


if __name__ == "__main__":
    unittest.main()