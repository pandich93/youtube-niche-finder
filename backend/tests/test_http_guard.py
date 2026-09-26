"""Защита локального HTTP API от чужих сайтов в браузере (interfaces/http/api.py).

Две проверки, обе в одном middleware:
  * Host -- только 127.0.0.1 / localhost / [::1] / имя docker-сервиса плюс
    NF_ALLOWED_HOSTS; иначе 400 (DNS rebinding);
  * изменяющие методы (POST/PUT/PATCH/DELETE) -- только с Content-Type JSON или
    с заголовком X-NF-Client; иначе 403 (простой cross-origin POST без preflight).

Прикладные функции заменены заглушками: ни сети, ни квоты, ни LLM.
Тот же одноразовый-схемный сетап, что и в test_http_api.py.
Запуск через pytest или напрямую: python3 tests/test_http_guard.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import interfaces.http.api as api  # noqa: E402

# Без заголовков по умолчанию: каждый тест сам решает, что шлёт клиент.
client = TestClient(api.app)


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def _no_rate_limit_no_real_key(monkeypatch):
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 0)
    monkeypatch.setattr(api, "API_KEY", None)


class _Stub:
    def __init__(self):
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return {"stub": True}


# Пять POST-маршрутов, которые берут параметры только из query: у них нет тела,
# поэтому строгая проверка content-type в FastAPI их не прикрывает.
QUERY_ONLY_POSTS = [
    ("/api/enrich/channels?limit=5", "EN", "classify_channels"),
    ("/api/enrich/videos", "EN", "tag_new_videos"),
    ("/api/events/scan", "AL", "scan"),
    ("/api/transcripts/v1/reindex", "TR", "reindex_transcript"),
    ("/api/niche-clusters/recompute?k=4", "NCL", "compute_clusters"),
]
IDS = [r[0] for r in QUERY_ONLY_POSTS]


@pytest.fixture
def stub(monkeypatch):
    def make(alias, func):
        s = _Stub()
        monkeypatch.setattr(getattr(api, alias), func, s)
        return s
    return make


# ------------------------------------------------ изменяющие методы

@pytest.mark.parametrize("url,alias,func", QUERY_ONLY_POSTS, ids=IDS)
def test_query_only_post_without_headers_is_403(stub, url, alias, func):
    s = stub(alias, func)
    resp = client.post(url)
    assert resp.status_code == 403
    assert "X-NF-Client" in resp.json()["detail"]
    assert s.calls == 0


@pytest.mark.parametrize("url,alias,func", QUERY_ONLY_POSTS, ids=IDS)
def test_query_only_post_with_client_header_passes(stub, url, alias, func):
    s = stub(alias, func)
    resp = client.post(url, headers={"X-NF-Client": "extension"})
    assert resp.status_code == 200, resp.text
    assert s.calls == 1


@pytest.mark.parametrize("url,alias,func", QUERY_ONLY_POSTS, ids=IDS)
def test_query_only_post_with_json_content_type_passes(stub, url, alias, func):
    s = stub(alias, func)
    resp = client.post(url, headers={"Content-Type": "application/json"})
    assert resp.status_code == 200, resp.text
    assert s.calls == 1


@pytest.mark.parametrize("ctype", [
    "application/json; charset=utf-8",
    "Application/JSON",
    "application/merge-patch+json",
])
def test_json_content_type_variants_pass(stub, ctype):
    s = stub("AL", "scan")
    assert client.post("/api/events/scan", headers={"Content-Type": ctype}).status_code == 200
    assert s.calls == 1


@pytest.mark.parametrize("headers", [
    {"Content-Type": "text/plain"},
    {"Content-Type": "application/x-www-form-urlencoded"},
    {"Content-Type": "multipart/form-data; boundary=x"},
    {"Content-Type": "text/plain; application/json"},
    {"X-NF-Client": ""},
    {"X-NF-Client": "   "},
])
def test_simple_request_shapes_are_refused(stub, headers):
    """Всё, что браузер отправит cross-origin без preflight, -- 403."""
    s = stub("AL", "scan")
    assert client.post("/api/events/scan", headers=headers).status_code == 403
    assert s.calls == 0


def test_delete_without_headers_is_403(stub):
    s = stub("T", "untrack")
    assert client.delete("/api/channels/tracked/UC1").status_code == 403
    assert s.calls == 0
    resp = client.delete("/api/channels/tracked/UC1", headers={"X-NF-Client": "dashboard"})
    assert resp.status_code == 200
    assert s.calls == 1


def test_get_without_headers_is_not_blocked(stub):
    stub("Q", "db_stats")
    assert client.get("/api/stats").status_code == 200


def test_preflight_from_the_extension_is_not_blocked():
    resp = client.options("/api/events/scan", headers={
        "Origin": "chrome-extension://abc",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-nf-client",
    })
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "chrome-extension://abc"


def test_preflight_from_a_foreign_site_is_not_allowed():
    resp = client.options("/api/events/scan", headers={
        "Origin": "https://evil.example",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-nf-client",
    })
    assert resp.headers.get("access-control-allow-origin") is None


def test_403_still_carries_cors_headers_for_the_extension(stub):
    """Иначе расширение увидит непрозрачную ошибку CORS вместо понятного 403."""
    stub("AL", "scan")
    resp = client.post("/api/events/scan", headers={"Origin": "chrome-extension://abc"})
    assert resp.status_code == 403
    assert resp.headers.get("access-control-allow-origin") == "chrome-extension://abc"


# ------------------------------------------------------------ Host

@pytest.mark.parametrize("host", [
    "evil.example", "evil.example:8080", "127.0.0.1.evil.example",
    "localhost.evil.example:8080", "127.0.0.2:8080", "[::2]:8080",
    "localhost:abc", "[::1", "",
])
def test_foreign_host_is_400(stub, host):
    stub("Q", "db_stats")
    resp = client.get("/api/stats", headers={"Host": host})
    assert resp.status_code == 400
    assert "Host" in resp.json()["detail"]


def test_foreign_host_blocks_static_files_too():
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400


def test_foreign_host_blocks_mutations_even_with_the_client_header(stub):
    s = stub("AL", "scan")
    resp = client.post("/api/events/scan",
                       headers={"Host": "evil.example", "X-NF-Client": "x"})
    assert resp.status_code == 400
    assert s.calls == 0


@pytest.mark.parametrize("host", [
    "127.0.0.1:8080", "127.0.0.1", "localhost:8080", "LOCALHOST:9000",
    "[::1]:8080", "[::1]", "web:8080",
])
def test_local_hosts_pass(stub, host):
    stub("Q", "db_stats")
    assert client.get("/api/stats", headers={"Host": host}).status_code == 200


def test_400_for_host_still_carries_cors_headers(stub):
    stub("Q", "db_stats")
    resp = client.get("/api/stats", headers={"Host": "evil.example",
                                             "Origin": "chrome-extension://abc"})
    assert resp.status_code == 400
    assert resp.headers.get("access-control-allow-origin") == "chrome-extension://abc"


def test_nf_allowed_hosts_extends_the_list():
    hosts = api._allowed_hosts(" nas.local , Box.Lan:8080,,")
    assert {"nas.local", "box.lan"} <= hosts
    assert {"127.0.0.1", "localhost", "[::1]", "web"} <= hosts
    assert api._allowed_hosts("") == api._allowed_hosts(None)


def test_extra_allowed_host_passes(stub, monkeypatch):
    stub("Q", "db_stats")
    monkeypatch.setattr(api, "ALLOWED_HOSTS", api._allowed_hosts("nas.local"))
    assert client.get("/api/stats", headers={"Host": "nas.local:8080"}).status_code == 200
    assert client.get("/api/stats", headers={"Host": "evil.example"}).status_code == 400


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
