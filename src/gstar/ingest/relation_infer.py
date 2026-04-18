"""엔티티 간 관계 추론 (typed relations 포함).

Phase A 확장:
- 기본 co_occurs / evidence_of (기존 동작 유지)
- 타입 쌍별 패턴 추론: MEASURES / PARTICIPATES_IN / BUDGETS_FOR / SCHEDULES /
  TESTS / USES / YIELDS / CALLS / IMPORTS / TESTED_BY / DEFINES / CITES / SUPPORTS

LLM 없이 패턴 기반 1차. Phase B coherence_gate 가 후차 검증.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from itertools import combinations


@dataclass
class InferredEdge:
    src: str
    dst: str
    kind: str                 # 기존 edge.kind (하위 호환)
    weight: float
    evidence_ids: list[str]
    relation_type: str | None = None   # v3: typed relation


@dataclass
class EntityTypeInfo:
    entity_id: str
    kind: str                 # EntityKind.value


_MEASURES_PAT = re.compile(r"(AP@?[\d.]*|mAP|F1|BLEU|ROUGE|정확도|정밀도|재현율|IoU|Top-?[1-9]).{0,50}[:=].{0,20}\d", re.IGNORECASE)
_BUDGET_PAT = re.compile(r"(\d+(억|천만|백만|만|천)?원|\$\d+|USD\s?\d+|예산|총\s?사업비)")
_SCHEDULE_PAT = re.compile(r"(202\d년|Q[1-4]|분기|상반기|하반기|\d+차년도|FY\d{2,4})")
_PARTICIPATES_PAT = re.compile(r"(참여|참여자|연구원|책임자|PI|담당|수행)")
_TESTS_PAT = re.compile(r"(검증|테스트|test|가설\s*검정)", re.IGNORECASE)
_USES_PAT = re.compile(r"(사용|활용|적용|이용|사용함|활용함)")
_YIELDS_PAT = re.compile(r"(결과|산출|yield|얻었|도출)")
_CITES_PAT = re.compile(r"(\[[0-9]+\]|doi:|https?://|arXiv:|ISBN)", re.IGNORECASE)
_SUPPORTS_PAT = re.compile(r"(근거|support|증거|뒷받침)")


_TYPED_PAIR_RULES: list[tuple[str, str, re.Pattern, str]] = [
    ("metric", "project", _MEASURES_PAT, "measures"),
    ("metric", "technology", _MEASURES_PAT, "measures"),
    ("budget", "project", _BUDGET_PAT, "budgets_for"),
    ("timeline", "project", _SCHEDULE_PAT, "schedules"),
    ("timeline", "experiment", _SCHEDULE_PAT, "schedules"),
    ("person", "project", _PARTICIPATES_PAT, "participates_in"),
    ("person", "org", _PARTICIPATES_PAT, "participates_in"),
    ("method", "hypothesis", _TESTS_PAT, "tests"),
    ("experiment", "dataset", _USES_PAT, "uses"),
    ("experiment", "method", _USES_PAT, "uses"),
    ("experiment", "result", _YIELDS_PAT, "yields"),
    ("function", "test_case", re.compile(r"test|assert", re.IGNORECASE), "tested_by"),
    ("module", "module", re.compile(r"^(?:from|import)\s+", re.MULTILINE), "imports"),
    ("function", "function", re.compile(r"\b[a-z_][a-z0-9_]*\s*\("), "calls"),
    ("citation", "claim", _CITES_PAT, "cites"),
    ("claim", "citation", _SUPPORTS_PAT, "supports"),
]


def _find_typed_relation(kind_a: str, kind_b: str, fact_text: str) -> str | None:
    for ka, kb, pat, rt in _TYPED_PAIR_RULES:
        if (ka == kind_a and kb == kind_b) or (ka == kind_b and kb == kind_a):
            if pat.search(fact_text):
                return rt
    return None


def infer_relations(
    entity_id_to_facts: dict[str, set[str]],
) -> list[InferredEdge]:
    """기존 시그니처 보존. co_occurs + evidence_of 만 반환."""
    return _infer_basic(entity_id_to_facts)


def infer_relations_typed(
    entity_id_to_facts: dict[str, set[str]],
    entity_types: dict[str, str] | None = None,
    fact_texts: dict[str, str] | None = None,
) -> list[InferredEdge]:
    """typed relation 포함 추론.

    entity_types: {entity_id: kind_value}. 없으면 기본 co_occurs 로 폴백.
    fact_texts: {fact_id: text}. typed 패턴 매칭에 필요.
    """
    basic = _infer_basic(entity_id_to_facts)
    if not entity_types or not fact_texts:
        return basic

    fact_to_entities: dict[str, list[str]] = defaultdict(list)
    for ent_id, fact_ids in entity_id_to_facts.items():
        for f_id in fact_ids:
            fact_to_entities[f_id].append(ent_id)

    typed: list[InferredEdge] = []
    pair_seen: dict[tuple[str, str], list[str]] = defaultdict(list)
    pair_rel: dict[tuple[str, str], str] = {}

    for f_id, ents in fact_to_entities.items():
        text = fact_texts.get(f_id, "")
        if not text or len(ents) < 2:
            continue
        for a, b in combinations(sorted(ents), 2):
            ka = entity_types.get(a)
            kb = entity_types.get(b)
            if not ka or not kb:
                continue
            rt = _find_typed_relation(ka, kb, text)
            if not rt:
                continue
            pair_seen[(a, b)].append(f_id)
            pair_rel[(a, b)] = rt

    for (a, b), facts in pair_seen.items():
        rt = pair_rel[(a, b)]
        typed.append(
            InferredEdge(
                src=a,
                dst=b,
                kind=rt,
                weight=float(len(facts)),
                evidence_ids=facts,
                relation_type=rt,
            )
        )

    for e in basic:
        if e.relation_type is None:
            e.relation_type = e.kind

    return basic + typed


def _infer_basic(entity_id_to_facts: dict[str, set[str]]) -> list[InferredEdge]:
    edges: list[InferredEdge] = []

    for ent_id, fact_ids in entity_id_to_facts.items():
        for f_id in fact_ids:
            edges.append(
                InferredEdge(
                    src=ent_id,
                    dst=f_id,
                    kind="evidence_of",
                    weight=1.0,
                    evidence_ids=[f_id],
                    relation_type="evidence_of",
                )
            )

    fact_to_entities: dict[str, list[str]] = defaultdict(list)
    for ent_id, fact_ids in entity_id_to_facts.items():
        for f_id in fact_ids:
            fact_to_entities[f_id].append(ent_id)

    pair_weight: dict[tuple[str, str], list[str]] = defaultdict(list)
    for f_id, ents in fact_to_entities.items():
        if len(ents) < 2:
            continue
        for a, b in combinations(sorted(ents), 2):
            pair_weight[(a, b)].append(f_id)

    for (a, b), facts in pair_weight.items():
        edges.append(
            InferredEdge(
                src=a,
                dst=b,
                kind="co_occurs",
                weight=float(len(facts)),
                evidence_ids=facts,
                relation_type="co_occurs",
            )
        )

    return edges
