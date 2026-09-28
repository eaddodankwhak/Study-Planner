"""Persistence access for the Stash feature.

Thin wrappers over the shared Backend/db.py connection helpers, scoped to the
stash_* tables. All functions accept/return plain dicts. Ownership checks are
done inside every user-scoped read/write so callers cannot read another user's
data even if a dangling id slips through.
"""

import uuid

import db

from .config import StashConfig, now_iso

#: Columns we allow update_document to mutate (whitelist keeps the API honest).
DOCUMENT_UPDATEABLE = {
    "title",
    "status",
    "progress_percent",
    "error_message",
    "total_cards",
    "page_count",
    "model_used",
    "provider_used",
    "preferred_provider",
    "preferred_model",
    "input_tokens",
    "output_tokens",
    "prompt_version",
    "storage_key",
}


def new_id():
    return uuid.uuid4().hex


def _conn():
    return db._conn_context()


def _commit(conn):
    conn.commit()


# --------------------------------------------------------------- documents

def create_document(user_id, *, title, original_filename, file_type, file_size_bytes,
                    storage_key, file_sha256, course_id=None,
                    preferred_provider=None, preferred_model=None):
    doc_id = new_id()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO stash_documents "
            "(id, user_id, course_id, title, original_filename, file_type,"
            " file_size_bytes, storage_key, file_sha256, status,"
            " preferred_provider, preferred_model)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)",
            (doc_id, user_id, course_id, title, original_filename, file_type,
             file_size_bytes, storage_key, file_sha256,
             preferred_provider, preferred_model),
        )
        _commit(conn)
    finally:
        conn.close()
    return get_document(doc_id)


def get_document(doc_id, user_id=None):
    if user_id:
        return db._query_one(
            "SELECT d.*, c.title AS course_title FROM stash_documents d"
            " LEFT JOIN courses c ON c.id = d.course_id"
            " WHERE d.id = ? AND d.user_id = ?",
            (doc_id, user_id),
        )
    return db._query_one("SELECT * FROM stash_documents WHERE id = ?", (doc_id,))


def list_documents(user_id, search=None, course_id=None):
    sql = (
        "SELECT d.*, c.title AS course_title FROM stash_documents d"
        " LEFT JOIN courses c ON c.id = d.course_id WHERE d.user_id = ?"
    )
    params = [user_id]
    if course_id:
        sql += " AND d.course_id = ?"
        params.append(course_id)
    if search:
        sql += " AND (d.title LIKE ? OR d.original_filename LIKE ?)"
        params.extend(("%" + search + "%", "%" + search + "%"))
    sql += " ORDER BY d.created_at DESC"
    return db._query_all(sql, params)


def find_duplicate(user_id, file_sha256):
    """Return an existing usable document with the same content hash, if any."""
    return db._query_one(
        "SELECT id, title, status FROM stash_documents"
        " WHERE user_id = ? AND file_sha256 = ? AND status <> 'error'"
        " ORDER BY created_at LIMIT 1",
        (user_id, file_sha256),
    )


def update_document(doc_id, **changes):
    if not changes:
        return
    cols = []
    params = []
    for key, value in changes.items():
        if key not in DOCUMENT_UPDATEABLE:
            continue
        cols.append(f"{key} = ?")
        params.append(value)
    if not cols:
        return
    cols.append("updated_at = ?")
    params.append(now_iso())
    params.append(doc_id)
    conn = _conn()
    try:
        conn.execute(f"UPDATE stash_documents SET {', '.join(cols)} WHERE id = ?", params)
        _commit(conn)
    finally:
        conn.close()


