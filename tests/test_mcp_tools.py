"""service.py(도구 7개) 테스트 — FastMCP 비의존, FakeEmbeddingBackend."""
import os

import pytest

from rag_mcp.config import Config
from rag_mcp.models import Chunk
from rag_mcp.service import RagService
from rag_mcp.vector_store import point_id_for


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


def _seed_legacy_collection(svc, document_id="legacy-ghost"):
    """이관 전 단일 컬렉션에 여러 문서 중 한 포인트가 있는 상태를 만든다."""
    from qdrant_client import models

    from rag_mcp.config import COLLECTION_BY_MODEL

    collection = COLLECTION_BY_MODEL["kure"]
    client = svc._client()
    client.create_collection(
        collection_name=collection,
        vectors_config=models.VectorParams(size=1, distance=models.Distance.COSINE),
    )
    client.upsert(
        collection_name=collection,
        points=[
            models.PointStruct(
                id=1,
                vector=[1.0],
                payload={
                    "document_id": document_id,
                    "doc_name": "레거시 단일 컬렉션",
                    "fiscal_year": 2026,
                },
            )
        ],
    )
    return collection


def test_ingest_and_list_documents(svc):
    res = _seed(svc)
    assert res["ok"] and res["num_chunks"] == 2 and res["status"] == "done"
    docs = svc.list_documents()
    assert len(docs) == 1
    assert docs[0]["document_id"] == "d1"
    assert docs[0]["fiscal_year"] == 2026


def test_service_shares_backend_per_model(tmp_path, monkeypatch, fake_backend):
    created = []

    def factory(model):
        created.append(model)
        return fake_backend

    monkeypatch.setattr("rag_mcp.service.get_backend", factory)
    cfg = Config(data_dir=tmp_path, qdrant_path=tmp_path / "qdrant")
    svc = RagService(cfg)
    retriever = svc._retriever("kure", "문서")
    indexer = svc._indexer("kure", "문서")
    assert created == ["kure"]
    assert retriever.backend is indexer.backend


def test_configured_embedding_model_is_default(tmp_path, fake_backend):
    cfg = Config(data_dir=tmp_path, qdrant_path=tmp_path / "qdrant", embedding_model="bge_m3")
    svc = RagService(cfg, backend=fake_backend)
    svc.ingest_chunks("model-default", [Chunk(chunk_id="model-default::c0", document_id="model-default", text="text")])
    assert svc.manifests.read("model-default").embedding_model == "bge_m3"


def test_empty_collection_status_uses_configured_model(tmp_path, fake_backend):
    cfg = Config(data_dir=tmp_path, qdrant_path=tmp_path / "qdrant", embedding_model="bge_m3")
    svc = RagService(cfg, backend=fake_backend)
    assert svc.collection_status()["collections"] == {}


def test_search_documents_tool(svc):
    _seed(svc)
    results = svc.search_documents("201-01 한도", document_id="d1", top_k=5, fiscal_year=2026)
    assert results, "검색 결과 없음"
    assert any(r["chunk_id"] == "d1::c0" for r in results)
    top = next(r for r in results if r["chunk_id"] == "d1::c0")
    assert top["source"]["needs_image"] is True
    assert top["source"]["page_image"].endswith("p9.png")


def test_search_unknown_document_fails_before_dense_embedding(svc, monkeypatch):
    embedded_queries = []
    original_embed_query = svc._backend.embed_query

    def observed_embed_query(text):
        embedded_queries.append(text)
        return original_embed_query(text)

    monkeypatch.setattr(svc._backend, "embed_query", observed_embed_query)

    with pytest.raises(
        ValueError,
        match=r"매니페스트와 문서별 컬렉션이 모두 없습니다.*migrate_to_per_doc_collections\.py",
    ):
        svc.search_documents("일반수용비", document_id="unknown-document")

    assert embedded_queries == []


def test_search_manifest_without_collection_requires_migration_before_embedding(svc, monkeypatch):
    svc.manifests.update(
        "manifest-only",
        status="done",
        embedding_model="kure",
        num_chunks=3,
    )
    embedded_queries = []
    original_embed_query = svc._backend.embed_query

    def observed_embed_query(text):
        embedded_queries.append(text)
        return original_embed_query(text)

    monkeypatch.setattr(svc._backend, "embed_query", observed_embed_query)

    with pytest.raises(
        ValueError,
        match=r"매니페스트는 있지만 문서별 Qdrant 컬렉션이 아직 없습니다.*migrate_to_per_doc_collections\.py",
    ):
        svc.search_documents("일반수용비", document_id="manifest-only")

    assert embedded_queries == []


