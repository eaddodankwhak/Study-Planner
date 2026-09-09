(function () {
    "use strict";

    var questions = [].slice.call(document.querySelectorAll("[data-question]"));
    if (!questions.length) return;

    var index = 0;
    var timer = document.querySelector("#quiz-timer");
    var form = document.querySelector("#personal-quiz-form");
    var warned = false;

    function draw() {
        questions.forEach(function (q, i) {
            q.classList.toggle("is-current", i === index);
        });
        document.querySelector("[data-progress]").textContent = (index + 1) + " / " + questions.length;
        document.querySelector("[data-previous]").disabled = index === 0;
        document.querySelector("[data-next]").hidden = index === questions.length - 1;
        document.querySelector("[data-submit]").hidden = index !== questions.length - 1;
    }

    document.querySelector("[data-next]").onclick = function () {
        index = Math.min(index + 1, questions.length - 1);
        draw();
    };

    document.querySelector("[data-previous]").onclick = function () {
        index = Math.max(index - 1, 0);
        draw();
    };

    var remaining = Number(timer.dataset.seconds);

    function finish() {
        form.submit();
    }

    function tick() {
        var m = Math.floor(remaining / 60);
        var s = remaining % 60;
        var display = m + ":" + String(s).padStart(2, "0");
        timer.textContent = display;
        timer.setAttribute("aria-live", "polite");

        if (remaining > 0 && remaining <= 30) {
            timer.classList.add("is-warning");
            if (!warned) {
                warned = true;
                timer.setAttribute("title", "Time is almost up — finish and submit your answers.");
            }
        }

        if (remaining <= 0) {
            if (window.confirm("Time is up! Submit your answers now?")) {
                finish();
                return;
            }
            // Decline once: grant a 2-minute grace period instead of locking out.
            remaining += 120;
            warned = true;
        }

        remaining -= 1;
        setTimeout(tick, 1000);
    }

    draw();
    tick();
}());