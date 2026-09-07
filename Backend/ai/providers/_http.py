"""Shared HTTP helpers for calling AI provider REST APIs without SDKs.

Kept deliberately thin. Providers would typically use official SDKs; this
stdlib-based client keeps the backend dependency-light and runs the same in
any environment. Providers using SDKs can bypass these helpers entirely.
"""

import json
import ssl
import urllib.request


class ProviderHTTPError(Exception):
    """Raised when an AI provider returns a non-2xx response."""

    def __init__(self, status, body):
        self.status = status
        self.body = body
        super().__init__(f"AI provider returned HTTP {status}")


def get_json(url, headers, timeout=30):
    """GET a URL and return the parsed JSON body (checking status).

    Used by provider.verify() calls to validate a per-user BYOK key.
    """
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            if not raw:
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as exc:  # noqa: F821
        body = exc.read().decode("utf-8", errors="replace")
        raise ProviderHTTPError(exc.code, body) from exc


def post_json(url, headers, payload, timeout=60):
    """POST a JSON payload and return the parsed JSON body (checking status)."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            if not raw:
                return {}
            return json.loads(raw)
    except urllib.error.HTTPError as exc:  # noqa: F821
        body = exc.read().decode("utf-8", errors="replace")
        raise ProviderHTTPError(exc.code, body) from exc


def stream_json_lines(url, headers, payload, timeout=120):
    """POST a JSON payload and yield parsed SSE/JSON lines as they arrive."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        buffer = b""
        while True:
            chunk = resp.read(1024)
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    line = line[len("data:"):].strip()
                if line and line != "[DONE]":
                    try:
                        yield json.loads(line)
                    except ValueError:
                        pass
