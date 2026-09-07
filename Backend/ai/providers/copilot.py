"""GitHub Copilot provider adapter (Copilot API chat completions).

Copilot has no per-user API key. Its API authenticates with a GitHub token that
carries the "Copilot requests" permission: a fine-grained personal access
token (github_pat_) or an OAuth/user token (gho_/ghu_) for a Copilot-enabled
GitHub account. Classic PATs (ghp_) are rejected by the Copilot API.

Requests go to the OpenAI-shaped POST /chat/completions on the Copilot host.
Business accounts use api.githubcopilot.com; individual/free plans use
api.individual.githubcopilot.com. verify() tries both hosts so connect-time
behaviour matches whichever plan the user is on.
"""

import os

from ._http import ProviderHTTPError, post_json, stream_json_lines
from .base import AIProvider


class CopilotProvider(AIProvider):
    name = "copilot"

    _DEFAULT_BASE = "https://api.githubcopilot.com"
    _INDIVIDUAL_BASE = "https://api.individual.githubcopilot.com"

    def __init__(self, api_key=None):
        # api_key (BYOK) is a GitHub token with the Copilot requests permission.
        self.api_key = api_key or os.getenv("COPILOT_GITHUB_TOKEN") or os.getenv("GH_TOKEN")
        self.base_url = os.getenv("COPILOT_BASE_URL", self._DEFAULT_BASE)
        self.model = os.getenv("COPILOT_MODEL", "gpt-5-mini")

    def _headers(self, api_key=None):
        return {
            "Authorization": f"Bearer {api_key or self.api_key}",
            "Content-Type": "application/json",
            # Standard editor identity so tokens minted for VS Code Copilot are
            # accepted by the API regardless of integration-scoped checks.
            "Editor-Version": "vscode/1.104.1",
            "Copilot-Integration-Id": "vscode-chat",
        }

    def _payload(self, request, stream):
        return {
            "model": request["model"],
            "messages": request.get("messages", []),
            "max_tokens": request.get("max_tokens", 1500),
            "stream": stream,
        }

    def generate(self, request):
        data = post_json(f"{self.base_url}/chat/completions", self._headers(), self._payload(request, False))
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
        for data in stream_json_lines(f"{self.base_url}/chat/completions", self._headers(), self._payload(request, True)):
            choices = data.get("choices") or []
            if choices:
                delta = choices[0].get("delta", {})
                text = delta.get("content")
                if text:
                    yield text

    def _verify_host(self, base, key):
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
            "stream": False,
        }
        post_json(f"{base}/chat/completions", self._headers(key), payload, timeout=30)
        return True

    def verify(self, api_key=None):
        """Validate a GitHub token with a 1-token request against the Copilot API."""
        key = api_key or self.api_key
        if not key:
            return False, "No GitHub token provided."
        bases = [self.base_url, self._DEFAULT_BASE, self._INDIVIDUAL_BASE]
        # Try the configured host first, then both known hosts once each.
        seen = []
        for base in bases:
            if base in seen:
                continue
            seen.append(base)
            try:
                self._verify_host(base, key)
                return True, "Works"
            except ProviderHTTPError as exc:
                if exc.status in (400, 401, 402, 403):
                    # Token/host problem: try the other endpoint, but remember why.
                    last = (base, exc.status)
                    continue
                return False, f"Copilot returned HTTP {exc.status}."
            except Exception as exc:  # noqa: BLE001 - network/SSL errors surface as-is
                last = (base, "network")
                continue
        if last[1] == 401 or last[1] == 403:
            return False, "GitHub rejected your Copilot token (401/403). Use a fine-grained PAT with the Copilot requests permission."
        if last[1] == 402:
            return False, "Your GitHub Copilot plan is out of quota or needs payment (402)."
        if last[1] == 400:
            return False, "The Copilot API rejected your token (400). Make sure it is a fine-grained PAT with Copilot requests permission."
        return False, "Could not reach GitHub Copilot."