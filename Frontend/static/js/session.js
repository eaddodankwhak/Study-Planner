(function () {
    "use strict";

    var display = document.querySelector("[data-timer-display]");
    var startBtn = document.querySelector("[data-timer-start]");
    var pauseBtn = document.querySelector("[data-timer-pause]");
    var resetBtn = document.querySelector("[data-timer-reset]");
    var durationInput = document.querySelector("[data-duration-input]");
    var rootDuration = document.querySelector("[data-root-duration]");

    if (!display) return;

    var DURATION = 25 * 60; // seconds
    var remaining = DURATION;
    var running = false;
    var tick = null;

    function fmt(total) {
        total = Math.max(0, total);
        var m = Math.floor(total / 60);
        var s = total % 60;
        return (m < 10 ? "0" + m : m) + ":" + (s < 10 ? "0" + s : s);
    }

    function render() {
        display.textContent = fmt(remaining);
    }

    function syncInput() {
        if (durationInput && rootDuration) {
            rootDuration.value = Math.round((DURATION - remaining) / 60);
        }
    }

    function stop() {
        running = false;
        if (tick) clearInterval(tick);
        tick = null;
    }

    function start() {
        if (running) return;
        running = true;
        tick = setInterval(function () {
            remaining -= 1;
            if (remaining <= 0) {
                remaining = 0;
                stop();
                display.textContent = "Time's up!";
            } else {
                render();
            }
            syncInput();
        }, 1000);
    }

    startBtn.addEventListener("click", start);
    pauseBtn.addEventListener("click", stop);
    resetBtn.addEventListener("click", function () {
        stop();
        remaining = DURATION;
        render();
        syncInput();
    });

    if (durationInput) {
        durationInput.addEventListener("change", function () {
            var v = parseInt(durationInput.value, 10);
            if (!isNaN(v) && v > 0) {
                running = false;
                DURATION = v * 60;
                remaining = DURATION;
                render();
                syncInput();
            }
        });
    }

    render();
    syncInput();
})();