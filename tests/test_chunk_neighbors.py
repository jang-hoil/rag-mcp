"""get_chunk 앞뒤 청크 조회 (리포트 #7).

chunk_id는 색인 시 이미 `문서ID::c순번`(읽기 순서, 표·본문 공통 연번)으로 저장된다.
따라서 순번 필드나 재색인 없이 이웃 청크 ID를 계산해 한 번에 조회할 수 있다.
"""
import os

import pytest

from rag_mcp.config import Config
from rag_mcp.models import Chunk
from rag_mcp.retrieval import neighbor_chunk_ids
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


def _seed(svc, document_id="d1", indexes=range(5)):
    chunks = [
        Chunk(
            chunk_id=f"{document_id}::c{i}",
            document_id=document_id,
            text=f"조항 {i}번 본문 내용",
            page=10 + i,
            heading_path=["제1장", f"제{i}조"],
        )
        for i in indexes
    ]
    return svc.ingest_chunks(document_id, chunks, doc_name="테스트 지침", fiscal_year=2026)


def test_neighbor_chunk_ids_are_computed_from_sequence():
    assert neighbor_chunk_ids("d1::c5", 2) == (["d1::c3", "d1::c4"], ["d1::c6", "d1::c7"])
    assert neighbor_chunk_ids("d1::c1", 3) == (["d1::c0"], ["d1::c2", "d1::c3", "d1::c4"])
    # 문서 ID 안에 '::c'가 있어도 마지막 순번만 사용한다
    assert neighbor_chunk_ids("a::cb::c2", 1) == (["a::cb::c1"], ["a::cb::c3"])
    # 순번 형식이 아니면 이웃을 계산할 수 없다
    assert neighbor_chunk_ids("d1::x", 1) == ([], [])
    assert neighbor_chunk_ids("d1::c2", 0) == ([], [])


def test_get_chunk_without_neighbors_keeps_existing_response(svc):
    _seed(svc)
    r = svc.get_chunk("d1::c2")
    assert r["ok"] is True
    assert r["text"] == "조항 2번 본문 내용"
    assert "before" not in r and "after" not in r


def test_get_chunk_returns_neighbors_in_reading_order(svc):
    _seed(svc)
    r = svc.get_chunk("d1::c2", neighbors=2)
    assert r["ok"] is True
    assert r["chunk_id"] == "d1::c2"
    assert [n["chunk_id"] for n in r["before"]] == ["d1::c0", "d1::c1"]
    assert [n["chunk_id"] for n in r["after"]] == ["d1::c3", "d1::c4"]
    assert r["before"][1]["text"] == "조항 1번 본문 내용"
    assert r["after"][0]["source"]["page"] == 13
    assert r["after"][0]["source"]["heading_path"] == ["제1장", "제3조"]


def test_get_chunk_neighbors_clip_at_document_edges(svc):
    _seed(svc)
    first = svc.get_chunk("d1::c0", neighbors=2)
    assert first["before"] == []
    assert [n["chunk_id"] for n in first["after"]] == ["d1::c1", "d1::c2"]
    last = svc.get_chunk("d1::c4", neighbors=3)
    assert [n["chunk_id"] for n in last["before"]] == ["d1::c1", "d1::c2", "d1::c3"]
    assert last["after"] == []


def test_get_chunk_neighbors_skip_missing_sequence(svc):
    _seed(svc, indexes=[0, 1, 3, 4])  # c2 없음(빈 청크 제외 등)
    r = svc.get_chunk("d1::c1", neighbors=2)
    assert [n["chunk_id"] for n in r["after"]] == ["d1::c3"]


def test_get_chunk_neighbors_stay_inside_the_document(svc):
    _seed(svc, document_id="d1")
    _seed(svc, document_id="d2")
    r = svc.get_chunk("d2::c1", neighbors=1)
    assert [n["chunk_id"] for n in r["before"]] == ["d2::c0"]
    assert all(n["source"]["document_id"] == "d2" for n in r["before"] + r["after"])


@pytest.mark.parametrize("bad", [-1, 6])
def test_get_chunk_rejects_out_of_range_neighbors(svc, bad):
    _seed(svc)
    r = svc.get_chunk("d1::c2", neighbors=bad)
    assert r["ok"] is False
    assert "neighbors" in r["error"]


def test_get_chunk_missing_chunk_with_neighbors_is_not_found(svc):
    _seed(svc)
    r = svc.get_chunk("d1::c99", neighbors=1)
    assert r["ok"] is False


def test_get_chunk_neighbors_without_sequence_returns_notice(svc):
    """순번 없는 chunk_id(예전 형식)도 깨지지 않고, 이웃 대신 재색인 안내를 준다."""
    chunk = Chunk(chunk_id="d1::legacy", document_id="d1", text="예전 형식 청크", page=1)
    svc.ingest_chunks("d1", [chunk], doc_name="테스트 지침", fiscal_year=2026)
    r = svc.get_chunk("d1::legacy", neighbors=1)
    assert r["ok"] is True
    assert r["before"] == [] and r["after"] == []
    assert "재색인" in r["notice"]
    assert "notice" not in svc.get_chunk("d1::legacy")
