"""Runtime diagnostics for optional OCR dependencies."""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from .config import Config


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def _run_command(command: list[str]) -> CommandResult:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _lang_tokens(raw: str) -> list[str]:
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    return [line for line in lines if not line.lower().startswith("list of available")]


def check_ocr_environment(
    config: Config,
    *,
    find_executable: Callable[[str], str | None] = shutil.which,
    run_command: Callable[[list[str]], CommandResult] = _run_command,
    module_available: Callable[[str], bool] = _module_available,
) -> dict:
    """Return OCR readiness details without mutating project state."""
    required_langs = [part for part in config.ocr_lang.split("+") if part]
    tesseract_path = find_executable("tesseract")
    pytesseract_found = module_available("pytesseract")
    pillow_found = module_available("PIL")
    issues: list[str] = []
    languages: list[str] = []

    if not pytesseract_found:
        issues.append("pytesseract 미설치: uv sync --extra ocr 필요")
    if not pillow_found:
        issues.append("Pillow 미설치: uv sync --extra ocr 필요")
    if tesseract_path is None:
        issues.append("tesseract 실행파일 없음: winget install --id UB-Mannheim.TesseractOCR 필요")
    else:
        result = run_command([tesseract_path, "--list-langs"])
        if result.returncode == 0:
            languages = _lang_tokens(result.stdout)
        else:
            issues.append(f"tesseract 언어 목록 확인 실패: {result.stderr.strip() or result.stdout.strip()}")

    missing_langs = [lang for lang in required_langs if lang not in languages]
    if tesseract_path is not None and missing_langs:
        issues.append(f"Tesseract 언어 데이터 누락: {', '.join(missing_langs)}")

    ok = config.ocr_mode == "off" or not issues
    return {
        "ok": ok,
        "ocr_mode": config.ocr_mode,
        "python_packages": {
            "pytesseract": pytesseract_found,
            "PIL": pillow_found,
        },
        "tesseract": {
            "found": tesseract_path is not None,
            "path": tesseract_path,
        },
        "languages": {
            "required": required_langs,
            "available": languages,
            "missing": missing_langs,
        },
        "issues": issues,
    }