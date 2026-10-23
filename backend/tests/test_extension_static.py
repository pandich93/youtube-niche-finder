"""Статическая проверка расширения для Chrome (extension/): без браузера.

  * каждый ключ t('…') / tn('…') из *.js и data-i18n* из popup.html есть в обоих
    _locales (для tn -- форма _other);
  * у en и ru один набор ключей с точностью до форм множественного числа;
  * доступ к хостам (P1-16): обязательный -- только 127.0.0.1 / localhost,
    к домену сервиса -- лишь optional_host_permissions, по запросу в попапе.

Запуск через pytest или напрямую: python3 tests/test_extension_static.py
"""
import json
import re
import sys
from pathlib import Path

import pytest

EXT = Path(__file__).resolve().parents[2] / "extension"
_PLURAL = re.compile(r"_(zero|one|two|few|many|other)$")


def _messages(lang):
    return json.loads((EXT / "_locales" / lang / "messages.json").read_text(encoding="utf-8"))


def _used_keys():
    keys, plurals = set(), set()
    for js in EXT.glob("*.js"):
        text = js.read_text(encoding="utf-8")
        keys |= set(re.findall(r"\bt\(\s*'([A-Za-z0-9_]+)'", text))
        plurals |= set(re.findall(r"\btn\(\s*'([A-Za-z0-9_]+)'", text))
    html = (EXT / "popup.html").read_text(encoding="utf-8")
    keys |= set(re.findall(r'data-i18n(?:-title|-placeholder)?="([A-Za-z0-9_]+)"', html))
    return keys, plurals


@pytest.mark.parametrize("lang", ["en", "ru"])
def test_every_used_key_exists(lang):
    messages = _messages(lang)
    keys, plurals = _used_keys()
    assert sorted(k for k in keys if k not in messages) == []
    assert sorted(k for k in plurals if k + "_other" not in messages) == []


def test_locales_have_the_same_keys_up_to_plural_forms():
    base = {lang: {_PLURAL.sub("", k) for k in _messages(lang)} for lang in ("en", "ru")}
    assert base["en"] == base["ru"]


def test_host_access_is_local_unless_granted():
    manifest = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
    assert sorted(manifest["host_permissions"]) == ["http://127.0.0.1/*", "http://localhost/*"]
    assert "optional_host_permissions" in manifest
    assert len(manifest["description"]) <= 132
    assert len(_messages("en")["extDescription"]["message"]) <= 132
    assert len(_messages("ru")["extDescription"]["message"]) <= 132


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
