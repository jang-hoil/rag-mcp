"""PDF 파일명에 맞춘 문서 이름 이관: 벡터 재사용과 실패 복구."""
from pathlib import Path

import pytest
from qdrant_client import QdrantClient

from rag_mcp.config import Config
from rag_mcp.indexer import Indexer
from rag_mcp.manifest import ManifestStore
from rag_mcp.models import Chunk
from rag_mcp.vector_store import VectorStore, point_id_for
from scripts.rename_documents_to_pdf_titles import build_plan, migrate, _same_vectors, _create


def seed(tmp_path, fake_backend):
    config = Config(data_dir=tmp_path / "data", qdrant_path=tmp_path / "unused")
    pdf_dir = tmp_path / "pdf"
    pdf_dir.mkdir()
    source = tmp_path / "original (9).pdf"
    source.write_bytes(b"identical PDF bytes")
    local = pdf_dir / "Readable manual (11).pdf"
    local.write_bytes(source.read_bytes())
    old = "english_internal_id"
    parsed = config.parsed_doc_dir(old)
    parsed.mkdir(parents=True)
    (parsed / "original (9)_images").mkdir()
    (parsed / "original (9)_images" / "image.png").write_bytes(b"image")
    (parsed / "original (9).json").write_text(
        '{"file name":"original (9).pdf","title":"intrinsic title","kids":[],"image":"original (9)_images/image.png"}', encoding="utf-8",
    )
    (parsed / "original (9).md").write_text("![image](original (9)_images/image.png)", encoding="utf-8")
    (parsed / "pages").mkdir()
    (parsed / "pages" / "p1.png").write_bytes(b"page")
    client = QdrantClient(":memory:")
    store = VectorStore(config, "kure", old, client=client)
    indexer = Indexer(config, "kure", old, backend=fake_backend, store=store)
    indexer.index_chunks(old, [Chunk(
        chunk_id=f"{old}::c0", document_id=old, text="unchanged searchable text", page=1,
        page_image=str(parsed / "pages" / "p1.png"), doc_name="wrong title",
        source_path=str(source), meta={"preserve": "metadata"},
    )], doc_name="wrong title", source_path=str(source))
    return config, client, pdf_dir, old, local


def test_plan_prefers_identical_pdf_in_project_and_does_not_write(tmp_path, fake_backend):
    config, client, pdf_dir, old, local = seed(tmp_path, fake_backend)
    try:
        before = ManifestStore(config).read(old)
        plan = build_plan(config, client, pdf_dir)
        assert len(plan) == 1
        assert plan[0]["old_id"] == old
        assert plan[0]["new_id"] == local.stem
        assert plan[0]["source_path"] == str(local.resolve())
        assert ManifestStore(config).read(old) == before
        assert len(client.get_collections().collections) == 1
    finally:
        client.close()


def test_migration_reuses_vectors_and_renames_all_links(tmp_path, fake_backend):
    config, client, pdf_dir, old, local = seed(tmp_path, fake_backend)
    try:
        old_collection = config.collection_name("kure", old)
        old_point = client.retrieve(old_collection, [point_id_for(f"{old}::c0")], with_vectors=True)[0]
        result = migrate(config, client, pdf_dir, tmp_path / "backup", apply=True)
        new = local.stem
        collection = config.collection_name("kure", new)
        assert result[0]["new_id"] == new
        assert not client.collection_exists(old_collection)
        point = client.retrieve(collection, [point_id_for(f"{new}::c0")], with_vectors=True)[0]
        assert point.vector["sparse"] == old_point.vector["sparse"]
        # Qdrant COSINE renormalizes float32 on upsert: require only rounding-size drift.
        assert point.vector["dense"] == pytest.approx(old_point.vector["dense"], abs=1e-7, rel=0)
        assert point.payload["text"] == old_point.payload["text"]
        assert point.payload["meta"] == {"preserve": "metadata"}
        assert point.payload["document_id"] == point.payload["doc_name"] == new
        assert point.payload["source_path"] == str(local.resolve())
        assert Path(point.payload["page_image"]).exists()
        parsed = config.parsed_doc_dir(new)
        assert not config.parsed_doc_dir(old).exists()
        assert (parsed / f"{new}.json").exists()
        assert (parsed / f"{new}.md").exists()
        assert (parsed / f"{new}_images" / "image.png").exists()
        import json
        parsed_tree = json.loads((parsed / f"{new}.json").read_text(encoding="utf-8"))
        assert parsed_tree["file name"] == local.name
        assert parsed_tree["title"] == "intrinsic title"
        assert f"{new}_images" in (parsed / f"{new}.md").read_text(encoding="utf-8")
        chunks = Indexer(config, document_id=new, store=object()).load_chunks(new)
        assert chunks[0].payload() == point.payload
        assert ManifestStore(config).read(old) is None
        assert ManifestStore(config).read(new).doc_name == new
        assert (tmp_path / "backup" / "plan.json").exists()
        # A second invocation has nothing to rename and creates no duplicate collections.
        assert build_plan(config, client, pdf_dir) == []
    finally:
        client.close()


