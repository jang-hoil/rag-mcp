"""색인 진행 상황 보고 테스트 — submit_ingest 안내문 / ingest_status 단계·진척도.

배경: ingest_pdf는 즉시 job_id를 돌려주지만 응답에 소요시간 정보가 없고 ingest_status는
"running" 한 단어만 줬다. MCP 클라이언트(코덱스 등)가 사용자에게 "파싱 중이고 몇 분 걸린다"를
말해줄 재료가 없어 침묵하거나 실패로 오해했다. 이 테스트는 그 재료가 응답에 실리는지 본다.
"""
import os
import threading
import time

import pytest

from rag_mcp.config import Config
from rag_mcp.indexer import Indexer
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


def _pdf(tmp_path, pages: int, name: str = "guide.pdf"):
    import fitz

    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 100), f"page {i + 1} operating expense", fontsize=12)
    path = tmp_path / name
    doc.save(str(path))
    doc.close()
    return path


def _poll(svc, job_id, timeout=5.0):
    deadline = time.time() + timeout
    st = svc.ingest_status(job_id)
    while st.get("status") == "running" and time.time() < deadline:
        time.sleep(0.02)
        st = svc.ingest_status(job_id)
    return st


# --- submit_ingest 안내문 ---


def test_submit_ingest_returns_page_count_and_estimate(svc, tmp_path, monkeypatch):
    pdf = _pdf(tmp_path, pages=5)
    monkeypatch.setattr(
        svc, "_ingest_pdf_unlocked",
        lambda path, **kw: {"ok": True, "document_id": "guide", "num_chunks": 1, "status": "done"},
    )
    res = svc.submit_ingest(str(pdf))

    assert res["ok"] is True
    assert res["pages"] == 5
    assert res["estimated_minutes"] >= 1
    assert res["poll_after_seconds"] >= 1
    # 클라이언트가 사용자에게 그대로 전달할 수 있는 한국어 안내
    assert "색인" in res["message"]
    assert "5쪽" in res["message"]
    assert "ingest_status" in res["message"]
    _poll(svc, res["job_id"])


def test_submit_ingest_guidance_survives_unreadable_pdf(svc, tmp_path, monkeypatch):
    # 쪽수를 못 읽어도 안내는 나가야 한다(추정 실패가 색인을 막으면 안 됨)
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4 not really a pdf")
    monkeypatch.setattr(
        svc, "_ingest_pdf_unlocked",
        lambda path, **kw: {"ok": True, "document_id": "broken", "num_chunks": 1, "status": "done"},
    )
    res = svc.submit_ingest(str(broken))

    assert res["ok"] is True
    assert res["pages"] is None
    assert res["estimated_minutes"] is None
    assert "색인" in res["message"]
    assert "ingest_status" in res["message"]
    _poll(svc, res["job_id"])


def test_submit_ingest_estimate_scales_with_page_count(svc, tmp_path, monkeypatch):
    monkeypatch.setattr(
        svc, "_ingest_pdf_unlocked",
        lambda path, **kw: {"ok": True, "document_id": "x", "num_chunks": 1, "status": "done"},
    )
    small = svc.submit_ingest(str(_pdf(tmp_path, 3, "small.pdf")))
    _poll(svc, small["job_id"])
    big = svc.submit_ingest(str(_pdf(tmp_path, 200, "big.pdf")))
    _poll(svc, big["job_id"])

    assert big["estimated_minutes"] > small["estimated_minutes"]
    assert big["poll_after_seconds"] >= small["poll_after_seconds"]


# --- ingest_status 단계·진척도 ---


def test_ingest_status_reports_stage_and_progress(svc, tmp_path, monkeypatch):
    pdf = _pdf(tmp_path, pages=2)
    gate = threading.Event()
    reported = threading.Event()

    def staged_ingest(path, on_progress=None, **kw):
        assert on_progress is not None, "service가 진행 보고 콜백을 넘겨야 한다"
        on_progress("파싱", 0, 0)
        on_progress("임베딩", 30, 100)
        reported.set()
        assert gate.wait(timeout=5)
        return {"ok": True, "document_id": "p", "num_chunks": 100, "status": "done"}

    monkeypatch.setattr(svc, "_ingest_pdf_unlocked", staged_ingest)
    sub = svc.submit_ingest(str(pdf), document_id="p")
    assert reported.wait(timeout=3)

    st = svc.ingest_status(sub["job_id"])
    assert st["status"] == "running"
    assert st["stage"] == "임베딩"
    assert st["processed"] == 30
    assert st["total"] == 100
    assert st["percent"] == 30
    assert st["elapsed_seconds"] >= 0
    assert "임베딩" in st["message"]
    assert "30/100" in st["message"]

    gate.set()
    done = _poll(svc, sub["job_id"])
    assert done["status"] == "done"
    assert done["stage"] == "완료"
    assert "완료" in done["message"]


