"""Язык интерфейса (план i18n): статические проверки без браузера и сервера.

Весь текст для человека живёт в словарях frontend/i18n/{ru,en}/<ns>.js; в коде
экранов и общих модулей кириллицы быть не должно -- иначе английский интерфейс
покажет русскую строку. Комментарии на русском разрешены: проверка снимает их
маленьким лексером (строки, шаблоны с ${...}, // и /* */).

    python -m pytest -q frontend/tests/test_i18n_static.py
"""
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1]
CYRILLIC = re.compile(r"[А-Яа-яЁё]")

# названия языков на самих этих языках -- кнопка переключения языка
ALLOWED = {("app.js", "Русский")}


def _code_only(src):
    """Исходник с вырезанными комментариями; строки и шаблоны остаются."""
    out, i, n = [], 0, len(src)
    stack = []          # для шаблонов: глубина фигурных скобок внутри ${...}
    while i < n:
        c, nxt = src[i], src[i + 1] if i + 1 < n else ""
        if stack and stack[-1] == "tpl":
            if c == "\\":
                out.append(src[i:i + 2]); i += 2; continue
            if c == "`":
                stack.pop(); out.append(c); i += 1; continue
            if c == "$" and nxt == "{":
                stack.append(0); out.append("${"); i += 2; continue
            out.append(c); i += 1; continue
        if c == "/" and nxt == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and nxt == "*":
            j = src.find("*/", i + 2)
            out.append("\n" * src.count("\n", i, n if j < 0 else j))
            i = n if j < 0 else j + 2
            continue
        if c in "'\"":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            out.append(src[i:j + 1]); i = j + 1; continue
        if c == "`":
            stack.append("tpl"); out.append(c); i += 1; continue
        if stack and c == "{":
            stack[-1] += 1
        if stack and c == "}":
            if stack[-1] == 0:
                stack.pop(); out.append(c); i += 1; continue
            stack[-1] -= 1
        out.append(c); i += 1
    return "".join(out)


def _ui_sources():
    for path in sorted(FRONTEND.rglob("*.js")):
        rel = path.relative_to(FRONTEND).as_posix()
        if rel.startswith(("i18n/ru/", "tests/")):
            continue
        yield rel, path.read_text(encoding="utf-8")


def test_lexer_drops_comments_and_keeps_strings():
    src = "const a = 'да'; // нет\n/* нет\n */ const b = `x ${f('да')} да`;\n"
    code = _code_only(src)
    assert "нет" not in code and code.count("да") == 3


def test_no_cyrillic_outside_the_russian_dictionaries():
    found = []
    for rel, src in _ui_sources():
        for lineno, line in enumerate(_code_only(src).splitlines(), 1):
            if not CYRILLIC.search(line):
                continue
            if any(rel == f and word in line for f, word in ALLOWED):
                continue
            found.append(f"{rel}:{lineno}: {line.strip()[:120]}")
    assert not found, "кириллица вне frontend/i18n/ru/:\n" + "\n".join(found)


def test_every_dictionary_has_a_pair():
    ru = {p.name for p in (FRONTEND / "i18n" / "ru").glob("*.js")}
    en = {p.name for p in (FRONTEND / "i18n" / "en").glob("*.js")}
    assert ru == en, f"только ru: {sorted(ru - en)}, только en: {sorted(en - ru)}"


def test_no_hard_coded_russian_locale_in_formatting():
    found = [f"{rel}: {m.group(0)}" for rel, src in _ui_sources()
             for m in re.finditer(r"['\"]ru-RU['\"]", _code_only(src))
             if rel != "i18n.js"]
    assert not found, "форматирование с жёстким 'ru-RU' -- используйте fmt* из i18n.js:\n" + "\n".join(found)