def delete_document(doc_id):
    """Delete a stash document and every row that hangs off it."""
    conn = _conn()
    try:
        # User-scoped rows first (they key on card/document ids, not user_id).
        conn.execute(
            "DELETE FROM stash_card_states WHERE card_id IN"
            " (SELECT id FROM stash_cards WHERE document_id = ?)",
            (doc_id,),
        )
        conn.execute(
            "DELETE FROM stash_highlights WHERE card_id IN"
            " (SELECT id FROM stash_cards WHERE document_id = ?)",
            (doc_id,),
        )
        conn.execute(
            "DELETE FROM stash_notes WHERE card_id IN"
            " (SELECT id FROM stash_cards WHERE document_id = ?)",
            (doc_id,),
        )
        conn.execute("DELETE FROM stash_progress WHERE document_id = ?", (doc_id,))
        conn.execute("DELETE FROM stash_jobs WHERE document_id = ?", (doc_id,))
        conn.execute("DELETE FROM stash_cards WHERE document_id = ?", (doc_id,))
        conn.execute("DELETE FROM stash_chunks WHERE document_id = ?", (doc_id,))
        conn.execute("DELETE FROM stash_sections WHERE document_id = ?", (doc_id,))
        conn.execute("DELETE FROM stash_documents WHERE id = ?", (doc_id,))
        _commit(conn)
    finally:
        conn.close()


# ------------------------------------------------- sections / chunks

def insert_sections(doc_id, sections):
    conn = _conn()
    try:
        for s in sections:
            conn.execute(
                "INSERT INTO stash_sections"
                " (id, document_id, parent_id, title, level, position, page_start, page_end)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (s["id"], doc_id, s.get("parent_id"), s.get("title", ""),
                 s.get("level", 1), s.get("position", 0),
                 s.get("page_start"), s.get("page_end")),
            )
        _commit(conn)
    finally:
        conn.close()


def list_sections(doc_id):
    return db._query_all(
        "SELECT * FROM stash_sections WHERE document_id = ? ORDER BY position", (doc_id,)
    )


def insert_chunks(doc_id, chunks):
    conn = _conn()
    try:
        for c in chunks:
            conn.execute(
                "INSERT INTO stash_chunks"
                " (id, document_id, section_id, position, text, page_start, page_end)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (c["id"], doc_id, c["section_id"], c["position"], c["text"],
                 c.get("page_start"), c.get("page_end")),
            )
        _commit(conn)
    finally:
        conn.close()


def list_chunks(doc_id, status=None):
    if status:
        return db._query_all(
            "SELECT * FROM stash_chunks WHERE document_id = ? AND status = ?"
            " ORDER BY position",
            (doc_id, status),
        )
    return db._query_all(
        "SELECT * FROM stash_chunks WHERE document_id = ? ORDER BY position", (doc_id,)
    )


def update_chunk(chunk_id, *, status=None, error=None, attempts=None):
    parts = []
    params = []
    if status is not None:
        parts.append("status = ?")
        params.append(status)
    if error is not None:
        parts.append("last_error = ?")
        params.append(error)
    if attempts is not None:
        parts.append("attempts = ?")
        params.append(attempts)
    if not parts:
        return
    params.append(chunk_id)
    conn = _conn()
    try:
        conn.execute(f"UPDATE stash_chunks SET {', '.join(parts)} WHERE id = ?", params)
        _commit(conn)
    finally:
        conn.close()


def count_chunks(doc_id):
    row = db._query_one(
        "SELECT COUNT(*) AS n FROM stash_chunks WHERE document_id = ?", (doc_id,)
    )
    return row["n"] if row else 0


def count_done_chunks(doc_id):
    row = db._query_one(
        "SELECT COUNT(*) AS n FROM stash_chunks WHERE document_id = ? AND status = 'done'",
        (doc_id,),
    )
    return row["n"] if row else 0


# ------------------------------------------------------------------ cards

def _card_columns():
    return (
        "id, document_id, section_id, chunk_id, position, card_type, title, body,"
        " example, key_term, key_term_definition, source_page_start, source_page_end,"
        " source_slide, content_hash, is_flagged"
    )


def _card_row(row):
    return dict(row) if row else None


