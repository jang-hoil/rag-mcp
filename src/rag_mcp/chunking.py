"""청킹 — JSON 표 격자 재구성·atomic 표 청크·본문 분할. 스펙 §6.3, §7.2.

- 표는 절대 중간 분할하지 않는다(atomic).
- borderless 뭉친 표(한 셀에 숫자 2개+ 공백 분리)는 needs_image.
- 연속 페이지 표(열 수 동일·페이지+1)는 병합 후 한 청크로 처리.
- 본문은 800자를 목표로 가까운 문장 경계에서 overlap 120으로 분할.
"""
from __future__ import annotations

import re
from typing import Any

from .config import Config
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
BODY_ABSOLUTE_MAX_FACTOR = 1.5

_SENTENCE_END_RE = re.compile(r"[.!?](?=\s|$)")
_LIST_MARKER_RE = re.compile(
    r"(?m)^[ \t]*(?=(?:[①-⑳]|[△▲▽▼▶▷]|[-·])(?:[ \t]|$))"
)


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

def sentence_boundary_positions(text: str) -> list[int]:
    """한국어 문장 종결과 줄 단위 목록 표지의 경계 위치를 반환한다.

    마침표·물음표·느낌표는 ``다.``, ``요.``, ``까?``, ``함.``, ``임.``,
    ``음.``, ``됩니다.``, ``습니다.``, ``입니다.``를 모두 포함한다.
    """
    boundaries = {match.end() for match in _SENTENCE_END_RE.finditer(text)}
    boundaries.update(match.start() for match in _LIST_MARKER_RE.finditer(text))
    return sorted(position for position in boundaries if 0 < position <= len(text))


def _nearest_boundary(
    boundaries: list[int],
    target: int,
    lower: int,
    upper: int,
) -> int | None:
    candidates = [
        position for position in boundaries
        if lower <= position <= upper
    ]
    if not candidates:
        return None
    # 같은 거리면 목표치 이전 경계를 택해 불필요하게 긴 청크를 피한다.
    return min(candidates, key=lambda position: (abs(position - target), position))


def split_long_text(
    text: str,
    max_len: int = BODY_MAX,
    overlap: int = BODY_OVERLAP,
    boundary_window: int = 160,
) -> list[str]:
    """본문을 목표 길이 근처의 문장 경계에서 나눈다.

    경계를 못 찾으면 기존 문자 수 절단으로 폴백하며, 어떤 경우에도 목표치의
    1.5배를 넘지 않는다. overlap 시작점도 가능한 경우 문장 경계를 사용한다.
    """
    text = text.strip()
    if not text:
        return []
    if max_len <= 0:
        raise ValueError("max_len은 0보다 커야 합니다")
    if not 0 <= overlap < max_len:
        raise ValueError("overlap은 0 이상 max_len 미만이어야 합니다")
    if boundary_window < 0:
        raise ValueError("boundary_window는 0 이상이어야 합니다")
    if len(text) <= max_len:
        return [text]

    absolute_max = max(max_len, int(max_len * BODY_ABSOLUTE_MAX_FACTOR))
    boundaries = sentence_boundary_positions(text)
    parts: list[str] = []
    start = 0
    while start < len(text):
        if len(text) - start <= max_len:
            parts.append(text[start:].strip())
            break

        target = start + max_len
        lower = max(start + 1, target - boundary_window)
        upper = min(
            len(text),
            start + absolute_max,
            target + boundary_window,
        )
        end = _nearest_boundary(boundaries, target, lower, upper) or target
        end = min(end, start + absolute_max, len(text))
        parts.append(text[start:end].strip())
        if end >= len(text):
            break

        desired_start = max(start + 1, end - overlap)
        overlap_start = _nearest_boundary(
            boundaries,
            desired_start,
            max(start + 1, desired_start - boundary_window),
            min(end - 1, desired_start + boundary_window),
        )
        start = overlap_start if overlap_start is not None else desired_start
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
    boundary_window: int,
) -> tuple[list[Chunk], int]:
    chunks: list[Chunk] = []
    for piece in split_long_text(buffer, boundary_window=boundary_window):
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
    config: Config | None = None,
) -> list[Chunk]:
    """ParsedDoc → 색인용 Chunk 리스트."""
    settings = config or Config()
    boundary_window = settings.chunk_boundary_window
    flush_at = min(
        BODY_MAX + boundary_window,
        int(BODY_MAX * BODY_ABSOLUTE_MAX_FACTOR),
    )
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
                    boundary_window=boundary_window,
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
                    boundary_window=boundary_window,
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
            if len(buffer) >= flush_at:
                new_chunks, chunk_index = _flush_body(
                    buffer,
                    document_id=parsed.document_id,
                    chunk_index=chunk_index,
                    heading_path=current_path(),
                    page=buffer_page,
                    doc_name=doc_name,
                    fiscal_year=fiscal_year,
                    source_path=source_path,
                    boundary_window=boundary_window,
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
            boundary_window=boundary_window,
        )
        chunks.extend(new_chunks)

    return chunks