def test_search_requires_document_id_and_lists_available_documents(svc):
    _seed(svc)
    with pytest.raises(ValueError, match=r"document_id가 필요합니다.*d1"):
        svc.search_documents("일반수용비")


def test_each_document_uses_its_own_collection(svc):
    _seed(svc)
    svc.ingest_chunks(
        "d2",
        [Chunk(chunk_id="d2::c0", document_id="d2", text="다른 지침")],
    )
    d1_store = svc._retriever("kure", "d1").store
    d2_store = svc._retriever("kure", "d2").store
    assert d1_store.collection != d2_store.collection
    assert d1_store.count_by_document("d1") == 2
    assert d1_store.count_by_document("d2") == 0
    assert d2_store.count_by_document("d2") == 1


def test_cross_document_search_and_ingest_serialize_client_access(svc, monkeypatch):
    """문서 A 검색과 문서 B 색인이 같은 QdrantLocal 클라이언트를 동시에 호출하지 않는다."""
    import threading

    _seed(svc)
    client = svc._client()
    original_query_points = client.query_points
    original_upsert = client.upsert
    search_entered = threading.Event()
    release_search = threading.Event()
    writer_started = threading.Event()
    upsert_entered = threading.Event()
    errors = []

    def blocking_query_points(*args, **kwargs):
        search_entered.set()
        if not release_search.wait(5):
            raise TimeoutError("검색 호출 해제 대기 시간 초과")
        return original_query_points(*args, **kwargs)

    def observed_upsert(*args, **kwargs):
        upsert_entered.set()
        return original_upsert(*args, **kwargs)

    def search_document_a():
        try:
            svc.search_documents("일반수용비", document_id="d1")
        except Exception as exc:  # pragma: no cover - 실패 시 assertion에 원인 표시
            errors.append(exc)

    def ingest_document_b():
        writer_started.set()
        try:
            svc.ingest_chunks(
                "d2",
                [Chunk(chunk_id="d2::c0", document_id="d2", text="동시 색인 문서")],
            )
        except Exception as exc:  # pragma: no cover - 실패 시 assertion에 원인 표시
            errors.append(exc)

    monkeypatch.setattr(client, "query_points", blocking_query_points)
    monkeypatch.setattr(client, "upsert", observed_upsert)
    reader = threading.Thread(target=search_document_a)
    writer = threading.Thread(target=ingest_document_b)

    reader.start()
    assert search_entered.wait(5)
    writer.start()
    assert writer_started.wait(5)
    concurrent_upsert = upsert_entered.wait(0.5)
    release_search.set()
    reader.join(5)
    writer.join(5)

    assert not reader.is_alive()
    assert not writer.is_alive()
    assert not errors, errors
    assert not concurrent_upsert, "문서 B upsert가 문서 A query와 동시에 클라이언트에 진입함"
    assert upsert_entered.is_set()


def test_list_documents_ignores_manifest_without_collection(svc):
    svc.manifests.update("manifest-only", status="done", num_chunks=3)
    assert svc.list_documents() == []


def test_legacy_single_collection_is_hidden_from_list_and_status(svc):
    legacy_collection = _seed_legacy_collection(svc)

    assert svc.list_documents() == []
    status = svc.collection_status()
    assert status["documents"] == 0
    assert status["collections"] == {}
    assert legacy_collection not in status["collections"]


def test_delete_unknown_legacy_payload_document_reports_not_found(svc):
    legacy_collection = _seed_legacy_collection(svc)

    result = svc.delete_document("legacy-ghost", confirm=True)

    assert result["ok"] is False
    assert "문서 없음" in result["error"]
    assert svc._client().collection_exists(legacy_collection)


def test_warmup_loads_backend(svc):
    """warmup()은 임베딩 백엔드와 토크나이저를 미리 데워 첫 검색 콜드 스타트를 없앤다."""
    backend = svc._retriever("kure", "d1").backend  # 캐시되는 동일 인스턴스
    calls: list[str] = []
    orig = backend.embed_query
    backend.embed_query = lambda text: (calls.append(text), orig(text))[1]
    svc.warmup()
    assert calls, "warmup이 임베딩 백엔드를 호출하지 않음"


def test_search_invalid_mode_raises(svc):
    _seed(svc)
    with pytest.raises(ValueError):
        svc.search_documents("x", document_id="d1", search_mode="fuzzy")


