"""Qdrant local-path 컬렉션을 HTTP 서버로 복사하는 도구 테스트."""
from types import SimpleNamespace
import uuid

import pytest
from qdrant_client import QdrantClient
from qdrant_client import models

from scripts import migrate_local_to_server as migration
from scripts.migrate_local_to_server import (
    build_parser,
    inspect_collections,
    main,
    migrate_collections,
)


def _record(point_id: str):
    return SimpleNamespace(
        id=point_id,
        vector={
            "dense": [0.1, 0.2],
            "sparse": models.SparseVector(indices=[1], values=[1.0]),
        },
        payload={"document_id": "문서", "fiscal_year": 2026, "text": "기존 본문"},
    )


class FakeClient:
    def __init__(
        self,
        collections=None,
        *,
        drop_upserts=False,
        vector_size=2,
    ):
        self.records = {
            name: list(points) for name, points in (collections or {}).items()
        }
        self.vectors_config = {
            "dense": models.VectorParams(
                size=vector_size,
                distance=models.Distance.COSINE,
            )
        }
        self.sparse_vectors_config = {
            "sparse": models.SparseVectorParams(modifier=models.Modifier.IDF)
        }
        self.write_calls = []
        self.indexes = {}
        self.drop_upserts = drop_upserts

    def get_collections(self):
        return SimpleNamespace(
            collections=[SimpleNamespace(name=name) for name in self.records]
        )

    def get_collection(self, name):
        return SimpleNamespace(
            config=SimpleNamespace(
                params=SimpleNamespace(
                    vectors=self.vectors_config,
                    sparse_vectors=self.sparse_vectors_config,
                )
            )
        )

    def count(self, name, exact=True):
        assert exact is True
        return SimpleNamespace(count=len(self.records[name]))

    def collection_exists(self, name):
        return name in self.records

    def scroll(self, collection_name, **kwargs):
        assert kwargs["with_payload"] is True
        assert kwargs["with_vectors"] is True
        return list(self.records[collection_name]), None

    def create_collection(self, collection_name, **kwargs):
        self.write_calls.append(("create_collection", collection_name, kwargs))
        self.records[collection_name] = []

    def upsert(self, collection_name, points, wait):
        assert wait is True
        points = list(points)
        self.write_calls.append(("upsert", collection_name, points))
        if not self.drop_upserts:
            self.records[collection_name].extend(points)

    def create_payload_index(self, collection_name, field_name, field_schema):
        self.write_calls.append(
            ("create_payload_index", collection_name, field_name, field_schema)
        )
        self.indexes.setdefault(collection_name, set()).add(
            (field_name, field_schema)
        )


def test_inspect_collections_reads_exact_counts_and_schema():
    source = FakeClient(
        collections={"rag_two": [_record("p2")], "rag_one": [_record("p1")]}
    )

    summaries = inspect_collections(source)

    assert [summary.name for summary in summaries] == ["rag_one", "rag_two"]
    assert summaries[0].points_count == 1
    assert summaries[0].vectors_config == source.vectors_config
    assert summaries[0].sparse_vectors_config == source.sparse_vectors_config


def test_dry_run_never_creates_or_upserts():
    source = FakeClient(collections={"rag_one": [_record("p1")]})
    target = FakeClient()

    report = migrate_collections(source, target, apply=False)

    assert report[0].source_count == 1
    assert report[0].target_count is None
    assert report[0].vectors_config == source.vectors_config
    assert report[0].sparse_vectors_config == source.sparse_vectors_config
    assert target.write_calls == []


def test_apply_preserves_ids_vectors_payloads_and_adds_filter_indexes():
    source = FakeClient(collections={"rag_one": [_record("p1")]})
    target = FakeClient()

    report = migrate_collections(source, target, apply=True, batch_size=1)

    point = target.records["rag_one"][0]
    assert point.id == "p1"
    assert point.vector == source.records["rag_one"][0].vector
    assert point.payload == source.records["rag_one"][0].payload
    assert target.indexes["rag_one"] == {
        ("fiscal_year", models.PayloadSchemaType.INTEGER),
        ("document_id", models.PayloadSchemaType.KEYWORD),
    }
    assert report[0].target_count == 1


def test_apply_rejects_nonempty_target_before_any_write():
    source = FakeClient(
        collections={"rag_one": [_record("p1")], "rag_two": [_record("p2")]}
    )
    target = FakeClient(collections={"rag_two": [_record("existing")]})

    with pytest.raises(RuntimeError, match="비어 있지"):
        migrate_collections(source, target, apply=True)

    assert target.write_calls == []


