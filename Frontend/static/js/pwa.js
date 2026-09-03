(function () {
    "use strict";

    // Inject the web app manifest + theme colour dynamically so every
    // page using the shared nav gets the app-shell head tags.
    if (!document.querySelector('link[rel="manifest"]')) {
        var link = document.createElement("link");
        link.rel = "manifest";
        link.href = "/static/manifest.json";
        document.head.appendChild(link);
    }
    if (!document.querySelector('meta[name="theme-color"]')) {
        var meta = document.createElement("meta");
        meta.name = "theme-color";
        meta.content = "#0f3a52";
        document.head.appendChild(meta);
    }

    // Register the service worker for offline / installable app shell.
    if ("serviceWorker" in navigator) {
        window.addEventListener("load", function () {
            navigator.serviceWorker.register("/static/sw.js").catch(function (err) {
                console.warn("SW register failed:", err);
            });
        });
    }
})();