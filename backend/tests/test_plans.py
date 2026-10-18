"""Tariffs in the application layer: whose plan applies, choosing, admin
assignment, expiry back to the base plan, count checks and admin edits.
Throwaway schema; no network.
Run with pytest, or directly: python3 tests/test_plans.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
from application import auth as AUTH  # noqa: E402
from application import plans as PL  # noqa: E402
from domain import plans as DP  # noqa: E402

PW = "correct horse battery"
NOW = datetime.now(timezone.utc)


def setup_module(_=None):
    db.init_db()


@pytest.fixture(autouse=True)
def world(monkeypatch):
    monkeypatch.setenv("NF_MULTI_USER", "1")
    monkeypatch.setenv("NF_USER_DAILY_UNITS", "2000")
    conn = db.get_conn()
    for t in ("plan_changes", "user_plans", "tracked_channels", "sessions"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM plans WHERE slug != 'free'")
    conn.execute("UPDATE plans SET is_base = 1, limits = '{}'::jsonb, features = '{}'::jsonb "
                 "WHERE slug = 'free'")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.commit()
    conn.close()


def user(email="a@example.com", admin=False):
    return AUTH.create_user(email, PW, is_admin=admin)


def pro(**over):
    data = {"slug": "pro", "name": "Pro", "price": 0, "periodDays": 30,
            "limits": {"tracked_channels": 2, "yt_units": 5000},
            "features": {"export": False}, "isPublic": True}
    data.update(over)
    return PL.create_plan(data)


def test_new_user_is_on_free_with_env_defaults():
    ent = PL.plan_for(user())
    assert ent.name == "Free" and ent.limits["yt_units"] == 2000
    assert ent.limits["tracked_channels"] is None and ent.features["export"] is True


def test_single_user_mode_gets_defaults(monkeypatch):
    monkeypatch.setenv("NF_MULTI_USER", "0")
    ent = PL.plan_for(1)
    assert not ent.unlimited and ent.limits["tracked_channels"] is None


def test_admin_and_system_are_unlimited():
    assert PL.plan_for(user("boss@example.com", admin=True)).unlimited
    assert PL.plan_for(0).unlimited


def test_choose_sets_expiry_and_logs():
    uid, p = user(), pro()
    PL.choose(uid, p["id"])
    ent = PL.plan_for(uid)
    assert ent.name == "Pro" and ent.limits["tracked_channels"] == 2
    assert ent.features["export"] is False
    exp = datetime.fromisoformat(ent.expires_at)
    assert timedelta(days=29) < exp - NOW < timedelta(days=31)
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT changed_by, to_plan_id FROM plan_changes WHERE user_id = ?",
                           (uid,)).fetchone()
    finally:
        conn.close()
    assert row["changed_by"] == "self" and row["to_plan_id"] == p["id"]


def test_choose_refuses_paid_hidden_and_archived():
    uid = user()
    with pytest.raises(PL.PlanError):
        PL.choose(uid, pro(slug="paid", price=9)["id"])
    with pytest.raises(PL.PlanError):
        PL.choose(uid, pro(slug="hidden", isPublic=False)["id"])
    arch = pro(slug="old")
    PL.set_archived(arch["id"], True)
    with pytest.raises(PL.PlanError):
        PL.choose(uid, arch["id"])


def test_admin_assigns_paid_plan():
    admin, uid = user("boss@example.com", admin=True), user()
    paid = pro(slug="paid", price=9)
    PL.assign(admin, uid, paid["id"], None)
    ent = PL.plan_for(uid)
    assert ent.name == "Pro" and ent.expires_at is None


def test_expired_plan_falls_back_then_expire_due_records_it():
    uid, p = user(), pro()
    PL.choose(uid, p["id"])
    conn = db.get_conn()
    conn.execute("UPDATE user_plans SET expires_at = ? WHERE user_id = ?",
                 ((NOW - timedelta(minutes=1)).isoformat(), uid))
    conn.commit()
    conn.close()
    assert PL.plan_for(uid).name == "Free"                  # at once, before the worker
    assert PL.expire_due() == 1
    assert PL.expire_due() == 0
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT up.expires_at, p.slug FROM user_plans up JOIN plans p "
                           "ON p.id = up.plan_id WHERE up.user_id = ?", (uid,)).fetchone()
        log = [r["changed_by"] for r in conn.execute(
            "SELECT changed_by FROM plan_changes WHERE user_id = ? ORDER BY id", (uid,)).fetchall()]
    finally:
        conn.close()
    assert row["slug"] == "free" and row["expires_at"] is None
    assert log == ["self", "system"]


def test_switching_base_moves_the_flag_and_users_follow():
    uid = user()
    p = pro(periodDays=None)
    PL.update_plan(p["id"], {**p, "isBase": True})
    plans = {x["slug"]: x for x in PL.list_plans()}
    assert plans["pro"]["isBase"] and not plans["free"]["isBase"]
    assert PL.plan_for(uid).name == "Pro"


def test_base_rules():
    free = next(x for x in PL.list_plans() if x["slug"] == "free")
    with pytest.raises(PL.PlanError):
        PL.set_archived(free["id"], True)
    with pytest.raises(PL.PlanError):
        PL.update_plan(free["id"], {**free, "isBase": False})
    with pytest.raises(PL.PlanError):
        PL.update_plan(free["id"], {**free, "periodDays": 30})
    with pytest.raises(PL.PlanError, match="базовый тариф бесплатный"):
        PL.update_plan(free["id"], {**free, "price": 5})


def test_bad_input_lists_fields():
    with pytest.raises(PL.PlanError) as e:
        PL.create_plan({"slug": "Плохой", "name": "", "price": -1,
                        "limits": {"yt_units": -5}, "features": {"llm": 1}})
    msg = str(e.value)
    for field in ("name", "slug", "price", "limits.yt_units", "features.llm"):
        assert field in msg


def test_ensure_room_counts_rows():
    uid, p = user(), pro()
    PL.choose(uid, p["id"])
    conn = db.get_conn()
    for ch in ("UCa", "UCb"):
        db.track_channel(conn, ch, None, user_id=uid)
    conn.commit()
    conn.close()
    with pytest.raises(DP.PlanLimitReached) as e:
        PL.ensure_room(uid, "tracked_channels")
    assert e.value.max == 2
    PL.ensure_room(uid, "saved_items")                       # no limit on this one


def test_ensure_feature():
    uid = user()
    PL.choose(uid, pro()["id"])
    with pytest.raises(DP.FeatureOff):
        PL.ensure_feature(uid, "export")
    PL.ensure_feature(uid, "llm")


def test_missing_base_falls_back_to_defaults():
    uid = user()
    conn = db.get_conn()
    conn.execute("UPDATE plans SET is_base = 0")
    conn.commit()
    conn.close()
    ent = PL.plan_for(uid)
    assert ent.limits["yt_units"] == 2000 and ent.features["llm"] is True


def test_summary_and_users_list():
    uid = user()
    s = PL.summary(uid)
    assert s["plan"]["name"] == "Free" and s["usage"]["counts"]["tracked_channels"] == 0
    rows = {r["email"]: r for r in PL.list_users()}
    assert rows["a@example.com"]["plan"]["name"] == "Free"
    assert "ytUnits" in rows["a@example.com"]["today"]


def test_creating_a_base_plan_clears_the_flag_on_free():
    p = PL.create_plan({"slug": "newbase", "name": "New base", "isBase": True, "periodDays": None})
    plans = {x["slug"]: x for x in PL.list_plans()}
    assert plans["newbase"]["isBase"] and p["isBase"]
    assert not plans["free"]["isBase"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))


@pytest.mark.parametrize("bad", ["nan", float("nan"), float("inf"), "inf", 1e9, -1])
def test_unstorable_prices_are_refused(bad):
    with pytest.raises(PL.PlanError, match="price"):
        PL.create_plan({"slug": "badprice", "name": "Bad", "price": bad})
    assert not any(p["slug"] == "badprice" for p in PL.list_plans())


def test_plan_dicts_carry_the_effective_limits_and_features():
    free = next(p for p in PL.public_plans() if p["slug"] == "free")
    assert set(free["effectiveLimits"]) == set(DP.LIMITS)
    assert set(free["effectiveFeatures"]) == set(DP.FEATURES)


def test_a_malformed_stored_plan_is_not_usable():
    free = next(p for p in PL.list_plans() if p["slug"] == "free")
    row = {"id": 1, "slug": "x", "name": "x", "description": "", "price": 0, "currency": "USD",
           "period_days": None, "is_base": 1, "is_public": 1, "archived_at": None,
           "sort_order": 0, "limits": [1, 2], "features": "oops"}
    assert not PL._usable(PL._shape(row))
    assert free
