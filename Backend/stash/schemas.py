"""Canonical Stash card schema and shape contracts.

Defines the card types the generator may emit, the exact field shape expected
from the AI (and therefore enforced by stash/ai/validator.py), and simple
reader-friendly labels. No external validation dependency: rules are plain
functions so tests can exercise them without a model call.
"""

#: Card types the AI generator may produce, in display order.
CARD_TYPES = [
    "concept",
    "definition",
    "process_step",
    "formula",
    "example",
    "key_takeaway",
    "warning",
    "recap",
]

CARD_TYPE_LABELS = {
    "concept": "Concept",
    "definition": "Definition",
    "process_step": "Process step",
    "formula": "Formula",
    "example": "Example",
    "key_takeaway": "Key takeaway",
    "warning": "Warning",
    "recap": "Chapter recap",
}

#: Card types that are only generated once per section (never per chunk).
SECTION_ONLY_TYPES = {"recap"}

#: Hard content limits from the build prompt.
MAX_TITLE_WORDS = 12
MIN_BODY_WORDS = 25
MAX_BODY_WORDS = 80
#: Below this confidence a card is still stored but flagged for review.
FLAG_CONFIDENCE = 0.6


def count_words(text):
    """Return the number of whitespace-separated words in a string."""
    return len(text.split())


def validate_card_shape(card):
    """Return a list of human-readable problems with a raw generator card.

    The raw card is what the LLM emitted (schema in stash/ai/prompts.py).
    Returns [] when the card can be persisted as-is.
    """
    if not isinstance(card, dict):
        return ["card is not an object"]

    problems = []

    for key in ("type", "title", "content"):
        if not isinstance(card.get(key), str) or not card[key].strip():
            problems.append(f"missing string field '{key}'")

    if isinstance(card.get("type"), str) and card["type"] not in CARD_TYPES:
        problems.append(f"unknown card type '{card['type']}'")

    title = card.get("title") or ""
    if isinstance(title, str) and title.strip():
        if count_words(title) > MAX_TITLE_WORDS:
            problems.append(
                f"title has {count_words(title)} words (max {MAX_TITLE_WORDS})"
            )

    body = card.get("content") or ""
    if isinstance(body, str) and body.strip():
        words = count_words(body)
        if words < 12:
            problems.append("content is too short to be an idea card")
        elif words > MAX_BODY_WORDS:
            problems.append(f"content has {words} words (max {MAX_BODY_WORDS})")

    confidence = card.get("confidence")
    if confidence is not None:
        if not isinstance(confidence, (int, float)):
            problems.append("confidence is not a number")

    return problems


def clamp_body_to_limit(body):
    """Trim a card body to MAX_BODY_WORDS words (for the worker, not the AI).

    Keeps the reader/highlight logic simple when a generator occasionally
    exceeds the hard cap: rather than discarding an otherwise valid card, the
    body is truncated at the last word boundary inside the cap.
    """
    words = body.split()
    if len(words) <= MAX_BODY_WORDS:
        return body
    return " ".join(words[:MAX_BODY_WORDS])


def to_db_card(raw):
    """Map a validated raw generator card onto stash_cards column values."""
    return {
        "card_type": raw["type"],
        "title": raw["title"].strip(),
        "body": clamp_body_to_limit(raw["content"].strip()),
        "example": (raw.get("example") or "").strip() or None,
        "key_term": (raw.get("key_term") or "").strip() or None,
        "key_term_definition": (raw.get("key_term_definition") or "").strip() or None,
        "confidence": raw.get("confidence"),
    }