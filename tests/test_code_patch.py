from __future__ import annotations

from local_claude.code import patch


def test_make_diff_and_apply_roundtrip() -> None:
    a = "line1\nline2\nline3\nline4\nline5\n"
    b = "line1\nLINE2\nline3\nline4\nline5\nline6\n"
    diff = patch.make_diff(a, b, from_file="a.py", to_file="b.py")
    assert "-line2" in diff
    assert "+LINE2" in diff
    assert "+line6" in diff
    result = patch.apply_diff(a, diff)
    assert result == b


def test_apply_noop_when_diff_empty() -> None:
    a = "x\ny\n"
    assert patch.apply_diff(a, "") == a


def test_multiple_hunks_applied_correctly() -> None:
    a = "\n".join(f"l{i}" for i in range(1, 21)) + "\n"
    b_lines = a.splitlines()
    b_lines[2] = "l3_changed"
    b_lines[15] = "l16_changed"
    b = "\n".join(b_lines) + "\n"
    diff = patch.make_diff(a, b)
    result = patch.apply_diff(a, diff)
    assert result == b


def test_invalid_hunk_raises(tmp_path) -> None:
    a = "x\n"
    bad_diff = (
        "--- a\n+++ b\n"
        "@@ -99,3 +99,3 @@\n"
        " context\n"
        "-old\n"
        "+new\n"
    )
    try:
        patch.apply_diff(a, bad_diff)
        raise AssertionError("expected PatchError")
    except patch.PatchError:
        pass


def test_make_diff_identical_is_empty() -> None:
    text = "a\nb\nc\n"
    assert patch.make_diff(text, text) == ""
