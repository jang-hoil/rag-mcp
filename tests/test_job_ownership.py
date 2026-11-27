"""재검증 지적 재현: job별 소유권 판정, 완료 경계 재확인, 스레드 시작 실패 기록.

- 다른 문서의 쓰기 작업이 공용 잠금을 잡아도, 소유 프로세스가 죽은 job은 계속 '중단'이다.
- 상태 조회는 잠금을 잡지 않으므로 정상 쓰기 요청이 busy를 받지 않는다.
- 소유자 확인이 불가능하면 예외 대신 running + owner_check로 알린다.
"""
import json
import subprocess
import sys
import textwrap

import pytest

from rag_mcp import jobs as jobs_mod
from rag_mcp.config import Config
from rag_mcp.jobs import OWNER_ALIVE, OWNER_DEAD, JobStore, default_owner_state
from rag_mcp.service import RagService

_OWNER_SCRIPT = textwrap.dedent(
    """
    import sys
    from pathlib import Path
    from rag_mcp.jobs import JobStore, STAGE_EMBEDDING

    store = JobStore(Path(sys.argv[1]))
    job = store.create("d1")
    store.progress(job.job_id, STAGE_EMBEDDING, 75, 100)
    print(job.job_id, flush=True)
    sys.stdin.readline()  # 테스트가 kill할 때까지 대기(색인 중인 소유 프로세스 역할)
    """
)


@pytest.fixture
def owner_process(tmp_path):
    state = tmp_path / "jobs" / "last_ingest_job.json"
    proc = subprocess.Popen(
        [sys.executable, "-c", _OWNER_SCRIPT, str(state)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        job_id = proc.stdout.readline().strip()
        assert job_id, "소유 프로세스가 job을 만들지 못했다"
        yield proc, job_id
    finally:
        proc.kill()
        proc.wait(timeout=10)


def _service(tmp_path, fake_backend):
    cfg = Config(data_dir=tmp_path, qdrant_path=tmp_path / "qdrant")
    return RagService(cfg, backend=fake_backend)


def test_job_of_live_owner_process_is_running(tmp_path, fake_backend, owner_process):
    _proc, job_id = owner_process
    observer = _service(tmp_path, fake_backend)

    status = observer.ingest_status(job_id)

    assert status["ok"] is True and status["status"] == "running"
    assert (status["processed"], status["total"]) == (75, 100)
    assert status["owner_check"] is None


def test_status_query_does_not_make_writes_busy(tmp_path, fake_backend, owner_process):
    """조회가 확인용으로 잠금을 잡지 않으므로, 조회 직후 쓰기 예약이 바로 성공한다."""
    _proc, job_id = owner_process
    observer = _service(tmp_path, fake_backend)
    writer = _service(tmp_path, fake_backend)

    observer.ingest_status(job_id)
    token, busy = writer._reserve_mutation("reindex_document")
    try:
        assert busy is None
        # 다른 작업이 잠금을 쥐고 있는 동안에도 조회는 예외 없이 소유자 기준으로 답한다.
        assert observer.ingest_status(job_id)["status"] == "running"
    finally:
        writer._release_mutation(token)


def test_dead_owner_stays_interrupted_while_another_write_holds_lock(
    tmp_path, fake_backend, owner_process
):
    """소유 프로세스 종료 후, 다른 문서의 쓰기 잠금이 잡혀도 같은 job이 running으로 되살아나지 않는다."""
    proc, job_id = owner_process
    proc.kill()
    proc.wait(timeout=10)
    observer = _service(tmp_path, fake_backend)
    other_writer = _service(tmp_path, fake_backend)

    assert observer.ingest_status(job_id)["status"] == "error"
    token, busy = other_writer._reserve_mutation("reindex_document")
    assert busy is None
    try:
        during = observer.ingest_status(job_id)
        assert during["status"] == "error" and "임베딩 75/100" in during["error"]
    finally:
        other_writer._release_mutation(token)
    assert observer.ingest_status(job_id)["status"] == "error"


def test_owner_check_failure_reports_unknown_instead_of_raising(tmp_path, monkeypatch):
    path = tmp_path / "last.json"
    job = JobStore(path).create("d1")

    def cannot_tell(_pid):
        raise OSError(5, "Access is denied")

    monkeypatch.setattr(jobs_mod, "process_start_token", cannot_tell)
    restored = JobStore(path).get(job.job_id)

    assert restored.status == "running"
    assert "확인 불가" in restored.owner_check
    assert "확인 불가" in restored.to_dict()["message"]


def test_finish_between_read_and_verdict_returns_done(tmp_path):
    """조회자가 running을 읽은 직후 소유자가 done을 저장하고 끝나면, 실패가 아니라 done을 돌려준다."""
    path = tmp_path / "last.json"
    owner = JobStore(path)
    job = owner.create("d1")

    def owner_finishes_then_exits(_pid, _token):
        owner.finish(job.job_id, {"num_chunks": 7})
        return OWNER_DEAD

    restored = JobStore(path, owner_state=owner_finishes_then_exits).get(job.job_id)

    assert restored.status == "done"
    assert restored.result == {"num_chunks": 7}


def test_owner_identity_distinguishes_reused_pid(tmp_path):
    import os

    me = os.getpid()
    assert default_owner_state(me, jobs_mod._self_token()) == OWNER_ALIVE
    assert default_owner_state(me, "다른 프로세스의 생성 시각") == OWNER_DEAD
    assert default_owner_state(None, None) == OWNER_DEAD

    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait(timeout=30)
    assert default_owner_state(finished.pid, "아무거나") == OWNER_DEAD


def test_thread_start_failure_records_job_error(tmp_path, fake_backend, monkeypatch):
    """백그라운드 스레드를 못 띄우면 job이 running으로 남지 않고 실패 이유가 기록된다."""
    from rag_mcp import service as service_mod

    svc = _service(tmp_path, fake_backend)
    pdf = tmp_path / "문서.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    class CannotStart:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(service_mod.threading, "Thread", CannotStart)
    with pytest.raises(RuntimeError):
        svc.submit_ingest(str(pdf))
    monkeypatch.undo()

    state = json.loads((tmp_path / "jobs" / "last_ingest_job.json").read_text(encoding="utf-8"))
    restarted = _service(tmp_path, fake_backend)
    persisted = restarted.ingest_status(state["job_id"])
    assert persisted["status"] == "error"
    persisted = restarted.jobs.get(state["job_id"])
    assert "시작 실패" in persisted.error and "can't start new thread" in persisted.error
    # 잠금도 풀려 다음 쓰기가 가능하다.
    token, busy = restarted._reserve_mutation("ingest_pdf")
    assert busy is None
    restarted._release_mutation(token)
