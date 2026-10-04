"""Format repeatability (plan 21): did a format work for several independent
channels, or was it one channel's luck? Pure, no DB.

Input: videos of OTHER channels that read close to the one being checked
(embedding neighbours), each with its outlier score. One channel counts
once, by its best video, so a channel repeating its own hit is not
"repeated". Verdicts:

  repeatable  3+ channels got an outlier >= HIT_SCORE with it
  mixed       1-2 channels did, the rest did not
  one_off     nobody else got an outlier with it
  unknown     fewer than MIN_VIDEOS similar videos or MIN_CHANNELS channels
              -- "we did not collect enough", not "it never worked"

An estimate of niche-finder over the corpus we collected, not YouTube data.
"""
import statistics as st

HIT_SCORE = 2.0
MIN_HIT_CHANNELS = 3
MIN_VIDEOS = 5
MIN_CHANNELS = 3
MAX_EXAMPLES = 8


def verdict(matches) -> dict:
    """matches: [{channelId, outlierScore, videoId, title, similarity, ...}]."""
    scored = [x for x in matches or [] if x.get("outlierScore") is not None]
    best = {}
    for x in scored:
        cur = best.get(x["channelId"])
        if cur is None or x["outlierScore"] > cur["outlierScore"]:
            best[x["channelId"]] = x
    channels = len(best)
    hit = [x for x in best.values() if x["outlierScore"] >= HIT_SCORE]
    examples = sorted(best.values(), key=lambda x: x["outlierScore"], reverse=True)
    out = {"verdict": None, "reason": None, "similarVideos": len(scored), "channels": channels,
           "channelsHit": len(hit), "hitScore": HIT_SCORE,
           "medianChannelBest": (round(st.median(x["outlierScore"] for x in best.values()), 2)
                                 if best else None),
           "examples": examples[:MAX_EXAMPLES]}
    if len(scored) < MIN_VIDEOS or channels < MIN_CHANNELS:
        out["verdict"], out["reason"] = "unknown", "few-similar-videos"
    elif len(hit) >= MIN_HIT_CHANNELS:
        out["verdict"] = "repeatable"
    elif hit:
        out["verdict"] = "mixed"
    else:
        out["verdict"] = "one_off"
    return out
