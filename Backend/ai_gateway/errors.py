"""Error types for the AI Gateway.

Distinguish failure classes so callers (features and the API layer) can map
them to the right HTTP response and/or user message. All messages are
user-safe by construction.
"""


class GatewayError(Exception):
    """Base class for gateway failures; message is user-safe."""


class AIDisabledError(GatewayError):
    """AI activity is turned off for the user (privacy switch)."""


class NoProviderAvailableError(GatewayError):
    """No enabled, keyed provider/model is usable for this request."""


class PreferenceError(GatewayError):
    """User's stored preference references a model that no longer exists."""


class ProviderCallError(GatewayError):
    """The provider call failed after retries (transient errors exhausted)."""


class JSONValidationError(GatewayError):
    """The provider returned text that was not valid/parseable JSON."""

    def __init__(self, message, raw_content=None):
        self.message = message
        self.raw_content = raw_content
        super().__init__(message)


class QuotaExceededError(GatewayError):
    """The user has used their per-day request/token budget on the server key."""


class AllProvidersFailedError(GatewayError):
    """Every eligible provider/model failed (after fallback attempts)."""