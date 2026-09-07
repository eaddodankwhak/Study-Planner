(function () {
    "use strict";

    var sidebar = document.querySelector("[data-course-sidebar]");
    if (!sidebar) return;

    var toggles = Array.prototype.slice.call(sidebar.querySelectorAll("[data-course-toggle]"));

    function setOpen(toggle, open) {
        toggles.forEach(function (item) {
            var panel = document.getElementById(item.getAttribute("aria-controls"));
            var isTarget = item === toggle;
            item.setAttribute("aria-expanded", String(isTarget && open));
            if (panel) panel.classList.toggle("is-open", isTarget && open);
        });
    }

    toggles.forEach(function (toggle) {
        toggle.addEventListener("click", function () {
            var isOpen = toggle.getAttribute("aria-expanded") === "true";
            var isCurrent = toggle.dataset.courseCurrent === "true";

            if (isCurrent) {
                setOpen(toggle, !isOpen);
                return;
            }

            setOpen(toggle, true);
            window.location.assign(toggle.dataset.courseUrl);
        });
    });

    // Arrow keys move through visible course controls and nested links. Native
    // Tab/Enter/Space behavior remains available for all controls and links.
    sidebar.addEventListener("keydown", function (event) {
        if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
        var items = Array.prototype.slice.call(sidebar.querySelectorAll("[data-course-toggle], .sites-nav__sublist.is-open [data-course-nav-item]"));
        var index = items.indexOf(document.activeElement);
        if (index === -1) return;
        event.preventDefault();
        var next = event.key === "ArrowDown" ? index + 1 : index - 1;
        if (next < 0) next = items.length - 1;
        if (next >= items.length) next = 0;
        items[next].focus();
    });
}());
