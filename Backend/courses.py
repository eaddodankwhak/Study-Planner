"""Courses: the single source of truth for what a student is taking.

Every piece of UI that asks "what are this student's courses" goes through
`get_user_courses`; every write goes through `upsert_course`. The dashboard
"My Subjects" cards, the /courses CRUD page, and onboarding all converge on
the same `courses` table, so the views structurally cannot disagree again.

`id` is a TEXT uuid (matching the app's uuid-based ids) so planner
events/deadlines/tasks and db notes/sessions keep linking to courses without
an id-space migration.
"""

import uuid

import db

COURSE_COLORS = ["navy", "teal", "orange", "green", "purple", "red"]
DEFAULT_TERM = "Fall 2026"


def auto_color(course_code):
    """Deterministic palette pick for an 'Auto' colour.

    Derived from the code (not insertion order) so a course keeps the same
    card hue across page loads, machines, and re-renders.
    """
    n = sum(ord(ch) for ch in course_code)
    return COURSE_COLORS[n % len(COURSE_COLORS)]


def _to_dict(row):
    """SQLite row -> dict with the friendly keys templates already expect.

    Stub rows (from onboarding) have NULL title/lecturer/etc.; we surfaces
    "To be assigned" instead of an empty grey gap.
    """
    d = dict(row)
    code = d.get("course_code") or ""
    d["code"] = code
    d["title"] = d.get("title") or "To be assigned"
    d["instructor"] = d.get("lecturer") or "To be assigned"
    d["credits"] = d.get("credits") or 0
    d["term"] = d.get("term") or DEFAULT_TERM
    d["color"] = d.get("color") or auto_color(code)
    return d


def get_user_courses(user_id):
    """The one place course lists are fetched — dashboard and /courses both
    call this, so the two pages render the same data by construction."""
    rows = db._query_all(
        "SELECT * FROM courses WHERE user_id = ? ORDER BY course_code",
        (user_id,),
    )
    return [_to_dict(r) for r in rows]


def get_course(user_id, course_id):
    row = db._query_one(
        "SELECT * FROM courses WHERE user_id = ? AND id = ?",
        (user_id, course_id),
    )
    return _to_dict(row) if row else None


def all_courses():
    """Raw rows across all users (used for resolver-style lookups)."""
    return db._query_all("SELECT * FROM courses")


def upsert_course(user_id, course_code, id=None, **fields):
    """Create-or-update one course row for a user.

    Used by both onboarding (code-only stubs) and the /courses Add form (full
    details). The UNIQUE(user_id, course_code) constraint plus this upsert
    means completing a stub later updates the SAME row instead of duplicating
    it. Incoming blank/NULL fields never clobber already-saved values.

    Normalisation is enforced at write time in one place: the code is stripped,
    whitespace-collapsed, and upper-cased, and an "Auto" colour is resolved to a
    concrete palette token here rather than on every render.
    """
    code = db.normalize_course_code(course_code)
    if not code:
        raise ValueError("A non-empty course code is required.")

    color = (fields.get("color") or "").strip()
    if not color or color.lower() == "auto":
        color = auto_color(code)
    term = (fields.get("term") or "").strip() or DEFAULT_TERM
    new_id = id or uuid.uuid4().hex

    conn = db._conn_context()
    try:
        conn.execute(
            """
            INSERT INTO courses
                (id, user_id, course_code, title, lecturer, credits,
                 schedule, description, color, term)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id, course_code) DO UPDATE SET
                title       = COALESCE(NULLIF(excluded.title, ''), courses.title),
                lecturer    = COALESCE(NULLIF(excluded.lecturer, ''), courses.lecturer),
                credits     = CASE WHEN excluded.credits > 0 THEN excluded.credits
                                   ELSE courses.credits END,
                schedule    = COALESCE(NULLIF(excluded.schedule, ''), courses.schedule),
                description = COALESCE(NULLIF(excluded.description, ''), courses.description),
                color       = COALESCE(NULLIF(excluded.color, ''), courses.color),
                term        = COALESCE(NULLIF(excluded.term, ''), courses.term)
            """,
            (
                new_id,
                user_id,
                code,
                (fields.get("title") or "").strip() or None,
                (fields.get("lecturer") or "").strip() or None,
                int(fields.get("credits") or 0),
                (fields.get("schedule") or "").strip() or None,
                (fields.get("description") or "").strip() or None,
                color,
                term,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM courses WHERE user_id = ? AND course_code = ?",
            (user_id, code),
        ).fetchone()
    finally:
        conn.close()
    return _to_dict(row)