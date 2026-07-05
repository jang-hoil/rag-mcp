"""청킹 — JSON 표 격자 재구성·atomic 표 청크·본문 분할. 스펙 §6.3, §7.2.

- 표는 절대 중간 분할하지 않는다(atomic).
- borderless 뭉친 표(한 셀에 숫자 2개+ 공백 분리)는 needs_image.
- 연속 페이지 표(열 수 동일·페이지+1)는 병합 후 한 청크로 처리.
- 본문은 800자 초과 시 overlap 120으로 분할.
"""
from __future__ import annotations

from typing import Any

from .metadata import has_amount, has_code
from .models import Chunk
from .pdf_parser import ParsedDoc, normalize_text
from .table_chunking import (
    block_page,
    is_mashed_table,
    merge_cross_page_tables,
    table_grid_text,
)

BODY_MAX = 800
BODY_OVERLAP = 120


def _heading_level(block: dict[str, Any]) -> int:
    raw = block.get("heading level", 99)
    if isinstance(raw, int):
        return raw
    s = str(raw).strip().lower()
    if s == "doctitle":
        return 1
    if s == "subtitle":
        return 2
    try:
        return int(s)
    except ValueError:
        return 99


def block_plain_text(block: dict[str, Any]) -> str:
    """paragraph / heading / caption / list 블록에서 검색용 평문 추출."""
    btype = block.get("type")
    if btype == "list":
        items = block.get("list items") or block.get("kids") or []
        parts = []
        for item in items:
            if isinstance(item, dict):
                c = item.get("content")
                if c:
                    parts.append(normalize_text(str(c)).strip())
        return "\n".join(p for p in parts if p)
    content = block.get("content")
    return normalize_text(str(content)).strip() if content else ""

def split_long_text(text: str, max_len: int = BODY_MAX, overlap: int = BODY_OVERLAP) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_len:
        return [text]
    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_len, len(text))
        parts.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [p for p in parts if p]


def _flush_body(
    buffer: str,
    *,
    document_id: str,
    chunk_index: int,
    heading_path: list[str],
    page: int | None,
    doc_name: str | None,
    fiscal_year: int | None,
    source_path: str | None,
) -> tuple[list[Chunk], int]:
    chunks: list[Chunk] = []
    for piece in split_long_text(buffer):
        chunks.append(
            Chunk(
                chunk_id=f"{document_id}::c{chunk_index}",
                document_id=document_id,
                text=piece,
                page=page,
                heading_path=list(heading_path),
                is_table=False,
                has_amount=has_amount(piece),
                has_code=has_code(piece),
                fiscal_year=fiscal_year,
                doc_name=doc_name,
                source_path=source_path,
            )
        )
        chunk_index += 1
    return chunks, chunk_index


def build_chunks(
    parsed: ParsedDoc,
    *,
    doc_name: str | None = None,
    fiscal_year: int | None = None,
    source_path: str | None = None,
) -> list[Chunk]:
    """ParsedDoc → 색인용 Chunk 리스트."""
    blocks = merge_cross_page_tables(list(parsed.iter_blocks()))
    heading_stack: list[tuple[int, str]] = []
    buffer = ""
    buffer_page: int | None = None
    chunks: list[Chunk] = []
    chunk_index = 0

    def update_heading(block: dict[str, Any]) -> None:
        nonlocal heading_stack
        level = _heading_level(block)
        title = block_plain_text(block)
        if not title:
            return
        heading_stack = [(lv, t) for lv, t in heading_stack if lv < level]
        heading_stack.append((level, title))

    def current_path() -> list[str]:
        return [t for _, t in heading_stack]

    for block in blocks:
        btype = block.get("type")
        page = block_page(block)

        if btype == "heading":
            if buffer.strip():
                new_chunks, chunk_index = _flush_body(
                    buffer,
                    document_id=parsed.document_id,
                    chunk_index=chunk_index,
                    heading_path=current_path(),
                    page=buffer_page,
                    doc_name=doc_name,
                    fiscal_year=fiscal_year,
                    source_path=source_path,
                )
                chunks.extend(new_chunks)
                buffer = ""
                buffer_page = None
            update_heading(block)
            continue

        if btype == "table":
            if buffer.strip():
                new_chunks, chunk_index = _flush_body(
                    buffer,
                    document_id=parsed.document_id,
                    chunk_index=chunk_index,
                    heading_path=current_path(),
                    page=buffer_page,
                    doc_name=doc_name,
                    fiscal_year=fiscal_year,
                    source_path=source_path,
                )
                chunks.extend(new_chunks)
                buffer = ""
                buffer_page = None

            text = table_grid_text(block)
            if not text.strip():
                continue
            mashed = is_mashed_table(block)
            chunks.append(
                Chunk(
                    chunk_id=f"{parsed.document_id}::c{chunk_index}",
                    document_id=parsed.document_id,
                    text=text,
                    page=page,
                    heading_path=current_path(),
                    is_table=True,
                    has_amount=has_amount(text),
                    has_code=has_code(text),
                    needs_image=mashed,
                    fiscal_year=fiscal_year,
                    doc_name=doc_name,
                    source_path=source_path,
                )
            )
            chunk_index += 1
            continue

        if btype in ("paragraph", "list", "caption"):
            piece = block_plain_text(block)
            if not piece:
                continue
            if buffer_page is None and page is not None:
                buffer_page = page
            if buffer:
                buffer += "\n" + piece
            else:
                buffer = piece
            if len(buffer) >= BODY_MAX:
                new_chunks, chunk_index = _flush_body(
                    buffer,
                    document_id=parsed.document_id,
                    chunk_index=chunk_index,
                    heading_path=current_path(),
                    page=buffer_page,
                    doc_name=doc_name,
                    fiscal_year=fiscal_year,
                    source_path=source_path,
                )
                chunks.extend(new_chunks)
                buffer = ""
                buffer_page = None

    if buffer.strip():
        new_chunks, chunk_index = _flush_body(
            buffer,
            document_id=parsed.document_id,
            chunk_index=chunk_index,
            heading_path=current_path(),
            page=buffer_page,
            doc_name=doc_name,
            fiscal_year=fiscal_year,
            source_path=source_path,
        )
        chunks.extend(new_chunks)

    return chunks
