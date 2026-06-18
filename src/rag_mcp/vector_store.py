"""Qdrant 벡터 저장소 (dense+sparse named vector, 하이브리드 검색). 스펙 §6.7, §6.8.

  - 컬렉션은 임베딩 모델로 결정(저장·검색 일치 강제).
  - 한 청크 = 한 포인트에 named dense + named sparse 동시 upsert (orphan 없음).
  - 검색: prefetch(dense+sparse) + 각 Prefetch.filter + FusionQuery(RRF|DBSF).
    (local 모드 검증: top-level query_filter는 무효 → 필터는 각 Prefetch에 넣는다.)
"""
from __future__ import annotations

import uuid

from qdrant_client import QdrantClient, models

from .config import Config
from .models import Chunk
from .sparse import to_sparse

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
        if client is not None:
            self.client = client
        elif config.qdrant_mode == "server" and config.qdrant_url:
            self.client = QdrantClient(url=config.qdrant_url)
        else:
            config.qdrant_path.mkdir(parents=True, exist_ok=True)
            self.client = QdrantClient(path=str(config.qdrant_path))

    # --- 컬렉션 ---
    def ensure_collection(self) -> None:
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
        self.ensure_collection()
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
        if points:
            self.client.upsert(collection_name=self.collection, points=points)
        return len(points)

    def delete_document(self, document_id: str) -> None:
        """문서의 모든 포인트 삭제 (멱등 재색인용)."""
        if not self.client.collection_exists(self.collection):
            return
        self.client.delete(
            collection_name=self.collection,
            points_selector=models.Filter(
                must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))]
            ),
        )

    # --- 검색 ---
    def _filter(self, fiscal_year: int | None, filters: dict | None) -> models.Filter | None:
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
        filters: dict | None = None,
    ) -> list[models.ScoredPoint]:
        if not self.client.collection_exists(self.collection):
            return []
        qfilter = self._filter(fiscal_year, filters)
        sparse_q = (
            models.SparseVector(indices=sparse_vec[0], values=sparse_vec[1])
            if sparse_vec is not None
            else None
        )

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

    def count_by_document(self, document_id: str) -> int:
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
