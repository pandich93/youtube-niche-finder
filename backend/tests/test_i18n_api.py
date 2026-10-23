"""Interface language (i18n plan): error details and hints of the HTTP API and
of application/* come back in English for X-NF-Locale: en, and stay Russian
without a header. Throwaway schema, no network, no YouTube key needed.
Run with pytest, or directly: python3 tests/test_i18n_api.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import interfaces.http.api as api  # noqa: E402

client = TestClient(api.app)
EN = {"x-nf-locale": "en"}


def setup_module(_=None):
    db.init_db()


def test_missing_youtube_key_is_explained_in_the_readers_language(monkeypatch):
    monkeypatch.setattr(api, "API_KEY", "")
    r = client.post("/api/collect/channel", json={"channel": "@someone"}, headers=EN)
    assert r.status_code == 428
    assert r.json()["detail"] == ("YOUTUBE_API_KEY is not set. Add it to .env next to "
                                  "docker-compose.yml and restart: docker compose up -d web")
    r = client.post("/api/collect/channel", json={"channel": "@someone"})
    assert r.status_code == 428 and r.json()["detail"].startswith("YOUTUBE_API_KEY не задан.")


def test_bad_input_details_are_english(monkeypatch):
    monkeypatch.setattr(api, "API_KEY", "test-key-not-used")
    r = client.post("/api/collect/channel", json={"channel": "  "}, headers=EN)
    assert r.status_code == 400 and r.json()["detail"] == "the channel field is required"
    r = client.post("/api/inspect/videos", json={"ids": "abc"}, headers=EN)
    assert r.status_code == 400 and r.json()["detail"] == "ids must be a list"
    r = client.post("/api/inspect/videos", json={"ids": "abc"})
    assert r.json()["detail"] == "ids должен быть списком"


def test_application_hints_follow_the_header(monkeypatch):
    # empty schema: the window holds no videos at all
    r = client.get("/api/categories", params={"period": "7d"}, headers=EN)
    assert r.status_code == 200
    assert r.json()["hint"] == ("There are no videos at all in the 7d window by 'published' — "
                                "collect some data first (collect_channel / collect_niche).")
    r = client.get("/api/categories", params={"period": "7d"})
    assert r.json()["hint"].startswith("В окне 7d по 'published' нет видео вообще")

    monkeypatch.setattr(api, "API_KEY", "")
    r = client.get("/api/inspect/video", params={"video_id": "dQw4w9WgXcQ"}, headers=EN)
    assert r.json()["found"] is False
    assert r.json()["hint"] == ("this video is not in the local database, and YOUTUBE_API_KEY "
                                "is not set — there is nowhere to fetch it from")


def test_guard_outside_the_locale_middleware_still_answers_in_english():
    """local_only_guard wraps locale_scope, so it reads the header itself."""
    r = client.post("/api/collect/niche", content="x",
                    headers={"content-type": "text/plain", **EN})
    assert r.status_code == 403
    assert r.json()["detail"].startswith("A modifying request without Content-Type")
    r = client.post("/api/collect/niche", content="x", headers={"content-type": "text/plain"})
    assert r.status_code == 403 and r.json()["detail"].startswith("Изменяющий запрос")


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
