"""manifest.py 테스트 (상태 전이·멱등)."""
import os

import pytest

from rag_mcp.config import Config
from rag_mcp.manifest import ManifestStore
from rag_mcp.models import Manifest


@pytest.fixture
def mstore(tmp_path):
    os.environ["RAG_DATA_DIR"] = str(tmp_path)
    try:
        yield ManifestStore(Config())
    finally:
        os.environ.pop("RAG_DATA_DIR", None)


def test_write_read_roundtrip(mstore):
    m = Manifest(document_id="doc1", doc_name="2026 예산기준", fiscal_year=2026, status="parsed")
    mstore.write(m)
    got = mstore.read("doc1")
    assert got is not None
    assert got.fiscal_year == 2026
    assert got.status == "parsed"
    assert got.created_at is not None and got.updated_at is not None


def test_status_transition(mstore):
    mstore.update("doc1", status="parsing")
    mstore.update("doc1", status="parsed")
    mstore.update("doc1", status="embedded", num_chunks=12)
    m = mstore.update("doc1", status="done")
    assert m.status == "done"
    assert m.num_chunks == 12  # 이전 필드 보존


def test_resume_from_partial(mstore):
    """중간(embedded) 상태에서 읽어 재개 가능."""
    mstore.update("doc1", status="embedded", num_chunks=5)
    m = mstore.read("doc1")
    assert m.status == "embedded"
    # 재개: done으로 진행
    mstore.update("doc1", status="done")
    assert mstore.read("doc1").status == "done"


def test_list_all(mstore):
    mstore.update("doc1", status="done")
    mstore.update("doc2", status="done", fiscal_year=2025)
    ids = {m.document_id for m in mstore.list_all()}
    assert ids == {"doc1", "doc2"}


def test_unknown_returns_none(mstore):
    assert mstore.read("nope") is None
    assert mstore.exists("nope") is False
