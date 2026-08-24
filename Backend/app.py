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
