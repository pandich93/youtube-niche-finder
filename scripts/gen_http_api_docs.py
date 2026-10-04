#!/usr/bin/env python3
"""Generate docs/http-api.md from backend/interfaces/http/api.py.

The route list, path/query parameters, JSON body fields and the matching MCP
tool are read from the code itself (ast, nothing is imported or executed), so
they cannot drift. The one-line summaries, groups and costs below are written
by hand -- the script refuses to write the file when a route has no summary
or a summary points at a route that no longer exists.

    python3 scripts/gen_http_api_docs.py           # rewrite docs/http-api.md
    python3 scripts/gen_http_api_docs.py --check   # exit 1 if it is out of date
"""
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = ROOT / "backend/interfaces/http/api.py"
SERVER = ROOT / "backend/interfaces/mcp/server.py"
OUT = ROOT / "docs/http-api.md"

GROUPS = [
    ("status", "Status"),
    ("discover", "Discovery sections"),
    ("niches", "Niches"),
    ("channels", "Channels and the watchlist"),
    ("videos", "Videos and repackaging"),
    ("tags", "Tags and AI labelling"),
    ("create", "Titles, hooks, metadata review, drafts and briefs"),
    ("saved", "Swipe file"),
    ("transcripts", "Transcripts"),
    ("thumbs", "Thumbnails"),
    ("alerts", "Alerts, digest and notifications"),
    ("collect", "Collection (spends YouTube quota)"),
    ("inspect", "Chrome extension: inspect any YouTube page"),
    ("own", "Your own channels (Google OAuth)"),
    ("auth", "Sign-in and personal tokens (multi-user mode)"),
]

