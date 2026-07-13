"""Qdrant 벡터 저장소 (dense+sparse named vector, 하이브리드 검색). 스펙 §6.7, §6.8.

  - 컬렉션은 임베딩 모델로 결정(저장·검색 일치 강제).
  - 한 청크 = 한 포인트에 named dense + named sparse 동시 upsert (orphan 없음).
  - 검색: prefetch(dense+sparse) + 각 Prefetch.filter + FusionQuery(RRF|DBSF).
    (local 모드 검증: top-level query_filter는 무효 → 필터는 각 Prefetch에 넣는다.)
"""
from __future__ import annotations

import threading
from collections.abc import Mapping
import uuid

from qdrant_client import QdrantClient, models

from .config import Config
from .models import Chunk
from .request_models import FilterValue
from .sparse import to_sparse

class StorageBusyError(RuntimeError):
    """Qdrant local 저장소를 다른 프로세스가 이미 열고 있을 때(단일 writer 위반).

    RuntimeError 서브클래스라 기존 `except RuntimeError` 경로와 호환되면서도,
    server.py의 단일 인스턴스 가드는 이 타입으로 "중복 실행"만 정확히 골라낼 수 있다.
    """


# chunk_id → 안정 포인트 UUID (재색인 멱등)
_NAMESPACE = uuid.UUID("a7f3c1e2-0b4d-4e6a-9c8f-1234567890ab")


def point_id_for(chunk_id: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, chunk_id))


