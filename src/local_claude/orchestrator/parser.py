"""LLM 출력에서 XML action 태그를 추출한다 (connect-ai 패턴).

지원 문법:
  <search_rag query="스마트서비스" top_k="5"/>
  <search_rag>스마트서비스</search_rag>
  <search_rag query="X">추가 본문</search_rag>
  <ask_deep rag="true">
    여러 줄 프롬프트
  </ask_deep>

- 속성값은 쌍따옴표/홑따옴표 모두 허용.
- 본문이 있으면 payload["_body"] 로 들어간다 — 각 action 이 해석.
- 중첩 태그는 지원하지 않는다 (LLM 출력이 중첩으로 생성하는 케이스는 없음).
- 태그 사이 텍스트(scratch pad)는 무시.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_TAG_NAMES: tuple[str, ...] = (
    "search_rag",
    "search_graph",
    "ask_deep",
    "validate",
    "critique",
    # Phase 3
    "rerank",
    "cross_check",
    # Phase 4
    "run_test",
    # 여기 이름을 추가하면 즉시 파싱 대상이 된다.
)

_ATTR_RE = re.compile(r"""(\w+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")


@dataclass
class ParsedAction:
    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    raw: str = ""
    start: int = 0
    end: int = 0


def _compile_tag_re(names: tuple[str, ...]) -> re.Pattern[str]:
    name_alt = "|".join(re.escape(n) for n in names)
    # self-closing: <name ... />  또는 paired: <name ...>body</name>
    # attrs 는 non-greedy — 속성값에 `/` 가 있어도(cmd="/bin/true") 매칭 가능.
    return re.compile(
        rf"<(?P<name>{name_alt})(?P<attrs>\s[^>]*?)?\s*"
        rf"(?:/>|>(?P<body>.*?)</(?P=name)>)",
        re.DOTALL,
    )


def _parse_attrs(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    attrs: dict[str, str] = {}
    for match in _ATTR_RE.finditer(raw):
        key = match.group(1)
        value = match.group(2) if match.group(2) is not None else match.group(3)
        attrs[key] = value
    return attrs


def extract(
    text: str,
    names: tuple[str, ...] = _TAG_NAMES,
) -> list[ParsedAction]:
    """text 에서 등록된 action 태그를 순서대로 추출한다."""
    if not text:
        return []
    pattern = _compile_tag_re(names)
    results: list[ParsedAction] = []
    for match in pattern.finditer(text):
        payload: dict[str, Any] = dict(_parse_attrs(match.group("attrs")))
        body = match.groupdict().get("body")
        if body is not None:
            stripped = body.strip()
            if stripped:
                payload["_body"] = stripped
        results.append(
            ParsedAction(
                name=match.group("name"),
                payload=payload,
                raw=match.group(0),
                start=match.start(),
                end=match.end(),
            )
        )
    return results
