"""Tariff routes: a user sees and chooses plans; only an admin signed in to
the dashboard edits plans and assigns them. Throwaway schema; no network.
Run with pytest, or directly: python3 tests/test_plan_routes.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import interfaces.http.api as api  # noqa: E402
from application import auth as AUTH  # noqa: E402

PW = "correct horse battery"


def setup_module(_=None):
    db.init_db()


def login(email, admin=False):
    uid = AUTH.create_user(email, PW, is_admin=admin)
    c = TestClient(api.app, headers={"X-NF-Client": "tests"})
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
    c.uid = uid
    return c


@pytest.fixture
def people(monkeypatch):
    monkeypatch.setenv("NF_MULTI_USER", "1")
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 0)
    conn = db.get_conn()
    for t in ("plan_changes", "user_plans", "sessions", "api_tokens"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM plans WHERE slug != 'free'")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.commit()
    conn.close()
    return login("boss@example.com", admin=True), login("a@example.com")


PRO = {"slug": "pro", "name": "Pro", "price": 0, "periodDays": 30,
       "limits": {"tracked_channels": 5}, "features": {"export": False}}


def test_admin_creates_user_chooses(people):
    boss, a = people
    r = boss.post("/api/admin/plans", json=PRO)
    assert r.status_code == 200
    pro = r.json()
    assert a.get("/api/auth/me").json()["plan"]["plan"]["name"] == "Free"
    page = a.get("/api/plan").json()
    assert {p["slug"] for p in page["plans"]} == {"free", "pro"}
    assert {x["key"] for x in page["catalog"]["features"]} >= {"export", "llm"}
    r = a.post("/api/plan/choose", json={"planId": pro["id"]})
    assert r.status_code == 200 and r.json()["plan"]["name"] == "Pro"


def test_paid_plan_needs_admin(people):
    boss, a = people
    paid = boss.post("/api/admin/plans", json={**PRO, "slug": "paid", "price": 9}).json()
    assert a.post("/api/plan/choose", json={"planId": paid["id"]}).status_code == 400
    r = boss.put(f"/api/admin/users/{a.uid}/plan", json={"planId": paid["id"], "expiresAt": None})
    assert r.status_code == 200
    assert a.get("/api/plan").json()["plan"]["name"] == "Pro"


def test_admin_routes_closed_to_users_and_tokens(people):
    boss, a = people
    assert a.get("/api/admin/plans").status_code == 403
    assert a.post("/api/admin/plans", json=PRO).status_code == 403
    token = AUTH.create_api_token(boss.uid, "x")["token"]
    bare = TestClient(api.app, headers={"X-NF-Client": "tests", "Authorization": f"Bearer {token}"})
    assert bare.get("/api/admin/plans").status_code == 403


def test_admin_validation_and_archive(people):
    boss, _ = people
    r = boss.post("/api/admin/plans", json={**PRO, "limits": {"yt_units": -1}})
    assert r.status_code == 400 and "limits.yt_units" in r.json()["detail"]
    pro = boss.post("/api/admin/plans", json=PRO).json()
    assert boss.post(f"/api/admin/plans/{pro['id']}/archive").json()["archived"] is True
    assert boss.post(f"/api/admin/plans/{pro['id']}/restore").json()["archived"] is False
    free = next(p for p in boss.get("/api/admin/plans").json()["plans"] if p["slug"] == "free")
    assert boss.post(f"/api/admin/plans/{free['id']}/archive").status_code == 400
    users = boss.get("/api/admin/users").json()["users"]
    assert {u["email"] for u in users} >= {"boss@example.com", "a@example.com"}


def test_user_cannot_touch_another_users_plan(people):
    _, a = people
    b = login("b@example.com")
    assert a.put(f"/api/admin/users/{b.uid}/plan", json={"planId": 1}).status_code == 403


def test_single_user_mode(monkeypatch):
    monkeypatch.setenv("NF_MULTI_USER", "0")
    c = TestClient(api.app, headers={"X-NF-Client": "tests"})
    assert c.get("/api/plan").status_code == 409
    assert c.get("/api/admin/plans").status_code == 409


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
