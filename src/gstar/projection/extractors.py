"""확장자별 텍스트 추출기 — jw/re 프로젝트 폴더의 비-text 파일 흡수.

asst/project.sh 가 만드는 구조 기준:
- `00-input/BASE/` — 공고문 PDF, 양식 HWPX, 킥오프 DOCX, 기업 PPTX
- `00-input/REF/` — 참고 PDF
- `00-input/ING/` — 진행 중 MD

각 extractor 는 `(text, meta)` 반환. 실패 시 `("", {"error": ...})`.
추출 실패는 파이프라인을 멈추지 않음 — 부분 텍스트라도 쓸 수 있게.
"""

from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_SUPPORTED_EXTS: set[str] = {
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".pdf",
    ".hwpx",
    ".docx",
    ".xlsx",
    ".csv",
}

_MAX_PDF_PAGES_DEFAULT = 80
_MAX_CHARS_PER_FILE_DEFAULT = 60_000


@dataclass
class ExtractResult:
    path: Path
    ext: str
    text: str
    meta: dict[str, Any]

    @property
    def ok(self) -> bool:
        return bool(self.text) and "error" not in self.meta


def supported_exts() -> set[str]:
    return set(_SUPPORTED_EXTS)


def extract_file(
    path: Path,
    *,
    max_chars: int = _MAX_CHARS_PER_FILE_DEFAULT,
    max_pdf_pages: int = _MAX_PDF_PAGES_DEFAULT,
) -> ExtractResult:
    path = Path(path)
    ext = path.suffix.lower()
    if not path.exists() or path.is_dir():
        return ExtractResult(path, ext, "", {"error": "not_file"})
    try:
        if ext in {".md", ".txt"}:
            text = _extract_text_plain(path)
        elif ext in {".json", ".yaml", ".yml"}:
            text = _extract_structured(path)
        elif ext == ".csv":
            text = _extract_csv(path)
        elif ext == ".pdf":
            text = _extract_pdf(path, max_pages=max_pdf_pages)
        elif ext == ".hwpx":
            text = _extract_hwpx(path)
        elif ext == ".docx":
            text = _extract_docx(path)
        elif ext == ".xlsx":
            text = _extract_xlsx(path)
        else:
            return ExtractResult(path, ext, "", {"error": f"unsupported ext {ext}"})
    except Exception as e:
        logger.debug("extract failed: %s (%s)", path, e)
        return ExtractResult(path, ext, "", {"error": str(e)[:200]})

    if len(text) > max_chars:
        text = text[:max_chars] + f"\n...[truncated at {max_chars} chars]"
    return ExtractResult(
        path=path,
        ext=ext,
        text=text,
        meta={"bytes": path.stat().st_size, "chars": len(text)},
    )


def _extract_text_plain(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="cp949", errors="replace")


