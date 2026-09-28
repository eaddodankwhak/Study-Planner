/*
 * Stash upload page + processing page.
 * The upload form streams the file to /api/stash/documents; on success the
 * reader opens (cards appear as they're generated). The processing page polls
 * each document and swaps in an "Open reader" link the moment cards are ready.
 */
(function () {
  "use strict";

  var toast = window.StudyPlannerToast;

  function json(res) {
    return res.json().catch(function () {
      return { ok: false, error: "Couldn't read the server response." };
    });
  }

  /* ---------------------------------------------------------------- upload */

  var form = document.getElementById("stash-upload-form");
  if (form) {
    var fileInput = document.getElementById("stash-file");
    var submit = document.getElementById("stash-submit");
    var label = document.getElementById("stash-file-label");
    var errorBox = document.getElementById("stash-upload-error");
    var drop = document.getElementById("stash-drop");
    var maxMb = parseInt(fileInput.getAttribute("data-max-mb") || "50", 10);

    function setError(message) {
      if (!message) { errorBox.hidden = true; return; }
      errorBox.textContent = message;
      errorBox.hidden = false;
    }

    function onFile() {
      var file = fileInput.files && fileInput.files[0];
      if (!file) { submit.disabled = true; label.textContent = "\u2026or click to choose a file"; return; }
      var okType = /\.pdf$|\.pptx$/i.test(file.name);
      if (!okType) {
        setError("Only .pdf and .pptx files are supported.");
        submit.disabled = true;
        return;
      }
      if (file.size > maxMb * 1024 * 1024) {
        setError("That file is over the " + maxMb + " MB limit.");
        submit.disabled = true;
        return;
      }
      setError("");
      submit.disabled = false;
      label.textContent = file.name + " (" + Math.max(1, Math.round(file.size / 1024)) + " KB)";
    }

    fileInput.addEventListener("change", onFile);

    ["dragenter", "dragover"].forEach(function (name) {
      drop.addEventListener(name, function (event) {
        event.preventDefault();
        drop.classList.add("stash-drop--drag");
      });
    });
    ["dragleave", "drop"].forEach(function (name) {
      drop.addEventListener(name, function (event) {
        event.preventDefault();
        drop.classList.remove("stash-drop--drag");
      });
    });
    drop.addEventListener("drop", function (event) {
      var file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
      if (!file) return;
      fileInput.files = null; // can't assign; fall back to letting user pick
      // Directly upload the dropped file even though the input can't show it.
      uploadFile(file, form);
    });

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      var file = fileInput.files && fileInput.files[0];
      if (!file) return;
      uploadFile(file, form);
    });

    function uploadFile(file, frm) {
      setError("");
      submit.disabled = true;
      submit.textContent = "Uploading\u2026";
      var data = new FormData();
      data.append("file", file, file.name);
      var course = frm.querySelector("select[name=course_id]");
      if (course && course.value) data.append("course_id", course.value);

      fetch("/api/stash/documents", { method: "POST", body: data, credentials: "same-origin" })
        .then(json)
        .then(function (res) {
          if (res.ok && res.document) {
            toast.success("Uploaded \u2014 generating cards\u2026");
            window.location.href = "/stash/reader/" + encodeURIComponent(res.document.id);
            return;
          }
          if (res.duplicate) {
            setError(res.error + " Open the library to read the existing copy.");
            submit.disabled = true;
            submit.textContent = "Upload & generate";
            return;
          }
          throw new Error(res.error || "Upload failed.");
        })
        .catch(function (err) {
          setError(err.message || "Upload failed.");
          submit.disabled = false;
          submit.textContent = "Upload & generate";
        });
    }
  }

  /* ------------------------------------------------------------ processing */

  var queue = document.querySelector("[data-stash-processing]");
  if (queue) {
    var articles = Array.prototype.slice.call(
      queue.querySelectorAll("[data-doc-id]")
    );
    if (!articles.length) return;

    var pending = articles.filter(function (a) {
      return a.getAttribute("data-doc-status") !== "done";
    });

    function update(doc, article) {
      var bar = article.querySelector("[data-progress-bar] .stash-progress__bar");
      var meta = article.querySelectorAll(".stash-card__meta");
      if (bar) bar.style.width = (doc.progress_percent || 0) + "%";
      if (doc.status === "ready") {
        var link = document.createElement("a");
        link.className = "button button--primary button--sm";
        link.href = "/stash/reader/" + encodeURIComponent(doc.id);
        link.textContent = "Open reader (" + (doc.total_cards || 0) + " cards)";
        var progressBar = article.querySelector(".stash-progress");
        if (progressBar) progressBar.remove();
        if (meta.length) article.removeChild(meta[0]);
        article.querySelector(".stash-card__title").insertAdjacentElement("afterend", link);
        article.setAttribute("data-doc-status", "done");
        return true;
      }
      if (doc.status === "error") {
        var err = document.createElement("p");
        err.className = "stash-card__error";
        err.textContent = doc.error_message || "Generation failed.";
        var progressBar2 = article.querySelector(".stash-progress");
        if (progressBar2) progressBar2.remove();
        if (meta.length) article.removeChild(meta[0]);
        article.querySelector(".stash-card__title").insertAdjacentElement("afterend", err);
        article.setAttribute("data-doc-status", "done");
        return true;
      }
      if (meta.length) meta[0].textContent = (doc.status === "queued" ? "Queued\u2026 " : "Generating\u2026 ") + (doc.progress_percent || 0) + "%";
      return false;
    }

    function tick() {
      var stillPending = [];
      pending.forEach(function (article) {
        var id = article.getAttribute("data-doc-id");
        fetch("/api/stash/documents/" + encodeURIComponent(id), { credentials: "same-origin" })
          .then(json)
          .then(function (res) {
            if (!res.ok || !res.document) return;
            if (!update(res.document, article)) stillPending.push(article);
          })
          .catch(function () { stillPending.push(article); });
      });
      pending = stillPending;
      if (pending.length) window.setTimeout(tick, 3000);
      else window.location.reload();
    }

    window.setTimeout(tick, 1500);
  }
})();