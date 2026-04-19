"""Gemma4 + WebSearch/WebFetch — Claude 의 tool-use 루프를 4090 Gemma4 에 재현.

Claude 가 자기 LLM 내부에서 WebSearch/WebFetch 를 호출하듯,
Gemma4 도 시스템 프롬프트에 정의된 tool 을 JSON 형태로 호출 → 우리가 실행 →
결과 re-inject → Gemma4 가 최종 답 생성.

철학:
- Gemma4 는 Claude 만큼 structured tool use 가 안정적이지 않음 → 엄격한 JSON
  포맷 + 재파싱 + 폴백 전략 필요
- 호출 실패 시 tool 없이 단순 응답으로 우회 (graceful degradation)
- max_iterations 제한으로 무한 루프 차단
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any


_TOOL_DEFINITIONS = {
    "web_search": {
        "description": (
            "외부 웹을 검색한다. 최신 정보·통계·수치·출처가 필요할 때 호출. "
            "한국어/영어 쿼리 가능."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색 쿼리"},
                "top_k": {"type": "integer", "default": 3},
            },
            "required": ["query"],
        },
    },
    "web_fetch": {
        "description": (
            "URL 의 본문 텍스트를 가져온다. web_search 결과 중 상세 내용이 필요한 "
            "페이지를 fetch 할 때 사용."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "max_chars": {"type": "integer", "default": 10000},
            },
            "required": ["url"],
        },
    },
}


_SYSTEM_PROMPT_KO = """당신은 전문 문서 작성 어시스턴트다. 외부 정보·통계·수치·출처가 필요하면 아래 tool 을 호출하라.

## 사용 가능한 tool
- web_search(query, top_k=3): 웹 검색
- web_fetch(url, max_chars=10000): URL 본문 추출

## 호출 형식 (엄격 준수)
tool 이 필요하면 **오직 다음 JSON 한 줄** 만 출력하고 설명은 금지:
```
{"tool": "web_search", "args": {"query": "외식업 폐업률 2024"}}
```
또는
```
{"tool": "web_fetch", "args": {"url": "https://..."}}
```

tool 이 필요없으면 평문으로 최종 답 작성 (JSON 금지).
tool 결과를 보면 이어서 추가 tool 을 부르거나 최종 답을 작성한다.
"""


@dataclass
class ToolCall:
    tool: str
    args: dict[str, Any]


@dataclass
class AgentStep:
    role: str            # "user" | "assistant" | "tool_result"
    content: str
    tool: str = ""


@dataclass
class AgentRun:
    task: str
    steps: list[AgentStep] = field(default_factory=list)
    final_answer: str = ""
    iterations: int = 0
    errors: list[str] = field(default_factory=list)


_TOOL_CALL_RE = re.compile(r"\{[^{}]*\"tool\"\s*:\s*\"[^\"]+\"[^{}]*\"args\"\s*:\s*\{[^{}]*\}[^{}]*\}", re.DOTALL)


def _parse_tool_call(text: str) -> ToolCall | None:
    """응답 텍스트에서 tool_call JSON 추출 (앞뒤 문자 포함 허용)."""
    text = text.strip()
    m = _TOOL_CALL_RE.search(text)
    if not m:
        if text.startswith("{") and "\"tool\"" in text:
            candidate = text
        else:
            return None
    else:
        candidate = m.group(0)
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    tool = data.get("tool")
    args = data.get("args", {})
    if not isinstance(tool, str) or tool not in _TOOL_DEFINITIONS:
        return None
    if not isinstance(args, dict):
        args = {}
    return ToolCall(tool=tool, args=args)


async def _execute_tool(call: ToolCall) -> str:
    """tool 실제 실행. 결과를 LLM 에게 돌려줄 텍스트로 직렬화."""
    from gstar.enrich.self_made import search_and_fetch, self_made_search
    from gstar.enrich.web_search import web_fetch

    try:
        if call.tool == "web_search":
            q = str(call.args.get("query", ""))[:500]
            top_k = int(call.args.get("top_k", 3))
            if not q:
                return "[web_search error] query 비어있음"
            hits = await self_made_search(q, top_k=top_k)
            if not hits:
                return f"[web_search] '{q}' 결과 0건"
            lines = [f"[web_search] '{q}' top_{len(hits)}"]
            for i, h in enumerate(hits):
                lines.append(f"{i+1}. {h.title[:120]}")
                lines.append(f"   URL: {h.url}")
                if h.snippet:
                    lines.append(f"   snippet: {h.snippet[:300]}")
            return "\n".join(lines)
        if call.tool == "web_fetch":
            url = str(call.args.get("url", ""))
            max_chars = int(call.args.get("max_chars", 10000))
            if not url.startswith("http"):
                return "[web_fetch error] invalid url"
            body = await web_fetch(url, max_chars=max_chars)
            if not body:
                return f"[web_fetch] '{url}' 본문 추출 실패"
            return f"[web_fetch] {url}\n---\n{body}"
    except Exception as e:
        return f"[{call.tool} error] {e}"
    return f"[unknown tool {call.tool}]"


async def run_agent(
    task: str,
    *,
    model: str | None = None,
    host: str | None = None,
    max_iterations: int = 5,
    system_prompt: str | None = None,
    user_context: str = "",
) -> AgentRun:
    """Gemma4 + tools 루프. Claude tool-use 와 동등 인터페이스.

    task: 최종적으로 답해야 할 목표 (섹션 guide 또는 질문).
    user_context: 추가 참고 자료 (선택).
    """
    try:
        from gstar.selector.gemma_client import OllamaChatClient, projection_host
    except Exception as e:
        return AgentRun(task=task, errors=[f"ollama import: {e}"])

    effective_host = host or projection_host()
    effective_model = model or os.environ.get("GP_OLLAMA_MODEL", "gemma4:26b-a4b-it-q4_K_M")
    sys_prompt = system_prompt or _SYSTEM_PROMPT_KO

    run = AgentRun(task=task)
    run.steps.append(AgentStep(role="user", content=task + (f"\n\n컨텍스트:\n{user_context}" if user_context else "")))

    import httpx

    messages: list[dict[str, str]] = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": run.steps[0].content},
    ]

    for i in range(max_iterations):
        run.iterations += 1
        try:
            async with httpx.AsyncClient(timeout=180.0) as client:
                r = await client.post(
                    f"{effective_host}/api/chat",
                    json={
                        "model": effective_model,
                        "stream": False,
                        "messages": messages,
                        "options": {"num_predict": 3000, "temperature": 0.2},
                    },
                )
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            run.errors.append(f"iter {i}: {e}")
            break

        assistant_text = (data.get("message", {}).get("content") or "").strip()
        run.steps.append(AgentStep(role="assistant", content=assistant_text))
        messages.append({"role": "assistant", "content": assistant_text})

        call = _parse_tool_call(assistant_text)
        if call is None:
            run.final_answer = assistant_text
            break

        tool_result = await _execute_tool(call)
        run.steps.append(AgentStep(role="tool_result", content=tool_result, tool=call.tool))
        messages.append({
            "role": "user",
            "content": f"<tool_result tool=\"{call.tool}\">\n{tool_result}\n</tool_result>\n\n위 결과를 반영해 이어 진행.",
        })
    else:
        run.final_answer = run.steps[-1].content if run.steps else ""
        run.errors.append(f"max_iterations({max_iterations}) reached")

    return run


def agent_tool_count(run: AgentRun, tool: str = "") -> int:
    if not tool:
        return sum(1 for s in run.steps if s.role == "tool_result")
    return sum(1 for s in run.steps if s.role == "tool_result" and s.tool == tool)