# "METHOD /path": (group, summary)
SUMMARY = {
    "GET /api/health": ("status", "Backend status: database counts, whether the YouTube key is set, remaining search calls and units today."),
    "GET /api/stats": ("status", "What is in the database right now (same numbers as the `db_stats` tool)."),
    "GET /api/coverage": ("status", "Whether the database has enough data for a period (videos in the window, with a category, with snapshots) -- call it when a section comes back empty."),
    "GET /api/overview": ("status", "Everything the Overview screen needs in one request (viral videos, outlier channels, categories, keywords, competition)."),
    "GET /api/viral": ("discover", "Viral videos from small channels over a period; `preset` applies the dashboard's ready-made filters."),
    "GET /api/categories": ("discover", "Most popular categories over a period, with the shift against the previous period."),
    "GET /api/keywords": ("discover", "Trending keywords with lift and momentum; `keywords_mode=semantic` groups near-synonyms."),
    "GET /api/tags/top-by-category": ("discover", "Literal creator tags per YouTube category, ranked by frequency and breakout correlation."),
    "GET /api/outlier-channels": ("discover", "Recently added channels whose best video beat the channel's median by `min_multiplier`."),
    "GET /api/competition": ("discover", "Young, fast-growing channels that are about to become competitors."),
    "GET /api/search": ("discover", "Outlier search across the local corpus with subscriber, length, RPM, Shorts and YPP-threshold filters."),
    "POST /api/ideas/check": ("discover", "Verdicts for a list of video ideas (free / recent / proven / flopped) against what is already collected."),
    "GET /api/best-time": ("discover", "Best weekday/hour to publish for a niche or channel, scored by median age-adjusted outlier."),
    "GET /api/title-patterns": ("discover", "Title phrases that correlate with outliers in a niche or channel (lift, examples)."),
    "GET /api/niches": ("niches", "Every niche label with its query, video count and last collection time."),
    "GET /api/niches/saturation": ("niches", "Trend of every niche in one pass: growing / stable / cooling / saturated / insufficient-data."),
    "GET /api/channels/{channel_id}/collabs": ("channels", "Collaboration partners: similar channels of your size, active, not templated, with the numbers that picked them (plan 31)."),
    "GET /api/profit": ("channels", "Net profit: revenue range minus a cost profile's costs, for a channel's month, a video or a niche's typical video (plan 30)."),
    "GET /api/cost-profiles": ("channels", "Your cost profiles: price per video, per minute of video, monthly overhead (plan 30)."),
    "POST /api/cost-profiles": ("channels", "Create a cost profile, or change one by `id` or the same `name` (plan 30)."),
    "DELETE /api/cost-profiles/{profile_id}": ("channels", "Remove one of your cost profiles (plan 30)."),
    "GET /api/niches/ranking": ("niches", "Every niche with a 0-100 score and the six-term breakdown behind it, best first; cached for an hour (`refresh`) (plan 27)."),
    "GET /api/niches/compare": ("niches", "2-3 comma-separated niches side by side: score breakdown plus overview numbers (plan 27)."),
    "GET /api/niches/{slug}": ("niches", "Niche density (channel sizes, viral skew, Shorts share), top videos by multiplier and the niche trend."),
    "GET /api/niches/{slug}/videos": ("niches", "Flat per-video list for the niche scatter chart, filterable by channel and Shorts."),
    "GET /api/niches/{slug}/outlier-traits": ("niches", "What the niche's outliers have in common against ordinary videos: title, length, tags, publishing time; Shorts and long apart (plan 29)."),
    "GET /api/niches/{slug}/sponsors": ("niches", "Sponsor map of a niche: brands named in descriptions, promo codes, affiliate links kept apart."),
    "GET /api/niches/{slug}/template-risk": ("niches", "Share of the niche's channels whose recent uploads look like one repeated template."),
    "GET /api/niches/{slug}/thumbnail-styles": ("niches", "Thumbnail style clusters of a niche (CLIP vectors) and how each style performs."),
    "GET /api/niches/{slug}/insights": ("niches", "Comment insights aggregated over the niche's top videos (from cached per-video reports)."),
    "GET /api/niches/{slug}/content-gaps": ("niches", "Questions and requests from cached comments that no collected video or transcript answers yet."),
    "POST /api/niches/{slug}/content-gaps": ("niches", "Same as the GET, but first reads the comments of top videos that are not cached yet."),
    "GET /api/niches/{slug}/hook-benchmark": ("niches", "Hook scores of the niche's outliers against ordinary videos (needs 10+ transcripts per group)."),
    "GET /api/niche-clusters": ("niches", "The niche map: k-means clusters of channels by embedding."),
    "POST /api/niche-clusters/recompute": ("niches", "Recompute the niche map now (optionally with a fixed `k`)."),
    "GET /api/niche/{slug}/export.{fmt}": ("niches", "Download the niche's videos as `tsv` or `csv`."),
    "GET /api/channels/tracked": ("channels", "The watchlist, optionally filtered by AI labels (`faceless`, `content_format`, `topic`)."),
    "POST /api/channels/track": ("channels", "Add a channel (UC id, @handle or URL) to the watchlist."),
    "DELETE /api/channels/tracked/{channel_id}": ("channels", "Remove a channel from the watchlist."),
    "GET /api/channels/{channel_id}": ("channels", "Channel analytics: profile, growth by window, cadence, revenue range, YPP thresholds, gone status."),
    "GET /api/channels/{channel_id}/velocity": ("channels", "Per-video views per hour and acceleration from the worker's snapshots."),
    "GET /api/channels/{channel_id}/history": ("channels", "Subscriber/view snapshots of the channel over time."),
    "GET /api/channels/{channel_id}/similar": ("channels", "Channels similar by video embeddings."),
    "GET /api/channels/{channel_id}/niche-overview": ("channels", "The niche overview anchored on a channel, built from its closest peers (`similar_channels`)."),
    "GET /api/channels/{channel_id}/sponsors": ("channels", "Brands that sponsor this channel, read from its video descriptions."),
    "GET /api/channels/{channel_id}/template-risk": ("channels", "How much the channel's recent uploads look like one template, with the reasons (a heuristic)."),
    "GET /api/videos/{video_id}/similar": ("videos", "Videos similar by embedding, optionally excluding the same channel."),
    "GET /api/videos/{video_id}/similar-thumbnails": ("videos", "Videos whose thumbnail looks like this one (CLIP vectors)."),
    "GET /api/channels/{channel_id}/policy-signals": ("channels", "Signals for YouTube's three inauthentic-content categories: level, reasons, policy text; no risk percentage (plan 22)."),
    "GET /api/niches/{slug}/policy-signals": ("niches", "How many channels of a niche show each inauthentic-content category's signals (plan 22)."),
    "GET /api/own/channels/{channel_id}/formats": ("own", "Your channel by format: Shorts vs long vs live, weekly link, watch hours toward YPP (plan 24)."),
    "GET /api/freshness": ("status", "How many stored videos and channels were not re-read for REFRESH_STALE_DAYS, and since when the history goes back (plan 16)."),
    "GET /api/scores": ("videos", "What every number is: YouTube data or an estimate of niche-finder, formula, inputs, minimum sample; `key` for one (plan 23)."),
    "GET /api/videos/trajectory": ("videos", "Views by age for up to 5 comma-separated `ids`, each with its channel's expected curve and swap marks (plan 20)."),
    "GET /api/videos/{video_id}/repeatability": ("videos", "Did this video's format work for other channels too: repeatable / mixed / one_off / unknown (plan 21)."),
    "GET /api/videos/{video_id}/language-gap": ("videos", "Is there something like this video in another language, and is it an outlier there: open / thin / covered (plan 26)."),
    "GET /api/language-gaps": ("discover", "Outliers of a source language with a verdict on a target language: open / thin / covered, demand and the closest target videos (plan 26)."),
    "GET /api/language-gaps/languages": ("discover", "Languages held in the database with video and channel counts."),
    "GET /api/videos/{video_id}/packaging": ("videos", "Title and thumbnail history of one video."),
    "GET /api/videos/{video_id}/hook": ("videos", "Hook score of the video's first ~30 seconds from its transcript; `llm=true` adds an LLM read."),
    "GET /api/video/{video_id}/why": ("videos", "\"Why did it take off\" -- an LLM explanation of an outlier; 204 when no LLM is configured."),
    "POST /api/videos/{video_id}/comments": ("videos", "Live fetch of a video's comments (never stored)."),
    "POST /api/videos/{video_id}/insights": ("videos", "Comment insights for one video: questions, complaints, requests."),
    "GET /api/packaging": ("videos", "Repackaging feed: title and thumbnail swaps with before/after and the views-per-hour effect."),
    "GET /api/title-changes": ("videos", "Raw title/thumbnail change log for a period or channel."),
    "GET /api/tags": ("tags", "Curated tags (theme / trigger / format ...) of a niche's videos or one video."),
    "POST /api/tags": ("tags", "Set tags on videos by hand (`replace` overwrites a video's tags)."),
    "GET /api/tags/stats": ("tags", "Hit rate of each tag group: how often videos with a tag become outliers."),
    "GET /api/tags/proposed": ("tags", "LLM-proposed tags outside the taxonomy, waiting to be accepted or rejected."),
    "POST /api/tags/proposed/resolve": ("tags", "Accept or reject one proposed tag."),
    "POST /api/enrich/channels": ("tags", "Run background AI labelling (faceless / format / topic) on up to `limit` channels now."),
    "POST /api/enrich/videos": ("tags", "Run LLM tagging on up to `limit` untagged videos now."),
    "POST /api/metadata/review": ("create", "Review a draft title/description/tags against your corpus: per-signal verdicts with sample sizes, no single score."),
    "POST /api/drafts": ("create", "Save a metadata draft, optionally with its review snapshot."),
    "GET /api/drafts": ("create", "List saved drafts."),
    "POST /api/drafts/{draft_id}/link": ("create", "Link a draft to the video it was published as."),
    "GET /api/drafts/outcomes": ("create", "Linked drafts old enough to judge: the review at save time next to real views."),
    "POST /api/briefs": ("create", "Outlier to brief: hook, title patterns, topic coverage, title candidates and thumbnail references for your own video."),
    "POST /api/titles/score": ("create", "Score title candidates (deterministic, plus an LLM score when one is configured)."),
    "POST /api/titles/suggest": ("create", "Generate title candidates for a topic in the niche's style."),
    "POST /api/hooks/score": ("create", "Score the text of a draft intro 0-100 (never stored, never sent to an LLM)."),
    "GET /api/saved": ("saved", "Saved videos and channels, optionally by kind and folder."),
    "GET /api/saved/folders": ("saved", "Swipe-file folders with item counts."),
    "POST /api/saved": ("saved", "Save a video or channel with a snapshot of its metrics."),
    "DELETE /api/saved/{item_id}": ("saved", "Remove one saved item."),
    "POST /api/transcripts/request": ("transcripts", "Put a video in the transcript queue."),
    "GET /api/transcripts/queue": ("transcripts", "The transcript queue, optionally by status."),
    "POST /api/transcripts/{video_id}/save": ("transcripts", "Save a pasted transcript (and index it for search)."),
    "POST /api/transcripts/{video_id}/reindex": ("transcripts", "Rebuild the search index of one transcript."),
    "GET /api/transcripts/search": ("transcripts", "Hybrid (keyword + semantic) search over saved transcripts."),
    "GET /api/thumbnails/search": ("thumbs", "Thumbnails matching a short visual description, e.g. \"red arrow, shocked face\"."),
    "POST /api/thumbnails/embed": ("thumbs", "Compute CLIP vectors for thumbnails that have none."),
    "GET /api/thumbnails/{video_id}/{captured_at}.jpg": ("thumbs", "An archived thumbnail version -- the only copy of a \"before\" image after a swap."),
    "GET /api/events": ("alerts", "Alert events (new outlier, acceleration, title change, silence break, gone) plus the unseen count."),
    "POST /api/events/seen": ("alerts", "Mark events as seen: `ids`, or everything with `all`."),
    "GET /api/topics": ("alerts", "Your watched topics (plan 19)."),
    "POST /api/topics": ("alerts", "Watch a topic: `text`, optional `threshold` (0.6); a close new video raises a personal `topic_match` alert."),
    "POST /api/topics/{topic_id}/pause": ("alerts", "Pause or resume a topic: `paused`."),
    "POST /api/topics/{topic_id}/search": ("alerts", "Turn a topic's daily YouTube search on or off: `on` (one search.list call a day)."),
    "DELETE /api/topics/{topic_id}": ("alerts", "Stop watching a topic; its past alerts stay."),
    "POST /api/events/scan": ("alerts", "Run the alert scan now instead of waiting for the worker."),
    "GET /api/digest": ("alerts", "What the daily digest would contain right now (read-only)."),
    "POST /api/digest/send": ("alerts", "Send the digest now, to check the Telegram/webhook setup."),
    "GET /api/settings/notifications": ("alerts", "Whether Telegram/webhook delivery is set and in which mode -- never the secrets."),
    "PUT /api/settings/notifications": ("alerts", "Set your own Telegram bot/chat or webhook and the delivery mode (stored encrypted)."),
    "POST /api/settings/notifications/test": ("alerts", "Send a test message to the configured Telegram chat or webhook."),
    "POST /api/collect/channel": ("collect", "Collect a channel through its uploads playlist; `track=true` also adds it to the watchlist."),
    "POST /api/collect/niche": ("collect", "Collect a niche by search query (the only route that uses `search.list`)."),
    "POST /api/refresh": ("collect", "Refresh view counts of recent videos and tracked channels now."),
    "GET /api/inspect/video": ("inspect", "Everything the watch-page panel shows for one video."),
    "GET /api/inspect/channel": ("inspect", "Everything the channel-page panel shows (`ref` = UC id, @handle or URL)."),
    "POST /api/inspect/videos": ("inspect", "Badge data (multiplier, views per hour) for up to a page of thumbnails at once."),
    "GET /api/own/status": ("own", "Whether OAuth is configured and which `.env` variables are missing."),
    "POST /api/own/connect": ("own", "Start the Google consent flow; returns the URL to open. `includeRevenue` also asks for the revenue scope (plan 25)."),
    "GET /api/own/oauth/callback": ("own", "Google's redirect after consent (stores the refresh token encrypted)."),
    "GET /api/own/channels": ("own", "Your connected channels with last-28-day views, revenue, RPM and retention."),
    "POST /api/own/sync": ("own", "Pull fresh YouTube Analytics numbers now (the worker does it daily)."),
    "GET /api/own/rpm-calibration": ("own", "Your real RPM against niche-finder's estimate for the same niche."),
    "GET /api/own/channels/{channel_id}/vs-niche": ("own", "Your channel's real numbers next to a niche's public ones."),
    "DELETE /api/own/channels/{channel_id}": ("own", "Disconnect a channel: revoke the token at Google and delete everything stored for it."),
    "GET /api/auth/me": ("auth", "The signed-in user and today's quota usage; in single-user mode it just says multi-user is off."),
    "POST /api/auth/login": ("auth", "Sign in with email and password; sets the session cookie."),
    "POST /api/auth/logout": ("auth", "Sign out and drop the session."),
    "GET /api/auth/tokens": ("auth", "Your personal API tokens (names and dates, never the secret)."),
    "POST /api/auth/tokens": ("auth", "Create a personal token for the extension or MCP over HTTP; shown once."),
    "DELETE /api/auth/tokens/{token_id}": ("auth", "Revoke a personal token."),
}

