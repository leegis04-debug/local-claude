# 사용 설명서 (실사용 중심)

복잡한 내부 구조는 다 잊어도 된다. 아래 4가지만 기억.

| 도구 | 언제 쓰나 | 어디서 도는가 |
|------|---------|-----------|
| `ctx` | 집·회사·오프라인 전환 | 맥북 |
| `g` | 지식 저장·검색·검증 | 맥북이 미니 PC 호출 |
| `jw` / `re` | 사업계획서·연구 초안 작성 | 맥북 (RAG 는 미니 PC) |
| SwiftBar | 상단바 아이콘으로 위의 것 빠르게 | 맥북 GUI |

---

## 1. 전체 그림 (한 장)

```
┌─────────────┐                      ┌──────────────────────┐
│ 맥북        │   Meshnet direct     │ 미니 PC 100.79.251.53│
│ (작업·선택) ├─────────────────────▶│  g-serve :9999       │
│             │                      │  Gateway :8000       │
│ SwiftBar 🏠 │                      │  Qdrant / Neo4j / MCP│
│ ctx / g / jw│                      │  ~/gstar-data        │
└─────────────┘                      └──────────┬───────────┘
       ▲                                         │
       │  주기 백업 (launchd, 매일 03시)          │
       └─────────────────────────────────────────┘
                    │
                    ▼
         ~/Dropbox/gstar-backups/
            ├── archives/gstar-*.tar.zst
            └── anchors/*.txt   (외부 앵커)
```

---

## 2. 매일 쓰는 명령어 (7개)

새 터미널이라면 먼저: `source ~/.zshrc`

### ① 내가 어디 있지?
```bash
ctx current           # home / office-daegyeom / offline
ctx list              # 전체 목록, * = 활성
```

### ② 위치 바꾸기
```bash
ctx switch office-daegyeom
# 또는 SwiftBar 상단바 드롭다운 클릭
```

### ③ SSID 기반 자동 전환
```bash
ctx autodetect            # 현재 WiFi 로 어디로 가야 하는지만 (dry-run)
ctx autodetect --apply    # 실제 전환
```
SwiftBar 가 30초마다 자동 호출. 수동 전환 후 10분은 쿨다운.

### ④ 지식 넣기
```bash
g ingest ~/path/to/docs       # markdown·txt 파일·폴더
# ctx 활성 namespace 자동 사용 (예: home → personal)
```

### ⑤ 질의
```bash
g goal set "OO 사업계획서" --kind proposal   # 목표 등록 (1회)
g gravity top <goal_id> --k 10               # 빠른 탐색 (LLM 없음)
g run <goal_id>                              # 선택기 루프 (5~30초)
g stellar build <goal_id>                    # 지식 항성 생성
g stellar list <goal_id>                     # 결과 목록
g emerge log <goal_id>                       # 창발 이벤트 (4연결)
```

### ⑥ 무결성 확인
```bash
g verify chain --ns personal                 # 체인 변조 감지
g verify cluster <cluster_id>                # Merkle root 대조
```

### ⑦ 사업계획서 작성 (jw shim)
```bash
jw idea "..."     # shim 이 ctx 의 jw.rag_backend 보고 G 또는 Gateway 경유
```

---

## 3. 상황별 (가끔)

### 이직 — 새 회사 context 추가
```bash
ctx add office-newcorp --from offline --display "🏢 NewCorp" --color "#d73a49"
ctx edit office-newcorp          # namespace, server_url 등 확인
ctx wifi add office-newcorp "NewCorp-Wifi"
ctx switch office-newcorp
g ingest ~/docs/newcorp          # namespace=newcorp 자동 생성
```

### 이직 시 이전 회사 지식 보존
이미 DS218 또는 Dropbox 에 매일 자동 백업되고 있음. 아무것도 안 해도 됨.
퇴사일에 한 번만:
```bash
~/workspace/local-claude/bin/g-dropbox-backup.sh   # 수동 마지막 백업
```

### 오프라인 (카페·비행기)
```bash
ctx switch offline        # 또는 WiFi 끊기면 SwiftBar 가 감지
# 현재는 g 커맨드가 서버 호출 실패 시 에러. 미러 sync 구현은 Phase 8 로 보류.
```

