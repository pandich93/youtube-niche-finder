"""Tests for infrastructure/thumbnails.py (plan 05): the dHash fingerprint is
stable across JPEG re-encoding and far apart for a genuinely different
image, and fetch_thumbnail never raises. Images are generated with Pillow,
the HTTP call is monkeypatched -- no network.
Run with pytest, or directly: python3 tests/test_thumbnails.py
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from PIL import Image, ImageDraw  # noqa: E402

import infrastructure.thumbnails as TH  # noqa: E402
from domain import packaging as PK  # noqa: E402


def _jpeg(img, quality=90):
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _design_a():
    img = Image.new("RGB", (320, 180), (20, 20, 20))
    d = ImageDraw.Draw(img)
    d.rectangle((10, 10, 150, 170), fill=(230, 40, 40))
    d.ellipse((190, 40, 300, 150), fill=(250, 220, 30))
    return img


def _design_b():
    img = Image.new("RGB", (320, 180), (240, 240, 240))
    d = ImageDraw.Draw(img)
    for x in range(0, 320, 40):
        d.rectangle((x, 0, x + 20, 180), fill=(10, 60, 200))
    return img


def test_dhash_is_64_bits_of_hex():
    h = TH.dhash(_jpeg(_design_a()))
    assert len(h) == 16
    int(h, 16)


def test_same_design_reencoded_is_not_a_change():
    a1 = TH.dhash(_jpeg(_design_a(), quality=95))
    a2 = TH.dhash(_jpeg(_design_a().resize((160, 90)), quality=40))
    assert not PK.thumbnail_changed(a1, a2), PK.hamming(a1, a2)


def test_different_design_is_a_change():
    a = TH.dhash(_jpeg(_design_a()))
    b = TH.dhash(_jpeg(_design_b()))
    assert PK.thumbnail_changed(a, b), PK.hamming(a, b)


def test_dhash_of_garbage_bytes_is_none():
    assert TH.dhash(b"not an image") is None
    assert TH.dhash(b"") is None


class _Resp:
    def __init__(self, status, content=b""):
        self.status_code = status
        self.content = content


def test_fetch_returns_bytes_on_200(monkeypatch):
    seen = {}

    def fake_get(url, timeout):
        seen["url"] = url
        return _Resp(200, b"jpegbytes")
    monkeypatch.setattr(TH.requests, "get", fake_get)
    assert TH.fetch_thumbnail("abc123") == b"jpegbytes"
    assert seen["url"] == "https://i.ytimg.com/vi/abc123/mqdefault.jpg"


def test_fetch_returns_none_on_404_or_network_error(monkeypatch):
    monkeypatch.setattr(TH.requests, "get", lambda url, timeout: _Resp(404))
    assert TH.fetch_thumbnail("gone") is None

    def boom(url, timeout):
        raise TH.requests.ConnectionError("down")
    monkeypatch.setattr(TH.requests, "get", boom)
    assert TH.fetch_thumbnail("abc123") is None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
