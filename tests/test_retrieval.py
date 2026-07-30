"""retrieval.py + indexer.py 테스트 (FakeEmbeddingBackend, 실모델/ PDF 불요)."""
import os

import pytest

from rag_mcp.config import Config
from rag_mcp.indexer import Indexer
from rag_mcp.models import Chunk
from rag_mcp.retrieval import Retriever
from rag_mcp.vector_store import VectorStore
from qdrant_client.models import ScoredPoint


@pytest.fixture
def env(tmp_path, fake_backend):
    os.environ["RAG_QDRANT_PATH"] = str(tmp_path / "qdrant")
    os.environ["RAG_DATA_DIR"] = str(tmp_path)
    try:
        cfg = Config()
        store = VectorStore(cfg, embedding_model="kure", document_id="d1")
        indexer = Indexer(
            cfg, embedding_model="kure", document_id="d1",
            backend=fake_backend, store=store,
        )
        retriever = Retriever(
            cfg, embedding_model="kure", document_id="d1",
            backend=fake_backend, store=store,
        )
        yield cfg, indexer, retriever
    finally:
        os.environ.pop("RAG_QDRANT_PATH", None)
        os.environ.pop("RAG_DATA_DIR", None)


def _chunks():
    return [
        Chunk(chunk_id="d1::c0", document_id="d1", text="201-01 일반수용비 한도 50,000,000원",
              fiscal_year=2026, has_code=True, has_amount=True, is_table=True, page=9,
              needs_image=True, page_image="data/parsed/d1/pages/p9.png"),
        Chunk(chunk_id="d1::c1", document_id="d1", text="명시이월 요건과 절차 설명", fiscal_year=2026, page=3),
        Chunk(chunk_id="d2::c0", document_id="d2", text="2025년 일상경비 단가 기준", fiscal_year=2025, page=1),
    ]


def test_index_and_search_code_query(env):
    cfg, indexer, retriever = env
    indexer.index_chunks("d1", [c for c in _chunks() if c.document_id == "d1"],
                         doc_name="2026 기준", fiscal_year=2026)
    results = retriever.search("201-01 한도", top_k=5)
    ids = [r.chunk_id for r in results]
    assert "d1::c0" in ids, ids
    top = next(r for r in results if r.chunk_id == "d1::c0")
    assert "sparse" in top.matched_by  # 코드 토큰 sparse 기여
    assert top.source.is_table is True


def test_needs_image_includes_page_image(env):
    cfg, indexer, retriever = env
    indexer.index_chunks("d1", [c for c in _chunks() if c.document_id == "d1"], fiscal_year=2026)
    results = retriever.search("50,000,000원 한도", top_k=5, search_mode="sparse")
    top = next(r for r in results if r.chunk_id == "d1::c0")
    assert top.source.needs_image is True
    assert top.source.page_image and top.source.page_image.endswith("p9.png")


def test_fiscal_year_filter_excludes_other_year(env):
    cfg, indexer, retriever = env
    all_chunks = _chunks()
    indexer.index_chunks("d1", [c for c in all_chunks if c.document_id == "d1"], fiscal_year=2026)
    d2_store = VectorStore(
        cfg, embedding_model="kure", document_id="d2", client=indexer.store.client
    )
    d2_indexer = Indexer(
        cfg, embedding_model="kure", document_id="d2",
        backend=indexer.backend, store=d2_store,
    )
    d2_indexer.index_chunks(
        "d2", [c for c in all_chunks if c.document_id == "d2"], fiscal_year=2025
    )
    results = retriever.search("일상경비 단가", top_k=5, fiscal_year=2026)
    assert all(r.source.fiscal_year == 2026 for r in results)
    assert "d2::c0" not in [r.chunk_id for r in results]


def test_reindex_reparse_false_no_pdf(env):
    cfg, indexer, retriever = env
    indexer.index_chunks("d1", [c for c in _chunks() if c.document_id == "d1"], fiscal_year=2026)
    # PDF 없이 재색인 (parsed chunks.jsonl 재사용)
    res = indexer.reindex_document("d1", reparse=False)
    assert res["ok"] is True
    assert res["num_chunks"] == 2
    # 중복 없음
    assert indexer.store.count_by_document("d1") == 2


