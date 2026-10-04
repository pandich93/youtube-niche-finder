"""Your own channels (plan 14): connect them through Google OAuth and pull
their real numbers from the YouTube Analytics API -- views, watch time,
retention (average view percentage), subscribers gained and, with the
monetary scope on a monetized channel, revenue, CPM and RPM -- to set them
next to the niche and to our public-data RPM estimate.

Everything here is PERSONAL (plan 15): rows carry user_id and every function
takes user_id. The OAuth refresh token is stored Fernet-encrypted
(infrastructure/secrets.py) and never returned, logged or put in an error.
Impressions and thumbnail click-through rate are not in the Analytics API
(YouTube Studio only), so they are not shown.
"""
import os
from datetime import date, timedelta

import infrastructure.postgres as db
import infrastructure.secrets as SEC
from application import own_formats as OF
from domain import metrics as M
from domain import own_metrics as OM
from domain import periods as P
from domain.users import LOCAL_USER_ID
from infrastructure.youtube import analytics as YA
from infrastructure.youtube import oauth as OA

CLIENT_ID_ENV, CLIENT_SECRET_ENV = "OWN_OAUTH_CLIENT_ID", "OWN_OAUTH_CLIENT_SECRET"
REDIRECT_ENV = "OWN_OAUTH_REDIRECT_URI"
# plan 25: "desktop" -- each installation's owner brings an OAuth client of
# type Desktop app with a loopback redirect (plan 14); "web" -- a service
# with ONE client of type Web application on its own https domain, which
# its customers connect through without touching Google Cloud
MODE_ENV = "OWN_OAUTH_MODE"
DEFAULT_REDIRECT = "http://127.0.0.1:8080/api/own/oauth/callback"
STATE_TTL_MINUTES = 10
LAG_DAYS = 3                 # Analytics data arrives 1-3 days late
WINDOW_DAYS = 28
MAX_VIDEOS = 200
FIRST_DAY = "2005-02-14"     # YouTube's first day, for a channel with no known start
NOTE = ("Real numbers from YouTube Analytics for your channel. Thumbnail impressions and "
        "click-through rate (CTR) are not in the API -- only YouTube Studio shows them.")


class NotConfigured(RuntimeError):
    pass


class ConnectError(RuntimeError):
    pass


class NotConnected(LookupError):
    pass


# ---------------------------------------------------------- configuration

def _client():
    return (os.environ.get(CLIENT_ID_ENV, "").strip(), os.environ.get(CLIENT_SECRET_ENV, "").strip())


def _redirect_uri():
    return os.environ.get(REDIRECT_ENV, "").strip() or DEFAULT_REDIRECT


def mode() -> str:
    return "web" if os.environ.get(MODE_ENV, "").strip().lower() == "web" else "desktop"


def _problems() -> list:
    """Settings that are present but wrong: in web mode the redirect must be
    the service's own https callback (localhost is fine for a dev setup)."""
    if mode() != "web":
        return []
    uri = os.environ.get(REDIRECT_ENV, "").strip()
    host_ok = uri.startswith("https://") or uri.startswith(("http://localhost", "http://127.0.0.1"))
    if not uri or not host_ok or not uri.endswith("/api/own/oauth/callback"):
        return [f"{REDIRECT_ENV} must be https://<your domain>/api/own/oauth/callback in web mode, "
                "registered as an authorized redirect URI of the Web application client"]
    return []


def status(user_id: int = LOCAL_USER_ID) -> dict:
    cid, secret = _client()
    missing = [name for name, ok in ((CLIENT_ID_ENV, cid), (CLIENT_SECRET_ENV, secret),
                                     (SEC.ENV, SEC.configured())) if not ok]
    problems = _problems()
    conn = db.get_conn()
    try:
        n = conn.execute("SELECT COUNT(*) FROM own_channels WHERE user_id = ?", (user_id,)).fetchone()[0]
    finally:
        conn.close()
    return {"configured": not missing and not problems, "missing": missing, "problems": problems,
            "mode": mode(), "redirectUri": _redirect_uri(), "connectedChannels": n}


