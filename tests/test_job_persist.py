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


def test_interrupted_job_keeps_last_progress_and_elapsed(tmp_path, monkeypatch):
    """진행 콜백의 단계 전환이 디스크에 남아, 중단 판정 시 마지막 진척·관측 시각을 쓴다."""
    from rag_mcp import jobs as jobs_mod

    clock = [1000.0]
    monkeypatch.setattr(jobs_mod.time, "time", lambda: clock[0])
    path = tmp_path / "last.json"
    store = JobStore(path)
    job = store.create("d1")
    clock[0] = 1090.0
    store.progress(job.job_id, jobs_mod.STAGE_EMBEDDING, 75, 100)  # 단계 전환 → 즉시 기록
    clock[0] = 5000.0  # 한참 뒤 재시작

    restored = JobStore(path, owner_alive=lambda: False).get(job.job_id)

    assert restored.status == "error"
    assert (restored.processed, restored.total) == (75, 100)
    assert "임베딩 75/100" in restored.error
    assert restored.elapsed_seconds() == 90.0  # 마지막 관측까지, 재시작 시각 아님


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


def test_running_job_of_live_other_instance_is_not_marked_failed(tmp_path, fake_backend):
    """다른 인스턴스가 쓰기 잠금을 쥐고 색인 중이면 그 job은 running 그대로 보인다."""
    from rag_mcp import jobs as jobs_mod
    from rag_mcp.config import Config
    from rag_mcp.service import RagService

    cfg = Config(data_dir=tmp_path, qdrant_path=tmp_path / "qdrant")
    owner = RagService(cfg, backend=fake_backend)
    other = RagService(cfg, backend=fake_backend)
    token, busy = owner._reserve_mutation("ingest_pdf")
    assert busy is None
    try:
        job = owner.jobs.create("d1")
        owner.jobs.progress(job.job_id, jobs_mod.STAGE_EMBEDDING, 75, 100)

        assert owner.ingest_status(job.job_id)["status"] == "running"
        seen = other.ingest_status(job.job_id)
        assert seen["ok"] is True and seen["status"] == "running"
        assert (seen["processed"], seen["total"]) == (75, 100)
    finally:
        owner._release_mutation(token)

    # 소유자가 잠금을 놓은 뒤(=죽은 것과 같은 상태)에는 중단으로 판정한다.
    after = other.ingest_status(job.job_id)
    assert after["status"] == "error" and "중단" in after["error"]
    # 탐침이 잠금을 남기지 않는다.
    token2, busy2 = other._reserve_mutation("ingest_pdf")
    assert busy2 is None
    other._release_mutation(token2)
