# G — 지식 항성 모델 (Knowledge Stellar Model)

파편화된 chunk 대신 **"의미 + 관계 + 시간 + 검증 가능성"을 함께 담은 개체 중심 지식**을
응집·창발·재분해하는 순환형 RAG.

**배경**: Gemma 4 e4b 4-bit 로 사업계획서를 작성했을 때 품질 부족. 원인은 모델 크기가
아니라 "모델이 다루는 지식 단위"의 파편화. G 는 이 단위를 재설계한다.

## 핵심 개념

| 요소 | 의미 |
|------|------|
| **정보 노드** | 블록체인적 최소 단위 (fact/entity/relation/event/state/evidence). 최대 3연결까지 안정 |
| **지식 중력장** | 목표 중심으로 노드를 끌어당기는 가중합 점수 |
| **지식 항성(Cluster)** | 중력장에서 응집된 안정 구조. A(의미·관계)/B(시간·무결성) 로 유지 |
| **창발(Emergence)** | 4번째 연결이 생기는 순간 = 상위 지식 생성 신호 |
| **Integrity Layer** | 해시 체인 + Merkle + namespace + 서명 슬롯 (이직·공유 대비) |

## 설치

```bash
cd local-claude
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[gstar]'
export PATH="$(pwd)/bin:$PATH"
```

의존성(gstar extras): `duckdb`, `faiss-cpu`, `sentence-transformers`,
`python-ulid`, `cryptography`, `typer`, `numpy`.

## 기본 흐름

```bash
g init                                      # ~/.gstar/state/ 생성
g ingest ~/path/to/docs --ns personal       # markdown/txt → 노드 + 엣지
g goal set "OO 사업계획서" --kind proposal    # 목표 + 임베딩 색인
g gravity top <goal_id> --k 20 --breakdown  # LLM 없이 상위 노드
g run <goal_id>                             # Gemma e4b 경계 판정 루프
g stellar build <goal_id>                   # connected components 클러스터링
g stellar list <goal_id>                    # 지식 항성 목록 (Merkle root 포함)
g stellar show <cluster_id>                 # 멤버 상세 (⭐ = 중심)
g emerge log <goal_id>                      # 4연결 창발 이벤트
```

## Integrity (이직·공유 대비)

```bash
g provenance list                           # 등록된 namespace
g provenance set-ns daegyeom                # 이직 시 새 namespace 전환
g verify chain --ns daegyeom                # 체인 무결성 (변조 감지)
g verify cluster <cluster_id>               # Merkle root 재계산·대조
g export --ns daegyeom > bundle.jsonl       # namespace 내보내기
g import bundle.jsonl                       # 다른 G 인스턴스로 이관
```

**체인 구조**: 각 노드는 `content_hash = sha256(kind|text|attrs|created_at|...|namespace)`
를 갖고, 같은 namespace 내 직전 노드의 `content_hash` 를 `prev_hash` 로 참조. 변조 시
`verify chain` 이 깨진 노드 id 를 반환한다.

**Merkle**: Cluster 의 멤버 `content_hash` 들을 정렬 후 binary 해시 트리로 요약. 창발·폭발 전후 diff 추적 가능.

**서명**(선택): `$GSTAR_HOME/keys/<namespace>.key` 에 Ed25519 키가 있으면 노드에 자동 서명.
키가 없으면 `signature=None` 으로 append. 나중에 키를 생성해도 기존 노드는 유효 유지.

## 중력 점수 공식 (`gravity/score.py`)

```
gravity(node, goal) =
    w_rel  * cos(node.emb, goal.emb)           # A: 의미 관련성
  + w_rec  * recency(node.created_at)          # B: 최신성 (halflife 지수감쇠)
  + w_cent * graph_centrality(node, seed)      # A: 관계 중심성 (BFS hop 역수)
  + w_ver  * version_validity(node)            # B: 버전 검증성
  + w_pur  * purpose_fit(node, goal.kind)      # 목적 적합성 (proposal/code/research)
  + w_stab * repeat_selection_rate(node)       # 반복 선택 안정성
```

기본 가중치: `w_rel=0.30, w_rec=0.10, w_cent=0.20, w_ver=0.10, w_pur=0.20, w_stab=0.10`.

**튜닝**: `$GSTAR_HOME/config.toml` 에 `[weights]` 블록 작성. 예:

