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
  ai_connections   - per-user BYOK provider keys (obfuscated)
  notes            - per-user study notes (course/topic scoped)
  sessions         - completed study/focus sessions + reflections

The database file lives at Database/instance/study_planner.db and is created
automatically on first use. Uses only Python's standard library (sqlite3).
"""

import base64
import hashlib
import hmac
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
    display_name TEXT,
    email       TEXT NOT NULL UNIQUE,
    password    TEXT NOT NULL,
    avatar_path TEXT,
    settings_json TEXT,
    school      TEXT,
    program     TEXT,
    goals       TEXT,
    courses_json TEXT,
    onboarded   INTEGER DEFAULT 0,
    available_hours REAL DEFAULT 4,
    ai_model    TEXT,
    ai_level    TEXT
);

-- Courses are the single source of truth shared by the dashboard "My Subjects"
-- cards, the /courses CRUD page, and onboarding. id stays a TEXT uuid so the
-- planner's events/deadlines/tasks and db notes/sessions can keep linking to a
-- course without an id-space migration. UNIQUE(user_id, course_code) stops the
-- "onboarding stub duplicated by the Add Course form" bug at the schema level.
CREATE TABLE IF NOT EXISTS courses (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    course_code TEXT NOT NULL,
    title       TEXT,
    lecturer    TEXT,
    credits     INTEGER DEFAULT 0,
    schedule    TEXT,
    description TEXT,
    color       TEXT,
    term        TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, course_code)
);

CREATE INDEX IF NOT EXISTS idx_courses_user ON courses (user_id);

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

-- Private PDF practice quizzes. Answer keys are deliberately stored only
-- after an attempt, so importing a question paper cannot reveal answers.
CREATE TABLE IF NOT EXISTS personal_quizzes (
    id             TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL,
    subject_slug   TEXT NOT NULL,
    title          TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL,
    questions_json TEXT NOT NULL,
    responses_json TEXT,
    answer_key_json TEXT,
    score          INTEGER,
    completed_at   INTEGER,
    created_at     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_personal_quizzes_user_subject
    ON personal_quizzes (user_id, subject_slug);

CREATE TABLE IF NOT EXISTS ai_conversations (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL,
    title      TEXT,
    model      TEXT,
    mode       TEXT,
    created_at INTEGER,
    updated_at INTEGER
);

-- Per-user BYOK connections ("connect your own Claude/GPT/Gemini account").
-- The key is obfuscated (XOR keystream + HMAC tag keyed from SECRET_KEY) so a
-- dumped db file doesn't leak the raw key; see _encrypt_connection_key.
-- UNIQUE(user_id, provider) makes "reconnecting" an update, never a duplicate.
CREATE TABLE IF NOT EXISTS ai_connections (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    provider    TEXT NOT NULL,
    api_key_enc TEXT NOT NULL,
    key_hint    TEXT,
    label       TEXT,
    status      TEXT DEFAULT 'connected',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, provider)
);

CREATE INDEX IF NOT EXISTS idx_ai_connections_user ON ai_connections (user_id);

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

-- ---------------------------------------------------------------------------
-- Collaborative workspace tables (Backend/collaboration/).
-- users.id is TEXT, so user FKs are TEXT to match the existing PK type.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workspaces (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    course_code TEXT,
    description TEXT,
    owner_id    TEXT NOT NULL REFERENCES users(id),
    invite_code TEXT NOT NULL UNIQUE,
    deadline    TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    archived    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS workspace_members (
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id      TEXT NOT NULL REFERENCES users(id),
    role         TEXT NOT NULL DEFAULT 'member' CHECK(role IN ('owner','member')),
    joined_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (workspace_id, user_id)
);

CREATE TABLE IF NOT EXISTS milestones (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    due_date     TEXT,
    sort_order   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS labels (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    color        TEXT NOT NULL DEFAULT '#64748b'
);

CREATE TABLE IF NOT EXISTS work_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id    INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    parent_id       INTEGER REFERENCES work_items(id) ON DELETE CASCADE,
    milestone_id    INTEGER REFERENCES milestones(id),
    title           TEXT NOT NULL,
    description     TEXT,
    assignee_id     TEXT REFERENCES users(id),
    status          TEXT NOT NULL DEFAULT 'todo' CHECK(status IN
        ('todo','in_progress','in_review','blocked','completed','cancelled')),
    priority        TEXT NOT NULL DEFAULT 'normal' CHECK(priority IN ('low','normal','high','urgent')),
    due_date        TEXT,
    blocked_reason  TEXT,
    created_by      TEXT NOT NULL REFERENCES users(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS work_item_labels (
    work_item_id INTEGER NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
    label_id     INTEGER NOT NULL REFERENCES labels(id) ON DELETE CASCADE,
    PRIMARY KEY (work_item_id, label_id)
);

CREATE TABLE IF NOT EXISTS work_item_dependencies (
    work_item_id   INTEGER NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
    depends_on_id  INTEGER NOT NULL REFERENCES work_items(id) ON DELETE CASCADE,
    PRIMARY KEY (work_item_id, depends_on_id)
);

CREATE TABLE IF NOT EXISTS comments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    work_item_id  INTEGER REFERENCES work_items(id) ON DELETE CASCADE,
    workspace_id  INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    author_id     TEXT NOT NULL REFERENCES users(id),
    body          TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS information_requests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id  INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    work_item_id  INTEGER REFERENCES work_items(id),
    requester_id  TEXT NOT NULL REFERENCES users(id),
    recipient_id  TEXT NOT NULL REFERENCES users(id),
    what_needed   TEXT NOT NULL,
    why_needed    TEXT,
    needed_by     TEXT,
    status        TEXT NOT NULL DEFAULT 'pending' CHECK(status IN
        ('pending','provided','unavailable','reassigned')),
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS daily_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id    INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id         TEXT NOT NULL REFERENCES users(id),
    log_date        TEXT NOT NULL,
    summary         TEXT NOT NULL,
    minutes_spent   INTEGER,
    blocked_by      TEXT,
    plan_for_tomorrow TEXT,
    UNIQUE(workspace_id, user_id, log_date)
);

CREATE TABLE IF NOT EXISTS activity_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    actor_id     TEXT REFERENCES users(id),
    event_type   TEXT NOT NULL,
    payload      TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS collab_quizzes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id        INTEGER NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    created_by          TEXT NOT NULL REFERENCES users(id),
    title               TEXT NOT NULL,
    duration_minutes    INTEGER NOT NULL,
    opens_at            TEXT,
    closes_at           TEXT,
    randomize_questions INTEGER NOT NULL DEFAULT 0,
    private_results     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS quiz_questions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id       INTEGER NOT NULL REFERENCES collab_quizzes(id) ON DELETE CASCADE,
    prompt        TEXT NOT NULL,
    options       TEXT NOT NULL,
    correct_index INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS quiz_attempts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id       INTEGER NOT NULL REFERENCES collab_quizzes(id) ON DELETE CASCADE,
    user_id       TEXT NOT NULL REFERENCES users(id),
    answers       TEXT,
    score         INTEGER,
    started_at    TEXT,
    submitted_at  TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      TEXT NOT NULL REFERENCES users(id),
    workspace_id INTEGER REFERENCES workspaces(id),
    kind         TEXT NOT NULL,
    body         TEXT NOT NULL,
    link         TEXT,
    read_at      TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_materials_slug ON materials (slug);
CREATE INDEX IF NOT EXISTS idx_quizzes_subject  ON quizzes (subject);
CREATE INDEX IF NOT EXISTS idx_attempts_quiz    ON attempts (quiz_id);
CREATE INDEX IF NOT EXISTS idx_ai_conv_user     ON ai_conversations (user_id);
CREATE INDEX IF NOT EXISTS idx_ai_msg_conv      ON ai_messages (conversation_id);
CREATE INDEX IF NOT EXISTS idx_notes_user       ON notes (user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_user    ON sessions (user_id);
CREATE INDEX IF NOT EXISTS idx_audit_user       ON ai_audit (user_id);
CREATE INDEX IF NOT EXISTS idx_collab_members_user ON workspace_members (user_id);
CREATE INDEX IF NOT EXISTS idx_collab_items_ws  ON work_items (workspace_id);
CREATE INDEX IF NOT EXISTS idx_collab_items_parent ON work_items (parent_id);
CREATE INDEX IF NOT EXISTS idx_collab_comments_item ON comments (work_item_id);
CREATE INDEX IF NOT EXISTS idx_collab_activity_ws ON activity_log (workspace_id);
CREATE INDEX IF NOT EXISTS idx_collab_requests_ws ON information_requests (workspace_id);
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
        _migrate_legacy_courses(conn)
        _migrate_ai_connections_schema(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate_ai_connections_schema(conn):
    """Rebuild ai_connections created by an early BYOK draft definition.

    The first version declared REFERENCES users(id); because connect() enables
    PRAGMA foreign_keys, that broke connections for users that exist only as a
    legacy planner legacy row. Recreate the table from the canonical (FK-free)
    schema when the old definition is detected; otherwise nothing happens.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'ai_connections'"
    ).fetchone()
    if not row or "REFERENCES users" not in (row["sql"] or ""):
        return
    conn.execute(
        "CREATE TABLE ai_connections_new ("
        "id          TEXT PRIMARY KEY,"
        "user_id     TEXT NOT NULL,"
        "provider    TEXT NOT NULL,"
        "api_key_enc TEXT NOT NULL,"
        "key_hint    TEXT,"
        "label       TEXT,"
        "status      TEXT DEFAULT 'connected',"
        "created_at  TEXT NOT NULL DEFAULT (datetime('now')),"
        "updated_at  TEXT NOT NULL DEFAULT (datetime('now')),"
        "UNIQUE(user_id, provider)"
        ")"
    )
    conn.execute(
        "INSERT INTO ai_connections_new "
        "(id, user_id, provider, api_key_enc, key_hint, label, status, created_at, updated_at) "
        "SELECT id, user_id, provider, api_key_enc, key_hint, label, status, created_at, updated_at "
        "FROM ai_connections"
    )
    conn.execute("DROP TABLE ai_connections")
    conn.execute("ALTER TABLE ai_connections_new RENAME TO ai_connections")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ai_connections_user "
        "ON ai_connections (user_id)"
    )


