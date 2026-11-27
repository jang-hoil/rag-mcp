"""page_render 테스트 — pymupdf로 실제 PDF 페이지 PNG 생성."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from rag_mcp.config import Config
from rag_mcp.page_render import render_pages

# 실제 샘플 문서는 저장소에 없다. 로컬 샘플로 돌리려면 RAG_TEST_SAMPLE_DOC 을 지정한다.
DOC_ID = os.environ.get("RAG_TEST_SAMPLE_DOC", "sample_budget_doc")
PDF = f"data/{DOC_ID}.pdf"


@pytest.fixture
def cfg(tmp_path):
    return Config(data_dir=tmp_path)


def test_render_pages_creates_png(cfg):
    if not Path(PDF).exists():
        pytest.skip("샘플 PDF 없음")
    paths = render_pages(PDF, DOC_ID, cfg, {2})
    assert 2 in paths
    png = Path(paths[2])
    assert png.exists()
    assert png.suffix == ".png"
    assert png.stat().st_size > 1000


def test_render_pages_idempotent(cfg):
    if not Path(PDF).exists():
        pytest.skip("샘플 PDF 없음")
    first = render_pages(PDF, DOC_ID, cfg, {3})
    second = render_pages(PDF, DOC_ID, cfg, {3})
    assert first[3] == second[3]
