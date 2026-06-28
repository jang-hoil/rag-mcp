"""OCR — needs_image 페이지 PNG → chunk.text 보강 (검색용). page_image는 vision용으로 유지.

pytesseract(+ 시스템 Tesseract) 미설치 시 페이지 OCR은 건너뛰고 page_image만 남긴다.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Protocol

from .config import Config
from .metadata import has_amount, has_code
from .models import Chunk
from .ocr_triage import chunk_needs_page_ocr

_OCR_SEP = "\n\n[OCR]\n"


class OcrBackend(Protocol):
    def recognize(self, image_path: Path) -> str: ...


class FakeOcrBackend:
    """테스트용 — 경로별 고정 텍스트."""

    def __init__(self, texts: dict[str, str] | None = None):
        self._texts = texts or {}
        self.calls = 0

    def recognize(self, image_path: Path) -> str:
        self.calls += 1
        return self._texts.get(str(image_path), "")


class TesseractOcrBackend:
    def __init__(self, lang: str = "kor+eng"):
        self.lang = lang

    def recognize(self, image_path: Path) -> str:
        try:
            import pytesseract
            from PIL import Image
        except ImportError as e:
            raise RuntimeError(
                "페이지 OCR에 pytesseract/Pillow가 필요합니다: uv sync --extra ocr"
            ) from e
        return pytesseract.image_to_string(Image.open(image_path), lang=self.lang) or ""


def get_ocr_backend(config: Config) -> Optional[OcrBackend]:
    if config.ocr_mode == "off":
        return None
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError:
        return None
    return TesseractOcrBackend(lang=config.ocr_lang)


def _refresh_chunk_flags(chunk: Chunk) -> None:
    chunk.has_amount = has_amount(chunk.text)
    chunk.has_code = has_code(chunk.text)


def augment_chunks_with_ocr(
    chunks: list[Chunk],
    config: Config,
    *,
    backend: OcrBackend | None = None,
) -> tuple[list[Chunk], dict]:
    """needs_image 청크 PNG OCR → text append. page_image·needs_image는 변경하지 않음."""
    info: dict = {"ocr_applied": False, "ocr_pages": [], "ocr_backend": None, "ocr_skipped": None}
    if config.ocr_mode == "off":
        return chunks, info

    ocr = backend if backend is not None else get_ocr_backend(config)
    if ocr is None:
        info["ocr_skipped"] = "pytesseract 미설치 — page_image(vision)만 사용"
        return chunks, info

    info["ocr_backend"] = type(ocr).__name__
    page_cache: dict[str, str] = {}
    pages_done: set[int] = set()

    for chunk in chunks:
        if not chunk_needs_page_ocr(chunk, config):
            continue
        img = chunk.page_image
        if not img:
            continue
        key = str(Path(img).resolve()) if Path(img).exists() else str(img)
        if key not in page_cache:
            try:
                page_cache[key] = ocr.recognize(Path(img)).strip()
            except Exception:
                page_cache[key] = ""
        ocr_text = page_cache[key]
        if ocr_text and _OCR_SEP.strip() not in chunk.text:
            chunk.text = chunk.text.rstrip() + _OCR_SEP + ocr_text
            _refresh_chunk_flags(chunk)
            chunk.meta = {**chunk.meta, "ocr_applied": True}
            if chunk.page is not None:
                pages_done.add(chunk.page)

    if page_cache and any(v for v in page_cache.values()):
        info["ocr_applied"] = True
        info["ocr_pages"] = sorted(pages_done)
    return chunks, info
