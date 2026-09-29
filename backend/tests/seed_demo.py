"""Fill a database with synthetic-but-realistic data so every section can be
exercised without spending YouTube quota.

    python3 tests/seed_demo.py            # seeds the Postgres DB from env vars
    NICHE_DB_SCHEMA=demo python3 tests/seed_demo.py   # into an isolated schema

It writes two stats snapshots 24h apart for every video, so velocity,
acceleration and period-over-period comparisons all have something to chew on.
"""
import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import infrastructure.postgres as db  # noqa: E402
from application import sponsors as SPN  # noqa: E402
from domain import metrics as M  # noqa: E402

random.seed(7)

NOW = datetime.now(timezone.utc)

CHANNELS = [
    # (id, title, subs, video_count, total_views, category, country)
    ("UC0000000000000000000a", "Tiny AI Lab", 800, 24, 240_000, "28", "US"),
    ("UC0000000000000000000b", "Faceless History", 9_400, 60, 4_100_000, "27", "GB"),
    ("UC0000000000000000000c", "Mid Tech Review", 48_000, 210, 31_000_000, "28", "US"),
    ("UC0000000000000000000d", "Big Gaming Hub", 2_400_000, 1_800, 900_000_000, "20", "US"),
    ("UC0000000000000000000e", "Нейросети по-русски", 3_200, 31, 610_000, "28", "RU"),
    ("UC0000000000000000000f", "Cooking Shorts Co", 15_000, 320, 22_000_000, "26", "US"),
]

TITLE_BITS = {
    "28": ["AI agents just changed everything", "I built an AI that {verb} for me",
           "GPT-5 vs Claude: the honest test", "This AI tool replaced my whole workflow",
           "Local LLM on a laptop in 10 minutes", "AI automation nobody talks about"],
    "27": ["The forgotten war that shaped {place}", "Why {place} collapsed overnight",
           "The truth about the {place} expedition", "History's strangest disappearance",
           "How one letter started a war"],
    "20": ["I survived 100 days in hardcore", "This update broke the game",
           "Speedrunning the impossible level", "Ranking every boss fight"],
    "26": ["3 ingredient dinner that actually works", "The pasta trick chefs hide",
           "Meal prep for a week in 20 minutes"],
}
VERBS = ["codes", "edits", "invests", "writes emails", "makes thumbnails"]
PLACES = ["Carthage", "Byzantium", "Novgorod", "Angkor", "Tenochtitlan"]
TAGS = {
    "28": ["ai", "artificial intelligence", "automation", "llm", "chatgpt"],
    "27": ["history", "documentary", "ancient history", "war"],
    "20": ["gaming", "minecraft", "speedrun"],
    "26": ["cooking", "recipe", "meal prep"],
}


def title_for(cat, i):
    pool = TITLE_BITS.get(cat, TITLE_BITS["28"])
    t = pool[i % len(pool)]
    return t.replace("{verb}", random.choice(VERBS)).replace("{place}", random.choice(PLACES))


def _demo_jpeg(bg, fg):
    import io

    from PIL import Image, ImageDraw
    img = Image.new("RGB", (320, 180), bg)
    ImageDraw.Draw(img).rectangle((20, 20, 170, 160), fill=fg)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def seed_packaging(conn):
    """Plan 05 demo: one retitled video and one thumbnail swap with both
    archived versions, so the "Перепаковки" screen has something to show."""
    vid = "avid000"
    before = (NOW - timedelta(days=3)).isoformat()
    after = (NOW - timedelta(days=1)).isoformat()
    for ts, h, img in ((before, "00ff00ff00ff00ff", _demo_jpeg((20, 20, 20), (230, 40, 40))),
                       (after, "ff00ff00ff00ff00", _demo_jpeg((240, 240, 240), (10, 60, 200)))):
        conn.execute("INSERT INTO thumbnail_archive (video_id, captured_at, dhash, image) "
                     "VALUES (?,?,?,?) ON CONFLICT (video_id, captured_at) DO NOTHING",
                     (vid, ts, h, img))
    conn.execute("INSERT INTO video_changes (video_id, changed_at, field, old_value, new_value) "
                 "VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING",
                 (vid, after, "thumbnail_image", "00ff00ff00ff00ff", "ff00ff00ff00ff00"))
    conn.execute("INSERT INTO video_changes (video_id, changed_at, field, old_value, new_value) "
                 "VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING",
                 ("avid001", after, "title", "My first AI agent", "AI agents just changed everything"))


