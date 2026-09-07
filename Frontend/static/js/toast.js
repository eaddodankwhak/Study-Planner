(function () {
    "use strict";

    var container = document.querySelector("[data-toast-container]");
    if (!container) return;

    var dismissAfter = 4000;
    var icons = { success: "✓", error: "!", info: "i" };

    function remove(toast) {
        if (!toast || !toast.parentNode) return;
        toast.classList.add("toast--leaving");
        window.setTimeout(function () { toast.remove(); }, 220);
    }

    function schedule(toast) {
        var timer = window.setTimeout(function () { remove(toast); }, dismissAfter);
        function pause() { window.clearTimeout(timer); }
        function resume() { timer = window.setTimeout(function () { remove(toast); }, dismissAfter); }
        toast.addEventListener("mouseenter", pause);
        toast.addEventListener("mouseleave", resume);
        toast.addEventListener("focusin", pause);
        toast.addEventListener("focusout", resume);
        toast.querySelector("[data-toast-close]").addEventListener("click", function () { remove(toast); });
    }

    function setup(toast) {
        if (!toast || toast.dataset.toastReady === "true") return;
        toast.dataset.toastReady = "true";
        schedule(toast);
    }

    function show(variant, message) {
        var toast = document.createElement("div");
        var role = variant === "error" ? "alert" : "status";
        toast.className = "toast toast--" + (icons[variant] ? variant : "info");
        toast.dataset.toast = "";
        toast.setAttribute("role", role);
        toast.setAttribute("aria-live", variant === "error" ? "assertive" : "polite");
        toast.setAttribute("aria-atomic", "true");
        toast.innerHTML = "<span class=\"toast__icon\" aria-hidden=\"true\">" + (icons[variant] || icons.info) + "</span>" +
            "<span class=\"toast__message\"></span>" +
            "<button class=\"toast__close\" type=\"button\" data-toast-close aria-label=\"Dismiss notification\">&times;</button>";
        toast.querySelector(".toast__message").textContent = message;
        container.prepend(toast);
        setup(toast);
    }

    container.querySelectorAll("[data-toast]").forEach(setup);

    window.StudyPlannerToast = {
        success: function (message) { show("success", message); },
        error: function (message) { show("error", message); },
        info: function (message) { show("info", message); }
    };
})();
