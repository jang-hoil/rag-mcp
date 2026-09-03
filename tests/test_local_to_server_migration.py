"""Qdrant local-path 컬렉션을 HTTP 서버로 복사하는 도구 테스트."""
from types import SimpleNamespace

import pytest
from qdrant_client import models

from scripts.migrate_local_to_server import inspect_collections, migrate_collections


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
    def __init__(self, collections=None, *, drop_upserts=False):
        self.records = {
            name: list(points) for name, points in (collections or {}).items()
        }
        self.vectors_config = {
            "dense": models.VectorParams(size=2, distance=models.Distance.COSINE)
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
