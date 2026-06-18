"""vector_store.py 테스트 (FakeEmbeddingBackend, 실모델 불요)."""
import os

import pytest

from rag_mcp.config import Config
from rag_mcp.models import Chunk
from rag_mcp.vector_store import VectorStore, point_id_for


def _chunk(cid, text, fy, **kw):
    return Chunk(chunk_id=cid, document_id=kw.pop("doc", "doc1"), text=text, fiscal_year=fy, **kw)


@pytest.fixture
def store(tmp_path):
    os.environ["RAG_QDRANT_PATH"] = str(tmp_path / "qdrant")
    os.environ["RAG_DATA_DIR"] = str(tmp_path)
    try:
        cfg = Config()
        yield VectorStore(cfg, embedding_model="kure")
    finally:
        os.environ.pop("RAG_QDRANT_PATH", None)
        os.environ.pop("RAG_DATA_DIR", None)


def _seed(store, fake_backend):
    chunks = [
        _chunk("doc1::c0", "201-01 일반수용비 한도 50,000,000원", 2026, has_code=True, has_amount=True, is_table=True),
        _chunk("doc1::c1", "명시이월 요건과 절차", 2026),
        _chunk("doc2::c0", "2025년 단가 기준 (혼입 금지)", 2025, doc="doc2"),
    ]
    vecs = fake_backend.embed_documents([c.text for c in chunks])
    n = store.upsert_chunks(chunks, vecs)
    return chunks, n


def test_upsert_and_count(store, fake_backend):
    _, n = _seed(store, fake_backend)
    assert n == 3
    assert store.count_by_document("doc1") == 2
    assert store.count_by_document("doc2") == 1


def test_hybrid_search_finds_code_chunk(store, fake_backend):
    _seed(store, fake_backend)
    qd = fake_backend.embed_query("201-01 한도")
    from rag_mcp.sparse import to_sparse
    qs = to_sparse("201-01 한도")
    points = store.query(qd, qs, top_k=5, search_mode="hybrid")
    ids = [p.payload["chunk_id"] for p in points]
    assert "doc1::c0" in ids, ids
    assert all(p.score > 0 for p in points)


def test_fiscal_year_filter(store, fake_backend):
    _seed(store, fake_backend)
    qd = fake_backend.embed_query("단가 기준")
    from rag_mcp.sparse import to_sparse
    qs = to_sparse("단가 기준")
    points = store.query(qd, qs, top_k=5, search_mode="hybrid", fiscal_year=2026)
    years = [p.payload["fiscal_year"] for p in points]
    assert all(y == 2026 for y in years), years
    assert "doc2::c0" not in [p.payload["chunk_id"] for p in points]


def test_sparse_only_exact_code(store, fake_backend):
    _seed(store, fake_backend)
    from rag_mcp.sparse import to_sparse
    qs = to_sparse("201-01")
    points = store.query(None, qs, top_k=5, search_mode="sparse")
    ids = [p.payload["chunk_id"] for p in points]
    assert "doc1::c0" in ids


def test_reindex_idempotent_no_duplicates(store, fake_backend):
    chunks, _ = _seed(store, fake_backend)
    # 같은 문서 재색인: 삭제 후 재삽입 → 중복 없음
    store.delete_document("doc1")
    doc1_chunks = [c for c in chunks if c.document_id == "doc1"]
    vecs = fake_backend.embed_documents([c.text for c in doc1_chunks])
    store.upsert_chunks(doc1_chunks, vecs)
    assert store.count_by_document("doc1") == 2  # 4가 아님


def test_point_id_stable():
    assert point_id_for("doc1::c0") == point_id_for("doc1::c0")
    assert point_id_for("doc1::c0") != point_id_for("doc1::c1")
