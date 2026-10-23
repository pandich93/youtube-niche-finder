"""Экраны «Админка» и «Тариф» в многопользовательском режиме (P1-17).

Свой сервер с NF_MULTI_USER=1 в своей схеме Postgres nf_mu_<hex> (после
прогона удаляется): администратор, обычные пользователи и тарифы создаются
напрямую через application-слой, вход -- через POST /api/auth/login в том же
контексте браузера, что и страница (cookie сессии общая).

Проверяется смысл, а не вёрстка: кто что видит, назначение и выбор тарифа
доходят до API, лимиты и расход на экране совпадают с /api/plan, тексты
экранов есть на обоих языках. YouTube и LLM выключены: ни квоты, ни денег.

Файл независим от test_smoke.py: свои фикстуры с областью module, чтобы
браузер и сервер закрылись до того, как смоук-тест поднимет свои.

    make frontend-test
"""
import json
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

PW = "correct horse battery staple"
ADMIN, USER, OTHER, CHOOSER = ("admin@example.com", "user@example.com", "other@example.com",
                               "chooser@example.com")
USED_UNITS = 123          # расход YouTube у USER сегодня, пишется прямо в meta
STARTER_UNITS, STARTER_DRAFTS, TEAM_UNITS = 500, 3, 900

# Кириллица в английском интерфейсе, которая объясняется данными, а не
# текстами экранов: описание встроенного базового тарифа из schema.py.
DATA_CYRILLIC = ["Базовый тариф"]

READY_JS = """() => {
  const v = document.querySelector('#view');
  return !!v && !v.querySelector('.skeleton-page') && v.textContent.trim().length > 0;
}"""

# Пользователи и тарифы: Starter -- бесплатный публичный, Pro -- платный
# публичный (выбрать нельзя, пока нет оплаты), Team -- скрытый, его назначает
# только администратор. USER уже на Starter, CHOOSER -- на базовом Free.
SETUP = r"""
import json, sys
import infrastructure.postgres as db
import infrastructure.youtube.client as yt
from application import auth as AUTH
from application import plans as PL

pw, admin, *others = sys.argv[1:]
db.init_db()
ids = {admin: AUTH.create_user(admin, pw, is_admin=True)}
ids.update({e: AUTH.create_user(e, pw) for e in others})
starter = PL.create_plan({"name": "Starter", "slug": "starter", "description": "Free starter plan",
                          "price": 0, "sortOrder": 1, "isPublic": True,
                          "limits": {"yt_units": %(starter_units)d, "drafts": %(starter_drafts)d},
                          "features": {"export": False}})
pro = PL.create_plan({"name": "Pro", "slug": "pro", "description": "Paid plan", "price": 9,
                      "sortOrder": 2, "isPublic": True, "limits": {"yt_units": 5000}})
team = PL.create_plan({"name": "Team", "slug": "team", "description": "Hidden plan", "price": 0,
                       "sortOrder": 3, "isPublic": False, "limits": {"yt_units": %(team_units)d}})
PL.assign(ids[admin], ids["%(user)s"], starter["id"], None)
conn = db.get_conn()
db.set_meta(conn, yt.user_units_key(ids["%(user)s"]), %(used)d)
conn.commit()
conn.close()
print(json.dumps({"users": ids, "plans": {"starter": starter["id"], "pro": pro["id"],
                                          "team": team["id"]}}))
""" % {"starter_units": STARTER_UNITS, "starter_drafts": STARTER_DRAFTS, "team_units": TEAM_UNITS,
       "user": USER, "used": USED_UNITS}


def _env(schema):
    # Пустые ключи перекрывают корневой .env (dotenv не перезаписывает заданные
    # переменные): ни YouTube, ни LLM, cookie без Secure на http://127.0.0.1.
    return dict(os.environ, NICHE_DB_SCHEMA=schema, YOUTUBE_API_KEY="", LLM_PROVIDER="none",
                NF_MULTI_USER="1", NF_COOKIE_SECURE="0", RATE_LIMIT_PER_MINUTE="0")


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
        warnings.warn(f"failed to drop schema {schema!r} (exit {result.returncode}):\n"
                      f"{result.stderr}", stacklevel=2)


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


