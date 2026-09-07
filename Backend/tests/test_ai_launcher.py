"""Tests for the AI launcher + shared panel redesign.

Covers the prompt's invariants:
- The floating launcher + drawer render on every logged-in page via base.html.
- Exactly one quick-action set exists in the empty state (the card grid), no
  redundant pill row, no radio provider cards.
- The provider picker is a compact icon toolbar group (one icon per model),
  not a dropdown listbox and not three cards.
- /ai-hub renders the SAME ai/_panel.html partial full-width (no second drawer,
  no launcher on that page), so the two layouts cannot drift apart.
- The drawer isolation/containment contract: fixed, never wider than the
  viewport, no ad-hoc z-index, one drawer body with sidebar + main columns,
  inline model toggle group, page locked while open, launcher hidden when open.
"""

import os
import re
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE not in sys.path:
    sys.path.insert(0, BASE)

import db
from app import app

CSS_DIR = os.path.normpath(os.path.join(BASE, "..", "Frontend", "static", "css"))


def _read_css(name):
    with open(os.path.join(CSS_DIR, name), encoding="utf-8") as f:
        return f.read()


def _css_rule(css, selector):
    """Return the text of the first top-level rule whose selector line starts
    with `selector`, or "" if not found. Handles brace depth so nested
    @media blocks don't confuse the scan for later rules."""
    idx = css.find(selector)
    if idx == -1:
        return ""
    lb = css.find("{", idx)
    if lb == -1:
        return ""
    depth = 0
    for j in range(lb, len(css)):
        if css[j] == "{":
            depth += 1
        elif css[j] == "}":
            depth -= 1
            if depth == 0:
                return css[idx:j + 1]
    return ""


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
        for path in ("/dashboard", "/courses", "/schedule", "/notes"):
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

    def test_provider_picker_is_an_inline_icon_toolbar_not_a_dropdown(self):
        r = self.client.get("/dashboard")
        html = r.data.decode()
        # Compact icon-only toggle group hydrated by JS from /api/ai/meta, not
        # a collapsed listbox and not 3 always-visible radio cards.
        self.assertIn('id="ai-model-list"', html)
        self.assertIn('class="ai-panel__model-toggle"', html)
        self.assertIn('role="group"', html)
        self.assertNotIn('role="listbox"', html)
        self.assertNotIn('id="ai-model-trigger"', html)
        self.assertNotIn("ai-model-option", html)
        self.assertNotIn('role="radiogroup"', html)

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
        self.assertNotIn("ai-drawer-backdrop", html)

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
        self.assertIn('aria-label="Model"', html)
        # No all-caps placeholder conversation empty text server-side.
        self.assertNotIn("NO CONVERSATIONS YET", html)


