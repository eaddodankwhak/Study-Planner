"""Entry point for the Study Planner Flask application."""

from flask import Flask, render_template


app = Flask(
    __name__,
    template_folder="../Frontend/templates",
    static_folder="../Frontend/static",
)


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
