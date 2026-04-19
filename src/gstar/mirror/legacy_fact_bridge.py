"""Phase H9 fine-grained — legacy chunk → 문장 분해 → G fact 매칭.

사용자 진단 (2026-04-19): 1차 coarse H9 (G → legacy search) 는 G 의 짧은
fact/entity 와 legacy 의 긴 chunk passage 간 granularity 불일치로 low=499/500.

해결: legacy chunk 를 문장 단위로 쪼개고 각 문장을 G `/search/hybrid` 에
검색. 매칭 high 이상 나오는 문장만 legacy→G anchor 로 기록. 나머지는
legacy_only 유지 (강제 정합화 금지 원칙 그대로).

입력: qdrant_meta jsonl dump (Phase A2 산출물). `text_preview` 필드 사용.
점진 처리: collection 당 cursor 파일로 offset 저장.

env:
    LEGACY_FACT_BRIDGE_ENABLED   (기본 off — 비용 주의, 명시적 opt-in)
    LEGACY_FACT_COLL             (쉼표 구분 collection, 기본 "proposals,personal_notes")
    LEGACY_FACT_PER_TICK         (tick 당 처리 chunk 수, 기본 200)
    LEGACY_FACT_MIN_LEN          (문장 최소 길이, 기본 15)
    LEGACY_FACT_HIGH_THRESHOLD   (기본 0.80)
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


_SENTENCE_SPLIT = re.compile(
    r"(?<=[.!?。])\s+(?=[A-Z가-힣])|(?<=다\.)\s+|(?<=요\.)\s+|(?<=음\.)\s+|\n\s*\n"
)


@dataclass
class FactBridgeResult:
    chunks_scanned: int = 0
    sentences_extracted: int = 0
    sentences_matched_high: int = 0
    sentences_matched_medium: int = 0
    errors: int = 0
    cursor_after: int = 0


def _split_sentences(text: str, min_len: int) -> list[str]:
    parts = _SENTENCE_SPLIT.split(text)
    return [p.strip() for p in parts if p and len(p.strip()) >= min_len]


def _qdrant_meta_dir() -> Path:
    env = os.environ.get("GSTAR_QDRANT_META_DIR")
    if env:
        return Path(env)
    home = os.environ.get("GSTAR_HOME", str(Path.home() / ".gstar"))
    return Path(home) / "qdrant_meta"


def _cursor_path(coll: str) -> Path:
    home = Path(os.environ.get("GSTAR_HOME", str(Path.home() / ".gstar")))
    return home / f"legacy_fact_cursor_{coll}.txt"


def _read_cursor(coll: str) -> int:
    p = _cursor_path(coll)
    if not p.exists():
        return 0
    try:
        return int(p.read_text(encoding="utf-8").strip() or "0")
    except Exception:
        return 0


def _write_cursor(coll: str, v: int) -> None:
    p = _cursor_path(coll)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(v), encoding="utf-8")


def _iter_chunks_from_jsonl(path: Path, start: int, limit: int):
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i < start:
                continue
            if i >= start + limit:
                break
            try:
                yield i, json.loads(line)
            except Exception:
                continue


def _g_search_hybrid(query: str, top_k: int = 1, timeout: float = 5.0) -> list[dict]:
    """G serve 의 /search/hybrid — 자기 프로세스이므로 localhost."""
    body = json.dumps({"query": query[:500], "top_k": top_k}).encode("utf-8")
    req = urllib.request.Request(
        "http://localhost:9999/search/hybrid",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception:
        return []
    return d if isinstance(d, list) else (d.get("results") or d.get("hits") or [])


def _record_link(
    store, g_node_id: str, external_id: str, status: str,
    collection: str, score: float,
) -> None:
    now = datetime.now(timezone.utc)
    with store.lock:
        store.conn.execute(
            "INSERT OR REPLACE INTO derived_view "
            "(g_node_id, view, external_id, collection, emitted_at, status, error) "
            "VALUES (?, 'legacy_fact', ?, ?, ?, ?, ?)",
            [g_node_id, external_id, collection, now, status, f"score={score:.3f}"],
        )


def bridge_legacy_to_g_facts(store, *, per_tick: int | None = None) -> FactBridgeResult:
    """legacy jsonl chunk → 문장 분해 → G fact 매칭.

    매칭된 G fact 의 node_id 에 legacy chunk id 를 anchor 로 연결.
    legacy 원본 chunk 는 보존 (derived_view 에 연결 레이어만).
    """
    res = FactBridgeResult()
    if not os.environ.get("LEGACY_FACT_BRIDGE_ENABLED", "off").lower() in {"on", "1", "true"}:
        return res

    per_tick = per_tick or int(os.environ.get("LEGACY_FACT_PER_TICK", "200"))
    min_len = int(os.environ.get("LEGACY_FACT_MIN_LEN", "15"))
    high_t = float(os.environ.get("LEGACY_FACT_HIGH_THRESHOLD", "0.80"))
    med_t = float(os.environ.get("LEGACY_FACT_MEDIUM_THRESHOLD", "0.68"))
    colls = [c.strip() for c in os.environ.get("LEGACY_FACT_COLL", "proposals,personal_notes").split(",") if c.strip()]

    meta_dir = _qdrant_meta_dir()
    processed_total = 0

    for coll in colls:
        if processed_total >= per_tick:
            break
        jsonl = meta_dir / f"{coll}.jsonl"
        if not jsonl.exists():
            continue
        start = _read_cursor(coll)
        remaining = per_tick - processed_total
        last_i = start
        for i, row in _iter_chunks_from_jsonl(jsonl, start, remaining):
            last_i = i + 1
            processed_total += 1
            res.chunks_scanned += 1
            text = (row.get("text_preview") or "")[:2000]
            if not text.strip():
                continue
            sentences = _split_sentences(text, min_len)
            for sent in sentences:
                res.sentences_extracted += 1
                hits = _g_search_hybrid(sent, top_k=1)
                if not hits:
                    continue
                top = hits[0]
                # G mirror 자기자신 excluded
                if (top.get("namespace") or "").startswith("claude_traces"):
                    continue
                score = float(top.get("score") or 0.0)
                if score < med_t:
                    continue
                g_nid = str(top.get("node_id") or "")
                if not g_nid:
                    continue
                ext_id = str(row.get("id") or "")
                if score >= high_t:
                    _record_link(store, g_nid, ext_id, "legacy_fact_high", coll, score)
                    res.sentences_matched_high += 1
                else:
                    _record_link(store, g_nid, ext_id, "legacy_fact_medium", coll, score)
                    res.sentences_matched_medium += 1
        _write_cursor(coll, last_i)
        res.cursor_after = last_i

    return res
