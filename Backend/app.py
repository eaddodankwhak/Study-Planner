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
    send_from_directory,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_DIR = os.path.join(BASE_DIR, "..", "Database")
USERS_FILE = os.path.join(DATABASE_DIR, "users.json")
UPLOADS_DIR = os.path.join(DATABASE_DIR, "uploads")

# Allowed file types for course materials (slides, PDFs, docs, etc.)
ALLOWED_EXTENSIONS = {
    "pdf", "ppt", "pptx", "doc", "docx", "xls", "xlsx",
    "txt", "md", "csv", "png", "jpg", "jpeg", "mp4", "zip",
}

app = Flask(
    __name__,
    template_folder="../Frontend/templates",
    static_folder="../Frontend/static",
)

# Secret key for session cookies. In production, move this to an env variable.
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "study-planner-dev-secret")

# Sample subjects shown on the dashboard (Sakai-style course cards).
SUBJECTS = [
    {
        "slug": "animal-nutrition",
        "code": "BIO 101",
        "title": "Animal Nutrition",
        "color": "navy",
        "instructor": "Dr. K. Mensah",
        "term": "Fall 2026",
        "description": "Principles of animal nutrition, feed composition, and dietary requirements.",
    },
    {
        "slug": "genetics",
        "code": "GEN 210",
        "title": "Genetics",
        "color": "teal",
        "instructor": "Prof. A. Osei",
        "term": "Fall 2026",
        "description": "Mendelian and molecular genetics, inheritance patterns, and gene expression.",
    },
    {
        "slug": "plant-pathology",
        "code": "PLT 305",
        "title": "Plant Pathology",
        "color": "orange",
        "instructor": "Dr. E. Adjei",
        "term": "Fall 2026",
        "description": "Plant disease identification, causes, and management strategies.",
    },
    {
        "slug": "general-chemistry",
        "code": "CHE 150",
        "title": "General Chemistry",
        "color": "green",
        "instructor": "Dr. S. Boateng",
        "term": "Fall 2026",
        "description": "Atomic structure, bonding, stoichiometry, and basic chemical reactions.",
    },
    {
        "slug": "calculus-ii",
        "code": "MAT 220",
        "title": "Calculus II",
        "color": "purple",
        "instructor": "Prof. J. Amoah",
        "term": "Fall 2026",
        "description": "Integration techniques, sequences, series, and applications.",
    },
    {
        "slug": "physics-ii",
        "code": "PHY 240",
        "title": "Physics II",
        "color": "red",
        "instructor": "Dr. R. Nkrumah",
        "term": "Fall 2026",
        "description": "Electricity, magnetism, circuits, and electromagnetic waves.",
    },
]


def get_subject(slug):
    """Return the subject dict for a slug, or None."""
    return next((s for s in SUBJECTS if s["slug"] == slug), None)


def get_subject_files(subject):
    """Return a sorted list of uploaded material filenames for a subject."""
    folder = os.path.join(UPLOADS_DIR, subject["slug"])
    if not os.path.isdir(folder):
        return []
    return sorted(os.listdir(folder))


def allowed_file(filename):
    """Return True if the file extension is allowed."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


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


@app.get("/subject/<slug>")
@login_required
def subject(slug):
    """Render a Sakai-style page for a single subject."""
    subject_info = get_subject(slug)
    if not subject_info:
        flash("Subject not found.", "error")
        return redirect(url_for("home"))

    tool = request.args.get("tool", "home")
    if tool not in ("home", "resources", "assignments", "calendar", "grades"):
        tool = "home"

    return render_template(
        "subject.html",
        active_tool=tool,
        user=current_user(),
        subject=subject_info,
        subjects=SUBJECTS,
        files=get_subject_files(subject_info),
    )


@app.post("/subject/<slug>/upload")
@login_required
def subject_upload(slug):
    """Upload a course material file for a subject."""
    subject_info = get_subject(slug)
    if not subject_info:
        flash("Subject not found.", "error")
        return redirect(url_for("home"))

    file = request.files.get("material")
    if not file or file.filename == "":
        flash("Please choose a file to upload.", "error")
        return redirect(url_for("subject", slug=slug, tool="resources"))

    if not allowed_file(file.filename):
        flash("That file type is not allowed.", "error")
        return redirect(url_for("subject", slug=slug, tool="resources"))

    folder = os.path.join(UPLOADS_DIR, slug)
    os.makedirs(folder, exist_ok=True)

    filename = secure_filename(file.filename)
    dest = os.path.join(folder, filename)

    # Avoid overwriting an existing file.
    uniqued = filename
    count = 1
    while os.path.exists(os.path.join(folder, uniqued)):
        stem, ext = os.path.splitext(filename)
        uniqued = f"{stem}_{count}{ext}"
        count += 1

    file.save(os.path.join(folder, uniqued))
    flash(f"Uploaded '{uniqued}' successfully.", "success")
    return redirect(url_for("subject", slug=slug, tool="resources"))


@app.get("/subject/<slug>/download/<path:filepath>")
@login_required
def subject_download(slug, filepath):
    """Serve an uploaded course material file for download."""
    subject_info = get_subject(slug)
    if not subject_info:
        flash("Subject not found.", "error")
        return redirect(url_for("home"))

    folder = os.path.join(UPLOADS_DIR, slug)
    return send_from_directory(folder, filepath, as_attachment=True)


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
