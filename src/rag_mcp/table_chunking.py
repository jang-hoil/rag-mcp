"""Table-specific chunking helpers."""
from __future__ import annotations

import re
from typing import Any

from .pdf_parser import cell_text

_NUM_TOKEN_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def block_page(block: dict[str, Any]) -> int | None:
    p = block.get("page number")
    return int(p) if p is not None else None


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
    na = int(a.get("number of rows") or len(rows_a))
    # b 셀의 row number는 1부터 다시 시작하므로 a의 행 수만큼 밀어야
    # table_grid_text 격자에서 a의 행을 덮어쓰지 않는다.
    # 원본 파스 트리 변형 금지 → 행/셀 dict는 복사 후 갱신.
    rows_b: list[dict[str, Any]] = []
    for row in b.get("rows", []) or []:
        cells = []
        for cell in row.get("cells", []) or []:
            if cell.get("row number") is not None:
                cell = {**cell, "row number": int(cell["row number"]) + na}
            cells.append(cell)
        rows_b.append({**row, "cells": cells})
    return {
        "type": "table",
        "page number": a.get("page number"),
        "number of rows": na + int(b.get("number of rows") or len(rows_b)),
        "number of columns": a.get("number of columns"),
        "rows": rows_a + rows_b,
    }


def merge_cross_page_tables(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """인접 table 블록 중 다음 페이지·동일 열 수면 병합."""
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
            pa, pb = block_page(cur), block_page(nxt)
            ca, cb = cur.get("number of columns"), nxt.get("number of columns")
            if pa is not None and pb == pa + 1 and ca == cb:
                cur = _merge_tables(cur, nxt)
                i += 1
            else:
                break
        out.append(cur)
        i += 1
    return out