def _require_configured(connecting: bool = True):
    """connecting=False (a sync of channels already connected) needs the
    client and the key but not the redirect: a wrong redirect in web mode
    must not stop the daily numbers."""
    st = status()
    if not st["configured"] and (connecting or st["missing"]):
        what = ", ".join(st["missing"]) if st["missing"] else "; ".join(st["problems"])
        raise NotConfigured(("set " if st["missing"] else "") + what
                            + " -- see backend/README.md \"Your own channels\"")


# ---------------------------------------------------------- connect

def start_connect(user_id: int = LOCAL_USER_ID, redirect_uri: str = None,
                  include_revenue: bool = None) -> dict:
    """A Google consent URL for this user. The state is single-use and
    expires in STATE_TTL_MINUTES; the PKCE verifier stays on the server.
    redirect_uri: the loopback address the dashboard is open on (the HTTP
    layer passes it), so the browser comes back to the same host name its
    state cookie belongs to; OWN_OAUTH_REDIRECT_URI, when set, wins.
    include_revenue: also ask for the monetary scope -- by default yes for
    one's own installation, no in web mode (plan 25: asked separately)."""
    _require_configured()
    redirect = os.environ.get(REDIRECT_ENV, "").strip() or redirect_uri or DEFAULT_REDIRECT
    if include_revenue is None:
        include_revenue = mode() != "web"
    scopes = OA.SCOPES if include_revenue else OA.BASE_SCOPES
    verifier, challenge = OA.pkce_pair()
    state = OA.new_state()
    conn = db.get_conn()
    try:
        conn.execute("DELETE FROM own_oauth_pending WHERE created_at::timestamptz < now() - "
                     "(? || ' minutes')::interval", (str(STATE_TTL_MINUTES),))
        conn.execute("INSERT INTO own_oauth_pending (state, user_id, code_verifier, created_at, "
                     "redirect_uri) VALUES (?,?,?,?,?)",
                     (state, user_id, verifier, db.now_iso(), redirect))
        conn.commit()
    finally:
        conn.close()
    # `state` is for the HTTP layer to bind to the browser that started this
    # (a short-lived cookie checked at the callback); it never reaches the page.
    return {"authUrl": OA.auth_url(_client()[0], redirect, state, challenge, scopes=scopes),
            "state": state, "expiresInMinutes": STATE_TTL_MINUTES, "redirectUri": redirect}


def finish_connect(state: str, code: str, today: date = None) -> dict:
    """The OAuth callback: check the state, trade the code for tokens, find
    the channel, store it with the refresh token encrypted, then sync once."""
    _require_configured()
    conn = db.get_conn()
    try:
        row = conn.execute("DELETE FROM own_oauth_pending WHERE state = ? RETURNING user_id, "
                           "code_verifier, created_at, redirect_uri", (state or "",)).fetchone()
        conn.commit()
    finally:
        conn.close()
    if not row:
        raise ConnectError("unknown or already used sign-in link -- start the connection again")
    if P.days_since(row["created_at"]) * 24 * 60 > STATE_TTL_MINUTES:
        raise ConnectError("the sign-in link expired -- start the connection again")
    user_id = row["user_id"]
    cid, secret = _client()
    try:
        tokens = OA.exchange_code(cid, secret, code, row["code_verifier"],
                                  row["redirect_uri"] or _redirect_uri())
    except OA.OAuthError as e:
        raise ConnectError(str(e)) from None
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise ConnectError("Google did not return a refresh token. Remove niche-finder at "
                           "https://myaccount.google.com/permissions and connect again.")
    try:
        ch = YA.mine_channel(tokens["access_token"])
    except YA.AnalyticsError as e:
        raise ConnectError(str(e)) from None
    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO own_channels (user_id, channel_id, title, published_at, scopes, token_enc, "
            "connected_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT (user_id, channel_id) DO UPDATE SET "
            "title = EXCLUDED.title, published_at = EXCLUDED.published_at, scopes = EXCLUDED.scopes, "
            "token_enc = EXCLUDED.token_enc, connected_at = EXCLUDED.connected_at, last_error = NULL",
            (user_id, ch["channelId"], ch["title"], ch["publishedAt"], tokens.get("scope"),
             SEC.encrypt(refresh), db.now_iso()))
        conn.commit()
    finally:
        conn.close()
    synced = sync(user_id=user_id, channel_id=ch["channelId"], today=today)["channels"][0]
    return {"channelId": ch["channelId"], "title": ch["title"], "synced": synced}


