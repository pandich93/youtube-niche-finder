"""Multi-user plan P1-01: whose data a use case touches is never implied.
No function in application/ has a user_id with a default, so a new route or
tool that forgets to pass it fails loudly instead of quietly working on user
1's (the admin's) data; single-user mode and the CLI pass LOCAL_USER_ID at
the boundary. The HTTP and MCP boundaries also refuse to name a user when no
one signed in. Static (AST) plus direct checks of the two boundaries; no database.
Run with pytest, or directly: python3 tests/test_user_id_required.py
"""
import ast
import os
import pathlib
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = pathlib.Path(HERE).parent
sys.path.insert(0, str(BACKEND))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402


def _defaults(fn):
    a = fn.args
    pos = a.posonlyargs + a.args
    yield from zip(pos, [None] * (len(pos) - len(a.defaults)) + list(a.defaults))
    yield from zip(a.kwonlyargs, a.kw_defaults)


def test_no_user_id_has_a_default():
    offenders = []
    for path in sorted((BACKEND / "application").rglob("*.py")):
        for fn in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                offenders += [f"{path.relative_to(BACKEND).as_posix()}:{fn.lineno} "
                              f"{getattr(fn, 'name', 'lambda')}(user_id={ast.unparse(d)})"
                              for arg, d in _defaults(fn) if arg.arg == "user_id" and d is not None]
    assert not offenders, "pass user_id explicitly:\n" + "\n".join(offenders)


class _State:
    pass


class _Request:
    def __init__(self, **state):
        self.state = _State()
        self.state.__dict__.update(state)


def test_http_uid_refuses_a_request_nobody_signed_in(monkeypatch):
    from fastapi import HTTPException

    from interfaces.http import api

    monkeypatch.setenv("NF_MULTI_USER", "1")
    with pytest.raises(HTTPException) as e:
        api._uid(_Request(user_id=None))
    assert e.value.status_code == 401
    with pytest.raises(HTTPException):
        api._uid(_Request())
    assert api._uid(_Request(user_id=7)) == 7

    monkeypatch.setenv("NF_MULTI_USER", "0")
    assert api._uid(_Request()) == 1


def test_mcp_over_http_without_a_token_is_nobody(monkeypatch):
    from interfaces.mcp import auth

    monkeypatch.setattr(auth, "get_access_token", lambda: None)
    monkeypatch.setenv("NF_MULTI_USER", "1")
    monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")
    with pytest.raises(PermissionError):
        auth.current_user_id()
    monkeypatch.setenv("MCP_TRANSPORT", "stdio")
    assert auth.current_user_id() == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
