"""Phase 4 — 코드 레이어.

외부 네이티브 도구(tree-sitter/ctags/LSP) 의존 없이 동작한다.
Python 은 표준 ast 모듈로 완전 파싱, 다른 언어는 regex 로 선언 추출.
"""

from . import fs_index, git_diff, patch, symbols, test_runner

__all__ = ["fs_index", "git_diff", "patch", "symbols", "test_runner"]
