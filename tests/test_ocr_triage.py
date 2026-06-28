"""ocr_triage — PDF/청크 OCR 필요 여부 판정 (PyMuPDF·플래그 기반)."""
from pathlib import Path

import fitz

from rag_mcp.config import Config
from rag_mcp.models import Chunk
from rag_mcp.ocr_triage import chunk_needs_page_ocr, document_needs_ocr, page_char_counts


def _make_pdf(path: Path, pages_text: list[str]) -> None:
    doc = fitz.open()
    for text in pages_text:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.save(str(path))
    doc.close()


def test_page_char_counts(tmp_path):
    pdf = tmp_path / "t.pdf"
    _make_pdf(pdf, ["hello world", ""])
    counts = page_char_counts(pdf)
    assert len(counts) == 2
    assert counts[0] >= 5
    assert counts[1] == 0


def test_document_needs_ocr_scan_like(tmp_path):
    pdf = tmp_path / "scan.pdf"
    _make_pdf(pdf, ["", "", "x"])
    assert document_needs_ocr(pdf, min_chars_per_page=30) is True


def test_document_needs_ocr_digital(tmp_path):
    pdf = tmp_path / "digital.pdf"
    long = "예산편성 일반수용비 201-01 과목코드 " * 5
    _make_pdf(pdf, [long, long])
    assert document_needs_ocr(pdf, min_chars_per_page=30) is False


def test_chunk_needs_page_ocr_auto():
    cfg = Config(ocr_mode="auto")
    c = Chunk(chunk_id="d::c0", document_id="d", text="표", needs_image=True, page_image="/x.png")
    assert chunk_needs_page_ocr(c, cfg) is True
    c2 = Chunk(chunk_id="d::c1", document_id="d", text="본문", needs_image=False)
    assert chunk_needs_page_ocr(c2, cfg) is False
    # PNG 렌더 전( page_image 없음) → OCR 대상 아님
    c3 = Chunk(chunk_id="d::c2", document_id="d", text="표", needs_image=True)
    assert chunk_needs_page_ocr(c3, cfg) is False


def test_chunk_needs_page_ocr_off():
    cfg = Config(ocr_mode="off")
    c = Chunk(chunk_id="d::c0", document_id="d", text="표", needs_image=True)
    assert chunk_needs_page_ocr(c, cfg) is False


def test_chunk_needs_page_ocr_force():
    cfg = Config(ocr_mode="force")
    c = Chunk(chunk_id="d::c0", document_id="d", text="표", needs_image=True, page_image="/x.png")
    assert chunk_needs_page_ocr(c, cfg) is True
