"""청킹 — JSON 표 격자 재구성·atomic 표 청크·본문 분할. 스펙 §6.3, §7.2.

- 표는 절대 중간 분할하지 않는다(atomic).
- borderless 뭉친 표(한 셀에 숫자 2개+ 공백 분리)는 needs_image.
- 연속 페이지 표(열 수 동일·페이지+1)는 병합 후 한 청크로 처리.
- 본문은 800자 초과 시 overlap 120으로 분할.
"""
from __future__ import annotations

import re
from typing import Any, Iterator

from .metadata import has_amount, has_code
from .models import Chunk
from .pdf_parser import ParsedDoc, cell_text

BODY_MAX = 800
BODY_OVERLAP = 120

_NUM_TOKEN_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _block_page(block: dict[str, Any]) -> int | None:
    p = block.get("page number")
    return int(p) if p is not None else None


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
                    parts.append(str(c).strip())
        return "\n".join(p for p in parts if p)
    content = block.get("content")
    return str(content).strip() if content else ""


def cell_is_mashed(text: str) -> bool:
    """한 셀에 공백으로 구분된 숫자 토큰이 2개 이상이면 뭉친 표로 본다."""
    if not text or " " not in text:
        return False
    return len(_NUM_TOKEN_RE.findall(text)) >= 2


def is_mashed_table(table: dict[str, Any]) -> bool:
    for row in table.get("rows", []) or []:
        for cell in row.get("cells", []) or []:
            if cell_is_mashed(cell_text(cell)):
                return True
    return False


def table_grid_text(table: dict[str, Any]) -> str:
    """row/column span을 반영해 표를 TSV 형태 문자열로 재구성."""
    nrows = int(table.get("number of rows") or 0)
    ncols = int(table.get("number of columns") or 0)
    if nrows <= 0 or ncols <= 0:
        return ""

    grid = [["" for _ in range(ncols)] for _ in range(nrows)]
    for row in table.get("rows", []) or []:
        for cell in row.get("cells", []) or []:
            r = int(cell.get("row number", 1)) - 1
            c = int(cell.get("column number", 1)) - 1
            if 0 <= r < nrows and 0 <= c < ncols:
                grid[r][c] = cell_text(cell)
    return "\n".join("\t".join(row) for row in grid)


def _merge_tables(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """페이지 넘김으로 쪼개진 표를 하나의 가상 table 블록으로 병합."""
    rows_a = list(a.get("rows", []) or [])
    rows_b = list(b.get("rows", []) or [])
    merged_rows = rows_a + rows_b
    nrows = int(a.get("number of rows") or len(rows_a)) + int(b.get("number of rows") or len(rows_b))
    return {
        "type": "table",
        "page number": a.get("page number"),
        "number of rows": nrows,
        "number of columns": a.get("number of columns"),
        "rows": merged_rows,
    }


def merge_cross_page_tables(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """인접 table 블록 중 다음 페이지·동일 열 수면 병합."""
    if not blocks:
        return []
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(blocks):
        cur = blocks[i]
        if cur.get("type") != "table":
            out.append(cur)
            i += 1
            continue
        while i + 1 < len(blocks):
            nxt = blocks[i + 1]
            if nxt.get("type") != "table":
                break
            pa, pb = _block_page(cur), _block_page(nxt)
            ca, cb = cur.get("number of columns"), nxt.get("number of columns")
            if pa is not None and pb == pa + 1 and ca == cb:
                cur = _merge_tables(cur, nxt)
                i += 1
            else:
                break
        out.append(cur)
        i += 1
    return out


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
        page = _block_page(block)

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
