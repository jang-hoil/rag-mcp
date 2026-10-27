"""독립 MCP 인스턴스의 공용 쓰기 예약과 임시 파일 충돌 회귀 테스트."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from rag_mcp.config import Config
from rag_mcp.indexer import Indexer
from rag_mcp.manifest import ManifestStore
from rag_mcp.models import Chunk, Manifest
from rag_mcp.service import RagService


def config_for(root):
    return Config(data_dir=root, qdrant_path=root / "qdrant")


def test_independent_services_share_mutation_reservation(tmp_path):
    owner = RagService(config_for(tmp_path))
    other = RagService(config_for(tmp_path))
    token, _ = owner._reserve_mutation("ingest_pdf")
    assert token is not None
    try:
        other_token, busy = other._reserve_mutation("reindex_document")
        try:
            assert other_token is None
            assert busy["ok"] is False
            assert busy["status"] == "busy"
            assert "진행 중" in busy["error"]
        finally:
            if other_token:
                other._release_mutation(other_token)
    finally:
        owner._release_mutation(token)
    next_token, busy = other._reserve_mutation("ingest_pdf")
    try:
        assert next_token is not None and busy is None
    finally:
        if next_token:
            other._release_mutation(next_token)


def test_independent_data_directories_do_not_block_each_other(tmp_path):
    services = [RagService(config_for(tmp_path / name)) for name in ("a", "b")]
    reservations = []
    try:
        for service in services:
            token, busy = service._reserve_mutation("ingest_pdf")
            reservations.append((service, token))
            assert token is not None and busy is None
    finally:
        for service, token in reservations:
            if token:
                service._release_mutation(token)


CHILD_RESERVATION = """
import json, sys
from pathlib import Path
from rag_mcp.config import Config
from rag_mcp.service import RagService
root = Path(sys.argv[1])
service = RagService(Config(data_dir=root, qdrant_path=root / 'qdrant'))
token, busy = service._reserve_mutation('ingest_pdf')
print(json.dumps({'acquired': token is not None, 'busy': busy}), flush=True)
if len(sys.argv) > 2:
    sys.stdin.readline()
if token:
    service._release_mutation(token)
