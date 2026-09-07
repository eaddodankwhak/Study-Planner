(function () {
    "use strict";

    var palette = document.querySelector("[data-palette]");
    if (!palette) return;

    var routes = [];
    try {
        routes = JSON.parse(palette.getAttribute("data-routes") || "[]");
    } catch (e) {
        routes = [];
    }

    var input = palette.querySelector("[data-palette-input]");
    var list = palette.querySelector("[data-palette-list]");
    var hint = palette.querySelector("[data-palette-hint]");
    var selectedIndex = -1;

    function open() {
        palette.classList.add("is-open");
        input.value = "";
        selectedIndex = -1;
        setTimeout(function () {
            render("");
            input.focus();
        }, 0);
    }

    function close() {
        palette.classList.remove("is-open");
    }

    document.querySelectorAll("[data-palette-trigger]").forEach(function (trigger) {
        trigger.addEventListener("click", open);
    });

    // Show suggested pages when there is no query; otherwise filter.
    function matches(r, query) {
        if (!query) return true;
        return r.label.toLowerCase().indexOf(query) !== -1 ||
               (r.crumb || "").toLowerCase().indexOf(query) !== -1;
    }

    function render(query) {
        query = (query || "").trim().toLowerCase();
        var items = routes.filter(function (r) { return matches(r, query); });
        list.innerHTML = "";

        if (items.length === 0) {
            var empty = document.createElement("li");
            empty.className = "palette__empty";
            empty.textContent = "No matches for \u201C" + input.value + "\u201D.";
            list.appendChild(empty);
            return;
        }

        items.forEach(function (r, idx) {
            var li = document.createElement("li");
            var a = document.createElement("a");
            a.className = "palette__item" + (idx === 0 ? " is-selected" : "");
            a.href = r.url;

            var label = document.createElement("span");
            label.className = "palette__label";
            label.textContent = r.label;

            var crumb = document.createElement("span");
            crumb.className = "palette__crumb";
            crumb.textContent = r.crumb || "";

            a.appendChild(label);
            a.appendChild(crumb);
            li.appendChild(a);
            list.appendChild(li);
        });
    }

    function move(step) {
        var anchors = list.querySelectorAll(".palette__item");
        if (anchors.length === 0) return;
        selectedIndex += step;
        if (selectedIndex < 0) selectedIndex = anchors.length - 1;
        if (selectedIndex >= anchors.length) selectedIndex = 0;
        anchors.forEach(function (el, i) {
            el.classList.toggle("is-selected", i === selectedIndex);
        });
        anchors[selectedIndex].scrollIntoView({ block: "nearest" });
    }

    function select() {
        var selected = list.querySelector(".palette__item.is-selected");
        if (selected && selected.href) {
            window.location.href = selected.href;
        }
    }

    document.addEventListener("keydown", function (e) {
        var open = palette.classList.contains("is-open");
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
            e.preventDefault();
            open ? close() : open();
        } else if (!open) {
            return;
        } else if (e.key === "Escape") {
            close();
        } else if (e.key === "ArrowDown") {
            e.preventDefault();
            move(1);
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            move(-1);
        } else if (e.key === "Enter") {
            e.preventDefault();
            select();
        }
    });

    input.addEventListener("input", function () {
        selectedIndex = -1;
        render(input.value);
    });

    // Backdrop click closes.
    palette.addEventListener("mousedown", function (e) {
        if (e.target.closest("[data-palette-close]")) close();
    });
})();
