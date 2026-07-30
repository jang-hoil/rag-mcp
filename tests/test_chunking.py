"""chunking 단위 테스트 — 합성 블록 + parsed 산출물 통합."""
from __future__ import annotations

import pytest

from rag_mcp.chunking import (
    BODY_MAX,
    BODY_OVERLAP,
    build_chunks,
    sentence_boundary_positions,
    split_long_text,
)
from rag_mcp.config import load_config
from rag_mcp.pdf_parser import parse_pdf
from rag_mcp.table_chunking import (
    cell_is_mashed,
    is_mashed_table,
    merge_cross_page_tables,
    table_grid_text,
)

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


def test_korean_sentence_and_list_boundaries_are_detected():
    text = "첫 기준입니다. 다음 항목인가요?\n① 첫째\n△ 둘째\n- 셋째\n· 넷째"
    positions = sentence_boundary_positions(text)
    boundaries = {text[:position].rstrip()[-1:] for position in positions}

    assert "." in boundaries
    assert "?" in boundaries
    for marker in ("①", "△", "-", "·"):
        assert text.index(marker) in positions


def test_split_long_text_prefers_sentence_boundary_near_target():
    first_sentence = "가" * (BODY_MAX - 70) + " 첫 번째 기준을 적용합니다."
    second_sentence = " 동일 재산으로 인한 소득은 중복하여 인정하지 않습니다."
    text = first_sentence + second_sentence + " 이후 처리 절차를 확인합니다." * 20

    parts = split_long_text(text, boundary_window=160)

    assert len(parts) >= 2
    assert parts[0].endswith(".")
    assert not parts[0].endswith("인정하")
    assert "동일 재산으로 인한 소득은 중복하여 인정하지 않습니다." in parts[0]


def test_split_long_text_without_boundary_falls_back_to_character_limit():
    text = "가" * (BODY_MAX * 2 + 17)
    parts = split_long_text(text, boundary_window=160)

    assert parts[0] == "가" * BODY_MAX
    assert all(len(part) <= BODY_MAX for part in parts)


def test_split_long_text_never_exceeds_absolute_limit():
    text = "가" * (BODY_MAX * 2) + " 문장입니다."
    parts = split_long_text(text, boundary_window=BODY_MAX)

    assert all(len(part) <= int(BODY_MAX * 1.5) for part in parts)


def test_sentence_aware_overlap_starts_at_boundary():
    sentence = "이 문장은 문장 경계 overlap을 검증합니다."
    text = " ".join([sentence] * 40)
    parts = split_long_text(text, boundary_window=160)

    assert len(parts) >= 2
    assert parts[1].startswith("이 문장은")
    assert not parts[1].startswith(("니다.", "합니다."))


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


def test_build_chunks_never_splits_oversized_table():
    from rag_mcp.pdf_parser import ParsedDoc

    large_text = "표 안의 매우 긴 값 " * 200
    parsed = ParsedDoc(
        document_id="large-table",
        tree={"kids": [_table(1, 1, [(1, 1, large_text)])]},
        json_path=None,
        md_path=None,
        parsed_dir=None,
    )

    chunks = build_chunks(parsed)

    assert len(chunks) == 1
    assert chunks[0].is_table is True
    assert len(chunks[0].text) > int(BODY_MAX * 1.5)


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
