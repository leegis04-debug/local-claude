"""share_fetcher 의 파서 로직 단위 테스트.

playwright 네트워크 호출은 하지 않는다 — 고정된 HTML/JSON 으로 추출만 검증.
"""
from __future__ import annotations

import json

import pytest

from local_claude.tools.share_fetcher import (
    CHATGPT_SHARE_RE,
    Conversation,
    Message,
    extract_chatgpt_share,
    _order_by_linkedlist,
)


def test_chatgpt_url_regex():
    assert CHATGPT_SHARE_RE.match("https://chatgpt.com/share/69e39cfc-651c-83e8-8985-bd19b42ff6e5")
    assert CHATGPT_SHARE_RE.match("http://chatgpt.com/share/abc-123")
    assert not CHATGPT_SHARE_RE.match("https://chat.openai.com/share/xyz")
    assert not CHATGPT_SHARE_RE.match("https://example.com/share/abc")


def test_conversation_to_markdown_layout():
    convo = Conversation(
        title="테스트 대화",
        url="https://chatgpt.com/share/x",
        messages=[
            Message(role="user", content="안녕"),
            Message(role="assistant", content="안녕하세요"),
        ],
    )
    md = convo.to_markdown()
    assert md.startswith("# 테스트 대화")
    assert "> Source: https://chatgpt.com/share/x" in md
    assert "## User" in md
    assert "## Assistant" in md
    assert "안녕하세요" in md


def test_conversation_to_json_roundtrip():
    convo = Conversation(
        title="t",
        url="u",
        messages=[Message(role="user", content="hi")],
    )
    data = json.loads(convo.to_json())
    assert data == {
        "title": "t",
        "url": "u",
        "messages": [{"role": "user", "content": "hi"}],
    }


def test_extract_from_next_data_linkedlist():
    next_data_payload = {
        "props": {
            "pageProps": {
                "serverResponse": {
                    "data": {
                        "title": "샘플 대화",
                        "mapping": {
                            "root": {
                                "id": "root",
                                "parent": None,
                                "children": ["a"],
                                "message": None,
                            },
                            "a": {
                                "id": "a",
                                "parent": "root",
                                "children": ["b"],
                                "message": {
                                    "author": {"role": "user"},
                                    "content": {"parts": ["안녕?"]},
                                },
                            },
                            "b": {
                                "id": "b",
                                "parent": "a",
                                "children": [],
                                "message": {
                                    "author": {"role": "assistant"},
                                    "content": {"parts": ["안녕하세요."]},
                                },
                            },
                        },
                    }
                }
            }
        }
    }
    html = f"""
    <html><body>
      <script id="__NEXT_DATA__" type="application/json">
        {json.dumps(next_data_payload)}
      </script>
    </body></html>
    """
    convo = extract_chatgpt_share(html, url="https://chatgpt.com/share/x", title="(fallback)")
    assert convo.title == "샘플 대화"
    assert convo.url == "https://chatgpt.com/share/x"
    assert [m.role for m in convo.messages] == ["user", "assistant"]
    assert convo.messages[0].content == "안녕?"
    assert convo.messages[1].content == "안녕하세요."


def test_extract_falls_back_to_dom_selector():
    html = """
    <html><body>
      <h1>제목 폴백</h1>
      <article data-message-author-role="user">
        <div>유저 메시지</div>
      </article>
      <article data-message-author-role="assistant">
        <div>어시스턴트 답</div>
      </article>
    </body></html>
    """
    convo = extract_chatgpt_share(html, url="u")
    assert convo.title == "제목 폴백"
    assert [m.role for m in convo.messages] == ["user", "assistant"]
    assert "유저 메시지" in convo.messages[0].content
    assert "어시스턴트 답" in convo.messages[1].content


def test_order_by_linkedlist_handles_cycle_gracefully():
    mapping = {
        "a": {"parent": None, "children": ["b"]},
        "b": {"parent": "a", "children": ["a"]},
    }
    ordered = _order_by_linkedlist(mapping)
    assert len(ordered) == 2


def test_empty_html_returns_empty_conversation():
    convo = extract_chatgpt_share("<html><body></body></html>", url="u")
    assert convo.messages == []


@pytest.mark.parametrize(
    "role,label",
    [
        ("user", "User"),
        ("assistant", "Assistant"),
        ("system", "System"),
        ("unknown_role", "Unknown_role"),
    ],
)
def test_role_label_capitalization(role, label):
    convo = Conversation(messages=[Message(role=role, content="x")])
    assert f"## {label}" in convo.to_markdown()
