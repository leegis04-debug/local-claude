"""GClient TestClient 기반 통합 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import gstar.serve as serve_mod
from gstar.client import GClient


@pytest.fixture
def gclient(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GSTAR_HOME", str(tmp_path))
    monkeypatch.setattr(serve_mod, "_state", None)
    with TestClient(serve_mod.app) as tc:
        c = GClient(base_url="http://testserver")
        c.http = tc  # fastapi TestClient 이 httpx.Client 호환
        yield c


def test_health_and_goals(gclient):
    h = gclient.health()
    assert h["ok"] is True

    goal = gclient.create_goal("G 사업계획서", kind="proposal")
    assert goal["text"] == "G 사업계획서"


def test_verify_chain_via_client(gclient):
    res = gclient.verify_chain("personal")
    assert res["ok"] is True
    assert res["checked"] == 0


def test_get_missing_node_returns_none(gclient):
    assert gclient.get_node("no-such-id") is None
