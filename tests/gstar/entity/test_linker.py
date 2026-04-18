"""Entity linker 테스트 — alias/lemma/fuzzy/created 4 경로."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("kiwipiepy")

from gstar.entity.linker import (  # noqa: E402
    link,
    link_batch,
    list_canonicals,
    stats,
)
from gstar.entity.types import EntityKind, Track
from gstar.storage.duckdb_store import DuckStore


@pytest.fixture
def store(tmp_path: Path):
    s = DuckStore(tmp_path / "t.duckdb")
    yield s
    s.close()


def test_link_creates_new_canonical(store):
    r = link("비전 AI", "proj-1", Track.PROPOSAL, store, kind_hint=EntityKind.TECHNOLOGY)
    assert r.is_new
    assert r.matched_by == "created"
    assert r.entity.kind is EntityKind.TECHNOLOGY
    assert r.entity.mentions == 1
    assert r.entity.project_id == "proj-1"


def test_link_same_surface_increments_mentions(store):
    r1 = link("데이터셋", "p", Track.RESEARCH, store, kind_hint=EntityKind.DATASET)
    r2 = link("데이터셋", "p", Track.RESEARCH, store, kind_hint=EntityKind.DATASET)
    assert r2.entity.id == r1.entity.id
    assert r2.entity.mentions == 2
    assert r2.is_new is False


def test_link_variant_by_alias_after_learning(store):
    link("다겸", "p", Track.PROPOSAL, store, kind_hint=EntityKind.PERSON)
    r = link("다겸이", "p", Track.PROPOSAL, store, kind_hint=EntityKind.PERSON)
    assert r.is_new is False
    assert r.matched_by in {"lemma", "fuzzy", "alias"}
    second = link("다겸이", "p", Track.PROPOSAL, store, kind_hint=EntityKind.PERSON)
    assert second.matched_by == "alias"


def test_link_different_project_isolated(store):
    r1 = link("프로젝트X", "pA", Track.PROPOSAL, store)
    r2 = link("프로젝트X", "pB", Track.PROPOSAL, store)
    assert r1.entity.id != r2.entity.id


def test_link_different_track_isolated(store):
    r1 = link("실험", "p", Track.PROPOSAL, store)
    r2 = link("실험", "p", Track.RESEARCH, store)
    assert r1.entity.id != r2.entity.id


def test_link_fuzzy_match(store):
    link("컨볼루션신경망", "p", Track.RESEARCH, store, kind_hint=EntityKind.TECHNOLOGY)
    r = link("컨볼루션신경쉥망", "p", Track.RESEARCH, store, kind_hint=EntityKind.TECHNOLOGY)
    assert r.matched_by in {"fuzzy", "lemma", "alias"}


def test_link_batch_processes_list(store):
    results = link_batch(
        [("수원", EntityKind.PROJECT), ("수원시", EntityKind.ORG)],
        "p",
        Track.PROPOSAL,
        store,
    )
    assert len(results) == 2


def test_link_rejects_empty_surface(store):
    with pytest.raises(ValueError):
        link("   ", "p", Track.PROPOSAL, store)


def test_list_canonicals_filters(store):
    link("비전", "p", Track.PROPOSAL, store, kind_hint=EntityKind.TECHNOLOGY)
    link("농업", "p", Track.PROPOSAL, store, kind_hint=EntityKind.TECHNOLOGY)
    link("김철수", "p", Track.PROPOSAL, store, kind_hint=EntityKind.PERSON)
    techs = list_canonicals(store, "p", Track.PROPOSAL, EntityKind.TECHNOLOGY)
    assert len(techs) == 2
    persons = list_canonicals(store, "p", Track.PROPOSAL, EntityKind.PERSON)
    assert len(persons) == 1


def test_stats_aggregates(store):
    link("A", "p", Track.PROPOSAL, store, kind_hint=EntityKind.TECHNOLOGY)
    link("B", "p", Track.PROPOSAL, store, kind_hint=EntityKind.TECHNOLOGY)
    link("C", "p", Track.PROPOSAL, store, kind_hint=EntityKind.PERSON)
    s = stats(store, "p", Track.PROPOSAL)
    assert s["total_canonical"] == 3
    assert s["by_kind"]["technology"] == 2
    assert s["by_kind"]["person"] == 1
