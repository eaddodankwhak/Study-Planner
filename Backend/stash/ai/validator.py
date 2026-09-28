"""Validation of the generator's JSON output.

Parsing is deliberately tolerant (fenced blocks, stray prose around the JSON)
while shape validation is strict. Every accepted card is normalized onto the
stash_cards column contract plus a per-generation `problems` list for the
retry loop; the DB row builders apply page/section fields outside this module.
"""

import json
import re

from .. import schemas


class GenerationError(ValueError):
    """The generator's response could not be turned into valid cards."""


def extract_json(text):
    """Return the first parseable JSON value embedded in `text`."""
    if not isinstance(text, str) or not text.strip():
        raise GenerationError("Empty response from the generator.")

    text = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()

    for open_idx in _valid_open_positions(text):
        candidate = _balanced_json(text, open_idx)
        if candidate is None:
            continue
        try:
            return json.loads(candidate)
        except (ValueError, TypeError):
            continue
    raise GenerationError("Could not find valid JSON in the generator response.")


def _valid_open_positions(text):
    positions = [i for i, ch in enumerate(text) if ch in "{["]
    if not positions:
        return []
    # Prefer an object at "{" (our schema is {"cards": [...]}).
    objects = [i for i in positions if text[i] == "{"]
    return (objects or positions)[:20]


def _balanced_json(text, open_idx):
    """Return the brace/paren-balanced substring from open_idx, or None."""
    open_ch = text[open_idx]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_str = False
    escape = False
    for i in range(open_idx, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[open_idx:i + 1]
    return None


def parse_cards(text, default_page=None, default_slide=None):
    """Parse generator output into normalized, DB-ready card dicts.

    Raises GenerationError when JSON is missing/malformed. Cards that fail
    shape validation are kept but flagged in their `problems` list so the
    client can retry once; structurally unusable cards are dropped.
    """
    payload = extract_json(text)
    if not isinstance(payload, dict) or "cards" not in payload:
        raise GenerationError("Response JSON must contain a 'cards' array.")
    raw_cards = payload.get("cards")
    if not isinstance(raw_cards, list):
        raise GenerationError("'cards' must be an array.")

    normalized = []
    for raw in raw_cards:
        problems = schemas.validate_card_shape(raw)
        if not isinstance(raw, dict):
            continue
        card = _normalize(raw, default_page, default_slide)
        card["problems"] = problems
        normalized.append(card)
    if not normalized:
        raise GenerationError("No usable cards were present in the response.")
    return normalized


def _normalize(raw, default_page, default_slide):
    confidence = raw.get("confidence")
    if not isinstance(confidence, (int, float)):
        confidence = 0.6
    confidence = min(1.0, max(0.0, float(confidence)))

    source_page = raw.get("source_page")
    source_slide = raw.get("source_slide")
    if not isinstance(source_page, int):
        source_page = default_page if raw.get("type") != "recap" else None
    if not isinstance(source_slide, int):
        source_slide = default_slide

    base = schemas.to_db_card(raw)
    base.update({
        "confidence": confidence,
        "is_flagged": int(confidence < schemas.FLAG_CONFIDENCE),
        "source_page_start": source_page,
        "source_page_end": source_page,
        "source_slide": source_slide,
    })
    return base


def content_hash(card):
    """Stable fingerprint used for duplicate detection within a document."""
    import hashlib

    payload = "|".join([
        card.get("card_type") or "",
        (card.get("title") or "").lower(),
        (card.get("body") or "").lower(),
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()