```toml
[weights]
w_rel = 0.40           # 임베딩 유사도 비중 상향
halflife_days = 7.0    # 최신성 감쇠 가속
seed_k = 10            # centrality 계산용 시드 top-K
candidate_k = 200      # gravity 계산 대상 후보 수
```

## ctx 연동

[ctx](../ctx/README.md) 가 설치돼 있으면 `g ingest` 가 `--ns` 미지정 시 활성
context 의 `gstar.namespace` 를 자동 사용한다. 회사·집 이동 시 맥북 상단바에서
클릭만으로 namespace 전환.

## 벤치마크

```bash
pytest tests/gstar                          # 단위 + 합성 벤치 (280 테스트)
python tests/gstar/real/run_agrifood.py     # 실제 사업계획서 E2E
```

**실제 벤치 결과** (`tests/gstar/real/report_agrifood.md`):
- 농식품 AI 응용제품 신속상용화 과제 (다겸·로칼·메디프레소 컨소시엄)
- Ingest: 3 markdown → 1,231 fact + 1,567 entity + 52k edges
- Selector 2 사이클 수렴 (21초)
- 지식 항성 3개, 창발 이벤트 48건
- 핵심 개체 커버리지 53.3% (양식 지시문 노이즈 필터 + 고유명사 entity 추출 개선 여지 있음)

## Phase 7 — 서버 배포 / 자동화 / 무결성

**아키텍처**: 맥북(작업) + 4090(LLM) + 미니 PC 100.79.251.53(G serve 정본) + DS218(백업).

### g serve (미니 PC)

```bash
# 미니 PC 에서
cd /opt/local-claude
docker compose -f deploy/gserve/docker-compose.yml up -d --build
curl http://localhost:9999/health
```

배포 가이드: [deploy/gserve/README.md](../../deploy/gserve/README.md).

### 맥북 클라이언트

```python
from gstar.client import from_env
c = from_env()          # ctx 활성 context 의 gstar.server_url 자동
hits = c.search("농식품 AI", top_k=5)
```

또는 `g query "..."` 가 `GSTAR_MODE=client` 면 HTTP, `local` 이면 로컬 DuckDB.

### ctx 자동 전환

```bash
ctx wifi add home "HomeWifi"
ctx wifi add office-daegyeom "Daegyeom-Wifi"
ctx autodetect                          # dry-run
ctx autodetect --apply                  # 실제 전환
# SwiftBar 는 30s 주기로 autodetect --apply 자동 호출 (쿨다운 10분)
```

### 새 context 추가 (이직·신규)

```bash
ctx add office-newcorp --from offline --display "🏢 NewCorp"
ctx wifi add office-newcorp "NewCorp-Wifi"
ctx switch office-newcorp               # G 에 namespace 자동 upsert
g ingest ~/docs/newcorp
```

SwiftBar 드롭다운 "➕ 새 context 추가" 도 같은 커맨드를 호출.

### DS218 외부 앵커

```bash
# DS218 SMB 마운트 후
g stellar list <goal_id>
g verify anchor-write <cluster_id> --snapshot-dir /Volumes/gstar-snapshots
g verify anchor <cluster_id> --snapshot-dir /Volumes/gstar-snapshots
```

DSM Hyper Backup 설정: [deploy/ds218/hyperbackup-config.md](../../deploy/ds218/hyperbackup-config.md).

### jw/re RAG backend 스위치 (본체 수정 없음)

```bash
# ctx TOML 의 [jw] rag_backend = "g" 로 설정
bin/jw-shim idea "농식품 AI..."         # 미니 PC g-serve 경유
# rag_backend="gateway" 면 기존 Gateway 그대로
```

### 오프라인 미러

```bash
# 수동
rsync -av ljw-op:/volume1/docker/gstar-data/state/ ~/.gstar-mirror/
# launchd agent
cp bin/g-mirror-sync.plist ~/Library/LaunchAgents/com.local-claude.g-mirror.plist
launchctl load ~/Library/LaunchAgents/com.local-claude.g-mirror.plist
```

## 제한 사항

- **PDF/HWP 미지원** — pdftotext/hwp5txt 설치 시 pre-processing 수동 필요
- **한국어 entity 빈도 필터** — 조사 변형(다겸이/다겸의) 으로 min_count 누락 가능
- **P축(4090 투영기) 미구현** — 지식 항성 → 최종 문서 평면화는 별도 플랜
- **단일 writer** — DuckDB 특성상 동시 기록 불가 (읽기는 병행 가능)
