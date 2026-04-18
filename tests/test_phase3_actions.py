from __future__ import annotations

import json

from local_claude.actions import cross_check as cc_action
from local_claude.actions import rerank as rerank_action
from local_claude.orchestrator import executor, parser


def test_rerank_action_via_body_json() -> None:
    body = json.dumps(
        [
            {"text": "RAG 하이브리드 검색 지식그래프"},
            {"text": "완전 다른 주제"},
            {"text": "검색 엔진 기본"},
        ]
    )
    # 단위 테스트는 Gemma 없이 돌려야 하니 heuristic 명시.
    res = rerank_action.RerankAction().execute(
        {"query": "RAG 하이브리드", "_body": body, "mode": "heuristic"}
    )
    assert res.ok is True
    assert res.output[0]["doc"]["text"].startswith("RAG")


def test_rerank_action_supports_string_list() -> None:
    res = rerank_action.RerankAction().execute(
        {
            "query": "Python",
            "docs": ["Python 타입힌트", "JavaScript 이벤트루프", "Python dataclass"],
            "top_k": "2",
            "mode": "heuristic",
        }
    )
    assert res.ok is True
    assert len(res.output) == 2
    assert all("Python" in r["doc"]["text"] for r in res.output)


def test_rerank_empty_query_fails() -> None:
    res = rerank_action.RerankAction().execute({"docs": [{"text": "x"}]})
    assert res.ok is False


def test_rerank_invalid_docs_fails() -> None:
    res = rerank_action.RerankAction().execute(
        {"query": "q", "_body": "not json at all"}
    )
    assert res.ok is False
    assert "파싱" in (res.error or "")


def test_cross_check_action_body_json() -> None:
    body = json.dumps(
        {
            "answer": "Gateway 는 X-Auth-Token 헤더를 사용한다.",
            "evidences": [
                {"text": "Gateway 인증은 X-Auth-Token 헤더로 이뤄진다 (Bearer 금지)"}
            ],
        }
    )
    res = cc_action.CrossCheckAction().execute({"_body": body})
    assert res.ok is True
    assert res.output["overall"] in {"supported", "partial"}


def test_cross_check_action_evidence_json_attr() -> None:
    ev_json = json.dumps([{"text": "Claude Code 는 Anthropic CLI 도구다"}])
    res = cc_action.CrossCheckAction().execute(
        {
            "_body": "Claude Code 는 Anthropic CLI 도구다.",
            "evidences_json": ev_json,
        }
    )
    assert res.ok is True


def test_cross_check_missing_answer_fails() -> None:
    res = cc_action.CrossCheckAction().execute(
        {"evidences": [{"text": "x"}]}
    )
    assert res.ok is False


def test_cross_check_missing_evidence_fails() -> None:
    res = cc_action.CrossCheckAction().execute({"_body": "answer here"})
    assert res.ok is False


def test_parser_picks_up_phase3_tags() -> None:
    text = (
        '<rerank query="X" mode="heuristic" top_k="3">[{"text":"a"},{"text":"b"}]</rerank>'
        '<cross_check mode="local">answer vs ev</cross_check>'
    )
    parsed = parser.extract(text)
    assert [p.name for p in parsed] == ["rerank", "cross_check"]
    assert parsed[0].payload["query"] == "X"
    assert parsed[0].payload["top_k"] == "3"
    assert parsed[0].payload["_body"].startswith("[")


def test_executor_dispatches_phase3_action(tmp_path) -> None:
    text = (
        '<rerank query="q" mode="heuristic">[{"text":"q 관련"},{"text":"완전 무관"}]</rerank>'
    )
    parsed = parser.extract(text)
    executed = executor.dispatch_all(parsed, project_dir=tmp_path)
    assert len(executed) == 1
    assert executed[0].name == "rerank"
    assert executed[0].result.ok is True
