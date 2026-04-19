"""Phase E1 — Selector loop 정식 (맥북 Gemma4 E4B 주역).

입력: goal(text), top_k_final
동작:
  1. G `/search/fused` 호출 → 초기 후보 ≤ candidate_k
  2. E4B 로 병렬 judge: 각 후보 (goal-relevant / irrelevant / neutral)
  3. 관련 후보만 누적. 최대 max_iter 반복.
  4. 수렴 기준: relevant 수 <= top_k_final AND 연속 2 iter 변화율 < 10%

기존 `src/gstar/selector/selection_loop.py` 의 gravity-기반 경계 판정을 **fused
검색 결과**로 확장. 하위 RAG (Gateway) 결과도 seed 에 포함.
"""

from __future__ import annotations

import concurrent.futures
import os
from dataclasses import dataclass, field
from typing import Any

from gstar.selector.gemma_client import OllamaChatClient, parse_yesno


def selector_host() -> str:
    """Selector 전용 Ollama 호스트. env `SELECTOR_OLLAMA_HOST` → OLLAMA_HOST → localhost."""
    return os.environ.get(
        "SELECTOR_OLLAMA_HOST",
        os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
    )


_SYSTEM = (
    "너는 Selector 이다. 주어진 지식 조각이 현재 목표에 '관련' 인지만 판정한다. "
    "답변은 반드시 '관련' 또는 '무관' 한 단어로만. 애매하면 '무관'."
)


@dataclass
class SelectorIteration:
    iter: int
    candidates: int
    relevant: int
    dropped: int


@dataclass
class SelectorResult:
    goal: str
    final_facts: list[dict]                  # kept 지식 항성
    iterations: list[SelectorIteration] = field(default_factory=list)
    converged: bool = False


def _judge_one(
    client: OllamaChatClient, goal: str, fact_text: str
) -> bool:
    try:
        out = client.judge(
            system=_SYSTEM,
            prompt=f"[목표] {goal}\n[조각]\n{fact_text[:600]}\n\n답:",
        )
    except Exception:
        return False
    verdict = parse_yesno(out)
    if verdict is True:
        return True
    if verdict is False:
        return False
    # Gemma 가 "관련"/"무관" 한국어로 답변
    t = out.strip().lower()
    return t.startswith("관련")


def _iterate_judges(
    goal: str,
    candidates: list[dict],
    client: OllamaChatClient,
    workers: int = 4,
) -> tuple[list[dict], list[dict]]:
    """병렬 judge. (relevant, dropped) 반환."""
    relevant: list[dict] = []
    dropped: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {
            ex.submit(_judge_one, client, goal, c.get("text") or ""): c
            for c in candidates
        }
        for fut in concurrent.futures.as_completed(futs):
            c = futs[fut]
            try:
                keep = fut.result()
            except Exception:
                keep = False
            (relevant if keep else dropped).append(c)
    return relevant, dropped


def run_selector(
    goal: str,
    *,
    gclient,
    top_k_final: int = 20,
    candidate_k: int = 80,
    max_iter: int = 5,
    workers: int = 4,
    selector_model: str | None = None,
    host: str | None = None,
    use_gateway: bool = True,
    namespace: str | None = None,
) -> SelectorResult:
    """G /search/fused 로 후보 수집 → E4B judge 반복 → 지식 항성 리턴."""
    model = selector_model or os.environ.get("SELECTOR_MODEL", "gemma4:e4b")
    h = host or selector_host()
    client = OllamaChatClient(
        host=h,
        model=model,
        timeout_s=30.0,
        num_predict=16,
        temperature=0.1,
    )

    # 초기 후보 수집 — fused (G + Gateway)
    hits = gclient.fused_search(
        goal,
        top_k=candidate_k,
        use_gateway=use_gateway,
        use_g=True,
        namespace=namespace,
    )
    candidates: list[dict] = []
    seen_texts: set[str] = set()
    for h_ in hits:
        txt = (h_.get("text") or "").strip()
        if not txt:
            continue
        key = txt[:150]
        if key in seen_texts:
            continue
        seen_texts.add(key)
        candidates.append(
            {
                "node_id": h_.get("node_id") or "",
                "text": txt,
                "score": h_.get("score") or 0.0,
                "origin": h_.get("origin") or "",
                "namespace": h_.get("namespace") or "",
            }
        )

    result = SelectorResult(goal=goal, final_facts=list(candidates))
    prev_count: int = len(candidates)

    for iter_i in range(1, max_iter + 1):
        if not candidates:
            break
        relevant, dropped = _iterate_judges(goal, candidates, client, workers=workers)
        result.iterations.append(
            SelectorIteration(
                iter=iter_i,
                candidates=len(candidates),
                relevant=len(relevant),
                dropped=len(dropped),
            )
        )
        # 수렴 기준
        if len(relevant) <= top_k_final:
            candidates = relevant
            break
        change = abs(prev_count - len(relevant)) / max(prev_count, 1)
        if change < 0.10:
            result.converged = True
            candidates = relevant[:top_k_final]
            break
        candidates = relevant
        prev_count = len(relevant)

    result.final_facts = candidates[:top_k_final]
    return result
