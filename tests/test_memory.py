from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_claude.memory import engine, policy, session
from local_claude.memory import project as project_layer
from local_claude.memory import user as user_layer


# ── project 층 ──────────────────────────────────────────────────────────────

def test_project_append(tmp_path: Path) -> None:
    assert project_layer.append("결정: X 채택", category="decision", project_dir=tmp_path)
    entries = project_layer.all(tmp_path)
    assert len(entries) == 1
    assert entries[0]["text"] == "결정: X 채택"
    assert entries[0]["category"] == "decision"
    assert entries[0]["created_at"]
    assert entries[0]["last_seen"]


def test_project_dedup_updates_last_seen(tmp_path: Path) -> None:
    assert project_layer.append("같은 내용", project_dir=tmp_path) is True
    assert project_layer.append("같은 내용", project_dir=tmp_path) is False
    entries = project_layer.all(tmp_path)
    assert len(entries) == 1
    # last_seen 갱신됨
    assert entries[0]["last_seen"] >= entries[0]["created_at"]


def test_project_file_location(tmp_path: Path) -> None:
    project_layer.append("x", project_dir=tmp_path)
    assert (tmp_path / ".memory" / "project.json").exists()


# ── session 층 ──────────────────────────────────────────────────────────────

def test_session_basic() -> None:
    session.set("k", "v")
    assert session.get("k") == "v"
    assert session.all() == {"k": "v"}
    session.clear()
    assert session.get("k") is None


# ── user 층 ─────────────────────────────────────────────────────────────────

def test_user_append(isolated_user_memory: Path) -> None:
    user_layer.append("한국어 간결한 답변 선호", category="preference")
    entries = user_layer.all()
    assert len(entries) == 1
    assert entries[0]["category"] == "preference"
    # 파일이 monkeypatch 된 경로에 생성됐는지
    assert isolated_user_memory.exists()


def test_user_isolated_across_projects(isolated_user_memory: Path, tmp_path: Path) -> None:
    user_layer.append("U1", category="preference")
    project_layer.append("P1", project_dir=tmp_path)
    # 두 층의 저장소가 실제로 분리되어야 함
    user_data = json.loads(isolated_user_memory.read_text())
    project_data = json.loads((tmp_path / ".memory" / "project.json").read_text())
    assert [e["text"] for e in user_data["entries"]] == ["U1"]
    assert [e["text"] for e in project_data["entries"]] == ["P1"]


# ── engine (통합 API) ───────────────────────────────────────────────────────

def test_engine_recall_shape(isolated_user_memory: Path, tmp_path: Path) -> None:
    session.set("s1", {"x": 1})
    user_layer.append("U1", category="preference")
    project_layer.append("P1", project_dir=tmp_path)

    bundle = engine.recall(tmp_path)
    assert set(bundle.keys()) == {"session", "project", "user"}
    assert bundle["session"] == {"s1": {"x": 1}}
    assert [e["text"] for e in bundle["project"]] == ["P1"]
    assert [e["text"] for e in bundle["user"]] == ["U1"]


def test_engine_remember_routes_to_scope(isolated_user_memory: Path, tmp_path: Path) -> None:
    engine.remember("sess", scope="session", project_dir=tmp_path)
    engine.remember("proj", scope="project", project_dir=tmp_path)
    engine.remember("usr", scope="user", project_dir=tmp_path)
    bundle = engine.recall(tmp_path)
    assert "sess" in bundle["session"]
    assert [e["text"] for e in bundle["project"]] == ["proj"]
    assert [e["text"] for e in bundle["user"]] == ["usr"]


# ── decay ───────────────────────────────────────────────────────────────────

def test_decay_archives_old_entries(tmp_path: Path, isolated_user_memory: Path) -> None:
    from datetime import datetime, timedelta

    from local_claude.memory import decay as decay_mod

    # project.json 직접 작성 — 오래된/새 항목 혼재
    mem = tmp_path / ".memory" / "project.json"
    mem.parent.mkdir(parents=True, exist_ok=True)
    old_ts = (datetime.now().astimezone() - timedelta(days=60)).isoformat()
    new_ts = datetime.now().astimezone().isoformat()
    mem.write_text(
        json.dumps(
            {
                "entries": [
                    {"text": "old", "category": "fact", "created_at": old_ts, "last_seen": old_ts},
                    {"text": "new", "category": "fact", "created_at": new_ts, "last_seen": new_ts},
                ]
            }
        )
    )

    counts = decay_mod.decay(tmp_path, days=30)
    assert counts["project"] == 1

    remaining = json.loads(mem.read_text())
    assert [e["text"] for e in remaining["entries"]] == ["new"]

    archive = json.loads((tmp_path / ".memory" / "project-archive.json").read_text())
    assert [e["text"] for e in archive["entries"]] == ["old"]


# ── policy (ask-gemma 브릿지) — subprocess mock ─────────────────────────────

def test_policy_auto_capture_parses_and_routes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    isolated_user_memory: Path,
) -> None:
    fake_llm_output = """여기 추출 결과:
    [
      {"text": "결정: RAG 사용", "category": "decision", "scope": "project"},
      {"text": "간결한 한국어 선호", "category": "preference", "scope": "user"}
    ]
    """

    def fake_run(prompt: str, timeout: int) -> str:
        assert "스니펫" in prompt
        return fake_llm_output

    monkeypatch.setattr("local_claude.memory.policy._run_ask_gemma", fake_run)

    counts = policy.auto_capture("sample snippet", project_dir=tmp_path)
    assert counts["project"] == 1
    assert counts["user"] == 1

    assert [e["text"] for e in project_layer.all(tmp_path)] == ["결정: RAG 사용"]
    assert [e["text"] for e in user_layer.all()] == ["간결한 한국어 선호"]


def test_policy_handles_empty_llm_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_user_memory: Path
) -> None:
    monkeypatch.setattr("local_claude.memory.policy._run_ask_gemma", lambda p, timeout: "")
    counts = policy.auto_capture("snippet", project_dir=tmp_path)
    assert counts == {"project": 0, "user": 0, "skipped": 0}


def test_policy_invalid_categories_defaulted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, isolated_user_memory: Path
) -> None:
    monkeypatch.setattr(
        "local_claude.memory.policy._run_ask_gemma",
        lambda p, timeout: '[{"text":"이상한 항목","category":"garbage","scope":"??"}]',
    )
    counts = policy.auto_capture("s", project_dir=tmp_path)
    assert counts["project"] == 1
    entries = project_layer.all(tmp_path)
    assert entries[0]["category"] == "fact"  # 유효하지 않은 값은 fact 로 강등
