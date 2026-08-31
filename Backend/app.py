"""Entry point for the Study Planner Flask application."""

from flask import Flask, render_template


app = Flask(
    __name__,
    template_folder="../Frontend/templates",
    static_folder="../Frontend/static",
)


@app.get("/onboarding")
def onboarding():
    """Render the onboarding page for new users."""
    return render_template("onboarding.html")


@app.get("/")
def home():
    """Render the first Study Planner page."""
    return render_template("index.html")


@app.get("/about")
def about():
    """Render the About page."""
    return render_template("about.html")


@app.get("/task")
def task():
    """Render the Task page."""
    return render_template("task.html")


@app.get("/progress")
def progress():
    """Render the Progress page."""
    return render_template("progress.html")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
