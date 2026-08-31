"""Entry point for the Study Planner Flask application.

Provides a Sakai-style interface with real authentication:
- /signup   create a new account (stored in Database/users.json)
- /login    sign in with existing credentials
- /logout   end the session
- /           home dashboard (subjects grid) - login required
- /about, /task, /progress, /onboarding - login required
"""

import json
import os
import uuid

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_DIR = os.path.join(BASE_DIR, "..", "Database")
USERS_FILE = os.path.join(DATABASE_DIR, "users.json")

app = Flask(
    __name__,
    template_folder="../Frontend/templates",
    static_folder="../Frontend/static",
)

# Secret key for session cookies. In production, move this to an env variable.
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "study-planner-dev-secret")

# Sample subjects shown on the dashboard (Sakai-style course cards).
SUBJECTS = [
    {"code": "BIO 101", "title": "Animal Nutrition", "color": "navy"},
    {"code": "GEN 210", "title": "Genetics", "color": "teal"},
    {"code": "PLT 305", "title": "Plant Pathology", "color": "orange"},
    {"code": "CHE 150", "title": "General Chemistry", "color": "green"},
    {"code": "MAT 220", "title": "Calculus II", "color": "purple"},
    {"code": "PHY 240", "title": "Physics II", "color": "red"},
]


def load_users():
    """Load the users JSON file into a dict of {id: user}."""
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_users(users):
    """Persist the users dict to the JSON file."""
    os.makedirs(DATABASE_DIR, exist_ok=True)
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, indent=2)


def login_required(view):
    """Route decorator that redirects unauthenticated users to the login page."""
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


@app.get("/signup")
def signup():
    """Render the sign-up page."""
    return render_template("signup.html")


@app.post("/signup")
def signup_post():
    """Create a new user account."""
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if not name or not email or not password:
        flash("Please fill in all fields.", "error")
        return redirect(url_for("signup"))

    users = load_users()
    if any(u["email"] == email for u in users.values()):
        flash("An account with that email already exists.", "error")
        return redirect(url_for("signup"))

    user_id = uuid.uuid4().hex
    users[user_id] = {
        "id": user_id,
        "name": name,
        "email": email,
        "password": generate_password_hash(password),
    }
    save_users(users)

    session["user_id"] = user_id
    flash(f"Welcome, {name}! Your study planner is ready.", "success")
    return redirect(url_for("home"))


@app.get("/login")
def login():
    """Render the login page."""
    if session.get("user_id"):
        return redirect(url_for("home"))
    return render_template("login.html")


@app.post("/login")
def login_post():
    """Authenticate a user and start a session."""
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    users = load_users()
    user = next((u for u in users.values() if u["email"] == email), None)

    if user and check_password_hash(user["password"], password):
        session["user_id"] = user["id"]
        flash(f"Welcome back, {user['name']}!", "success")
        return redirect(url_for("home"))

    flash("Invalid email or password.", "error")
    return redirect(url_for("login"))


@app.get("/logout")
def logout():
    """End the user's session."""
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


def current_user():
    """Return the logged-in user dict, or None."""
    users = load_users()
    return users.get(session.get("user_id"))


@app.get("/onboarding")
@login_required
def onboarding():
    """Render the onboarding page for new users."""
    return render_template("onboarding.html", user=current_user())


@app.get("/")
@login_required
def home():
    """Render the subject dashboard."""
    return render_template("index.html", user=current_user(), subjects=SUBJECTS)


@app.get("/about")
@login_required
def about():
    """Render the About page."""
    return render_template("about.html", user=current_user())


@app.get("/task")
@login_required
def task():
    """Render the Task page."""
    return render_template("task.html", user=current_user())


@app.get("/progress")
@login_required
def progress():
    """Render the Progress page."""
    return render_template("progress.html", user=current_user())


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
