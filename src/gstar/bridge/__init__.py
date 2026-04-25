"""
gstar.bridge — 09-bridge (award-to-dev) 공용 도구.

jw/re 트랙 산출물을 Jira Cloud 가 직접 import 가능한 3-file CSV + 연구지시서 폴더로
분해한다. 프로젝트별 설정은 `09-bridge/bridge.yaml` 로 주입.

근본 원칙:
  1. Jira Cloud CSV import 의 Epic→Task→Sub-task 순서 제약을 따르기 위해 CSV 는 3 개로 분할.
  2. Description 은 작업자가 Jira 페이지만 보고 착수할 수 있는 깊이 (배경·목적·방법론·DoD).
  3. ID 네임스페이스 규약 (DT-E*, DT-T*, DT-ST*, DT-TPM.*, DT-TR.*, DT-TP*.*) 엄격 준수.
  4. Task Jira Key 는 import 후 확정 — Sub-task Parent 는 `refresh_subtasks` 로 후처리.

상위 참조: `docs/ops-bridge.md`, deep-tect/09-bridge/ 실사례.
"""

from gstar.bridge.config import BridgeConfig, load_config
from gstar.bridge.csv_gen import generate_csvs
from gstar.bridge.ri_scaffold import scaffold_ri

__all__ = ["BridgeConfig", "load_config", "generate_csvs", "scaffold_ri"]
