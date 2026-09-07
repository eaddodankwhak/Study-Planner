/*
 * AI assistant floating launcher.
 *
 * A persistent, draggable chat head on every page (Intercom-style). Position
 * persists per-device in localStorage; snap-to-edge with a spring settle on
 * release. Works identically by click, by keyboard (Ctrl/Cmd+J), and by drag —
 * a click is never mistaken for a failed drag. No idle/looping animation:
 * a button seen on every page load earns a presence, not motion.
 */
(function () {
  "use strict";

  var launcher = document.getElementById("ai-launcher");
  var drawer = document.getElementById("ai-drawer");
  if (!launcher || !drawer) return;

  var STORAGE_KEY = "ai-launcher-position";
  var MARGIN = 12;

  function clampToViewport(x, y, el) {
    var maxX = window.innerWidth - el.offsetWidth - MARGIN;
    var maxY = window.innerHeight - el.offsetHeight - MARGIN;
    return {
      x: Math.min(Math.max(MARGIN, x), maxX),
      y: Math.min(Math.max(MARGIN, y), maxY),
    };
  }

  function applyPosition(x, y) {
    var c = clampToViewport(x, y, launcher);
    launcher.style.left = c.x + "px";
    launcher.style.top = c.y + "px";
  }

  // Restore saved position on load; default to bottom-right.
  var saved = null;
  try { saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null"); } catch (e) { saved = null; }
  applyPosition(
    saved && typeof saved.x === "number" ? saved.x : window.innerWidth - 80,
    saved && typeof saved.y === "number" ? saved.y : window.innerHeight - 100
  );

  function persist(snapX, y) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ x: snapX, y: y }));
    } catch (e) { /* storage unavailable; keep in-memory position */ }
  }

  function toggleDrawer(open) {
    var isOpen = drawer.getAttribute("aria-hidden") === "false";
    var next = typeof open === "boolean" ? open : !isOpen;
    drawer.setAttribute("aria-hidden", String(!next));
    drawer.classList.toggle("ai-drawer--open", next);
    launcher.setAttribute("aria-expanded", String(next));
    // Lock the page behind the drawer (stops background scroll) and show the
    // backdrop in lockstep with the open state. body.ai-drawer-open also hides
    // the launcher via CSS so it can never peek out from behind the panel.
    document.body.classList.toggle("ai-drawer-open", next);
    var backdrop = document.getElementById("ai-drawer-backdrop");
    if (backdrop) backdrop.hidden = !next;
    if (next) {
      var input = drawer.querySelector("input, textarea, button");
      if (input) input.focus();
    }
  }

  // Expose for the global AI hub controller + tests.
  window.AI_LAUNCHER = { toggle: toggleDrawer, isOpen: function () { return drawer.getAttribute("aria-hidden") === "false"; } };

  // Click / tap toggles the drawer; drag repositions instead.
  var dragging = false;
  var moved = false;
  var offsetX = 0;
  var offsetY = 0;

  launcher.addEventListener("pointerdown", function (e) {
    dragging = true;
    moved = false;
    offsetX = e.clientX - launcher.offsetLeft;
    offsetY = e.clientY - launcher.offsetTop;
    try { launcher.setPointerCapture(e.pointerId); } catch (err) { /* unsupported */ }
  });

  launcher.addEventListener("pointermove", function (e) {
    if (!dragging) return;
    moved = true;
    applyPosition(e.clientX - offsetX, e.clientY - offsetY);
  });

  launcher.addEventListener("pointerup", function (e) {
    if (!dragging) return;
    dragging = false;
    if (moved) {
      // Snap to the nearest horizontal edge with a spring-like settle.
      var snapX = launcher.offsetLeft < window.innerWidth / 2
        ? MARGIN
        : window.innerWidth - launcher.offsetWidth - MARGIN;
      var fromY = launcher.offsetTop;
      var fromX = launcher.offsetLeft;
      var anim = launcher.animate(
        [{ left: fromX + "px", top: fromY + "px" }, { left: snapX + "px", top: fromY + "px" }],
        { duration: 280, easing: "cubic-bezier(0.32, 0.72, 0, 1)", fill: "forwards" }
      );
      anim.onfinish = function () {
        launcher.style.left = snapX + "px";
        launcher.style.top = fromY + "px";
        persist(snapX, fromY);
      };
    } else {
      toggleDrawer();
    }
  });

  // Clicking the backdrop closes the drawer.
  var backdrop = document.getElementById("ai-drawer-backdrop");
  if (backdrop) {
    backdrop.addEventListener("click", function () {
      toggleDrawer(false);
    });
  }

  // Keyboard: Ctrl/Cmd+J opens/closes the assistant from anywhere.
  document.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "j") {
      e.preventDefault();
      toggleDrawer();
    }
  });

  // Close with Escape when the drawer is open.
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape" && drawer.getAttribute("aria-hidden") === "false") {
      toggleDrawer(false);
    }
  });
})();
