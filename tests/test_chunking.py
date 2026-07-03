"""chunking 단위 테스트 — 합성 블록 + parsed 산출물 통합."""
from __future__ import annotations

import pytest

from rag_mcp.chunking import (
    BODY_MAX,
    BODY_OVERLAP,
    build_chunks,
    cell_is_mashed,
    is_mashed_table,
    merge_cross_page_tables,
    split_long_text,
    table_grid_text,
)
from rag_mcp.config import load_config
from rag_mcp.pdf_parser import cell_text, parse_pdf

DOC_ID = "예산편성_예산부서"
PDF = "data/예산편성_예산부서.pdf"


def _table(rows: int, cols: int, cells: list[tuple[int, int, str]], page: int = 1) -> dict:
  """간단한 table 블록 헬퍼 (1-based row/col)."""
  cell_objs = []
  for r, c, text in cells:
      cell_objs.append({
          "row number": r,
          "column number": c,
          "row span": 1,
          "column span": 1,
          "kids": [{"content": text}],
      })
  return {
      "type": "table",
      "page number": page,
      "number of rows": rows,
      "number of columns": cols,
      "rows": [{"cells": cell_objs}],
  }


def test_cell_is_mashed_detects_multi_numbers():
    assert cell_is_mashed("3 4 5 7 8") is True
    assert cell_is_mashed("개정번호") is False
    assert cell_is_mashed("50,000,000원") is False


def test_table_grid_text_with_span():
    table = _table(2, 2, [(1, 1, "A"), (1, 2, "B"), (2, 1, "C"), (2, 2, "D")])
    text = table_grid_text(table)
    assert "A\tB" in text
    assert "C\tD" in text


def test_merge_cross_page_tables_same_columns():
    t1 = _table(2, 3, [(1, 1, "h1"), (2, 1, "r1")], page=5)
    t2 = _table(2, 3, [(1, 1, "h2"), (2, 1, "r2")], page=6)
    merged = merge_cross_page_tables([t1, t2])
    assert len(merged) == 1
    assert merged[0]["number of rows"] == 4


def test_merge_cross_page_tables_keeps_both_pages_rows():
    """병합 표 텍스트에 두 페이지 내용이 모두 남아야 한다.

    두 번째 표 셀의 row number(1부터 재시작)를 오프셋하지 않으면 격자에서
    첫 표의 행을 덮어써 앞 페이지 데이터가 색인에서 사라진다(회귀 방지).
    """
    t1 = _table(2, 2, [(1, 1, "과목"), (1, 2, "예산액"), (2, 1, "201-01"), (2, 2, "1,000")], page=5)
    t2 = _table(2, 2, [(1, 1, "202-01"), (1, 2, "2,000"), (2, 1, "203-01"), (2, 2, "3,000")], page=6)
    merged = merge_cross_page_tables([t1, t2])
    assert len(merged) == 1
    text = table_grid_text(merged[0])
    rows = text.split("\n")
    assert rows == [
        "과목\t예산액",
        "201-01\t1,000",
        "202-01\t2,000",
        "203-01\t3,000",
    ]
    # 병합이 원본 블록을 변형하면 안 된다(같은 파스 트리 재사용 대비)
    assert t2["rows"][0]["cells"][0]["row number"] == 1


def test_split_long_text_overlap():
    text = "가" * (BODY_MAX + 50)
    parts = split_long_text(text)
    assert len(parts) >= 2
    assert all(len(p) <= BODY_MAX for p in parts)
    # overlap 구간 존재
    assert parts[0][-BODY_OVERLAP:] == parts[1][:BODY_OVERLAP]


def test_is_mashed_table_on_synthetic():
    mashed = _table(1, 1, [(1, 1, "10 20 30")])
    clean = _table(1, 1, [(1, 1, "개정번호")])
    assert is_mashed_table(mashed) is True
    assert is_mashed_table(clean) is False


def test_build_chunks_atomic_table():
    from rag_mcp.pdf_parser import ParsedDoc

    parsed = ParsedDoc(
        document_id="t1",
        tree={"kids": [
            {"type": "heading", "heading level": 2, "content": "제1장", "page number": 1},
            _table(2, 2, [(1, 1, "개정번호"), (1, 2, "작성자"), (2, 1, "1"), (2, 2, "홍길동")]),
        ]},
        json_path=None,
        md_path=None,
        parsed_dir=None,
    )
    chunks = build_chunks(parsed, doc_name="테스트", fiscal_year=2026)
    table_chunks = [c for c in chunks if c.is_table]
    assert len(table_chunks) == 1
    assert "개정번호" in table_chunks[0].text
    assert table_chunks[0].needs_image is False


@pytest.fixture(scope="module")
def parsed():
    cfg = load_config()
    out_dir = cfg.parsed_doc_dir(DOC_ID)
    import glob

    if not glob.glob(str(out_dir / "*.json")):
        pytest.skip("parsed 산출물 없음")
    return parse_pdf(PDF, DOC_ID, cfg, force=False)


def test_build_chunks_from_sample(parsed):
    chunks = build_chunks(parsed, fiscal_year=2026)
    assert len(chunks) >= 50
    assert any(c.is_table for c in chunks)
    assert all(c.chunk_id.startswith(f"{DOC_ID}::c") for c in chunks)
    # 골든 표 헤더가 atomic 청크에 포함
    assert any("개정번호" in c.text for c in chunks if c.is_table)
