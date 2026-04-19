"""G 백그라운드 워커 — Phase B1.

주기 tick:
  1. community detection (Louvain) — entity graph 재클러스터링
  2. stellar_packer — fact 재패킹 (clusters)
  3. reinforce cycle — 시행착오 학습 (옵션, 존재 시)
  4. stats 집계

외부 API:
  cycle.run(store, faiss, embedder=None) -> TickReport   # 한 번 실행
  cli (python -m gstar.worker.cli) — 미니 PC 컨테이너에서 상주

Pause/Resume (B2):
  worker.paused 파일 (GSTAR_HOME/worker.paused) 존재 시 tick 시작 직전에 skip.
"""

from gstar.worker.cycle import TickReport, run_cycle

__all__ = ["TickReport", "run_cycle"]
