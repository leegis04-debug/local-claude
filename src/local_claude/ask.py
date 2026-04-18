"""lcai ask — Gemma 범용 Q&A 래퍼.

- 기본: 맥북 e4b (빠름, ~2s)
- `--deep`: 4090 a4b (깊은 추론, ~30-60s cold)
- `--rag`: gateway `/search/hybrid` 결과를 prompt 앞에 prepend

내부는 `~/.local/bin/ask-gemma` subprocess 를 직접 호출한다 —
그게 이미 맥북/4090 분기, ollama health, think 옵션 등을 처리하므로
여기서 재구현하지 않는다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from . import config
from .infra.gateway import Gateway


@dataclass
class AskResult:
    ok: bool
    text: str = ""
    model: str = ""
    error: str | None = None
    rag_evidence: list[dict[str, Any]] | None = None
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "text": self.text,
            "model": self.model,
            "error": self.error,
            "rag_evidence": self.rag_evidence,
            "duration_ms": self.duration_ms,
        }


def _format_rag_block(hits: list[dict[str, Any]], limit: int = 5) -> str:
    """검색 결과 상위 N 개를 프롬프트 앞에 붙일 [근거] 블록으로 변환."""
    lines = ["[근거]"]
    for i, hit in enumerate(hits[:limit]):
        # 다양한 스키마에 대응
        text = ""
        for key in ("text", "content", "snippet", "passage"):
            if isinstance(hit.get(key), str):
                text = hit[key]
                break
        if not text:
            text = json.dumps(hit, ensure_ascii=False)[:400]
        src = hit.get("source") or hit.get("src") or ""
        prefix = f"[{i + 1}]" + (f" ({src})" if src else "")
        lines.append(f"{prefix} {text[:400]}")
    lines.append("")
    return "\n".join(lines)


def ask(
    prompt: str,
    *,
    deep: bool = False,
    rag: bool = False,
    rag_top_k: int = 5,
    timeout_s: int = 180,
) -> AskResult:
    import time

    if not prompt or not prompt.strip():
        return AskResult(ok=False, error="prompt 가 비어있음")

    evidence: list[dict[str, Any]] | None = None
    final_prompt = prompt

    if rag:
        try:
            resp = Gateway().search_hybrid(prompt, top_k=rag_top_k)
        except Exception as exc:
            resp = None
            rag_error = f"gateway 실패: {exc}"
        else:
            rag_error = None
        if resp is not None and resp.ok and isinstance(resp.data, dict):
            hits = resp.data.get("hits") or resp.data.get("results") or []
            if isinstance(hits, list) and hits:
                evidence = hits
                final_prompt = _format_rag_block(hits, limit=rag_top_k) + "\n" + prompt
        # rag 실패여도 질의 자체는 진행 — 사용자가 알 수 있게 evidence=None 로 전달.

    bin_path = shutil.which(config.ASK_GEMMA_BIN) or config.ASK_GEMMA_BIN
    cmd = [bin_path]
    if deep:
        cmd.append("--deep")

    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            input=final_prompt,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError:
        return AskResult(
            ok=False, error=f"ask-gemma 를 찾을 수 없음: {bin_path}",
            rag_evidence=evidence,
        )
    except subprocess.TimeoutExpired:
        return AskResult(
            ok=False, error=f"ask-gemma 타임아웃 ({timeout_s}s)",
            rag_evidence=evidence,
        )
    duration_ms = int((time.monotonic() - start) * 1000)

    if result.returncode != 0:
        return AskResult(
            ok=False,
            error=f"ask-gemma exit={result.returncode}: {result.stderr[-300:]}",
            rag_evidence=evidence,
            duration_ms=duration_ms,
        )
    return AskResult(
        ok=True,
        text=(result.stdout or "").strip(),
        model="gemma4:a4b" if deep else "gemma4:e4b",
        rag_evidence=evidence,
        duration_ms=duration_ms,
    )
