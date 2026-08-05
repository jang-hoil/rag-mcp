"""원본 PDF 경로(source_path) 복구 — WSL 경로 자동 변환 + 명시적 재지정.

배경(2026-08-03): 매니페스트 6건 중 4건의 source_path가 옛 WSL 경로(`/mnt/d/...`)로 남아
있었다. 검색·임베딩은 멀쩡하지만 **캐시되지 않은 페이지 이미지를 렌더할 수 없다**
(get_page_image가 원본 PDF를 열어야 하므로). 실측 결과 두 부류였다:
  - 파일은 제자리, 표기만 WSL식 → 경로 변환으로 자동 복구 가능(2건)
  - 파일이 다른 폴더로 이동 → 새 경로를 사람이 지정해야 함(2건)

재색인은 필요 없다. 청크·임베딩은 그대로 두고 경로만 고치면 된다.
"""
import json
import os
from pathlib import Path

import pytest

from rag_mcp.config import Config
from rag_mcp.manifest import resolve_source_path
from rag_mcp.models import Chunk
from rag_mcp.service import RagService

WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="드라이브 문자 경로는 Windows 전용")


@pytest.fixture
def svc(tmp_path, fake_backend):
    os.environ["RAG_QDRANT_PATH"] = str(tmp_path / "qdrant")
    os.environ["RAG_DATA_DIR"] = str(tmp_path)
    try:
        yield RagService(Config(), backend=fake_backend)
    finally:
        os.environ.pop("RAG_QDRANT_PATH", None)
        os.environ.pop("RAG_DATA_DIR", None)


def _to_wsl(path: Path) -> str:
    """C:\\a\\b -> /mnt/c/a/b (옛 WSL 세션이 남긴 형태를 재현)."""
    drive = path.drive.rstrip(":").lower()
    rest = str(path)[len(path.drive):].replace("\\", "/").lstrip("/")
    return f"/mnt/{drive}/{rest}"


def _pdf(path: Path, pages: int = 3) -> Path:
    import fitz

    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 100), f"page {i + 1}", fontsize=14)
    doc.save(str(path))
    doc.close()
    return path


def _seed(svc, document_id: str, source_path: str, pages_recorded: int | None = None):
    """청크 1개짜리 문서를 색인하고 source_path를 지정한 값으로 남긴다."""
    chunks = [Chunk(chunk_id=f"{document_id}::c0", document_id=document_id,
                    text="일반수용비 집행 기준", fiscal_year=2026)]
    svc.ingest_chunks(document_id, chunks, doc_name=document_id,
                      fiscal_year=2026, source_path=source_path)
    if pages_recorded is not None:
        parsed = svc.config.parsed_doc_dir(document_id)
        parsed.mkdir(parents=True, exist_ok=True)
        (parsed / f"{document_id}.json").write_text(
            json.dumps({"file name": f"{document_id}.pdf", "number of pages": pages_recorded,
                        "kids": []}, ensure_ascii=False),
            encoding="utf-8",
        )


# --- resolve_source_path: 읽을 때 WSL 경로 되살리기 ---


def test_resolve_returns_none_without_path():
    assert resolve_source_path(None) is None
    assert resolve_source_path("") is None


def test_resolve_keeps_existing_literal_path(tmp_path):
    real = _pdf(tmp_path / "a.pdf")
    assert resolve_source_path(str(real)) == real


@WINDOWS_ONLY
def test_resolve_translates_wsl_path_when_file_is_there(tmp_path):
    real = _pdf(tmp_path / "b.pdf")
    recorded = _to_wsl(real)
    assert recorded.startswith("/mnt/")

    assert resolve_source_path(recorded) == real


@WINDOWS_ONLY
def test_resolve_keeps_original_when_translation_also_missing(tmp_path):
    # 변환해도 파일이 없으면 기록된 경로를 그대로 돌려준다(구분자만 OS 정규화됨)
    recorded = _to_wsl(tmp_path / "사라진.pdf")
    assert resolve_source_path(recorded) == Path(recorded)