def test_search_invalid_fusion_raises(svc):
    """fusion 오타(예: 'rff')가 조용히 RRF로 동작하지 않게 명시 검증."""
    _seed(svc)
    with pytest.raises(ValueError):
        svc.search_documents("x", document_id="d1", fusion="rff")
    # 정상값은 통과
    svc.search_documents("일반수용비", document_id="d1", fusion="dbsf")
    svc.search_documents("일반수용비", document_id="d1", fusion="rrf")


def test_search_top_k_out_of_range_raises(svc):
    _seed(svc)
    # 하한(0·음수)·상한(100 초과)·bool(True가 1로 통과하는 함정) 모두 거부
    for bad in (0, -1, 101, True):
        with pytest.raises(ValueError):
            svc.search_documents("x", document_id="d1", top_k=bad)


def test_search_rejects_unknown_filter_key(svc):
    _seed(svc)
    with pytest.raises(ValueError):
        svc.search_documents("x", document_id="d1", filters={"unknown_field": 1})


def test_search_accepts_allowed_filter_key(svc):
    _seed(svc)
    # is_table=True인 d1::c0만 후보로 남아야 함(에러 없이 동작)
    results = svc.search_documents("일반수용비 한도", document_id="d1", filters={"is_table": True})
    assert all(r["source"]["is_table"] for r in results)


def test_search_returns_korean_diagnostic_when_thresholds_remove_all(svc, monkeypatch):
    """임계값으로 전부 제거되면 빈 배열 대신 원인을 설명하는 진단을 반환한다."""
    from rag_mcp.retrieval import SearchOutcome

    _seed(svc)
    svc.config.min_dense_score = 0.9
    retriever = svc._retriever("kure", "d1")
    monkeypatch.setattr(
        retriever,
        "search_with_diagnostics",
        lambda *args, **kwargs: SearchOutcome(
            results=[],
            candidates_before_filter=2,
            filtered_by_dense=2,
            filtered_by_relative=0,
        ),
    )

    response = svc.search_documents("무관한 질의", document_id="d1")

    assert len(response) == 1
    diagnostic = response[0]["diagnostic"]
    assert "임계값" in diagnostic["message"]
    assert diagnostic["candidates_before_filter"] == 2
    assert diagnostic["thresholds"]["min_dense_score"] == pytest.approx(0.9)


def test_sparse_search_reports_ignored_dense_threshold(svc):
    """sparse 검색에서 dense 기준선을 적용하지 못한 사실을 결과로 알린다."""
    _seed(svc)
    svc.config.min_dense_score = 0.8

    response = svc.search_documents(
        "일반수용비 한도",
        document_id="d1",
        search_mode="sparse",
    )

    diagnostic = response[-1]["diagnostic"]
    assert "sparse" in diagnostic["message"]
    assert "적용하지 않았" in diagnostic["message"]
    assert diagnostic["ignored_thresholds"]["min_dense_score"] == pytest.approx(0.8)


def test_metadata_stored_and_shown(svc):
    # metadata가 청크에 저장되고 검색 결과 source.meta로 노출되는지
    chunks = [Chunk(chunk_id="m1::c0", document_id="m1", text="201-01 일반수용비 한도", fiscal_year=2026)]
    svc.ingest_chunks("m1", chunks, doc_name="2026 예산기준", fiscal_year=2026,
                      metadata={"부서": "예산과", "작성자": "홍길동"})
    res = svc.search_documents("일반수용비 한도", document_id="m1", fiscal_year=2026)
    assert res, "검색 결과 없음"
    assert res[0]["source"]["meta"]["부서"] == "예산과"
    assert res[0]["source"]["meta"]["작성자"] == "홍길동"


def test_metadata_filter_narrows_search(svc):
    # 같은 본문이라도 meta.부서 필터로 해당 문서만 좁혀지는지
    common = "공통검색어 알파베타"
    svc.ingest_chunks("m2", [Chunk(chunk_id="m2::c0", document_id="m2", text=common, fiscal_year=2026)],
                      metadata={"부서": "예산과"})
    svc.ingest_chunks("m3", [Chunk(chunk_id="m3::c0", document_id="m3", text=common, fiscal_year=2026)],
                      metadata={"부서": "회계과"})
    res = svc.search_documents(common, document_id="m2", filters={"meta.부서": "예산과"})
    assert res, "필터 결과 없음"
    assert all(r["source"]["meta"].get("부서") == "예산과" for r in res)
    assert all(r["source"]["document_id"] == "m2" for r in res)