@pytest.fixture(scope="module")
def mu(tmp_path_factory):
    """Сервер с NF_MULTI_USER=1: {"url", "users": {email: id}, "plans": {slug: id}}."""
    schema = f"nf_mu_{uuid.uuid4().hex[:8]}"
    env = _env(schema)
    proc = None
    try:
        out = subprocess.run([sys.executable, "-c", SETUP, PW, ADMIN, USER, OTHER, CHOOSER],
                             cwd=BACKEND, env=env, check=True, capture_output=True, text=True)
        made = json.loads(out.stdout.strip().splitlines()[-1])
        port = _free_port()
        log_path = tmp_path_factory.mktemp("uvicorn-mu") / "server.log"
        with open(log_path, "w") as log:
            proc = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "api:app", "--host", "127.0.0.1", "--port", str(port)],
                cwd=BACKEND, env=env, stdout=log, stderr=subprocess.STDOUT)
        url = f"http://127.0.0.1:{port}"
        _wait_healthy(url, proc, log_path)
        yield {"url": url, **made}
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        _drop_schema(env, schema)


@pytest.fixture(scope="module")
def pw_browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


class Session:
    """Вошедший пользователь: страница и запросы к API с той же cookie."""

    def __init__(self, browser, mu, email, locale="ru"):
        self.url = mu["url"]
        self.locale = locale
        self.ctx = browser.new_context(viewport={"width": 1400, "height": 900}, locale="en-US")
        self.ctx.add_init_script(f"localStorage.setItem('nf.locale', '{locale}')")
        r = self.ctx.request.post(f"{self.url}/api/auth/login",
                                  data={"email": email, "password": PW})
        assert r.ok, f"login {email}: {r.status} {r.text()}"
        self.page = self.ctx.new_page()
        self.problems = []
        self.page.on("pageerror", lambda e: self.problems.append(f"pageerror: {e}"))
        self.page.on("response", lambda r: "/api/" in r.url and r.status >= 500
                     and self.problems.append(f"HTTP {r.status}: {r.url}"))

    def api(self, method, path, body=None):
        r = self.ctx.request.fetch(f"{self.url}{path}", method=method, data=body,
                                   headers={"x-nf-locale": self.locale})
        return r.status, (r.json() if r.body() else None)

    def open(self, route):
        self.page.goto(f"{self.url}/#/{route}")
        self.page.wait_for_function(READY_JS, timeout=30000)
        self.page.wait_for_load_state("networkidle")
        return self.text()

    def text(self):
        return self.page.inner_text("#view")

    def wait_text(self, part):
        # после действия экран перерисовывается сам (render()) -- ждём нужный текст
        self.page.wait_for_function(
            "(s) => document.querySelector('#view').innerText.toLowerCase().includes(s)",
            arg=part.lower(), timeout=15000)

    def card(self, name):
        """Карточка тарифа на экране «Тариф» по его названию."""
        title = self.page.locator(".section-title", has_text=re.compile(rf"^{re.escape(name)}$"))
        return self.page.locator("#view .card", has=title)

    def close(self):
        self.ctx.close()
        assert not self.problems, "\n".join(self.problems)


@pytest.fixture
def login(pw_browser, mu):
    opened = []

    def _login(email, locale="ru"):
        s = Session(pw_browser, mu, email, locale)
        opened.append(s)
        return s

    yield _login
    for s in opened:
        s.close()


def _has(text, *parts):
    # подписи и заголовки таблиц CSS выводит заглавными -- сравниваем без регистра
    for part in parts:
        assert part.lower() in text.lower(), f"{part!r} not on screen: {text[:600]}"


def _hasnt(text, *parts):
    for part in parts:
        assert part.lower() not in text.lower(), f"{part!r} must not be on screen: {text[:600]}"


def _user(admin, email):
    status, data = admin.api("GET", "/api/admin/users")
    assert status == 200, data
    return next(u for u in data["users"] if u["email"] == email)


