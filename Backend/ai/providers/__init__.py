"""Provider registry / router for the AI Learning Hub.

The rest of the application obtains a provider via get_provider(provider_id)
and never constructs adapters directly. A provider keyed to its configured API
key is returned when present; otherwise the mock provider is returned so the
feature always works (clearly isolated for development).
"""

from .base import AIProvider
from .mock import MockProvider

_PROVIDERS = {}


def _register(provider):
    """Register a provider instance under its name key."""
    _PROVIDERS[provider.name] = provider


def _build_providers():
    """Instantiate and register all known provider adapters."""
    from .openai import OpenAIProvider
    from .anthropic import AnthropicProvider
    from .google import GoogleProvider

    _register(OpenAIProvider())
    _register(AnthropicProvider())
    _register(GoogleProvider())
    _register(MockProvider())


def get_provider(provider_id):
    """Return a provider adapter.

    If the requested provider has a configured API key it is returned; otherwise
    the mock provider is returned so requests still work (and so the AI Hub is
    usable during development without credentials).
    """
    if not _PROVIDERS:
        _build_providers()
    provider = _PROVIDERS.get(provider_id)
    if provider is None:
        return MockProvider()
    if getattr(provider, "is_mock", False):
        return provider
    # Real provider -> only use it when credentials are present.
    if getattr(provider, "api_key", None):
        return provider
    return MockProvider()


def provider_available(provider_id):
    """Return True if a provider has a configured API key (or is the mock)."""
    if not _PROVIDERS:
        _build_providers()
    provider = _PROVIDERS.get(provider_id)
    if provider is None:
        return False
    if getattr(provider, "is_mock", False):
        return True
    return bool(provider.api_key)


def list_providers():
    """Return metadata about all registered providers."""
    if not _PROVIDERS:
        _build_providers()
    return [
        {
            "id": key,
            "name": p.name,
            "isMock": bool(getattr(p, "is_mock", False)),
            "available": provider_available(key),
        }
        for key, p in _PROVIDERS.items()
    ]


# Make the base class importable from this package level for subclasses.
__all__ = ["AIProvider", "get_provider", "provider_available", "list_providers"]
