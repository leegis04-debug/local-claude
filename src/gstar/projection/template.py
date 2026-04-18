"""섹션 스키마 데이터.

트랙별 단계별 섹션 구성. `TaskDefinition.section_schema()` 가 사용.
jw wrapper 가 하드코딩하던 섹션 매트릭스(jw_wrapper_facts 메모리)를 선언적으로 관리.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SectionSpec:
    id: str                          # "proposal_background", "spec_api", ...
    title: str                       # "추진배경" 등 출력 헤더
    guide: str                       # Ollama 에게 줄 지시문
    min_words: int = 300
    max_words: int = 3000
    required_fact_kinds: list[str] = field(default_factory=list)
    required_entity_kinds: list[str] = field(default_factory=list)


PROPOSAL_8: list[SectionSpec] = [
    SectionSpec(
        id="background",
        title="추진배경",
        guide="본 사업의 필요성·시장 맥락·기존 한계를 서술. 수치 근거 포함.",
        required_fact_kinds=["decision", "constraint"],
    ),
    SectionSpec(
        id="goals_kpi",
        title="목표 및 KPI",
        guide="정량 목표 (지표·수치) 와 정성 목표를 명시.",
        required_fact_kinds=["metric"],
        required_entity_kinds=["metric"],
    ),
    SectionSpec(
        id="development",
        title="개발 내용",
        guide="핵심 개발 요소·기술 스택·모듈별 기능을 구체 서술.",
        required_entity_kinds=["technology"],
    ),
    SectionSpec(
        id="organization",
        title="추진체계",
        guide="참여 기관·연구원·역할 분담을 명시.",
        required_entity_kinds=["person", "org"],
    ),
    SectionSpec(
        id="commercialization",
        title="사업화·확산",
        guide="시장 진입 전략·확산 경로·생태계 효과.",
    ),
    SectionSpec(
        id="budget",
        title="예산",
        guide="항목별 예산·총사업비 내역.",
        required_entity_kinds=["budget"],
    ),
    SectionSpec(
        id="outcome",
        title="기대효과",
        guide="경제적·기술적·사회적 효과 정량 추정.",
    ),
    SectionSpec(
        id="risk",
        title="위험관리",
        guide="주요 리스크·완화 방안·컨틴전시.",
    ),
]


SPEC_8: list[SectionSpec] = [
    SectionSpec(id="overview", title="기술개요", guide="핵심 기술 요약과 위치", max_words=2000),
    SectionSpec(id="architecture", title="아키텍처", guide="컴포넌트 다이어그램과 데이터 흐름"),
    SectionSpec(id="core", title="핵심기술", guide="알고리즘·모델 구조 상세"),
    SectionSpec(id="api", title="API 명세", guide="엔드포인트·입출력 스키마", required_entity_kinds=["api_endpoint"]),
    SectionSpec(id="dataflow", title="데이터 흐름", guide="단계별 데이터 변환"),
    SectionSpec(id="performance_kpi", title="성능 · KPI", guide="목표 지표·측정 조건", required_fact_kinds=["metric"]),
    SectionSpec(id="validation", title="검증·테스트", guide="테스트 케이스·평가 방법"),
    SectionSpec(id="dependencies", title="의존성·제약", guide="외부 의존성·운영 제약"),
]


STRUCTURE_7: list[SectionSpec] = [
    SectionSpec(id="problem", title="문제 정의", guide="해결 대상 문제 구체화"),
    SectionSpec(id="hypothesis", title="가설", guide="검증 가능한 가설 진술", required_entity_kinds=["hypothesis"]),
    SectionSpec(id="objectives", title="목표·산출물", guide="구체 목표와 산출물"),
    SectionSpec(id="method", title="방법론", guide="접근·알고리즘·프로세스", required_entity_kinds=["method"]),
    SectionSpec(id="validation", title="검증 계획", guide="실험·평가 설계"),
    SectionSpec(id="risks", title="리스크", guide="예상 리스크 및 완화"),
    SectionSpec(id="schedule", title="일정·체계", guide="타임라인·마일스톤"),
]


FINAL_DOC_8: list[SectionSpec] = [
    SectionSpec(id="summary", title="Executive Summary", guide="1페이지 요약", max_words=800),
    SectionSpec(id="context", title="배경·목적", guide="과제 맥락·의의"),
    SectionSpec(id="progress", title="추진 경과", guide="단계별 진행"),
    SectionSpec(id="achievements", title="핵심 성과", guide="정량 성과 중심", required_fact_kinds=["metric"]),
    SectionSpec(id="tech_contrib", title="기술 기여", guide="기술적 독창성"),
    SectionSpec(id="commercialization", title="사업화·확산", guide="후속 확산"),
    SectionSpec(id="conclusion", title="결론·시사점", guide="교훈·후속 과제"),
    SectionSpec(id="appendix", title="부록", guide="상세 자료"),
]


IDEA_3: list[SectionSpec] = [
    SectionSpec(id="context", title="맥락", guide="아이디어 배경", max_words=800),
    SectionSpec(id="core", title="핵심 가치", guide="1-2 문장 가치 제안"),
    SectionSpec(id="next", title="다음 단계", guide="확인 필요 사항"),
]


DEBATE_4: list[SectionSpec] = [
    SectionSpec(id="for", title="찬성 논거", guide="3개 이상 논거"),
    SectionSpec(id="against", title="반대 논거", guide="3개 이상 논거"),
    SectionSpec(id="synthesis", title="종합", guide="타협·방향"),
    SectionSpec(id="open_questions", title="미결 질문", guide="해결 안 된 이슈"),
]


RESEARCH_9: list[SectionSpec] = [
    SectionSpec(id="hypothesis", title="가설", guide="검증 대상", required_entity_kinds=["hypothesis"]),
    SectionSpec(id="method", title="방법", guide="실험 프로토콜", required_entity_kinds=["method"]),
    SectionSpec(id="dataset", title="데이터", guide="사용 데이터셋", required_entity_kinds=["dataset"]),
    SectionSpec(id="experiment", title="실험", guide="실행 조건", required_entity_kinds=["experiment"]),
    SectionSpec(id="results", title="결과", guide="정량 결과", required_entity_kinds=["result"]),
    SectionSpec(id="discussion", title="논의", guide="해석·한계"),
    SectionSpec(id="related_work", title="관련 연구", guide="비교 대상"),
    SectionSpec(id="future", title="후속 연구", guide="다음 단계"),
    SectionSpec(id="references", title="참고문헌", guide="인용 목록", required_entity_kinds=["citation"]),
]


CODING_STAGES: dict[str, list[SectionSpec]] = {
    "explore": [
        SectionSpec(id="map", title="코드 지도", guide="관련 파일·심볼 요약"),
        SectionSpec(id="patterns", title="기존 패턴", guide="따를 관례·기존 유틸"),
        SectionSpec(id="open_questions", title="불확실성", guide="확인 필요"),
    ],
    "plan": [
        SectionSpec(id="files", title="변경 파일", guide="신규·수정 파일 리스트"),
        SectionSpec(
            id="signatures",
            title="함수·클래스 시그니처",
            guide="추가·변경 시그니처",
            required_entity_kinds=["function", "class"],
        ),
        SectionSpec(id="dependencies", title="의존성", guide="새 import·패키지"),
        SectionSpec(id="tests", title="테스트 계획", guide="검증 케이스"),
    ],
    "implement": [
        SectionSpec(id="code", title="코드", guide="파일별 diff 또는 전체 내용", max_words=8000),
    ],
    "test": [
        SectionSpec(id="cases", title="테스트 케이스", guide="pytest 스켈레톤", required_entity_kinds=["test_case"]),
    ],
    "review": [
        SectionSpec(id="type_check", title="타입 일관성", guide="시그니처 vs 구현 일치"),
        SectionSpec(id="import_check", title="Import 유효성", guide="경로 검증"),
        SectionSpec(id="coverage", title="테스트 커버리지", guide="미커버 심볼"),
    ],
}


DOCUMENT_STAGES: dict[str, list[SectionSpec]] = {
    "outline": [
        SectionSpec(id="goal", title="목표·독자", guide="글의 목적·타겟"),
        SectionSpec(id="sections", title="섹션 구성", guide="5-10개 섹션 제목"),
        SectionSpec(id="claims", title="핵심 주장", guide="주요 claim 목록", required_entity_kinds=["claim"]),
    ],
    "draft": [
        SectionSpec(id="body", title="본문", guide="outline 따라 섹션별 작성", max_words=6000),
    ],
    "revise": [
        SectionSpec(id="polish", title="문장 개선", guide="어색한 표현·문단 순서"),
        SectionSpec(id="citations", title="인용 보강", guide="주장 → 인용 연결", required_entity_kinds=["citation"]),
    ],
    "finalize": [
        SectionSpec(id="final", title="최종본", guide="포맷 정돈·abstract 추가"),
    ],
}


ALL_SCHEMAS: dict[str, list[SectionSpec]] = {
    "proposal_8": PROPOSAL_8,
    "spec_8": SPEC_8,
    "structure_7": STRUCTURE_7,
    "final_doc_8": FINAL_DOC_8,
    "idea_3": IDEA_3,
    "debate_4": DEBATE_4,
    "research_9": RESEARCH_9,
}


def get_schema(name: str) -> list[SectionSpec]:
    if name in ALL_SCHEMAS:
        return list(ALL_SCHEMAS[name])
    raise KeyError(f"unknown schema: {name}")