def test_inactive_manifest_is_not_restored_to_vector_db(tmp_path):
    config = Config(data_dir=tmp_path / "data", qdrant_path=tmp_path / "unused")
    pdf_dir = tmp_path / "pdf"
    pdf_dir.mkdir()
    old = "old-id"
    parsed = config.parsed_doc_dir(old)
    parsed.mkdir(parents=True)
    (parsed / "chunks.jsonl").write_text(
        Chunk(chunk_id=f"{old}::c0", document_id=old, text="archive").model_dump_json() + "\n",
        encoding="utf-8",
    )
    ManifestStore(config).update(old, source_path=str(tmp_path / "missing title.pdf"), num_chunks=1)
    client = QdrantClient(":memory:")
    try:
        migrate(config, client, pdf_dir, tmp_path / "backup", apply=True)
        assert not client.get_collections().collections
        assert ManifestStore(config).read("missing title").num_chunks == 1
    finally:
        client.close()


def test_existing_target_is_rejected_before_mutation(tmp_path, fake_backend):
    config, client, pdf_dir, old, local = seed(tmp_path, fake_backend)
    config.parsed_doc_dir(local.stem).mkdir()
    try:
        with pytest.raises(ValueError, match="target"):
            migrate(config, client, pdf_dir, tmp_path / "backup", apply=True)
        assert config.parsed_doc_dir(old).exists()
        assert client.collection_exists(config.collection_name("kure", old))
    finally:
        client.close()


def test_failed_copy_restores_original_files_and_collections(tmp_path, fake_backend, monkeypatch):
    config, client, pdf_dir, old, local = seed(tmp_path, fake_backend)
    original = client.upsert

    def fail_new(collection_name, *args, **kwargs):
        if collection_name == config.collection_name("kure", local.stem):
            raise RuntimeError("injected copy failure")
        return original(collection_name, *args, **kwargs)

    monkeypatch.setattr(client, "upsert", fail_new)
    try:
        with pytest.raises(RuntimeError, match="injected copy failure"):
            migrate(config, client, pdf_dir, tmp_path / "backup", apply=True)
        assert config.parsed_doc_dir(old).exists()
        assert not config.parsed_doc_dir(local.stem).exists()
        assert ManifestStore(config).read(old).doc_name == "wrong title"
        assert client.collection_exists(config.collection_name("kure", old))
        assert not client.collection_exists(config.collection_name("kure", local.stem))
    finally:
        client.close()


def test_image_resource_path_uses_renamed_resource_folder(tmp_path, fake_backend):
    config, client, pdf_dir, old, local = seed(tmp_path, fake_backend)
    store = VectorStore(config, "kure", old, client=client)
    chunk = Indexer(config, document_id=old, store=store).load_chunks(old)[0]
    chunk.page_image = str(config.parsed_doc_dir(old) / "original (9)_images" / "image.png")
    Indexer(config, document_id=old, backend=fake_backend, store=store).index_chunks(
        old, [chunk], doc_name="wrong title", source_path=str(tmp_path / "original (9).pdf"),
    )
    try:
        migrate(config, client, pdf_dir, tmp_path / "backup", apply=True)
        point = client.retrieve(config.collection_name("kure", local.stem), [point_id_for(local.stem + "::c0")])[0]
        assert Path(point.payload["page_image"]).is_file()
        assert Path(point.payload["page_image"]).parent.name == local.stem + "_images"
    finally:
        client.close()


