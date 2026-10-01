/*
 * AI assistant front-end controller (shared).
 *
 * Sole client for the /api/ai/* JSON endpoints (SSE for streaming). Runs once
 * in BOTH contexts: inside the floating drawer on every page, and full-width on
 * /ai-hub, hydrating the same ai/_panel.html partial. No provider-specific
 * logic lives here. One component, two layouts.
 */
(function () {
  "use strict";

  var API = "/api/ai";
  var GATEWAY_API = "/api/ai-gateway";
  var panel = document.querySelector("[data-ai-panel]");
  if (!panel) return;

  var state = {
    meta: { models: [], modes: [], preferences: { model: "claude", level: "intermediate" }, usage: { used: 0, limit: 0 }, mockMode: true },
    gateway: null,
    conversations: [],
    current: null,
    model: "auto",
    mode: "ask",
    streaming: false,
    material: null,
  };

  var els = {
    panel: panel,
    sidebar: document.getElementById("ai-sidebar"),
    convList: document.getElementById("ai-conv-list"),
    modelPicker: document.getElementById("ai-model-picker"),
    modelNote: document.getElementById("ai-model-note"),
    mockHint: document.getElementById("ai-mock-hint"),
    modes: document.getElementById("ai-modes"),
    chat: document.getElementById("ai-chat"),
    emptyState: document.getElementById("ai-empty-state"),
    status: document.getElementById("ai-status"),
    input: document.getElementById("ai-input"),
    send: document.getElementById("ai-send"),
    title: document.getElementById("ai-conv-title"),
    usage: document.getElementById("ai-usage"),
    attachBtn: document.getElementById("ai-attach-btn"),
    fileInput: document.getElementById("ai-file-input"),
    attachments: document.getElementById("ai-attachments"),
    clearConv: document.getElementById("ai-clear-conv"),
    newConv: document.getElementById("ai-new-conv"),
  };

  if (!els.chat || !els.input) return;

  // ------------------------------------------------------------------ utils

  function escapeHtml(s) {
    var div = document.createElement("div");
    div.textContent = s == null ? "" : String(s);
    return div.innerHTML;
  }

  function fromUrlSafeBase64(s) {
    var padded = s.replace(/-/g, "+").replace(/_/g, "/");
    while (padded.length % 4) padded += "=";
    return decodeURIComponent(escape(window.atob(padded)));
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function api(url, opts) {
    opts = opts || {};
    var init = { method: opts.method || "GET", headers: { "Content-Type": "application/json" }, credentials: "same-origin" };
    if (opts.body) init.body = JSON.stringify(opts.body);
    return fetch(API + url, init).then(function (res) {
      if (!res.ok) {
        return res.json().then(function (d) {
          var err = new Error((d && d.error) || "Request failed");
          err.status = res.status;
          throw err;
        });
      }
      return res.json();
    });
  }

  function setStatus(msg, isError) {
    els.status.textContent = msg || "";
    els.status.className = "ai-status" + (isError ? " ai-status--error" : "");
  }

  function renderMarkdown(text) {
    var t = escapeHtml(text || "");
    t = t.replace(/```(\w*)\n([\s\S]*?)```/g, function (m, lang, code) {
      return '<pre><code class="lang-' + escapeHtml(lang) + '">' + code + "</code></pre>";
    });
    t = t.replace(/`([^`]+)`/g, function (m, c) { return "<code>" + c + "</code>"; });
    t = t.replace(/^### (.*)$/gm, "<h3>$1</h3>");
    t = t.replace(/^## (.*)$/gm, "<h2>$1</h2>");
    t = t.replace(/^# (.*)$/gm, "<h1>$1</h1>");
    t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    t = t.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
    t = t.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    t = t.replace(/^\s*[-*]\s+/gm, "<li>").replace(/(<li>[^<]*\n?)+/g, function (m) {
      return "<ul>" + m.replace(/\n/g, "") + "</ul>";
    });
    t = t.replace(/^\s*\d+\.\s+/gm, "<li>").replace(/(<li>[^<]*\n?)+/g, function (m) {
      return "<ol>" + m.replace(/\n/g, "") + "</ol>";
    });
    t = t.replace(/\n{2,}/g, "</p><p>");
    t = t.replace(/\n/g, "<br>");
    if (t.indexOf("<p>") === -1 && t.indexOf("<li>") === -1) t = "<p>" + t + "</p>";
    t = t.replace(/<p>(&gt;|&gt;&gt;) ([^<]*)<\/p>/g, "<blockquote>$2</blockquote>");
    return t;
  }

  // ------------------------------------------------------------- models

  function findModel(id) {
    return state.meta.models.find(function (m) { return m.id === id; }) || null;
  }

  /*
   * Gateway model picker.
   *
   * The gateway is the only source of truth for which models exist, so this
   * replaces the old hard-coded icon toolbar (which listed vendor ids the
   * gateway never used). "Auto" is the default and is deliberately first: a
   * student who never touches this control still gets a working assistant,
   * because the gateway picks a healthy model for them.
   */
  function renderModelPicker(payload) {
    state.gateway = payload;
    var select = els.modelPicker;
    if (!select) return;
    select.innerHTML = "";

    var auto = el("option", null, "Auto (recommended)");
    auto.value = "";
    select.appendChild(auto);

    var available = payload.models.filter(function (m) { return m.available; });
    var groups = {};
    available.forEach(function (m) {
      (groups[m.tierLabel] = groups[m.tierLabel] || []).push(m);
    });
    Object.keys(groups).forEach(function (label) {
      var group = document.createElement("optgroup");
      group.label = label;
      groups[label].forEach(function (m) {
        // "Your key" marks models billed to the student's own provider key
        // instead of the quota included with the account, so an unexpected
        // personal charge is visible before they send a message.
        var opt = el("option", null, m.name + " · " + m.provider
          + (m.paidBy === "personal" ? " (your key)" : ""));
        opt.value = m.id;
        group.appendChild(opt);
      });

      select.appendChild(group);
    });

    // Unavailable models stay visible but disabled, with the reason as the
    // tooltip. Hiding them would make the picker silently reshuffle itself
    // when a provider has an outage, which reads as a bug to a student.
    var unavailable = payload.models.filter(function (m) { return !m.available; });
    if (unavailable.length) {
      var ug = document.createElement("optgroup");
      ug.label = "Unavailable right now";
      unavailable.forEach(function (m) {
        var opt = el("option", null, m.name);
        opt.value = m.id;
        opt.disabled = true;
        opt.title = m.unavailableReason || "Not available right now";
        ug.appendChild(opt);
      });
      select.appendChild(ug);
    }

    select.value = payload.autoMode ? "" : (payload.preferredModelId || "");

    var pinned = select.value ? findGatewayModel(select.value) : null;
    if (els.modelNote) {
      var note = "";
      if (pinned) {
        note = pinned.tierLabel + " · " + pinned.bestFor.replace(/,/g, ", ");
      } else if (available.length === 0) {
        note = "No AI models are available right now. Please try again shortly.";
      } else {
        note = "We'll pick a working model for you.";
      }
      els.modelNote.textContent = note;
      els.modelNote.hidden = !note;
    }
  }

  function findGatewayModel(id) {
    if (!state.gateway) return null;
    var found = state.gateway.models.filter(function (m) { return m.id === id; });
    return found.length ? found[0] : null;
  }

  /*
   * What to print under an answer. Students should never see a vendor model
   * id: in auto mode the gateway chose, so the honest label is the name of
   * whatever it picked, and failing that we say "Auto".
   */
  function friendlyModelName() {
    if (state.model && state.model !== "auto") {
      var pinned = findGatewayModel(state.model);
      if (pinned) return pinned.name;
    }
    if (state.gateway && state.gateway.autoMode) {
      var usable = state.gateway.models.filter(function (m) { return m.available; });
      if (usable.length) return usable[0].name;
    }
    return "Auto";
  }

  function loadGatewayModels() {
    return fetch(GATEWAY_API + "/models", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) {
        if (!data) return;
        renderModelPicker(data);
        renderUsage(data.usage);
      })
      .catch(function () { /* the panel still works without the picker */ });
  }

  function setModel(id) {
    // An empty id means auto mode: the gateway chooses. Persisted server-side
    // so the choice survives a reload, a new conversation and a new device.
    state.model = id || "auto";
    renderModelPicker(state.gateway);
    fetch(GATEWAY_API + "/models", {
      method: "PUT",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(id ? { modelId: id, autoMode: false } : { autoMode: true }),
    }).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (data) renderUsage(data.usage); })
      .catch(function () { });
  }

  // ------------------------------------------------------------- modes

  function renderModes() {
    els.modes.innerHTML = "";
    state.meta.modes.forEach(function (m) {
      var chip = el("button", "ai-mode-chip" + (m.id === state.mode ? " ai-mode-chip--active" : ""), m.label);
      chip.type = "button";
      chip.title = m.description;
      chip.setAttribute("role", "tab");
      chip.setAttribute("aria-selected", String(m.id === state.mode));
      chip.addEventListener("click", function () { setMode(m.id); });
      els.modes.appendChild(chip);
    });
  }

  function setMode(id) {
    state.mode = id;
    renderModes();
    els.input.focus();
  }

  // ------------------------------------------------------ conversations

  function loadConversations(selectId) {
    return api("/conversations").then(function (d) {
      state.conversations = d.conversations || [];
      renderConvList(selectId);
      return state.conversations;
    });
  }

  function renderConvList(selectId) {
    els.convList.innerHTML = "";
    if (!state.conversations.length) {
      // Designed empty state (not all-caps placeholder text).
      var empty = el("li", "ai-panel__conv-empty");
      empty.appendChild(el("p", "ai-panel__conv-empty-title", "No conversations yet"));
      empty.appendChild(el("p", "ai-panel__conv-empty-body", "Start with a quick action below, or just type a question."));
      els.convList.appendChild(empty);
      return;
    }
    state.conversations.forEach(function (c) {
      var item = el("li", "ai-conv-item");
      var link = el("a", "ai-conv-link" + (c.id === selectId || (state.current && c.id === state.current.id) ? " ai-conv-link--active" : ""), c.title || "Untitled");
      link.href = "#";
      link.addEventListener("click", function (e) {
        e.preventDefault();
        openConversation(c.id);
      });
      var actions = el("div", "ai-conv-actions");
      var del = el("button", "ai-iconbtn", "🗑");
      del.type = "button";
      del.title = "Delete conversation";
      del.setAttribute("aria-label", "Delete conversation");
      del.addEventListener("click", function (e) {
        e.stopPropagation();
        var title = c.title || "Untitled";
        if (!window.confirm('Delete the conversation "' + title + '"? This cannot be undone.')) return;
        deleteConversation(c.id);
      });
      actions.appendChild(del);
      item.append(link, actions);
      els.convList.appendChild(item);
    });
  }

  function createConversation() {
    setStatus("Creating…");
    api("/conversations", { method: "POST", body: { model: state.model, mode: state.mode } })
      .then(function (d) {
        state.current = d.conversation;
        els.title.textContent = state.current.title;
        state.conversations.unshift(state.current);
        renderChatFromMessages([]);
        renderConvList();
        setStatus("");
      })
      .catch(function (e) { setStatus(e.message, true); });
  }

  function openConversation(id) {
    api("/conversations/" + id)
      .then(function (d) {
        state.current = d.conversation;
        // The model is deliberately NOT restored from the conversation: the
        // gateway preference is a single account-wide choice, so switching
        // conversations must not silently change the model under the student.
        state.mode = state.current.mode || state.mode;
        els.title.textContent = state.current.title;
        renderModes();
        renderChatFromMessages(state.current.messages || []);
        renderConvList();
        setStatus("");
      })
      .catch(function (e) { setStatus(e.message, true); });
  }

  function deleteConversation(id) {
    api("/conversations/" + id, { method: "DELETE" })
      .then(function () {
        if (state.current && state.current.id === id) {
          state.current = null;
          els.title.textContent = "AI assistant";
          renderChatFromMessages([]);
        }
        return loadConversations();
      })
      .catch(function (e) { setStatus(e.message, true); });
  }

  function clearCurrent() {
    if (!state.current) return;
    api("/conversations/" + state.current.id, { method: "DELETE" })
      .then(function () {
        state.current = null;
        els.title.textContent = "AI assistant";
        renderChatFromMessages([]);
        return loadConversations();
      })
      .catch(function (e) { setStatus(e.message, true); });
  }

  // -------------------------------------------------------- chat render

  function renderChatFromMessages(messages) {
    els.chat.innerHTML = "";
    if (!messages || !messages.length) {
      showEmptyState(true);
      renderWelcome();
      return;
    }
    showEmptyState(false);
    messages.forEach(function (m) {
      appendMessage(m.role, m.content, m.metadata || {}, false);
    });
    scrollBottom();
  }

  function showEmptyState(visible) {
    if (els.emptyState) {
      els.emptyState.classList.toggle("is-hidden", !visible);
      if (visible) {
        // One-time stagger mount for the quick-action cards.
        els.emptyState.classList.add("is-mounted");
      }
    }
  }

  function renderWelcome() {
    // Static server-rendered quick-action grid is the empty state. Focus the
    // composer only; the cards carry data-init-prompt handled at init.
    if (state.current) {
      els.title.textContent = state.current.title;
    }
  }

  function bubble(role, content, metadata, isStreaming) {
    var msg = el("div", "ai-msg ai-msg--" + role);
    var bubbleNode = el("div", "ai-msg__bubble");
    if (role === "assistant") {
      bubbleNode.innerHTML = renderMarkdown(content);
    } else {
      bubbleNode.textContent = content;
    }
    var meta = el("div", "ai-msg__meta");
    var modelName = metadata && metadata.model ? metadata.model : state.model;
    if (role === "assistant") {
      meta.appendChild(aiTag());
      meta.appendChild(document.createTextNode(modelName));
    } else {
      meta.textContent = "You";
    }
    msg.append(bubbleNode, meta);
    return msg;
  }

  function aiTag() {
    var tag = el("span", "ai-msg__tag");
    tag.textContent = "AI";
    return tag;
  }

  function appendMessage(role, content, metadata, withActions) {
    var msg = bubble(role, content, metadata || {});
    els.chat.appendChild(msg);
    if (role === "assistant" && withActions) {
      addFollowUps(msg);
    }
    scrollBottom();
  }

  // Contextual follow-up suggestions under the latest AI reply. These are the
  // only pills, and they appear only once a conversation is underway.
  function addFollowUps(msgNode) {
    var wrap = el("div", "ai-followups");
    wrap.appendChild(el("span", "ai-followups__label", "Follow up"));
    var chips = [
      { label: "Simplify", mode: "simplify", text: "Rewrite your previous explanation in simpler language." },
      { label: "Go deeper", mode: "explain", text: "Go deeper on the topic you just explained with more detail and examples." },
      { label: "Give example", mode: "explain", text: "Give another concrete example of the topic you just discussed." },
      { label: "Quiz me", mode: "quiz", text: "Create quiz questions on the topic you just discussed." },
    ];
    chips.forEach(function (c) {
      var b = el("button", "ai-action ai-action--followup", c.label);
      b.type = "button";
      b.addEventListener("click", function () {
        setMode(c.mode);
        sendMessage(c.text);
      });
      wrap.appendChild(b);
    });
    msgNode.appendChild(wrap);
  }

  // ---------------------------------------------------------------- sending

  function autosize() {
    els.input.style.height = "auto";
    els.input.style.height = Math.min(els.input.scrollHeight, 192) + "px";
  }

  function sendMessage(messageText) {
    var text = (messageText != null ? messageText : els.input.value).trim();
    if (!text) return;
    if (state.streaming) return;
    if (!state.current) {
      createConversationAndSend(text);
      return;
    }
    doSend(text);
  }

  function createConversationAndSend(text) {
    api("/conversations", { method: "POST", body: { model: state.model, mode: state.mode } })
      .then(function (d) {
        state.current = d.conversation;
        state.conversations.unshift(state.current);
        renderConvList();
        els.title.textContent = state.current.title;
        showEmptyState(false);
        doSend(text);
      })
      .catch(function (e) { setStatus(e.message, true); });
  }

  function doSend(text) {
    els.input.value = "";
    autosize();
    showEmptyState(false);

    appendMessage("user", text, {}, false);
    var welcome = els.chat.querySelector(".ai-followups");
    if (welcome) welcome.remove();

    var aiMsg = el("div", "ai-msg ai-msg--assistant");
    var bubbleNode = el("div", "ai-msg__bubble");
    var typing = el("span", "ai-typing");
    typing.append(el("span"), el("span"), el("span"));
    bubbleNode.appendChild(typing);
    var meta = el("div", "ai-msg__meta");
    meta.appendChild(aiTag());
    meta.appendChild(document.createTextNode(state.model));
    aiMsg.append(bubbleNode, meta);
    els.chat.appendChild(aiMsg);
    scrollBottom();

    state.streaming = true;
    els.send.disabled = true;
    setStatus("AI is thinking…");

    var body = { message: text, mode: state.mode, model: state.model };
    if (state.material) body.materialId = state.material.id;

    function finishStream(aiMsgNode, payload, full) {
      state.streaming = false;
      els.send.disabled = false;
      setStatus("");
      if (full) {
        bubbleNode.innerHTML = renderMarkdown(full);
        var m = aiMsgNode.querySelector(".ai-msg__meta");
        m.textContent = "";
        m.appendChild(aiTag());
        m.appendChild(document.createTextNode(payload.model || friendlyModelName()));
        addFollowUps(aiMsgNode);
        loadConversations();
      } else {
        aiMsgNode.remove();
      }
      // One request was just spent, so pull the real number rather than
      // incrementing a local guess.
      refreshUsage();
      scrollBottom();
    }

    function finishStreamError(aiMsgNode) {
      state.streaming = false;
      els.send.disabled = false;
      aiMsgNode.remove();
      renderWelcomeIfEmpty();
      scrollBottom();
    }

    fetch(API + "/conversations/" + state.current.id + "/messages?stream=1", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(body),
    })
      .then(function (res) {
        if (!res.ok) {
          return res.json().then(function (d) { throw new Error((d && d.error) || "Request failed"); });
        }
        var full = "";
        var started = false;
        var reader = res.body.getReader();
        var decoder = new TextDecoder();
        function pump() {
          return reader.read().then(function (result) {
            if (result.done) {
              if (!started) full = "";
              return finishStream(aiMsg, body, full);
            }
            var chunkText = decoder.decode(result.value, { stream: true });
            var frames = chunkText.split("\n\n");
            frames.forEach(function (frame) {
              frame.split("\n").forEach(function (line) {
                if (line.indexOf("data: ") === 0) {
                  var payload = line.slice(6);
                  if (payload === "[DONE]") return;
                  try {
                    var data = JSON.parse(payload);
                    if (data.type === "chunk" && data.text) {
                      if (!started) { started = true; typing.remove(); }
                      full += data.text;
                      bubbleNode.innerHTML = renderMarkdown(full);
                      scrollBottom();
                    } else if (data.type === "error") {
                      setStatus(data.message, true);
                      finishStreamError(aiMsg);
                      return;
                    }
                  } catch (e) { /* ignore malformed frame */ }
                }
              });
            });
            return pump();
          });
        }
        return pump();
      })
      .catch(function (err) {
        setStatus(err.message, true);
        finishStreamError(aiMsg);
      });
  }

  function renderWelcomeIfEmpty() {
    if (!els.chat.querySelectorAll(".ai-msg").length) {
      showEmptyState(true);
      renderWelcome();
    }
  }

  // ------------------------------------------------------------ attachments

  function onFileSelected(e) {
    var file = e.target.files && e.target.files[0];
    if (!file) return;
    var fd = new FormData();
    fd.append("file", file);
    setStatus("Uploading…");
    fetch(API + "/upload", { method: "POST", body: fd, credentials: "same-origin" })
      .then(function (res) {
        if (!res.ok) return res.json().then(function (d) { throw new Error((d && d.error) || "Upload failed"); });
        return res.json();
      })
      .then(function (d) {
        state.material = { id: d.materialId, filename: d.filename, note: d.note || "" };
        if (d.note) setStatus(d.note);
        renderAttachments();
      })
      .catch(function (err) { setStatus(err.message, true); });
    els.fileInput.value = "";
  }

  function renderAttachments() {
    els.attachments.innerHTML = "";
    if (!state.material) return;
    var chip = el("div", "ai-file");
    chip.appendChild(el("span", null, state.material.filename));
    var rm = el("button", "ai-file__remove", "×");
    rm.type = "button";
    rm.setAttribute("aria-label", "Remove attached file");
    rm.addEventListener("click", function () {
      state.material = null;
      renderAttachments();
    });
    chip.appendChild(rm);
    els.attachments.appendChild(chip);
  }

  // ------------------------------------------------------------------ usage

  /*
   * Quota meter, fed by the gateway (real server-side numbers) rather than
   * the AI Hub's placeholder counter. A null limit means "no cap configured",
   * which is shown as an unlimited state instead of a divide-by-zero.
   */
  function renderUsage(usage) {
    if (!els.usage || !usage) return;
    var used = usage.requestsUsed || 0;
    var limit = usage.requestsLimit;
    var isUnlimited = !limit;
    var pct = isUnlimited ? 0 : Math.min(100, Math.round((used / limit) * 100));
    els.usage.innerHTML = "";

    var text = isUnlimited
      ? "AI requests today: " + used
      : "AI requests today: " + used + " of " + limit;
    var label = el("span", "ai-panel__quota-label", text);
    if (!isUnlimited && used >= limit) {
      label.classList.add("ai-panel__quota-label--spent");
    }

    var bar = el("div", "ai-panel__quota-track");
    bar.setAttribute("role", "progressbar");
    bar.setAttribute("aria-valuemin", "0");
    bar.setAttribute("aria-valuemax", String(isUnlimited ? Math.max(1, used) : limit));
    bar.setAttribute("aria-valuenow", String(used));
    bar.setAttribute("aria-label", text);
    var fill = el("div", "ai-panel__quota-fill" + (pct >= 90 ? " ai-panel__quota-fill--warn" : ""));
    fill.style.width = (isUnlimited ? 100 : pct) + "%";
    bar.appendChild(fill);
    els.usage.append(label, bar);
  }

  function refreshUsage() {
    return fetch(GATEWAY_API + "/usage", { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (data) renderUsage(data); })
      .catch(function () { });
  }

  // ------------------------------------------------------------------ init

  function scrollBottom() {
    els.chat.scrollTop = els.chat.scrollHeight;
  }

  function init() {
    els.input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });
    els.input.addEventListener("input", autosize);
    els.send.addEventListener("click", function () { sendMessage(); });
    if (els.modelPicker) {
      els.modelPicker.addEventListener("change", function () {
        setModel(els.modelPicker.value);
      });
    }
    els.newConv.addEventListener("click", createConversation);
    els.clearConv.addEventListener("click", function () {
      if (!state.current) return;
      if (!window.confirm("Clear the current conversation? This cannot be undone.")) return;
      clearCurrent();
    });
    els.attachBtn.addEventListener("click", function () { els.fileInput.click(); });
    els.fileInput.addEventListener("change", onFileSelected);

    // Server-rendered quick-action cards: fill the composer and send.
    if (els.emptyState) {
      els.emptyState.querySelectorAll("[data-init-prompt]").forEach(function (card) {
        card.addEventListener("click", function () {
          var template = card.getAttribute("data-init-prompt") || "";
          var subject = "";
          var text = template.replace("{subject}", subject).trim();
          els.input.value = text;
          els.input.focus();
          autosize();
        });
      });
    }

    // Deep link from Stash: /ai#ask=<urlsafe_base64({text, context})>.
    // Fill the composer with the quoted card so the user can ask about it.
    if (location.hash && location.hash.indexOf("#ask=") === 0) {
      try {
        var askPayload = JSON.parse(fromUrlSafeBase64(location.hash.slice(5)));
        var askText = (((askPayload && askPayload.text) || "").trim());
        if (askText) {
          els.input.value = askText;
          els.input.focus();
          autosize();
          if (els.input.scrollIntoView) {
            els.input.scrollIntoView({ behavior: "smooth", block: "center" });
          }
          history.replaceState(null, "", location.pathname + location.search);
        }
      } catch (err) { /* bad payload — leave the page as-is */ }
    }

    api("/meta")
      .then(function (m) {
        // The AI Hub's own meta still drives the task modes and the
        // conversation list; models and quota now come from the gateway.
        state.mode = "ask";
        renderModes();
        // Kick off in parallel: neither blocks the other, and a picker failure
        // must not stop the chat from loading.
        loadGatewayModels();
        return loadConversations();
      })
      .then(function () {
        if (state.conversations.length && !state.current) {
          openConversation(state.conversations[0].id);
        } else {
          renderWelcome();
          // Ensure the freshly-server-rendered empty state staggers in even if
          // no chat runs this session (cards already visible in static markup).
          if (els.emptyState && !els.emptyState.classList.contains("is-hidden")) {
            setTimeout(function () {
              els.emptyState.classList.add("is-mounted");
            }, 30);
          }
        }
      })
      .catch(function (e) { setStatus(e.message, true); });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
