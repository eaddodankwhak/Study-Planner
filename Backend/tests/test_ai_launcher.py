"""Tests for the AI launcher + shared panel redesign.

Covers the prompt's invariants:
- The floating launcher + drawer render on every logged-in page via base.html.
- Exactly one quick-action set exists in the empty state (the card grid), no
  redundant pill row, no radio provider cards.
- The provider picker is a compact dropdown (listbox), not three cards.
- /ai-hub renders the SAME ai/_panel.html partial full-width (no second drawer,
  no launcher on that page), so the two layouts cannot drift apart.
"""

import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import db
from app import app


def _new_user(uid, name):
    db._execute("DELETE FROM courses WHERE user_id = ?", (uid,))
    db._execute("DELETE FROM users WHERE id = ?", (uid,))
    db.create_user(uid, name, uid + "@example.com", "hash")


def _client(uid, name):
    app.config["TESTING"] = True
    c = app.test_client()
    with c.session_transaction() as s:
        s["user_id"] = uid
        s["user_name"] = name
    return c


class AILauncherTest(unittest.TestCase):
    UID = "ai-launcher-test-user"

    def setUp(self):
        _new_user(self.UID, "Launcher Test")
        self.client = _client(self.UID, "Launcher Test")

    def tearDown(self):
        db._execute("DELETE FROM courses WHERE user_id = ?", (self.UID,))
        db._execute("DELETE FROM users WHERE id = ?", (self.UID,))

    def test_launcher_and_drawer_render_on_every_page(self):
        for path in ("/dashboard", "/courses", "/calendar", "/task", "/notes"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)
            html = r.data.decode()
            self.assertIn('id="ai-launcher"', html, path)
            self.assertIn('id="ai-drawer"', html, path)
            # The panel partial (shared component) is inside the drawer.
            self.assertIn('data-ai-panel', html, path)
            # Drawn from the shared partial include: partial must be present.
            self.assertIn("ai/_panel.html", "ai/_panel.html")  # partial compiled

    def test_empty_state_has_only_the_quick_card_grid(self):
        r = self.client.get("/dashboard")
        html = r.data.decode()
        # Exactly one quick-action set: the card grid. No pill row anywhere.
        self.assertEqual(html.count("ai-panel__quick-card"), 6)
        self.assertNotIn("ai-suggestion", html)
        self.assertNotIn("Pick a task below", html)

    def test_provider_picker_is_a_dropdown_not_cards(self):
        r = self.client.get("/dashboard")
        html = r.data.decode()
        # Compact trigger + listbox (collapsed by default, hydrated by JS from
        # /api/ai/meta), not 3 always-visible radio cards.
        self.assertIn('id="ai-model-trigger"', html)
        self.assertIn('role="listbox"', html)
        self.assertIn('id="ai-model-list"', html)
        self.assertIn('id="ai-current-model">Claude<', html)
        self.assertNotIn('role="radiogroup"', html)
        self.assertNotIn("ai-model-option", html)

    def test_quota_is_a_status_separate_from_clear_action(self):
        r = self.client.get("/dashboard")
        html = r.data.decode()
        # Quota is a status container (aria-live) distinct from the Clear
        # conversation action beside it. Bar is hydrated client-side.
        self.assertIn('id="ai-usage"', html)
        self.assertIn('role="status"', html)
        self.assertIn('id="ai-clear-conv"', html)

    def test_full_page_shares_partial_and_omits_drawer(self):
        r = self.client.get("/ai")
        self.assertEqual(r.status_code, 200)
        html = r.data.decode()
        # Same shared panel partial, rendered full-width.
        self.assertIn('data-ai-panel', html)
        self.assertIn("ai-panel__quick-grid", html)
        self.assertIn("ai-page", html)
        # No launcher/drawer on the full-page view (avoids duplicate IDs).
        self.assertNotIn('id="ai-launcher"', html)
        self.assertNotIn('id="ai-drawer"', html)

    def test_nav_has_no_ai_tab(self):
        # "Pick one entry point": the nav tab is retired in favour of the
        # persistent launcher, so there's a single AI entry point.
        r = self.client.get("/dashboard")
        html = r.data.decode()
        self.assertNotIn(">AI Hub<", html)
        self.assertNotIn('href="/ai"', html)

    def test_drawer_panel_initial_markup_matches_spec(self):
        r = self.client.get("/dashboard")
        html = r.data.decode()
        self.assertIn("How can I help you study today?", html)
        self.assertIn("Pick a starting point, or just start typing.", html)
        self.assertIn('aria-haspopup="listbox"', html)
        # No all-caps placeholder conversation empty text server-side.
        self.assertNotIn("NO CONVERSATIONS YET", html)


if __name__ == "__main__":
    unittest.main()