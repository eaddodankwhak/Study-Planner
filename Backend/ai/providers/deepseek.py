"""DeepSeek provider adapter (OpenAI-compatible chat completions API).

DeepSeek exposes an OpenAI-compatible surface: a single Bearer API key against
https://api.deepseek.com, no OAuth. The adapter reuses OpenAI's request/response
shape; only the base URL, env key, and verification differ.

Model IDs (2026): deepseek-v4-flash / deepseek-v4-pro. The legacy
deepseek-chat / deepseek-reasoner IDs were retired in July 2026.
"""

import os

from ._http import ProviderHTTPError, post_json
from .openai import OpenAIProvider


class DeepSeekProvider(OpenAIProvider):
    name = "deepseek"

    def __init__(self, api_key=None):
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        self.base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    def verify(self, api_key=None):
        """Validate a key by asking for a 1-token reply.

        DeepSeek's OpenAI-compatible surface has no models-list guarantee, so a
        tiny chat call is the truthful check: it succeeds only with a valid key
        and a topped-up balance, which is exactly what send-time requires.
        """
        key = api_key or self.api_key
        if not key:
            return False, "No API key provided."
        payload = {
            "model": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "stream": False,
        }
        try:
            post_json(f"{self.base_url}/chat/completions", self._headers(key), payload, timeout=30)
        except ProviderHTTPError as exc:
            if exc.status == 401:
                return False, "Key rejected by DeepSeek (401)."
            if exc.status == 402:
                return False, "DeepSeek needs a topped-up balance (402)."
            return False, f"DeepSeek returned HTTP {exc.status}."
        except Exception as exc:  # noqa: BLE001 - network/SSL errors surface as-is
            return False, f"Could not reach DeepSeek: {exc}"
        return True, "Works"