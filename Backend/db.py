"""SQLite persistence layer for the Study Planner.

Centralizes the database connection, schema bootstrap, and lightweight row
helpers. Tables:

  users            - accounts + onboarding profile + AI preferences
  memberships      - subject membership (collaboration)
  subject_codes    - per-subject collaboration invite codes
  materials        - shared course-material metadata
  quizzes          - quizzes (questions stored as JSON)
  attempts         - quiz attempts
  ai_conversations - AI chat conversations (per user)
  ai_messages      - messages within an AI conversation
  ai_usage         - per-user AI usage counters (JSON payload)
  notes            - per-user study notes (course/topic scoped)
  sessions         - completed study/focus sessions + reflections

The database file lives at Database/instance/study_planner.db and is created
automatically on first use. Uses only Python's standard library (sqlite3).
"""

import json
import os
import random
import sqlite3
import string
import time
import uuid

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_DIR = os.path.join(BASE_DIR, "..", "Database")
INSTANCE_DIR = os.path.join(DATABASE_DIR, "instance")
DB_PATH = os.getenv("DATABASE_PATH", os.path.join(INSTANCE_DIR, "study_planner.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    password    TEXT NOT NULL,
    school      TEXT,
    program     TEXT,
    goals       TEXT,
    courses_json TEXT,
    onboarded   INTEGER DEFAULT 0,
    available_hours REAL DEFAULT 4,
    ai_model    TEXT,
    ai_level    TEXT
);

CREATE TABLE IF NOT EXISTS memberships (
    slug    TEXT,
    user_id TEXT,
    PRIMARY KEY (slug, user_id)
);

CREATE TABLE IF NOT EXISTS subject_codes (
    slug TEXT PRIMARY KEY,
    code TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS materials (
    id          TEXT PRIMARY KEY,
    slug        TEXT NOT NULL,
    filename    TEXT NOT NULL,
    uploader_id TEXT,
    date        TEXT
);

CREATE TABLE IF NOT EXISTS quizzes (
    id             TEXT PRIMARY KEY,
    subject        TEXT NOT NULL,
    title          TEXT NOT NULL,
    description    TEXT,
    creator        TEXT,
    invite_code    TEXT,
    questions_json TEXT
);

CREATE TABLE IF NOT EXISTS attempts (
    id      TEXT PRIMARY KEY,
    quiz_id TEXT NOT NULL,
    user_id TEXT,
    score   INTEGER,
    total   INTEGER,
    date    TEXT
);

CREATE TABLE IF NOT EXISTS ai_conversations (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    title      TEXT,
    model      TEXT,
    mode       TEXT,
    created_at INTEGER,
    updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS ai_messages (
    id             TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    user_id        TEXT NOT NULL,
    role           TEXT NOT NULL,
    content        TEXT NOT NULL,
    model          TEXT,
    created_at     INTEGER,
    metadata_json  TEXT
);

CREATE TABLE IF NOT EXISTS ai_usage (
    user_id  TEXT PRIMARY KEY,
    data_json TEXT
);

CREATE TABLE IF NOT EXISTS notes (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    course_id   TEXT,
    slug        TEXT,
    title       TEXT,
    body        TEXT,
    topic       TEXT,
    updated_at  INTEGER
);

CREATE TABLE IF NOT EXISTS ai_audit (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id   TEXT,
    action    TEXT NOT NULL,
    detail    TEXT,
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    course_id     TEXT,
    task_id       TEXT,
    slug          TEXT,
    duration_minutes INTEGER,
    started_at    INTEGER,
    ended_at      INTEGER,
    confidence    INTEGER,
    notes         TEXT
);

CREATE INDEX IF NOT EXISTS idx_materials_slug ON materials (slug);
CREATE INDEX IF NOT EXISTS idx_quizzes_subject  ON quizzes (subject);
CREATE INDEX IF NOT EXISTS idx_attempts_quiz    ON attempts (quiz_id);
CREATE INDEX IF NOT EXISTS idx_ai_conv_user     ON ai_conversations (user_id);
CREATE INDEX IF NOT EXISTS idx_ai_msg_conv      ON ai_messages (conversation_id);
CREATE INDEX IF NOT EXISTS idx_notes_user       ON notes (user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user    ON sessions (user_id);
CREATE INDEX IF NOT EXISTS idx_audit_user       ON ai_audit (user_id);
"""

_COLUMN_CACHE = {}


def ensure_db_file():
    os.makedirs(INSTANCE_DIR, exist_ok=True)


def connect():
    """Open a new SQLite connection with row access by name."""
    ensure_db_file()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create tables if they do not yet exist."""
    conn = connect()
    try:
        conn.executescript(_SCHEMA)
        _migrate_add_columns(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate_add_columns(conn):
    """Apply lightweight schema migrations to already-created databases."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "available_hours" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN available_hours REAL DEFAULT 4")


def _conn_context():
    conn = connect()
    return conn


# ------------------------------------------------------------------ helpers

def _todict(row):
    if row is None:
        return None
    return dict(row)


def _query_all(sql, params=()):
    conn = _conn_context()
    try:
        cur = conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _query_one(sql, params=()):
    conn = _conn_context()
    try:
        cur = conn.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _execute(sql, params=()):
    conn = _conn_context()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _executemany(sql, seq):
    conn = _conn_context()
    try:
        conn.executemany(sql, seq)
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------------------- users

def load_users():
    """Return {user_id: user_dict} for every account (for name resolution)."""
    rows = _query_all("SELECT * FROM users")
    return {r["id"]: user_to_dict(r) for r in rows}


def user_to_dict(row):
    """Convert a users row into the shape the rest of the app expects.

    'courses' is stored as JSON in courses_json and exposed as a Python list,
    matching the old JSON-file format so templates and routes keep working.
    """
    d = dict(row)
    d["courses"] = json.loads(d.get("courses_json") or "[]")
    d["onboarded"] = bool(d.get("onboarded"))
    return d


def get_user(user_id):
    if not user_id:
        return None
    row = _query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    return user_to_dict(row) if row else None


def get_user_by_email(email):
    row = _query_one("SELECT * FROM users WHERE email = ?", (email,))
    return user_to_dict(row) if row else None


def create_user(user_id, name, email, password_hash):
    conn = _conn_context()
    try:
        conn.execute(
            "INSERT INTO users (id, name, email, password, onboarded) VALUES (?, ?, ?, ?, 0)",
            (user_id, name, email, password_hash),
        )
        conn.commit()
    finally:
        conn.close()


def update_user(user_id, fields):
    """Update a user row with the given {column: value} dictionary."""
    if not fields:
        return
    cols = []
    params = []
    for key, value in fields.items():
        cols.append(f"{key} = ?")
        params.append(value)
    params.append(user_id)
    conn = _conn_context()
    try:
        conn.execute(f"UPDATE users SET {', '.join(cols)} WHERE id = ?", params)
        conn.commit()
    finally:
        conn.close()


def set_user_onboarded(user_id, school, program, courses, goals, available_hours=4):
    update_user(user_id, {
        "school": school,
        "program": program,
        "courses_json": json.dumps(courses),
        "goals": goals,
        "available_hours": available_hours,
        "onboarded": 1,
    })


def set_ai_preferences(user_id, model=None, level=None):
    fields = {}
    if model is not None:
        fields["ai_model"] = model
    if level is not None:
        fields["ai_level"] = level
    update_user(user_id, fields)


# ------------------------------------------------------------------ collab

def get_members(slug):
    rows = _query_all(
        "SELECT user_id FROM memberships WHERE slug = ? ORDER BY user_id", (slug,)
    )
    return [r["user_id"] for r in rows]


def add_member(slug, user_id):
    _execute("INSERT OR IGNORE INTO memberships (slug, user_id) VALUES (?, ?)", (slug, user_id))


def remove_member(slug, user_id):
    _execute("DELETE FROM memberships WHERE slug = ? AND user_id = ?", (slug, user_id))


def get_subject_code(slug):
    row = _query_one("SELECT code FROM subject_codes WHERE slug = ?", (slug,))
    if row:
        return row["code"]
    suffix = "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(4))
    code = f"SP-{suffix}"
    _execute("INSERT OR REPLACE INTO subject_codes (slug, code) VALUES (?, ?)", (slug, code))
    return code


def get_materials(slug):
    rows = _query_all(
        "SELECT id, slug, filename, uploader_id, date FROM materials "
        "WHERE slug = ? ORDER BY rowid DESC",
        (slug,),
    )
    out = []
    for r in rows:
        out.append({
            "filename": r["filename"],
            "uploader_id": r["uploader_id"],
            "date": r["date"],
        })
    return out


def add_material(slug, filename, uploader_id):
    _execute(
        "INSERT INTO materials (id, slug, filename, uploader_id, date) VALUES (?, ?, ?, ?, NULL)",
        (uuid.uuid4().hex, slug, filename, uploader_id),
    )


def list_quizzes(slug):
    rows = _query_all("SELECT * FROM quizzes WHERE subject = ? ORDER BY rowid", (slug,))
    return [_quiz_from_row(r) for r in rows]


def get_quiz(quiz_id):
    row = _query_one("SELECT * FROM quizzes WHERE id = ?", (quiz_id,))
    return _quiz_from_row(row) if row else None


def find_quiz_by_code(code):
    code = str(code or "").strip().upper()
    row = _query_one("SELECT * FROM quizzes WHERE invite_code = ?", (code,))
    return _quiz_from_row(row) if row else None


def _quiz_from_row(row):
    d = dict(row)
    d["questions"] = json.loads(d.pop("questions_json") or "[]")
    return d


def create_quiz(slug, title, description, questions, creator_id):
    def rcode(length):
        return "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(length))

    quiz_id = rcode(8).lower()
    invite = rcode(6)
    _execute(
        "INSERT INTO quizzes (id, subject, title, description, creator, invite_code, questions_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (quiz_id, slug, title, description, creator_id, invite, json.dumps(questions)),
    )
    return get_quiz(quiz_id)


def add_attempt(quiz_id, user_id, score, total):
    attempt_id = "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(10)).lower()
    _execute(
        "INSERT INTO attempts (id, quiz_id, user_id, score, total, date) VALUES (?, ?, ?, ?, ?, NULL)",
        (attempt_id, quiz_id, user_id, score, total),
    )


def get_attempts(quiz_id):
    return _query_all(
        "SELECT * FROM attempts WHERE quiz_id = ? ORDER BY rowid", (quiz_id,)
    )


# -------------------------------------------------------------- AI storage

def ai_list_conversations(user_id):
    rows = _query_all(
        "SELECT * FROM ai_conversations WHERE user_id = ? ORDER BY updated_at DESC",
        (user_id,),
    )
    return [_ai_conv_from_row(r) for r in rows]


def ai_get_conversation(user_id, conversation_id):
    row = _query_one(
        "SELECT * FROM ai_conversations WHERE id = ? AND user_id = ?",
        (conversation_id, user_id),
    )
    if not row:
        return None
    conv = _ai_conv_from_row(row)
    conv["messages"] = _ai_messages(user_id, conversation_id)
    return conv


def ai_create_conversation(user_id, title="New conversation", model="claude", mode="ask"):
    cid = uuid.uuid4().hex
    now = int(time.time())
    _execute(
        "INSERT INTO ai_conversations (id, user_id, title, model, mode, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (cid, user_id, title, model, mode, now, now),
    )
    return _ai_conv_from_row(_query_one("SELECT * FROM ai_conversations WHERE id = ?", (cid,)))


def ai_rename_conversation(user_id, conversation_id, title):
    _execute(
        "UPDATE ai_conversations SET title = ?, updated_at = ? WHERE id = ? AND user_id = ?",
        (title, int(time.time()), conversation_id, user_id),
    )
    return ai_get_conversation(user_id, conversation_id) is not None


def ai_set_conversation_model(user_id, conversation_id, model):
    _execute(
        "UPDATE ai_conversations SET model = ?, updated_at = ? WHERE id = ? AND user_id = ?",
        (model, int(time.time()), conversation_id, user_id),
    )
    return ai_get_conversation(user_id, conversation_id) is not None


def ai_delete_conversation(user_id, conversation_id):
    conn = _conn_context()
    try:
        cur = conn.execute(
            "DELETE FROM ai_messages WHERE conversation_id = ? AND user_id = ?",
            (conversation_id, user_id),
        )
        cur2 = conn.execute(
            "DELETE FROM ai_conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        )
        conn.commit()
        return cur2.rowcount > 0
    finally:
        conn.close()


def ai_add_message(user_id, conversation_id, role, content, model=None, metadata=None):
    import time
    import uuid
    conv = _query_one(
        "SELECT * FROM ai_conversations WHERE id = ? AND user_id = ?",
        (conversation_id, user_id),
    )
    if not conv:
        return None
    msg = {
        "id": uuid.uuid4().hex,
        "conversationId": conversation_id,
        "role": role,
        "content": content,
        "model": model or conv["model"],
        "createdAt": int(time.time()),
        "metadata": metadata or {},
    }
    _execute(
        "INSERT INTO ai_messages (id, conversation_id, user_id, role, content, model, created_at, metadata_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (msg["id"], conversation_id, user_id, role, content, msg["model"],
         msg["createdAt"], json.dumps(msg["metadata"])),
    )
    # Auto-title from first user message if still default.
    if role == "user" and conv["title"] in (None, "", "New conversation"):
        first_line = content.strip().splitlines()[0]
        title = first_line[:50] if first_line else "New conversation"
        _execute(
            "UPDATE ai_conversations SET title = ?, updated_at = ? WHERE id = ?",
            (title, int(time.time()), conversation_id),
        )
    else:
        _execute(
            "UPDATE ai_conversations SET updated_at = ? WHERE id = ?",
            (int(time.time()), conversation_id),
        )
    return msg


def _ai_messages(user_id, conversation_id):
    rows = _query_all(
        "SELECT * FROM ai_messages WHERE conversation_id = ? AND user_id = ? ORDER BY rowid",
        (conversation_id, user_id),
    )
    return [_ai_msg_from_row(r) for r in rows]


def _ai_msg_from_row(row):
    d = dict(row)
    return {
        "id": d["id"],
        "conversationId": d["conversation_id"],
        "role": d["role"],
        "content": d["content"],
        "model": d["model"],
        "createdAt": d["created_at"],
        "metadata": json.loads(d.get("metadata_json") or "{}"),
    }


def _ai_conv_from_row(row):
    d = dict(row)
    return {
        "id": d["id"],
        "user_id": d["user_id"],
        "title": d["title"],
        "model": d["model"],
        "mode": d["mode"],
        "created_at": d["created_at"],
        "updated_at": d["updated_at"],
        "messages": [],
    }


def ai_search_conversations(user_id, query):
    query = (query or "").lower()
    if not query:
        return ai_list_conversations(user_id)
    matches = []
    for conv in ai_list_conversations(user_id):
        cid = conv["id"]
        haystack = (conv["title"] or "").lower() + " " + " ".join(
            m.get("content", "").lower() for m in _ai_messages(user_id, cid)
        )
        if query in haystack:
            conv["messages"] = _ai_messages(user_id, cid)
            matches.append(conv)
    return matches


def ai_get_usage(user_id):
    row = _query_one("SELECT data_json FROM ai_usage WHERE user_id = ?", (user_id,))
    if not row:
        return {}
    try:
        return json.loads(row["data_json"])
    except ValueError:
        return {}


def ai_record_usage(user_id, model=None, mode=None, input_tokens=0, output_tokens=0):
    import time
    usage = ai_get_usage(user_id)
    if not usage:
        usage = {"total_requests": 0, "days": {}}
    day = time.strftime("%Y-%m-%d", time.gmtime())
    day_entry = usage["days"].setdefault(day, {"requests": 0, "input_tokens": 0, "output_tokens": 0})
    day_entry["requests"] += 1
    day_entry["input_tokens"] += int(input_tokens or 0)
    day_entry["output_tokens"] += int(output_tokens or 0)
    usage["total_requests"] = usage.get("total_requests", 0) + 1
    if model:
        by_model = usage.setdefault("models", {})
        by_model[model] = by_model.get(model, 0) + 1
    if mode:
        by_mode = usage.setdefault("modes", {})
        by_mode[mode] = by_mode.get(mode, 0) + 1
    _execute(
        "INSERT INTO ai_usage (user_id, data_json) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET data_json = excluded.data_json",
        (user_id, json.dumps(usage)),
    )
    return usage


def ai_reset_usage_for_tests(user_id=None):
    """Delete usage for a user (or all) - used by tests."""
    conn = _conn_context()
    try:
        cur = conn.execute("DELETE FROM ai_usage") if user_id is None else conn.execute(
            "DELETE FROM ai_usage WHERE user_id = ?", (user_id,)
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def ai_purge_user(user_id):
    """Delete all user data (AI, notes, sessions, audit) for a user.

    Tolerant of a database that predates the notes/sessions tables so that
    data-reset tooling degrades gracefully on older installs.
    """
    conn = _conn_context()
    try:
        conn.execute("DELETE FROM ai_messages WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM ai_conversations WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM ai_usage WHERE user_id = ?", (user_id,))
        for tbl in ("notes", "sessions", "ai_audit"):
            try:
                conn.execute(f"DELETE FROM {tbl} WHERE user_id = ?", (user_id,))
            except sqlite3.OperationalError:
                continue
        conn.commit()
    finally:
        conn.close()


# ------------------------------------------------------------ audit trail

def log_audit(user_id, action, detail=""):
    """Append an AI audit entry (who/what/when) for accountability."""
    conn = _conn_context()
    try:
        conn.execute(
            "INSERT INTO ai_audit (user_id, action, detail, created_at) VALUES (?, ?, ?, ?)",
            (user_id, action, detail, int(time.time())),
        )
        conn.commit()
    except sqlite3.OperationalError:
        # Tolerate tables that predate the audit trail.
        pass
    finally:
        conn.close()


def list_audit(user_id=None, limit=200):
    """Return audit entries, newest first, optionally filtered to a user."""
    if user_id:
        return _query_all(
            "SELECT * FROM ai_audit WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        )
    return _query_all(
        "SELECT * FROM ai_audit ORDER BY created_at DESC LIMIT ?", (limit,)
    )


def delete_user(user_id):
    """Permanently delete an account and all of its rows (per-user isolation)."""
    conn = _conn_context()
    try:
        for sql in (
            "DELETE FROM ai_messages WHERE user_id = ?",
            "DELETE FROM ai_conversations WHERE user_id = ?",
            "DELETE FROM ai_usage WHERE user_id = ?",
            "DELETE FROM memberships WHERE user_id = ?",
            "DELETE FROM attempts WHERE user_id = ?",
            "DELETE FROM users WHERE id = ?",
        ):
            conn.execute(sql, (user_id,))
        for tbl in ("notes", "sessions", "ai_audit"):
            try:
                conn.execute(f"DELETE FROM {tbl} WHERE user_id = ?", (user_id,))
            except sqlite3.OperationalError:
                continue
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------- study notes

def list_notes(user_id, course_id=None):
    """Return a user's notes, optionally filtered to one course/topic owner."""
    if course_id:
        return _query_all(
            "SELECT * FROM notes WHERE user_id = ? AND course_id = ?"
            " ORDER BY updated_at DESC",
            (user_id, course_id),
        )
    return _query_all(
        "SELECT * FROM notes WHERE user_id = ? ORDER BY updated_at DESC", (user_id,)
    )


def get_note(note_id, user_id=None):
    """Return a single note. If user_id is given, scope to that user's note."""
    if user_id:
        return _query_one(
            "SELECT * FROM notes WHERE id = ? AND user_id = ?", (note_id, user_id)
        )
    return _query_one("SELECT * FROM notes WHERE id = ?", (note_id,))


def create_note(user_id, title="", body="", course_id=None, slug=None, topic=""):
    """Create a note and return it."""
    note_id = uuid.uuid4().hex
    now = int(time.time())
    _execute(
        "INSERT INTO notes (id, user_id, course_id, slug, title, body, topic, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (note_id, user_id, course_id, slug, title, body, topic, now),
    )
    return get_note(note_id)


def update_note(note_id, user_id, title=None, body=None, topic=None):
    """Update mutable fields of a note scoped to the owner."""
    fields = {"updated_at": int(time.time())}
    if title is not None:
        fields["title"] = title
    if body is not None:
        fields["body"] = body
    if topic is not None:
        fields["topic"] = topic
    cols = ", ".join(f"{k} = ?" for k in fields)
    params = list(fields.values()) + [note_id, user_id]
    conn = _conn_context()
    try:
        cur = conn.execute(
            f"UPDATE notes SET {cols} WHERE id = ? AND user_id = ?", params
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_note(note_id, user_id):
    """Delete a note scoped to its owner. Returns True if deleted."""
    conn = _conn_context()
    try:
        cur = conn.execute(
            "DELETE FROM notes WHERE id = ? AND user_id = ?", (note_id, user_id)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


# ----------------------------------------------------------- study sessions

def list_sessions(user_id, limit=50):
    """Return a user's study sessions, most recent first."""
    return _query_all(
        "SELECT * FROM sessions WHERE user_id = ? ORDER BY started_at DESC LIMIT ?",
        (user_id, limit),
    )


def get_session(session_id, user_id=None):
    """Return a single study session, optionally scoped to the owner."""
    if user_id:
        return _query_one(
            "SELECT * FROM sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        )
    return _query_one("SELECT * FROM sessions WHERE id = ?", (session_id,))


def create_session(user_id, duration_minutes=0, course_id=None, task_id=None,
                   slug=None, started_at=None, ended_at=None):
    """Record a completed study session."""
    session_id = uuid.uuid4().hex
    now = int(time.time())
    started_at = started_at if started_at is not None else now
    ended_at = ended_at if ended_at is not None else now + int(duration_minutes or 0) * 60
    _execute(
        "INSERT INTO sessions (id, user_id, course_id, task_id, slug,"
        " duration_minutes, started_at, ended_at, confidence, notes)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
        (session_id, user_id, course_id, task_id, slug,
         int(duration_minutes or 0), int(started_at), int(ended_at)),
    )
    return get_session(session_id)


def set_session_reflection(session_id, user_id, confidence=None, notes=None):
    """Store the end-of-session reflection (confidence 1-5 and optional note)."""
    sets = []
    params = []
    if confidence is not None:
        sets.append("confidence = ?")
        params.append(int(confidence))
    if notes is not None:
        sets.append("notes = ?")
        params.append(notes)
    if not sets:
        return False
    params += [session_id, user_id]
    conn = _conn_context()
    try:
        cur = conn.execute(
            f"UPDATE sessions SET {', '.join(sets)} WHERE id = ? AND user_id = ?", params
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_session(session_id, user_id):
    """Delete a study session scoped to its owner."""
    conn = _conn_context()
    try:
        cur = conn.execute(
            "DELETE FROM sessions WHERE id = ? AND user_id = ?", (session_id, user_id)
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def minutes_by_day(user_id, days=14):
    """Return { 'YYYY-MM-DD': total_minutes } for the last N days of study + attempts."""
    conn = _conn_context()
    try:
        cur = conn.execute(
            "SELECT started_at, duration_minutes FROM sessions WHERE user_id = ?"
            " AND started_at >= ?",
            (user_id, int(time.time()) - days * 86400),
        )
        rows = {"sessions": [dict(r) for r in cur.fetchall()]}
        halt = conn.execute(
            "SELECT date, score, total FROM attempts WHERE user_id = ?", (user_id,)
        )
        rows["attempts"] = [dict(r) for r in halt.fetchall()]
        return rows
    finally:
        conn.close()


def compute_streak(active_days):
    """Given a set of 'YYYY-MM-DD' active days, return the current streak length."""
    from datetime import date, timedelta
    if not active_days:
        return 0
    days = set(active_days)
    today = date.today()
    # count backwards from today (or yesterday) to allow an unfinished today
    cursor = today
    if cursor.isoformat() not in days:
        cursor = today - timedelta(days=1)
    streak = 0
    while cursor.isoformat() in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


# ------------------------------------------------------------- test helpers

def reset_db_for_tests():
    """Drop all tables and recreate (used by tests)."""
    conn = _conn_context()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        tables = [r["name"] for r in cur.fetchall()]
        for t in tables:
            conn.execute(f'DROP TABLE IF EXISTS "{t}"')
        conn.commit()
    finally:
        conn.close()
    init_db()