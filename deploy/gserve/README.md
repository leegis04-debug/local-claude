# g-serve 배포 (미니 PC)

**호스트 전제**: 미니 PC = 100.79.251.53 (Gateway + Qdrant + Neo4j + MCP 와 같은 장치).
x86 + Docker. Meshnet 으로 맥북과 연결.

## 1. 최초 배포

```bash
# 미니 PC 에 저장소 clone
cd /opt
git clone <local-claude-repo> gstar
cd gstar

# 데이터 디렉토리 생성 (DS218 Hyper Backup 대상과 동일 경로 권장)
export GSTAR_DATA_DIR=/volume1/docker/gstar-data  # 또는 ./gstar-data
mkdir -p "$GSTAR_DATA_DIR"

# 빌드·실행
docker compose -f deploy/gserve/docker-compose.yml up -d --build

# 헬스체크
curl -s http://localhost:9999/health | jq .
```

## 2. 맥북에서 확인

```bash
ctx get network.gateway_url          # http://100.79.251.53:8000 (기존 Gateway)
curl -s http://100.79.251.53:9999/health | jq .
```

맥북 `ctx` TOML 에 `[gstar] server_url = "http://100.79.251.53:9999"` 추가되어 있으면
`g query "..."` 가 이 서버를 호출.

## 3. 업데이트

```bash
cd /opt/gstar
git pull
docker compose -f deploy/gserve/docker-compose.yml up -d --build
```

## 4. 로그 / 디버그

```bash
docker compose -f deploy/gserve/docker-compose.yml logs -f g-serve
docker exec -it g-serve bash
```

## 5. DS218 백업 연동

DSM Hyper Backup 에서:
- 소스: 미니 PC SMB 공유 → `/volume1/docker/gstar-data`
- 목적지: DS218 `/volume1/backups/gstar/`
- 일일 새벽 3시, 보존 7/4/12 (일/주/월)
- 상세: `deploy/ds218/hyperbackup-config.md`

## 6. 외부(회사) 접근

미니 PC 는 Meshnet 으로 100.79.251.53. SSH 터널은 불필요 (Gateway 8000 은 기존 터널,
9999 는 Meshnet direct 로 충분). 외부에서 맥북이 Meshnet direct 실패 시
`ssh -fN -L 9999:100.79.251.53:9999 ljw-op` 로 우회.
