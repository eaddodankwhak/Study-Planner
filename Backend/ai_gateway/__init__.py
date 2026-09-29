"""AI Gateway: server-side AI access for the Study Planner.

The gateway owns provider/model selection, key resolution (server-side keys),
usage accounting, quotas, caching, fallback and circuit-breaking, so students
never enter an API key. See AI_GATEWAY_README.md for the full design.
"""

from .registry import (
    list_models as list_enabled_models,
    list_providers,
    get_provider,
    get_model,
    ensure_seeded,
)

__all__ = [
    "list_enabled_models",
    "list_providers",
    "get_provider",
    "get_model",
    "ensure_seeded",
]

__all__ = [
    "list_enabled_models",
    "list_providers",
    "get_provider",
    "get_model",
    "ensure_seeded",
]