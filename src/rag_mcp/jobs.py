"""백그라운드 색인 작업(job) 추적 — MCP ingest 비동기화.

큰 PDF는 동기 색인 시 임베딩에만 수 분이 걸려 MCP 클라이언트 타임아웃(약 4~5분)을 넘긴다.
색인을 백그라운드 스레드로 던지고 즉시 job_id를 반환 → ingest_status로 폴링한다.

job은 단계(stage)와 진척도를 함께 들고 있다. status만으로는 클라이언트가 사용자에게
"지금 뭘 하는 중이고 얼마나 남았는지"를 말해줄 수 없어, 폴링해도 침묵하게 되기 때문이다.
to_dict()가 한국어 message까지 만들어 주므로 클라이언트는 그대로 옮기기만 하면 된다.

MCP 서버는 단일 stdio 프로세스이므로 job 상태는 인메모리로 관리한다(스레드 안전 dict).
색인 결과 자체는 manifest에 영구 저장되므로 서버 재시작으로 job이 사라져도 데이터는 안전.
다만 재시작 후에도 "방금 그 색인 어떻게 됐지?"에 답하도록 **마지막 job 1건**만
persist_path(JSON)에 남긴다(생성·단계 전환·완료·실패 시점).

디스크에만 있는 running job은 **그 job을 만든 프로세스**(PID + 프로세스 생성 시각 토큰)가
아직 살아 있는지로 판정한다. 공용 쓰기 잠금 점유 여부는 보지 않는다 — 다른 문서의 쓰기
작업이 잠금을 쥐어도 이 job의 소유자와는 무관하고, 확인하려고 잠금을 잡으면 정상 쓰기 요청이
busy를 받는다. 소유자가 죽었으면 '중단됨' 실패, 확인할 수 없으면 running에 "확인 불가"를 붙인다.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Callable, Optional

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
    # 마지막으로 진척을 관측(보고)한 시각. 중단 판정 시 경과 시간의 끝으로 쓴다.
    updated_at: Optional[float] = None
    # job을 만든 프로세스. PID 재사용과 구별하려고 프로세스 생성 시각 토큰을 함께 둔다.
    owner_pid: Optional[int] = None
    owner_token: Optional[str] = None
    # 소유자 생존을 확인하지 못했을 때만 채운다(저장하지 않는 조회 시점 정보).
    owner_check: Optional[str] = None

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
        note = f" ({self.owner_check})" if self.owner_check else ""
        pct = self.percent()
        if pct is None:
            return f"{self.stage} 진행 중 — 경과 {elapsed}{note}"
        return (
            f"{self.stage} 진행 중 {self.processed:,}/{self.total:,} ({pct}%) "
            f"— 경과 {elapsed}{note}"
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
            "owner_check": self.owner_check,
            "message": self.message(),
        }


OWNER_ALIVE = "alive"
OWNER_DEAD = "dead"
OWNER_UNKNOWN = "unknown"
_OWNER_UNKNOWN_NOTE = "소유 프로세스 생존 확인 불가 — 진행 중일 수도, 중단됐을 수도 있음"


def process_start_token(pid: int) -> Optional[str]:
    """실행 중인 pid의 생성 시각 토큰. 프로세스가 없으면 None, 판정할 수 없으면 OSError."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        process_query_limited_information = 0x1000
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            err = ctypes.get_last_error()
            if err == 87:  # ERROR_INVALID_PARAMETER: 그런 pid 없음
                return None
            raise OSError(err, f"OpenProcess 실패(pid={pid})")
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                raise OSError(ctypes.get_last_error(), "GetExitCodeProcess 실패")
            if code.value != 259:  # STILL_ACTIVE가 아니면 종료된 프로세스
                return None
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                raise OSError(ctypes.get_last_error(), "GetProcessTimes 실패")
            created = times[0]
            return str((created.dwHighDateTime << 32) | created.dwLowDateTime)
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)  # POSIX: 신호 0은 존재 확인만 한다
    except ProcessLookupError:
        return None
    except PermissionError:
        pass  # 존재하지만 다른 사용자 프로세스
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat.rsplit(")", 1)[1].split()[19]  # starttime
    except (OSError, IndexError):
        # ponytail: /proc 없는 플랫폼은 생존만 확인(PID 재사용 구별 불가), 필요 시 psutil로 교체
        return "alive"


_self_token_cache: list[Optional[str]] = []


def _self_token() -> Optional[str]:
    if not _self_token_cache:
        try:
            _self_token_cache.append(process_start_token(os.getpid()))
        except OSError:
            _self_token_cache.append(None)
    return _self_token_cache[0]


