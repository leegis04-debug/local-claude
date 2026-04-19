"""Phase H3 event/evidence 정규식 테스트."""

from gstar.ingest.event_extract import (
    EventExtract,
    event_text,
    extract_events_from_text,
    extract_evidence_from_text,
)


def test_date_iso():
    events = extract_events_from_text("보고서 2026-04-19 작성됨.")
    assert len(events) == 1
    assert (events[0].year, events[0].month, events[0].day) == (2026, 4, 19)
    assert event_text(events[0]) == "2026-04-19"


def test_date_korean():
    events = extract_events_from_text("2025년 11월 3일 회의록")
    assert len(events) == 1
    assert (events[0].year, events[0].month, events[0].day) == (2025, 11, 3)


def test_date_month_only():
    events = extract_events_from_text("2024년 6월 프로젝트")
    assert len(events) == 1
    assert events[0].month == 6 and events[0].day is None
    assert event_text(events[0]) == "2024-06"


def test_date_year_only_dedup():
    events = extract_events_from_text("2023년 계획과 2023년 예산")
    # 중복 제거 후 1건
    assert len(events) == 1
    assert events[0].year == 2023


def test_invalid_month_day_filtered():
    # 13월, 32일은 reject
    events = extract_events_from_text("잘못된 날짜 2024-13-01 과 2024-05-32")
    assert all(
        (e.month is None or 1 <= e.month <= 12) and (e.day is None or 1 <= e.day <= 31)
        for e in events
    )


def test_url_evidence():
    ev = extract_evidence_from_text("자세한 내용은 https://example.com/path 참고.")
    kinds = {e.kind for e in ev}
    assert "url" in kinds
    urls = [e for e in ev if e.kind == "url"]
    assert urls[0].text == "https://example.com/path"


def test_citation_korean():
    ev = extract_evidence_from_text("출처: Microsoft Research GraphRAG 논문 2024")
    assert any(e.kind == "citation" for e in ev)


def test_paren_cite():
    ev = extract_evidence_from_text("GraphRAG (Microsoft Research, 2024) 는 새로운 접근.")
    assert any(e.kind == "paren_cite" for e in ev)


def test_no_date_no_event():
    assert extract_events_from_text("날짜 없는 문장입니다.") == []
