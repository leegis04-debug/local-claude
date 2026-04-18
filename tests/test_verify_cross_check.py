from __future__ import annotations

import pytest

from local_claude.verify import cross_check as cc
from local_claude.verify import detector as det


def test_supported_when_evidence_contains_claim_tokens() -> None:
    claim = "미트앤퓨쳐 프로젝트는 비전 AI 기반 육묘 품질 판별 시스템이다"
    evidences = [
        {"text": "미트앤퓨쳐 프로젝트 — 비전 AI 를 활용해 육묘 품질 판별 자동화", "source": "project"},
        {"text": "완전 무관한 문서", "source": "other"},
    ]
    result = cc.cross_check(claim, evidences)
    assert result.verdict == "supported"
    assert result.best_score >= cc.SUPPORT_HIGH
    assert result.matches[0].evidence_index == 0


def test_unsupported_when_no_overlap() -> None:
    result = cc.cross_check(
        "Amazon Bedrock 은 월 99 달러 구독형 서비스다",
        [{"text": "완전 다른 맥락 — 농식품 스마트팜 보조금 정책"}],
    )
    assert result.verdict == "unsupported"


def test_partial_medium_overlap() -> None:
    result = cc.cross_check(
        "RAG 파이프라인은 RRF 병합과 리랭커를 사용한다",
        [{"text": "RAG 파이프라인 설명"}],
        high=0.9,
        low=0.2,
    )
    assert result.verdict == "partial"


def test_empty_claim_or_evidences() -> None:
    assert cc.cross_check("", [{"text": "x"}]).verdict == "unsupported"
    assert cc.cross_check("some claim", []).verdict == "unsupported"


def test_llm_mode_parses_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd, input=None, capture_output=True, text=True, timeout=30, check=False):  # noqa: A002
        class R:
            returncode = 0
            stdout = "답변: SUPPORTED"
            stderr = ""

        return R()

    monkeypatch.setattr("local_claude.verify.cross_check.subprocess.run", fake_run)
    monkeypatch.setattr("local_claude.verify.cross_check.shutil.which", lambda _n: "/u/b/ask-gemma")

    result = cc.cross_check("x", [{"text": "y"}], mode="llm")
    assert result.verdict == "supported"
    assert result.mode == "llm"


def test_llm_mode_falls_back_to_unsupported(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr("local_claude.verify.cross_check.subprocess.run", fake_run)
    result = cc.cross_check("x", [{"text": "y"}], mode="llm")
    assert result.verdict == "unsupported"


# ── detector (aggregate) ────────────────────────────────────────────────────

def test_detector_aggregate_supported() -> None:
    answer = (
        "미트앤퓨쳐 프로젝트는 비전 AI 기반 육묘 품질 판별 시스템이다. "
        "Gateway 는 X-Auth-Token 헤더를 사용한다."
    )
    evidences = [
        {"text": "미트앤퓨쳐는 비전 AI 를 활용한 육묘 품질 판별 자동화 프로젝트"},
        {"text": "Gateway 인증은 X-Auth-Token 헤더로 이뤄진다 (Bearer 금지)"},
    ]
    report = det.detect(answer, evidences)
    assert report.overall == "supported"
    assert report.total_claims >= 2
    assert report.score >= 0.75


def test_detector_unsupported_answer() -> None:
    answer = "Claude Sonnet 은 월 99 달러 구독이고 2030 년에 출시됐다."
    evidences = [{"text": "완전 다른 주제 — 스마트팜 정책"}]
    report = det.detect(answer, evidences)
    assert report.overall == "unsupported"
    assert report.score < 0.4


def test_detector_no_claims_empty_answer() -> None:
    report = det.detect("", [{"text": "x"}])
    assert report.overall == "no_claims"
    assert report.total_claims == 0