def _migrate_add_columns(conn):
    """Apply lightweight schema migrations to already-created databases."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "available_hours" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN available_hours REAL DEFAULT 4")
    if "notify_digest" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN notify_digest INTEGER DEFAULT 0")
    if "display_name" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN display_name TEXT")
    if "avatar_path" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN avatar_path TEXT")
    if "settings_json" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN settings_json TEXT")


def normalize_course_code(value):
    """'dcit  204' -> 'DCIT 204': strip, collapse whitespace, uppercase.

    The single place course codes are normalized — every write goes through
    it so the stored value (and everything that renders it) is consistent.
    """
    if not value:
        return ""
    return " ".join((value.strip() or "").split()).upper()


def _migrate_legacy_courses(conn):
    """One-time move of the two legacy course stores into the courses table.

    Runs on every boot through INSERT OR IGNORE, so it is idempotent:

      1. planner.json courses keep their legacy uuid ids — this preserves the
         course_id links held by planner events/deadlines/tasks.
      2. onboarding codes in users.courses_json become stub rows (no title), so
         a not-yet-completed course shows "To be assigned" and can be completed
         later by the /courses form (upsert, never a duplicate).

    The stub ids are deterministic (uuid5 of the user+code) so a re-run cannot
    spawn a second row. Full courses added going forward use random uuids.
    """
    try:
        import planner as _planner
        doc = _planner.load_all()
    except Exception:
        doc = {}
    if isinstance(doc, dict):
        for user_id, block in doc.items():
            if not isinstance(block, dict):
                continue
            for cid, c in (block.get("courses") or {}).items():
                if not isinstance(c, dict):
                    continue
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO courses "
                        "(id, user_id, course_code, title, lecturer, credits, "
                        "description, schedule, color, term) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (
                            str(cid),
                            str(user_id),
                            normalize_course_code(c.get("code") or ""),
                            (c.get("title") or "").strip() or None,
                            (c.get("lecturer") or "").strip() or None,
                            int(c.get("credits") or 0),
                            (c.get("description") or "").strip() or None,
                            (c.get("schedule") or "").strip() or None,
                            (c.get("color") or "").strip() or None,
                            (c.get("term") or "").strip() or None,
                        ),
                    )
                except sqlite3.IntegrityError:
                    # Legacy rows can reference users that no longer exist.
                    continue

    for row in conn.execute("SELECT id, courses_json FROM users").fetchall():
        try:
            codes = json.loads(row["courses_json"] or "[]")
        except (TypeError, ValueError):
            codes = []
        for code in codes:
            code = normalize_course_code(code)
            if not code:
                continue
            stub_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{row['id']}:{code}").hex
            conn.execute(
                "INSERT OR IGNORE INTO courses (id, user_id, course_code) "
                "VALUES (?,?,?)",
                (stub_id, row["id"], code),
            )


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


def save_onboarding_progress(user_id, school, program, courses, goals, available_hours=4):
    """Persist partial onboarding answers WITHOUT flipping onboarded=1.

    Lets the wizard keep a draft between steps so "save and finish later"
    never loses work; completion still goes through set_user_onboarded.
    """
    update_user(user_id, {
        "school": school,
        "program": program,
        "courses_json": json.dumps(courses),
        "goals": goals,
        "available_hours": available_hours,
    })


def set_ai_preferences(user_id, model=None, level=None):
    fields = {}
    if model is not None:
        fields["ai_model"] = model
    if level is not None:
        fields["ai_level"] = level
    update_user(user_id, fields)


# --------------------------------------------------------- ai connections

def _connection_secret():
    """Return (key, salt) derived from SECRET_KEY (falls back to the same
    default the Flask app uses) so key ciphers survive app restarts."""
    secret = (os.environ.get("SECRET_KEY") or "study-planner-dev-secret").encode("utf-8")
    salt = hashlib.sha256(b"study-planner:ai-connections").digest()[:16]
    key = hashlib.pbkdf2_hmac("sha256", secret, salt, 200_000, dklen=32)
    return key, salt


def _encrypt_connection_key(api_key):
    """Obfuscate an API key before storage.

    Not a real KDF-envelope (that would need a dedicated secrets store), but
    XOR with a keyed PRF stream plus an HMAC tag means a raw db dump never
    contains the plaintext key. Format: base64(nonce).base64(tag).base64(cipher).
    """
    key, _ = _connection_secret()
    nonce = os.urandom(12)
    payload = api_key.encode("utf-8")
    stream = b"".join(
        hmac.new(key, nonce + i.to_bytes(4, "big"), hashlib.sha256).digest()
        for i in range((len(payload) // 32) + 1)
    )[:len(payload)]
    cipher = bytes(a ^ b for a, b in zip(payload, stream))
    tag = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    enc = base64.urlsafe_b64encode(nonce).decode("ascii")
    tag_b = base64.urlsafe_b64encode(tag).decode("ascii")
    cip = base64.urlsafe_b64encode(cipher).decode("ascii")
    return f"{enc}.{tag_b}.{cip}"


def _decrypt_connection_key(payload):
    """Reverse _encrypt_connection_key; returns None on tamper/format errors."""
    try:
        nonce_b, tag_b, cip_b = payload.split(".")
        nonce = base64.urlsafe_b64decode(nonce_b.encode("ascii"))
        tag = base64.urlsafe_b64decode(tag_b.encode("ascii"))
        cipher = base64.urlsafe_b64decode(cip_b.encode("ascii"))
    except (ValueError, TypeError):
        return None
    key, _ = _connection_secret()
    expected = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, tag):
        return None
    stream = b"".join(
        hmac.new(key, nonce + i.to_bytes(4, "big"), hashlib.sha256).digest()
        for i in range((len(cipher) // 32) + 1)
    )[:len(cipher)]
    return bytes(a ^ b for a, b in zip(cipher, stream)).decode("utf-8")


def _mask_connection(row):
    """Strip the encrypted blob from a connection row and expose key_hint."""
    if row is None:
        return None
    return {
        "provider": row["provider"],
        "key_hint": row.get("key_hint") or "",
        "label": row.get("label") or "",
        "status": row.get("status") or "connected",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def set_ai_connection(user_id, provider, api_key, label=""):
    """Upsert a user's BYOK connection for a provider.

    Returns the masked connection dict. The plaintext key is never returned
    and the stored value is obfuscated (see _encrypt_connection_key).
    """
    conn = _conn_context()
    try:
        conn.execute(
            "INSERT INTO ai_connections "
            "(id, user_id, provider, api_key_enc, key_hint, label, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'connected') "
            "ON CONFLICT(user_id, provider) DO UPDATE SET "
            "api_key_enc = excluded.api_key_enc, "
            "key_hint = excluded.key_hint, "
            "label = excluded.label, "
            "status = 'connected', "
            "updated_at = datetime('now')",
            (
                uuid.uuid4().hex,
                user_id,
                provider,
                _encrypt_connection_key(api_key),
                f"••••{api_key[-4:]}" if len(api_key) >= 4 else "connected",
                (label or "").strip(),
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT provider, key_hint, label, status, created_at, updated_at "
            "FROM ai_connections WHERE user_id = ? AND provider = ?",
            (user_id, provider),
        ).fetchone()
    finally:
        conn.close()
    return _mask_connection(dict(row)) if row else None


def get_ai_connections(user_id):
    """Return all of a user's connections with keys masked."""
    rows = _query_all(
        "SELECT provider, key_hint, label, status, created_at, updated_at "
        "FROM ai_connections WHERE user_id = ? ORDER BY updated_at DESC",
        (user_id,),
    )
    return [_mask_connection(r) for r in rows]


