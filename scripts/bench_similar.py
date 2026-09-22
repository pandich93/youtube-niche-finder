#!/usr/bin/env python3
"""Stage 06 benchmark: pgvector/HNSW vs the Python-cosine fallback on a
synthetic corpus, so the number isn't just "whatever's in my local DB right
now". Generates N videos across a handful of channels with random unit
vectors, times similar_videos() through both code paths, prints both.

Run against a Postgres that actually has pgvector for a real "after" number:
  docker run -d --name nf-pgvector-bench -p 5544:5432 \
    -e POSTGRES_DB=niches -e POSTGRES_USER=niches -e POSTGRES_PASSWORD=niches \
    pgvector/pgvector:pg16
  NICHE_DATABASE_URL=postgresql://niches:niches@localhost:5544/niches \
    python3 scripts/bench_similar.py --n 50000
  docker rm -f nf-pgvector-bench

Without pgvector, only the "before" (Python) number is meaningful -- the
"after" column will print "n/a (no pgvector on this Postgres)".
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))

import numpy as np                                       # noqa: E402
import infrastructure.postgres as db                       # noqa: E402
from application import search as Q                         # noqa: E402
import infrastructure.embeddings.fastembed_provider as emb   # noqa: E402


def _rand_vec(rng):
    v = rng.normal(size=db.EMBEDDING_DIM).astype(np.float32)
    return v / np.linalg.norm(v)


def seed(n: int, channels: int = 200, batch: int = 500):
    rng = np.random.default_rng(42)
    conn = db.get_conn()
    conn.execute(
        "INSERT INTO channels (channel_id, title, first_seen_at) "
        "SELECT 'UCbench' || g, 'bench ' || g, ? FROM generate_series(0, ?) g "
        "ON CONFLICT (channel_id) DO NOTHING",
        (db.now_iso(), channels - 1))
    conn.commit()
    for start in range(0, n, batch):
        chunk = min(batch, n - start)
        rows = []
        for i in range(start, start + chunk):
            vec = _rand_vec(rng)
            rows.append((f"vbench{i}", f"UCbench{i % channels}", f"bench video {i}",
                        emb.to_blob(vec), db.now_iso()))
        conn.executemany(
            "INSERT INTO videos (video_id, channel_id, title, embedding, first_seen_at) "
            "VALUES (?,?,?,?,?) ON CONFLICT (video_id) DO UPDATE SET embedding=excluded.embedding",
            rows)
        conn.commit()
        if db.pgvector_available():
            for video_id, _, _, blob, _ in rows:
                db.sync_embedding_v(conn, video_id, blob)
            conn.commit()
    conn.close()


def bench(video_id: str, repeats: int = 20):
    times = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        Q.similar_videos(video_id, limit=10)
        times.append(time.perf_counter() - t0)
    return sorted(times)[len(times) // 2]  # median, less noisy than mean


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=50_000, help="synthetic video count")
    ap.add_argument("--repeats", type=int, default=20)
    args = ap.parse_args()

    db.init_db()
    print(f"seeding {args.n} synthetic videos with random embeddings...")
    seed(args.n)
    print("seeded.\n")

    import application.search as search_mod
    orig = db.pgvector_available
    search_mod.db.pgvector_available = lambda: False
    try:
        before = bench("vbench0", args.repeats)
    finally:
        search_mod.db.pgvector_available = orig

    if orig():
        after = bench("vbench0", args.repeats)
        after_s = f"{after * 1000:.2f} ms"
        speedup = f"{before / after:.1f}x" if after > 0 else "n/a"
    else:
        after_s = "n/a (no pgvector on this Postgres)"
        speedup = "n/a"

    print(f"similar_videos(limit=10) over {args.n} candidates, median of {args.repeats} runs:")
    print(f"  Python cosine (before): {before * 1000:.2f} ms")
    print(f"  pgvector HNSW  (after): {after_s}")
    print(f"  speedup: {speedup}")


if __name__ == "__main__":
    main()
