"""Entry point for the Study Planner Flask application.

Provides a Sakai-style interface with real authentication:
- /signup   create a new account (stored in SQLite)
- /login    sign in with existing credentials
- /logout   end the session
- /           public welcome landing
- /dashboard   home dashboard (subjects grid + planning panel) - login required
- /about, /task, /progress, /onboarding, /session, /notes - login required
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
import db
import planner
import stats
import ai as ai_pkg

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_DIR = os.path.join(BASE_DIR, "..", "Database")
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

# Register the AI Learning Hub API blueprint.
app.register_blueprint(ai_pkg.get_ai_blueprint())

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


def subject_slug(title):
    """Turn a course title into a stable URL slug."""
    slug = "".join(c if c.isalnum() else "-" for c in title.lower()).strip("-")
    return slug or "course"


def custom_subject(title, position):
    """Build a subject dict for a user-entered course title."""
    return {
        "slug": subject_slug(title),
        "code": f"CRS {100 + position:03d}",
        "title": title,
        "color": ("navy", "teal", "green", "purple", "orange", "red")[position % 6],
        "instructor": "To be assigned",
        "term": "Fall 2026",
        "description": f"Custom course: {title}",
    }


def user_subjects(user):
    """Return the subject cards for a user's onboarded courses.

    Preloaded subjects are matched by title; anything else becomes a custom
    course entry. Falls back to the full preloaded set when the user has no
    courses saved.
    """
    course_names = [c.strip() for c in (user or {}).get("courses", []) if c.strip()]

    if not course_names:
        return list(SUBJECTS)

    subjects = []
    for position, title in enumerate(course_names):
        matching = next(
            (s for s in SUBJECTS if s["title"].lower() == title.lower()), None
        )
        if matching:
            subjects.append(matching)
        else:
            subjects.append(custom_subject(title, position))
    return subjects


def get_subject(slug):
    """Return the subject dict for a slug, or None.

    Resolves both the preloaded SUBJECTS list and any custom courses that
    users entered during onboarding (rebuilt from the users database each call
    so newly-added courses become available immediately).
    """
    for s in SUBJECTS:
        if s["slug"] == slug:
            return s
    for user in load_users().values():
        for position, title in enumerate(user.get("courses", [])):
            if subject_slug(title) == slug:
                return custom_subject(title, position)
    return None


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
    """Return {user_id: user} for every account from the SQLite database."""
    return db.load_users()


def save_users(users):
    """Legacy compatibility helper.

    User writes now go through the db module directly; this is kept so any
    remaining callers that only ever read user data still resolve.
    """
    raise NotImplementedError("User writes are handled through the db module.")


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
    """Render the welcome splash."""
    return render_template("welcome.html", user=current_user())


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

    if db.get_user_by_email(email):
        flash("An account with that email already exists.", "error")
        return redirect(url_for("signup"))

    user_id = uuid.uuid4().hex
    db.create_user(user_id, name, email, generate_password_hash(password))

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

    user = db.get_user_by_email(email)

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
    return db.get_user(session.get("user_id"))


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

    school = request.form.get("school", "").strip()
    program = request.form.get("program", "").strip()
    courses = [c.strip() for c in request.form.get("courses", "").split(",") if c.strip()]
    goals = request.form.get("goals", "").strip()

    if not school or not program or not courses or not goals:
        flash("Please complete all steps.", "error")
        return redirect(url_for("onboarding"))

    available_hours = request.form.get("available_hours", "4").strip()
    try:
        available_hours = float(available_hours)
    except ValueError:
        available_hours = 4
    available_hours = max(1, min(12, available_hours))

    db.set_user_onboarded(user["id"], school, program, courses, goals, available_hours)

    flash("You're all set! Your personalized planner is ready.", "success")
    return redirect(url_for("home"))


@app.get("/")
def landing():
    """Landing page: always show the welcome splash."""
    return render_template("welcome.html", user=current_user())


@app.get("/dashboard")
@login_required
def home():
    """Render the planning dashboard plus the user's subject cards."""
    user = current_user()
    deadline_list = planner.list_deadlines(user["id"])
    task_list = planner.list_tasks(user["id"])
    checkpoint_data = _home_checkpoints(user, deadline_list, task_list)
    return render_template(
        "index.html",
        user=user,
        subjects=user_subjects(user),
        courses=planner.list_courses(user["id"]),
        deadlines=deadline_list,
        tasks=task_list,
        checkpoints=checkpoint_data,
    )


