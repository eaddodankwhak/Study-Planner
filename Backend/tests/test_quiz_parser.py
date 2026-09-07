"""Regression tests for Markdown and plain-text quiz imports."""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

from app import parse_multiple_choice_text


class QuizParserTest(unittest.TestCase):
    def test_markdown_bold_headings_and_mixed_option_markers(self):
        source = """**1. Which principle supports usability?**
a) Learnability
b) Robustness
c) Flexibility
d) All of the above

**2. What does feedback do?**
A. Confirms the user's action
B. Hides system state
C. Removes all choices
D. Replaces testing
"""
        questions = parse_multiple_choice_text(source)
        self.assertEqual(len(questions), 2)
        self.assertEqual(questions[0]["prompt"], "Which principle supports usability?")
        self.assertEqual([option["label"] for option in questions[0]["options"]], ["A", "B", "C", "D"])
        self.assertEqual(questions[1]["options"][0]["text"], "Confirms the user's action")

    def test_compact_options_and_numbered_questions_without_punctuation(self):
        source = """1 Which law describes target acquisition time?
A) Fitts's Law B) Norman's Law C) Moore's Law D) None
2 Which is a usability principle?
a) Learnability b) Robustness c) Flexibility d) All of these
"""
        questions = parse_multiple_choice_text(source)
        self.assertEqual(len(questions), 2)
        self.assertEqual(questions[0]["prompt"], "Which law describes target acquisition time?")
        self.assertEqual([option["label"] for option in questions[0]["options"]], ["A", "B", "C", "D"])
        self.assertEqual(questions[1]["options"][-1]["text"], "All of these")


if __name__ == "__main__":
    unittest.main()