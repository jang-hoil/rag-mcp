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
import json
import os
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
    exe = project / ".venv" / "Scripts" / "rag-mcp.exe"
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
