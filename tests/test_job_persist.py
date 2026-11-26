"""마지막 색인 job 1건이 재시작(새 JobStore) 후에도 ingest_status로 조회되는지 (리포트 #14)."""
from rag_mcp.jobs import JobStore


def test_finished_job_survives_restart(tmp_path):
    path = tmp_path / "jobs" / "last_ingest_job.json"
    store = JobStore(path)
    job = store.create(document_id="d1")
    store.finish(job.job_id, {"num_chunks": 12})

    restored = JobStore(path).get(job.job_id)

    assert restored is not None
    assert restored.status == "done"
    assert restored.document_id == "d1"
    assert "12청크" in restored.to_dict()["message"]


def test_running_job_reports_interrupted_after_restart(tmp_path):
    path = tmp_path / "last.json"
    job = JobStore(path).create(document_id="d1")

    restored = JobStore(path).get(job.job_id)

    assert restored.status == "error"
    assert "중단" in restored.error


def test_only_last_job_is_kept(tmp_path):
    path = tmp_path / "last.json"
    store = JobStore(path)
    first = store.create("d1")
    second = store.create("d2")

    fresh = JobStore(path)
    assert fresh.get(first.job_id) is None
    assert fresh.get(second.job_id).document_id == "d2"


def test_failed_job_survives_restart_and_missing_file_is_none(tmp_path):
    path = tmp_path / "last.json"
    assert JobStore(path).get("없음") is None
    store = JobStore(path)
    job = store.create("d1")
    store.fail(job.job_id, "파싱 오류")
    restored = JobStore(path).get(job.job_id)
    assert restored.status == "error" and restored.error == "파싱 오류"


def test_service_ingest_status_reads_persisted_job(tmp_path, fake_backend):
    from rag_mcp.config import Config
    from rag_mcp.service import RagService

    cfg = Config(data_dir=tmp_path, qdrant_path=tmp_path / "qdrant")
    svc = RagService(cfg, backend=fake_backend)
    job = svc.jobs.create("d1")
    svc.jobs.finish(job.job_id, {"num_chunks": 3})

    restarted = RagService(cfg, backend=fake_backend)
    status = restarted.ingest_status(job.job_id)

    assert status["ok"] is True and status["status"] == "done"
    assert (tmp_path / "jobs" / "last_ingest_job.json").exists()
