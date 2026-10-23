"""Tests for interfaces/mcp/prompts.py (plan 11): the ready-made scenarios a
Claude client shows under "+". Every tool a scenario names must exist, so
renaming a tool breaks this test instead of silently breaking a scenario.
No network, no quota, no database writes.
Run with pytest, or directly: python3 tests/test_mcp_prompts.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import anyio  # noqa: E402
import pytest  # noqa: E402

import interfaces.mcp.server as srv  # noqa: E402

EXPECTED = {
    "find_niche": {"topic"},
    "analyze_competitor": {"channel"},
    "validate_idea": {"idea", "niche"},
    "outlier_to_video": {"video_id"},
    "weekly_review": set(),
    "find_content_gaps": {"niche"},
    "niche_health": {"niche"},
}
EXAMPLE_ARGS = {"topic": "space facts", "channel": "@kurzgesagt", "idea": "black holes",
                "niche": "space", "video_id": "dQw4w9WgXcQ"}


def run(coro_fn, *args):
    return anyio.run(coro_fn, *args)


def prompts():
    return {p.name: p for p in run(srv.mcp.list_prompts)}


def tool_names():
    return {t.name for t in run(srv.mcp.list_tools)}


def text_of(name):
    p = prompts()[name]
    args = {a.name: EXAMPLE_ARGS[a.name] for a in (p.arguments or []) if a.required}
    res = run(lambda: srv.mcp.get_prompt(name, args))
    return "\n".join(m.content.text for m in res.messages)


def test_all_scenarios_are_listed_with_their_required_arguments():
    got = prompts()
    assert set(got) == set(EXPECTED)
    for name, required in EXPECTED.items():
        p = got[name]
        assert p.title and p.description, name
        assert {a.name for a in (p.arguments or []) if a.required} == required, name


def test_every_argument_is_described_for_the_client_menu():
    for name, p in prompts().items():
        for a in p.arguments or []:
            assert a.description, f"{name}.{a.name} has no description"


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_tool_a_scenario_names_exists(name):
    named = set(re.findall(r"`([a-z_]+)\(", text_of(name)))
    assert named, f"{name} names no tools"
    missing = named - tool_names()
    assert not missing, f"{name} names tools that do not exist: {missing}"


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_scenario_states_its_quota_cost(name):
    assert "Квота" in text_of(name)


def test_arguments_are_filled_into_the_text():
    assert "space facts" in text_of("find_niche")
    assert "@kurzgesagt" in text_of("analyze_competitor")
    assert "black holes" in text_of("validate_idea")


def test_scenarios_that_collect_check_the_quota_first():
    for name in ("find_niche", "analyze_competitor"):
        t = text_of(name)
        assert t.index("`db_stats(") < t.index("`collect_"), name


def test_gaps_scenario_fetches_only_after_the_user_agrees():
    t = text_of("find_content_gaps")
    assert "fetch=false" in t and "fetch=true" in t
    assert "согласи" in t


def test_optional_arguments_have_defaults():
    t = text_of("find_niche")
    assert "pages=1" in t or "1 страниц" in t


# ------------------------------------------------- language (i18n plan)

@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_english_scenarios_have_no_russian_and_name_the_same_tools(name):
    from domain import i18n
    ru = text_of(name)
    with i18n.use_locale("en"):
        en = text_of(name)
    assert not re.search("[А-Яа-яЁё]", en), en
    assert "Quota" in en
    assert set(re.findall(r"`([a-z_]+)\(", en)) == set(re.findall(r"`([a-z_]+)\(", ru))


def test_russian_is_the_default_scenario_language():
    assert "Квота" in text_of("weekly_review") and "Quota" not in text_of("weekly_review")


def run_middleware(saved):
    """The language a tool or scenario sees behind locale_middleware when the
    user saved `saved` (None = never chose)."""
    from domain import i18n
    from interfaces.mcp import auth as mcp_auth

    async def call_next(_ctx):
        return i18n.current_locale()

    original = mcp_auth._saved_locale
    mcp_auth._saved_locale = lambda _uid: saved
    try:
        return run(mcp_auth.locale_middleware, None, call_next)
    finally:
        mcp_auth._saved_locale = original


def test_middleware_uses_the_language_the_user_saved(monkeypatch):
    monkeypatch.delenv("NF_DEFAULT_LOCALE", raising=False)
    assert run_middleware("en") == "en"
    assert run_middleware("ru") == "ru"


def test_middleware_falls_back_to_the_installation_default(monkeypatch):
    monkeypatch.setenv("NF_DEFAULT_LOCALE", "en")
    assert run_middleware(None) == "en"
    monkeypatch.setenv("NF_DEFAULT_LOCALE", "ru")
    assert run_middleware(None) == "ru"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
