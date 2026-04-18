"""확장자별 텍스트 추출기 테스트."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from gstar.projection.extractors import (
    combined_text,
    extract_directory,
    extract_file,
    supported_exts,
)


def test_supported_exts_covers_core_formats():
    exts = supported_exts()
    for e in [".md", ".txt", ".pdf", ".hwpx", ".docx", ".xlsx", ".json"]:
        assert e in exts, f"missing {e}"


def test_extract_md(tmp_path: Path):
    f = tmp_path / "note.md"
    f.write_text("# 제목\n\n본문 한줄.", encoding="utf-8")
    r = extract_file(f)
    assert r.ok
    assert "제목" in r.text
    assert r.ext == ".md"


def test_extract_txt(tmp_path: Path):
    f = tmp_path / "t.txt"
    f.write_text("plain text content", encoding="utf-8")
    assert extract_file(f).ok


def test_extract_json_pretty_prints(tmp_path: Path):
    f = tmp_path / "form.json"
    f.write_text(json.dumps({"form_id": "abc", "title": "T"}), encoding="utf-8")
    r = extract_file(f)
    assert r.ok
    assert "form_id" in r.text
    assert "abc" in r.text


def test_extract_csv(tmp_path: Path):
    f = tmp_path / "data.csv"
    f.write_text("col1,col2\nval1,val2\nval3,val4\n", encoding="utf-8")
    r = extract_file(f)
    assert r.ok
    assert "val1" in r.text


def test_extract_cp949_fallback(tmp_path: Path):
    f = tmp_path / "legacy.md"
    f.write_bytes("한글 내용".encode("cp949"))
    r = extract_file(f)
    assert r.ok
    assert "한글" in r.text


def test_extract_nonexistent_file():
    r = extract_file(Path("/nonexistent/xyz.md"))
    assert not r.ok
    assert "error" in r.meta


def test_extract_unsupported_ext(tmp_path: Path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"\x00\x01\x02")
    r = extract_file(f)
    assert not r.ok


def test_extract_hwpx_from_zip_xml(tmp_path: Path):
    hwpx = tmp_path / "sample.hwpx"
    xml_content = (
        """<?xml version="1.0" encoding="UTF-8"?>
<hp:sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">
  <hp:p><hp:run><hp:t>첫 단락 내용</hp:t></hp:run></hp:p>
  <hp:p><hp:run><hp:t>둘째 단락 KPI AP@0.5 = 85%</hp:t></hp:run></hp:p>
</hp:sec>
"""
    ).encode("utf-8")
    with zipfile.ZipFile(hwpx, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("Contents/section0.xml", xml_content)
        zf.writestr("mimetype", "application/hwp+zip")
    r = extract_file(hwpx)
    assert r.ok, r.meta
    assert "첫 단락 내용" in r.text
    assert "KPI" in r.text


def test_extract_hwpx_namespace_agnostic(tmp_path: Path):
    hwpx = tmp_path / "plain.hwpx"
    xml_content = b"""<?xml version="1.0"?><sec><p><t>no ns</t></p></sec>"""
    with zipfile.ZipFile(hwpx, "w") as zf:
        zf.writestr("Contents/section0.xml", xml_content)
    r = extract_file(hwpx)
    assert "no ns" in r.text


def test_extract_pdf_fake(tmp_path: Path):
    """pypdf 는 빈 PDF 에도 오류 없이 빈 텍스트 반환."""
    pytest.importorskip("pypdf")
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    pdf = tmp_path / "blank.pdf"
    with pdf.open("wb") as f:
        writer.write(f)
    r = extract_file(pdf)
    assert r.ext == ".pdf"


def test_extract_docx(tmp_path: Path):
    pytest.importorskip("docx")
    from docx import Document

    d = Document()
    d.add_heading("제목", level=1)
    d.add_paragraph("본문 단락입니다.")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "A"
    t.cell(0, 1).text = "B"
    path = tmp_path / "doc.docx"
    d.save(str(path))
    r = extract_file(path)
    assert r.ok
    assert "본문 단락" in r.text


def test_extract_xlsx(tmp_path: Path):
    pytest.importorskip("openpyxl")
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "sheet1"
    ws.append(["col1", "col2"])
    ws.append(["v1", 42])
    path = tmp_path / "data.xlsx"
    wb.save(str(path))
    r = extract_file(path)
    assert r.ok
    assert "col1" in r.text
    assert "v1" in r.text


def test_extract_directory_skips_hidden(tmp_path: Path):
    (tmp_path / ".hidden.md").write_text("hidden", encoding="utf-8")
    (tmp_path / "visible.md").write_text("visible content", encoding="utf-8")
    results = extract_directory(tmp_path)
    assert all("visible" in r.text or r.path.name == "visible.md" for r in results)
    assert not any(r.path.name == ".hidden.md" for r in results)


def test_extract_directory_filters_unsupported(tmp_path: Path):
    (tmp_path / "a.md").write_text("md", encoding="utf-8")
    (tmp_path / "b.bin").write_bytes(b"\x00")
    results = extract_directory(tmp_path)
    paths = [r.path.name for r in results]
    assert "a.md" in paths
    assert "b.bin" not in paths


def test_max_chars_truncation(tmp_path: Path):
    big = tmp_path / "big.md"
    big.write_text("X" * 10_000, encoding="utf-8")
    r = extract_file(big, max_chars=500)
    assert len(r.text) <= 600
    assert "truncated" in r.text


def test_combined_text_respects_total_limit(tmp_path: Path):
    (tmp_path / "a.md").write_text("A" * 2000, encoding="utf-8")
    (tmp_path / "b.md").write_text("B" * 2000, encoding="utf-8")
    results = extract_directory(tmp_path)
    combined = combined_text(results, max_total_chars=1500)
    assert len(combined) <= 1700
