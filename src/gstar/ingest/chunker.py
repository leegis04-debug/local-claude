"""문서 → fact 노드 분해.

Phase H1+H2 — 3단계 정규화:
- markdown 헤더 → `Section` 계층 (레벨별 트리)
- 리스트/문단 → `Fact` — 직전 section 에 속함
- 파일 자체 → `Document` root (모든 section/fact 의 조상)

규칙:
- 최소 길이 필터(기본 8자)로 너무 짧은 단편 제거.
- fact.section_id 가 있으면 fact →(part_of)→ section → (part_of) → document 체인 구성.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
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
    # Phase H2: 계층 링크용 — chunk_file_structured 만 채움
    section_id: str = ""  # 소속 section 의 placeholder id
    document_id: str = ""  # 소속 document 의 placeholder id


@dataclass
class Section:
    """Phase H2 — 헤더 한 개가 하나의 section."""

    id: str                # placeholder (chunk 시 생성, ingest 시 Node.id 로 교체)
    level: int             # # 개수
    title: str
    source: str
    line_no: int
    parent_id: str = ""    # 부모 section 또는 document id
    document_id: str = ""


@dataclass
class Document:
    """Phase H2 — 파일 한 개가 하나의 document."""

    id: str                # placeholder
    source: str            # 상대 경로 문자열
    content_hash: str      # 원본 파일 sha1 (version 체인 키)
    title: str = ""        # 첫 h1 을 기본값


@dataclass
class StructuredChunks:
    """`chunk_file_structured` 결과. ingest_path 가 document/section/fact 를 모두 저장."""

    document: Document
    sections: list[Section] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)


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


# ---------- Phase H2: 구조화된 chunker (document/section/fact 동시 생성) -------


def _new_id(prefix: str) -> str:
    import uuid
    return f"_{prefix}_{uuid.uuid4().hex[:12]}"


def chunk_file_structured(
    path: Path, root: Path | None = None, min_len: int = 8
) -> StructuredChunks:
    """단일 파일 → document + section tree + fact 리스트.

    ingest_path (Phase H7) 가 반환값을 Node(document/section/fact) 로 저장하고
    part_of edge 로 계층 연결. placeholder id 는 Node 생성 시 실제 id 로 치환.
    """
    raw = path.read_text(encoding="utf-8", errors="replace")
    # Phase G 에서 추가된 NULL/제어문자 정화 유지
    if "\x00" in raw or any(ord(c) < 32 and c not in "\t\n\r" for c in raw[:1024]):
        raw = "".join(c for c in raw if c in "\t\n\r" or ord(c) >= 32)

    source = str(path.relative_to(root)) if root else str(path)
    content_hash = hashlib.sha1(raw.encode("utf-8")).hexdigest()

    doc = Document(id=_new_id("doc"), source=source, content_hash=content_hash)

    sections: list[Section] = []
    facts: list[Fact] = []
    # 레벨별 최근 section id 스택. 부모 결정용.
    stack: list[tuple[int, str]] = []    # (level, section_id)
    current_section_id: str = doc.id

    for line_no, raw_line in enumerate(raw.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        m = _HEADER_RE.match(line)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            sid = _new_id("sec")
            # 스택 pop until parent level < current level
            while stack and stack[-1][0] >= level:
                stack.pop()
            parent_id = stack[-1][1] if stack else doc.id
            sec = Section(
                id=sid,
                level=level,
                title=title,
                source=source,
                line_no=line_no,
                parent_id=parent_id,
                document_id=doc.id,
            )
            sections.append(sec)
            stack.append((level, sid))
            current_section_id = sid
            if level == 1 and not doc.title:
                doc.title = title
            continue

        m = _LIST_RE.match(line) or _ORDERED_LIST_RE.match(line)
        if m:
            t = m.group(1).strip()
            if len(t) >= min_len:
                facts.append(
                    Fact(
                        text=t,
                        source=source,
                        section=sections[-1].title if sections else "",
                        line_no=line_no,
                        section_id=current_section_id,
                        document_id=doc.id,
                    )
                )
            continue

        for s in _split_sentences(line):
            if len(s) < min_len:
                continue
            facts.append(
                Fact(
                    text=s,
                    source=source,
                    section=sections[-1].title if sections else "",
                    line_no=line_no,
                    section_id=current_section_id,
                    document_id=doc.id,
                )
            )

    # 기본 title 백업 — 첫 section 또는 파일명
    if not doc.title:
        doc.title = sections[0].title if sections else Path(source).stem

    return StructuredChunks(document=doc, sections=sections, facts=facts)


def chunk_path_structured(
    target: Path, root: Path | None = None, min_len: int = 8
) -> list[StructuredChunks]:
    """디렉터리 재귀 → 파일별 StructuredChunks 리스트."""
    root = root or (target if target.is_dir() else target.parent)
    if target.is_dir():
        files = sorted(
            p
            for p in target.rglob("*")
            if p.is_file() and p.suffix.lower() in {".md", ".txt", ".markdown"}
        )
    else:
        files = [target]
    return [chunk_file_structured(f, root=root, min_len=min_len) for f in files]
