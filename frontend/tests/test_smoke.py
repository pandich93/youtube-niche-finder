"""Смоук-тест дашборда: каждый экран открывается и рисуется без ошибок.

Поднимает бэкенд на синтетических данных (backend/tests/seed_demo.py) в своей
схеме Postgres, обходит все экраны в headless Chromium и проверяет: загрузка
закончилась, нет плашки «Не удалось загрузить данные», нет ошибок в консоли и
исключений на странице, ни один ответ /api/* не 5xx. С каждого экрана --
скриншот в frontend/tests/screenshots/.

Кнопки не нажимаются: POST тратит квоту YouTube и деньги LLM. Рабочие данные не
трогаются: своя схема nf_smoke_<hex>, после прогона удаляется.

    make frontend-test
"""
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
import warnings
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
SHOTS = Path(__file__).resolve().parent / "screenshots"

# 26 ключей ROUTES из router.js (до разбивки -- из app.js) + глубокие ссылки
# на объекты из seed_demo.py.
ROUTES = [
    "overview", "find", "viral", "channels", "categories", "keywords", "tags",
    "tracker", "alerts", "ideas", "language", "transcripts", "clusters", "titles", "saved", "packaging", "metadata", "calendar",
    "niches", "enter", "own", "data", "help", "mcp", "plan", "admin",
    "niche/demo", "channel/UC0000000000000000000a", "channel/UC0000000000000000000f", "brief/avid000",
    "brief/avid000?gap=how%20to%20start%20a%20tiny%20AI%20lab%3F",
    "compare/avid000,avid001",
]

LOCALES = ["ru", "en"]

# Кириллица, допустимая в английском интерфейсе: данные из seed_demo.py
# (название канала), а не текст интерфейса.
DATA_CYRILLIC = ["Нейросети по-русски"]

READY_JS = """() => {
  const v = document.querySelector('#view');
  return !!v && !v.querySelector('.skeleton-page') && v.textContent.trim().length > 0;
}"""


def _env(schema):
    # Пустой ключ перекрывает корневой .env (dotenv не перезаписывает заданные
    # переменные), LLM выключен: ни квоты, ни денег.
    return dict(os.environ, NICHE_DB_SCHEMA=schema, YOUTUBE_API_KEY="", LLM_PROVIDER="none")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _drop_schema(env, schema):
    code = ("import infrastructure.postgres as db\n"
            "c = db.get_conn()\n"
            f"c.execute('DROP SCHEMA IF EXISTS \"{schema}\" CASCADE')\n"
            "c.commit()\n"
            "c.close()\n")
    result = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env,
                             check=False, capture_output=True, text=True)
    if result.returncode != 0:
        # Не роняем иначе зелёный прогон -- но не молчим: без этого предупреждения
        # схема nf_smoke_* тихо остаётся в базе.
        warnings.warn(
            f"failed to drop schema {schema!r} (exit {result.returncode}):\n{result.stderr}",
            stacklevel=2)


def _wait_healthy(url, proc, log_path, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            pytest.fail(f"uvicorn exited early:\n{log_path.read_text()[-3000:]}")
        try:
            with urllib.request.urlopen(f"{url}/api/health", timeout=5) as r:
                if r.status == 200:
                    return
        except OSError:
            pass
        time.sleep(0.5)
    pytest.fail(f"/api/health not ready in {timeout}s:\n{log_path.read_text()[-3000:]}")


@pytest.fixture(scope="session")
def base_url(tmp_path_factory):
    schema = f"nf_smoke_{uuid.uuid4().hex[:8]}"
    env = _env(schema)
    proc = None
    try:
        subprocess.run([sys.executable, "tests/seed_demo.py"], cwd=BACKEND, env=env, check=True)
        port = _free_port()
        log_path = tmp_path_factory.mktemp("uvicorn") / "server.log"
        with open(log_path, "w") as log:
            proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "api:app", "--host", "127.0.0.1", "--port", str(port)],
                cwd=BACKEND, env=env, stdout=log, stderr=subprocess.STDOUT)
        url = f"http://127.0.0.1:{port}"
        _wait_healthy(url, proc, log_path)
        yield url
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        _drop_schema(env, schema)


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def _new_page(browser, locale="ru"):
    """Страница с выбранным языком интерфейса: nf.locale в localStorage ставится
    до загрузки скриптов. Проверки текста ниже написаны по-русски -- по умолчанию ru.
    Закрывать через page.context.close()."""
    ctx = browser.new_context(viewport={"width": 1400, "height": 900}, locale="en-US")
    ctx.add_init_script(f"localStorage.setItem('nf.locale', '{locale}')")
    return ctx.new_page()


