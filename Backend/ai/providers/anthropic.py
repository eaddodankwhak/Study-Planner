"""Anthropic provider adapter (Messages API)."""

import os

from ._http import post_json, stream_json_lines
from .base import AIProvider


class AnthropicProvider(AIProvider):
    name = "anthropic"

    def __init__(self, api_key=None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        self.base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1")

    def _headers(self):
        return {
            "x-api-key": self.api_key,
            "anthropic-version": os.getenv("ANTHROPIC_VERSION", "2023-06-01"),
            "Content-Type": "application/json",
        }

    def _messages(self, request):
        # Split system message out; Anthropic takes a separate system param.
        system = ""
        messages = []
        for m in request.get("messages", []):
            if m["role"] == "system":
                system += (m["content"] or "")
            else:
                messages.append({"role": m["role"], "content": m["content"]})
        return system, messages

    def generate(self, request):
        system, messages = self._messages(request)
        payload = {
            "model": request["model"],
            "max_tokens": request.get("max_tokens", 1500),
            "messages": messages,
        }
        if system:
            payload["system"] = system
        data = post_json(f"{self.base_url}/messages", self._headers(), payload)
        blocks = data.get("content", [])
        content = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
        usage = data.get("usage", {})
        return {
            "content": content,
            "usage": {
                "inputTokens": usage.get("input_tokens", 0),
                "outputTokens": usage.get("output_tokens", 0),
            },
        }

    def stream(self, request):
        system, messages = self._messages(request)
        payload = {
            "model": request["model"],
            "max_tokens": request.get("max_tokens", 1500),
            "messages": messages,
            "stream": True,
        }
        if system:
            payload["system"] = system
        for data in stream_json_lines(f"{self.base_url}/messages", self._headers(), payload):
            if data.get("type") == "content_block_delta":
                delta = data.get("delta", {})
                if delta.get("type") == "text_delta" and delta.get("text"):
                    yield delta["text"]
