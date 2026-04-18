from __future__ import annotations

from local_claude.orchestrator.parser import extract


def test_self_closing() -> None:
    parsed = extract('잡담 <search_rag query="스마트서비스" top_k="3"/> 이후.')
    assert len(parsed) == 1
    p = parsed[0]
    assert p.name == "search_rag"
    assert p.payload == {"query": "스마트서비스", "top_k": "3"}
    assert "_body" not in p.payload


def test_paired_with_body() -> None:
    parsed = extract("<ask_deep rag='true'>\n여러 줄\n프롬프트\n</ask_deep>")
    assert len(parsed) == 1
    p = parsed[0]
    assert p.name == "ask_deep"
    assert p.payload["rag"] == "true"
    assert p.payload["_body"] == "여러 줄\n프롬프트"


def test_multiple_actions_in_order() -> None:
    text = (
        "<search_rag>Q1</search_rag>"
        "중간 텍스트"
        '<validate tool="validate_step"/>'
        "<critique>이 글 검토</critique>"
    )
    parsed = extract(text)
    assert [p.name for p in parsed] == ["search_rag", "validate", "critique"]
    assert parsed[0].payload["_body"] == "Q1"
    assert parsed[1].payload == {"tool": "validate_step"}


def test_ignores_unknown_tags() -> None:
    parsed = extract("<foo>bar</foo><search_rag>Q</search_rag><random/>")
    assert [p.name for p in parsed] == ["search_rag"]


def test_empty_text() -> None:
    assert extract("") == []
    assert extract(None) == []  # type: ignore[arg-type]


def test_attribute_quotes_mixed() -> None:
    parsed = extract('<search_rag query="X" top_k=\'7\'/>')
    assert parsed[0].payload == {"query": "X", "top_k": "7"}


def test_empty_body_not_added() -> None:
    parsed = extract("<search_rag>   \n  </search_rag>")
    assert parsed[0].payload == {}


def test_unclosed_tag_ignored() -> None:
    parsed = extract("<search_rag query=\"Q\">본문")
    # 닫는 태그 없으면 매칭 실패 — 안전한 기본.
    assert parsed == []
