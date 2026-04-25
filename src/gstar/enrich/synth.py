"""Web 검색 결과 → Fact 추출.

프롬프트 원칙:
- 기존 under-connected 노드 (target_node_text) 와 관계 있는 문장만 추출
- 구체 수치·고유명사·사실 중심
- 서술형 1-3 문장 (fact node 의 text)

env:
- `ENRICH_OLLAMA_HOST` 또는 직접 인자 → 엔드포인트 host
- `ENRICH_OLLAMA_MODEL` 또는 직접 인자 → 모델 이름
- `ENRICH_LLM_API` (`ollama`|`openai`) 또는 `GP_LLM_API` 계승 → schema 선택
  `openai` 면 `/v1/chat/completions` (vllm-mlx, llama.cpp, OpenAI-compat).
"""

from __future__ import annotations

import asyncio
import os
from typing import Any


def _enrich_api_schema() -> str:
    v = (os.environ.get("ENRICH_LLM_API") or os.environ.get("GP_LLM_API", "ollama")).lower()
    return "openai" if v in {"openai", "llamacpp", "llama.cpp", "v1"} else "ollama"


async def synthesize_from_result(
    result: dict[str, Any],
    *,
    target_node_text: str,
    target_node_kind: str = "fact",
    ollama_host: str | None = None,
    ollama_model: str | None = None,
) -> str:
    host = ollama_host or os.environ.get("ENRICH_OLLAMA_HOST", "http://localhost:11434")
    model = ollama_model or os.environ.get("ENRICH_OLLAMA_MODEL", "gemma4:26b-a4b-it-q4_K_M")
    content = result.get("content") or result.get("snippet") or ""
    if not content.strip():
        return ""

    system = (
        "당신은 검색 결과에서 타겟 엔티티와 관련된 사실만 1~2 문장으로 요약한다. "
        "구체 수치·연도·고유명사 보존. 의견·추측·서술 금지. 본문만 출력."
    )
    prompt = (
        f"타겟 엔티티: {target_node_text}\n"
        f"타입: {target_node_kind}\n"
        f"검색 결과 본문:\n{content[:2000]}\n\n"
        f"요약 (1-2 문장, 타겟과 관련된 사실만):"
    )

    api_schema = _enrich_api_schema()
    use_openai = api_schema == "openai"

    try:
        import httpx

        if use_openai:
            body = {
                "model": model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 256,
                "temperature": 0.2,
            }
            url = f"{host}/v1/chat/completions"
        else:
            body = {
                "model": model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "options": {"num_predict": 256, "temperature": 0.2},
            }
            url = f"{host}/api/chat"

        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(url, json=body)
            if r.status_code != 200:
                return _fallback_extract(content, target_node_text)
            data = r.json()
            if use_openai:
                try:
                    msg = (data["choices"][0]["message"]["content"] or "").strip()
                except Exception:
                    msg = ""
            else:
                msg = data.get("message", {}).get("content", "").strip()
            if msg:
                return msg
            return _fallback_extract(content, target_node_text)
    except Exception:
        return _fallback_extract(content, target_node_text)


def _fallback_extract(content: str, target: str) -> str:
    """Ollama 실패 시 간단 문장 추출."""
    sentences = [s.strip() for s in content.replace("\n", " ").split(".") if s.strip()]
    for s in sentences:
        if target.lower() in s.lower() and 20 < len(s) < 400:
            return s
    return sentences[0][:300] if sentences else ""
