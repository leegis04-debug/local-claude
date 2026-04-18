from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from local_claude.memory import _store, decay, dedup, restore
from local_claude.memory import project as project_layer
from local_claude.memory import user as user_layer


def _write_memory(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"entries": entries}, ensure_ascii=False))


def _iso_days_ago(days: int) -> str:
    return (datetime.now().astimezone() - timedelta(days=days)).isoformat()


# ── 카테고리별 TTL ──────────────────────────────────────────────────────────

def test_category_specific_ttl_decision_preserved(tmp_path: Path) -> None:
    """decision(90d) — 60일 지난 것은 아직 유지, 100일은 archive."""
    mem = tmp_path / ".memory" / "project.json"
    _write_memory(
        mem,
        [
            {"text": "결정 60d", "category": "decision", "created_at": _iso_days_ago(60), "last_seen": _iso_days_ago(60)},
            {"text": "결정 100d", "category": "decision", "created_at": _iso_days_ago(100), "last_seen": _iso_days_ago(100)},
            {"text": "fact 40d", "category": "fact", "created_at": _iso_days_ago(40), "last_seen": _iso_days_ago(40)},
        ],
    )
    counts = decay.decay(tmp_path)  # days=None → 카테고리별 TTL
    assert counts["project"] == 2  # decision 100d + fact 40d

    remaining = [e["text"] for e in json.loads(mem.read_text())["entries"]]
    assert remaining == ["결정 60d"]


def test_preference_never_decays(tmp_path: Path, isolated_user_memory: Path) -> None:
    _write_memory(
        isolated_user_memory,
        [
            {"text": "preference old", "category": "preference",
             "created_at": _iso_days_ago(365), "last_seen": _iso_days_ago(365)},
            {"text": "fact old", "category": "fact",
             "created_at": _iso_days_ago(365), "last_seen": _iso_days_ago(365)},
        ],
    )
    counts = decay.decay(tmp_path)
    assert counts["user"] == 1  # fact 만 archive
    remaining = [e["text"] for e in json.loads(isolated_user_memory.read_text())["entries"]]
    assert remaining == ["preference old"]


def test_touch_count_protects_from_decay(tmp_path: Path) -> None:
    mem = tmp_path / ".memory" / "project.json"
    _write_memory(
        mem,
        [
            {"text": "touched", "category": "fact",
             "created_at": _iso_days_ago(100), "last_seen": _iso_days_ago(100),
             "touch_count": 5},
            {"text": "cold", "category": "fact",
             "created_at": _iso_days_ago(100), "last_seen": _iso_days_ago(100),
             "touch_count": 0},
        ],
    )
    counts = decay.decay(tmp_path)
    assert counts["project"] == 1  # cold 만 archive
    remaining = [e["text"] for e in json.loads(mem.read_text())["entries"]]
    assert "touched" in remaining


def test_legacy_days_mode_applies_uniformly(tmp_path: Path) -> None:
    """기존 시그니처 호환 — days=30 주면 카테고리 무시."""
    mem = tmp_path / ".memory" / "project.json"
    _write_memory(
        mem,
        [
            {"text": "decision 60d", "category": "decision",
             "created_at": _iso_days_ago(60), "last_seen": _iso_days_ago(60)},
        ],
    )
    counts = decay.decay(tmp_path, days=30)
    # days=30 legacy 모드면 decision 도 30일 기준 적용 → archive 됨
    assert counts["project"] == 1


# ── touch_count 동작 ────────────────────────────────────────────────────────

def test_append_duplicate_increments_touch_count(tmp_path: Path) -> None:
    project_layer.append("같은 것", category="fact", project_dir=tmp_path)
    project_layer.append("같은 것", category="fact", project_dir=tmp_path)
    project_layer.append("같은 것", category="fact", project_dir=tmp_path)
    entries = project_layer.all(tmp_path)
    assert len(entries) == 1
    assert entries[0]["touch_count"] == 2  # 첫 생성은 0, 중복 2번이면 2


# ── dedup ──────────────────────────────────────────────────────────────────

