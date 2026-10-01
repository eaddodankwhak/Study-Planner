"""AI Gateway: server-side AI access for the Study Planner.

The gateway owns provider/model selection, key resolution (server-side keys,
falling back to the student's own BYOK), usage accounting, quotas, caching,
fallback and circuit-breaking, so students never enter an API key. See
AI_GATEWAY_README.md for the full design.
"""

from .registry import (
    list_models as list_enabled_models,
    list_providers,
    get_provider,
    get_model,
    ensure_seeded,
)
from .errors import (
    GatewayError,
    AIDisabledError,
    NoProviderAvailableError,
    PreferenceError,
    ProviderCallError,
    JSONValidationError,
    QuotaExceededError,
    AllProvidersFailedError,
)
from .gateway import (
    gateway_enabled,
    select_model,
    generate_text,
    generate_json,
    stream_text,
    server_key_for,
    get_preference,
    set_preference,
    preferred_model,
)
from . import cache, health, quotas  # noqa: F401

__all__ = [
    "list_enabled_models",
    "list_providers",
    "get_provider",
    "get_model",
    "ensure_seeded",
    "GatewayError",
    "AIDisabledError",
    "NoProviderAvailableError",
    "PreferenceError",
    "ProviderCallError",
    "JSONValidationError",
    "QuotaExceededError",
    "AllProvidersFailedError",
    "gateway_enabled",
    "select_model",
    "generate_text",
    "generate_json",
    "stream_text",
    "server_key_for",
    "get_preference",
    "set_preference",
    "preferred_model",
    "get_gateway_blueprint",
]


def get_gateway_blueprint():
    """The student-facing blueprint, imported lazily.

    Imported on demand rather than at package import time so that server-side
    callers (``generate_text`` and friends) do not drag Flask's request
    context into a plain Python/worker process.
    """
    from .api import gateway_api

    return gateway_api


def get_admin_blueprint():
    """The admin console blueprint, imported lazily for the same reason."""
    from .admin import admin_api

    return admin_api
