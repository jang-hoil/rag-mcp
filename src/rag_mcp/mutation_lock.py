"""같은 데이터 폴더를 사용하는 MCP/CLI의 변경 작업을 OS 잠금으로 직렬화한다."""
from __future__ import annotations

import errno
import os
import threading
import uuid
from pathlib import Path
from typing import BinaryIO


class DataMutationLock:
    """비블로킹 파일 잠금. 스레드 간 인계 가능하며 프로세스 종료 시 OS가 해제한다.

    잠금 파일은 삭제하지 않는다. 지우면 대기자가 서로 다른 파일을 잠글 수 있다.
    인스턴스 자체의 동시 접근은 MutationGuard의 상태 락으로 보호한다.
    """

    def __init__(self, data_dir: Path):
        self.path = data_dir.resolve() / ".locks" / "mutation.lock"
        self._stream: BinaryIO | None = None

    def try_acquire(self) -> bool:
        if self._stream is not None:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                stream.seek(0)
                # Windows는 EOF 뒤의 바이트도 잠글 수 있으므로 파일 쓰기가 필요 없다.
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            stream.close()
            if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                return False
            raise
        except BaseException:
            stream.close()
            raise
        self._stream = stream
        return True

    def release(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            # 두 플랫폼 모두 파일 핸들을 닫으면 잠금이 해제된다.
            stream.close()


class MutationGuard:
    """데이터 변경 작업 1개 제한 — 프로세스 안(상태+스레드 락)과 프로세스 간(DataMutationLock).

    reserve()는 기다리지 않는다. 이미 다른 쓰기 작업이 있으면 (None, busy 응답 dict)를,
    획득하면 (token, None)을 돌려준다. release(token)은 같은 token일 때만 해제한다.
    """

    def __init__(self, data_dir: Path):
        self._state_lock = threading.Lock()
        self._lock = DataMutationLock(data_dir)
        self.active: dict[str, str | None] | None = None

    def reserve(
        self, operation: str, job_id: str | None = None
    ) -> tuple[str | None, dict | None]:
        with self._state_lock:
            if self.active is not None:
                active = {
                    key: value
                    for key, value in self.active.items()
                    if key != "token" and value is not None
                }
                return None, {
                    "ok": False,
                    "status": "busy",
                    "error": "다른 쓰기 작업이 진행 중입니다. 완료 후 다시 시도하세요.",
                    **active,
                }
            if not self._lock.try_acquire():
                return None, {
                    "ok": False,
                    "status": "busy",
                    "error": (
                        "같은 데이터 폴더의 다른 MCP/CLI에서 쓰기 작업이 진행 중입니다. "
                        "완료 후 다시 시도하세요."
                    ),
                }
            token = uuid.uuid4().hex
            self.active = {
                "token": token,
                "operation": operation,
                "job_id": job_id,
            }
            return token, None

    def attach_job_id(self, token: str, job_id: str) -> None:
        with self._state_lock:
            if self.active and self.active["token"] == token:
                self.active["job_id"] = job_id

    def release(self, token: str) -> None:
        with self._state_lock:
            if self.active and self.active["token"] == token:
                self._lock.release()
                self.active = None
