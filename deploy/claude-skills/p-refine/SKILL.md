---
name: p-refine
description: |
  P축 (gjw/gre/gcode/gdoc) Gemma svrr 초안을 Claude 가 정제하고 그 과정을 G 에 로그로 남겨
  Gemma Selector 가 다음 번 'Claude 스타일'로 작성하도록 학습시키는 후속 파이프라인.
  자동 트리거: 사용자가 `!gjw <stage> "..."` `!gre ...` `!gcode ...` `!gdoc ...` 실행 직후,
  또는 "분량 작다", "다듬어줘", "이어서 Claude 로 정제", "초안 업데이트", "이거 G 에 가르쳐줘",
  "refine", "p-refine" 발화. gjw 자체 실행은 bin/gjw shell binary (Bash tool) 담당이며
  이 skill 은 그 결과를 받는 다음 단계.
---

# /p-refine — Gemma 초안 → Claude 정제 + G 학습 루프

## 역할

P축 shim (`gjw`/`gre`/`gcode`/`gdoc`) 이 Gemma svrr 로 생성한 초안을 Claude 가 정제·확장하고,
그 전후 diff 와 절차를 G 에 기록. 축적된 데이터는 worker procedure miner (Phase G6) 가
`procedure` 노드로 승격 → Selector (Phase G7) 가 다음 svrr 루프에서 retrieval → **Gemma 가
Claude 의 정제 패턴을 재현**. 이 루프가 돌수록 Claude 개입 없이도 충분한 결과가 나옴.

기존 `/g-trace` 는 일반 trace 기록용. `/p-refine` 은 **P축 파이프라인 전용 고수준 래퍼** —
초안 로드 + G RAG + Claude 정제 + 원본 보존 + G 로그 4종을 자동화.

## 실행 조건

사용자 입력에 하나 이상 해당:
- `!gjw`·`!gre`·`!gcode`·`!gdoc` 실행 직후 (Bash 출력에 `elapsed:` + `run_id:` + `output:` 포함)
- "분량 작다" / "부족하다" / "다듬어줘" / "이거 Claude 로 정제" / "p-refine" / "refine"
- 명시적 slash: `/p-refine <project-dir> <stage>`

gjw 가 실행되지 않았는데 대상 파일이 없으면 skill 중단. "먼저 `!gjw <stage> '...' --mode svrr` 를 실행하세요" 안내.

## 단계

### 1. 대상 산출물 확인

```
<project-dir>/NN-<stage>/stage.md                 # classic
<project-dir>/NN-<stage>/_mode_svrr/stage.md      # svrr 모드 (우선)
<project-dir>/NN-<stage>/_mode_svrr/report.json   # svrr 메타
```

없으면 classic 산출물 fallback. 둘 다 없으면 skill 종료.

### 2. G 근거 수집 (정제 재료)

```bash
G_URL="${GSTAR_SERVER_URL:-http://100.79.251.53:9999}"
PROJECT_ID="<project-dir 마지막 segment>"   # e.g. agri-food-ai-gjw
QUERY="<stage goal> + <user-input keywords> + <domain>"

# 프로젝트 namespace 우선 검색
curl -sf -X POST "$G_URL/search/hybrid" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg q "$QUERY" --arg ns "$PROJECT_ID" '{query:$q, top_k:10, namespace:$ns}')"

# 넓은 컨텍스트도 fused 로
curl -sf -X POST "$G_URL/search/fused" \
  -H "Content-Type: application/json" \
  -d "$(jq -n --arg q "$QUERY" '{query:$q, top_k:8, use_gateway:true, use_g:true}')"
```

상위 5~10 hit 의 text·source·score 를 Claude 컨텍스트에 반영. 동일 stage 의
`_versions/*.md` 가 있으면 최신본도 참고.

### 3. Claude 정제 원칙

각 섹션을 다음 7가지 기준으로 보강:

| 기준 | 동작 |
|---|---|
| 구체 수치 | "크다/심각" 같은 형용어 → 출처 있는 수치 (YoY, %, 원) |
| KPI | 누락 정량 지표 삽입 (RMSE, MAPE, 도입률, 비용절감 %) |
| 모듈 구조 | 기술 섹션에 L2-1~L2-5 같은 기존 아키텍처 배치 적용 |
| TRL 현황 | 현 TRL + 목표 TRL + 근거 (G 발췌 인용) |
| 정책 연계 | 공고문 사업 목적 문구와 1:1 매핑 |
| 리스크 맵 | risk-check-v* 에서 상위 3~5 리스크 요약 |
| 인용 해시 | 본문 끝 `<!-- citations: <hash>, <hash> -->` 유지·보강 |

**파괴적 수정 금지**: Gemma 초안의 **문장을 삭제하지 말고 보강**. 대체 불가피 시 원문을
인용 블록 (`> 원본:`) 으로 남기고 정제본 바로 아래 배치.

### 4. 저장

```bash
# 원본 버전 보존
STAGE_DIR="<project>/NN-<stage>"
VER_DIR="$STAGE_DIR/_versions"
mkdir -p "$VER_DIR"
N=$(ls "$VER_DIR"/*.md 2>/dev/null | wc -l | tr -d ' ')
NEXT=$((N+1))
cp "$STAGE_DIR/stage.md" "$VER_DIR/stage-v${NEXT}-gemma.md"

# Write 정제본을 stage.md 로 덮어쓰기
```

### 5. G 로그 3종

#### (a) 업데이트 요약 — `/notes`