def _ui_cyrillic(text):
    """Куски кириллицы, которые не объясняются данными из seed_demo.py."""
    # слова одной строки: аватар «Н» и название канала под ним -- разные куски
    pieces = re.findall(r"[А-Яа-яЁё][А-Яа-яЁё\-]*(?:[ \t]+[А-Яа-яЁё][А-Яа-яЁё\-]*)*", text)
    return sorted({p for p in pieces if not any(p in d for d in DATA_CYRILLIC)})


def _is_benign_console(text):
    # Ответ 4xx/5xx браузер сам пишет в консоль как "Failed to load resource".
    # 4xx для экранов -- штатно (например, 428 без ключа YouTube), а 5xx
    # ловится отдельно по ответам.
    return text.startswith("Failed to load resource")


def _router_js_route_keys():
    """Ключи объекта ROUTES из frontend/router.js (без глубоких ссылок)."""
    text = (ROOT / "frontend" / "router.js").read_text(encoding="utf-8")
    block = re.search(r"const\s+ROUTES\s*=\s*\{(.*?)\n\};", text, re.S)
    assert block, "router.js: не нашли блок `const ROUTES = { ... };`"
    return re.findall(r"^\s*([A-Za-z_$][\w$]*)\s*:\s*\{", block.group(1), re.M)


def test_routes_list_matches_router_js():
    # Экран, добавленный в router.js, но не в ROUTES здесь, тестировался бы
    # молча никогда -- эта проверка ловит рассинхронизацию без браузера/сервера.
    expected = {r for r in ROUTES if "/" not in r}
    actual = set(_router_js_route_keys())
    assert actual == expected, (
        f"ROUTES в test_smoke.py разошёлся с router.js: "
        f"отсутствуют в тесте {sorted(actual - expected)}, "
        f"лишние в тесте {sorted(expected - actual)}"
    )


@pytest.mark.parametrize("locale", LOCALES)
@pytest.mark.parametrize("route", ROUTES)
def test_screen_renders_without_errors(browser, base_url, route, locale):
    page = _new_page(browser, locale)
    problems = []
    page.on("console", lambda m: m.type == "error" and not _is_benign_console(m.text)
            and problems.append(f"console.error: {m.text}"))
    page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))
    page.on("response", lambda r: "/api/" in r.url and r.status >= 500
            and problems.append(f"HTTP {r.status}: {r.url}"))
    try:
        page.goto(f"{base_url}/#/{route}")
        page.wait_for_function(READY_JS, timeout=30000)
        page.wait_for_load_state("networkidle")
        SHOTS.mkdir(exist_ok=True)
        shot = re.sub(r"[^\w.-]+", "_", route)
        page.screenshot(path=str(SHOTS / f"{shot}.{locale}.png"), full_page=True)
        text = page.inner_text("#view")
        chrome = page.inner_text(".sidebar") + "\n" + page.inner_text(".topbar")
    finally:
        page.context.close()
    assert "Не удалось загрузить данные" not in text and "Could not load the data" not in text, text[:500]
    assert not problems, "\n".join(problems)
    if locale == "en":
        # кнопка языка законно показывает «Русский» -- остальное должно быть английским
        left = _ui_cyrillic(text + "\n" + chrome.replace("Русский", ""))
        assert not left, f"#/{route} (en): русский текст в интерфейсе: {left}"


