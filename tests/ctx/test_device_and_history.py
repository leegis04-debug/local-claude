"""fingerprint 결정성·감사 로그 무결성."""

from __future__ import annotations

import json
from pathlib import Path

from ctx.config import Paths
from ctx.device import get_or_init_device
from ctx.history import log_switch, read_history


def _paths(tmp_path: Path) -> Paths:
    return Paths(
        home=tmp_path,
        current=tmp_path / "current",
        contexts_dir=tmp_path / "contexts",
        history=tmp_path / "history.jsonl",
        device=tmp_path / "device.json",
    )


def test_device_fingerprint_is_cached_and_stable(tmp_path: Path):
    paths = _paths(tmp_path)
    d1 = get_or_init_device(paths)
    d2 = get_or_init_device(paths)
    assert d1.fingerprint == d2.fingerprint
    assert len(d1.fingerprint) == 16
    assert (tmp_path / "device.json").exists()


def test_history_append_only(tmp_path: Path):
    paths = _paths(tmp_path)
    paths.ensure()

    log_switch(to="home", from_=None, trigger="init", paths=paths)
    log_switch(to="office-daegyeom", from_="home", trigger="manual", paths=paths)
    log_switch(to="offline", from_="office-daegyeom", trigger="auto-wifi", paths=paths)

    entries = read_history(paths)
    assert len(entries) == 3
    assert entries[0]["trigger"] == "init"
    assert entries[1]["to"] == "office-daegyeom"
    assert entries[2]["trigger"] == "auto-wifi"
    # device_fp 가 모든 엔트리에 박혀 있음
    fps = {e["device_fp"] for e in entries}
    assert len(fps) == 1


def test_history_preserves_meta(tmp_path: Path):
    paths = _paths(tmp_path)
    paths.ensure()

    log_switch(
        to="home", from_="office-daegyeom", trigger="auto-wifi",
        meta={"ssid": "HomeWifi", "rssi": -45},
        paths=paths,
    )
    entries = read_history(paths)
    assert entries[0]["meta"] == {"ssid": "HomeWifi", "rssi": -45}
