"""Tests for domain/outlier_traits.py (plan 29): pure comparison, no database.
Run with pytest, or directly: python3 tests/test_outlier_traits_domain.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from domain import outlier_traits as T  # noqa: E402

MON_NOON = "2026-09-07T12:00:00Z"   # a Monday
SAT_NIGHT = "2026-09-05T02:00:00Z"  # a Saturday


def _vid(score, title="plain title", duration=600, when=MON_NOON, tags=5, channel="c"):
    return T.video(title, score, duration=duration, published=when, tags=tags, channel=channel)


def _group(n, score, **kw):
    return [_vid(score, channel=f"c{i}", **kw) for i in range(n)]


def _trait(res, key):
    return next(t for t in res["traits"] if t["key"] == key)


def test_a_share_that_differs_by_enough_points_is_significant_and_signed():
    vids = _group(12, 5.0, title="7 ways to win?") + _group(12, 1.0, title="a plain title")
    res = T.compare(vids)
    for key in ("number", "question"):
        t = _trait(res, key)
        assert t["significant"] and t["outliers"] == 100.0 and t["ordinary"] == 0.0
        assert t["diff"] == 100.0 and t["direction"] == "more"
    assert not _trait(res, "brackets")["significant"] and _trait(res, "brackets")["direction"] is None


def test_a_feature_outliers_lack_comes_out_as_less():
    vids = _group(12, 5.0) + _group(12, 1.0, title="(official) title")
    t = _trait(T.compare(vids), "brackets")
    assert t["significant"] and t["direction"] == "less" and t["diff"] == -100.0


def test_a_gap_under_the_threshold_is_shown_but_not_significant():
    out = _group(10, 5.0, title="7 ways") + _group(10, 5.0)             # 50 %
    reg = _group(8, 1.0, title="7 ways") + _group(12, 1.0)              # 40 %
    t = _trait(T.compare(out + reg), "number")
    assert t["outliers"] == 50.0 and t["ordinary"] == 40.0 and not t["significant"]


def test_medians_compare_as_a_ratio_in_both_directions():
    shorter = T.compare(_group(12, 5.0, duration=20) + _group(12, 1.0, duration=60))
    t = _trait(shorter, "duration")
    assert t["outliers"] == 20 and t["ordinary"] == 60 and t["significant"] and t["direction"] == "less"
    assert t["diff"] == 0.33
    longer = _trait(T.compare(_group(12, 5.0, duration=900) + _group(12, 1.0, duration=600)), "duration")
    assert longer["significant"] and longer["direction"] == "more" and longer["diff"] == 1.5
    close = _trait(T.compare(_group(12, 5.0, duration=620) + _group(12, 1.0, duration=600)), "duration")
    assert not close["significant"]


def test_publishing_time_features_read_utc_and_ignore_unknown_times():
    vids = _group(12, 5.0, when=SAT_NIGHT) + _group(12, 1.0, when=MON_NOON)
    res = T.compare(vids)
    assert _trait(res, "weekend")["outliers"] == 100.0 and _trait(res, "night")["significant"]
    assert _trait(res, "afternoon")["ordinary"] == 100.0
    unknown = T.compare(_group(12, 5.0, when=None) + _group(12, 1.0, when=None))
    assert _trait(unknown, "weekend")["outliers"] is None and _trait(unknown, "weekend")["nOutliers"] == 0


def test_title_features_cover_caps_emoji_and_brackets():
    vids = _group(12, 5.0, title="AUREL [live] 🔥") + _group(12, 1.0, title="aurel live")
    res = T.compare(vids)
    assert all(_trait(res, k)["significant"] for k in ("caps", "emoji", "brackets"))


def test_too_small_a_group_says_so_and_checks_nothing():
    res = T.compare(_group(9, 5.0) + _group(30, 1.0))
    assert not res["reliable"] and res["reason"] == "few-videos" and res["traits"] == []
    assert res["outliers"] == 9 and res["ordinary"] == 30


def test_the_videos_between_the_groups_are_left_out():
    res = T.compare(_group(10, 5.0) + _group(10, 1.0) + _group(50, 2.0))
    assert res["outliers"] == 10 and res["ordinary"] == 10


def test_unscored_videos_are_not_counted():
    res = T.compare(_group(10, 5.0) + _group(10, 1.0) + _group(10, None))
    assert res["outliers"] == 10 and res["ordinary"] == 10


def test_significant_traits_come_first_the_strongest_first():
    vids = (_group(12, 5.0, title="7 ways? (live)", duration=20) +
            _group(12, 1.0, title="plain title", duration=70))
    res = T.compare(vids)
    flags = [t["significant"] for t in res["traits"]]
    assert flags == sorted(flags, reverse=True)
    assert res["traits"][0]["significant"]


def test_outliers_from_one_channel_are_flagged_as_concentrated():
    one = [_vid(5.0, channel="solo") for _ in range(12)] + _group(12, 1.0)
    res = T.compare(one)
    assert res["concentrated"] and res["topChannelShare"] == 1.0 and res["outlierChannels"] == 1
    many = T.compare(_group(12, 5.0) + _group(12, 1.0))
    assert not many["concentrated"] and many["outlierChannels"] == 12


def test_the_thresholds_can_be_moved():
    vids = _group(12, 3.2, title="7 ways") + _group(12, 1.0)
    assert _trait(T.compare(vids), "number")["significant"]
    assert T.compare(vids, outlier_min=4.0)["outliers"] == 0
