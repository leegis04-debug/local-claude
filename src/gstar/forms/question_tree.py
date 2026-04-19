"""Phase P-Q1 — 양식 md (template-full.md) → 질문 트리 추출.

양식의 모든 목차(# ## ###)·표 헤더·첫 컬럼 셀은 **답해야 할 질문** 이라는 관점.
이 모듈은 md 텍스트를 받아 QuestionNode 트리를 만든다.

추출 규칙
---------
1. `#`/`##`/`###`/`####` 등 heading → QuestionNode(type="heading", depth=1~N)
2. markdown 표 (GitHub-style `| ... |`) →
   - 헤더 행의 각 컬럼명 → QuestionNode(type="table_header")
   - 첫 컬럼 셀 텍스트 (라벨 역할) → QuestionNode(type="table_row_label")
   - 빈 셀·`□ ...` / `(   )` 형태 → QuestionNode(type="table_cell_answer_slot")
3. 빈 라인·plain 텍스트는 무시. 표와 heading 만 질문으로 취급.

노드 id 는 안정적 hash-like 문자열 (path 기반) + 전체 순서 seq.

사용 예
-------
    tree = parse_questions_from_md(Path("template-full.md").read_text())
    for q in tree.iter_leaves():
        print(q.path, q.title)
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


# ---------- 모델 ------------------------------------------------------------


@dataclass
class QuestionNode:
    id: str                              # stable hash-like id
    seq: int                             # 전체 등장 순서 (0-base)
    path: str                            # "Heading1 > Heading2 > 표1 > row:2" 같은 breadcrumb
    title: str                           # 질문 본문 (한 줄)
    type: str                            # heading | table_header | table_row_label | table_cell_answer_slot
    depth: int                           # heading 용 (1~6), 표는 0
    parent_id: str | None = None
    line_no: int = 0                     # md 원문 줄 번호 (1-base)
    extras: dict = field(default_factory=dict)  # heading_text, table_seq, col_idx 등 원시 메타


@dataclass
class QuestionTree:
    nodes: list[QuestionNode] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.nodes)

    def by_id(self, nid: str) -> QuestionNode | None:
        return next((n for n in self.nodes if n.id == nid), None)

    def children_of(self, nid: str) -> list[QuestionNode]:
        return [n for n in self.nodes if n.parent_id == nid]

    def iter_leaves(self) -> Iterator[QuestionNode]:
        """자식 없는 노드 = 실제 답변 대상 질문."""
        ids_with_children = {n.parent_id for n in self.nodes if n.parent_id}
        for n in self.nodes:
            if n.id not in ids_with_children:
                yield n

    def by_type(self, t: str) -> list[QuestionNode]:
        return [n for n in self.nodes if n.type == t]

    def stats(self) -> dict[str, int]:
        s: dict[str, int] = {}
        for n in self.nodes:
            s[n.type] = s.get(n.type, 0) + 1
        s["total"] = len(self.nodes)
        s["leaves"] = sum(1 for _ in self.iter_leaves())
        return s


# ---------- 파서 ------------------------------------------------------------

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")


def _mk_id(path: str, seq: int) -> str:
    h = hashlib.sha1(f"{path}#{seq}".encode("utf-8")).hexdigest()[:10]
    return f"q_{seq:04d}_{h}"


def _clean_cell(text: str) -> str:
    return text.strip().replace("\u00a0", " ")


def _is_answer_slot(cell: str) -> bool:
    """빈 셀이나 체크박스/괄호 플레이스홀더 → 답변 슬롯."""
    c = cell.strip()
    if not c:
        return True
    # 모든 셀이 '□' 체크박스로만 이뤄졌거나 '(  )' 만 있는 경우
    stripped = re.sub(r"[□☐\(\)\s·_]+", "", c)
    if not stripped:
        return True
    return False


def _parse_table_row(line: str) -> list[str] | None:
    m = _TABLE_ROW_RE.match(line)
    if not m:
        return None
    inner = m.group(1)
    cells = [_clean_cell(c) for c in inner.split("|")]
    return cells


def parse_questions_from_md(
    md_text: str,
    *,
    min_title_len: int = 1,
    skip_boilerplate_headings: tuple[str, ...] = (),
) -> QuestionTree:
    """md → QuestionTree.

    `skip_boilerplate_headings` 에 들어있는 정확한 heading title 은 제외 (문서 메타 등).
    """
    tree = QuestionTree()
    lines = md_text.splitlines()

    # heading stack — 현재 섹션 경로 유지 (depth → last node id)
    heading_stack: list[tuple[int, str, str]] = []   # (depth, title, node_id)
    seq = 0

    # 표 파싱 상태
    in_table = False
    table_header: list[str] | None = None
    table_parent_id: str | None = None
    table_seq = 0  # 이 섹션 내 표 번호

    def _breadcrumb() -> str:
        return " > ".join(t for _, t, _ in heading_stack)

    def _push(node: QuestionNode):
        nonlocal seq
        tree.nodes.append(node)
        seq += 1

    for idx, raw in enumerate(lines, start=1):
        line = raw.rstrip()
        if not line.strip():
            # 빈 줄 → 표 종료
            if in_table:
                in_table = False
                table_header = None
            continue

        # -- heading?
        hm = _HEADING_RE.match(line)
        if hm:
            # 진행 중이던 표 종료
            in_table = False
            table_header = None

            depth = len(hm.group(1))
            title = hm.group(2).strip()
            if title in skip_boilerplate_headings:
                continue
            if len(title) < min_title_len:
                continue
            # stack 정리 — depth 이상 pop
            while heading_stack and heading_stack[-1][0] >= depth:
                heading_stack.pop()

            parent_id = heading_stack[-1][2] if heading_stack else None
            path = (_breadcrumb() + " > " + title).strip(" >")
            node_id = _mk_id(path, seq)
            node = QuestionNode(
                id=node_id,
                seq=seq,
                path=path,
                title=title,
                type="heading",
                depth=depth,
                parent_id=parent_id,
                line_no=idx,
                extras={"heading_level": depth},
            )
            _push(node)
            heading_stack.append((depth, title, node_id))
            table_seq = 0  # heading 바뀌면 표 카운터 리셋
            continue

        # -- table ?
        row_cells = _parse_table_row(line)
        if row_cells is None:
            # 표 중이었는데 비-표 줄이 나오면 표 종료
            if in_table:
                in_table = False
                table_header = None
            continue

        # separator 행 (---|---) → 헤더 다음 행. 그냥 패스.
        if _TABLE_SEP_RE.match(line):
            continue

        if not in_table:
            # 새 표 시작 — 이 행이 헤더
            in_table = True
            table_seq += 1
            table_header = row_cells
            parent_id = heading_stack[-1][2] if heading_stack else None
            # 표 자체를 "표 N" 노드로 묶기
            path = (_breadcrumb() + f" > 표{table_seq}").strip(" >")
            table_node_id = _mk_id(path, seq)
            table_node = QuestionNode(
                id=table_node_id,
                seq=seq,
                path=path,
                title=f"표{table_seq}: {row_cells[0][:40]}" if row_cells else f"표{table_seq}",
                type="table",
                depth=0,
                parent_id=parent_id,
                line_no=idx,
                extras={"table_seq": table_seq, "header_cells": row_cells},
            )
            _push(table_node)
            table_parent_id = table_node_id

            # 헤더 셀 각각을 table_header 질문으로
            for ci, cell in enumerate(row_cells):
                if not cell or _is_answer_slot(cell):
                    continue
                cpath = f"{path} > header:{cell[:30]}"
                cid = _mk_id(cpath, seq)
                _push(QuestionNode(
                    id=cid,
                    seq=seq,
                    path=cpath,
                    title=cell,
                    type="table_header",
                    depth=0,
                    parent_id=table_parent_id,
                    line_no=idx,
                    extras={"col_idx": ci},
                ))
            continue

        # 표 데이터 행
        # 첫 컬럼 = 라벨 질문, 나머지 = 답 슬롯 (혹은 선택지 묶음)
        if table_header is None or not row_cells:
            continue
        row_label = row_cells[0] if row_cells else ""
        parent = table_parent_id
        if row_label and not _is_answer_slot(row_label):
            path_row = f"{_breadcrumb()} > 표{table_seq} > row:{row_label[:30]}"
            row_id = _mk_id(path_row, seq)
            _push(QuestionNode(
                id=row_id,
                seq=seq,
                path=path_row,
                title=row_label,
                type="table_row_label",
                depth=0,
                parent_id=parent,
                line_no=idx,
                extras={},
            ))
            parent = row_id

        # 나머지 컬럼 — 빈 셀이나 체크박스형이면 답 슬롯으로 기록
        for ci, cell in enumerate(row_cells[1:], start=1):
            if _is_answer_slot(cell):
                header_title = (
                    table_header[ci] if ci < len(table_header) else f"col{ci}"
                )
                path_a = f"{_breadcrumb()} > 표{table_seq} > slot:{header_title[:20]}:{ci}"
                aid = _mk_id(path_a, seq)
                _push(QuestionNode(
                    id=aid,
                    seq=seq,
                    path=path_a,
                    title=f"{row_label or '-'} / {header_title or f'col{ci}'}",
                    type="table_cell_answer_slot",
                    depth=0,
                    parent_id=parent,
                    line_no=idx,
                    extras={"col_idx": ci, "header": header_title, "raw_cell": cell},
                ))

    return tree


def load_questions(md_path: Path, **kwargs) -> QuestionTree:
    """파일 경로로 로드."""
    return parse_questions_from_md(md_path.read_text(encoding="utf-8"), **kwargs)
