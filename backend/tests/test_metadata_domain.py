"""Tests for domain/metadata.py -- pure functions, no DB, no key.
Run: python3 tests/test_metadata_domain.py (or pytest)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import domain.metadata as MD  # noqa: E402


def test_percentiles_basic():
    p = MD.percentiles([10, 20, 30, 40, 50])
    assert p["median"] == 30 and p["sample"] == 5
    assert p["p25"] <= p["median"] <= p["p75"]


def test_percentiles_empty_is_none():
    assert MD.percentiles([None, None]) is None
    assert MD.percentiles([]) is None


def test_length_signal_unreliable_under_min_sample():
    sig = MD.length_signal("title_length", 60, [40, 50, 60], "длина заголовка")
    assert sig["verdict"] == "unreliable"
    assert sig["value"] == 60


def test_length_signal_ok_inside_band():
    baseline = list(range(30, 30 + 40))  # 40 samples, p25-p75 comfortably covers 50
    sig = MD.length_signal("title_length", 50, baseline, "длина заголовка")
    assert sig["verdict"] == "ok", sig
    assert sig["baseline"]["sample"] == 40


def test_length_signal_warn_outside_band():
    baseline = list(range(30, 30 + 40))
    sig = MD.length_signal("title_length", 200, baseline, "длина заголовка")
    assert sig["verdict"] == "warn", sig


def test_visible_prefix_flags_truncation_and_missing_phrase():
    long_title = "Это очень длинный заголовок который точно обрежется в выдаче мобильной"
    sig = MD.visible_prefix_signal(long_title, key_phrase="обрежется")
    assert sig["truncated"] is True
    assert sig["verdict"] == "warn"  # "обрежется" is past char 45

    sig2 = MD.visible_prefix_signal("Короткий и ясный заголовок", key_phrase="Короткий")
    assert sig2["truncated"] is False
    assert sig2["verdict"] == "ok"


def test_structural_features_detects_each():
    f = MD.structural_features("ТОП 5 способов (не поверишь)?")
    assert f["has_digit"] and f["has_brackets"] and f["has_question"] and f["has_caps_word"]
    assert not MD.structural_features("обычный заголовок без ничего")["has_digit"]


def test_structural_lift_unreliable_with_few_rows():
    rows = [{"title": "5 способов", "outlier": 5.0}] * 3
    lift = MD.structural_lift(rows)
    digit = next(x for x in lift if x["feature"] == "has_digit")
    assert digit["verdict"] == "unreliable"


def test_structural_lift_computes_with_enough_rows():
    rows = []
    for i in range(30):
        rows.append({"title": f"{i} способов", "outlier": 5.0})   # has digit, outlier
    for i in range(30):
        rows.append({"title": "способов без цифры", "outlier": 0.5})  # no digit, not outlier
    lift = MD.structural_lift(rows, outlier_threshold=3.0)
    digit = next(x for x in lift if x["feature"] == "has_digit")
    assert digit["verdict"] == "ok"
    assert digit["lift"] > 1.5, digit  # digit strongly correlates with outlier here


def test_tag_overlap_matches_and_flags_over_limit():
    draft = ["ai", "нейросети"]
    trending = ["AI", "автоматизация", "нейросети"]
    res = MD.tag_overlap(draft, trending)
    assert res["matchedTrending"] == ["ai", "нейросети"]
    assert "автоматизация" in res["missingTrending"]
    assert res["verdict"] == "ok"

    huge = ["x" * 100] * 6  # 600 chars > 500 limit
    res2 = MD.tag_overlap(huge, [])
    assert res2["verdict"] == "warn"


def test_description_signal_unreliable_when_empty():
    sig = MD.description_signal("")
    assert sig["verdict"] == "unreliable"


def test_description_signal_detects_timestamps_and_key_phrase():
    desc = "В этом видео: 00:00 вступление, 01:30 нейросети в деле"
    sig = MD.description_signal(desc, key_phrase="нейросети")
    assert sig["hasTimestamps"] is True
    assert sig["verdict"] == "ok"

    sig2 = MD.description_signal("Просто описание без ключевой фразы в начале " + "х" * 200,
                                 key_phrase="нейросети")
    assert sig2["verdict"] == "warn"


def test_summarize_counts_verdicts():
    signals = [{"verdict": "ok"}, {"verdict": "ok"}, {"verdict": "warn"}, {"verdict": "unreliable"}]
    s = MD.summarize(signals)
    assert s == {"ok": 2, "warn": 1, "unreliable": 1, "total": 4}


def _run_all():
    ns = dict(globals())
    tests = [(name, fn) for name, fn in ns.items() if name.startswith("test_")]
    passed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok  {name}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {name}: {e}")
        except Exception as e:  # pragma: no cover
            print(f"ERROR {name}: {e!r}")
    print(f"\n{passed}/{len(tests)} прошло")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