def get_ai_connection(user_id, provider):
    row = _query_one(
        "SELECT provider, key_hint, label, status, created_at, updated_at "
        "FROM ai_connections WHERE user_id = ? AND provider = ?",
        (user_id, provider),
    )
    return _mask_connection(row)


def get_ai_connection_key(user_id, provider):
    """Return the decrypted key for routing, or None when not connected."""
    row = _query_one(
        "SELECT api_key_enc FROM ai_connections WHERE user_id = ? AND provider = ?",
        (user_id, provider),
    )
    if not row:
        return None
    return _decrypt_connection_key(row["api_key_enc"])


def delete_ai_connection(user_id, provider):
    conn = _conn_context()
    try:
        cur = conn.execute(
            "DELETE FROM ai_connections WHERE user_id = ? AND provider = ?",
            (user_id, provider),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def set_digest_preference(user_id, enabled):
    update_user(user_id, {"notify_digest": 1 if enabled else 0})


DEFAULT_SETTINGS = {
    "notifications": {
        "deadlines": True,
        "deadline_days": "3",
        "weekly_digest": False,
        "ai_suggestions": True,
        "workspace_activity": True,
        "channel": "in-app",
    },
    "study": {
        "weekly_hours": 4,
        "focus_minutes": 25,
        "spaced_repetition": "balanced",
        "planning_aggressiveness": "balanced",
    },
    "appearance": {"theme": "system", "text_size": "default", "reduce_motion": False},
    "privacy": {"ai_activity": True, "workspace_visibility": "members"},
}


def get_settings(user_id):
    """Return merged settings, preserving defaults for older accounts."""
    user = get_user(user_id) or {}
    try:
        stored = json.loads(user.get("settings_json") or "{}")
    except (TypeError, ValueError):
        stored = {}

    def merge(default, value):
        result = dict(default)
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, dict) and isinstance(result.get(key), dict):
                    result[key] = merge(result[key], item)
                elif key in result:
                    result[key] = item
        return result

    settings = merge(DEFAULT_SETTINGS, stored)
    settings["notifications"]["weekly_digest"] = bool(user.get("notify_digest"))
    return settings


