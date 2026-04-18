"""Entity classifier 규칙 매트릭스 테스트."""

from __future__ import annotations

import pytest

from gstar.entity.classifier import classify, classify_verbose
from gstar.entity.types import EntityKind, Track


@pytest.mark.parametrize(
    "surface,expected",
    [
        ("한국농촌경제연구원", EntityKind.ORG),
        ("서울대학교", EntityKind.ORG),
        ("XYZ 재단", EntityKind.ORG),
        ("환경부", EntityKind.ORG),
        ("2025년", EntityKind.TIMELINE),
        ("2026-04-19", EntityKind.TIMELINE),
        ("Q3", EntityKind.TIMELINE),
        ("상반기", EntityKind.TIMELINE),
    ],
)
def test_common_rules(surface, expected):
    assert classify(surface, track=Track.PROPOSAL) is expected


@pytest.mark.parametrize(
    "surface,expected",
    [
        ("mAP", EntityKind.METRIC),
        ("F1", EntityKind.METRIC),
        ("AP@0.5", EntityKind.METRIC),
        ("정확도", EntityKind.METRIC),
        ("85.3%", EntityKind.METRIC),
        ("5억원", EntityKind.BUDGET),
        ("3천만원", EntityKind.BUDGET),
        ("비전AI 프로젝트", EntityKind.PROJECT),
        ("스마트농업 시스템", EntityKind.PROJECT),
        ("강화학습", EntityKind.TECHNOLOGY),
        ("LLM", EntityKind.TECHNOLOGY),
    ],
)
def test_proposal_rules(surface, expected):
    assert classify(surface, track=Track.PROPOSAL) is expected


@pytest.mark.parametrize(
    "surface,expected",
    [
        ("가설", EntityKind.HYPOTHESIS),
        ("H1", EntityKind.HYPOTHESIS),
        ("ImageNet Dataset", EntityKind.DATASET),
        ("MNIST dataset", EntityKind.DATASET),
        ("Transformer 알고리즘", EntityKind.METHOD),
        ("BERT model", EntityKind.METHOD),
        ("실험", EntityKind.EXPERIMENT),
        ("결과", EntityKind.RESULT),
    ],
)
def test_research_rules(surface, expected):
    assert classify(surface, track=Track.RESEARCH) is expected


@pytest.mark.parametrize(
    "surface,context,expected",
    [
        ("MyClass", "class MyClass(Base):", EntityKind.CLASS),
        ("compute_gravity", "def compute_gravity(goal, store):", EntityKind.FUNCTION),
        ("httpx", "import httpx", EntityKind.MODULE),
        ("duckdb_store", "from gstar.storage import duckdb_store", EntityKind.MODULE),
        ("/search/hybrid", "", EntityKind.API_ENDPOINT),
        ("GET /nodes", "", EntityKind.API_ENDPOINT),
        ("test_gravity_score", "", EntityKind.TEST_CASE),
        ("TestGravity", "", EntityKind.TEST_CASE),
        ("MAX_RETRIES", "", EntityKind.VARIABLE),
    ],
)
def test_coding_rules(surface, context, expected):
    assert classify(surface, context, track=Track.CODING) is expected


@pytest.mark.parametrize(
    "surface,expected",
    [
        ("[12]", EntityKind.CITATION),
        ("doi:10.1234/abc", EntityKind.CITATION),
        ("https://example.com/paper", EntityKind.CITATION),
        ("arXiv:2301.12345", EntityKind.CITATION),
        ("핵심 주장", EntityKind.CLAIM),
    ],
)
def test_document_rules(surface, expected):
    assert classify(surface, track=Track.DOCUMENT) is expected


def test_other_default_for_unknown():
    assert classify("xyz그런거", track=Track.PROPOSAL) is EntityKind.OTHER


def test_classify_verbose_returns_confidence_and_rule():
    r = classify_verbose("한국농촌경제연구원", track=Track.PROPOSAL)
    assert r.kind is EntityKind.ORG
    assert 0.0 <= r.confidence <= 1.0
    assert r.rule


def test_track_string_accepted():
    """Track 을 StrEnum 대신 순수 문자열로 넘겨도 동작."""
    assert classify("mAP", track="proposal") is EntityKind.METRIC