def seed_alerts(conn):
    """Plan 04 demo: two tracked channels, one of them confirmed gone, and a
    few events, so the "Алерты" screen and the channel's "gone" mark render."""
    alive, gone = "UC0000000000000000000a", "UC0000000000000000000f"
    missing = (NOW - timedelta(days=2)).isoformat()
    for cid in (alive, gone):
        conn.execute("INSERT INTO tracked_channels (user_id, channel_id, added_at, active) "
                     "VALUES (1, ?, ?, 1) ON CONFLICT DO NOTHING", (cid, missing))
    conn.execute("INSERT INTO gone_items (kind, ref_id, first_missing_at, last_missing_at, "
                 "miss_count, confirmed_at) VALUES ('channel', ?, ?, ?, 2, ?) "
                 "ON CONFLICT DO NOTHING", (gone, missing, NOW.isoformat(), NOW.isoformat()))
    events = [
        ("outlier", "avid000", alive, {"videoId": "avid000", "channelId": alive,
                                        "title": "demo outlier", "outlierScore": 6.2,
                                        "views": 120_000}),
        ("channel_gone", gone, gone, {"channelId": gone, "title": "Cooking Shorts Co",
                                      "subscribers": 15_000, "views": 22_000_000,
                                      "videoCount": 320, "goneSince": missing}),
    ]
    for kind, ref, cid, payload in events:
        conn.execute("INSERT INTO events (kind, ref_id, payload, created_at, channel_id) "
                     "VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING",
                     (kind, ref, json.dumps(payload), NOW.isoformat(), cid))


def description_for(t, i):
    """Plan 09 demo: every 7th video names a sponsor with a promo code, every
    11th carries an Amazon affiliate link. Deterministic -- no random calls, so
    the rest of the seeded data stays as it was."""
    desc = t + " — full breakdown."
    if i % 7 == 0:
        desc += "\n\nThis video is sponsored by NordVPN. Use code DEMO at https://nordvpn.com/demo"
    if i % 11 == 0:
        desc += "\n\nGear I use: https://amzn.to/demo" + str(i)
    return desc


def seed(days_back=75, per_channel=34):
    db.init_db()
    conn = db.get_conn()
    now_iso = NOW.isoformat()

    for cid, title, subs, vcount, total_views, cat, country in CHANNELS:
        created = NOW - timedelta(days=random.randint(400, 2200))
        db.upsert_channel(conn, {
            "channel_id": cid, "title": title, "custom_url": "@" + title.lower().replace(" ", ""),
            "country": country, "description": f"{title} channel", "default_language": None,
            "subscriber_count": subs, "video_count": vcount, "view_count": total_views,
            "thumbnail": None, "updated_at": now_iso, "published_at": created.isoformat(),
            "topic_categories": json.dumps(["Knowledge"]), "keywords": None,
            "uploads_playlist": "UU" + cid[2:], "hidden_subs": 0,
        })
        # two channel snapshots so growth blocks resolve
        db.record_channel_stats(conn, cid, int(subs * 0.94), vcount - 2,
                                int(total_views * 0.93),
                                (NOW - timedelta(days=30)).isoformat())
        db.record_channel_stats(conn, cid, int(subs * 0.99), vcount,
                                int(total_views * 0.995),
                                (NOW - timedelta(days=1)).isoformat())
        db.record_channel_stats(conn, cid, subs, vcount, total_views, now_iso)

        base = total_views // max(vcount, 1)
        lang = "ru" if country == "RU" else "en"
        for i in range(per_channel):
            age_days = random.uniform(0.2, days_back)
            published = NOW - timedelta(days=age_days)
            vid = f"{cid[-1]}vid{i:03d}"
            # most videos near baseline, a few genuine breakouts
            roll = random.random()
            mult = 0.4 + random.random() * 1.2
            if roll > 0.93:
                mult = random.uniform(6, 40)
            elif roll > 0.85:
                mult = random.uniform(2.5, 6)
            views = int(base * mult * M.maturity(age_days))
            is_short = 1 if (cid.endswith("f") and i % 2 == 0) else 0
            duration = random.randint(20, 60) if is_short else random.randint(300, 1500)
            t = title_for(cat, i)
            db.upsert_video(conn, {
                "video_id": vid, "channel_id": cid, "title": t,
                "description": description_for(t, i),
                "published_at": published.isoformat(),
                "duration_seconds": duration, "view_count": views,
                "like_count": int(views * random.uniform(0.02, 0.06)),
                "comment_count": int(views * random.uniform(0.001, 0.005)),
                "thumbnail": None,
                "tags": json.dumps(random.sample(TAGS.get(cat, ["misc"]),
                                                 k=min(3, len(TAGS.get(cat, ["misc"]))))),
                "default_language": lang, "embedding": None, "updated_at": now_iso,
                "category_id": cat, "region": country, "is_short": is_short,
                "topic_categories": None, "live_content": "none",
            })
            db.link_video_niche(conn, vid, "demo")
            # snapshots: 48h ago, 24h ago, now -> enables vph24h + acceleration
            for hours, share in ((48, 0.90), (24, 0.96), (0, 1.0)):
                if age_days * 24 > hours:
                    db.record_video_stats(
                        conn, vid, int(views * share),
                        int(views * share * 0.04), int(views * share * 0.003), t, None,
                        (NOW - timedelta(hours=hours)).isoformat())
    seed_packaging(conn)
    seed_alerts(conn)
    db.upsert_niche(conn, "demo", "synthetic demo corpus", "Demo")
    conn.commit()
    conn.close()
    SPN.scan_sponsors()
    return {"channels": len(CHANNELS), "videos": len(CHANNELS) * per_channel}


if __name__ == "__main__":
    print(seed())
    print("db:", db.display_dsn())
