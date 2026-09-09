"""Tests for Backend/stats.py — Progress page statistics."""

import os
import sys
import time
import unittest
from datetime import date, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import db
import stats

db.init_db()

UID = "stats-test-user"


class WeeklyMinutesTest(unittest.TestCase):
    def setUp(self):
        db._execute("DELETE FROM sessions WHERE user_id = ?", (UID,))
        db._execute("DELETE FROM users WHERE id = ?", (UID,))
        db.create_user(UID, "Stats Test", "stats@example.com", "hash")

    def tearDown(self):
        db._execute("DELETE FROM sessions WHERE user_id = ?", (UID,))
        db._execute("DELETE FROM users WHERE id = ?", (UID,))

    def test_weekly_minutes_returns_one_row_per_day_oldest_first(self):
        rows = stats.weekly_minutes(UID, db, days=7)
        self.assertEqual(len(rows), 7)
        # Oldest day first, then strictly increasing.
        dates = [r["date"] for r in rows]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(dates[0], (date.today() - timedelta(days=6)).isoformat())
        self.assertEqual(dates[-1], date.today().isoformat())
        for r in rows:
            self.assertEqual(r["minutes"], 0)
            self.assertEqual(r["pct"], 0)
            self.assertIn(r["weekday"], ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"))

    def test_weekly_minutes_counts_and_normalizes_sessions(self):
        now = int(time.time())
        today_mid = int(time.mktime(date.today().timetuple()))
        db.create_session(UID, duration_minutes=45, started_at=now - 60, ended_at=now)
        db.create_session(UID, duration_minutes=15, started_at=now, ended_at=now + 60)
        # A session far outside the window is excluded.
        db.create_session(UID, duration_minutes=999, started_at=today_mid - 10 * 86400,
                          ended_at=today_mid - 10 * 86400)
        rows = stats.weekly_minutes(UID, db, days=7)
        today_row = [r for r in rows if r["date"] == date.today().isoformat()][0]
        self.assertEqual(today_row["minutes"], 60)
        self.assertEqual(today_row["pct"], 100)
        self.assertEqual(sum(r["minutes"] for r in rows), 60)


if __name__ == "__main__":
    unittest.main()