# ------------------------------------------------------------------ Админка

def test_admin_sees_users_and_plans(login, mu):
    admin = login(ADMIN)
    text = admin.open("admin")
    _has(text, "Тарифы", "Пользователи", ADMIN, USER, OTHER, CHOOSER,
         "Free", "Starter", "Pro", "Team")
    assert admin.page.is_visible('a.nav-item[href="#/admin"]')
    # у каждого обычного пользователя -- его текущий тариф, как в /api/admin/users
    for email in (USER, OTHER, CHOOSER):
        u = _user(admin, email)
        shown = admin.page.eval_on_selector(f'.uplan[data-id="{u["id"]}"]',
                                            "e => e.selectedOptions[0].textContent")
        assert shown == u["plan"]["name"], (email, shown, u["plan"])
    assert _user(admin, USER)["plan"]["name"] == "Starter"
    # у администратора тарифа нет -- он без ограничений, выбирать нечего
    assert admin.page.query_selector(f'.uplan[data-id="{mu["users"][ADMIN]}"]') is None


def test_admin_assigns_a_plan_and_the_user_gets_it(login, mu):
    admin = login(ADMIN)
    oid, team = mu["users"][OTHER], mu["plans"]["team"]
    assert _user(admin, OTHER)["plan"]["name"] == "Free"
    admin.open("admin")
    admin.page.select_option(f'.uplan[data-id="{oid}"]', str(team))
    admin.page.click(f'.usave[data-id="{oid}"]')
    admin.page.wait_for_selector("#toasts .toast.ok", timeout=15000)
    _has(admin.page.inner_text("#toasts"), "Тариф пользователя сохранён")

    # API: и у администратора в списке, и у самого пользователя
    assert _user(admin, OTHER)["plan"] == {"id": team, "name": "Team"}
    status, plans = admin.api("GET", "/api/admin/plans")
    assert status == 200 and {p["slug"]: p["users"] for p in plans["plans"]}["team"] == 1
    # UI после перерисовки и после перезагрузки страницы
    admin.page.wait_for_function(
        "([id, plan]) => document.querySelector(`.uplan[data-id=\"${id}\"]`)?.value === String(plan)",
        arg=[oid, team], timeout=15000)
    admin.open("admin")
    assert admin.page.eval_on_selector(f'.uplan[data-id="{oid}"]', "e => e.value") == str(team)

    other = login(OTHER)
    status, mine = other.api("GET", "/api/plan")
    assert status == 200 and mine["plan"] == {"id": team, "name": "Team"}
    assert mine["limits"]["yt_units"] == TEAM_UNITS
    _has(other.open("plan"), "Ваш тариф: Team", f"/ {TEAM_UNITS} units")


def test_regular_user_has_no_admin(login, mu):
    user = login(USER)
    text = user.open("admin")
    assert not user.page.is_visible('a.nav-item[href="#/admin"]')
    _has(text, "Раздел «Админка» не входит в ваш тариф")
    _hasnt(text, ADMIN, OTHER, CHOOSER, "Пользователи")
    # сервер отказывает сам, а не только экран прячет
    assert user.api("GET", "/api/admin/users")[0] == 403
    assert user.api("GET", "/api/admin/plans")[0] == 403
    status, _ = user.api("PUT", f"/api/admin/users/{mu['users'][USER]}/plan",
                         {"planId": mu["plans"]["pro"], "expiresAt": None})
    assert status == 403
    assert user.api("GET", "/api/plan")[1]["plan"]["name"] == "Starter"


# ------------------------------------------------------------------ Тариф

