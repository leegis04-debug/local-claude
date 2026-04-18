"""저토큰 고반복 선택 루프.

D 관점 구현. 단계:
 1. 중력 top-N 후보를 seed pool 로 수집
 2. 각 사이클마다 Gemma 에게 "이 노드가 목표에 유효한가? 예/아니오" 경계 판정
 3. kept/dropped 를 selection_log 에 기록 → repeat_selection_rate 가중치가 다음 사이클에 반영
 4. 연속 stable_window 사이클 동안 kept 집합이 안정되면 종료
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from gstar.config import Weights
from gstar.gravity.field import GravityEntry, compute_gravity, get_goal_embedding
from gstar.schema import Goal
from gstar.selector.gemma_client import Judge, parse_yesno
from gstar.storage.duckdb_store import DuckStore
from gstar.storage.faiss_index import FaissStore


_SYSTEM_PROMPT = (
    "너는 사업계획서·코드·연구 지식을 정리하는 보조 시스템이다. "
    "주어지는 지식 조각이 현재 목표에 필요한지 '예' 또는 '아니오' 한 단어로만 답한다. "
    "모호하면 '아니오'."
)


@dataclass
class CycleResult:
    cycle: int
    candidates: int
    kept: list[str]
    dropped: list[str]
    undecided: list[str]


@dataclass
class RunResult:
    goal_id: str
    final_kept: list[str]
    cycles: list[CycleResult] = field(default_factory=list)
    converged: bool = False


def run_selection(
    goal: Goal,
    store: DuckStore,
    faiss: FaissStore,
    weights: Weights,
    judge: Judge,
    *,
    top_k: int = 30,
    max_cycles: int = 6,
    stable_window: int = 2,
) -> RunResult:
    """사이클별로 Gemma 경계 판정 → 안정 시 종료."""

    goal_emb = get_goal_embedding(faiss, goal.id)
    if goal_emb is None:
        raise ValueError(f"goal embedding not found for {goal.id}")

    recent_kept: list[set[str]] = []
    result = RunResult(goal_id=goal.id, final_kept=[])

    for cycle in range(1, max_cycles + 1):
        entries = compute_gravity(goal, goal_emb, store, faiss, weights)
        top = entries[:top_k]
        cr = _judge_cycle(goal, top, store, judge, cycle)
        result.cycles.append(cr)

        # selection_log 에 기록 → 다음 사이클 repeat_selection_rate 가중치에 반영
        kept_set = set(cr.kept)
        log_rows = [(nid, next(e.total for e in top if e.node_id == nid), nid in kept_set)
                    for nid in [e.node_id for e in top]]
        store.log_selection(goal.id, cycle, log_rows)

        recent_kept.append(kept_set)
        if len(recent_kept) >= stable_window:
            window = recent_kept[-stable_window:]
            if all(s == window[0] for s in window) and window[0]:
                result.final_kept = sorted(kept_set)
                result.converged = True
                return result

    # max_cycles 도달. 마지막 사이클 결과 사용
    result.final_kept = sorted(recent_kept[-1]) if recent_kept else []
    return result


def _judge_cycle(
    goal: Goal,
    entries: Iterable[GravityEntry],
    store: DuckStore,
    judge: Judge,
    cycle: int,
) -> CycleResult:
    kept: list[str] = []
    dropped: list[str] = []
    undecided: list[str] = []
    count = 0
    for e in entries:
        node = store.get_node(e.node_id)
        if node is None or node.kind == "goal":
            continue
        count += 1
        prompt = _build_prompt(goal, node.text[:240])
        raw = judge.judge(system=_SYSTEM_PROMPT, prompt=prompt)
        decision = parse_yesno(raw)
        if decision is True:
            kept.append(e.node_id)
        elif decision is False:
            dropped.append(e.node_id)
        else:
            undecided.append(e.node_id)
    return CycleResult(
        cycle=cycle,
        candidates=count,
        kept=kept,
        dropped=dropped,
        undecided=undecided,
    )


def _build_prompt(goal: Goal, node_text: str) -> str:
    return (
        f"목표: {goal.text}\n"
        f"유형: {goal.kind}\n"
        f"지식 조각: {node_text}\n"
        f"이 조각이 목표에 필요한가? '예' 또는 '아니오'로만 답하라."
    )
