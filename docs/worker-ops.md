# Worker 운영 가이드 (Phase H10 이후)

`g-serve` (100.79.251.53:9999) 의 백그라운드 worker 를 tick 과 개별 엔드포인트로
나눠 운용하는 방법. 이전에는 `/worker/tick` 하나가 community·procedures·mirror·
bridge·code_repos 를 전부 돌렸고, 무거운 step 하나가 터지면 전체가 같이 죽었다.
지금은 가벼운 tick + 무거운 step 개별 호출 조합.

## 1. 한 줄 요약

| 용도 | 호출 |
|---|---|
| 주기 tick (가벼움, 2분 내) | `POST /worker/tick {}` |
| Qdrant 파생뷰 동기화 | `POST /worker/qdrant_mirror {"limit":1000}` |
| Neo4j 파생뷰 동기화 | `POST /worker/neo4j_mirror {"limit_nodes":200}` |
| legacy Gateway coarse 연결 | `POST /worker/legacy_bridge {"limit":500}` |
| legacy chunk → G fact 매칭 | `POST /worker/legacy_fact {"per_tick":200}` |
| NAS code_repos 점진 이관 | `POST /worker/code_repos {"per_tick":50}` |
| 이전 버전 호환 (전체 실행) | `POST /worker/tick {"steps":["*"]}` |
| 일시정지 / 재개 / 상태 | `POST /worker/{pause,resume}` · `GET /worker/status` |

## 2. `/worker/tick` 동작

```jsonc
// req
{
  "steps": null,              // 기본: LIGHT_STEPS = community + procedures
  "min_community_size": 3,
  "project_ids": null,        // null → 전체
  "mode": "per_project"       // or "global"
}
```

- `steps=null` → community + procedures 만. duration ~100ms, crash 위험 낮음.
- `steps=["*"]` → env 플래그 하에 모든 step 실행 (이전 버전 동일 동작).
- `steps=["community"]` → 명시한 것만. 가상 step 이름: `community`, `procedures`,
  `qdrant_mirror`, `neo4j_mirror`, `legacy_bridge`, `legacy_fact_bridge`, `code_repos`.

응답 `steps._requested` 로 실제 실행 대상 확인 가능.

## 3. 개별 heavy 엔드포인트

### `/worker/qdrant_mirror`
G fact/evidence/section 노드를 Qdrant `g_mirror` collection 으로 복제. 중복은
`derived_view` 테이블이 멱등성 보장. `limit` 미지정 시 env `QDRANT_MIRROR_LIMIT`
(기본 1000) 사용.

### `/worker/neo4j_mirror`
G entity/edge 를 Neo4j `GEntity` / `G_REL` 로 복제. legacy `Paper/Method` 와
분리. `ASST_TOKEN` 필요 (컨테이너 env). 미지정 시 `nodes_upserted=0` + errors.

### `/worker/legacy_bridge` (coarse)
G 노드 텍스트를 Gateway `/search/hybrid` 로 질의 → score 3등급으로
derived_view 에 기록. `ASST_TOKEN` 필수.

### `/worker/legacy_fact` (fine-grained)
반대 방향. qdrant_meta jsonl dump 의 chunk 를 문장 분해 → G `/search/hybrid`.
`per_tick` 작게 (50~200) 유지 권장 — 문장당 1 RPC 이라 무거움. cursor 파일이
`${GSTAR_HOME}/legacy_fact_cursor_<coll>.txt` 에 저장돼 재시작 안전.
env 플래그 `LEGACY_FACT_BRIDGE_ENABLED` 는 endpoint 호출 시 자동 우회됨.

### `/worker/code_repos`
NAS `/nas/workspace/code_repos` 13k files 점진 이관. cursor 파일이
`${GSTAR_HOME}/code_repos.cursor`. `per_tick` 30~50 권장. env 플래그
`CODE_REPOS_ENABLED` 자동 우회됨.

## 4. 권장 운용 패턴

### A. 수동 on-demand (현재 권장)
```bash
# 매일 상주 tick: light 만 (community·procedures 갱신)
curl -sf -X POST http://100.79.251.53:9999/worker/tick \
  -H "Content-Type: application/json" -d '{}'

# 주 1회 — 무거운 파생뷰 동기화
for ep in qdrant_mirror neo4j_mirror legacy_bridge; do
  curl -sf -X POST http://100.79.251.53:9999/worker/$ep -d '{}'
done

# code_repos 는 tick 당 50 씩, 전량 완료까지 ~260회 호출
curl -sf -X POST http://100.79.251.53:9999/worker/code_repos \
  -H "Content-Type: application/json" -d '{"per_tick":50}'
```

### B. launchd / cron 자동화 (예시, 맥북 또는 미니PC)
```
*/10 * * * *  curl -fsS -X POST http://localhost:9999/worker/tick -d '{}' >/dev/null
0 4 * * *     curl -fsS -X POST http://localhost:9999/worker/qdrant_mirror -d '{}' >/dev/null
0 5 * * 0     curl -fsS -X POST http://localhost:9999/worker/neo4j_mirror -d '{}' >/dev/null
0 */2 * * *   curl -fsS -X POST http://localhost:9999/worker/code_repos -d '{"per_tick":50}' >/dev/null
```

## 5. 유지보수

### 로그
```bash
ssh ljw-op 'docker logs -f g-serve 2>&1 | tail -50'
```

