"""connect-ai 가 호출하는 도구 매니페스트 + 표준 JSON 실행 프로토콜.

connect-ai(Antigravity extension) 관점:
  1. `lc tools list` 로 사용 가능한 도구와 입력 스키마 획득.
  2. 필요 시 `lc tools show <name>` 로 단일 도구 상세 조회.
  3. `lc tools call <name>` 를 child_process 로 spawn 하고 stdin 에 JSON payload,
     stdout 에서 표준 envelope {ok, output, error, meta, duration_ms} 수신.

이 패키지는 기존 `actions/` 레지스트리를 thin wrap — 새 로직 추가 없이
connect-ai 친화적 인터페이스만 제공한다.
"""

from . import manifest, runner

__all__ = ["manifest", "runner"]
