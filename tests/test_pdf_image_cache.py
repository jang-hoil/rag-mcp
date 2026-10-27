"""PDF 갱신·재파싱 시 이전 버전의 페이지 PNG를 재사용하지 않는다."""
import json
import os
from pathlib import Path
import threading

import fitz
import pytest

from rag_mcp.config import Config
from rag_mcp.page_render import invalidate_page_cache, render_pages
from rag_mcp.pdf_parser import parse_pdf
from rag_mcp.pipeline import parse_and_chunk
from rag_mcp.service import RagService


def _pdf(path, label, *, pages=2):
    with fitz.open() as document:
        for index in range(pages):
            page = document.new_page(width=180, height=200)
            color = (1, 0.8, 0.8) if label == "OLD" else (0.8, 0.8, 1)
            page.draw_rect(page.rect, color=color, fill=color)
            page.insert_text((12, 40), f"{label} page {index + 1}")
        document.save(str(path))
    return path


@pytest.fixture
def cache_pipeline(tmp_path, monkeypatch):
    config = Config(data_dir=tmp_path / "data", qdrant_path=tmp_path / "qdrant",
                    ocr_mode="off", render_dpi=72, page_image_dpi=72)
    source = _pdf(tmp_path / "manual.pdf", "OLD")
    state = {"tables": False}

    def convert(**kwargs):
        blocks = []
        with fitz.open(kwargs["input_path"]) as document:
            for index, page in enumerate(document):
                text = page.get_text().strip()
                if state["tables"]:
                    blocks.append({
                        "type": "table", "page number": index + 1,
                        "number of rows": 1, "number of columns": 1,
                        "rows": [{"cells": [{"row number": 1, "column number": 1,
                                             "kids": [{"content": text + " 10 20"}]}]}],
                    })
                else:
                    blocks.append({"type": "paragraph", "page number": index + 1, "content": text})
            tree = {"number of pages": len(document), "kids": blocks}
        folder = Path(kwargs["output_dir"])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "manual.json").write_text(json.dumps(tree), encoding="utf-8")

    monkeypatch.setattr("opendataloader_pdf.convert", convert)
    return config, source, state


@pytest.mark.parametrize("tables", [False, True])
def test_updated_pdf_ingest_and_reparse_return_updated_page_image(cache_pipeline, fake_backend, tables):
    config, source, state = cache_pipeline
    state["tables"] = tables
    service = RagService(config, backend=fake_backend)
    try:
        assert service.ingest_pdf(str(source))["ok"]
        original = service.get_page_image("manual", 1)[0]["image"]
        _pdf(source, "UPDATED")

        assert service.ingest_pdf(str(source))["ok"]
        updated = service.get_page_image("manual", 1)[0]

        assert updated["image"] != original
        assert "UPDATED" in service.get_chunk("manual::c0")["text"]
        _pdf(source, "LATEST VERSION")
        assert service.reindex_document("manual", reparse=True)["ok"]
        assert service.get_page_image("manual", 1)[0]["image"] != updated["image"]
    finally:
        if service._qdrant_client is not None:
            service._qdrant_client.close()


def test_reparse_invalidates_unrequested_and_removed_pages_only_for_this_document(cache_pipeline):
    config, source, _ = cache_pipeline
    parse_and_chunk(source, "manual", config, force=True)
    cached = render_pages(source, "manual", config, {1, 2})
    other = Path(render_pages(source, "other", config, {1})[1])
    other_bytes = other.read_bytes()
    notes = config.pages_dir("manual") / "notes.txt"
    notes.write_text("preserve", encoding="utf-8")
    _pdf(source, "UPDATED", pages=1)

    parse_and_chunk(source, "manual", config, force=True)

    assert not any(Path(path).exists() for path in cached.values())
    assert other.read_bytes() == other_bytes
    assert notes.read_text(encoding="utf-8") == "preserve"
    assert render_pages(source, "manual", config, {2}) == {}


@pytest.mark.parametrize("failure", ["converter", "missing_json", "invalid_json"])
def test_failed_reparse_preserves_previous_images(cache_pipeline, monkeypatch, failure):
    config, source, _ = cache_pipeline
    parsed = parse_pdf(source, "manual", config, force=True)
    cached = Path(render_pages(source, "manual", config, {1})[1])
    previous = cached.read_bytes()
    _pdf(source, "UPDATED")

    def fail_convert(**kwargs):
        if failure == "converter":
            raise RuntimeError("converter failed")
        if failure == "invalid_json":
            parsed.json_path.write_text("{invalid", encoding="utf-8")

    monkeypatch.setattr("opendataloader_pdf.convert", fail_convert)
    with pytest.raises((RuntimeError, json.JSONDecodeError)):
        parse_pdf(source, "manual", config, force=True)

    assert cached.read_bytes() == previous


def test_loading_existing_parsed_json_preserves_cached_images(cache_pipeline, monkeypatch):
    config, source, _ = cache_pipeline
    parse_pdf(source, "manual", config, force=True)
    cached = Path(render_pages(source, "manual", config, {1})[1])
    previous = cached.read_bytes()
    previous_stat = cached.stat()

    def should_not_convert(**kwargs):
        raise AssertionError("cached parse must not run converter")

    monkeypatch.setattr("opendataloader_pdf.convert", should_not_convert)
    parse_and_chunk(source, "manual", config, force=False)

    assert cached.read_bytes() == previous
    assert cached.stat().st_mtime_ns == previous_stat.st_mtime_ns


