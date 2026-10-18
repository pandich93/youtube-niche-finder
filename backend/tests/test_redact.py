"""Secrets never reach logs or error text (2026-10-18: a YouTube API key was
printed in the worker log inside an SSL error's URL). infrastructure.redact
masks key/token parameters, Google API keys and the configured secrets.
Fake values only. No database, no network.
Run with pytest, or directly: python3 tests/test_redact.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from infrastructure.redact import redact  # noqa: E402

FAKE_KEY = "AIza" + "F" * 35


def test_masks_query_parameters():
    text = ("GET /youtube/v3/channels?part=snippet&key=abc123&id=UC1 "
            "access_token=ya29.tok&refresh_token=1//ref client_secret=shh")
    out = redact(text)
    for secret in ("abc123", "ya29.tok", "1//ref", "shh"):
        assert secret not in out
    assert "part=snippet" in out and "id=UC1" in out
    assert "key=<redacted>" in out


def test_masks_google_api_keys_anywhere():
    assert FAKE_KEY not in redact(f"failed with {FAKE_KEY} in it")


def test_masks_configured_secrets(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "my-own-odd-key-value")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-fake")
    out = redact("x my-own-odd-key-value y sk-or-fake z")
    assert "my-own-odd-key-value" not in out and "sk-or-fake" not in out


def test_leaves_plain_text_and_handles_none():
    assert redact("rss watch: {'channels_checked': 7}") == "rss watch: {'channels_checked': 7}"
    assert redact(None) == ""


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
