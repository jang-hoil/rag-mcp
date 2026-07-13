"""vector_store.py 테스트 (FakeEmbeddingBackend, 실모델 불요)."""
import os

import pytest

from rag_mcp.config import Config
from rag_mcp.models import Chunk
from rag_mcp.vector_store import StorageBusyError, VectorStore, point_id_for


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


def test_local_storage_lock_friendly_error(store):
    # store fixture가 이미 local path 락 점유 → 같은 경로 두 번째 오픈은 친절한 한국어 에러로
    # (serve 중 CLI ingest 동시 실행 방어 — 스펙 §1.3 파일락)
    cfg = Config()
    # StorageBusyError로 던지되(server.py 단일 인스턴스 가드가 타입으로 감지),
    # RuntimeError 서브클래스라 기존 except RuntimeError 경로와도 호환된다.
    assert issubclass(StorageBusyError, RuntimeError)
    with pytest.raises(StorageBusyError, match="사용 중"):
        VectorStore(cfg, embedding_model="kure")


def test_server_mode_without_url_raises(tmp_path, monkeypatch):
    """server 모드인데 URL이 비면 조용히 local로 폴백하지 말고 설정 오류를 알린다."""
    monkeypatch.setenv("RAG_QDRANT_MODE", "server")
    monkeypatch.setenv("RAG_QDRANT_URL", "")
    monkeypatch.setenv("RAG_QDRANT_PATH", str(tmp_path / "qdrant"))
    with pytest.raises(ValueError, match="RAG_QDRANT_URL"):
        VectorStore(Config(), embedding_model="kure")


def test_point_id_stable():
    assert point_id_for("doc1::c0") == point_id_for("doc1::c0")
    assert point_id_for("doc1::c0") != point_id_for("doc1::c1")


def test_concurrent_query_during_upsert_is_safe(store, fake_backend):
    # 배경 색인 스레드가 upsert 하는 동안 메인 스레드가 검색해도 QdrantLocal이 깨지면 안 됨.
    # VectorStore 락이 접근을 직렬화하므로 예외 없이 동작해야 한다(비동기 ingest 전제).
    import threading

    from rag_mcp.sparse import to_sparse

    _seed(store, fake_backend)  # 초기 데이터(검색이 빈 컬렉션을 만나지 않게)
    errors = []
    stop = threading.Event()

    def writer():
        try:
            for i in range(20):
                if stop.is_set():
                    break
                cid = f"docw::c{i}"
                ch = _chunk(cid, f"동시성 문서 {i}", 2026, doc="docw")
                store.upsert_chunks([ch], fake_backend.embed_documents([ch.text]))
        except Exception as e:  # pragma: no cover - 실패 시 메시지 확인용
            errors.append(("writer", repr(e)))

    def reader():
        try:
            qd = fake_backend.embed_query("일반수용비")
            qs = to_sparse("일반수용비")
            for _ in range(40):
                if stop.is_set():
                    break
                store.query(qd, qs, top_k=3, search_mode="hybrid")
        except Exception as e:  # pragma: no cover
            errors.append(("reader", repr(e)))

    tw, tr = threading.Thread(target=writer), threading.Thread(target=reader)
    tw.start(); tr.start()
    tw.join(10); stop.set(); tr.join(10)
    assert not errors, errors
    assert store.count_by_document("docw") == 20


def test_replace_document_reports_rollback_failure(store, fake_backend, monkeypatch):
    _seed(store, fake_backend)
    replacement = [
        _chunk("doc1::c0", "replacement", 2026),
        _chunk("doc1::c2", "inserted only", 2026),
    ]
    vectors = fake_backend.embed_documents([chunk.text for chunk in replacement])
    original_upsert = store.client.upsert
    calls = 0

    def fail_primary_then_restore(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            original_upsert(*args, **kwargs)
            raise RuntimeError("primary upsert failure")
        raise RuntimeError("restore failure")

    monkeypatch.setattr(store.client, "upsert", fail_primary_then_restore)

    with pytest.raises(RuntimeError, match="rollback failed") as excinfo:
        store.replace_document("doc1", replacement, vectors)

    message = str(excinfo.value)
    assert "primary upsert failure" in message
    assert "restore failure" in message
    assert store.retrieve_chunk("doc1::c2") is None


def test_document_replacements_are_serialized(store, fake_backend, monkeypatch):
    import threading

    _seed(store, fake_backend)
    first = [_chunk("doc1::c0", "first replacement", 2026)]
    second = [_chunk("doc1::c0", "second replacement", 2026)]
    first_vectors = fake_backend.embed_documents([chunk.text for chunk in first])
    second_vectors = fake_backend.embed_documents([chunk.text for chunk in second])
    replace_document = store.replace_document
    original_upsert = store.upsert_chunks
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()
    second_lock_attempted = threading.Event()
    call_lock = threading.Lock()
    errors = []
    calls = 0
    second_thread = None

    class ObservedRLock:
        def __init__(self, lock):
            self._lock = lock

        def __enter__(self):
            if threading.current_thread() is second_thread:
                second_lock_attempted.set()
            self._lock.acquire()
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            self._lock.release()

    def blocking_upsert(chunks, vectors):
        nonlocal calls
        with call_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            first_entered.set()
            if not release_first.wait(5):
                raise TimeoutError("first replacement was not released")
        else:
            second_entered.set()
        return original_upsert(chunks, vectors)

    def run_replacement(chunks, vectors):
        try:
            replace_document("doc1", chunks, vectors)
        except Exception as exc:  # pragma: no cover - assertion reports details
            errors.append(exc)

    monkeypatch.setattr(store, "upsert_chunks", blocking_upsert)
    monkeypatch.setattr(store, "_lock", ObservedRLock(store._lock))
    first_thread = threading.Thread(target=run_replacement, args=(first, first_vectors))
    second_thread = threading.Thread(target=run_replacement, args=(second, second_vectors))

    first_thread.start()
    assert first_entered.wait(5)
    second_thread.start()
    assert second_lock_attempted.wait(5)
    assert not second_entered.is_set(), "second replacement entered before the first completed"

    release_first.set()
    first_thread.join(5)
    second_thread.join(5)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert not errors, errors
    assert second_entered.is_set()
    assert store.retrieve_chunk("doc1::c0").payload["text"] == "second replacement"
