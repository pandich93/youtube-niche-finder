"""Tests for the plan-14 infrastructure: token encryption at rest
(infrastructure/secrets.py), the OAuth loopback flow with PKCE
(infrastructure/youtube/oauth.py) and the Analytics API client
(infrastructure/youtube/analytics.py) against recorded responses. Every HTTP
call is monkeypatched -- no network, no Google account.
Run with pytest, or directly: python3 tests/test_own_channels_infra.py
"""
import base64
import hashlib
import json
import os
import sys
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import pytest  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402

import infrastructure.secrets as SEC  # noqa: E402
from infrastructure.youtube import analytics as YA  # noqa: E402
from infrastructure.youtube import oauth as OA  # noqa: E402

SECRET_TOKEN = "1//refresh-token-that-must-never-leak"


class Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("OWN_TOKENS_KEY", Fernet.generate_key().decode())


# ------------------------------------------------------------ secrets

def test_encrypt_round_trip_and_ciphertext_hides_the_token(key):
    blob = SEC.encrypt(SECRET_TOKEN)
    assert SECRET_TOKEN.encode() not in blob
    assert SEC.decrypt(blob) == SECRET_TOKEN


def test_without_a_key_nothing_is_stored(monkeypatch):
    monkeypatch.delenv("OWN_TOKENS_KEY", raising=False)
    assert SEC.configured() is False
    with pytest.raises(SEC.SecretsNotConfigured):
        SEC.encrypt(SECRET_TOKEN)


def test_a_malformed_key_is_reported_as_not_configured(monkeypatch):
    monkeypatch.setenv("OWN_TOKENS_KEY", "not-a-fernet-key")
    assert SEC.configured() is False


def test_a_different_key_cannot_read_the_token(key, monkeypatch):
    blob = SEC.encrypt(SECRET_TOKEN)
    monkeypatch.setenv("OWN_TOKENS_KEY", Fernet.generate_key().decode())
    with pytest.raises(SEC.SecretsNotConfigured):
        SEC.decrypt(blob)


# ------------------------------------------------------------ oauth

def test_pkce_challenge_is_the_s256_of_the_verifier():
    verifier, challenge = OA.pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    assert challenge == expected.decode() and 43 <= len(verifier) <= 128


def test_auth_url_asks_for_read_only_scopes_offline_with_pkce():
    url = OA.auth_url("cid.apps.googleusercontent.com", "http://127.0.0.1:8080/cb", "st4te", "ch4l")
    q = parse_qs(urlsplit(url).query)
    assert q["client_id"] == ["cid.apps.googleusercontent.com"]
    assert q["redirect_uri"] == ["http://127.0.0.1:8080/cb"]
    assert q["state"] == ["st4te"] and q["code_challenge"] == ["ch4l"]
    assert q["code_challenge_method"] == ["S256"] and q["access_type"] == ["offline"]
    scopes = q["scope"][0].split()
    assert all(s.endswith(".readonly") for s in scopes)
    assert "https://www.googleapis.com/auth/yt-analytics-monetary.readonly" in scopes


def test_exchange_code_returns_tokens(monkeypatch):
    sent = {}

    def post(url, data=None, timeout=None):
        sent.update(data)
        return Resp(200, {"access_token": "at", "refresh_token": SECRET_TOKEN, "expires_in": 3599,
                          "scope": " ".join(OA.SCOPES)})
    monkeypatch.setattr(OA.requests, "post", post)
    out = OA.exchange_code("cid", "csecret", "c0de", "verif", "http://127.0.0.1:8080/cb")
    assert out["refresh_token"] == SECRET_TOKEN and sent["code_verifier"] == "verif"
    assert sent["grant_type"] == "authorization_code"


def test_oauth_errors_never_carry_tokens(monkeypatch):
    monkeypatch.setattr(OA.requests, "post", lambda *a, **k: Resp(
        400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}))
    with pytest.raises(OA.OAuthError) as e:
        OA.refresh_access_token("cid", "csecret", SECRET_TOKEN)
    assert "invalid_grant" in str(e.value) and SECRET_TOKEN not in str(e.value)


def test_revoke_is_best_effort(monkeypatch):
    def boom(*a, **k):
        raise OA.requests.RequestException("offline")
    monkeypatch.setattr(OA.requests, "post", boom)
    assert OA.revoke(SECRET_TOKEN) is False


# ------------------------------------------------------------ analytics

def test_report_turns_the_result_table_into_dicts(monkeypatch):
    with open(os.path.join(HERE, "fixtures", "analytics_video_report.json")) as f:
        payload = json.load(f)
    seen = {}

    def get(url, params=None, headers=None, timeout=None):
        seen.update(params=params, headers=headers)
        return Resp(200, payload)
    monkeypatch.setattr(YA.requests, "get", get)
    rows = YA.report("at", "UCown", "2026-09-01", "2026-09-27", ["views", "estimatedRevenue"],
                     dimensions="video", sort="-views", max_results=200)
    assert rows[0]["video"] == "ownvid00001" and rows[0]["views"] == 12000
    assert rows[1]["averageViewPercentage"] == 33.0
    assert seen["params"]["ids"] == "channel==UCown" and seen["params"]["dimensions"] == "video"
    assert seen["headers"]["Authorization"] == "Bearer at"


def test_report_errors_carry_the_reason(monkeypatch):
    monkeypatch.setattr(YA.requests, "get", lambda *a, **k: Resp(403, {"error": {
        "code": 403, "message": "Forbidden", "errors": [{"reason": "forbidden"}]}}))
    with pytest.raises(YA.AnalyticsError) as e:
        YA.report("at", "UCown", "2026-09-01", "2026-09-27", ["estimatedRevenue"])
    assert e.value.reason == "forbidden" and "at" not in e.value.args[0].split()


def test_quota_exceeded_is_its_own_error(monkeypatch):
    monkeypatch.setattr(YA.requests, "get", lambda *a, **k: Resp(403, {"error": {
        "code": 403, "message": "Quota exceeded", "errors": [{"reason": "quotaExceeded"}]}}))
    with pytest.raises(YA.QuotaExceeded):
        YA.report("at", "UCown", "2026-09-01", "2026-09-27", ["views"])


def test_mine_channel_reads_id_and_title(monkeypatch):
    monkeypatch.setattr(YA.requests, "get", lambda *a, **k: Resp(200, {"items": [
        {"id": "UCown", "snippet": {"title": "My Channel", "publishedAt": "2024-01-01T00:00:00Z"}}]}))
    assert YA.mine_channel("at") == {"channelId": "UCown", "title": "My Channel",
                                     "publishedAt": "2024-01-01T00:00:00Z"}


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
