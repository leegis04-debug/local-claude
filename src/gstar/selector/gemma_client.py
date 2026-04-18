"""Ollama /api/chat 저토큰 래퍼.

D 관점: Gemma 는 생성기가 아니라 "선택기". 긴 서술 대신 예/아니오·점수·ID만 뽑는다.
기본 모델: 맥북 e4b (http://localhost:11434). 네트워크 실패 시 명확한 예외.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx


class Judge(Protocol):
    """경계 판정기. 테스트에서 mock 주입."""

    def judge(self, *, system: str, prompt: str) -> str: ...


@dataclass
class OllamaChatClient:
    host: str = "http://localhost:11434"
    model: str = "gemma4:e4b"
    timeout_s: float = 30.0
    num_predict: int = 64
    temperature: float = 0.1

    def judge(self, *, system: str, prompt: str) -> str:
        """짧은 응답을 끌어내는 chat 호출. 실패 시 HTTPError 전파."""
        body = {
            "model": self.model,
            "stream": False,
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
