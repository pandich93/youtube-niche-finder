"""Interface language (i18n plan) for the domain catalogs that reach the
dashboard: the score catalog, plan labels, metadata-review signals, outlier
traits, language names and the where-to-enter terms. In English no Cyrillic
is left in the translated fields; in Russian the output is what it was.
Pure, no DB. Run with pytest, or directly: python3 tests/test_i18n_domain.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

from application import plans as PL  # noqa: E402
from domain import i18n  # noqa: E402
from domain import language_gaps as LG  # noqa: E402
from domain import metadata as MD  # noqa: E402
from domain import niche_ranking as NRK  # noqa: E402
from domain import outlier_traits as OTR  # noqa: E402
from domain import plans as DP  # noqa: E402
from domain import score_catalog as SC  # noqa: E402

CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def _no_cyrillic(value):
    left = [s for s in _strings(value) if CYRILLIC.search(s)]
    assert not left, left


# ------------------------------------------------------------ score catalog

def test_score_catalog_in_russian_is_the_catalog_itself():
    with i18n.use_locale("ru"):
        full = SC.catalog()
        assert full["scores"] == SC.CATALOG
        assert full["sources"] == {SC.YOUTUBE: "данные YouTube API",
                                   SC.ESTIMATE: "оценка niche-finder, не данные YouTube"}
        assert SC.catalog("nicheRank") == {"nicheRank": SC.CATALOG["nicheRank"]}
        # plain strings out, not the template-carrying subclass
        assert all(type(s) is str for s in _strings(full))


def test_score_catalog_in_english_has_no_russian_left_and_keeps_the_numbers():
    with i18n.use_locale("en"):
        full = SC.catalog()
        _no_cyrillic(full)
        assert list(full["scores"]) == list(SC.CATALOG)
        for key, e in full["scores"].items():
            assert list(e) == list(SC.CATALOG[key]) and e["source"] == SC.CATALOG[key]["source"]
        f = full["scores"]["outlierScore"]["formula"]
        assert f"previous {SC.M.DEFAULT_BASELINE_N} uploads" in f
        assert "demand 25" in full["scores"]["nicheRank"]["formula"]
        assert list(SC.catalog("vsr")) == ["vsr"]


# -------------------------------------------------------------------- plans

def test_plan_catalog_labels_and_units():
    with i18n.use_locale("ru"):
        ru = PL.catalog()
        assert [lim["label"] for lim in ru["limits"]] == [s["label"] for s in DP.LIMITS.values()]
        assert [lim["unit"] for lim in ru["limits"]] == [s["unit"] for s in DP.LIMITS.values()]
        assert [f["label"] for f in ru["features"]] == list(DP.FEATURES.values())
    with i18n.use_locale("en"):
        en = PL.catalog()
        _no_cyrillic(en)
        assert [lim["key"] for lim in en["limits"]] == list(DP.LIMITS)
        assert en["limits"][0]["label"] == "YouTube quota per day"


def test_unlimited_plan_name_is_translated_but_admin_names_are_not(monkeypatch):
    monkeypatch.setattr(PL, "usage", lambda _uid: {})
    monkeypatch.setattr(PL, "plan_for", lambda _uid: PL._unlimited())
    with i18n.use_locale("ru"):
        assert PL.summary(1)["plan"]["name"] == "Без ограничений"
    with i18n.use_locale("en"):
        assert PL.summary(1)["plan"]["name"] == "Unlimited"
    monkeypatch.setattr(PL, "plan_for", lambda _uid: PL.Entitlements(7, "Про", {}, {}))
    with i18n.use_locale("en"):
        assert PL.summary(1)["plan"]["name"] == "Про"


def test_plan_messages_and_validation():
    bad = ({"nope": 1, "yt_units": -1, "own_channels": 1.5, "topics": "x"}, {"zzz": True, "llm": 1})
    with i18n.use_locale("ru"):
        assert str(DP.PlanLimitReached("own_channels", 3)) == (
            "ваш тариф позволяет не больше 3 (свои каналы) -- сменить тариф можно на странице «Тариф»")
        assert str(DP.FeatureOff("llm")) == (
            "«Функции с LLM» не входит в ваш тариф -- сменить тариф можно на странице «Тариф»")
        assert DP.validate(*bad) == ["limits.nope: неизвестный лимит",
                                     "limits.yt_units: не может быть меньше 0",
                                     "limits.own_channels: нужно целое число",
                                     "limits.topics: нужно число или null",
                                     "features.zzz: неизвестная функция",
                                     "features.llm: нужно true или false"]
        assert DP.validate([], {}) == ["limits: ожидается объект"]
    with i18n.use_locale("en"):
        assert str(DP.PlanLimitReached("own_channels", 3)) == (
            "your plan allows at most 3 (own channels) -- you can change your plan on the Plan page")
        assert "LLM features" in str(DP.FeatureOff("llm"))
        errors = DP.validate(*bad) + DP.validate([], {}) + DP.validate({}, [])
        assert len(errors) == 8
        _no_cyrillic(errors)


# ----------------------------------------------------------------- metadata

def _metadata_signals():
    return [MD.length_signal("title_length", 40, [30] * 5, "длина заголовка"),
            MD.length_signal("title_length", 40, list(range(20, 60)), "длина заголовка"),
            MD.visible_prefix_signal("x" * 60, key_phrase="рыба"),
            MD.visible_prefix_signal("рыба " + "x" * 60, key_phrase="рыба"),
            MD.tag_overlap(["a" * 300, "b" * 300], ["a" * 300, "рыбалка"]),
            MD.description_signal("0:00 intro " + "x" * 200, key_phrase="рыба"),
            MD.description_signal("рыба", key_phrase="рыба")]


def test_metadata_explanations_in_russian_are_unchanged():
    with i18n.use_locale("ru"):
        s = _metadata_signals()
    assert s[0]["explanation"] == ("длина заголовка: 40 симв.; в базе только 5 выбросов ниши "
                                   "для сравнения (нужно 20+)")
    assert s[1]["explanation"] == ("длина заголовка: 40 симв. против медианы 40 (p25-p75: 30-49) "
                                   "у выбросов ниши, выборка 40")
    assert s[2]["explanation"] == ("первые 45 символов: «" + "x" * 45 + "» -- фраза «рыба» "
                                   "НЕ попадает в видимую часть")
    assert s[3]["explanation"].endswith("-- фраза «рыба» попадает в видимую часть")
    assert s[4]["explanation"] == ("2 тегов, 600/500 символов -- ПРЕВЫШЕН лимит YouTube, лишние "
                                   "теги будут отброшены; из 2 трендовых фраз ниши совпало 1")
    assert s[5]["explanation"] == ("211 симв.; фраза «рыба» отсутствует в первых 150 символах "
                                   "(видно до «ещё»); таймкоды есть")
    assert s[6]["explanation"] == ("4 симв.; фраза «рыба» есть в первых 150 символах "
                                   "(видно до «ещё»); таймкодов нет")
    with i18n.use_locale("ru"):
        assert [f["label"] for f in MD.structural_lift([])] == list(MD.FEATURE_LABELS.values())


def test_metadata_explanations_in_english():
    rows = [{"title": f"Title {i}?", "outlier": 4 if i % 2 else 1} for i in range(30)]
    with i18n.use_locale("en"):
        s = _metadata_signals()
        lift = MD.structural_lift(rows) + MD.structural_lift([])
    # user data (titles, key phrases) stays as typed; our words are English
    _no_cyrillic([x["explanation"].replace("рыбалка", "").replace("рыба", "") for x in s])
    _no_cyrillic([x["label"] for x in lift])


# ----------------------------------------------------------- outlier traits

def _videos():
    out = [OTR.video(f"Top {i}?", 5.0, 600, "2026-10-03T20:00:00Z", 10, f"c{i}") for i in range(12)]
    reg = [OTR.video(f"plain {i}", 1.0, 300, "2026-10-05T08:00:00Z", 3, f"d{i}") for i in range(12)]
    return out + reg


def test_outlier_trait_labels():
    with i18n.use_locale("ru"):
        ru = OTR.compare(_videos())
    by_key = {key: label for key, label, _k, _f in OTR.FEATURES}
    assert ru["reliable"] and all(t["label"] == by_key[t["key"]] for t in ru["traits"])
    with i18n.use_locale("en"):
        en = OTR.compare(_videos())
    assert [t["key"] for t in en["traits"]] == [t["key"] for t in ru["traits"]]
    _no_cyrillic([t["label"] for t in en["traits"]])


# ----------------------------------------------------------- language gaps

def test_language_names_and_thin_corpus_hint():
    with i18n.use_locale("ru"):
        assert LG.lang_label("en") == "английском языке"
        assert LG.lang_label("xx") == "языке «xx»"
        assert LG.corpus_hint(12, "de") == (
            "В базе только 12 видео на немецком языке: «открыто» может значить «не собрано». "
            "Соберите каналы этой ниши на этом языке и проверьте снова.")
    with i18n.use_locale("en"):
        _no_cyrillic([LG.lang_label(code) for code in LG._NAMES] + [LG.lang_label("xx")])
        hint = LG.corpus_hint(12, "de")
        assert hint.startswith("Your database has only 12 videos in German")
        assert LG.corpus_hint(LG.THIN_CORPUS_VIDEOS, "de") is None


# ------------------------------------------------------------ niche ranking

def test_where_to_enter_term_labels():
    m = {"status": "growing", "demandRatio": 1.2, "supplyRatio": 1.0, "newcomersShare": 0.1,
         "rpmMid": 3.0, "templateShare": 0.1, "policyShare": 0.1}
    with i18n.use_locale("ru"):
        ru = NRK.score(m)
    assert [b["label"] for b in ru["breakdown"]] == list(NRK.LABELS.values())
    with i18n.use_locale("en"):
        en = NRK.score(m)
    assert {k: v for k, v in en.items() if k != "breakdown"} == \
        {k: v for k, v in ru.items() if k != "breakdown"}
    _no_cyrillic([b["label"] for b in en["breakdown"]])


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
