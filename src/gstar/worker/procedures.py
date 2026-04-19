"""Phase G6 — worker tick 내에서 trace → procedure 패턴 추출.

로직 (MVP, 단순 명확):
  1. 최근 `recent_days` 안의 closed bracket (close phase 존재하는 task) 집계
  2. task_id 별로 trace 를 created_at 순으로 모아 phase 시퀀스 생성
  3. 시퀀스 요약 텍스트 + open phase description 을 text 로 Node(kind='procedure') 저장
  4. 멱등성: task_id 당 procedure 1개 (이미 있으면 skip)

procedure 노드는 Phase G7 selector_loop 가 /trace/patterns 로 retrieval → system prompt 에 주입.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from gstar.schema import Node


@dataclass
class MinerResult:
    tasks_scanned: int = 0
    procedures_created: int = 0
    procedures_skipped_existing: int = 0
    errors: int = 0


def _task_sequences(store, *, since: datetime, min_steps: int = 2) -> list[dict]:
    """closed bracket 가 있는 task 들의 trace 시퀀스 추출."""
    with store.lock:
        rows = store.conn.execute(
            "SELECT task_id, bracket_phase, description, created_at "
            "FROM claude_trace WHERE task_id IS NOT NULL AND created_at >= ? "
            "ORDER BY task_id, created_at",
            [since],
        ).fetchall()

    by_task: dict[str, list[dict]] = {}
    for r in rows:
        by_task.setdefault(r[0], []).append(
            {"phase": r[1], "description": r[2] or "", "created_at": r[3]}
        )

    out: list[dict] = []
    for tid, steps in by_task.items():
        if len(steps) < min_steps:
            continue
        phases = [s["phase"] for s in steps]
        # close phase 존재 OR session phase 존재 → 완결된 task 만
        if "close" not in phases and "session" not in phases:
            continue
        # open goal (description of first open/session/step)
        opening = next(
            (s for s in steps if s["phase"] in ("open", "session")),
            steps[0],
        )
        out.append(
            {
                "task_id": tid,
                "goal": opening["description"],
                "steps": steps,
                "phase_chain": phases,
            }
        )
    return out


def _format_procedure_text(task: dict) -> str:
    """시퀀스를 1문장 요약으로 포맷."""
    goal = task["goal"][:120]
    chain = " → ".join(task["phase_chain"])
    # 주요 step description (decision/tool_use 우선) 3개까지
    highlights = []
    for s in task["steps"]:
        if s["phase"] in ("decision", "tool_use"):
            highlights.append(f"[{s['phase']}] {s['description'][:80]}")
        if len(highlights) >= 3:
            break
    body = "\n".join(highlights) if highlights else ""
    return f"목표: {goal}\n시퀀스: {chain}\n" + (f"핵심:\n{body}" if body else "")


def mine_procedures(
    store,
    *,
    embedder=None,
    faiss=None,
    recent_days: int = 30,
    namespace: str = "claude_traces",
) -> MinerResult:
    """worker tick 에서 호출. trace → procedure 노드 추출."""
    res = MinerResult()
    since = datetime.now(timezone.utc) - timedelta(days=recent_days)
    try:
        tasks = _task_sequences(store, since=since)
    except Exception:
        res.errors += 1
        return res
    res.tasks_scanned = len(tasks)

    # 기존 procedure 노드 중 task_id attrs 이 있는 것 검사 (멱등성)
    with store.lock:
        try:
            existing = store.conn.execute(
                "SELECT attrs_json FROM node WHERE kind='procedure' AND source_namespace=?",
                [namespace],
            ).fetchall()
        except Exception:
            existing = []
    existing_tids: set[str] = set()
    for r in existing:
        try:
            a = json.loads(r[0]) if r[0] else {}
            tid = a.get("task_id")
            if tid:
                existing_tids.add(tid)
        except Exception:
            continue

    texts_to_embed: list[str] = []
    ids_to_embed: list[str] = []

    for task in tasks:
        tid = task["task_id"]
        if tid in existing_tids:
            res.procedures_skipped_existing += 1
            continue
        text = _format_procedure_text(task)
        node = Node(
            kind="procedure",
            text=text,
            attrs={
                "task_id": tid,
                "phase_chain": task["phase_chain"],
                "step_count": len(task["steps"]),
            },
            source_namespace=namespace,
        )
        try:
            store.insert_node(node)
            texts_to_embed.append(text)
            ids_to_embed.append(node.id)
            res.procedures_created += 1
        except Exception:
            res.errors += 1

    # FAISS indexing (Selector retrieval 용)
    if texts_to_embed and embedder is not None and faiss is not None:
        try:
            vecs = embedder.encode(texts_to_embed)
            faiss.add_batch(ids_to_embed, vecs)
            faiss.save()
        except Exception:
            res.errors += 1

    return res
