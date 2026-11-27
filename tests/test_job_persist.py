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


def test_running_job_after_restart_is_not_judged(tmp_path):
    """메모리에 없는 running job은 실패로 단정하지 않고 안내만 붙인다."""
    path = tmp_path / "last.json"
    job = JobStore(path).create(document_id="d1")

    restored = JobStore(path).get(job.job_id)

    assert restored.status == "running" and restored.error is None
    assert "중단됐거나 다른 클라이언트에서 진행 중" in restored.note
    assert restored.note in restored.to_dict()["message"]


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


def test_restored_job_keeps_last_progress_and_elapsed(tmp_path, monkeypatch):
    """진행 콜백의 단계 전환이 디스크에 남아, 재시작 후 마지막 진척·갱신 시각을 보여준다."""
    from rag_mcp import jobs as jobs_mod

    clock = [1000.0]
    monkeypatch.setattr(jobs_mod.time, "time", lambda: clock[0])
    path = tmp_path / "last.json"
    store = JobStore(path)
    job = store.create("d1")
    clock[0] = 1090.0
    store.progress(job.job_id, jobs_mod.STAGE_EMBEDDING, 75, 100)  # 단계 전환 → 즉시 기록
    clock[0] = 5000.0  # 한참 뒤 재시작

    restored = JobStore(path).get(job.job_id)

    assert restored.status == "running" and restored.stage == jobs_mod.STAGE_EMBEDDING
    assert (restored.processed, restored.total) == (75, 100)
    assert "마지막 갱신 65분 10초 전" in restored.note  # 5000 - 1090
    assert restored.elapsed_seconds() == 90.0  # 마지막 갱신까지, 재시작 시각 아님
    assert "임베딩 진행 중 75/100" in restored.to_dict()["message"]


def test_progress_within_same_stage_is_throttled(tmp_path, monkeypatch):
    from rag_mcp import jobs as jobs_mod

    mono = [0.0]
    monkeypatch.setattr(jobs_mod.time, "monotonic", lambda: mono[0])
    path = tmp_path / "last.json"
    store = JobStore(path)
    job = store.create("d1")
    store.progress(job.job_id, jobs_mod.STAGE_EMBEDDING, 1, 100)
    mono[0] = 1.0
    store.progress(job.job_id, jobs_mod.STAGE_EMBEDDING, 2, 100)  # 간격 미달 → 기록 안 함
    assert JobStore(path).get(job.job_id).processed == 1
    mono[0] = 1.0 + JobStore.PROGRESS_PERSIST_INTERVAL
    store.progress(job.job_id, jobs_mod.STAGE_EMBEDDING, 3, 100)
    assert JobStore(path).get(job.job_id).processed == 3


def test_non_object_state_file_is_ignored(tmp_path):
    path = tmp_path / "last.json"
    path.write_text("[]", encoding="utf-8")
    assert JobStore(path).get("아무거나") is None
