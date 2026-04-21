"""Ollama /api/chat 래퍼.

아키텍처:
- **작성 주체 (생성기)**: 4090 서버 (http://100.105.221.243:11434, 모델 `gemma4:a4b` 등).
  P축 projection 의 summarizer·renderer·coherence_gate 에서 호출.
- **선택기 (짧은 예/아니오)**: 로컬 또는 4090. Selector loop 의 judge.
- **G 운영 주체**: 미니 PC (http://100.79.251.53:9999, g-serve HTTP).

기본값은 환경변수로 override 가능:
- `OLLAMA_HOST`      — 전체 기본 Ollama 호스트
- `GP_OLLAMA_HOST`   — projection 전용 override (작성 주체 = 4090)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

import httpx


_DEFAULT_OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
_PROJECTION_OLLAMA_HOST = os.environ.get("GP_OLLAMA_HOST", "http://100.105.221.243:11434")


def projection_host() -> str:
    """Projection(생성) 전용 Ollama 엔드포인트. 기본: 4090."""
    return os.environ.get("GP_OLLAMA_HOST", _PROJECTION_OLLAMA_HOST)


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

    def judge(self, *, system: str, prompt: str) -> str:
        """짧은 응답을 끌어내는 chat 호출. 실패 시 HTTPError 전파.

        `think=false` 기본: Gemma 4 thinking 모델은 default 가 thinking on 이라
        `message.content` 가 비어있고 `message.thinking` 에만 답이 쓰인다. Projector/
        Selector 모두 최종 출력만 필요하므로 thinking 을 끈다. `GP_THINK=on` env 로
        재활성화 가능 (디버그·복잡 추론 필요 시).
        """
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
