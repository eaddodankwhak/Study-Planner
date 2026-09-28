"""Stash service layer.

Owns the document lifecycle: creation (validate + store + queue), the
processing pipeline (parse -> structure -> generate -> finalize), regeneration
of selected sections, and deletion.  The pipeline is crash-safe: chunks carry
their own status as they go, so a restarted job only reprocesses what is still
pending — everything else is skipped.
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from . import repository  # noqa: E402
from . import security  # noqa: E402
from .ai import client  # noqa: E402
from .config import StashConfig  # noqa: E402
from .parsing import build_structure, parse_document, pptx_parser  # noqa: E402

#: Longest recap source we're willing to send (controls per-section cost).
RECAP_MAX_CHARS = 9000


class StashServiceError(Exception):
    """Base for service-level failures."""


class PermanentError(StashServiceError):
    """Failure that retrying will not fix; the job should stop."""


class TransientError(StashServiceError):
    """Retryable failure (network blips, provider hiccups)."""


class DuplicateDocumentError(StashServiceError):
    """The user already has this exact file in their library."""

    def __init__(self, message, existing=None):
        super().__init__(message)
        self.existing = existing


def create_document(uid, filename, blob, course_id=None, provider=None, model=None):
    """Validate an upload, persist it, and enqueue processing."""
    ext, kind = security.classify(filename or "", blob)
    title = security.safe_title(filename or "document")
    sha = security.checksum(blob)

    existing = repository.find_duplicate(uid, sha)
    if existing:
        raise DuplicateDocumentError(
            f'"{existing["title"]}" — you already have this file in your library.',
            existing,
        )

    doc = repository.create_document(
        uid,
        title=title,
        original_filename=filename or "document",
        file_type=kind,
        file_size_bytes=len(blob),
        storage_key="",
        file_sha256=sha,
        course_id=course_id,
        preferred_provider=provider or None,
        preferred_model=model or None,
    )
    if StashConfig.keep_source_files:
        key = security.save_source(blob, uid, doc["id"], ext)
        repository.update_document(doc["id"], storage_key=key)

    repository.enqueue_job(doc["id"])
    return repository.get_document(doc["id"])


def delete_document(doc_id):
    """Remove a document, its rows, and its stored source file."""
    doc = repository.get_document(doc_id)
    if doc and doc.get("storage_key"):
        security.delete_source(doc["storage_key"])
    repository.delete_document(doc_id)


def regenerate(doc_id, section_indexes=None, provider=None, model=None):
    """Re-queue processing for selected sections (or the whole document).

    section_indexes lists 1-based section positions as shown in the TOC. When a
    provider/model pick is supplied it is stored as the new preference so the
    re-run resolves through that provider.
    """
    if provider or model:
        repository.update_document(
            doc_id,
            preferred_provider=provider or None,
            preferred_model=model or None,
        )
    if section_indexes:
        sections = repository.list_sections(doc_id)
        targets = [s for s in sections if s["position"] in section_indexes]
        if not targets:
            raise PermanentError("None of the requested chapters are present.")
        for section in targets:
            repository.delete_section_cards(doc_id, section["id"])
            for chunk in repository.list_chunks(doc_id):
                if chunk["section_id"] == section["id"]:
                    repository.update_chunk(
                        chunk["id"], status="pending", error=None
                    )
    else:
        repository.reset_document(doc_id)

    repository.update_document(
        doc_id,
        status="queued",
        error_message=None,
        progress_percent=0,
        total_cards=0,
    )
    repository.enqueue_job(doc_id)
    return repository.get_document(doc_id)


# ------------------------------------------------------------- processing

def process_document(doc_id):
    """Run (or resume) the full generation pipeline for one document.

    Idempotent enough to be run by a retried job: chunks already 'done' are
    skipped, and sections/chunks are only recreated when they're missing.
    """
    doc = repository.get_document(doc_id)
    if not doc:
        raise PermanentError("That document no longer exists.")
    uid = doc["user_id"]

    if doc["status"] in ("queued", "error"):
        repository.update_document(doc_id, status="processing", progress_percent=2)

    # Fail fast on problems retries would never fix.
    if not client.privacy_allows(uid):
        _fail(doc_id, "AI activity is turned off in your privacy settings. Turn it "
                      "on in Settings → Privacy to use Stash.")
        raise PermanentError("privacy block")
    try:
        generation = client.resolve_generation(
            uid, preferred=_preferred_pick(doc)
        )
    except (client.NoProviderError, client.StashAIError) as exc:
        _fail(doc_id, str(exc))
        raise PermanentError(str(exc)) from exc

    chunk_target, chunk_max = _chunk_caps(generation)

    chunks = repository.list_chunks(doc_id)
    if not chunks:
        try:
            repository.clear_structure(doc_id)  # drop any partial leftovers
            chunks = _preprocess(doc, chunk_target, chunk_max)
        except PermanentError as exc:
            _fail(doc_id, str(exc))
            raise
        except pptx_parser.ParseError as exc:
            _fail(doc_id, str(exc))
            raise PermanentError(str(exc)) from exc

    sections_by_id = {s["id"]: s for s in repository.list_sections(doc_id)}
    total_chunks = float(len(chunks) or 1)
    done_ok = float(repository.count_done_chunks(doc_id))
    limit_hit = False
    token_in = int(doc.get("input_tokens") or 0)
    token_out = int(doc.get("output_tokens") or 0)

    for chunk in chunks:
        if chunk["status"] == "done":
            continue
        section = sections_by_id.get(chunk["section_id"]) or {}
        repository.update_chunk(
            chunk["id"], status="processing",
            attempts=int(chunk["attempts"] or 0) + 1,
        )
        try:
            cards, (input_tokens, output_tokens) = client.generate_chunk_cards(
                uid, generation, doc, section, chunk
            )
        except client.DailyLimitError:
            limit_hit = True
            repository.update_chunk(chunk["id"], status="pending")
            break
        except (client.PrivacyBlockedError, client.NoProviderError) as exc:
            _fail(doc_id, str(exc))
            raise PermanentError(str(exc)) from exc
        except client.StashAIError as exc:
            repository.update_chunk(
                chunk["id"], status="error", error=str(exc)[:500]
            )
            continue

        rows = [_db_card(c, section, chunk) for c in cards]
        repository.append_cards(doc_id, rows)
        repository.update_chunk(chunk["id"], status="done")
        repository.add_usage(
            uid, input_tokens, output_tokens,
            server_paid=generation["paid_by"] == "server",
        )
        token_in += input_tokens
        token_out += output_tokens
        done_ok += 1
        _progress(doc_id, min(90, int(done_ok / total_chunks * 90)))

    if not limit_hit:
        try:
            _generate_recaps(uid, doc_id, doc, chunks, generation)
        except client.DailyLimitError:
            limit_hit = True

    return _finalize(doc_id, limit_hit, generation, token_in, token_out)


def _preprocess(doc, chunk_target, chunk_max):
    """Parse the source file and persist sections + chunks for the first time."""
    data = security.load_source(doc.get("storage_key")) if doc.get("storage_key") else None
    if data is None:
        raise PermanentError(
            "The source file is no longer available on the server, so extraction "
            "can't start. Upload the document again."
        )

    parsed = parse_document(data, doc["file_type"])
    limit = (
        StashConfig.max_pdf_pages if doc["file_type"] == "pdf"
        else StashConfig.max_pptx_slides
    )
    if parsed["page_count"] > limit:
        raise PermanentError(
            f"Documents over {limit} pages/slides are not supported yet."
        )

    pages, structure = build_structure(
        parsed, chunk_target=chunk_target, chunk_max=chunk_max
    )
    if not structure["chunks"] or not structure["sections"]:
        raise PermanentError(
            "No readable text could be extracted from this document."
        )

    doc_id = doc["id"]
    repository.update_document(doc_id, page_count=parsed["page_count"])

    section_ids = [repository.new_id() for _ in structure["sections"]]
    section_rows = [
        {
            "id": section_ids[i],
            "parent_id": None,
            "title": sec["title"],
            "level": sec["level"],
            "position": i + 1,
            "page_start": sec["page_start"],
            "page_end": sec["page_end"],
        }
        for i, sec in enumerate(structure["sections"])
    ]
    repository.insert_sections(doc_id, section_rows)

    chunk_rows = [
        {
            "id": repository.new_id(),
            "section_id": section_ids[c["section_index"]],
            "position": i + 1,
            "text": c["text"],
            "page_start": c["page_start"],
            "page_end": c["page_end"],
        }
        for i, c in enumerate(structure["chunks"])
    ]
    repository.insert_chunks(doc_id, chunk_rows)
    return repository.list_chunks(doc_id)


def _generate_recaps(uid, doc_id, doc, chunks, generation):
    """Append one recap card for every section that produced cards."""
    fresh = {s["id"]: s for s in repository.list_sections(doc_id)}
    for section in fresh.values():
        if not section["card_count"]:
            continue
        if repository.section_has_recap(section["id"]):
            continue
        sec_text = "\n\n".join(
            c["text"] for c in chunks if c["section_id"] == section["id"]
        )[:RECAP_MAX_CHARS]
        if not sec_text.strip():
            continue
        recap, (input_tokens, output_tokens) = client.generate_recap_card(
            uid, generation, doc, section, sec_text
        )
        if recap:
            repository.append_cards(doc_id, [_db_card(recap, section, None)])
            repository.add_usage(
                uid, input_tokens, output_tokens,
                server_paid=generation["paid_by"] == "server",
            )


def _chunk_caps(generation):
    """Derive readability-safe chunk caps from the resolved model's budget.

    Chunks are sized so a section's text fits inside the model's context window
    minus its max output (with a small prompt-overhead buffer), estimated at ~4
    characters per token. The configured STASH_CHUNK_* caps still bind first so
    unchanged deployments see identical behaviour.
    """
    model = generation["model"]
    context = int(model.get("context_window") or 8000)
    max_out = int(model.get("max_output") or 8192)
    budget_chars = max(1, (context - max_out - 600) * 4)
    return (
        min(StashConfig.chunk_target_chars, budget_chars),
        min(StashConfig.chunk_max_chars, budget_chars),
    )


def _preferred_pick(doc):
    """Registry-id provider/model pick stored on the document, if any."""
    provider = (doc or {}).get("preferred_provider")
    model = (doc or {}).get("preferred_model")
    if provider and model:
        return {"provider": provider, "model": model}
    return None


def _finalize(doc_id, limit_hit, generation, token_in, token_out):
    total_cards = repository.count_cards(doc_id)
    repository.renumber_positions(doc_id)
    provider_used = generation["provider_id"]
    model_used = generation["model_id"]

    if limit_hit:
        message = (
            "Daily AI limit reached — processing stopped to save your budget."
            " Cards already generated are ready to read."
        )
        if total_cards:
            repository.update_document(
                doc_id, status="ready", total_cards=total_cards,
                progress_percent=100, error_message=message,
                input_tokens=token_in, output_tokens=token_out,
                model_used=model_used, provider_used=provider_used,
                prompt_version=StashConfig.prompt_version,
            )
        else:
            repository.update_document(
                doc_id, status="error", total_cards=0,
                error_message=message, input_tokens=token_in,
                output_tokens=token_out,
            )
        raise PermanentError("daily limit reached")

    if total_cards:
        repository.update_document(
            doc_id, status="ready", total_cards=total_cards,
            progress_percent=100, error_message=None,
            input_tokens=token_in, output_tokens=token_out,
            model_used=model_used, provider_used=provider_used,
            prompt_version=StashConfig.prompt_version,
        )
    else:
        pending = repository.count_chunks(doc_id) - repository.count_done_chunks(doc_id)
        message = (
            "No cards could be generated."
            if not pending
            else "Generation is incomplete — some sections could not be read."
        )
        repository.update_document(
            doc_id, status="error", total_cards=0, error_message=message,
            input_tokens=token_in, output_tokens=token_out,
        )
        raise PermanentError(message)


def _progress(doc_id, percent):
    repository.update_document(doc_id, progress_percent=percent)


def _fail(doc_id, message):
    repository.update_document(doc_id, status="error", error_message=message)


def _db_card(card, section, chunk):
    return {
        "section_id": (section or {}).get("id"),
        "chunk_id": (chunk or {}).get("id"),
        "card_type": card["card_type"],
        "title": card["title"],
        "body": card["body"],
        "example": card.get("example"),
        "key_term": card.get("key_term"),
        "key_term_definition": card.get("key_term_definition"),
        "source_page_start": card.get("source_page_start"),
        "source_page_end": card.get("source_page_end"),
        "source_slide": card.get("source_slide"),
        "content_hash": card.get("content_hash"),
        "is_flagged": card.get("is_flagged") or 0,
    }