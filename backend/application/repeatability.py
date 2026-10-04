"""Format repeatability of one video (plan 21) -- the rule is in
domain/repeatability.py. Neighbours come from similar_videos (embedding
cosine, other channels only); their outlier scores from the same
load_window everything else uses. Zero quota, no LLM.
"""
from application import discovery as trends
from application import search as Q
from domain import repeatability as R
from domain import template_risk as TR

DEFAULT_MIN_SIMILARITY = 0.6
NEIGHBOURS = 60
MIN_OPENING_WORDS = 3
NOTE = ("An estimate of niche-finder over the channels we collected: 'one_off' can also mean "
        "the channels that repeated it were never collected.")


def _score(r):
    return r.get("outlierScoreAgeAdjusted") or r.get("outlierScore")


def _title_opening(conn, video_id, title, channel_id) -> dict:
    """Other channels whose titles start the same way (first words) -- a
    second, cruder signal shown apart from the verdict."""
    opening, _ = TR._skeleton_keys(title)
    if not opening or len(opening.split()) < MIN_OPENING_WORDS:
        return {"opening": None, "otherChannels": 0}
    # SQL only narrows by the first word anywhere in the title: the opening
    # is punctuation-free ("how to make"), the title may not be ("How To: Make",
    # "🔥 how to make"); the exact comparison is _skeleton_keys below
    first = opening.split()[0]
    like = "%" + first.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    rows = conn.execute("SELECT channel_id, title FROM videos WHERE channel_id != ? "
                        "AND LOWER(title) LIKE ?", (channel_id, like)).fetchall()
    same = {r["channel_id"] for r in rows if TR._skeleton_keys(r["title"])[0] == opening}
    return {"opening": opening, "otherChannels": len(same)}


def format_repeatability(video_id: str, min_similarity: float = DEFAULT_MIN_SIMILARITY,
                         niche: str = None) -> dict:
    import infrastructure.postgres as db
    sim = Q.similar_videos(video_id, niche=niche, limit=NEIGHBOURS, exclude_same_channel=True)
    base = {"videoId": video_id, "minSimilarity": min_similarity, "note": NOTE}
    if sim.get("hint") and not sim.get("similar"):
        return {**base, **R.verdict([]), "reason": "no-embedding", "hint": sim["hint"],
                "titleOpening": {"opening": None, "otherChannels": 0}}
    close = [s for s in sim.get("similar", []) if s["similarity"] >= min_similarity]
    channel_ids = sorted({s["channelId"] for s in close})
    rows = (trends.load_window(period="all", channel_ids=channel_ids,
                               video_ids=[s["videoId"] for s in close]) if channel_ids else [])
    scores = {r["video_id"]: _score(r) for r in rows}
    matches = [{**s, "outlierScore": scores.get(s["videoId"])} for s in close]
    conn = db.get_conn()
    try:
        src = conn.execute("SELECT title, channel_id FROM videos WHERE video_id = ?",
                           (video_id,)).fetchone()
        opening = (_title_opening(conn, video_id, src["title"], src["channel_id"])
                   if src else {"opening": None, "otherChannels": 0})
    finally:
        conn.close()
    return {**base, **R.verdict(matches), "titleOpening": opening}
