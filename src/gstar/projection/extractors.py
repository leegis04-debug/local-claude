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


_CODE_EXTS: frozenset[str] = frozenset({
    ".py", ".pyi",
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".kt", ".swift",
    ".c", ".h", ".cpp", ".hpp", ".cc",
    ".sh", ".bash", ".zsh",
    ".rb",
    ".sql",
})

_SUPPORTED_EXTS: set[str] = {
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".pdf",
    ".hwp",
    ".hwpx",
    ".docx",
    ".pptx",
    ".xlsx",
    ".csv",
} | set(_CODE_EXTS)

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
        elif ext == ".hwp":
            text = _extract_hwp(path)
        elif ext == ".hwpx":
            text = _extract_hwpx(path)
        elif ext == ".docx":
            text = _extract_docx(path)
        elif ext == ".pptx":
            text = _extract_pptx(path)
        elif ext == ".xlsx":
            text = _extract_xlsx(path)
        elif ext in _CODE_EXTS:
            text = _extract_text_plain(path)
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
    """HWPX = ZIP + XML. Contents/section*.xml 에서 텍스트 + 표 마크다운 추출.

    `<hp:t>` 텍스트 + `<hp:p>` 단락 경계 + `<hp:tbl>` 표 → 마크다운 표.
    외부 라이브러리 불필요 (stdlib 만).
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


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _hwpx_walk(elem) -> str:
    """재귀 walk. 단락(p)/표(tbl) 경계 처리. 표는 마크다운으로 변환되며 자식 재귀 멈춤."""
    tag = _local_name(elem.tag).lower()

    if tag == "tbl":
        md = _hwpx_table_to_markdown(elem)
        out = ["\n", md, "\n"]
        if elem.tail:
            out.append(elem.tail)
        return "".join(out)

    out: list[str] = []
    is_para = tag == "p"
    is_text = tag == "t"

    if is_text and elem.text:
        out.append(elem.text)
    for child in elem:
        out.append(_hwpx_walk(child))
    if is_para:
        out.append("\n")
    if elem.tail:
        out.append(elem.tail)
    return "".join(out)


def _hwpx_table_to_markdown(tbl) -> str:
    """hp:tbl → 마크다운 표. 셀 내부 다중 단락은 공백으로 평탄화."""
    rows: list[str] = []
    for tr in tbl:
        if _local_name(tr.tag).lower() != "tr":
            continue
        cells: list[str] = []
        for tc in tr:
            if _local_name(tc.tag).lower() != "tc":
                continue
            cell_text = _hwpx_text_only(tc).strip().replace("|", "\\|")
            cells.append(cell_text)
        if cells:
            rows.append("| " + " | ".join(cells) + " |")
    if not rows:
        return ""
    if len(rows) > 1:
        n_cols = max(len(rows[0].split("|")) - 2, 1)
        rows.insert(1, "| " + " | ".join(["---"] * n_cols) + " |")
    return "\n".join(rows)


def _hwpx_text_only(elem) -> str:
    """elem 내부 모든 <t> 텍스트만 공백으로 이어붙임 (표 셀 추출용)."""
    parts: list[str] = []
    if _local_name(elem.tag).lower() == "t" and elem.text:
        parts.append(elem.text)
    for child in elem:
        sub = _hwpx_text_only(child)
        if sub:
            parts.append(sub)
    return " ".join(p for p in parts if p)


_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _extract_hwp(path: Path) -> str:
    """HWP 5.x (OLE Compound) → hwp5html(pyhwp) HTML 변환 후 마크다운화.

    표 구조 보존이 핵심 (양식 분석용). hwp5txt 는 표를 `<표>` placeholder 로만
    내보내므로 사용하지 않는다.

    `.hwp` 확장자라도 실제로는 OLE2 가 아닌 변종 (HWPML XML, 손상 파일 등) 이
    종종 있다 — 이 경우 raise 해서 호출자(extract_file)가 meta.error 에 사유를
    기록하도록 한다.
    """
    import shutil
    import subprocess
    import sys
    import tempfile

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return ""

    with open(path, "rb") as f:
        magic = f.read(8)
    if not magic.startswith(_OLE2_MAGIC):
        # `.hwp` 확장자라도 정부 법제처 시스템 등은 HWPML XML 로 자동 생성한다.
        # XML 이면 그쪽 파서로 fallback, 아니면 unsupported.
        return _extract_hwpml_xml(path)

    hwp5html = shutil.which("hwp5html")
    if not hwp5html:
        candidate = Path(sys.executable).parent / "hwp5html"
        if candidate.exists():
            hwp5html = str(candidate)
    if not hwp5html:
        return ""

    with tempfile.TemporaryDirectory() as tmp:
        try:
            subprocess.run(
                [hwp5html, "--output", tmp, str(path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=120,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            return ""
        index = Path(tmp) / "index.xhtml"
        if not index.exists():
            raise ValueError("hwp5html produced no output (file may be encrypted or corrupted)")
        html = index.read_text(encoding="utf-8", errors="replace")

    soup = BeautifulSoup(html, "html.parser")
    return _html_body_to_markdown(soup)


def _html_body_to_markdown(soup) -> str:
    """bs4 soup → 단락·표·헤더 마크다운. 표 안 단락은 셀로 흡수."""
    body = soup.body or soup
    parts: list[str] = []
    for elem in body.find_all(["h1", "h2", "h3", "h4", "p", "table"], recursive=True):
        if elem.find_parent("table"):
            continue
        if elem.name == "table":
            md = _html_table_to_markdown(elem)
            if md:
                parts.append(md)
        elif elem.name in ("h1", "h2", "h3", "h4"):
            text = elem.get_text(separator=" ", strip=True)
            if text:
                parts.append("#" * int(elem.name[1]) + " " + text)
        else:
            text = elem.get_text(separator=" ", strip=True)
            if text:
                parts.append(text)
    return "\n\n".join(parts)


def _html_table_to_markdown(table) -> str:
    rows: list[str] = []
    for tr in table.find_all("tr"):
        cells: list[str] = []
        for td in tr.find_all(["td", "th"]):
            txt = td.get_text(separator=" ", strip=True).replace("|", "\\|")
            cells.append(txt)
        if cells:
            rows.append("| " + " | ".join(cells) + " |")
    if not rows:
        return ""
    if len(rows) > 1:
        n_cols = max(len(rows[0].split("|")) - 2, 1)
        rows.insert(1, "| " + " | ".join(["---"] * n_cols) + " |")
    return "\n".join(rows)


def _extract_pptx(path: Path) -> str:
    """PPTX → 슬라이드별 텍스트 + 표 마크다운."""
    try:
        from pptx import Presentation
    except ImportError:
        return ""

    prs = Presentation(str(path))
    parts: list[str] = []
    for i, slide in enumerate(prs.slides, 1):
        slide_parts: list[str] = [f"## Slide {i}"]
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    text = para.text.strip()
                    if text:
                        slide_parts.append(text)
            if getattr(shape, "has_table", False) and shape.has_table:
                slide_parts.append(_pptx_table_to_markdown(shape.table))
        if len(slide_parts) > 1:
            parts.append("\n".join(slide_parts))
    return "\n\n".join(parts)


def _pptx_table_to_markdown(table) -> str:
    rows: list[str] = []
    for row in table.rows:
        cells = [(c.text or "").strip().replace("|", "\\|") for c in row.cells]
        rows.append("| " + " | ".join(cells) + " |")
    if not rows:
        return ""
    if len(rows) > 1:
        n_cols = max(len(rows[0].split("|")) - 2, 1)
        rows.insert(1, "| " + " | ".join(["---"] * n_cols) + " |")
    return "\n".join(rows)


# HWPML XML 변종 처리 — `.hwp` 확장자로 위장한 한컴 XML 직렬화 포맷.
# 정부 법제처(국가법령정보센터)가 훈령·고시·지침 등을 자동 export 할 때 사용.
# root: <HWPML Version="2.x">, 본문은 BODY > SECTION > P > TEXT > CHAR(.text),
# 표는 TABLE > ROW > CELL > P > ... 구조.
_HWPML_SKIP_TAGS = frozenset({
    "SHAPEOBJECT", "PICTURE", "BINITEM", "BINDATALIST",
    "DOCSUMMARY", "DOCSETTING", "MAPPINGTABLE",
})


def _extract_hwpml_xml(path: Path) -> str:
    """`.hwp` 확장자에 실제는 HWPML XML 인 파일.

    정상 형식이 아니면 ValueError 를 raise — 호출자가 meta.error 에 사유 기록.
    """
    import xml.etree.ElementTree as ET

    try:
        tree = ET.parse(path)
    except ET.ParseError as e:
        raise ValueError(f"file is neither OLE2 HWP nor valid XML ({e})")
    root = tree.getroot()
    if _local_name(root.tag).upper() != "HWPML":
        raise ValueError(f"unknown XML root <{root.tag}> (expected HWPML)")

    body = None
    for child in root:
        if _local_name(child.tag).upper() == "BODY":
            body = child
            break
    if body is None:
        return ""

    text = _hwpml_walk(body)
    # 다중 빈 줄 정리
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    return text.strip()


def _hwpml_walk(elem) -> str:
    """HWPML element → 텍스트. 단락 P 는 줄바꿈, TABLE 은 마크다운 표."""
    tag = _local_name(elem.tag).upper()

    if tag in _HWPML_SKIP_TAGS:
        return ""

    if tag == "TABLE":
        md = _hwpml_table_to_markdown(elem)
        out = ["\n", md, "\n"]
        if elem.tail:
            out.append(elem.tail)
        return "".join(out)

    out: list[str] = []
    if tag == "CHAR" and elem.text:
        out.append(elem.text)

    for child in elem:
        out.append(_hwpml_walk(child))

    if tag == "P":
        out.append("\n")
    if elem.tail:
        out.append(elem.tail)
    return "".join(out)


def _hwpml_table_to_markdown(tbl) -> str:
    rows: list[str] = []
    for row in tbl:
        if _local_name(row.tag).upper() != "ROW":
            continue
        cells: list[str] = []
        for cell in row:
            if _local_name(cell.tag).upper() != "CELL":
                continue
            txt = _hwpml_text_only(cell).strip().replace("|", "\\|")
            cells.append(txt)
        if cells:
            rows.append("| " + " | ".join(cells) + " |")
    if not rows:
        return ""
    if len(rows) > 1:
        n_cols = max(len(rows[0].split("|")) - 2, 1)
        rows.insert(1, "| " + " | ".join(["---"] * n_cols) + " |")
    return "\n".join(rows)


def _hwpml_text_only(elem) -> str:
    """elem 내부의 모든 CHAR 텍스트를 공백으로 이어붙임 (셀 추출용)."""
    parts: list[str] = []
    tag = _local_name(elem.tag).upper()
    if tag in _HWPML_SKIP_TAGS:
        return ""
    if tag == "CHAR" and elem.text:
        parts.append(elem.text)
    for child in elem:
        sub = _hwpml_text_only(child)
        if sub:
            parts.append(sub)
    return " ".join(p.strip() for p in parts if p and p.strip())


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