# Anything not listed here only reads the local database: zero quota, no LLM.
COST = {
    "POST /api/collect/channel": "YouTube: ~1 unit per 50 videos",
    "POST /api/collect/niche": "YouTube: 1 `search.list` call per page (100/day) + ~1 unit per 50 videos",
    "POST /api/refresh": "YouTube: ~1 unit per 50 videos / channels",
    "POST /api/channels/track": "YouTube: 1 unit to resolve a handle/URL not seen before",
    "POST /api/videos/{video_id}/comments": "YouTube: 1 unit",
    "POST /api/videos/{video_id}/insights": "YouTube: 1 unit + LLM on a cache miss",
    "POST /api/niches/{slug}/content-gaps": "YouTube: 1 unit per uncached video (+ LLM in LLM mode)",
    "GET /api/niches/{slug}/content-gaps": "LLM only with `use_llm`",
    "GET /api/niches/{slug}/insights": "LLM pass over cached reports",
    "GET /api/video/{video_id}/why": "LLM on a cache miss",
    "GET /api/videos/{video_id}/hook": "LLM only with `llm=true`",
    "POST /api/enrich/channels": "LLM",
    "POST /api/enrich/videos": "LLM",
    "POST /api/titles/suggest": "LLM (required)",
    "POST /api/titles/score": "LLM when configured",
    "POST /api/briefs": "LLM when configured (`useLlm`)",
    "GET /api/inspect/video": "0 from the database; with `fetch=true` a miss costs 1 unit (+1 for an unknown channel)",
    "GET /api/inspect/channel": "0 from the database; with `fetch=true` a miss costs 1-2 units",
    "POST /api/inspect/videos": "with `fetch=true`: 1 unit per 50 unknown videos",
    "POST /api/own/sync": "YouTube Analytics API (your own OAuth project)",
    "POST /api/thumbnails/embed": "no quota; downloads thumbnails, CPU",
    "POST /api/digest/send": "sends a Telegram/webhook message",
    "POST /api/settings/notifications/test": "sends a Telegram/webhook message",
}

