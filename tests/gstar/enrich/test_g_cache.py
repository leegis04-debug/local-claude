"""Web-to-G 자동 캐시 테스트 (네트워크 없음).

SBERT/FAISS 로딩을 피하기 위해 `cache_first_web_search` 의 precheck·ingest 경로를
monkeypatch 로 스텁 처리한 단위·흐름 테스트 중심.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from gstar.enrich import g_cache


@pytest.fixture(autouse=True)
def _isolated_web_cache(monkeypatch, tmp_path: Path):
    """모든 테스트가 임시 web_cache 디렉터리를 사용."""
    root = tmp_path / "web_cache"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(g_cache, "_WEB_CACHE_ROOT", root, raising=True)
    monkeypatch.setattr(g_cache, "_URL_INDEX_PATH", root / "_url_index.json", raising=True)
    yield root


def test_url_index_roundtrip():
    idx = {"abc": {"url": "https://example.com", "fetched_at": 100, "ingested": True}}
    g_cache._save_url_index(idx)
    assert g_cache._load_url_index() == idx


def test_url_index_handles_missing_file():
    assert g_cache._load_url_index() == {}


def test_url_index_handles_corrupt_json(_isolated_web_cache: Path):
    (_isolated_web_cache / "_url_index.json").write_text("not{json", encoding="utf-8")
    assert g_cache._load_url_index() == {}


class _FakeReport:
    def __init__(self, facts: int) -> None:
        self.facts = facts
        self.entities = 0
        self.edges = 0
        self.files_scanned = 0


def _patch_ingest_stubs(monkeypatch, facts_per_call: int = 3):
    """_ingest_web_results 내부의 G 저장 경로를 전부 스텁으로 대체."""
    from gstar.enrich import g_cache as gc

    class _FakePaths:
        def __init__(self, tmp: Path) -> None:
            self.db = tmp / "g.duckdb"
            self.faiss = tmp / "e.faiss"
        @classmethod
        def load(cls): return cls(_tmp)  # noqa

    class _FakeStore:
        def __init__(self, *a, **kw): pass
        def close(self): pass

    class _FakeFaiss:
        def __init__(self, *a, **kw): pass
        def save(self): pass

    class _FakeEmbedder:
        dim = 384

    import gstar.config
    import gstar.embedding.sbert
    import gstar.ingest.pipeline
    import gstar.storage.duckdb_store
    import gstar.storage.faiss_index

    # paths.db 가 exists() True 리턴하도록 실제 임시 파일 생성이 필요.
    # 간단하게 Paths.load 를 replace 해서 db_exists=True 를 흉내낸다.
    def _fake_ingest_path(cache_dir, store, faiss, embedder, *, namespace, track, **kw):
        return _FakeReport(facts=facts_per_call)

    monkeypatch.setattr(gstar.ingest.pipeline, "ingest_path", _fake_ingest_path)
    monkeypatch.setattr(gstar.storage.duckdb_store, "DuckStore", _FakeStore)
    monkeypatch.setattr(gstar.storage.faiss_index, "FaissStore", _FakeFaiss)
    monkeypatch.setattr(gstar.embedding.sbert, "SBertEmbedder", _FakeEmbedder)

    class _DummyPaths:
        def __init__(self):
            p = Path(_tmp)
            self.db = p / "g.duckdb"
            self.faiss = p / "e.faiss"
            self.db.touch()
        @classmethod
        def load(cls): return cls()
    monkeypatch.setattr(gstar.config, "Paths", _DummyPaths)


_tmp = None  # set by fixture


@pytest.fixture
def _patched(tmp_path, monkeypatch):
    global _tmp
    _tmp = str(tmp_path)
    _patch_ingest_stubs(monkeypatch, facts_per_call=3)

    async def _fake_fetch(url: str, *, max_chars: int = 20000) -> str:
        return f"BODY of {url}"

    monkeypatch.setattr(g_cache, "web_fetch", _fake_fetch)
    yield


def test_ingest_dedupes_on_second_call(_patched):
    results = [
        {"url": "https://a.com", "title": "A", "snippet": "aaa", "content": "x" * 2000},
        {"url": "https://b.com", "title": "B", "snippet": "bbb", "content": "y" * 2000},
    ]
    ingested1, skipped1 = asyncio.run(
        g_cache._ingest_web_results("q", results, "ns", fetch_full=False, ttl_days=30)
    )
    assert ingested1 > 0
    assert skipped1 == 0

    ingested2, skipped2 = asyncio.run(
        g_cache._ingest_web_results("q", results, "ns", fetch_full=False, ttl_days=30)
    )
    # 두 번째 호출은 모두 TTL 내 → skip, 신규 ingest 없음
    assert ingested2 == 0
    assert skipped2 == 2


def test_ingest_respects_ttl_zero_means_always_reingest(_patched):
    results = [{"url": "https://a.com", "title": "A", "snippet": "aaa", "content": "x" * 2000}]
    asyncio.run(g_cache._ingest_web_results("q", results, "ns", fetch_full=False, ttl_days=30))
    # ttl_days=0 이면 dedupe 무시 → 재ingest
    ingested2, skipped2 = asyncio.run(
        g_cache._ingest_web_results("q", results, "ns", fetch_full=False, ttl_days=0)
    )
    assert ingested2 > 0
    assert skipped2 == 0


def test_force_web_bypasses_cache(monkeypatch, _patched):
    """force_web=True 시 g_hits 무시하고 web_search 호출."""
    calls = {"g_precheck": 0, "web_search": 0}

    async def _fake_g_precheck(*a, **kw):
        calls["g_precheck"] += 1
        return [{"title": f"G-{i}", "url": f"gstar://n/{i}", "score": 0.9, "snippet": "s", "content": "c"} for i in range(10)]

    async def _fake_web_search(*a, **kw):
        calls["web_search"] += 1
        return [{"url": "https://x.com", "title": "X", "snippet": "sx", "content": "xx" * 1000}]

    monkeypatch.setattr(g_cache, "_g_precheck", _fake_g_precheck)
    monkeypatch.setattr(g_cache, "web_search", _fake_web_search)

    r = asyncio.run(g_cache.cache_first_web_search("q", backend="brave", top_k=5, force_web=True))
    assert r.from_cache is False
    assert r.forced_fresh is True
    assert calls["web_search"] == 1
    assert calls["g_precheck"] == 0


def test_cache_hit_when_g_sufficient(monkeypatch, _patched):
    async def _fake_g_precheck(*a, **kw):
        return [{"title": f"G-{i}", "url": f"gstar://n/{i}", "score": 0.9, "snippet": "s", "content": "c"} for i in range(10)]

    async def _fake_web_search(*a, **kw):  # 호출되면 실패
        raise AssertionError("web_search should not be called on cache hit")

    monkeypatch.setattr(g_cache, "_g_precheck", _fake_g_precheck)
    monkeypatch.setattr(g_cache, "web_search", _fake_web_search)

    r = asyncio.run(g_cache.cache_first_web_search("q", backend="brave", top_k=5, min_hits=3))
    assert r.from_cache is True
    assert r.g_hits >= 3
    assert r.web_hits == 0


def test_cache_miss_triggers_web_and_ingest(monkeypatch, _patched):
    async def _fake_g_precheck(*a, **kw):
        return []  # G 비어있음

    async def _fake_web_search(*a, **kw):
        return [{"url": "https://new.com", "title": "N", "snippet": "n", "content": "c" * 2000}]

    monkeypatch.setattr(g_cache, "_g_precheck", _fake_g_precheck)
    monkeypatch.setattr(g_cache, "web_search", _fake_web_search)

    r = asyncio.run(g_cache.cache_first_web_search("brand new query", backend="brave", top_k=5))
    assert r.from_cache is False
    assert r.web_hits == 1
    assert r.ingested > 0

    # 인덱스에 등록됐는지 확인
    idx = g_cache._load_url_index()
    assert any(v.get("url") == "https://new.com" for v in idx.values())
