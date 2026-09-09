/* Task-detail panel behaviors (loaded via fetch into the tasks tab):
   - AJAX comment posting (chat + per-task threads)
   - assignment ("assign to me", owner reassign select)
   - label toggles
   - the "blocked" form inside the panel (status + optional info request)
*/
(function () {
    "use strict";

    var USER_ID = (document.querySelector('meta[name="user-id"]') || {}).content || "";
    var WORKSPACE_ID = (function () {
        var main = document.querySelector("[data-workspace-id]");
        return main ? main.getAttribute("data-workspace-id") : "";
    })();

    function postJSON(url, payload) {
        return fetch(url, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
            credentials: "same-origin",
        });
    }

    function postForm(url, form) {
        return fetch(url, { method: "POST", body: new FormData(form), credentials: "same-origin" });
    }

    function setBadge(scope, status, text) {
        var badge = scope.querySelector("[data-status-badge]");
        if (!badge) return;
        badge.className = "status-badge status--" + status;
        var el = badge.querySelector(".status-badge__text");
        if (el) el.textContent = text || status.replace(/_/g, " ");
    }

    // global.js/hci.js disables the submit button on every form submit; AJAX
    // forms cancel navigation instead, so re-arm them after the request lands.
    function rearm(form) {
        if (!form) return;
        form.dataset.submitting = "false";
        form.setAttribute("aria-busy", "false");
        var submit = form.querySelector("button[type=submit], input[type=submit]");
        if (submit) {
            submit.disabled = false;
            if (submit.dataset.originalLabel) {
                if (submit.tagName === "INPUT") {
                    submit.value = submit.dataset.originalLabel;
                } else {
                    submit.textContent = submit.dataset.originalLabel;
                }
                delete submit.dataset.originalLabel;
            }
        }
    }

    // ------------------------------------------------------------------ forms
    document.addEventListener("submit", function (ev) {
        var form = ev.target.closest("[data-comment-form], [data-chat-form]");
        if (!form) return;
        var ajax = (form.querySelector('[name="ajax"]') || {}).value === "1";
        if (!ajax) return;
        ev.preventDefault();
        var list = null;
        var thread = form.closest(".chat") && form.closest(".chat").querySelector("[data-chat-thread]");
        var comments = form.closest(".task-detail__comments") && form.closest(".task-detail__comments").querySelector("[data-comments-list]");
        list = thread || comments;
        if (!list) return;

        postForm(form.action, form).then(function (res) {
            if (!res.ok) return res.json().then(function (j) { alert(j.error || "Could not post."); });
            return res.json().then(function (j) {
                if (list.querySelector(".text-muted")) list.innerHTML = "";
                list.insertAdjacentHTML("beforeend", j.html);
                form.reset();
                rearm(form);
            });
        }).catch(function () {
            rearm(form);
            alert("Could not post. Check your connection.");
        });
    });

    // ------------------------------------------------------- block form (panel)
    document.addEventListener("submit", function (ev) {
        var form = ev.target.closest("[data-block-submit]");
        if (!form) return;
        ev.preventDefault();
        var panel = form.closest(".task-detail");
        var taskId = (function () {
            const card = form.closest("[data-task-card]");
            return card ? card.getAttribute("data-task-id") : null;
        })();
        if (!taskId) return;
        var fields = new FormData(form);
        var payload = {
            status: "blocked",
            blocked_reason: fields.get("blocked_reason"),
        };
        if (fields.get("recipient_id")) payload.recipient_id = fields.get("recipient_id");
        if (fields.get("what_needed")) payload.what_needed = fields.get("what_needed");
        if (fields.get("needed_by")) payload.needed_by = fields.get("needed_by");

        postJSON(WORKSPACE_ID ? "/workspaces/" + WORKSPACE_ID + "/tasks/" + taskId + "/status" : "", payload)
            .then(function (res) {
                if (!res.ok) return res.json().then(function (j) { alert(j.error || "Could not block task."); });
                return res.json().then(function () {
                    setBadge(panel, "blocked", "Blocked");
                    var card = form.closest("[data-task-card]");
                    if (card) {
                        card.setAttribute("data-status-current", "blocked");
                        setBadge(card, "blocked");
                    }
                    form.closest("[data-block-form]").open = false;
                    form.reset();
                    rearm(form);
                });
            })
            .catch(function () {
                rearm(form);
                alert("Could not block task.");
            });
    });

    // ------------------------------------------------------------ assignment
    document.addEventListener("click", function (ev) {
        var btn = ev.target.closest("[data-assign-me]");
        if (!btn || !USER_ID) return;
        ev.preventDefault();
        var panel = btn.closest(".task-detail");
        var card = btn.closest("[data-task-card]");
        var taskId = card ? card.getAttribute("data-task-id") : null;
        if (!taskId) return;
        postJSON("/workspaces/" + WORKSPACE_ID + "/tasks/" + taskId + "/assign", { assignee_id: USER_ID })
            .then(function (res) {
                if (!res.ok) return res.json().then(function (j) { alert(j.error || "Could not assign."); });
                return res.json().then(function () {
                    var label = panel.querySelector("[data-assignee-label]");
                    if (label) label.textContent = "You";
                    var me = btn;
                    if (me) me.remove();
                });
            })
            .catch(function () { alert("Could not assign."); });
    });

    document.addEventListener("change", function (ev) {
        var sel = ev.target.closest("[data-assign-select]");
        if (!sel) return;
        var assignee = sel.value;
        var panel = sel.closest(".task-detail");
        var card = sel.closest("[data-task-card]");
        var taskId = card ? card.getAttribute("data-task-id") : null;
        if (!taskId) return;
        postJSON("/workspaces/" + WORKSPACE_ID + "/tasks/" + taskId + "/assign", { assignee_id: assignee || null })
            .then(function (res) {
                if (!res.ok) return res.json().then(function (j) { alert(j.error || "Could not assign."); });
                return res.json().then(function () {
                    var label = panel.querySelector("[data-assignee-label]");
                    if (label) {
                        label.textContent = assignee ? sel.options[sel.selectedIndex].text : "Unassigned";
                    }
                });
            })
            .catch(function () { alert("Could not assign."); });
    });

    // ----------------------------------------------------------------- labels
    document.addEventListener("click", function (ev) {
        var btn = ev.target.closest("[data-label-toggle]");
        if (!btn || btn.disabled) return;
        ev.preventDefault();
        var labelId = btn.getAttribute("data-label-id");
        var card = btn.closest("[data-task-card]");
        var taskId = card ? card.getAttribute("data-task-id") : null;
        if (!taskId) return;
        postJSON("/workspaces/" + WORKSPACE_ID + "/tasks/" + taskId + "/labels/toggle", { label_id: Number(labelId) })
            .then(function (res) {
                if (!res.ok) return res.json().then(function (j) { alert(j.error || "Could not update label."); });
                return res.json().then(function () {
                    btn.classList.toggle("is-on", j.attached);
                });
            })
            .catch(function () { alert("Could not update label."); });
    });
})();