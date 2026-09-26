"""Tests for interfaces/cli/cli.py: every command parses its flags into the
right application call and prints JSON, hints go to stderr, and doctor turns
each failure mode into the right advice. Application calls, the YouTube
client, LLM and notifier are all stubbed, and _key() is replaced so the real
key in a local .env never reaches a stub or the output. Same throwaway-schema
setup as test_tags_http.py, in case a stub is ever missed.
Run with pytest, or directly: python3 tests/test_cli.py
"""
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest                                          # noqa: E402

import interfaces.cli.cli as cli                        # noqa: E402
import infrastructure.youtube.client as yt              # noqa: E402
from application import collecting                      # noqa: E402
from application import discovery                       # noqa: E402
from application import channel_tracking                # noqa: E402
from application import search                          # noqa: E402
from application import niche_export                    # noqa: E402
from application import llm_gateway                     # noqa: E402
from infrastructure.llm import factory as llm_factory   # noqa: E402
from infrastructure.notify import factory as notify_factory  # noqa: E402
from infrastructure.notify.null import NullNotifier      # noqa: E402

GOOD_KEY = "AIza" + "x" * 35


class _Stub:
    def __init__(self, result=None, raises=None):
        self.result = {"ok": True} if result is None else result
        self.raises = raises
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.raises:
            raise self.raises
        return self.result


@pytest.fixture
def run(monkeypatch, capsys):
    """run(*argv, key=...) -> (exit_code, stdout, stderr)"""
    def _run(*argv, key=GOOD_KEY):
        monkeypatch.setattr(cli, "_key", lambda: key)
        monkeypatch.setattr(sys, "argv", ["cli.py", *argv])
        with pytest.raises(SystemExit) as exc:
            cli.main()
        captured = capsys.readouterr()
        return exc.value.code, captured.out, captured.err
    return _run


@pytest.fixture
def stub(monkeypatch):
    def make(module, func, **kw):
        s = _Stub(**kw)
        monkeypatch.setattr(module, func, s)
        return s
    return make


# ------------------------------------------------------- simple commands
# (argv, module, function, expected args, expected kwargs subset)
COMMANDS = [
    (["collect-channel", "@tiny", "--max-videos", "30", "--niche", "n1", "--embed"],
     collecting, "collect_channel", (GOOD_KEY, "@tiny"),
     {"max_videos": 30, "niche": "n1", "embed": True}),
    (["collect-channel", "@tiny"], collecting, "collect_channel", (GOOD_KEY, "@tiny"),
     {"max_videos": 100, "niche": None, "embed": False}),
    (["collect", "cold showers", "--period", "7d", "--pages", "2", "--region", "US"],
     collecting, "collect_niche", (GOOD_KEY, "cold showers"),
     {"period": "7d", "pages": 2, "region": "US", "label": None}),
    (["embed-videos", "--limit", "50"], collecting, "backfill_embeddings", (),
     {"limit": 50}),
    (["viral", "--period", "24h", "--max-subscribers", "5000", "--min-vsr", "2.5"],
     discovery, "viral_videos_small_channels", (),
     {"period": "24h", "max_subscribers": 5000, "min_views_per_subscriber": 2.5,
      "period_by": "published", "limit": 25}),
    (["categories", "--rank-by", "channels", "--niche", "n1"], discovery,
     "most_popular_categories", (), {"rank_by": "channels", "niche": "n1",
                                     "period": "7d"}),
    (["keywords", "--limit", "10", "--min-videos", "2"], discovery,
     "trending_keywords", (), {"top_n": 10, "min_videos": 2, "period": "24h",
                               "sort_by": "momentum"}),
    (["channels", "--min-multiplier", "3"], channel_tracking,
     "recently_added_outlier_channels", (),
     {"min_multiplier": 3.0, "period_by": "discovered", "period": "24h"}),
    (["stats"], search, "db_stats", (), {}),
    (["fix-tracked"], channel_tracking, "fix_tracked", (GOOD_KEY,), {"apply": False}),
    (["fix-tracked", "--apply"], channel_tracking, "fix_tracked", (GOOD_KEY,),
     {"apply": True}),
]


@pytest.mark.parametrize("argv,module,func,args,kwargs", COMMANDS,
                         ids=[" ".join(c[0]) for c in COMMANDS])
def test_command_calls_the_application_and_prints_json(run, stub, argv, module, func,
                                                        args, kwargs):
    s = stub(module, func, result={"answer": 42})
    code, out, err = run(*argv)
    assert code == 0
    assert len(s.calls) == 1
    got_args, got_kwargs = s.calls[0]
    assert got_args == args
    for k, v in kwargs.items():
        assert got_kwargs[k] == v, k
    assert json.loads(out) == {"answer": 42}
    assert err == ""


