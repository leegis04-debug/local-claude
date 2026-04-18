from __future__ import annotations

from pathlib import Path

import pytest

from local_claude.reinforce import policy


@pytest.fixture
def isolated_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("local_claude.config.LC_DATA_DIR", tmp_path / "lc-data")
    return tmp_path / "lc-data" / "policy"


def test_set_new_policy_no_backup(isolated_policy: Path) -> None:
    rev = policy.set_policy("greet", "hello world")
    assert rev == 0
    assert policy.show("greet") == "hello world"
    assert policy.list_revisions("greet") == []


def test_set_existing_policy_backs_up(isolated_policy: Path) -> None:
    policy.set_policy("p", "v1")
    rev = policy.set_policy("p", "v2")
    assert rev == 1
    rev2 = policy.set_policy("p", "v3")
    assert rev2 == 2
    assert policy.list_revisions("p") == [1, 2]
    assert policy.show_revision("p", 1) == "v1"
    assert policy.show_revision("p", 2) == "v2"
    assert policy.show("p") == "v3"


def test_rollback_restores_and_rebackups(isolated_policy: Path) -> None:
    policy.set_policy("p", "v1")
    policy.set_policy("p", "v2")
    # v1 으로 롤백 — 현재 v2 는 새 rev 로 백업됨
    assert policy.rollback("p", 1) is True
    assert policy.show("p") == "v1"
    revs = policy.list_revisions("p")
    assert 1 in revs
    # v2 가 새 rev 로 백업됐는지 (rev 2 는 원래도 있었고, rev 3 이 새로 추가됨)
    assert len(revs) >= 2


def test_rollback_nonexistent_returns_false(isolated_policy: Path) -> None:
    policy.set_policy("p", "v1")
    assert policy.rollback("p", 99) is False


def test_diff_shows_changes(isolated_policy: Path) -> None:
    policy.set_policy("p", "line1\nline2\n")
    policy.set_policy("p", "line1\nCHANGED\nline3\n")
    diff = policy.diff_revision("p", 1)
    assert "-line2" in diff
    assert "+CHANGED" in diff


def test_invalid_name_rejected(isolated_policy: Path) -> None:
    with pytest.raises(ValueError):
        policy.set_policy("bad/name", "x")
    with pytest.raises(ValueError):
        policy.set_policy("", "x")


def test_list_policies(isolated_policy: Path) -> None:
    policy.set_policy("a", "x")
    policy.set_policy("b", "y")
    policy.set_policy("b", "y2")  # rev 1
    infos = {p.name: p for p in policy.list_policies()}
    assert "a" in infos
    assert "b" in infos
    assert infos["b"].revision_count == 1
    assert infos["a"].revision_count == 0


def test_export_and_import(isolated_policy: Path) -> None:
    policy.set_policy("p1", "A")
    policy.set_policy("p2", "B")
    exported = policy.export_all()
    assert exported == {"p1": "A", "p2": "B"}

    # 새 내용 일괄 import
    policy.import_from_iter([("p1", "A2"), ("p2", "B2")])
    assert policy.show("p1") == "A2"
    assert policy.show("p2") == "B2"
    # 이전 내용은 revision 으로 남음
    assert 1 in policy.list_revisions("p1")
