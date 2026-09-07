/*
 * Courses page — Add-a-course modal + live preview.
 *
 * The add form is an "occasional" interaction (a few per semester) so it gets
 * a real transition: the modal fades + scales in from the trigger, traps focus
 * while open, closes on Escape / backdrop click / Cancel, and returns focus to
 * the trigger on close. The swatch picker replaces a native color-name
 * dropdown with see-and-pick swatches, and the preview card shows the real
 * course card (code, title, colour) as the student types.
 */
(function () {
  "use strict";

  var trigger = document.getElementById("add-course-trigger");
  var modal = document.getElementById("add-course-modal");
  if (!trigger || !modal) return;

  var lastFocused = null;

  function focusables() {
    var nodes = modal.querySelectorAll(
      'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), [href], [tabindex]:not([tabindex="-1"])'
    );
    return Array.prototype.filter.call(nodes, function (el) {
      return el.offsetParent !== null || el === document.activeElement;
    });
  }

  function openModal() {
    lastFocused = trigger;
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
    // Next frame so the entrance transition (opacity + scale) actually runs.
    requestAnimationFrame(function () {
      modal.classList.add("modal--open");
      var code = document.getElementById("course_code");
      if (code) code.focus();
    });
  }

  function closeModal() {
    modal.classList.remove("modal--open");
    modal.setAttribute("aria-hidden", "true");
    modal.hidden = true;
    if (lastFocused && lastFocused.focus) lastFocused.focus();
  }

  trigger.addEventListener("click", function (e) {
    e.preventDefault();
    openModal();
  });

  var backdrop = modal.querySelector(".modal__backdrop");
  if (backdrop) {
    backdrop.addEventListener("click", function () {
      closeModal();
    });
  }

  var cancel = document.getElementById("cancel-add-course");
  if (cancel) cancel.addEventListener("click", closeModal);

  modal.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      closeModal();
      return;
    }
    if (e.key === "Tab" && modal.classList.contains("modal--open")) {
      var list = focusables();
      if (!list.length) return;
      var idx = list.indexOf(document.activeElement);
      if (e.shiftKey) {
        e.preventDefault();
        list[(idx - 1 + list.length) % list.length].focus();
      } else if (idx === list.length - 1 || idx === -1) {
        e.preventDefault();
        list[0].focus();
      }
    }
  });

  // Colour swatch picker: buttons implementing the radio pattern; the picked
  // flat hue (the value stored per course) goes into the hidden input so the
  // fallback path without JS still submits a colour.
  var picker = document.querySelector(".color-swatch-picker");
  var colorInput = document.getElementById("course-color");
  var previewCard = document.getElementById("course-preview-card");
  if (picker) {
    var swatches = picker.querySelectorAll(".color-swatch");
    Array.prototype.forEach.call(swatches, function (sw) {
      sw.addEventListener("click", function () {
        Array.prototype.forEach.call(swatches, function (o) {
          o.setAttribute("aria-checked", String(o === sw));
        });
        var color = sw.getAttribute("data-color") || "";
        if (colorInput) colorInput.value = color;
        if (previewCard) {
          previewCard.style.setProperty(
            "--card-color",
            color || "var(--color-accent)"
          );
        }
      });
    });
  }

  // Live preview: code + title update as the student types; empty fields fall
  // back to the same placeholder text the card renders for a stub row.
  var codeInput = document.getElementById("course_code");
  var titleInput = document.getElementById("title");
  var previewCode = document.getElementById("preview-code");
  var previewTitle = document.getElementById("preview-title");
  function syncPreview() {
    if (previewCode) {
      previewCode.textContent = (codeInput && codeInput.value.trim()) || "DCIT 204";
    }
    if (previewTitle) {
      previewTitle.textContent = (titleInput && titleInput.value.trim()) || "To be assigned";
    }
  }
  if (codeInput) codeInput.addEventListener("input", syncPreview);
  if (titleInput) titleInput.addEventListener("input", syncPreview);
})();