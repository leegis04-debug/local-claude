"""프로젝트 디렉터리 → StageArtifact 목록 로더.

asst/project.sh 가 만드는 jw/re 디렉터리 규약 호환:
- `00-input/BASE/` — 공고문(PDF), 양식(HWPX), 킥오프 DOCX, 기업 자료
- `00-input/REF/`  — 참고 PDF·논문
- `00-input/ING/`  — 진행 중 MD
- `00-input/form-ref.json` — HWPX 양식 매칭 결과 (자동 생성)
- `00-form/` (선택적 구버전 경로)
- `NN-<stage>/` — 단계별 산출물. 메인 파일명은 `*-canvas.md`, `*-report.md`, `*-frame.md`, `tech-spec.md`, `proposal.md`, `final-document.md` 등 규약 다양.

추출기(`extractors.py`) 로 PDF·HWPX·DOCX·XLSX 도 흡수.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from gstar.projection.extractors import (
    ExtractResult,
    combined_text,
    extract_directory,
    extract_file,
    supported_exts,
)


_STAGE_DIR_RE = re.compile(r"^(\d{2})-([a-z][a-z0-9-]*)$")

_INPUT_SUBDIRS = ("BASE", "REF", "ING")

_STAGE_MAIN_HINTS: dict[str, list[str]] = {
    "input": ["input-summary.md", "BASE.md", "research-context-map.md"],
    "idea": ["idea-canvas.md", "research-canvas.md"],
    "debate": ["debate-report.md", "debate-result.md", "research-debate.md"],
    "structure": ["structure-frame.md", "research-frame.md"],
    "spec": ["tech-spec.md", "research-spec.md"],
    "risk-check": ["risk-report.md", "risk-check-v1.md", "research-risk.md"],
    "experiment-plan": ["experiment-protocol.md", "experiment-plan.md"],
    "proposal": ["proposal.md", "research-proposal.md"],
    "final-doc": ["final-document.md", "final.md"],
    "bridge": ["dev-tasks.md", "task-breakdown.md", "award-to-dev.md"],
    "award-to-dev": ["dev-tasks.md", "task-breakdown.md"],
    "lab-note": ["lab-note.md", "research-lab-note.md"],
    "outline": ["outline.md"],
    "draft": ["draft.md"],
    "revise": ["revise.md"],
    "finalize": ["finalize.md", "final.md"],
    "explore": ["explore.md"],
    "plan": ["plan.md"],
    "implement": ["implement.md"],
    "test": ["test.md"],
    "review": ["review.md"],
}


@dataclass
class StageArtifact:
    stage: str
    seq: int
    path: Path
    text: str
    words: int
    created_at: datetime
    sources: list[Path] = field(default_factory=list)

    @property
    def body(self) -> str:
        return self.text


@dataclass
class FormContext:
    form_id: str
    path: Path
    raw: str
    meta: dict = field(default_factory=dict)


@dataclass
class InputBundle:
    """00-input/ 전체 흡수 결과 (BASE/REF/ING 통합 + 최상위 파일)."""

    root: Path
    base: list[ExtractResult] = field(default_factory=list)
    ref: list[ExtractResult] = field(default_factory=list)
    ing: list[ExtractResult] = field(default_factory=list)
    top_level: list[ExtractResult] = field(default_factory=list)

    @property
    def all_results(self) -> list[ExtractResult]:
        return self.base + self.ref + self.ing + self.top_level

    def as_artifact(
        self,
        *,
        max_chars: int = 20000,
    ) -> StageArtifact | None:
        results = self.all_results
        if not results:
            return None
        text = combined_text(results, max_total_chars=max_chars)
        if not text.strip():
            return None
        return StageArtifact(
            stage="input",
            seq=0,
            path=self.root,
            text=text,
            words=len(text),
            created_at=_dir_mtime(self.root),
            sources=[r.path for r in results if r.ok],
        )

    def meta_summary(self) -> dict:
        all_results = self.all_results
        return {
            "base": [str(r.path.name) for r in self.base],
            "ref": [str(r.path.name) for r in self.ref],
            "ing": [str(r.path.name) for r in self.ing],
            "top_level": [str(r.path.name) for r in self.top_level],
            "errors": [
                {"path": str(r.path.name), "err": r.meta.get("error")}
                for r in results_with_errors(all_results)
            ],
        }


def results_with_errors(results: list[ExtractResult]) -> list[ExtractResult]:
    return [r for r in results if "error" in r.meta]


def _dir_mtime(d: Path) -> datetime:
    try:
        return datetime.fromtimestamp(d.stat().st_mtime)
    except OSError:
        return datetime.now()


def _scan_stage_dir(d: Path) -> tuple[int, str] | None:
    m = _STAGE_DIR_RE.match(d.name)
    if not m:
        return None
    return int(m.group(1)), m.group(2)


def _pick_main_file(md_files: list[Path], stage: str) -> Path:
    """단계별 메인 파일명 힌트 우선 선택. 없으면 첫 파일."""
    hints = _STAGE_MAIN_HINTS.get(stage, [])
    by_name = {f.name: f for f in md_files}
    for h in hints:
        if h in by_name:
            return by_name[h]
    for h in hints:
        matches = [f for f in md_files if h.lower() in f.name.lower()]
        if matches:
            return matches[0]
    non_prefixed = [f for f in md_files if not f.name.startswith("_") and f.stem != "prompt"]
    return non_prefixed[0] if non_prefixed else md_files[0]


def _read_stage_dir(
    d: Path,
    stage: str,
    *,
    max_chars: int = 40000,
    include_wip: bool = False,
) -> tuple[str, Path | None, datetime | None, list[Path]]:
    md_files = sorted([f for f in d.glob("*.md") if f.is_file()])
    if not include_wip:
        md_files = [f for f in md_files if not f.parent.name.startswith("_")]

    if not md_files:
        sub_results = extract_directory(d, max_chars_per_file=max_chars // 2, recurse=False)
        text = combined_text(sub_results, max_total_chars=max_chars)
        if not text:
            return "", None, None, []
        return text, d, _dir_mtime(d), [r.path for r in sub_results if r.ok]

    main = _pick_main_file(md_files, stage)
    text_parts: list[str] = []
    for f in md_files:
        try:
            text_parts.append(f.read_text(encoding="utf-8"))
        except OSError:
            continue

    combined = "\n\n---\n\n".join(text_parts)
    if len(combined) > max_chars:
        combined = combined[:max_chars] + f"\n...[truncated at {max_chars} chars]"
    try:
        mtime = datetime.fromtimestamp(main.stat().st_mtime)
    except OSError:
        mtime = datetime.now()
    return combined, main, mtime, list(md_files)


def load_input_bundle(
    project_dir: Path | str,
    *,
    max_chars_per_file: int = 40_000,
) -> InputBundle:
    """`00-input/{BASE,REF,ING}` + 최상위 파일 흡수. 확장자 지원: extractors.supported_exts()."""
    root = Path(project_dir).resolve()
    input_dir = root / "00-input"
    bundle = InputBundle(root=input_dir)
    if not input_dir.exists() or not input_dir.is_dir():
        return bundle

    for name in _INPUT_SUBDIRS:
        sub = input_dir / name
        if sub.exists() and sub.is_dir():
            res = extract_directory(sub, max_chars_per_file=max_chars_per_file, recurse=True)
            if name == "BASE":
                bundle.base = res
            elif name == "REF":
                bundle.ref = res
            elif name == "ING":
                bundle.ing = res

    top_level: list[ExtractResult] = []
    for p in sorted(input_dir.iterdir()):
        if not p.is_file():
            continue
        if p.name.startswith("."):
            continue
        if p.suffix.lower() not in supported_exts():
            continue
        top_level.append(extract_file(p, max_chars=max_chars_per_file))
    bundle.top_level = top_level

    return bundle


def load_project(
    project_dir: Path | str,
    *,
    include_input: bool = True,
    input_max_chars: int = 20_000,
    stage_max_chars: int = 40_000,
) -> list[StageArtifact]:
    """프로젝트 루트 → 정렬된 StageArtifact 리스트.

    include_input=True 이면 00-input/ 을 seq=0, stage='input' 으로 합쳐 포함.
    """
    root = Path(project_dir).resolve()
    if not root.exists():
        raise FileNotFoundError(f"project_dir not found: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"project_dir must be directory: {root}")

    artifacts: list[StageArtifact] = []

    if include_input:
        bundle = load_input_bundle(root, max_chars_per_file=stage_max_chars)
        art = bundle.as_artifact(max_chars=input_max_chars)
        if art is not None:
            artifacts.append(art)

    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        parsed = _scan_stage_dir(d)
        if parsed is None:
            continue
        seq, stage = parsed
        if stage == "input":
            continue
        text, main_path, mtime, sources = _read_stage_dir(d, stage, max_chars=stage_max_chars)
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
                sources=sources,
            )
        )
    artifacts.sort(key=lambda a: (a.seq, a.stage))
    return artifacts


def _merge_template_map(root: Path, fc: FormContext) -> FormContext:
    """00-input/template-map.md 가 있으면 FormContext 에 합쳐 sections 주입."""
    tm = root / "00-input" / "template-map.md"
    if not tm.exists():
        return fc
    try:
        raw = tm.read_text(encoding="utf-8")
    except OSError:
        return fc
    sections = _parse_template_map_md(raw)
    if sections:
        fc.meta = dict(fc.meta)
        fc.meta.setdefault("sections", sections)
        # raw 에도 병합 (텍스트 fallback 용)
        fc.raw = (fc.raw + "\n\n---\n\n" + raw) if fc.raw else raw
    return fc


def _parse_template_map_md(raw: str) -> list[dict]:
    """jw wrapper 가 만든 `template-map.md` 포맷 → sections 리스트.

    포맷 (예):
    | # | 섹션명 | 유형 | 양식 위치 | 주요 항목 |
    |---|--------|------|-----------|----------|
    | 8 | 1. 상용화 대상 개요 | 서술 | 표21-24 | 1-1. 상용화대상 소개...|

    + `### 섹션 N: 제목` 아래의 양식 지시사항·예상 분량·필요 내용 등.
    """
    import re as _re

    sections: list[dict] = []
    # 1) 표 파싱
    table_re = _re.compile(
        r"^\|\s*(\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|",
        _re.MULTILINE,
    )
    for m in table_re.finditer(raw):
        num = m.group(1).strip()
        title = m.group(2).strip()
        kind_label = m.group(3).strip()
        items_text = m.group(5).strip()
        if title in ("섹션명", "-") or not title:
            continue
        sections.append({
            "number": num,
            "title": title,
            "kind_label": kind_label,
            "required_fields": _split_items(items_text),
            "guide": items_text,
        })

    # 2) `### 섹션 N: 제목` 상세 병합 — guide 에 추가 정보
    detail_re = _re.compile(
        r"###\s*섹션\s*(\d+)\s*[:：]\s*([^\n]+)\n(.+?)(?=\n###\s*섹션|\Z)",
        _re.DOTALL,
    )
    details: dict[str, str] = {}
    for m in detail_re.finditer(raw):
        num = m.group(1).strip()
        body = m.group(3).strip()
        details[num] = body[:800]

    for s in sections:
        if s["number"] in details:
            s["author_notes"] = details[s["number"]]
            s["guide"] = (s.get("guide", "") + " / " + details[s["number"]])[:600]

    # kind_label 로 서술 섹션만 필터링 (체크리스트·정보입력 제외)
    narrative = [s for s in sections if s.get("kind_label", "").startswith(("서술", "서술+표"))]
    return narrative or sections


def _split_items(text: str) -> list[str]:
    import re as _re

    parts = _re.split(r"[,·]|\s{2,}", text)
    return [p.strip() for p in parts if p.strip()]


def load_form(project_dir: Path | str) -> FormContext | None:
    """양식 로드. 탐색 순서:
    1. `00-input/form-ref.json` (asst/jw 신규 규약) — 있으면 template-map.md 도 병합
    2. `00-form/*.json` (구버전)
    3. `00-form/*.md` (메타 부재 시 최초 md)
    """
    root = Path(project_dir).resolve()

    ref_json = root / "00-input" / "form-ref.json"
    if ref_json.exists():
        try:
            raw = ref_json.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                form_id = data.get("form_id") or ref_json.stem
                fc = FormContext(form_id=form_id, path=ref_json, raw=raw, meta=data)
                return _merge_template_map(root, fc)
        except (OSError, json.JSONDecodeError):
            pass

    form_dir = root / "00-form"
    if form_dir.exists():
        json_files = sorted(form_dir.glob("*.json"))
        for f in json_files:
            try:
                raw = f.read_text(encoding="utf-8")
                data = json.loads(raw)
                if isinstance(data, dict):
                    form_id = data.get("form_id") or f.stem
                    return FormContext(form_id=form_id, path=f, raw=raw, meta=data)
            except (OSError, json.JSONDecodeError):
                continue
        md_files = sorted(form_dir.glob("*.md"))
        if md_files:
            raw = md_files[0].read_text(encoding="utf-8")
            return FormContext(form_id=md_files[0].stem, path=md_files[0], raw=raw)

    base_dir = root / "00-input" / "BASE"
    if base_dir.exists():
        hwpx_files = sorted(base_dir.glob("*.hwpx"))
        if hwpx_files:
            r = extract_file(hwpx_files[0])
            return FormContext(
                form_id=hwpx_files[0].stem,
                path=hwpx_files[0],
                raw=r.text,
                meta={"source": "hwpx_fallback"},
            )

    return None


def stage_by_name(artifacts: list[StageArtifact], stage: str) -> StageArtifact | None:
    for a in artifacts:
        if a.stage == stage:
            return a
    return None


def prev_stages(
    artifacts: list[StageArtifact], current_stage: str
) -> list[StageArtifact]:
    """현재 단계 이전 모든 단계 (seq 오름차순). include_input=True 로 로드된
    'input' 아티팩트(seq=0)도 자연스레 포함됨."""
    current_seq = None
    for a in artifacts:
        if a.stage == current_stage:
            current_seq = a.seq
            break
    if current_seq is None:
        return list(artifacts)
    return [a for a in artifacts if a.seq < current_seq]