def append_cards(doc_id, cards):
    """Insert cards at the end of a document's feed, in order.

    cards is a list of dicts with fields: section_id, chunk_id, card_type,
    title, body, example, key_term, key_term_definition, source_page_start,
    source_page_end, source_slide, content_hash, is_flagged.
    Positions continue from the current max so UNIQUE(document_id, position)
    holds. Callers must wrap regeneration in renumber_positions() afterwards.
    """
    if not cards:
        return []
    row = db._query_one(
        "SELECT MAX(position) AS m FROM stash_cards WHERE document_id = ?", (doc_id,)
    )
    start = (row["m"] + 1) if row and row["m"] is not None else 1
    conn = _conn()
    inserted = []
    try:
        for offset, card in enumerate(cards):
            card_id = new_id()
            conn.execute(
                "INSERT INTO stash_cards"
                " (id, document_id, section_id, chunk_id, position, card_type, title,"
                "  body, example, key_term, key_term_definition, source_page_start,"
                "  source_page_end, source_slide, content_hash, is_flagged)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (card_id, doc_id, card["section_id"], card.get("chunk_id"),
                 start + offset, card["card_type"], card["title"], card["body"],
                 card.get("example"), card.get("key_term"),
                 card.get("key_term_definition"),
                 card.get("source_page_start"), card.get("source_page_end"),
                 card.get("source_slide"), card["content_hash"],
                 1 if card.get("is_flagged") else 0),
            )
            inserted.append(card_id)
            conn.execute(
                "UPDATE stash_sections SET card_count = card_count + 1 WHERE id = ?",
                (card["section_id"],),
            )
        _commit(conn)
    finally:
        conn.close()
    return inserted


def renumber_positions(doc_id):
    """Rebuild contiguous 1..n positions for a document's cards.

    Positions currently hold UNIQUE(document_id, position), so a naive inline
    reorder could transiently collide. Shifting every position to a unique
    negative value first (positions are >= 1, so -(pos + 1000000) is disjoint
    from the positive space) makes the reassignment collision-free.
    """
    conn = _conn()
    try:
        conn.execute(
            "UPDATE stash_cards SET position = -(position + 1000000)"
            " WHERE document_id = ?",
            (doc_id,),
        )
        rows = conn.execute(
            "SELECT id FROM stash_cards WHERE document_id = ?"
            " ORDER BY position, id",
            (doc_id,),
        ).fetchall()
        for i, row in enumerate(rows, start=1):
            conn.execute(
                "UPDATE stash_cards SET position = ? WHERE id = ?", (i, row["id"])
            )
        _commit(conn)
    finally:
        conn.close()


def list_cards(doc_id, limit=20, cursor=None):
    """Return a page of cards. cursor is an exclusive position (last seen)."""
    if cursor is not None:
        rows = db._query_all(
            "SELECT * FROM stash_cards WHERE document_id = ? AND position > ?"
            " ORDER BY position LIMIT ?",
            (doc_id, cursor, limit),
        )
    else:
        rows = db._query_all(
            "SELECT * FROM stash_cards WHERE document_id = ? ORDER BY position LIMIT ?",
            (doc_id, limit),
        )
    return rows


def count_cards(doc_id):
    row = db._query_one(
        "SELECT COUNT(*) AS n FROM stash_cards WHERE document_id = ?", (doc_id,)
    )
    return row["n"] if row else 0


def cards_for_quiz(doc_id, limit=60):
    """Non-recap cards oldest-first, used as the deterministic quiz pool."""
    return db._query_all(
        "SELECT * FROM stash_cards WHERE document_id = ? AND card_type <> 'recap'"
        " ORDER BY position LIMIT ?",
        (doc_id, limit),
    )


def list_cards_by_section(doc_id, section_id):
    return db._query_all(
        "SELECT * FROM stash_cards WHERE document_id = ? AND section_id = ?"
        " ORDER BY position",
        (doc_id, section_id),
    )


