# 패턴 C: 구현 병렬

## 용도
수주 후 구현 단계에서 4방향 동시 설계

## 에이전트 구성
| 세션명 | profile | 역할 | 출력 파일 |
|--------|---------|------|----------|
| {project}-code | jw-architect | 코드 구조·저장소 설계 | 06-implementation/code-structure.md |
| {project}-data | jw-analyst | 데이터 파이프라인·입출력 | 06-implementation/data-pipeline.md |
| {project}-test | jw-critic | 테스트·실험 프로토콜 | 06-implementation/test-protocol.md |
| {project}-doc | jw-writer | 구현 문서·변경 관리 | 06-implementation/dev-docs.md |

## 실행 위치
```bash
# 구현은 dev/ 디렉토리의 별도 git repo에서 수행
cd dev/{project}
claude-squad
```

## 주의사항
- 구현 단계에서는 GSD가 활성화될 수 있음
- dev/ 디렉토리에서 /gsd-new-project로 초기화 후 사용
- 이 패턴은 설계 문서 작성용. 실제 코드 구현은 Claude Code 본체로 수행
