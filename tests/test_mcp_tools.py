"""service.py(도구 7개) 테스트 — FastMCP 비의존, FakeEmbeddingBackend."""
import os

import pytest

from rag_mcp.config import Config
from rag_mcp.models import Chunk
from rag_mcp.service import RagService


@pytest.fixture
def svc(tmp_path, fake_backend):
    os.environ["RAG_QDRANT_PATH"] = str(tmp_path / "qdrant")
    os.environ["RAG_DATA_DIR"] = str(tmp_path)
    try:
        yield RagService(Config(), backend=fake_backend)
    finally:
        os.environ.pop("RAG_QDRANT_PATH", None)
        os.environ.pop("RAG_DATA_DIR", None)


def _seed(svc):
    chunks = [
        Chunk(chunk_id="d1::c0", document_id="d1", text="201-01 일반수용비 한도 50,000,000원",
              fiscal_year=2026, has_code=True, has_amount=True, is_table=True, page=9,
              needs_image=True, page_image="data/parsed/d1/pages/p9.png"),
        Chunk(chunk_id="d1::c1", document_id="d1", text="명시이월 요건과 절차", fiscal_year=2026, page=3),
    ]
    return svc.ingest_chunks("d1", chunks, doc_name="2026 예산기준", fiscal_year=2026)


def test_ingest_and_list_documents(svc):
    res = _seed(svc)
    assert res["ok"] and res["num_chunks"] == 2 and res["status"] == "done"
    docs = svc.list_documents()
    assert len(docs) == 1
    assert docs[0]["document_id"] == "d1"
    assert docs[0]["fiscal_year"] == 2026


def test_search_documents_tool(svc):
    _seed(svc)
    results = svc.search_documents("201-01 한도", top_k=5, fiscal_year=2026)
    assert results, "검색 결과 없음"
    assert any(r["chunk_id"] == "d1::c0" for r in results)
    top = next(r for r in results if r["chunk_id"] == "d1::c0")
    assert top["source"]["needs_image"] is True
    assert top["source"]["page_image"].endswith("p9.png")


def test_search_invalid_mode_raises(svc):
    _seed(svc)
    with pytest.raises(ValueError):
        svc.search_documents("x", search_mode="fuzzy")


def test_get_chunk_tool(svc):
    _seed(svc)
    r = svc.get_chunk("d1::c0")
    assert r["ok"] is True
    assert "201-01" in r["text"]
    miss = svc.get_chunk("nope")
    assert miss["ok"] is False


def test_delete_requires_confirm(svc):
    _seed(svc)
    guarded = svc.delete_document("d1", confirm=False)
    assert guarded["ok"] is False
    assert svc.list_documents()  # 아직 존재
    done = svc.delete_document("d1", confirm=True)
    assert done["ok"] is True
    assert svc.list_documents() == []


def test_reindex_tool(svc):
    _seed(svc)
    res = svc.reindex_document("d1", reparse=False)
    assert res["ok"] is True
    assert res["num_chunks"] == 2


def test_collection_status_tool(svc):
    _seed(svc)
    st = svc.collection_status()
    assert st["documents"] == 1
    assert st["by_fiscal_year"].get("2026") == 1
    assert "kure" in st["collections"]


def test_ingest_pdf_missing_file(svc):
    res = svc.ingest_pdf("nonexistent.pdf")
    assert res["ok"] is False


def test_server_imports_and_registers_tools():
    """FastMCP server가 import되고 도구 7개가 등록되는지."""
    import asyncio
    from rag_mcp import server

    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert {
        "search_documents", "ingest_pdf", "get_chunk", "list_documents",
        "delete_document", "reindex_document", "collection_status",
    } <= names