def delete_section_cards(doc_id, section_id):
    conn = _conn()
    try:
        conn.execute(
            "DELETE FROM stash_card_states WHERE card_id IN"
            " (SELECT id FROM stash_cards WHERE document_id = ? AND section_id = ?)",
            (doc_id, section_id),
        )
        conn.execute(
            "DELETE FROM stash_highlights WHERE card_id IN"
            " (SELECT id FROM stash_cards WHERE document_id = ? AND section_id = ?)",
            (doc_id, section_id),
        )
        conn.execute(
            "DELETE FROM stash_notes WHERE card_id IN"
            " (SELECT id FROM stash_cards WHERE document_id = ? AND section_id = ?)",
            (doc_id, section_id),
        )
        conn.execute(
            "DELETE FROM stash_cards WHERE document_id = ? AND section_id = ?",
            (doc_id, section_id),
        )
        conn.execute(
            "UPDATE stash_sections SET card_count = 0 WHERE id = ?", (section_id,)
        )
        _commit(conn)
    finally:
        conn.close()


def section_has_recap(section_id):
    row = db._query_one(
        "SELECT COUNT(*) AS n FROM stash_cards"
        " WHERE section_id = ? AND card_type = 'recap'",
        (section_id,),
    )
    return bool(row and row["n"])


def reset_document(doc_id):
    """Clear a document for full regeneration: cards gone, chunks pending."""
    conn = _conn()
    try:
        # Kill any work already queued for this document first.
        conn.execute(
            "DELETE FROM stash_jobs WHERE document_id = ?"
            " AND status IN ('queued', 'running')",
            (doc_id,),
        )
        for table in ("stash_card_states", "stash_highlights", "stash_notes"):
            conn.execute(
                f"DELETE FROM {table} WHERE card_id IN"
                " (SELECT id FROM stash_cards WHERE document_id = ?)",
                (doc_id,),
            )
        conn.execute("DELETE FROM stash_progress WHERE document_id = ?", (doc_id,))
        conn.execute("DELETE FROM stash_cards WHERE document_id = ?", (doc_id,))
        conn.execute(
            "UPDATE stash_chunks SET status = 'pending', last_error = NULL,"
            " attempts = 0 WHERE document_id = ?",
            (doc_id,),
        )
        conn.execute(
            "UPDATE stash_sections SET card_count = 0 WHERE document_id = ?",
            (doc_id,),
        )
        _commit(conn)
    finally:
        conn.close()


def clear_structure(doc_id):
    """Drop leftover chunk/section rows so a fresh parse can repopulate them."""
    conn = _conn()
    try:
        conn.execute("DELETE FROM stash_chunks WHERE document_id = ?", (doc_id,))
        conn.execute("DELETE FROM stash_sections WHERE document_id = ?", (doc_id,))
        _commit(conn)
    finally:
        conn.close()


def get_card(card_id, doc_id=None):
    if doc_id:
        return _card_row(db._query_one(
            "SELECT * FROM stash_cards WHERE id = ? AND document_id = ?",
            (card_id, doc_id),
        ))
    return _card_row(db._query_one("SELECT * FROM stash_cards WHERE id = ?", (card_id,)))


def search_cards(doc_id, term):
    like = f"%{term}%"
    return db._query_all(
        "SELECT * FROM stash_cards WHERE document_id = ?"
        " AND (title LIKE ? OR body LIKE ? OR key_term LIKE ?)"
        " ORDER BY position",
        (doc_id, like, like, like),
    )


def saved_cards(user_id, course_id=None, limit=100):
    sql = (
        "SELECT c.*, d.title AS document_title, d.course_id, st.status AS my_status,"
        " st.updated_at AS saved_at FROM stash_cards c"
        " JOIN stash_documents d ON d.id = c.document_id"
        " JOIN stash_card_states st ON st.card_id = c.id AND st.user_id = ?"
        " WHERE st.is_saved = 1"
    )
    params = [user_id]
    if course_id:
        sql += " AND d.course_id = ?"
        params.append(course_id)
    sql += " ORDER BY st.updated_at DESC LIMIT ?"
    params.append(limit)
    return db._query_all(sql, params)


