"""기존 단일 Qdrant 컬렉션을 문서별 컬렉션으로 복사한다.

기본 동작은 읽기 전용 dry-run이다. ``--apply``를 명시한 경우에만 새 컬렉션을
만들어 기존 dense/sparse 벡터와 payload를 그대로 upsert한다. KURE 재임베딩은
수행하지 않으며 원본 ``rag_kure_chunks``도 삭제하거나 수정하지 않는다.
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from pathlib import Path

from qdrant_client import models

from rag_mcp.config import Config
from rag_mcp.vector_store import open_qdrant_client


SOURCE_COLLECTION = "rag_kure_chunks"
TARGET_DOCUMENTS = (
    "2026년 공직윤리 실무과정",
    "pas_public_audit_info_system_manual_v1_01_2023",
    "2025년_공직실무_재산등록(온라인용)",
)
EXPECTED_COUNTS = {
    "2026년 공직윤리 실무과정": 669,
    "pas_public_audit_info_system_manual_v1_01_2023": 370,
    "2025년_공직실무_재산등록(온라인용)": 38,
}
DISCARDED_DOCUMENTS = {
    "2025년_공직실무_윤리업무(온라인용)": 101,
    "칠곡군청_2026 통합회계실무교재": 857,
}
_BATCH_SIZE = 128


def collect_target_records(client) -> dict[str, list]:
    """원본 컬렉션을 전체 scroll해 이관 대상 레코드와 기존 벡터를 모은다."""
    grouped = {document_id: [] for document_id in TARGET_DOCUMENTS}
    offset = None
    while True:
        records, offset = client.scroll(
            collection_name=SOURCE_COLLECTION,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        for record in records:
            document_id = (record.payload or {}).get("document_id")
            if document_id in grouped:
                grouped[document_id].append(record)
        if offset is None:
            return grouped


def _validate_counts(grouped: dict[str, list]) -> list[str]:
    errors = []
    for document_id, expected in EXPECTED_COUNTS.items():
        actual = len(grouped.get(document_id, []))
        if actual != expected:
            errors.append(
                f"{document_id}: 예상 {expected}청크, 원본에서 확인 {actual}청크"
            )
    return errors


def _batches(records: list, size: int = _BATCH_SIZE) -> Iterable[list]:
    for start in range(0, len(records), size):
        yield records[start : start + size]


def _create_target_collection(client, config: Config, document_id: str) -> str:
    collection = config.collection_name("kure", document_id)
    client.create_collection(
        collection_name=collection,
        vectors_config={
            "dense": models.VectorParams(
                size=config.dimension("kure"),
                distance=models.Distance.COSINE,
            )
        },
        sparse_vectors_config={
            "sparse": models.SparseVectorParams(modifier=models.Modifier.IDF)
        },
    )
    return collection


def apply_migration(client, config: Config, grouped: dict[str, list]) -> None:
    """검증된 레코드를 새 문서별 컬렉션에 기존 벡터 그대로 복사한다."""
    targets = {
        document_id: config.collection_name("kure", document_id)
        for document_id in TARGET_DOCUMENTS
    }
    existing = [
        collection
        for collection in targets.values()
        if client.collection_exists(collection)
    ]
    if existing:
        joined = ", ".join(existing)
        raise RuntimeError(
            f"대상 컬렉션이 이미 존재해 덮어쓰지 않습니다: {joined}"
        )

    for document_id in TARGET_DOCUMENTS:
        collection = _create_target_collection(client, config, document_id)
        for batch in _batches(grouped[document_id]):
            # record.vector의 dense/sparse 값을 재사용한다. 임베딩 백엔드는 호출하지 않는다.
            points = [
                models.PointStruct(
                    id=record.id,
                    vector=record.vector,
                    payload=record.payload,
                )
                for record in batch
            ]
            client.upsert(collection_name=collection, points=points, wait=True)
        print(
            f"[이관 완료] {document_id} -> {collection}: "
            f"{len(grouped[document_id])}청크"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="기존 rag_kure_chunks를 문서별 Qdrant 컬렉션으로 이관"
    )
    parser.add_argument(
        "--qdrant-path",
        type=Path,
        default=None,
        help="Qdrant local 저장 경로(기본: RAG_QDRANT_PATH 또는 data/qdrant)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="검증 후 새 컬렉션을 실제 생성(dry-run 해제)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    args = build_parser().parse_args(argv)
    config = Config(
        qdrant_path=args.qdrant_path.resolve()
        if args.qdrant_path is not None
        else Config().qdrant_path
    )
    print(
        "[중요] 실행 전에 Qdrant 원본 디렉터리를 반드시 별도 위치에 백업하세요. "
        "MCP 서버를 완전히 종료해 local 파일락이 해제된 상태에서만 실행해야 합니다."
    )
    print(
        f"[모드] {'APPLY(새 컬렉션 생성)' if args.apply else 'DRY-RUN(쓰기 없음)'}"
    )

    client = open_qdrant_client(config)
    try:
        if not client.collection_exists(SOURCE_COLLECTION):
            print(f"[오류] 원본 컬렉션이 없습니다: {SOURCE_COLLECTION}", file=sys.stderr)
            return 1
        grouped = collect_target_records(client)
        for document_id in TARGET_DOCUMENTS:
            print(
                f"[확인] {document_id}: {len(grouped[document_id])}청크 "
                f"(예상 {EXPECTED_COUNTS[document_id]})"
            )
        print(
            "[제외] "
            + ", ".join(
                f"{document_id} {count}청크"
                for document_id, count in DISCARDED_DOCUMENTS.items()
            )
        )

        errors = _validate_counts(grouped)
        if errors:
            print("[오류] 원본 청크 수가 확정값과 다릅니다:", file=sys.stderr)
            for error in errors:
                print(f"  - {error}", file=sys.stderr)
            print("이관을 중단했습니다. 원본과 manifest를 확인하세요.", file=sys.stderr)
            return 1

        if not args.apply:
            print("[DRY-RUN 완료] 쓰기 작업을 하지 않았습니다. 실제 이관은 --apply가 필요합니다.")
            return 0

        apply_migration(client, config, grouped)
        print(
            "[완료] 새 문서별 컬렉션에 복사했습니다. "
            "원본 rag_kure_chunks는 보존되어 있습니다."
        )
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
