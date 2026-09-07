"""Base AI provider abstraction shared by all adapters.

The rest of the application talks to AIProvider instances (via the provider
router in providers/__init__.py) rather than to any provider SDK directly. This
makes it trivial to add a new provider: implement this interface and register
it in providers/__init__.py.
"""

import abc


class AIProvider(abc.ABC):
    """Common interface every AI provider adapter implements."""

    #: Set to True on the mock provider so callers can detect test/demo mode.
    is_mock = False

    #: Human readable provider name.
    name = "generic"

    @abc.abstractmethod
    def generate(self, request):
        """Return the full text response for a request dict.

        request: {
            "messages": [{"role": "system"|"user"|"assistant", "content": str}],
            "model": "gpt-4o-mini" (provider-specific model id),
            "max_tokens": int,
        }
        Returns {"content": str, "usage": {"inputTokens": int, "outputTokens": int}}
        """
        raise NotImplementedError

    def stream(self, request):
        """Yield text chunks progressively for a request dict.

        Default implementation delegates to generate() and yields one chunk, so
        providers without native streaming still work. Providers that support
        streaming override this to yield incremental fragments.
        """
        result = self.generate(request)
        yield result["content"]

    def supports(self, capability):
        """Return True if this provider supports the given capability."""
        return True

    def verify(self, api_key=None):
        """Validate an API key against the provider.

        Returns (ok: bool, note: str) where note is a short human-readable
        description ("Works" or why it failed). Used by the BYOK connection
        flow in AI settings and mocked in hermetic tests.
        """
        return False, "Key verification is not supported for this provider."
