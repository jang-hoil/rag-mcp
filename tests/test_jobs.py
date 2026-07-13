"""비동기 ingest job 테스트 — submit_ingest / ingest_status.

큰 PDF는 동기 색인 시 MCP 클라이언트 타임아웃(약 4분)을 유발한다. 색인을 백그라운드
스레드로 던지고 즉시 job_id를 반환 → ingest_status로 폴링하는 구조를 검증한다.
실제 PDF 파싱·임베딩 없이 svc._ingest_pdf_unlocked를 monkeypatch해 빠르게 검증.
"""
import os
import threading
import time

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


def _poll(svc, job_id, timeout=5.0):
    deadline = time.time() + timeout
    st = svc.ingest_status(job_id)
    while st.get("status") == "running" and time.time() < deadline:
        time.sleep(0.02)
        st = svc.ingest_status(job_id)
    return st


def _seed(svc):
    chunks = [
        Chunk(
            chunk_id="d1::c0",
            document_id="d1",
            text="general operating expense guidance",
            fiscal_year=2026,
        )
    ]
    return svc.ingest_chunks("d1", chunks, doc_name="2026 budget guidance", fiscal_year=2026)


def test_active_ingest_rejects_delete_and_reindex_but_allows_search(
    svc, tmp_path, monkeypatch
):
    _seed(svc)
    pdf = tmp_path / "slow.pdf"
    pdf.write_bytes(b"%PDF")
    started = threading.Event()
    release = threading.Event()

    def slow_ingest(*args, **kwargs):
        started.set()
        assert release.wait(timeout=5)
        return {"ok": True, "document_id": "slow", "num_chunks": 1, "status": "done"}

    monkeypatch.setattr(svc, "_ingest_pdf_unlocked", slow_ingest)
    submitted = svc.submit_ingest(str(pdf), document_id="slow")
    assert started.wait(timeout=2)
    try:
        delete_result = svc.delete_document("d1", confirm=True)
        reindex_result = svc.reindex_document("d1")
        assert delete_result.get("status") == "busy"
        assert reindex_result.get("status") == "busy"
        assert delete_result["operation"] == "ingest_pdf"
        assert delete_result["job_id"] == submitted["job_id"]
        assert svc.search_documents("general operating expense")
        assert svc.manifests.read("d1") is not None
        assert svc.ingest_status(submitted["job_id"])["status"] == "running"
    finally:
        release.set()

    assert _poll(svc, submitted["job_id"])["status"] == "done"


def test_submit_ingest_returns_job_immediately_and_completes(svc, tmp_path, monkeypatch):
    pdf = tmp_path / "bg.pdf"
    pdf.write_text("dummy")

    def fake_ingest(path, **kw):
        time.sleep(0.05)  # 색인이 즉시 끝나지 않음을 흉내
        return {"ok": True, "document_id": "bg", "num_chunks": 3, "status": "done"}

    monkeypatch.setattr(svc, "_ingest_pdf_unlocked", fake_ingest)

    sub = svc.submit_ingest(str(pdf))
    # 즉시 반환되어야 함(블로킹 금지)
    assert sub["ok"] is True
    assert sub["status"] == "running"
    assert sub["job_id"]
    assert sub["document_id"] == "bg"

    st = _poll(svc, sub["job_id"])
    assert st["ok"] is True
    assert st["status"] == "done"
    assert st["result"]["num_chunks"] == 3


def test_submit_ingest_missing_file(svc):
    res = svc.submit_ingest("nonexistent.pdf")
    assert res["ok"] is False
    assert "job_id" not in res  # 스레드를 시작하지 않음


def test_ingest_status_unknown_job(svc):
    res = svc.ingest_status("deadbeefdeadbeef")
    assert res["ok"] is False
    assert "error" in res


def test_submit_ingest_records_error(svc, tmp_path, monkeypatch):
    pdf = tmp_path / "err.pdf"
    pdf.write_text("dummy")

    def boom(path, **kw):
        raise RuntimeError("색인 폭발")

    monkeypatch.setattr(svc, "_ingest_pdf_unlocked", boom)
    sub = svc.submit_ingest(str(pdf))
    assert sub["ok"] is True
    st = _poll(svc, sub["job_id"])
    assert st["status"] == "error"
    assert "색인 폭발" in st["error"]


def test_submit_ingest_propagates_ingest_failure(svc, tmp_path, monkeypatch):
    # ingest_pdf가 예외 없이 ok=False를 반환하는 경우(예: 파싱 실패)도 job error로 기록
    pdf = tmp_path / "f.pdf"
    pdf.write_text("dummy")
    monkeypatch.setattr(
        svc, "_ingest_pdf_unlocked", lambda path, **kw: {"ok": False, "error": "파싱 실패"}
    )
    sub = svc.submit_ingest(str(pdf))
    st = _poll(svc, sub["job_id"])
    assert st["status"] == "error"
    assert "파싱 실패" in st["error"]


def test_submit_ingest_rejects_concurrent(svc, tmp_path, monkeypatch):
    # Qdrant local은 단일 writer 전제 → 동시 ingest 1개만 허용
    pdf = tmp_path / "c.pdf"
    pdf.write_text("dummy")
    gate = threading.Event()

    def slow(path, **kw):
        gate.wait(3)
        return {"ok": True, "document_id": "c", "num_chunks": 1, "status": "done"}

    monkeypatch.setattr(svc, "_ingest_pdf_unlocked", slow)
    first = svc.submit_ingest(str(pdf))
    assert first["ok"] is True
    second = svc.submit_ingest(str(pdf))
    assert second["ok"] is False
    assert "진행 중" in second["error"]
    gate.set()
    st = _poll(svc, first["job_id"])
    assert st["status"] == "done"
    # 첫 작업이 끝나면 다시 제출 가능
    gate.clear()
    again = svc.submit_ingest(str(pdf))
    assert again["ok"] is True
    gate.set()
    _poll(svc, again["job_id"])
