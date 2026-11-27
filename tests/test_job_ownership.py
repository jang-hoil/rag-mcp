"""다른 프로세스가 만든 job 조회와 스레드 시작 실패 기록.

- 다른 프로세스의 running job은 생사를 단정하지 않고 마지막 진척 + note로 안내한다.
- 상태 조회는 잠금을 잡지 않으므로 정상 쓰기 요청이 busy를 받지 않는다.
"""
import json
import subprocess
import sys
import textwrap

import pytest

from rag_mcp.config import Config
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


def test_other_process_job_is_reported_without_verdict(tmp_path, fake_backend, owner_process):
    """살아 있든 죽었든 다른 프로세스의 job은 running + 마지막 진척 + 비단정 안내로 답한다."""
    proc, job_id = owner_process
    observer = _service(tmp_path, fake_backend)

    for _ in range(2):  # 소유 프로세스 생존 중 → 종료 후
        status = observer.ingest_status(job_id)
        assert status["ok"] is True and status["status"] == "running"
        assert (status["processed"], status["total"]) == (75, 100)
        assert "이 서버 인스턴스에서 실행 중인 작업이 아님" in status["note"]
        assert "list_documents" in status["message"]
        proc.kill()
        proc.wait(timeout=10)


def test_status_query_does_not_make_writes_busy(tmp_path, fake_backend, owner_process):
    """조회가 확인용으로 잠금을 잡지 않으므로, 조회 직후 쓰기 예약이 바로 성공한다."""
    _proc, job_id = owner_process
    observer = _service(tmp_path, fake_backend)
    writer = _service(tmp_path, fake_backend)

    observer.ingest_status(job_id)
    token, busy = writer._reserve_mutation("reindex_document")
    try:
        assert busy is None
        # 다른 작업이 잠금을 쥐고 있는 동안에도 조회는 예외 없이 답한다.
        assert observer.ingest_status(job_id)["status"] == "running"
    finally:
        writer._release_mutation(token)


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
