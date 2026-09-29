"""Configuration and hard limits for the AI Gateway feature.

Mirrors the stash.config pattern: every tunable lives here, read from
environment once at import, so the rest of the gateway package refers to one
source of truth. All values have safe, configurable defaults.
"""

import os


def _env_int(name, default):
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name, default):
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


class GatewayConfig:
    """Runtime configuration for the AI Gateway (environment-overridable)."""

    def __init__(self):
        # Per-user daily request quota for free-tier providers.
        self.free_daily_requests = _env_int("AI_GATEWAY_FREE_DAILY_REQUESTS", 40)
        self.standard_daily_requests = _env_int("AI_GATEWAY_STANDARD_DAILY_REQUESTS", 60)
        self.premium_daily_requests = _env_int("AI_GATEWAY_PREMIUM_DAILY_REQUESTS", 80)

        # Number of days a free/standard/premium tier's quota window spans.
        self.quota_window_days = _env_int("AI_GATEWAY_QUOTA_WINDOW_DAYS", 1)

        # Cache: shared document-generation responses are cached by a
        # content-hash key so re-uploading the same textbook costs nothing.
        # ai_cache is a single global table (the payload is content, not
        # personal data), so the cap is a global row count, not per user.
        self.cache_enabled = os.getenv("AI_GATEWAY_CACHE_ENABLED", "1") not in (
            "0", "false", "no"
        )
        self.cache_max_rows = _env_int("AI_GATEWAY_CACHE_MAX_ROWS", 500)
        self.cache_ttl_seconds = _env_int("AI_GATEWAY_CACHE_TTL_SECONDS", 24 * 3600)

        # Fallback: try the first healthy enabled model, then the next.
        self.fallback_max_tries = _env_int("AI_GATEWAY_FALLBACK_MAX_TRIES", 3)

        # Circuit breaker: open after N consecutive failures on a provider,
        # stay open for this many seconds before a half-open retry.
        self.circuit_failure_threshold = _env_int("AI_GATEWAY_CIRCUIT_THRESHOLD", 3)
        self.circuit_open_seconds = _env_int("AI_GATEWAY_CIRCUIT_OPEN_SECONDS", 300)

        # Client request timeout (provider calls).
        self.request_timeout_seconds = _env_float("AI_GATEWAY_TIMEOUT_SECONDS", 60.0)

        # Server-side provider keys for the built-in (gateway-owned) providers.
        # These are read lazily by registry/adapters so a missing key simply
        # disables that provider rather than crashing the app.
        self.server_keys = {
            "GEMINI_API_KEY": os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_AI_API_KEY"),
            "DEEPSEEK_API_KEY": os.getenv("DEEPSEEK_API_KEY"),
            "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
            "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY"),
            "GROQ_API_KEY": os.getenv("GROQ_API_KEY"),
            "MISTRAL_API_KEY": os.getenv("MISTRAL_API_KEY"),
            "OPENROUTER_API_KEY": os.getenv("OPENROUTER_API_KEY"),
            "CEREBRAS_API_KEY": os.getenv("CEREBRAS_API_KEY"),
            "COPILOT_GITHUB_TOKEN": os.getenv("COPILOT_GITHUB_TOKEN") or os.getenv("GH_TOKEN"),
        }


GatewayConfig = GatewayConfig()  # noqa: E305  (module-level singleton)