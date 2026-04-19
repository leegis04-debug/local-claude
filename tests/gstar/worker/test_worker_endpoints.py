"""`/worker/*` 개별 엔드포인트 smoke 테스트 (Phase H10 tick 분리).

외부 Gateway/Qdrant/Neo4j/NAS 없는 환경에서도 안전하게 통과해야 한다 —
ASST_TOKEN 미설정·NAS root 미존재 조건을 다 포함한 degraded 경로가 200 을
반환하는지 확인한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import gstar.serve as serve_mod


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GSTAR_HOME", str(tmp_path))
    monkeypatch.setattr(serve_mod, "_state", None)
    # 외부 서비스 의존 env 정리 — 각 엔드포인트가 graceful no-op 해야 함
    monkeypatch.delenv("ASST_TOKEN", raising=False)
    monkeypatch.setenv("CODE_REPOS_ROOT", str(tmp_path / "no_such_dir"))
    with TestClient(serve_mod.app) as c:
        yield c


def test_worker_tick_default_light(client):
    r = client.post("/worker/tick", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "success"
    assert sorted(body["steps"]["_requested"]) == ["community", "procedures"]


def test_worker_tick_steps_wildcard(client):
    r = client.post("/worker/tick", json={"steps": ["*"]})
    assert r.status_code == 200
    body = r.json()
    # 전부 요청됨. gate(env+data) 로 실제 결과 dict 는 비어도 OK.
    assert "code_repos" in body["steps"]["_requested"]
    assert "qdrant_mirror" in body["steps"]["_requested"]


def test_worker_tick_steps_filtered(client):
    r = client.post("/worker/tick", json={"steps": ["community"]})
    assert r.status_code == 200
    body = r.json()
    assert body["steps"]["_requested"] == ["community"]
    assert "procedures" not in body["steps"]


def test_worker_legacy_bridge_without_token(client):
    """ASST_TOKEN 없을 때 graceful (errors 1, scanned 0)."""
    r = client.post("/worker/legacy_bridge", json={"limit": 10})
    assert r.status_code == 200
    body = r.json()
    assert body["scanned"] == 0
    assert body["errors"] >= 1


def test_worker_code_repos_missing_root(client):
    """NAS root 없을 때 scanned=0 ingested=0 graceful 반환."""
    r = client.post("/worker/code_repos", json={"per_tick": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["scanned"] == 0
    assert body["ingested"] == 0


def test_worker_legacy_fact_without_data(client):
    """qdrant_meta jsonl 없을 때 chunks_scanned=0."""
    r = client.post("/worker/legacy_fact", json={"per_tick": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["chunks_scanned"] == 0


def test_worker_neo4j_mirror_without_token(client):
    """ASST_TOKEN 없을 때 errors 에 메시지 있고 200 반환."""
    r = client.post("/worker/neo4j_mirror", json={"limit_nodes": 1, "limit_edges": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["nodes_upserted"] == 0
    assert any("ASST_TOKEN" in e for e in body["errors"]) or body["nodes_scanned"] == 0
