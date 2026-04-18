"""엔티티 타입 분류기.

규칙 매트릭스 (track × pattern → EntityKind). LLM 없이 1차 분류.
애매한 경우는 EntityKind.OTHER 로 떨어지고, Phase B 에서 선택적 Ollama 보강 가능.

트랙별 규칙:
- 공통: 기관 접미사, 연도, 문서·양식
- proposal: 지표(AP/F1/mAP/%), 예산(원/만원/억), 기술
- research: 가설, 데이터셋, 방법, 실험, 결과
- coding: 파이썬 심볼(def/class/import), API 경로, 테스트
- document: 인용 패턴, 주장
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from gstar.entity.types import EntityKind, Track


_ORG_SUFFIX = re.compile(r"(공사|재단|협회|조합|기관|연구원|연구소|청|부|처|위원회|센터|학회|대학교|대학|법인|회사|주식회사|㈜|Inc\.?|Corp\.?|Ltd\.?|LLC)$")
_YEAR = re.compile(r"^(19|20|21)\d{2}(년|년도)?$|^20\d{2}-\d{2}-\d{2}$")
_TIMELINE_KW = re.compile(r"^(Q[1-4]|분기|상반기|하반기|\d+차년도|\d+개월|\d+일|FY\d{2,4})$")
_METRIC_PATTERN = re.compile(r"^(AP@?[\d.]*|mAP|F1|BLEU|ROUGE|정확도|정밀도|재현율|IoU|Top-?[1-9])$", re.IGNORECASE)
_METRIC_VALUE = re.compile(r"^\d+(\.\d+)?\s*%$")
_BUDGET = re.compile(r"(\d+(억|천만|백만|만|천)?원$|\$\d+|USD\s?\d+|예산|총\s?사업비)")

_HYPOTHESIS_KW = re.compile(r"^(가설|H[0-9]+|Hypothesis|[Hh]\d+)$")
_DATASET_KW = re.compile(r"(데이터셋|Dataset|corpus|코퍼스|벤치마크|benchmark)$", re.IGNORECASE)
_METHOD_KW = re.compile(r"(방법론|알고리즘|method|algorithm|모델|model|아키텍처|architecture)$", re.IGNORECASE)
_EXPERIMENT_KW = re.compile(r"(실험|experiment|run|시도|trial)$", re.IGNORECASE)
_RESULT_KW = re.compile(r"(결과|result|outcome|성능)$", re.IGNORECASE)

_PY_CLASS = re.compile(r"^class\s+([A-Z][A-Za-z0-9_]*)")
_PY_FUNC = re.compile(r"^(?:async\s+)?def\s+([a-z_][a-zA-Z0-9_]*)")
_PY_IMPORT = re.compile(r"^(?:from|import)\s+([a-zA-Z_][a-zA-Z0-9_.]*)")
_PY_IMPORT_TARGETS = re.compile(r"\bimport\s+([a-zA-Z_][a-zA-Z0-9_.,\s]*)")
_IDENT_CAMEL = re.compile(r"^[A-Z][a-zA-Z0-9_]{2,}$")
_IDENT_SNAKE = re.compile(r"^[a-z_][a-z0-9_]{2,}$")
_IDENT_CONST = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
_API_PATH = re.compile(r"^(GET|POST|PUT|PATCH|DELETE)\s+/|^/[a-zA-Z_][\w/{}-]*$")
_TEST_CASE = re.compile(r"^test_[a-z0-9_]+$|^[A-Z][A-Za-z0-9]*Test$|^Test[A-Z][A-Za-z0-9]*$")

_CITATION_KW = re.compile(r"^(\[[0-9]+\]|doi:|https?://|arXiv:|ISBN)", re.IGNORECASE)
_CLAIM_KW = re.compile(r"(주장|claim|명제|proposition)$", re.IGNORECASE)

_TECH_KW = re.compile(
    r"(AI|ML|딥러닝|neural|transformer|CNN|RNN|LSTM|GAN|RAG|LLM|VLM|"
    r"비전|vision|NLP|강화학습|그래프|graph)",
    re.IGNORECASE,
)
_PROJECT_KW = re.compile(r"(프로젝트|project|과제|사업|시스템)$", re.IGNORECASE)


@dataclass
class ClassifyResult:
    kind: EntityKind
    confidence: float  # 0.0~1.0
    rule: str          # 적중한 규칙 이름 (디버그용)


def classify(
    surface: str,
    context: str = "",
    track: str | Track = Track.PROPOSAL,
    *,
    use_llm: bool = False,
) -> EntityKind:
    """Surface form + 주변 컨텍스트 → EntityKind. `classify_verbose` 의 kind 만."""
    return classify_verbose(surface, context, track, use_llm=use_llm).kind


def classify_verbose(
    surface: str,
    context: str = "",
    track: str | Track = Track.PROPOSAL,
    *,
    use_llm: bool = False,
) -> ClassifyResult:
    s = surface.strip()
    t = Track(track) if isinstance(track, str) else track

    if not s:
        return ClassifyResult(EntityKind.OTHER, 0.0, "empty")

    common = _classify_common(s, context)
    if common is not None:
        return common

    if t is Track.CODING:
        r = _classify_coding(s, context)
        if r is not None:
            return r
    if t is Track.RESEARCH:
        r = _classify_research(s, context)
        if r is not None:
            return r
    if t is Track.DOCUMENT:
        r = _classify_document(s, context)
        if r is not None:
            return r
    if t in (Track.PROPOSAL, Track.RESEARCH):
        r = _classify_proposal(s, context)
        if r is not None:
            return r

    if _TECH_KW.search(s):
        return ClassifyResult(EntityKind.TECHNOLOGY, 0.5, "tech_keyword")

    if use_llm:
        llm = _classify_llm(s, context, t)
        if llm is not None:
            return llm

    return ClassifyResult(EntityKind.OTHER, 0.1, "default_other")


def _classify_common(s: str, context: str) -> ClassifyResult | None:
    if _ORG_SUFFIX.search(s):
        return ClassifyResult(EntityKind.ORG, 0.9, "org_suffix")
    if _YEAR.match(s) or _TIMELINE_KW.match(s):
        return ClassifyResult(EntityKind.TIMELINE, 0.9, "year_or_timeline")
    return None


def _classify_proposal(s: str, context: str) -> ClassifyResult | None:
    if _METRIC_PATTERN.match(s) or _METRIC_VALUE.match(s):
        return ClassifyResult(EntityKind.METRIC, 0.9, "metric_pattern")
    if _BUDGET.search(s):
        return ClassifyResult(EntityKind.BUDGET, 0.85, "budget_pattern")
    if _PROJECT_KW.search(s):
        return ClassifyResult(EntityKind.PROJECT, 0.7, "project_keyword")
    if _TECH_KW.search(s):
        return ClassifyResult(EntityKind.TECHNOLOGY, 0.6, "tech_keyword")
    return None


def _classify_research(s: str, context: str) -> ClassifyResult | None:
    if _HYPOTHESIS_KW.match(s) or "가설" in context.lower()[:50]:
        if _HYPOTHESIS_KW.match(s):
            return ClassifyResult(EntityKind.HYPOTHESIS, 0.95, "hypothesis_kw")
    if _DATASET_KW.search(s):
        return ClassifyResult(EntityKind.DATASET, 0.9, "dataset_kw")
    if _METHOD_KW.search(s):
        return ClassifyResult(EntityKind.METHOD, 0.85, "method_kw")
    if _EXPERIMENT_KW.search(s):
        return ClassifyResult(EntityKind.EXPERIMENT, 0.8, "experiment_kw")
    if _RESULT_KW.search(s):
        return ClassifyResult(EntityKind.RESULT, 0.8, "result_kw")
    return None


def _classify_coding(s: str, context: str) -> ClassifyResult | None:
    ctx_head = context[:120]
    m = _PY_CLASS.match(ctx_head)
    if m and m.group(1) == s:
        return ClassifyResult(EntityKind.CLASS, 0.98, "py_class_def")
    m = _PY_FUNC.match(ctx_head)
    if m and m.group(1) == s:
        return ClassifyResult(EntityKind.FUNCTION, 0.98, "py_func_def")
    m = _PY_IMPORT.match(ctx_head)
    if m and (s == m.group(1) or m.group(1).startswith(s + ".") or m.group(1).endswith("." + s)):
        return ClassifyResult(EntityKind.MODULE, 0.9, "py_import")
    for tm in _PY_IMPORT_TARGETS.finditer(ctx_head):
        targets = [x.strip() for x in tm.group(1).split(",")]
        if s in targets:
            return ClassifyResult(EntityKind.MODULE, 0.9, "py_import_target")
    if _API_PATH.match(s):
        return ClassifyResult(EntityKind.API_ENDPOINT, 0.9, "api_path")
    if _TEST_CASE.match(s):
        return ClassifyResult(EntityKind.TEST_CASE, 0.95, "test_case")
    if _IDENT_CONST.match(s):
        return ClassifyResult(EntityKind.VARIABLE, 0.75, "const_ident")
    if _IDENT_CAMEL.match(s):
        return ClassifyResult(EntityKind.CLASS, 0.55, "camel_ident")
    if _IDENT_SNAKE.match(s) and len(s) >= 3:
        return ClassifyResult(EntityKind.FUNCTION, 0.45, "snake_ident")
    return None


def _classify_document(s: str, context: str) -> ClassifyResult | None:
    if _CITATION_KW.search(s):
        return ClassifyResult(EntityKind.CITATION, 0.9, "citation_pattern")
    if _CLAIM_KW.search(s):
        return ClassifyResult(EntityKind.CLAIM, 0.7, "claim_keyword")
    return None


def _classify_llm(s: str, context: str, track: Track) -> ClassifyResult | None:
    """Ollama judge 로 애매한 케이스 분류. `G_ENTITY_LLM=on` 필요."""
    try:
        from gstar.selector.gemma_client import OllamaChatClient
    except Exception:
        return None
    try:
        kinds = ", ".join(k.value for k in EntityKind)
        client = OllamaChatClient()
        system = (
            "당신은 엔티티 분류기다. 주어진 surface form 과 주변 문맥을 보고 "
            f"다음 중 정확히 하나의 kind 만 답한다: {kinds}. 설명 금지. kind 만 출력."
        )
        prompt = f"Track: {track.value}\nSurface: {s}\nContext: {context[:300]}\nKind:"
        resp = client.judge(system=system, prompt=prompt).strip().lower()
        try:
            return ClassifyResult(EntityKind(resp), 0.7, "llm")
        except ValueError:
            return None
    except Exception:
        return None
