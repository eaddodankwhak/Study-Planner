(function () {
    "use strict";

    if (document.body && document.body.dataset.reduceMotion === "true") {
        document.documentElement.classList.add("reduce-motion");
    }

    var status = document.querySelector("[data-app-status]");

    function announce(message) {
        if (!status) return;
        status.textContent = "";
        window.requestAnimationFrame(function () {
            status.textContent = message;
        });
    }

    document.querySelectorAll(".flashes").forEach(function (messages) {
        var hasError = messages.querySelector(".error, .danger");
        messages.setAttribute("role", hasError ? "alert" : "status");
        messages.setAttribute("aria-live", hasError ? "assertive" : "polite");
    });

    document.querySelectorAll("form:not([data-no-loading])").forEach(function (form) {
        form.addEventListener("submit", function (event) {
            if (form.dataset.submitting === "true") {
                event.preventDefault();
                return;
            }

            form.dataset.submitting = "true";
            form.setAttribute("aria-busy", "true");
            var submit = form.querySelector("button[type=submit], input[type=submit]");
            if (submit) {
                submit.disabled = true;
                submit.dataset.originalLabel = submit.value || submit.textContent;
                if (submit.tagName === "INPUT") {
                    submit.value = "Working...";
                } else {
                    submit.textContent = "Working...";
                }
            }
            announce("Working. Please wait for confirmation.");
        });
    });

    document.querySelectorAll("[data-confirm]").forEach(function (control) {
        control.addEventListener("click", function (event) {
            if (!window.confirm(control.dataset.confirm)) {
                event.preventDefault();
                event.stopPropagation();
            }
        });
    });

    document.querySelectorAll("[data-profile-trigger]").forEach(function (trigger) {
        trigger.setAttribute("aria-expanded", "false");
        trigger.setAttribute("aria-haspopup", "menu");
        trigger.addEventListener("click", function () {
            trigger.setAttribute("aria-expanded", String(trigger.getAttribute("aria-expanded") !== "true"));
        });
    });

    document.querySelectorAll("[data-nav-toggle]").forEach(function (toggle) {
        toggle.addEventListener("click", function () {
            toggle.setAttribute("aria-label", toggle.getAttribute("aria-expanded") === "true" ? "Close menu" : "Open menu");
        });
    });
})();
