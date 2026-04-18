"""context TOML I/O 단위 테스트."""

from __future__ import annotations

from pathlib import Path

from ctx.config import Paths
from ctx.context import (
    ContextNotFound,
    get_key_path,
    list_context_names,
    load_context,
    write_context,
)


def _paths(tmp_path: Path) -> Paths:
    return Paths(
        home=tmp_path,
        current=tmp_path / "current",
        contexts_dir=tmp_path / "contexts",
        history=tmp_path / "history.jsonl",
        device=tmp_path / "device.json",
    )


def test_write_and_load_roundtrip(tmp_path: Path):
    paths = _paths(tmp_path)
    data = {
        "display": "🏢 다겸",
        "color": "#0066cc",
        "wifi": ["A", "B"],
        "gstar": {"namespace": "daegyeom"},
        "jw": {"rag_enabled": True},
    }
    write_context("office-daegyeom", data, paths)
    loaded = load_context("office-daegyeom", paths)
    assert loaded["display"] == "🏢 다겸"
    assert loaded["wifi"] == ["A", "B"]
    assert loaded["gstar"]["namespace"] == "daegyeom"
    assert loaded["jw"]["rag_enabled"] is True


def test_load_missing_raises(tmp_path: Path):
    paths = _paths(tmp_path)
    paths.ensure()
    try:
        load_context("nope", paths)
    except ContextNotFound:
        return
    raise AssertionError("ContextNotFound 기대")


def test_list_context_names(tmp_path: Path):
    paths = _paths(tmp_path)
    write_context("a", {"x": 1}, paths)
    write_context("b", {"x": 2}, paths)
    assert list_context_names(paths) == ["a", "b"]


def test_get_key_path():
    data = {"gstar": {"namespace": "personal", "nested": {"deep": 42}}}
    assert get_key_path(data, "gstar.namespace") == "personal"
    assert get_key_path(data, "gstar.nested.deep") == 42
    try:
        get_key_path(data, "gstar.missing")
    except KeyError:
        return
    raise AssertionError("KeyError 기대")
