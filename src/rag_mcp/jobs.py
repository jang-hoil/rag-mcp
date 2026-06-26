"""백그라운드 색인 작업(job) 추적 — MCP ingest 비동기화.

큰 PDF는 동기 색인 시 임베딩에만 수 분이 걸려 MCP 클라이언트 타임아웃(약 4분)을 넘긴다.
색인을 백그라운드 스레드로 던지고 즉시 job_id를 반환 → ingest_status로 폴링한다.

MCP 서버는 단일 stdio 프로세스이므로 job 상태는 인메모리로 충분하다(스레드 안전 dict).
색인 결과 자체는 manifest에 영구 저장되므로 서버 재시작으로 job이 사라져도 데이터는 안전.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Optional


@dataclass
class Job:
    job_id: str
    status: str  # "running" | "done" | "error"
    document_id: Optional[str] = None
    result: Optional[dict] = None
    error: Optional[str] = None
    started_at: float = 0.0
    finished_at: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "document_id": self.document_id,
            "result": self.result,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class JobStore:
    """스레드 안전 job 레지스트리."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, document_id: str | None = None) -> Job:
        job = Job(job_id=uuid.uuid4().hex, status="running",
                  document_id=document_id, started_at=time.time())
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def finish(self, job_id: str, result: dict) -> None:
        with self._lock:
            j = self._jobs.get(job_id)
            if j is not None:
                j.status = "done"
                j.result = result
                j.finished_at = time.time()

    def fail(self, job_id: str, error: str) -> None:
        with self._lock:
            j = self._jobs.get(job_id)
            if j is not None:
                j.status = "error"
                j.error = error
                j.finished_at = time.time()

    def running(self) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if j.status == "running"]
