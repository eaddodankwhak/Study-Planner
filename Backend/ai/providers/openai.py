"""OpenAI provider adapter (chat completions API)."""

import os

from ._http import ProviderHTTPError, get_json, post_json, stream_json_lines
from .base import AIProvider


class OpenAIProvider(AIProvider):
    name = "openai"

    def __init__(self, api_key=None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    def _headers(self, api_key=None):
        return {
            "Authorization": f"Bearer {api_key or self.api_key}",
            "Content-Type": "application/json",
        }

    def generate(self, request):
        payload = {
            "model": request["model"],
            "messages": request.get("messages", []),
            "max_tokens": request.get("max_tokens", 1500),
            "stream": False,
        }
        data = post_json(f"{self.base_url}/chat/completions", self._headers(), payload)
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return {
            "content": content or "",
            "usage": {
                "inputTokens": usage.get("prompt_tokens", 0),
                "outputTokens": usage.get("completion_tokens", 0),
            },
        }

    def stream(self, request):
        payload = {
            "model": request["model"],
            "messages": request.get("messages", []),
            "max_tokens": request.get("max_tokens", 1500),
            "stream": True,
        }
        for data in stream_json_lines(f"{self.base_url}/chat/completions", self._headers(), payload):
            choices = data.get("choices") or []
            if choices:
                delta = choices[0].get("delta", {})
                text = delta.get("content")
                if text:
                    yield text

    def verify(self, api_key=None):
        """Validate a key via the OpenAI models list endpoint."""
        key = api_key or self.api_key
        if not key:
            return False, "No API key provided."
        try:
            get_json(f"{self.base_url}/models", self._headers(key), timeout=30)
        except ProviderHTTPError as exc:
            if exc.status == 401:
                return False, "Key rejected by OpenAI (401)."
            return False, f"OpenAI returned HTTP {exc.status}."
        except Exception as exc:  # noqa: BLE001 - network/SSL errors surface as-is
            return False, f"Could not reach OpenAI: {exc}"
        return True, "Works"
