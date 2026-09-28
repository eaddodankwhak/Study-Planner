"""Prompt builders for Stash card generation.

Versioned by StashConfig.prompt_version. The output contract is a strict JSON
object so the validator has a stable target; the model is told the exact word
and fidelity rules from the build prompt. The word limits below intentionally
mirror the constants in stash/schemas.py.
"""

import json

from ..config import StashConfig

SYSTEM_PROMPT = """You convert study material into bite-size idea cards for a reading feed.

Rules — follow them exactly:
1. One idea per card. Extract the clearest, most useful idea from the excerpt.
2. Card types you may emit: concept, definition, process_step, formula, example,
   key_takeaway, warning. For a recap request, emit type "recap" only.
3. Facts must come strictly from the source excerpt. Never invent, extrapolate,
   or add outside knowledge. Numbers, names and terms must match the source.
4. "title": short headline under 12 words.
5. "content": one full idea, 25–80 words, plain prose.
6. "confidence": your confidence that the card is accurate and faithful to the
   source, as a number 0.0–1.0. Use low values (below 0.6) when the excerpt is
   ambiguous, contradictory, or barely covers the idea.
7. Exactly one excerpt is provided. Produce a concise, non-redundant set of
   cards (aim for 2–6 cards; a recap request produces exactly 1).
8. When source_page/source_slide is known, set it; otherwise null.

Respond with ONLY a single JSON object, no markdown fences, no commentary. The
shape is exactly:
{"cards": [{"type": "concept", "title": "...", "content": "...",
    "example": "...", "key_term": "...", "key_term_definition": "...",
    "confidence": 0.9, "source_page": 12, "source_slide": null}]}
Optional fields (example, key_term, key_term_definition, source_page,
source_slide) may be omitted or null when not applicable."""


def build_chunk_prompt(document_title, section_title, chunk_text):
    """User message for generating cards from one chunk of a section."""
    return json.dumps({
        "task": "extract_cards",
        "version": StashConfig.prompt_version,
        "document": document_title,
        "section": section_title,
        "excerpt": chunk_text,
    })


def build_recap_prompt(document_title, section_title, section_text):
    """User message for the single recap card summarizing a whole section."""
    return json.dumps({
        "task": "section_recap",
        "version": StashConfig.prompt_version,
        "document": document_title,
        "section": section_title,
        "excerpt": section_text,
    })


def build_fix_prompt(problems, previous_response):
    """User message asking for a corrected response after validation failed."""
    return json.dumps({
        "task": "fix_cards",
        "version": StashConfig.prompt_version,
        "previous_response": previous_response,
        "validation_problems": problems[:8],
        "instruction": (
            "Your previous response failed validation. Return a corrected JSON "
            "object with the same shape, fixing every listed problem."
        ),
    })