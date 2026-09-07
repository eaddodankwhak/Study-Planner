/*
 * AI Learning Hub front-end controller.
 * Talks to the /api/ai/* JSON endpoints (and /api/ai/.../messages?stream=1 for
 * Server-Sent Events). No provider-specific logic lives here.
 */
(function () {
  "use strict";

  var API = "/api/ai";

  var state = {
    meta: { models: [], modes: [], preferences: { model: "claude", level: "intermediate" }, usage: { used: 0, limit: 0 }, mockMode: true },
    conversations: [],
    current: null, // conversation object
    model: "claude",
    mode: "ask",
    streaming: false,
    material: null, // {id, filename}
  };

  var els = {
    sidebar: document.getElementById("ai-sidebar"),
    convList: document.getElementById("ai-conv-list"),
    modelList: document.getElementById("ai-model-list"),
    modes: document.getElementById("ai-modes"),
    chat: document.getElementById("ai-chat"),
    status: document.getElementById("ai-status"),
    input: document.getElementById("ai-input"),
    send: document.getElementById("ai-send"),
    title: document.getElementById("ai-conv-title"),
    usage: document.getElementById("ai-usage"),
    currentModel: document.getElementById("ai-current-model"),
    attachBtn: document.getElementById("ai-attach-btn"),
    fileInput: document.getElementById("ai-file-input"),
    attachments: document.getElementById("ai-attachments"),
    clearConv: document.getElementById("ai-clear-conv"),
    newConv: document.getElementById("ai-new-conv"),
    menuToggle: document.getElementById("ai-menu-toggle"),
  };

  // ------------------------------------------------------------------ utils

  function escapeHtml(s) {
    var div = document.createElement("div");
    div.textContent = s == null ? "" : String(s);
    return div.innerHTML;
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

  // minimal markdown -> trusted HTML for AI (markdown) output. Content is from
  // the AI provider and rendered deliberately (not escaped) so formatting shows.
  function renderMarkdown(text) {
    var t = escapeHtml(text || "");
    // code fences
    t = t.replace(/```(\w*)\n([\s\S]*?)```/g, function (m, lang, code) {
      return '<pre><code class="lang-' + escapeHtml(lang) + '">' + code + "</code></pre>";
    });
    // inline code
    t = t.replace(/`([^`]+)`/g, function (m, c) {
      return "<code>" + c + "</code>";
    });
    // headings
    t = t.replace(/^### (.*)$/gm, "<h3>$1</h3>");
    t = t.replace(/^## (.*)$/gm, "<h2>$1</h2>");
    t = t.replace(/^# (.*)$/gm, "<h1>$1</h1>");
    // bold
    t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    // italic
    t = t.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
    // links
    t = t.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    // bullet lists
    t = t.replace(/^\s*[-*]\s+/gm, "<li>").replace(/(<li>[^<]*\n?)+/g, function (m) {
      return "<ul>" + m.replace(/\n/g, "") + "</ul>";
    });
    // numbered lists
    t = t.replace(/^\s*\d+\.\s+/gm, "<li>").replace(/(<li>[^<]*\n?)+/g, function (m) {
      return "<ol>" + m.replace(/\n/g, "") + "</ol>";
    });
    // paragraphs
    t = t.replace(/\n{2,}/g, "</p><p>");
    t = t.replace(/\n/g, "<br>");
    if (t.indexOf("<p>") === -1 && (t.indexOf("<li>") === -1)) {
      t = "<p>" + t + "</p>";
    }
    // blockquote
    t = t.replace(/<p>(&gt;|&gt;&gt;) ([^<]*)<\/p>/g, "<blockquote>$2</blockquote>");
    return t;
  }

  // ------------------------------------------------------------------ model list

  function renderModels() {
    els.modelList.innerHTML = "";
    state.meta.models.forEach(function (m) {
      var label = el("label", "ai-model-option" + (m.id === state.model ? " ai-model-option--selected" : ""));
      var radio = document.createElement("input");
      radio.type = "radio";
      radio.name = "ai-model";
      radio.value = m.id;
      radio.checked = m.id === state.model;
      radio.setAttribute("aria-label", m.displayName);
      radio.addEventListener("change", function () {
        setModel(m.id);
      });
      var strong = el("strong", null, m.displayName);
      var small = el("small", null, m.description);
      var body = el("div");
      body.append(strong, small);
      label.append(radio, body);
      els.modelList.appendChild(label);
    });
  }

  function setModel(id) {
    state.model = id;
    els.currentModel.textContent = (state.meta.models.find(function (m) { return m.id === id; }) || {}).displayName || id;
    renderModels();
    if (state.current) {
      api("/conversations/" + state.current.id, { method: "PATCH", body: { model: id } }).then(function () {
        state.current.model = id;
      }).catch(function () {});
    }
    api("/preferences", { method: "PUT", body: { model: id } }).catch(function () {});
  }

  // ------------------------------------------------------------------ modes

  function renderModes() {
    els.modes.innerHTML = "";
    state.meta.modes.forEach(function (m) {
      var chip = el("button", "ai-mode-chip" + (m.id === state.mode ? " ai-mode-chip--active" : ""), m.label);
      chip.type = "button";
      chip.title = m.description;
      chip.setAttribute("role", "tab");
      chip.setAttribute("aria-selected", m.id === state.mode);
      chip.addEventListener("click", function () { setMode(m.id); });
      els.modes.appendChild(chip);
    });
  }

  function setMode(id) {
    state.mode = id;
    renderModes();
    els.input.focus();
  }

  // ------------------------------------------------------------------ conversations

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
      var empty = el("li", "ai-sidebar__heading", "No conversations yet.");
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
        state.model = state.current.model || state.model;
        state.mode = state.current.mode || state.mode;
        els.title.textContent = state.current.title;
        renderModels();
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
          els.title.textContent = "AI Learning Hub";
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
        els.title.textContent = "AI Learning Hub";
        renderChatFromMessages([]);
        return loadConversations();
      })
      .catch(function (e) { setStatus(e.message, true); });
  }

  // ------------------------------------------------------------------ chat render

  function renderChatFromMessages(messages) {
    els.chat.innerHTML = "";
    if (!messages || !messages.length) {
      renderWelcome();
      return;
    }
    messages.forEach(function (m) {
      appendMessage(m.role, m.content, m.metadata || {});
    });
    scrollBottom();
  }

  function renderWelcome() {
    els.chat.innerHTML = "";
    var wrap = el("div", "ai-welcome");
    wrap.appendChild(el("h2", null, "How can I help you study today?"));
    wrap.appendChild(el("h3", null, "Pick a task below or just start typing."));

    var grid = el("div", "ai-suggestions");
    var suggestions = [
      { label: "Explain a topic", text: "Explain recursion to me like I'm a beginner." },
      { label: "Summarize notes", text: "Summarize the key points of photosynthesis." },
      { label: "Solve a question", text: "Solve: integrate x^2 from 0 to 3, step by step." },
      { label: "Generate a quiz", text: "Create 5 quiz questions about the periodic table." },
      { label: "Create flashcards", text: "Make flashcards for the key terms in genetics." },
      { label: "Build a study plan", text: "Make me a 1-week study plan for my statistics exam." },
      { label: "Prepare for an exam", text: "Help me prepare for my calculus exam next week." },
      { label: "Ask anything", text: "Can you explain how DNA replication works?" },
    ];
    suggestions.forEach(function (s) {
      var b = el("button", "ai-suggestion", s.label);
      b.type = "button";
      b.addEventListener("click", function () {
        els.input.value = s.text;
        els.input.focus();
        autosize();
      });
      grid.appendChild(b);
    });
    wrap.appendChild(grid);
    els.chat.appendChild(wrap);
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

  // Small "AI" pill marking that this content was machine-generated.
  function aiTag() {
    var tag = el("span", "ai-msg__tag");
    tag.textContent = "AI";
    return tag;
  }

  function appendMessage(role, content, metadata) {
    els.chat.appendChild(bubble(role, content, metadata || {}));
    scrollBottom();
  }

  // ------------------------------------------------------------------ actions on replies

  function addActionButtons(msgNode, content) {
    var actions = el("div", "ai-msg__actions");

    var copy = el("button", "ai-action", "Copy");
    copy.type = "button";
    copy.addEventListener("click", function () {
      navigator.clipboard.writeText(content).then(function () { setStatus("Copied to clipboard."); });
    });

    var simplify = el("button", "ai-action", "Simplify");
    simplify.type = "button";
    simplify.addEventListener("click", function () {
      setMode("simplify");
      sendMessage("Rewrite your previous explanation in simpler language.");
    });

    var deeper = el("button", "ai-action", "Explain deeper");
    deeper.type = "button";
    deeper.addEventListener("click", function () {
      setMode("explain");
      sendMessage("Go deeper on the topic you just explained with more detail and examples.");
    });

    var example = el("button", "ai-action", "Give example");
    example.type = "button";
    example.addEventListener("click", function () {
      setMode("explain");
      sendMessage("Give another concrete example of the topic you just discussed.");
    });

    var quiz = el("button", "ai-action", "Quiz me");
    quiz.type = "button";
    quiz.addEventListener("click", function () {
      setMode("quiz");
      sendMessage("Create quiz questions on the topic you just discussed.");
    });

    var save = el("button", "ai-action", "Save to notes");
    save.type = "button";
    save.addEventListener("click", function () {
      saveToNotes(content);
    });

    actions.append(copy, save, simplify, deeper, example, quiz);
    msgNode.appendChild(actions);
  }

  function saveToNotes(content) {
    var blob = new Blob(["AI Learning Hub note\n\n" + content], { type: "text/plain" });
    var a = document.createElement("a");
    var url = URL.createObjectURL(blob);
    a.href = url;
    a.download = "ai-note.md";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    setStatus("Note downloaded as ai-note.md. (Paste into your planner to save.)");
  }

  // ------------------------------------------------------------------ sending

  function autosize() {
    els.input.style.height = "auto";
    els.input.style.height = Math.min(els.input.scrollHeight, 192) + "px";
  }

  function sendMessage(messageText) {
    var text = (messageText != null ? messageText : els.input.value).trim();
    if (!text) return;
    if (state.streaming) return;
    if (!state.current) {
      // auto-create a conversation on first message
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
        doSend(text);
      })
      .catch(function (e) { setStatus(e.message, true); });
  }

  function doSend(text) {
    els.input.value = "";
    autosize();

    // Append user message locally.
    appendMessage("user", text, {});
    // Clear welcome if present.
    var welcome = els.chat.querySelector(".ai-welcome");
    if (welcome) welcome.remove();

    // Creating assistant placeholder + typing indicator.
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

    var body = {
      message: text,
      mode: state.mode,
      model: state.model,
    };
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
        m.appendChild(document.createTextNode(payload.model || state.model));
        addActionButtons(aiMsgNode, full);
        loadConversations();
      } else {
        aiMsgNode.remove();
      }
      scrollBottom();
    }

    function finishStreamError(aiMsgNode) {
      state.streaming = false;
      els.send.disabled = false;
      aiMsgNode.remove();
      renderWelcomeIfEmpty();
      scrollBottom();
    }

    // Use SSE streaming with fetch.
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
              if (!started) {
                // No chunks received but stream ended normally -> empty reply.
                full = "";
              }
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
                      if (!started) {
                        started = true;
                        typing.remove();
                      }
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
      renderWelcome();
    }
  }

  // ------------------------------------------------------------------ attachments

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

  function renderUsage(m) {
    state.meta = m;
    els.usage.textContent = "AI requests today: " + m.usage.used + " / " + (m.usage.limit === 0 ? "∞" : m.usage.limit);
    var mockHint = m.mockMode ? " (demo mode — no API key)" : "";
    els.currentModel.textContent = (m.models.find(function (x) { return x.id === state.model; }) || {}).displayName || state.model;
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
    els.newConv.addEventListener("click", createConversation);
    els.clearConv.addEventListener("click", clearCurrent);
    els.attachBtn.addEventListener("click", function () { els.fileInput.click(); });
    els.fileInput.addEventListener("change", onFileSelected);
    if (els.menuToggle) {
      els.menuToggle.addEventListener("click", function () {
        els.sidebar.classList.toggle("is-open");
      });
    }

    api("/meta")
      .then(function (m) {
        renderUsage(m);
        state.model = m.preferences.model || "claude";
        state.mode = "ask";
        renderModels();
        renderModes();
        return loadConversations();
      })
      .then(function () {
        if (state.conversations.length && !state.current) {
          openConversation(state.conversations[0].id);
        } else {
          renderWelcome();
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
