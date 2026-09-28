/*
 * Stash reader.
 *
 * Renders the TOC drawer, lazy-loads cards from /api/stash, wires per-card
 * actions (save / got it / review / note / ask / highlight / source), tracks
 * reading progress, and provides keyboard (J/K/S/G/R), focus mode and font
 * scaling. Progressively enhances the server-rendered card list.
 */
(function () {
  "use strict";

  var root = document.querySelector("[data-stash-reader]");
  if (!root) return;

  var toast = window.StudyPlannerToast;
  var docId = root.getAttribute("data-doc-id");
  var askBase = root.getAttribute("data-ask-href");
  if (!askBase) askBase = "/ai";
  var totalCards = parseInt(root.getAttribute("data-total") || "0", 10);
  var nextCursor = parseInt(root.getAttribute("data-next-cursor") || "0", 10);
  var hasMore = root.getAttribute("data-has-more") === "true";
  var sections = JSON.parse(root.getAttribute("data-sections") || "[]");
  var highlights = JSON.parse(root.getAttribute("data-highlights") || "{}");
  var notes = JSON.parse(root.getAttribute("data-notes") || "{}");
  var progressLast = parseInt(root.getAttribute("data-progress-last") || "0", 10);

  var cardsEl = document.getElementById("stash-cards");
  var sentinel = document.querySelector("[data-sentinel]");
  var progressBar = document.querySelector(".stash-reader__progress-bar");
  var tocList = document.querySelector(".stash-toc__list");
  var currentCard = null;
  var loading = false;

  var LABELS = {
    concept: "Concept", definition: "Definition", process_step: "Process step",
    formula: "Formula", example: "Example", key_takeaway: "Key takeaway",
    warning: "Warning", recap: "Chapter recap"
  };

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function json(res) {
    return res.json().catch(function () { return { ok: false, error: "Server error." }; });
  }

  function post(url, body) {
    return fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
      credentials: "same-origin"
    }).then(json);
  }

  function put(url, body) {
    return fetch(url, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined,
      credentials: "same-origin"
    }).then(json);
  }

  function del(url) {
    return fetch(url, { method: "DELETE", credentials: "same-origin" }).then(json);
  }

  function pageLabel(card) {
    if (card.source_slide) return "slide " + card.source_slide;
    if (card.source_page_start) return "page " + card.source_page_start;
    return "";
  }

  /* ------------------------------------------------------------ card markup */

  function cardHtml(card) {
    var noteDot = notes[card.id] ? " <span aria-hidden=\"true\">\u2022</span>" : "";
    return (
      "<article class=\"stash-item\" data-card-id=\"" + esc(card.id) +
      "\" data-card-type=\"" + esc(card.card_type) +
      "\" data-section-id=\"" + esc(card.section_id || "") +
      "\" data-source-page=\"" + esc(card.source_page_start || "") +
      "\" data-source-slide=\"" + esc(card.source_slide || "") +
      "\" data-saved=\"" + (card.saved || 0) +
      "\" data-status=\"" + esc(card.status || "") + "\">" +
      "<header class=\"stash-item__head\">" +
      "<span class=\"stash-item__badge\">" + esc(LABELS[card.card_type] || card.card_type) + "</span>" +
      (card.is_flagged ? "<span class=\"stash-item__flag\">For review</span>" : "") +
      "<span class=\"stash-item__source\">" + esc(pageLabel(card)) + "</span>" +
      "</header>" +
      "<h2 class=\"stash-item__title\">" + esc(card.title) + "</h2>" +
      "<div class=\"stash-item__body\"><span class=\"stash-item__highlightable\">" + esc(card.body) + "</span></div>" +
      (card.example ? "<div class=\"stash-item__example\"><strong>Example</strong> \u2014 " + esc(card.example) + "</div>" : "") +
      (card.key_term ? "<div class=\"stash-item__term\"><strong>" + esc(card.key_term) + "</strong>" +
        (card.key_term_definition ? " \u2014 " + esc(card.key_term_definition) : "") + "</div>" : "") +
      "<footer class=\"stash-item__actions\">" +
      "<button class=\"stash-item__act stash-item__act--save\" type=\"button\" data-save aria-pressed=\"" + (card.saved ? "true" : "false") + "\">" + (card.saved ? "Saved" : "Save") + "</button>" +
      "<button class=\"stash-item__act stash-item__act--gotit\" type=\"button\" data-gotit aria-pressed=\"" + (card.status === "got_it" ? "true" : "false") + "\">" + (card.status === "got_it" ? "I\u2019ve got it" : "I got it") + "</button>" +
      "<button class=\"stash-item__act\" type=\"button\" data-review>Review again</button>" +
      "<button class=\"stash-item__act\" type=\"button\" data-note>Note" + noteDot + "</button>" +
      "<button class=\"stash-item__act\" type=\"button\" data-ask>Ask about this</button>" +
      "<button class=\"stash-item__act\" type=\"button\" data-highlight>Highlight</button>" +
      "<button class=\"stash-item__act\" type=\"button\" data-source>Source</button>" +
      "</footer></article>"
    );
  }

  /* --------------------------------------------------------------- loading */

  function loadMore() {
    if (!hasMore || loading) return;
    loading = true;
    fetch("/api/stash/documents/" + encodeURIComponent(docId) + "/cards?limit=20&cursor=" + nextCursor, { credentials: "same-origin" })
      .then(json)
      .then(function (res) {
        if (!res.ok) return;
        res.cards.forEach(function (card) {
          cardsEl.insertAdjacentHTML("beforeend", cardHtml(card));
          var el = cardsEl.lastElementChild;
          indexCard(el);
        });
        nextCursor = res.next_cursor || nextCursor;
        hasMore = !!res.has_more;
        markHighlights();
      })
      .finally(function () { loading = false; });
  }

  if (sentinel && "IntersectionObserver" in window) {
    new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) loadMore();
      });
    }, { rootMargin: "600px 0px" }).observe(sentinel);
  }

  /* ------------------------------------------------------------ highlights */

  function applyHighlight(textEl, h) {
    var walker = document.createTreeWalker(textEl, NodeFilter.SHOW_TEXT);
    var node;
    var pos = 0;
    while ((node = walker.nextNode())) {
      var len = node.nodeValue.length;
      var start = h.start_offset, end = h.end_offset;
      if (pos + len > start && pos < end) {
        var s = Math.max(start - pos, 0);
        var e = Math.min(end - pos, len);
        if (s < e) {
          var mark = document.createElement("mark");
          mark.className = "stash-mark stash-mark--" + h.color;
          mark.dataset.hid = h.id;
          mark.appendChild(document.createTextNode(node.nodeValue.slice(s, e)));
          var rest = document.createTextNode(node.nodeValue.slice(e));
          node.nodeValue = node.nodeValue.slice(0, s);
          node.parentNode.insertBefore(mark, node.nextSibling);
          if (rest.nodeValue) node.parentNode.insertBefore(rest, mark.nextSibling);
        }
        break;
      }
      pos += len;
    }
  }

  function markHighlights() {
    cardsEl.querySelectorAll(".stash-item").forEach(function (article) {
      var id = article.getAttribute("data-card-id");
      var list = highlights[id];
      if (!list) return;
      list.forEach(function (h) {
        if (h.field !== "body") return;
        var holder = article.querySelector(".stash-item__highlightable");
        if (holder && !holder.querySelector("mark[data-hid=\"" + h.id + "\"]")) applyHighlight(holder, h);
      });
    });
  }

  markHighlights();

  /* ------------------------------------------------------ selection popover */

  var pop = document.createElement("div");
  pop.className = "stash-pop";
  pop.hidden = true;
  ["yellow", "green", "blue", "pink"].forEach(function (color) {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "stash-pop__" + color;
    b.setAttribute("aria-label", "Highlight in " + color);
    b.dataset.color = color;
    pop.appendChild(b);
  });
  document.body.appendChild(pop);

  function selectedRange(holder) {
    var sel = window.getSelection();
    if (!sel || sel.isCollapsed) return null;
    var range = sel.getRangeAt(0);
    if (!holder.contains(range.commonAncestorContainer)) return null;
    return range;
  }

  function offsetsIn(holder, range) {
    var pre = range.cloneRange();
    pre.selectNodeContents(holder);
    pre.setEnd(range.startContainer, range.startOffset);
    var start = pre.toString().length;
    pre.setEnd(range.endContainer, range.endOffset);
    return { start: start, end: pre.toString().length };
  }

  document.addEventListener("mouseup", function (event) {
    var holder = event.target.closest ? event.target.closest(".stash-item__highlightable") : null;
    window.setTimeout(function () {
      if (!holder) { pop.hidden = true; return; }
      var range = selectedRange(holder);
      if (!range) { pop.hidden = true; return; }
      var article = holder.closest(".stash-item");
      var rect = range.getBoundingClientRect();
      pop.hidden = false;
      pop.style.left = Math.max(8, rect.left + rect.width / 2 - 55) + "px";
      pop.style.top = (rect.top - 44 > 0 ? rect.top - 44 : rect.bottom + 8) + "px";
      pop.dataset.cardId = article.getAttribute("data-card-id");
      var offs = offsetsIn(holder, range);
      pop.dataset.start = offs.start;
      pop.dataset.end = offs.end;
    }, 0);
  });

  document.addEventListener("mousedown", function (event) {
    if (!pop.hidden && !pop.contains(event.target)) pop.hidden = true;
  });

  pop.addEventListener("click", function (event) {
    var button = event.target.closest("[data-color]");
    if (!button || !pop.dataset.cardId) return;
    var cardId = pop.dataset.cardId;
    var start = parseInt(pop.dataset.start, 10), end = parseInt(pop.dataset.end, 10);
    var color = button.dataset.color;
    post("/api/stash/cards/" + encodeURIComponent(cardId) + "/highlight", {
      field: "body", start_offset: start, end_offset: end, color: color
    }).then(function (res) {
      if (!res.ok) { toast.error(res.error || "Couldn't save the highlight."); return; }
      var article = cardsEl.querySelector("[data-card-id=\"" + cardId + "\"]");
      var holder = article && article.querySelector(".stash-item__highlightable");
      if (holder) {
        var range = selectedRange(holder);
        if (range) {
          try {
            var mark = document.createElement("mark");
            mark.className = "stash-mark stash-mark--" + color;
            mark.dataset.hid = res.highlight_id;
            range.surroundContents(mark);
          } catch (err) { /* partial wrap; the saved highlight still rerenders on reload */ }
        }
      }
      if (!highlights[cardId]) highlights[cardId] = [];
      highlights[cardId].push({ id: res.highlight_id, field: "body", color: color,
        start_offset: start, end_offset: end });
      toast.success("Highlighted.");
    });
    pop.hidden = true;
    window.getSelection().removeAllRanges();
  });

  document.addEventListener("click", function (event) {
    var mark = event.target.closest ? event.target.closest("mark.stash-mark") : null;
    if (!mark || !mark.dataset.hid) return;
    if (!window.confirm("Remove this highlight?")) return;
    del("/api/stash/highlights/" + encodeURIComponent(mark.dataset.hid)).then(function (res) {
      if (!res.ok) return;
      mark.remove();
    });
  });

  /* ---------------------------------------------------------------- actions */

  function cardFor(el) {
    return el.closest(".stash-item");
  }

  cardsEl.addEventListener("click", function (event) {
    var target = event.target;
    if (!target.closest) return;

    var save = target.closest("[data-save]");
    if (save) return toggleSave(save);

    var gotIt = target.closest("[data-gotit]");
    if (gotIt) return toggleGotIt(gotIt);

    var review = target.closest("[data-review]");
    if (review) return setReview(review);

    var note = target.closest("[data-note]");
    if (note) return openNote(note);

    var ask = target.closest("[data-ask]");
    if (ask) return openAsk(ask);

    var src = target.closest("[data-source]");
    if (src) return openSource(src);
  });

  function setSaved(card, saved) {
    card.dataset.saved = saved ? "1" : "0";
    var btn = card.querySelector("[data-save]");
    btn.setAttribute("aria-pressed", saved ? "true" : "false");
    btn.textContent = saved ? "Saved" : "Save";
    btn.classList.toggle("stash-item__act--saved", !!saved);
  }

  function setStatus(card, status) {
    card.dataset.status = status || "";
    var btn = card.querySelector("[data-gotit]");
    btn.setAttribute("aria-pressed", status === "got_it" ? "true" : "false");
    btn.textContent = status === "got_it" ? "I\u2019ve got it" : "I got it";
  }

  function toggleSave(btn) {
    var card = cardFor(btn);
    var saved = card.getAttribute("data-saved") === "1";
    var action = saved ? "unsave" : "save";
    post("/api/stash/cards/" + encodeURIComponent(card.getAttribute("data-card-id")) + "/" + action, {})
      .then(function (res) {
        if (!res.ok) { toast.error(res.error || "Couldn't update."); return; }
        setSaved(card, !saved);
        if (action === "save") toast.success("Saved.");
      });
  }

  function toggleGotIt(btn) {
    var card = cardFor(btn);
    post("/api/stash/cards/" + encodeURIComponent(card.getAttribute("data-card-id")) + "/gotit", {})
      .then(function (res) {
        if (!res.ok) { toast.error(res.error || "Couldn't update."); return; }
        setStatus(card, card.getAttribute("data-status") === "got_it" ? "" : "got_it");
      });
  }

  function setReview(btn) {
    var card = cardFor(btn);
    post("/api/stash/cards/" + encodeURIComponent(card.getAttribute("data-card-id")) + "/review", {})
      .then(function (res) {
        if (!res.ok) { toast.error(res.error || "Couldn't update."); return; }
        setStatus(card, "");
        toast.info("Put in review \u2014 come back to it later.");
      });
  }

  function openAsk(btn) {
    var card = cardFor(btn);
    var body = card.querySelector(".stash-item__body").textContent.trim();
    var payload = JSON.stringify({
      text: card.querySelector(".stash-item__title").textContent + ": " + body,
      context: "ask about this study card"
    });
    var b64 = window.btoa(unescape(encodeURIComponent(payload)))
      .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    window.location.href = askBase + "#ask=" + b64;
  }

  /* ------------------------------------------------------------------ note */

  var noteModal = document.querySelector("[data-note-modal]");
  var noteInput = document.querySelector("[data-note-input]");
  var noteCardLabel = document.querySelector("[data-note-card]");
  var noteDelete = document.querySelector("[data-note-delete]");
  var activeNoteCard = null;

  function openNote(btn) {
    var card = cardFor(btn);
    activeNoteCard = card;
    noteInput.value = notes[card.getAttribute("data-card-id")] || "";
    noteCardLabel.textContent = card.querySelector(".stash-item__title").textContent;
    noteDelete.hidden = !notes[card.getAttribute("data-card-id")];
    if (!noteModal.showModal) { toast.info("Your browser doesn't support dialogs yet."); return; }
    noteModal.showModal();
    window.setTimeout(function () { noteInput.focus(); }, 50);
  }

  document.querySelector("[data-note-save]").addEventListener("click", function () {
    var card = activeNoteCard;
    if (!card) return;
    var id = card.getAttribute("data-card-id");
    var content = noteInput.value.trim();
    if (!content) { deleteNote(id); return; }
    put("/api/stash/cards/" + encodeURIComponent(id) + "/note", { content: content })
      .then(function (res) {
        if (!res.ok) { toast.error(res.error || "Couldn't save the note."); return; }
        notes[id] = content;
        markNote(card, true);
        noteModal.close();
        toast.success("Note saved.");
      });
  });

  function deleteNote(id) {
    del("/api/stash/cards/" + encodeURIComponent(id) + "/note").then(function (res) {
      if (!res.ok) return;
      delete notes[id];
      markNote(activeNoteCard, false);
      noteModal.close();
      toast.success("Note removed.");
    });
  }

  noteDelete.addEventListener("click", function () { deleteNote(activeNoteCard.getAttribute("data-card-id")); });

  function markNote(card, present) {
    var btn = card.querySelector("[data-note]");
    var has = btn.querySelector("span[aria-hidden=\"true\"]");
    if (present && !has) btn.insertAdjacentHTML("beforeend", " <span aria-hidden=\"true\">\u2022</span>");
    if (!present && has) has.remove();
  }

  /* ---------------------------------------------------------- source modal */

  var sourceModal = document.querySelector("[data-source-modal]");
  var sourceBody = document.querySelector("[data-source-body]");
  var sourceRef = document.querySelector("[data-source-ref]");

  function openSource(btn) {
    var card = cardFor(btn);
    sourceBody.textContent = card.querySelector(".stash-item__body").textContent;
    var ref = card.getAttribute("data-source-slide") && "slide " + card.getAttribute("data-source-slide")
      || card.getAttribute("data-source-page") && "page " + card.getAttribute("data-source-page");
    sourceRef.textContent = ref ? "Originally " + ref + " of the uploaded file." : "No page reference recorded.";
    if (sourceModal.showModal) sourceModal.showModal();
  }

  if (document.querySelector("[data-source-close]")) {
    document.querySelector("[data-source-close]").addEventListener("click", function () { sourceModal.close(); });
  }

  /* --------------------------------------------------------------- TOC */

  function buildToc() {
    if (!tocList) return;
    sections.forEach(function (section) {
      var li = document.createElement("li");
      var btn = document.createElement("button");
      btn.type = "button";
      btn.innerHTML = esc(section.title || ("\u00A7 " + section.position)) +
        (section.card_count ? " <small>" + section.card_count + " cards</small>" : "");
      btn.addEventListener("click", function () { jumpToSection(section); });
      li.appendChild(btn);
      tocList.appendChild(li);
    });
  }

  function jumpToSection(section) {
    var cards = cardsEl.querySelectorAll(".stash-item");
    var target = null;
    for (var i = 0; i < cards.length; i++) {
      if (cards[i].getAttribute("data-section-id") === section.id) { target = cards[i]; break; }
    }
    if (!target) target = cards[section.position - 1];
    if (target) {
      target.scrollIntoView({ behavior: "smooth", block: "start" });
      focusCard(target);
    }
    if (tocDrawer) { tocDrawer.hidden = true; tocBtn.setAttribute("aria-expanded", "false"); }
  }

  /* ------------------------------------------------- focus mode + keys */

  var focusBtn = document.querySelector("[data-stash-focus]");
  var tocBtn = document.querySelector("[data-stash-toc]");
  var tocDrawer = document.querySelector("[data-toc]");

  function focusCard(card) {
    if (!card) return;
    if (currentCard) currentCard.classList.remove("stash-item--focused");
    currentCard = card;
    if (root.classList.contains("stash-reader--focus")) card.classList.add("stash-item--focused");
  }

  function visibleCards() {
    return Array.prototype.slice.call(cardsEl.querySelectorAll(".stash-item"));
  }

  function nearestCard() {
    var cards = visibleCards();
    var best = cards[0];
    var viewportMid = window.scrollY + window.innerHeight / 2;
    cards.forEach(function (card) {
      var top = card.getBoundingClientRect().top + window.scrollY;
      var curOff = Math.abs(best.getBoundingClientRect().top + window.scrollY - viewportMid);
      if (Math.abs(top - viewportMid) < curOff) best = card;
    });
    return best;
  }

  function moveFocus(delta) {
    var cards = visibleCards();
    var idx = cards.indexOf(currentCard);
    if (idx < 0) idx = cards.indexOf(nearestCard());
    var next = cards[Math.max(0, Math.min(cards.length - 1, idx + delta))];
    if (next) {
      next.scrollIntoView({ behavior: "smooth", block: "start" });
      focusCard(next);
    }
  }

  function toggleFocus() {
    var on = root.classList.toggle("stash-reader--focus");
    focusBtn.setAttribute("aria-pressed", on ? "true" : "false");
    if (on) focusCard(nearestCard());
    else if (currentCard) currentCard.classList.remove("stash-item--focused");
  }

  if (focusBtn) focusBtn.addEventListener("click", toggleFocus);

  if (tocBtn && tocDrawer) {
    tocBtn.addEventListener("click", function () {
      var show = tocDrawer.hidden;
      tocDrawer.hidden = !show;
      tocBtn.setAttribute("aria-expanded", show ? "true" : "false");
    });
  }

  document.addEventListener("keydown", function (event) {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (document.activeElement && /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) return;
    if (document.querySelector("dialog[open]")) return;

    switch (event.key) {
      case "j": case "J": event.preventDefault(); moveFocus(1); break;
      case "k": case "K": event.preventDefault(); moveFocus(-1); break;
      case "s": case "S": {
        var c = currentCard || nearestCard();
        var btn = c && c.querySelector("[data-save]");
        if (btn) toggleSave(btn);
        break;
      }
      case "g": case "G": {
        var c2 = currentCard || nearestCard();
        var btn2 = c2 && c2.querySelector("[data-gotit]");
        if (btn2) toggleGotIt(btn2);
        break;
      }
      case "r": case "R": {
        var c3 = currentCard || nearestCard();
        var btn3 = c3 && c3.querySelector("[data-review]");
        if (btn3) setReview(btn3);
        break;
      }
      case "f": case "F": toggleFocus(); break;
      case "Escape":
        if (tocDrawer && !tocDrawer.hidden) { tocDrawer.hidden = true; tocBtn.setAttribute("aria-expanded", "false"); }
        break;
    }
  });

  /* ------------------------------------------------------- font scaling */

  var fontKey = "stash-font-" + docId;
  var fontLevels = ["sm", "default", "lg", "xl"];
  var fontIdx = fontLevels.indexOf(localStorage.getItem(fontKey) || "default");

  function applyFont() {
    if (fontIdx < 0) fontIdx = 0;
    if (fontIdx >= fontLevels.length) fontIdx = fontLevels.length - 1;
    var level = fontLevels[fontIdx];
    root.setAttribute("data-reader-font", level);
    localStorage.setItem(fontKey, level);
  }

  if (root.querySelector("[data-stash-font]")) {
    root.querySelectorAll("[data-stash-font]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        fontIdx += btn.getAttribute("data-stash-font") === "up" ? 1 : -1;
        applyFont();
      });
    });
  }
  applyFont();

  /* ------------------------------------------------------------- progress */

  var lastReported = progressLast || 0;

  function reportPosition(position) {
    lastReported = Math.max(lastReported, position);
    if (progressBar) progressBar.style.width = Math.min(100, Math.round(lastReported / (totalCards || 1) * 100)) + "%";
    window.setTimeout(function () {
      post("/api/stash/documents/" + encodeURIComponent(docId) + "/progress", { last_position: lastReported || null });
    }, 1200);
  }

  var progressIO = null;
  if ("IntersectionObserver" in window) {
    progressIO = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        var card = entry.target;
        if (!card.getAttribute("data-seen")) {
          post("/api/stash/cards/" + encodeURIComponent(card.getAttribute("data-card-id")) + "/seen", {});
          card.setAttribute("data-seen", "1");
        }
        reportPosition(parseInt(card.getAttribute("data-position") || "0", 10) || 0);
      });
    }, { rootMargin: "0px 0px -30% 0px" });
  }

  function indexCard(card) {
    var pos = cardsEl.querySelectorAll(".stash-item").length;
    card.setAttribute("data-position", pos);
    if (progressIO) progressIO.observe(card);
  }

  cardsEl.querySelectorAll(".stash-item").forEach(indexCard);

  if (progressLast > 1) {
    if ("ResizeObserver" in window) {
      new ResizeObserver(function () { return; }).observe(cardsEl);
    }
    var resumeTarget = cardsEl.querySelectorAll(".stash-item")[progressLast - 1];
    if (resumeTarget) {
      window.setTimeout(function () { resumeTarget.scrollIntoView({ block: "start" }); }, 0);
    }
  }

  buildToc();
  applyFont();

  /* --------------------------------------------------- generating poll */

  var docStatus = root.getAttribute("data-doc-status") || "ready";
  if (docStatus === "queued" || docStatus === "processing") {
    var pollTries = 0;
    function pollStatus() {
      pollTries += 1;
      if (pollTries > 400) return;
      fetch("/api/stash/documents/" + encodeURIComponent(docId), { credentials: "same-origin" })
        .then(json)
        .then(function (res) {
          if (res.ok && res.document &&
              (res.document.status === "ready" || res.document.status === "error")) {
            window.location.reload();
            return;
          }
          window.setTimeout(pollStatus, 3000);
        })
        .catch(function () { window.setTimeout(pollStatus, 3000); });
    }
    window.setTimeout(pollStatus, 2500);
  }

  /* ------------------------------------------------------ quiz & regen */

  var quizModal = document.querySelector("[data-quiz-modal]");
  var quizBody = document.querySelector("[data-quiz-body]");
  document.querySelector("[data-stash-quiz]").addEventListener("click", function () {
    quizBody.innerHTML = "<p>Loading quiz\u2026</p>";
    if (quizModal.showModal) quizModal.showModal();
    fetch("/api/stash/documents/" + encodeURIComponent(docId) + "/quiz", { credentials: "same-origin" })
      .then(json)
      .then(function (res) {
        if (!res.ok) {
          quizBody.innerHTML = "<p class=\"text-muted\">" + esc(res.error || "Couldn't build a quiz yet.") + "</p>";
          return;
        }
        renderQuiz(res.quiz.questions);
      });
  });

  function renderQuiz(questions) {
    quizBody.innerHTML = "";
    questions.forEach(function (q, i) {
      var box = document.createElement("div");
      var h = document.createElement("h3");
      h.textContent = (i + 1) + ". " + q.question;
      box.appendChild(h);
      var list = document.createElement("div");
      list.className = "stash-quiz__answer";
      q.options.forEach(function (opt, oi) {
        var label = document.createElement("label");
        var input = document.createElement("input");
        input.type = "radio";
        input.name = "stashq" + i;
        input.value = String(oi);
        input.addEventListener("change", function () {
          var correctText = q.options[q.answer];
          box.querySelectorAll("label").forEach(function (l) { l.style.borderColor = ""; });
          label.style.borderColor = correctText === opt ? "var(--color-success)" : "var(--color-error)";
          if (q.answer !== oi && !label.querySelector("p")) {
            var reveal = document.createElement("p");
            reveal.className = "text-muted";
            reveal.style.margin = "0";
            reveal.textContent = "Correct answer: " + correctText;
            label.insertAdjacentElement("afterend", reveal);
          }
        });
        label.appendChild(input);
        label.appendChild(document.createTextNode(opt));
        list.appendChild(label);
      });
      if (q.explanation) {
        var exp = document.createElement("p");
        exp.className = "text-muted";
        exp.textContent = q.explanation;
        list.appendChild(exp);
      }
      box.appendChild(list);
      quizBody.appendChild(box);
    });
  }

  if (document.querySelector("[data-quiz-close]")) {
    document.querySelector("[data-quiz-close]").addEventListener("click", function () { quizModal.close(); });
  }

  var regenModal = document.querySelector("[data-regen-modal]");
  var regenChapters = document.querySelector("[data-regen-chapters]");
  document.querySelector("[data-stash-regenerate]").addEventListener("click", function () {
    regenChapters.innerHTML = "";
    sections.forEach(function (section, i) {
      var label = document.createElement("label");
      var box = document.createElement("input");
      box.type = "checkbox";
      box.value = String(i);
      label.appendChild(box);
      label.appendChild(document.createTextNode(section.title || ("\u00A7 " + section.position)));
      regenChapters.appendChild(label);
    });
    if (regenModal.showModal) regenModal.showModal();
  });

  if (document.querySelector("[data-regen-all]")) {
    document.querySelector("[data-regen-all]").addEventListener("click", function () {
      var chosen = Array.prototype.map.call(
        regenChapters.querySelectorAll("input:checked"),
        function (el) { return parseInt(el.value, 10); }
      );
      var body = chosen.length ? { chapters: chosen } : {};
      var pick = document.getElementById("regen-ai-pick");
      if (pick && pick.value) {
        var parts = String(pick.value).split(":");
        if (parts.length === 2) {
          body.provider = parts[0];
          body.model = parts[1];
        }
      }
      post("/api/stash/documents/" + encodeURIComponent(docId) + "/regenerate", body)
        .then(function (res) {
          if (!res.ok) { toast.error(res.error || "Couldn't start regeneration."); return; }
          regenModal.close();
          toast.info("Regenerating in the background \u2014 you'll be redirected.");
          window.location.href = "/stash/processing";
        });
    });
  }

  if (document.querySelector("[data-regen-close]")) {
    document.querySelector("[data-regen-close]").addEventListener("click", function () { regenModal.close(); });
  }

  document.querySelectorAll("dialog.stash-modal").forEach(function (dialog) {
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) dialog.close();
    });
  });
})();