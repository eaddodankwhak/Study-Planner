"""Gateway adapter layer: how the AI Gateway talks to providers.

Exactly two adapters exist, matching the build prompt:
  * ``openai_adapter`` — OpenAI-compatible chat-completions surface. Covers
    OpenAI, Gemini (OpenAI-compat endpoint), DeepSeek, Groq, Mistral,
    OpenRouter and Cerebras. Granted ``base_url`` + key come only from the
    registry (never user input), so no SSRF vector is introduced.
  * ``anthropic_adapter`` — Anthropic Messages API (Claude).

Each adapter is a tiny stateless wrapper: the gateway constructs one per call
with the resolved key, calls it, gets usage back, and discards it. No key is
ever cached on a shared instance.

The two adapters return the same result dict used everywhere else in the app:
{"content": str, "usage": {"inputTokens": int, "outputTokens": int}}.
"""

import json

from ai.providers._http import ProviderHTTPError, post_json

#: Appended to the system prompt whenever a caller asks for JSON. Kept in one
#: place so the two adapters cannot drift into describing a schema differently.
_SCHEMA_INSTRUCTION = (
    "Reply with a single JSON object and nothing else. It must match this "
    "JSON schema exactly:\n{schema}"
)


def build_adapter(provider, api_key):
    """Return an adapter instance for a registry provider row + resolved key."""
    adapter = provider["adapter"]

    if adapter == "anthropic":
        return AnthropicAdapter(api_key, provider.get("base_url"))
    if adapter == "openai_compat":
        return OpenAICompatAdapter(api_key, provider.get("base_url"))

    raise ValueError(f"Unknown gateway adapter: {adapter!r}")


def _with_schema_in_prompt(messages, schema):
    """Return messages with the JSON schema stated in the system prompt.

    Every provider we support except OpenAI's own strict ``json_schema`` mode
    will happily return a perfectly valid JSON object of the wrong shape, so
    the shape has to be in the prompt. The messages list is copied rather than
    mutated: the gateway reuses one request dict across retry attempts.
    """
    instruction = _SCHEMA_INSTRUCTION.format(schema=json.dumps(schema, indent=2))
    out = []
    inserted = False
    for m in messages:
        if m.get("role") == "system":
            content = (m.get("content") or "").rstrip()
            out.append({**m, "content": f"{content}\n\n{instruction}".strip()})
            inserted = True
        else:
            out.append(dict(m))
    if not inserted:
        out.insert(0, {"role": "system", "content": instruction})
    return out


class OpenAICompatAdapter:
    """An OpenAI-compatible chat completions client (no SDK dependency)."""

    provider_role = "openai_compat"

    def __init__(self, api_key, base_url):
        self.api_key = api_key
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _endpoint(self):
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def _payload(self, request, schema=None, stream=False):
        messages = list(request.get("messages", []))
        payload = {
            "model": request["model"],
            "messages": messages,
            "stream": stream,
        }
        if request.get("max_tokens") is not None:
            payload["max_tokens"] = request["max_tokens"]
        if request.get("temperature") is not None:
            payload["temperature"] = request["temperature"]
        if schema is not None:
            payload["response_format"] = {
                "type": "json_object",
            }
        return payload

    def _post(self, request, schema=None):
        data = post_json(
            self._endpoint(),
            self._headers(),
            self._payload(request, schema=schema),
            timeout=120,
        )
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        return {
            "content": content or "",
            "usage": {
                "inputTokens": usage.get("prompt_tokens", 0),
                "outputTokens": usage.get("completion_tokens", 0),
            },
        }

    def generate(self, request):
        """Return {"content", "usage"} using standard chat completions."""
        return self._post(request)

    def generate_json(self, request, schema=None):
        """JSON mode: constrain the reply to a single JSON object.

        ``response_format=json_object`` is the widest-supported JSON knob
        across OpenAI-compatible providers, but it only guarantees *valid
        JSON*, never the right shape, so the schema is also stated in the
        system prompt and the gateway re-validates the parsed result.
        """
        if schema is None:
            return self._post(request)
        request = dict(request)
        request["messages"] = _with_schema_in_prompt(
            request.get("messages", []), schema
        )
        return self._post(request, schema=schema)

    def verify(self):
        """Validate the key against the provider's model list endpoint.

        Not all OpenAI-compatible providers expose /models; those that need a
        different probe can override this method.
        """
        try:
            from ai.providers._http import get_json

            get_json(f"{self.base_url}/models", self._headers(), timeout=30)
        except ProviderHTTPError as exc:
            if exc.status == 401:
                return False, "Key rejected (401)."
            return False, f"Provider returned HTTP {exc.status}."
        except Exception as exc:  # noqa: BLE001 - network errors surface as-is
            return False, f"Could not reach provider: {exc}"
        return True, "Works"


class AnthropicAdapter:
    """Anthropic Messages API client (no SDK dependency)."""

    provider_role = "anthropic"

    def __init__(self, api_key, base_url):
        self.api_key = api_key
        self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")

    def _headers(self):
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    def _messages(self, request, schema=None):
        """Split an OpenAI-shaped request into Anthropic's system + messages.

        Anthropic takes the system prompt as a top-level field rather than a
        message, so the gateway's system message is flattened here. When a
        schema is supplied it is folded into that same system prompt, because
        the Messages API has no response_format equivalent.
        """
        system = ""
        messages = []
        for m in request.get("messages", []):
            if m["role"] == "system":
                system += (m["content"] or "")
            else:
                messages.append({"role": m["role"], "content": m["content"]})
        if schema is not None:
            system = (
                f"{system}\n\n{_SCHEMA_INSTRUCTION.format(schema=json.dumps(schema, indent=2))}"
            ).strip()
        return system, messages

    def _endpoint(self):
        if self.base_url.endswith("/messages"):
            return self.base_url
        return f"{self.base_url}/v1/messages"

    def _post(self, request, schema=None):
        system, messages = self._messages(request, schema=schema)
        payload = {
            "model": request["model"],
            "max_tokens": request.get("max_tokens", 1500),
            "messages": messages,
        }
        if request.get("temperature") is not None:
            payload["temperature"] = request["temperature"]
        if system:
            payload["system"] = system
        data = post_json(self._endpoint(), self._headers(), payload, timeout=120)
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

    def generate(self, request):
        return self._post(request)

    def generate_json(self, request, schema=None):
        """JSON mode via a prompt-level schema + caller-side validation.

        The Messages API has no response_format equivalent, so the schema is
        appended to the system prompt. Passing it through here (rather than
        dropping it, which would leave the model guessing the shape) is what
        makes Claude usable for the structured Stash/quiz payloads.
        """
        if schema is None:
            return self._post(request)
        return self._post(request, schema=schema)

    def verify(self):
        from ai.providers._http import get_json

        try:
            get_json(f"{self.base_url}/v1/models", self._headers(), timeout=30)
        except ProviderHTTPError as exc:
            if exc.status in (401, 403):
                return False, "Key rejected by Claude (401)."
            return False, f"Claude returned HTTP {exc.status}."
        except Exception as exc:  # noqa: BLE001 - network errors surface as-is
            return False, f"Could not reach Claude: {exc}"
        return True, "Works"