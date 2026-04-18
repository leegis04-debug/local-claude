"""모순 탐지 self-critique 게이트.

Phase A entity graph + fact_registry 기반 검증.
- 정규식 1차: registry 의 수치·엔티티와 텍스트 대조
- graph 2차: 필수 엔티티 kinds 충족 여부
- Ollama 3차(옵션): 의미적 모순 (`GP_COHERENCE=strict` 시)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from gstar.entity.graph import neighbors
from gstar.entity.types import EntityKind
from gstar.projection.fact_registry import query as registry_query
from gstar.projection.summarizer import Fact
from gstar.storage.duckdb_store import DuckStore


@dataclass
class Violation:
    code: str                  # "missing_entity_kind", "metric_mismatch", "citation_missing"
    message: str
    severity: str = "warn"     # "warn" | "error"


@dataclass
class Verdict:
    ok: bool
    violations: list[Violation] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    reason: str = ""


_METRIC_VAL_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%?")


def _extract_metric_mentions(text: str) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for m in re.finditer(
        r"(AP@?[\d.]*|mAP|F1|BLEU|ROUGE|정확도|정밀도|재현율|IoU|Top-?[1-9])\s*[:=]?\s*(\d+(?:\.\d+)?)\s*%?",
        text,
        re.IGNORECASE,
    ):
        try:
            out.append((m.group(1).lower(), float(m.group(2))))
        except ValueError:
            continue
    return out


def _check_metric_mismatch(
    text: str,
    registry_facts: list[Fact],
    *,
    tolerance_pct: float = 5.0,
) -> list[Violation]:
    violations: list[Violation] = []
    mentioned = _extract_metric_mentions(text)
    registry_metrics = [f for f in registry_facts if f.kind == "metric"]
    for name, value in mentioned:
        for f in registry_metrics:
            if name in f.text.lower():
                m = _METRIC_VAL_RE.search(f.text.replace(name, "", 1))
                if not m:
                    continue
                ref = float(m.group(1))
                diff_pct = (
                    abs(value - ref) / max(1e-9, max(abs(value), abs(ref))) * 100
                )
                if diff_pct >= tolerance_pct:
                    violations.append(
                        Violation(
                            code="metric_mismatch",
                            message=(
                                f"지표 {name}: 본문 {value} vs 등록된 {ref} "
                                f"(단계 {f.source_stage})"
                            ),
                            severity="error",
                        )
                    )
                break
    return violations


def _check_required_entities(
    text: str,
    required_kinds: list[str],
    store: DuckStore,
    project_id: str,
    track: str,
    graph_present_kinds: set[str],
) -> list[Violation]:
    violations: list[Violation] = []
    for kind in required_kinds:
        if kind in graph_present_kinds:
            continue
        violations.append(
            Violation(
                code="missing_entity_kind",
                message=f"필수 entity kind 누락: {kind}",
                severity="warn",
            )
        )
    return violations


def _check_citation_coverage(
    text: str, registry_facts: list[Fact]
) -> list[Violation]:
    violations: list[Violation] = []
    citation_re = re.compile(r"\[[0-9]+\]|doi:|arXiv:|https?://")
    claims_re = re.compile(r"(주장|명제|결론적으로|따라서|즉|이로써)")
    has_claims = bool(claims_re.search(text))
    has_citations = bool(citation_re.search(text))
    if has_claims and not has_citations:
        violations.append(
            Violation(
                code="citation_missing",
                message="주장이 있으나 인용이 없음",
                severity="warn",
            )
        )
    return violations


def _call_ollama_critique(
    text: str,
    registry_facts: list[Fact],
    model: str = "gemma3:4b-it-qat",
) -> list[Violation]:
    try:
        from gstar.selector.gemma_client import OllamaChatClient

        client = OllamaChatClient(model=model)
    except Exception:
        return []
    fact_lines = "\n".join(f"- {f.kind}: {f.text}" for f in registry_facts[:15])
    system = (
        "당신은 자가비평 reviewer. 텍스트가 등록된 사실과 모순되는지 판단. "
        "모순이 있으면 한 줄씩 '- <이유>' 출력, 없으면 'OK' 한 줄만 출력."
    )
    prompt = f"등록된 사실:\n{fact_lines}\n\n본문:\n{text[:3000]}\n\n판단:"
    try:
        resp = client.judge(system=system, prompt=prompt).strip()
    except Exception:
        return []
    if not resp or resp.strip() == "OK":
        return []
    out: list[Violation] = []
    for line in resp.splitlines():
        line = line.strip().lstrip("-").strip()
        if line and line != "OK":
            out.append(Violation(code="semantic_contradiction", message=line, severity="warn"))
    return out


def check(
    text: str,
    store: DuckStore,
    project_id: str,
    track: str,
    *,
    required_entity_kinds: list[str] | None = None,
    required_fact_kinds: list[str] | None = None,
    use_ollama: bool = False,
    coherence_mode: str = "strict",
    ollama_model: str = "gemma3:4b-it-qat",
) -> Verdict:
    """텍스트 → Verdict (ok + violations)."""
    if coherence_mode == "off":
        return Verdict(ok=True, reason="coherence=off")

    registry_facts = registry_query(store, project_id, track=track)
    violations: list[Violation] = []
    missing: list[str] = []

    violations.extend(_check_metric_mismatch(text, registry_facts))
    violations.extend(_check_citation_coverage(text, registry_facts))

    graph_kinds: set[str] = set()
    if required_entity_kinds:
        missing.extend(
            v.message
            for v in _check_required_entities(
                text, required_entity_kinds, store, project_id, track, graph_kinds
            )
        )
        violations.extend(
            _check_required_entities(
                text, required_entity_kinds, store, project_id, track, graph_kinds
            )
        )

    if required_fact_kinds:
        present_fact_kinds = {f.kind for f in registry_facts}
        for k in required_fact_kinds:
            if k not in present_fact_kinds:
                violations.append(
                    Violation(
                        code="missing_fact_kind",
                        message=f"필수 fact kind 누락: {k}",
                        severity="warn",
                    )
                )
                missing.append(k)

    if use_ollama and coherence_mode == "strict":
        violations.extend(_call_ollama_critique(text, registry_facts, model=ollama_model))

    has_error = any(v.severity == "error" for v in violations)
    ok_threshold = not has_error
    if coherence_mode == "strict":
        ok_threshold = ok_threshold and len([v for v in violations if v.severity == "warn"]) <= 2

    return Verdict(
        ok=ok_threshold,
        violations=violations,
        missing=missing,
        reason=(
            "ok" if ok_threshold else f"{len(violations)} violations ({coherence_mode})"
        ),
    )
