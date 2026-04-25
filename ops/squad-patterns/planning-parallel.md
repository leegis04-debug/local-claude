# 패턴 A: 기획 병렬

## 용도
아이디어·토론 단계에서 3방향 동시 탐색

## 에이전트 구성
| 세션명 | profile | 역할 | 출력 파일 |
|--------|---------|------|----------|
| {project}-problem | jw-analyst | 문제 존재·규모·원인 | 01-idea/problem-definition.md |
| {project}-market | jw-strategist | 시장 수요·경쟁·타이밍 | 01-idea/market-demand.md |
| {project}-tech | jw-architect | 기술 가능성·아키텍처 | 01-idea/tech-feasibility.md |

## 실행 절차
```bash
cd projects/{project}
claude-squad

# TUI에서:
# n → jw-analyst → N: "01-idea/problem-definition.md에 [주제]의 문제를 분석하라"
# n → jw-strategist → N: "01-idea/market-demand.md에 [주제]의 시장을 분석하라"
# n → jw-architect → N: "01-idea/tech-feasibility.md에 [주제]의 기술 가능성을 분석하라"

# 각 에이전트 완료 후: c → s
# TUI 종료: q
```

## 통합
```bash
git merge worktree-{project}-problem --no-ff
git merge worktree-{project}-market --no-ff
git merge worktree-{project}-tech --no-ff

claude-squad
# n → jw-integrator → "01-idea/의 세 파일을 읽고 idea-canvas.md로 통합하라"
```

## 주의사항
- 세 에이전트가 각각 다른 파일을 쓰므로 merge 충돌 거의 없음
- 통합 후 idea-canvas.md의 품질 게이트 확인