def test_collect_embed_flag_cannot_be_switched_off(run, stub):
    # --embed is store_true with default=True, so embeddings are always on
    # for `collect`; pinned here so a fix is a deliberate, visible change
    s = stub(collecting, "collect_niche")
    run("collect", "q")
    assert s.calls[0][1]["embed"] is True


def test_fix_tracked_without_a_key_passes_none(run, stub):
    s = stub(channel_tracking, "fix_tracked")
    run("fix-tracked", key="")
    assert s.calls[0][0] == (None,)


def test_refresh_updates_videos_then_tracked_channels(run, stub):
    videos = stub(collecting, "refresh_stats", result={"videos": 1})
    channels = stub(collecting, "refresh_channels", result={"channels": 2})
    code, out, _ = run("refresh", "--scope", "tracked", "--limit", "10")
    assert code == 0
    assert videos.calls[0] == ((GOOD_KEY,), {"scope": "tracked", "period": "30d",
                                             "limit": 10})
    assert channels.calls[0] == ((GOOD_KEY,), {"only_tracked": True})
    assert '"videos": 1' in out and '"channels": 2' in out


def test_hint_is_repeated_on_stderr(run, stub):
    stub(discovery, "viral_videos_small_channels",
         result={"videos": [], "hint": "lower --min-views"})
    code, out, err = run("viral")
    assert json.loads(out)["hint"] == "lower --min-views"
    assert ">>> lower --min-views" in err


def test_unknown_choice_is_rejected_by_argparse(run):
    code, _, err = run("refresh", "--scope", "galaxy")
    assert code == 2 and "invalid choice" in err


# ---------------------------------------------------------- export-niche

def test_export_niche_writes_the_file_it_reports(run, stub, tmp_path):
    s = stub(niche_export, "export_niche",
             result={"content": b"a\tb\n", "filename": "n1.tsv", "rowCount": 1})
    target = tmp_path / "out.tsv"
    code, out, _ = run("export-niche", "n1", "--out", str(target))
    assert code == 0
    assert s.calls[0] == (("n1",), {"fmt": "tsv"})
    assert target.read_bytes() == b"a\tb\n"
    assert json.loads(out) == {"written": str(target), "rows": 1}


def test_export_niche_defaults_to_the_suggested_filename(run, stub, tmp_path,
                                                         monkeypatch):
    monkeypatch.chdir(tmp_path)
    stub(niche_export, "export_niche",
         result={"content": b"x", "filename": "n1_videos.csv", "rowCount": 0})
    run("export-niche", "n1", "--format", "csv")
    assert (tmp_path / "n1_videos.csv").read_bytes() == b"x"


def test_export_niche_error_exits_1_with_message(run, stub):
    stub(niche_export, "export_niche", raises=ValueError("unknown niche 'n9'"))
    code, _, err = run("export-niche", "n9")
    assert code == 1 and "error: unknown niche 'n9'" in err


# ------------------------------------------------------------ notify, seed

def test_notify_test_without_a_channel_explains_how_to_set_one(run, monkeypatch):
    monkeypatch.setattr(notify_factory, "get_notifier", lambda: NullNotifier())
    code, out, err = run("notify-test")
    assert json.loads(out)["sent"] is False
    assert "NOTIFY_TELEGRAM_BOT_TOKEN" in err


def test_notify_test_sends_through_the_configured_channel(run, monkeypatch):
    sent = []
    notifier = types.SimpleNamespace(send=lambda text: sent.append(text) or True)
    monkeypatch.setattr(notify_factory, "get_notifier", lambda: notifier)
    monkeypatch.setattr(notify_factory, "display_target", lambda: "telegram:123")
    code, out, _ = run("notify-test")
    assert json.loads(out) == {"sent": True, "channel": "telegram:123"}
    assert len(sent) == 1 and "niche-finder" in sent[0]


def test_seed_runs_the_demo_seeder(run, monkeypatch):
    fake = types.ModuleType("seed_demo")
    fake.seed = lambda: {"seeded": 3}
    monkeypatch.setitem(sys.modules, "seed_demo", fake)
    code, out, _ = run("seed")
    assert code == 0 and json.loads(out) == {"seeded": 3}


# ---------------------------------------------------------------- doctor

HEALTHY_DB = {"db_path": "postgresql://test", "channels": 5, "videos": 50,
              "niches": 1, "video_stat_snapshots": 100,
              "history_since": "2026-09-01T00:00:00Z"}
COVERAGE = {"videosPublishedInPeriod": 3, "videosTotal": 50,
            "velocityMetricsAvailable": True}


@pytest.fixture
def healthy(monkeypatch, stub):
    """Everything doctor checks works; tests break one thing at a time."""
    monkeypatch.setenv("LLM_PROVIDER", "none")
    monkeypatch.setattr(cli.db, "init_db", lambda: None)
    return {
        "categories": stub(yt, "video_categories", result=[{"id": "10"}]),
        "stats": stub(search, "db_stats", result=dict(HEALTHY_DB)),
        "coverage": stub(discovery, "coverage", result=dict(COVERAGE)),
    }