def _home_checkpoints(user, deadline_list, task_list):
    """Build the Home 'today' checkpoints: due-now tasks, open deadlines with
    backward-plan feasibility, and 'I'm behind' recovery flags."""
    today = planner._today().isoformat()
    overdue_tasks = [t for t in task_list if t.get("status") != "done"
                     and t.get("due_date") and t.get("due_date") < today]
    today_tasks = [t for t in task_list if t.get("status") != "done"
                   and t.get("due_date") == today]

    open_deadlines = [d for d in deadline_list if d.get("status") != "done"]
    deadline_reports = {}
    behind = []
    for d in open_deadlines:
        rep = planner.deadline_feasible(user["id"], d["id"])
        deadline_reports[d["id"]] = rep
        if rep and not rep["feasible"]:
            behind.append({**d, "_report": rep})

    return {
        "today": today,
        "overdue_tasks": overdue_tasks,
        "today_tasks": today_tasks,
        "deadline_reports": deadline_reports,
        "behind": behind,
        "open_deadlines": len(open_deadlines),
    }


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
        subjects=user_subjects(user),
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
    return render_template("quiz_take.html", user=current_user(), subjects=user_subjects(current_user()), error=error)


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
        subjects=user_subjects(current_user()),
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
        subjects=user_subjects(current_user()),
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
    user = current_user()
    return render_template("task.html", user=user, tasks=planner.list_tasks(user["id"]), courses=planner.list_courses(user["id"]))


@app.post("/task")
@login_required
def task_add():
    """Create a new task."""
    user = current_user()
    course_id = request.form.get("course_id")
    deadline_id = request.form.get("deadline_id")
    if deadline_id == "":
        deadline_id = None
    if course_id == "":
        course_id = None
    planner.add_task(
        user["id"],
        course_id=course_id,
        deadline_id=deadline_id,
        title=request.form.get("title", ""),
        due_date=request.form.get("due_date", ""),
        estimated_minutes=int(request.form.get("estimated_minutes") or 60),
        priority=request.form.get("priority", "medium"),
    )
    flash("Task added.", "success")
    return redirect(url_for("task"))


@app.post("/task/<task_id>/toggle")
@login_required
def task_toggle(task_id):
    """Flip a task between todo and done."""
    user = current_user()
    planner.toggle_task(user["id"], task_id)
    back = request.form.get("next") or url_for("task")
    return redirect(back)


# ---------------------------------------------------------------------------
# Courses
# ---------------------------------------------------------------------------

@app.get("/courses")
@login_required
def courses():
    """Render the course & semester management page."""
    user = current_user()
    return render_template("courses.html", user=user, courses=planner.list_courses(user["id"]))


@app.post("/courses")
@login_required
def courses_add():
    """Create a new course."""
    user = current_user()
    planner.create_course(
        user["id"],
        code=request.form.get("code", ""),
        title=request.form.get("title", ""),
        lecturer=request.form.get("lecturer", ""),
        credits=request.form.get("credits", 0),
        description=request.form.get("description", ""),
        schedule=request.form.get("schedule", ""),
        color=request.form.get("color", ""),
    )
    flash("Course added.", "success")
    return redirect(url_for("courses"))


@app.get("/courses/<course_id>")
@login_required
def course_detail(course_id):
    """Render a single course's detail page."""
    user = current_user()
    course = planner.get_course(user["id"], course_id)
    if not course:
        flash("Course not found.", "error")
        return redirect(url_for("courses"))
    deadline_list = planner.list_deadlines(user["id"])
    course_deadlines = [d for d in deadline_list if d.get("course_id") == course_id]
    course_tasks = [t for t in planner.list_tasks(user["id"]) if t.get("course_id") == course_id]
    course_notes = db.list_notes(user["id"], course_id=course_id)
    return render_template(
        "course_detail.html",
        user=user,
        course=course,
        deadlines=course_deadlines,
        tasks=course_tasks,
        notes=course_notes,
    )


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------

@app.get("/calendar")
@login_required
def calendar():
    """Render the academic calendar page."""
    user = current_user()
    return render_template("calendar.html", user=user, events=planner.list_events(user["id"]), courses=planner.list_courses(user["id"]))


@app.post("/calendar")
@login_required
def calendar_add():
    """Create a new calendar event."""
    user = current_user()
    course_id = request.form.get("course_id")
    if course_id == "":
        course_id = None
    planner.create_event(
        user["id"],
        type=request.form.get("type", "other"),
        title=request.form.get("title", ""),
        course_id=course_id,
        start=request.form.get("start", ""),
        duration_minutes=request.form.get("duration_minutes", 60),
        recurrence=request.form.get("recurrence", "none"),
        weekday=request.form.get("weekday", ""),
    )
    flash("Event added.", "success")
    return redirect(url_for("calendar"))