def test_metadata_filter_key_allowed(svc):
    # meta.* 필터 키는 allowlist를 통과해야 함(에러 없이 동작)
    _seed(svc)
    svc.search_documents("x", document_id="d1", filters={"meta.anything": "v"})  # ValueError 안 나면 통과


def test_search_result_includes_has_code(svc):
    # has_code는 Chunk·payload에 있으니 검색 결과 source에도 노출돼야 함(스키마 일관성)
    _seed(svc)
    results = svc.search_documents("201-01 한도", document_id="d1", fiscal_year=2026)
    top = next(r for r in results if r["chunk_id"] == "d1::c0")
    assert top["source"]["has_code"] is True


def test_review_before_ingest_returns_incoming_and_full_list(svc):
    # 두 버전(2025·2026)을 색인 → 2026 PDF 검토 시: incoming 식별자/연도 + 두 문서 모두 반환
    svc.ingest_chunks("예산지침_2025",
                      [Chunk(chunk_id="예산지침_2025::c0", document_id="예산지침_2025",
                             text="2025년 예산편성 지침", fiscal_year=2025)],
                      doc_name="2025 예산지침", fiscal_year=2025)
    svc.ingest_chunks("예산지침_2026",
                      [Chunk(chunk_id="예산지침_2026::c0", document_id="예산지침_2026",
                             text="2026년 예산편성 지침", fiscal_year=2026)],
                      doc_name="2026 예산지침", fiscal_year=2026)

    res = svc.review_before_ingest("/inbox/예산지침_2026.pdf")

    assert res["ok"] is True
    # 들어올 문서: 파일명 stem과 파일명에서 추출한 연도
    assert res["incoming"]["document_id"] == "예산지침_2026"
    assert res["incoming"]["fiscal_year"] == 2026
    # 전체 색인 목록에 두 문서 모두 포함(가공·필터 없음)
    ids = {d["document_id"] for d in res["indexed_documents"]}
    assert {"예산지침_2025", "예산지침_2026"} <= ids


def test_get_chunk_tool(svc):
    _seed(svc)
    r = svc.get_chunk("d1::c0")
    assert r["ok"] is True
    assert "201-01" in r["text"]
    miss = svc.get_chunk("nope")
    assert miss["ok"] is False


def test_delete_requires_confirm(svc):
    _seed(svc)
    collection = svc._retriever("kure", "d1").store.collection
    guarded = svc.delete_document("d1", confirm=False)
    assert guarded["ok"] is False
    assert svc.list_documents()  # 아직 존재
    done = svc.delete_document("d1", confirm=True)
    assert done["ok"] is True
    assert not svc._client().collection_exists(collection)
    assert svc.list_documents() == []


def test_index_embed_failure_preserves_existing(svc, monkeypatch):
    # 재색인 중 임베딩이 실패해도 기존 색인 데이터가 사라지면 안 됨(실패 안전성)
    svc.ingest_chunks("e1", [Chunk(chunk_id="e1::c0", document_id="e1",
                                   text="원본 데이터 보존 확인", fiscal_year=2026)])
    assert svc.search_documents("원본 데이터 보존 확인", document_id="e1"), "초기 색인 실패"

    def boom(texts):
        raise RuntimeError("embed 실패 주입")

    monkeypatch.setattr(svc._backend, "embed_documents", boom)
    with pytest.raises(RuntimeError):
        svc.ingest_chunks("e1", [Chunk(chunk_id="e1::c1", document_id="e1",
                                       text="새 데이터", fiscal_year=2026)])
    # 핵심: 임베딩 실패로 기존 데이터가 유실되면 안 됨
    assert svc.search_documents(
        "원본 데이터 보존 확인", document_id="e1"
    ), "임베딩 실패로 기존 데이터 유실됨!"


def test_upsert_failure_preserves_existing_points(svc, monkeypatch):
    _seed(svc)
    store = svc._indexer("kure", "d1").store
    monkeypatch.setattr(
        store,
        "upsert_chunks",
        lambda chunks, vecs, **kwargs: (_ for _ in ()).throw(RuntimeError("upsert")),
    )

    with pytest.raises(RuntimeError, match="upsert"):
        svc.ingest_chunks(
            "d1",
            [Chunk(chunk_id="d1::c0", document_id="d1", text="replacement")],
        )

    assert svc.get_chunk("d1::c0")["ok"] is True
    assert svc.get_chunk("d1::c1")["ok"] is True