# ---------------------------------------------------------- sync

def _access_token(conn, user_id, channel_id):
    row = conn.execute("SELECT token_enc FROM own_channels WHERE user_id = ? AND channel_id = ?",
                       (user_id, channel_id)).fetchone()
    if not row or not row["token_enc"]:
        raise NotConnected(channel_id)
    cid, secret = _client()
    return OA.refresh_access_token(cid, secret, SEC.decrypt(row["token_enc"]))


def _report(token, channel_id, start, end):
    """Per-video numbers; without the monetary scope (or on an unmonetized
    channel) the revenue columns are simply left empty."""
    kw = {"dimensions": "video", "sort": "-views", "max_results": MAX_VIDEOS}
    try:
        return YA.report(token, channel_id, start, end, YA.VIDEO_METRICS + YA.MONETARY_METRICS,
                         **kw), True
    except YA.QuotaExceeded:
        raise
    except YA.AnalyticsError as e:
        if e.status not in (401, 403):
            raise
    return YA.report(token, channel_id, start, end, YA.VIDEO_METRICS, **kw), False


def _store(conn, user_id, channel_id, window, start, end, rows):
    now = db.now_iso()
    for r in rows:
        conn.execute(
            "INSERT INTO own_video_metrics (user_id, channel_id, video_id, window_name, start_date, "
            "end_date, views, minutes_watched, avg_view_duration, avg_view_pct, subscribers_gained, "
            "revenue, cpm, playback_cpm, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT (user_id, video_id, window_name) DO UPDATE SET start_date = EXCLUDED.start_date, "
            "end_date = EXCLUDED.end_date, views = EXCLUDED.views, "
            "minutes_watched = EXCLUDED.minutes_watched, avg_view_duration = EXCLUDED.avg_view_duration, "
            "avg_view_pct = EXCLUDED.avg_view_pct, subscribers_gained = EXCLUDED.subscribers_gained, "
            "revenue = EXCLUDED.revenue, cpm = EXCLUDED.cpm, playback_cpm = EXCLUDED.playback_cpm, "
            "fetched_at = EXCLUDED.fetched_at",
            (user_id, channel_id, r["video"], window, start, end, r.get("views"),
             r.get("estimatedMinutesWatched"), r.get("averageViewDuration"),
             r.get("averageViewPercentage"), r.get("subscribersGained"), r.get("estimatedRevenue"),
             r.get("cpm"), r.get("playbackBasedCpm"), now))


