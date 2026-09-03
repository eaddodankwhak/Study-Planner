"""AI Learning Hub package.

Exposes the API blueprint and the main subsystems. Register the blueprint in
app.py via register_ai_blueprint(app).
"""

from . import limits, models, prompts, service, storage

__all__ = ["limits", "models", "prompts", "service", "storage", "get_ai_blueprint"]


def get_ai_blueprint():
    """Return the AI API blueprint (registers its provider first)."""
    from .api import ai_api
    # The API module imports get_provider at call time; ensure registry builds.
    from .providers import get_provider as _gp
    _gp("mock")  # causes registry to be built
    return ai_api