def _extract_structured(path: Path) -> str:
    raw = _extract_text_plain(path)
    if path.suffix.lower() == ".json":
        try:
            obj = json.loads(raw)
            return json.dumps(obj, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            return raw
    return raw


def _extract_csv(path: Path) -> str:
    import csv

    buf = io.StringIO()
    with path.open("r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        rows = list(reader)
    for row in rows[:500]:
        buf.write(" | ".join(row))
        buf.write("\n")
    if len(rows) > 500:
        buf.write(f"... (total {len(rows)} rows)\n")
    return buf.getvalue()


def _extract_pdf(path: Path, *, max_pages: int = 80) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        return ""
    reader = PdfReader(str(path))
    buf: list[str] = []
    for i, page in enumerate(reader.pages[:max_pages]):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if text.strip():
            buf.append(f"=== p{i+1} ===\n{text.strip()}")
    total = len(reader.pages)
    if total > max_pages:
        buf.append(f"\n...[truncated at {max_pages}/{total} pages]")
    return "\n\n".join(buf)


def _extract_docx(path: Path) -> str:
    try:
        from docx import Document  # python-docx
    except ImportError:
        return ""
    doc = Document(str(path))
    parts: list[str] = []
    for p in doc.paragraphs:
        if p.text.strip():
            parts.append(p.text)
    for t in doc.tables:
        for row in t.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _extract_xlsx(path: Path) -> str:
    try:
        from openpyxl import load_workbook
    except ImportError:
        return ""
    wb = load_workbook(str(path), data_only=True, read_only=True)
    parts: list[str] = []
    for sheet_name in wb.sheetnames:
        parts.append(f"=== sheet: {sheet_name} ===")
        ws = wb[sheet_name]
        n = 0
        for row in ws.iter_rows(values_only=True):
            if not any(v not in (None, "") for v in row):
                continue
            parts.append(
                " | ".join("" if v is None else str(v) for v in row)
            )
            n += 1
            if n >= 300:
                parts.append(f"...[sheet {sheet_name} truncated at {n} rows]")
                break
    wb.close()
    return "\n".join(parts)


_HWPX_SECTION_GLOBS = ("Contents/section*.xml", "BinData/section*.xml")
_HWPX_TEXT_TAGS = {"t", "p"}


def _extract_hwpx(path: Path) -> str:
    """HWPX = ZIP + XML. Contents/section*.xml 에서 텍스트 추출.

    외부 라이브러리 없이 stdlib 만 사용.
    `<hp:t>` 또는 `<t>` 태그 내 text 를 이어붙이고, 단락(`<hp:p>`) 경계에서 줄바꿈.
    """
    import xml.etree.ElementTree as ET
    import zipfile

    text_parts: list[str] = []
    with zipfile.ZipFile(str(path), "r") as zf:
        section_names = [
            n
            for n in zf.namelist()
            if (n.startswith("Contents/section") or n.startswith("BinData/section"))
            and n.endswith(".xml")
        ]
        if not section_names:
            section_names = [n for n in zf.namelist() if n.endswith(".xml")]

        for name in sorted(section_names):
            try:
                raw = zf.read(name)
            except KeyError:
                continue
            try:
                root = ET.fromstring(raw)
            except ET.ParseError:
                continue
            section_text = _hwpx_walk(root)
            if section_text.strip():
                text_parts.append(section_text.strip())

    return "\n\n".join(text_parts)


def _hwpx_walk(elem) -> str:
    """재귀 walk. 네임스페이스 관계없이 local tag 이름이 t/p 면 텍스트·단락 경계 처리."""
    out: list[str] = []
    tag = elem.tag
    if "}" in tag:
        tag = tag.split("}", 1)[1]
    is_para = tag.lower() == "p"
    is_text = tag.lower() == "t"

    if is_text and elem.text:
        out.append(elem.text)
    for child in elem:
        out.append(_hwpx_walk(child))
    if is_para:
        out.append("\n")
    if elem.tail:
        out.append(elem.tail)

    return "".join(out)


def extract_directory(
    d: Path,
    *,
    max_chars_per_file: int = _MAX_CHARS_PER_FILE_DEFAULT,
    recurse: bool = True,
    exclude_hidden: bool = True,
) -> list[ExtractResult]:
    """디렉터리 내 지원 확장자 전체 추출."""
    d = Path(d)
    if not d.exists() or not d.is_dir():
        return []
    out: list[ExtractResult] = []
    iterator = d.rglob("*") if recurse else d.iterdir()
    for p in sorted(iterator):
        if not p.is_file():
            continue
        if exclude_hidden and any(part.startswith(".") for part in p.parts):
            continue
        if p.suffix.lower() not in _SUPPORTED_EXTS:
            continue
        r = extract_file(p, max_chars=max_chars_per_file)
        out.append(r)
    return out


def combined_text(results: list[ExtractResult], *, max_total_chars: int = 30000) -> str:
    """여러 ExtractResult 를 하나의 텍스트 블록으로 합침 (파일별 헤더 포함)."""
    parts: list[str] = []
    remaining = max_total_chars
    for r in results:
        if not r.ok or remaining <= 0:
            continue
        rel = r.path.name
        header = f"\n\n========= {rel} ({r.ext}) ========="
        body = r.text[: max(0, remaining - len(header))]
        parts.append(header + "\n" + body)
        remaining -= len(header) + len(body)
    return "".join(parts).lstrip()
