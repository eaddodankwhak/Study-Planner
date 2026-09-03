"""Model registry and capability system for the AI Learning Hub.

This module is the single source of truth for which AI models exist, which
provider backs them, and what they can do. The UI and API read from this
registry rather than hard-coding provider logic anywhere else. Adding a new
model is just a new entry here (plus a provider adapter in providers/).
"""

import os

# Capability identifiers used across the app. Keep these stable.
CAPABILITIES = {
    "TEXT": "text",
    "IMAGE": "image",
    "PDF": "pdf",
    "FILES": "files",
    "LONG_CONTEXT": "long_context",
    "STREAMING": "streaming",
    "CODE": "code",
    "MATH": "math",
}

# Modes the AI Learning Hub supports. Add new modes here and provide a prompt
# for them in prompts.py.
MODES = [
    {"id": "explain", "label": "Explain", "description": "Understand a concept clearly."},
    {"id": "summarize", "label": "Summarize", "description": "Condense notes or pasted text."},
    {"id": "solve", "label": "Solve", "description": "Solve a question step by step."},
    {"id": "quiz", "label": "Quiz Me", "description": "Generate practice questions."},
    {"id": "flashcards", "label": "Flashcards", "description": "Create study flashcards."},
    {"id": "study_plan", "label": "Study Plan", "description": "Build a personalized study plan."},
    {"id": "simplify", "label": "Simplify", "description": "Rewrite material in simpler language."},
    {"id": "exam_prep", "label": "Exam Prep", "description": "Revision material for exams."},
    {"id": "ask", "label": "Ask Anything", "description": "General academic assistant."},
]


def _model(model_id, provider, display_name, description, **kw):
    """Build a normalized model descriptor dict."""
    caps = set(kw.pop("capabilities", []))
    return {
        "id": model_id,
        "provider": provider,
        "displayName": display_name,
        "description": description,
        "availability": kw.pop("availability", "unavailable"),
        "context_window": kw.pop("context_window", 8000),
        "supports_images": "IMAGE" in caps,
        "supports_files": "FILES" in caps,
        "supports_streaming": "STREAMING" in caps,
        "model_id": kw.pop("model_api_id", model_id),
        "capabilities": sorted(caps | {"TEXT", "STREAMING"}),
    }


#: The full model registry. A model is listed regardless of whether its API key
#: is configured; models_available() determines which are actually offered.
MODELS = [
    _model(
        "claude",
        "anthropic",
        "Claude",
        "Deep reasoning and clear explanations.",
        model_api_id=os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-latest"),
        context_window=200000,
        capabilities=["TEXT", "CODE", "MATH", "PDF", "FILES", "LONG_CONTEXT"],
    ),
    _model(
        "gpt",
        "openai",
        "GPT",
        "Versatile general-purpose study assistant.",
        model_api_id=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        context_window=128000,
        capabilities=["TEXT", "CODE", "MATH", "IMAGE", "FILES", "LONG_CONTEXT"],
    ),
    _model(
        "gemini",
        "google",
        "Gemini",
        "Useful for multimodal and broad study tasks.",
        model_api_id=os.getenv("GOOGLE_MODEL", "gemini-1.5-flash"),
        context_window=1000000,
        capabilities=["TEXT", "CODE", "MATH", "IMAGE", "PDF", "FILES", "LONG_CONTEXT"],
    ),
]


def enabled_provider_ids():
    """Return the set of provider ids whose API keys are configured."""
    ids = set()
    if os.getenv("OPENAI_API_KEY"):
        ids.add("openai")
    if os.getenv("ANTHROPIC_API_KEY"):
        ids.add("anthropic")
    if os.getenv("GOOGLE_AI_API_KEY"):
        ids.add("google")
    return ids


def models_available():
    """Return the models that should be offered to users.

    Every model in the registry is offered. When its real provider lacks an API
    key, the provider router transparently falls back to the mock provider, so
    the feature is fully usable without credentials and switches to real
    providers automatically once keys are set.
    """
    return list(MODELS)


def get_model(model_id):
    """Return a model descriptor by id, or None."""
    for m in MODELS:
        if m["id"] == model_id:
            return m
    return None


def model_supports(model_id, capability):
    """Return True if a model advertises a capability."""
    model = get_model(model_id)
    if not model:
        return False
    return capability in model["capabilities"]
