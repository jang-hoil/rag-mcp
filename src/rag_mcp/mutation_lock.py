"""같은 데이터 폴더를 사용하는 MCP/CLI의 변경 작업을 OS 잠금으로 직렬화한다."""
from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import BinaryIO


class DataMutationLock:
    """비블로킹 파일 잠금. 스레드 간 인계 가능하며 프로세스 종료 시 OS가 해제한다.

    잠금 파일은 삭제하지 않는다. 지우면 대기자가 서로 다른 파일을 잠글 수 있다.
    인스턴스 자체의 동시 접근은 RagService의 _mutation_state_lock으로 보호한다.
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
