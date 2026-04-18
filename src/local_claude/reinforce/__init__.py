"""Phase 6 — P-Reinforce 자기개선 루프.

Karpathy LLM-Wiki + RL 정책 업데이트 개념 차용.
모델 가중치는 안 건드리고 **프롬프트·정책 파일**을 매 사이클 조정한다.

단일 호출로 돌아가는 루프:
  `lc reinforce cycle` → analyze → findings → suggestions → (선택) apply

외부 cron/n8n 없이 local-claude 안에서 자기완결.
"""

from . import analyzer, cycle, findings, policy, suggestions

__all__ = ["analyzer", "cycle", "findings", "policy", "suggestions"]
