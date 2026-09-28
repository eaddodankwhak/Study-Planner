"""Deterministic quiz generation from Stash cards.

The build prompt calls for a quiz you can run from a deck "with a simple
built-in fallback if there is no native quiz engine". This is that fallback:
questions are assembled from real card content with no AI spend — each
question quotes a card as the correct statement and three other cards as
distractors. Teaching is truthful because every option is a direct quote from
the document.
"""

import hashlib
import random
import re

from . import repository


def build_quiz(doc_id, limit=8, max_options_char=110):
    """Return {"questions": [...]} sampled deterministically from the deck."""
    pool = repository.cards_for_quiz(doc_id, limit=60)
    if len(pool) < 2:
        return {"questions": []}

    seed = int(hashlib.sha256(doc_id.encode("utf-8")).hexdigest()[:8], 16)
    rng = random.Random(seed)

    sample = rng.sample(pool, min(limit, len(pool)))
    picked_ids = {card["id"] for card in sample}
    questions = []
    for card in sample:
        distractors = [
            c for c in pool
            if c["id"] not in picked_ids or c["id"] != card["id"]
        ]
        rng.shuffle(distractors)
        options = [card["body"]]
        for d in distractors[:3]:
            if d["body"] not in options:
                options.append(d["body"])
        options = options[:4]
        if len(options) < 2:
            continue
        rng.shuffle(options)
        correct = options.index(card["body"])
        stem = _stem(card)
        explanation = f'Source: {card["title"]}'
        page = card.get("source_page_start")
        if page:
            explanation += f" (p. {page})"
        explanation += f'. {_one_line(card["body"], 200)}'
        questions.append({
            "question": stem,
            "options": [_one_line(o, max_options_char) for o in options],
            "answer": correct,
            "card_id": card["id"],
            "explanation": explanation,
        })
    return {"questions": questions}


def _stem(card):
    if card["card_type"] == "definition" and card.get("key_term"):
        return f"According to the source, what does “{card['key_term']}” mean?"
    return f"According to the source material ({card['title']}):"


def _one_line(text, max_len):
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) > max_len:
        text = text[:max_len].rstrip() + "…"
    return text