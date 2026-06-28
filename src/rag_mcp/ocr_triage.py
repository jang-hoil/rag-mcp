"""OCR triage — PDF/청크별 OCR 필요 여부 (가벼운 사전 검사).

- document_needs_ocr: PyMuPDF 페이지 텍스트 밀도 → 스캔 PDF 의심.
- chunk_needs_page_ocr: needs_image 청크(뭉친 표) → PNG OCR 대상.
"""
from __future__ import annotations

from pathlib import Path

from .config import Config
from .models import Chunk


def page_char_counts(pdf_path: str | Path) -> list[int]:
    """페이지별 추출 텍스트 글자 수(공백 제외)."""
    import fitz

    pdf_path = Path(pdf_path)
    doc = fitz.open(str(pdf_path))
    try:
        return [len(doc[i].get_text("text").replace(" ", "").replace("\n", "")) for i in range(doc.page_count)]
    finally:
        doc.close()


def document_needs_ocr(pdf_path: str | Path, min_chars_per_page: int = 30) -> bool:
    """대부분 페이지 텍스트가 빈약하면 스캔 PDF로 보고 문서 단위 OCR(hybrid) 후보."""
    counts = page_char_counts(pdf_path)
    if not counts:
        return True
    sparse = sum(1 for n in counts if n < min_chars_per_page)
    return sparse / len(counts) >= 0.5


def chunk_needs_page_ocr(chunk: Chunk, config: Config) -> bool:
    """needs_image PNG에 대한 페이지 OCR 대상 여부 (vision page_image는 항상 유지)."""
    if config.ocr_mode == "off":
        return False
    if config.ocr_mode == "force":
        return bool(chunk.needs_image and chunk.page_image)
    # auto: 뭉친 표·이미지 폴백 청크만
    return bool(chunk.needs_image and chunk.page_image)
