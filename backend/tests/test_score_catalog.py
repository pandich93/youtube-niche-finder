"""Tests for domain/score_catalog.py (plan 23): every number on screen says
whether it is YouTube data or an estimate of niche-finder, how it is
computed and from how much data. Pure, plus the HTTP/MCP doors.
Run with pytest, or directly: python3 tests/test_score_catalog.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import schema_scope  # noqa: F401,E402

import pytest  # noqa: E402

from domain import metrics as M  # noqa: E402
from domain import score_catalog as C  # noqa: E402


def test_every_entry_is_complete_and_labelled():
    for key, e in C.CATALOG.items():
        assert e["source"] in (C.YOUTUBE, C.ESTIMATE), key
        assert e["name"] and e["formula"] and isinstance(e["inputs"], list), key
        assert "minSample" in e, key


def test_youtube_data_and_estimates_are_both_there():
    sources = {e["source"] for e in C.CATALOG.values()}
    assert sources == {C.YOUTUBE, C.ESTIMATE}
    assert C.CATALOG["views"]["source"] == C.YOUTUBE
    assert C.CATALOG["outlierScore"]["source"] == C.ESTIMATE


def test_thresholds_come_from_the_code():
    assert f"{M.DEFAULT_BASELINE_N} предыдущих" in C.CATALOG["outlierScore"]["formula"]
    assert f"×{M.MONETISATION_DISCOUNT}" in C.CATALOG["revenueRange"]["formula"].replace("× ", "×")


def test_one_key_and_an_unknown_key():
    assert list(C.catalog("vsr")) == ["vsr"]
    with pytest.raises(KeyError):
        C.catalog("nope")


def test_http_and_mcp_doors():

    from fastapi.testclient import TestClient

    import interfaces.http.api as api
    import interfaces.mcp.server as srv
    client = TestClient(api.app)
    d = client.get("/api/scores").json()
    assert "outlierScore" in d["scores"] and d["sources"]["niche-finder"]
    assert client.get("/api/scores?key=nope").status_code == 404
    assert list(srv.explain_scores("vsr")["vsr"]) == list(C.CATALOG["vsr"])
