"""/ingest/web 엔드포인트 + SBERT 싱글톤 단위 테스트.

실제 SBERT 로딩은 무거워 `monkeypatch` 로 stub 처리한다 — 엔드포인트 계약,
URL dedupe TTL, 인덱스 갱신, AppState.embedder 싱글톤 재사용 검증 중심.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import gstar.serve as serve_mod
from gstar.ingest.pipeline import IngestReport


class _FakeEmbedder:
    """SBertEmbedder 스텁. encode 는 고정 dim 0 벡터. ingest_path 내부에서만 호출됨."""

    dim = 8
    calls: int = 0

    def encode(self, texts):
        _FakeEmbedder.calls += len(texts)
        import numpy as np
        return np.zeros((len(texts), self.dim), dtype="float32")


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GSTAR_HOME", str(tmp_path))
    monkeypatch.setenv("GSTAR_PRELOAD_EMBEDDER", "off")  # lifespan 에서 SBERT 로딩 스킵
    monkeypatch.setattr(serve_mod, "_state", None)
    _FakeEmbedder.calls = 0

    # SBertEmbedder import 지점(serve.py AppState.embedder property) 을 스텁으로
    import gstar.embedding.sbert as sbert_mod
    monkeypatch.setattr(sbert_mod, "SBertEmbedder", _FakeEmbedder)

    with TestClient(serve_mod.app) as c:
        yield c


def _fake_ingest_report(facts=3, entities=2, edges=1):
    return IngestReport(facts=facts, entities=entities, edges=edges, files_scanned=1)


def test_ingest_web_happy_path(client, monkeypatch):
    """신규 URL 2건 → ingest_path 1회 호출, 인덱스 기록."""
    captured = {}

    def _fake_ingest_path(staging, store, faiss, embedder, namespace, track, **kw):
        captured["staging"] = Path(staging)
        captured["namespace"] = namespace
        # staging 에 md 2개가 실제로 작성됐는지 확인
        captured["mds"] = sorted(p.name for p in Path(staging).iterdir())
        return _fake_ingest_report(facts=5, entities=3, edges=2)

    monkeypatch.setattr("gstar.ingest.pipeline.ingest_path", _fake_ingest_path)

    r = client.post("/ingest/web", json={
        "query": "KAMIS 농산물",
        "namespace": "web_cache",
        "ttl_days": 30,
        "results": [
            {"url": "https://a.com", "title": "A", "snippet": "as", "content": "A body"},
            {"url": "https://b.com", "title": "B", "snippet": "bs", "content": "B body"},
        ],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["facts"] == 5
    assert body["entities"] == 3
    assert body["edges"] == 2
    assert body["skipped_dedupe"] == 0
    assert captured["namespace"] == "web_cache"
    assert len(captured["mds"]) == 2


def test_ingest_web_ttl_dedupe(client, monkeypatch):
    """같은 URL 재호출 시 TTL 내면 skipped, ingest_path 호출 안 됨."""
    calls = {"n": 0}

    def _fake_ingest_path(*a, **kw):
        calls["n"] += 1
        return _fake_ingest_report(facts=1)

    monkeypatch.setattr("gstar.ingest.pipeline.ingest_path", _fake_ingest_path)

    body = {
        "query": "q",
        "namespace": "web_cache",
        "ttl_days": 30,
        "results": [{"url": "https://a.com", "title": "A", "snippet": "s", "content": "c"}],
    }
    r1 = client.post("/ingest/web", json=body)
    r2 = client.post("/ingest/web", json=body)
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["skipped_dedupe"] == 0
    assert r2.json()["skipped_dedupe"] == 1
    assert r2.json()["facts"] == 0
    assert calls["n"] == 1  # 두 번째는 ingest_path 호출 안 됨


def test_ingest_web_ttl_zero_reingest(client, monkeypatch):
    """ttl_days=0 → dedupe 무시하고 매번 재ingest."""
    calls = {"n": 0}
    monkeypatch.setattr(
        "gstar.ingest.pipeline.ingest_path",
        lambda *a, **kw: (calls.__setitem__("n", calls["n"] + 1) or _fake_ingest_report(facts=1)),
    )

    body = {
        "query": "q",
        "namespace": "web_cache",
        "ttl_days": 0,
        "results": [{"url": "https://a.com", "title": "A", "snippet": "s", "content": "c"}],
    }
    client.post("/ingest/web", json=body)
    client.post("/ingest/web", json=body)
    assert calls["n"] == 2


def test_ingest_web_empty_results(client, monkeypatch):
    calls = {"n": 0}
    monkeypatch.setattr(
        "gstar.ingest.pipeline.ingest_path",
        lambda *a, **kw: (calls.__setitem__("n", calls["n"] + 1) or _fake_ingest_report()),
    )
    r = client.post("/ingest/web", json={"query": "q", "namespace": "ns", "results": []})
    assert r.status_code == 200
    body = r.json()
    assert body["facts"] == 0 and body["skipped_dedupe"] == 0
    assert calls["n"] == 0


def test_ingest_web_url_index_persists(client, monkeypatch, tmp_path):
    """인덱스 파일이 GSTAR_HOME 아래 생성 + 재시작 후에도 유지."""
    monkeypatch.setattr("gstar.ingest.pipeline.ingest_path", lambda *a, **kw: _fake_ingest_report())
    r = client.post("/ingest/web", json={
        "query": "q", "namespace": "ns", "ttl_days": 30,
        "results": [{"url": "https://persist.com", "title": "P", "snippet": "x", "content": "x"}],
    })
    assert r.status_code == 200
    idx_path = tmp_path / "web_url_index.json"
    assert idx_path.exists()
    data = json.loads(idx_path.read_text())
    assert any(v.get("url") == "https://persist.com" for v in data.values())


def test_embedder_singleton_reused(client):
    """AppState.embedder 는 2번 이상 access 해도 같은 인스턴스."""
    s = serve_mod.get_state()
    e1 = s.embedder
    e2 = s.embedder
    assert e1 is e2  # 싱글톤 재사용


def test_search_hybrid_uses_state_embedder(client, monkeypatch):
    """/search/hybrid 가 매 호출마다 새 embedder 를 만들지 않고 state.embedder 를 재사용."""
    # fresh embedder call count reset
    _FakeEmbedder.calls = 0
    # compute_gravity 는 무거우므로 빈 entries 반환하도록 스텁
    monkeypatch.setattr("gstar.serve.compute_gravity", lambda *a, **kw: [])
    for _ in range(3):
        r = client.post("/search/hybrid", json={"query": "test", "top_k": 3})
        assert r.status_code == 200
    # 각 호출당 encode 1번 → 3번 누적. 단일 embedder 객체가 재사용됨.
    assert _FakeEmbedder.calls == 3