def test_disk_rollback_runs_even_if_vector_restore_fails(tmp_path, fake_backend, monkeypatch):
    config, client, pdf_dir, old, local = seed(tmp_path, fake_backend)
    same_name = local.with_name(old + ".pdf")
    local.rename(same_name)
    chunks_path = config.parsed_doc_dir(old) / "chunks.jsonl"
    before_chunks = chunks_path.read_bytes()
    before_manifest = config.manifest_path(old).read_bytes()
    original_upsert, calls = client.upsert, []

    def fail_restore(*args, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            raise RuntimeError("vector restore offline")
        return original_upsert(*args, **kwargs)

    def fail_manifest(*args, **kwargs):
        raise RuntimeError("manifest commit failed")

    monkeypatch.setattr(client, "upsert", fail_restore)
    monkeypatch.setattr(ManifestStore, "write", fail_manifest)
    try:
        with pytest.raises(RuntimeError):
            migrate(config, client, pdf_dir, tmp_path / "backup", apply=True)
        assert chunks_path.read_bytes() == before_chunks
        assert config.manifest_path(old).read_bytes() == before_manifest
    finally:
        client.close()


def test_vector_verification_accepts_rounding_and_rejects_content_change():
    before = {"dense": [0.5, 0.5], "sparse": {"indices": [1], "values": [2.0]}}
    rounding = {"dense": [0.5 + 8e-9, 0.5], "sparse": before["sparse"]}
    wrong_dense = {"dense": [0.501, 0.5], "sparse": before["sparse"]}
    wrong_sparse = {"dense": before["dense"], "sparse": {"indices": [2], "values": [2.0]}}
    assert _same_vectors(before, rounding)
    assert not _same_vectors(before, wrong_dense)
    assert not _same_vectors(before, wrong_sparse)


def test_late_old_collection_delete_failure_restores_committed_files(tmp_path, fake_backend, monkeypatch):
    config, client, pdf_dir, old, local = seed(tmp_path, fake_backend)
    original_delete = client.delete_collection
    old_collection = config.collection_name("kure", old)
    before = config.manifest_path(old).read_bytes()

    def delete_then_fail(collection_name, *args, **kwargs):
        result = original_delete(collection_name, *args, **kwargs)
        if collection_name == old_collection:
            raise RuntimeError("late delete failure")
        return result

    monkeypatch.setattr(client, "delete_collection", delete_then_fail)
    try:
        with pytest.raises(RuntimeError, match="late delete failure"):
            migrate(config, client, pdf_dir, tmp_path / "backup", apply=True)
        assert config.parsed_doc_dir(old).is_dir()
        assert not config.parsed_doc_dir(local.stem).exists()
        assert config.manifest_path(old).read_bytes() == before
        assert not config.manifest_path(local.stem).exists()
        assert client.collection_exists(old_collection)
        assert client.count(old_collection).count == 1
        assert not client.collection_exists(config.collection_name("kure", local.stem))
    finally:
        client.close()


def test_copied_collection_settings_validate_for_http_api(tmp_path, fake_backend):
    from qdrant_client import models
    config, client, pdf_dir, old, local = seed(tmp_path, fake_backend)
    info = client.get_collection(config.collection_name("kure", old))

    class HttpSchemaClient:
        def create_collection(self, **kwargs):
            models.CreateCollection(
                vectors=kwargs["vectors_config"], sparse_vectors=kwargs["sparse_vectors_config"],
                hnsw_config=kwargs["hnsw_config"], optimizers_config=kwargs["optimizers_config"],
                on_disk_payload=kwargs["on_disk_payload"],
            )

        def create_payload_index(self, *args, **kwargs):
            pass

    try:
        _create(HttpSchemaClient(), "new_collection", info)
    finally:
        client.close()
