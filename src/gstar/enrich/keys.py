"""Enrich API 키 로더 — ~/.gstar/keys.env 에서 자동 로드.

보안:
- 키는 ~/.gstar/keys.env (퍼미션 0600) 에 저장
- git ignore 됨 (gstar-data 는 .gitignore 에 이미 반영됨)
- env 에 이미 있으면 파일값 무시 (shell export 우선)
- 파일 없거나 해당 키 없으면 silent skip

사용자 작업:
    mkdir -p ~/.gstar
    cat > ~/.gstar/keys.env <<EOF
    TAVILY_API_KEY=tvly-...
    EXA_API_KEY=...
    BRAVE_API_KEY=BSA...
    EOF
    chmod 600 ~/.gstar/keys.env
"""

from __future__ import annotations

import os
from pathlib import Path


SUPPORTED_KEYS = (
    "TAVILY_API_KEY",
    "EXA_API_KEY",
    "BRAVE_API_KEY",
    "SEARCHAPI_KEY",
    "YOU_API_KEY",
    "SERPAPI_KEY",
    "PERPLEXITY_API_KEY",
    "FIRECRAWL_API_KEY",
)


def keys_file_path() -> Path:
    p = os.environ.get("GP_KEYS_FILE")
    if p:
        return Path(p)
    return Path.home() / ".gstar" / "keys.env"


def load_keys(file_path: Path | None = None) -> dict[str, str]:
    """파일에서 키 로드 후 os.environ 에 주입 (env 에 이미 있으면 건너뜀).

    반환: 실제 로드된 키 이름 → 값 (값은 4자 + ... + 4자 마스킹).
    """
    path = file_path or keys_file_path()
    if not path.exists():
        return {}
    loaded: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, _, v = line.partition("=")
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if not k or not v or k not in SUPPORTED_KEYS:
                continue
            if os.environ.get(k):
                continue  # shell export 가 우선
            os.environ[k] = v
            masked = v[:4] + "..." + v[-4:] if len(v) > 10 else "***"
            loaded[k] = masked
    except OSError:
        return {}
    return loaded


def active_keys_masked() -> dict[str, str]:
    """현재 환경에 세팅된 enrich 키들을 마스킹해서 반환 (디버깅)."""
    out: dict[str, str] = {}
    for k in SUPPORTED_KEYS:
        v = os.environ.get(k)
        if v:
            out[k] = v[:4] + "..." + v[-4:] if len(v) > 10 else "***"
    return out


def ensure_template() -> Path:
    """키 파일이 없으면 템플릿 생성 (실제 값 없음, 사용자가 채워야 함)."""
    path = keys_file_path()
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    template = (
        "# Enrich API keys — 각 값을 = 뒤에 채우시오. 모두 선택 사항.\n"
        "# 이 파일은 ~/.gstar/keys.env 로 저장되며 0600 권한 필수.\n"
        "# 진짜 무료 (카드 불필요):\n"
        "TAVILY_API_KEY=\n"
        "EXA_API_KEY=\n"
        "# 카드 등록 필요:\n"
        "# BRAVE_API_KEY=\n"
        "# SEARCHAPI_KEY=\n"
        "# YOU_API_KEY=\n"
        "# SERPAPI_KEY=\n"
        "# PERPLEXITY_API_KEY=\n"
        "# FIRECRAWL_API_KEY=\n"
    )
    path.write_text(template, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path
