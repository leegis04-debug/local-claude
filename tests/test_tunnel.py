from __future__ import annotations

import socket
import threading
from typing import Iterator

import pytest

from local_claude.infra import tunnel


@pytest.fixture
def listener() -> Iterator[int]:
    """테스트용 로컬 포트 바인딩 — 실제 LISTEN 상태 재현."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    stop = threading.Event()

    def _accept_loop() -> None:
        sock.settimeout(0.1)
        while not stop.is_set():
            try:
                client, _ = sock.accept()
                client.close()
            except socket.timeout:
                continue
            except OSError:
                break

    t = threading.Thread(target=_accept_loop, daemon=True)
    t.start()
    try:
        yield port
    finally:
        stop.set()
        sock.close()


def test_port_listening_detects_open_port(listener: int) -> None:
    assert tunnel._port_listening(listener, timeout=1.0) is True


def test_port_listening_rejects_closed_port() -> None:
    # 1 은 reserved — 거의 확정적으로 닫혀있음.
    assert tunnel._port_listening(1, timeout=0.2) is False


def test_status_reports_per_port(listener: int, monkeypatch: pytest.MonkeyPatch) -> None:
    # 설정 포트를 테스트 포트로 치환
    monkeypatch.setattr(
        "local_claude.config.SSH_TUNNEL_PORTS",
        ((listener, "127.0.0.1", listener), (1, "127.0.0.1", 1)),
    )
    st = tunnel.status()
    assert st.listening[listener] is True
    assert st.listening[1] is False
    assert st.all_up is False


def test_up_skips_when_all_listening(
    listener: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **_: object) -> object:
        calls.append(cmd)

        class R:
            returncode = 0
            stdout = b""
            stderr = b""

        return R()

    monkeypatch.setattr("local_claude.infra.tunnel.subprocess.run", fake_run)
    monkeypatch.setattr(
        "local_claude.config.SSH_TUNNEL_PORTS",
        ((listener, "127.0.0.1", listener),),
    )
    st = tunnel.up()
    assert st.all_up is True
    # 이미 LISTEN — ssh 호출 안 했어야 함
    assert calls == []


def test_up_invokes_ssh_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "local_claude.config.SSH_TUNNEL_PORTS",
        ((1, "127.0.0.1", 1),),  # 닫힌 포트
    )
    cmds: list[list[str]] = []

    def fake_run(cmd: list[str], **_: object) -> object:
        cmds.append(cmd)

        class R:
            returncode = 0
            stdout = b""
            stderr = b""

        return R()

    monkeypatch.setattr("local_claude.infra.tunnel.subprocess.run", fake_run)
    monkeypatch.setattr("local_claude.infra.tunnel.shutil.which", lambda _n: "/usr/bin/ssh")

    tunnel.up(ssh_host="fake-host")
    assert len(cmds) == 1
    cmd = cmds[0]
    assert cmd[0] == "/usr/bin/ssh"
    assert "-fN" in cmd
    assert any("1:127.0.0.1:1" in a for a in cmd)
    assert cmd[-1] == "fake-host"


def test_up_noop_if_ssh_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("local_claude.infra.tunnel.shutil.which", lambda _n: None)
    monkeypatch.setattr(
        "local_claude.config.SSH_TUNNEL_PORTS", ((1, "127.0.0.1", 1),)
    )
    called = False

    def fake_run(*_a: object, **_k: object) -> object:
        nonlocal called
        called = True

        class R:
            returncode = 0

        return R()

    monkeypatch.setattr("local_claude.infra.tunnel.subprocess.run", fake_run)
    st = tunnel.up()
    assert st.all_up is False
    assert called is False
