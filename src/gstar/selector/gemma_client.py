"""Ollama /api/chat + llama.cpp /v1/chat/completions (OpenAI-compatible) 통합 래퍼.

아키텍처:
- **작성 주체 (생성기)**: 4090 서버 Ollama :11434 또는 llama.cpp :8081 (Qwen 14B Q5).
- **선택기 (짧은 예/아니오)**: 로컬 또는 4090 :8082 llama.cpp (Gemma 3 4B Q4).
- **G 운영 주체**: 미니 PC (http://100.79.251.53:9999, g-serve HTTP).

env:
- `OLLAMA_HOST`        전체 기본 Ollama 호스트
- `GP_OLLAMA_HOST`     projection 전용 override (Projector host)
- `GP_LLM_API`         `ollama` (기본) | `openai` — llama.cpp server 쓰면 `openai`
- `GP_SELECTOR_HOST`   selector 전용 host (llama.cpp :8082 등). 없으면 OLLAMA_HOST
- `GP_SELECTOR_API`    selector 만 별도 API 스키마. 없으면 GP_LLM_API
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Protocol

import httpx


_DEFAULT_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
_PROJECTION_OLLAMA_HOST = os.environ.get("GP_OLLAMA_HOST", "http://100.105.221.243:11434")


def projection_host() -> str:
    """Projection(생성) 전용 엔드포인트. 기본: 4090 Ollama. llama.cpp 로 전환
    시 `GP_OLLAMA_HOST=http://100.105.221.243:8081` + `GP_LLM_API=openai`."""
    return os.environ.get("GP_OLLAMA_HOST", _PROJECTION_OLLAMA_HOST)


def _default_api_schema() -> str:
    v = os.environ.get("GP_LLM_API", "ollama").lower()
    return "openai" if v in {"openai", "llamacpp", "llama.cpp", "v1"} else "ollama"


class Judge(Protocol):
    """경계 판정기. 테스트에서 mock 주입."""

    def judge(self, *, system: str, prompt: str) -> str: ...


@dataclass
class OllamaChatClient:
    host: str = _DEFAULT_OLLAMA_HOST
    model: str = "gemma4:e4b"
    timeout_s: float = 30.0
    num_predict: int = 64
    temperature: float = 0.1
    api_schema: str = field(default_factory=_default_api_schema)  # "ollama" | "openai"

    def judge(self, *, system: str, prompt: str) -> str:
        """짧은 응답을 끌어내는 chat 호출. 실패 시 HTTPError 전파.

        api_schema=openai 는 llama.cpp server(`/v1/chat/completions`) 또는 OpenAI
        호환 endpoint 호출. `num_predict` 는 `max_tokens`, `temperature` 그대로 전달.
        Ollama 의 `think` 는 OpenAI 스키마에 없어서 무시.
        """
        if self.api_schema == "openai":
            body = {
                "model": self.model,
                "stream": False,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": self.num_predict,
                "temperature": self.temperature,
            }
            r = httpx.post(
                f"{self.host}/v1/chat/completions", json=body, timeout=self.timeout_s,
            )
            r.raise_for_status()
            data = r.json()
            try:
                return (data["choices"][0]["message"]["content"] or "").strip()
            except Exception:
                return ""

        # Ollama /api/chat (기본)
        think = os.environ.get("GP_THINK", "off").lower() in {"on", "1", "true"}
        body = {
            "model": self.model,
            "stream": False,
            "think": think,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "options": {
                "num_predict": self.num_predict,
                "temperature": self.temperature,
            },
        }
        r = httpx.post(f"{self.host}/api/chat", json=body, timeout=self.timeout_s)
        r.raise_for_status()
        data = r.json()
        msg = data.get("message", {}).get("content", "")
        # fallback: 구버전 Ollama·모델이 think=false 를 무시해 content 가 비면
        # thinking 을 fallback 으로 (완벽하진 않지만 완전 빈 응답보단 낫다).
        if not msg.strip():
            msg = data.get("message", {}).get("thinking", "") or msg
        return msg.strip()


def parse_yesno(text: str) -> bool | None:
    """Gemma 응답에서 예/아니오 추출. 명확하지 않으면 None."""
    t = text.strip().lower()
    if not t:
        return None
    # 한국어 우선
    if t.startswith("예") or t.startswith("네") or t.startswith("맞"):
        return True
    if t.startswith("아니") or t.startswith("no") or t.startswith("n "):
        return False
    if t.startswith("yes") or t.startswith("y ") or t == "y":
        return True
    if t.startswith("no") or t == "n":
        return False
    return None
