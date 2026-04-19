"""문서 → fact 노드 분해.

규칙:
- markdown 헤더(#, ##, ...)는 상위 섹션 메타로만 보존. 노드화 안 함.
- 리스트 항목(- / *)은 각각 하나의 fact.
- 일반 문단은 한국어/영어 문장 경계로 분리.
- 최소 길이 필터(기본 8자)로 너무 짧은 단편 제거.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。\.])\s+(?=[A-Z가-힣])|(?<=다\.)\s+|(?<=요\.)\s+")
_HEADER_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_LIST_RE = re.compile(r"^[-*]\s+(.*)$")
_ORDERED_LIST_RE = re.compile(r"^\d+[.)]\s+(.*)$")


@dataclass
class Fact:
    text: str
    source: str           # 파일 경로 (상대 표현)
    section: str          # 가장 최근 헤더 텍스트 (없으면 "")
    line_no: int


def chunk_file(path: Path, root: Path | None = None, min_len: int = 8) -> list[Fact]:
    """단일 파일을 fact 리스트로 분해."""

    raw = path.read_text(encoding="utf-8", errors="replace")
    # NULL byte / 기타 control character 정화 — kiwipiepy 같은 C extension 이 null byte 에서
    # `corrupted double-linked list` 메모리 손상 유발 (2026-04-19 company crash 재현 조건).
    # 허용 제어문자: \t \n \r. 나머지는 제거.
    if "\x00" in raw or any(ord(c) < 32 and c not in "\t\n\r" for c in raw[:1024]):
        raw = "".join(c for c in raw if c in "\t\n\r" or ord(c) >= 32)
    source = str(path.relative_to(root)) if root else str(path)

    facts: list[Fact] = []
    section = ""
    for line_no, raw_line in enumerate(raw.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        m = _HEADER_RE.match(line)
        if m:
            section = m.group(2).strip()
            continue

        m = _LIST_RE.match(line) or _ORDERED_LIST_RE.match(line)
        if m:
            _append_fact(facts, m.group(1).strip(), source, section, line_no, min_len)
            continue

        for s in _split_sentences(line):
            _append_fact(facts, s, source, section, line_no, min_len)

    return facts


def chunk_path(target: Path, root: Path | None = None, min_len: int = 8) -> list[Fact]:
    """파일 또는 디렉토리 일괄 분해. .md / .txt 대상."""

    root = root or (target if target.is_dir() else target.parent)
    files: list[Path]
    if target.is_dir():
        files = sorted(
            p
            for p in target.rglob("*")
            if p.is_file() and p.suffix.lower() in {".md", ".txt", ".markdown"}
        )
    else:
        files = [target]

    all_facts: list[Fact] = []
    for f in files:
        all_facts.extend(chunk_file(f, root=root, min_len=min_len))
    return all_facts


def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_SPLIT.split(text)
    return [p.strip() for p in parts if p and p.strip()]


def _append_fact(
    facts: list[Fact], text: str, source: str, section: str, line_no: int, min_len: int
) -> None:
    if len(text) < min_len:
        return
    facts.append(Fact(text=text, source=source, section=section, line_no=line_no))
