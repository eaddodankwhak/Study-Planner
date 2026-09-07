(function () {
    "use strict";

    var trigger = document.getElementById("add-course-trigger");
    var modal = document.getElementById("add-course-modal");
    if (!trigger || !modal) return;

    var panel = modal.querySelector(".modal__panel");
    var backdrop = modal.querySelector(".modal__backdrop");
    var cancel = document.getElementById("cancel-add-course");
    var form = modal.querySelector(".course-form");
    var codeInput = document.getElementById("course_code");
    var titleInput = document.getElementById("title");
    var colorInput = document.getElementById("course-color");
    var preview = document.getElementById("course-preview-card");
    var previewCode = document.getElementById("preview-code");
    var previewTitle = document.getElementById("preview-title");
    var lastFocused = trigger;
    var closeTimer;

    function focusableElements() {
        return panel.querySelectorAll(
            "a[href], button:not([disabled]), input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex=\"-1\"])"
        );
    }

    function openModal() {
        window.clearTimeout(closeTimer);
        lastFocused = document.activeElement || trigger;
        modal.hidden = false;
        modal.setAttribute("aria-hidden", "false");
        modal.classList.add("modal--open");
        document.body.classList.add("modal-open");
        window.setTimeout(function () { codeInput.focus(); }, 0);
    }

    function closeModal() {
        modal.classList.remove("modal--open");
        modal.setAttribute("aria-hidden", "true");
        document.body.classList.remove("modal-open");
        closeTimer = window.setTimeout(function () {
            modal.hidden = true;
        }, 240);
        lastFocused.focus();
    }

    function updatePreview() {
        previewCode.textContent = codeInput.value.trim() || "DCIT 204";
        previewTitle.textContent = titleInput.value.trim() || "To be assigned";
    }

    function selectColor(button) {
        var swatches = modal.querySelectorAll(".color-swatch");
        swatches.forEach(function (swatch) {
            swatch.setAttribute("aria-checked", swatch === button ? "true" : "false");
        });
        colorInput.value = button.getAttribute("data-color") || "";
        preview.style.setProperty("--card-color", button.getAttribute("data-color") ? button.style.getPropertyValue("--swatch") : "var(--color-accent)");
    }

    trigger.addEventListener("click", openModal);
    if (cancel) cancel.addEventListener("click", closeModal);
    if (backdrop) backdrop.addEventListener("click", closeModal);
    codeInput.addEventListener("input", updatePreview);
    titleInput.addEventListener("input", updatePreview);

    modal.querySelectorAll(".color-swatch").forEach(function (button) {
        button.addEventListener("click", function () { selectColor(button); });
    });

    modal.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
            event.preventDefault();
            closeModal();
            return;
        }
        if (event.key !== "Tab") return;

        var elements = focusableElements();
        if (!elements.length) return;
        var first = elements[0];
        var last = elements[elements.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    });

    if (form) form.addEventListener("submit", function () {
        modal.setAttribute("aria-hidden", "true");
    });
})();