def test_partial_upsert_failure_restores_pre_replacement_state(svc, monkeypatch):
    _seed(svc)
    store = svc._indexer("kure", "d1").store
    old_ids = [point_id_for("d1::c0"), point_id_for("d1::c1")]

    def old_record_state():
        records = store.client.retrieve(
            store.collection,
            ids=old_ids,
            with_payload=True,
            with_vectors=True,
        )
        return {record.id: record for record in records}

    before = old_record_state()
    original_upsert = store.upsert_chunks

    def partially_upsert_then_fail(chunks, vecs, **kwargs):
        original_upsert(chunks[:2], vecs[:2], **kwargs)
        raise RuntimeError("partial upsert")

    monkeypatch.setattr(store, "upsert_chunks", partially_upsert_then_fail)
    replacement = [
        Chunk(chunk_id="d1::c0", document_id="d1", text="overwritten", page=99),
        Chunk(chunk_id="d1::c2", document_id="d1", text="inserted only"),
        Chunk(chunk_id="d1::c3", document_id="d1", text="never written"),
    ]

    with pytest.raises(RuntimeError, match="partial upsert"):
        svc.ingest_chunks("d1", replacement)

    after = old_record_state()
    assert after.keys() == before.keys()
    for point_id, old_record in before.items():
        restored = after[point_id]
        assert restored.payload == old_record.payload
        assert restored.vector["sparse"] == old_record.vector["sparse"]
        assert restored.vector["dense"] == pytest.approx(old_record.vector["dense"])
    assert svc.get_chunk("d1::c2")["ok"] is False


def test_successful_replacement_deletes_only_stale_points(svc):
    _seed(svc)

    svc.ingest_chunks(
        "d1",
        [Chunk(chunk_id="d1::c0", document_id="d1", text="replacement")],
    )

    assert svc.get_chunk("d1::c0")["text"] == "replacement"
    assert svc.get_chunk("d1::c1")["ok"] is False


def test_empty_reindex_preserves_existing_document(svc):
    _seed(svc)
    old_manifest = svc.manifests.read("d1")
    old_chunks = (svc.config.parsed_doc_dir("d1") / "chunks.jsonl").read_bytes()

    with pytest.raises(ValueError, match="청크"):
        svc.ingest_chunks("d1", [])

    assert svc.get_chunk("d1::c0")["ok"] is True
    assert svc.manifests.read("d1") == old_manifest
    assert (svc.config.parsed_doc_dir("d1") / "chunks.jsonl").read_bytes() == old_chunks


def test_blank_chunk_reindex_preserves_existing_document(svc):
    _seed(svc)
    old_manifest = svc.manifests.read("d1")
    old_chunks = (svc.config.parsed_doc_dir("d1") / "chunks.jsonl").read_bytes()
    blank = Chunk(chunk_id="d1::blank", document_id="d1", text=" \t\n")

    with pytest.raises(ValueError, match="청크"):
        svc.ingest_chunks("d1", [blank])

    assert svc.get_chunk("d1::c0")["ok"] is True
    assert svc.manifests.read("d1") == old_manifest
    assert (svc.config.parsed_doc_dir("d1") / "chunks.jsonl").read_bytes() == old_chunks


def test_chunk_write_failure_preserves_existing_jsonl(svc, monkeypatch):
    _seed(svc)
    path = svc.config.parsed_doc_dir("d1") / "chunks.jsonl"
    old_chunks = path.read_bytes()
    replacement = [Chunk(chunk_id="d1::new", document_id="d1", text="새 본문")]

    def fail_serialization(self):
        raise RuntimeError("직렬화 실패 주입")

    monkeypatch.setattr(Chunk, "model_dump_json", fail_serialization)

    with pytest.raises(RuntimeError, match="직렬화 실패"):
        svc.ingest_chunks("d1", replacement)

    assert path.read_bytes() == old_chunks


def test_reindex_tool(svc):
    _seed(svc)
    res = svc.reindex_document("d1", reparse=False)
    assert res["ok"] is True
    assert res["num_chunks"] == 2