def test_plan_screen_shows_your_plan_limits_and_usage(login, mu):
    user = login(USER)
    assert user.api("POST", "/api/drafts", {"title": "Plan screen draft"})[0] == 200
    status, d = user.api("GET", "/api/plan")
    assert status == 200 and d["plan"]["name"] == "Starter"
    assert d["usage"]["daily"]["yt_units"] == USED_UNITS
    assert d["limits"]["yt_units"] == STARTER_UNITS and d["limits"]["drafts"] == STARTER_DRAFTS
    drafts = d["usage"]["counts"]["drafts"]
    assert drafts >= 1

    text = user.open("plan")
    _has(text, "Ваш тариф: Starter",
         f"Квота YouTube: {USED_UNITS} / {STARTER_UNITS} units",
         f"Черновики и брифы: {drafts} / {STARTER_DRAFTS}")
    # в меню -- «Тариф», строка внизу -- тариф и расход квоты
    assert user.page.is_visible('a.nav-item[href="#/plan"]')
    _has(user.page.inner_text("#myQuota"), "Starter", f"{USED_UNITS}/{STARTER_UNITS}")

    # карточки: свой тариф отмечен, выключенная функция видна, платный не выбрать,
    # скрытый тариф не показан
    starter, pro = user.card("Starter"), user.card("Pro")
    _has(starter.inner_text(), "ваш тариф", "без: Выгрузка ниш")
    assert starter.locator("button.choose").count() == 0
    assert pro.locator("button").is_disabled()
    _has(pro.inner_text(), "Оплата скоро")
    assert user.card("Team").count() == 0
    assert "Team" not in [p["name"] for p in d["plans"]]


def test_choosing_a_free_public_plan(login, mu):
    chooser = login(CHOOSER)
    text = chooser.open("plan")
    _has(text, "Ваш тариф: Free")
    chooser.card("Starter").locator("button.choose").click()
    chooser.wait_text("Ваш тариф: Starter")
    _has(chooser.page.inner_text("#toasts"), "Тариф сменён")
    status, d = chooser.api("GET", "/api/plan")
    assert status == 200 and d["plan"] == {"id": mu["plans"]["starter"], "name": "Starter"}
    _has(chooser.card("Starter").inner_text(), "ваш тариф")

    # платный и скрытый тарифы самому не выбрать -- тариф не меняется
    for slug in ("pro", "team"):
        status, _ = chooser.api("POST", "/api/plan/choose", {"planId": mu["plans"][slug]})
        assert status == 400, slug
    assert chooser.api("GET", "/api/plan")[1]["plan"]["name"] == "Starter"
    chooser.open("plan")
    _has(chooser.text(), "Ваш тариф: Starter")


# ------------------------------------------------------------------ языки

def _dict(session, ns):
    return session.page.evaluate(
        "async ([ns, loc]) => (await import(`/i18n/${loc}/${ns}.js`)).default",
        [ns, session.locale])


def _ui_cyrillic(text):
    pieces = re.findall(r"[А-Яа-яЁё][А-Яа-яЁё\-]*(?:[ \t]+[А-Яа-яЁё][А-Яа-яЁё\-]*)*", text)
    return sorted({p for p in pieces if not any(p in d for d in DATA_CYRILLIC)})


@pytest.mark.parametrize("locale", ["ru", "en"])
@pytest.mark.parametrize("email,route,ns,keys", [
    (ADMIN, "admin", "admin", ["plansTitle", "plansSub", "usersTitle", "usersSub", "colPlan",
                               "colPrice", "colPeriod", "colPublic", "colUsers", "colUntil",
                               "colToday", "baseChip", "newPlan", "createUser", "saveUser"]),
    (USER, "plan", "plan", ["yourPlanSub", "yourPlanChip", "paymentSoon", "deleteTitle",
                            "deleteMe", "free"]),
])
def test_screen_texts_exist_in_both_languages(login, email, route, ns, keys, locale):
    s = login(email, locale)
    text = s.open(route)
    d = _dict(s, ns)
    missing = [k for k in keys if not isinstance(d.get(k), str) or d[k].lower() not in text.lower()]
    assert not missing, f"#/{route} ({locale}): no text for {missing}: {text[:600]}"
    crumb = s.page.inner_text("#crumbSection")
    assert crumb == _dict(s, "router")[route], crumb
    if locale == "en":
        left = _ui_cyrillic(text + "\n" + crumb)
        assert not left, f"#/{route} (en): Russian text on the screen: {left}"
    else:
        assert re.search(r"[А-Яа-яЁё]", d[keys[0]]), d[keys[0]]