def test_dedup_finds_similar_within_same_category(tmp_path: Path) -> None:
    mem = tmp_path / ".memory" / "project.json"
    _write_memory(
        mem,
        [
            {"text": "Gateway 는 X-Auth-Token 을 헤더로 사용한다",
             "category": "fact", "created_at": _iso_days_ago(10), "last_seen": _iso_days_ago(10)},
            {"text": "Gateway 는 X-Auth-Token을 헤더로 사용함",
             "category": "fact", "created_at": _iso_days_ago(5), "last_seen": _iso_days_ago(5)},
            {"text": "완전 다른 항목",
             "category": "fact", "created_at": _iso_days_ago(5), "last_seen": _iso_days_ago(5)},
        ],
    )
    result = dedup.dedup(tmp_path, threshold=0.8, apply=False)
    cands = result["project"]["candidates"]
    assert len(cands) == 1
    assert cands[0]["ratio"] >= 0.8
    # dry-run 이므로 병합 안 됨
    assert result["project"]["merged"] == 0
    assert len(json.loads(mem.read_text())["entries"]) == 3


def test_dedup_apply_merges_entries(tmp_path: Path) -> None:
    mem = tmp_path / ".memory" / "project.json"
    _write_memory(
        mem,
        [
            {"text": "API 는 토큰 인증을 쓴다",
             "category": "fact", "created_at": _iso_days_ago(10), "last_seen": _iso_days_ago(10),
             "touch_count": 2},
            {"text": "API는 토큰 인증을 씁니다",
             "category": "fact", "created_at": _iso_days_ago(5), "last_seen": _iso_days_ago(5),
             "touch_count": 1},
        ],
    )
    result = dedup.dedup(tmp_path, threshold=0.7, apply=True)
    assert result["project"]["merged"] == 1
    entries = json.loads(mem.read_text())["entries"]
    assert len(entries) == 1
    # touch_count 합산 + 1 (merge 자체)
    assert entries[0]["touch_count"] == 2 + 1 + 1


def test_dedup_respects_category_boundary(tmp_path: Path) -> None:
    mem = tmp_path / ".memory" / "project.json"
    _write_memory(
        mem,
        [
            {"text": "같은 내용",
             "category": "decision", "created_at": _iso_days_ago(10), "last_seen": _iso_days_ago(10)},
            {"text": "같은 내용",
             "category": "fact", "created_at": _iso_days_ago(5), "last_seen": _iso_days_ago(5)},
        ],
    )
    result = dedup.dedup(tmp_path, threshold=0.5)
    # 카테고리 다르면 병합 후보 아님
    assert result["project"]["candidates"] == []


# ── restore ─────────────────────────────────────────────────────────────────

def test_restore_by_text_partial_match(tmp_path: Path) -> None:
    mem = tmp_path / ".memory" / "project.json"
    archive = tmp_path / ".memory" / "project-archive.json"
    _write_memory(mem, [])
    _write_memory(
        archive,
        [
            {"text": "보존할 결정 X", "category": "decision",
             "created_at": _iso_days_ago(200), "last_seen": _iso_days_ago(200)},
            {"text": "다른 결정 Y", "category": "decision",
             "created_at": _iso_days_ago(200), "last_seen": _iso_days_ago(200)},
        ],
    )
    result = restore.restore(tmp_path, text="결정 X")
    assert result.restored_project == 1
    assert [e["text"] for e in json.loads(mem.read_text())["entries"]] == ["보존할 결정 X"]
    # archive 에는 Y 만 남음
    assert [e["text"] for e in json.loads(archive.read_text())["entries"]] == ["다른 결정 Y"]


def test_restore_skips_duplicates_already_active(tmp_path: Path) -> None:
    mem = tmp_path / ".memory" / "project.json"
    archive = tmp_path / ".memory" / "project-archive.json"
    _write_memory(
        mem,
        [{"text": "이미 있음", "category": "fact", "created_at": _iso_days_ago(1), "last_seen": _iso_days_ago(1)}],
    )
    _write_memory(
        archive,
        [{"text": "이미 있음", "category": "fact", "created_at": _iso_days_ago(100), "last_seen": _iso_days_ago(100)}],
    )
    result = restore.restore(tmp_path, text="이미 있음")
    assert result.restored_project == 0
    assert result.skipped == 1


def test_restore_requires_filter(tmp_path: Path) -> None:
    # text/category 둘 다 없으면 전체 복원은 거부 (안전장치)
    result = restore.restore(tmp_path)
    assert result.restored_project == 0
    assert result.restored_user == 0
