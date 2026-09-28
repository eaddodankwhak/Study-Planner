"""DeepSeek provider adapter (OpenAI-compatible chat completions API).

DeepSeek exposes an OpenAI-compatible surface: a single Bearer API key against
https://api.deepseek.com, no OAuth. The adapter reuses OpenAI's request/response
shape; only the base URL, env key, and verification differ.

Model IDs (2026): deepseek-v4-flash / deepseek-v4-pro. The legacy
deepseek-chat / deepseek-reasoner IDs were retired in July 2026.
"""

import json
import os

from ._http import ProviderHTTPError, post_json
from .openai import OpenAIProvider


class DeepSeekProvider(OpenAIProvider):
    name = "deepseek"

    def __init__(self, api_key=None):
        self.api_key = api_key or os.getenv("DEEPSEEK_API_KEY")
        self.base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    def generate_json(self, request):
        """DeepSeek JSON mode: json_object + the schema embedded in the prompt.

        DeepSeek's OpenAI-compatible surface only offers `json_object` (no
        json_schema), so the expected shape is spelled out in the user prompt
        and the response is constrained to be a single JSON object.
        """
        if not request.get("response_schema"):
            return self.generate(request)
        system = "".join(m["content"] for m in request.get("messages", []) if m["role"] == "system")
        user = "".join(m["content"] for m in request.get("messages", []) if m["role"] != "system")
        schema_text = json.dumps(request["response_schema"])
        req = dict(request)
        req["messages"] = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": (
                    f"{user}\n\nReturn ONLY a single JSON object that conforms to "
                    f"this JSON schema, with no markdown fences and no commentary:\n"
                    f"{schema_text}"
                ),
            },
        ]
        req["response_format"] = {"type": "json_object"}
        return self.generate(req)

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