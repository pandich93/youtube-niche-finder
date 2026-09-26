"""Tests for infrastructure/youtube/client.py's endpoint wrappers: which path
and parameters each one sends, 50-id batching, paging, and the fallbacks
(batchGetStats -> videos.list, forHandle -> forUsername, comments disabled).
requests.get is replaced by a recorder and the units counter by a no-op, so
there is no network and no database. Retries and unit accounting in _get
itself are covered by test_unit_quota.py.
Run with pytest, or directly: python3 tests/test_youtube_client.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import pytest  # noqa: E402

import infrastructure.youtube.client as yt  # noqa: E402


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


@pytest.fixture
def api(monkeypatch):
    """Returns (sent, replies): every request lands in `sent` as
    (path, params); `replies` is a list of _Resp served in order (an empty
    200 once it runs out)."""
    sent, replies = [], []

    def get(url, params=None, timeout=None):
        sent.append((url.rsplit("/", 1)[1], dict(params)))
        return replies.pop(0) if replies else _Resp()

    monkeypatch.setattr(yt.requests, "get", get)
    monkeypatch.setattr(yt, "_record_units", lambda path: None)
    return sent, replies


def _ids(n, prefix="v"):
    return [f"{prefix}{i}" for i in range(n)]


# ---------------------------------------------------------------- helpers

@pytest.mark.parametrize("raw,seconds", [
    ("PT12M34S", 754), ("PT1H", 3600), ("P1DT2H", 93600), ("PT45S", 45),
    ("P0D", 0), ("", 0), (None, 0), ("garbage", 0),
])
def test_parse_duration(raw, seconds):
    assert yt.parse_duration(raw) == seconds


def test_batched_splits_into_chunks_of_50():
    chunks = list(yt._batched(_ids(120)))
    assert [len(c) for c in chunks] == [50, 50, 20]
    assert list(yt._batched([])) == []


def test_get_drops_none_params_and_adds_the_key(api):
    sent, _ = api
    yt._get("videos", "KEY", id="a", pageToken=None)
    assert sent == [("videos", {"id": "a", "key": "KEY"})]


# ------------------------------------------------------------------ search

def test_search_videos_sends_a_video_search_capped_at_50(api):
    sent, _ = api
    yt.search_videos("KEY", "cold showers", max_results=200, region_code="US")
    path, params = sent[0]
    assert path == "search"
    assert params["q"] == "cold showers" and params["type"] == "video"
    assert params["maxResults"] == 50 and params["regionCode"] == "US"
    assert "channelId" not in params  # None params are dropped


# ------------------------------------------------------------------ videos

def test_videos_list_batches_ids_and_concatenates_items(api):
    sent, replies = api
    replies += [_Resp(payload={"items": [{"id": "a"}]}),
                _Resp(payload={"items": [{"id": "b"}, {"id": "c"}]})]
    items = yt.videos_list("KEY", _ids(60))
    assert [i["id"] for i in items] == ["a", "b", "c"]
    assert [p["id"].count(",") + 1 for _, p in sent] == [50, 10]


def test_batch_get_stats_collects_items_and_failed_ids(api):
    sent, replies = api
    replies.append(_Resp(payload={"items": [{"id": "a"}],
                                  "summary": {"failedVideoIds": ["x"]}}))
    items, failed = yt.videos_batch_get_stats("KEY", ["a", "x"])
    assert sent[0][0] == "videos:batchGetStats"
    assert items == [{"id": "a"}] and failed == ["x"]


def test_batch_get_stats_falls_back_to_videos_list_on_error(api):
    sent, replies = api
    replies += [_Resp(404, text="not deployed here"),
                _Resp(payload={"items": [{"id": "a"}]})]
    items, failed = yt.videos_batch_get_stats("KEY", ["a"])
    assert [path for path, _ in sent] == ["videos:batchGetStats", "videos"]
    assert items == [{"id": "a"}] and failed == []


def test_most_popular_follows_page_tokens_up_to_pages(api):
    sent, replies = api
    replies += [_Resp(payload={"items": [{"id": "a"}], "nextPageToken": "p2"}),
                _Resp(payload={"items": [{"id": "b"}], "nextPageToken": "p3"})]
    items = yt.most_popular("KEY", region_code="DE", pages=2)
    assert [i["id"] for i in items] == ["a", "b"]
    assert sent[0][1]["chart"] == "mostPopular" and sent[0][1]["regionCode"] == "DE"
    assert "pageToken" not in sent[0][1] and sent[1][1]["pageToken"] == "p2"


def test_most_popular_stops_when_there_is_no_next_page(api):
    sent, replies = api
    replies.append(_Resp(payload={"items": [{"id": "a"}]}))
    assert len(yt.most_popular("KEY", pages=5)) == 1
    assert len(sent) == 1


# ---------------------------------------------------------------- channels

def test_channels_list_dedupes_ids_before_batching(api):
    sent, _ = api
    yt.channels_list("KEY", ["c1", "c1", "c2"])
    assert len(sent) == 1
    assert sorted(sent[0][1]["id"].split(",")) == ["c1", "c2"]


def test_channel_by_handle_normalises_the_at_sign(api):
    sent, replies = api
    replies.append(_Resp(payload={"items": [{"id": "UC1"}]}))
    assert yt.channel_by_handle("KEY", "tinylab") == {"id": "UC1"}
    assert sent == [("channels", {"part": yt.CHANNEL_PARTS, "forHandle": "@tinylab",
                                  "key": "KEY"})]


def test_channel_by_handle_falls_back_to_legacy_username(api):
    sent, replies = api
    replies += [_Resp(payload={"items": []}), _Resp(payload={"items": [{"id": "UC2"}]})]
    assert yt.channel_by_handle("KEY", "@oldname") == {"id": "UC2"}
    assert sent[1][1]["forUsername"] == "oldname"


def test_channel_by_handle_returns_none_when_nothing_matches(api):
    _, replies = api
    replies += [_Resp(payload={"items": []}), _Resp(payload={"items": []})]
    assert yt.channel_by_handle("KEY", "@nobody") is None


# ------------------------------------------------------------ playlistItems

def _page(ids, token=None):
    payload = {"items": [{"contentDetails": {"videoId": i, "videoPublishedAt": "t"}}
                         for i in ids]}
    if token:
        payload["nextPageToken"] = token
    return _Resp(payload=payload)


def test_playlist_items_pages_until_max_items(api):
    sent, replies = api
    replies += [_page(_ids(50), "p2"), _page(_ids(20, "w"), "p3")]
    out, calls = yt.playlist_items("KEY", "UU1", max_items=70)
    assert len(out) == 70 and calls == 2
    assert out[0] == {"video_id": "v0", "published_at": "t"}
    # the second page only asks for what is still missing
    assert [p["maxResults"] for _, p in sent] == [50, 20]
    assert sent[1][1]["pageToken"] == "p2"


def test_playlist_items_stops_at_the_last_page_and_skips_items_without_id(api):
    _, replies = api
    page = _page(["a"])
    page._payload["items"].append({"contentDetails": {}})
    replies.append(page)
    out, calls = yt.playlist_items("KEY", "UU1", max_items=200)
    assert [o["video_id"] for o in out] == ["a"] and calls == 1


# ----------------------------------------------------- categories, comments

def test_video_categories_maps_assignable_flag(api):
    _, replies = api
    replies.append(_Resp(payload={"items": [
        {"id": "10", "snippet": {"title": "Music", "assignable": True}},
        {"id": "18", "snippet": {"title": "Short Movies"}},
    ]}))
    assert yt.video_categories("KEY") == [
        {"id": "10", "title": "Music", "assignable": True},
        {"id": "18", "title": "Short Movies", "assignable": False},
    ]


def test_comment_threads_caps_results_at_100(api):
    sent, replies = api
    replies.append(_Resp(payload={"items": [{"id": "t1"}]}))
    assert yt.comment_threads("KEY", "vid", max_results=500) == [{"id": "t1"}]
    assert sent[0][1]["maxResults"] == 100 and sent[0][1]["videoId"] == "vid"


def test_comment_threads_returns_empty_when_comments_are_disabled(api):
    _, replies = api
    replies.append(_Resp(403, text='{"error": {"errors": [{"reason": "commentsDisabled"}]}}'))
    assert yt.comment_threads("KEY", "vid") == []


def test_comment_threads_reraises_other_errors(api):
    _, replies = api
    replies.append(_Resp(404, text="videoNotFound"))
    with pytest.raises(RuntimeError, match="404"):
        yt.comment_threads("KEY", "vid")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-p", "no:cacheprovider"]))
