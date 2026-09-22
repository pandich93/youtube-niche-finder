"""Pure-function tests for domain/transcripts.py (stage 19). No DB, no
network. Run: python3 tests/test_transcripts_domain.py (or pytest)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import domain.transcripts as T  # noqa: E402


def test_mm_ss_timestamps_are_parsed_into_segments():
    raw = "0:00\nHello there\n0:05\nwelcome to the video"
    out = T.parse_transcript(raw)
    assert out["hasTimestamps"] is True
    assert out["segments"] == [
        {"startSec": 0, "text": "Hello there"},
        {"startSec": 5, "text": "welcome to the video"},
    ]


def test_hh_mm_ss_timestamps_are_parsed():
    raw = "1:02:33\nlong video moment"
    out = T.parse_transcript(raw)
    assert out["segments"][0]["startSec"] == 3600 + 120 + 33


def test_multi_line_caption_text_is_joined_into_one_segment():
    raw = "0:00\nfirst line\nsecond line\n0:10\nnext segment"
    out = T.parse_transcript(raw)
    assert out["segments"][0]["text"] == "first line second line"


def test_no_timestamps_falls_back_to_one_untimed_segment():
    raw = "This is a plain paragraph with no timing information at all."
    out = T.parse_transcript(raw)
    assert out["hasTimestamps"] is False
    assert len(out["segments"]) == 1
    assert out["segments"][0]["startSec"] is None


def test_word_count_sums_across_segments():
    raw = "0:00\none two three\n0:05\nfour five"
    out = T.parse_transcript(raw)
    assert out["wordCount"] == 5


def test_empty_input_gives_no_segments():
    out = T.parse_transcript("")
    assert out["segments"] == []
    assert out["wordCount"] == 0


def test_junk_before_the_first_timestamp_is_dropped():
    raw = "Показать больше\n0:00\nreal caption text"
    out = T.parse_transcript(raw)
    assert len(out["segments"]) == 1
    assert out["segments"][0]["text"] == "real caption text"


def test_chunking_splits_long_transcripts_into_target_sized_windows():
    segments = [{"startSec": 0, "text": " ".join(f"w{i}" for i in range(400))}]
    chunks = T.chunk_segments(segments, target_words=160, overlap_words=30)
    assert len(chunks) >= 3
    assert all(len(c["text"].split()) <= 160 for c in chunks)


def test_chunking_overlaps_consecutive_chunks():
    segments = [{"startSec": 0, "text": " ".join(f"w{i}" for i in range(200))}]
    chunks = T.chunk_segments(segments, target_words=160, overlap_words=30)
    first_words = chunks[0]["text"].split()
    second_words = chunks[1]["text"].split()
    overlap = set(first_words[-30:]) & set(second_words[:30])
    assert len(overlap) > 0


def test_chunking_preserves_each_chunks_starting_timestamp():
    segments = [{"startSec": 0, "text": " ".join(f"a{i}" for i in range(160))},
               {"startSec": 100, "text": " ".join(f"b{i}" for i in range(160))}]
    chunks = T.chunk_segments(segments, target_words=160, overlap_words=0)
    assert chunks[0]["startSec"] == 0
    assert chunks[1]["startSec"] == 100


def test_chunking_empty_segments_gives_no_chunks():
    assert T.chunk_segments([]) == []


if __name__ == "__main__":
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
