"""doctor — OCR runtime diagnostics."""

from rag_mcp.config import Config
from rag_mcp.doctor import CommandResult, check_ocr_environment


def test_doctor_reports_missing_tesseract():
    cfg = Config(ocr_mode="auto", ocr_lang="kor+eng")

    report = check_ocr_environment(cfg, find_executable=lambda name: None)

    assert report["ocr_mode"] == "auto"
    assert report["ok"] is False
    assert report["tesseract"]["found"] is False
    assert any("tesseract" in issue.lower() for issue in report["issues"])


def test_doctor_reports_required_languages():
    cfg = Config(ocr_mode="auto", ocr_lang="kor+eng")

    def fake_run(command: list[str]) -> CommandResult:
        assert command[-1] == "--list-langs"
        return CommandResult(returncode=0, stdout="List of available languages (2):\neng\nkor\n", stderr="")

    report = check_ocr_environment(
        cfg,
        find_executable=lambda name: "C:/Tesseract/tesseract.exe",
        run_command=fake_run,
        module_available=lambda name: True,
    )

    assert report["ok"] is True
    assert report["tesseract"]["path"].endswith("tesseract.exe")
    assert report["languages"]["required"] == ["kor", "eng"]
    assert report["languages"]["missing"] == []