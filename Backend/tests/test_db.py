"""Tests for the shared SQLite layer (db.py): users, collab, and AI tables."""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import db
import collab


class UsersTest(unittest.TestCase):
    def setUp(self):
        self.uid = "db-test-user"
        self.email = "db-test@example.com"
        db._execute("DELETE FROM users WHERE id = ?", (self.uid,))

    def tearDown(self):
        db._execute("DELETE FROM users WHERE id = ?", (self.uid,))

    def test_create_and_get_user(self):
        db.create_user(self.uid, "Test User", self.email, "hash")
        user = db.get_user(self.uid)
        self.assertEqual(user["name"], "Test User")
        self.assertEqual(user["email"], self.email)
        self.assertIs(user["onboarded"], False)
        self.assertEqual(user["courses"], [])

    def test_get_user_by_email(self):
        db.create_user(self.uid, "Test User", self.email, "hash")
        user = db.get_user_by_email(self.email)
        self.assertEqual(user["id"], self.uid)

    def test_load_users_dict(self):
        db.create_user(self.uid, "Test User", self.email, "hash")
        users = db.load_users()
        self.assertIn(self.uid, users)
        self.assertEqual(users[self.uid]["name"], "Test User")

    def test_set_onboarded_and_preferences(self):
        db.create_user(self.uid, "Test User", self.email, "hash")
        db.set_user_onboarded(self.uid, "UG", "CS", ["DCIT 204", "STAT 222"], "pass")
        user = db.get_user(self.uid)
        self.assertTrue(user["onboarded"])
        self.assertEqual(user["courses"], ["DCIT 204", "STAT 222"])
        self.assertEqual(user["program"], "CS")

        db.set_ai_preferences(self.uid, model="gpt", level="advanced")
        user = db.get_user(self.uid)
        self.assertEqual(user["ai_model"], "gpt")
        self.assertEqual(user["ai_level"], "advanced")

    def test_update_user(self):
        db.create_user(self.uid, "Test User", self.email, "hash")
        db.update_user(self.uid, {"goals": "graduate"})
        self.assertEqual(db.get_user(self.uid)["goals"], "graduate")

    def test_save_onboarding_progress_keeps_draft_without_onboarding(self):
        db.create_user(self.uid, "Test User", self.email, "hash")
        db.save_onboarding_progress(self.uid, "UG", "CS", ["DCIT 204"], "", available_hours=6)
        user = db.get_user(self.uid)
        self.assertIs(user["onboarded"], False)
        self.assertEqual(user["school"], "UG")
        self.assertEqual(user["program"], "CS")
        self.assertEqual(user["courses"], ["DCIT 204"])
        self.assertEqual(user["available_hours"], 6)

        # Running it again overwrites the draft, never completing it.
        db.save_onboarding_progress(self.uid, "UG", "CS", ["DCIT 204", "STAT 222"], "pass")
        user = db.get_user(self.uid)
        self.assertIs(user["onboarded"], False)
        self.assertEqual(user["courses"], ["DCIT 204", "STAT 222"])
        self.assertEqual(user["goals"], "pass")

        # Finishing flips the flag on top of the same draft.
        db.set_user_onboarded(self.uid, "UG", "CS", ["DCIT 204", "STAT 222"], "pass")
        self.assertTrue(db.get_user(self.uid)["onboarded"])


class CollabTest(unittest.TestCase):
    def setUp(self):
        self.slug = "db-test-subject"
        self.uid = "db-test-collab-user"
        db._execute("DELETE FROM memberships WHERE slug = ?", (self.slug,))
        db._execute("DELETE FROM materials WHERE slug = ?", (self.slug,))
        db._execute("DELETE FROM quizzes WHERE subject = ?", (self.slug,))

    def tearDown(self):
        db._execute("DELETE FROM memberships WHERE slug = ?", (self.slug,))
        db._execute("DELETE FROM materials WHERE slug = ?", (self.slug,))
        db._execute("DELETE FROM quizzes WHERE subject = ?", (self.slug,))

    def test_membership(self):
        self.assertFalse(collab.is_member(self.slug, self.uid))
        collab.add_member(self.slug, self.uid)
        self.assertTrue(collab.is_member(self.slug, self.uid))
        self.assertIn(self.uid, collab.get_members(self.slug))
        collab.remove_member(self.slug, self.uid)
        self.assertFalse(collab.is_member(self.slug, self.uid))

    def test_subject_code(self):
        code = collab.get_subject_code(self.slug)
        self.assertTrue(code.startswith("SP-"))
        self.assertTrue(collab.subject_code_match(self.slug, code))
        self.assertEqual(collab.get_subject_code(self.slug), code)  # stable

    def test_materials(self):
        collab.add_material(self.slug, "notes.pdf", self.uid)
        self.assertEqual(len(collab.get_materials(self.slug)), 1)
        self.assertEqual(collab.get_materials(self.slug)[0]["filename"], "notes.pdf")

    def test_quiz_lifecycle(self):
        quiz = collab.create_quiz(
            self.slug, "Midterm", "Quick check",
            [{"q": "2+2", "a": "4"}], self.uid,
        )
        self.assertEqual(collab.list_quizzes(self.slug)[0]["id"], quiz["id"])
        self.assertIsNotNone(collab.get_quiz(quiz["id"]))
        self.assertIsNotNone(collab.find_quiz_by_code(quiz["invite_code"]))
        collab.add_attempt(quiz["id"], self.uid, 5, 10)
        self.assertEqual(len(collab.get_attempts(quiz["id"])), 1)
        self.assertEqual(collab.user_best_score(quiz["id"], self.uid), 5)


if __name__ == "__main__":
    unittest.main()