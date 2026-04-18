#!/usr/bin/env bash
# <bitbar.title>ctx context</bitbar.title>
# <bitbar.version>v0.1</bitbar.version>
# <bitbar.author>Jae Won Lee</bitbar.author>
# <bitbar.desc>맥북 현재 context (집/회사/오프라인) 표시 + 전환</bitbar.desc>
# <bitbar.dependencies>bash,python3</bitbar.dependencies>
#
# 설치:
#   1) SwiftBar 설치 (brew install swiftbar) 후 Plugin 디렉토리 열기
#   2) 이 파일을 ~/Library/Application\ Support/SwiftBar/Plugins/ 에 복사 or 심볼릭 링크
#        ln -s /Users/ljw0904/workspace/local-claude/swiftbar/ctx.30s.sh \
#              ~/Library/Application\ Support/SwiftBar/Plugins/ctx.30s.sh
#   3) chmod +x, SwiftBar 재시작

set -eu

# bin/ctx 경로 결정 (repo 위치 하드코딩 — 본인 환경 기준)
CTX_BIN="${CTX_BIN:-$HOME/workspace/local-claude/bin/ctx}"

if [ ! -x "$CTX_BIN" ]; then
  echo "ctx ❓"
  echo "---"
  echo "ctx CLI 없음: $CTX_BIN | color=red"
  exit 0
fi

# 자동 감지 먼저 (silent). 매칭되면 실제 전환.
"$CTX_BIN" autodetect --apply >/dev/null 2>&1 || true

CURRENT="$("$CTX_BIN" current 2>/dev/null || echo "unset")"
DISPLAY=""
COLOR=""

if [ "$CURRENT" != "unset" ]; then
  DISPLAY="$("$CTX_BIN" get display --context "$CURRENT" 2>/dev/null || echo "$CURRENT")"
  COLOR="$("$CTX_BIN" get color --context "$CURRENT" 2>/dev/null || echo '')"
fi

# 메뉴바 표시
if [ -z "$DISPLAY" ]; then
  echo "ctx ❓"
else
  if [ -n "$COLOR" ]; then
    echo "$DISPLAY | color=$COLOR"
  else
    echo "$DISPLAY"
  fi
fi

# 드롭다운
echo "---"
echo "현재: $CURRENT"
echo "---"
echo "전환"
"$CTX_BIN" list --names 2>/dev/null | while IFS= read -r NAME; do
  [ -z "$NAME" ] && continue
  DISP="$("$CTX_BIN" get display --context "$NAME" 2>/dev/null || echo "$NAME")"
  PREFIX=""
  [ "$NAME" = "$CURRENT" ] && PREFIX="✓ "
  echo "--${PREFIX}${DISP} (${NAME}) | bash=\"$CTX_BIN\" param1=switch param2=${NAME} terminal=false refresh=true"
done
echo "---"
# 새 context 추가 (이직 대비) — AppleScript 입력 창
OSASCRIPT=$(cat <<'APPLESCRIPT'
set ctxBin to (POSIX path of (path to home folder)) & "workspace/local-claude/bin/ctx"
set nameResp to display dialog "새 context 이름 (예: office-newcorp)" default answer "" with title "ctx add"
set ctxName to text returned of nameResp
if ctxName is "" then return
set displayResp to display dialog "화면 표시 (예: 🏢 NewCorp)" default answer ("🏢 " & ctxName) with title "ctx add"
set ctxDisplay to text returned of displayResp
set tplResp to display dialog "복사할 템플릿 (home/office-daegyeom/offline)" default answer "offline" with title "ctx add"
set ctxTemplate to text returned of tplResp
do shell script quoted form of ctxBin & " add " & quoted form of ctxName & " --from " & quoted form of ctxTemplate & " --display " & quoted form of ctxDisplay
display notification "context '" & ctxName & "' 생성됨" with title "ctx"
APPLESCRIPT
)
OSA_ENC=$(printf '%s' "$OSASCRIPT" | base64)
echo "➕ 새 context 추가 | shell=/bin/bash param1=-c param2='echo \"$OSA_ENC\" | base64 -d | osascript -' terminal=false refresh=true"
echo "📝 활성 context 편집 | bash=\"$CTX_BIN\" param1=edit param2=\"$CURRENT\" terminal=false refresh=true"
echo "---"
echo "최근 전환 이력 | bash=\"$CTX_BIN\" param1=history param2=--n param3=5 terminal=true"
echo "장치 지문 | bash=\"$CTX_BIN\" param1=device terminal=true"
echo "---"
echo "새로고침 | refresh=true"
