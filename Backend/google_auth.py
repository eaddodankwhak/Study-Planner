"""Server-side helper for "Continue with Google" (OAuth 2.0, PKCE).

Runs against Google's public OAuth endpoints using only the standard library
(urllib). The app stores the OAuth Client ID locally (Settings > Sign-in
methods) — no client secret, no shipped key. PKCE is what authorizes the
code exchange, so the correct Google Cloud setup is a single free OAuth
Client ID created as a *Desktop app* (loopback redirect, secret optional).

Security notes:
- ``state`` and the PKCE ``code_verifier`` are random per request and kept in
  the user's signed session cookie.
- The ``id_token`` is validated by Google's tokeninfo endpoint (Google checks
  the RS256 signature), then we re-verify ``aud`` matches our client id and
  that ``email_verified`` is true before trusting the email.
"""

import base64
import hashlib
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
TOKENINFO_ENDPOINT = "https://oauth2.googleapis.com/tokeninfo"
SCOPE = "openid email profile"
REDIRECT_PATH = "/oauth/google/callback"
HTTP_TIMEOUT = 15


class GoogleAuthError(Exception):
    """Raised when a Google sign-in flow cannot complete; message is user-safe."""


def _b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def new_state():
    return secrets.token_urlsafe(32)


def new_code_verifier():
    return _b64url(secrets.token_bytes(48))  # 64 chars, valid range 43..128


def code_challenge(verifier):
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def redirect_uri(base_url):
    """Absolute callback URL built from a request root like 'http://127.0.0.1:5000/'."""
    return base_url.rstrip("/") + REDIRECT_PATH


def authorize_url(client_id, redirect_uri_value, state, challenge):
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri_value,
        "response_type": "code",
        "scope": SCOPE,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    return AUTH_ENDPOINT + "?" + urllib.parse.urlencode(params)


def _post_form(url, data):
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            if resp.status != 200:
                raise GoogleAuthError("Google rejected the sign-in request.")
            payload = resp.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        raise GoogleAuthError("Could not reach Google. Check your connection.") from exc
    try:
        return json.loads(payload)
    except ValueError as exc:
        raise GoogleAuthError("Google returned an unexpected response.") from exc


def _get_json(url):
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as resp:
            if resp.status != 200:
                raise GoogleAuthError("Google rejected the sign-in request.")
            payload = resp.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        raise GoogleAuthError("Could not reach Google. Check your connection.") from exc
    try:
        return json.loads(payload)
    except ValueError as exc:
        raise GoogleAuthError("Google returned an unexpected response.") from exc


def exchange_code(client_id, redirect_uri_value, verifier, code):
    """Swap the authorization code for an ``id_token`` (public client, PKCE)."""
    token = _post_form(
        TOKEN_ENDPOINT,
        {
            "code": code,
            "client_id": client_id,
            "redirect_uri": redirect_uri_value,
            "grant_type": "authorization_code",
            "code_verifier": verifier,
        },
    )
    if "id_token" not in token:
        raise GoogleAuthError("Google did not provide a sign-in token.")
    return token


def verify_id_token(id_token, client_id):
    """Ask Google to validate the id_token; return its verified claims once
    we re-check audience, issuer, and the verified email."""
    claims = _get_json(TOKENINFO_ENDPOINT + "?id_token=" + urllib.parse.quote(id_token))
    if claims.get("aud") != client_id:
        raise GoogleAuthError("The Google sign-in token was not issued to this app.")
    if claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
        raise GoogleAuthError("The Google sign-in token has an unknown issuer.")
    if not claims.get("email"):
        raise GoogleAuthError("Google did not provide an email address.")
    verified = str(claims.get("email_verified", "")).strip().lower()
    if claims.get("email_verified") is not True and verified != "true":
        raise GoogleAuthError("Your Google email is not verified.")
    return claims