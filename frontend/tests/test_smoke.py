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
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
SHOTS = Path(__file__).resolve().parent / "screenshots"

# 18 ключей ROUTES из router.js (до разбивки -- из app.js) + две глубокие ссылки
# на объекты из seed_demo.py.
ROUTES = [
    "overview", "find", "viral", "channels", "categories", "keywords", "tags",
    "tracker", "ideas", "transcripts", "clusters", "titles", "saved", "metadata",
    "niches", "data", "help", "mcp",
    "niche/demo", "channel/UC0000000000000000000a",
]

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
    subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, check=False)


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
    subprocess.run([sys.executable, "tests/seed_demo.py"], cwd=BACKEND, env=env, check=True)
    port = _free_port()
    log_path = tmp_path_factory.mktemp("uvicorn") / "server.log"
    with open(log_path, "w") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "api:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=BACKEND, env=env, stdout=log, stderr=subprocess.STDOUT)
    url = f"http://127.0.0.1:{port}"
    try:
        _wait_healthy(url, proc, log_path)
        yield url
    finally:
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


def _is_benign_console(text):
    # Ответ 4xx/5xx браузер сам пишет в консоль как "Failed to load resource".
    # 4xx для экранов -- штатно (например, 428 без ключа YouTube), а 5xx
    # ловится отдельно по ответам.
    return text.startswith("Failed to load resource")


@pytest.mark.parametrize("route", ROUTES)
def test_screen_renders_without_errors(browser, base_url, route):
    page = browser.new_page(viewport={"width": 1400, "height": 900})
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
        page.screenshot(path=str(SHOTS / f"{route.replace('/', '_')}.png"), full_page=True)
        text = page.inner_text("#view")
    finally:
        page.close()
    assert "Не удалось загрузить данные" not in text, text[:500]
    assert not problems, "\n".join(problems)
