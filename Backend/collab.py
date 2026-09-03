"""Collaboration and quiz data layer for the Study Planner.

Thin wrapper over the shared SQLite layer (db.py). Stores memberships, shared
materials metadata, quizzes and attempts. Files themselves live on disk under
Database/uploads/<subject-slug>/ (see app.py).
"""

import os
import sys

# Make the Backend package importable so `db` resolves.
if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db


# ---------------------------------------------------------------- membership

def get_members(slug):
    """Return the list of member user ids for a subject."""
    return db.get_members(slug)


def is_member(slug, user_id):
    return user_id in db.get_members(slug)


def add_member(slug, user_id):
    db.add_member(slug, user_id)


def remove_member(slug, user_id):
    db.remove_member(slug, user_id)


def get_subject_code(slug):
    """Return (and lazily create) the invite code for a subject."""
    return db.get_subject_code(slug)


def subject_code_match(slug, code):
    return bool(code) and str(code).strip().upper() == db.get_subject_code(slug)


# ---------------------------------------------------------------- materials

def get_materials(slug):
    """Return materials metadata for a subject, newest first."""
    return db.get_materials(slug)


def add_material(slug, filename, uploader_id):
    db.add_material(slug, filename, uploader_id)


# ---------------------------------------------------------------- quizzes

def list_quizzes(slug):
    return db.list_quizzes(slug)


def get_quiz(quiz_id):
    return db.get_quiz(quiz_id)


def find_quiz_by_code(code):
    return db.find_quiz_by_code(code)


def create_quiz(slug, title, description, questions, creator_id):
    return db.create_quiz(slug, title, description, questions, creator_id)


def add_attempt(quiz_id, user_id, score, total):
    db.add_attempt(quiz_id, user_id, score, total)


def get_attempts(quiz_id):
    return db.get_attempts(quiz_id)


def user_best_score(quiz_id, user_id):
    attempts = db.get_attempts(quiz_id)
    mine = [a for a in attempts if a["user_id"] == user_id]
    if not mine:
        return None
    return max(a["score"] for a in mine)