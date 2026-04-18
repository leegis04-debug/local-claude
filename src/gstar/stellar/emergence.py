"""3연결 안정 / 4연결 창발 감지.

3개까지는 최소 단위(원자). 4번째 연결이 생기는 순간 그 노드는 더 이상 원자가 아니라
**상위 지식의 씨앗**이라는 G 모델의 핵심 규칙을 구현한다.

구현 전략:
- 주어진 goal 범위에서 각 노드의 degree 를 계산
- degree ≥ 4 인 노드를 창발 후보로 식별
- 이미 같은 연결 집합으로 기록된 이벤트가 있으면 스킵 (중복 방지)
- 새 이벤트는 DuckStore.insert_emergence 로 append
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from gstar.schema import EmergenceEvent, Goal
from gstar.storage.duckdb_store import DuckStore


@dataclass
class ScanResult:
    detected: int                    # 새로 기록된 이벤트 수
    already_logged: int              # 이미 있던 이벤트 수
    under_threshold: int             # degree < 4 인 후보


def scan_emergence(
    goal: Goal,
    candidate_node_ids: list[str],
    store: DuckStore,
    *,
    threshold: int = 4,
) -> ScanResult:
    """후보 노드들의 degree 를 검사해 threshold 이상이면 emergence_event 생성."""

    existing = store.emergence_for_goal(goal.id)
    seen_keys = {
        (ev.trigger_node_id, tuple(sorted(ev.connected_node_ids)))
        for ev in existing
    }

    detected = 0
    already = 0
    under = 0
    for nid in candidate_node_ids:
        edges = store.edges_of(nid)
        # 이웃 집합 (양방향 엣지 고려)
        neighbors = sorted({(e.dst if e.src == nid else e.src) for e in edges})
        if len(neighbors) < threshold:
            under += 1
            continue
        key = (nid, tuple(neighbors))
        if key in seen_keys:
            already += 1
            continue

        # 창발 서술 — LLM 없이 트리거 노드 텍스트 + 이웃 요약으로 간단 생성
        trig = store.get_node(nid)
        trig_text = (trig.text[:60] if trig else nid)
        nb_texts: list[str] = []
        for nb in neighbors[:threshold]:
            n = store.get_node(nb)
            nb_texts.append(n.text[:40] if n else nb)
        candidate_desc = (
            f"[창발] {trig_text} ← "
            + " | ".join(nb_texts)
        )

        ev = EmergenceEvent(
            goal_id=goal.id,
            trigger_node_id=nid,
            connected_node_ids=neighbors,
            new_node_candidate=candidate_desc,
        )
        store.insert_emergence(ev)
        detected += 1

    return ScanResult(
        detected=detected, already_logged=already, under_threshold=under
    )