# -------------------------------------------------------- card states

def get_card_state(user_id, card_id):
    return db._query_one(
        "SELECT * FROM stash_card_states WHERE user_id = ? AND card_id = ?",
        (user_id, card_id),
    )


def _upsert_state(user_id, card_id, changes):
    """Insert a state row or update it (fields in changes map to columns)."""
    cols = list(changes)
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO stash_card_states (id, user_id, card_id, "
            + ", ".join(cols) + ") VALUES (?, ?, ?, "
            + ", ".join("?" for _ in cols) + ") "
            "ON CONFLICT (user_id, card_id) DO UPDATE SET "
            + ", ".join(f"{col} = excluded.{col}" for col in cols),
            [new_id(), user_id, card_id] + [changes[c] for c in cols],
        )
        _commit(conn)
    finally:
        conn.close()


def set_card_saved(user_id, card_id, is_saved):
    _upsert_state(user_id, card_id, {"is_saved": 1 if is_saved else 0,
                                     "updated_at": now_iso()})


def set_card_status(user_id, card_id, status):
    _upsert_state(user_id, card_id, {"status": status, "updated_at": now_iso()})


def record_card_seen(user_id, card_id):
    existing = get_card_state(user_id, card_id)
    now = now_iso()
    if existing:
        conn = _conn()
        try:
            conn.execute(
                "UPDATE stash_card_states SET last_seen_at = ?, updated_at = ?"
                " WHERE user_id = ? AND card_id = ?",
                (now, now, user_id, card_id),
            )
            _commit(conn)
        finally:
            conn.close()
    else:
        _upsert_state(user_id, card_id, {"first_seen_at": now, "last_seen_at": now})


def states_for_document(user_id, doc_id):
    rows = db._query_all(
        "SELECT c.id AS card_id, st.* FROM stash_cards c"
        " LEFT JOIN stash_card_states st ON st.card_id = c.id AND st.user_id = ?"
        " WHERE c.document_id = ?",
        (user_id, doc_id),
    )
    return {r["card_id"]: dict(r) for r in rows}


# -------------------------------------------------------------- highlights

def add_highlight(user_id, card_id, field, start_offset, end_offset, color="yellow", note=None):
    hid = new_id()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO stash_highlights"
            " (id, user_id, card_id, field, start_offset, end_offset, color, note)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (hid, user_id, card_id, field, start_offset, end_offset, color, note),
        )
        _commit(conn)
    finally:
        conn.close()
    return hid


def delete_highlight(user_id, highlight_id):
    conn = _conn()
    try:
        conn.execute(
            "DELETE FROM stash_highlights WHERE id = ? AND user_id = ?",
            (highlight_id, user_id),
        )
        _commit(conn)
    finally:
        conn.close()


def list_highlights(user_id, card_id):
    return db._query_all(
        "SELECT * FROM stash_highlights WHERE user_id = ? AND card_id = ?",
        (user_id, card_id),
    )


# ---------------------------------------------------------------- notes

def get_note(user_id, card_id):
    return db._query_one(
        "SELECT * FROM stash_notes WHERE user_id = ? AND card_id = ?",
        (user_id, card_id),
    )


def upsert_note(user_id, card_id, content):
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO stash_notes (id, user_id, card_id, content, updated_at)"
            " VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (user_id, card_id) DO UPDATE SET content = excluded.content,"
            " updated_at = excluded.updated_at",
            (new_id(), user_id, card_id, content, now_iso()),
        )
        _commit(conn)
    finally:
        conn.close()


