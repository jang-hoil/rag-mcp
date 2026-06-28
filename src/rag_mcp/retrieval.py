"""검색 (hybrid/dense/sparse + fiscal_year 필터). 스펙 §6.8, §7.3.

  - 질의 임베딩(dense) + 질의 sparse(Kiwi 코드/금액 보존)를 vector_store로 융합 검색.
  - RRF 기본, fusion="dbsf" 분기. 점수 직접 가산 금지(RRF/DBSF만).
  - needs_image면 page_image를 결과에 포함(스펙 §5 스키마).
  - matched_by: dense/sparse 중 실제 기여한 신호 표기.
"""
from __future__ import annotations

from collections.abc import Mapping

from qdrant_client.models import ScoredPoint

from .config import Config
from .embeddings import EmbeddingBackend, get_backend
from .models import SearchResult, SearchSource
from .request_models import FilterValue
from .sparse import to_sparse
from .vector_store import VectorStore


class Retriever:
    def __init__(
        self,
        config: Config,
        embedding_model: str = "kure",
        backend: EmbeddingBackend | None = None,
        store: VectorStore | None = None,
    ):
        self.config = config
        self.embedding_model = embedding_model
        self._backend = backend  # lazy: 실제 호출 시 로딩
        self.store = store or VectorStore(config, embedding_model)

    @property
    def backend(self) -> EmbeddingBackend:
        if self._backend is None:
            self._backend = get_backend(self.embedding_model)
        return self._backend

    def search(
        self,
        query: str,
        top_k: int = 8,
        search_mode: str = "hybrid",
        fusion: str = "rrf",
        fiscal_year: int | None = None,
        filters: Mapping[str, FilterValue] | None = None,
    ) -> list[SearchResult]:
        dense_vec = None
        sparse_vec = None
        if search_mode in ("dense", "hybrid"):
            dense_vec = self.backend.embed_query(query)
        if search_mode in ("sparse", "hybrid"):
            sparse_vec = to_sparse(query)

        q_sparse_idx = set(sparse_vec[0]) if sparse_vec else set()
        points = self.store.query(
            dense_vec, sparse_vec, top_k=top_k, search_mode=search_mode,
            fusion=fusion, fiscal_year=fiscal_year, filters=filters,
        )
        return [self._to_result(p, search_mode, q_sparse_idx) for p in points]

    def _to_result(self, p: ScoredPoint, search_mode: str, q_sparse_idx: set[int]) -> SearchResult:
        payload = p.payload or {}
        matched_by: list[str] = []
        if search_mode in ("dense", "hybrid"):
            matched_by.append("dense")
        if search_mode in ("sparse", "hybrid"):
            # 질의 sparse 토큰이 청크 텍스트와 겹치면 sparse 기여로 표기
            doc_idx, _ = to_sparse(payload.get("text", ""))
            if q_sparse_idx & set(doc_idx):
                matched_by.append("sparse")

        source = SearchSource(
            document_id=payload.get("document_id", ""),
            doc_name=payload.get("doc_name"),
            fiscal_year=payload.get("fiscal_year"),
            source_path=payload.get("source_path"),
            page=payload.get("page"),
            heading_path=payload.get("heading_path") or [],
            is_table=bool(payload.get("is_table")),
            has_amount=bool(payload.get("has_amount")),
            has_code=bool(payload.get("has_code")),
            needs_image=bool(payload.get("needs_image")),
            page_image=payload.get("page_image") if payload.get("needs_image") else None,
            meta=payload.get("meta") or {},
        )
        return SearchResult(
            chunk_id=payload.get("chunk_id", str(p.id)),
            text=payload.get("text", ""),
            score=float(p.score),
            matched_by=matched_by,
            source=source,
        )

    def get_chunk(self, chunk_id: str) -> SearchResult | None:
        """chunk_id로 단건 조회 (point id는 chunk_id의 uuid5)."""
        rec = self.store.retrieve_chunk(chunk_id)
        if rec is None:
            return None
        payload = rec.payload or {}
        source = SearchSource(
            document_id=payload.get("document_id", ""),
            doc_name=payload.get("doc_name"),
            fiscal_year=payload.get("fiscal_year"),
            source_path=payload.get("source_path"),
            page=payload.get("page"),
            heading_path=payload.get("heading_path") or [],
            is_table=bool(payload.get("is_table")),
            has_amount=bool(payload.get("has_amount")),
            has_code=bool(payload.get("has_code")),
            needs_image=bool(payload.get("needs_image")),
            page_image=payload.get("page_image") if payload.get("needs_image") else None,
            meta=payload.get("meta") or {},
        )
        return SearchResult(
            chunk_id=chunk_id, text=payload.get("text", ""), score=1.0,
            matched_by=[], source=source,
        )
