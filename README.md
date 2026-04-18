# local-claude

개인용 Claude급 시스템 (Phase 1~6 완성) — 상태 압축기·3층 메모리·성능 로깅·gateway 브릿지·5+3개 XML action·리랭커·hallucination 검증·심볼 인덱스·git diff 영향도·run_test·카테고리 TTL decay·dedup·connect-ai 도구 manifest·P-Reinforce 자기개선 루프.

기존 `~/.local/bin/jw`, `~/.local/bin/re` wrapper를 **건드리지 않고** `$PATH` 앞쪽의 shim으로 훅을 주입한다. 설치 롤백은 shell rc 한 줄 제거.

## 구성

| 모듈 | 역할 |
|------|------|
| `src/local_claude/state/` | `{프로젝트}/state.json` 읽기·갱신 (atomic write, 손상 복구, 리스트 상한) |
| `src/local_claude/memory/` | 3층 메모리(session/project/user) + 자동 기억(`ask-gemma`) + 카테고리별 TTL decay + dedup + restore + touch_count 보호 |
| `src/local_claude/perf/` | `{프로젝트}/.perf/log-YYYY-MM-DD.jsonl` JSONL append |
| `src/local_claude/infra/` | gateway httpx 클라이언트 (X-Auth-Token, `/search/hybrid`, SSH 터널 자동 fallback) + tunnel setup/down |
| `src/local_claude/actions/` | `search_rag` `search_graph` `ask_deep` `validate` `critique` `rerank` `cross_check` `run_test` 8개 XML action |
| `src/local_claude/orchestrator/` | XML 태그 파서 + action dispatcher(perf/state 자동 기록) + plan→exec→verify 루프 |
| `src/local_claude/verify/` | 3종 리랭커(noop/heuristic/llm) + claim 추출 + cross_check hallucination detector |
| `src/local_claude/code/` | 심볼 인덱스(Python ast + regex 5언어) + git diff 영향도 + unified diff patch + test_runner 자동 감지 |
| `src/local_claude/tools/` | connect-ai 호환 도구 manifest + 표준 JSON envelope runner |
| `src/local_claude/reinforce/` | P-Reinforce 루프 — perf 분석·findings·suggestions·policy 파일 버저닝·cycle |
| `src/local_claude/diagnostics.py` | `lcai status` / `lcai doctor` |
| `bin/{jw,re}` | 원본 wrapper 위임 shim — perf/state 훅 포함 |
| `bin/lcai` | `python -m local_claude.cli` 위임 (`lc`는 macOS Mono와 충돌해서 `lcai`로 명명) |

## 설치

```bash
./install.sh
# 새 터미널을 열거나 'source ~/.zshrc'
```

`install.sh`가 하는 일:
1. shim 파일에 +x 권한
2. `httpx` + `pydantic` 의존성 설치 (`python3 -m pip`)
3. `~/.zshrc`(+`~/.bashrc`)에 아래 한 줄 추가:

```sh
export PATH="<repo>/bin:$PATH"  # local-claude
```

## 제거

```bash
./uninstall.sh
```

PATH 한 줄을 제거한다. 사용자 데이터(`~/.local-claude/`)와 프로젝트별 `state.json`·`.memory/`·`.perf/`는 건드리지 않는다.

## 환경변수

| 변수 | 기본 | 용도 |
|------|------|------|
| `LC_DATA_DIR` | `~/.local-claude` | User 층 저장 루트 (policy 디렉토리 포함) |
| `LC_ASK_GEMMA` | `~/.local/bin/ask-gemma` | 자동 기억·리랭커·suggestion 용 로컬 LLM 브릿지 |
| `LC_PYTHON` | `python3` | shim이 실행할 Python |
| `ASST_TOKEN` | (`~/.config/asst/config` 폴백) | gateway 인증 `X-Auth-Token` 헤더 값 |
| `LC_GATEWAY_URL` | `http://100.79.251.53:8000` | gateway direct URL |
| `LC_GATEWAY_URL_TUNNEL` | `http://localhost:8000` | SSH 터널 통해 재시도할 URL |
| `LC_GATEWAY_TIMEOUT` | `30` | httpx 타임아웃(초) |
| `LC_SSH_HOST` | `ljw-op` | 터널 목적지 SSH 호스트 |

## CLI (모두 `lcai ...`)

### Phase 1 — 상태·메모리·성능