def update_settings(user_id, updates):
    """Merge a validated settings patch into the user's JSON preferences."""
    settings = get_settings(user_id)
    for section, values in updates.items():
        if section in settings and isinstance(values, dict):
            settings[section].update({key: value for key, value in values.items() if key in settings[section]})
    update_user(user_id, {
        "settings_json": json.dumps(settings),
        "notify_digest": 1 if settings["notifications"]["weekly_digest"] else 0,
    })
    return settings


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


def _personal_quiz_from_row(row):
    item = dict(row)
    item["questions"] = json.loads(item.pop("questions_json") or "[]")
    item["responses"] = json.loads(item.pop("responses_json") or "{}")
    item["answer_key"] = json.loads(item.pop("answer_key_json") or "{}")
    return item


def create_personal_quiz(user_id, subject_slug, title, duration_minutes, questions):
    quiz_id = uuid.uuid4().hex
    _execute(
        "INSERT INTO personal_quizzes (id, user_id, subject_slug, title, duration_minutes, questions_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (quiz_id, user_id, subject_slug, title, duration_minutes, json.dumps(questions), int(time.time())),
    )
    return get_personal_quiz(quiz_id, user_id)


def get_personal_quiz(quiz_id, user_id):
    row = _query_one("SELECT * FROM personal_quizzes WHERE id = ? AND user_id = ?", (quiz_id, user_id))
    return _personal_quiz_from_row(row) if row else None


