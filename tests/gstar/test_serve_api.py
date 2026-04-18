"""FastAPI 서버 엔드포인트 단위 테스트."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import gstar.serve as serve_mod


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GSTAR_HOME", str(tmp_path))
    # 기존 singleton 초기화
    monkeypatch.setattr(serve_mod, "_state", None)
    with TestClient(serve_mod.app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["nodes"] == 0
    assert "personal" in data["namespaces"]


def test_insert_node_and_get(client):
    r = client.post("/nodes", json={
        "kind": "fact",
        "text": "테스트 사실",
        "source_namespace": "test",
    })
    assert r.status_code == 200
    nid = r.json()["id"]

    r = client.get(f"/nodes/{nid}")
    assert r.status_code == 200
    body = r.json()
    assert body["text"] == "테스트 사실"
    assert body["content_hash"]
    assert body["source_namespace"] == "test"


def test_list_nodes_filter(client):
    client.post("/nodes", json={"kind": "fact", "text": "A", "source_namespace": "ns1"})
    client.post("/nodes", json={"kind": "entity", "text": "B", "source_namespace": "ns1"})
    client.post("/nodes", json={"kind": "fact", "text": "C", "source_namespace": "ns2"})

    r = client.get("/nodes", params={"namespace": "ns1"})
    assert r.status_code == 200
    items = r.json()
    assert len(items) == 2

    r = client.get("/nodes", params={"kind": "entity"})
    assert len(r.json()) == 1


def test_create_goal_and_list(client):
    r = client.post("/goals", json={"text": "G 테스트", "kind": "proposal"})
    assert r.status_code == 200
    gid = r.json()["id"]
    r = client.get("/goals")
    assert len(r.json()) == 1
    assert r.json()[0]["id"] == gid


def test_verify_chain_empty(client):
    r = client.get("/verify/chain", params={"ns": "personal"})
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["checked"] == 0


def test_verify_chain_after_insert(client):
    for i in range(3):
        client.post("/nodes", json={
            "kind": "fact", "text": f"사실 {i}", "source_namespace": "t",
        })
    r = client.get("/verify/chain", params={"ns": "t"})
    data = r.json()
    assert data["ok"] is True
    assert data["checked"] == 3


def test_get_nonexistent_node(client):
    r = client.get("/nodes/nope")
    assert r.status_code == 404
