#!/usr/bin/env bash
# g-dropbox-backup — 미니 PC 의 gstar-data 를 Dropbox 폴더로 주기 백업.
#
# 동작: ssh ljw-op '/gstar-data 를 tar zstd 로 stream' → Dropbox/gstar-backups/ 저장.
# Dropbox desktop 이 자동으로 클라우드 sync.
#
# 설치:
#   1) Dropbox 가 맥북에 설치돼 있어야 함
#   2) chmod +x 이 파일
#   3) 수동 실행: ~/workspace/local-claude/bin/g-dropbox-backup.sh
#   4) 자동 실행: launchctl load ~/Library/LaunchAgents/com.local-claude.g-dropbox-backup.plist

set -eu

REMOTE_HOST="${GSTAR_REMOTE_HOST:-ljw-op}"
REMOTE_DIR="${GSTAR_REMOTE_DIR:-gstar-data}"  # 미니 PC 홈 기준 상대 경로
DROPBOX_DIR="${GSTAR_DROPBOX_DIR:-$HOME/Dropbox/gstar-backups}"
KEEP_DAYS="${GSTAR_BACKUP_KEEP_DAYS:-30}"

mkdir -p "$DROPBOX_DIR/archives" "$DROPBOX_DIR/anchors"

STAMP=$(date +%Y%m%d-%H%M%S)
OUT="$DROPBOX_DIR/archives/gstar-${STAMP}.tar.zst"

echo "[backup] $REMOTE_HOST:$REMOTE_DIR → $OUT"
if command -v zstd >/dev/null 2>&1; then
  ssh "$REMOTE_HOST" "tar cf - $REMOTE_DIR" | zstd -19 -T0 > "$OUT"
else
  OUT="${OUT%.zst}.gz"
  ssh "$REMOTE_HOST" "tar czf - $REMOTE_DIR" > "$OUT"
fi

SIZE_MB=$(du -m "$OUT" | awk '{print $1}')
echo "[backup] 완료: ${SIZE_MB} MB"

find "$DROPBOX_DIR/archives" -maxdepth 1 -type f -name 'gstar-*.tar.*' \
  -mtime "+${KEEP_DAYS}" -print -delete 2>/dev/null || true

echo "[backup] keep_days=${KEEP_DAYS} — 오래된 파일 정리 완료"