def list_personal_quizzes(user_id, subject_slug):
    rows = _query_all(
        "SELECT * FROM personal_quizzes WHERE user_id = ? AND subject_slug = ? ORDER BY created_at DESC",
        (user_id, subject_slug),
    )
    return [_personal_quiz_from_row(row) for row in rows]


def save_personal_responses(quiz_id, user_id, responses):
    _execute(
        "UPDATE personal_quizzes SET responses_json = ? WHERE id = ? AND user_id = ?",
        (json.dumps(responses), quiz_id, user_id),
    )


def mark_personal_quiz(quiz_id, user_id, answer_key):
    quiz = get_personal_quiz(quiz_id, user_id)
    if not quiz:
        return None
    score = sum(1 for index, answer in answer_key.items() if quiz["responses"].get(str(index)) == answer)
    _execute(
        "UPDATE personal_quizzes SET answer_key_json = ?, score = ?, completed_at = ? WHERE id = ? AND user_id = ?",
        (json.dumps(answer_key), score, int(time.time()), quiz_id, user_id),
    )
    return get_personal_quiz(quiz_id, user_id)


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
            "DELETE FROM ai_connections WHERE user_id = ?",
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


# -------------------------------------------------------------- workspaces

_COLLAB_STATUSES = ("todo", "in_progress", "in_review", "blocked", "completed", "cancelled")
_COLLAB_REQUEST_STATUSES = ("pending", "provided", "unavailable", "reassigned")


def _conn_or_new(conn):
    """Return (conn, owns). If conn is provided, the caller keeps transaction control."""
    if conn is not None:
        return conn, False
    return _conn_context(), True


def collab_role(workspace_id, user_id):
    """Return the member role ('owner'/'member') or None."""
    row = _query_one(
        "SELECT role FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
        (workspace_id, user_id),
    )
    return row["role"] if row else None


def collab_members(workspace_id):
    """Members of a workspace with display info, oldest first."""
    return _query_all(
        "SELECT wm.user_id, wm.role, u.name, u.email "
        "FROM workspace_members wm JOIN users u ON u.id = wm.user_id "
        "WHERE wm.workspace_id = ? ORDER BY wm.joined_at ASC",
        (workspace_id,),
    )


def collab_workspace(workspace_id):
    return _query_one("SELECT * FROM workspaces WHERE id = ?", (workspace_id,))


def collab_workspaces_for(user_id):
    """Workspaces the user belongs to, with usage counts."""
    return _query_all(
        "SELECT w.id, w.name, w.course_code, w.description, w.owner_id, w.invite_code, "
        "w.deadline, w.created_at, w.archived, "
        "(SELECT COUNT(*) FROM workspace_members wm WHERE wm.workspace_id = w.id) AS member_count, "
        "(SELECT COUNT(*) FROM work_items wi WHERE wi.workspace_id = w.id) AS num_tasks, "
        "(SELECT COUNT(*) FROM work_items wi WHERE wi.workspace_id = w.id AND wi.status = 'completed') AS num_completed "
        "FROM workspaces w JOIN workspace_members wm ON wm.workspace_id = w.id "
        "WHERE wm.user_id = ? AND w.archived = 0 "
        "ORDER BY w.created_at DESC",
        (user_id,),
    )


def collab_find_workspace_by_invite(code):
    if not code:
        return None
    return _query_one(
        "SELECT * FROM workspaces WHERE invite_code = ?", (str(code).strip().upper(),)
    )


def collab_create_workspace(conn, name, course_code, description, deadline,
                            owner_id, invite_code):
    """Create a workspace and its owner membership in one transaction."""
    c, commit = _conn_or_new(conn)
    try:
        cur = c.execute(
            "INSERT INTO workspaces (name, course_code, description, deadline, "
            "owner_id, invite_code) VALUES (?, ?, ?, ?, ?, ?)",
            (name, course_code, description, deadline, owner_id, invite_code),
        )
        ws_id = cur.lastrowid
        c.execute(
            "INSERT INTO workspace_members (workspace_id, user_id, role) "
            "VALUES (?, ?, 'owner')",
            (ws_id, owner_id),
        )
        if commit:
            c.commit()
        return ws_id
    finally:
        if commit:
            c.close()


def collab_add_member(conn, workspace_id, user_id, role="member"):
    c, commit = _conn_or_new(conn)
    try:
        cur = c.execute(
            "INSERT OR IGNORE INTO workspace_members (workspace_id, user_id, role) "
            "VALUES (?, ?, ?)",
            (workspace_id, user_id, role),
        )
        added = cur.rowcount > 0
        if added and commit:
            c.commit()
        return added
    finally:
        if commit:
            c.close()


def collab_remove_member(conn, workspace_id, user_id):
    c, commit = _conn_or_new(conn)
    try:
        cur = c.execute(
            "DELETE FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
            (workspace_id, user_id),
        )
        if commit:
            c.commit()
        return cur.rowcount > 0
    finally:
        if commit:
            c.close()


