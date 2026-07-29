"""문서별 컬렉션 이관 스크립트 테스트(실제 data/qdrant는 열지 않음)."""
from types import SimpleNamespace

from scripts.migrate_to_per_doc_collections import (
    EXPECTED_COUNTS,
    SOURCE_COLLECTION,
    TARGET_DOCUMENTS,
    apply_migration,
    build_parser,
    collect_target_records,
)
from rag_mcp.config import Config


class FakeClient:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def scroll(self, **kwargs):
        self.calls.append(kwargs)
        return self.pages.pop(0)

    def collection_exists(self, name):
        return False

    def create_collection(self, **kwargs):
        self.calls.append({"create_collection": kwargs})

    def upsert(self, **kwargs):
        self.calls.append({"upsert": kwargs})


def _record(document_id, point_id):
    return SimpleNamespace(
        id=point_id,
        payload={"document_id": document_id, "text": "기존 본문"},
        vector={"dense": [0.1, 0.2], "sparse": {"indices": [1], "values": [1.0]}},
    )


def test_collect_target_records_reuses_vectors_and_skips_deleted_documents():
    keep = TARGET_DOCUMENTS[0]
    discarded = "2025년_공직실무_윤리업무(온라인용)"
    pages = [
        ([_record(keep, "keep-1"), _record(discarded, "drop-1")], "next"),
        ([_record(keep, "keep-2")], None),
    ]
    client = FakeClient(pages)

    grouped = collect_target_records(client)

    assert [record.id for record in grouped[keep]] == ["keep-1", "keep-2"]
    assert discarded not in grouped
    assert all(call["collection_name"] == SOURCE_COLLECTION for call in client.calls)
    assert all(call["with_vectors"] is True for call in client.calls)
    assert all(call["with_payload"] is True for call in client.calls)


def test_migration_constants_match_confirmed_document_counts():
    assert EXPECTED_COUNTS == {
        "2026년 공직윤리 실무과정": 669,
        "pas_public_audit_info_system_manual_v1_01_2023": 370,
        "2025년_공직실무_재산등록(온라인용)": 38,
    }


def test_dry_run_is_default_and_apply_flag_is_explicit():
    parser = build_parser()
    assert parser.parse_args([]).apply is False
    assert parser.parse_args(["--apply"]).apply is True


def test_apply_migration_upserts_existing_vectors_without_embedding(tmp_path):
    client = FakeClient([])
    grouped = {
        document_id: [_record(document_id, f"point-{index}")]
        for index, document_id in enumerate(TARGET_DOCUMENTS)
    }
    config = Config(data_dir=tmp_path, qdrant_path=tmp_path / "qdrant")

    apply_migration(client, config, grouped)

    upserts = [call["upsert"] for call in client.calls if "upsert" in call]
    assert len(upserts) == 3
    assert upserts[0]["points"][0].vector["dense"] == [0.1, 0.2]
    assert upserts[0]["points"][0].payload["text"] == "기존 본문"
