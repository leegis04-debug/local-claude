# Dropbox 기반 G 백업 (DS218 대체)

DS218 Hyper Backup 설정이 번거로우면 **이미 쓰고 있는 Dropbox** 를 백업 저장소로
활용할 수 있다. 맥북이 ssh 로 미니 PC 에서 tar 스트림을 받아 `~/Dropbox/gstar-backups/`
에 저장하면, Dropbox desktop 이 자동으로 클라우드 sync.

## 장점 (DS218 대비)
- DSM UI 설정·방화벽·SMB 마운트 불필요
- Dropbox 자체 30일 버전 이력 (Plus 이상은 180일) → Integrity 시점별 보존과 시너지
- 여러 기기에서 동일 백업 접근 (맥북, 아이패드 등)
- 이직해도 개인 Dropbox 는 그대로 유지

## 한계
- Dropbox 용량 한계 (Basic 2GB) — agri-food-ai 규모라면 5~50MB/백업, 충분
- 민감 데이터 클라우드 업로드 → 필요하면 tar 전 `openssl aes-256-cbc -pbkdf2 -pass file:~/.gstar/pass` 로 암호화 추가
- 즉시 복원은 Dropbox 앱·웹 → 다운로드 → tar 풀기 수동

## 사용법

### ① 1회 수동 백업 (먼저 동작 확인)
```bash
~/workspace/local-claude/bin/g-dropbox-backup.sh
# 출력: ~/Dropbox/gstar-backups/archives/gstar-<date>.tar.zst (수 MB)
ls -lh ~/Dropbox/gstar-backups/archives/
```

### ② 매일 자동 백업 (launchd)
```bash
cp ~/workspace/local-claude/bin/g-dropbox-backup.plist \
   ~/Library/LaunchAgents/com.local-claude.g-dropbox-backup.plist
launchctl load ~/Library/LaunchAgents/com.local-claude.g-dropbox-backup.plist

# 확인
launchctl list | grep g-dropbox
tail -f /tmp/g-dropbox-backup.log
```

### ③ G Integrity anchor 를 Dropbox 경로로 기록
```bash
# 클러스터 Merkle root 를 Dropbox/gstar-backups/anchors/ 에 남김
g verify anchor-write <cluster_id> --snapshot-dir ~/Dropbox/gstar-backups

# 나중에 검증 (Dropbox 클라이언트가 자동 sync 된 상태에서)
g verify anchor <cluster_id> --snapshot-dir ~/Dropbox/gstar-backups
```

Dropbox 가 Merkle root 파일을 저장하는 순간, Dropbox 서버 타임스탬프·해시가
**외부 증인** 역할을 한다 (Bitcoin OP_RETURN 보다 훨씬 저렴한 수준의 외부 앵커).

### ④ 복원 (이직·재설치 시)
```bash
# Dropbox 에서 가장 최근 백업 고르기
LATEST=$(ls -t ~/Dropbox/gstar-backups/archives/gstar-*.tar.zst 2>/dev/null | head -1)
echo "복원 대상: $LATEST"

# 미니 PC 에 복원
scp "$LATEST" ljw-op:/tmp/
ssh ljw-op "cd ~ && zstd -d < /tmp/$(basename "$LATEST") | tar xf -"
# 미니 PC 에서 docker compose restart g-serve
ssh ljw-op "cd ~/local-claude && docker compose -f deploy/gserve/docker-compose.yml restart g-serve"
```

## 환경변수 오버라이드
- `GSTAR_REMOTE_HOST` (기본 `ljw-op`)
- `GSTAR_REMOTE_DIR` (기본 `gstar-data` — 미니 PC 홈 기준)
- `GSTAR_DROPBOX_DIR` (기본 `~/Dropbox/gstar-backups`)
- `GSTAR_BACKUP_KEEP_DAYS` (기본 30)

예: 업무용 Dropbox Business 를 쓰면
```
GSTAR_DROPBOX_DIR=~/Dropbox\ (다겸)/gstar-backups ~/workspace/local-claude/bin/g-dropbox-backup.sh
```