ALIASES = {"ref_id": "refId", "video_id": "videoId", "channel_id": "channelId", "is_short": "isShort",
           "use_llm": "useLlm", "gap_topic": "gapTopic", "tag_group": "tagGroup", "top_videos": "topVideos"}


def mcp_tools():
    lines = SERVER.read_text(encoding="utf-8").splitlines()
    out = set()
    for i, line in enumerate(lines):
        if line.startswith("@mcp.tool"):
            for nxt in lines[i + 1:i + 6]:
                m = re.match(r"(?:async\s+)?def\s+(\w+)", nxt)
                if m:
                    out.add(m.group(1))
                    break
    return out


def routes():
    tools = mcp_tools()
    tree = ast.parse(API.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                    and isinstance(dec.func.value, ast.Name) and dec.func.value.id == "app"
                    and dec.func.attr in ("get", "post", "put", "delete", "patch")):
                continue
            path = dec.args[0].value
            if not path.startswith("/api/"):     # /privacy, /terms: pages, not the API
                continue
            path_params = re.findall(r"{(\w+)}", path)
            args = node.args.args
            defaults = [None] * (len(args) - len(node.args.defaults)) + list(node.args.defaults)
            query, has_body = [], False
            for arg, default in zip(args, defaults):
                if arg.arg in path_params or arg.arg in ("request", "response"):
                    continue
                if isinstance(default, ast.Call) and getattr(default.func, "id", "") == "Body":
                    has_body = True
                    continue
                query.append(arg.arg if default is None else f"{arg.arg}={ast.unparse(default)}")
            body = []
            for call in ast.walk(node):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "get" and isinstance(call.func.value, ast.Name)
                        and call.func.value.id == "payload" and call.args
                        and isinstance(call.args[0], ast.Constant)):
                    key = ALIASES.get(call.args[0].value, call.args[0].value)
                    if key not in body:
                        body.append(key)
            twins = sorted({a.attr for a in ast.walk(node) if isinstance(a, ast.Attribute) and a.attr in tools})
            yield {"method": dec.func.attr.upper(), "path": path, "query": query,
                   "body": body if has_body else None, "twins": twins}


