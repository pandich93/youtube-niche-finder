"""Multi-user plan P1-13: the production compose file keeps the server closed.
Only Caddy is published, accounts and the Secure cookie are on, the proxy is
trusted by subnet and never '*', MCP and its OAuth metadata go to mcp-http,
and the backup service runs the checked script in the database's own image.
Reads docker-compose*.yml, infra/ and .gitattributes; no database, no Docker.
Run with pytest, or directly: python3 tests/test_prod_compose.py
"""
import os

import pytest
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
PROD = os.path.join(ROOT, "docker-compose.prod.yml")

pytestmark = pytest.mark.skipif(not os.path.exists(PROD),
                                reason="needs the repository checkout, not only backend/")


class _Tagged:
    """A value marked with compose's !reset or !override."""

    def __init__(self, tag, value):
        self.tag, self.value = tag, value


class _Loader(yaml.SafeLoader):
    pass


def _tagged(loader, node):
    if isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node)
    elif isinstance(node, yaml.MappingNode):
        value = loader.construct_mapping(node)
    else:
        value = loader.construct_scalar(node)
    return _Tagged(node.tag, value)


_Loader.add_constructor("!reset", _tagged)
_Loader.add_constructor("!override", _tagged)


def _load(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return yaml.load(f, Loader=_Loader)


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8", newline="") as f:
        return f.read()


BASE = _load("docker-compose.yml")["services"] if os.path.exists(PROD) else {}
OVER = _load("docker-compose.prod.yml")["services"] if os.path.exists(PROD) else {}


def _published(name):
    """Ports a service publishes once the production file is layered on."""
    over = OVER.get(name, {}).get("ports")
    if isinstance(over, _Tagged):
        return [] if over.tag == "!reset" else over.value
    return (over or []) + (BASE.get(name, {}).get("ports") or [])


def test_only_the_proxy_is_published():
    published = {name: _published(name) for name in set(BASE) | set(OVER)
                 if name not in ("mcp", "mcp-https", "ollama")}  # opt-in profiles, not started
    assert {name for name, ports in published.items() if ports} == {"proxy"}
    assert sorted(published["proxy"]) == ["443:443", "443:443/udp", "80:80"]
    assert BASE["web"]["ports"] and BASE["postgres"]["ports"]  # local copy: still on 127.0.0.1


def test_web_runs_as_a_signed_in_service_behind_the_proxy():
    env = OVER["web"]["environment"]
    assert env["NF_MULTI_USER"] == "1"
    assert env["NF_COOKIE_SECURE"] == "1"
    assert env["NF_ALLOWED_HOSTS"].startswith("${NF_DOMAIN:?")
    assert "${NF_ALLOWED_HOSTS" in env["NF_ALLOWED_HOSTS"]
    proxies = env["NF_TRUSTED_PROXIES"]
    assert "*" not in proxies and "172.16.0.0/12" in proxies
    assert OVER["worker"]["environment"]["NF_MULTI_USER"] == "1"


def test_postgres_password_is_required():
    assert ":?" in OVER["postgres"]["environment"]["POSTGRES_PASSWORD"]
    assert ":?" in OVER["backup"]["environment"]["PGPASSWORD"]


def test_mcp_http_starts_with_the_stack_and_advertises_the_domain():
    over = OVER["mcp-http"]
    assert isinstance(over["profiles"], _Tagged) and over["profiles"].tag == "!reset"
    assert over["environment"]["NF_MULTI_USER"] == "1"
    assert over["environment"]["MCP_PUBLIC_URL"].startswith("https://${NF_DOMAIN")
    assert not BASE["mcp-http"].get("ports")


def test_caddy_routes_mcp_and_its_metadata_to_mcp_http():
    caddy = _read("infra", "caddy", "Caddyfile.prod")
    assert "{$NF_DOMAIN} {" in caddy and "tls internal" not in caddy
    matcher = next(line for line in caddy.splitlines() if line.strip().startswith("@mcp "))
    # the SDK serves RFC 9728 metadata at /.well-known/oauth-protected-resource/mcp
    for path in ("/mcp", "/mcp/*", "/.well-known/oauth-protected-resource/*"):
        assert f" {path}" in matcher
    assert caddy.index("reverse_proxy mcp-http:8765") < caddy.index("reverse_proxy web:8080")
    # P1-14: Prometheus scrapes web:8080/metrics inside compose, never through the proxy
    assert "respond /metrics 404" in caddy
    assert OVER["web"]["environment"]["NF_LOG_FORMAT"] == "${NF_LOG_FORMAT:-json}"
    assert OVER["proxy"]["volumes"][0] == "./infra/caddy/Caddyfile.prod:/etc/caddy/Caddyfile:ro"


def test_backup_runs_the_checked_script_in_the_database_image():
    backup = OVER["backup"]
    assert backup["image"] == BASE["postgres"]["image"]
    assert backup["entrypoint"] == ["sh", "/backup.sh"]
    assert "./infra/backup/backup.sh:/backup.sh:ro" in backup["volumes"]
    assert backup["depends_on"]["postgres"]["condition"] == "service_healthy"
    script = _read("infra", "backup", "backup.sh")
    assert "\r" not in script
    assert "set -eu" in script and "pg_restore" in script and "--exit-on-error" in script
    assert ".partial" in script  # a half-written dump never counts as a backup


def test_shell_scripts_keep_lf_in_a_windows_checkout():
    assert "*.sh text eol=lf" in _read(".gitattributes")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
