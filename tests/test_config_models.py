"""config.py / models.py 기반 모듈 테스트."""
import os

from rag_mcp.config import Config, COLLECTION_BY_MODEL
from rag_mcp.models import Chunk, Manifest, SearchResult, SearchSource


def test_collection_by_model():
    cfg = Config()
    assert cfg.collection_name("kure") == "rag_kure_chunks"
    assert cfg.collection_name("bge_m3") == "rag_bge_m3_chunks"
    assert cfg.dimension("kure") == 1024


def test_ocr_default_uses_auto_triage():
    cfg = Config()
    assert cfg.ocr_mode == "auto"


def test_unknown_model_raises():
    cfg = Config()
    try:
        cfg.collection_name("gpt")
        assert False, "알 수 없는 모델인데 예외 미발생"
    except ValueError:
        pass


def test_derived_paths(tmp_path):
    os.environ["RAG_DATA_DIR"] = str(tmp_path)
    try:
        cfg = Config()
        assert cfg.parsed_doc_dir("doc1") == tmp_path.resolve() / "parsed" / "doc1"
        assert cfg.pages_dir("doc1").name == "pages"
        assert cfg.manifest_path("doc1").name == "doc1.json"
        cfg.ensure_dirs()
        assert cfg.parsed_dir.exists() and cfg.manifests_dir.exists()
    finally:
        del os.environ["RAG_DATA_DIR"]


def test_chunk_payload_roundtrip():
    c = Chunk(
        chunk_id="doc1::c0",
        document_id="doc1",
        text="201-01 일반수용비 한도 50,000,000원",
        page=9,
        heading_path=["제3장 세출예산", "201 일반수용비"],
        is_table=True,
        has_amount=True,
        has_code=True,
        needs_image=True,
        page_image="data/parsed/doc1/pages/p9.png",
        fiscal_year=2026,
        doc_name="2026 예산편성 운영기준",
    )
    payload = c.payload()
    assert payload["fiscal_year"] == 2026
    assert payload["is_table"] is True
    restored = Chunk.from_payload(payload)
    assert restored.chunk_id == c.chunk_id
    assert restored.heading_path == c.heading_path
    assert restored.needs_image is True


def test_search_result_schema():
    r = SearchResult(
        chunk_id="doc1::c0",
        text="...",
        score=0.82,
        matched_by=["dense", "sparse"],
        source=SearchSource(document_id="doc1", fiscal_year=2026, page=9, is_table=True),
    )
    d = r.model_dump()
    assert d["matched_by"] == ["dense", "sparse"]
    assert d["source"]["fiscal_year"] == 2026


def test_manifest_status_default():
    m = Manifest(document_id="doc1")
    assert m.status == "parsing"
    m.status = "done"
    assert m.status == "done"