@app.post("/calendar/<event_id>/delete")
@login_required
def calendar_delete(event_id):
    """Delete a calendar event."""
    user = current_user()
    planner.delete_event(user["id"], event_id)
    flash("Event removed.", "success")
    return redirect(url_for("calendar"))


# ---------------------------------------------------------------------------
# Deadlines
# ---------------------------------------------------------------------------

@app.get("/deadlines")
@login_required
def deadlines():
    """Render the deadline & assessment manager."""
    user = current_user()
    return render_template("deadlines.html", user=user, deadlines=planner.list_deadlines(user["id"]), courses=planner.list_courses(user["id"]), courses_by_id={c["id"]: c for c in planner.list_courses(user["id"])})


@app.post("/deadlines")
@login_required
def deadlines_add():
    """Create a new deadline."""
    user = current_user()
    planner.create_deadline(
        user["id"],
        course_id=request.form.get("course_id"),
        title=request.form.get("title", ""),
        type=request.form.get("type", "assignment"),
        due_date=request.form.get("due_date", ""),
        weight=request.form.get("weight", 0),
        estimated_hours=request.form.get("estimated_hours", 0),
    )
    flash("Deadline added.", "success")
    return redirect(url_for("deadlines"))


@app.get("/deadlines/<deadline_id>")
@login_required
def deadline_detail(deadline_id):
    """Render a single deadline with its lead-up steps."""
    user = current_user()
    deadline = planner.get_deadline(user["id"], deadline_id)
    if not deadline:
        flash("Deadline not found.", "error")
        return redirect(url_for("deadlines"))
    course = planner.get_course(user["id"], deadline.get("course_id")) if deadline.get("course_id") else None
    all_tasks = {t["id"]: t for t in planner.list_tasks(user["id"])}
    steps = [all_tasks[tid] for tid in deadline.get("steps", []) if tid in all_tasks]
    report = planner.deadline_feasible(user["id"], deadline_id)
    return render_template(
        "deadline_detail.html",
        user=user,
        deadline=deadline,
        course=course,
        steps=steps,
        report=report,
        remaining_days=planner.remaining_hours,
    )


@app.post("/deadlines/<deadline_id>/step")
@login_required
def deadline_step_add(deadline_id):
    """Add a manual lead-up step to a deadline."""
    user = current_user()
    deadline = planner.get_deadline(user["id"], deadline_id)
    if not deadline:
        flash("Deadline not found.", "error")
        return redirect(url_for("deadlines"))
    planner.add_task(
        user["id"],
        deadline_id=deadline_id,
        course_id=deadline.get("course_id"),
        title=request.form.get("title", ""),
        due_date=request.form.get("due_date", deadline.get("due_date")),
        estimated_minutes=int(request.form.get("estimated_minutes") or 60),
        priority=request.form.get("priority", "medium"),
    )
    flash("Step added.", "success")
    return redirect(url_for("deadline_detail", deadline_id=deadline_id))


@app.post("/deadlines/<deadline_id>/complete")
@login_required
def deadline_complete(deadline_id):
    """Mark a deadline as completed."""
    user = current_user()
    planner.complete_deadline(user["id"], deadline_id)
    flash("Deadline marked complete.", "success")
    return redirect(url_for("deadlines"))


@app.post("/deadlines/<deadline_id>/plan")
@login_required
def deadline_plan(deadline_id):
    """Backward auto-plan a deadline into daily lead-up steps (Home checkpoint)."""
    user = current_user()
    if not planner.get_deadline(user["id"], deadline_id):
        flash("Deadline not found.", "error")
        return redirect(url_for("deadlines"))
    created = planner.auto_plan_deadline(user["id"], deadline_id)
    flash(f"Backward plan created: {len(created)} step(s) before the due date.", "success")
    return redirect(url_for("deadline_detail", deadline_id=deadline_id))


@app.post("/deadlines/<deadline_id>/recover")
@login_required
def deadline_recover(deadline_id):
    """'I'm behind' recovery: rebuild the lead-up plan into the time remaining."""
    user = current_user()
    if not planner.get_deadline(user["id"], deadline_id):
        flash("Deadline not found.", "error")
        return redirect(url_for("deadlines"))
    report = planner.recovery_plan(user["id"], deadline_id)
    if report and report.get("recovery"):
        flash("Recovery plan rebuilt to fit your remaining time.", "success")
    elif report:
        flash("This deadline is still on track — no recovery needed.", "info")
    else:
        flash("Recovery plan built.", "success")
    return redirect(url_for("deadline_detail", deadline_id=deadline_id))


