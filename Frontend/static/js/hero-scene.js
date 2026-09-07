(function () {
    "use strict";

    var canvas = document.querySelector("[data-hero-scene]");
    if (!canvas || !canvas.getContext) return;

    var context = canvas.getContext("2d", { alpha: true });
    if (!context) return;

    var reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    var compactDevice = window.matchMedia("(max-width: 700px)").matches ||
        (navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 4) ||
        (navigator.deviceMemory && navigator.deviceMemory <= 4);
    // Tune nodeCount for density; keep the compact value low for mobile GPUs.
    var nodeCount = compactDevice ? 18 : 34;
    var nodes = [];
    var width = 0;
    var height = 0;
    var animationFrame = 0;
    var pointerX = 0;
    var pointerY = 0;
    var scrollOffset = 0;
    var dpr = Math.min(window.devicePixelRatio || 1, compactDevice ? 1.25 : 1.75);

    function resize() {
        var bounds = canvas.getBoundingClientRect();
        width = bounds.width;
        height = bounds.height;
        canvas.width = Math.floor(width * dpr);
        canvas.height = Math.floor(height * dpr);
        context.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function seedNodes() {
        nodes = [];
        for (var index = 0; index < nodeCount; index += 1) {
            nodes.push({
                x: Math.random() * width,
                y: Math.random() * height,
                radius: 2 + Math.random() * 3.5,
                depth: 0.35 + Math.random() * 0.65,
                phase: Math.random() * Math.PI * 2,
                drift: 0.00025 + Math.random() * 0.00045,
                // Tune these three colors together with the welcome scrim for contrast.
                color: index % 3 === 0 ? "#8fd8d0" : (index % 3 === 1 ? "#bad0ff" : "#e9c6f4")
            });
        }
    }

    function draw(time) {
        context.clearRect(0, 0, width, height);
        var movement = reducedMotion.matches ? 0 : time;
        var shiftX = pointerX * 10;
        var shiftY = pointerY * 7 - scrollOffset * 0.035;

        nodes.forEach(function (node, index) {
            // Increase drift multipliers carefully; slow motion is intentional here.
            var x = node.x + Math.sin(movement * node.drift + node.phase) * 18 * node.depth + shiftX * node.depth;
            var y = node.y + Math.cos(movement * node.drift * 0.8 + node.phase) * 14 * node.depth + shiftY * node.depth;
            node.renderX = x;
            node.renderY = y;
            node.renderIndex = index;
        });

        context.lineWidth = 1;
        nodes.forEach(function (node, index) {
            nodes.slice(index + 1).forEach(function (other) {
                var dx = node.renderX - other.renderX;
                var dy = node.renderY - other.renderY;
                var distance = Math.sqrt(dx * dx + dy * dy);
                if (distance > 190) return;
                context.strokeStyle = "rgba(177, 218, 231, " + ((1 - distance / 190) * 0.22) + ")";
                context.beginPath();
                context.moveTo(node.renderX, node.renderY);
                context.lineTo(other.renderX, other.renderY);
                context.stroke();
            });
        });

        nodes.forEach(function (node) {
            var glow = context.createRadialGradient(node.renderX, node.renderY, 0, node.renderX, node.renderY, node.radius * 5);
            glow.addColorStop(0, node.color + "cc");
            glow.addColorStop(1, node.color + "00");
            context.fillStyle = glow;
            context.beginPath();
            context.arc(node.renderX, node.renderY, node.radius * 5, 0, Math.PI * 2);
            context.fill();
            context.fillStyle = node.color;
            context.beginPath();
            context.arc(node.renderX, node.renderY, node.radius, 0, Math.PI * 2);
            context.fill();
        });

        if (!reducedMotion.matches) animationFrame = window.requestAnimationFrame(draw);
    }

    function pointerMove(event) {
        if (compactDevice || reducedMotion.matches) return;
        pointerX = (event.clientX / window.innerWidth - 0.5) * 2;
        pointerY = (event.clientY / window.innerHeight - 0.5) * 2;
    }

    function stop() {
        if (animationFrame) window.cancelAnimationFrame(animationFrame);
        animationFrame = 0;
    }

    function start() {
        stop();
        draw(performance.now());
    }

    resize();
    seedNodes();
    start();
    window.addEventListener("resize", function () {
        resize();
        seedNodes();
        start();
    }, { passive: true });
    window.addEventListener("pointermove", pointerMove, { passive: true });
    window.addEventListener("scroll", function () {
        scrollOffset = window.scrollY || 0;
    }, { passive: true });
    reducedMotion.addEventListener("change", start);
})();
