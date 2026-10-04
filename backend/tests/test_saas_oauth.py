"""Tests for plan 25: running niche-finder as a service where customers
connect their own channels with ONE OAuth client of the service
(OWN_OAUTH_MODE=web), revenue access asked for separately, and public
/privacy and /terms pages for Google's app verification. Every Google call
is monkeypatched; throwaway schema; no network.
Run with pytest, or directly: python3 tests/test_saas_oauth.py
"""
import os
import sys
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import interfaces.http.api as api  # noqa: E402
from application import auth as AUTH  # noqa: E402
from application import own_channels as OWN  # noqa: E402
from infrastructure.youtube import analytics as YA  # noqa: E402
from infrastructure.youtube import oauth as OA  # noqa: E402

PW = "correct horse battery"
CH = "UC" + "saasoauth".ljust(22, "0")
HTTPS = "https://nf.example.com/api/own/oauth/callback"


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    conn = db.get_conn()
    for t in ("own_channel_daily", "own_video_metrics", "own_channels", "own_oauth_pending", "sessions"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.commit()
    conn.close()
    monkeypatch.setenv("OWN_TOKENS_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("OWN_OAUTH_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("OWN_OAUTH_CLIENT_SECRET", "csecret")
    monkeypatch.delenv("OWN_OAUTH_MODE", raising=False)
    monkeypatch.delenv("OWN_OAUTH_REDIRECT_URI", raising=False)
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 0)


def _scopes(url):
    return set(parse_qs(urlsplit(url).query)["scope"][0].split())


# ------------------------------------------------------------ scopes

def test_desktop_mode_keeps_asking_for_revenue_by_default():
    url = OWN.start_connect()["authUrl"]
    assert OA.MONETARY_SCOPE in _scopes(url)


def test_web_mode_asks_for_revenue_only_when_wanted(monkeypatch):
    monkeypatch.setenv("OWN_OAUTH_MODE", "web")
    monkeypatch.setenv("OWN_OAUTH_REDIRECT_URI", HTTPS)
    base = _scopes(OWN.start_connect()["authUrl"])
    assert base == set(OA.BASE_SCOPES) and OA.MONETARY_SCOPE not in base
    assert OA.MONETARY_SCOPE in _scopes(OWN.start_connect(include_revenue=True)["authUrl"])


def test_revenue_access_is_reported_per_channel(monkeypatch):
    monkeypatch.setattr(OA, "exchange_code", lambda *a, **k: {
        "access_token": "at", "refresh_token": "r", "scope": " ".join(OA.BASE_SCOPES)})
    monkeypatch.setattr(OA, "refresh_access_token", lambda *a, **k: "at")
    monkeypatch.setattr(YA, "mine_channel", lambda at: {"channelId": CH, "title": "Mine",
                                                        "publishedAt": "2024-01-01T00:00:00Z"})
    monkeypatch.setattr(YA, "report", lambda *a, **k: [])
    url = OWN.start_connect()["authUrl"]
    OWN.finish_connect(parse_qs(urlsplit(url).query)["state"][0], "code")
    assert OWN.list_channels()["channels"][0]["monetaryScope"] is False


# ------------------------------------------------------------ web mode config

def test_web_mode_needs_an_https_redirect(monkeypatch):
    monkeypatch.setenv("OWN_OAUTH_MODE", "web")
    st = OWN.status()
    assert st["mode"] == "web" and st["configured"] is False
    assert any("OWN_OAUTH_REDIRECT_URI" in p for p in st["problems"])
    monkeypatch.setenv("OWN_OAUTH_REDIRECT_URI", "http://nf.example.com/api/own/oauth/callback")
    assert OWN.status()["configured"] is False
    monkeypatch.setenv("OWN_OAUTH_REDIRECT_URI", HTTPS)
    st = OWN.status()
    assert st["configured"] is True and st["problems"] == [] and st["redirectUri"] == HTTPS


def test_customers_do_not_see_the_setup_details(monkeypatch):
    monkeypatch.setenv("NF_MULTI_USER", "1")
    monkeypatch.setenv("OWN_OAUTH_MODE", "web")
    monkeypatch.delenv("OWN_OAUTH_CLIENT_SECRET")
    AUTH.create_user("admin@example.com", PW, is_admin=True)
    AUTH.create_user("customer@example.com", PW)
    out = {}
    for email in ("admin@example.com", "customer@example.com"):
        c = TestClient(api.app, headers={"X-NF-Client": "tests"})
        assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
        out[email] = c.get("/api/own/status").json()
    assert "OWN_OAUTH_CLIENT_SECRET" in out["admin@example.com"]["missing"]
    cust = out["customer@example.com"]
    assert cust["configured"] is False and cust["adminOnly"] is True
    assert "missing" not in cust and "problems" not in cust


def test_the_connect_route_passes_the_revenue_choice(monkeypatch):
    monkeypatch.setenv("OWN_OAUTH_MODE", "web")
    monkeypatch.setenv("OWN_OAUTH_REDIRECT_URI", HTTPS)
    c = TestClient(api.app, headers={"X-NF-Client": "tests"})
    plain = c.post("/api/own/connect", json={}).json()
    paid = c.post("/api/own/connect", json={"includeRevenue": True}).json()
    assert OA.MONETARY_SCOPE not in _scopes(plain["authUrl"])
    assert OA.MONETARY_SCOPE in _scopes(paid["authUrl"])
    assert plain["redirectUri"] == HTTPS


# ------------------------------------------------------------ public pages

def test_privacy_and_terms_are_public_and_name_the_operator(monkeypatch):
    monkeypatch.setenv("NF_MULTI_USER", "1")
    monkeypatch.setenv("NF_SERVICE_NAME", "Niche Lab")
    monkeypatch.setenv("NF_CONTACT_EMAIL", "privacy@example.com")
    c = TestClient(api.app)
    p = c.get("/privacy")
    assert p.status_code == 200 and "text/html" in p.headers["content-type"]
    assert "Niche Lab" in p.text and "privacy@example.com" in p.text
    assert "Limited Use" in p.text and "https://policies.google.com/privacy" in p.text
    assert "https://www.youtube.com/t/terms" in p.text
    assert "https://myaccount.google.com/permissions" in p.text
    t = c.get("/terms")
    assert t.status_code == 200 and "https://www.youtube.com/t/terms" in t.text
    monkeypatch.setenv("NF_SERVICE_NAME", "<script>x</script>")
    assert "<script>x</script>" not in c.get("/privacy").text


def test_a_wrong_redirect_blocks_connecting_but_not_the_daily_sync(monkeypatch):
    monkeypatch.setenv("OWN_OAUTH_MODE", "web")
    monkeypatch.setenv("OWN_OAUTH_REDIRECT_URI", "http://nf.example.com/api/own/oauth/callback")
    with pytest.raises(OWN.NotConfigured):
        OWN.start_connect()
    assert OWN.sync()["channels"] == []          # no channels, but no NotConfigured either
