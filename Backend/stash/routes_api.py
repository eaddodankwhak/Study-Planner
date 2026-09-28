"""JSON API blueprint for Stash.

Every endpoint requires a logged-in session and authorizes against the
document's owner, so one user can never read or delete another's stash.
Uploads stream to disk with a size + content-type guard before anything is
queued. Responses are plain JSON dicts.
"""

import json
import os
import sys

from flask import Blueprint, abort, jsonify, request, session, url_for

if os.path.dirname(os.path.dirname(os.path.abspath(__file__))) not in sys.path:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db  # noqa: E402
import courses  # noqa: E402

from . import quiz  # noqa: E402
from . import repository  # noqa: E402
from . import security  # noqa: E402
from . import service  # noqa: E402
from .config import StashConfig  # noqa: E402
from .worker import ensure_worker  # noqa: E402

stash_api = Blueprint("stash_api", __name__, url_prefix="/api/stash")


def _uid():
    return session.get("user_id")


def _doc_or_404(doc_id, uid):
    doc = repository.get_document(doc_id, user_id=uid)
    if not doc:
        abort(404)
    return doc


def _public_doc(doc):
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
        "model_used": doc.get("model_used"),
        "created_at": doc.get("created_at"),
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


# ------------------------------------------------------------------- upload


@stash_api.post("/documents")
def upload_document():
    uid = _uid()
    if not uid:
        return jsonify({"ok": False, "error": "Sign in to use Stash."}), 401

    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        return jsonify({"ok": False, "error": "Choose a PDF or PowerPoint file."}), 400

    course_id = (request.form.get("course_id") or "").strip() or None
    if course_id and not courses.get_course(uid, course_id):
        return jsonify({"ok": False, "error": "That course is not yours."}), 400

    try:
        blob = security.read_upload(uploaded)
        doc = service.create_document(uid, uploaded.filename, blob, course_id=course_id)
    except security.StashSecurityError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except service.DuplicateDocumentError as exc:
        return jsonify({"ok": False, "error": str(exc), "duplicate": True}), 409

    ensure_worker()
    return jsonify({"ok": True, "document": _public_doc(doc)}), 201


# ---------------------------------------------------------------- documents


@stash_api.get("/documents")
def list_documents():
    uid = _uid()
    if not uid:
        return jsonify({"ok": False, "error": "Sign in."}), 401
    search = (request.args.get("q") or "").strip() or None
    course_id = (request.args.get("course_id") or "").strip() or None
    docs = repository.list_documents(uid, search=search, course_id=course_id)
    return jsonify({
        "ok": True,
        "documents": [_public_doc(d) for d in docs],
        "text_size": (db.get_settings(uid) or {}).get("appearance", {}).get("text_size", "default"),
    })


@stash_api.get("/documents/<doc_id>")
def document_detail(doc_id):
    uid = _uid()
    if not uid:
        return jsonify({"ok": False, "error": "Sign in."}), 401
    doc = _doc_or_404(doc_id, uid)
    return jsonify({"ok": True, "document": _public_doc(doc)})


@stash_api.delete("/documents/<doc_id>")
def delete_document(doc_id):
    uid = _uid()
    if not uid:
        return jsonify({"ok": False, "error": "Sign in."}), 401
    _doc_or_404(doc_id, uid)
    service.delete_document(doc_id)
    return jsonify({"ok": True})


@stash_api.post("/documents/<doc_id>/regenerate")
def regenerate_document(doc_id):
    uid = _uid()
    if not uid:
        return jsonify({"ok": False, "error": "Sign in."}), 401
    doc = _doc_or_404(doc_id, uid)
    body = request.get_json(silent=True) or {}
    chapters = body.get("chapters")
    try:
        if chapters:
            indexes = [int(v) for v in chapters]
            doc = service.regenerate(doc_id, section_indexes=set(indexes))
        else:
            doc = service.regenerate(doc_id)
    except service.PermanentError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    ensure_worker()
    return jsonify({"ok": True, "document": _public_doc(doc)})


# ------------------------------------------------------------------- reading


