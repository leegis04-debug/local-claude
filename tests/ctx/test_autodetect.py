"""ctx/autodetect 단위 테스트."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ctx.autodetect import autodetect, match_context, should_auto_switch
from ctx.config import Paths
from ctx.context import current_context_name, set_current_context_name, write_context
from ctx.history import log_switch


def _paths(tmp_path: Path) -> Paths:
    return Paths(
        home=tmp_path,
        current=tmp_path / "current",
        contexts_dir=tmp_path / "contexts",
        history=tmp_path / "history.jsonl",
        device=tmp_path / "device.json",
    )


def _seed_contexts(paths: Paths):
    paths.ensure()
    write_context(
        "home",
        {"display": "🏠", "color": "#0a0", "wifi": ["HomeWifi"]},
        paths,
    )
    write_context(
        "office-daegyeom",
        {"display": "🏢", "color": "#00f", "wifi": ["Daegyeom-Wifi", "Daegyeom-Guest"]},
        paths,
    )
    write_context(
        "offline",
        {"display": "✈️", "color": "#888", "wifi": []},
        paths,
    )
    set_current_context_name("home", paths)


def test_match_context_direct(tmp_path: Path):
    paths = _paths(tmp_path)
    _seed_contexts(paths)
    assert match_context("HomeWifi", paths) == "home"
    assert match_context("Daegyeom-Wifi", paths) == "office-daegyeom"
    assert match_context("Daegyeom-Guest", paths) == "office-daegyeom"
    assert match_context("Unknown-SSID", paths) is None
    assert match_context(None, paths) is None


def test_should_auto_switch_default_true(tmp_path: Path):
    paths = _paths(tmp_path)
    paths.ensure()
    assert should_auto_switch(paths) is True


def test_should_auto_switch_blocked_by_recent_manual(tmp_path: Path):
    paths = _paths(tmp_path)
    paths.ensure()
    log_switch(to="offline", from_="home", trigger="manual", paths=paths)
    assert should_auto_switch(paths) is False


def test_should_auto_switch_allows_after_cooldown(tmp_path: Path, monkeypatch):
    paths = _paths(tmp_path)
    paths.ensure()
    log_switch(to="offline", from_="home", trigger="manual", paths=paths)
    # 15분 뒤엔 허용
    future = datetime.now(timezone.utc) + timedelta(minutes=15)
    assert should_auto_switch(paths, now=future) is True


def test_should_auto_switch_ignores_auto_wifi_trigger(tmp_path: Path):
    paths = _paths(tmp_path)
    paths.ensure()
    # 방금 auto-wifi 로 전환한 엔트리는 쿨다운 발동 안 해야 함
    log_switch(to="home", from_=None, trigger="auto-wifi", paths=paths)
    assert should_auto_switch(paths) is True


def test_autodetect_no_ssid(tmp_path: Path, monkeypatch):
    paths = _paths(tmp_path)
    _seed_contexts(paths)
    monkeypatch.setenv("CTX_HOME", str(tmp_path))
    monkeypatch.setattr("ctx.autodetect.current_ssid", lambda: None)
    res = autodetect(apply=True)
    assert res.action == "no-ssid"
    assert current_context_name(paths) == "home"  # 변경 없음


def test_autodetect_no_match(tmp_path: Path, monkeypatch):
    paths = _paths(tmp_path)
    _seed_contexts(paths)
    monkeypatch.setenv("CTX_HOME", str(tmp_path))
    monkeypatch.setattr("ctx.autodetect.current_ssid", lambda: "Coffee-Shop-Wifi")
    res = autodetect(apply=True)
    assert res.action == "no-match"
    assert current_context_name(paths) == "home"


def test_autodetect_switches_to_matched(tmp_path: Path, monkeypatch):
    paths = _paths(tmp_path)
    _seed_contexts(paths)
    monkeypatch.setenv("CTX_HOME", str(tmp_path))
    monkeypatch.setattr("ctx.autodetect.current_ssid", lambda: "Daegyeom-Wifi")
    res = autodetect(apply=True)
    assert res.action == "switched"
    assert res.matched == "office-daegyeom"
    assert current_context_name(paths) == "office-daegyeom"


def test_autodetect_dry_run_does_not_switch(tmp_path: Path, monkeypatch):
    paths = _paths(tmp_path)
    _seed_contexts(paths)
    monkeypatch.setenv("CTX_HOME", str(tmp_path))
    monkeypatch.setattr("ctx.autodetect.current_ssid", lambda: "Daegyeom-Wifi")
    res = autodetect(apply=False)
    assert res.action == "switched"
    assert current_context_name(paths) == "home"  # 적용 안 됨


def test_autodetect_respects_cooldown(tmp_path: Path, monkeypatch):
    paths = _paths(tmp_path)
    _seed_contexts(paths)
    monkeypatch.setenv("CTX_HOME", str(tmp_path))
    # 사용자가 방금 수동 전환 → 자동 감지 skip
    log_switch(to="offline", from_="home", trigger="manual", paths=paths)
    set_current_context_name("offline", paths)
    monkeypatch.setattr("ctx.autodetect.current_ssid", lambda: "Daegyeom-Wifi")
    res = autodetect(apply=True)
    assert res.action == "cooldown"
    assert current_context_name(paths) == "offline"


def test_autodetect_force_overrides_cooldown(tmp_path: Path, monkeypatch):
    paths = _paths(tmp_path)
    _seed_contexts(paths)
    monkeypatch.setenv("CTX_HOME", str(tmp_path))
    log_switch(to="offline", from_="home", trigger="manual", paths=paths)
    set_current_context_name("offline", paths)
    monkeypatch.setattr("ctx.autodetect.current_ssid", lambda: "Daegyeom-Wifi")
    res = autodetect(apply=True, force=True)
    assert res.action == "switched"
    assert current_context_name(paths) == "office-daegyeom"


def test_autodetect_same_ssid_as_current(tmp_path: Path, monkeypatch):
    paths = _paths(tmp_path)
    _seed_contexts(paths)
    monkeypatch.setenv("CTX_HOME", str(tmp_path))
    monkeypatch.setattr("ctx.autodetect.current_ssid", lambda: "HomeWifi")
    res = autodetect(apply=True)
    assert res.action == "same"
    assert current_context_name(paths) == "home"