def test_reindex_restores_manifest_avgdl_and_sparse_values(svc, monkeypatch):
    """일반 재색인은 저장된 avgdl을 복원해 동일 sparse 값을 다시 만든다."""
    chunks = [
        Chunk(chunk_id="avg::c0", document_id="avg", text="한도 한도 한도"),
        Chunk(chunk_id="avg::c1", document_id="avg", text="절차 기준"),
    ]
    svc.ingest_chunks("avg", chunks)
    manifest = svc.manifests.read("avg")
    assert manifest is not None
    assert manifest.avgdl == pytest.approx(2.5)

    store = svc._indexer("kure", "avg").store
    point_id = point_id_for("avg::c0")

    def sparse_values():
        record = store.client.retrieve(
            store.collection,
            ids=[point_id],
            with_payload=True,
            with_vectors=True,
        )[0]
        return record.vector["sparse"]

    before = sparse_values()
    monkeypatch.setattr(
        "rag_mcp.indexer.average_document_length",
        lambda texts: (_ for _ in ()).throw(AssertionError("avgdl을 다시 계산함")),
    )
    result = svc.reindex_document("avg", reparse=False)

    assert result["ok"] is True
    assert sparse_values() == before


def test_reindex_restores_manifest_bm25_settings_and_warns_on_mismatch(svc):
    """재색인은 저장된 k1/b를 복원하고 현재 환경값 불일치를 알린다."""
    chunks = [
        Chunk(chunk_id="bm25::c0", document_id="bm25", text="한도 한도 한도"),
        Chunk(chunk_id="bm25::c1", document_id="bm25", text="절차 기준"),
    ]
    svc.ingest_chunks("bm25", chunks)
    manifest = svc.manifests.read("bm25")
    assert manifest is not None
    assert manifest.bm25_k1 == pytest.approx(1.2)
    assert manifest.bm25_b == pytest.approx(0.75)

    store = svc._indexer("kure", "bm25").store
    point_id = point_id_for("bm25::c0")

    def sparse_values():
        return store.client.retrieve(
            store.collection,
            ids=[point_id],
            with_payload=True,
            with_vectors=True,
        )[0].vector["sparse"]

    before = sparse_values()
    svc.config.bm25_k1 = 2.0
    svc.config.bm25_b = 0.2

    with pytest.warns(RuntimeWarning, match="저장된 BM25.*현재 설정"):
        result = svc.reindex_document("bm25", reparse=False)

    assert result["ok"] is True
    restored = svc.manifests.read("bm25")
    assert restored is not None
    assert restored.bm25_k1 == pytest.approx(1.2)
    assert restored.bm25_b == pytest.approx(0.75)
    assert sparse_values() == before


def test_legacy_manifest_without_bm25_settings_uses_current_config(svc):
    """k1/b가 없는 레거시 매니페스트는 현재 설정을 채택해 이후 재색인에 고정한다."""
    chunks = [Chunk(chunk_id="legacy-bm25::c0", document_id="legacy-bm25", text="한도 기준")]
    svc.ingest_chunks("legacy-bm25", chunks)
    manifest = svc.manifests.read("legacy-bm25")
    assert manifest is not None
    manifest.bm25_k1 = None
    manifest.bm25_b = None
    svc.manifests.write(manifest)

    svc.config.bm25_k1 = 1.7
    svc.config.bm25_b = 0.4
    result = svc.reindex_document("legacy-bm25", reparse=False)

    assert result["ok"] is True
    upgraded = svc.manifests.read("legacy-bm25")
    assert upgraded is not None
    assert upgraded.bm25_k1 == pytest.approx(1.7)
    assert upgraded.bm25_b == pytest.approx(0.4)


def test_reindex_reparse_without_source_pdf(svc):
    # source_path가 없는 문서는 reparse 불가 — 친절 에러
    _seed(svc)  # ingest_chunks라 source_path 없음
    res = svc.reindex_document("d1", reparse=True)
    assert res["ok"] is False
    assert "PDF" in res["error"]


def test_reindex_reparse_reparses_and_preserves_meta(svc, monkeypatch, tmp_path):
    # reparse=True: PDF 재파싱으로 본문 갱신 + 사용자 metadata는 manifest에서 복원
    pdf = tmp_path / "doc.pdf"
    pdf.write_text("dummy")
    svc.ingest_chunks("r1", [Chunk(chunk_id="r1::c0", document_id="r1", text="옛 본문", fiscal_year=2026)],
                      source_path=str(pdf), metadata={"부서": "예산과"})

    def fake_parse(path, doc_id, config, *, fiscal_year=None, doc_name=None, force=False):
        new = [Chunk(chunk_id="r1::c0", document_id="r1", text="새로 파싱된 본문", fiscal_year=fiscal_year)]
        return new, {"doc_name": doc_name, "fiscal_year": fiscal_year}

    monkeypatch.setattr("rag_mcp.pipeline.parse_and_chunk", fake_parse)
    res = svc.reindex_document("r1", reparse=True)
    assert res["ok"] is True and res["reparse"] is True
    hits = svc.search_documents("새로 파싱된 본문", document_id="r1")
    assert hits, "재파싱된 본문이 검색되지 않음"
    assert hits[0]["source"]["meta"]["부서"] == "예산과", "reparse 후 사용자 metadata 유실됨"


