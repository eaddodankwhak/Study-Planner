"""Gateway adapter layer: how the AI Gateway talks to providers.

Three adapters exist, matching the build prompt:
  * ``openai_compat`` — OpenAI-compatible chat-completions surface. Covers
    OpenAI, Gemini (OpenAI-compat endpoint), DeepSeek, Groq, Mistral,
    OpenRouter and Cerebras. Granted ``base_url`` + key come only from the
    registry (never user input), so no SSRF vector is introduced.
  * ``anthropic`` — Anthropic Messages API (Claude).
  * ``copilot`` — GitHub Copilot, which is OpenAI-compatible on the wire but
    requires an editor identity header and a GitHub token rather than a key.

Each adapter is a tiny stateless wrapper: the gateway constructs one per call
with the resolved key, calls it, gets usage back, and discards it. No key is
ever cached on a shared instance.

The two adapters return the same result dict used everywhere else in the app:
{"content": str, "usage": {"inputTokens": int, "outputTokens": int}}.
"""

import json

from ai.providers._http import ProviderHTTPError, post_json, stream_json_lines

#: Appended to the system prompt whenever a caller asks for JSON. Kept in one
#: place so the adapters cannot drift into describing a schema differently.
_SCHEMA_INSTRUCTION = (
    "Reply with a single JSON object and nothing else. It must match this "
    "JSON schema exactly:\n{schema}"
)


def build_adapter(provider, api_key):
    """Return an adapter instance for a registry provider row + resolved key."""
    adapter = provider["adapter"]

    if adapter == "anthropic":
        return AnthropicAdapter(api_key, provider.get("base_url"))
    if adapter == "copilot":
        return CopilotAdapter(api_key, provider.get("base_url"))
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
        #: Token counts from the most recent call. Streamed responses only
        #: report usage as the stream ends, so the caller reads this after it
        #: has drained the generator.
        self.last_usage = {"inputTokens": 0, "outputTokens": 0}

    def _headers(self, extra=None):
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

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

    def stream(self, request):
        """Yield text deltas as the provider produces them.

        ``stream_options.include_usage`` makes OpenAI send a final usage-only
        chunk, which is the only way to bill a streamed request accurately;
        without it a stream would always look free.
        """
        payload = self._payload(request, stream=True)
        payload["stream_options"] = {"include_usage": True}
        for data in stream_json_lines(self._endpoint(), self._headers(), payload):
            usage = data.get("usage")
            if usage:
                self.last_usage = {
                    "inputTokens": usage.get("prompt_tokens", 0),
                    "outputTokens": usage.get("completion_tokens", 0),
                }
            for choice in data.get("choices") or []:
                text = (choice.get("delta") or {}).get("content")
                if text:
                    yield text


class AnthropicAdapter:
    """Anthropic Messages API client (no SDK dependency)."""

    provider_role = "anthropic"

    def __init__(self, api_key, base_url):
        self.api_key = api_key
        self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")
        self.last_usage = {"inputTokens": 0, "outputTokens": 0}

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

    def stream(self, request):
        """Yield text deltas from the Messages streaming API.

        Anthropic reports usage in ``message_start`` (input tokens) and the
        final ``message_delta`` (output tokens), so both are captured here
        rather than leaving every streamed request looking free.
        """
        system, messages = self._messages(request)
        payload = {
            "model": request["model"],
            "max_tokens": request.get("max_tokens", 1500),
            "messages": messages,
            "stream": True,
        }
        if request.get("temperature") is not None:
            payload["temperature"] = request["temperature"]
        if system:
            payload["system"] = system
        usage = {"inputTokens": 0, "outputTokens": 0}
        for data in stream_json_lines(self._endpoint(), self._headers(), payload):
            kind = data.get("type")
            if kind == "message_start":
                start = (data.get("message") or {}).get("usage") or {}
                usage["inputTokens"] = start.get("input_tokens", 0)
            elif kind == "content_block_delta":
                text = (data.get("delta") or {}).get("text")
                if text:
                    yield text
            elif kind == "message_delta":
                out = (data.get("usage") or {}).get("output_tokens")
                if out is not None:
                    usage["outputTokens"] = out
        self.last_usage = usage


class CopilotAdapter(OpenAICompatAdapter):
    """GitHub Copilot: OpenAI-compatible on the wire, GitHub-auth in practice.

    Two things make it different from a plain OpenAI-compatible endpoint:
    it authenticates with a GitHub token that carries the "Copilot requests"
    permission rather than a provider API key, and it rejects requests that
    do not present an editor identity. Business plans and individual plans
    live on different hosts, so verify() tries both.
    """

    provider_role = "copilot"

    _INDIVIDUAL_BASE = "https://api.individual.githubcopilot.com"

    def _headers(self, extra=None):
        headers = super()._headers(extra)
        # Tokens minted for VS Code Copilot are accepted regardless of
        # integration-scoped checks when the request looks like an editor.
        headers["Editor-Version"] = "vscode/1.104.1"
        headers["Copilot-Integration-Id"] = "vscode-chat"
        return headers

    def verify(self):
        """Probe the Copilot hosts with a 1-token request, not /models.

        Copilot exposes no model-list endpoint, and the correct host depends
        on which plan the token is on, so both are tried once each.
        """
        payload = {
            "model": "gpt-5-mini",
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "stream": False,
        }
        last = ("", None)
        for base in (self.base_url, self._INDIVIDUAL_BASE):
            try:
                post_json(f"{base}/chat/completions", self._headers(), payload, timeout=30)
                return True, "Works"
            except ProviderHTTPError as exc:
                if exc.status in (400, 401, 402, 403):
                    last = (base, exc.status)
                    continue
                return False, f"Copilot returned HTTP {exc.status}."
            except Exception:  # noqa: BLE001 - try the other host
                last = (base, "network")
                continue
        if last[1] in (401, 403):
            return False, (
                "GitHub rejected your Copilot token (401/403). Use a "
                "fine-grained PAT with the Copilot requests permission."
            )
        if last[1] == 402:
            return False, "Your GitHub Copilot plan is out of quota or needs payment (402)."
        return False, "Could not reach GitHub Copilot."