def test_reindex_missing_chunks_errors(env):
    cfg, indexer, retriever = env
    indexer.manifests.update("ghost", status="parsed")
    res = indexer.reindex_document("ghost", reparse=False)
    assert res["ok"] is False


def test_get_chunk(env):
    cfg, indexer, retriever = env
    indexer.index_chunks("d1", [c for c in _chunks() if c.document_id == "d1"], fiscal_year=2026)
    r = retriever.get_chunk("d1::c0")
    assert r is not None
    assert r.chunk_id == "d1::c0"
    assert "201-01" in r.text


class _ThresholdStore:
    def __init__(self, points, dense_scores):
        self.points = points
        self.dense_scores = dense_scores

    def require_collection(self):
        return None

    def query(self, *args, **kwargs):
        return self.points

    def dense_scores_for_points(self, dense_vec, point_ids):
        return {point_id: self.dense_scores[point_id] for point_id in point_ids}


def _point(point_id: int, chunk_id: str, score: float) -> ScoredPoint:
    return ScoredPoint(
        id=point_id,
        version=0,
        score=score,
        payload={"chunk_id": chunk_id, "document_id": "d1", "text": chunk_id},
    )


def test_dense_absolute_threshold_uses_separate_cosine_score(fake_backend):
    """하이브리드 RRF 점수가 아니라 별도 dense 코사인 점수로 후보를 거른다."""
    points = [_point(1, "d1::first", 0.6667), _point(2, "d1::second", 0.5)]
    store = _ThresholdStore(points, {1: 0.69, 2: 0.91})
    retriever = Retriever(
        Config(min_dense_score=0.7),
        document_id="d1",
        backend=fake_backend,
        store=store,
    )

    outcome = retriever.search_with_diagnostics("질의", search_mode="hybrid")

    assert [result.chunk_id for result in outcome.results] == ["d1::second"]
    assert outcome.candidates_before_filter == 2
    assert outcome.filtered_by_dense == 1


def test_relative_score_ratio_filters_large_rank_drop(fake_backend):
    points = [_point(1, "d1::first", 1.0), _point(2, "d1::second", 0.59)]
    store = _ThresholdStore(points, {1: 0.8, 2: 0.8})
    retriever = Retriever(
        Config(rel_score_ratio=0.6),
        document_id="d1",
        backend=fake_backend,
        store=store,
    )

    outcome = retriever.search_with_diagnostics("질의", search_mode="hybrid")

    assert [result.chunk_id for result in outcome.results] == ["d1::first"]
    assert outcome.filtered_by_relative == 1


def test_threshold_defaults_preserve_all_candidates_and_order(fake_backend):
    points = [_point(1, "d1::first", 0.6667), _point(2, "d1::second", 0.5)]
    store = _ThresholdStore(points, {1: 0.01, 2: 0.99})
    retriever = Retriever(
        Config(),
        document_id="d1",
        backend=fake_backend,
        store=store,
    )

    outcome = retriever.search_with_diagnostics("질의", search_mode="hybrid")

    assert [result.chunk_id for result in outcome.results] == [
        "d1::first",
        "d1::second",
    ]
    assert outcome.filtered_by_dense == 0
    assert outcome.filtered_by_relative == 0


def test_lexical_reranker_promotes_relevant_candidate_from_wider_pool(fake_backend):
    points = [
        _point(1, "무관한 후보", 0.6667),
        ScoredPoint(
            id=2,
            version=0,
            score=0.5,
            payload={
                "chunk_id": "d1::relevant",
                "document_id": "d1",
                "text": "201-01 일반수용비 한도",
                "heading_path": ["201-01 일반수용비"],
                "has_code": True,
            },
        ),
    ]
    store = _ThresholdStore(points, {1: 0.8, 2: 0.8})
    retriever = Retriever(
        Config(reranker="lexical", rerank_fetch_k=5),
        document_id="d1",
        backend=fake_backend,
        store=store,
    )

    outcome = retriever.search_with_diagnostics("201-01 한도", top_k=1)

    assert [result.chunk_id for result in outcome.results] == ["d1::relevant"]