@stash_api.get("/documents/<doc_id>/cards")
def list_cards(doc_id):
    uid = _uid()
    if not uid:
        return jsonify({"ok": False, "error": "Sign in."}), 401
    _doc_or_404(doc_id, uid)
    try:
        limit = min(int(request.args.get("limit", "20")), 40)
    except ValueError:
        limit = 20
    cursor = request.args.get("cursor")
    cursor = int(cursor) if cursor and str(cursor).isdigit() else None

    cards = repository.list_cards(doc_id, limit=limit, cursor=cursor)
    states = repository.states_for_document(uid, doc_id)
    total = repository.count_cards(doc_id)
    has_more = len(cards) == limit
    next_cursor = cards[-1]["position"] if (cards and has_more) else None
    return jsonify({
        "ok": True,
        "cards": [_with_state(_public_card(c), states.get(c["id"])) for c in cards],
        "next_cursor": next_cursor,
        "has_more": has_more,
        "total": total,
    })


@stash_api.get("/documents/<doc_id>/toc")
def document_toc(doc_id):
    uid = _uid()
    if not uid:
        return jsonify({"ok": False, "error": "Sign in."}), 401
    _doc_or_404(doc_id, uid)
    return jsonify({
        "ok": True,
        "sections": [
            {
                "id": s["id"],
                "title": s["title"],
                "level": s["level"],
                "position": s["position"],
                "page_start": s["page_start"],
                "page_end": s["page_end"],
                "card_count": s["card_count"],
            }
            for s in repository.list_sections(doc_id)
        ],
    })


@stash_api.get("/documents/<doc_id>/quiz")
def document_quiz(doc_id):
    uid = _uid()
    if not uid:
        return jsonify({"ok": False, "error": "Sign in."}), 401
    _doc_or_404(doc_id, uid)
    payload = quiz.build_quiz(doc_id)
    if not payload["questions"]:
        return jsonify({"ok": False, "error": "Not enough cards yet to build a quiz — save a few more, then retry."}), 200
    return jsonify({"ok": True, "quiz": payload})


# ----------------------------------------------------------------- progress


@stash_api.post("/documents/<doc_id>/progress")
def save_progress(doc_id):
    uid = _uid()
    if not uid:
        return jsonify({"ok": False, "error": "Sign in."}), 401
    _doc_or_404(doc_id, uid)
    body = request.get_json(silent=True) or {}
    repository.upsert_progress(
        uid, doc_id,
        last_position=body.get("last_position"),
        cards_seen=body.get("cards_seen"),
        cards_got_it=body.get("cards_got_it"),
    )
    return jsonify({"ok": True})


@stash_api.get("/usage")
def usage():
    uid = _uid()
    if not uid:
        return jsonify({"ok": False, "error": "Sign in."}), 401
    used = repository.daily_token_total(uid)
    return jsonify({
        "ok": True,
        "used": used,
        "cap": StashConfig.daily_token_cap,
        "remaining": max(0, StashConfig.daily_token_cap - used),
    })


# --------------------------------------------------------------- card state


@stash_api.post("/cards/<card_id>/save")
def card_save(card_id):
    return _card_state_action(card_id, "is_saved", 1)


@stash_api.post("/cards/<card_id>/unsave")
def card_unsave(card_id):
    return _card_state_action(card_id, "is_saved", 0)


@stash_api.post("/cards/<card_id>/gotit")
def card_got_it(card_id):
    _require_card_owner(card_id)
    state = repository.get_card_state(_uid(), card_id)
    if state and state.get("status") == "got_it":
        repository.set_card_status(_uid(), card_id, "")
    else:
        repository.set_card_status(_uid(), card_id, "got_it")
        repository.record_card_seen(_uid(), card_id)
    return jsonify({"ok": True})


@stash_api.post("/cards/<card_id>/review")
def card_review(card_id):
    _require_card_owner(card_id)
    repository.set_card_status(_uid(), card_id, "review")
    return jsonify({"ok": True})


@stash_api.post("/cards/<card_id>/seen")
def card_seen(card_id):
    _require_card_owner(card_id)
    repository.record_card_seen(_uid(), card_id)
    return jsonify({"ok": True})


@stash_api.get("/saved")
def saved_cards():
    uid = _uid()
    if not uid:
        return jsonify({"ok": False, "error": "Sign in."}), 401
    cards = repository.saved_cards(uid, limit=200)
    return jsonify({
        "ok": True,
        "cards": [_public_card(c) for c in cards],
    })


