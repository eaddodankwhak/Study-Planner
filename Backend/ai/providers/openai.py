"""OpenAI provider adapter (chat completions API)."""

import os

from ._http import post_json, stream_json_lines
from .base import AIProvider


class OpenAIProvider(AIProvider):
    name = "openai"

    def __init__(self, api_key=None):
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
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
