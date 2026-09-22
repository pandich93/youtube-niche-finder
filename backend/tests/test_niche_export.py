"""Tests for application/niche_export.py (stage 18) and its HTTP endpoint.
Same throwaway-schema setup as test_tags.py. Run with pytest, or directly:
python3 tests/test_niche_export.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

from fastapi.testclient import TestClient  # noqa: E402

import infrastructure.postgres as db            # noqa: E402
from application import niche_export as NE        # noqa: E402
import interfaces.http.api as api                   # noqa: E402

client = TestClient(api.app)


def setup_module(_=None):
    db.init_db()


def _channel(cid, subs=1000):
    return {
        "channel_id": cid, "title": cid, "custom_url": f"@{cid}", "country": None,
        "description": "", "default_language": None, "subscriber_count": subs,
        "video_count": 1, "view_count": 1000, "thumbnail": None,
        "published_at": None, "topic_categories": None, "keywords": None,
        "uploads_playlist": None, "hidden_subs": 0,
    }


def _video(vid, cid, title, views=1000):
    return {
        "video_id": vid, "channel_id": cid, "title": title, "description": "",
        "published_at": "2026-01-01T00:00:00Z", "duration_seconds": 300,
        "view_count": views, "like_count": 10, "comment_count": 2, "thumbnail": None,
        "tags": "[]", "default_language": "en", "embedding": None,
        "updated_at": "2026-01-01T00:00:00Z", "category_id": None, "region": None,
        "is_short": 0, "topic_categories": None, "live_content": None,
    }


def _seed(video_id, channel_id, title, niche_slug, views=1000):
    conn = db.get_conn()
    db.upsert_channel(conn, _channel(channel_id))
    db.upsert_video(conn, _video(video_id, channel_id, title, views))
    db.upsert_niche(conn, niche_slug, niche_slug, niche_slug)
    db.link_video_niche(conn, video_id, niche_slug)
    conn.commit()
    conn.close()


def test_export_rows_on_an_empty_niche_gives_an_empty_list():
    assert NE.export_rows("no-such-niche-export") == []


def test_export_rows_includes_every_column_even_without_stage_14_or_16():
    _seed("vexp1", "UCexp0000000000000000001", "a title", "n-export-basic")

    rows = NE.export_rows("n-export-basic")

    assert len(rows) == 1
    row = rows[0]
    assert set(row.keys()) == set(NE.COLUMNS)
    assert row["video_id"] == "vexp1"
    assert row["channel"] == "UCexp0000000000000000001"
    assert row["tags"] == ""  # no video_tags rows -- empty, not missing


def test_export_rows_includes_manual_tags_joined_by_semicolon():
    _seed("vexp2", "UCexp0000000000000000002", "tagged video", "n-export-tags")
    conn = db.get_conn()
    db.upsert_video_tag(conn, "vexp2", "theme", "fear", "manual")
    db.upsert_video_tag(conn, "vexp2", "theme", "howto", "manual")
    conn.commit()
    conn.close()

    rows = NE.export_rows("n-export-tags")

    assert rows[0]["tags"] == "fear;howto"


def test_export_rows_excludes_unaccepted_proposed_tags():
    _seed("vexp3", "UCexp0000000000000000003", "proposed video", "n-export-proposed")
    conn = db.get_conn()
    db.upsert_video_tag(conn, "vexp3", "theme", "real-tag", "manual")
    db.upsert_video_tag(conn, "vexp3", "theme", "not-yet-accepted", "llm", proposed=True)
    conn.commit()
    conn.close()

    rows = NE.export_rows("n-export-proposed")

    assert rows[0]["tags"] == "real-tag"


def test_export_niche_tsv_uses_tab_delimiter_and_no_bom():
    _seed("vexp4", "UCexp0000000000000000004", "tsv video", "n-export-tsv")

    out = NE.export_niche("n-export-tsv", fmt="tsv")

    assert out["filename"].endswith(".tsv")
    assert out["rowCount"] == 1
    text = out["content"].decode("utf-8")
    assert not text.startswith("﻿")
    header = text.splitlines()[0]
    assert "\t" in header
    assert "," not in header.split("\t")[0]  # not comma-separated


def test_export_niche_csv_has_utf8_bom_for_excel():
    _seed("vexp5", "UCexp0000000000000000005", "csv video", "n-export-csv")

    out = NE.export_niche("n-export-csv", fmt="csv")

    assert out["filename"].endswith(".csv")
    assert out["content"].startswith(b"\xef\xbb\xbf")  # UTF-8 BOM


def test_export_niche_escapes_tabs_newlines_and_quotes_in_titles():
    _seed("vexp6", "UCexp0000000000000000006",
         'A "Weird" Title\twith tab and\nnewline', "n-export-escape")

    tsv_out = NE.export_niche("n-export-escape", fmt="tsv")
    text = tsv_out["content"].decode("utf-8")
    # embedded \t/\n/" in the title must not split the record or shift
    # columns -- round-trip through a real TSV parser and check there's
    # still exactly one data row with the title intact
    import csv as csv_mod
    import io as io_mod
    reader = csv_mod.DictReader(io_mod.StringIO(text), delimiter="\t")
    parsed_rows = list(reader)
    assert len(parsed_rows) == 1
    assert "Weird" in parsed_rows[0]["title"]


def test_export_niche_rejects_an_unknown_format():
    try:
        NE.export_niche("n-export-badfmt", fmt="xlsx")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_http_endpoint_sets_content_disposition_and_returns_the_file():
    _seed("vexp7", "UCexp0000000000000000007", "http export video", "n-export-http")

    r = client.get("/api/niche/n-export-http/export.tsv")

    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "")
    assert "n-export-http_videos_" in r.headers["content-disposition"]
    assert b"vexp7" in r.content


def test_http_endpoint_rejects_an_unknown_format_with_400():
    r = client.get("/api/niche/n-export-http/export.xlsx")
    assert r.status_code == 400


if __name__ == "__main__":
    setup_module()

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except Exception as e:
            failed += 1
            import traceback
            print(f"  FAIL  {fn.__name__}: {e}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
