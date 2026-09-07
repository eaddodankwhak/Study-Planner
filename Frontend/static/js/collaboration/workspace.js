/* Workspace list + detail page behaviors:
   - copy invite codes
   - optimistic task status updates (rollback on failure)
   - quick "Block" from the task card
   - in-place task panel loading (JS enhancement; links still work without it)
*/
(function () {
    "use strict";

    var USER_ID = (document.querySelector('meta[name="user-id"]') || {}).content || "";

    function postJSON(url, payload) {
        return fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
            credentials: "same-origin",
        });
    }

    function statusLabel(status) {
        return status === "todo" ? "To do"
            : status === "blocked" ? "Blocked"
            : status.replace(/_/g, " ");
    }

    function badgeTextFor(status, blockedReason) {
        var text = status === "todo" ? "To do" : statusLabel(status).replace(/\b\w/g, function (m) { return m.toUpperCase(); });
        if (status === "blocked" && blockedReason) text = "Blocked";
        return text;
    }

    // ------------------------------------------------------------------ copy
    document.addEventListener("click", function (ev) {
        var btn = ev.target.closest("[data-copy]");
        if (!btn) return;
        var value = btn.getAttribute("data-copy") || "";
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(value).then(function () {
                var old = btn.textContent;
                btn.textContent = "Copied!";
                setTimeout(function () { btn.textContent = old; }, 1200);
            });
        } else {
            var ta = document.createElement("textarea");
            ta.value = value;
            document.body.appendChild(ta);
            ta.select();
            document.execCommand("copy");
            ta.remove();
            btn.textContent = "Copied!";
            setTimeout(function () { btn.textContent = "Copy"; }, 1200);
        }
    });

    // --------------------------------------------------- status (optimistic)
    function setBadge(card, status, blockedReason) {
        var badge = card.querySelector("[data-status-badge]");
        if (!badge) return;
        badge.className = "status-badge status--" + status;
        var icon = badge.querySelector(".status-badge__icon");
        if (icon) {
            var glyphs = { todo: "\u25cb", in_progress: "\u25cf", in_review: "\u21bb", blocked: "\u26d4", completed: "\u2713", cancelled: "\u2715" };
            icon.textContent = glyphs[status] || "?";
        }
        var text = badge.querySelector(".status-badge__text");
        if (text) text.textContent = badgeTextFor(status, blockedReason);
    }

    function refreshCardButtons(card) {
        card.querySelectorAll("[data-status-btn]").forEach(function (btn) {
            btn.disabled = btn.getAttribute("data-status") === (card.getAttribute("data-status-current") || "");
        });
    }

    function statusEndpoint() {
        var main = document.querySelector("[data-workspace-id]");
        return main ? "/workspaces/" + main.getAttribute("data-workspace-id") : "";
    }

    function handleStatus(btn) {
        var taskId = btn.getAttribute("data-task-id");
        var status = btn.getAttribute("data-status");
        var card = btn.closest("[data-task-card]");
        var api = statusEndpoint() + "/tasks/" + taskId + "/status";
        if (!card || !api) return;

        var previous = card.getAttribute("data-status-current") || "todo";
        // optimistic
        card.setAttribute("data-status-current", status);
        setBadge(card, status);
        refreshCardButtons(card);

        postJSON(api, { status: status }).then(function (res) {
            if (!res.ok) {
                card.setAttribute("data-status-current", previous);
                setBadge(card, previous);
                refreshCardButtons(card);
                return res.json().then(function (j) {
                    alert(j.error || "Could not update status.");
                });
            }
            return res.json().then(function (j) {
                if (j.status) {
                    card.setAttribute("data-status-current", j.status);
                    setBadge(card, j.status);
                    refreshCardButtons(card);
                }
                if (j.warning) alert(j.warning);
            });
        }).catch(function () {
            card.setAttribute("data-status-current", previous);
            setBadge(card, previous);
            refreshCardButtons(card);
            alert("Could not update status. Check your connection.");
        });
    }

    document.addEventListener("click", function (ev) {
        var btn = ev.target.closest("[data-status-btn]");
        if (!btn) return;
        ev.preventDefault();
        handleStatus(btn);
    });

    // quick "Block" from the card: prompt for a reason, JSON update
    document.addEventListener("click", function (ev) {
        var btn = ev.target.closest("[data-block-btn]");
        if (!btn) return;
        ev.preventDefault();
        var taskId = btn.getAttribute("data-task-id");
        var reason = window.prompt("Why is this task blocked? (what are you stuck on?)", "");
        if (reason === null) return;
        reason = reason.trim();
        if (!reason) return;
        var card = btn.closest("[data-task-card]");
        var api = statusEndpoint() + "/tasks/" + taskId + "/status";
        postJSON(api, { status: "blocked", blocked_reason: reason }).then(function (res) {
            if (!res.ok) return res.json().then(function (j) { alert(j.error || "Could not block task."); });
            return res.json().then(function () {
                if (card) {
                    card.setAttribute("data-status-current", "blocked");
                    setBadge(card, "blocked", reason);
                    refreshCardButtons(card);
                    // a blocked task deserves a beat of attention
                    card.classList.remove("is-blocked-flash");
                    void card.offsetWidth;
                    card.classList.add("is-blocked-flash");
                }
                alert("Task marked blocked. Consider raising an info request on the Requests tab.");
            });
        }).catch(function () {
            alert("Could not block task.");
        });
    });

    // -------------------------------------------------- task panel loading
    // The Details <a> still navigates when JS is off; otherwise load in place.
    document.addEventListener("click", function (ev) {
        var link = ev.target.closest("[data-expand-task]");
        if (!link) return;
        ev.preventDefault();
        var card = link.closest("[data-task-card]");
        if (!card) return;
        var panel = card.querySelector("[data-task-panel]");
        if (!panel) return;
        var taskId = card.getAttribute("data-task-id");
        var api = statusEndpoint() + "/tasks/" + taskId + "/panel";

        var toggling = card.classList.toggle("is-open");
        if (!toggling) {
            panel.hidden = true;
            return;
        }
        if (panel.dataset.loaded === "1") {
            panel.hidden = false;
            return;
        }
        panel.hidden = false;
        fetch(api, { credentials: "same-origin" }).then(function (res) {
            if (!res.ok) throw new Error("load failed");
            return res.text();
        }).then(function (html) {
            panel.innerHTML = html;
            panel.dataset.loaded = "1";
            if (window.dispatchEvent) {
                window.dispatchEvent(new CustomEvent("collab:panel", { detail: { taskId: taskId } }));
            }
        }).catch(function () {
            panel.querySelector(".task-panel__loader").textContent = "Could not load task details.";
        });
    });
})();