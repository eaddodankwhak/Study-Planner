"""Tests for conversation storage and usage limits.

These operate against the SQLite database using a dedicated test user id and
clean up that user's rows so real user data is never touched.
"""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import db
from ai import storage  # noqa: E402
from ai import limits  # noqa: E402


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.uid = "test-storage-user"
        db.ai_purge_user(self.uid)

    def tearDown(self):
        db.ai_purge_user(self.uid)

    def test_create_and_get_conversation(self):
        conv = storage.create_conversation(self.uid, model="claude", mode="ask")
        self.assertEqual(conv["model"], "claude")
        got = storage.get_conversation(self.uid, conv["id"])
        self.assertEqual(got["id"], conv["id"])
        self.assertEqual(len(got["messages"]), 0)

    def test_conversation_scoped_to_user(self):
        conv = storage.create_conversation(self.uid)
        other = storage.get_conversation("someone-else", conv["id"])
        self.assertIsNone(other)

    def test_add_message_and_auto_title(self):
        conv = storage.create_conversation(self.uid)
        storage.add_message(self.uid, conv["id"], "user", "Explain DNA replication please")
        updated = storage.get_conversation(self.uid, conv["id"])
        self.assertEqual(len(updated["messages"]), 1)
        self.assertEqual(updated["title"], "Explain DNA replication please")
        self.assertEqual(updated["messages"][0]["role"], "user")

    def test_add_message_unknown_conv_returns_none(self):
        self.assertIsNone(storage.add_message(self.uid, "nope", "user", "hi"))

    def test_rename_conversation(self):
        conv = storage.create_conversation(self.uid)
        self.assertTrue(storage.rename_conversation(self.uid, conv["id"], "Renamed"))
        self.assertEqual(storage.get_conversation(self.uid, conv["id"])["title"], "Renamed")
        self.assertFalse(storage.rename_conversation(self.uid, "missing", "x"))

    def test_delete_conversation(self):
        conv = storage.create_conversation(self.uid)
        self.assertTrue(storage.delete_conversation(self.uid, conv["id"]))
        self.assertIsNone(storage.get_conversation(self.uid, conv["id"]))
        self.assertFalse(storage.delete_conversation(self.uid, conv["id"]))

    def test_search_conversations(self):
        conv = storage.create_conversation(self.uid, title="Biology help")
        storage.add_message(self.uid, conv["id"], "user", "What is a cell?")
        results = storage.search_conversations(self.uid, "cell")
        self.assertEqual(len(results), 1)
        results = storage.search_conversations(self.uid, "zzzz")
        self.assertEqual(len(results), 0)


class LimitsTest(unittest.TestCase):
    def setUp(self):
        self.uid = "test-limits-user"
        db.ai_purge_user(self.uid)

    def tearDown(self):
        db.ai_purge_user(self.uid)

    def test_within_limit_succeeds(self):
        limits.check_limit(self.uid)  # should not raise

    def test_limit_enforced(self):
        original = limits.MAX_REQUESTS_PER_DAY
        limits.MAX_REQUESTS_PER_DAY = 1
        try:
            storage.record_usage(self.uid, model="claude", mode="ask")
            limits.check_limit(self.uid)  # reached exactly limit -> raises at >= limit
            self.fail("Expected RateLimitError")
        except limits.RateLimitError:
            pass
        finally:
            limits.MAX_REQUESTS_PER_DAY = original

    def test_remaining_requests(self):
        used, limit = limits.remaining_requests(self.uid)
        self.assertEqual(used, 0)
        self.assertEqual(limit, limits.MAX_REQUESTS_PER_DAY)

    def test_record_usage(self):
        storage.record_usage(self.uid, model="gpt", mode="solve", input_tokens=5, output_tokens=10)
        usage = storage.get_usage(self.uid)
        self.assertEqual(usage["total_requests"], 1)
        self.assertEqual(usage["models"]["gpt"], 1)
        self.assertEqual(usage["modes"]["solve"], 1)
        used, _ = limits.remaining_requests(self.uid)
        self.assertEqual(used, 1)


if __name__ == "__main__":
    unittest.main()