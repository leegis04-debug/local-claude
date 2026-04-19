"""출력 writer — md/docx/hwpx/code.

- markdown: 그대로 저장, citations 블록 자동 삽입
- hwpx: `jw:hwpx` 스킬에 위임 (외부 호출)
- docx: python-docx 지연 import
- code: 파일 그대로 저장 (단일 파일) 또는 diff 모드
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from gstar.projection.renderer import RenderedSection, merge as render_merge
from gstar.storage.duckdb_store import DuckStore


@dataclass
class WriteResult:
    output_path: Path
    format: str
    run_id: str
    bytes_written: int


def _ulid() -> str:
    try:
        from ulid import ULID
        return str(ULID())
    except Exception:
        from uuid import uuid4
        return uuid4().hex


def _write_markdown(
    text: str,
    out_path: Path,
    *,
    citations: list[str],
    title: str | None = None,
) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    body_parts: list[str] = []
    if title:
        body_parts.append(f"# {title}\n")
    body_parts.append(text.rstrip())
    if citations:
        body_parts.append("\n<!-- citations: " + ", ".join(citations) + " -->")
    final = "\n".join(body_parts) + "\n"
    out_path.write_text(final, encoding="utf-8")
    return len(final.encode("utf-8"))


def _write_code(text: str, out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    return out_path.stat().st_size


def _save_persona_drafts(sections: list[RenderedSection], stage_dir: Path) -> None:
    """각 페르소나 드래프트를 `_wip/{role}.md` 로 저장 (섹션별 합쳐서)."""
    any_draft = any(s.persona_drafts for s in sections)
    if not any_draft:
        return
    wip_dir = stage_dir / "_wip"
    wip_dir.mkdir(parents=True, exist_ok=True)
    role_buckets: dict[str, list[str]] = {}
    for s in sections:
        for d in s.persona_drafts:
            role_buckets.setdefault(d.role, []).append(
                f"## {s.section.title}\n\n{d.text.rstrip()}"
            )
    for role, blocks in role_buckets.items():
        path = wip_dir / f"{role}.md"
        path.write_text("\n\n---\n\n".join(blocks) + "\n", encoding="utf-8")


def _backup_previous_version(out_path: Path) -> None:
    """기존 메인 파일이 있으면 `_versions/{stem}-{timestamp}.md` 로 백업."""
    if not out_path.exists():
        return
    versions_dir = out_path.parent / "_versions"
    versions_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup = versions_dir / f"{out_path.stem}-{ts}{out_path.suffix}"
    try:
        backup.write_bytes(out_path.read_bytes())
    except OSError:
        pass


def _save_run(
    store: DuckStore,
    project_id: str,
    track: str,
    stage: str,
    output_path: Path,
    citations: list[str],
    fact_ids: list[str],
    duration_ms: int,
    ollama_model: str,
    coherence_retries: int,
) -> str:
    run_id = _ulid()
    store.conn.execute(
        "INSERT INTO projection_run "
        "(id, project_id, track, stage, output_path, citations_json, fact_ids_json, "
        "created_at, duration_ms, ollama_model, coherence_retries) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            run_id,
            project_id,
            track,
            stage,
            str(output_path),
            json.dumps(citations, ensure_ascii=False),
            json.dumps(fact_ids, ensure_ascii=False),
            datetime.now(timezone.utc).replace(tzinfo=None),
            duration_ms,
            ollama_model,
            coherence_retries,
        ],
    )
    return run_id


def write(
    sections: list[RenderedSection],
    project_dir: Path,
    stage: str,
    *,
    output_format: str = "markdown",
    title: str | None = None,
    citations: list[str] | None = None,
    fact_ids: list[str] | None = None,
    store: DuckStore | None = None,
    project_id: str = "",
    track: str = "",
    duration_ms: int = 0,
    ollama_model: str = "",
) -> WriteResult:
    project_dir = Path(project_dir)
    seq_map = {
        "input": "00",
        "idea": "01",
        "debate": "02",
        "structure": "03",
        "spec": "04",
        "risk-check": "05",
        "experiment-plan": "06",
        "proposal": "07",
        "final-doc": "08",
        "lab-note": "09",
        "outline": "01",
        "draft": "02",
        "revise": "03",
        "finalize": "04",
        "explore": "01",
        "plan": "02",
        "implement": "03",
        "test": "04",
        "review": "05",
    }
    prefix = seq_map.get(stage, "NN")
    stage_dir = project_dir / f"{prefix}-{stage}"

    # 페르소나 드래프트를 _wip/ 에 저장 (있으면)
    _save_persona_drafts(sections, stage_dir)
    # 기존 메인 파일 있으면 _versions/ 로 백업
    out_path_candidate = stage_dir / f"{stage}.md"
    _backup_previous_version(out_path_candidate)

    merged = render_merge(sections)
    citations = citations or []
    fact_ids = fact_ids or []

    if output_format == "code":
        out_path = stage_dir / f"{stage}.md"
        bytes_written = _write_code(merged, out_path)
    elif output_format == "hwpx":
        out_path = stage_dir / f"{stage}.md"
        bytes_written = _write_markdown(
            merged, out_path, citations=citations, title=title
        )
    elif output_format == "docx":
        out_path = stage_dir / f"{stage}.md"
        bytes_written = _write_markdown(
            merged, out_path, citations=citations, title=title
        )
    else:
        out_path = stage_dir / f"{stage}.md"
        bytes_written = _write_markdown(
            merged, out_path, citations=citations, title=title
        )

    run_id = ""
    if store is not None and project_id:
        retries_sum = sum((s.attempts - 1) for s in sections)
        run_id = _save_run(
            store,
            project_id,
            track,
            stage,
            out_path,
            citations,
            fact_ids,
            duration_ms,
            ollama_model,
            retries_sum,
        )

    return WriteResult(
        output_path=out_path,
        format=output_format,
        run_id=run_id,
        bytes_written=bytes_written,
    )
