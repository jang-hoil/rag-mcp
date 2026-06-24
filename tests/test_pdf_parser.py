"""pdf_parser 테스트 — OpenDataLoader 변환 산출물 구조/표 셀 골든.

이미 변환된 data/parsed/{id}/ 산출물을 재사용한다(없으면 skip — Java/ODL 미가용 환경 대비).
표 셀은 JSON에서 추출되며 row/column span 정보가 보존됨을 검증한다.
"""
from __future__ import annotations

import pytest

from rag_mcp.config import load_config
from rag_mcp.pdf_parser import cell_text, parse_pdf

DOC_ID = "예산편성_예산부서"
PDF = "data/예산편성_예산부서.pdf"


@pytest.fixture(scope="module")
def parsed():
    cfg = load_config()
    out_dir = cfg.parsed_doc_dir(DOC_ID)
    import glob

    if not glob.glob(str(out_dir / "*.json")):
        pytest.skip("parsed 산출물 없음(ODL/Java 미실행). pipeline 통합 테스트에서 생성됨.")
    # force=False → 기존 JSON 로드(재변환 없음)
    return parse_pdf(PDF, DOC_ID, cfg, force=False)


def test_num_pages(parsed):
    assert parsed.num_pages == 107


def test_tables_have_grid_with_span(parsed):
    tables = [b for b in parsed.iter_blocks() if b.get("type") == "table"]
    assert len(tables) >= 50
    t = tables[0]
    assert t.get("number of rows") and t.get("number of columns")
    # 셀에 행/열 + span 정보 존재(JSON에서 격자 재구성 가능)
    cell = t["rows"][0]["cells"][0]
    for key in ("row number", "column number", "row span", "column span"):
        assert key in cell


def test_cell_text_golden(parsed):
    """제·개정이력 표의 헤더 셀 텍스트가 JSON에서 정확히 추출된다."""
    texts = set()
    for b in parsed.iter_blocks():
        if b.get("type") == "table":
            for row in b["rows"]:
                for c in row["cells"]:
                    texts.add(cell_text(c))
    assert "개정번호" in texts
    assert "작성자" in texts
