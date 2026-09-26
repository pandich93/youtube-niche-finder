"""HTTP-layer tests for interfaces/http/api.py: every route reaches the right
application function with the right arguments, and the layer's own logic --
the 428 no-key guard, 400/404 validation, QuotaExceeded -> 429, the 204
"feature off" contract, export headers -- behaves as documented. Application
functions are replaced by recorders, so no network, no YouTube key and no
LLM. Same throwaway-schema setup as test_tags_http.py.
Run with pytest, or directly: python3 tests/test_http_api.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest                                   # noqa: E402
from fastapi.testclient import TestClient       # noqa: E402

import infrastructure.postgres as db             # noqa: E402
import infrastructure.youtube.client as yt        # noqa: E402
import interfaces.http.api as api                 # noqa: E402

# X-NF-Client -- как у настоящих клиентов: без него local_only_guard отвечает 403
# на POST/DELETE без JSON-тела (сама защита -- в test_http_guard.py).
client = TestClient(api.app, headers={"X-NF-Client": "tests"})


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def _no_rate_limit_no_real_key(monkeypatch):
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 0)
    # api.py reads YOUTUBE_API_KEY from a local .env at import; never let a
    # real key reach a stub (or an assertion message). with_key sets a fake.
    monkeypatch.setattr(api, "API_KEY", None)


class _Stub:
    """Stand-in for an application function: records calls, returns (or
    raises) whatever it was given."""

    def __init__(self, result=None, raises=None):
        self.result = {"stub": True} if result is None else result
        self.raises = raises
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.raises:
            raise self.raises
        return self.result

    @property
    def args(self):
        return self.calls[-1][0]

    @property
    def kwargs(self):
        return self.calls[-1][1]


@pytest.fixture
def stub(monkeypatch):
    def make(module_alias, func, **kw):
        s = _Stub(**kw)
        monkeypatch.setattr(getattr(api, module_alias), func, s)
        return s
    return make


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setattr(api, "API_KEY", "TESTKEY")


# ------------------------------------------------------ plain delegations
# (http method, url, module alias in api.py, function, expected args,
#  expected kwargs subset, how the body wraps the stub's result)
DELEGATIONS = [
    ("get", "/api/stats", "Q", "db_stats", (), {}, None),
    ("get", "/api/coverage?period=7d", "trends", "coverage", ("7d",), {}, None),
    ("get", "/api/viral?period=24h&niche=n1&limit=5&exclude_shorts=false",
     "trends", "viral_videos_small_channels", (),
     {"period": "24h", "niche": "n1", "limit": 5, "exclude_shorts": False}, None),
    ("get", "/api/categories?rank_by=channels&min_videos=3", "trends",
     "most_popular_categories", (), {"rank_by": "channels", "min_videos": 3}, None),
    ("get", "/api/keywords?keywords_mode=semantic&semantic_similarity=0.9",
     "trends", "trending_keywords", (),
     {"keywords_mode": "semantic", "semantic_similarity": 0.9, "period": "24h"}, None),
    ("get", "/api/tags/top-by-category?top_n=3", "trends", "top_tags_by_category",
     (), {"top_n": 3, "min_videos": 3}, None),
    ("get", "/api/outlier-channels?min_multiplier=3&max_subscribers=5000", "T",
     "recently_added_outlier_channels", (),
     {"min_multiplier": 3.0, "max_subscribers": 5000, "period_by": "discovered"}, None),
    ("get", "/api/competition?niche=n1", "T", "high_future_competition", (),
     {"niche": "n1", "period": "30d"}, None),
    ("get", "/api/search?query=&niche=n1&sort_by=views", "Q", "search_outliers", (),
     {"query": None, "niche": "n1", "sort_by": "views"}, "results"),
    ("get", "/api/niches", "Q", "list_niches", (), {}, "niches"),
    ("get", "/api/niches/abc?top_n=2", "Q", "niche_overview", ("abc",),
     {"top_n": 2, "period": "all"}, None),
    ("get", "/api/niches/abc/videos?channels=UC1,UC2&include_shorts=false", "Q",
     "niche_videos", ("abc",), {"channel_ids": ["UC1", "UC2"],
                                "include_shorts": False}, None),
    ("get", "/api/niches/abc/videos", "Q", "niche_videos", ("abc",),
     {"channel_ids": None}, None),
    ("get", "/api/channels/tracked?faceless=true&topic=history", "T", "list_tracked",
     (), {"faceless": True, "topic": "history"}, "channels"),
    ("get", "/api/channels/UC1/velocity?limit=3", "T", "channel_velocity", ("UC1",),
     {"limit": 3}, None),
    ("get", "/api/channels/UC1/history", "T", "channel_history", ("UC1",),
     {"limit": 400}, None),
    ("get", "/api/channels/UC1/similar?niche=n1", "Q", "similar_channels", ("UC1",),
     {"niche": "n1", "limit": 10}, None),
    ("get", "/api/channels/UC1/niche-overview?period=90d", "Q",
     "niche_overview_from_channel", ("UC1",), {"period": "90d", "limit": 15}, None),
    ("get", "/api/videos/v1/similar?exclude_same_channel=true", "Q", "similar_videos",
     ("v1",), {"exclude_same_channel": True}, None),
    ("get", "/api/saved?kind=video&folder=ideas", "L", "list_items", (),
     {"kind": "video", "folder": "ideas"}, "items"),
    ("get", "/api/saved/folders", "L", "list_folders", (), {}, "folders"),
    ("delete", "/api/saved/7", "L", "delete_item", (7,), {}, None),
    ("get", "/api/tags/stats?niche=n1&tag_group=format", "TG", "tag_stats",
     ("n1", "format"), {"outlier_threshold": 3.0}, None),
    ("get", "/api/tags/proposed?niche=n1", "TG", "list_proposed_tags", ("n1",), {},
     "proposed"),
    ("post", "/api/enrich/channels?limit=5", "EN", "classify_channels", (),
     {"limit": 5}, None),
    ("post", "/api/enrich/videos", "EN", "tag_new_videos", (), {"limit": 100}, None),
    ("get", "/api/drafts?unpublished_only=true", "MR", "list_drafts", (),
     {"unpublished_only": True}, "drafts"),
    ("get", "/api/drafts/outcomes?min_age_days=3", "MR", "draft_outcomes", (),
     {"min_age_days": 3.0}, "outcomes"),
    ("post", "/api/events/scan", "AL", "scan", (), {}, None),
    ("get", "/api/title-changes?channel_id=UC1", "T", "title_changes", (),
     {"channel_id": "UC1", "period": "7d"}, None),
    ("get", "/api/best-time?timezone_offset_hours=5", "T", "best_time_to_publish", (),
     {"timezone_offset_hours": 5, "period": "90d"}, None),
    ("get", "/api/title-patterns?niche=n1&top_n=4", "T", "title_patterns", (),
     {"niche": "n1", "top_n": 4}, None),
    ("get", "/api/niches/abc/insights?top_n=2", "EN", "niche_comment_insights",
     ("abc",), {"top_n": 2}, None),
    ("get", "/api/transcripts/queue?status=pending", "TR", "list_transcript_queue",
     (), {"status": "pending"}, "queue"),
    ("post", "/api/transcripts/v1/reindex", "TR", "reindex_transcript", ("v1",), {},
     None),
    ("get", "/api/niche-clusters", "NCL", "niche_map", (), {}, None),
    ("post", "/api/niche-clusters/recompute?k=4", "NCL", "compute_clusters", (),
     {"k": 4}, None),
    ("delete", "/api/channels/tracked/UC1", "T", "untrack", ("UC1",), {}, None),
    ("get", "/api/inspect/video?video_id=v1&fetch=false", "I", "inspect_video",
     (None, "v1"), {"fetch": False, "refresh": False}, None),
    ("get", "/api/inspect/channel?ref=@tiny&refresh=true", "I", "inspect_channel",
     (None, "@tiny"), {"refresh": True}, None),
]


@pytest.mark.parametrize("method,url,alias,func,args,kwargs,wrap", DELEGATIONS,
                         ids=[f"{d[0]} {d[1]}" for d in DELEGATIONS])
def test_route_delegates_with_the_right_arguments(stub, monkeypatch, method, url,
                                                   alias, func, args, kwargs, wrap):
    s = stub(alias, func)
    resp = getattr(client, method)(url)
    assert resp.status_code == 200, resp.text
    assert len(s.calls) == 1
    assert s.args == args
    for k, v in kwargs.items():
        assert s.kwargs[k] == v, k
    body = resp.json()
    assert (body[wrap] if wrap else body) == {"stub": True}


# ------------------------------------------------ POST bodies are mapped

def test_metadata_review_and_draft_accept_both_key_styles(stub):
    review = stub("MR", "review_metadata")
    draft = stub("MR", "save_draft")
    client.post("/api/metadata/review", json={"title": "T", "channel_id": "UC1",
                                              "isShort": True})
    assert review.args == ("T",)
    assert review.kwargs["channel_id"] == "UC1" and review.kwargs["is_short"] is True
    assert review.kwargs["period"] == "180d" and review.kwargs["tags"] == []
    client.post("/api/drafts", json={"title": "T", "channelId": "UC2", "review": {"a": 1}})
    assert draft.kwargs["channel_id"] == "UC2" and draft.kwargs["review"] == {"a": 1}


def test_resolve_proposed_tag_maps_camel_and_snake_keys(stub):
    s = stub("TG", "resolve_proposed_tag")
    client.post("/api/tags/proposed/resolve",
                json={"video_id": "v1", "tagGroup": "format", "tag": "list", "accept": 1})
    assert s.args == ("v1", "format", "list", True)


def test_events_list_and_mark_seen(stub):
    events = stub("AL", "list_events", result=[{"id": 1}])
    stub("AL", "unseen_count", result=3)
    body = client.get("/api/events?unseen_only=true&kind=spike").json()
    assert body == {"events": [{"id": 1}], "unseenCount": 3}
    assert events.kwargs == {"unseen_only": True, "kind": "spike", "limit": 100}

    seen = stub("AL", "mark_seen")
    client.post("/api/events/seen", json={})
    assert seen.kwargs == {"ids": None, "all_unseen": True}
    client.post("/api/events/seen", json={"ids": [1, 2]})
    assert seen.kwargs == {"ids": [1, 2], "all_unseen": False}


def test_overview_widens_short_periods_for_the_slow_panels(stub):
    for alias, func in (("trends", "coverage"), ("Q", "db_stats"),
                        ("T", "recently_added_outlier_channels"),
                        ("trends", "trending_keywords"),
                        ("trends", "most_popular_categories"),
                        ("trends", "viral_videos_small_channels")):
        stub(alias, func)
    competition = stub("T", "high_future_competition")
    body = client.get("/api/overview?period=24h&niche=n1").json()
    assert body["widePeriod"] == "30d" and competition.kwargs["period"] == "30d"
    body = client.get("/api/overview?period=90d").json()
    assert body["widePeriod"] == "90d" and competition.kwargs["period"] == "90d"


# ----------------------------------------------- quota-spending endpoints

QUOTA_ROUTES = [
    ("/api/collect/channel", {"channel": "@x"}),
    ("/api/collect/niche", {"query": "q"}),
    ("/api/refresh", {}),
    ("/api/videos/v1/comments", {}),
    ("/api/videos/v1/insights", {}),
]


@pytest.mark.parametrize("url,body", QUOTA_ROUTES, ids=[r[0] for r in QUOTA_ROUTES])
def test_quota_routes_refuse_without_a_key(monkeypatch, url, body):
    monkeypatch.setattr(api, "API_KEY", "")
    resp = client.post(url, json=body)
    assert resp.status_code == 428
    assert "YOUTUBE_API_KEY" in resp.json()["detail"]


def test_collect_channel_validates_passes_and_tracks(stub, with_key):
    assert client.post("/api/collect/channel", json={"channel": "  "}).status_code == 400

    s = stub("collector", "collect_channel", result={"error": "no such channel"})
    resp = client.post("/api/collect/channel", json={"channel": "@nope"})
    assert resp.status_code == 404 and resp.json()["detail"] == "no such channel"

    s = stub("collector", "collect_channel", result={"channelId": "UC1"})
    track = stub("T", "track")
    body = client.post("/api/collect/channel",
                       json={"channel": " @tiny ", "max_videos": "30", "niche": "n1",
                             "track": True, "note": "watch"}).json()
    assert s.args == ("TESTKEY", "@tiny")
    assert s.kwargs == {"max_videos": 30, "niche": "n1"}
    assert track.args == ("UC1", "watch") and body["tracked"] is True


def test_collect_niche_validates_and_passes_options(stub, with_key):
    assert client.post("/api/collect/niche", json={}).status_code == 400
    s = stub("collector", "collect_niche")
    client.post("/api/collect/niche", json={"query": " ai ", "pages": "2", "label": "",
                                            "period": "7d"})
    assert s.args == ("TESTKEY", "ai")
    assert s.kwargs["pages"] == 2 and s.kwargs["label"] is None
    assert s.kwargs["period"] == "7d"


def test_quota_exceeded_becomes_429(stub, with_key):
    stub("collector", "collect_niche", raises=yt.QuotaExceeded("search bucket empty"))
    resp = client.post("/api/collect/niche", json={"query": "q"})
    assert resp.status_code == 429
    assert resp.json()["error"] == "QuotaExceeded"
    assert "search bucket empty" in resp.json()["detail"]


def test_refresh_updates_videos_and_tracked_channels(stub, with_key):
    videos = stub("collector", "refresh_stats", result={"v": 1})
    channels = stub("collector", "refresh_channels", result={"c": 1})
    body = client.post("/api/refresh", json={"period": "7d", "limit": "50"}).json()
    assert body == {"videos": {"v": 1}, "channels": {"c": 1}}
    assert videos.kwargs == {"scope": "recent", "period": "7d", "limit": 50}
    assert channels.kwargs == {"only_tracked": True}


def test_comments_and_insights_pass_their_options(stub, with_key):
    comments = stub("collector", "video_comments")
    client.post("/api/videos/v1/comments", json={"max_results": "20", "order": "time"})
    assert comments.args == ("TESTKEY", "v1")
    assert comments.kwargs == {"max_results": 20, "order": "time"}
    insights = stub("EN", "comment_insights")
    client.post("/api/videos/v1/insights", json={"force_refresh": 1})
    assert insights.kwargs == {"max_comments": 200, "force_refresh": True}


# ------------------------------------------------------- 400 / 404 paths

VALUE_ERROR_ROUTES = [
    ("post", "/api/ideas/check", {"ideas": []}, "Q", "check_ideas"),
    ("post", "/api/saved", {"kind": "bogus"}, "L", "save_item"),
    ("get", "/api/tags?niche=n1", None, "TG", "list_video_tags"),
    ("post", "/api/tags", {"items": []}, "TG", "tag_videos"),
    ("get", "/api/transcripts/search?query=x", None, "TR", "search_transcripts"),
    ("post", "/api/titles/score", {"candidates": []}, "EN", "score_titles"),
    ("post", "/api/titles/suggest", {"topic": ""}, "EN", "suggest_titles"),
    ("get", "/api/niche/n1/export.xlsx", None, "NE", "export_niche"),
]


@pytest.mark.parametrize("method,url,body,alias,func", VALUE_ERROR_ROUTES,
                         ids=[r[1] for r in VALUE_ERROR_ROUTES])
def test_value_error_from_the_application_becomes_400(stub, method, url, body, alias, func):
    stub(alias, func, raises=ValueError("bad input: explained"))
    kw = {"json": body} if body is not None else {}
    resp = getattr(client, method)(url, **kw)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "bad input: explained"


MISSING_FIELD_ROUTES = [
    ("/api/drafts/1/link", {}, "videoId"),
    ("/api/transcripts/request", {"reason": "x"}, "videoId"),
    ("/api/transcripts/v1/save", {"text": "   "}, "text"),
    ("/api/inspect/videos", {"ids": "v1,v2"}, "ids"),
    ("/api/channels/track", {"channel_id": " "}, "channel_id"),
]


@pytest.mark.parametrize("url,body,field", MISSING_FIELD_ROUTES,
                         ids=[r[0] for r in MISSING_FIELD_ROUTES])
def test_missing_or_malformed_field_is_400(url, body, field):
    resp = client.post(url, json=body)
    assert resp.status_code == 400
    assert field in resp.json()["detail"]


def test_valid_bodies_reach_the_application(stub):
    link = stub("MR", "link_draft")
    client.post("/api/drafts/4/link", json={"video_id": "v9"})
    assert link.args == (4, "v9")
    req = stub("TR", "request_transcript")
    client.post("/api/transcripts/request", json={"videoId": "v1", "compareGroup": "a"})
    assert req.args == ("v1",) and req.kwargs["requested_by"] == "dashboard"
    assert req.kwargs["compare_group"] == "a"
    save = stub("TR", "save_transcript")
    client.post("/api/transcripts/v1/save", json={"text": "hello", "language": "en"})
    assert save.args == ("v1", "hello") and save.kwargs == {"language": "en"}
    many = stub("I", "inspect_videos")
    client.post("/api/inspect/videos", json={"ids": ["a", "b"], "fetch": False})
    assert many.args == (None, ["a", "b"]) and many.kwargs == {"fetch": False}


def test_channel_analytics_is_404_when_not_collected(stub):
    stub("T", "channel_analytics", result={"found": False, "hint": "collect it first"})
    resp = client.get("/api/channels/UCmissing")
    assert resp.status_code == 404 and resp.json()["detail"] == "collect it first"
    stub("T", "channel_analytics", result={"found": True, "channelId": "UC1"})
    assert client.get("/api/channels/UC1?period=7d").json()["channelId"] == "UC1"


def test_track_resolves_the_reference_and_404s_when_it_cannot(stub, monkeypatch):
    stub("T", "resolve_channel_id", raises=ValueError("cannot resolve @x"))
    resp = client.post("/api/channels/track", json={"channel_id": "@x"})
    assert resp.status_code == 404 and resp.json()["detail"] == "cannot resolve @x"

    resolve = stub("T", "resolve_channel_id", result="UC1")
    track = stub("T", "track")
    client.post("/api/channels/track", json={"channel_id": " @x ", "note": "n"})
    assert resolve.args[1:] == (None, "@x")
    assert track.args == ("UC1", "n")


# ----------------------------------------------------- response contracts

def test_why_viral_is_204_when_the_llm_is_off(stub):
    stub("EN", "explain_outlier", result={"hint": "set LLM_PROVIDER to enable this"})
    resp = client.get("/api/video/v1/why")
    assert resp.status_code == 204 and resp.content == b""
    s = stub("EN", "explain_outlier", result={"hooks": ["x"]})
    assert client.get("/api/video/v1/why?force_refresh=true").json() == {"hooks": ["x"]}
    assert s.kwargs == {"force_refresh": True}


@pytest.mark.parametrize("fmt,ctype", [("tsv", "text/tab-separated-values"),
                                       ("csv", "text/csv")])
def test_export_sets_type_and_download_name(stub, fmt, ctype):
    stub("NE", "export_niche", result={"content": "a\tb\n", "filename": f"n1.{fmt}"})
    resp = client.get(f"/api/niche/n1/export.{fmt}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == f"{ctype}; charset=utf-8"
    assert resp.headers["content-disposition"] == f'attachment; filename="n1.{fmt}"'
    assert resp.text == "a\tb\n"


def test_static_files_are_not_cached_but_api_is_untouched():
    page = client.get("/")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-cache, must-revalidate"
    assert "cache-control" not in client.get("/api/stats").headers


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
