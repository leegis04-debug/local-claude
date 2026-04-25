# 패턴 D: 공격/방어 병렬

## 용도
품질 검증용. 모든 단계에서 사용 가능. 3라운드 순차+병렬 혼합.

## 라운드 구성

### 라운드 1: 작성 (단독)
| 세션명 | profile | 역할 |
|--------|---------|------|
| {project}-author | jw-writer | 산출물을 최선으로 작성 |

### 라운드 2: 검증 (병렬)
| 세션명 | profile | 역할 |
|--------|---------|------|
| {project}-reviewer | jw-critic | 논리적 허점 탐지 |
| {project}-reality | jw-analyst | 현실성·비용·일정 검증 |

### 라운드 3: 수정 (단독)
| 세션명 | profile | 역할 |
|--------|---------|------|
| {project}-rewriter | jw-integrator | 피드백 반영 수정안 작성 |

## 실행 절차
```bash
cd projects/{project}
claude-squad

# [라운드 1] Author
# n → jw-writer → N: "산출물을 최선으로 작성하라."
# 완료 후 c → s → q
# git merge worktree-{project}-author --no-ff

# [라운드 2] Reviewer + Reality 동시
claude-squad
# n → jw-critic → N: "Author 산출물의 논리적 허점을 찾아라."
# n → jw-analyst → N: "현실성, 비용, 일정 관점에서 검증하라."
# 완료 후 c → s → q
# git merge 2개 브랜치

# [라운드 3] Rewriter
claude-squad
# n → jw-integrator → N: "피드백을 반영하여 수정안을 작성하라."
# 완료 후 c → s → q
# git merge
```

## 주의사항
- Squad는 모든 에이전트가 동시 작업하므로 순차 의존이 있는 이 패턴은 라운드를 나눠야 함
- 라운드 1 커밋 → 라운드 2 에이전트가 그 결과를 읽음 → 라운드 3 통합
- 라운드 2의 두 에이전트는 동시 실행 가능 (서로 다른 파일에 작성)