def sync(user_id: int = LOCAL_USER_ID, channel_id: str = None, today: date = None) -> dict:
    """Refresh the last 28 days and lifetime per-video numbers of your
    connected channels (or one). Uses the Analytics API quota, not the Data
    API key's. A quota error is recorded and the old numbers are kept."""
    _require_configured(connecting=False)
    today = today or date.today()
    end = today - timedelta(days=LAG_DAYS)
    start28 = end - timedelta(days=WINDOW_DAYS - 1)
    conn = db.get_conn()
    try:
        q = "SELECT channel_id, published_at FROM own_channels WHERE user_id = ?"
        params = [user_id]
        if channel_id:
            q += " AND channel_id = ?"
            params.append(channel_id)
        channels = [dict(r) for r in conn.execute(q, params).fetchall()]
        results = []
        for ch in channels:
            out = {"channelId": ch["channel_id"], "videos": 0, "monetary": None, "error": None}
            try:
                token = _access_token(conn, user_id, ch["channel_id"])
                first = (ch["published_at"] or FIRST_DAY)[:10]
                for window, start in (("28d", start28.isoformat()), ("lifetime", first)):
                    rows, money = _report(token, ch["channel_id"], start, end.isoformat())
                    _store(conn, user_id, ch["channel_id"], window, start, end.isoformat(), rows)
                    out["monetary"] = money if out["monetary"] is None else out["monetary"] and money
                    if window == "lifetime":
                        out["videos"] = len(rows)
                # plan 24: day x format for a year; a query YouTube does not
                # support for this channel must not cost the rest of the sync
                try:
                    out["formatDays"] = OF.sync_daily(conn, token, user_id, ch["channel_id"], end)
                except YA.QuotaExceeded:
                    raise
                except YA.AnalyticsError as e:
                    out["formatDays"] = None
                    out["formatsError"] = str(e)[:300]
                conn.execute("UPDATE own_channels SET last_synced_at = ?, last_error = NULL "
                             "WHERE user_id = ? AND channel_id = ?",
                             (db.now_iso(), user_id, ch["channel_id"]))
            except YA.QuotaExceeded:
                out["error"] = "quota-exceeded"
            except (OA.OAuthError, YA.AnalyticsError, SEC.SecretsNotConfigured) as e:
                out["error"] = str(e)[:300]
            if out["error"]:
                conn.execute("UPDATE own_channels SET last_error = ? WHERE user_id = ? AND channel_id = ?",
                             (out["error"], user_id, ch["channel_id"]))
            conn.commit()
            results.append(out)
    finally:
        conn.close()
    return {"channels": results, "window": {"start": start28.isoformat(), "end": end.isoformat()}}


def sync_all(today: date = None) -> dict:
    """The worker's daily run: every user's connected channels, each with that
    user's own token (plan 15)."""
    conn = db.get_conn()
    try:
        users = [r["user_id"] for r in conn.execute(
            "SELECT DISTINCT user_id FROM own_channels ORDER BY user_id").fetchall()]
    finally:
        conn.close()
    return {str(uid): sync(user_id=uid, today=today) for uid in users}


# ---------------------------------------------------------- reading

def _owned(conn, user_id, channel_id):
    if not conn.execute("SELECT 1 FROM own_channels WHERE user_id = ? AND channel_id = ?",
                        (user_id, channel_id)).fetchone():
        raise NotConnected(channel_id)


def _metrics(conn, user_id, channel_id, window):
    return [dict(r) for r in conn.execute(
        "SELECT * FROM own_video_metrics WHERE user_id = ? AND channel_id = ? AND window_name = ?",
        (user_id, channel_id, window)).fetchall()]


def _summary(rows) -> dict:
    views = sum(r["views"] or 0 for r in rows)
    money = [r["revenue"] for r in rows if r["revenue"] is not None]
    revenue = round(sum(money), 2) if money else None
    retention = OM.versus_niche([{"views": r["views"], "averageViewPercentage": r["avg_view_pct"]}
                                 for r in rows], [])["ownMedianRetentionPct"]
    return {"videos": len(rows), "views": views, "revenue": revenue, "rpm": OM.rpm(revenue, views),
            "medianRetentionPct": retention}


def list_channels(user_id: int = LOCAL_USER_ID) -> dict:
    conn = db.get_conn()
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT channel_id, title, scopes, connected_at, last_synced_at, last_error "
            "FROM own_channels WHERE user_id = ? ORDER BY title", (user_id,)).fetchall()]
        channels = [{"channelId": r["channel_id"], "title": r["title"],
                     "monetaryScope": "yt-analytics-monetary" in (r["scopes"] or ""),
                     "connectedAt": r["connected_at"], "lastSyncedAt": r["last_synced_at"],
                     "lastError": r["last_error"],
                     "last28d": _summary(_metrics(conn, user_id, r["channel_id"], "28d"))}
                    for r in rows]
    finally:
        conn.close()
    return {"channels": channels, "note": NOTE}


def _estimated_range(channel_id) -> dict:
    from application import channel_tracking as T
    a = T.channel_analytics(channel_id)
    if a.get("found"):
        return a["revenue"]["nicheModel"]["rpm_range"], "channel category"
    return M.rpm_range("default"), "default (channel not collected yet)"


