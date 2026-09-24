/* dashboard.js — Home page enhancements.
 *
 * All behaviour is progressive enhancement: with JavaScript off, the page is
 * fully server-rendered and every stat tile still works as an anchor link.
 */
(function () {
    "use strict";

    var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    /* Greeting by time of day. */
    var greeting = document.getElementById("home-greeting");
    if (greeting) {
        var hour = new Date().getHours();
        greeting.textContent = hour < 12 ? "Good morning" : (hour < 18 ? "Good afternoon" : "Good evening");
    }

    /* Stat tiles smooth-scroll to their section and flash its border. */
    var scrollers = document.querySelectorAll(".stat[data-scroll]");
    Array.prototype.forEach.call(scrollers, function (tile) {
        tile.addEventListener("click", function (event) {
            var href = tile.getAttribute("href");
            if (!href || href.charAt(0) !== "#") return;
            event.preventDefault();
            var target = document.getElementById(href.slice(1));
            if (!target) return;
            target.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "start" });
            target.classList.remove("is-highlighted");
            void target.offsetWidth;
            target.classList.add("is-highlighted");
            window.setTimeout(function () {
                target.classList.remove("is-highlighted");
            }, 2600);
        });
    });

    /* Live stat refresh: keep the tiles honest when data changes elsewhere. */
    var statValues = document.querySelectorAll("[data-stat]");
    if (!statValues.length || !("fetch" in window)) return;

    function hoopTo(ring, hours) {
        var goal = parseFloat(ring.style.getPropertyValue("--goal")) || 6;
        var pct = Math.round(Math.max(0, Math.min(hours / goal, 1)) * 100);
        ring.style.setProperty("--pct", String(pct));
    }

    fetch("/api/home/stats", { headers: { "Accept": "application/json" } })
        .then(function (resp) {
            if (!resp.ok) throw new Error("HTTP " + resp.status);
            return resp.json();
        })
        .then(function (data) {
            if (!data || data.ok !== true) throw new Error("bad payload");
            Array.prototype.forEach.call(statValues, function (node) {
                var key = node.getAttribute("data-stat");
                if (key === "courses") node.textContent = data.courses;
                if (key === "deadlines") node.textContent = data.deadlines;
                if (key === "tasks") node.textContent = data.tasks;
                if (key === "available_hours") {
                    var round = Math.round(data.available_hours * 10) / 10;
                    node.textContent = round + "h";
                    var ring = node.closest(".stat--availability");
                    if (ring && ring.querySelector(".stat__ring")) {
                        hoopTo(ring.querySelector(".stat__ring"), data.available_hours);
                    }
                    var hint = ring && ring.querySelector(".stat__hint");
                    if (hint && ring.querySelector(".stat__ring")) {
                        var hintGoal = parseFloat(ring.querySelector(".stat__ring").style.getPropertyValue("--goal")) || 6;
                        hint.textContent = round + "h of " + hintGoal + "h goal";
                    }
                }
            });
        })
        .catch(function () {
            Array.prototype.forEach.call(statValues, function (node) {
                var card = node.closest(".stat");
                if (card && !card.classList.contains("is-stale")) {
                    card.classList.add("is-stale");
                    card.setAttribute(
                        "title",
                        "Couldn't refresh — showing the last known numbers."
                    );
                }
            });
        });
}());