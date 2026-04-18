"""심볼 인덱스 — zero-dep.

Python: 표준 `ast` 로 class/function/method/const/assign 완전 추출.
다른 언어: 언어별 정규식(JS/TS, Go, Rust, Java). 부분적이지만 "어디 정의?"
질문엔 충분.

언어가 식별 안 되면 범용 regex 로 `function|def|class|func|fn|type` 근처의
식별자만 스캔한다.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# ── 언어 자동 감지 ──────────────────────────────────────────────────────────

_EXT_LANG: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "javascript",
    ".tsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "java",
    ".rb": "generic",
    ".php": "generic",
    ".c": "generic",
    ".cpp": "generic",
    ".cc": "generic",
    ".h": "generic",
    ".hpp": "generic",
}


def detect_language(path: Path | str) -> str:
    return _EXT_LANG.get(Path(path).suffix.lower(), "generic")


# ── 데이터 모델 ─────────────────────────────────────────────────────────────

@dataclass
class Symbol:
    name: str
    kind: str  # "class" | "function" | "method" | "const" | "type" | "struct"
    file: str  # 프로젝트 루트 기준 상대경로
    line: int
    parent: str | None = None
    language: str = "generic"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "file": self.file,
            "line": self.line,
            "parent": self.parent,
            "language": self.language,
        }


@dataclass
class SymbolIndex:
    symbols: list[Symbol] = field(default_factory=list)

    # 조회 편의 메서드
    def by_file(self, file: str) -> list[Symbol]:
        return [s for s in self.symbols if s.file == file]

    def by_name(self, name: str) -> list[Symbol]:
        return [s for s in self.symbols if s.name == name]

    def names(self) -> set[str]:
        return {s.name for s in self.symbols}

    def to_list(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self.symbols]


# ── Python (ast) ────────────────────────────────────────────────────────────

def _parse_python(source: str, rel_path: str) -> list[Symbol]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    out: list[Symbol] = []

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self._stack: list[str] = []

        def _parent(self) -> str | None:
            return self._stack[-1] if self._stack else None

        def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
            out.append(
                Symbol(
                    name=node.name,
                    kind="class",
                    file=rel_path,
                    line=node.lineno,
                    parent=self._parent(),
                    language="python",
                )
            )
            self._stack.append(node.name)
            self.generic_visit(node)
            self._stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
            kind = "method" if self._parent() else "function"
            out.append(
                Symbol(
                    name=node.name,
                    kind=kind,
                    file=rel_path,
                    line=node.lineno,
                    parent=self._parent(),
                    language="python",
                )
            )
            # 함수 내부 중첩 def 는 parent 로 이 함수를 두지 않음 — 단순화.
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
            self.visit_FunctionDef(node)  # type: ignore[arg-type]

        def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
            # 모듈 레벨(스택 빔) 의 UPPER_CASE 상수만 수집.
            if self._stack:
                return
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    out.append(
                        Symbol(
                            name=target.id,
                            kind="const",
                            file=rel_path,
                            line=node.lineno,
                            language="python",
                        )
                    )
            self.generic_visit(node)

        def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
            if self._stack:
                return
            if isinstance(node.target, ast.Name) and node.target.id.isupper():
                out.append(
                    Symbol(
                        name=node.target.id,
                        kind="const",
                        file=rel_path,
                        line=node.lineno,
                        language="python",
                    )
                )
            self.generic_visit(node)

    _Visitor().visit(tree)
    return out


# ── Regex 파서 ──────────────────────────────────────────────────────────────

_JS_RE = [
    (re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.M), "function"),
    (re.compile(r"^\s*(?:export\s+)?class\s+(\w+)", re.M), "class"),
    (re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Z_][A-Z0-9_]{1,})\s*=", re.M), "const"),
    (re.compile(r"^\s*(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:async\s*)?\(.*?\)\s*=>", re.M), "function"),
]

_GO_RE = [
    (re.compile(r"^\s*func\s+(?:\([^)]*\)\s+)?(\w+)\s*\(", re.M), "function"),
    (re.compile(r"^\s*type\s+(\w+)\s+struct\b", re.M), "struct"),
    (re.compile(r"^\s*type\s+(\w+)\s+interface\b", re.M), "type"),
]

_RUST_RE = [
    (re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)", re.M), "function"),
    (re.compile(r"^\s*(?:pub\s+)?struct\s+(\w+)", re.M), "struct"),
    (re.compile(r"^\s*(?:pub\s+)?enum\s+(\w+)", re.M), "type"),
    (re.compile(r"^\s*(?:pub\s+)?trait\s+(\w+)", re.M), "type"),
]

_JAVA_RE = [
    (re.compile(r"^\s*(?:public|private|protected|abstract|static|final|\s)+class\s+(\w+)", re.M), "class"),
    (re.compile(r"^\s*(?:public|private|protected|abstract|static|final|\s)+interface\s+(\w+)", re.M), "type"),
    (re.compile(
        r"^\s*(?:public|private|protected|static|final|synchronized|abstract|\s)+"
        r"(?:<[^>]+>\s+)?[\w<>\[\],\s]+\s+(\w+)\s*\([^)]*\)\s*(?:throws[^{]+)?\{",
        re.M,
    ), "method"),
]

_GENERIC_RE = [
    (re.compile(r"^\s*(?:def|function|func|fn)\s+(\w+)", re.M), "function"),
    (re.compile(r"^\s*(?:class|struct|type)\s+(\w+)", re.M), "class"),
]

_LANG_TO_RULES: dict[str, list[tuple[re.Pattern[str], str]]] = {
    "javascript": _JS_RE,
    "go": _GO_RE,
    "rust": _RUST_RE,
    "java": _JAVA_RE,
    "generic": _GENERIC_RE,
}


def _line_of_pos(source: str, pos: int) -> int:
    return source.count("\n", 0, pos) + 1


def _parse_regex(source: str, rel_path: str, language: str) -> list[Symbol]:
    rules = _LANG_TO_RULES.get(language, _GENERIC_RE)
    out: list[Symbol] = []
    seen: set[tuple[str, int]] = set()
    for pattern, kind in rules:
        for match in pattern.finditer(source):
            name = match.group(1)
            line = _line_of_pos(source, match.start())
            key = (name, line)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                Symbol(
                    name=name,
                    kind=kind,
                    file=rel_path,
                    line=line,
                    language=language,
                )
            )
    return out


# ── public API ──────────────────────────────────────────────────────────────

def parse_file(path: Path, *, project_root: Path | None = None) -> list[Symbol]:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return []
    root = project_root or path.parent
    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = str(path)
    language = detect_language(path)
    if language == "python":
        return _parse_python(source, rel)
    return _parse_regex(source, rel, language)


def build_index(files: Iterable[Path], *, project_root: Path) -> SymbolIndex:
    index = SymbolIndex()
    for path in files:
        if not path.is_file():
            continue
        index.symbols.extend(parse_file(path, project_root=project_root))
    return index
