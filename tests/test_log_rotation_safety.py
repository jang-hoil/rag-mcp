"""재검증 지적 재현: 회전 실패 시 백업 보존, 쓰기 실패 직후 다음 줄 시각 누락."""
import os
import re

import pytest

from rag_mcp import server as server_mod

_STAMP = re.compile(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\] ")


def _seed_logs(log_dir):
    log_dir.mkdir(exist_ok=True)
    (log_dir / "server.log").write_text("활성" * 50, encoding="utf-8")
    for i in (1, 2, 3):
        (log_dir / f"server.log.{i}").write_text(f"백업{i}", encoding="utf-8")


def _backups(log_dir):
    return {
        i: (log_dir / f"server.log.{i}").read_text(encoding="utf-8")
        for i in (1, 2, 3)
        if (log_dir / f"server.log.{i}").exists()
    }


@pytest.mark.skipif(os.name != "nt", reason="열린 파일의 이름 변경 거부는 Windows 동작")
def test_backups_survive_when_active_log_is_held_open(tmp_path):
    """다른 인스턴스가 활성 로그를 열고 있으면 재기동을 반복해도 백업 3개가 그대로 남는다."""
    _seed_logs(tmp_path)
    log = tmp_path / "server.log"
    with log.open("a", encoding="utf-8"):
        for _ in range(3):
            assert server_mod.rotate_log_if_needed(log, 10, 3) is False
    assert _backups(tmp_path) == {1: "백업1", 2: "백업2", 3: "백업3"}
    assert log.read_text(encoding="utf-8") == "활성" * 50


def test_backups_untouched_when_active_rename_fails(tmp_path, monkeypatch):
    """플랫폼과 무관하게: 활성 로그 이동이 실패하면 백업 삭제·이동이 일어나지 않는다."""
    _seed_logs(tmp_path)
    log = tmp_path / "server.log"
    real_replace = os.replace

    def replace(src, dst):
        if os.fspath(src) == os.fspath(log):
            raise PermissionError("다른 프로세스가 사용 중")
        return real_replace(src, dst)

    monkeypatch.setattr(server_mod.os, "replace", replace)
    assert server_mod.rotate_log_if_needed(log, 10, 3) is False
    assert _backups(tmp_path) == {1: "백업1", 2: "백업2", 3: "백업3"}
    assert not (tmp_path / "server.log.rotating").exists()


def test_successful_rotation_still_shifts_backups(tmp_path):
    _seed_logs(tmp_path)
    log = tmp_path / "server.log"
    assert server_mod.rotate_log_if_needed(log, 10, 3) is True
    assert _backups(tmp_path) == {1: "활성" * 50, 2: "백업1", 3: "백업2"}
    assert not log.exists()
    assert not (tmp_path / "server.log.rotating").exists()


def test_failed_write_keeps_line_start_so_next_line_is_stamped():
    """줄바꿈 없는 첫 조각 쓰기만 실패해도, 다음 조각은 줄 머리이므로 시각이 붙어야 한다."""

    class FlakyFile:
        def __init__(self):
            self.fail_next = True
            self.chunks = []

        def write(self, text):
            if self.fail_next:
                self.fail_next = False
                raise OSError(28, "No space left on device")
            self.chunks.append(text)

        def flush(self):
            pass

    target = FlakyFile()
    stream = server_mod.TimestampedLogStream(target)
    stream.write("사라질 조각")  # 실패
    stream.write("다음 줄\n")
    stream.write("그다음 줄\n")

    lines = "".join(target.chunks).splitlines()
    assert lines == [line for line in lines if _STAMP.match(line)]
    assert len(lines) == 2
