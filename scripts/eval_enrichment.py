#!/usr/bin/env python3
"""Compare stage 03's LLM labels against human ground truth -- accuracy
numbers, not a pass/fail gate. No network, no YouTube key: only reads
Postgres (NICHE_DATABASE_URL, same as backend/tests).

Channels: needs a JSON fixture you fill in by hand --
  {"UCxxxxxxxxxxxxxxxxxxxxxx": {"is_faceless": true, "content_format": "voiceover_stock"}, ...}
compared field-by-field against channels.llm_labels.

Videos: no fixture needed -- every video_tags row with source in
('manual', 'claude-mcp') is ground truth for its (video_id, tag_group),
compared against that same video's source='llm' tags (proposed or not) in
the same group. Accuracy is reported per tag_group as the share of videos
where the LLM's tag set exactly matches the human one.

Usage:
  python3 scripts/eval_enrichment.py --channels fixtures/channel_labels.json
  python3 scripts/eval_enrichment.py --videos-only
  python3 scripts/eval_enrichment.py --channels fixtures/channel_labels.json --videos-only=false
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))

import infrastructure.postgres as db  # noqa: E402

CHANNEL_FIELDS = ("is_faceless", "content_format")


def eval_channels(fixture_path: str) -> None:
    with open(fixture_path, encoding="utf-8") as f:
        expected = json.load(f)
    if not expected:
        print("channels: фикстура пуста")
        return

    conn = db.get_conn()
    rows = conn.execute(
        "SELECT channel_id, llm_labels FROM channels WHERE channel_id = ANY(?)",
        (list(expected.keys()),)).fetchall()
    conn.close()
    actual = {r["channel_id"]: (r["llm_labels"] or {}) for r in rows}

    print(f"\n=== Каналы: {len(expected)} в фикстуре ===")
    for field in CHANNEL_FIELDS:
        total = matched = unlabeled = 0
        for channel_id, exp in expected.items():
            if field not in exp:
                continue
            total += 1
            got = actual.get(channel_id) or {}
            if field not in got:
                unlabeled += 1
                continue
            if got[field] == exp[field]:
                matched += 1
        pct = round(100 * matched / total, 1) if total else 0.0
        suffix = f" ({unlabeled} ещё не размечены)" if unlabeled else ""
        print(f"  {field}: {matched}/{total} = {pct}%{suffix}")


def eval_videos() -> None:
    conn = db.get_conn()
    manual_rows = conn.execute(
        "SELECT video_id, tag_group, tag FROM video_tags "
        "WHERE source IN ('manual', 'claude-mcp')").fetchall()
    llm_rows = conn.execute(
        "SELECT video_id, tag_group, tag FROM video_tags WHERE source = 'llm'").fetchall()
    conn.close()

    manual = {}
    for r in manual_rows:
        manual.setdefault((r["video_id"], r["tag_group"]), set()).add(r["tag"])
    llm = {}
    for r in llm_rows:
        llm.setdefault((r["video_id"], r["tag_group"]), set()).add(r["tag"])

    by_group = {}
    for (video_id, tag_group), manual_tags in manual.items():
        stats = by_group.setdefault(tag_group, {"total": 0, "matched": 0, "unlabeled": 0})
        stats["total"] += 1
        llm_tags = llm.get((video_id, tag_group))
        if llm_tags is None:
            stats["unlabeled"] += 1
        elif llm_tags == manual_tags:
            stats["matched"] += 1

    print(f"\n=== Видео: {len(manual)} (video_id, tag_group) с ручной разметкой ===")
    if not by_group:
        print("  нет video_tags с source in (manual, claude-mcp) -- нечего сравнивать")
        return
    for tag_group, s in sorted(by_group.items()):
        pct = round(100 * s["matched"] / s["total"], 1) if s["total"] else 0.0
        suffix = f" ({s['unlabeled']} ещё не тегированы LLM)" if s["unlabeled"] else ""
        print(f"  {tag_group}: {s['matched']}/{s['total']} = {pct}%{suffix}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--channels", metavar="FIXTURE.json",
                    help="путь к JSON-фикстуре ожидаемой разметки каналов")
    ap.add_argument("--videos-only", action="store_true",
                    help="пропустить сравнение каналов, даже если --channels передан")
    args = ap.parse_args()

    if args.channels and not args.videos_only:
        eval_channels(args.channels)
    eval_videos()


if __name__ == "__main__":
    main()