def test_dictionaries_match_and_every_key_used_exists(browser, base_url):
    """У каждого неймспейса одинаковые ключи и {плейсхолдеры} в ru и en, и каждый
    t('ключ') в коде есть в словаре своего модуля."""
    i18n = ROOT / "frontend" / "i18n"
    names = sorted(p.stem for p in (i18n / "ru").glob("*.js"))
    page = _new_page(browser, "ru")
    try:
        page.goto(f"{base_url}/#/overview")
        dicts = page.evaluate("""async (names) => {
          const out = {};
          for (const ns of names) out[ns] = {
            ru: (await import(`/i18n/ru/${ns}.js`)).default,
            en: (await import(`/i18n/en/${ns}.js`)).default };
          return out;
        }""", names)
    finally:
        page.context.close()

    def flat(v):
        return " ".join(v.values()) if isinstance(v, dict) else str(v)

    errors = []
    for ns, d in dicts.items():
        for k in sorted(set(d["ru"]) ^ set(d["en"])):
            errors.append(f"{ns}.{k}: есть только в {'ru' if k in d['ru'] else 'en'}")
        for k in set(d["ru"]) & set(d["en"]):
            ph = [sorted(set(re.findall(r"\{(\w+)\}", flat(d[loc][k])))) for loc in ("ru", "en")]
            if ph[0] != ph[1]:
                errors.append(f"{ns}.{k}: плейсхолдеры ru {ph[0]} != en {ph[1]}")

    imp = re.compile(r"import\s+ru\s+from\s+'[./]*i18n/ru/(\w+)\.js'")
    for path in (ROOT / "frontend").rglob("*.js"):
        # i18n.js -- сама библиотека: в её комментарии пример импорта и t('title')
        if "i18n" in path.parts or "tests" in path.parts or path.name == "i18n.js":
            continue
        src = path.read_text(encoding="utf-8")
        m = imp.search(src)
        if not m:
            continue
        keys = dicts.get(m.group(1), {}).get("en", {})
        for key in set(re.findall(r"\bt\(\s*'(\w+)'", src)):
            if key not in keys:
                errors.append(f"{path.relative_to(ROOT).as_posix()}: t('{key}') нет в словаре {m.group(1)}")
    assert not errors, "\n".join(sorted(errors))


def test_language_switch_rerenders_without_reload(browser, base_url):
    page = _new_page(browser, "en")
    problems = []
    page.on("pageerror", lambda e: problems.append(str(e)))
    try:
        page.goto(f"{base_url}/#/overview")
        page.wait_for_function(READY_JS, timeout=30000)
        assert page.inner_text("#crumbSection") == "Overview"
        page.evaluate("window.__sameDocument = true")
        page.click("#langToggle")
        page.wait_for_function("() => document.querySelector('#crumbSection').textContent === 'Обзор'",
                               timeout=15000)
        state = page.evaluate("""() => ({ lang: document.documentElement.lang,
          same: window.__sameDocument === true, stored: localStorage.getItem('nf.locale'),
          nav: document.querySelector('#nav .nav-item .nav-label').textContent })""")
    finally:
        page.context.close()
    assert state == {"lang": "ru", "same": True, "stored": "ru", "nav": "Обзор"}, state
    assert not problems, problems