```bash
lcai state show                        # 현재 state.json 덤프
lcai state update --current-goal "X"   # 필드 업데이트
lcai memory recall                     # 3층 메모리 전체 조회
lcai memory remember "결정 Y" --scope project --category decision
lcai memory auto --stdin               # stdin 요약에서 자동 추출 (ask-gemma)
lcai memory decay                      # 카테고리별 TTL 적용 (기본)
lcai memory decay --days 30            # legacy 균일 모드
lcai memory dedup --threshold 0.85 [--apply]
lcai memory restore --text X | --category X
lcai perf tail -n 20                   # 오늘 로그 꼬리
```

### Phase 2 — action·gateway

```bash
lcai action list                                            # 등록된 action
lcai action parse --stdin                                   # XML 태그만 추출
lcai action run search_rag --set query="스마트서비스"        # 단일 실행
lcai action dispatch --stdin                                # LLM 응답 파싱 + 순차 실행
lcai orchestrate --stdin --max-retries 2                    # plan→exec→verify 루프

lcai infra gateway health                 # /health (실패 시 SSH 터널 자동 시도)
lcai infra gateway health --no-tunnel     # direct 만 시도
lcai infra tunnel status|up|down          # SSH 포트포워딩 제어
```

### Phase 3 — verify (리랭커 + hallucination)

```bash
lcai verify rerank --query "..." --stdin [--mode heuristic|llm|noop]
lcai verify cross-check --answer "..." --evidence-file ev.json [--mode local|llm]
```

### Phase 4 — code·test

```bash
lcai code index [--format json|paths] [--extensions .py,.ts,...]
lcai code impact --base main      # git 변경 영향도
lcai code keyfiles                # CLAUDE.md/README/pyproject 등
lcai code tree                    # tracked 파일 목록
lcai code diff a.py b.py          # unified diff
lcai test run [--cmd "..."] [--retries N]
lcai test detect                  # 감지된 테스트 명령
```

### Phase 5 — tools(connect-ai) + status·doctor

```bash
lcai tools list                   # connect-ai 가 읽을 manifest
lcai tools show search_rag        # 단일 도구 스키마
lcai tools call <name>            # stdin JSON → stdout envelope (child_process 호환)

lcai status                       # 1페이지 요약
lcai doctor                       # 토큰·SSH·gateway·파일 헬스체크
```

### Phase 6 — P-Reinforce 자기개선

```bash
lcai reinforce analyze [--days 7]
lcai reinforce findings
lcai reinforce suggest [--no-llm]
lcai reinforce cycle [--apply]    # analyze→findings→suggest→기록

lcai policy list
lcai policy show <name>
lcai policy set <name>            # stdin 또는 --file
lcai policy revisions <name>
lcai policy diff <name> <rev>
lcai policy rollback <name> <rev>
```

## 지원 XML action

| 태그 | 백엔드 | 비고 |
|------|--------|------|
| `<search_rag query="..." top_k="5"/>` | gateway `/search/hybrid` | `govsupport` 기본 제외 |
| `<search_graph>MATCH ... RETURN n</search_graph>` | gateway `/graph/query` | 파괴적 cypher 차단 |
| `<ask_deep rag="true">프롬프트</ask_deep>` | `ask-gemma --deep` | 4090 a4b |
| `<validate tool="validate_step">본문</validate>` | gateway `/mcp/jw/{tool}` | MCP jw-validator |
| `<critique tool="critique_thought">생각</critique>` | gateway `/mcp/doc/{tool}` | MCP doc-thinking |
| `<rerank query="Q">[{"text":"..."}]</rerank>` | 로컬 heuristic / LLM | 근거 재정렬 |
| `<cross_check mode="local">{"answer":"...","evidences":[...]}</cross_check>` | 로컬 / LLM | hallucination 판정 |
| `<run_test cmd="pytest">/</run_test>` | test_runner 자동 감지 | 재시도 지원 |

## connect-ai 연동

connect-ai(Antigravity extension) 관점에서:

```typescript
// 도구 목록 (초기 레지스트리)
const manifest = JSON.parse(execSync('lcai tools list').toString());

// 도구 호출
const result = JSON.parse(
  execSync('lcai tools call search_rag', {
    input: JSON.stringify({ query: '...', top_k: 5 })
  }).toString()
);
// result = { ok, name, output, error, meta, duration_ms }
```

## 테스트

```bash
python3 -m pytest
```

## 추가 모듈

| 모듈 | 역할 | 문서 |
|------|------|------|
| `src/gstar/` | G — 지식 항성 모델 (entity-centric RAG + integrity + selector loop) | [src/gstar/README.md](src/gstar/README.md) |
| `src/ctx/` | 통합 location/context 스위치 (집·회사·오프라인) + SwiftBar 상단바 | [src/ctx/README.md](src/ctx/README.md) |
