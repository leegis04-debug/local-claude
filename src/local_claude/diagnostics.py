"""lc status / lc doctor — 시스템 전반 상태 덤프.

status: 가볍게 요약만.
doctor: 체크리스트(pass/warn/fail) + 외부 의존 도달성.
"""

from __future__ import annotations

import json
import shutil
import socket
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import config
from .code import fs_index
from .infra import gateway as gateway_mod
from .infra import tunnel as tunnel_mod
from .memory import project as project_memory
from .memory import user as user_memory
from .perf import logger as perf_logger
from .state import compressor as state_mod


# ── status (요약) ───────────────────────────────────────────────────────────

def _perf_summary(project_dir: Path | None, days: int = 14) -> dict[str, Any]:
    total = 0
    by_action: Counter[str] = Counter()
    fail_count = 0
    for i in range(days):
        day = datetime.now() - timedelta(days=i)
        path = perf_logger.log_path(project_dir, when=day)
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                event = json.loads(line)
            except Exception:
                continue
            total += 1
            by_action[str(event.get("action", ""))] += 1
            if event.get("exit_code", 0) != 0:
                fail_count += 1
    return {
        "days": days,
        "events": total,
        "failures": fail_count,
        "top_actions": by_action.most_common(5),
    }


def status(project_dir: Path | str | None = None) -> dict[str, Any]:
    root = Path(project_dir).resolve() if project_dir else Path.cwd()
    state = state_mod.load(root)
    return {
        "project_root": str(root),
        "state": {
            "project": state.project,
            "current_goal": state.current_goal,
            "next_action": state.next_action,
            "last_action": state.last_action,
            "last_exit_code": state.last_exit_code,
            "updated_at": state.updated_at,
            "open_issues": len(state.open_issues),
            "decisions": len(state.decisions),
        },
        "memory": {
            "project_entries": len(project_memory.all(root)),
            "user_entries": len(user_memory.all()),
        },
        "perf": _perf_summary(root),
        "key_files": fs_index.key_files(root),
    }


# ── doctor (체크리스트) ─────────────────────────────────────────────────────

@dataclass
class Check:
    name: str
    ok: bool
    level: str = "pass"  # "pass" | "warn" | "fail"
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "ok": self.ok, "level": self.level, "detail": self.detail}


@dataclass
class DoctorReport:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "", *, warn: bool = False) -> None:
        level = "pass" if ok else ("warn" if warn else "fail")
        self.checks.append(Check(name=name, ok=ok, level=level, detail=detail))

    def summary(self) -> dict[str, int]:
        counts = Counter(c.level for c in self.checks)
        return {"pass": counts["pass"], "warn": counts["warn"], "fail": counts["fail"]}

    def to_dict(self) -> dict[str, Any]:
        return {
            "checks": [c.to_dict() for c in self.checks],
            "summary": self.summary(),
        }


def _check_token() -> Check:
    token = gateway_mod.load_token()
    if token:
        # 토큰 마스킹
        masked = token[:4] + "…" + token[-3:] if len(token) > 8 else "(short)"
        return Check(name="ASST_TOKEN", ok=True, level="pass", detail=masked)
    return Check(
        name="ASST_TOKEN",
        ok=False,
        level="warn",
        detail="환경변수/~/.config/asst/config 에 토큰 없음 — gateway 호출 시 인증 실패",
    )


def _check_ask_gemma() -> Check:
    path = shutil.which(config.ASK_GEMMA_BIN)
    if path:
        return Check(name="ask-gemma", ok=True, detail=path)
    if Path(config.ASK_GEMMA_BIN).exists():
        return Check(name="ask-gemma", ok=True, detail=config.ASK_GEMMA_BIN)
    return Check(
        name="ask-gemma",
        ok=False,
        level="warn",
        detail=f"찾을 수 없음: {config.ASK_GEMMA_BIN} — 자동 기억/LLM 모드 불가",
    )


def _check_gateway() -> Check:
    gw = gateway_mod.Gateway()
    resp = gw.health()
    if resp.ok:
        return Check(name="gateway /health", ok=True, detail=f"via={resp.via}")
    return Check(
        name="gateway /health",
        ok=False,
        level="warn",
        detail=f"via={resp.via} error={resp.error}",
    )


def _check_tunnel() -> Check:
    st = tunnel_mod.status()
    if st.all_up:
        return Check(name="SSH tunnel", ok=True, detail=", ".join(str(p) for p in st.listening))
    missing = [str(p) for p, v in st.listening.items() if not v]
    return Check(
        name="SSH tunnel",
        ok=False,
        level="warn",
        detail=f"미개방 포트: {','.join(missing)}",
    )


def _check_ssh_host() -> Check:
    host = config.SSH_HOST
    try:
        socket.getaddrinfo(host, 22)
        return Check(name=f"SSH host ({host})", ok=True, detail="DNS 해석 OK")
    except socket.gaierror as exc:
        return Check(
            name=f"SSH host ({host})",
            ok=False,
            level="warn",
            detail=f"DNS 해석 실패: {exc}",
        )


def _check_state(project_dir: Path) -> Check:
    path = config.project_state_path(project_dir)
    if not path.exists():
        return Check(name="state.json", ok=True, level="warn", detail="(아직 없음 — 첫 저장 전)")
    try:
        state_mod.load(project_dir)
        return Check(name="state.json", ok=True, detail=str(path))
    except Exception as exc:
        return Check(name="state.json", ok=False, detail=str(exc))


def _check_memory_files(project_dir: Path) -> list[Check]:
    out = []
    for name, path in (
        ("project memory", config.project_memory_path(project_dir)),
        ("user memory", config.USER_MEMORY_PATH),
    ):
        if not path.exists():
            out.append(Check(name=name, ok=True, level="warn", detail="(아직 없음)"))
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out.append(
                Check(
                    name=name,
                    ok=True,
                    detail=f"{len(data.get('entries', []))} entries @ {path}",
                )
            )
        except Exception as exc:
            out.append(Check(name=name, ok=False, detail=str(exc)))
    return out


def doctor(project_dir: Path | str | None = None) -> DoctorReport:
    root = Path(project_dir).resolve() if project_dir else Path.cwd()
    report = DoctorReport()
    for check in (
        _check_token(),
        _check_ask_gemma(),
        _check_ssh_host(),
        _check_tunnel(),
        _check_gateway(),
        _check_state(root),
        *_check_memory_files(root),
    ):
        report.checks.append(check)
    return report
