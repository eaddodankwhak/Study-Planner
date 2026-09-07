"""Provider registry / router for the AI Learning Hub.

The rest of the application obtains a provider via get_provider(provider_id)
and never constructs adapters directly. A provider keyed to its configured API
key is returned when present; otherwise the mock provider is returned so the
feature always works (clearly isolated for development).

Since the BYOK flow gives each user their own key, adapters are constructed
fresh on every call so a per-user api_key can be threaded through; there is no
shared cached instance to leak one user's key into another user's request.
"""

from .base import AIProvider
from .mock import MockProvider

_BUILDERS = {}


def _register(builder):
    """Register a provider class under its name key."""
    _BUILDERS[builder.name] = builder


def _build_providers():
    """Register all known provider adapter classes."""
    from .openai import OpenAIProvider
    from .anthropic import AnthropicProvider
    from .google import GoogleProvider

    _register(OpenAIProvider)
    _register(AnthropicProvider)
    _register(GoogleProvider)
    _register(MockProvider)


def get_provider(provider_id, api_key=None):
    """Return a provider adapter, built fresh for this call.

    api_key takes precedence over the environment-configured key so a per-user
    BYOK connection can be used without mutating any shared state. Falls back to
    the mock provider when no usable key is present (development/demo).
    """
    if not _BUILDERS:
        _build_providers()
    builder = _BUILDERS.get(provider_id)
    if builder is None:
        return MockProvider()
    provider = builder(api_key=api_key)
    if getattr(provider, "is_mock", False):
        return provider
    # Real provider -> only use it when credentials are present.
    if getattr(provider, "api_key", None):
        return provider
    return MockProvider()


def provider_available(provider_id, api_key=None):
    """Return True if a provider has a usable key (or is the mock).

    Judged by the *requested* provider rather than the router's mock fallback,
    so an unkeyed real provider is correctly reported as unavailable even
    though get_provider() hands back the mock for safe requests.
    """
    if not _BUILDERS:
        _build_providers()
    builder = _BUILDERS.get(provider_id)
    if builder is None:
        return False
    if getattr(builder, "is_mock", False):
        return True
    if api_key is not None:
        return bool(api_key)
    return bool(builder().api_key)


def list_providers():
    """Return metadata about all registered providers."""
    if not _BUILDERS:
        _build_providers()
    result = []
    for key, builder in _BUILDERS.items():
        if getattr(builder, "is_mock", False):
            result.append({"id": key, "name": builder.name, "isMock": True, "available": True})
        else:
            probe = builder()
            result.append({
                "id": key,
                "name": builder.name,
                "isMock": False,
                "available": bool(probe.api_key),
            })
    return result