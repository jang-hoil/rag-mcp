"""config.py / models.py 기반 모듈 테스트."""
import os

import pytest

from rag_mcp.config import Config
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
    finally:
        del os.environ["RAG_DATA_DIR"]


@pytest.mark.parametrize("bad", ["", ".", "..", "../escape", r"..\escape", "C:escape", "bad\x00id"])
def test_document_id_rejects_path_escape(tmp_path, bad):
    cfg = Config(data_dir=tmp_path / "data")
    with pytest.raises(ValueError, match="document_id"):
        cfg.parsed_doc_dir(bad)
    with pytest.raises(ValueError, match="document_id"):
        cfg.manifest_path(bad)


def test_document_id_allows_korean_spaces_and_parentheses(tmp_path):
    cfg = Config(data_dir=tmp_path / "data")
    assert cfg.parsed_doc_dir("2026 예산지침(최종)").is_relative_to(cfg.parsed_dir)


def test_qdrant_path_follows_data_dir_when_not_overridden(monkeypatch, tmp_path):
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path / "custom"))
    monkeypatch.delenv("RAG_QDRANT_PATH", raising=False)
    cfg = Config()
    assert cfg.qdrant_path == cfg.data_dir / "qdrant"


def test_qdrant_path_explicit_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path / "custom"))
    monkeypatch.setenv("RAG_QDRANT_PATH", str(tmp_path / "vectors"))
    assert Config().qdrant_path == (tmp_path / "vectors").resolve()


def test_invalid_qdrant_mode_fails_before_store_open():
    with pytest.raises(ValueError, match="local|server"):
        Config(qdrant_mode="sever")


def test_invalid_embedding_model_fails_before_store_open():
    with pytest.raises(ValueError, match="임베딩 모델"):
        Config(embedding_model="unknown")


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
    assert payload["heading_path"] == c.heading_path
    assert payload["needs_image"] is True


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

