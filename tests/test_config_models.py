"""config.py / models.py 기반 모듈 테스트."""
import os

import pytest

from rag_mcp.config import Config
from rag_mcp.models import Chunk, Manifest, SearchResult, SearchSource


def test_collection_by_model():
    cfg = Config()
    assert (
        cfg.collection_name("kure", "2026 공직윤리 실무과정")
        == "rag_kure_2026_공직윤리_실무과정_4b9257"
    )
    assert (
        cfg.collection_name("bge_m3", "2026 예산지침(최종)")
        == "rag_bge_m3_2026_예산지침최종_160361"
    )
    assert cfg.dimension("kure") == 1024


def test_collection_name_requires_document_id():
    cfg = Config()
    with pytest.raises(ValueError, match="document_id가 필요합니다"):
        cfg.collection_name("kure", None)
    with pytest.raises(ValueError, match="document_id가 필요합니다"):
        cfg.collection_name("kure", "   ")


def test_collection_name_hash_prevents_normalization_collision():
    cfg = Config()
    left = cfg.collection_name("kure", "예산 지침")
    right = cfg.collection_name("kure", "예산_지침")
    assert left.rsplit("_", 1)[0] == right.rsplit("_", 1)[0]
    assert left != right


def test_ocr_default_uses_auto_triage():
    cfg = Config()
    assert cfg.ocr_mode == "auto"


def test_unknown_model_raises():
    cfg = Config()
    try:
        cfg.collection_name("gpt", "문서")
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


def test_bm25_defaults_and_environment_overrides(monkeypatch):
    monkeypatch.delenv("RAG_BM25_K1", raising=False)
    monkeypatch.delenv("RAG_BM25_B", raising=False)
    defaults = Config()
    assert defaults.bm25_k1 == pytest.approx(1.2)
    assert defaults.bm25_b == pytest.approx(0.75)

    monkeypatch.setenv("RAG_BM25_K1", "1.8")
    monkeypatch.setenv("RAG_BM25_B", "0.4")
    configured = Config()
    assert configured.bm25_k1 == pytest.approx(1.8)
    assert configured.bm25_b == pytest.approx(0.4)


def test_relevance_threshold_defaults_and_environment_overrides(monkeypatch):
    monkeypatch.delenv("RAG_MIN_DENSE_SCORE", raising=False)
    monkeypatch.delenv("RAG_REL_SCORE_RATIO", raising=False)
    defaults = Config()
    assert defaults.min_dense_score == 0.0
    assert defaults.rel_score_ratio == 0.0

    monkeypatch.setenv("RAG_MIN_DENSE_SCORE", "0.72")
    monkeypatch.setenv("RAG_REL_SCORE_RATIO", "0.65")
    configured = Config()
    assert configured.min_dense_score == pytest.approx(0.72)
    assert configured.rel_score_ratio == pytest.approx(0.65)


def test_reranker_defaults_and_environment_overrides(monkeypatch):
    monkeypatch.delenv("RAG_RERANKER", raising=False)
    monkeypatch.delenv("RAG_RERANK_FETCH_K", raising=False)
    defaults = Config()
    assert defaults.reranker == "off"
    assert defaults.rerank_fetch_k == 30

    monkeypatch.setenv("RAG_RERANKER", "lexical")
    monkeypatch.setenv("RAG_RERANK_FETCH_K", "40")
    monkeypatch.setenv("RAG_RERANK_LEXICAL_WEIGHT", "1.5")
    configured = Config()
    assert configured.reranker == "lexical"
    assert configured.rerank_fetch_k == 40
    assert configured.rerank_lexical_weight == pytest.approx(1.5)


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
    assert m.avgdl is None
    m.status = "done"
    assert m.status == "done"

