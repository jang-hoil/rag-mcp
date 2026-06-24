"""pipeline 통합 테스트 — parse_and_chunk end-to-end."""
from __future__ import annotations

import glob
from pathlib import Path

import pytest

from rag_mcp.config import Config
from rag_mcp.pipeline import parse_and_chunk

DOC_ID = "예산편성_예산부서"
PDF = "data/예산편성_예산부서.pdf"


@pytest.fixture
def cfg(tmp_path):
    # parsed 산출물은 원본 data/ 사용, pages/manifest는 tmp
    base = Path(__file__).resolve().parents[1] / "data"
    return Config(data_dir=base)


def test_parse_and_chunk_from_cached_parsed(cfg):
    if not glob.glob(str(cfg.parsed_doc_dir(DOC_ID) / "*.json")):
        pytest.skip("parsed 산출물 없음")
    if not Path(PDF).exists():
        pytest.skip("샘플 PDF 없음")

    chunks, meta = parse_and_chunk(PDF, DOC_ID, cfg, fiscal_year=2026)
    assert len(chunks) >= 50
    assert meta.get("fiscal_year") == 2026
    assert meta.get("doc_name")

    mashed = [c for c in chunks if c.needs_image]
    if mashed:
        assert mashed[0].page_image
        assert Path(mashed[0].page_image).exists()