@pytest.mark.parametrize("route,expected", [
    # Карточка пробелов (план 03): без кэша комментариев -- кнопка «Прочитать
    # комментарии», квота сама не тратится.
    ("niche/demo", ["Пробелы в контенте", "Прочитать комментарии"]),
    # Стили превью (план 13): без векторов -- кнопка посчитать, квота не тратится.
    ("niche/demo", ["Стили превью", "Посчитать векторы превью"]),
    # Мои каналы (план 14): без OAuth-клиента -- пошаговая настройка, а не ошибка.
    ("own", ["Подключение не настроено", "OWN_TOKENS_KEY"]),
    ("own?error=access_denied", ["Подключить не удалось", "access_denied"]),
    # Сценарии MCP (план 11): раздел на экране подключения.
    ("mcp", ["Сценарии", "find_niche", "niche_health"]),
    # Тренд ниши (план 08): блок на экране ниши и колонка в списке ниш.
    ("niche/demo", ["Тренд ниши", "Видео за 30 дней"]),
    ("niches", ["Тренд"]),
    # Что общего у выстреливших (план 29): блок на экране ниши, на демо-данных -- «мало данных».
    ("niche/demo", ["Что общего у выстреливших", "Корреляция, не причина"]),
    # Другой язык (план 26): на демо-данных -- либо выбор пары, либо подсказка собрать второй язык.
    ("language", ["Другой язык"]),
    # Где заходить (план 27): рейтинг ниш с пояснением, что веса — суждение.
    ("enter", ["Где заходить", "Веса — наше суждение", "Сравнить выбранные"]),
    # Чистая прибыль (план 30): карточка канала и ниши, редактор профилей на «Данных».
    ("channel/UC0000000000000000000a", ["Чистая прибыль", "Профиля расходов нет"]),
    # Контент-календарь (план 33): неделя и черновики без даты.
    ("calendar", ["Календарь", "Без даты", "хранится в UTC"]),
    # Партнёры для коллабораций (план 31): блок на экране канала.
    ("channel/UC0000000000000000000a", ["Партнёры для коллабораций", "контактов YouTube не даёт"]),
    ("niche/demo", ["Чистая прибыль", "Окупается с"]),
    ("data", ["Профили расходов", "Профилей пока нет"]),
    # Бриф под вопрос зрителей: тема из ?gap= доходит до экрана.
    ("brief/avid000?gap=how%20to%20start%20a%20tiny%20AI%20lab%3F",
     ["Бриф под пробел", "how to start a tiny AI lab?"]),
])
def test_content_gaps_parts_are_on_screen(browser, base_url, route, expected):
    page = _new_page(browser)
    try:
        page.goto(f"{base_url}/#/{route}")
        page.wait_for_function(READY_JS, timeout=30000)
        page.wait_for_load_state("networkidle")
        text = page.inner_text("#view")
    finally:
        page.context.close()
    # Подписи плиток и заголовки таблиц CSS выводит заглавными, inner_text
    # отдаёт уже преобразованный текст -- сравниваем без регистра.
    for part in expected:
        assert part.lower() in text.lower(), f"{part!r} not on #/{route}: {text[:400]}"


def test_sign_in_screen_when_multi_user_mode_has_no_session(browser, base_url):
    # План 15: сервер с NF_MULTI_USER=1 без сессии -- дашборд показывает вход, а не
    # сыплет ошибками 401 по экранам. Ответ /api/auth/me подменяем.
    page = _new_page(browser)
    problems = []
    page.on("pageerror", lambda e: problems.append(str(e)))
    page.route("**/api/auth/me", lambda r: r.fulfill(
        status=200, content_type="application/json", body='{"multiUser": true, "user": null}'))
    try:
        page.goto(f"{base_url}/#/overview")
        page.wait_for_selector("#signInForm", timeout=30000)
        text = page.inner_text("#view")
    finally:
        page.context.close()
    assert "Вход в niche-finder" in text and not problems



def test_token_section_shows_only_with_sign_in(browser, base_url):
    # План 15 (5.7-5.8): без входа раздела токенов нет, с входом -- есть.
    page = _new_page(browser)
    page.route("**/api/auth/me", lambda r: r.fulfill(status=200, content_type="application/json",
        body='{"multiUser": true, "user": {"id": 2, "email": "a@x", "isAdmin": false}, "quota": null}'))
    page.route("**/api/auth/tokens", lambda r: r.fulfill(status=200, content_type="application/json",
        body='{"tokens": [{"id": 1, "name": "ext", "createdAt": "2026-09-29T00:00:00+00:00", "lastUsedAt": null}]}'))
    try:
        page.goto(f"{base_url}/#/mcp")
        page.wait_for_selector("#apiTokens .card", timeout=30000)
        text = page.inner_text("#apiTokens")
    finally:
        page.context.close()
    assert "Личные токены" in text and "ext" in text