class VectorStore:
    def __init__(self, config: Config, embedding_model: str | None = None, client: QdrantClient | None = None):
        self.config = config
        self.embedding_model = embedding_model or config.embedding_model
        self.collection = config.collection_name(self.embedding_model)
        self.dimension = config.dimension(self.embedding_model)
        # QdrantLocal은 thread-safe가 아니다. 비동기 ingest(백그라운드 스레드)의 upsert와
        # 검색 요청이 같은 클라이언트를 동시 접근하면 손상될 수 있어 접근을 직렬화한다.
        # RLock인 이유: upsert_chunks가 내부에서 ensure_collection을 호출(같은 스레드 재진입).
        self._lock = threading.RLock()
        if client is not None:
            self.client = client
        elif config.qdrant_mode == "server":
            if not config.qdrant_url:
                raise ValueError(
                    "RAG_QDRANT_MODE=server인데 RAG_QDRANT_URL이 비어 있습니다 "
                    "(조용히 local로 폴백하지 않음 — URL을 설정하거나 mode를 local로)."
                )
            self.client = QdrantClient(url=config.qdrant_url)
        else:
            config.qdrant_path.mkdir(parents=True, exist_ok=True)
            try:
                self.client = QdrantClient(path=str(config.qdrant_path))
            except RuntimeError as e:
                # Qdrant local은 단일 프로세스만 저장소를 열 수 있다(스펙 §1.3 파일락).
                # serve(MCP)가 떠 있는 동안 CLI ingest 등을 동시 실행하면 여기서 막힌다.
                if "already accessed" in str(e):
                    raise StorageBusyError(
                        f"Qdrant local 저장소({config.qdrant_path})가 다른 프로세스에서 사용 중입니다. "
                        "MCP serve가 실행 중이면 종료한 뒤 다시 시도하세요"
                        "(local path 모드는 동시 접근 불가 — 동시 사용이 필요하면 server 모드 사용)."
                    ) from e
                raise

    # --- 컬렉션 ---
    def ensure_collection(self) -> None:
        with self._lock:
            self._ensure_collection_locked()

    def _ensure_collection_locked(self) -> None:
        if self.client.collection_exists(self.collection):
            return
        self.client.create_collection(
            collection_name=self.collection,
            vectors_config={
                "dense": models.VectorParams(size=self.dimension, distance=models.Distance.COSINE),
            },
            sparse_vectors_config={
                # IDF 서버측 계산(BM25 유사). local 모드 미지원 시 값(tf)만으로도 동작.
                "sparse": models.SparseVectorParams(modifier=models.Modifier.IDF),
            },
        )
        # 필터용 payload 인덱스 (local no-op, server 대비 유지)
        for field, schema in [
            ("fiscal_year", models.PayloadSchemaType.INTEGER),
            ("document_id", models.PayloadSchemaType.KEYWORD),
        ]:
            try:
                self.client.create_payload_index(self.collection, field, field_schema=schema)
            except Exception:
                pass

    # --- 색인 ---
    def upsert_chunks(self, chunks: list[Chunk], dense_vectors: list[list[float]]) -> int:
        if len(chunks) != len(dense_vectors):
            raise ValueError("chunks와 dense_vectors 길이 불일치")
        points = []
        for chunk, dvec in zip(chunks, dense_vectors):
            idx, val = to_sparse(chunk.text)
            points.append(
                models.PointStruct(
                    id=point_id_for(chunk.chunk_id),
                    vector={
                        "dense": dvec,
                        "sparse": models.SparseVector(indices=idx, values=val),
                    },
                    payload=chunk.payload(),
                )
            )
        with self._lock:
            self._ensure_collection_locked()
            if points:
                self.client.upsert(collection_name=self.collection, points=points)
        return len(points)

    def delete_document(self, document_id: str) -> None:
        """문서의 모든 포인트 삭제 (멱등 재색인용)."""
        with self._lock:
            if not self.client.collection_exists(self.collection):
                return
            self.client.delete(
                collection_name=self.collection,
                points_selector=models.Filter(
                    must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))]
                ),
            )

    def point_ids_by_document(self, document_id: str) -> set[str | int]:
        with self._lock:
            return {
                point.id
                for point in self._document_points_locked(
                    document_id,
                    with_payload=False,
                    with_vectors=False,
                )
            }

    def _document_points_locked(
        self,
        document_id: str,
        *,
        with_payload: bool,
        with_vectors: bool,
    ) -> list[models.Record]:
        if not self.client.collection_exists(self.collection):
            return []
        found = []
        offset = None
        while True:
            points, offset = self.client.scroll(
                collection_name=self.collection,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="document_id",
                            match=models.MatchValue(value=document_id),
                        )
                    ]
                ),
                limit=256,
                offset=offset,
                with_payload=with_payload,
                with_vectors=with_vectors,
            )
            found.extend(points)
            if offset is None:
                return found

    def delete_point_ids(self, point_ids: set[str | int]) -> None:
        if not point_ids:
            return
        with self._lock:
            if not self.client.collection_exists(self.collection):
                return
            self.client.delete(
                collection_name=self.collection,
                points_selector=models.PointIdsList(points=list(point_ids)),
            )

    def replace_document(
        self,
        document_id: str,
        chunks: list[Chunk],
        dense_vectors: list[list[float]],
    ) -> int:
        """문서 포인트 교체를 직렬화하고 실패 시 교체 전 상태로 복구한다."""
        new_point_ids = {point_id_for(chunk.chunk_id) for chunk in chunks}
        with self._lock:
            old_points = self._document_points_locked(
                document_id,
                with_payload=True,
                with_vectors=True,
            )
            old_point_ids = {point.id for point in old_points}
            try:
                count = self.upsert_chunks(chunks, dense_vectors)
                self.delete_point_ids(old_point_ids - new_point_ids)
                return count
            except Exception as primary_error:
                rollback_errors = []
                if old_points:
                    try:
                        self.client.upsert(
                            collection_name=self.collection,
                            points=[
                                models.PointStruct(
                                    id=point.id,
                                    vector=point.vector,
                                    payload=point.payload,
                                )
                                for point in old_points
                            ],
                        )
                    except Exception as restore_error:
                        rollback_errors.append(("restore old points", restore_error))
                try:
                    self.delete_point_ids(new_point_ids - old_point_ids)
                except Exception as cleanup_error:
                    rollback_errors.append(("remove inserted points", cleanup_error))
                if rollback_errors:
                    details = "; ".join(
                        f"{stage}: {error!r}" for stage, error in rollback_errors
                    )
                    raise RuntimeError(
                        f"Qdrant replacement failed ({primary_error!r}); rollback failed: {details}"
                    ) from rollback_errors[0][1]
                raise

    # --- 검색 ---
    def _filter(self, fiscal_year: int | None, filters: Mapping[str, FilterValue] | None) -> models.Filter | None:
        must = []
        if fiscal_year is not None:
            must.append(models.FieldCondition(key="fiscal_year", match=models.MatchValue(value=fiscal_year)))
        if filters:
            for key, value in filters.items():
                must.append(models.FieldCondition(key=key, match=models.MatchValue(value=value)))
        return models.Filter(must=must) if must else None

    def query(
        self,
        dense_vec: list[float] | None,
        sparse_vec: tuple[list[int], list[float]] | None,
        top_k: int = 8,
        search_mode: str = "hybrid",
        fusion: str = "rrf",
        fiscal_year: int | None = None,
        filters: Mapping[str, FilterValue] | None = None,
    ) -> list[models.ScoredPoint]:
        qfilter = self._filter(fiscal_year, filters)
        sparse_q = (
            models.SparseVector(indices=sparse_vec[0], values=sparse_vec[1])
            if sparse_vec is not None
            else None
        )
        with self._lock:
            if not self.client.collection_exists(self.collection):
                return []
            if search_mode == "dense":
                return self.client.query_points(
                    self.collection, query=dense_vec, using="dense",
                    query_filter=qfilter, limit=top_k, with_payload=True,
                ).points
            if search_mode == "sparse":
                return self.client.query_points(
                    self.collection, query=sparse_q, using="sparse",
                    query_filter=qfilter, limit=top_k, with_payload=True,
                ).points

            # hybrid: prefetch + 각 Prefetch.filter + FusionQuery
            fusion_kind = models.Fusion.DBSF if fusion == "dbsf" else models.Fusion.RRF
            prefetch = []
            if dense_vec is not None:
                prefetch.append(models.Prefetch(query=dense_vec, using="dense", limit=max(20, top_k), filter=qfilter))
            if sparse_q is not None:
                prefetch.append(models.Prefetch(query=sparse_q, using="sparse", limit=max(30, top_k), filter=qfilter))
            return self.client.query_points(
                self.collection,
                prefetch=prefetch,
                query=models.FusionQuery(fusion=fusion_kind),
                limit=top_k,
                with_payload=True,
            ).points

    def retrieve_chunk(self, chunk_id: str):
        """chunk_id 단건 조회 (point id는 chunk_id의 uuid5). 없으면 None."""
        with self._lock:
            if not self.client.collection_exists(self.collection):
                return None
            recs = self.client.retrieve(
                self.collection, ids=[point_id_for(chunk_id)], with_payload=True
            )
        return recs[0] if recs else None

    def count_by_document(self, document_id: str) -> int:
        with self._lock:
            if not self.client.collection_exists(self.collection):
                return 0
            res = self.client.count(
                self.collection,
                count_filter=models.Filter(
                    must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))]
                ),
                exact=True,
            )
            return res.count

    def status(self) -> dict:
        with self._lock:
            exists = self.client.collection_exists(self.collection)
            if not exists:
                return {"collection": self.collection, "exists": False, "dimension": self.dimension}
            info = self.client.get_collection(self.collection)
        return {
            "collection": self.collection,
            "exists": True,
            "dimension": self.dimension,
            "points": info.points_count,
            "has_sparse": True,
            "embedding_model": self.embedding_model,
        }
