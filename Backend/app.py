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

import collab

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


def user_name(user_id, users=None):
    """Resolve a user id to a display name (falls back to 'Unknown')."""
    users = users if users is not None else load_users()
    user = users.get(user_id)
    return user["name"] if user else "Unknown"


def member_names(slug):
    """Return a list of member display names for a subject's collaboration."""
    users = load_users()
    return [user_name(uid, users) for uid in collab.get_members(slug)]


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
    """Route decorator that redirects unauthenticated users to the welcome page."""
    from functools import wraps

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("welcome"))
        return view(*args, **kwargs)

    return wrapped


@app.get("/welcome")
def welcome():
    """Render the welcome splash shown before sign-in."""
    if session.get("user_id"):
        return redirect(url_for("home"))
    return render_template("welcome.html")


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
    flash(f"Welcome, {name}! Let's personalize your study planner.", "success")
    return redirect(url_for("onboarding"))


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


@app.post("/onboarding")
@login_required
def onboarding_post():
    """Save the new user's school, program, courses, and goals and finish onboarding."""
    user = current_user()
    users = load_users()

    school = request.form.get("school", "").strip()
    program = request.form.get("program", "").strip()
    courses = [c.strip() for c in request.form.get("courses", "").split(",") if c.strip()]
    goals = request.form.get("goals", "").strip()

    if not school or not program or not courses or not goals:
        flash("Please complete all steps.", "error")
        return redirect(url_for("onboarding"))

    users[user["id"]].update({
        "school": school,
        "program": program,
        "courses": courses,
        "goals": goals,
        "onboarded": True,
    })
    save_users(users)

    flash("You're all set! Your personalized planner is ready.", "success")
    return redirect(url_for("home"))


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
    if tool not in ("home", "resources", "assignments", "calendar", "grades", "collab", "quizzes"):
        tool = "home"

    users = load_users()
    user = current_user()
    is_member = collab.is_member(slug, user["id"]) if user else False
    shared_files = collab.get_materials(slug)

    return render_template(
        "subject.html",
        active_tool=tool,
        user=user,
        subject=subject_info,
        subjects=SUBJECTS,
        files=get_subject_files(subject_info),
        is_member=is_member,
        invite_code=collab.get_subject_code(slug),
        member_names=member_names(slug),
        shared_files=shared_files,
        user_name=user_name,
        quizzes=collab.list_quizzes(slug),
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
    collab.add_material(slug, uniqued, current_user()["id"])
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


@app.post("/subject/<slug>/collab/join")
@login_required
def collab_join(slug):
    """Join a subject's collaboration using its invite code."""
    subject_info = get_subject(slug)
    if not subject_info:
        flash("Subject not found.", "error")
        return redirect(url_for("home"))

    code = request.form.get("code", "").strip()
    user = current_user()

    if not code:
        flash("Please enter the collaboration code.", "error")
        return redirect(url_for("subject", slug=slug, tool="collab"))

    if not collab.subject_code_match(slug, code):
        flash("That code is not valid for this collaboration.", "error")
        return redirect(url_for("subject", slug=slug, tool="collab"))

    if collab.is_member(slug, user["id"]):
        flash("You are already part of this collaboration.", "success")
        return redirect(url_for("subject", slug=slug, tool="collab"))

    collab.add_member(slug, user["id"])
    flash(f"Joined the {subject_info['title']} collaboration!", "success")
    return redirect(url_for("subject", slug=slug, tool="collab"))


@app.post("/subject/<slug>/collab/leave")
@login_required
def collab_leave(slug):
    """Leave a subject's collaboration."""
    if not get_subject(slug):
        flash("Subject not found.", "error")
        return redirect(url_for("home"))

    collab.remove_member(slug, current_user()["id"])
    flash("You left the collaboration.", "success")
    return redirect(url_for("subject", slug=slug, tool="collab"))


@app.get("/quiz/take")
@login_required
def quiz_take_page():
    """A page to enter a quiz invite code and try it."""
    quizzes = None
    error = None
    if request.args.get("code"):
        quiz = collab.find_quiz_by_code(request.args.get("code"))
        if quiz:
            return redirect(url_for("quiz_take", quiz_id=quiz["id"]))
        error = "No quiz found for that code."
    return render_template("quiz_take.html", user=current_user(), subjects=SUBJECTS, error=error)


@app.get("/quiz/<quiz_id>")
@login_required
def quiz_take(quiz_id):
    """Render a quiz for a user to attempt."""
    quiz = collab.get_quiz(quiz_id)
    if not quiz:
        flash("Quiz not found.", "error")
        return redirect(url_for("home"))

    subject_info = get_subject(quiz["subject"])
    return render_template(
        "quiz.html",
        user=current_user(),
        subjects=SUBJECTS,
        quiz=quiz,
        subject=subject_info,
        user_name=user_name,
    )


@app.post("/quiz/<quiz_id>/attempt")
@login_required
def quiz_attempt(quiz_id):
    """Grade a submitted quiz attempt."""
    quiz = collab.get_quiz(quiz_id)
    if not quiz:
        flash("Quiz not found.", "error")
        return redirect(url_for("home"))

    if not collab.is_member(quiz["subject"], current_user()["id"]):
        flash("You must join the course collaboration to take this quiz.", "error")
        return redirect(url_for("quiz_take", quiz_id=quiz_id))

    score = 0
    total = len(quiz["questions"])
    for i, question in enumerate(quiz["questions"]):
        chosen = request.form.get(f"q{i}", "")
        if chosen.isdigit() and int(chosen) == question["correct"]:
            score += 1

    collab.add_attempt(quiz_id, current_user()["id"], score, total)
    flash(f"You scored {score} out of {total}.", "success")
    return redirect(url_for("quiz_take", quiz_id=quiz_id))


@app.post("/subject/<slug>/quiz")
@login_required
def quiz_create(slug):
    """Create a new quiz for a subject's collaboration."""
    subject_info = get_subject(slug)
    if not subject_info:
        flash("Subject not found.", "error")
        return redirect(url_for("home"))

    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()

    questions = []
    idx = 0
    while request.form.get(f"q{idx}_prompt"):
        prompt = request.form.get(f"q{idx}_prompt", "").strip()
        options = [request.form.get(f"q{idx}_opt{o}", "").strip() for o in range(4)]
        correct_raw = request.form.get(f"q{idx}_correct", "0")
        correct = int(correct_raw) if correct_raw.isdigit() and 0 <= int(correct_raw) < 4 else 0

        if prompt and any(options):
            questions.append(
                {
                    "prompt": prompt,
                    "options": options,
                    "correct": correct,
                }
            )
        idx += 1

    if not title or not questions:
        flash("Please provide a title and at least one complete question.", "error")
        return redirect(url_for("subject", slug=slug, tool="quizzes"))

    quiz = collab.create_quiz(slug, title, description, questions, current_user()["id"])
    flash(f"Quiz '{quiz['title']}' created. Share the invite code: {quiz['invite_code']}", "success")
    return redirect(url_for("subject", slug=slug, tool="quizzes"))


@app.get("/quiz/<quiz_id>/results")
@login_required
def quiz_results(quiz_id):
    """Show results/leaderboard for a quiz."""
    quiz = collab.get_quiz(quiz_id)
    if not quiz:
        flash("Quiz not found.", "error")
        return redirect(url_for("home"))

    subject_info = get_subject(quiz["subject"])
    users = load_users()
    attempts = collab.get_attempts(quiz_id)

    # Aggregate best score per user for the leaderboard.
    best = {}
    for a in attempts:
        uid = a["user_id"]
        if uid not in best or a["score"] > best[uid]["score"]:
            best[uid] = {"score": a["score"], "total": a["total"]}

    rows = [
        {
            "name": user_name(uid, users),
            "score": v["score"],
            "total": v["total"],
            "is_you": uid == current_user()["id"],
        }
        for uid, v in sorted(best.items(), key=lambda kv: (-kv[1]["score"], kv[0]))
    ]

    return render_template(
        "quiz_results.html",
        user=current_user(),
        subjects=SUBJECTS,
        quiz=quiz,
        subject=subject_info,
        rows=rows,
    )


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
