"""Лимитер запросов к HTTP API (interfaces/http/api.py).

Тот же одноразовый-схемный сетап, что и в остальных тестах (tests/schema_scope.py):
сеть и ключ YouTube не нужны -- дёргаем только /api/health.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

from fastapi.testclient import TestClient  # noqa: E402

import interfaces.http.api as api  # noqa: E402

client = TestClient(api.app)


def _reset():
    api._rate_hits.clear()


def test_default_limit_survives_a_realistic_extension_burst():
    """Расширение шлёт до 40 id за раз с флашем раз в 500 мс плюс до четырёх
    параллельных запросов на панель канала -- это ~120-200 запросов в минуту.
    Дефолт обязан пропускать такой всплеск целиком."""
    _reset()
    assert api.RATE_LIMIT_PER_MINUTE >= 400, "дефолт слишком низкий для расширения"
    codes = {client.get("/api/health").status_code for _ in range(250)}
    assert codes == {200}


def test_over_the_limit_gives_429_with_retry_after(monkeypatch):
    _reset()
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 5)
    assert [client.get("/api/health").status_code for _ in range(5)] == [200] * 5

    resp = client.get("/api/health")
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) >= 1
    _reset()


def test_limiter_does_not_count_frontend_static_files(monkeypatch):
    """Одна загрузка дашборда стоит десятка файлов -- если считать и их,
    бюджет выгорает на ровном месте."""
    _reset()
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 2)
    for _ in range(10):
        client.get("/index.html")
    assert client.get("/api/health").status_code == 200
    _reset()


def test_zero_disables_the_limiter(monkeypatch):
    _reset()
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 0)
    codes = {client.get("/api/health").status_code for _ in range(50)}
    assert codes == {200}
    _reset()


def test_429_still_carries_cors_headers(monkeypatch):
    """Лимитер объявлен выше CORSMiddleware намеренно: иначе расширение
    увидит непрозрачную ошибку CORS вместо честного 429."""
    _reset()
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 1)
    origin = {"Origin": "chrome-extension://abcdef"}
    client.get("/api/health", headers=origin)
    resp = client.get("/api/health", headers=origin)
    assert resp.status_code == 429
    assert resp.headers.get("access-control-allow-origin") == "chrome-extension://abcdef"
    _reset()


if __name__ == "__main__":
    class _Monkeypatch:
        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, value):
            self._undo.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def undo(self):
            for obj, name, value in reversed(self._undo):
                setattr(obj, name, value)

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        mp = _Monkeypatch()
        try:
            if "monkeypatch" in fn.__code__.co_varnames[:fn.__code__.co_argcount]:
                fn(mp)
            else:
                fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as e:
            failed += 1
            import traceback
            print(f"  FAIL  {fn.__name__}: {e}")
            traceback.print_exc()
        finally:
            mp.undo()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
