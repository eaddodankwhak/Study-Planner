"""Collaboration and quiz data layer for the Study Planner.

Stores memberships, shared materials metadata, quizzes and attempts in a
single JSON file (Database/collab.json). Files themselves live on disk under
Database/uploads/<subject-slug>/ (see app.py).
"""

import json
import os
import random
import string

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_DIR = os.path.join(BASE_DIR, "..", "Database")
COLLAB_FILE = os.path.join(DATABASE_DIR, "collab.json")


def _load():
    if not os.path.exists(COLLAB_FILE):
        return {
            "memberships": {},
            "subject_codes": {},
            "materials": {},
            "quizzes": {},
            "attempts": [],
        }
    with open(COLLAB_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data):
    os.makedirs(DATABASE_DIR, exist_ok=True)
    with open(COLLAB_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _random_code(length, prefix=""):
    alphabet = string.ascii_uppercase + string.digits
    suffix = "".join(random.choice(alphabet) for _ in range(length))
    return f"{prefix}{suffix}"


# ---------------------------------------------------------------- membership

def get_members(slug):
    """Return the list of member user ids for a subject."""
    return list(_load().get("memberships", {}).get(slug, []))


def is_member(slug, user_id):
    return user_id in get_members(slug)


def add_member(slug, user_id):
    data = _load()
    members = data["memberships"].setdefault(slug, [])
    if user_id not in members:
        members.append(user_id)
        _save(data)


def remove_member(slug, user_id):
    data = _load()
    members = data["memberships"].get(slug, [])
    if user_id in members:
        members.remove(user_id)
        _save(data)


def get_subject_code(slug):
    """Return (and lazily create) the invite code for a subject."""
    data = _load()
    if slug not in data.get("subject_codes", {}):
        data.setdefault("subject_codes", {})[slug] = _random_code(4, prefix="SP-")
        _save(data)
    return data["subject_codes"][slug]


def subject_code_match(slug, code):
    return code and str(code).strip().upper() == get_subject_code(slug)


# ---------------------------------------------------------------- materials

def get_materials(slug):
    """Return materials metadata for a subject, newest first."""
    data = _load()
    return list(reversed(data.get("materials", {}).get(slug, [])))


def add_material(slug, filename, uploader_id):
    data = _load()
    if slug not in data.get("materials", {}):
        data.setdefault("materials", {})[slug] = []
    data["materials"][slug].append(
        {"filename": filename, "uploader_id": uploader_id, "date": None}
    )
    _save(data)


# ---------------------------------------------------------------- quizzes

def list_quizzes(slug):
    data = _load()
    return [q for q in data.get("quizzes", {}).values() if q["subject"] == slug]


def get_quiz(quiz_id):
    return _load().get("quizzes", {}).get(quiz_id)


def find_quiz_by_code(code):
    code = str(code or "").strip().upper()
    data = _load()
    for q in data.get("quizzes", {}).values():
        if q.get("invite_code") == code:
            return q
    return None


def create_quiz(slug, title, description, questions, creator_id):
    data = _load()
    quiz_id = _random_code(8).lower()
    quiz = {
        "id": quiz_id,
        "subject": slug,
        "title": title,
        "description": description,
        "creator": creator_id,
        "invite_code": _random_code(6),
        "questions": questions,
    }
    data.setdefault("quizzes", {})[quiz_id] = quiz
    _save(data)
    return quiz


def add_attempt(quiz_id, user_id, score, total):
    data = _load()
    data.setdefault("attempts", []).append(
        {
            "id": _random_code(10).lower(),
            "quiz_id": quiz_id,
            "user_id": user_id,
            "score": score,
            "total": total,
            "date": None,
        }
    )
    _save(data)


def get_attempts(quiz_id):
    data = _load()
    return [a for a in data.get("attempts", []) if a["quiz_id"] == quiz_id]


def user_best_score(quiz_id, user_id):
    attempts = get_attempts(quiz_id)
    mine = [a for a in attempts if a["user_id"] == user_id]
    if not mine:
        return None
    return max(a["score"] for a in mine)
