"""Unit tests for infrastructure/llm/openrouter.py -- no network anywhere,
httpx.Client.post is monkeypatched. Run with pytest, or directly:
python3 tests/test_llm_provider.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import httpx  # noqa: E402

import infrastructure.llm.openrouter as openrouter_mod  # noqa: E402
from infrastructure.llm.openrouter import OpenRouterProvider  # noqa: E402
from infrastructure.llm.null import NullProvider  # noqa: E402

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


def _ok(content_obj, model="openai/gpt-4o-mini", usage=None):
    return _FakeResponse(200, {
        "model": model,
        "choices": [{"message": {"content": json.dumps(content_obj)}}],
        "usage": usage or {"prompt_tokens": 12, "completion_tokens": 34, "cost": 0.001},
    })


def test_null_provider_never_calls_anything():
    assert NullProvider().complete_json("s", "u", SCHEMA) is None


def test_successful_call_returns_parsed_result(monkeypatch):
    calls = []

    def fake_post(self, url, headers=None, json=None):
        calls.append((url, headers, json))
        return _ok({"title": "x", "score": 3})

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    result = OpenRouterProvider("sk-test-key").complete_json("sys", "usr", SCHEMA)

    assert result is not None
    assert result.data == {"title": "x", "score": 3}
    assert result.prompt_tokens == 12
    assert result.completion_tokens == 34
    assert result.cost_usd == 0.001
    assert len(calls) == 1
    assert calls[0][1]["Authorization"] == "Bearer sk-test-key"
    assert calls[0][2]["response_format"]["type"] == "json_schema"


def test_429_is_retried_then_succeeds(monkeypatch):
    responses = [_FakeResponse(429, text="rate limited"), _ok({"title": "y", "score": 1})]

    def fake_post(self, url, headers=None, json=None):
        return responses.pop(0)

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    monkeypatch.setattr(openrouter_mod.time, "sleep", lambda s: None)

    result = OpenRouterProvider("sk-test-key").complete_json("sys", "usr", SCHEMA)

    assert result is not None
    assert result.data == {"title": "y", "score": 1}


def test_exhausted_retries_on_persistent_429_returns_none(monkeypatch):
    def fake_post(self, url, headers=None, json=None):
        return _FakeResponse(429, text="still rate limited")

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    monkeypatch.setattr(openrouter_mod.time, "sleep", lambda s: None)

    assert OpenRouterProvider("sk-test-key").complete_json("sys", "usr", SCHEMA) is None


def test_model_rejecting_response_format_falls_back_to_plain_text(monkeypatch):
    """First call (with response_format) -- 400; second call (without) -- 200
    with the JSON embedded in plain text."""
    attempts = []

    def fake_post(self, url, headers=None, json=None):
        attempts.append(dict(json))  # complete_json() mutates payload in place via pop()
        if "response_format" in json:
            return _FakeResponse(400, text="response_format not supported")
        return _FakeResponse(200, {
            "model": "some/model",
            "choices": [{"message": {"content": 'Sure, here it is: {"title": "z", "score": 9} thanks'}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "cost": 0.0},
        })

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    result = OpenRouterProvider("sk-test-key").complete_json("sys", "usr", SCHEMA)

    assert result is not None
    assert result.data == {"title": "z", "score": 9}
    assert len(attempts) == 2
    assert "response_format" in attempts[0]
    assert "response_format" not in attempts[1]


def test_broken_json_in_content_returns_none(monkeypatch):
    def fake_post(self, url, headers=None, json=None):
        return _FakeResponse(200, {
            "model": "m", "choices": [{"message": {"content": "not json at all"}}],
            "usage": {},
        })

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    assert OpenRouterProvider("sk-test-key").complete_json("sys", "usr", SCHEMA) is None


def test_response_missing_required_field_fails_validation(monkeypatch):
    def fake_post(self, url, headers=None, json=None):
        return _ok({"title": "no score field"})

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    assert OpenRouterProvider("sk-test-key").complete_json("sys", "usr", SCHEMA) is None


def test_api_key_is_never_present_in_a_logged_error(monkeypatch, caplog):
    def fake_post(self, url, headers=None, json=None):
        raise httpx.ConnectError("boom while calling with key sk-secret-abc123")

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    with caplog.at_level("WARNING"):
        result = OpenRouterProvider("sk-secret-abc123").complete_json("sys", "usr", SCHEMA)

    assert result is None
    assert "sk-secret-abc123" not in caplog.text


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

    class _CapLog:
        def __init__(self):
            self.records = []

        def at_level(self, level):
            import contextlib
            return contextlib.nullcontext()

        @property
        def text(self):
            return ""

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        mp = _Monkeypatch()
        try:
            argnames = fn.__code__.co_varnames[:fn.__code__.co_argcount]
            kwargs = {}
            if "monkeypatch" in argnames:
                kwargs["monkeypatch"] = mp
            if "caplog" in argnames:
                kwargs["caplog"] = _CapLog()
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
