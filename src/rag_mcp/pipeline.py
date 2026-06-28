"""PDF → 청크 파이프라인 오케스트레이션. 스펙 §6.1~6.4."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .chunking import build_chunks
from .config import Config
from .metadata import extract_doc_meta
from .models import Chunk
from .ocr import augment_chunks_with_ocr
from .page_render import render_pages
from .pdf_parser import parse_pdf


def parse_and_chunk(
    pdf_path: str | Path,
    document_id: str,
    config: Config,
    *,
    fiscal_year: int | None = None,
    doc_name: str | None = None,
    force: bool = False,
) -> tuple[list[Chunk], dict[str, Any]]:
    """PDF 파싱 → 메타 추출 → 청킹 → needs_image PNG → (선택) OCR text 보강."""
    parsed = parse_pdf(pdf_path, document_id, config, force=force)
    meta = extract_doc_meta(parsed, doc_name=doc_name, fiscal_year=fiscal_year)
    meta["parsed_dir"] = str(parsed.parsed_dir)
    meta["source_path"] = str(Path(pdf_path).resolve())

    chunks = build_chunks(
        parsed,
        doc_name=meta.get("doc_name"),
        fiscal_year=meta.get("fiscal_year"),
        source_path=meta["source_path"],
    )

    need_pages = {c.page for c in chunks if c.needs_image and c.page}
    if need_pages:
        page_paths = render_pages(pdf_path, document_id, config, need_pages)
        for chunk in chunks:
            if chunk.needs_image and chunk.page in page_paths:
                chunk.page_image = page_paths[chunk.page]

    chunks, ocr_info = augment_chunks_with_ocr(chunks, config)
    if ocr_info.get("ocr_applied") or ocr_info.get("ocr_skipped"):
        meta["ocr"] = ocr_info

    return chunks, meta