def test_page_image_error_shows_recorded_path(svc, tmp_path):
    """오류 메시지에는 매니페스트에 **기록된 원문**이 보여야 원인을 알 수 있다."""
    recorded = "/mnt/d/어딘가/사라진.pdf"
    _seed(svc, "broken", recorded)

    with pytest.raises(ValueError) as excinfo:
        svc.get_page_image("broken", 1)

    assert recorded in str(excinfo.value)
    assert "set_source_path" in str(excinfo.value), "복구 방법을 알려줘야 한다"


def test_resolve_leaves_plain_missing_path_alone(tmp_path):
    missing = tmp_path / "없다.pdf"
    assert resolve_source_path(str(missing)) == missing


# --- get_page_image가 실제로 되살아나는지 ---


@WINDOWS_ONLY
def test_get_page_image_renders_through_wsl_path(svc, tmp_path):
    real = _pdf(tmp_path / "guide.pdf", pages=3)
    _seed(svc, "wsl_doc", _to_wsl(real))

    images = svc.get_page_image("wsl_doc", 2)  # 캐시에 없는 페이지

    assert len(images) == 1
    assert images[0]["page"] == 2
    assert images[0]["cache_hit"] is False
    assert images[0]["image"]


# --- set_source_path: 이동한 파일을 사람이 다시 물려주기 ---


def test_set_source_path_updates_manifest(svc, tmp_path):
    old = _pdf(tmp_path / "old.pdf", pages=3)
    _seed(svc, "moved", str(old), pages_recorded=3)
    (tmp_path / "sub").mkdir(parents=True, exist_ok=True)
    new = _pdf(tmp_path / "sub" / "new.pdf", pages=3)

    res = svc.set_source_path("moved", str(new))

    assert res["ok"] is True
    assert res["previous_source_path"] == str(old)
    assert Path(res["source_path"]) == new.resolve()
    assert Path(svc.manifests.read("moved").source_path) == new.resolve()


def test_set_source_path_rejects_unknown_document(svc, tmp_path):
    pdf = _pdf(tmp_path / "x.pdf")
    res = svc.set_source_path("없는문서", str(pdf))
    assert res["ok"] is False
    assert "없는문서" in res["error"]


def test_set_source_path_rejects_missing_file(svc, tmp_path):
    old = _pdf(tmp_path / "old.pdf")
    _seed(svc, "doc", str(old))

    res = svc.set_source_path("doc", str(tmp_path / "없는파일.pdf"))

    assert res["ok"] is False
    assert Path(svc.manifests.read("doc").source_path) == old, "실패 시 매니페스트를 건드리면 안 된다"


def test_set_source_path_rejects_page_count_mismatch(svc, tmp_path):
    """다른 PDF를 물리면 잘못된 페이지가 조용히 나온다 → 쪽수로 막는다."""
    old = _pdf(tmp_path / "old.pdf", pages=5)
    _seed(svc, "doc", str(old), pages_recorded=5)
    wrong = _pdf(tmp_path / "wrong.pdf", pages=9)

    res = svc.set_source_path("doc", str(wrong))

    assert res["ok"] is False
    assert "쪽" in res["error"]
    assert res["recorded_pages"] == 5
    assert res["new_pages"] == 9
    assert Path(svc.manifests.read("doc").source_path) == old


def test_set_source_path_force_overrides_mismatch(svc, tmp_path):
    old = _pdf(tmp_path / "old.pdf", pages=5)
    _seed(svc, "doc", str(old), pages_recorded=5)
    wrong = _pdf(tmp_path / "wrong.pdf", pages=9)

    res = svc.set_source_path("doc", str(wrong), force=True)

    assert res["ok"] is True
    assert Path(svc.manifests.read("doc").source_path) == wrong.resolve()
