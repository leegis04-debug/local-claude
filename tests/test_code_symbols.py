from __future__ import annotations

from pathlib import Path

from local_claude.code import symbols


def test_detect_language() -> None:
    assert symbols.detect_language("x.py") == "python"
    assert symbols.detect_language("x.tsx") == "javascript"
    assert symbols.detect_language("x.go") == "go"
    assert symbols.detect_language("x.rs") == "rust"
    assert symbols.detect_language("x.java") == "java"
    assert symbols.detect_language("Makefile") == "generic"


def test_python_ast_extracts_classes_methods_functions(tmp_path: Path) -> None:
    src = '''\
"""module"""

CONFIG_VALUE = 42
lower_case = 1  # 무시되어야 함

def top_level():
    return 1

async def async_fn():
    pass

class Foo:
    def method_one(self):
        return 2

    async def method_two(self):
        return 3

    class Inner:
        def inner_method(self):
            pass
'''
    path = tmp_path / "sample.py"
    path.write_text(src, encoding="utf-8")

    result = symbols.parse_file(path, project_root=tmp_path)
    by_name = {s.name: s for s in result}

    assert by_name["CONFIG_VALUE"].kind == "const"
    assert "lower_case" not in by_name  # 소문자 할당은 제외

    assert by_name["top_level"].kind == "function"
    assert by_name["async_fn"].kind == "function"

    assert by_name["Foo"].kind == "class"
    assert by_name["method_one"].kind == "method"
    assert by_name["method_one"].parent == "Foo"
    assert by_name["method_two"].kind == "method"

    assert by_name["Inner"].parent == "Foo"
    assert by_name["inner_method"].parent == "Inner"
    assert all(s.language == "python" for s in result)


def test_python_syntax_error_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "broken.py"
    path.write_text("def oops(:\n  pass", encoding="utf-8")
    assert symbols.parse_file(path, project_root=tmp_path) == []


def test_javascript_regex_extracts_class_function_const(tmp_path: Path) -> None:
    src = """\
export class Widget { }

function helper() { return 1; }
export async function fetchData() { }

const API_URL = "https://x";
const lowered = () => {};
export const doThing = async () => { };
"""
    path = tmp_path / "sample.ts"
    path.write_text(src, encoding="utf-8")
    out = symbols.parse_file(path, project_root=tmp_path)
    names = {(s.name, s.kind) for s in out}
    assert ("Widget", "class") in names
    assert ("helper", "function") in names
    assert ("fetchData", "function") in names
    assert ("API_URL", "const") in names
    assert ("doThing", "function") in names  # arrow = async function
    assert all(s.language == "javascript" for s in out)


def test_go_regex_extracts_function_and_struct(tmp_path: Path) -> None:
    src = """\
package main

func main() { }
func (r *Repo) Save() { }

type User struct {
    ID int
}

type Repo interface {
    Save() error
}
"""
    path = tmp_path / "sample.go"
    path.write_text(src, encoding="utf-8")
    out = symbols.parse_file(path, project_root=tmp_path)
    names_kinds = {(s.name, s.kind) for s in out}
    assert ("main", "function") in names_kinds
    assert ("Save", "function") in names_kinds
    assert ("User", "struct") in names_kinds
    assert ("Repo", "type") in names_kinds


def test_rust_regex_extracts_fn_struct_trait(tmp_path: Path) -> None:
    src = """\
pub fn run(x: i32) -> i32 { x + 1 }
async fn fetch() { }
pub struct Config { }
pub enum Mode { A, B }
pub trait Handler { }
"""
    path = tmp_path / "sample.rs"
    path.write_text(src, encoding="utf-8")
    out = symbols.parse_file(path, project_root=tmp_path)
    kinds = {s.name: s.kind for s in out}
    assert kinds["run"] == "function"
    assert kinds["fetch"] == "function"
    assert kinds["Config"] == "struct"
    assert kinds["Mode"] == "type"
    assert kinds["Handler"] == "type"


def test_build_index_aggregates_multiple_files(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def a_fn():\n  pass\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("class Bee:\n  pass\n", encoding="utf-8")
    idx = symbols.build_index(list(tmp_path.glob("*.py")), project_root=tmp_path)
    assert len(idx.symbols) == 2
    assert idx.names() == {"a_fn", "Bee"}
    assert len(idx.by_file("a.py")) == 1
    assert len(idx.by_file("b.py")) == 1
