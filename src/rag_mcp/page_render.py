"""페이지 PNG 렌더 — needs_image 청크용 pymupdf 폴백. 스펙 §6.4."""
from __future__ import annotations

from pathlib import Path

from .config import Config


def render_pages(
    pdf_path: str | Path,
    document_id: str,
    config: Config,
    pages: set[int],
) -> dict[int, str]:
    """지정 페이지를 PNG로 렌더하고 {page: path} 맵을 반환."""
    import fitz  # pymupdf

    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF 없음: {pdf_path}")

    pages_dir = config.pages_dir(document_id)
    pages_dir.mkdir(parents=True, exist_ok=True)

    out: dict[int, str] = {}
    if not pages:
        return out

    doc = fitz.open(str(pdf_path))
    try:
        for page_no in sorted(p for p in pages if p and p >= 1):
            if page_no > doc.page_count:
                continue
            out_path = pages_dir / f"p{page_no}.png"
            if not out_path.exists():
                pix = doc[page_no - 1].get_pixmap(dpi=config.render_dpi)
                pix.save(str(out_path))
            out[page_no] = str(out_path)
    finally:
        doc.close()
    return out
