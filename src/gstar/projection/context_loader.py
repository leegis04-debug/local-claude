"""프로젝트 디렉터리 → StageArtifact 목록 로더.

jw/re 디렉터리 규약 호환:
- `00-input/` — 원본 입력 (BASE 파일 등)
- `00-form/` — 선택적 양식 메타
- `NN-<stage>/` — 단계별 산출물 (01-idea, 02-debate, ...)

산출물은 단계 디렉터리 안의 `.md` 파일. 여러 개면 모두 읽어 이어붙인다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


_STAGE_DIR_RE = re.compile(r"^(\d{2})-([a-z][a-z0-9-]*)$")


@dataclass
class StageArtifact:
    stage: str
    seq: int
    path: Path
    text: str
    words: int
    created_at: datetime

    @property
    def body(self) -> str:
        return self.text


@dataclass
class FormContext:
    form_id: str
    path: Path
    raw: str
    meta: dict = field(default_factory=dict)


def _scan_stage_dir(d: Path) -> tuple[int, str] | None:
    m = _STAGE_DIR_RE.match(d.name)
    if not m:
        return None
    return int(m.group(1)), m.group(2)


def _read_dir_markdown(d: Path) -> tuple[str, Path | None, datetime | None]:
    md_files = sorted(d.glob("*.md"))
    if not md_files:
        return "", None, None
    main = next((f for f in md_files if f.stem != "prompt" and not f.stem.startswith(".")), md_files[0])
    text_parts: list[str] = []
    for f in md_files:
        try:
            text_parts.append(f.read_text(encoding="utf-8"))
        except OSError:
            continue
    combined = "\n\n---\n\n".join(text_parts)
    try:
        mtime = datetime.fromtimestamp(main.stat().st_mtime)
    except OSError:
        mtime = datetime.now()
    return combined, main, mtime


def load_project(project_dir: Path | str) -> list[StageArtifact]:
    """프로젝트 루트 → 정렬된 StageArtifact 리스트.

    00-input/ 이 있으면 seq=0, stage='input' 으로 포함.
    """
    root = Path(project_dir).resolve()
    if not root.exists():
        raise FileNotFoundError(f"project_dir not found: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"project_dir must be directory: {root}")

    artifacts: list[StageArtifact] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        parsed = _scan_stage_dir(d)
        if parsed is None:
            continue
        seq, stage = parsed
        text, main_path, mtime = _read_dir_markdown(d)
        if not text:
            continue
        artifacts.append(
            StageArtifact(
                stage=stage,
                seq=seq,
                path=main_path or d,
                text=text,
                words=len(text),
                created_at=mtime or datetime.now(),
            )
        )
    artifacts.sort(key=lambda a: (a.seq, a.stage))
    return artifacts


def load_form(project_dir: Path | str) -> FormContext | None:
    """00-form/ 에서 form context 로드."""
    root = Path(project_dir).resolve()
    form_dir = root / "00-form"
    if not form_dir.exists():
        return None
    ref_files = sorted(form_dir.glob("*.json"))
    raw = ""
    meta: dict = {}
    form_id = ""
    for f in ref_files:
        try:
            import json

            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                form_id = data.get("form_id") or form_id or f.stem
                meta = data
                raw = f.read_text(encoding="utf-8")
                break
        except (OSError, json.JSONDecodeError):
            continue
    if not form_id:
        md_files = sorted(form_dir.glob("*.md"))
        if md_files:
            raw = md_files[0].read_text(encoding="utf-8")
            form_id = md_files[0].stem
    if not form_id:
        return None
    return FormContext(form_id=form_id, path=form_dir, raw=raw, meta=meta)


def stage_by_name(artifacts: list[StageArtifact], stage: str) -> StageArtifact | None:
    for a in artifacts:
        if a.stage == stage:
            return a
    return None


def prev_stages(
    artifacts: list[StageArtifact], current_stage: str
) -> list[StageArtifact]:
    """현재 단계 이전 모든 단계 (seq 오름차순)."""
    current_seq = None
    for a in artifacts:
        if a.stage == current_stage:
            current_seq = a.seq
            break
    if current_seq is None:
        return list(artifacts)
    return [a for a in artifacts if a.seq < current_seq]
