"""PDF text + outline extraction for Stash.

Uses PyPDF2 (already a runtime dependency of the AI Hub). Pages whose text
cannot be extracted are kept with empty text so page numbering stays stable;
empty pages are skipped later during chunking.
"""

import io

import PyPDF2

from .cleaner import clean_page


def parse_pdf(data):
    """Return the normalized Stash parse for a PDF byte string.

    {"kind": "pdf", "page_count": N, "pages": [...], "outline": [...]}
    """
    reader = PyPDF2.PdfReader(io.BytesIO(data))
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:  # noqa: BLE001 - one bad page must not kill the deck
            text = ""
        pages.append({"num": i, "text": clean_page(text)})
    return {
        "kind": "pdf",
        "page_count": len(pages),
        "pages": pages,
        "outline": _flatten_outline(reader, reader.outline),
    }


def _outline_page_for(reader, item):
    """Best-effort 1-based page number for a PDF bookmark."""
    try:
        resolver = getattr(reader, "get_destination_page_number", None)
        if resolver:
            return int(resolver(item)) + 1
    except Exception:  # noqa: BLE001
        pass
    try:
        page = getattr(item, "page", None)
        if page is not None and hasattr(reader, "pages"):
            for idx, p in enumerate(reader.pages, start=1):
                if p is page or getattr(p, "indirect_reference", None) == getattr(
                    page, "indirect_reference", None
                ):
                    return idx
    except Exception:  # noqa: BLE001
        pass
    return None


def _flatten_outline(reader, outline, level=1, dest=None):
    dest = dest if dest is not None else []
    if not outline:
        return dest
    for item in outline:
        if isinstance(item, list):
            _flatten_outline(reader, item, level + 1, dest)
            continue
        try:
            page = _outline_page_for(reader, item)
        except Exception:  # noqa: BLE001
            page = None
        if page is None:
            continue
        dest.append({"title": (item.title or "").strip(), "level": level, "page": page})
    return dest