### 사업계획서 파일 생성 (수동)
```bash
# 지식 항성 결과를 파일로
g stellar show <cluster_id> > ~/Desktop/knowledge.md
# P축(자동 문서 투영) 은 미구현 — 수동 복사·정리
```

### 상단바 안 뜨면
```bash
open -a SwiftBar                              # 실행
# 메뉴바 아이콘 → Preferences → Refresh All
```

---

## 4. 상단바 (SwiftBar) UI

```
🏠 집                         ← 현재 context
 ─────
 현재: home
 ─────
 전환
  ✓ 🏠 집 (home)
    🏢 다겸 (office-daegyeom)
    ✈️ 오프라인 (offline)
 ─────
 ➕ 새 context 추가           ← 이직 시 클릭
 📝 활성 context 편집
 ─────
 최근 전환 이력
 장치 지문
 ─────
 새로고침
```

드롭다운의 어느 항목이든 클릭하면 즉시 반영.

---

## 5. 설정 파일 (건드릴 일 거의 없음)

| 파일 | 용도 |
|------|------|
| `~/.ctx/contexts/<name>.toml` | 각 context 설정 (WiFi SSID, namespace, server_url) |
| `~/.ctx/current` | 활성 context 이름 한 줄 |
| `~/.ctx/history.jsonl` | 전환 이력 (감사 로그) |
| `~/.ctx/device.json` | 맥북 fingerprint (수정 금지) |
| `~/.gstar/config.toml` | G 가중치 (선택) |
| `~/.gstar-mirror/` | 오프라인 미러 (Phase 8, 현재 미사용) |
| `~/Dropbox/gstar-backups/archives/` | 매일 백업 tar.zst |
| `~/Dropbox/gstar-backups/anchors/` | Merkle root 앵커 |
| 미니 PC `~/gstar-data/` | G 정본 DuckDB + FAISS |
| 미니 PC `~/local-claude/` | 배포된 코드 |

---

## 6. 트러블슈팅

### "command not found: ctx / g"
```bash
source ~/.zshrc           # PATH 재로드
which ctx                 # /Users/ljw0904/workspace/local-claude/bin/ctx 나와야 함
```

### "no ssid detected"
macOS Sequoia 위치 권한 문제 가능. 이미 `system_profiler` fallback 이 들어있어
보통 해결됨. 그래도 안 되면:
```bash
system_profiler SPAirPortDataType | grep -A 1 "Current Network Information"
```

### "Connection timeout" (맥북 → 미니 PC)
- Meshnet 연결 확인: `ping -c 2 100.79.251.53`
- 외부 네트워크: `ssh -fN -L 9999:100.79.251.53:9999 ljw-op` 후 `ctx get gstar.server_url`
  을 `http://localhost:9999` 로 변경

### g-serve 재시작
```bash
ssh ljw-op 'cd ~/local-claude && docker compose -f deploy/gserve/docker-compose.yml restart g-serve'
```

### g-serve 로그
```bash
ssh ljw-op 'docker logs --tail 50 g-serve'
```

### 코드 업데이트 (맥북 → 미니 PC)
```bash
rsync -az --exclude=.venv --exclude=.git --exclude=__pycache__ \
  ~/workspace/local-claude/ ljw-op:~/local-claude/
ssh ljw-op 'cd ~/local-claude && docker compose -f deploy/gserve/docker-compose.yml up -d --build'
```

---

## 7. 무엇을 언제 쓰는가 (요약)

| 상황 | 명령 |
|------|------|
| 출근·귀가 시 | **아무것도 안 해도 됨** (SwiftBar 자동 감지) |
| 새 문서 추가 | `g ingest ~/path` |
| 자료 찾기 | `g gravity top <goal> --k 10` |
| 사업계획서 초안 | `jw idea "..."` |
| 변조 의심 | `g verify chain` |
| 이직 | `ctx add office-xxx ...` |
| 회사 바뀐 날 밤 | 아무것도 안 해도 됨 (매일 백업) |
| 오프라인 작업 | (Phase 8 미러 구현 후) |

기본적으로 **SwiftBar 드롭다운 + jw 명령어 1~2개**로 99% 끝난다. 나머지는 달 1회 정도.