def collab_log_activity(conn, workspace_id, actor_id, event_type, payload):
    """Insert an activity_log row (share the caller's transaction via conn)."""
    c, commit = _conn_or_new(conn)
    try:
        cur = c.execute(
            "INSERT INTO activity_log (workspace_id, actor_id, event_type, payload) "
            "VALUES (?, ?, ?, ?)",
            (workspace_id, actor_id, event_type, payload),
        )
        if commit:
            c.commit()
        return cur.lastrowid
    finally:
        if commit:
            c.close()


def collab_activity(workspace_id, limit=100):
    return _query_all(
        "SELECT a.id, a.actor_id, a.event_type, a.payload, a.created_at, "
        "u.name AS actor_name FROM activity_log a "
        "LEFT JOIN users u ON u.id = a.actor_id "
        "WHERE a.workspace_id = ? ORDER BY a.id DESC LIMIT ?",
        (workspace_id, limit),
    )


# ------------------------------------------------------------------ work items

def collab_work_item(item_id):
    return _query_one(
        "SELECT wi.*, u.name AS assignee_name FROM work_items wi "
        "LEFT JOIN users u ON u.id = wi.assignee_id WHERE wi.id = ?",
        (item_id,),
    )


def collab_work_items(workspace_id, status=None, assignee_id=None,
                      milestone_id=None, parent_id=None):
    """Tasks in a workspace. parent_id=0 means top-level tasks only."""
    sql = ("SELECT wi.*, u.name AS assignee_name, p.title AS parent_title "
           "FROM work_items wi LEFT JOIN users u ON u.id = wi.assignee_id "
           "LEFT JOIN work_items p ON p.id = wi.parent_id "
           "WHERE wi.workspace_id = ?")
    params = [workspace_id]
    if status:
        sql += " AND wi.status = ?"
        params.append(status)
    if assignee_id:
        sql += " AND wi.assignee_id = ?"
        params.append(assignee_id)
    if milestone_id:
        sql += " AND wi.milestone_id = ?"
        params.append(milestone_id)
    if parent_id is not None:
        if parent_id == 0:
            sql += " AND wi.parent_id IS NULL"
        else:
            sql += " AND wi.parent_id = ?"
            params.append(parent_id)
    sql += " ORDER BY wi.created_at DESC"
    return _query_all(sql, tuple(params))


def collab_create_work_item(conn, workspace_id, title, description, assignee_id,
                            priority, due_date, created_by, milestone_id=None,
                            parent_id=None):
    c, commit = _conn_or_new(conn)
    try:
        cur = c.execute(
            "INSERT INTO work_items (workspace_id, parent_id, milestone_id, title, "
            "description, assignee_id, priority, due_date, created_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (workspace_id, parent_id, milestone_id, title, description,
             assignee_id, priority, due_date, created_by),
        )
        item_id = cur.lastrowid
        if commit:
            c.commit()
        return item_id
    finally:
        if commit:
            c.close()


def collab_update_work_item(conn, item_id, **fields):
    allowed = ("title", "description", "assignee_id", "priority", "due_date",
               "milestone_id", "parent_id")
    cols = {k: v for k, v in fields.items() if k in allowed}
    if not cols:
        return False
    sets = ", ".join(f"{k} = ?" for k in cols)
    params = list(cols.values()) + [item_id]
    c, commit = _conn_or_new(conn)
    try:
        cur = c.execute(
            f"UPDATE work_items SET {sets}, updated_at = datetime('now') "
            f"WHERE id = ?",
            params,
        )
        if commit:
            c.commit()
        return cur.rowcount > 0
    finally:
        if commit:
            c.close()


def collab_set_status(conn, item_id, status, blocked_reason=None):
    """Set status enforcing that 'blocked' requires a blocker reason."""
    if status not in _COLLAB_STATUSES:
        raise ValueError(f"invalid status: {status}")
    if status == "blocked" and not (blocked_reason and blocked_reason.strip()):
        raise ValueError("blocked status requires a blocker reason")
    c, commit = _conn_or_new(conn)
    try:
        cur = c.execute(
            "UPDATE work_items SET status = ?, blocked_reason = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (status, blocked_reason, item_id),
        )
        if commit:
            c.commit()
        return cur.rowcount > 0
    finally:
        if commit:
            c.close()


def collab_item_labels(work_item_id):
    return _query_all(
        "SELECT l.id, l.name, l.color FROM labels l "
        "JOIN work_item_labels wil ON wil.label_id = l.id "
        "WHERE wil.work_item_id = ? ORDER BY l.id",
        (work_item_id,),
    )


def collab_ws_labels_map(workspace_id):
    """Return {work_item_id: [label dicts]} for all items in a workspace."""
    rows = _query_all(
        "SELECT wil.work_item_id, l.id, l.name, l.color FROM work_item_labels wil "
        "JOIN labels l ON l.id = wil.label_id "
        "JOIN work_items wi ON wi.id = wil.work_item_id "
        "WHERE wi.workspace_id = ? ORDER BY l.id",
        (workspace_id,),
    )
    out = {}
    for r in rows:
        out.setdefault(r["work_item_id"], []).append(r)
    return out


