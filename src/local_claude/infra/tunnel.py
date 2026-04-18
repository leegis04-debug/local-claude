"""SSH 포트 포워딩 — CLAUDE.md 1단계 폴백 (맥북이 외부 네트워크일 때).

`ssh -fN -L 8000:100.79.251.53:8000 -L 8765:... -L 8766:... ljw-op`
를 멱등하게 실행한다. 이미 떠 있으면 아무것도 안 한다.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
from dataclasses import dataclass
from typing import Iterable

from .. import config


@dataclass
class TunnelStatus:
    listening: dict[int, bool]  # local_port → 바인딩 여부
    all_up: bool


def _port_listening(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    """로컬 포트가 LISTEN 상태인지 TCP connect 로 확인.

    lsof/ss 호출보다 이식성·속도 모두 낫다. connect 성공 = 누가 LISTEN 중.
    """
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def status(ports: Iterable[int] | None = None) -> TunnelStatus:
    target_ports = list(ports) if ports is not None else [p[0] for p in config.SSH_TUNNEL_PORTS]
    listening = {port: _port_listening(port) for port in target_ports}
    return TunnelStatus(listening=listening, all_up=all(listening.values()))


def up(
    ssh_host: str | None = None,
    ports: Iterable[tuple[int, str, int]] | None = None,
    extra_ssh_args: list[str] | None = None,
) -> TunnelStatus:
    """SSH 터널을 올린다 (이미 떠 있으면 no-op).

    반환: up 이후 상태. all_up=False 면 ssh 실행 실패 또는 host 도달 불가.
    """
    ssh_bin = shutil.which("ssh")
    if ssh_bin is None:
        return status([p[0] for p in (ports or config.SSH_TUNNEL_PORTS)])

    target_ports = list(ports) if ports is not None else list(config.SSH_TUNNEL_PORTS)
    current = status([p[0] for p in target_ports])
    missing = [p for p in target_ports if not current.listening[p[0]]]
    if not missing:
        return current  # 이미 전부 LISTEN — 아무것도 안 함 (멱등).

    host = ssh_host or config.SSH_HOST
    cmd: list[str] = [ssh_bin, "-fN", "-o", "ExitOnForwardFailure=yes"]
    for local, remote_host, remote_port in missing:
        cmd += ["-L", f"{local}:{remote_host}:{remote_port}"]
    if extra_ssh_args:
        cmd += extra_ssh_args
    cmd.append(host)

    try:
        subprocess.run(cmd, check=False, timeout=10, capture_output=True)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return status([p[0] for p in target_ports])


def down(ports: Iterable[int] | None = None) -> int:
    """`ssh -fN -L <port>...` 프로세스를 종료한다. 종료된 PID 수 반환."""
    target_ports = list(ports) if ports is not None else [p[0] for p in config.SSH_TUNNEL_PORTS]
    if not shutil.which("pgrep") or not shutil.which("kill"):
        return 0
    # 대표 포트로 pgrep — 첫 번째 포트를 포함하는 ssh -fN 명령만 정리.
    pattern = rf"ssh -fN.*-L {target_ports[0]}:"
    try:
        result = subprocess.run(
            ["pgrep", "-f", pattern],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 0
    pids = [p for p in result.stdout.strip().splitlines() if p.strip().isdigit()]
    killed = 0
    for pid in pids:
        try:
            subprocess.run(["kill", pid], check=False, timeout=2)
            killed += 1
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return killed
