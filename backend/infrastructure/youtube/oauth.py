"""Google OAuth for your own channels (plan 14): the installed-app flow with a
loopback redirect (http://127.0.0.1:<port>/...) and PKCE, read-only scopes.
Each user brings their own OAuth client (Google Cloud, type "Desktop app",
testing mode is fine for yourself) -- see backend/README.md "Your own
channels". Errors carry Google's error code, never a token.
"""
import base64
import hashlib
import secrets
from urllib.parse import urlencode

import requests

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
BASE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]
# plan 25: revenue is the most sensitive grant; a service asks for it only
# when the customer wants revenue and RPM (incremental authorization)
MONETARY_SCOPE = "https://www.googleapis.com/auth/yt-analytics-monetary.readonly"
SCOPES = BASE_SCOPES + [MONETARY_SCOPE]
TIMEOUT = 20


class OAuthError(RuntimeError):
    pass


def pkce_pair():
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    return verifier, challenge.decode()


def new_state() -> str:
    return secrets.token_urlsafe(24)


def auth_url(client_id: str, redirect_uri: str, state: str, challenge: str,
             scopes=None) -> str:
    return AUTH_URL + "?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": " ".join(scopes or SCOPES), "access_type": "offline", "prompt": "consent",
        "include_granted_scopes": "true", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256"})


def _post(data) -> dict:
    try:
        resp = requests.post(TOKEN_URL, data=data, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise OAuthError(f"cannot reach Google: {type(e).__name__}") from None
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code != 200:
        raise OAuthError(f"Google refused: {body.get('error', resp.status_code)}"
                         + (f" ({body['error_description']})" if body.get("error_description") else ""))
    return body


def exchange_code(client_id, client_secret, code, verifier, redirect_uri) -> dict:
    return _post({"client_id": client_id, "client_secret": client_secret, "code": code,
                  "code_verifier": verifier, "redirect_uri": redirect_uri,
                  "grant_type": "authorization_code"})


def refresh_access_token(client_id, client_secret, refresh_token) -> str:
    return _post({"client_id": client_id, "client_secret": client_secret,
                  "refresh_token": refresh_token, "grant_type": "refresh_token"})["access_token"]


def revoke(token: str) -> bool:
    """Best effort: a revoke that cannot reach Google is not fatal -- the
    local copy is deleted either way, and access can also be removed at
    https://myaccount.google.com/permissions."""
    try:
        return requests.post(REVOKE_URL, data={"token": token}, timeout=TIMEOUT).status_code == 200
    except requests.RequestException:
        return False
