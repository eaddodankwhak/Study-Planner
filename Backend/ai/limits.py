"""Usage limits and rate limiting for the AI Learning Hub.

Limits are configurable via environment variables (or sensible dev defaults) so
subscription tiers can be introduced later without code changes. This module
also records per-request usage for observability.
"""

import os
import time

from . import storage

#: Optional per-user daily request cap. Set AI_MAX_REQUESTS_PER_DAY to override.
MAX_REQUESTS_PER_DAY = int(os.getenv("AI_MAX_REQUESTS_PER_DAY", "100"))

#: Maximum characters allowed for a single user question.
MAX_QUESTION_CHARS = int(os.getenv("AI_MAX_QUESTION_CHARS", "8000"))

#: Maximum characters of extracted study-material text we will send.
MAX_MATERIAL_CHARS = int(os.getenv("AI_MAX_MATERIAL_CHARS", "60000"))

#: Maximum output tokens requested from a provider per request.
MAX_OUTPUT_TOKENS = int(os.getenv("AI_MAX_OUTPUT_TOKENS", "2000"))

#: Maximum bytes for an uploaded study material file.
MAX_FILE_BYTES = int(os.getenv("AI_MAX_FILE_BYTES", str(10 * 1024 * 1024)))


class RateLimitError(Exception):
    """Raised when a user exceeds their configured usage limits."""


class ValidationError(Exception):
    """Raised when a request is invalid (too large, empty, etc.)."""


def check_limit(user_id):
    """Raise RateLimitError if the user has hit their daily request cap."""
    usage = storage.get_usage(user_id)
    day = time.strftime("%Y-%m-%d", time.gmtime())
    count = usage.get("days", {}).get(day, {}).get("requests", 0)
    if count >= MAX_REQUESTS_PER_DAY:
        raise RateLimitError(
            f"You've used your AI requests for today ({MAX_REQUESTS_PER_DAY}). "
            "Please try again tomorrow."
        )


def remaining_requests(user_id):
    """Return (used, limit) for the current UTC day."""
    usage = storage.get_usage(user_id)
    day = time.strftime("%Y-%m-%d", time.gmtime())
    used = usage.get("days", {}).get(day, {}).get("requests", 0)
    return used, MAX_REQUESTS_PER_DAY