### 상태·마지막 tick 결과
```bash
curl -sf http://100.79.251.53:9999/worker/status | jq .
# 또는 컨테이너 내 DuckDB 직접 조회
docker exec -it g-serve python -c "
from gstar.storage.duckdb_store import DuckStore
from pathlib import Path
s=DuckStore(Path('/app/state/state/g.duckdb'))
for r in s.conn.execute('SELECT started_at, duration_ms, status, steps_json FROM worker_tick ORDER BY started_at DESC LIMIT 5').fetchall():
    print(r[0], r[1], 'ms', r[2], r[3][:120])
"
```

### 일시정지 (예: `jw` 장시간 작업 중 리소스 보호)
```bash
curl -sf -X POST http://100.79.251.53:9999/worker/pause
# … 작업 …
curl -sf -X POST http://100.79.251.53:9999/worker/resume
```
pause 플래그 파일: `${GSTAR_HOME}/worker.paused`. 호출 없이도 `touch`/`rm` 가능.

### 재시작 · 재배포
```bash
# 맥북에서 rsync → 미니PC (.git/.venv/DB/FAISS 제외)
rsync -avz --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
  --exclude='*.duckdb' --exclude='*.faiss' --exclude='.pytest_cache' \
  /Users/ljw0904/workspace/local-claude/ ljw-op:/home/ljw-op/local-claude/

# 미니PC 에서 재빌드
ssh ljw-op 'cd /home/ljw-op/local-claude && \
  docker compose -f deploy/gserve/docker-compose.yml up -d --build g-serve'

# 헬스체크
curl -sf http://100.79.251.53:9999/health | jq .
```

### cursor 초기화 (전량 재스캔이 필요할 때)
```bash
docker exec -it g-serve bash -c 'rm -f /app/state/*.cursor /app/state/legacy_fact_cursor_*.txt'
```

### DB 크기 · 백업
```bash
ssh ljw-op 'du -sh /home/ljw-op/gstar-data/'
# launchd 주기 백업: ~/Dropbox/gstar-backups/archives/ (과거 세션 설정)
```

## 6. 환경변수 치트시트

| 변수 | 기본값 | 역할 |
|---|---|---|
| `GSTAR_HOME` | `/app/state` (컨테이너) | cursor/pause flag 위치 |
| `QDRANT_MIRROR_ENABLED` | `on` | tick steps=["*"] 시 qdrant mirror gate |
| `QDRANT_MIRROR_LIMIT` | `1000` | 한 번에 스캔할 노드 수 |
| `NEO4J_MIRROR_ENABLED` | `on` | 동 neo4j |
| `NEO4J_MIRROR_NODES` / `_EDGES` | `200` / `500` | batch limit |
| `NEO4J_MIRROR_BATCH` | `100` | cypher UNWIND 배치 |
| `LEGACY_BRIDGE_ENABLED` | `on` | coarse bridge gate |
| `LEGACY_BRIDGE_LIMIT` | `500` | tick 당 처리 |
| `LEGACY_FACT_BRIDGE_ENABLED` | `off` | fine bridge gate (endpoint 호출 시 우회됨) |
| `LEGACY_FACT_PER_TICK` | `200` | chunk 수 |
| `LEGACY_FACT_MIN_LEN` | `15` | 문장 최소 길이 |
| `LEGACY_FACT_HIGH_THRESHOLD` | `0.80` | high 기준 |
| `LEGACY_FACT_MEDIUM_THRESHOLD` | `0.68` | medium 기준 |
| `CODE_REPOS_ENABLED` | `off` | code_repos gate (endpoint 호출 시 우회됨) |
| `CODE_REPOS_ROOT` | `/nas/workspace/code_repos` | NAS mount 경로 |
| `CODE_REPOS_NAMESPACE` | `code_repos` | G source_namespace |
| `CODE_REPOS_PER_TICK` | `50` | tick 당 처리 파일 수 |
| `CODE_REPOS_GLOBS` | `*.md,*.py,*.ts,...` | 확장자 필터 |
| `ASST_TOKEN` | (필수) | Gateway / Neo4j cypher 인증 |

## 7. 트러블슈팅

### `/worker/tick` 가 `FatalException: duplicate key` 반환 → (해소됨)
`a3a9dd4` fix(gstar): entity_canonical PK 중복 방어. `neo4j_mapper` ·
`entity/linker` 두 INSERT 지점에 `ON CONFLICT DO NOTHING` + `store.lock`
serialize. 재발 시 DuckDB conn 자체가 굳는 내부 버그가 있으니 컨테이너 재시작.

### heavy step 이 timeout
- Neo4j: `NEO4J_MIRROR_NODES` 를 100 으로 낮춤
- legacy_fact: `per_tick` 을 50 으로 낮춤
- code_repos: NAS SMB 느릴 때 timeout. `per_tick=10` 으로

### `ASST_TOKEN` missing
컨테이너 env 확인: `docker exec g-serve env | grep ASST_TOKEN`. compose `.env`
에 명시돼야 함 (`/home/ljw-op/local-claude/deploy/gserve/.env`).

### 맥북 → 미니PC 접근 불가
`CLAUDE.md` 의 "맥북 네트워크 접근 전략" 섹션 참고. 회사 네트워크에서
`ssh -fN -L 9999:100.79.251.53:9999 ljw-op` 후 `http://localhost:9999` 로 접근.