def delete_note(user_id, card_id):
    conn = _conn()
    try:
        conn.execute(
            "DELETE FROM stash_notes WHERE user_id = ? AND card_id = ?",
            (user_id, card_id),
        )
        _commit(conn)
    finally:
        conn.close()


# --------------------------------------------------------------- progress

def get_progress(user_id, doc_id):
    return db._query_one(
        "SELECT * FROM stash_progress WHERE user_id = ? AND document_id = ?",
        (user_id, doc_id),
    )


def upsert_progress(user_id, doc_id, last_position=None, cards_seen=None, cards_got_it=None):
    """Record (or update) a user's reading progress inside a stash document.

    None-valued counters are taken from the existing row (if any) so write
    sites only need to send the slices they actually observed.
    """
    existing = get_progress(user_id, doc_id)
    if existing:
        last_position = int(last_position) if last_position is not None else existing["last_card_position"]
        cards_seen = (existing["cards_seen"] + 1) if cards_seen else existing["cards_seen"]
        cards_got_it = (existing["cards_got_it"] + 1) if cards_got_it else existing["cards_got_it"]
    else:
        last_position = int(last_position or 0)
        cards_seen = 1 if cards_seen else 0
        cards_got_it = 1 if cards_got_it else 0

    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO stash_progress (id, user_id, document_id, last_card_position,"
            " cards_seen, cards_got_it, last_opened_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (user_id, document_id) DO UPDATE SET"
            " last_card_position = excluded.last_card_position,"
            " cards_seen = excluded.cards_seen,"
            " cards_got_it = excluded.cards_got_it,"
            " last_opened_at = excluded.last_opened_at",
            (new_id(), user_id, doc_id, last_position, cards_seen, cards_got_it, now_iso()),
        )
        _commit(conn)
    finally:
        conn.close()


# ----------------------------------------------------------------- jobs

def enqueue_job(document_id, job_type="process_document", payload=None, run_after=None):
    job_id = new_id()
    now = now_iso()
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO stash_jobs (id, document_id, job_type, payload, status,"
            " run_after, max_attempts, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
            (job_id, document_id, job_type,
             payload if payload is None else db_json(payload),
             run_after or now, StashConfig.job_max_attempts, now, now),
        )
        _commit(conn)
    finally:
        conn.close()
    return job_id


def get_job(job_id):
    if job_id is None:
        return None
    return db._query_one("SELECT * FROM stash_jobs WHERE id = ?", (job_id,))


def update_job(job_id, *, status=None, error=None, attempts=None):
    parts = []
    params = []
    if status is not None:
        parts.append("status = ?")
        params.append(status)
    if error is not None:
        parts.append("last_error = ?")
        params.append(error)
    if attempts is not None:
        parts.append("attempts = ?")
        params.append(attempts)
    parts.append("updated_at = ?")
    params.append(now_iso())
    params.append(job_id)
    conn = _conn()
    try:
        conn.execute(f"UPDATE stash_jobs SET {', '.join(parts)} WHERE id = ?", params)
        _commit(conn)
    finally:
        conn.close()


def heartbeat_job(job_id):
    conn = _conn()
    try:
        conn.execute(
            "UPDATE stash_jobs SET heartbeat_at = ?, updated_at = ? WHERE id = ?",
            (now_iso(), now_iso(), job_id),
        )
        _commit(conn)
    finally:
        conn.close()


def delay_job(job_id, seconds):
    """Push a queued job's run_after forward (retry backoff)."""
    from datetime import datetime, timedelta, timezone

    run_at = (
        datetime.now(timezone.utc) + timedelta(seconds=seconds)
    ).strftime("%Y-%m-%d %H:%M:%S")
    conn = _conn()
    try:
        conn.execute(
            "UPDATE stash_jobs SET run_after = ?, updated_at = ? WHERE id = ?",
            (run_at, now_iso(), job_id),
        )
        _commit(conn)
    finally:
        conn.close()