def test_new_conversion_without_json_invalidates_existing_orphan_cache(cache_pipeline):
    config, source, _ = cache_pipeline
    cached = Path(render_pages(source, "manual", config, {1})[1])
    _pdf(source, "UPDATED")

    parse_pdf(source, "manual", config, force=False)

    assert not cached.exists()


@pytest.mark.parametrize("same_instance", [False, True])
def test_read_during_pdf_replacement_cannot_recreate_image_from_old_manifest(
    cache_pipeline, fake_backend, monkeypatch, same_instance,
):
    config, source, _ = cache_pipeline
    owner = RagService(config, backend=fake_backend)
    reader = owner if same_instance else RagService(config, backend=fake_backend)
    try:
        assert owner.ingest_pdf(str(source), document_id="manual")["ok"]
        original = owner.get_page_image("manual", 1)[0]["image"]
        replacement = _pdf(source.with_name("replacement.pdf"), "UPDATED")
        commit = owner._ingest_chunks_unlocked
        read_errors = []

        def interleaved_read(*args, **kwargs):
            # The new parse has removed the cache, but the manifest still names the old PDF.
            try:
                reader.get_page_image("manual", 1)
            except ValueError as error:
                read_errors.append(str(error))
            return commit(*args, **kwargs)

        monkeypatch.setattr(owner, "_ingest_chunks_unlocked", interleaved_read)
        assert owner.ingest_pdf(str(replacement), document_id="manual")["ok"]

        assert reader.get_page_image("manual", 1)[0]["image"] != original
        assert len(read_errors) == 1 and "다시" in read_errors[0]
    finally:
        if owner._qdrant_client is not None:
            owner._qdrant_client.close()


def test_cached_image_remains_readable_while_another_instance_indexes(cache_pipeline):
    config, source, _ = cache_pipeline
    owner, reader = RagService(config), RagService(config)
    cached = Path(render_pages(source, "manual", config, {1})[1])
    token, _ = owner._reserve_mutation("ingest_pdf")
    assert token is not None
    try:
        result = reader.get_page_image("manual", 1)[0]
        assert result["cache_hit"] is True
        assert result["image"] == cached.read_bytes()
    finally:
        owner._release_mutation(token)


def test_cache_disappearing_before_read_retries_without_raw_file_error(cache_pipeline, monkeypatch):
    from rag_mcp import service as service_module

    config, source, _ = cache_pipeline
    owner, reader = RagService(config), RagService(config)
    render_pages(source, "manual", config, {1})
    token, _ = owner._reserve_mutation("ingest_pdf")
    assert token is not None
    read_image = service_module._page_image_bytes

    def cache_was_invalidated(path, max_width):
        path.unlink(missing_ok=True)
        return read_image(path, max_width)

    monkeypatch.setattr(service_module, "_page_image_bytes", cache_was_invalidated)
    try:
        with pytest.raises(ValueError, match="다시"):
            reader.get_page_image("manual", 1)
    finally:
        owner._release_mutation(token)


@pytest.mark.parametrize("fail_read", [False, True])
def test_image_generation_holds_os_lock_through_read_and_releases_on_error(
    cache_pipeline, monkeypatch, fail_read,
):
    from rag_mcp import service as service_module

    config, source, _ = cache_pipeline
    reader, other = RagService(config), RagService(config)
    reader.manifests.update("manual", source_path=str(source), status="done")
    read_image = service_module._page_image_bytes
    blocked = []

    def read_under_lock(path, max_width):
        token, busy = other._reserve_mutation("ingest_pdf")
        try:
            blocked.append(token is None and busy["status"] == "busy")
        finally:
            if token:
                other._release_mutation(token)
        if fail_read:
            raise ValueError("expected read failure")
        return read_image(path, max_width)

    monkeypatch.setattr(service_module, "_page_image_bytes", read_under_lock)
    if fail_read:
        with pytest.raises(ValueError, match="expected read failure"):
            reader.get_page_image("manual", 1)
    else:
        assert reader.get_page_image("manual", 1)[0]["image"]
    assert blocked == [True]
    token, busy = other._reserve_mutation("ingest_pdf")
    try:
        assert token is not None and busy is None
    finally:
        if token:
            other._release_mutation(token)


@pytest.mark.skipif(os.name != "nt", reason="Windows 파일 공유 위반 재현")
def test_cache_invalidation_retries_short_windows_reader_overlap(cache_pipeline, monkeypatch):
    config, source, _ = cache_pipeline
    cached = Path(render_pages(source, "manual", config, {1})[1])
    opened, release = threading.Event(), threading.Event()
    failures = []

    def read_file():
        with cached.open("rb"):
            opened.set()
            release.wait(5)

    thread = threading.Thread(target=read_file)
    thread.start()
    original_unlink = Path.unlink

    def unlink_during_read(path, *args, **kwargs):
        try:
            return original_unlink(path, *args, **kwargs)
        except PermissionError as error:
            if path == cached:
                failures.append(error.winerror)
                release.set()
                thread.join(2)
            raise

    try:
        assert opened.wait(2)
        monkeypatch.setattr(Path, "unlink", unlink_during_read)
        invalidate_page_cache("manual", config)
        assert failures == [32]
        assert not cached.exists()
    finally:
        release.set()
        thread.join(5)
