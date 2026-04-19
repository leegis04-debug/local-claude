"""2-Layer Verifier — Phase E3.

L1 (rules, LLM 불필요, 빠름):
  - 숫자 일관성: 섹션 내 동일 entity 수치가 fact 와 일치
  - entity chain 완전성: 트랙별 요구 체인 존재
  - citation: 각 claim 에 source 링크
  - 최신성: fact.created_at 이 stale_after_days 이내

L2 (RAG cross-check, 기존 Gateway 대조):
  - 각 fact 의 text 를 Gateway /search/hybrid 에 질의
  - top-3 결과 중 cosine ≥ RAG_CROSS_MIN 이거나 ROUGE-L ≥ 0.3 → pass
  - pass → trust_score += 1, verified_by 에 passage_id append
  - fail → trust_score -= 0.5
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from ulid import ULID


# ---------- 모델 ------------------------------------------------------------


@dataclass
class VerifierIssue:
    kind: str                           # "number_mismatch" | "missing_citation" | "stale" | "chain_incomplete" | "rag_mismatch"
    node_id: str | None
    detail: str
    severity: str = "warn"              # "warn" | "fail"


@dataclass
class VerificationReport:
    pass_: bool
    issues: list[VerifierIssue] = field(default_factory=list)
    trust_deltas: dict[str, float] = field(default_factory=dict)    # node_id → delta
    l2_evidence: dict[str, list[str]] = field(default_factory=dict) # node_id → passage ids
    retry_hint: str | None = None

    def to_json(self) -> dict:
        return {
            "pass": self.pass_,
            "issues": [asdict(i) for i in self.issues],
            "trust_deltas": self.trust_deltas,
            "l2_evidence": self.l2_evidence,
            "retry_hint": self.retry_hint,
        }


# ---------- L1: 규칙 검증 ---------------------------------------------------


_NUM_RE = re.compile(r"(\d+(?:[.,]\d+)*)\s*(억원|백만원|만원|원|%|년|달|개|건|명)?")


def _extract_numbers(text: str) -> list[tuple[str, str]]:
    """(value, unit?) 쌍 추출."""
    return [(m.group(1), m.group(2) or "") for m in _NUM_RE.finditer(text or "")]


def _number_consistency(
    section_text: str, fact_texts: Iterable[str]
) -> list[VerifierIssue]:
    """섹션 내 숫자가 fact 에도 있는지 확인. 동일 unit 매칭."""
    issues: list[VerifierIssue] = []
    fact_nums: set[tuple[str, str]] = set()
    for ft in fact_texts:
        fact_nums.update(_extract_numbers(ft))
    for n, u in _extract_numbers(section_text):
        if (n, u) not in fact_nums and not _is_common_number(n):
            issues.append(
                VerifierIssue(
                    kind="number_mismatch",
                    node_id=None,
                    detail=f"섹션 수치 {n}{u} 가 fact 에 없음",
                    severity="fail",
                )
            )
    return issues


def _is_common_number(n: str) -> bool:
    """흔한 값은 warn 제외 (순번·연도 전반)."""
    try:
        v = int(str(n).replace(",", "").replace(".", ""))
    except ValueError:
        return False
    return v <= 10 or 1900 <= v <= 2100


_TRACK_CHAIN_REQUIREMENTS: dict[str, set[str]] = {
    "proposal": {"project", "metric"},
    "research": {"hypothesis", "method"},
    "coding": {"function", "test_case"},
    "document": {"claim"},
}


def _entity_chain(
    fact_attrs: list[dict[str, Any]], track: str
) -> list[VerifierIssue]:
    """track 에 맞는 entity kind 가 모두 등장하는지."""
    required = _TRACK_CHAIN_REQUIREMENTS.get(track, set())
    if not required:
        return []
    present: set[str] = set()
    for a in fact_attrs:
        kinds = a.get("entity_kinds") or []
        for k in kinds:
            present.add(str(k).lower())
    missing = required - present
    if missing:
        return [
            VerifierIssue(
                kind="chain_incomplete",
                node_id=None,
                detail=f"track={track} 에 필요한 entity 종류 누락: {sorted(missing)}",
                severity="warn",
            )
        ]
    return []


def _citations(facts: list[dict[str, Any]]) -> list[VerifierIssue]:
    """각 fact 에 source 링크(attrs.source) 존재."""
    issues: list[VerifierIssue] = []
    for f in facts:
        fid = f.get("node_id") or ""
        src = (f.get("attrs") or {}).get("source")
        if not src:
            issues.append(
                VerifierIssue(
                    kind="missing_citation",
                    node_id=fid,
                    detail="fact source 누락",
                    severity="warn",
                )
            )
    return issues


def _freshness(
    facts: list[dict[str, Any]], stale_after_days: int
) -> list[VerifierIssue]:
    if stale_after_days <= 0:
        return []
    issues: list[VerifierIssue] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=stale_after_days)
    for f in facts:
        c = f.get("created_at")
        if isinstance(c, str):
            try:
                c = datetime.fromisoformat(c.replace("Z", "+00:00"))
            except Exception:
                c = None
        if c is None:
            continue
        if c < cutoff:
            issues.append(
                VerifierIssue(
                    kind="stale",
                    node_id=f.get("node_id"),
                    detail=f"created_at={c.isoformat()} > {stale_after_days}d",
                    severity="warn",
                )
            )
    return issues


def verify_l1(
    *,
    section_text: str,
    facts: list[dict[str, Any]],
    track: str = "document",
    stale_after_days: int = 365,
) -> VerificationReport:
    """L1 규칙 검증. facts 각 항: {node_id, text, attrs, created_at, entity_kinds?}."""
    rep = VerificationReport(pass_=True)
    fact_texts = [f.get("text", "") for f in facts]
    fact_attrs = [f.get("attrs", {}) for f in facts]

    rep.issues.extend(_number_consistency(section_text, fact_texts))
    rep.issues.extend(_entity_chain(fact_attrs, track))
    rep.issues.extend(_citations(facts))
    rep.issues.extend(_freshness(facts, stale_after_days))

    fail_issues = [i for i in rep.issues if i.severity == "fail"]
    if fail_issues:
        rep.pass_ = False
        rep.retry_hint = "; ".join(i.detail for i in fail_issues[:3])
    return rep


# ---------- L2: RAG cross-check --------------------------------------------


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text or "") if len(t) > 1]


def _rouge_l_approx(a: str, b: str) -> float:
    """간이 ROUGE-L: 최장 공통 부분수열 / max(|a|,|b|). O(|a|·|b|)."""
    ta, tb = _tokens(a)[:200], _tokens(b)[:200]
    if not ta or not tb:
        return 0.0
    la, lb = len(ta), len(tb)
    dp = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la):
        for j in range(lb):
            if ta[i] == tb[j]:
                dp[i + 1][j + 1] = dp[i][j] + 1
            else:
                dp[i + 1][j + 1] = max(dp[i + 1][j], dp[i][j + 1])
    return dp[la][lb] / max(la, lb)


def _call_gateway_hybrid(
    gateway_url: str, token: str, query: str, top_k: int = 3, timeout: float = 5.0
) -> list[dict]:
    body = json.dumps({"query": query[:500], "top_k": top_k}).encode("utf-8")
    req = urllib.request.Request(
        f"{gateway_url.rstrip('/')}/search/hybrid",
        data=body,
        headers={"Content-Type": "application/json", "X-Auth-Token": token},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception:
        return []
    return d.get("results") or d.get("hits") or []


def verify_l2_rag_cross(
    *,
    facts: list[dict[str, Any]],
    gateway_url: str | None = None,
    token: str | None = None,
    min_rouge_l: float = 0.30,
    min_sim: float = 0.55,
    top_k: int = 3,
    timeout: float = 5.0,
) -> VerificationReport:
    """각 fact 를 Gateway /search/hybrid 에 질의. top 결과와 비교해 trust 업데이트."""
    rep = VerificationReport(pass_=True)
    gurl = gateway_url or os.environ.get("GATEWAY_URL", "http://100.79.251.53:8000")
    tok = token or os.environ.get("ASST_TOKEN", "")
    if not tok:
        rep.pass_ = False
        rep.retry_hint = "ASST_TOKEN 없음 — L2 skip"
        return rep

    pass_count = 0
    for f in facts:
        nid = f.get("node_id") or ""
        text = f.get("text", "")
        if not text.strip():
            continue
        hits = _call_gateway_hybrid(gurl, tok, text, top_k=top_k, timeout=timeout)
        if not hits:
            rep.trust_deltas[nid] = rep.trust_deltas.get(nid, 0.0) - 0.5
            rep.issues.append(
                VerifierIssue(
                    kind="rag_mismatch",
                    node_id=nid,
                    detail="Gateway 대조 결과 없음",
                    severity="warn",
                )
            )
            continue
        best_sim = max(float(h.get("score") or h.get("sim") or 0.0) for h in hits)
        best_rouge = max(_rouge_l_approx(text, h.get("text") or "") for h in hits)
        if best_sim >= min_sim or best_rouge >= min_rouge_l:
            pass_count += 1
            rep.trust_deltas[nid] = rep.trust_deltas.get(nid, 0.0) + 1.0
            pid = str(hits[0].get("id") or hits[0].get("node_id") or "")
            if pid:
                rep.l2_evidence.setdefault(nid, []).append(pid)
        else:
            rep.trust_deltas[nid] = rep.trust_deltas.get(nid, 0.0) - 0.5
            rep.issues.append(
                VerifierIssue(
                    kind="rag_mismatch",
                    node_id=nid,
                    detail=f"sim={best_sim:.2f} rouge={best_rouge:.2f} 모두 기준 미달",
                    severity="warn",
                )
            )

    if pass_count == 0 and facts:
        rep.pass_ = False
        rep.retry_hint = "L2: 어떤 fact 도 기존 RAG 와 매칭되지 않음"
    return rep


# ---------- 적용: DuckStore 에 trust 업데이트 ------------------------------


def apply_trust_deltas(
    store,
    report: VerificationReport,
    *,
    layer: str,
) -> int:
    """trust_score/verified_by_json/last_verified_at 업데이트 + verification_log 삽입."""
    if not report.trust_deltas:
        return 0
    now = datetime.now(timezone.utc)
    updated = 0
    with store.lock:
        for nid, delta in report.trust_deltas.items():
            # 현재 값 읽기
            row = store.conn.execute(
                "SELECT trust_score, verified_by_json FROM node WHERE id=?", [nid]
            ).fetchone()
            if not row:
                continue
            cur_score = float(row[0] or 0.0)
            cur_verified = row[1] or "[]"
            try:
                verified_list = json.loads(cur_verified) if cur_verified else []
            except Exception:
                verified_list = []
            for pid in report.l2_evidence.get(nid, []):
                if pid not in verified_list:
                    verified_list.append(pid)
            new_score = max(-5.0, min(10.0, cur_score + delta))
            store.conn.execute(
                "UPDATE node SET trust_score=?, verified_by_json=?, last_verified_at=? "
                "WHERE id=?",
                [new_score, json.dumps(verified_list), now, nid],
            )
            store.conn.execute(
                "INSERT INTO verification_log "
                "(id, node_id, layer, pass, delta, evidence_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    str(ULID()),
                    nid,
                    layer,
                    delta > 0,
                    delta,
                    json.dumps({"ev": report.l2_evidence.get(nid, [])}),
                    now,
                ],
            )
            updated += 1
    return updated
