"""서버 로그가 stdio 파이프를 막지 않는지 검증.

배경(2026-07-31 실측): MCP stdio 서버는 요청마다 `logger.info("Processing request ...")`를
**이벤트 루프 스레드에서** stderr로 쓴다(mcp/server/lowlevel/server.py:733). 클라이언트가
stderr를 읽어가지 않으면 Windows 파이프 버퍼(4,096바이트)가 차고, 그 write가 블록되면서
서버 전체가 정지한다 — 기동 79바이트 + 호출당 162바이트라 **25번째 호출**에서 얼어붙었다.
증상은 "도구 하나가 영원히 무응답, 앞뒤 호출은 정상"으로 2026-07-30 ingest_pdf 300초
타임아웃과 같은 모양이다.

방어는 두 겹이다: ① 요청마다 나가던 INFO를 끈다(log_level=WARNING)
② 기동 검사 후에는 stderr를 로그 파일로 돌려 파이프를 아예 쓰지 않는다.
"""
import contextlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from rag_mcp import server as server_mod
from rag_mcp.config import Config


def test_request_logging_is_not_written_per_call():
    """MCPServer 기본 INFO 로그가 stderr를 막지 않도록 WARNING으로 고정한다."""
    assert server_mod.mcp.settings.log_level == "WARNING"


def test_redirect_logs_to_file_replaces_stderr(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path))
    original = sys.stderr
    try:
        path = server_mod.redirect_logs_to_file(Config())
        assert path is not None
        assert path.exists()
        assert sys.stderr is not original, "stderr가 파일로 교체되어야 한다"
        print("색인 로그 한 줄", file=sys.stderr)
        sys.stderr.flush()
        assert "색인 로그 한 줄" in path.read_text(encoding="utf-8")
    finally:
        if sys.stderr is not original:
            sys.stderr.close()
        sys.stderr = original


def test_redirect_logs_to_file_falls_back_when_unwritable(tmp_path, monkeypatch):
    """로그 파일을 못 열어도 서버는 계속 떠야 한다(stderr 유지)."""
    blocker = tmp_path / "logs"
    blocker.write_text("나는 파일이라 디렉터리가 될 수 없다", encoding="utf-8")
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path))
    original = sys.stderr

    path = server_mod.redirect_logs_to_file(Config())

    assert path is None
    assert sys.stderr is original, "실패 시 stderr를 건드리면 안 된다"


# --- 로그 크기 제한(기동 시 회전)과 시각 표시 (리포트 #8) ---

_STAMP = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] ")


@contextlib.contextmanager
def _redirected(config):
    original = sys.stderr
    try:
        yield server_mod.redirect_logs_to_file(config)
    finally:
        if sys.stderr is not original:
            sys.stderr.close()
        sys.stderr = original


def _log_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def test_log_lines_are_timestamped_once_per_line(tmp_path, monkeypatch):
    """print는 본문과 줄바꿈을 따로 쓴다 — 줄마다 시각이 정확히 한 번만 붙어야 한다."""
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path))
    with _redirected(Config()) as path:
        print("첫 줄", file=sys.stderr)
        sys.stderr.write("둘째 줄\n셋째 ")
        sys.stderr.write("줄 이어쓰기\n")
        sys.stderr.flush()
    lines = _log_lines(path)
    body = [line for line in lines if "줄" in line]
    assert len(body) == 3
    for line in lines:
        assert _STAMP.match(line), f"시각 누락: {line!r}"
        assert len(_STAMP.findall(line)) == 1
    assert body[2].endswith("셋째 줄 이어쓰기")


def test_startup_marker_is_logged(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path))
    with _redirected(Config()) as path:
        sys.stderr.flush()
    assert any(f"pid={os.getpid()}" in line for line in _log_lines(path))


def test_oversized_log_is_rotated_at_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAG_LOG_MAX_BYTES", "100")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "server.log").write_text("옛 로그\n" * 50, encoding="utf-8")

    with _redirected(Config()) as path:
        print("새 기동", file=sys.stderr)
        sys.stderr.flush()

    rotated = log_dir / "server.log.1"
    assert rotated.exists()
    assert rotated.read_text(encoding="utf-8") == "옛 로그\n" * 50
    current = path.read_text(encoding="utf-8")
    assert "옛 로그" not in current and "새 기동" in current


def test_rotation_shifts_backups_and_drops_oldest(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAG_LOG_MAX_BYTES", "10")
    monkeypatch.setenv("RAG_LOG_BACKUPS", "2")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "server.log").write_text("현재" * 20, encoding="utf-8")
    (log_dir / "server.log.1").write_text("백업1", encoding="utf-8")
    (log_dir / "server.log.2").write_text("백업2", encoding="utf-8")

    with _redirected(Config()):
        pass

    assert (log_dir / "server.log.1").read_text(encoding="utf-8") == "현재" * 20
    assert (log_dir / "server.log.2").read_text(encoding="utf-8") == "백업1"
    assert not (log_dir / "server.log.3").exists()


def test_small_log_is_appended_not_rotated(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path))
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "server.log").write_text("이전 기록\n", encoding="utf-8")

    with _redirected(Config()) as path:
        print("이어서", file=sys.stderr)
        sys.stderr.flush()

    assert not (log_dir / "server.log.1").exists()
    text = path.read_text(encoding="utf-8")
    assert text.startswith("이전 기록\n") and "이어서" in text