def collab_ws_comment_counts(workspace_id):
    """Return {work_item_id: comment_count} for a workspace."""
    rows = _query_all(
        "SELECT c.work_item_id, COUNT(*) AS n FROM comments c "
        "JOIN work_items wi ON wi.id = c.work_item_id "
        "WHERE wi.workspace_id = ? AND c.work_item_id IS NOT NULL "
        "GROUP BY c.work_item_id",
        (workspace_id,),
    )
    return {r["work_item_id"]: r["n"] for r in rows}


def collab_ws_dependency_pairs(workspace_id):
    """Return [(work_item_id, depends_on_id)] for a workspace."""
    rows = _query_all(
        "SELECT wid.work_item_id, wid.depends_on_id FROM work_item_dependencies wid "
        "JOIN work_items wi ON wi.id = wid.work_item_id "
        "WHERE wi.workspace_id = ?",
        (workspace_id,),
    )
    return [(r["work_item_id"], r["depends_on_id"]) for r in rows]


def collab_item_dependencies(item_id):
    """Tasks that this item depends on."""
    return _query_all(
        "SELECT wi.id, wi.title, wi.status, wi.blocked_reason FROM work_items wi "
        "JOIN work_item_dependencies wid ON wid.depends_on_id = wi.id "
        "WHERE wid.work_item_id = ? ORDER BY wi.id",
        (item_id,),
    )


def collab_item_dependents(item_id):
    """Tasks that depend on this item."""
    return _query_all(
        "SELECT wi.id, wi.title, wi.status FROM work_items wi "
        "JOIN work_item_dependencies wid ON wid.work_item_id = wi.id "
        "WHERE wid.depends_on_id = ? ORDER BY wi.id",
        (item_id,),
    )


# ----------------------------------------------------------------- milestones

def collab_milestones(workspace_id, with_counts=False):
    sql = ("SELECT * FROM milestones WHERE workspace_id = ? ORDER BY sort_order, id")
    if with_counts:
        sql = ("SELECT m.*, (SELECT COUNT(*) FROM work_items wi WHERE wi.milestone_id = m.id) "
               "AS num_tasks FROM milestones m WHERE m.workspace_id = ? ORDER BY m.sort_order, m.id")
    return _query_all(sql, (workspace_id,))


def collab_create_milestone(conn, workspace_id, title, due_date=None):
    c, commit = _conn_or_new(conn)
    try:
        cur = c.execute(
            "INSERT INTO milestones (workspace_id, title, due_date) VALUES (?, ?, ?)",
            (workspace_id, title, due_date),
        )
        mid = cur.lastrowid
        if commit:
            c.commit()
        return mid
    finally:
        if commit:
            c.close()


def collab_delete_milestone(conn, milestone_id):
    c, commit = _conn_or_new(conn)
    try:
        c.execute("UPDATE work_items SET milestone_id = NULL WHERE milestone_id = ?",
                  (milestone_id,))
        cur = c.execute("DELETE FROM milestones WHERE id = ?", (milestone_id,))
        if commit:
            c.commit()
        return cur.rowcount > 0
    finally:
        if commit:
            c.close()


# --------------------------------------------------------------------- labels

def collab_labels(workspace_id):
    return _query_all(
        "SELECT * FROM labels WHERE workspace_id = ? ORDER BY id", (workspace_id,)
    )


def collab_create_label(conn, workspace_id, name, color="#64748b"):
    c, commit = _conn_or_new(conn)
    try:
        cur = c.execute(
            "INSERT INTO labels (workspace_id, name, color) VALUES (?, ?, ?)",
            (workspace_id, name, color),
        )
        if commit:
            c.commit()
        return cur.lastrowid
    finally:
        if commit:
            c.close()


def collab_delete_label(conn, label_id):
    c, commit = _conn_or_new(conn)
    try:
        c.execute("DELETE FROM work_item_labels WHERE label_id = ?", (label_id,))
        cur = c.execute("DELETE FROM labels WHERE id = ?", (label_id,))
        if commit:
            c.commit()
        return cur.rowcount > 0
    finally:
        if commit:
            c.close()


def collab_toggle_label(conn, work_item_id, label_id):
    """Attach a label to an item; toggle it off if already attached. Returns attached."""
    c, commit = _conn_or_new(conn)
    try:
        cur = c.execute(
            "INSERT OR IGNORE INTO work_item_labels (work_item_id, label_id) "
            "VALUES (?, ?)",
            (work_item_id, label_id),
        )
        attached = cur.rowcount > 0
        if not attached:
            c.execute(
                "DELETE FROM work_item_labels WHERE work_item_id = ? AND label_id = ?",
                (work_item_id, label_id),
            )
        if commit:
            c.commit()
        return attached
    finally:
        if commit:
            c.close()


# ---------------------------------------------------------------- dependencies

def collab_add_dependency(conn, work_item_id, depends_on_id):
    if work_item_id == depends_on_id:
        raise ValueError("a task cannot depend on itself")
    c, commit = _conn_or_new(conn)
    try:
        cur = c.execute(
            "INSERT OR IGNORE INTO work_item_dependencies (work_item_id, depends_on_id) "
            "VALUES (?, ?)",
            (work_item_id, depends_on_id),
        )
        if commit:
            c.commit()
        return cur.rowcount > 0
    finally:
        if commit:
            c.close()


