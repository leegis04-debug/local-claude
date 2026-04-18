# ctx — 통합 location/context 스위치

맥북 하나를 집/회사/외부/오프라인 어디에서 쓰는지 명시적으로 표현하고, **G · jw · re ·
향후 도구들이 같은 활성 context 를 읽어 설정을 스위치**하게 한다.

## 핵심 개념

| 개념 | 의미 |
|------|------|
| **Location** | 맥북이 어디 있는가 (집 LAN · 회사 · 외부 카페) |
| **Namespace** | 지식이 어느 조직에서 왔는가 (personal · daegyeom · newcorp) |
| **Network mode** | Gateway 접근 경로 (meshnet-direct · ssh-tunnel · disabled) |

세 개념을 느슨하게 묶어 **한 번의 context 전환**으로 모든 도구가 동시에 맞춰진다.

## 설치

```bash
cd local-claude
.venv/bin/python -m pip install -e '.[ctx]'
export PATH="$(pwd)/bin:$PATH"
ctx init
```

기본 3 context 가 생성된다: `home`, `office-daegyeom`, `offline`. 사용자 `~/.ctx/`
구조:

```
~/.ctx/
  current                # 활성 context 이름
  contexts/
    home.toml
    office-daegyeom.toml
    offline.toml
  history.jsonl          # append-only 전환 감사 로그
  device.json            # 맥북 Serial 기반 fingerprint (sha256 16자)
  keys/                  # (선택) 서명 키
```

## 사용법

```bash
ctx current                          # 활성 context 이름
ctx list                             # 전체 목록
ctx switch office-daegyeom           # 전환
ctx get gstar.namespace              # 도구 스크립트에서 값 조회
ctx get display --context offline    # 특정 context 의 값
ctx show                             # 활성 context TOML JSON 덤프
ctx history --n 20                   # 최근 전환 이력
ctx device                           # 장치 fingerprint

# bash env var 로 덤프 (각 도구가 읽음)
eval "$(ctx export-env)"
echo $CTX_GSTAR_NAMESPACE            # → daegyeom
```

## TOML 구조 예

`~/.ctx/contexts/office-daegyeom.toml`:

```toml
display = "🏢 다겸"
color   = "#0066cc"
wifi    = ["Daegyeom-Wifi", "Daegyeom-Guest"]

[gstar]
namespace = "daegyeom"

[jw]
rag_enabled  = true
gateway_mode = "ssh-tunnel"

[network]
gateway_url       = "http://100.79.251.53:8000"
ssh_host          = "ljw-op"
ssh_forward_ports = [8000, 8765, 8766]
```

## SwiftBar 상단바 연동

```bash
# 1) SwiftBar 설치 (cask!)
brew install --cask swiftbar
open -a SwiftBar

# 2) 플러그인 심볼릭 링크
mkdir -p ~/Library/Application\ Support/SwiftBar/Plugins
ln -sfn ~/workspace/local-claude/swiftbar/ctx.30s.sh \
        ~/Library/Application\ Support/SwiftBar/Plugins/ctx.30s.sh

# 3) SwiftBar 메뉴 → Refresh All
```

상단바에 `🏠 집` / `🏢 다겸` / `✈️ 오프라인` 표시되고, 드롭다운 클릭으로 즉시 전환됨.
전환할 때마다 `~/.ctx/history.jsonl` 에 `{ts, from, to, device_fp, trigger}` append.

## 감사 로그 예

```json
{"ts":"2026-04-18T10:30:00Z","from":"home","to":"office-daegyeom",
 "device_fp":"71e0daea54dc068f","trigger":"manual","meta":{}}
```

`device_fp` 는 `ioreg` Serial 또는 `system_profiler SPHardwareDataType`
(utf-8 안전) 에서 추출. 비 macOS 에서는 hostname fallback.

## 도구 연동

### G
`g ingest <path>` 미지정 시 `ctx get gstar.namespace` 를 자동 사용.

### 기존 jw/re wrapper
`~/.local/bin/jw` 등은 수정 대상이 아님. `ctx export-env` 로 환경변수를 주입해
기존 스크립트가 읽도록:

```bash
# ~/.zshrc 또는 세션 시작 시
eval "$(ctx export-env)"
# jw/re 가 $CTX_JW_RAG_ENABLED / $CTX_JW_GATEWAY_MODE / $CTX_NETWORK_GATEWAY_URL 참조
```

### 향후 도구
`ctx.config.Paths`, `ctx.context.load_context/get_key_path` 를 import 하여 Python
레벨에서도 접근 가능 (G CLI 의 `_ctx_namespace_hint()` 참고).

## 테스트

```bash
pytest tests/ctx     # 14 개 — init/switch/get/history/device/fingerprint
```
