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

    function open() {
        palette.hidden = false;
        input.value = "";
        render("all");
        setTimeout(function () { input.focus(); }, 0);
    }

    function close() {
        palette.hidden = true;
    }

    function render(mode) {
        list.innerHTML = "";
        routes
            .filter(function (r) {
                return mode === "all" || r.label.toLowerCase().indexOf(mode) !== -1;
            })
            .forEach(function (r) {
                var li = document.createElement("li");
                var a = document.createElement("a");
                a.textContent = r.label;
                a.href = r.url;
                li.appendChild(a);
                list.appendChild(li);
            });
        if (list.children.length === 0) {
            var li = document.createElement("li");
            li.textContent = "No matches.";
            list.appendChild(li);
        }
    }

    document.addEventListener("keydown", function (e) {
        if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
            e.preventDefault();
            open();
        } else if (e.key === "Escape") {
            close();
        }
    });

    input.addEventListener("input", function () {
        render(input.value.trim().toLowerCase());
    });

    palette.querySelector("[data-palette-close]").addEventListener("click", close);
})();