def test_apply_fails_when_final_count_differs():
    source = FakeClient(collections={"rag_one": [_record("p1")]})
    target = FakeClient(drop_upserts=True)

    with pytest.raises(RuntimeError, match="포인트 수 불일치"):
        migrate_collections(source, target, apply=True)


def test_apply_rejects_empty_target_with_different_schema_before_writes():
    source = FakeClient(collections={"rag_one": [_record("p1")]}, vector_size=2)
    target = FakeClient(collections={"rag_one": []}, vector_size=3)

    with pytest.raises(RuntimeError, match="스키마가 다릅니다"):
        migrate_collections(source, target, apply=True)

    assert target.write_calls == []


def test_parser_defaults_to_dry_run():
    args = build_parser().parse_args(
        [
            "--source-path",
            "source",
            "--server-storage-path",
            "storage",
            "--target-url",
            "http://127.0.0.1:6333",
        ]
    )

    assert args.apply is False
    assert args.batch_size == 128
    assert args.timeout == 60


def test_main_rejects_source_path_equal_to_server_storage(tmp_path, capsys):
    result = main(
        [
            "--source-path",
            str(tmp_path),
            "--server-storage-path",
            str(tmp_path),
            "--target-url",
            "http://127.0.0.1:6333",
            "--apply",
        ]
    )

    assert result == 2
    assert "같을 수 없습니다" in capsys.readouterr().err


def test_main_rejects_non_local_target_url(tmp_path, capsys):
    source = tmp_path / "source"
    storage = tmp_path / "storage"
    source.mkdir()
    storage.mkdir()

    result = main(
        [
            "--source-path",
            str(source),
            "--server-storage-path",
            str(storage),
            "--target-url",
            "https://qdrant.example.com",
        ]
    )

    assert result == 2
    assert "localhost" in capsys.readouterr().err


def test_main_passes_long_timeout_to_http_client(tmp_path, monkeypatch):
    source = tmp_path / "source"
    storage = tmp_path / "storage"
    source.mkdir()
    storage.mkdir()
    calls = []

    class ClosableClient:
        def close(self):
            pass

    def fake_client(**kwargs):
        calls.append(kwargs)
        return ClosableClient()

    monkeypatch.setattr(migration, "_check_health", lambda _url: None)
    monkeypatch.setattr(migration, "QdrantClient", fake_client)
    monkeypatch.setattr(migration, "migrate_collections", lambda *args, **kwargs: [])

    result = migration.main(
        [
            "--source-path",
            str(source),
            "--server-storage-path",
            str(storage),
            "--target-url",
            "http://127.0.0.1:6333",
        ]
    )

    assert result == 0
    assert calls == [
        {"path": str(source.resolve())},
        {"url": "http://127.0.0.1:6333", "timeout": 60},
    ]


def test_real_clients_preserve_vectors_and_payloads(tmp_path):
    source = QdrantClient(path=str(tmp_path / "source"))
    target = QdrantClient(path=str(tmp_path / "target"))
    collection = "rag_real"
    point_id = str(uuid.uuid4())
    try:
        source.create_collection(
            collection_name=collection,
            vectors_config={
                "dense": models.VectorParams(
                    size=2,
                    distance=models.Distance.COSINE,
                )
            },
            sparse_vectors_config={
                "sparse": models.SparseVectorParams(modifier=models.Modifier.IDF)
            },
        )
        source.upsert(
            collection_name=collection,
            points=[
                models.PointStruct(
                    id=point_id,
                    vector={
                        "dense": [0.1, 0.2],
                        "sparse": models.SparseVector(
                            indices=[1],
                            values=[1.0],
                        ),
                    },
                    payload={
                        "document_id": "실제 문서",
                        "fiscal_year": 2026,
                        "text": "보존할 본문",
                    },
                )
            ],
            wait=True,
        )
        stored_source = source.retrieve(
            collection_name=collection,
            ids=[point_id],
            with_payload=True,
            with_vectors=True,
        )[0]

        report = migrate_collections(source, target, apply=True)

        copied = target.retrieve(
            collection_name=collection,
            ids=[point_id],
            with_payload=True,
            with_vectors=True,
        )[0]
        assert report[0].target_count == 1
        assert copied.id == point_id
        assert copied.vector["dense"] == pytest.approx(
            stored_source.vector["dense"]
        )
        assert copied.vector["sparse"].indices == stored_source.vector["sparse"].indices
        assert copied.vector["sparse"].values == pytest.approx(
            stored_source.vector["sparse"].values
        )
        assert copied.payload == stored_source.payload
    finally:
        source.close()
        target.close()
