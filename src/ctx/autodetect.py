"""WiFi SSID 기반 context 자동 감지.

동작:
 1. `networksetup -getairportnetwork <iface>` 로 현재 SSID 조회
 2. 각 context TOML 의 `wifi = [...]` 배열과 매칭 (배열 순서 = 우선순위)
 3. 쿨다운(기본 10분) 내 수동 전환 있으면 auto skip → 사용자 의도 존중
 4. 매칭·변경 필요 시 set_current_context_name + log_switch("auto-wifi")
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ctx.config import Paths
from ctx.context import (
    current_context_name,
    list_context_names,
    load_context,
    set_current_context_name,
)
from ctx.history import log_switch, read_history


AUTO_COOLDOWN_MIN = 10
_IFACES = ("en0", "en1")


def current_ssid() -> str | None:
    """현재 연결된 WiFi SSID.

    경로 1: `networksetup -getairportnetwork <iface>` — macOS 14 이하에서 OK,
    Sequoia(15+) 에서는 위치 권한 없으면 "not associated" 반환.
    경로 2 (fallback): `system_profiler SPAirPortDataType` — 권한 불필요, 항상 동작.
    """
    # --- 경로 1: networksetup ---
    for iface in _IFACES:
        try:
            out = subprocess.run(
                ["networksetup", "-getairportnetwork", iface],
                capture_output=True, timeout=3,
            )
        except Exception:
            continue
        if out.returncode != 0:
            continue
        text = out.stdout.decode("utf-8", errors="replace").strip()
        m = re.match(r"^Current Wi-Fi Network:\s*(.+)$", text)
        if m:
            ssid = m.group(1).strip()
            if ssid and "not associated" not in ssid.lower():
                return ssid

    # --- 경로 2: system_profiler (Sequoia 대응) ---
    try:
        out = subprocess.run(
            ["system_profiler", "SPAirPortDataType"],
            capture_output=True, timeout=10,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    text = out.stdout.decode("utf-8", errors="replace")
    # "Current Network Information:" 블록 아래 첫 들여쓰기 라인의 `<SSID>:` 를 뽑는다.
    in_current = False
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if "Current Network Information" in line:
            in_current = True
            continue
        if in_current:
            # 들여쓰기 있고 ":" 로 끝나면 SSID
            stripped = line.strip()
            if stripped.endswith(":"):
                ssid = stripped[:-1].strip()
                if ssid:
                    return ssid
            # 더 상위 section 으로 빠지면 종료
            if line and not line.startswith(" "):
                break
    return None


def match_context(ssid: str | None, paths: Paths | None = None) -> str | None:
    """주어진 SSID 를 가진 첫 번째 context 반환. 미매칭 시 None."""
    if not ssid:
        return None
    paths = paths or Paths.load()
    for name in list_context_names(paths):
        try:
            data = load_context(name, paths)
        except Exception:
            continue
        ssids = data.get("wifi") or []
        if not isinstance(ssids, list):
            continue
        for entry in ssids:
            if str(entry).strip() == ssid:
                return name
    return None


def should_auto_switch(
    paths: Paths | None = None,
    *,
    cooldown_min: int = AUTO_COOLDOWN_MIN,
    now: datetime | None = None,
) -> bool:
    """최근 쿨다운 내 `manual` 전환이 있으면 False (사용자 의도 보호)."""
    paths = paths or Paths.load()
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=cooldown_min)
    for ev in reversed(read_history(paths)):
        ts_str = ev.get("ts", "")
        try:
            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except Exception:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < cutoff:
            break
        if ev.get("trigger") == "manual":
            return False
    return True


@dataclass
class AutoResult:
    ssid: str | None
    matched: str | None
    current: str | None
    action: str  # "switched" | "same" | "no-match" | "cooldown" | "no-ssid"
    meta: dict


def autodetect(
    paths: Paths | None = None,
    *,
    apply: bool = False,
    force: bool = False,
    now: datetime | None = None,
) -> AutoResult:
    paths = paths or Paths.load()
    ssid = current_ssid()
    cur = current_context_name(paths)
    meta = {"ssid": ssid}

    if not ssid:
        return AutoResult(ssid=None, matched=None, current=cur, action="no-ssid", meta=meta)

    matched = match_context(ssid, paths)
    if matched is None:
        return AutoResult(ssid=ssid, matched=None, current=cur, action="no-match", meta=meta)

    if matched == cur:
        return AutoResult(ssid=ssid, matched=matched, current=cur, action="same", meta=meta)

    if not force and not should_auto_switch(paths, now=now):
        return AutoResult(
            ssid=ssid, matched=matched, current=cur, action="cooldown", meta=meta
        )

    if apply:
        set_current_context_name(matched, paths)
        log_switch(
            to=matched, from_=cur, trigger="auto-wifi", meta=meta, paths=paths,
        )

    return AutoResult(ssid=ssid, matched=matched, current=cur, action="switched", meta=meta)