# ---------------------------------------------------------------- notes

@stash_api.put("/cards/<card_id>/note")
def put_note(card_id):
    _require_card_owner(card_id)
    body = request.get_json(silent=True) or {}
    content = (body.get("content") or "").strip()
    if len(content) > 5000:
        return jsonify({"ok": False, "error": "Notes are capped at 5,000 characters."}), 400
    repository.upsert_note(_uid(), card_id, content)
    return jsonify({"ok": True})


@stash_api.delete("/cards/<card_id>/note")
def remove_note(card_id):
    _require_card_owner(card_id)
    repository.delete_note(_uid(), card_id)
    return jsonify({"ok": True})


# ------------------------------------------------------------- highlights

@stash_api.post("/cards/<card_id>/highlight")
def add_highlight(card_id):
    _require_card_owner(card_id)
    body = request.get_json(silent=True) or {}
    field = (body.get("field") or "body").lower()
    if field not in {"title", "body", "example"}:
        field = "body"
    color = (body.get("color") or "yellow").lower()
    if color not in {"yellow", "green", "blue", "pink"}:
        color = "yellow"
    try:
        start = int(body.get("start_offset", 0))
        end = int(body.get("end_offset", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Bad highlight range."}), 400
    hid = repository.add_highlight(
        _uid(), card_id, field, start, end, color=color,
        note=(body.get("note") or "").strip() or None,
    )
    return jsonify({"ok": True, "highlight_id": hid})


@stash_api.delete("/highlights/<h_id>")
def remove_highlight(h_id):
    uid = _uid()
    if not uid:
        return jsonify({"ok": False, "error": "Sign in."}), 401
    repository.delete_highlight(uid, h_id)
    return jsonify({"ok": True})


@stash_api.get("/cards/<card_id>")
def card_detail(card_id):
    uid = _uid()
    if not uid:
        return jsonify({"ok": False, "error": "Sign in."}), 401
    card = repository.get_card(card_id)
    if not card or not _owns(uid, card):
        abort(404)
    note = repository.get_note(uid, card_id)
    highlights = repository.list_highlights(uid, card_id)
    state = repository.get_card_state(uid, card_id)
    result = _public_card(card)
    result["ask_prompt"] = (
        f'From the study card "{card["title"]}" (doc: {card.get("document_id")}): '
        f'source says: "{card["body"]}"'
    )
    result["ask_url"] = url_for("ai_hub", _external=False) + "#ask=" + _b64(card)
    result["note"] = note["content"] if note else None
    result["highlights"] = [
        {k: h[k] for k in ("id", "field", "start_offset", "end_offset", "color", "note")}
        for h in highlights
    ]
    if state:
        result["saved"] = 1 if state.get("is_saved") else 0
        result["status"] = state.get("status") or ""
    else:
        result["saved"] = 0
        result["status"] = ""
    return jsonify({"ok": True, "card": result})


# ---------------------------------------------------------------- helpers


def _card_state_action(card_id, col, value):
    uid = _uid()
    if not uid:
        return jsonify({"ok": False, "error": "Sign in."}), 401
    _require_card_owner(card_id)
    if col == "is_saved":
        repository.set_card_saved(uid, card_id, value)
    repository.record_card_seen(uid, card_id)
    return jsonify({"ok": True})


def _require_card_owner(card_id):
    """Abort unless the logged-in user owns the card's document."""
    uid = _uid()
    if not uid:
        abort(401)
    card = repository.get_card(card_id)
    if not card or not _owns(uid, card):
        abort(404)


def _owns(uid, card):
    doc = repository.get_document(card["document_id"])
    return bool(doc and doc["user_id"] == uid)


def _with_state(card, state):
    if state:
        card["saved"] = 1 if state.get("is_saved") else 0
        card["status"] = state.get("status") or ""
    else:
        card["saved"] = 0
        card["status"] = ""
    return card


def _b64(card):
    import base64

    payload = json.dumps({
        "text": f'{card["title"]}: {card["body"]}',
        "context": "ask about this study card",
    })
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("utf-8")