def render():
    found = list(routes())
    keys = {f"{r['method']} {r['path']}" for r in found}
    missing = sorted(keys - SUMMARY.keys())
    stale = sorted(SUMMARY.keys() - keys)
    if missing or stale:
        for k in missing:
            print(f"no summary for route: {k}", file=sys.stderr)
        for k in stale:
            print(f"summary for a route that no longer exists: {k}", file=sys.stderr)
        sys.exit(2)
    out = [
        "# HTTP API reference",
        "",
        "<!-- Generated by scripts/gen_http_api_docs.py from backend/interfaces/http/api.py."
        " Do not edit by hand: change the code or the summaries in the script, then re-run it. -->",
        "",
        f"The dashboard and the Chrome extension talk to the backend through these {len(found)} routes."
        " Every route calls the same use cases in `backend/application/` as the MCP server, so the"
        " numbers match what Claude sees; where a route has an MCP twin, its full description is in"
        " [backend/README.md](../backend/README.md#tools).",
        "",
        "- **Base URL:** `http://127.0.0.1:8080` (the port is `WEB_PORT` in `.env`). The service listens"
        " on localhost only.",
        "- **Interactive docs:** FastAPI serves Swagger UI at `/api/docs` and the schema at"
        " `/api/openapi.json` on a running backend.",
        "- **Writes need a header:** a `POST`/`PUT`/`DELETE` must carry a JSON body or"
        " `X-NF-Client: <anything>`, otherwise it gets `403` -- this stops other websites open in your"
        " browser from spending your quota. See [SECURITY.md](../SECURITY.md).",
        "- **Multi-user mode** (`NF_MULTI_USER=1`): every route except `/api/health`, `/api/auth/login`,"
        " `/api/auth/logout`, `/api/auth/me` and the OAuth callback needs a session cookie or"
        " `Authorization: Bearer <personal token>`; without one it answers `401`.",
        "- **Cost:** a route not marked otherwise only reads your local Postgres -- zero YouTube quota,"
        " no LLM call. LLM costs apply only when `LLM_PROVIDER` is set.",
        "- **Body fields** are camelCase; most also accept the snake_case spelling.",
        "",
        "## Contents",
        "",
    ]
    by_group = {g: [] for g, _ in GROUPS}
    for r in found:
        by_group[SUMMARY[f"{r['method']} {r['path']}"][0]].append(r)
    for g, title in GROUPS:
        anchor = re.sub(r"[^a-z0-9 -]", "", title.lower()).replace(" ", "-")
        out.append(f"- [{title}](#{anchor}) ({len(by_group[g])})")
    for g, title in GROUPS:
        out += ["", f"## {title}", "", "| Route | What it does | Parameters | Cost | MCP twin |", "|---|---|---|---|---|"]
        for r in by_group[g]:
            key = f"{r['method']} {r['path']}"
            params = []
            if r["query"]:
                params.append("query: " + ", ".join(f"`{q}`" for q in r["query"]))
            if r["body"] is not None:
                params.append("body: " + (", ".join(f"`{b}`" for b in r["body"]) or "JSON"))
            twins = ", ".join(f"`{t}`" for t in r["twins"]) if 0 < len(r["twins"]) <= 2 else ""
            cost = COST.get(key, "free")
            summary = SUMMARY[key][1].replace("|", "\\|")
            out.append(f"| `{r['method']} {r['path']}` | {summary} | {'<br>'.join(params) or '--'} | {cost} | {twins or '--'} |")
    return "\n".join(out) + "\n"


def main():
    text = render()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print(f"{OUT.relative_to(ROOT)} is out of date: run python3 scripts/gen_http_api_docs.py",
                  file=sys.stderr)
            sys.exit(1)
        print(f"{OUT.relative_to(ROOT)} is up to date")
        return
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
