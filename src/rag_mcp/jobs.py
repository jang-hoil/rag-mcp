"""백그라운드 색인 작업(job) 추적 — MCP ingest 비동기화.

큰 PDF는 동기 색인 시 임베딩에만 수 분이 걸려 MCP 클라이언트 타임아웃(약 4~5분)을 넘긴다.
색인을 백그라운드 스레드로 던지고 즉시 job_id를 반환 → ingest_status로 폴링한다.

job은 단계(stage)와 진척도를 함께 들고 있다. status만으로는 클라이언트가 사용자에게
"지금 뭘 하는 중이고 얼마나 남았는지"를 말해줄 수 없어, 폴링해도 침묵하게 되기 때문이다.
to_dict()가 한국어 message까지 만들어 주므로 클라이언트는 그대로 옮기기만 하면 된다.

MCP 서버는 단일 stdio 프로세스이므로 job 상태는 인메모리로 충분하다(스레드 안전 dict).
색인 결과 자체는 manifest에 영구 저장되므로 서버 재시작으로 job이 사라져도 데이터는 안전.
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from typing import Optional

# 단계 이름은 클라이언트가 사용자에게 그대로 보여주는 값이라 한국어로 고정한다.
STAGE_WAITING = "대기"
STAGE_PARSING = "파싱"
STAGE_EMBEDDING = "임베딩"
STAGE_STORING = "저장"
STAGE_DONE = "완료"
STAGE_FAILED = "실패"


def format_duration(seconds: float) -> str:
    """경과 시간을 사람이 읽는 한국어로. 소수점·시간 단위는 쓰지 않는다(체감 정보만)."""
    total = int(max(0.0, seconds))
    if total < 60:
        return f"{total}초"
    minutes, rest = divmod(total, 60)
    if rest == 0:
        return f"{minutes}분"
    return f"{minutes}분 {rest}초"


@dataclass
class Job:
    job_id: str
    status: str  # "running" | "done" | "error"
    document_id: Optional[str] = None
    result: Optional[dict] = None
    error: Optional[str] = None
    started_at: float = 0.0
    finished_at: Optional[float] = None
    stage: str = STAGE_WAITING
    processed: int = 0
    total: int = 0

    def elapsed_seconds(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.time()
        return max(0.0, end - self.started_at)

    def percent(self) -> Optional[int]:
        if self.total <= 0:
            return None
        return min(100, round(self.processed / self.total * 100))

    def message(self) -> str:
        """클라이언트가 사용자에게 그대로 전달할 한국어 한 줄."""
        elapsed = format_duration(self.elapsed_seconds())
        if self.status == "error":
            return f"색인 실패 — {self.error} (경과 {elapsed})"
        if self.status == "done":
            chunks = (self.result or {}).get("num_chunks")
            head = "색인 완료" if chunks is None else f"색인 완료 — {chunks:,}청크"
            return f"{head}, 소요 {elapsed}"
        pct = self.percent()
        if pct is None:
            return f"{self.stage} 진행 중 — 경과 {elapsed}"
        return (
            f"{self.stage} 진행 중 {self.processed:,}/{self.total:,} ({pct}%) "
            f"— 경과 {elapsed}"
        )

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "status": self.status,
            "document_id": self.document_id,
            "result": self.result,
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "stage": self.stage,
            "processed": self.processed,
            "total": self.total,
            "percent": self.percent(),
            "elapsed_seconds": round(self.elapsed_seconds(), 1),
            "message": self.message(),
        }


class JobStore:
    """스레드 안전 job 레지스트리."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self, document_id: str | None = None) -> Job:
        job = Job(job_id=uuid.uuid4().hex, status="running",
                  document_id=document_id, started_at=time.time(),
                  stage=STAGE_WAITING)
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def progress(self, job_id: str, stage: str, processed: int = 0, total: int = 0) -> None:
        """색인 스레드가 단계 전환·진척을 보고한다(끝난 job은 갱신하지 않음)."""
        with self._lock:
            j = self._jobs.get(job_id)
            if j is not None and j.status == "running":
                j.stage = stage
                j.processed = processed
                j.total = total

    def finish(self, job_id: str, result: dict) -> None:
        with self._lock:
            j = self._jobs.get(job_id)
            if j is not None:
                j.status = "done"
                j.result = result
                j.finished_at = time.time()
                j.stage = STAGE_DONE
                num_chunks = result.get("num_chunks")
                if isinstance(num_chunks, int):
                    j.processed = j.total = num_chunks

    def fail(self, job_id: str, error: str) -> None:
        with self._lock:
            j = self._jobs.get(job_id)
            if j is not None:
                j.status = "error"
                j.error = error
                j.finished_at = time.time()
                j.stage = STAGE_FAILED

    def running(self) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if j.status == "running"]