def test_ingest_status_message_on_error(svc, tmp_path, monkeypatch):
    pdf = _pdf(tmp_path, pages=1)

    def boom(path, **kw):
        raise RuntimeError("파서 폭발")

    monkeypatch.setattr(svc, "_ingest_pdf_unlocked", boom)
    sub = svc.submit_ingest(str(pdf))
    st = _poll(svc, sub["job_id"])

    assert st["status"] == "error"
    assert st["stage"] == "실패"
    assert "파서 폭발" in st["message"]


# --- indexer 임베딩 진척 콜백 ---


def test_index_chunks_reports_embedding_and_store_stages(tmp_path, fake_backend, monkeypatch):
    monkeypatch.setenv("RAG_QDRANT_PATH", str(tmp_path / "qdrant"))
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path))
    config = Config()
    indexer = Indexer(config, "kure", "d1", backend=fake_backend)
    chunks = [
        Chunk(chunk_id=f"d1::c{i}", document_id="d1", text=f"항목 {i} 일반수용비", fiscal_year=2026)
        for i in range(10)
    ]
    seen: list[tuple[str, int, int]] = []

    indexer.index_chunks("d1", chunks, on_progress=lambda s, d, t: seen.append((s, d, t)))

    embedding = [row for row in seen if row[0] == "임베딩"]
    assert embedding, "임베딩 진척 보고가 있어야 한다"
    assert embedding[-1][1] == 10, "마지막 보고는 전체 청크 수여야 한다"
    assert all(row[2] == 10 for row in embedding), "total은 전체 청크 수로 고정"
    # 진척은 단조 증가
    assert [row[1] for row in embedding] == sorted(row[1] for row in embedding)
    assert any(row[0] == "저장" for row in seen), "Qdrant 저장 단계도 보고해야 한다"


def test_progress_chain_from_submit_to_real_indexer(svc, tmp_path, monkeypatch):
    """파서만 대체하고 색인기·job store는 실물로 태워 단계 전이 순서를 확인한다.

    단위 테스트가 각 조각을 따로 보므로, 여기서는 조각들이 실제로 이어지는지만 본다.
    """
    pdf = _pdf(tmp_path, pages=2, name="real.pdf")
    chunks = [
        Chunk(chunk_id=f"e2e::c{i}", document_id="e2e", text=f"제{i}항 일반수용비 집행 기준",
              fiscal_year=2026)
        for i in range(150)  # 임베딩 배치(64)를 여러 번 돌게 하는 수
    ]
    monkeypatch.setattr(
        "rag_mcp.pipeline.parse_and_chunk",
        lambda *a, **k: (chunks, {"doc_name": "실무 지침", "fiscal_year": 2026}),
    )
    seen: list[tuple[str, int, int]] = []
    original = svc.jobs.progress

    def spy(job_id, stage, processed=0, total=0):
        seen.append((stage, processed, total))
        original(job_id, stage, processed, total)

    monkeypatch.setattr(svc.jobs, "progress", spy)

    sub = svc.submit_ingest(str(pdf), document_id="e2e")
    st = _poll(svc, sub["job_id"], timeout=60)

    assert st["status"] == "done", st.get("error")
    assert st["stage"] == "완료"
    assert st["total"] == 150
    assert "색인 완료" in st["message"] and "150청크" in st["message"]

    stages = [row[0] for row in seen]
    assert stages[0] == "파싱"
    assert "임베딩" in stages and "저장" in stages
    assert stages.index("임베딩") < stages.index("저장"), "임베딩이 저장보다 먼저"
    embedding = [row for row in seen if row[0] == "임베딩"]
    assert embedding[-1][1] == 150, "임베딩 진척이 전체 청크 수까지 올라가야 한다"
    assert len(embedding) > 2, "배치마다 보고되어야 한다(150청크 / 배치 64)"


def test_index_chunks_without_callback_still_works(tmp_path, fake_backend, monkeypatch):
    # 콜백은 선택 인자 — CLI·기존 호출부가 그대로 동작해야 한다
    monkeypatch.setenv("RAG_QDRANT_PATH", str(tmp_path / "qdrant"))
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path))
    config = Config()
    indexer = Indexer(config, "kure", "d2", backend=fake_backend)
    chunks = [Chunk(chunk_id="d2::c0", document_id="d2", text="일상경비 한도", fiscal_year=2026)]

    manifest = indexer.index_chunks("d2", chunks)

    assert manifest.status == "done"
    assert manifest.num_chunks == 1