def claim_job():
    """Atomically claim the oldest queued job and return it (or None)."""
    for _attempt in range(3):
        candidate = db._query_one(
            "SELECT id FROM stash_jobs WHERE status = 'queued' AND run_after <= ?"
            " ORDER BY created_at, id LIMIT 1",
            (now_iso(),),
        )
        if not candidate:
            return None
        conn = _conn()
        claimed = False
        try:
            cur = conn.execute(
                "UPDATE stash_jobs SET status = 'running', locked_by = ?,"
                " locked_at = ?, heartbeat_at = ?, attempts = attempts + 1,"
                " updated_at = ? WHERE id = ? AND status = 'queued'",
                ("worker", now_iso(), now_iso(), now_iso(), candidate["id"]),
            )
            _commit(conn)
            claimed = bool(cur.rowcount)
        finally:
            conn.close()
        if claimed:
            return get_job(candidate["id"])
    return None


def requeue_stale_jobs():
    """Return abandoned running jobs to the queue (post-crash recovery)."""
    stale_before = None
    from datetime import datetime, timedelta, timezone

    stale_before = (
        datetime.now(timezone.utc) - timedelta(seconds=StashConfig.reap_after_seconds)
    ).strftime("%Y-%m-%d %H:%M:%S")
    conn = _conn()
    try:
        conn.execute(
            "UPDATE stash_jobs SET status = 'queued', locked_by = NULL,"
            " locked_at = NULL, updated_at = ? WHERE status = 'running'"
            " AND heartbeat_at < ?",
            (now_iso(), stale_before),
        )
        _commit(conn)
    finally:
        conn.close()


# ----------------------------------------------------------------- usage

def day_key():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).date().isoformat()


def today_usage(user_id):
    row = db._query_one(
        "SELECT input_tokens, output_tokens FROM stash_usage WHERE user_id = ? AND day = ?",
        (user_id, day_key()),
    )
    if not row:
        return {"inputTokens": 0, "outputTokens": 0}
    return {"inputTokens": row["input_tokens"], "outputTokens": row["output_tokens"]}


def add_usage(user_id, input_tokens, output_tokens, server_paid=True):
    """Record token usage for a user's day bucket.

    server_paid marks usage that the server's AI key pays for (as opposed to a
    personal BYOK key). Only server-paid usage counts against the per-user daily
    cap and the server cost footprint; personal keys never burn the cap.
    """
    server_in = input_tokens if server_paid else 0
    server_out = output_tokens if server_paid else 0
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO stash_usage (id, user_id, day, input_tokens, output_tokens,"
            " server_input_tokens, server_output_tokens)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (user_id, day) DO UPDATE SET"
            " input_tokens = input_tokens + excluded.input_tokens,"
            " output_tokens = output_tokens + excluded.output_tokens,"
            " server_input_tokens = server_input_tokens + excluded.server_input_tokens,"
            " server_output_tokens = server_output_tokens + excluded.server_output_tokens",
            (new_id(), user_id, day_key(), input_tokens, output_tokens, server_in, server_out),
        )
        _commit(conn)
    finally:
        conn.close()


def today_server_tokens(user_id):
    row = db._query_one(
        "SELECT server_input_tokens, server_output_tokens FROM stash_usage"
        " WHERE user_id = ? AND day = ?",
        (user_id, day_key()),
    )
    if not row:
        return {"inputTokens": 0, "outputTokens": 0}
    return {"inputTokens": row["server_input_tokens"], "outputTokens": row["server_output_tokens"]}


def daily_token_total(user_id):
    usage = today_server_tokens(user_id)
    return usage["inputTokens"] + usage["outputTokens"]


# ------------------------------------------------------------- payload io

def db_json(value):
    """Serialize a payload for a TEXT column (None stays None)."""
    if value is None:
        return None
    import json

    return json.dumps(value)


def load_db_json(raw):
    if raw is None:
        return None
    import json

    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None