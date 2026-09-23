"""Unit tests for infrastructure/llm/ollama.py -- no network anywhere,
httpx.Client.post/get is monkeypatched. Run with pytest, or directly:
python3 tests/test_ollama_provider.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import httpx  # noqa: E402

from infrastructure.llm import factory as llm_factory  # noqa: E402
from infrastructure.llm.null import NullProvider  # noqa: E402
from infrastructure.llm.ollama import OllamaProvider  # noqa: E402
from infrastructure.llm.openrouter import OpenRouterProvider  # noqa: E402

SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "score": {"type": "integer"}},
    "required": ["title", "score"],
}


class _FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


def _ok(content_obj, model="llama3.1"):
    return _FakeResponse(200, {
        "model": model,
        "message": {"role": "assistant", "content": json.dumps(content_obj)},
        "done": True,
        "prompt_eval_count": 12,
        "eval_count": 34,
    })


def test_successful_call_returns_parsed_result(monkeypatch):
    calls = []

    def fake_post(self, url, json=None):
        calls.append((url, json))
        return _ok({"title": "x", "score": 3})

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    result = OllamaProvider("http://127.0.0.1:11434", model="llama3.1").complete_json(
        "sys", "usr", SCHEMA)

    assert result is not None
    assert result.data == {"title": "x", "score": 3}
    assert result.prompt_tokens == 12
    assert result.completion_tokens == 34
    assert result.cost_usd == 0.0
    assert len(calls) == 1
    assert calls[0][0] == "http://127.0.0.1:11434/api/chat"
    assert calls[0][1]["model"] == "llama3.1"
    assert calls[0][1]["stream"] is False
    assert calls[0][1]["format"] == SCHEMA


def test_no_model_configured_returns_none_without_a_network_call(monkeypatch):
    calls = []
    monkeypatch.setattr(httpx.Client, "post", lambda self, url, json=None: calls.append(1))

    result = OllamaProvider("http://127.0.0.1:11434").complete_json("sys", "usr", SCHEMA)

    assert result is None
    assert calls == []


def test_explicit_model_kwarg_overrides_the_configured_default(monkeypatch):
    calls = []

    def fake_post(self, url, json=None):
        calls.append(json["model"])
        return _ok({"title": "y", "score": 1}, model=json["model"])

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    result = OllamaProvider("http://127.0.0.1:11434", model="default-model").complete_json(
        "sys", "usr", SCHEMA, model="other-model")

    assert result.model == "other-model"
    assert calls == ["other-model"]


def test_connection_error_returns_none(monkeypatch):
    def fake_post(self, url, json=None):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    assert OllamaProvider("http://127.0.0.1:11434", model="m").complete_json(
        "sys", "usr", SCHEMA) is None


def test_non_200_status_returns_none(monkeypatch):
    def fake_post(self, url, json=None):
        return _FakeResponse(500, text="internal error")

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    assert OllamaProvider("http://127.0.0.1:11434", model="m").complete_json(
        "sys", "usr", SCHEMA) is None


def test_broken_json_in_content_returns_none(monkeypatch):
    def fake_post(self, url, json=None):
        return _FakeResponse(200, {
            "model": "m", "message": {"content": "not json at all"},
            "prompt_eval_count": 0, "eval_count": 0,
        })

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    assert OllamaProvider("http://127.0.0.1:11434", model="m").complete_json(
        "sys", "usr", SCHEMA) is None


def test_json_embedded_in_prose_is_extracted(monkeypatch):
    def fake_post(self, url, json=None):
        return _FakeResponse(200, {
            "model": "m",
            "message": {"content": 'Sure, here it is: {"title": "z", "score": 9} thanks'},
            "prompt_eval_count": 1, "eval_count": 2,
        })

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    result = OllamaProvider("http://127.0.0.1:11434", model="m").complete_json(
        "sys", "usr", SCHEMA)

    assert result.data == {"title": "z", "score": 9}


def test_response_missing_required_field_fails_validation(monkeypatch):
    def fake_post(self, url, json=None):
        return _ok({"title": "no score field"})

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    assert OllamaProvider("http://127.0.0.1:11434", model="m").complete_json(
        "sys", "usr", SCHEMA) is None


def test_base_url_trailing_slash_is_stripped(monkeypatch):
    calls = []

    def fake_post(self, url, json=None):
        calls.append(url)
        return _ok({"title": "x", "score": 1})

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    OllamaProvider("http://127.0.0.1:11434/", model="m").complete_json("sys", "usr", SCHEMA)

    assert calls == ["http://127.0.0.1:11434/api/chat"]


def test_available_reports_ok_when_model_is_pulled(monkeypatch):
    def fake_get(self, url):
        return _FakeResponse(200, {"models": [{"name": "llama3.1:latest"}, {"name": "phi3"}]})

    monkeypatch.setattr(httpx.Client, "get", fake_get)

    ok, msg = OllamaProvider("http://127.0.0.1:11434", model="llama3.1").available()

    assert ok is True
    assert "llama3.1" in msg


def test_available_reports_missing_model(monkeypatch):
    def fake_get(self, url):
        return _FakeResponse(200, {"models": [{"name": "phi3:latest"}]})

    monkeypatch.setattr(httpx.Client, "get", fake_get)

    ok, msg = OllamaProvider("http://127.0.0.1:11434", model="llama3.1").available()

    assert ok is False
    assert "llama3.1" in msg


def test_available_reports_unreachable_server(monkeypatch):
    def fake_get(self, url):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx.Client, "get", fake_get)

    ok, msg = OllamaProvider("http://127.0.0.1:11434", model="llama3.1").available()

    assert ok is False


def test_factory_selects_ollama_provider_with_configured_url_and_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", "http://ollama-host:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.1")

    provider = llm_factory.get_provider()

    assert isinstance(provider, OllamaProvider)
    assert provider.base_url == "http://ollama-host:11434"
    assert provider.model == "llama3.1"


def test_factory_ollama_falls_back_to_default_url_when_unset(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)

    provider = llm_factory.get_provider()

    assert isinstance(provider, OllamaProvider)
    assert provider.base_url == llm_factory.DEFAULT_OLLAMA_URL
    assert provider.model is None


def test_factory_unknown_provider_falls_back_to_null(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "something-else")

    assert isinstance(llm_factory.get_provider(), NullProvider)


def test_factory_still_selects_openrouter_when_configured(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")

    assert isinstance(llm_factory.get_provider(), OpenRouterProvider)


def test_display_model_shows_ollama_model_when_selected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.1")

    assert llm_factory.display_model() == "llama3.1"


def test_display_model_flags_missing_ollama_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)

    assert "не задан" in llm_factory.display_model()


if __name__ == "__main__":
    class _Monkeypatch:
        def __init__(self):
            self._undo = []

        def setattr(self, obj, name, value):
            self._undo.append(("attr", obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def setenv(self, name, value):
            self._undo.append(("env", os.environ, name, os.environ.get(name)))
            os.environ[name] = value

        def delenv(self, name, raising=True):
            had = name in os.environ
            self._undo.append(("env", os.environ, name, os.environ.get(name) if had else None))
            if had:
                del os.environ[name]
            elif raising:
                raise KeyError(name)

        def undo(self):
            for kind, obj, name, value in reversed(self._undo):
                if kind == "attr":
                    setattr(obj, name, value)
                elif value is None:
                    obj.pop(name, None)
                else:
                    obj[name] = value

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        mp = _Monkeypatch()
        try:
            argnames = fn.__code__.co_varnames[:fn.__code__.co_argcount]
            kwargs = {"monkeypatch": mp} if "monkeypatch" in argnames else {}
            fn(**kwargs)
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
