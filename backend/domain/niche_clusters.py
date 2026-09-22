"""Stage 08: plain numpy k-means -- the plan for this stage says to confirm
a new heavy dependency (scikit-learn/HDBSCAN) with the user before adding
one; this avoids needing either at all. k-means++ initialization (spreads
the starting centroids out instead of picking them uniformly at random, the
usual fix for k-means's sensitivity to bad initial centroids), then
standard Lloyd's-algorithm iteration to convergence or max_iter.
"""
import numpy as np


def kmeans(vectors: np.ndarray, k: int, max_iter: int = 100, seed: int = 42) -> dict:
    """vectors: (n, dim) array, one row per item to cluster (this stage
    passes one row per channel, its video embeddings averaged). k is
    clamped to n if there are fewer points than clusters requested.
    Returns {"labels": [int, ...] (len n), "centroids": (k, dim) array}."""
    n = vectors.shape[0]
    k = max(1, min(k, n))
    rng = np.random.default_rng(seed)

    # k-means++ init
    first = rng.integers(n)
    centroids = [vectors[first]]
    for _ in range(k - 1):
        c = np.array(centroids)
        dists = np.min(np.linalg.norm(vectors[:, None, :] - c[None, :, :], axis=2), axis=1)
        total = dists.sum()
        probs = (dists / total) if total > 0 else np.full(n, 1.0 / n)
        centroids.append(vectors[rng.choice(n, p=probs)])
    centroids = np.array(centroids)

    labels = np.full(n, -1)
    for iteration in range(max_iter):
        dists = np.linalg.norm(vectors[:, None, :] - centroids[None, :, :], axis=2)
        new_labels = dists.argmin(axis=1)
        if iteration > 0 and np.array_equal(new_labels, labels):
            labels = new_labels
            break
        labels = new_labels
        for i in range(k):
            mask = labels == i
            if mask.any():
                centroids[i] = vectors[mask].mean(axis=0)

    return {"labels": labels.tolist(), "centroids": centroids}


def choose_k(n_items: int, target_cluster_size: int = 5, k_min: int = 2, k_max: int = 20) -> int:
    """Default cluster count when the caller doesn't pin one -- aims for
    roughly target_cluster_size items per cluster, clamped to a sane range
    so a small corpus doesn't get shredded into singleton clusters and a
    huge one doesn't get one giant blob."""
    return max(k_min, min(k_max, n_items // target_cluster_size))
