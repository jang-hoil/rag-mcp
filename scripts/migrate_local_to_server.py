"""Qdrant local-path 컬렉션을 Qdrant 서버로 안전하게 복사한다.

기본 동작은 쓰기가 없는 dry-run이다. 실제 복사는 ``--apply``를 명시한 경우에만
수행하며, 원본 컬렉션이나 포인트는 수정하거나 삭제하지 않는다.
"""
from __future__ import annotations

import argparse
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen

from qdrant_client import QdrantClient
from qdrant_client import models

from rag_mcp.vector_store import QDRANT_SERVER_TIMEOUT_SECONDS


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
    vectors_config: Any
    sparse_vectors_config: Any


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
                vectors_config=summary.vectors_config,
                sparse_vectors_config=summary.sparse_vectors_config,
            )
            for summary in summaries
        ]

    for summary in summaries:
        if not target.collection_exists(summary.name):
            continue
        if target.count(summary.name, exact=True).count > 0:
            raise RuntimeError(
                f"대상 컬렉션이 비어 있지 않아 덮어쓰지 않습니다: {summary.name}"
            )
        target_info = target.get_collection(summary.name)
        target_params = target_info.config.params
        if (
            target_params.vectors != summary.vectors_config
            or target_params.sparse_vectors != summary.sparse_vectors_config
        ):
            raise RuntimeError(
                f"비어 있는 대상 컬렉션의 벡터 스키마가 다릅니다: {summary.name}"
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
                vectors_config=summary.vectors_config,
                sparse_vectors_config=summary.sparse_vectors_config,
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Qdrant local-path 컬렉션을 localhost Qdrant 서버로 복사"
    )
    parser.add_argument(
        "--source-path",
        type=Path,
        required=True,
        help="기존 Qdrant local 저장 경로",
    )
    parser.add_argument(
        "--server-storage-path",
        type=Path,
        required=True,
        help="Qdrant 서버의 실제 storage 경로(원본 경로 충돌 검사용)",
    )
    parser.add_argument(
        "--target-url",
        required=True,
        help="localhost Qdrant HTTP URL(예: http://127.0.0.1:6333)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=128,
        help="scroll/upsert 배치 크기(기본 128)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=QDRANT_SERVER_TIMEOUT_SECONDS,
        help="Qdrant 서버 요청 제한(초, 기본 60)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="실제 컬렉션 생성과 포인트 복사 수행(기본은 dry-run)",
    )
    return parser


def _validate_args(args) -> tuple[Path, Path, str]:
    source_path = args.source_path.resolve()
    server_storage_path = args.server_storage_path.resolve()
    if not source_path.is_dir():
        raise ValueError(f"원본 Qdrant 경로가 없습니다: {source_path}")
    if source_path == server_storage_path:
        raise ValueError("원본 local 경로와 서버 storage 경로는 같을 수 없습니다")
    if args.batch_size < 1:
        raise ValueError("batch-size는 1 이상이어야 합니다")
    if args.timeout < 1:
        raise ValueError("timeout은 1초 이상이어야 합니다")

    target_url = args.target_url.rstrip("/")
    parsed = urlparse(target_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        raise ValueError("target-url은 localhost Qdrant HTTP 주소여야 합니다")
    return source_path, server_storage_path, target_url


def _check_health(target_url: str) -> None:
    with urlopen(f"{target_url}/healthz", timeout=5) as response:
        if response.status != 200:
            raise RuntimeError(
                f"Qdrant healthz 응답이 정상이 아닙니다: HTTP {response.status}"
            )


def _configure_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    _configure_utf8_console()
    args = build_parser().parse_args(argv)
    try:
        source_path, _server_storage_path, target_url = _validate_args(args)
    except ValueError as exc:
        print(f"[설정 오류] {exc}", file=sys.stderr)
        return 2

    source = None
    target = None
    try:
        _check_health(target_url)
        source = QdrantClient(path=str(source_path))
        target = QdrantClient(url=target_url, timeout=args.timeout)
        results = migrate_collections(
            source,
            target,
            apply=args.apply,
            batch_size=args.batch_size,
        )
        mode = "APPLY" if args.apply else "DRY-RUN"
        print(f"[{mode}] {len(results)}개 컬렉션")
        for result in results:
            target_text = "쓰기 없음" if result.target_count is None else result.target_count
            print(
                f"- {result.name}: source={result.source_count}, "
                f"target={target_text}"
            )
            print(f"  dense={result.vectors_config!r}")
            print(f"  sparse={result.sparse_vectors_config!r}")
        return 0
    except Exception as exc:
        print(f"[이관 오류] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if source is not None:
            source.close()
        if target is not None:
            target.close()


if __name__ == "__main__":
    raise SystemExit(main())
