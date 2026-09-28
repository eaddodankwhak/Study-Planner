"""Structure + chunk builder for Stash.

Converts the flat page list into a section tree (approximated from the PDF
outline when present, else from heading heuristics) and then into
context-sized chunks of extracted text. Chunks are what the generator sees;
each carries an inclusive 1-based page range for source refs and "Show source".
"""

import re

from .cleaner import _PAGE_NUM_RE

#: A section whose total text is below this is too thin to mine for cards.
MIN_SECTION_TEXT = 250
#: Fallback section size for documents with no usable headings/outline.
GROUP_PAGES_PER_SECTION = 5
CHUNK_TARGET = 1700
CHUNK_MAX = 2100


def build_structure(pages, outline=None, kind="pdf"):
    """Return {"sections": [...], "chunks": [...]}.

    Sections: [{"title", "level", "page_start", "page_end"}]
    Chunks:   [{"section_index", "text", "page_start", "page_end"}]
    section_index refers to the position of the section inside the sections
    list so the caller can resolve ids after persisting the sections.
    """
    sections = _sections(pages, outline or [], kind)
    chunks = _chunk_sections(pages, sections, CHUNK_TARGET, CHUNK_MAX)

    # Drop sections with almost no extractable text (scanned/empty pages).
    kept = [
        idx for idx, sec in enumerate(sections)
        if _text_len(pages, sec) >= MIN_SECTION_TEXT
    ]

    sections_out = [sections[i] for i in kept]
    index_map = {old: new for new, old in enumerate(kept)}
    chunks_out = []
    for chunk in chunks:
        if chunk["section_index"] in index_map:
            chunk["section_index"] = index_map[chunk["section_index"]]
            chunks_out.append(chunk)
    return {"sections": sections_out, "chunks": chunks_out}


def _text_len(pages, sec):
    total = 0
    for p in range(sec["page_start"], sec["page_end"] + 1):
        if 1 <= p <= len(pages):
            total += len(pages[p - 1]["text"])
    return total


# ---------------------------------------------------------------- sections

def _sections(pages, outline, kind):
    if kind == "pptx":
        sections = _sections_from_slide_headings(pages)
    elif outline:
        sections = _sections_from_outline(pages, outline)
    else:
        sections = _sections_by_groups(pages)
    if not sections:
        sections = _sections_by_groups(pages)

    last_page = pages[-1]["num"] if pages else 1
    cleaned = []
    for sec in sections:
        start = max(1, min(int(sec["page_start"]), last_page))
        end = max(start, min(int(sec["page_end"]), last_page))
        title = re.sub(r"\s+", " ", (sec.get("title") or "").strip())[:120]
        if not title:
            title = f"Part {len(cleaned) + 1}"
        if cleaned and cleaned[-1]["title"] == title and cleaned[-1]["page_end"] + 1 >= start:
            cleaned[-1]["page_end"] = max(cleaned[-1]["page_end"], end)
            continue
        cleaned.append({
            "title": title,
            "level": int(sec.get("level", 1)),
            "page_start": start,
            "page_end": end,
        })
    return cleaned


def _sections_from_slide_headings(pages):
    sections = []
    current = None
    for page in pages:
        heading = _slide_heading(page["text"])
        if heading is not None and (current is None or heading != current["title"]):
            if current is not None:
                current["page_end"] = page["num"] - 1
                if current["page_end"] >= current["page_start"]:
                    sections.append(current)
            current = {"title": heading, "level": 1, "page_start": page["num"]}
    if current is not None:
        current["page_end"] = pages[-1]["num"] if pages else current["page_start"]
        sections.append(current)
    return sections


def _slide_heading(text):
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if _PAGE_NUM_RE.fullmatch(line):
            return None
        if 2 <= len(line) <= 80:
            return line
        return None
    return None


def _sections_from_outline(pages, outline):
    anchors = sorted(
        (node for node in outline if node.get("page") and node.get("title")),
        key=lambda n: n["page"],
    )
    last_page = pages[-1]["num"] if pages else 1
    sections = []
    for i, node in enumerate(anchors):
        start = int(node["page"])
        end = int(anchors[i + 1]["page"]) - 1 if i + 1 < len(anchors) else last_page
        sections.append({
            "title": node["title"],
            "level": int(node.get("level", 1)),
            "page_start": start,
            "page_end": max(start, end),
        })
    if sections and sections[0]["page_start"] > 1:
        sections[0]["page_start"] = 1  # front matter belongs to the first section
    return sections


def _sections_by_groups(pages):
    sections = []
    for offset in range(0, len(pages), GROUP_PAGES_PER_SECTION):
        group = pages[offset:offset + GROUP_PAGES_PER_SECTION]
        first_text = group[0]["text"] if group else ""
        title = _slide_heading(first_text) or f"Part {len(sections) + 1}"
        sections.append({
            "title": title,
            "level": 1,
            "page_start": group[0]["num"],
            "page_end": group[-1]["num"],
        })
    return sections


# ----------------------------------------------------------------- chunks

def _chunk_sections(pages, sections, target, chunk_max):
    chunks = []
    for sec_index, sec in enumerate(sections):
        pairs = []
        for p in range(sec["page_start"], sec["page_end"] + 1):
            if 1 <= p <= len(pages) and pages[p - 1]["text"].strip():
                pairs.append((p, pages[p - 1]["text"].strip()))
        if not pairs:
            continue
        for c in _assemble(pairs, target, chunk_max):
            chunks.append({
                "section_index": sec_index,
                "text": c["text"],
                "page_start": c["page_start"],
                "page_end": c["page_end"],
            })
    return chunks


def _assemble(pairs, target, chunk_max):
    """Group (page, text) pairs into chunks of roughly `target` characters.

    Pages are never cut mid-text (a page may exceed the cap on its own; it is
    then split at paragraph boundaries by _split_long).
    """
    chunks = []
    buf = []
    buf_len = 0

    def flush():
        nonlocal buf, buf_len
        if not buf:
            return
        text = "\n\n".join(t for _, t in buf).strip()
        if text:
            chunks.append({
                "text": text,
                "page_start": buf[0][0],
                "page_end": buf[-1][0],
            })
        buf = []
        buf_len = 0

    for pnum, text in pairs:
        if buf and buf_len + len(text) > target:
            flush()
        buf.append((pnum, text))
        buf_len += len(text)
        if buf_len >= chunk_max:
            flush()
    flush()

    split = []
    for c in chunks:
        if len(c["text"]) <= chunk_max:
            split.append(c)
        else:
            split.extend(_split_long(c, chunk_max))
    return split


def _split_long(chunk, chunk_max):
    paras = [p.strip() for p in chunk["text"].split("\n\n") if p.strip()]
    pieces = []
    cur = ""
    for para in paras:
        candidate = (cur + "\n\n" + para).strip() if cur else para
        if cur and len(candidate) > chunk_max:
            pieces.append(cur)
            cur = para
        else:
            cur = candidate
    if cur:
        pieces.append(cur)
    return [
        {"text": p, "page_start": chunk["page_start"], "page_end": chunk["page_end"]}
        for p in pieces
    ]