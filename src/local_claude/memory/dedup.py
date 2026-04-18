"""opt-in 중복 메모리 항목 병합.

자동 실행 안 함. `find_candidates(...)` 로 후보만 보거나
`dedup(..., apply=True)` 로 명시 병합.

유사도 측정: difflib.SequenceMatcher.ratio() (표준 라이브러리).
같은 category 안에서만 비교 — 카테고리 다른 항목은 유사해도 병합 금지.

병합 정책:
- 더 오래된 `created_at` 을 keep (기원 보존)
- `last_seen` 은 max
- `touch_count` 는 합산
- text 는 더 긴 쪽 유지 (더 정보 많음)
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from .. import config
from . import _store

DEFAULT_THRESHOLD = 0.85


@dataclass
class DuplicateCandidate:
    keep_index: int
    merge_index: int
    ratio: float
    keep_text: str
    merge_text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "keep_index": self.keep_index,
            "merge_index": self.merge_index,
            "ratio": round(self.ratio, 3),
            "keep_text": self.keep_text,
            "merge_text": self.merge_text,
        }


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(a=a, b=b, autojunk=False).ratio()


def find_candidates(
    entries: list[dict[str, Any]],
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[DuplicateCandidate]:
    """유사 항목 쌍을 반환. O(n²) — 메모리 항목 규모 작아 실용적."""
    results: list[DuplicateCandidate] = []
    paired: set[int] = set()  # 이미 병합 후보가 된 인덱스는 추가 매칭 스킵
    for i, a in enumerate(entries):
        if i in paired:
            continue
        text_a = str(a.get("text", ""))
        cat_a = str(a.get("category", ""))
        if not text_a:
            continue
        for j in range(i + 1, len(entries)):
            if j in paired:
                continue
            b = entries[j]
            if str(b.get("category", "")) != cat_a:
                continue
            text_b = str(b.get("text", ""))
            if not text_b:
                continue
            ratio = _ratio(text_a, text_b)
            if ratio >= threshold:
                # keep 은 더 오래된 created_at 기준. 동률이면 i.
                ca = str(a.get("created_at", ""))
                cb = str(b.get("created_at", ""))
                if cb and ca and cb < ca:
                    keep_i, merge_i = j, i
                    keep_text, merge_text = text_b, text_a
                else:
                    keep_i, merge_i = i, j
                    keep_text, merge_text = text_a, text_b
                results.append(
                    DuplicateCandidate(
                        keep_index=keep_i,
                        merge_index=merge_i,
                        ratio=ratio,
                        keep_text=keep_text,
                        merge_text=merge_text,
                    )
                )
                paired.add(j)
                break  # i 당 1쌍만 — 체인 병합은 다음 dedup 호출에서.
    return results


def _merge(keep: dict[str, Any], merge: dict[str, Any]) -> dict[str, Any]:
    result = dict(keep)
    # 더 긴 text 를 정보량 기준으로 유지.
    if len(str(merge.get("text", ""))) > len(str(result.get("text", ""))):
        result["text"] = merge["text"]
    # last_seen max
    for field in ("last_seen",):
        a = str(result.get(field, ""))
        b = str(merge.get(field, ""))
        if b and b > a:
            result[field] = b
    # touch_count 합산
    result["touch_count"] = int(result.get("touch_count", 0)) + int(merge.get("touch_count", 0)) + 1
    return result


def _dedup_file(
    path: Path,
    *,
    threshold: float,
    apply: bool,
) -> tuple[list[DuplicateCandidate], int]:
    if not path.exists():
        return [], 0
    data = _store.load(path)
    entries: list[dict[str, Any]] = data.get("entries", [])
    candidates = find_candidates(entries, threshold=threshold)
    if not apply or not candidates:
        return candidates, 0

    # 병합 — 인덱스 기반으로 교체.
    merged = list(entries)
    drop_indices: set[int] = set()
    for cand in candidates:
        if cand.keep_index in drop_indices or cand.merge_index in drop_indices:
            continue
        merged[cand.keep_index] = _merge(merged[cand.keep_index], merged[cand.merge_index])
        drop_indices.add(cand.merge_index)

    new_entries = [e for i, e in enumerate(merged) if i not in drop_indices]
    data["entries"] = new_entries
    _store.save(path, data)
    return candidates, len(drop_indices)


def dedup(
    project_dir: Path | str | None = None,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    apply: bool = False,
) -> dict[str, Any]:
    """프로젝트+유저 메모리에서 중복 후보 탐색. apply=True 면 병합 실행."""
    result: dict[str, Any] = {"threshold": threshold, "applied": apply}
    for layer, path in (
        ("project", config.project_memory_path(project_dir)),
        ("user", config.USER_MEMORY_PATH),
    ):
        candidates, merged = _dedup_file(path, threshold=threshold, apply=apply)
        result[layer] = {
            "candidates": [c.to_dict() for c in candidates],
            "merged": merged,
        }
    return result