"""


def child_env():
    return {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}


def test_actual_process_cannot_reserve_while_owner_is_active(tmp_path):
    owner = RagService(config_for(tmp_path))
    token, _ = owner._reserve_mutation("ingest_pdf")
    assert token is not None
    try:
        child = subprocess.run(
            [sys.executable, "-c", CHILD_RESERVATION, str(tmp_path)],
            capture_output=True, text=True, env=child_env(), timeout=20,
        )
        assert child.returncode == 0, child.stderr
        result = json.loads(child.stdout)
        assert result["acquired"] is False
        assert result["busy"]["status"] == "busy"
    finally:
        owner._release_mutation(token)


def test_forced_process_exit_releases_reservation(tmp_path):
    child = subprocess.Popen(
        [sys.executable, "-u", "-c", CHILD_RESERVATION, str(tmp_path), "hold"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=child_env(),
    )
    try:
        # Bound the readiness wait as well as teardown if the child fails to start.
        lines = []
        reader = threading.Thread(target=lambda: lines.append(child.stdout.readline()), daemon=True)
        reader.start()
        reader.join(20)
        assert not reader.is_alive(), "child did not report its reservation"
        assert json.loads(lines[0])["acquired"] is True
        owner = RagService(config_for(tmp_path))
        token, busy = owner._reserve_mutation("ingest_pdf")
        try:
            assert token is None and busy["status"] == "busy"
        finally:
            if token:
                owner._release_mutation(token)
    finally:
        child.kill()
        child.communicate(timeout=10)
    after = RagService(config_for(tmp_path))
    token, busy = after._reserve_mutation("ingest_pdf")
    try:
        assert token is not None and busy is None
    finally:
        if token:
            after._release_mutation(token)


@pytest.mark.parametrize("fail", [False, True])
def test_background_ingest_holds_shared_lock_until_finish(tmp_path, monkeypatch, fail):
    owner = RagService(config_for(tmp_path))
    other = RagService(config_for(tmp_path))
    pdf = tmp_path / "guide.pdf"
    pdf.write_bytes(b"dummy")
    started, release = threading.Event(), threading.Event()

    def ingest(path, **kwargs):
        started.set()
        assert release.wait(5)
        if fail:
            raise RuntimeError("expected failure")
        return {"ok": True, "document_id": "guide", "num_chunks": 1}

    monkeypatch.setattr(owner, "_ingest_pdf_unlocked", ingest)
    monkeypatch.setattr(other, "_ingest_pdf_unlocked", lambda *a, **kw: pytest.fail("must not parse"))
    submitted = owner.submit_ingest(str(pdf), document_id="guide")
    assert started.wait(3)
    try:
        result = other.ingest_pdf(str(pdf), document_id="guide")
        assert result["status"] == "busy"
    finally:
        release.set()
    deadline = time.monotonic() + 5
    while owner._active_mutation is not None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert owner.ingest_status(submitted["job_id"])["status"] == ("error" if fail else "done")
    token, busy = other._reserve_mutation("ingest_pdf")
    try:
        assert token is not None and busy is None
    finally:
        if token:
            other._release_mutation(token)


def test_sync_exception_releases_shared_reservation(tmp_path, monkeypatch):
    owner = RagService(config_for(tmp_path))
    pdf = tmp_path / "guide.pdf"
    pdf.write_bytes(b"dummy")

    def fail(*args, **kwargs):
        raise RuntimeError("expected failure")

    monkeypatch.setattr(owner, "_ingest_pdf_unlocked", fail)
    with pytest.raises(RuntimeError, match="expected failure"):
        owner.ingest_pdf(str(pdf))
    other = RagService(config_for(tmp_path))
    token, busy = other._reserve_mutation("ingest_pdf")
    try:
        assert token is not None and busy is None
    finally:
        if token:
            other._release_mutation(token)


def test_staged_chunks_have_independent_paths_and_contents(tmp_path):
    config = config_for(tmp_path)
    # This test exercises staging only, so no DB/embedding backend is needed.
    first = Indexer(config, document_id="guide", store=object())
    second = Indexer(config, document_id="guide", store=object())
    a = first._stage_chunks("guide", [Chunk(chunk_id="a", document_id="guide", text="first")])
    b = second._stage_chunks("guide", [Chunk(chunk_id="b", document_id="guide", text="second")])
    try:
        assert a != b
        assert Chunk.model_validate_json(a.read_text(encoding="utf-8").strip()).text == "first"
        assert Chunk.model_validate_json(b.read_text(encoding="utf-8").strip()).text == "second"
    finally:
        a.unlink(missing_ok=True)
        b.unlink(missing_ok=True)


def test_failed_final_chunk_rename_cleans_only_own_stage(tmp_path, fake_backend, monkeypatch):
    from types import SimpleNamespace
    from rag_mcp import indexer as module

    config = config_for(tmp_path)
    indexer = Indexer(
        config, document_id="guide", backend=fake_backend,
        store=SimpleNamespace(replace_document=lambda *args, **kwargs: 1),
    )
    original = Chunk(chunk_id="a", document_id="guide", text="original")
    target = indexer.save_chunks("guide", [original])
    unrelated = target.parent / ".other-task.stage"
    unrelated.write_text("another task", encoding="utf-8")
    replace = module.os.replace

    def fail_stage_commit(source, destination):
        if Path(source).suffix == ".stage":
            raise OSError("commit rename failed")
        return replace(source, destination)

    monkeypatch.setattr(module.os, "replace", fail_stage_commit)
    with pytest.raises(OSError, match="commit rename failed"):
        indexer.index_chunks("guide", [Chunk(chunk_id="a", document_id="guide", text="updated")])
    assert list(target.parent.glob("*.stage")) == [unrelated]
    assert unrelated.read_text(encoding="utf-8") == "another task"
    assert indexer.load_chunks("guide")[0].text == "original"


def test_thread_start_failure_releases_shared_reservation(tmp_path, monkeypatch):
    owner = RagService(config_for(tmp_path))
    pdf = tmp_path / "guide.pdf"
    pdf.write_bytes(b"dummy")

    def fail_start(self):
        raise RuntimeError("cannot start worker")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    with pytest.raises(RuntimeError, match="cannot start worker"):
        owner.submit_ingest(str(pdf))
    other = RagService(config_for(tmp_path))
    token, busy = other._reserve_mutation("ingest_pdf")
    try:
        assert token is not None and busy is None
    finally:
        if token:
            other._release_mutation(token)


def test_unusable_lock_directory_is_an_error_not_busy(tmp_path):
    (tmp_path / ".locks").write_bytes(b"not a directory")
    service = RagService(config_for(tmp_path))
    with pytest.raises(OSError):
        service._reserve_mutation("ingest_pdf")
    assert service._active_mutation is None


@pytest.mark.parametrize("kind", ["manifest", "chunks"])
def test_atomic_writes_use_own_temp_file(tmp_path, monkeypatch, kind):
    from rag_mcp import manifest as module
    store = ManifestStore(config_for(tmp_path))
    sources = []
    replace = module.os.replace

    def record(source, target):
        sources.append(source)
        return replace(source, target)

    monkeypatch.setattr(module.os, "replace", record)
    for _ in range(2):
        if kind == "manifest":
            store.write(Manifest(document_id="guide"))
        else:
            Indexer(config_for(tmp_path), document_id="guide", store=object()).save_chunks(
                "guide", [Chunk(chunk_id="a", document_id="guide", text="content")],
            )
    assert sources[0] != sources[1]
    assert all(not source.exists() for source in sources)


def test_overlapping_ingests_preserve_db_chunks_and_manifest(tmp_path, fake_backend, monkeypatch):
    from qdrant_client import QdrantClient
    from rag_mcp import pipeline

    config = Config(
        data_dir=tmp_path, qdrant_path=tmp_path / "qdrant",
        qdrant_mode="server", qdrant_url="http://127.0.0.1:6333",
    )
    owner = RagService(config, backend=fake_backend)
    other = RagService(config, backend=fake_backend)
    # The real VectorStore runs against an isolated in-memory DB, never the live server.
    client = QdrantClient(":memory:")
    owner._qdrant_client = other._qdrant_client = client
    owner.ingest_chunks("guide", [Chunk(chunk_id="guide::c0", document_id="guide", text="initial")])
    started, release = threading.Event(), threading.Event()
    first, second = tmp_path / "first.pdf", tmp_path / "second.pdf"
    first.write_bytes(b"dummy")
    second.write_bytes(b"dummy")
    parsed_paths = []

    def parse(path, document_id, *args, **kwargs):
        parsed_paths.append(Path(path).stem)
        if Path(path) == first:
            started.set()
            assert release.wait(5)
        return [Chunk(chunk_id="guide::c0", document_id=document_id, text=Path(path).stem)], {}

    monkeypatch.setattr(pipeline, "parse_and_chunk", parse)
    submitted = owner.submit_ingest(str(first), document_id="guide")
    try:
        assert started.wait(3)
        assert other.ingest_pdf(str(second), document_id="guide")["status"] == "busy"
        assert parsed_paths == ["first"]
        assert other.search_documents("initial", document_id="guide", search_mode="sparse")[0]["text"] == "initial"
    finally:
        release.set()
    try:
        deadline = time.monotonic() + 5
        while owner._active_mutation is not None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert owner.ingest_status(submitted["job_id"])["status"] == "done"
        assert other.ingest_pdf(str(second), document_id="guide")["ok"] is True
        assert owner.get_chunk("guide::c0")["text"] == "second"
        assert other._indexer("kure", "guide").load_chunks("guide")[0].text == "second"
        assert owner.manifests.read("guide").status == "done"
        assert owner.manifests.read("guide").source_path == str(second)
        assert not list(config.parsed_doc_dir("guide").glob("*.stage"))
    finally:
        client.close()
