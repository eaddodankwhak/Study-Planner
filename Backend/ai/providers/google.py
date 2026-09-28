"""Google Gemini provider adapter (v1beta generateContent API)."""

import os
import urllib.parse

from ._http import ProviderHTTPError, get_json, post_json
from .base import AIProvider


class GoogleProvider(AIProvider):
    name = "google"

    def __init__(self, api_key=None):
        # Accept the legacy Gemini env name as an alias; GOOGLE_AI_API_KEY wins.
        self.api_key = api_key or os.getenv("GOOGLE_AI_API_KEY") or os.getenv("GEMINI_API_KEY")
        self.base_url = os.getenv("GOOGLE_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")

    def _url(self, model, stream=False):
        endpoint = "streamGenerateContent" if stream else "generateContent"
        qs = urllib.parse.urlencode({"key": self.api_key, "alt": "sse"} if stream else {"key": self.api_key})
        return f"{self.base_url}/models/{model}:{endpoint}?{qs}"

    def _google_schema(self, schema):
        """Convert a JSON Schema dict into a Gemini responseSchema.

        Gemini's responseSchema accepts a small OpenAPI-style subset: no type
        unions like ["string", "null"] (those become type + nullable), and no
        $schema/$defs. Returns None when the schema is unusable so callers can
        fall back to plain JSON mode.
        """
        if not isinstance(schema, dict):
            return None
        out = {}
        for key, value in schema.items():
            if key == "type" and isinstance(value, list):
                non_null = [t for t in value if t != "null"]
                out["type"] = (non_null or ["string"])[0]
                if len(non_null) != len(value):
                    out["nullable"] = True
            elif key == "properties" and isinstance(value, dict):
                out["properties"] = {
                    k: self._google_schema(v) for k, v in value.items()
                }
            elif key in ("required", "additionalProperties", "items", "enum"):
                out[key] = value
            elif key in ("const", "default", "examples"):
                continue
            else:
                out[key] = value
        return out

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
        if request.get("response_schema"):
            # Constrain the reply to structured JSON at the API level.
            generation_config["responseMimeType"] = "application/json"
            transformed = self._google_schema(request["response_schema"])
            if transformed:
                generation_config["responseSchema"] = transformed
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

    def verify(self, api_key=None):
        """Validate a key via the Gemini models list endpoint."""
        key = api_key or self.api_key
        if not key:
            return False, "No API key provided."
        list_url = f"{self.base_url}/models?key={urllib.parse.quote(key)}"
        try:
            data = get_json(list_url, {"Content-Type": "application/json"}, timeout=30)
            if isinstance(data.get("models"), list):
                return True, "Works"
            return False, "Google returned an unexpected response."
        except ProviderHTTPError as exc:
            if exc.status == 400 or exc.status == 403:
                return False, "Key rejected by Gemini (403)."
            return False, f"Gemini returned HTTP {exc.status}."
        except Exception as exc:  # noqa: BLE001 - network/SSL errors surface as-is
            return False, f"Could not reach Gemini: {exc}"
