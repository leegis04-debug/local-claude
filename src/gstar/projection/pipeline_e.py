"""Phase E4 — Pipeline 조립 (PROJECTION_MODE env).

모드:
  classic — 기존 직접 LLM (구현 X, 기존 projection/cli.py 가 classic 역할)
  sv      — Selector + Projector
  svr     — + Verifier L1
  svrr    — + Verifier L2 (기존 RAG 대조) + Reinforce  (권장)

사용:
  from gstar.projection.pipeline_e import run_svrr
  out = run_svrr(goal="AI 농업 플랫폼", track="proposal", section=..., gclient=...)
"""

from __future__ import annotations

import concurrent.futures
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from gstar.projection.projector import ProjectionInput, ProjectorOutput, SectionSpec, project_section
from gstar.projection.reinforce import ReinforceResult, reinforce_to_gateway
from gstar.projection.selector_loop import SelectorResult, run_selector


# ---------- Stage B(3): 실시간 테마 그룹핑 ----------


def _theme_labels_single(
    goal: str, facts: list[dict], max_themes: int = 4, min_themes: int = 3
) -> list[str]:
    """단일 호출 — facts 리스트 하나를 Gemma 에게 테마 분류 요청. 내부용."""
    if len(facts) < 4:
        return []
    from gstar.projection.selector_loop import selector_host, selector_api
    from gstar.selector.gemma_client import OllamaChatClient
    client = OllamaChatClient(
        host=selector_host(),
        model=os.environ.get("GP_THEME_MODEL", os.environ.get("SELECTOR_MODEL", "gemma4:e4b")),
        timeout_s=60.0,
        num_predict=300,
        temperature=0.3,
        api_schema=selector_api(),
    )
    facts_block = "\n".join(
        f"[{i}] {(f.get('text') or '')[:180]}" for i, f in enumerate(facts)
    )
    prompt = (
        f"[목표] {goal}\n\n"
        f"[지식 조각 {len(facts)}개]\n{facts_block}\n\n"
        f"작업: 위 {len(facts)}개 조각을 **정확히 {min_themes}~{max_themes}개 테마**로 분류.\n"
        "절대 1개 테마로 뭉치지 말고, 의미·도메인·사용처 축으로 균등하게 나누어라.\n"
        "각 테마에 1개 이상의 조각이 배정돼야 함.\n\n"
        "형식 (정확히 준수. 다른 어떤 텍스트도 금지):\n"
        "1. <테마 라벨 1>\n"
        "2. <테마 라벨 2>\n"
        "3. <테마 라벨 3>\n"
        f"[최대 {max_themes}줄까지 번호]\n\n"
        "라벨 규칙: 명사구 5~25자. **, ` 같은 기호 금지."
    )
    try:
        out = client.judge(system="너는 지식 조각 분류 전문가다.", prompt=prompt)
    except Exception:
        return []
    labels: list[str] = []
    for line in out.splitlines():
        m = re.match(r"^\s*\d+\.\s*(.+?)\s*$", line)
        if m:
            lab = m.group(1).strip().strip("*").strip("`").strip()
            lab = re.sub(r"\*+", "", lab).strip()
            if 3 <= len(lab) <= 40:
                labels.append(lab)
    return labels[:max_themes]