def collab_remove_dependency(conn, work_item_id, depends_on_id):
    c, commit = _conn_or_new(conn)
    try:
        cur = c.execute(
            "DELETE FROM work_item_dependencies WHERE work_item_id = ? AND depends_on_id = ?",
            (work_item_id, depends_on_id),
        )
        if commit:
            c.commit()
        return cur.rowcount > 0
    finally:
        if commit:
            c.close()


# -------------------------------------------------------------------- comments

def collab_comments(workspace_id, work_item_id=None):
    if work_item_id is None:
        return _query_all(
            "SELECT c.*, u.name AS author_name FROM comments c JOIN users u ON u.id = c.author_id "
            "WHERE c.workspace_id = ? AND c.work_item_id IS NULL ORDER BY c.id ASC",
            (workspace_id,),
        )
    return _query_all(
        "SELECT c.*, u.name AS author_name FROM comments c JOIN users u ON u.id = c.author_id "
        "WHERE c.workspace_id = ? AND c.work_item_id = ? ORDER BY c.id ASC",
        (workspace_id, work_item_id),
    )


def collab_add_comment(conn, workspace_id, author_id, body, work_item_id=None):
    c, commit = _conn_or_new(conn)
    try:
        if work_item_id is None:
            cur = c.execute(
                "INSERT INTO comments (workspace_id, author_id, body) VALUES (?, ?, ?)",
                (workspace_id, author_id, body),
            )
        else:
            cur = c.execute(
                "INSERT INTO comments (workspace_id, work_item_id, author_id, body) "
                "VALUES (?, ?, ?, ?)",
                (workspace_id, work_item_id, author_id, body),
            )
        if commit:
            c.commit()
        return cur.lastrowid
    finally:
        if commit:
            c.close()


# -------------------------------------------------------- information requests

def collab_requests(workspace_id, status=None):
    if status:
        return _query_all(
            "SELECT r.*, requester.name AS requester_name, recipient.name AS recipient_name "
            "FROM information_requests r "
            "JOIN users requester ON requester.id = r.requester_id "
            "JOIN users recipient ON recipient.id = r.recipient_id "
            "WHERE r.workspace_id = ? AND r.status = ? ORDER BY r.id ASC",
            (workspace_id, status),
        )
    return _query_all(
        "SELECT r.*, requester.name AS requester_name, recipient.name AS recipient_name "
        "FROM information_requests r "
        "JOIN users requester ON requester.id = r.requester_id "
        "JOIN users recipient ON recipient.id = r.recipient_id "
        "WHERE r.workspace_id = ? ORDER BY r.id ASC",
        (workspace_id,),
    )


def collab_create_request(conn, workspace_id, work_item_id, requester_id,
                          recipient_id, what_needed, why_needed, needed_by):
    c, commit = _conn_or_new(conn)
    try:
        cur = c.execute(
            "INSERT INTO information_requests (workspace_id, work_item_id, requester_id, "
            "recipient_id, what_needed, why_needed, needed_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (workspace_id, work_item_id, requester_id, recipient_id,
             what_needed, why_needed, needed_by),
        )
        if commit:
            c.commit()
        return cur.lastrowid
    finally:
        if commit:
            c.close()


def collab_update_request_status(conn, request_id, status):
    if status not in _COLLAB_REQUEST_STATUSES:
        raise ValueError(f"invalid request status: {status}")
    c, commit = _conn_or_new(conn)
    try:
        cur = c.execute(
            "UPDATE information_requests SET status = ? WHERE id = ?",
            (status, request_id),
        )
        if commit:
            c.commit()
        return cur.rowcount > 0
    finally:
        if commit:
            c.close()


# ------------------------------------------------------------------- daily logs

def collab_daily_log_for(workspace_id, user_id, log_date):
    return _query_one(
        "SELECT * FROM daily_logs WHERE workspace_id = ? AND user_id = ? AND log_date = ?",
        (workspace_id, user_id, log_date),
    )


def collab_upsert_daily_log(conn, workspace_id, user_id, log_date, summary,
                            minutes_spent, blocked_by, plan_for_tomorrow):
    c, commit = _conn_or_new(conn)
    try:
        cur = c.execute(
            "INSERT INTO daily_logs (workspace_id, user_id, log_date, summary, "
            "minutes_spent, blocked_by, plan_for_tomorrow) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(workspace_id, user_id, log_date) DO UPDATE SET "
            "summary = excluded.summary, minutes_spent = excluded.minutes_spent, "
            "blocked_by = excluded.blocked_by, "
            "plan_for_tomorrow = excluded.plan_for_tomorrow",
            (workspace_id, user_id, log_date, summary, minutes_spent,
             blocked_by, plan_for_tomorrow),
        )
        if commit:
            c.commit()
        return cur.lastrowid
    finally:
        if commit:
            c.close()


def collab_daily_logs(workspace_id, log_date=None):
    if log_date:
        return _query_all(
            "SELECT l.*, u.name AS user_name FROM daily_logs l JOIN users u ON u.id = l.user_id "
            "WHERE l.workspace_id = ? AND l.log_date = ? ORDER BY l.id ASC",
            (workspace_id, log_date),
        )
    return _query_all(
        "SELECT l.*, u.name AS user_name FROM daily_logs l JOIN users u ON u.id = l.user_id "
        "WHERE l.workspace_id = ? ORDER BY l.log_date DESC, l.id ASC LIMIT 500",
        (workspace_id,),
    )


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
