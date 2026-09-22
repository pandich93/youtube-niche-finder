"""Local, free multilingual embeddings for semantic niche search (no external API)."""
import numpy as np
from functools import lru_cache
from fastembed import TextEmbedding

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


@lru_cache(maxsize=1)
def _model():
    return TextEmbedding(model_name=MODEL_NAME)


def embed(texts):
    """texts: str or list[str] -> list of float32 np.ndarray (already normalized)."""
    single = isinstance(texts, str)
    if single:
        texts = [texts]
    vecs = list(_model().embed(texts))
    vecs = [np.asarray(v, dtype=np.float32) for v in vecs]
    return vecs[0] if single else vecs


def to_blob(vec: np.ndarray) -> bytes:
    return vec.astype(np.float32).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def to_pgvector_literal(vec: np.ndarray) -> str:
    """Text form pgvector's input parser accepts for a ::vector cast --
    stage 06's migration/dual-write path uses this instead of adding the
    separate `pgvector` psycopg2 adapter package for one conversion."""
    return "[" + ",".join(f"{float(x):.8f}" for x in vec) + "]"


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = (np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0:
        return 0.0
    return float(np.dot(a, b) / denom)
