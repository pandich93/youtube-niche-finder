"""Interface language (i18n plan): domain/i18n.py lookups and the HTTP layer
picking the reader's language. Same throwaway-schema setup as the other HTTP
tests -- no network, no YouTube key. Run with pytest, or directly:
python3 tests/test_i18n.py
"""
import ast
import os
import re
import string
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import interfaces.http.api as api  # noqa: E402
from domain import i18n  # noqa: E402

client = TestClient(api.app)
BACKEND = Path(HERE).parent


def setup_module(_=None):
    db.init_db()


# ------------------------------------------------------------ domain/i18n.py

def test_normalize_maps_regional_and_related_languages():
    assert i18n.normalize("en-US") == "en"
    assert i18n.normalize("RU") == "ru"
    assert i18n.normalize("uk-UA") == "ru"
    assert i18n.normalize("de") is None
    assert i18n.normalize(None) is None


def test_accept_language_picks_the_first_served_language_by_q():
    assert i18n.from_accept_language("de-DE,de;q=0.9,en;q=0.8,ru;q=0.7") == "en"
    assert i18n.from_accept_language("fr;q=0.9, ru;q=0.95") == "ru"
    assert i18n.from_accept_language("de, fr") is None
    assert i18n.from_accept_language("en;q=0") is None
    assert i18n.from_accept_language(None) is None


def test_russian_source_is_returned_as_is_and_english_comes_from_the_catalog():
    with i18n.use_locale("ru"):
        assert i18n.tr("нужно поле channel_id") == "нужно поле channel_id"
    with i18n.use_locale("en"):
        assert i18n.tr("нужно поле channel_id") == "the channel_id field is required"
        # no translation yet: the source comes back rather than nothing
        assert i18n.tr("строка без перевода") == "строка без перевода"


def test_params_are_substituted_after_the_lookup(monkeypatch):
    monkeypatch.setitem(i18n._catalogs, "en", {"отсёк {n} видео": "dropped {n} videos"})
    with i18n.use_locale("en"):
        assert i18n.tr("отсёк {n} видео", n=5) == "dropped 5 videos"
    with i18n.use_locale("ru"):
        assert i18n.tr("отсёк {n} видео", n=5) == "отсёк 5 видео"


def test_default_locale_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv("NF_DEFAULT_LOCALE", raising=False)
    assert i18n.current_locale() == "ru"
    monkeypatch.setenv("NF_DEFAULT_LOCALE", "en")
    assert i18n.current_locale() == "en"
    monkeypatch.setenv("NF_DEFAULT_LOCALE", "xx")
    assert i18n.current_locale() == "ru"


def _fields(text):
    return sorted(f for _lit, f, _spec, _conv in string.Formatter().parse(text) if f)


def test_every_catalog_entry_keeps_the_sources_placeholders():
    """A translation that drops or renames {param} would raise KeyError, or
    silently lose a number, at the moment a person reads it."""
    for locale in i18n.SUPPORTED:
        if locale == i18n.SOURCE_LOCALE:
            continue
        for src, text in i18n._catalog(locale).items():
            assert _fields(src) == _fields(text), (locale, src, text)


def _tr_sources():
    """Every literal string passed to tr(...) in the backend."""
    found = set()
    for path in BACKEND.rglob("*.py"):
        if "tests" in path.parts or "locales" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "tr"
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                found.add(node.args[0].value)
    return found


def test_every_russian_tr_source_has_an_english_translation():
    cyrillic = re.compile(r"[А-Яа-яЁё]")
    missing = sorted(s for s in _tr_sources()
                     if cyrillic.search(s) and s not in i18n._catalog("en"))
    assert not missing, missing


# ------------------------------------------------------------------ HTTP

def test_error_detail_follows_the_x_nf_locale_header():
    r = client.post("/api/channels/track", json={"channel_id": ""}, headers={"x-nf-locale": "en"})
    assert r.status_code == 400 and r.json()["detail"] == "the channel_id field is required"
    r = client.post("/api/channels/track", json={"channel_id": ""}, headers={"x-nf-locale": "ru"})
    assert r.json()["detail"] == "нужно поле channel_id"


def test_without_a_header_the_answer_stays_russian_by_default():
    r = client.post("/api/channels/track", json={"channel_id": ""})
    assert r.json()["detail"] == "нужно поле channel_id"


def test_accept_language_is_used_when_no_header_and_nothing_saved():
    r = client.post("/api/channels/track", json={"channel_id": ""},
                    headers={"accept-language": "en-GB,en;q=0.9"})
    assert r.json()["detail"] == "the channel_id field is required"


def test_saved_locale_is_returned_by_me_and_wins_over_accept_language():
    try:
        r = client.put("/api/me/locale", json={"locale": "en"})
        assert r.status_code == 200 and r.json() == {"locale": "en"}
        assert client.get("/api/auth/me").json()["locale"] == "en"
        r = client.post("/api/channels/track", json={"channel_id": ""},
                        headers={"accept-language": "ru"})
        assert r.json()["detail"] == "the channel_id field is required"
    finally:
        client.put("/api/me/locale", json={"locale": "ru"})


def test_unknown_locale_is_refused():
    r = client.put("/api/me/locale", json={"locale": "xx"})
    assert r.status_code == 400


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