def test_doctor_all_good(run, healthy):
    code, out, _ = run("doctor")
    assert code == 0 and "Всё в порядке." in out
    assert healthy["categories"].calls[0] == ((GOOD_KEY,), {"region_code": "US"})
    assert "потрачен 1 unit" in out


def test_doctor_without_a_key_skips_the_network_check(run, healthy):
    code, out, _ = run("doctor", key="")
    assert code == 1
    assert "Впишите YOUTUBE_API_KEY" in out and "пропущено (нет ключа)" in out
    assert healthy["categories"].calls == []


def test_doctor_flags_a_truncated_key(run, healthy):
    code, out, _ = run("doctor", key="AIzaSHORT")
    assert code == 1 and "скопирован не полностью" in out


@pytest.mark.parametrize("error,advice", [
    ("accessNotConfigured: YouTube Data API v3 has not been used", "не включён YouTube Data"),
    ("API key not valid. Please pass a valid API key.", "Ключ недействителен"),
    ("ipRefererBlocked", "ограничение по HTTP referrer"),
    ("quotaExceeded: The request cannot be completed", "Квота на сегодня исчерпана"),
    ("Connection refused", "Сеть до googleapis.com"),
])
def test_doctor_maps_api_errors_to_advice(run, healthy, stub, error, advice):
    stub(yt, "video_categories", raises=RuntimeError(error))
    code, out, _ = run("doctor")
    assert code == 1 and advice in out


def test_doctor_never_prints_the_key_from_an_error(run, healthy, stub):
    stub(yt, "video_categories",
         raises=RuntimeError(f"YouTube API error 400: key={GOOD_KEY} rejected"))
    _, out, _ = run("doctor")
    assert GOOD_KEY not in out and "<KEY>" in out


@pytest.mark.parametrize("stats,advice", [
    ({"videos": 0}, "База пустая"),
    ({"history_since": None}, "Истории нет"),
])
def test_doctor_flags_an_empty_or_unwatched_database(run, healthy, stats, advice):
    healthy["stats"].result.update(stats)
    code, out, _ = run("doctor")
    assert code == 1 and advice in out


def test_doctor_reports_a_database_that_will_not_open(run, healthy, monkeypatch):
    def boom():
        raise RuntimeError("connection refused on 5433")
    monkeypatch.setattr(cli.db, "init_db", boom)
    code, out, _ = run("doctor")
    assert code == 1 and "База не открывается: connection refused on 5433" in out


def test_doctor_coverage_failure_is_shown_but_not_a_problem(run, healthy, stub):
    stub(discovery, "coverage", raises=RuntimeError("no rows"))
    code, out, _ = run("doctor")
    assert code == 0 and "ОШИБКА: no rows" in out


def test_doctor_openrouter_without_a_key(run, healthy, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    code, out, _ = run("doctor")
    assert code == 1 and "OPENROUTER_API_KEY не задан" in out


def test_doctor_openrouter_ping_only_with_the_llm_flag(run, healthy, monkeypatch, stub):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    ping = stub(llm_gateway, "run", result={"ok": True})
    code, out, _ = run("doctor")
    assert code == 0 and "пинг пропущен" in out and ping.calls == []
    code, out, _ = run("doctor", "--llm")
    assert code == 0 and "провайдер ответил" in out and len(ping.calls) == 1


def test_doctor_failed_llm_ping_is_a_problem(run, healthy, monkeypatch, stub):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    stub(llm_gateway, "run", result={"ok": False})
    code, out, _ = run("doctor", "--llm")
    assert code == 1 and "Пинг LLM-провайдера не удался" in out


@pytest.mark.parametrize("available,flag,expect_code,expect_text", [
    ((False, "server not reachable"), [], 1, "запустите `ollama serve`"),
    ((True, "model pulled"), [], 0, "пинг пропущен (передайте --llm)"),
    ((True, "model pulled"), ["--llm"], 0, "провайдер ответил"),
])
def test_doctor_ollama(run, healthy, monkeypatch, stub, available, flag,
                       expect_code, expect_text):
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    provider = types.SimpleNamespace(base_url="http://ollama:11434",
                                     available=lambda: available)
    monkeypatch.setattr(llm_factory, "get_provider", lambda: provider)
    monkeypatch.setattr(llm_factory, "display_model", lambda: "qwen")
    stub(llm_gateway, "run", result={"ok": True})
    code, out, _ = run("doctor", *flag)
    assert code == expect_code
    assert expect_text in out and "url=http://ollama:11434" in out


def test_key_reads_dotenv_and_strips(monkeypatch):
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: None)
    monkeypatch.setenv("YOUTUBE_API_KEY", "  KEY123  ")
    assert cli._key() == "KEY123"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
