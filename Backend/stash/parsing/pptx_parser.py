"""PPTX slide text extraction for Stash.

Reads the Open XML package with Python's stdlib (zipfile + ElementTree) so no
new dependency is required: any modern PowerPoint deck is a zip of XML parts.
Bullet text comes from the body paragraphs (a:p -> a:r -> a:t); slide notes
are intentionally ignored. Malformed/oversized decks raise ParseError.
"""

import io
import re
import zipfile
import xml.etree.ElementTree as ET

from .cleaner import clean_page

A = "http://schemas.openxmlformats.org/drawingml/2006/main"
P = "http://schemas.openxmlformats.org/presentationml/2006/main"

_SLIDE_RE = re.compile(r"ppt/slides/slide(\d+)\.xml")

#: Zip-bomb guard: nothing inside a deck may inflate past this size.
MAX_UNCOMPRESSED = 256 * 1024 * 1024


class ParseError(Exception):
    """Raised when a deck cannot be parsed as presentable Open XML."""


def parse_pptx(data):
    """Return the normalized Stash parse for a PPTX byte string."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        total = sum(info.file_size for info in zf.infolist())
        if total > MAX_UNCOMPRESSED:
            raise ParseError("Deck is too large to process safely.")
        slide_names = sorted(
            (n for n in zf.namelist() if _SLIDE_RE.fullmatch(n)),
            key=lambda n: int(_SLIDE_RE.fullmatch(n).group(1)),
        )
        if not slide_names:
            raise ParseError("No slides found in that deck.")

        pages = []
        for name in slide_names:
            num = int(_SLIDE_RE.fullmatch(name).group(1))
            try:
                text = _slide_text(zf.read(name))
            except Exception as exc:  # noqa: BLE001
                raise ParseError(f"Could not read slide {num}: {exc}") from exc
            pages.append({"num": num, "text": clean_page(text)})

    return {"kind": "pptx", "page_count": len(pages), "pages": pages, "outline": []}


def _slide_text(xml_bytes):
    root = ET.fromstring(xml_bytes)
    lines = []
    for para in root.iter(f"{{{A}}}p"):
        parts = [t.text or "" for t in para.iter(f"{{{A}}}t")]
        line = "".join(parts).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def slide_title(xml_bytes):
    """Return the title-placeholder text of a slide, or None."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    for sp in root.iter(f"{{{P}}}sp"):
        ph = sp.find(f".//{{{P}}}ph")
        if ph is None:
            continue
        if ph.get("type") in ("title", "ctrTitle"):
            parts = [t.text or "" for t in sp.iter(f"{{{A}}}t")]
            text = "".join(parts).strip()
            return text or None
    return None