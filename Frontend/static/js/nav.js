(function () {
    "use strict";

    var toggle = document.querySelector("[data-nav-toggle]");
    var bar = document.querySelector("[data-nav-bar]");
    var profile = document.querySelector("[data-profile]");
    var profileTrigger = document.querySelector("[data-profile-trigger]");
    var profileMenu = profile && profile.querySelector("[data-profile-menu]");

    function closeDrawer() {
        if (bar) {
            bar.classList.remove("is-open");
            toggle.setAttribute("aria-expanded", "false");
        }
    }

    if (toggle && bar) {
        toggle.addEventListener("click", function () {
            var open = bar.classList.toggle("is-open");
            toggle.setAttribute("aria-expanded", String(open));
        });
        // Close the drawer after navigating to a page.
        bar.querySelectorAll("[data-nav-item]").forEach(function (el) {
            el.addEventListener("click", closeDrawer);
        });
    }

    if (profileTrigger && profileMenu) {
        profileTrigger.addEventListener("click", function (e) {
            e.stopPropagation();
            profileMenu.hidden = !profileMenu.hidden;
        });
        // Close the menu when clicking anywhere outside it.
        document.addEventListener("click", function (e) {
            if (profile && !profile.contains(e.target)) {
                profileMenu.hidden = true;
            }
        });
        // Close the menu with Esc.
        document.addEventListener("keydown", function (e) {
            if (e.key === "Escape") profileMenu.hidden = true;
        });
    }
})();