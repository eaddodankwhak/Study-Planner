"""Google Gemini provider adapter (v1beta generateContent API)."""

import os
import urllib.parse

from ._http import post_json
from .base import AIProvider


class GoogleProvider(AIProvider):
    name = "google"

    def __init__(self, api_key=None):
        self.api_key = api_key or os.getenv("GOOGLE_AI_API_KEY")
        self.base_url = os.getenv("GOOGLE_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")

    def _url(self, model, stream=False):
        endpoint = "streamGenerateContent" if stream else "generateContent"
        qs = urllib.parse.urlencode({"key": self.api_key, "alt": "sse"} if stream else {"key": self.api_key})
        return f"{self.base_url}/models/{model}:{endpoint}?{qs}"

    def _payload(self, request):
        contents = []
        for m in request.get("messages", []):
            if m["role"] == "system":
                # Prepend system guidance as a user-model pairing isn't supported;
                # inject into first user turn using a special role is not available.
                continue
            role = "model" if m["role"] == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": m["content"]}]})
        # Build generationConfig with token cap.
        generation_config = {"maxOutputTokens": request.get("max_tokens", 1500)}
        system_instruction = None
        sys_text = " ".join(m["content"] for m in request.get("messages", []) if m["role"] == "system")
        if sys_text:
            system_instruction = {"parts": [{"text": sys_text}]}
        payload = {"contents": contents, "generationConfig": generation_config}
        if system_instruction:
            payload["systemInstruction"] = system_instruction
        return payload

    def generate(self, request):
        data = post_json(self._url(request["model"]), {"Content-Type": "application/json"}, self._payload(request))
        text = ""
        for candidate in data.get("candidates", []):
            for part in candidate.get("content", {}).get("parts", []):
                text += part.get("text", "")
        usage = data.get("usageMetadata", {})
        return {
            "content": text,
            "usage": {
                "inputTokens": usage.get("promptTokenCount", 0),
                "outputTokens": usage.get("candidatesTokenCount", 0),
            },
        }

    def stream(self, request):
        data = post_json(self._url(request["model"], stream=True), {"Content-Type": "application/json"}, self._payload(request))
        # alt=sse returns a JSON object containing a "data" array of chunks.
        pairs = data.get("data") or []
        for chunk in pairs:
            for candidate in chunk.get("candidates", []):
                for part in candidate.get("content", {}).get("parts", []):
                    text = part.get("text", "")
                    if text:
                        yield text
