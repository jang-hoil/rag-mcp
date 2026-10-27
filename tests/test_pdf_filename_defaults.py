"""신규 PDF의 기본 문서 ID·표시명·파싱 폴더·벡터 payload 계약."""
import json
from pathlib import Path
import time

import pytest

from rag_mcp.config import Config
from rag_mcp.pdf_parser import ParsedDoc
from rag_mcp.pipeline import parse_and_chunk
from rag_mcp.service import RagService


@pytest.fixture
def filename_pipeline(tmp_path, monkeypatch):
    from rag_mcp import pipeline

    config = Config(data_dir=tmp_path / "data", qdrant_path=tmp_path / "qdrant", ocr_mode="off")
    calls = []

    def fake_parse(path, document_id, settings, *, force=False):
        folder = settings.parsed_doc_dir(document_id)
        folder.mkdir(parents=True, exist_ok=True)
        calls.append((Path(path), document_id))
        tree = {
            "title": "2027년 교육 교재 형태", "number of pages": 1,
            "kids": [{"type": "paragraph", "page number": 1, "content": "일반수용비 집행 기준"}],
        }
        source = folder / (Path(path).stem + ".json")
        source.write_text(json.dumps(tree, ensure_ascii=False), encoding="utf-8")
        return ParsedDoc(document_id, tree, source, None, folder)

    monkeypatch.setattr(pipeline, "parse_pdf", fake_parse)
    return config, calls


@pytest.mark.parametrize("filename", ["징계업무편람 (3).pdf", "manual.v2 (1).PDF"])
def test_pipeline_prefers_pdf_filename_over_internal_title(filename_pipeline, tmp_path, filename):
    config, _ = filename_pipeline
    path = tmp_path / filename
    chunks, meta = parse_and_chunk(path, path.stem, config)

    assert meta["doc_name"] == path.stem
    assert chunks[0].doc_name == path.stem
    # Naming does not remove the existing year extraction from PDF title/body.
    assert meta["fiscal_year"] == chunks[0].fiscal_year == 2027


def test_pipeline_keeps_explicit_display_name(filename_pipeline, tmp_path):
    config, _ = filename_pipeline
    chunks, meta = parse_and_chunk(tmp_path / "source.pdf", "manual-id", config, doc_name="직접 지정 이름")
    assert meta["doc_name"] == chunks[0].doc_name == "직접 지정 이름"


@pytest.mark.parametrize("background", [False, True])
@pytest.mark.parametrize("custom_id,custom_name", [
    (None, None), ("explicit-id", None), (None, "직접 지정 이름"), ("explicit-id", "직접 지정 이름"),
])
def test_ingest_names_are_consistent_in_files_db_and_results(
    filename_pipeline, fake_backend, tmp_path, background, custom_id, custom_name,
):
    config, calls = filename_pipeline
    path = tmp_path / "업무편람.v2 (1).pdf"
    path.write_bytes(b"dummy PDF - parser is replaced by a fixture")
    service = RagService(config, backend=fake_backend)
    expected_id = custom_id or path.stem
    expected_name = custom_name or path.stem
    try:
        if background:
            result = service.submit_ingest(str(path), document_id=custom_id, doc_name=custom_name)
            assert result["document_id"] == expected_id
            deadline = time.monotonic() + 10
            status = service.ingest_status(result["job_id"])
            while status["status"] == "running" and time.monotonic() < deadline:
                time.sleep(0.01)
                status = service.ingest_status(result["job_id"])
            assert status["status"] == "done", status
        else:
            result = service.ingest_pdf(str(path), document_id=custom_id, doc_name=custom_name)
            assert result["ok"] and result["document_id"] == expected_id
        assert calls == [(path, expected_id)]
        assert config.parsed_doc_dir(expected_id).is_dir()
        manifest = service.manifests.read(expected_id)
        assert manifest.doc_name == expected_name
        chunks = service._indexer("kure", expected_id).load_chunks(expected_id)
        assert chunks[0].doc_name == expected_name
        hit = service.search_documents("일반수용비", document_id=expected_id, search_mode="sparse")[0]
        assert hit["source"]["document_id"] == expected_id
        assert hit["source"]["doc_name"] == expected_name
        listed = service.list_documents()[0]
        assert listed["doc_name"] == expected_name
        assert listed["collection"] == config.collection_name("kure", expected_id)
    finally:
        if service._qdrant_client is not None:
            service._qdrant_client.close()
