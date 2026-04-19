"""code_repos 점진 이관 — worker tick 당 N files.

동기 (2026-04-19): code_repos (NAS) 에는 약 13k files. 한 번에 이관 시 4h+
필요하고 중간 crash 시 복구 복잡. 대신 worker tick 마다 N files 씩 점진 이관.

state:
- 파일 목록을 NAS 에서 생성 (tick 당) — sorted order 로 deterministic
- cursor = `${GSTAR_HOME}/code_repos.cursor`: 마지막 처리한 파일 path
- 다음 tick 은 cursor 이후부터 N 파일 처리
- dedupe (text+ns) 가 이미 있으므로 재시도해도 안전. cursor 는 속도 최적화용.

env:
    CODE_REPOS_ENABLED     (기본 off — 명시적으로 on)
    CODE_REPOS_ROOT        (컨테이너 내 경로, 기본 /nas/workspace/code_repos)
    CODE_REPOS_NAMESPACE   (기본 code_repos)
    CODE_REPOS_PER_TICK    (기본 50)
    CODE_REPOS_GLOBS       (기본 '*.md,*.py,*.ts,*.tsx,*.js,*.jsx,*.go,*.rs,*.java,*.yaml,*.toml,*.json')
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


_DEFAULT_GLOBS = "*.md,*.py,*.ts,*.tsx,*.js,*.jsx,*.go,*.rs,*.java,*.yaml,*.toml,*.json"


@dataclass
class CodeReposResult:
    scanned: int = 0
    ingested: int = 0
    skipped: int = 0
    errors: int = 0
    cursor_before: str = ""
    cursor_after: str = ""


def _cursor_path() -> Path:
    home = Path(os.environ.get("GSTAR_HOME", str(Path.home() / ".gstar")))
    return home / "code_repos.cursor"


def _read_cursor() -> str:
    p = _cursor_path()
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _write_cursor(val: str) -> None:
    p = _cursor_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(val, encoding="utf-8")


def _collect_files(root: Path, globs: list[str]) -> list[Path]:
    """root 아래 globs 확장자 파일을 sorted 순서로 수집."""
    if not root.exists() or not root.is_dir():
        return []
    out: list[Path] = []
    for g in globs:
        g = g.strip().lstrip("*")
        if not g:
            continue
        # rglob('*.md') 패턴으로
        out.extend(root.rglob(f"*{g}"))
    return sorted({p for p in out if p.is_file()})


def ingest_code_repos_batch(
    store, faiss, embedder,
    *,
    per_tick: int | None = None,
    root: Path | None = None,
    namespace: str | None = None,
) -> CodeReposResult:
    """한 tick 분량만 처리. 전체 완료는 여러 tick 걸쳐 진행."""
    from gstar.ingest.pipeline import ingest_path
    from gstar.ingest.qdrant_meta import default_index_path, load_from_dir

    res = CodeReposResult()
    if not os.environ.get("CODE_REPOS_ENABLED", "off").lower() in {"on", "1", "true"}:
        return res

    root = root or Path(os.environ.get("CODE_REPOS_ROOT", "/nas/workspace/code_repos"))
    ns = namespace or os.environ.get("CODE_REPOS_NAMESPACE", "code_repos")
    n = per_tick or int(os.environ.get("CODE_REPOS_PER_TICK", "50"))
    globs = [g for g in os.environ.get("CODE_REPOS_GLOBS", _DEFAULT_GLOBS).split(",") if g]

    files = _collect_files(root, globs)
    res.scanned = len(files)
    if not files:
        return res

    cursor = _read_cursor()
    res.cursor_before = cursor
    # cursor 이후 파일만
    start_idx = 0
    if cursor:
        for i, f in enumerate(files):
            if str(f) > cursor:
                start_idx = i
                break
        else:
            start_idx = len(files)      # 모든 파일 이미 cursor 아래
    batch = files[start_idx : start_idx + n]
    if not batch:
        return res

    # Qdrant meta 인덱스 (공유)
    meta_dir = default_index_path()
    meta_index = load_from_dir(meta_dir) if meta_dir.exists() else None

    for fp in batch:
        try:
            ingest_path(
                fp, store=store, faiss=faiss, embedder=embedder,
                root=root, namespace=ns, track="document",
                meta_index=meta_index,
            )
            res.ingested += 1
            res.cursor_after = str(fp)
        except Exception:
            res.errors += 1
        # 크래시 방지 — 파일 1개 처리마다 cursor 업데이트 (복구 가능)
        try:
            _write_cursor(str(fp))
        except Exception:
            pass

    try:
        faiss.save()
    except Exception:
        pass

    res.skipped = max(0, start_idx - 0)   # cursor 이전 파일은 skip 한 것
    return res
