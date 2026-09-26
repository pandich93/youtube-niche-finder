"""Pure-function tests for domain/niche_clusters.py:kmeans (stage 08). No
DB, no network. Run: python3 tests/test_niche_clusters_domain.py (or pytest)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import numpy as np  # noqa: E402

import domain.niche_clusters as NC  # noqa: E402


def _synthetic_blobs(seed=1):
    """3 well-separated blobs in 5D, 10 points each -- k=3 should recover
    them cleanly."""
    rng = np.random.default_rng(seed)
    centers = np.array([
        [10, 0, 0, 0, 0],
        [0, 10, 0, 0, 0],
        [0, 0, 10, 0, 0],
    ], dtype=np.float64)
    points = []
    true_labels = []
    for i, c in enumerate(centers):
        blob = c + rng.normal(scale=0.3, size=(10, 5))
        points.append(blob)
        true_labels += [i] * 10
    return np.vstack(points), np.array(true_labels)


def test_kmeans_recovers_well_separated_synthetic_blobs():
    points, true_labels = _synthetic_blobs()

    out = NC.kmeans(points, k=3)
    labels = np.array(out["labels"])

    # cluster assignment order is arbitrary -- check that every pair of
    # points from the same true blob ends up in the same predicted cluster,
    # and every pair from different blobs ends up in different clusters
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            same_true = true_labels[i] == true_labels[j]
            same_pred = labels[i] == labels[j]
            assert same_true == same_pred, f"mismatch at pair ({i},{j})"


def test_kmeans_returns_k_centroids_of_the_right_dimension():
    points, _ = _synthetic_blobs()
    out = NC.kmeans(points, k=3)
    assert out["centroids"].shape == (3, 5)


def test_kmeans_clamps_k_to_the_number_of_points():
    points = np.array([[0.0, 0.0], [1.0, 1.0]])
    out = NC.kmeans(points, k=10)
    assert out["centroids"].shape[0] == 2
    assert len(out["labels"]) == 2


def test_kmeans_is_deterministic_for_a_fixed_seed():
    points, _ = _synthetic_blobs()
    out1 = NC.kmeans(points, k=3, seed=7)
    out2 = NC.kmeans(points, k=3, seed=7)
    assert out1["labels"] == out2["labels"]


def test_choose_k_targets_roughly_5_items_per_cluster():
    assert NC.choose_k(50) == 10
    assert NC.choose_k(5) == 2  # clamped to k_min
    assert NC.choose_k(1000) == 20  # clamped to k_max


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
