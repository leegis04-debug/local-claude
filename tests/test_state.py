from __future__ import annotations

import json
from pathlib import Path

from local_claude.state import compressor


def test_load_missing_returns_default(tmp_path: Path) -> None:
    s = compressor.load(tmp_path)
    assert s.project == tmp_path.name
    assert s.updated_at
    assert s.current_goal == ""


def test_update_roundtrip(tmp_path: Path) -> None:
    compressor.update(tmp_path, current_goal="foo", next_action="bar")
    s = compressor.load(tmp_path)
    assert s.current_goal == "foo"
    assert s.next_action == "bar"


def test_list_append_and_dedup(tmp_path: Path) -> None:
    compressor.update(tmp_path, recent_failures="f1")
    compressor.update(tmp_path, recent_failures="f2")
    compressor.update(tmp_path, recent_failures="f1")  # dedup → 맨 뒤로 이동
    s = compressor.load(tmp_path)
    assert s.recent_failures == ["f2", "f1"]


def test_list_limit_trim(tmp_path: Path) -> None:
    # recent_failures 상한 10
    for i in range(15):
        compressor.update(tmp_path, recent_failures=f"f{i}")
    s = compressor.load(tmp_path)
    assert len(s.recent_failures) == 10
    assert s.recent_failures[0] == "f5"
    assert s.recent_failures[-1] == "f14"


def test_atomic_write_no_tmp_leftover(tmp_path: Path) -> None:
    compressor.update(tmp_path, current_goal="x")
    assert (tmp_path / "state.json").exists()
    assert not (tmp_path / "state.json.tmp").exists()


def test_corrupt_file_recovery(tmp_path: Path) -> None:
    (tmp_path / "state.json").write_text("not json at all")
    s = compressor.load(tmp_path)
    assert s.project == tmp_path.name
    assert (tmp_path / "state.json.corrupt").exists()


def test_update_preserves_other_fields(tmp_path: Path) -> None:
    compressor.update(tmp_path, current_goal="G", decisions="D1")
    compressor.update(tmp_path, next_action="N")
    s = compressor.load(tmp_path)
    assert s.current_goal == "G"
    assert s.decisions == ["D1"]
    assert s.next_action == "N"


def test_serialized_is_valid_json(tmp_path: Path) -> None:
    compressor.update(tmp_path, current_goal="G")
    data = json.loads((tmp_path / "state.json").read_text())
    assert data["current_goal"] == "G"
    assert "updated_at" in data