def test_reindex_reparse_forces_fresh_parse(svc, monkeypatch, tmp_path):
    """reparse=True는 parsed JSON 캐시를 버리고 실제 재파싱해야 한다(force=True).

    force가 전달되지 않으면 parse_pdf가 기존 parsed/{id}/*.json을 재사용해
    PDF가 바뀌어도 옛 내용으로 재색인되는 버그의 회귀 방지.
    """
    pdf = tmp_path / "doc.pdf"
    pdf.write_text("dummy")
    svc.ingest_chunks("r2", [Chunk(chunk_id="r2::c0", document_id="r2", text="본문", fiscal_year=2026)],
                      source_path=str(pdf))

    seen_force: list[bool] = []

    def fake_parse(path, doc_id, config, *, fiscal_year=None, doc_name=None, force=False):
        seen_force.append(force)
        return [Chunk(chunk_id="r2::c0", document_id="r2", text="본문", fiscal_year=fiscal_year)], {}

    monkeypatch.setattr("rag_mcp.pipeline.parse_and_chunk", fake_parse)
    res = svc.reindex_document("r2", reparse=True)
    assert res["ok"] is True
    assert seen_force == [True], "reparse=True인데 parse_and_chunk에 force=True가 전달되지 않음"


def test_collection_status_tool(svc):
    _seed(svc)
    st = svc.collection_status()
    assert st["documents"] == 1
    assert st["by_fiscal_year"].get("2026") == 1
    assert len(st["collections"]) == 1
    assert next(iter(st["collections"])).startswith("rag_kure_d1_")


