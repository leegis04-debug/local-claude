"""Plan → Execute → Verify 루프 뼈대 (plan 파일 §① 오케스트레이터 루프).

실제 LLM planner 통합은 Phase 3+ 에서 추가된다. 현재는:
- LLM 이 이미 생성한 응답(XML 태그 포함 문자열)을 받아
- 파싱 → 실행 → 결과 요약
- validate action 이 fail 시 재시도 훅(max_retries)

재시도 핵심: Phase 1 plan §⑥ "검증 실패 시 자동 재시도" 의 뼈대만 제공.
구체적 피드백 재주입·재플래닝은 외부 LLM 훅에 맡긴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import executor, parser


Planner = Callable[[str, list["executor.ExecutedAction"]], str]
"""재시도 시 이전 실행 결과를 받아 다음 LLM 응답(XML 태그 포함) 을 생성하는 훅."""


@dataclass
class LoopResult:
    executed: list[executor.ExecutedAction] = field(default_factory=list)
    attempts: int = 0
    verified: bool = False
    last_input: str = ""

    def summary(self) -> dict[str, Any]:
        return {
            "attempts": self.attempts,
            "verified": self.verified,
            "actions": [
                {"name": ex.name, "ok": ex.result.ok, "duration_ms": ex.duration_ms}
                for ex in self.executed
            ],
        }


def run(
    llm_output: str,
    *,
    project_dir: Path | str | None = None,
    gateway: Any = None,
    max_retries: int = 2,
    planner: Planner | None = None,
) -> LoopResult:
    """LLM 출력 1개로 시작해 최대 max_retries 번 재시도.

    검증 정의: validate action 이 등장했다면 그 결과를 기준. 없으면 전체 ok 여부.
    """
    result = LoopResult(last_input=llm_output)

    current = llm_output
    for attempt in range(max_retries + 1):
        result.attempts = attempt + 1
        actions = parser.extract(current)
        if not actions:
            break
        executed = executor.dispatch_all(actions, project_dir=project_dir, gateway=gateway)
        result.executed.extend(executed)

        # verify 단계: validate 또는 cross_check 우선. 없으면 전체 ok 여부.
        verify_names = {"validate", "cross_check"}
        verify_results = [ex for ex in executed if ex.name in verify_names]
        if verify_results:
            result.verified = all(ex.result.ok for ex in verify_results)
        else:
            result.verified = all(ex.result.ok for ex in executed)

        if result.verified or planner is None or attempt >= max_retries:
            break
        # 재시도 — planner 가 다음 입력을 만든다.
        try:
            current = planner(current, executed)
        except Exception:
            break
        if not current:
            break

    return result