def test_rotation_failure_still_redirects(tmp_path, monkeypatch):
    """다른 인스턴스가 로그를 열고 있으면(Windows) 이름 변경이 실패한다 — 회전만 건너뛴다."""
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAG_LOG_MAX_BYTES", "10")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "server.log").write_text("x" * 100, encoding="utf-8")

    def locked(*_args, **_kwargs):
        raise PermissionError("다른 프로세스가 사용 중")

    monkeypatch.setattr(server_mod.os, "replace", locked)
    with _redirected(Config()) as path:
        assert path is not None
        print("회전 실패 후에도 기록", file=sys.stderr)
        sys.stderr.flush()

    assert "회전 실패 후에도 기록" in path.read_text(encoding="utf-8")


def test_log_limit_zero_disables_rotation(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAG_LOG_MAX_BYTES", "0")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "server.log").write_text("x" * 10_000, encoding="utf-8")
    with _redirected(Config()):
        pass
    assert not (log_dir / "server.log.1").exists()


@pytest.mark.parametrize(
    ("key", "value"),
    [("RAG_LOG_MAX_BYTES", "-1"), ("RAG_LOG_BACKUPS", "0"), ("RAG_LOG_BACKUPS", "21")],
)
def test_log_settings_are_validated(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        Config()


# --- 여러 인스턴스가 같은 server.log를 쓸 때 (검증 지적: 회전 시 RecursionError·유실) ---


def test_startup_rotation_with_log_held_open_by_another_instance(tmp_path, monkeypatch):
    """다른 인스턴스가 server.log를 열어 둔 상태에서 크기 초과로 기동해도 예외 없이 이어 쓴다.

    Windows에서는 열린 파일의 이름 변경이 실제로 실패한다(monkeypatch 아닌 실제 핸들).
    실행 중 회전은 하지 않으므로 이후 쓰기도 두 인스턴스 모두 같은 파일에 남는다.
    """
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RAG_LOG_MAX_BYTES", "100")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log = log_dir / "server.log"
    log.write_text("기존 인스턴스 로그\n" * 20, encoding="utf-8")

    with log.open("a", encoding="utf-8", buffering=1) as other_instance:
        with _redirected(Config()) as path:
            assert path == log
            for i in range(200):  # 한도의 수십 배를 써도 실행 중 회전·예외 없음
                print(f"새 인스턴스 {i}", file=sys.stderr)
            other_instance.write("다른 인스턴스 계속 기록\n")
            sys.stderr.flush()

    text = "".join(
        p.read_text(encoding="utf-8") for p in sorted(log_dir.glob("server.log*"))
    )
    assert "새 인스턴스 199" in text
    assert "다른 인스턴스 계속 기록" in text


def test_log_write_failure_is_swallowed_without_recursion(tmp_path, monkeypatch):
    """파일 쓰기가 실패해도 print·logging이 예외(RecursionError 포함)를 내지 않는다."""
    import logging

    class BrokenFile:
        def write(self, _text):
            raise OSError(28, "No space left on device")

        def flush(self):
            raise OSError(28, "No space left on device")

        def close(self):
            pass

    original = sys.stderr
    sys.stderr = server_mod.TimestampedLogStream(BrokenFile())
    handler = logging.StreamHandler()  # 기본 stream = 현재 sys.stderr(깨진 파일)
    logger = logging.getLogger("rag_mcp.test_broken_log")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        print("기록 실패해도 계속", file=sys.stderr, flush=True)
        logger.warning("핸들러 오류가 같은 stderr로 되돌아와도 재귀하지 않는다")
    finally:
        logger.removeHandler(handler)
        sys.stderr = original


# --- 실제 서버 프로세스로 재현·회귀 (무거워서 기본 제외) ---

_RUN_SERVER = os.environ.get("RAG_RUN_SERVER_TESTS") == "1"


@pytest.mark.skipif(
    not _RUN_SERVER,
    reason="실제 서버 spawn 테스트: RAG_RUN_SERVER_TESTS=1 필요(임베딩 모델 로드)",
)
def test_server_survives_unread_stderr(tmp_path):
    """클라이언트가 stderr를 한 바이트도 안 읽어도 도구 호출이 계속 응답해야 한다.

    수정 전에는 25번째 호출에서 정지했다. 여유를 두고 60회를 확인한다.
    """
    project = Path(__file__).resolve().parents[1]
    exe = Path(sys.executable).with_name("rag-mcp.exe")
    if not exe.exists():
        pytest.skip(f"서버 실행 파일 없음: {exe}")

    env = dict(os.environ)
    env["RAG_DATA_DIR"] = str(tmp_path)
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        [str(exe), "serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,  # 일부러 읽지 않는다
        cwd=str(project), env=env, bufsize=0,
    )
    replies: dict[int, bool] = {}

    def reader() -> None:
        for raw in proc.stdout:
            try:
                msg = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if msg.get("id") is not None:
                replies[msg["id"]] = True

    threading.Thread(target=reader, daemon=True).start()

    def send(obj: dict) -> None:
        proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
        proc.stdin.flush()

    def wait_for(i: int, budget: float) -> bool:
        end = time.time() + budget
        while i not in replies and time.time() < end:
            time.sleep(0.02)
        return i in replies

    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0"}}})
        assert wait_for(1, 60), "initialize 응답 없음"
        send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        for i in range(2, 62):
            send({"jsonrpc": "2.0", "id": i, "method": "tools/call",
                  "params": {"name": "list_documents", "arguments": {}}})
            assert wait_for(i, 20), f"{i - 1}번째 호출에서 정지 — stderr 파이프가 막혔다"
    finally:
        proc.kill()
