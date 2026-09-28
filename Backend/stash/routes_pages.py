"""HTML page routes for Stash.

Follows the app's page conventions: authenticated pages redirect to the
welcome splash when there's no session, and every page passes an `active_nav`
so the shared header highlights the right tab.
"""

import os
import sys
from functools import wraps

from flask import Blueprint, abort, redirect, render_template, request, session, url_for

if os.path.dirname(os.path.dirname(os.path.abspath(__file__))) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import planner

import db

from . import repository
from .ai import client
from .config import StashConfig

stash_pages = Blueprint("stash", __name__)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("welcome"))
        return view(*args, **kwargs)

    return wrapped


def _provide_hints(uid):
    """Small context for the pages that explain how Stash generates cards."""
    return client.describe_provider_availability(uid)


@stash_pages.get("/stash")
@login_required
def library():
    uid = session["user_id"]
    search = (request.args.get("q") or "").strip()
    course_id = (request.args.get("course_id") or "").strip() or None
    docs = repository.list_documents(uid, search=search, course_id=course_id)
    saved_count = len(repository.saved_cards(uid, limit=500))
    return render_template(
        "stash/library.html",
        user=db.get_user(uid) or {},
        active_nav="stash",
        documents=docs,
        search=search,
        course_id=course_id,
        courses=planner.list_courses(uid),
        saved_count=saved_count,
        hints=_provide_hints(uid),
    )


@stash_pages.get("/stash/upload")
@login_required
def upload():
    uid = session["user_id"]
    hints = _provide_hints(uid)
    hints["max_size_mb"] = max(1, StashConfig.max_file_bytes // (1024 * 1024))
    hints["max_slides"] = StashConfig.max_pptx_slides
    hints["max_pages"] = StashConfig.max_pdf_pages
    return render_template(
        "stash/upload.html",
        user=db.get_user(uid) or {},
        active_nav="stash",
        courses=planner.list_courses(uid),
        hints=hints,
    )


@stash_pages.get("/stash/saved")
@login_required
def saved():
    uid = session["user_id"]
    course_id = (request.args.get("course_id") or "").strip() or None
    cards = repository.saved_cards(uid, course_id=course_id)
    return render_template(
        "stash/saved.html",
        user=db.get_user(uid) or {},
        active_nav="stash",
        cards=[_public_card(c) for c in cards],
        course_id=course_id,
        courses=planner.list_courses(uid),
    )


@stash_pages.get("/stash/processing")
@login_required
def processing():
    """Overview of documents still being generated (uploaded a moment ago)."""
    uid = session["user_id"]
    docs = repository.list_documents(uid)
    active = [d for d in docs if d["status"] in ("queued", "processing")]
    return render_template(
        "stash/processing.html",
        user=db.get_user(uid) or {},
        active_nav="stash",
        documents=active,
    )


@stash_pages.get("/stash/reader/<doc_id>")
@login_required
def reader(doc_id):
    uid = session["user_id"]
    doc = repository.get_document(doc_id, user_id=uid)
    if not doc:
        abort(404)
    sections = repository.list_sections(doc_id)
    cards = repository.list_cards(doc_id, limit=20)
    states = repository.states_for_document(uid, doc_id)
    notes = {
        row["card_id"]: row["content"]
        for row in db._query_all(
            "SELECT card_id, content FROM stash_notes WHERE user_id = ?", (uid,)
        )
    }
    highlights = {
        cid: db._query_all(
            "SELECT * FROM stash_highlights WHERE user_id = ? AND card_id = ?",
            (uid, cid),
        )
        for cid in [c["id"] for c in cards]
    }
    progress = repository.get_progress(uid, doc_id)
    return render_template(
        "stash/reader.html",
        user=db.get_user(uid) or {},
        active_nav="stash",
        document=_public_doc(doc),
        sections=sections,
        cards=[_merge_state(_public_card(c), states.get(c["id"])) for c in cards],
        notes=notes,
        highlights=highlights,
        progress=progress,
        page_count=doc.get("page_count"),
        hints=_provide_hints(uid),
    )


# ---------------------------------------------------------------- shaping


def _public_doc(doc):
    """The subset of a document the templates may render."""
    return {
        "id": doc["id"],
        "title": doc["title"],
        "original_filename": doc["original_filename"],
        "file_type": doc["file_type"],
        "file_size_bytes": doc["file_size_bytes"],
        "status": doc["status"],
        "progress_percent": doc.get("progress_percent") or 0,
        "error_message": doc.get("error_message"),
        "total_cards": doc.get("total_cards") or 0,
        "page_count": doc.get("page_count"),
        "course_id": doc.get("course_id"),
        "course_title": doc.get("course_title"),
        "created_at": doc.get("created_at"),
        "model_used": doc.get("model_used"),
    }


def _public_card(card):
    return {
        "id": card["id"],
        "position": card["position"],
        "card_type": card["card_type"],
        "title": card["title"],
        "body": card["body"],
        "example": card.get("example"),
        "key_term": card.get("key_term"),
        "key_term_definition": card.get("key_term_definition"),
        "source_page_start": card.get("source_page_start"),
        "source_page_end": card.get("source_page_end"),
        "source_slide": card.get("source_slide"),
        "is_flagged": 1 if card.get("is_flagged") else 0,
        "section_id": card.get("section_id"),
        "document_id": card.get("document_id"),
        "document_title": card.get("document_title"),
    }


def _merge_state(card, state):
    """Attach the current user's per-card state flags to a public card dict."""
    if state:
        card["saved"] = 1 if state.get("is_saved") else 0
        card["status"] = state.get("status") or ""
        card["last_seen_at"] = state.get("last_seen_at")
        card["got_it_at"] = state.get("got_it_at")
    else:
        card["saved"] = 0
        card["status"] = ""
    return card
