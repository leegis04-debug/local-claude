"""Sprint C #1 — wiki/_inbox 감시자 (인간 편집 → G 재흡수).

Architecture v4.1 §4.4 편집 피드백 루프:
  사용자 Obsidian 에서 _inbox/my_note.md 작성
    → 다음 worker tick 이 감지
    → POST /ingest/nas 와 동일 경로로 G 흡수
    → 다음 wiki_mirror tick 이 wiki 전체 regenerate → 노트 내용 관련 entity/topic md 에 반영
    → _inbox/my_note.md 는 _processed/<ts>_my_note.md 로 archive

원칙 (§4):
- wiki 본문 md 는 독자 상태 없음. 인간 편집은 오로지 `_inbox/` 에.
- ingest 성공 시 원본을 `_processed/` 로 이동 (유실 방지, 감사 목적).
- 실패 시 `_failed/` 로 이동 + errors 에 기록 (loop 재시도 않음).

env:
  GSTAR_WIKI_INBOX_NS     ingest 대상 namespace (기본 personal_notes)
  GSTAR_WIKI_INBOX_TRACK  track (기본 document)
  GSTAR_WIKI_INBOX_MAX    한 tick 당 처리 상한 (기본 50, 폭주 방어)
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from gstar.embedding import Embedder
from gstar.ingest.pipeline import ingest_path
from gstar.mirror.wiki_view import _out_dir
from gstar.storage.duckdb_store import DuckStore
from gstar.storage.faiss_index import FaissStore


@dataclass
class InboxResult:
    scanned: int = 0
    ingested: int = 0
    skipped: int = 0
    failed: int = 0
    facts_added: int = 0
    entities_added: int = 0
    edges_added: int = 0
    errors: list[str] = None

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


def _inbox_dir(wiki_dir: Path | None = None) -> Path:
    return (wiki_dir or _out_dir()) / "_inbox"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _move_safely(src: Path, dst_dir: Path) -> Path:
    """CIFS 에서 rename 이 실패할 수 있어 copy+unlink 로 폴백."""
    dst_dir.mkdir(parents=True, exist_ok=True)
    target = dst_dir / f"{_ts()}_{src.name}"
    try:
        src.rename(target)
    except OSError:
        shutil.copy2(src, target)
        try:
            src.unlink()
        except OSError:
            pass
    return target


def ingest_inbox(
    store: DuckStore,
    faiss: FaissStore,
    embedder: Embedder,
    wiki_dir: Path | None = None,
) -> InboxResult:
    inbox = _inbox_dir(wiki_dir)
    result = InboxResult()
    if not inbox.exists():
        return result

    ns = os.environ.get("GSTAR_WIKI_INBOX_NS", "personal_notes")
    track = os.environ.get("GSTAR_WIKI_INBOX_TRACK", "document")
    try:
        max_per_tick = int(os.environ.get("GSTAR_WIKI_INBOX_MAX", "50"))
    except ValueError:
        max_per_tick = 50

    processed_dir = inbox / "_processed"
    failed_dir = inbox / "_failed"

    # glob 상위 md 만. _processed / _failed 자체는 제외.
    candidates = sorted(
        p for p in inbox.glob("*.md") if p.is_file()
    )[:max_per_tick]

    for md in candidates:
        result.scanned += 1
        try:
            report = ingest_path(
                md,
                store=store,
                faiss=faiss,
                embedder=embedder,
                namespace=ns,
                track=track,
                min_entity_count=1,   # inbox 는 짧은 노트 허용
            )
        except Exception as exc:
            result.failed += 1
            result.errors.append(f"{md.name}: {type(exc).__name__}: {exc}")
            try:
                _move_safely(md, failed_dir)
            except Exception as mv_exc:
                result.errors.append(f"{md.name} mv_failed: {mv_exc}")
            continue

        if report.facts == 0:
            # 내용 너무 짧거나 chunk 0 → skip (파일은 남겨서 사용자 인지 가능)
            result.skipped += 1
            continue

        result.ingested += 1
        result.facts_added += report.facts
        result.entities_added += report.entities
        result.edges_added += report.edges
        try:
            _move_safely(md, processed_dir)
        except Exception as mv_exc:
            result.errors.append(f"{md.name} mv_processed: {mv_exc}")

    return result
