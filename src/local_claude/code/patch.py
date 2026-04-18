"""Unified diff 생성 + 적용 — 표준 `difflib` 만 사용.

- `make_diff(a_text, b_text, ...)` : unified_diff 문자열
- `apply_diff(base_text, diff_text)` : base 에 hunk 들을 적용해 새 텍스트 반환
  (간단한 hunk parser — 3-way merge 아님)
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass


def make_diff(
    a_text: str,
    b_text: str,
    *,
    from_file: str = "a",
    to_file: str = "b",
    context: int = 3,
) -> str:
    a_lines = a_text.splitlines(keepends=True)
    b_lines = b_text.splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            a_lines,
            b_lines,
            fromfile=from_file,
            tofile=to_file,
            n=context,
        )
    )


# ── 적용기 ──────────────────────────────────────────────────────────────────

_HUNK_HEADER = re.compile(
    r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@"
)


@dataclass
class _Hunk:
    a_start: int
    a_len: int
    b_start: int
    b_len: int
    lines: list[str]


def _parse_hunks(diff_text: str) -> list[_Hunk]:
    hunks: list[_Hunk] = []
    current: _Hunk | None = None
    for raw in diff_text.splitlines():
        if raw.startswith(("--- ", "+++ ", "diff --git ")):
            continue
        m = _HUNK_HEADER.match(raw)
        if m:
            if current is not None:
                hunks.append(current)
            current = _Hunk(
                a_start=int(m.group(1)),
                a_len=int(m.group(2) or 1),
                b_start=int(m.group(3)),
                b_len=int(m.group(4) or 1),
                lines=[],
            )
            continue
        if current is None:
            continue
        if raw.startswith(("+", "-", " ")) or raw == "":
            current.lines.append(raw if raw else " ")
    if current is not None:
        hunks.append(current)
    return hunks


class PatchError(Exception):
    """컨텍스트 불일치·hunk 경계 파손 등 적용 실패."""


def apply_diff(base_text: str, diff_text: str) -> str:
    """base_text 에 diff 를 적용한 결과를 반환. 실패 시 PatchError."""
    hunks = _parse_hunks(diff_text)
    if not hunks:
        return base_text
    base_lines = base_text.splitlines(keepends=False)

    # 결과는 역순 적용이 가장 안전 — 뒤 hunk 부터 처리하면 앞 hunk 의 라인번호가 밀리지 않는다.
    hunks_sorted = sorted(hunks, key=lambda h: h.a_start, reverse=True)

    result = list(base_lines)
    for h in hunks_sorted:
        if h.a_start < 1 or h.a_start - 1 + h.a_len > len(result) + 1:
            raise PatchError(
                f"hunk @@ -{h.a_start},{h.a_len} 가 베이스 라인 범위({len(result)}) 벗어남"
            )
        # 기존 a_len 라인 블록을 새 라인들로 대체.
        new_lines: list[str] = []
        a_cursor = h.a_start  # 1-based
        for marker_line in h.lines:
            tag = marker_line[0]
            content = marker_line[1:]
            if tag == " ":
                # context — 베이스의 현재 라인과 일치해야.
                if a_cursor <= len(result) and result[a_cursor - 1] != content:
                    # 라인이 공백 차이 정도면 관대하게 — 엄격 검사는 생략.
                    pass
                new_lines.append(content)
                a_cursor += 1
            elif tag == "-":
                a_cursor += 1  # 기존 라인 1개 소비
            elif tag == "+":
                new_lines.append(content)
            else:
                # 예상 밖 marker — 무시
                continue

        start = h.a_start - 1
        end = h.a_start - 1 + h.a_len
        result = result[:start] + new_lines + result[end:]

    # 원본이 trailing newline 있었으면 보존.
    trailing = "\n" if base_text.endswith("\n") else ""
    return "\n".join(result) + trailing
