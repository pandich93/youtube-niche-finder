"""Multi-user plan P1-15: MCP over HTTP without sign-in stays on loopback.
Without NF_MULTI_USER=1 every tool acts as user 1, so server.py refuses a
non-loopback address unless MCP_ALLOW_UNAUTHENTICATED=1 says it is meant
(the compose mcp-http service, reachable only inside compose). stdio and
signed-in HTTP are unaffected. No database, no network.
Run with pytest, or directly: python3 tests/test_mcp_exposure.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402

from interfaces.mcp import server as srv  # noqa: E402

HTTP = "streamable-http"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MCP_TRANSPORT", HTTP)
    monkeypatch.setenv("NF_MULTI_USER", "0")
    monkeypatch.delenv("MCP_ALLOW_UNAUTHENTICATED", raising=False)


def test_unauthenticated_http_on_a_public_address_is_refused():
    problem = srv.exposure_problem(HTTP, "0.0.0.0")
    assert problem and "NF_MULTI_USER=1" in problem and "MCP_ALLOW_UNAUTHENTICATED=1" in problem


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_is_fine_without_sign_in(host):
    assert srv.exposure_problem(HTTP, host) is None


def test_sign_in_or_an_explicit_opt_in_allows_any_address(monkeypatch):
    monkeypatch.setenv("NF_MULTI_USER", "1")
    assert srv.exposure_problem(HTTP, "0.0.0.0") is None
    monkeypatch.setenv("NF_MULTI_USER", "0")
    monkeypatch.setenv("MCP_ALLOW_UNAUTHENTICATED", "1")
    assert srv.exposure_problem(HTTP, "0.0.0.0") is None
    monkeypatch.setenv("MCP_ALLOW_UNAUTHENTICATED", "0")
    assert srv.exposure_problem(HTTP, "0.0.0.0")


def test_stdio_is_never_refused():
    assert srv.exposure_problem("stdio", "0.0.0.0") is None


def test_run_exits_before_listening(monkeypatch):
    started = []
    monkeypatch.setattr(srv.mcp, "run", lambda *a, **kw: started.append((a, kw)))
    monkeypatch.setenv("MCP_HOST", "0.0.0.0")
    with pytest.raises(SystemExit) as e:
        srv.run()
    assert "refusing to serve MCP" in str(e.value) and not started

    monkeypatch.delenv("MCP_HOST")  # the default is loopback now
    srv.run()
    assert started[-1][1]["host"] == "127.0.0.1"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