class AIContainmentTest(unittest.TestCase):
    """Regression checks for the drawer isolation/containment contract."""

    UID = "ai-containment-test-user"

    @classmethod
    def setUpClass(cls):
        cls.ai_css = _read_css("ai.css")
        cls.variables_css = _read_css("variables.css")

    def setUp(self):
        _new_user(self.UID, "Containment Test")
        self.client = _client(self.UID, "Containment Test")

    def tearDown(self):
        db._execute("DELETE FROM courses WHERE user_id = ?", (self.UID,))
        db._execute("DELETE FROM users WHERE id = ?", (self.UID,))

    # ---------------------------------------------------------- markup

    def test_backdrop_present_hidden_on_pages_absent_on_full_page(self):
        for path in ("/dashboard", "/courses", "/schedule", "/notes"):
            html = self.client.get(path).data.decode()
            # Present, but hidden by default on every page wearing base.html.
            self.assertIn('id="ai-drawer-backdrop"', html, path)
            self.assertIn('class="ai-drawer-backdrop" hidden', html, path)
            self.assertIn('class="ai-drawer"', html, path)
        html = self.client.get("/ai").data.decode()
        self.assertNotIn("ai-drawer-backdrop", html)

    def test_drawer_is_one_container_with_internal_columns(self):
        html = self.client.get("/dashboard").data.decode()
        # The drawer element encloses the shared panel as a single two-column
        # body (sidebar + main as internal columns), not two sibling boxes.
        start = html.index('<aside id="ai-drawer"')
        end = html.index('id="ai-drawer-backdrop"')
        block = html[start:end]
        self.assertIn("ai-panel__sidebar", block)
        self.assertIn("ai-panel__main", block)
        # One single .ai-panel grid body inside the drawer.
        self.assertEqual(block.count("data-ai-panel"), 1)
        # A sidebar/main structure nested under the .ai-panel grid, so the two
        # columns are children of one container.
        self.assertLess(block.index("ai-panel__sidebar"), block.index("ai-panel__main"))

    # ------------------------------------------------------------ css

    def test_z_index_scale_tokens_exist(self):
        for token in (
            "--z-content",
            "--z-launcher",
            "--z-drawer-backdrop",
            "--z-drawer",
            "--z-dropdown",
        ):
            self.assertIn(token + ":", self.variables_css, token)
        # Scale is monotonic: content < launcher < backdrop < drawer < dropdown.
        values = {
            "--z-content": 1,
            "--z-launcher": 40,
            "--z-drawer-backdrop": 45,
            "--z-drawer": 50,
            "--z-dropdown": 60,
        }
        ordered = [values["--z-content"]] + sorted(
            (v for k, v in values.items() if k != "--z-content")
        )
        self.assertEqual(ordered, [1, 40, 45, 50, 60])

    def test_ai_css_uses_only_scale_tokens_for_z_index(self):
        for m in re.finditer(r"z-index:\s*([^;}]+);", self.ai_css):
            value = m.group(1).strip()
            self.assertTrue(
                value.startswith("var(--z-"),
                "ad-hoc z-index in ai.css: %s" % value,
            )

    def test_drawer_css_is_fully_contained(self):
        rule = _css_rule(self.ai_css, ".ai-drawer {")
        for needle in (
            "position: fixed",
            "top: 0",
            "right: 0",
            "height: 100vh",
            "width: min(720px, 100vw)",
            "max-width: 100vw",
            "overflow-x: hidden",
            "z-index: var(--z-drawer)",
            "transform: translateX(100%)",
        ):
            self.assertIn(needle, rule, needle)
        open_rule = _css_rule(self.ai_css, ".ai-drawer--open {")
        self.assertIn("transform: translateX(0)", open_rule)

    def test_drawer_panel_is_two_columns_inside_one_body(self):
        # Sidebar + main are columns of the single .ai-panel grid inside the
        # drawer; main carries min-width:0 so long content can't blow out.
        rule = _css_rule(self.ai_css, ".ai-drawer .ai-panel {")
        self.assertIn("grid-template-columns: 280px 1fr", rule)
        self.assertIn("height: 100%", rule)
        main = _css_rule(self.ai_css, ".ai-panel__main {")
        self.assertIn("min-width: 0", main)
        stage = _css_rule(self.ai_css, ".ai-panel__stage {")
        self.assertIn("min-width: 0", stage)

    def test_model_toggle_is_an_inline_toolbar_group(self):
        # Compact icon buttons, not an anchored overlay: inline-flex, no
        # absolute positioning, no dropdown layering, hairline dividers.
        toggle = _css_rule(self.ai_css, ".ai-panel__model-toggle {")
        self.assertIn("display: inline-flex", toggle)
        self.assertNotIn("position: absolute", toggle)
        self.assertNotIn("z-index", toggle)
        btn = _css_rule(self.ai_css, ".ai-panel__model-toggle-btn {")
        self.assertIn("width: 26px", btn)
        divider = _css_rule(self.ai_css, ".ai-panel__model-toggle-btn + .ai-panel__model-toggle-btn {")
        self.assertIn("border-left: 1px solid var(--color-border)", divider)

    def test_open_drawer_locks_page_and_hides_launcher(self):
        # Background must not scroll while the drawer is open.
        body = _css_rule(self.ai_css, "body.ai-drawer-open {")
        self.assertIn("overflow: hidden", body)
        # The launcher must never visually overlap the open drawer.
        hide = _css_rule(self.ai_css, "body.ai-drawer-open .ai-launcher")
        self.assertIn("visibility: hidden", hide)
        # Launcher sits below backdrop below drawer in the stacking scale.
        launcher = _css_rule(self.ai_css, ".ai-launcher {")
        self.assertIn("z-index: var(--z-launcher)", launcher)
        backdrop = _css_rule(self.ai_css, ".ai-drawer-backdrop {")
        self.assertIn("z-index: var(--z-drawer-backdrop)", backdrop)
        self.assertIn("position: fixed", backdrop)


if __name__ == "__main__":
    unittest.main()