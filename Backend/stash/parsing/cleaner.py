"""Text cleaning for Stash extraction output.

Applies ordering-insensitive normalization and removes page furniture
(headers, footers, running page/slide numbers) that would otherwise be treated
as content by the chunker and the generator.
"""

import re

_PAGE_NUM_RE = re.compile(
    r"^\s*(page|slide|p\.|s\.)?\s*[-–—]?\s*\d{1,4}\s*[-–—]?\s*$", re.IGNORECASE
)


def clean_page(text):
    """Normalize one page/slide of extracted text."""
    if not text:
        return ""
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            lines.append("")
            continue
        line = re.sub(r"[ \t]+", " ", line)
        if _PAGE_NUM_RE.fullmatch(line):
            continue
        lines.append(line)
    return _collapse_blank_lines("\n".join(lines))


def _collapse_blank_lines(text):
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def identify_boilerplate(pages):
    """Return the set of repeated page header/footer lines across a document.

    A line that opens or closes a section of text on more than half of the
    pages is very likely running furniture, safe to drop from every page.
    This is conservative: it only strips exact repeated boundary lines.
    """
    from collections import Counter

    threshold = max(2, (len(pages) + 1) // 2)
    opens, closes = Counter(), Counter()
    for page in pages:
        lines = [ln for ln in (page.get("text") or "").splitlines() if ln.strip()]
        if not lines:
            continue
        opens[lines[0].strip()] += 1
        closes[lines[-1].strip()] += 1
    return {
        line
        for line, count in opens.items()
        if count >= threshold
    } | {
        line
        for line, count in closes.items()
        if count >= threshold
    }


def strip_boilerplate(pages, boilerplate):
    """Return a new page list with boilerplate boundary lines removed."""
    if not boilerplate:
        return pages
    cleaned = []
    for page in pages:
        lines = (page.get("text") or "").splitlines()
        out = []
        for line in lines:
            if line.strip() in boilerplate:
                continue
            out.append(line)
        cleaned.append({**page, "text": _collapse_blank_lines("\n".join(out))})
    return cleaned