def rpm_calibration(user_id: int = LOCAL_USER_ID) -> dict:
    """Your real 28-day RPM next to the range niche-finder would have guessed
    for the same channel from public data (plan 06)."""
    conn = db.get_conn()
    try:
        ids = [r["channel_id"] for r in conn.execute(
            "SELECT channel_id FROM own_channels WHERE user_id = ?", (user_id,)).fetchall()]
        summaries = {cid: _summary(_metrics(conn, user_id, cid, "28d")) for cid in ids}
    finally:
        conn.close()
    out = []
    for cid, s in summaries.items():
        rng, basis = _estimated_range(cid)
        out.append({"channelId": cid, "views28d": s["views"], "revenue28d": s["revenue"],
                    "estimateBasis": basis, **OM.calibrate(s["rpm"], rng)})
    return {"channels": out,
            "note": "RPM = estimated revenue per 1,000 views of all views, as in YouTube Studio. "
                    "Unknown without the monetary scope or on an unmonetized channel."}


def own_vs_niche(channel_id: str, niche: str, user_id: int = LOCAL_USER_ID) -> dict:
    """Your videos' lifetime views and retention against the videos of a niche
    we collected (their public lifetime views)."""
    conn = db.get_conn()
    try:
        _owned(conn, user_id, channel_id)
        own = _metrics(conn, user_id, channel_id, "lifetime")
        niche_views = [r["view_count"] for r in conn.execute(
            "SELECT v.view_count FROM videos v JOIN video_niches vn ON vn.video_id = v.video_id "
            "WHERE vn.niche_slug = ? AND v.channel_id != ?", (niche, channel_id)).fetchall()]
    finally:
        conn.close()
    cmp = OM.versus_niche([{"views": r["views"], "averageViewPercentage": r["avg_view_pct"]}
                           for r in own], niche_views)
    top = sorted(own, key=lambda r: r["views"] or 0, reverse=True)[:10]
    return {"channelId": channel_id, "niche": niche, **cmp,
            "topVideos": [{"videoId": r["video_id"], "views": r["views"],
                           "averageViewPercentage": r["avg_view_pct"],
                           "rpm": OM.rpm(r["revenue"], r["views"])} for r in top],
            "note": NOTE}


def metrics_for_videos(video_ids, user_id: int = LOCAL_USER_ID) -> dict:
    """{video_id: lifetime numbers} for draft_outcomes -- empty when the
    videos are not on a connected channel."""
    ids = [v for v in video_ids if v]
    if not ids:
        return {}
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM own_video_metrics WHERE user_id = ? AND window_name = 'lifetime' "
            "AND video_id IN (%s)" % ",".join("?" * len(ids)), [user_id] + ids).fetchall()
    finally:
        conn.close()
    return {r["video_id"]: {"views": r["views"], "averageViewPercentage": r["avg_view_pct"],
                            "averageViewDuration": r["avg_view_duration"],
                            "subscribersGained": r["subscribers_gained"],
                            "rpm": OM.rpm(r["revenue"], r["views"]), "fetchedAt": r["fetched_at"]}
            for r in rows}


# ---------------------------------------------------------- disconnect

def disconnect(channel_id: str, user_id: int = LOCAL_USER_ID) -> dict:
    """Revoke the token at Google (best effort) and delete everything stored
    for this channel."""
    conn = db.get_conn()
    try:
        row = conn.execute("SELECT token_enc FROM own_channels WHERE user_id = ? AND channel_id = ?",
                           (user_id, channel_id)).fetchone()
        if not row:
            raise NotConnected(channel_id)
        revoked = False
        try:
            revoked = OA.revoke(SEC.decrypt(row["token_enc"])) if row["token_enc"] else False
        except SEC.SecretsNotConfigured:
            revoked = False
        conn.execute("DELETE FROM own_video_metrics WHERE user_id = ? AND channel_id = ?",
                     (user_id, channel_id))
        conn.execute("DELETE FROM own_channel_daily WHERE user_id = ? AND channel_id = ?",
                     (user_id, channel_id))
        conn.execute("DELETE FROM own_channels WHERE user_id = ? AND channel_id = ?",
                     (user_id, channel_id))
        conn.commit()
    finally:
        conn.close()
    return {"channelId": channel_id, "revoked": revoked, "deleted": True}