def default_owner_state(pid: Optional[int], token: Optional[str]) -> str:
    """기록된 소유 프로세스가 지금도 같은 프로세스로 살아 있는가."""
    if pid is None:
        return OWNER_DEAD  # 소유 정보가 없는 예전 기록
    try:
        current = process_start_token(pid)
    except OSError:
        return OWNER_UNKNOWN
    if current is None:
        return OWNER_DEAD
    if token is None or current == token:
        return OWNER_ALIVE
    return OWNER_DEAD  # 같은 PID를 다른 프로세스가 재사용


class JobStore:
    """스레드 안전 job 레지스트리."""

    # 진행 중 진척을 디스크에 남기는 최소 간격(초). 단계 전환은 간격과 무관하게 바로 기록한다.
    PROGRESS_PERSIST_INTERVAL = 5.0

    def __init__(
        self,
        persist_path: Path | None = None,
        owner_state: Callable[[Optional[int], Optional[str]], str] = default_owner_state,
    ) -> None:
        """owner_state(pid, token): 디스크에만 있는 running job 소유 프로세스의 상태
        (OWNER_ALIVE / OWNER_DEAD / OWNER_UNKNOWN). 테스트에서 재시작을 흉내 낼 때 주입한다."""
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._persist_path = persist_path
        self._owner_state = owner_state
        self._last_persist = 0.0

    def _persist(self, job: Job) -> None:
        """마지막 job 1건을 원자적으로 기록한다(락 안에서 호출). 실패해도 색인은 계속."""
        if self._persist_path is None:
            return
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._persist_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(job), ensure_ascii=False, default=str), encoding="utf-8")
            os.replace(tmp, self._persist_path)
            self._last_persist = time.monotonic()
        except OSError:
            pass

    def _read_persisted(self, job_id: str) -> Job | None:
        if self._persist_path is None:
            return None
        try:
            data = json.loads(self._persist_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("job_id") != job_id:
                return None
            return Job(**{f.name: data[f.name] for f in fields(Job) if f.name in data})
        except (OSError, ValueError, TypeError):
            return None

    def _load_persisted(self, job_id: str) -> Job | None:
        """이 인스턴스 메모리에 없는 job을 디스크의 마지막 기록에서 복원한다."""
        job = self._read_persisted(job_id)
        if job is None or job.status != "running":
            return job
        state = self._owner_state(job.owner_pid, job.owner_token)
        if state == OWNER_ALIVE:
            return job  # 다른 인스턴스가 색인 중 — 그 기록이 최신 진척이다
        if state != OWNER_DEAD:
            job.owner_check = _OWNER_UNKNOWN_NOTE
            return job
        # 소유자가 방금 완료·실패를 기록하고 끝났을 수 있다 → 중단 판정 직전에 다시 읽는다.
        latest = self._read_persisted(job_id)
        if latest is not None and latest.status != "running":
            return latest
        last = job.stage
        if job.total > 0:
            last += f" {job.processed:,}/{job.total:,}"
        job.status = "error"
        job.error = (f"서버가 재시작되어 색인이 중단되었습니다(마지막 관측: {last}). "
                     "list_documents로 상태를 확인하고 필요하면 다시 색인하세요")
        job.stage = STAGE_FAILED
        job.finished_at = job.updated_at or job.started_at
        return job

    def create(self, document_id: str | None = None) -> Job:
        job = Job(job_id=uuid.uuid4().hex, status="running",
                  document_id=document_id, started_at=time.time(),
                  stage=STAGE_WAITING, owner_pid=os.getpid(), owner_token=_self_token())
        with self._lock:
            self._jobs[job.job_id] = job
            self._persist(job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job if job is not None else self._load_persisted(job_id)

    def progress(self, job_id: str, stage: str, processed: int = 0, total: int = 0) -> None:
        """색인 스레드가 단계 전환·진척을 보고한다(끝난 job은 갱신하지 않음)."""
        with self._lock:
            j = self._jobs.get(job_id)
            if j is not None and j.status == "running":
                stage_changed = j.stage != stage
                j.stage = stage
                j.processed = processed
                j.total = total
                j.updated_at = time.time()
                # 재시작 후 '어디까지 했는지' 복원용: 단계 전환은 즉시, 그 외엔 일정 간격으로 기록.
                if (
                    stage_changed
                    or time.monotonic() - self._last_persist >= self.PROGRESS_PERSIST_INTERVAL
                ):
                    self._persist(j)

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
                self._persist(j)

    def fail(self, job_id: str, error: str) -> None:
        with self._lock:
            j = self._jobs.get(job_id)
            if j is not None:
                j.status = "error"
                j.error = error
                j.finished_at = time.time()
                j.stage = STAGE_FAILED
                self._persist(j)

    def running(self) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if j.status == "running"]
