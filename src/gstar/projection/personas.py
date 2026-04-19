"""페르소나 패널 — jw wrapper 의 _wip/{analyst,strategist,architect,critic,writer} 패턴 이식.

각 단계가 복수 관점에서 드래프트 → critic 이 통합. Claude jw 품질의 핵심 원천.

단계별 패널 구성:
- idea: analyst (문제분석) → strategist (기회포착) → architect (기술접근) → critic (검토)
- debate: 찬반 + synthesis 는 이미 섹션 스키마가 처리. persona 불필요.
- structure / spec / risk-check / experiment-plan: analyst + architect + critic
- proposal / final-doc: analyst + strategist + architect + critic + writer(통합)

`GP_PERSONAS=off` 로 비활성 (기본). 활성 시 섹션당 호출 수 3-5배 → 시간 증가.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PersonaSpec:
    role: str              # "analyst" | "strategist" | "architect" | "critic" | "writer"
    system_prompt: str
    max_words: int = 1500


_ROLE_PROMPTS: dict[str, str] = {
    "analyst": (
        "당신은 문제 분석가다. 주어진 섹션 주제에 대해 시장·경쟁·기술적 현황을 "
        "구체 수치와 출처 근거로 분석한다. 가정이 아닌 사실, 일반론이 아닌 구체, "
        "'~할 것이다'가 아닌 '~다' 로 단정적 서술."
    ),
    "strategist": (
        "당신은 전략가다. 분석된 문제·기회를 시장 진입·확산 관점에서 재구성한다. "
        "타겟 세그먼트·경쟁 우위·수익 모델·확산 경로를 단계별로 제시."
    ),
    "architect": (
        "당신은 기술 아키텍트다. 시스템·컴포넌트·데이터 흐름·API 를 구체 설계한다. "
        "TRL·지표·검증 방법을 정량적으로 명시."
    ),
    "critic": (
        "당신은 비판적 reviewer다. 과장·모호성·논리 비약·수치 불일치·양식 미커버 "
        "영역을 지적한다. 각 문제에 해결 방안까지 제시."
    ),
    "writer": (
        "당신은 최종 통합 writer 다. 위 관점들을 하나의 일관된 서사로 통합한다. "
        "장황함 제거, 핵심 유지, 심사위원 관점에서 설득력 극대화."
    ),
}


_STAGE_PANEL: dict[str, list[str]] = {
    "idea": ["analyst", "strategist", "architect", "critic"],
    "debate": [],
    "structure": ["analyst", "architect", "critic"],
    "spec": ["architect", "analyst", "critic"],
    "risk-check": ["critic", "analyst"],
    "experiment-plan": ["architect", "analyst", "critic"],
    "proposal": ["analyst", "strategist", "architect", "critic", "writer"],
    "final-doc": ["writer", "analyst", "critic"],
}


def panel_for(stage: str) -> list[PersonaSpec]:
    roles = _STAGE_PANEL.get(stage, [])
    return [PersonaSpec(role=r, system_prompt=_ROLE_PROMPTS[r]) for r in roles]


def integration_role() -> PersonaSpec:
    """critic + writer 통합용. panel 마지막에 실행."""
    return PersonaSpec(
        role="integrator",
        system_prompt=(
            "당신은 통합 편집자다. 여러 페르소나의 드래프트를 하나의 섹션으로 합친다. "
            "각 관점의 강점을 유지하고, 중복·충돌을 제거한다. 심사위원 관점에서 최종본."
        ),
        max_words=3000,
    )
