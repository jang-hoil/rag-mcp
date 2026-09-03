"""Qdrant local-path 컬렉션을 Qdrant 서버로 안전하게 복사한다.

기본 동작은 쓰기가 없는 dry-run이다. 실제 복사는 ``--apply``를 명시한 경우에만
수행하며, 원본 컬렉션이나 포인트는 수정하거나 삭제하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Iterator
from typing import Any

from qdrant_client import models


@dataclass(frozen=True)
class CollectionSummary:
    """이관 전에 확인한 원본 컬렉션 정보."""

    name: str
    points_count: int
    vectors_config: Any
    sparse_vectors_config: Any


@dataclass(frozen=True)
class MigrationResult:
    """컬렉션별 이관 결과. dry-run이면 target_count는 None이다."""

    name: str
    source_count: int
    target_count: int | None


def inspect_collections(client) -> list[CollectionSummary]:
    """모든 컬렉션의 이름, 정확한 포인트 수, 벡터 스키마를 읽는다."""
    summaries = []
    names = sorted(item.name for item in client.get_collections().collections)
    for name in names:
        info = client.get_collection(name)
        summaries.append(
            CollectionSummary(
                name=name,
                points_count=client.count(name, exact=True).count,
                vectors_config=info.config.params.vectors,
                sparse_vectors_config=info.config.params.sparse_vectors,
            )
        )
    return summaries


def migrate_collections(
    source,
    target,
    *,
    apply: bool,
    batch_size: int = 128,
) -> list[MigrationResult]:
    """원본 컬렉션을 대상에 복사하고 컬렉션별 정확한 개수를 검증한다."""
    if batch_size < 1:
        raise ValueError("batch_size는 1 이상이어야 합니다")
    summaries = inspect_collections(source)
    if not apply:
        return [
            MigrationResult(
                name=summary.name,
                source_count=summary.points_count,
                target_count=None,
            )
            for summary in summaries
        ]

    for summary in summaries:
        if (
            target.collection_exists(summary.name)
            and target.count(summary.name, exact=True).count > 0
        ):
            raise RuntimeError(
                f"대상 컬렉션이 비어 있지 않아 덮어쓰지 않습니다: {summary.name}"
            )

    results = []
    for summary in summaries:
        if not target.collection_exists(summary.name):
            target.create_collection(
                collection_name=summary.name,
                vectors_config=summary.vectors_config,
                sparse_vectors_config=summary.sparse_vectors_config,
            )
        for batch in _iter_point_batches(source, summary.name, batch_size):
            target.upsert(
                collection_name=summary.name,
                points=[
                    models.PointStruct(
                        id=point.id,
                        vector=point.vector,
                        payload=point.payload,
                    )
                    for point in batch
                ],
                wait=True,
            )
        for field_name, field_schema in (
            ("fiscal_year", models.PayloadSchemaType.INTEGER),
            ("document_id", models.PayloadSchemaType.KEYWORD),
        ):
            target.create_payload_index(
                summary.name,
                field_name,
                field_schema,
            )
        target_count = target.count(summary.name, exact=True).count
        if target_count != summary.points_count:
            raise RuntimeError(
                f"{summary.name}: 포인트 수 불일치"
                f"(source={summary.points_count}, target={target_count})"
            )
        results.append(
            MigrationResult(
                name=summary.name,
                source_count=summary.points_count,
                target_count=target_count,
            )
        )
    return results


def _iter_point_batches(client, collection_name: str, batch_size: int) -> Iterator[list]:
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection_name,
            limit=batch_size,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        if points:
            yield points
        if offset is None:
            return
