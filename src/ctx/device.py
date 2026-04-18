"""장치 fingerprint.

맥북 Serial Number 를 sha256 해서 16자 fingerprint 로 저장한다.
매번 계산하지 않고 $CTX_HOME/device.json 에 캐싱.

`system_profiler SPHardwareDataType` 또는 ioreg 에서 Serial 을 읽는다.
비 macOS 환경(리눅스 테스트 등)에서는 hostname fallback.
"""

from __future__ import annotations

import hashlib
import json
import platform
import socket
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone

from ctx.config import Paths


@dataclass(frozen=True)
class Device:
    fingerprint: str
    source: str          # "macos-serial" | "hostname-fallback"
    hostname: str
    platform: str
    recorded_at: str     # ISO


def _read_macos_serial() -> str | None:
    # 경로 1: ioreg 전체 덤프 (non-utf8 바이트 포함 가능 → errors=replace).
    try:
        out = subprocess.run(
            ["ioreg", "-l"],
            capture_output=True, timeout=5,
        )
    except Exception:
        out = None
    if out is not None and out.returncode == 0:
        text = out.stdout.decode("utf-8", errors="replace")
        for line in text.splitlines():
            if "IOPlatformSerialNumber" in line:
                parts = line.split("=")
                if len(parts) == 2:
                    serial = parts[1].strip().strip('"')
                    if serial:
                        return serial

    # 경로 2: system_profiler 를 fallback. JSON 출력이라 파싱 안전.
    try:
        out = subprocess.run(
            ["system_profiler", "SPHardwareDataType", "-json"],
            capture_output=True, timeout=10,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    try:
        import json as _json
        data = _json.loads(out.stdout.decode("utf-8", errors="replace"))
        items = data.get("SPHardwareDataType", [])
        if items:
            serial = items[0].get("serial_number")
            if serial:
                return serial
    except Exception:
        return None
    return None


def get_or_init_device(paths: Paths | None = None) -> Device:
    paths = paths or Paths.load()
    paths.ensure()
    if paths.device.exists():
        data = json.loads(paths.device.read_text(encoding="utf-8"))
        return Device(**data)

    hostname = socket.gethostname()
    plat = platform.platform()
    if platform.system() == "Darwin":
        serial = _read_macos_serial()
        if serial:
            fp = hashlib.sha256(serial.encode("utf-8")).hexdigest()[:16]
            dev = Device(
                fingerprint=fp,
                source="macos-serial",
                hostname=hostname,
                platform=plat,
                recorded_at=datetime.now(timezone.utc).isoformat(),
            )
            paths.device.write_text(
                json.dumps(dev.__dict__, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return dev

    # fallback
    fp = hashlib.sha256(f"{hostname}|{plat}".encode("utf-8")).hexdigest()[:16]
    dev = Device(
        fingerprint=fp,
        source="hostname-fallback",
        hostname=hostname,
        platform=plat,
        recorded_at=datetime.now(timezone.utc).isoformat(),
    )
    paths.device.write_text(
        json.dumps(dev.__dict__, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return dev
