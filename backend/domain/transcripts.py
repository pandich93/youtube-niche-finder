"""Stage 19: turn a transcript pasted from YouTube's own UI into timed
segments, then merge those into ~120-200 word chunks for embedding. Pure
functions, no DB, no network -- YouTube's transcript panel, when you select
and copy it, comes out as alternating lines: a timestamp on its own line
("0:00" or "1:02:33"), then the caption text on the line(s) after it, until
the next timestamp. Untimed paste (a plain paragraph, no timestamp lines at
all) is also accepted, just without per-chunk timing.
"""
import re

_TS_RE = re.compile(r"^(?:\d{1,2}:)?\d{1,2}:\d{2}$")


def _parse_timestamp(s: str) -> int | None:
    parts = s.strip().split(":")
    if len(parts) not in (2, 3) or not all(p.isdigit() for p in parts):
        return None
    parts = [int(p) for p in parts]
    if len(parts) == 2:
        m, sec = parts
        return m * 60 + sec
    h, m, sec = parts
    return h * 3600 + m * 60 + sec


def parse_transcript(raw_text: str) -> dict:
    """{"hasTimestamps": bool, "segments": [{"startSec": int|None, "text": str}],
    "wordCount": int}. A line that isn't part of any recognized timestamp
    block before the first timestamp (a YouTube UI header, stray junk) is
    dropped rather than merged into the first segment."""
    lines = [l.strip() for l in (raw_text or "").splitlines()]
    lines = [l for l in lines if l]
    has_timestamps = any(_TS_RE.match(l) for l in lines)

    segments = []
    if has_timestamps:
        i = 0
        while i < len(lines):
            if not _TS_RE.match(lines[i]):
                i += 1
                continue
            start_sec = _parse_timestamp(lines[i])
            i += 1
            text_parts = []
            while i < len(lines) and not _TS_RE.match(lines[i]):
                text_parts.append(lines[i])
                i += 1
            text = " ".join(text_parts).strip()
            if text:
                segments.append({"startSec": start_sec, "text": text})
    else:
        text = " ".join(lines).strip()
        if text:
            segments.append({"startSec": None, "text": text})

    word_count = sum(len(s["text"].split()) for s in segments)
    return {"hasTimestamps": has_timestamps, "segments": segments, "wordCount": word_count}


def chunk_segments(segments: list, target_words: int = 160, overlap_words: int = 30) -> list:
    """Merge parsed segments into ~target_words-word chunks (120-200 word
    range is the intent; target_words is where the plan's range centers),
    each overlapping the previous by overlap_words so a fact split across a
    chunk boundary is still findable from either side. A chunk's startSec is
    its first word's segment timestamp (None if the transcript was untimed).
    """
    words = []  # (word, its segment's startSec)
    for seg in segments:
        for w in seg["text"].split():
            words.append((w, seg["startSec"]))
    if not words:
        return []

    step = max(1, target_words - overlap_words)
    chunks = []
    i = 0
    while i < len(words):
        window = words[i:i + target_words]
        text = " ".join(w for w, _ in window)
        start_sec = next((s for _, s in window if s is not None), None)
        chunks.append({"startSec": start_sec, "text": text})
        if i + target_words >= len(words):
            break
        i += step
    return chunks
