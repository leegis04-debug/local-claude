"""경로 상수 + 환경변수 오버라이드.

모듈 변수(USER_MEMORY_PATH 등)는 런타임 변경 없이 import 시점에 결정된다.
테스트에서는 monkeypatch.setattr("local_claude.config.USER_MEMORY_PATH", ...) 로 덮는다.
"""

from __future__ import annotations

import os
from pathlib import Path


def _env_path(var: str, default: Path) -> Path:
    raw = os.environ.get(var)
    return Path(raw).expanduser() if raw else default


LC_DATA_DIR: Path = _env_path("LC_DATA_DIR", Path.home() / ".local-claude")
USER_MEMORY_PATH: Path = LC_DATA_DIR / "user" / "preferences.json"

ASK_GEMMA_BIN: str = os.environ.get(
    "LC_ASK_GEMMA", str(Path.home() / ".local" / "bin" / "ask-gemma")
)

# ── Gateway (Phase 2) ───────────────────────────────────────────────────────
# 글로벌 CLAUDE.md 규칙: URL/헤더/엔드포인트 고정. localhost/Bearer 금지.
GATEWAY_URL_DIRECT: str = os.environ.get(
    "LC_GATEWAY_URL", "http://100.79.251.53:8000"
)
GATEWAY_URL_TUNNEL: str = os.environ.get(
    "LC_GATEWAY_URL_TUNNEL", "http://localhost:8000"
)
GATEWAY_TIMEOUT_S: float = float(os.environ.get("LC_GATEWAY_TIMEOUT", "30"))
GATEWAY_TOKEN_CONFIG: Path = _env_path(
    "LC_GATEWAY_TOKEN_CONFIG", Path.home() / ".config" / "asst" / "config"
)

# SSH 터널 (CLAUDE.md 1단계 폴백).
SSH_HOST: str = os.environ.get("LC_SSH_HOST", "ljw-op")
SSH_TUNNEL_PORTS: tuple[tuple[int, str, int], ...] = (
    (8000, "100.79.251.53", 8000),   # gateway
    (8765, "100.79.251.53", 8765),   # mcp-doc-thinking
    (8766, "100.79.251.53", 8766),   # mcp-jw-validator
)


def _project_root(project_dir: Path | str | None) -> Path:
    return Path(project_dir).expanduser() if project_dir else Path.cwd()


def project_state_path(project_dir: Path | str | None = None) -> Path:
    return _project_root(project_dir) / "state.json"


def project_memory_path(project_dir: Path | str | None = None) -> Path:
    return _project_root(project_dir) / ".memory" / "project.json"


def project_perf_dir(project_dir: Path | str | None = None) -> Path:
    return _project_root(project_dir) / ".perf"
