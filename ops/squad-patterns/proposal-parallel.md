# 패턴 B: 사업계획서 병렬

## 용도
제안 단계에서 5방향 동시 작성 + 심사 대응 검증

## 에이전트 구성
| 세션명 | profile | 역할 | 출력 파일 |
|--------|---------|------|----------|
| {project}-bg | jw-writer | 추진배경·문제 심각성 | 05-proposal/background.md |
| {project}-tech | jw-architect | 기술개발 내용·시스템 구성 | 05-proposal/tech-content.md |
| {project}-kpi | jw-analyst | 정량 목표·KPI 설계 | 05-proposal/kpi-targets.md |
| {project}-effect | jw-writer | 기대효과·사업화 전략 | 05-proposal/expected-effects.md |
| {project}-defense | jw-critic | 심사평가 기준 공격 | 05-proposal/review-defense.md |

## 실행 절차
```bash
cd projects/{project}
claude-squad

# TUI에서 5개 세션 생성 (각각 N으로 프롬프트 입력)
# 모든 에이전트 완료 후: c → s → q
```

## 통합 절차
```bash
# 1. 5개 브랜치 순차 merge
git merge worktree-{project}-bg --no-ff
git merge worktree-{project}-tech --no-ff
git merge worktree-{project}-kpi --no-ff
git merge worktree-{project}-effect --no-ff
git merge worktree-{project}-defense --no-ff

# 2. critic의 review-defense.md를 기반으로 수정 필요성 판단
# 3. integrator로 proposal-final.md 통합
claude-squad
# n → jw-integrator → "05-proposal/의 5개 파일을 읽고 proposal-final.md로 통합. review-defense.md의 지적 반영."

# 4. /risk-check 실행하여 최종 검증
```

## 주의사항
- 5개 에이전트는 각각 다른 파일 작성 → merge 충돌 없음
- critic(review-defense)은 edit 권한 없음 → 검토만 수행
- 통합 시 critic 피드백을 반드시 반영
- proposal-final.md 작성 전에 현재 버전을 _archive/에 보관
