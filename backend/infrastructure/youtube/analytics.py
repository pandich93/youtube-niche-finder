"""YouTube Analytics API v2 (plan 14): reports.query for your own channel,
with the OAuth access token of that channel. Its quota is separate from the
Data API key's. Impressions and thumbnail click-through rate are not in this
API (YouTube Studio only), so they are never requested.
"""
import requests

REPORTS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
TIMEOUT = 30

VIDEO_METRICS = ["views", "estimatedMinutesWatched", "averageViewDuration",
                 "averageViewPercentage", "subscribersGained"]
MONETARY_METRICS = ["estimatedRevenue", "cpm", "playbackBasedCpm"]


class AnalyticsError(RuntimeError):
    def __init__(self, message, reason=None, status=None):
        super().__init__(message)
        self.reason = reason
        self.status = status


class QuotaExceeded(AnalyticsError):
    pass


def _get(url, access_token, params) -> dict:
    try:
        resp = requests.get(url, params=params, timeout=TIMEOUT,
                            headers={"Authorization": f"Bearer {access_token}"})
    except requests.RequestException as e:
        raise AnalyticsError(f"cannot reach Google: {type(e).__name__}") from None
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code != 200:
        err = body.get("error") or {}
        reason = ((err.get("errors") or [{}])[0]).get("reason")
        cls = QuotaExceeded if reason in ("quotaExceeded", "rateLimitExceeded") else AnalyticsError
        raise cls(f"YouTube Analytics API {resp.status_code}: {err.get('message') or reason}",
                  reason=reason, status=resp.status_code)
    return body


def report(access_token, channel_id, start_date, end_date, metrics, dimensions=None,
           sort=None, max_results=None, filters=None) -> list:
    params = {"ids": f"channel=={channel_id}", "startDate": start_date, "endDate": end_date,
              "metrics": ",".join(metrics)}
    for k, v in (("dimensions", dimensions), ("sort", sort), ("maxResults", max_results),
                 ("filters", filters)):
        if v is not None:
            params[k] = v
    body = _get(REPORTS_URL, access_token, params)
    names = [h["name"] for h in body.get("columnHeaders") or []]
    return [dict(zip(names, row)) for row in body.get("rows") or []]


def mine_channel(access_token) -> dict:
    """The channel the token belongs to (Data API channels.list mine=true,
    1 unit of the OAuth client's own project quota)."""
    body = _get(CHANNELS_URL, access_token, {"part": "snippet", "mine": "true"})
    items = body.get("items") or []
    if not items:
        raise AnalyticsError("this Google account has no YouTube channel", reason="noChannel")
    it = items[0]
    return {"channelId": it["id"], "title": (it.get("snippet") or {}).get("title"),
            "publishedAt": (it.get("snippet") or {}).get("publishedAt")}
