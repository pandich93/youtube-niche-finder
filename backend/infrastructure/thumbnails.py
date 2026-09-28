"""Download a video's thumbnail from YouTube's image CDN and fingerprint it
(plan 05). This is not the Data API: i.ytimg.com costs no quota, but it is a
request to Google, so the worker only does it for tracked channels (see
PRIVACY.md).

Why fingerprint at all: the thumbnail URL the API returns never changes when
a creator uploads a new thumbnail -- the same URL starts serving the new
image -- so comparing URLs (infrastructure/postgres/repositories.py
record_video_stats) never sees a thumbnail swap. A 64-bit difference hash of
the image does.
"""
import io
import logging

import requests

log = logging.getLogger(__name__)

# 320x180, always present (maxresdefault is missing for many videos) and
# ~10-20 KB -- small enough to keep every version in thumbnail_archive.
URL = "https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"
TIMEOUT = 10


def fetch_thumbnail(video_id: str):
    """JPEG bytes of the current thumbnail, or None on 404/any network error.
    Never raises: one unreachable image must not stop a worker batch."""
    try:
        resp = requests.get(URL.format(video_id=video_id), timeout=TIMEOUT)
    except requests.RequestException as e:
        log.warning("thumbnail fetch failed for %s: %s", video_id, e)
        return None
    if resp.status_code != 200 or not resp.content:
        return None
    return resp.content


def dhash(image_bytes: bytes):
    """64-bit difference hash as 16 hex chars: grayscale 9x8, one bit per
    "is this pixel brighter than its right neighbour". Survives CDN
    re-encoding and resizing; a different design flips dozens of bits.
    None when the bytes are not a readable image."""
    if not image_bytes:
        return None
    from PIL import Image, UnidentifiedImageError  # Pillow ships with fastembed
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("L").resize((9, 8), Image.LANCZOS)
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    px = img.tobytes()  # mode "L": one byte per pixel, row by row
    bits = 0
    for row in range(8):
        for col in range(8):
            left, right = px[row * 9 + col], px[row * 9 + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return f"{bits:016x}"
