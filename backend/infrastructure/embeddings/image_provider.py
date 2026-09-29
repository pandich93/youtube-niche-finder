"""Local CLIP embeddings for thumbnails (plan 13): images and short text land
in one 512-d space, so "thumbnails like this one" and "thumbnails that look
like 'red arrow, shocked face'" are the same cosine search. ONNX on CPU via
fastembed, no PyTorch. Both models download once on first use (~0.34 GB for
images, ~0.25 GB for text) into fastembed's cache (FASTEMBED_CACHE_PATH, the
/models volume in Docker) and load lazily -- importing this module costs
nothing, so the worker pays for the model only when WORKER_THUMB_EMBED is on.

CLIP compares style and content, not the words written on a thumbnail.
"""
import io
from functools import lru_cache

import numpy as np

IMAGE_MODEL = "Qdrant/clip-ViT-B-32-vision"
TEXT_MODEL = "Qdrant/clip-ViT-B-32-text"
DIM = 512


@lru_cache(maxsize=1)
def _image_model():
    from fastembed import ImageEmbedding
    return ImageEmbedding(model_name=IMAGE_MODEL)


@lru_cache(maxsize=1)
def _text_model():
    from fastembed import TextEmbedding
    return TextEmbedding(model_name=TEXT_MODEL)


def _unit(v) -> np.ndarray:
    v = np.asarray(v, dtype=np.float32)
    n = float(np.linalg.norm(v))
    return v / n if n else v


def embed_images(images) -> list:
    """JPEG/PNG bytes -> unit vectors, None where the bytes are not a
    readable image (one broken thumbnail must not sink a batch)."""
    from PIL import Image, UnidentifiedImageError
    decoded, slots = [], []
    for i, raw in enumerate(images):
        if not raw:
            continue
        try:
            decoded.append(Image.open(io.BytesIO(raw)).convert("RGB"))
            slots.append(i)
        except (UnidentifiedImageError, OSError, ValueError):
            continue
    out = [None] * len(images)
    if decoded:
        for i, vec in zip(slots, _image_model().embed(decoded)):
            out[i] = _unit(vec)
    return out


def embed_text(text: str) -> np.ndarray:
    return _unit(next(iter(_text_model().embed([text]))))


def to_blob(vec) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def to_pgvector_literal(vec) -> str:
    return "[" + ",".join(f"{float(x):.8f}" for x in vec) + "]"
