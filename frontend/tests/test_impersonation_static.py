"""Просмотр под пользователем (многопользовательский план P2-05): баннер есть
на каждом экране и переведён целиком. Статически, без браузера и сервера.

    python -m pytest -q frontend/tests/test_impersonation_static.py
"""
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1]


def _keys(path):
    return set(re.findall(r"^\s+(\w+):", path.read_text(encoding="utf-8"), re.M))


def test_the_banner_lives_in_the_page_shell_not_in_a_screen():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert 'id="impersonationBanner"' in html
    app = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert "from './impersonation.js'" in app and "setView(me)" in app


def test_every_banner_key_is_whole_and_in_both_dictionaries():
    src = (FRONTEND / "impersonation.js").read_text(encoding="utf-8")
    used = set(re.findall(r"\bt\('(\w+)'", src))
    assert used, "impersonation.js should translate its texts"
    # ключ целиком, без склейки: t(`x${y}`) или t('a' + b) проверка словарей не видит
    assert not re.search(r"\bt\(\s*(`|'[^']*'\s*\+)", src)
    for lang in ("ru", "en"):
        missing = used - _keys(FRONTEND / "i18n" / lang / "impersonation.js")
        assert not missing, f"{lang}: {sorted(missing)}"


def test_the_admin_screen_offers_the_view_and_labels_its_log_events():
    src = (FRONTEND / "screens" / "admin.js").read_text(encoding="utf-8")
    assert "startView(" in src
    used = set(re.findall(r"\bt\('(\w+)'", src))
    for key in ("viewAs", "viewAsTitle", "auditGroupSupport", "evImpersonationStart",
                "evImpersonationEnd", "evImpersonationExpired"):
        assert key in used
        for lang in ("ru", "en"):
            assert key in _keys(FRONTEND / "i18n" / lang / "admin.js"), (lang, key)
