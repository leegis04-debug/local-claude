"""Qdrant dump jsonl 를 읽어 path → metadata 매핑 dict 제공 — Phase A2.

NAS ingest 시 각 .md 파일 경로를 Qdrant payload 의 `path` 필드와 매칭해
attrs 에 주입 (`source`, `project`, `tags`, `date`, `security_level`, `priority` 등).

Qdrant chunks 는 여러 개지만 같은 원본 파일의 메타는 보통 동일 → 파일 단위로 통합.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


# 원본 파일 단위로 합쳐서 보관할 메타 키 (chunk_* 제외)
_META_KEYS: tuple[str, ...] = (
    "source", "project", "axis", "doc_type", "priority",
    "tags", "date", "security_level", "year", "filename", "filepath",
)


def _normalize_path(p: str) -> str:
    """Qdrant payload 의 path 와 NAS 실제 경로 사이 정규화.

    NAS 루트(`/mnt/nas/workspace/`, 컨테이너에서는 `/nas/workspace/`)를 기준으로
    leading slash 와 trailing whitespace 를 제거. 상대 경로로 통일.
    """
    if not p:
        return ""
    p = p.strip()
    for prefix in ("/mnt/nas/workspace/", "/nas/workspace/", "mnt/nas/workspace/"):
        if p.startswith(prefix):
            p = p[len(prefix):]
            break
    return p.lstrip("/").replace("\\", "/")


def _merge_meta(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """같은 path 여러 chunk → 메타 통합. tags 는 union, 나머지는 first-wins."""
    out = dict(existing)
    for k in _META_KEYS:
        v = incoming.get(k)
        if v is None or v == "":
            continue
        if k == "tags":
            cur = set(out.get("tags") or [])
            cur.update(v if isinstance(v, list) else [v])
            out["tags"] = sorted(cur)
        elif k not in out or out[k] in (None, ""):
            out[k] = v
    # qdrant_ids 누적 (역참조용)
    if incoming.get("_id") is not None:
        out.setdefault("qdrant_ids", []).append(incoming["_id"])
    return out


@dataclass
class QdrantMetaIndex:
    """path → merged metadata dict."""

    by_path: dict[str, dict[str, Any]] = field(default_factory=dict)
    files_seen: int = 0
    rows_read: int = 0

    def lookup(self, path: str) -> dict[str, Any] | None:
        """NAS 실제 파일 경로(절대 또는 상대) → metadata dict.

        경로가 정확히 일치하지 않으면 suffix 매칭(basename·tail 2-3 컴포넌트) 로 폴백.
        """
        key = _normalize_path(path)
        if key in self.by_path:
            return self.by_path[key]
        # suffix 매칭
        parts = key.split("/")
        for n in (3, 2, 1):
            if len(parts) < n:
                continue
            suffix = "/".join(parts[-n:])
            for p, meta in self.by_path.items():
                if p.endswith(suffix):
                    return meta
        return None


def load_from_jsonl(paths: Iterable[Path]) -> QdrantMetaIndex:
    """여러 jsonl 파일을 읽어 path 단위로 메타 병합."""
    idx = QdrantMetaIndex()
    for jl in paths:
        if not jl.exists():
            continue
        with jl.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                idx.rows_read += 1
                path = row.get("path") or row.get("filepath") or ""
                path = _normalize_path(path)
                if not path:
                    continue
                # payload 행은 `{id, path, source, ...}` — id 를 _id 로 옮겨 meta 와 분리
                incoming = {k: v for k, v in row.items() if k in _META_KEYS or k == "id"}
                if "id" in incoming:
                    incoming["_id"] = incoming.pop("id")
                cur = idx.by_path.get(path, {})
                if not cur:
                    idx.files_seen += 1
                idx.by_path[path] = _merge_meta(cur, incoming)
    return idx


def load_from_dir(dir_path: Path) -> QdrantMetaIndex:
    if not dir_path.exists():
        return QdrantMetaIndex()
    jsonls = sorted(dir_path.glob("*.jsonl"))
    return load_from_jsonl(jsonls)


def default_index_path() -> Path:
    """기본 인덱스 위치. `GSTAR_QDRANT_META_DIR` env 또는 `$GSTAR_HOME/qdrant_meta/`."""
    env = os.environ.get("GSTAR_QDRANT_META_DIR")
    if env:
        return Path(env)
    home = os.environ.get("GSTAR_HOME", str(Path.home() / ".gstar"))
    return Path(home) / "qdrant_meta"
