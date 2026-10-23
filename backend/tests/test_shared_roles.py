"""Multi-user plan P0-02: the corpus (tags, transcripts) and the server's own
work (refresh, enrichment, alert scan, thumbnail vectors, the niche map) are
shared. With NF_MULTI_USER=1 a regular user may add to the corpus but not
replace or remove what others added, and may not start the server's work by
hand; an admin may do both, by session or API token. Single-user mode is
unchanged. Throwaway schema; no network, zero quota, no model loaded.
Run with pytest, or directly: python3 tests/test_shared_roles.py
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db  # noqa: E402
import interfaces.http.api as api  # noqa: E402
from application import auth as AUTH  # noqa: E402
from application import tags as TG  # noqa: E402
from application import transcripts as TR  # noqa: E402

PW = "correct horse battery"
TEXT = "0:00 first line of the transcript\n0:05 second line of the transcript"

# the server's own work: route -> the use case it starts (stubbed here)
OPS = {
    "/api/enrich/channels": (api.EN, "classify_channels"),
    "/api/enrich/videos": (api.EN, "tag_new_videos"),
    "/api/events/scan": (api.AL, "scan"),
    "/api/thumbnails/embed": (api.TS, "embed_thumbnails"),
    "/api/niche-clusters/recompute": (api.NCL, "compute_clusters"),
    "/api/refresh": (api.collector, "refresh_stats"),
}


def setup_module(_=None):
    db.init_db()


class _Emb:
    def embed(self, texts):
        return [np.zeros(db.EMBEDDING_DIM, dtype=np.float32) for _ in texts]

    def to_blob(self, v):
        return np.asarray(v, dtype=np.float32).tobytes()


def login(email, admin=False):
    uid = AUTH.create_user(email, PW, is_admin=admin)
    c = TestClient(api.app, headers={"X-NF-Client": "tests"})
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
    c.uid = uid
    return c


@pytest.fixture
def ran(monkeypatch):
    started = []
    for mod, attr in OPS.values():
        monkeypatch.setattr(mod, attr, lambda *a, _n=attr, **k: started.append(_n) or {"ok": True})
    monkeypatch.setattr(api.collector, "refresh_channels", lambda *a, **k: {})
    monkeypatch.setattr(api, "API_KEY", "key")
    monkeypatch.setattr(api, "RATE_LIMIT_PER_MINUTE", 0)
    monkeypatch.setattr(TR, "_embeddings", lambda: _Emb())
    conn = db.get_conn()
    for t in ("sessions", "api_tokens", "transcript_requests", "transcript_chunks", "transcripts",
              "video_tags"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("DELETE FROM users WHERE id != 1")
    conn.commit()
    conn.close()
    return started


@pytest.fixture
def people(monkeypatch, ran):
    monkeypatch.setenv("NF_MULTI_USER", "1")
    return login("boss@example.com", admin=True), login("a@example.com"), login("b@example.com")


# ------------------------------------------------------------ the server's own work

@pytest.mark.parametrize("path", sorted(OPS))
def test_ops_routes_need_an_admin(people, ran, path):
    boss, a, _b = people
    r = a.post(path, json={})
    assert r.status_code == 403 and "администратор" in r.json()["detail"]
    assert ran == []                                    # nothing started
    assert boss.post(path, json={}).status_code == 200
    assert ran                                          # the admin's call ran


@pytest.mark.parametrize("path", sorted(OPS))
def test_an_admins_api_token_may_run_them(people, monkeypatch, path):
    boss, _a, _b = people
    monkeypatch.setattr(api, "_feature_block", lambda request, user_id: None)  # roles, not tariffs
    token = AUTH.create_api_token(boss.uid, "ops")["token"]
    bare = TestClient(api.app, headers={"X-NF-Client": "tests", "Authorization": f"Bearer {token}"})
    assert bare.post(path, json={}).status_code == 200


@pytest.mark.parametrize("path", sorted(OPS))
def test_single_user_mode_is_unchanged(ran, monkeypatch, path):
    monkeypatch.delenv("NF_MULTI_USER", raising=False)
    c = TestClient(api.app, headers={"X-NF-Client": "tests"})
    assert c.post(path, json={}).status_code == 200


def test_the_refusal_speaks_the_request_language(people):
    _boss, a, _b = people
    r = a.post("/api/events/scan", headers={"X-NF-Locale": "en"})
    assert r.status_code == 403 and r.json()["detail"].startswith("The background collector")


# ------------------------------------------------------------ shared transcripts

def _transcript(video_id):
    conn = db.get_conn()
    row = conn.execute("SELECT text, created_by FROM transcripts WHERE video_id = ?",
                       (video_id,)).fetchone()
    conn.close()
    return row


def test_only_the_author_or_an_admin_replaces_a_transcript(people):
    boss, a, b = people
    assert a.post("/api/transcripts/vrole1/save", json={"text": TEXT}).json()["status"] == "ready"
    r = b.post("/api/transcripts/vrole1/save", json={"text": "0:00 junk from b"}).json()
    assert r["status"] == "ready" and r["alreadyExists"] is True and r["chunks"] >= 1
    assert _transcript("vrole1")["text"] == TEXT and _transcript("vrole1")["created_by"] == a.uid
    conn = db.get_conn()
    mine = conn.execute("SELECT status FROM transcript_requests WHERE video_id = 'vrole1' "
                        "AND user_id = ?", (b.uid,)).fetchone()
    conn.close()
    assert mine["status"] == "ready"                    # b's own request is answered
    more = TEXT + "\n0:09 a third line"
    assert "alreadyExists" not in a.post("/api/transcripts/vrole1/save", json={"text": more}).json()
    assert _transcript("vrole1")["text"] == more        # the author may replace it
    assert "alreadyExists" not in boss.post("/api/transcripts/vrole1/save",
                                            json={"text": TEXT}).json()
    after = _transcript("vrole1")
    assert after["text"] == TEXT and after["created_by"] == a.uid      # the author stays


def test_reindex_is_for_the_author_or_an_admin(people):
    boss, a, b = people
    a.post("/api/transcripts/vrole2/save", json={"text": TEXT})
    assert b.post("/api/transcripts/vrole2/reindex").status_code == 403
    assert a.post("/api/transcripts/vrole2/reindex").json()["status"] == "ready"
    assert boss.post("/api/transcripts/vrole2/reindex").json()["status"] == "ready"


def test_operator_transcripts_belong_to_admins(people):
    # older rows have no author (NULL): only an admin replaces them
    _boss, a, _b = people
    TR.save_transcript("vrole3", TEXT)                  # the operator, as before
    conn = db.get_conn()
    conn.execute("UPDATE transcripts SET created_by = NULL WHERE video_id = 'vrole3'")
    conn.commit()
    conn.close()
    assert a.post("/api/transcripts/vrole3/save", json={"text": "0:00 x"}).json()["alreadyExists"]


# ------------------------------------------------------------ shared tags

def _tags(video_id="vtag1", group="theme"):
    return sorted(t["tag"] for t in TG.list_video_tags(video_id=video_id)
                  if t["tagGroup"] == group)


def _item(tags, video_id="vtag1", **extra):
    return {"items": [{"video_id": video_id, "tag_group": "theme", "tags": tags}],
            "source": "claude-mcp", **extra}


def test_replace_removes_only_the_users_own_tags(people):
    boss, a, b = people
    assert a.post("/api/tags", json=_item(["space", "physics"])).status_code == 200
    assert b.post("/api/tags", json=_item(["history"])).status_code == 200      # adding is fine
    assert _tags() == ["history", "physics", "space"]
    b.post("/api/tags", json=_item(["myth"], replace=True))
    assert _tags() == ["myth", "physics", "space"]                              # a's tags stay
    boss.post("/api/tags", json=_item(["only"], replace=True))
    assert _tags() == ["only"]                                                  # an admin's replace


def test_repeating_someone_elses_tag_does_not_take_it_over(people):
    # re-writing an existing row would also reset `proposed`: a user could
    # quietly accept the worker's proposal just by repeating the tag
    _boss, _a, b = people
    conn = db.get_conn()
    db.upsert_video_tag(conn, "vtag2", "theme", "maybe", "llm", proposed=True)   # the worker's
    conn.commit()
    conn.close()
    b.post("/api/tags", json={**_item(["maybe"], video_id="vtag2"), "source": "llm"})
    conn = db.get_conn()
    row = conn.execute("SELECT proposed, created_by FROM video_tags WHERE video_id = 'vtag2'").fetchone()
    conn.close()
    assert row["proposed"] == 1 and row["created_by"] is None


def test_resolving_the_workers_proposals_is_for_admins(people):
    boss, a, _b = people
    conn = db.get_conn()
    for tag in ("p1", "p2"):
        db.upsert_video_tag(conn, "vtag3", "theme", tag, "llm", proposed=True)
    conn.commit()
    conn.close()
    body = {"videoId": "vtag3", "tagGroup": "theme", "tag": "p1", "accept": False}
    assert a.post("/api/tags/proposed/resolve", json=body).json()["found"] is False
    assert boss.post("/api/tags/proposed/resolve", json=body).json()["found"] is True
    assert _tags("vtag3") == ["p2"]


def test_single_user_mode_still_replaces_everything(ran, monkeypatch):
    monkeypatch.delenv("NF_MULTI_USER", raising=False)
    TG.tag_videos([{"video_id": "vtag4", "tag_group": "theme", "tags": ["old"]}], "claude-mcp",
                  user_id=99, may_overwrite=False)                              # someone's old row
    c = TestClient(api.app, headers={"X-NF-Client": "tests"})
    c.post("/api/tags", json=_item(["new"], video_id="vtag4", replace=True))
    assert _tags("vtag4") == ["new"]


# ------------------------------------------------------------ MCP

def test_mcp_admin_tools_refuse_a_regular_token(people, monkeypatch):
    from application import plans as PL
    from interfaces.mcp import auth as MA
    real = PL.plan_for

    def everything(uid):                                # roles, not tariffs
        ent = real(uid)
        return type(ent)(ent.plan_id, ent.name, ent.limits, {k: True for k in ent.features})

    monkeypatch.setattr(PL, "plan_for", everything)
    boss, a, _b = people
    for tool in sorted(MA.MCP_ADMIN_TOOLS):
        assert MA.blocked_feature(a.uid, "tools/call", {"name": tool}) == "admin", tool
        assert MA.blocked_feature(boss.uid, "tools/call", {"name": tool}) is None, tool
    assert MA.blocked_feature(a.uid, "tools/call", {"name": "list_niches"}) is None
    assert MA.blocked_feature(a.uid, "tools/call", {"name": "tag_videos"}) is None


def test_mcp_refusal_is_admin_only_not_a_tariff(people, monkeypatch):
    import anyio
    from mcp.server.auth.provider import AccessToken

    from domain.plans import AdminOnly
    from interfaces.mcp import auth as MA
    _boss, a, _b = people
    monkeypatch.setattr(MA, "get_access_token",
                        lambda: AccessToken(token="nf_x", client_id=str(a.uid), scopes=[MA.SCOPE]))
    monkeypatch.setattr(MA, "blocked_feature", lambda uid, method, params: "admin")

    class Ctx:
        method, params = "tools/call", {"name": "scan_for_alerts"}

    async def call_next(_ctx):
        return "ran"

    with pytest.raises(AdminOnly):
        anyio.run(MA.quota_owner_middleware, Ctx(), call_next)


def test_mcp_tag_tools_pass_the_tokens_rights(people, monkeypatch):
    from mcp.server.auth.provider import AccessToken

    import interfaces.mcp.server as srv
    from interfaces.mcp import auth as MA
    boss, a, _b = people
    seen = []
    monkeypatch.setattr(srv.tags_uc, "tag_videos",
                        lambda items, **k: seen.append((k["user_id"], k["may_overwrite"])) or {})
    for who in (a, boss):
        monkeypatch.setattr(MA, "get_access_token",
                            lambda who=who: AccessToken(token="nf", client_id=str(who.uid),
                                                        scopes=[MA.SCOPE]))
        srv.tag_videos([], "claude-mcp")
    assert seen == [(a.uid, False), (boss.uid, True)]


def test_mcp_over_stdio_is_the_local_operator(monkeypatch):
    from interfaces.mcp import auth as MA
    monkeypatch.setattr(MA, "get_access_token", lambda: None)
    assert MA.may_overwrite() is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
