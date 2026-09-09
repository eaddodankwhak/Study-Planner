(function () {
    "use strict";

    // Keyboard navigation (adaptability): single-letter keys jump between the
    // main pages, and "?" opens a cheatsheet. The key set lives in the
    // palette's data-routes JSON (single source of truth); entries with an
    // explicit `key` become shortcuts.
    var routesEl = document.querySelector("[data-routes]");
    if (!routesEl) return;

    var routes = [];
    try {
        routes = JSON.parse(routesEl.getAttribute("data-routes") || "[]");
    } catch (e) {
        routes = [];
    }

    var shortcuts = {};
    routes.forEach(function (r) {
        if (r && r.key && r.url) shortcuts[r.key.toLowerCase()] = r.url;
    });

    var specials = [
        { keys: ["Ctrl", "K"], label: "Command palette" },
        { keys: ["Ctrl", "J"], label: "AI assistant" },
        { keys: ["?"], label: "This shortcut help" }
    ];

    var sheet = null;
    var lastTrigger = null;

    function isEditable(el) {
        if (!el || el.isContentEditable) return true;
        var tag = (el.tagName || "").toUpperCase();
        return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
    }

    function anyModalOpen() {
        var drawer = document.querySelector("#ai-drawer[aria-hidden=\"false\"]");
        if (drawer) return true;
        var palette = document.querySelector("[data-palette]");
        if (palette && palette.getAttribute("aria-hidden") !== "true") return true;
        return false;
    }

    function buildSheet() {
        if (sheet) return;

        var overlay = document.createElement("div");
        overlay.className = "shortcuts is-closed";
        overlay.setAttribute("data-shortcuts", "");
        overlay.setAttribute("aria-hidden", "true");

        var backdrop = document.createElement("div");
        backdrop.className = "shortcuts__backdrop";
        overlay.appendChild(backdrop);

        var dialog = document.createElement("div");
        dialog.className = "shortcuts__dialog";
        dialog.setAttribute("role", "dialog");
        dialog.setAttribute("aria-modal", "true");
        dialog.setAttribute("aria-label", "Keyboard shortcuts");

        var title = document.createElement("h2");
        title.className = "shortcuts__title";
        title.textContent = "Keyboard shortcuts";
        dialog.appendChild(title);

        var list = document.createElement("ul");
        list.className = "shortcuts__grid";

        Object.keys(shortcuts).forEach(function (key) {
            var li = document.createElement("li");
            li.className = "shortcuts__row";

            var kbd = document.createElement("kbd");
            kbd.className = "shortcuts__kbd";
            kbd.textContent = key;

            var label = document.createElement("span");
            label.className = "shortcuts__label";
            var entry = routes.filter(function (r) { return r.key === key; })[0];
            label.textContent = entry ? entry.label : key;

            li.appendChild(kbd);
            li.appendChild(label);
            list.appendChild(li);
        });

        specials.forEach(function (s) {
            var li = document.createElement("li");
            li.className = "shortcuts__row";

            var kbd = document.createElement("span");
            kbd.className = "shortcuts__kbd";
            s.keys.forEach(function (k, i) {
                var code = document.createElement("kbd");
                code.textContent = k;
                kbd.appendChild(code);
                if (i < s.keys.length - 1) kbd.appendChild(document.createTextNode(" + "));
            });

            var label = document.createElement("span");
            label.className = "shortcuts__label";
            label.textContent = s.label;

            li.appendChild(kbd);
            li.appendChild(label);
            list.appendChild(li);
        });

        dialog.appendChild(list);

        var closeBtn = document.createElement("button");
        closeBtn.type = "button";
        closeBtn.className = "button button--ghost button--small shortcuts__close";
        closeBtn.textContent = "Close";
        dialog.appendChild(closeBtn);

        overlay.appendChild(dialog);
        document.body.appendChild(overlay);
        sheet = overlay;

        // Backdrop click closes; ignore clicks inside the dialog.
        overlay.addEventListener("mousedown", function (e) {
            if (!e.target.closest(".shortcuts__dialog")) closeSheet();
        });

        // Escape within the dialog closes it too.
        overlay.addEventListener("keydown", function (e) {
            if (e.key === "Escape") {
                e.stopImmediatePropagation();
                closeSheet();
            }
        });
    }

    function openSheet() {
        buildSheet();
        sheet.classList.remove("is-closed");
        sheet.setAttribute("aria-hidden", "false");
        lastTrigger = document.activeElement;
        var closeBtn = sheet.querySelector(".shortcuts__close");
        if (closeBtn) closeBtn.focus();
    }

    function closeSheet() {
        if (!sheet) return;
        sheet.classList.add("is-closed");
        sheet.setAttribute("aria-hidden", "true");
        if (lastTrigger && typeof lastTrigger.focus === "function") lastTrigger.focus();
    }

    function isSheetOpen() {
        return sheet && !sheet.classList.contains("is-closed");
    }

    document.addEventListener("keydown", function (e) {
        if (e.key === "Escape" && isSheetOpen()) {
            e.preventDefault();
            closeSheet();
            return;
        }

        if (e.ctrlKey || e.metaKey || e.altKey) return;
        if (isSheetOpen() || anyModalOpen()) return;
        if (isEditable(e.target)) return;

        if (e.key === "?" || (e.key === "/" && e.shiftKey)) {
            e.preventDefault();
            isSheetOpen() ? closeSheet() : openSheet();
            return;
        }

        if (e.key.length !== 1) return;
        var url = shortcuts[e.key.toLowerCase()];
        if (url) {
            e.preventDefault();
            window.location.href = url;
        }
    });
})();