def _theme_labels(goal: str, facts: list[dict], max_themes: int = 4) -> list[str]:
    """Gemma 로 facts 테마 분류. 40+ fact 는 chunk 로 나눠 여러 번 호출 후 merge.

    실패 시 빈 리스트 (호출자는 단일 호출 폴백). Projector 프롬프트 앞에 scaffold
    (## 1. <라벨1> ## 2. <라벨2>) 로 주입되어 평면 기술을 테마별 구조로 승격.

    **수정 (d66fda5 follow-up)**: 단일 호출로 40 fact 주면 E4B 가 overwhelm 돼
    1개 테마로 뭉치는 문제. chunk_size=20 으로 나눠 각각 분류 후 라벨 중복 제거로
    통합. 총 라벨 수는 max_themes 로 제한.
    """
    if len(facts) < 4:
        return []
    chunk_size = int(os.environ.get("GP_THEME_CHUNK", "20") or "20")
    # 소규모면 단일 호출
    if len(facts) <= chunk_size:
        return _theme_labels_single(goal, facts, max_themes=max_themes)

    # 대규모: chunk 별 분류 후 merge
    chunks = [facts[i : i + chunk_size] for i in range(0, len(facts), chunk_size)]
    per_chunk_max = max(2, max_themes // len(chunks) + 1)  # chunk 당 2~N 라벨
    all_labels: list[str] = []
    for ci, ch in enumerate(chunks):
        ls = _theme_labels_single(goal, ch, max_themes=per_chunk_max, min_themes=2)
        all_labels.extend(ls)
    return _merge_near_duplicate_labels(all_labels, max_themes=max_themes)


def _label_tokens(lab: str) -> set[str]:
    """라벨을 비교용 토큰 집합으로 정규화 — 한글·영숫자 2자+ 연속 추출, lowercase."""
    return {t for t in re.findall(r"[가-힣]{2,}|[A-Za-z0-9]{2,}", lab.lower())}


def _merge_near_duplicate_labels(labels: list[str], *, max_themes: int = 4) -> list[str]:
    """chunk 간 반복 호출로 생긴 유사 라벨 중복 제거.

    전략 (독립 · 의존 순서대로 적용):
    1. 공백·대소문자 제거 후 완전 일치 → 첫 라벨만 유지
    2. 토큰 overlap 이 max(|A|,|B|) 의 2/3 이상 → 짧은 라벨 유지 ("농식품 유통 및 판매
       시스템 구축" vs "농산물 유통 및 판매 시스템 구축" 병합)
    3. 길이 순이 아니라 **원 순서** 유지 (앞선 chunk 의 라벨 우선). 상한 max_themes
    """
    # Step 1 — 정규화 완전일치 dedup
    seen_key: set[str] = set()
    normed: list[str] = []
    for lab in labels:
        k = re.sub(r"\s+", "", lab.lower())
        if k in seen_key:
            continue
        seen_key.add(k)
        normed.append(lab)

    # Step 2 — 토큰 overlap 기반 near-dup merge
    kept: list[str] = []
    kept_tokens: list[set[str]] = []
    sim_threshold = float(os.environ.get("GP_THEME_DEDUP_SIM", "0.66") or "0.66")
    for lab in normed:
        toks = _label_tokens(lab)
        if not toks:
            kept.append(lab)
            kept_tokens.append(toks)
            continue
        merged = False
        for i, prev_toks in enumerate(kept_tokens):
            if not prev_toks:
                continue
            overlap = len(toks & prev_toks)
            denom = max(len(toks), len(prev_toks))
            if denom > 0 and overlap / denom >= sim_threshold:
                # 병합 — 더 짧은 라벨을 대표로 (핵심 명사구 남기기)
                if len(lab) < len(kept[i]):
                    kept[i] = lab
                    kept_tokens[i] = toks
                merged = True
                break
        if not merged:
            kept.append(lab)
            kept_tokens.append(toks)

    return kept[:max_themes]


def _with_theme_scaffold(section: SectionSpec, labels: list[str]) -> SectionSpec:
    """SectionSpec.instruction 에 테마 scaffold 를 prepend. 원본 불변."""
    scaffold = "[응답 구조 — 아래 테마별로 섹션 구성]\n" + "\n".join(
        f"## {i+1}. {lab}" for i, lab in enumerate(labels)
    )
    return SectionSpec(
        name=section.name,
        instruction=f"{scaffold}\n\n{section.instruction}",
        target_tokens=section.target_tokens,
    )
from gstar.projection.verifier import (
    VerificationReport,
    apply_trust_deltas,
    verify_l1,
    verify_l2_rag_cross,
)


def _project_panel(proj_in, *, track_name: str, rep) -> ProjectorOutput:
    """Phase E2+Panel — stage 별 persona 패널로 projector 다중 호출 후 integrator 합성.

    각 persona 는 project_section 을 재사용하되 ProjectionInput.section.instruction
    앞에 persona system prompt 를 prepend 해 관점을 강제. integrator 는 drafts 전체를
    받아 합성.

    panel 이 비어 있으면 (stage 에 panel 정의 없음) 단일 project_section 로 폴백.
    """
    from gstar.projection.personas import panel_for, integration_role

    stage = proj_in.section.name
    panel = panel_for(stage)
    if not panel:
        # stage 에 panel 정의 없음 — 단일 호출 폴백
        rep.warnings.append(f"[panel] stage='{stage}' panel 미정의 → 단일 호출")
        return project_section(proj_in)

    # Phase P-Q3 — persona 패널 병렬 dispatch.
    # 순차 for-loop 는 N persona × 단일 latency → 20분급. ThreadPoolExecutor 로
    # Ollama/vLLM 에 동시 요청 → 백엔드의 parallel decode 활용 (Ollama 는
    # OLLAMA_NUM_PARALLEL, vLLM 은 continuous batching). IO bound 이므로 GIL 무관.
    _panel_workers = int(os.environ.get("GP_PANEL_WORKERS", str(len(panel))) or 1)
    _panel_workers = max(1, min(_panel_workers, len(panel)))

    def _run_persona(spec) -> tuple[str, str | None, str | None]:
        """(role, text_or_none, error_or_none)."""
        persona_in = ProjectionInput(
            goal=proj_in.goal,
            track=proj_in.track,
            facts=proj_in.facts,
            section=SectionSpec(
                name=f"{stage}::{spec.role}",
                instruction=f"[역할: {spec.role}] {spec.system_prompt}\n\n{proj_in.section.instruction}",
                target_tokens=proj_in.section.target_tokens,
            ),
        )
        try:
            out = project_section(persona_in)
            if out.text and "(Projector 호출 실패" not in out.text:
                return (spec.role, out.text, None)
            return (spec.role, None, "빈 draft 또는 Projector 호출 실패")
        except Exception as exc:
            return (spec.role, None, f"{type(exc).__name__}: {exc}")

    # 원래 panel 순서대로 drafts 보존 (integrator 프롬프트 안정성).
    drafts: list[tuple[str, str]] = []
    results_by_role: dict[str, tuple[str | None, str | None]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=_panel_workers) as ex:
        for role, text, err in ex.map(_run_persona, panel):
            results_by_role[role] = (text, err)
    for spec in panel:
        text, err = results_by_role.get(spec.role, (None, "결과 누락"))
        if text is not None:
            drafts.append((spec.role, text))
        else:
            rep.warnings.append(f"[panel] {spec.role} 드래프트 실패 — {err}")

    if not drafts:
        rep.warnings.append("[panel] 모든 persona 실패 → 단일 호출 폴백")
        return project_section(proj_in)

    # integrator 로 합성
    integ = integration_role()
    integrated_block = "\n\n".join(f"## {role} 관점\n{text}" for role, text in drafts)
    integ_in = ProjectionInput(
        goal=proj_in.goal,
        track=proj_in.track,
        facts=proj_in.facts,
        section=SectionSpec(
            name=f"{stage}::integrated",
            instruction=(
                f"[역할: integrator] {integ.system_prompt}\n\n"
                f"[원래 섹션 지시] {proj_in.section.instruction}\n\n"
                f"[{len(drafts)}개 persona 드래프트]\n{integrated_block}\n\n"
                "위 관점들을 하나의 일관된 섹션으로 합쳐라. 각 persona 의 강점 유지, 중복·충돌 제거."
            ),
            target_tokens=int(proj_in.section.target_tokens * 1.5),
        ),
    )
    try:
        integrated = project_section(integ_in)
        # 원래 섹션명으로 복원
        integrated.section_name = stage
        return integrated
    except Exception as exc:
        rep.warnings.append(f"[panel] integrator 실패: {exc} → 첫 draft 사용")
        role0, text0 = drafts[0]
        return ProjectorOutput(
            section_name=stage,
            text=text0,
            used_fact_ids=[str(f.get("node_id") or "") for f in proj_in.facts],
            model=f"panel-fallback-{role0}",
        )


# ---------- Stage C: 창발 질문 유출 + 웹 seed 재귀 루프 ----------


def _emerged_questions(goal: str, facts: list[dict], max_q: int = 3) -> list[str]:
    """Gemma E4B 로 fact 집합의 설명 안 된 간극을 채울 웹 검색 질문 N개 생성.

    실패·빈 결과 시 [] 반환. 호출자는 루프 종료.
    """
    if not facts:
        return []
    from gstar.projection.selector_loop import selector_host, selector_api
    from gstar.selector.gemma_client import OllamaChatClient
    client = OllamaChatClient(
        host=selector_host(),
        model=os.environ.get("SELECTOR_MODEL", "gemma4:e4b"),
        timeout_s=60.0,
        num_predict=400,
        temperature=0.5,
        api_schema=selector_api(),
    )
    facts_block = "\n".join(
        f"- {(f.get('text') or '')[:180]}" for i, f in enumerate(facts[:20])
    )
    prompt = (
        f"[목표] {goal}\n\n"
        f"[이미 수집한 지식 {min(len(facts), 20)}개]\n{facts_block}\n\n"
        "작업:\n"
        "1) [목표] 문장을 단어·명사구로 쪼개, 각각이 [이미 수집한 지식]에 등장하는지 대조.\n"
        "2) 등장 빈도가 0 이거나 부실한 키워드 = '빈 슬롯'. 빈 슬롯 우선 주목.\n"
        f"3) 빈 슬롯을 채울 웹 검색 질의 {max_q}개 생성.\n\n"
        "질의 규칙:\n"
        "- 순수한 질의만 한 줄씩. **, `, 따옴표, 괄호, 콜론 금지.\n"
        "- 이미 수집된 내용 재요약 금지. 새 정보를 찾는 것.\n"
        "- 구체적 사례·제품명·기업명·수치·연도 중 하나 이상 포함.\n"
        "- 8~40자. 한국어 검색 친화적.\n\n"
        "예시 (별개 주제):\n"
        "목표: 수소버스 연료전지 내구성\n"
        "이미 수집: 수소연료전지 원리, 국내 버스 시범사업 개요\n"
        "빈 슬롯: 내구성 (수명·성능 열화 데이터 전무)\n"
        "출력:\n"
        "1. 수소버스 연료전지 수명 실증 데이터\n"
        "2. 저온 환경 연료전지 성능 열화 사례\n"
        "3. 현대 수소버스 일렉시티 내구성 보고서\n\n"
        "이제 위 [목표]·[이미 수집한 지식]에 동일하게 적용하여 출력만 생성:\n"
    )
    try:
        out = client.judge(system="너는 지식 간극 분석·질의 생성 전문가다.", prompt=prompt)
    except Exception:
        return []
    questions: list[str] = []
    for line in out.splitlines():
        m = re.match(r"^\s*\d+\.\s*(.+?)\s*$", line)
        if m:
            q = m.group(1).strip().strip("*").strip("`").strip('"').strip("'")
            # markdown 강조 · 콜론 이후 괄호 등 제거
            q = re.sub(r"\*+", "", q).strip()
            q = re.sub(r":\s*\(.+\)$", "", q).strip()
            if 8 <= len(q) <= 200:
                questions.append(q)
    return questions[:max_q]


def _run_emergence_loop(
    goal: str,
    *,
    gclient,
    skwargs: dict,
    rep: "PipelineResult",
) -> SelectorResult:
    """Stage C 재귀 seed 확장 루프.

    반복:
      1) run_selector → current facts
      2) Gemma 가 fact 간극 기반 질의 N개 유출
      3) 각 질의에 cache_first_web_search 실행 (Brave + G 역삽입)
      4) run_selector 재호출 → 새 fact 반영된 결과
      5) fact 수 증가 미미 or max_iter 도달 → 종료

    env:
      GP_EMERGENCE_MAX_ITER=2       — 루프 상한 (기본 2)
      GP_EMERGENCE_QUESTIONS=3      — iter 당 질의 수 (기본 3)
      GP_EMERGENCE_MIN_GAIN=3       — 종료 임계 fact 증가량 (기본 3)
      GP_EMERGENCE_WEB_TOP_K=5      — 각 질의 웹검색 top-K (기본 5)
      GP_EMERGENCE_TOP_K=40         — Selector final_facts 상한 (기본 40, vs
                                       기본 20). 새 web fact 가 기존 top 에 진입할
                                       헤드룸 확보
      GP_EMERGENCE_CANDIDATE_K=160  — G fused_search 초기 후보 수 (기본 160,
                                       vs 기본 80). 더 넓은 pool 에서 관련 필터
    """
    import asyncio
    from gstar.enrich.g_cache import cache_first_web_search

    max_iter = int(os.environ.get("GP_EMERGENCE_MAX_ITER", "2") or "2")
    n_questions = int(os.environ.get("GP_EMERGENCE_QUESTIONS", "3") or "3")
    min_gain = int(os.environ.get("GP_EMERGENCE_MIN_GAIN", "3") or "3")
    web_top_k = int(os.environ.get("GP_EMERGENCE_WEB_TOP_K", "5") or "5")

    # Selector pool 확대 — emergence 는 새 web fact 가 top 에 진입할 헤드룸 필요.
    # 호출자가 이미 skwargs 에 top_k_final/candidate_k 명시하면 그걸 존중.
    em_skwargs = dict(skwargs)
    em_skwargs.setdefault(
        "top_k_final",
        int(os.environ.get("GP_EMERGENCE_TOP_K", "40") or "40"),
    )
    em_skwargs.setdefault(
        "candidate_k",
        int(os.environ.get("GP_EMERGENCE_CANDIDATE_K", "160") or "160"),
    )

    sel = run_selector(goal, gclient=gclient, **em_skwargs)
    prev_count = len(sel.final_facts)
    rep.warnings.append(
        f"[emergence] iter 0: {prev_count} facts "
        f"(top_k={em_skwargs['top_k_final']} cand={em_skwargs['candidate_k']})"
    )

    # Stage B(2) — graph 1~2 hop 확장으로 seed fact 의 edge 인접 노드 수집.
    # emergence 루프와 병행 시 창발 fact 가 기존 지식항성과 관계(edge)로
    # 연결되는 확장 경로 형성. GP_GRAPH_EXPAND=on 에만 활성.
    use_graph = os.environ.get("GP_GRAPH_EXPAND", "off").lower() in {"on", "1", "true"}
    graph_hops = int(os.environ.get("GP_GRAPH_HOPS", "1") or "1")
    graph_limit = int(os.environ.get("GP_GRAPH_EXPAND_LIMIT", "40") or "40")

    def _maybe_graph_merge(sel_obj: "SelectorResult", label: str) -> None:
        """sel_obj.final_facts 의 seed node_ids 로 graph expand → merge."""
        if not use_graph or not sel_obj.final_facts:
            return
        seed_ids = [f.get("node_id") for f in sel_obj.final_facts if f.get("node_id")]
        if not seed_ids:
            return
        try:
            neighbors = gclient.graph_expand(
                seed_ids[:20],  # seed 상한 — 너무 크면 서버 부담
                hops=graph_hops,
                limit=graph_limit,
            )
        except Exception as exc:
            rep.warnings.append(f"[graph] expand 실패 {label}: {type(exc).__name__}: {exc}")
            return
        if not neighbors:
            rep.warnings.append(f"[graph] expand {label}: neighbor 0")
            return
        existing_ids = {f.get("node_id") for f in sel_obj.final_facts}
        existing_texts = {(f.get("text") or "")[:150] for f in sel_obj.final_facts}
        added = 0
        for n in neighbors:
            nid = n.get("node_id")
            txt = (n.get("text") or "")[:150]
            if nid in existing_ids or txt in existing_texts:
                continue
            sel_obj.final_facts.append(
                {
                    "node_id": nid,
                    "text": n.get("text") or "",
                    "score": 0.5,  # graph 출처는 semantic score 없어 중립값
                    "origin": f"graph:dist{n.get('distance', 1)}",
                    "namespace": n.get("namespace") or "",
                }
            )
            existing_ids.add(nid)
            added += 1
        rep.warnings.append(f"[graph] expand {label}: +{added} neighbor merged (hops={graph_hops})")

    _maybe_graph_merge(sel, "iter0")

    if prev_count == 0:
        # seed 가 전무하면 web 만 돌려 초기 축적 (강제 fresh — 이미 부재 확인됨)
        rep.warnings.append("[emergence] seed 0 → goal 직접 웹검색 1회 (force_web)")
        try:
            asyncio.run(cache_first_web_search(goal, top_k=web_top_k, force_web=True))
        except Exception as exc:
            rep.warnings.append(f"[emergence] goal 웹검색 실패: {exc}")
        sel = run_selector(goal, gclient=gclient, **em_skwargs)
        prev_count = len(sel.final_facts)
        rep.warnings.append(f"[emergence] seed 보충 후: {prev_count} facts")
        if prev_count == 0:
            rep.warnings.append("[emergence] 보충 후에도 seed 0 → 종료")
            return sel

    # emergence 의 목적은 간극 채우기 → cache_first 가 "충분" 판단해 web skip 하면
    # 간극을 절대 메울 수 없음. 기본 force_web=True 로 매번 웹 호출 강제.
    # GP_EMERGENCE_CACHE_FIRST=on 으로 override 가능 (이미 축적된 주제 재실행 시).
    force_web_default = os.environ.get("GP_EMERGENCE_CACHE_FIRST", "off").lower() not in {"on", "1", "true"}

    # Sprint C #C1 — wiki-first 조회. web 호출 전 wiki topic/entity 인덱스에서
    # 충분한 synthesis 내용을 발견하면 해당 질문은 web skip. wiki 는 이미
    # louvain community 단위로 compound 된 상위 지식이라 raw facts 보다 풍부.
    use_wiki_first = os.environ.get("GP_EMERGENCE_WIKI_FIRST", "on").lower() in {"on", "1", "true"}
    wiki_top_k = int(os.environ.get("GP_EMERGENCE_WIKI_TOP_K", "3") or "3")
    wiki_min_score = float(os.environ.get("GP_EMERGENCE_WIKI_MIN_SCORE", "0.15") or "0.15")
    wiki_min_excerpt = int(os.environ.get("GP_EMERGENCE_WIKI_MIN_EXCERPT", "300") or "300")

    for it in range(1, max_iter + 1):
        questions = _emerged_questions(goal, sel.final_facts, max_q=n_questions)
        if not questions:
            rep.warnings.append(f"[emergence] iter {it}: 추가 질의 없음 → 종료")
            break
        rep.warnings.append(f"[emergence] iter {it} 질의 {len(questions)}개: {questions}")

        total_ingested = 0
        total_web_hits = 0
        total_wiki_hits = 0
        wiki_skipped_web = 0
        for q in questions:
            # wiki-first: 질문이 이미 wiki topic/entity 에 synthesis 돼있는지 조회.
            wiki_covered = False
            if use_wiki_first:
                try:
                    w_items = gclient.wiki_search(q, top_k=wiki_top_k)
                except Exception as exc:
                    rep.warnings.append(
                        f"[emergence] wiki_search 실패 '{q[:40]}': {type(exc).__name__}: {exc}"
                    )
                    w_items = []
                strong = [
                    w for w in w_items
                    if (w.get("score") or 0) >= wiki_min_score
                    and len(w.get("excerpt") or "") >= wiki_min_excerpt
                ]
                if strong:
                    total_wiki_hits += len(strong)
                    wiki_covered = True
                    # wiki 내용을 sel.final_facts 에 주입 (origin="wiki") — 다음
                    # selector 재호출 전 즉시 Projector 가 활용할 수 있도록.
                    for w in strong:
                        sel.final_facts.append({
                            "node_id": f"wiki:{w.get('path')}",
                            "text": f"[wiki/{w.get('type')}] {w.get('title','')}\n{w.get('excerpt','')}",
                            "score": float(w.get("score") or 0.5),
                            "origin": f"wiki:{w.get('type')}",
                            "namespace": "wiki",
                        })
                    wiki_skipped_web += 1
                    rep.warnings.append(
                        f"[emergence] iter {it} wiki-hit '{q[:40]}' "
                        f"→ {len(strong)}건 주입, web skip"
                    )

            if wiki_covered:
                continue

            try:
                res = asyncio.run(
                    cache_first_web_search(q, top_k=web_top_k, force_web=force_web_default)
                )
                total_ingested += int(getattr(res, "ingested", 0) or 0)
                total_web_hits += int(getattr(res, "web_hits", 0) or 0)
            except Exception as exc:
                rep.warnings.append(f"[emergence] web_search 실패 '{q[:40]}': {type(exc).__name__}: {exc}")

        rep.warnings.append(
            f"[emergence] iter {it} wiki_hits={total_wiki_hits} "
            f"(web_skipped={wiki_skipped_web}/{len(questions)}) · "
            f"웹 hits={total_web_hits} · G 축적={total_ingested}"
        )

        new_sel = run_selector(goal, gclient=gclient, **em_skwargs)
        _maybe_graph_merge(new_sel, f"iter{it}")
        new_count = len(new_sel.final_facts)
        sel_gain = new_count - prev_count
        rep.warnings.append(
            f"[emergence] iter {it} 후 selector: {new_count} facts (sel_gain=+{sel_gain})"
        )

        sel = new_sel
        # 종료 조건: Selector top_k 상한 때문에 sel_gain 이 0 나오더라도 G 에 실제
        # ingest 된 fact 가 많으면 계속 진행 (다음 iter 에서 더 구체 질의가 기존
        # top 을 밀어낼 기회). ingested >= min_gain * 20 을 보조 기준으로.
        ingest_gain_threshold = min_gain * 20
        sufficient_ingest = total_ingested >= ingest_gain_threshold
        if sel_gain < min_gain and not sufficient_ingest:
            rep.warnings.append(
                f"[emergence] iter {it}: sel_gain {sel_gain}<{min_gain} AND "
                f"ingest {total_ingested}<{ingest_gain_threshold} → 종료"
            )
            break
        if sel_gain < min_gain and sufficient_ingest:
            rep.warnings.append(
                f"[emergence] iter {it}: sel_gain 은 낮지만 ingest {total_ingested}"
                f"≥{ingest_gain_threshold} 로 계속"
            )
        prev_count = new_count

    return sel


@dataclass
class PipelineResult:
    mode: str
    goal: str
    track: str
    section: str
    selector: SelectorResult | None = None
    projector: ProjectorOutput | None = None
    verifier_l1: VerificationReport | None = None
    verifier_l2: VerificationReport | None = None
    reinforce: ReinforceResult | None = None
    warnings: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        d = {
            "mode": self.mode,
            "goal": self.goal,
            "track": self.track,
            "section": self.section,
            "warnings": self.warnings,
        }
        if self.selector is not None:
            d["selector"] = {
                "final_facts_count": len(self.selector.final_facts),
                "converged": self.selector.converged,
                "iterations": [asdict(i) for i in self.selector.iterations],
            }
        if self.projector is not None:
            d["projector"] = {
                "section": self.projector.section_name,
                "used_fact_ids": self.projector.used_fact_ids,
                "model": self.projector.model,
                "text_preview": self.projector.text[:300],
            }
        if self.verifier_l1 is not None:
            d["verifier_l1"] = self.verifier_l1.to_json()
        if self.verifier_l2 is not None:
            d["verifier_l2"] = self.verifier_l2.to_json()
        if self.reinforce is not None:
            d["reinforce"] = {
                "scanned": self.reinforce.scanned,
                "eligible": self.reinforce.eligible,
                "pushed": self.reinforce.pushed,
                "failed": self.reinforce.failed,
            }
        return d


def run_pipeline(
    goal: str,
    *,
    track: str,
    section: SectionSpec,
    gclient,
    store=None,
    mode: str | None = None,
    selector_kwargs: dict | None = None,
    reinforce_min_trust: float = 3.0,
) -> PipelineResult:
    """mode: "sv" | "svr" | "svrr". None → env PROJECTION_MODE 또는 "svrr"."""
    mode = mode or os.environ.get("PROJECTION_MODE", "svrr")
    rep = PipelineResult(mode=mode, goal=goal, track=track, section=section.name)

    # --- Selector (+ Stage C 창발 루프 옵션) ---
    skwargs = selector_kwargs or {}
    use_emergence = os.environ.get("GP_EMERGENCE", "off").lower() in {"on", "1", "true"}
    if use_emergence:
        sel = _run_emergence_loop(goal, gclient=gclient, skwargs=skwargs, rep=rep)
    else:
        sel = run_selector(goal, gclient=gclient, **skwargs)
    rep.selector = sel
    if not sel.final_facts:
        rep.warnings.append("Selector 가 빈 fact 집합을 반환 — Projector skip")
        return rep

    # --- Stage B(3): 실시간 테마 그룹핑 ---
    # Selector 결과를 Gemma E4B 로 2~4 테마 분류 → Projector 프롬프트에 scaffold 주입.
    # 평면 기술을 테마별 구조(## 1. <라벨> ...)로 승격. `GP_THEME_GROUP=on` 에만 활성.
    section_for_proj = section
    use_theme = os.environ.get("GP_THEME_GROUP", "off").lower() in {"on", "1", "true"}
    if use_theme:
        labels = _theme_labels(goal, sel.final_facts)
        if labels:
            section_for_proj = _with_theme_scaffold(section, labels)
            rep.warnings.append(f"[theme] {len(labels)}개 테마: {labels}")
        else:
            rep.warnings.append("[theme] 테마 분류 실패 또는 fact<4 → scaffold 건너뜀")

    # --- Projector ---
    proj_in = ProjectionInput(goal=goal, track=track, facts=sel.final_facts, section=section_for_proj)
    use_panel = os.environ.get("GP_PERSONAS", "off").lower() in {"on", "1", "true"}
    if use_panel:
        proj_out = _project_panel(proj_in, track_name=track, rep=rep)
    else:
        proj_out = project_section(proj_in)
    rep.projector = proj_out

    if mode == "sv":
        return rep

    # --- Verifier L1 ---
    l1 = verify_l1(
        section_text=proj_out.text,
        facts=[
            {
                "node_id": f.get("node_id"),
                "text": f.get("text", ""),
                "attrs": {"source": f.get("namespace")},
                "created_at": None,
                "entity_kinds": [],
            }
            for f in sel.final_facts
        ],
        track=track,
    )
    rep.verifier_l1 = l1
    if not l1.pass_:
        rep.warnings.append(f"L1 실패 — retry_hint: {l1.retry_hint}")

    if mode == "svr":
        return rep

    # --- Verifier L2 (RAG cross) ---
    l2 = verify_l2_rag_cross(
        facts=[
            {"node_id": f.get("node_id"), "text": f.get("text", "")}
            for f in sel.final_facts
        ]
    )
    rep.verifier_l2 = l2
    if store is not None and l2.trust_deltas:
        try:
            apply_trust_deltas(store, l2, layer="L2")
        except Exception as exc:
            rep.warnings.append(f"apply_trust_deltas 실패: {exc}")

    # --- Reinforce ---
    if store is not None and os.environ.get("REINFORCE_ENABLED", "on").lower() in {"on", "1", "true"}:
        try:
            r = reinforce_to_gateway(store, min_trust=reinforce_min_trust)
            rep.reinforce = r
        except Exception as exc:
            rep.warnings.append(f"reinforce 실패: {exc}")

    return rep
