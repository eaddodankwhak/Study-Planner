/*
 * Stash library + saved-cards pages.
 * Delete/regenerate/retry a document, and unsave a card. Small, occasional
 * actions, so native confirm() is enough — the heavy interactions live in the
 * reader.
 */
(function () {
  "use strict";

  var toast = window.StudyPlannerToast;

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
      credentials: "same-origin"
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: "Server error" }; });
    });
  }

  function del(url) {
    return fetch(url, { method: "DELETE", credentials: "same-origin" })
      .then(function (r) { return r.json().catch(function () { return { ok: false }; }); });
  }

  function removeCard(article) {
    article.addEventListener("animationend", function () { article.remove(); }, { once: true });
    article.classList.add("toast--leaving");
  }

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-stash-delete]");
    if (!button) return;
    event.preventDefault();
    var id = button.getAttribute("data-stash-delete");
    var title = button.getAttribute("data-stash-title") || "this document";
    if (!window.confirm("Delete \u201C" + title + "\u201D and all its cards? This can't be undone.")) return;
    button.disabled = true;
    del("/api/stash/documents/" + encodeURIComponent(id)).then(function (res) {
      if (!res.ok) { toast.error(res.error || "Couldn't delete the document."); return; }
      toast.success("Document deleted.");
      window.location.reload();
    });
  });

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-stash-regenerate]");
    if (!button) return;
    event.preventDefault();
    var id = button.getAttribute("data-stash-regenerate");
    if (!window.confirm("Regenerate all cards for this document? This uses some of your daily AI budget.")) return;
    button.disabled = true;
    post("/api/stash/documents/" + encodeURIComponent(id) + "/regenerate", {}).then(function (res) {
      if (!res.ok) { toast.error(res.error || "Couldn't start regeneration."); return; }
      toast.info("Regenerating cards in the background\u2026");
      window.location.href = "/stash/processing";
    });
  });

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-stash-redeploy]");
    if (!button) return;
    event.preventDefault();
    var id = button.getAttribute("data-stash-redeploy");
    button.disabled = true;
    post("/api/stash/documents/" + encodeURIComponent(id) + "/regenerate", {}).then(function (res) {
      if (!res.ok) { toast.error(res.error || "Couldn't retry."); return; }
      window.location.href = "/stash/processing";
    });
  });

  document.addEventListener("click", function (event) {
    var button = event.target.closest("[data-unsave]");
    if (!button) return;
    event.preventDefault();
    var id = button.getAttribute("data-unsave");
    post("/api/stash/cards/" + encodeURIComponent(id) + "/unsave", {}).then(function (res) {
      if (!res.ok) { toast.error(res.error || "Couldn't unsave the card."); return; }
      toast.success("Removed from saved cards.");
      var article = event.target.closest(".stash-item") || event.target.closest("article");
      if (article) removeCard(article); else window.location.reload();
    });
  });
})();