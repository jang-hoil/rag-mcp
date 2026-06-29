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


def test_warmup_loads_backend(svc):
    """warmup()은 임베딩 백엔드와 토크나이저를 미리 데워 첫 검색 콜드 스타트를 없앤다."""
    backend = svc._retriever("kure").backend  # 캐시되는 동일 인스턴스
    calls: list[str] = []
    orig = backend.embed_query
    backend.embed_query = lambda text: (calls.append(text), orig(text))[1]
    svc.warmup()
    assert calls, "warmup이 임베딩 백엔드를 호출하지 않음"


def test_search_invalid_mode_raises(svc):
    _seed(svc)
    with pytest.raises(ValueError):
        svc.search_documents("x", search_mode="fuzzy")


def test_search_top_k_out_of_range_raises(svc):
    _seed(svc)
    # 하한(0·음수)·상한(100 초과)·bool(True가 1로 통과하는 함정) 모두 거부
    for bad in (0, -1, 101, True):
        with pytest.raises(ValueError):
            svc.search_documents("x", top_k=bad)


def test_search_rejects_unknown_filter_key(svc):
    _seed(svc)
    with pytest.raises(ValueError):
        svc.search_documents("x", filters={"unknown_field": 1})


def test_search_accepts_allowed_filter_key(svc):
    _seed(svc)
    # is_table=True인 d1::c0만 후보로 남아야 함(에러 없이 동작)
    results = svc.search_documents("일반수용비 한도", filters={"is_table": True})
    assert all(r["source"]["is_table"] for r in results)


def test_metadata_stored_and_shown(svc):
    # metadata가 청크에 저장되고 검색 결과 source.meta로 노출되는지
    chunks = [Chunk(chunk_id="m1::c0", document_id="m1", text="201-01 일반수용비 한도", fiscal_year=2026)]
    svc.ingest_chunks("m1", chunks, doc_name="2026 예산기준", fiscal_year=2026,
                      metadata={"부서": "예산과", "작성자": "홍길동"})
    res = svc.search_documents("일반수용비 한도", fiscal_year=2026)
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
    res = svc.search_documents(common, filters={"meta.부서": "예산과"})
    assert res, "필터 결과 없음"
    assert all(r["source"]["meta"].get("부서") == "예산과" for r in res)
    assert all(r["source"]["document_id"] == "m2" for r in res)


def test_metadata_filter_key_allowed(svc):
    # meta.* 필터 키는 allowlist를 통과해야 함(에러 없이 동작)
    _seed(svc)
    svc.search_documents("x", filters={"meta.anything": "v"})  # ValueError 안 나면 통과


def test_search_result_includes_has_code(svc):
    # has_code는 Chunk·payload에 있으니 검색 결과 source에도 노출돼야 함(스키마 일관성)
    _seed(svc)
    results = svc.search_documents("201-01 한도", fiscal_year=2026)
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
    guarded = svc.delete_document("d1", confirm=False)
    assert guarded["ok"] is False
    assert svc.list_documents()  # 아직 존재
    done = svc.delete_document("d1", confirm=True)
    assert done["ok"] is True
    assert svc.list_documents() == []


def test_index_embed_failure_preserves_existing(svc, monkeypatch):
    # 재색인 중 임베딩이 실패해도 기존 색인 데이터가 사라지면 안 됨(실패 안전성)
    svc.ingest_chunks("e1", [Chunk(chunk_id="e1::c0", document_id="e1",
                                   text="원본 데이터 보존 확인", fiscal_year=2026)])
    assert svc.search_documents("원본 데이터 보존 확인"), "초기 색인 실패"

    def boom(texts):
        raise RuntimeError("embed 실패 주입")

    monkeypatch.setattr(svc._backend, "embed_documents", boom)
    with pytest.raises(RuntimeError):
        svc.ingest_chunks("e1", [Chunk(chunk_id="e1::c1", document_id="e1",
                                       text="새 데이터", fiscal_year=2026)])
    # 핵심: 임베딩 실패로 기존 데이터가 유실되면 안 됨
    assert svc.search_documents("원본 데이터 보존 확인"), "임베딩 실패로 기존 데이터 유실됨!"


def test_reindex_tool(svc):
    _seed(svc)
    res = svc.reindex_document("d1", reparse=False)
    assert res["ok"] is True
    assert res["num_chunks"] == 2


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
    hits = svc.search_documents("새로 파싱된 본문")
    assert hits, "재파싱된 본문이 검색되지 않음"
    assert hits[0]["source"]["meta"]["부서"] == "예산과", "reparse 후 사용자 metadata 유실됨"


def test_collection_status_tool(svc):
    _seed(svc)
    st = svc.collection_status()
    assert st["documents"] == 1
    assert st["by_fiscal_year"].get("2026") == 1
    assert "kure" in st["collections"]

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
    hit = svc.search_documents("OCR 본문")
    assert hit[0]["source"]["meta"]["ocr"]["ocr_skipped"] == "pytesseract 미설치"


def test_search_rejects_non_scalar_filter_value(svc):
    _seed(svc)
    with pytest.raises(ValueError):
        svc.search_documents("일반수용비", filters={"page": [9]})

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
        def __init__(self, config, model):
            created.append(model)
            time.sleep(0.02)  # race 창 확대

        def status(self):
            return {}

    monkeypatch.setattr(service_mod, "VectorStore", CountingStore)

    results: list[object] = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()  # 8개 스레드를 동시에 진입시켜 경쟁 유발
        results.append(svc._retriever("kure"))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert created.count("kure") == 1, f"VectorStore가 {created.count('kure')}번 생성됨(파일락 위험)"
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
