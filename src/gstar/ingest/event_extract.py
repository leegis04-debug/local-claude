"""Phase H3 — event/evidence 정규식 MVP.

event: fact.text 에서 날짜/시점 패턴 추출 → Node(kind='event')
evidence: URL/인용 마커 추출 → Node(kind='evidence')

pipeline.ingest_path 의 구조화 모드(GSTAR_STRUCTURED=on) 에서만 호출.
각 추출 결과는 원본 fact 와 edge 로 연결:
  (event) -[when_of]-> (fact)
  (fact)  -[evidence_of]-> (evidence)
"""

from __future__ import annotations

import re
from dataclasses import dataclass


# 날짜 패턴 — 한국어 + 국제 스타일
_DATE_PATTERNS = [
    # 2026-04-19, 2026.04.19, 2026/04/19
    re.compile(r"\b(20\d{2})[-./](\d{1,2})[-./](\d{1,2})\b"),
    # 2026년 04월 19일 / 2026년 4월 19일 (연·월·일 모두)
    re.compile(r"\b(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일"),
    # 2026년 4월 (월만)
    re.compile(r"\b(20\d{2})년\s*(\d{1,2})월(?!\s*\d)"),
    # 2026년 (연만)
    re.compile(r"\b(20\d{2})년"),
]

# URL + citation 패턴
_URL_RE = re.compile(r"https?://[^\s\)\]<>'\"]+")
_CITATION_RE = re.compile(
    r"(출처|참고|Reference|Source|인용)[\s:：]+([^\n。\.]{5,200})",
    re.IGNORECASE,
)
# 연도 포함 괄호 citation: (Microsoft Research, 2024), (홍길동 2023)
_PAREN_CITE_RE = re.compile(r"\([^()]{0,40}(20\d{2})[^()]{0,20}\)")


@dataclass
class EventExtract:
    date_text: str            # 원문 매칭 문자열
    year: int
    month: int | None = None
    day: int | None = None
    line_no: int = 0


@dataclass
class EvidenceExtract:
    kind: str                 # "url" | "citation" | "paren_cite"
    text: str                 # 추출된 원문
    line_no: int = 0


def extract_events_from_text(text: str, *, line_no: int = 0) -> list[EventExtract]:
    """fact.text 에서 날짜 패턴 추출. 한 fact 당 보통 0~2개.

    패턴이 더 구체적인 것 우선(연월일 > 연월 > 연) — span 기반 overlap 제거.
    """
    out: list[EventExtract] = []
    covered_spans: list[tuple[int, int]] = []
    seen_values: set[tuple[int, int | None, int | None]] = set()

    def _overlaps(a: int, b: int) -> bool:
        for s, e in covered_spans:
            if not (b <= s or a >= e):
                return True
        return False

    for pat in _DATE_PATTERNS:
        for m in pat.finditer(text):
            if _overlaps(m.start(), m.end()):
                continue
            g = m.groups()
            y = int(g[0])
            mo = int(g[1]) if len(g) >= 2 and g[1] and g[1].isdigit() else None
            d = int(g[2]) if len(g) >= 3 and g[2] and g[2].isdigit() else None
            if mo is not None and not (1 <= mo <= 12):
                continue
            if d is not None and not (1 <= d <= 31):
                continue
            value_key = (y, mo, d)
            if value_key in seen_values:
                covered_spans.append((m.start(), m.end()))  # 범위는 잡아둠
                continue
            seen_values.add(value_key)
            covered_spans.append((m.start(), m.end()))
            out.append(EventExtract(date_text=m.group(0), year=y, month=mo, day=d, line_no=line_no))
    return out


def extract_evidence_from_text(text: str, *, line_no: int = 0) -> list[EvidenceExtract]:
    out: list[EvidenceExtract] = []
    seen: set[tuple[str, str]] = set()
    for m in _URL_RE.finditer(text):
        key = ("url", m.group(0))
        if key in seen:
            continue
        seen.add(key)
        out.append(EvidenceExtract(kind="url", text=m.group(0), line_no=line_no))
    for m in _CITATION_RE.finditer(text):
        ref = m.group(2).strip()
        key = ("citation", ref)
        if key in seen:
            continue
        seen.add(key)
        out.append(EvidenceExtract(kind="citation", text=ref, line_no=line_no))
    for m in _PAREN_CITE_RE.finditer(text):
        full = m.group(0).strip()
        key = ("paren_cite", full)
        if key in seen:
            continue
        seen.add(key)
        out.append(EvidenceExtract(kind="paren_cite", text=full, line_no=line_no))
    return out


def event_text(ev: EventExtract) -> str:
    """Node.text 로 쓸 canonical 표현."""
    if ev.month is None:
        return f"{ev.year}"
    if ev.day is None:
        return f"{ev.year}-{ev.month:02d}"
    return f"{ev.year}-{ev.month:02d}-{ev.day:02d}"