@app.get("/progress")
@login_required
def progress():
    """Render the Progress overview with real computed statistics."""
    user = current_user()
    overview = stats.overview(user["id"], db, planner)
    return render_template("progress.html", user=user, stats=overview)


@app.get("/ai")
@login_required
def ai_hub():
    """Render the AI Learning Hub."""
    user = current_user()
    return render_template(
        "ai_hub.html",
        user=user,
        subjects=user_subjects(user),
    )


# ---------------------------------------------------------------------------
# Focus Timer + Study Sessions (Flow A: Home -> Task -> Timer -> Reflection)
# ---------------------------------------------------------------------------

@app.get("/session")
@login_required
def session_page():
    """Render the Focus Timer. Accepts ?task_id= or ?course_id= as context."""
    user = current_user()
    task_id = request.args.get("task_id")
    course_id = request.args.get("course_id")
    task = planner.get_task(user["id"], task_id) if task_id else None
    course = planner.get_course(user["id"], course_id) if course_id else None
    courses = planner.list_courses(user["id"])
    return render_template(
        "session.html",
        user=user,
        task=task,
        course=course,
        courses=courses,
        sessions=db.list_sessions(user["id"], limit=10),
    )


@app.post("/session/complete")
@login_required
def session_complete():
    """Record a completed study session and its reflection.

    If confidence is low (<= 2) the user is routed into Practice (quiz) so the
    core loop keeps momentum; otherwise they return where they started.
    """
    user = current_user()
    duration_minutes = request.form.get("duration_minutes", "0")
    try:
        duration_minutes = int(float(duration_minutes))
    except (TypeError, ValueError):
        duration_minutes = 0

    course_id = request.form.get("course_id") or None
    task_id = request.form.get("task_id") or None
    slug = request.form.get("slug") or None
    confidence = request.form.get("confidence")
    notes = request.form.get("notes") or None

    sess = db.create_session(
        user["id"],
        duration_minutes=duration_minutes,
        course_id=course_id,
        task_id=task_id,
        slug=slug,
    )
    try:
        conf = int(confidence)
    except (TypeError, ValueError):
        conf = None
    if conf is not None:
        db.set_session_reflection(sess["id"], user["id"], confidence=conf, notes=notes)

    flash("Session recorded. Nice work!", "success")

    # Route low-confidence, testable sessions into a quick quiz (Flow A).
    if conf is not None and conf <= 2:
        return redirect(url_for("quiz_take_page"))

    back = request.form.get("back") or request.referrer or url_for("dashboard")
    return redirect(back)


@app.post("/session/<session_id>/delete")
@login_required
def session_delete(session_id):
    user = current_user()
    db.delete_session(session_id, user["id"])
    flash("Session removed.", "success")
    back = request.form.get("back") or request.referrer or url_for("progress")
    return redirect(back)


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------

@app.get("/notes")
@login_required
def notes():
    """Render the Notes page (all of the user's study notes)."""
    user = current_user()
    courses = planner.list_courses(user["id"])
    by_id = {c["id"]: c for c in courses}
    note_list = db.list_notes(user["id"])
    for n in note_list:
        n["course_label"] = by_id.get(n.get("course_id"), {}).get("title", "")
    return render_template("notes.html", user=user, notes=note_list, courses=courses)


@app.post("/notes")
@login_required
def notes_create():
    """Create a new note."""
    user = current_user()
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()
    course_id = request.form.get("course_id") or None
    topic = request.form.get("topic", "").strip()
    if not title:
        flash("Please give the note a title.", "error")
    else:
        db.create_note(user["id"], title=title, body=body, course_id=course_id, topic=topic)
        flash("Note saved.", "success")
    back = request.form.get("back") or url_for("notes")
    return redirect(back)


@app.post("/notes/<note_id>/update")
@login_required
def notes_update(note_id):
    """Update a note's title/body/topic."""
    user = current_user()
    db.update_note(
        note_id,
        user["id"],
        title=request.form.get("title"),
        body=request.form.get("body"),
        topic=request.form.get("topic") or "",
    )
    flash("Note updated.", "success")
    back = request.form.get("back") or url_for("notes")
    return redirect(back)


@app.post("/notes/<note_id>/delete")
@login_required
def notes_delete(note_id):
    """Delete a note."""
    user = current_user()
    db.delete_note(note_id, user["id"])
    flash("Note deleted.", "success")
    back = request.form.get("back") or url_for("notes")
    return redirect(back)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