```bash
curl -sf -X POST "$G_URL/notes" \
  -H "Content-Type: application/json" \
  -d "$(jq -n \
    --arg proj "$PROJECT_ID" \
    --arg stage "$STAGE" \
    --arg before "$BEFORE_BYTES" \
    --arg after "$AFTER_BYTES" \
    --arg sections "$ADDED_SECTIONS" \
    --arg cites "$CITED_SOURCES" \
    '{
      text: "[p-refine] \($proj)/\($stage): Gemma 초안 \($before)B → Claude 정제 \($after)B. 추가 섹션: \($sections). 근거: \($cites)",
      source: "claude-p-refine",
      tags: ["p-refine", $stage, $proj],
      namespace: "personal_notes"
    }')"
```

#### (b) 절차 trace — `/trace/record` (bracket open → decisions → close)

```bash
TASK_ID="$(python3 -c 'import ulid; print(str(ulid.ULID()))')"

# open
curl -sf -X POST "$G_URL/trace/record" -H "Content-Type: application/json" \
  -d "$(jq -n --arg tid "$TASK_ID" --arg d "p-refine $PROJECT_ID/$STAGE: Gemma→Claude 정제" \
        '{source:"skill",bracket_phase:"open",task_id:$tid,description:$d}')"

# decision: 근거 선택
curl -sf -X POST "$G_URL/trace/record" -H "Content-Type: application/json" \
  -d "$(jq -n --arg tid "$TASK_ID" --arg d "G 근거 $NHITS 건 채택 ($TOP_SOURCES)" \
        '{source:"skill",bracket_phase:"decision",task_id:$tid,description:$d}')"

# decision: 추가 섹션
curl -sf -X POST "$G_URL/trace/record" -H "Content-Type: application/json" \
  -d "$(jq -n --arg tid "$TASK_ID" --arg d "추가 섹션 $ADDED_SECTIONS, KPI $KPI_COUNT 건" \
        '{source:"skill",bracket_phase:"decision",task_id:$tid,description:$d}')"

# close
curl -sf -X POST "$G_URL/trace/record" -H "Content-Type: application/json" \
  -d "$(jq -n --arg tid "$TASK_ID" --arg d "정제 완료 $BEFORE_BYTES→$AFTER_BYTES bytes" \
        '{source:"skill",bracket_phase:"close",task_id:$tid,description:$d}')"
```

MCP g-trace 서버 (port 8767) 살아있으면 `mcp__g-trace__g_trace_bracket_open` 등 tool 로
대체 호출 가능. 우선순위: **MCP tool > curl**.

#### (c) Diff 자체를 G 에 ingest (선택, `P_REFINE_DIFF_INGEST=on` 시)

```bash
# Gemma 원본 vs Claude 정제본 diff 를 별도 노드로
diff -u "$VER_DIR/stage-v${NEXT}-gemma.md" "$STAGE_DIR/stage.md" > /tmp/p-refine-diff.md
curl -sf -X POST "$G_URL/notes" -H "Content-Type: application/json" \
  -d "$(jq -n --rawfile diff /tmp/p-refine-diff.md --arg tid "$TASK_ID" \
    '{text:$diff, source:"claude-p-refine-diff", tags:["p-refine-diff",$tid], namespace:"agri-food-ai-gjw"}')"
```

이 diff 가 Selector 의 procedure retrieval 재료로 **"Gemma 이렇게 썼을 때 Claude 는 이렇게 고쳤다"** 패턴 학습의 핵심 원천.

### 6. 사용자 보고

한 블록으로:

```
p-refine 완료
├─ 대상: <project>/NN-<stage>/stage.md
├─ 원본: _versions/stage-v<N>-gemma.md (Gemma svrr, <before>B)
├─ 결과: stage.md (<after>B, +<delta>B)
├─ 추가 섹션: <list>
├─ G 근거: <N> 건 편입 (<top sources>)
└─ G 로그: task_id=<TID>, notes=OK, diff=<ingest|skip>
```

## 환경변수

| 변수 | 기본 | 역할 |
|---|---|---|
| `GSTAR_SERVER_URL` | `http://100.79.251.53:9999` | G 서버 (SSH 터널 시 `localhost:9999`) |
| `P_REFINE_DIFF_INGEST` | `off` | diff 자체를 G 노드로 적재 (학습 풍부도 ↑, DB 크기 ↑) |
| `P_REFINE_DRY_RUN` | `off` | 실제 파일 저장 안 하고 미리보기만 |
| `P_REFINE_NS` | `<project_id>` | diff ingest 시 namespace override |

## 주의사항

- **원본 보존 필수** — `_versions/stage-v<N>-gemma.md` 없이는 Selector 가 "Gemma → Claude" 델타를
  학습할 수 없음. 버전 덮어쓰지 말 것.
- **Gemma 의도 존중** — 초안을 통째로 교체하면 학습 데이터 오염. 보강이 원칙.
- **G 서버 다운 시** — 파일 저장은 수행, G 로그만 스킵. 사용자에게 경고 출력.
- **MCP g-trace 서버 우선** — curl 보다 MCP tool 이 신뢰도 높음. 단 Ollama/4090 안정성 조건은
  gjw 실행 시 확인이므로 p-refine 은 해당 없음.

## 재사용 패턴

- 한 stage 의 v1 (Gemma) → v2 (Claude) → v3 (Gemma re-svrr with procedure retrieval)
  → v4 (Claude 확인만) 루프. 점진적으로 Claude 개입 ↓.
- `gjw idea "..." --mode svrr` 직후 자동 `/p-refine` 호출을 전역 user 의도로 설정:
  사용자가 "이어서 정제" "다듬어" 한 마디만 해도 skill 자동 발동.
