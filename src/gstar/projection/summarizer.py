"""계층 요약 — 단순 truncate 대체.

jw wrapper 의 `PREV_CONTEXT = head -c 1500` 단순 잘라붙이기를 대체.
Ollama judge 기반 2-pass:
1. Pass A: fact 추출 (metric/decision/constraint/claim)
2. Pass B: skeleton (fact 인용한 narrative)

Ollama 미가용 시: 규칙 기반 fallback (문장 추출 + 첫 단락). 본 세션에선 Ollama 가
원격 (4090) 에 있으므로 fallback 경로가 기본 보호망.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from gstar.integrity.hash_chain import compute_content_hash
from gstar.projection.context_loader import StageArtifact
from gstar.projection.stage_roles import StageRole
from gstar.storage.duckdb_store import DuckStore


@dataclass
class Fact:
    text: str
    kind: str
    source_stage: str
    source_hash: str
    entity_ids: list[str] = field(default_factory=list)
    track: str = ""


@dataclass
class StageSummary:
    stage: str
    source_hash: str
    facts: list[Fact]
    skeleton: str


@dataclass
class PrevSummary:
    facts: list[Fact]
    skeleton: str
    citations: list[str]
    raw_refs: dict[str, str] = field(default_factory=dict)

    def as_prompt_block(self, max_chars: int = 2500) -> str:
        lines: list[str] = ["=== 이전 단계 요약 (P축 summarizer) ==="]
        if self.skeleton:
            lines.append(self.skeleton.strip())
        if self.facts:
            lines.append("\n=== 고정 사실 ===")
            for f in self.facts[:20]:
                lines.append(f"- [{f.kind}] {f.text} (from {f.source_stage})")
        for stage, ref in self.raw_refs.items():
            lines.append(f"\n=== {stage} 원문 발췌 ===\n{ref.strip()}")
        block = "\n".join(lines)
        if len(block) > max_chars:
            return block[: max_chars - 3] + "..."
        return block


_FACT_METRIC_RE = re.compile(
    r"(AP@?[\d.]*|mAP|F1|BLEU|ROUGE|정확도|정밀도|재현율|IoU|Top-?[1-9])\s*[:=]?\s*\d+(?:\.\d+)?\s*%?",
    re.IGNORECASE,
)
_FACT_DECISION_RE = re.compile(
    r"(채택|결정|선정|선택|확정|배제|제외|도입)", re.IGNORECASE
)
_FACT_CONSTRAINT_RE = re.compile(
    r"(제약|필수|금지|필요하|해야 한|해야 함|반드시|하지 않)", re.IGNORECASE
)


def _content_hash_for_text(stage: str, text: str) -> str:
    """노드 기반 hash 는 과함. 안정 해시: sha256(stage + text[:1000])."""
    import hashlib

    h = hashlib.sha256()
    h.update(stage.encode("utf-8"))
    h.update(b"\n")
    h.update(text[:2000].encode("utf-8"))
    return h.hexdigest()[:16]


def _fallback_extract_facts(artifact: StageArtifact, track: str = "") -> list[Fact]:
    """Ollama 없이 규칙 기반으로 fact 추출. 문장 단위."""
    facts: list[Fact] = []
    src_hash = _content_hash_for_text(artifact.stage, artifact.text)
    seen_texts: set[str] = set()
    for line in artifact.text.splitlines():
        line = line.strip().lstrip("-").lstrip("*").strip()
        if not line or len(line) < 10 or len(line) > 300:
            continue
        if line in seen_texts:
            continue
        kind: str | None = None
        if _FACT_METRIC_RE.search(line):
            kind = "metric"
        elif _FACT_DECISION_RE.search(line):
            kind = "decision"
        elif _FACT_CONSTRAINT_RE.search(line):
            kind = "constraint"
        if kind is None:
            continue
        seen_texts.add(line)
        facts.append(
            Fact(
                text=line,
                kind=kind,
                source_stage=artifact.stage,
                source_hash=src_hash,
                track=track,
            )
        )
    return facts[:25]


def _fallback_skeleton(artifact: StageArtifact, max_chars: int = 700) -> str:
    paras = [p.strip() for p in artifact.text.split("\n\n") if p.strip()]
    if not paras:
        return ""
    head = paras[0][:300]
    tail = paras[-1][:200] if len(paras) > 1 else ""
    return f"[{artifact.stage}] {head}\n...\n{tail}"[:max_chars]


def _call_ollama_extract(
    artifact: StageArtifact,
    role: StageRole,
    track: str,
    target_kinds: list[str],
) -> list[Fact] | None:
    try:
        from gstar.selector.gemma_client import OllamaChatClient

        client = OllamaChatClient(model=role.ollama_model)
    except Exception:
        return None

    system = (
        "당신은 문서에서 사실을 JSON 배열로 추출하는 엔진이다. "
        "각 fact 는 {text, kind} 키만 갖는다. "
        f"kind 는 다음 중 하나: {', '.join(target_kinds)}. "
        "배열만 출력. 설명 금지."
    )
    prompt = (
        f"단계: {artifact.stage}\n"
        f"본문 (일부):\n{artifact.text[:3500]}\n\n"
        "JSON 배열:"
    )
    try:
        raw = client.judge(system=system, prompt=prompt)
    except Exception:
        return None

    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return None
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return None
    src_hash = _content_hash_for_text(artifact.stage, artifact.text)
    out: list[Fact] = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        kind = str(item.get("kind") or "").strip().lower()
        if not text or not kind:
            continue
        if kind not in target_kinds:
            continue
        out.append(
            Fact(
                text=text,
                kind=kind,
                source_stage=artifact.stage,
                source_hash=src_hash,
                track=track,
            )
        )
    return out


def _call_ollama_skeleton(artifact: StageArtifact, role: StageRole) -> str | None:
    try:
        from gstar.selector.gemma_client import OllamaChatClient

        client = OllamaChatClient(model=role.ollama_model)
    except Exception:
        return None
    system = "당신은 요약가. 400자 이내 핵심 내러티브로 요약. 수치·고유명사 보존."
    prompt = f"단계: {artifact.stage}\n본문: {artifact.text[:3500]}\n\n요약:"
    try:
        return client.judge(system=system, prompt=prompt).strip()
    except Exception:
        return None


def _cache_get(store: DuckStore, stage: str, source_hash: str, depth: str, track: str) -> dict | None:
    row = store.conn.execute(
        "SELECT json FROM projection_summary "
        "WHERE stage = ? AND source_hash = ? AND depth = ? AND track = ?",
        [stage, source_hash, depth, track],
    ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


def _cache_put(
    store: DuckStore,
    stage: str,
    source_hash: str,
    depth: str,
    track: str,
    payload: dict,
) -> None:
    store.conn.execute(
        "INSERT OR REPLACE INTO projection_summary (stage, source_hash, depth, track, json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            stage,
            source_hash,
            depth,
            track,
            json.dumps(payload, ensure_ascii=False),
            datetime.now(timezone.utc).replace(tzinfo=None),
        ],
    )


def summarize_stage(
    artifact: StageArtifact,
    role: StageRole,
    track: str,
    *,
    store: DuckStore | None = None,
    target_kinds: list[str] | None = None,
    use_ollama: bool = True,
) -> StageSummary:
    """단일 단계 → StageSummary."""
    src_hash = _content_hash_for_text(artifact.stage, artifact.text)
    target_kinds = target_kinds or ["metric", "decision", "constraint"]

    if store is not None:
        cached = _cache_get(store, artifact.stage, src_hash, role.summary_depth, track)
        if cached:
            facts = [Fact(**f) for f in cached.get("facts", [])]
            return StageSummary(
                stage=artifact.stage,
                source_hash=src_hash,
                facts=facts,
                skeleton=cached.get("skeleton", ""),
            )

    facts: list[Fact] | None = None
    skeleton: str | None = None
    if use_ollama and role.summary_depth != "none":
        facts = _call_ollama_extract(artifact, role, track, target_kinds)
        skeleton = _call_ollama_skeleton(artifact, role)

    if facts is None:
        facts = _fallback_extract_facts(artifact, track=track)
    if not skeleton:
        skeleton = _fallback_skeleton(artifact)

    summary = StageSummary(
        stage=artifact.stage,
        source_hash=src_hash,
        facts=facts,
        skeleton=skeleton,
    )
    if store is not None:
        _cache_put(
            store,
            artifact.stage,
            src_hash,
            role.summary_depth,
            track,
            {"facts": [asdict(f) for f in facts], "skeleton": skeleton},
        )
    return summary


def summarize_prev(
    artifacts: list[StageArtifact],
    role: StageRole,
    track: str,
    *,
    store: DuckStore | None = None,
    target_kinds: list[str] | None = None,
    use_ollama: bool = True,
    raw_excerpt_chars: int = 180,
) -> PrevSummary:
    """여러 이전 단계를 통합 PrevSummary 로."""
    all_facts: list[Fact] = []
    skeleton_parts: list[str] = []
    citations: list[str] = []
    raw_refs: dict[str, str] = {}

    for a in artifacts:
        ss = summarize_stage(
            a,
            role,
            track,
            store=store,
            target_kinds=target_kinds,
            use_ollama=use_ollama,
        )
        all_facts.extend(ss.facts)
        if ss.skeleton:
            skeleton_parts.append(f"[{a.stage}] {ss.skeleton}")
        citations.append(ss.source_hash)
        head = a.text.strip().splitlines()[0] if a.text else ""
        raw_refs[a.stage] = head[:raw_excerpt_chars]

    merged_skeleton = "\n".join(skeleton_parts)
    return PrevSummary(
        facts=all_facts,
        skeleton=merged_skeleton,
        citations=citations,
        raw_refs=raw_refs,
    )