def test_ingest_pdf_forces_fresh_parse(svc, monkeypatch, tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF")
    seen = []

    def fake_parse(path, doc_id, config, *, fiscal_year=None, doc_name=None, force=False):
        seen.append(force)
        return [Chunk(chunk_id=f"{doc_id}::c0", document_id=doc_id, text="new")], {}

    monkeypatch.setattr("rag_mcp.pipeline.parse_and_chunk", fake_parse)
    assert svc.ingest_pdf(str(pdf), document_id="doc")["ok"] is True
    assert seen == [True]

def test_ingest_pdf_records_ocr_info_in_manifest_meta(svc, monkeypatch, tmp_path):
    pdf = tmp_path / "ocr.pdf"
    pdf.write_bytes(b"%PDF")

    def fake_parse(path, doc_id, config, *, fiscal_year=None, doc_name=None, force=False):
        chunks = [Chunk(chunk_id="ocrdoc::c0", document_id="ocrdoc", text="OCR 본문", fiscal_year=2026)]
        return chunks, {
            "doc_name": "OCR 문서",
            "fiscal_year": 2026,
            "ocr": {"ocr_applied": False, "ocr_skipped": "pytesseract 미설치"},
        }

    monkeypatch.setattr("rag_mcp.pipeline.parse_and_chunk", fake_parse)

    res = svc.ingest_pdf(str(pdf), document_id="ocrdoc", metadata={"부서": "예산과"})

    assert res["ok"] is True
    manifest = svc.manifests.read("ocrdoc")
    assert manifest is not None
    assert manifest.meta["부서"] == "예산과"
    assert manifest.meta["ocr"]["ocr_skipped"] == "pytesseract 미설치"
    hit = svc.search_documents("OCR 본문", document_id="ocrdoc")
    assert hit[0]["source"]["meta"]["ocr"]["ocr_skipped"] == "pytesseract 미설치"


def test_search_rejects_non_scalar_filter_value(svc):
    _seed(svc)
    with pytest.raises(ValueError):
        svc.search_documents("일반수용비", document_id="d1", filters={"page": [9]})

def test_ingest_pdf_missing_file(svc):
    res = svc.ingest_pdf("nonexistent.pdf")
    assert res["ok"] is False


def test_retriever_cache_is_thread_safe(svc, monkeypatch):
    """동시 호출에도 모델별 VectorStore(=Qdrant local client)는 단 한 번만 생성돼야 한다.

    Qdrant local path 모드는 단일 writer 전제다. retriever 캐시를 lock 없이 lazy로 만들면
    백그라운드 ingest 스레드와 메인 검색 스레드가 같은 모델의 VectorStore를 두 개 열어
    같은 경로에 파일락 충돌을 낸다. 생성자를 느리게 만들고 Barrier로 동시 진입시켜
    race 창을 강제로 벌린다 — lock이 없으면 created가 2개 이상 쌓여 실패한다.
    """
    import threading
    import time

    from rag_mcp import service as service_mod

    created: list[str] = []

    class CountingStore:
        def __init__(self, config, model, document_id, client=None, lock=None):
            created.append((model, document_id))
            time.sleep(0.02)  # race 창 확대

        def status(self):
            return {}

    monkeypatch.setattr(service_mod, "VectorStore", CountingStore)

    results: list[object] = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()  # 8개 스레드를 동시에 진입시켜 경쟁 유발
        results.append(svc._retriever("kure", "d1"))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert created.count(("kure", "d1")) == 1, (
        f"VectorStore가 {created.count(('kure', 'd1'))}번 생성됨(파일락 위험)"
    )
    assert len({id(r) for r in results}) == 1, "스레드마다 다른 retriever 인스턴스를 받음"


def test_server_imports_and_registers_tools():
    """FastMCP server가 import되고 도구 8개(ingest_status 포함)가 등록되는지."""
    import asyncio
    from rag_mcp import server

    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert {
        "search_documents", "ingest_pdf", "ingest_status", "get_chunk", "list_documents",
        "delete_document", "reindex_document", "collection_status",
    } <= names


def test_preflight_detects_duplicate_instance(svc, fake_backend):
    """단일 인스턴스 가드: 같은 Qdrant local 경로를 두 번째 인스턴스가 preflight하면
    StorageBusyError가 나야 한다(server.py가 이걸 받아 중복 인스턴스를 종료)."""
    from rag_mcp.vector_store import StorageBusyError

    svc.preflight()  # 첫 인스턴스가 local 저장소 락 선점(임베딩 로드 없이 빠르게)
    dup = RagService(Config(), backend=fake_backend)  # svc와 같은 RAG_QDRANT_PATH
    with pytest.raises(StorageBusyError):
        dup.preflight()


def test_server_main_exits_when_storage_busy(monkeypatch):
    """중복 인스턴스는 좀비로 남지 않고 exit(0)으로 조용히 물러난다."""
    from rag_mcp import server
    from rag_mcp.vector_store import StorageBusyError

    monkeypatch.setattr(server, "_service", None)

    class _BusySvc:
        def preflight(self):
            raise StorageBusyError("사용 중")

        def warmup(self):  # 도달하면 안 됨
            raise AssertionError("preflight 실패 시 warmup을 부르면 안 됨")

    monkeypatch.setattr(server, "service", lambda: _BusySvc())
    # mcp.run()까지 가면 안 됨 — 도달 시 즉시 실패
    monkeypatch.setattr(server.mcp, "run", lambda: (_ for _ in ()).throw(AssertionError("mcp.run 도달")))

    with pytest.raises(SystemExit) as ei:
        server.main()
    assert ei.value.code == 0


def test_server_ingest_pdf_tool_returns_job(tmp_path, monkeypatch):
    """server.ingest_pdf 도구는 (동기 색인이 아니라) 즉시 job을 반환해야 함."""
    import asyncio

    from rag_mcp import server

    os.environ["RAG_QDRANT_PATH"] = str(tmp_path / "qdrant")
    os.environ["RAG_DATA_DIR"] = str(tmp_path)
    try:
        # service 싱글톤 초기화(테스트 격리)
        server._service = None
        pdf = tmp_path / "s.pdf"
        pdf.write_text("dummy")
        monkeypatch.setattr(
            server.RagService, "ingest_pdf",
            lambda self, path, **kw: {"ok": True, "document_id": "s", "num_chunks": 1, "status": "done"},
        )
        res = asyncio.run(server.ingest_pdf(str(pdf)))
        assert res["ok"] is True
        assert res["status"] == "running"
        assert res["job_id"]
    finally:
        server._service = None
        os.environ.pop("RAG_QDRANT_PATH", None)
        os.environ.pop("RAG_DATA_DIR", None)
