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
import re
import time
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
import courses as courses_mod
import db
import planner
import stats
import ai as ai_pkg
import collaboration as collab_pkg

# Ensure tables and any lightweight migrations (e.g. notify_digest) exist.
db.init_db()

# ---------------------------------------------------------------------------
# Login brute-force protection (in-memory, single-process)
# ---------------------------------------------------------------------------
LOGIN_MAX_ATTEMPTS = int(os.environ.get("LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_WINDOW_SECONDS = int(os.environ.get("LOGIN_WINDOW_SECONDS", "300"))
LOGIN_LOCKOUT_SECONDS = int(os.environ.get("LOGIN_LOCKOUT_SECONDS", "300"))
_login_track = {}  # key -> {"fails": [(ts,...)], "locked_until": ts}


def _login_key(email, ip):
    return f"{email}|{ip}"


def _login_blocked(email, ip):
    rec = _login_track.get(_login_key(email, ip))
    if not rec:
        return False, 0
    now = time.time()
    lock_until = rec.get("locked_until", 0)
    if lock_until > now:
        return True, int(lock_until - now)
    return False, 0


def _record_login_failure(email, ip):
    key = _login_key(email, ip)
    now = time.time()
    rec = _login_track.get(key, {"fails": [], "locked_until": 0})
    rec["fails"] = [ts for ts in rec["fails"] if now - ts < LOGIN_WINDOW_SECONDS]
    rec["fails"].append(now)
    if len(rec["fails"]) >= LOGIN_MAX_ATTEMPTS:
        rec["locked_until"] = now + LOGIN_LOCKOUT_SECONDS
        rec["fails"] = []
    _login_track[key] = rec
    return rec


def _clear_login_failures(email, ip):
    _login_track.pop(_login_key(email, ip), None)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_DIR = os.path.join(BASE_DIR, "..", "Database")
UPLOADS_DIR = os.path.join(DATABASE_DIR, "uploads")
PROFILE_UPLOADS_DIR = os.path.join(DATABASE_DIR, "profile_uploads")
MAX_PROFILE_IMAGE_BYTES = 5 * 1024 * 1024

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

# Register the Collaborative Workspace blueprint.
app.register_blueprint(collab_pkg.get_collaboration_blueprint())

# ---------------------------------------------------------------------------
# AI assistant quick-start affordances (shared by the floating launcher drawer
# and the full /ai-hub page — one server-rendered set, never duplicated).
# ---------------------------------------------------------------------------
AI_QUICK_ACTIONS = [
    {"label": "Explain a topic", "template": "Explain {subject} to me like I'm a beginner."},
    {"label": "Summarize notes", "template": "Summarize the key points of photosynthesis."},
    {"label": "Solve a question", "template": "Solve the following step by step: integrate x^2 from 0 to 3."},
    {"label": "Generate a quiz", "template": "Create 5 quiz questions about the periodic table."},
    {"label": "Create flashcards", "template": "Make flashcards for the key terms in genetics."},
    {"label": "Build a study plan", "template": "Make me a 1-week study plan for my statistics exam."},
]

AI_PROVIDERS = [
    {"id": "claude", "name": "Claude", "tagline": "Deep reasoning and clear explanations."},
    {"id": "gpt", "name": "GPT", "tagline": "Versatile general-purpose study assistant."},
    {"id": "gemini", "name": "Gemini", "tagline": "Great for multimodal and broad study tasks."},
]


@app.context_processor
def inject_ai_launcher():
    """Give every rendered page the AI assistant launcher data (quick actions
    and provider list are the single source of truth shared by drawer and page)."""
    return {
        "quick_actions": AI_QUICK_ACTIONS,
        "providers": AI_PROVIDERS,
        "user_settings": db.get_settings(session["user_id"]) if session.get("user_id") else None,
    }

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


def subject_slug(value):
    """Turn a course code or title into a stable URL slug."""
    slug = "".join(c if c.isalnum() else "-" for c in str(value).lower()).strip("-")
    return slug or "course"


def custom_subject(row):
    """Build a Sakai-style subject dict from a row of the shared courses table.

    The row's real data only — no invented identifiers like "CRS 100".
    """
    code = row.get("course_code") or row.get("code") or ""
    return {
        "id": row.get("id"),
        "slug": subject_slug(code),
        "code": code,
        "title": row.get("title") or "To be assigned",
        "color": row.get("color") or courses_mod.auto_color(code),
        "instructor": row.get("lecturer") or "To be assigned",
        "term": row.get("term") or "Fall 2026",
        "description": row.get("description") or f"Course {code}",
    }


def user_subjects(user):
    """Return the subject cards for a user's dashboard.

    Reads the shared courses table — exactly the rows the /courses page lists —
    so dashboard cards and the /courses grid are the same data, rendered from
    the same query, and cannot drift apart again.
    """
    return [
        custom_subject(row)
        for row in courses_mod.get_user_courses((user or {}).get("id"))
    ]


def get_subject(slug):
    """Return the subject dict for a slug, or None.

    Resolves both the preloaded SUBJECTS list and any course rows from the
    shared courses table (rebuilt each call so newly-added courses become
    available immediately).
    """
    for s in SUBJECTS:
        if s["slug"] == slug:
            return s
    for row in courses_mod.all_courses():
        if subject_slug(row["course_code"]) == slug:
            return custom_subject(row)
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


def parse_multiple_choice_pdf(content):
    """Extract common numbered A-D question blocks from a text-based PDF."""
    try:
        import PyPDF2
    except ImportError:
        raise ValueError("PDF quiz import needs PyPDF2. Install the project requirements and try again.")

    try:
        reader = PyPDF2.PdfReader(__import__("io").BytesIO(content))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        raise ValueError("This PDF could not be read. Use a text-based PDF rather than a scan.") from exc

    questions = []
    blocks = re.findall(r"(?ms)^\s*\d{1,3}[.)]\s*(.+?)(?=^\s*\d{1,3}[.)]|\Z)", text)
    for block in blocks:
        option_matches = list(re.finditer(r"(?mi)^\s*([A-D])[.)]\s*(.+?)(?=^\s*[A-D][.)]|\Z)", block))
        if len(option_matches) < 2:
            continue
        prompt = block[:option_matches[0].start()].strip()
        options = [{"label": match.group(1).upper(), "text": " ".join(match.group(2).split())} for match in option_matches]
        if prompt and all(option["text"] for option in options):
            questions.append({"prompt": " ".join(prompt.split()), "options": options})
    if not questions:
        raise ValueError("No multiple-choice questions were found. Format questions as 1. ... followed by A. ... B. ...")
    return questions


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
    return redirect(url_for("onboarding"))


@app.get("/login")
def login():
    """Render the login page."""
    if session.get("user_id"):
        return redirect(url_for("home"))
    return render_template("login.html")


@app.post("/login")
def login_post():
    """Authenticate a user and start a session (rate-limited)."""
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    ip = request.remote_addr or "unknown"

    blocked, wait = _login_blocked(email, ip)
    if blocked:
        flash(f"Too many attempts. Try again in {wait} seconds.", "error")
        return redirect(url_for("login"))

    user = db.get_user_by_email(email)

    if user and check_password_hash(user["password"], password):
        _clear_login_failures(email, ip)
        session["user_id"] = user["id"]
        flash(f"Welcome back, {user['name']}!", "success")
        return redirect(url_for("home"))

    _record_login_failure(email, ip)
    flash("Invalid email or password.", "error")
    return redirect(url_for("login"))


@app.get("/logout")
def logout():
    """End the user's session."""
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


@app.get("/settings")
@login_required
def settings():
    """Render the account settings surface and its persisted preferences."""
    user = current_user()
    return render_template(
        "settings.html",
        user=user,
        active_nav="settings",
        settings=db.get_settings(user["id"]),
        workspaces=db.collab_workspaces_for(user["id"]),
    )


@app.post("/settings/profile")
@login_required
def settings_profile():
    """Update the editable profile fields."""
    user = current_user()
    name = request.form.get("name", "").strip()
    display_name = request.form.get("display_name", "").strip()
    email = request.form.get("email", "").strip().lower()
    if not name or not email:
        flash("Full name and email are required.", "error")
        return redirect(url_for("settings"))
    existing = db.get_user_by_email(email)
    if existing and existing["id"] != user["id"]:
        flash("That email address is already in use.", "error")
        return redirect(url_for("settings"))
    db.update_user(user["id"], {"name": name, "display_name": display_name or None, "email": email})
    flash("Profile saved.", "success")
    return redirect(url_for("settings"))


@app.post("/settings/preferences")
@login_required
def settings_preferences():
    """Persist the settings panels as one validated patch."""
    user = current_user()
    allowed = db.get_settings(user["id"])
    notifications = {
        "deadlines": request.form.get("deadlines") == "on",
        "deadline_days": request.form.get("deadline_days", "3"),
        "weekly_digest": request.form.get("weekly_digest") == "on",
        "ai_suggestions": request.form.get("ai_suggestions") == "on",
        "workspace_activity": request.form.get("workspace_activity") == "on",
        "channel": request.form.get("channel", "in-app"),
    }
    if notifications["deadline_days"] not in {"1", "3", "7"}:
        notifications["deadline_days"] = allowed["notifications"]["deadline_days"]
    if notifications["channel"] not in {"email", "push", "in-app"}:
        notifications["channel"] = "in-app"
    updates = {}
    notification_keys = {"deadlines", "weekly_digest", "ai_suggestions", "workspace_activity", "deadline_days", "channel"}
    if notification_keys.intersection(request.form):
        updates["notifications"] = notifications
    if {"weekly_hours", "focus_minutes", "spaced_repetition", "planning_aggressiveness"}.intersection(request.form):
        try:
            weekly_hours = max(1, min(80, int(request.form.get("weekly_hours", 4))))
            focus_minutes = max(5, min(120, int(request.form.get("focus_minutes", 25))))
        except (TypeError, ValueError):
            weekly_hours, focus_minutes = 4, 25
        updates["study"] = {
            "weekly_hours": weekly_hours,
            "focus_minutes": focus_minutes,
            "spaced_repetition": request.form.get("spaced_repetition", "balanced"),
            "planning_aggressiveness": request.form.get("planning_aggressiveness", "balanced"),
        }
    if {"theme", "text_size", "reduce_motion"}.intersection(request.form):
        updates["appearance"] = {
            "theme": request.form.get("theme", "system"),
            "text_size": request.form.get("text_size", "default"),
            "reduce_motion": request.form.get("reduce_motion") == "on",
        }
    if {"ai_activity", "workspace_visibility"}.intersection(request.form):
        updates["privacy"] = {
            "ai_activity": request.form.get("ai_activity") == "on",
            "workspace_visibility": request.form.get("workspace_visibility", "members"),
        }
    db.update_settings(user["id"], updates)
    flash("Settings saved.", "success")
    return redirect(url_for("settings"))


@app.post("/settings/avatar")
@login_required
def settings_avatar():
    """Store a small profile image locally; replace/remove is immediately visible."""
    user = current_user()
    uploaded = request.files.get("avatar")
    remove = request.form.get("remove") == "1"
    folder = PROFILE_UPLOADS_DIR
    os.makedirs(folder, exist_ok=True)
    if remove:
        db.update_user(user["id"], {"avatar_path": None})
        flash("Profile photo removed.", "success")
        return redirect(url_for("settings"))
    if not uploaded or not uploaded.filename:
        flash("Choose an image first.", "error")
        return redirect(url_for("settings"))
    if uploaded.mimetype not in {"image/jpeg", "image/png", "image/gif", "image/webp"}:
        flash("Please choose a JPG, PNG, GIF, or WebP image.", "error")
        return redirect(url_for("settings"))
    uploaded.stream.seek(0, os.SEEK_END)
    size = uploaded.stream.tell()
    uploaded.stream.seek(0)
    if size > MAX_PROFILE_IMAGE_BYTES:
        flash("That image is larger than 5 MB. Please choose a smaller file.", "error")
        return redirect(url_for("settings"))
    extension = secure_filename(uploaded.filename).rsplit(".", 1)[-1].lower() if "." in uploaded.filename else {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/gif": "gif",
        "image/webp": "webp",
    }[uploaded.mimetype]
    filename = f"{user['id']}.{extension}"
    uploaded.save(os.path.join(folder, filename))
    db.update_user(user["id"], {"avatar_path": filename})
    flash("Profile photo updated.", "success")
    return redirect(url_for("settings"))


@app.get("/profile/avatar/<path:filename>")
@login_required
def profile_avatar(filename):
    """Serve only the current user's stored avatar."""
    user = current_user()
    if filename != user.get("avatar_path"):
        return ("", 404)
    return send_from_directory(PROFILE_UPLOADS_DIR, filename)


@app.post("/settings/notifications")
@login_required
def settings_notifications():
    """Toggle the weekly digest notification preference."""
    user = current_user()
    wanted = request.form.get("notify_digest") == "on"
    db.update_settings(user["id"], {"notifications": {"weekly_digest": wanted}})
    db.log_audit(user["id"], "notification_pref", "weekly_digest=on" if wanted else "weekly_digest=off")
    flash("Notification preferences updated.", "success")
    return redirect(url_for("settings"))


def _week_context(user_id):
    """Compute the next-7-days digest summary for a user (deadlines, tasks, effort)."""
    from datetime import datetime as _dt, timedelta as _td
    today = _dt.now().date()
    week = today + _td(days=7)

    deadlines = planner.list_deadlines(user_id)
    upcoming_deadlines = [
        d for d in deadlines
        if d.get("status") != "done" and planner.parse_date(d.get("due_date"))
        and today <= planner.parse_date(d.get("due_date")) <= week
    ]
    tasks = planner.list_tasks(user_id)
    upcoming_tasks = [
        t for t in tasks
        if t.get("status") != "done" and t.get("due_date") and today <= planner.parse_date(t.get("due_date")) <= week
    ]
    courses = planner.list_courses(user_id)
    by_id = {c["id"]: c for c in courses}
    upcoming_deadlines.sort(key=lambda d: d.get("due_date", ""))
    upcoming_tasks.sort(key=lambda t: t.get("due_date", ""))
    total_minutes = sum(int(t.get("estimated_minutes") or 0) for t in upcoming_tasks)
    return {
        "week_end": week.isoformat(),
        "upcoming_deadlines": upcoming_deadlines,
        "upcoming_tasks": upcoming_tasks,
        "by_id": by_id,
        "total_minutes": total_minutes,
    }


@app.get("/export")
@login_required
def export_data():
    """Download the user's data as a JSON bundle (GDPR-style data export)."""
    from flask import Response, jsonify as _jsonify

    user = current_user()
    bundle = {
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "profile": user,
        "planner": planner.load_all().get(user["id"], {}),
        "notes": db.list_notes(user["id"]),
        "sessions": db.list_sessions(user["id"]),
        "ai_conversations": db.ai_list_conversations(user["id"]) if hasattr(db, "ai_list_conversations") else [],
        "ai_usage": db.ai_get_usage(user["id"]) if hasattr(db, "ai_get_usage") else None,
        "attempts": [
            dict(r) for r in db.connect().execute(
                "SELECT * FROM attempts WHERE user_id = ?", (user["id"],)
            ).fetchall()
        ] if _column_exists("attempts") else [],
    }
    payload = json.dumps(bundle, indent=2, default=str)
    filename = "study-planner-export.json"
    return Response(
        payload,
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.post("/settings/delete")
@login_required
def delete_account():
    """Permanently delete the account and all associated data."""
    user = current_user()
    confirmation = request.form.get("confirmation", "").strip()
    if confirmation not in {user["email"], "DELETE"}:
        flash("Type your account email or DELETE before removing the account.", "error")
        return redirect(url_for("settings"))
    db.log_audit(user["id"], "account_delete")
    db.delete_user(user["id"])
    session.clear()
    flash("Your account and data have been permanently deleted.", "success")
    return redirect(url_for("welcome"))


def _column_exists(table):
    try:
        conn = db.connect()
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (table,)
        ).fetchall()
        conn.close()
        return len(rows) > 0
    except Exception:
        return False


@app.get("/calendar/export")
@login_required
def calendar_export():
    """Download the user's deadlines and events as an .ics calendar feed."""
    from flask import Response

    user = current_user()
    from datetime import datetime as _dt

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Study Planner//Study Planner//EN",
        "CALSCALE:GREGORIAN",
    ]
    for d in planner.list_deadlines(user["id"]):
        due = planner.parse_date(d.get("due_date"))
        if not due:
            continue
        due_dt = _dt.combine(due, _dt.min.time())
        uid = f"deadline-{d['id']}@studyplanner"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{_dt.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART;VALUE=DATE:{due.strftime('%Y%m%d')}",
            f"SUMMARY:{_ics_esc(d.get('title', 'Deadline'))}",
            f"DESCRIPTION:{_ics_esc(str(d.get('weight', '')))}% weight deadline",
            "END:VEVENT",
        ]
    for e in planner.list_events(user["id"]):
        start = e.get("start", "")
        if not start:
            continue
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:event-{e['id']}@studyplanner")
        lines.append(f"DTSTAMP:{_dt.utcnow().strftime('%Y%m%dT%H%M%SZ')}")
        lines.append(f"DTSTART:{_ics_dt(start)}")
        lines.append(f"SUMMARY:{_ics_esc(e.get('title', 'Event'))}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    payload = "\r\n".join(lines) + "\r\n"
    return Response(
        payload,
        mimetype="text/calendar",
        headers={"Content-Disposition": "attachment; filename=study-planner.ics"},
    )


def _ics_esc(value):
    return (value or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")


def _ics_dt(value):
    """Best-effort conversion of a 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD' to iCal."""
    from datetime import datetime as _dt
    v = (value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            d = _dt.strptime(v, fmt)
            if fmt == "%Y-%m-%d":
                return d.strftime("%Y%m%d")
            return d.strftime("%Y%m%dT%H%M%S")
        except ValueError:
            continue
    return _dt.utcnow().strftime("%Y%m%dT%H%M%SZ")


@app.get("/report")
@login_required
def report():
    """Print-friendly progress report (add ?print=1 or use browser print)."""
    user = current_user()
    overview = stats.overview(user["id"], db, planner)
    courses = planner.list_courses(user["id"])
    deadlines = planner.list_deadlines(user["id"])
    open_deadlines = [d for d in deadlines if d.get("status") != "done"]
    return render_template(
        "report.html",
        user=user,
        stats=overview,
        courses=courses,
        deadlines=open_deadlines,
        generated=time.strftime("%Y-%m-%d %H:%M"),
    )


def current_user():
    """Return the logged-in user dict, or None."""
    return db.get_user(session.get("user_id"))


@app.get("/onboarding")
@login_required
def onboarding():
    """Render the onboarding wizard; ?step= lets bookmarking/back keep position."""
    try:
        step = int(request.args.get("step", "1"))
    except ValueError:
        step = 1
    step = max(1, min(5, step))
    return render_template("onboarding.html", user=current_user(), current_step=step)


# Which step's text field gates advancing to the next step. Availability
# (step 5) is an optional number and never blocks an advance or completion.
_STEP_REQUIRED = {1: "school", 2: "program", 3: "courses", 4: "goals"}


@app.post("/onboarding")
@login_required
def onboarding_post():
    """Wizard-style POST: save a draft, advance/finish, or bail out with a draft kept."""
    user = current_user()
    user_id = user["id"]

    school = request.form.get("school", "").strip()
    program = request.form.get("program", "").strip()
    courses = [c.strip() for c in request.form.get("courses", "").split(",") if c.strip()]
    goals = request.form.get("goals", "").strip()

    available_hours = request.form.get("available_hours", "4").strip()
    try:
        available_hours = float(available_hours)
    except ValueError:
        available_hours = 4
    available_hours = max(1, min(12, available_hours))

    # Always persist the draft — "save and finish later" must never lose work.
    db.save_onboarding_progress(user_id, school, program, courses, goals, available_hours)

    # Every Step 3 code becomes a real row in the shared courses table (a stub
    # with no title -> the dashboard shows "To be assigned", which the /courses
    # form completes later). Re-joining is a no-op thanks to the upsert.
    for code in courses:
        courses_mod.upsert_course(user_id, code)

    action = request.form.get("action", "continue")
    if action == "finish-later":
        return redirect(url_for("home"))

    try:
        step = int(request.form.get("step", "1"))
    except ValueError:
        step = 1
    step = max(1, min(5, step))

    if step >= 5:
        if not school or not program or not courses or not goals:
            flash("Let's wrap up — please finish the earlier steps first.", "error")
            return redirect(url_for("onboarding", step=5))
        db.set_user_onboarded(user_id, school, program, courses, goals, available_hours)
        flash("You're all set! Your personalized planner is ready.", "success")
        return redirect(url_for("home"))

    required = _STEP_REQUIRED[step]
    if not request.form.get(required, "").strip():
        flash("That field is required — let's fill it in before moving on.", "error")
        return redirect(url_for("onboarding", step=step))

    return redirect(url_for("onboarding", step=step + 1))


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
        active_nav="home",
        subjects=user_subjects(user),
        courses=planner.list_courses(user["id"]),
        deadlines=deadline_list,
        tasks=task_list,
        checkpoints=checkpoint_data,
        workspaces=db.collab_workspaces_for(user["id"]) if hasattr(db, "collab_workspaces_for") else [],
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
        active_nav="courses",
        subject=subject_info,
        subjects=user_subjects(user),
        files=get_subject_files(subject_info),
        is_member=is_member,
        invite_code=collab.get_subject_code(slug),
        member_names=member_names(slug),
        shared_files=shared_files,
        user_name=user_name,
        quizzes=collab.list_quizzes(slug),
        personal_quizzes=db.list_personal_quizzes(user["id"], slug),
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


@app.post("/subject/<slug>/personal-quiz/import")
@login_required
def personal_quiz_import(slug):
    if not get_subject(slug):
        flash("Course not found.", "error")
        return redirect(url_for("home"))
    uploaded = request.files.get("question_pdf")
    if not uploaded or not uploaded.filename or not uploaded.filename.lower().endswith(".pdf"):
        flash("Choose a PDF containing numbered multiple-choice questions.", "error")
        return redirect(url_for("subject", slug=slug, tool="quizzes"))
    content = uploaded.read()
    if len(content) > 12 * 1024 * 1024:
        flash("Keep the PDF below 12 MB.", "error")
        return redirect(url_for("subject", slug=slug, tool="quizzes"))
    try:
        questions = parse_multiple_choice_pdf(content)
        duration = max(1, min(int(request.form.get("duration_minutes") or 30), 180))
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("subject", slug=slug, tool="quizzes"))
    title = request.form.get("title", "").strip() or uploaded.filename.rsplit(".", 1)[0]
    quiz = db.create_personal_quiz(current_user()["id"], slug, title, duration, questions)
    flash(f"Imported {len(questions)} questions. Your answer key will be requested after the attempt.", "success")
    return redirect(url_for("personal_quiz_take", quiz_id=quiz["id"]))


@app.get("/personal-quiz/<quiz_id>")
@login_required
def personal_quiz_take(quiz_id):
    quiz = db.get_personal_quiz(quiz_id, current_user()["id"])
    if not quiz:
        flash("Practice quiz not found.", "error")
        return redirect(url_for("home"))
    if quiz["answer_key"]:
        return redirect(url_for("personal_quiz_result", quiz_id=quiz_id))
    return render_template("personal_quiz_take.html", user=current_user(), active_nav="practice", quiz=quiz)


@app.post("/personal-quiz/<quiz_id>/submit")
@login_required
def personal_quiz_submit(quiz_id):
    quiz = db.get_personal_quiz(quiz_id, current_user()["id"])
    if not quiz:
        flash("Practice quiz not found.", "error")
        return redirect(url_for("home"))
    responses = {str(index): request.form.get(f"q{index}", "").upper() for index in range(len(quiz["questions"]))}
    db.save_personal_responses(quiz_id, current_user()["id"], responses)
    return redirect(url_for("personal_quiz_key", quiz_id=quiz_id))


@app.get("/personal-quiz/<quiz_id>/answer-key")
@login_required
def personal_quiz_key(quiz_id):
    quiz = db.get_personal_quiz(quiz_id, current_user()["id"])
    if not quiz:
        flash("Practice quiz not found.", "error")
        return redirect(url_for("home"))
    return render_template("personal_quiz_key.html", user=current_user(), active_nav="practice", quiz=quiz)


@app.post("/personal-quiz/<quiz_id>/answer-key")
@login_required
def personal_quiz_mark(quiz_id):
    quiz = db.get_personal_quiz(quiz_id, current_user()["id"])
    if not quiz:
        flash("Practice quiz not found.", "error")
        return redirect(url_for("home"))
    key = {str(index): request.form.get(f"a{index}", "").upper() for index in range(len(quiz["questions"]))}
    if any(not answer for answer in key.values()):
        flash("Enter an answer for every question before marking.", "error")
        return redirect(url_for("personal_quiz_key", quiz_id=quiz_id))
    db.mark_personal_quiz(quiz_id, current_user()["id"], key)
    return redirect(url_for("personal_quiz_result", quiz_id=quiz_id))


@app.get("/personal-quiz/<quiz_id>/result")
@login_required
def personal_quiz_result(quiz_id):
    quiz = db.get_personal_quiz(quiz_id, current_user()["id"])
    if not quiz or not quiz["answer_key"]:
        return redirect(url_for("personal_quiz_take", quiz_id=quiz_id))
    return render_template("personal_quiz_result.html", user=current_user(), active_nav="practice", quiz=quiz)


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
    return render_template("quiz_take.html", user=current_user(), active_nav="practice", subjects=user_subjects(current_user()), error=error)


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
        active_nav="practice",
        subjects=user_subjects(current_user()),
        quiz=quiz,
        subject=subject_info,
        active_tool="quizzes",
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
        active_nav="practice",
        subjects=user_subjects(current_user()),
        quiz=quiz,
        subject=subject_info,
        active_tool="quizzes",
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
    """Practice (tasks + focus) now lives on the merged Schedule page."""
    return redirect(url_for("schedule"))


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
    return redirect(url_for("schedule"))


@app.post("/task/<task_id>/toggle")
@login_required
def task_toggle(task_id):
    """Flip a task between todo and done."""
    user = current_user()
    planner.toggle_task(user["id"], task_id)
    back = request.form.get("next") or url_for("schedule")
    return redirect(back)


# ---------------------------------------------------------------------------
# Courses
# ---------------------------------------------------------------------------

@app.get("/courses")
@login_required
def courses():
    """Render the course & semester management page."""
    user = current_user()
    return render_template("courses.html", user=user, active_nav="courses", courses=planner.list_courses(user["id"]))


@app.post("/courses")
@login_required
def courses_add():
    """Create a new course."""
    user = current_user()
    planner.create_course(
        user["id"],
        code=request.form.get("course_code", request.form.get("code", "")),
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
        active_nav="courses",
        course=course,
        deadlines=course_deadlines,
        tasks=course_tasks,
        notes=course_notes,
        subject_slug=subject_slug(course["code"]),
    )


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------

@app.get("/calendar")
@login_required
def calendar():
    """Calendar now lives on the merged Schedule page."""
    return redirect(url_for("schedule"))


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
    return redirect(url_for("schedule"))


@app.post("/calendar/<event_id>/delete")
@login_required
def calendar_delete(event_id):
    """Delete a calendar event."""
    user = current_user()
    planner.delete_event(user["id"], event_id)
    flash("Event removed.", "success")
    return redirect(url_for("schedule"))


# ---------------------------------------------------------------------------
# Schedule (merged Calendar + Practice)
# ---------------------------------------------------------------------------

@app.get("/schedule")
@login_required
def schedule():
    """Render the Schedule page: calendar events, deadlines, tasks, and a focus CTA on one page."""
    user = current_user()
    return render_template(
        "schedule.html",
        user=user,
        active_nav="schedule",
        events=planner.list_events(user["id"]),
        deadlines=planner.list_deadlines(user["id"]),
        tasks=planner.list_tasks(user["id"]),
        courses=planner.list_courses(user["id"]),
        courses_by_id={c["id"]: c for c in planner.list_courses(user["id"])},
        open_tasks=sum(1 for t in planner.list_tasks(user["id"]) if t["status"] != "done"),
    )


# ---------------------------------------------------------------------------
# Deadlines
# ---------------------------------------------------------------------------

@app.get("/deadlines")
@login_required
def deadlines():
    """Render the deadline & assessment manager."""
    user = current_user()
    return render_template("deadlines.html", user=user, active_nav="schedule", deadlines=planner.list_deadlines(user["id"]), courses=planner.list_courses(user["id"]), courses_by_id={c["id"]: c for c in planner.list_courses(user["id"])})


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
        active_nav="schedule",
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
    return render_template("progress.html", user=user, active_nav="progress", stats=overview)


@app.get("/ai")
@login_required
def ai_hub():
    """Render the AI Learning Hub full-page view.

    Shares the exact same ai/_panel.html partial the floating drawer uses, so
    the two layouts can never drift apart. The drawer is the primary entry
    point; this route just lets someone pin the AI to a full browser tab.
    """
    return render_template("ai_hub.html", user=current_user())


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
        active_nav="schedule",
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
    return render_template("notes.html", user=user, active_nav="practice", notes=note_list, courses=courses)


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
