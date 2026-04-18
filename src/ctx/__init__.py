"""ctx — 통합 location/context 레이어.

맥북이 집/회사/외부/오프라인 중 어디 있는지를 명시적으로 표현하고,
G · jw · re · 향후 도구들이 같은 활성 context 를 읽어 설정을 스위치한다.

파일 레이아웃: $CTX_HOME (기본 ~/.ctx)
  current           # 활성 context 이름 (한 줄)
  contexts/         # context 별 TOML 설정
    home.toml
    office-daegyeom.toml
    offline.toml
  history.jsonl     # append-only 감사 로그
  device.json       # 장치 fingerprint (맥북 Serial sha256)
"""

__version__ = "0.1.0"
