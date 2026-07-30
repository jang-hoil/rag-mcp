"""검색 (hybrid/dense/sparse + fiscal_year 필터). 스펙 §6.8, §7.3.

  - 질의 임베딩(dense) + 질의 sparse(Kiwi 코드/금액 보존)를 vector_store로 융합 검색.
  - RRF 기본, fusion="dbsf" 분기. 점수 직접 가산 금지(RRF/DBSF만).
  - needs_image면 page_image를 결과에 포함(스펙 §5 스키마).
  - matched_by: dense/sparse 중 실제 기여한 신호 표기.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import threading

from qdrant_client.models import ScoredPoint

from .config import Config
from .embeddings import EmbeddingBackend, get_backend
from .models import SearchResult, SearchSource
from .rerank import Reranker, build_reranker
from .request_models import FilterValue
from .sparse import to_sparse_query
from .vector_store import VectorStore


@dataclass(frozen=True)
class SearchOutcome:
    """임계값 적용 결과와 필터 전후 진단 수치."""

    results: list[SearchResult]
    candidates_before_filter: int
    filtered_by_dense: int
    filtered_by_relative: int
    notices: tuple[str, ...] = ()


def _source_from_payload(payload: dict) -> SearchSource:
    """Qdrant payload → SearchSource (스펙 §5 스키마). needs_image일 때만 page_image 노출."""
    return SearchSource(
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


class Retriever:
    def __init__(
        self,
        config: Config,
        embedding_model: str = "kure",
        document_id: str | None = None,
        backend: EmbeddingBackend | None = None,
        store: VectorStore | None = None,
    ):
        self.config = config
        self.embedding_model = embedding_model
        if document_id is None:
            raise ValueError("Retriever를 만들려면 document_id가 필요합니다")
        self.document_id = document_id
        self._backend = backend  # lazy: 실제 호출 시 로딩
        self.store = store or VectorStore(config, embedding_model, document_id)
        self._reranker: Reranker | None = None
        self._reranker_cache_key: tuple | None = None
        self._reranker_lock = threading.Lock()

    @property
    def backend(self) -> EmbeddingBackend:
        if self._backend is None:
            self._backend = get_backend(self.embedding_model)
        return self._backend

    def _current_reranker_key(self) -> tuple:
        """리랭커 인스턴스 생성에 영향을 주는 설정 키."""
        return (
            self.config.reranker,
            self.config.cross_reranker_model,
            self.config.rerank_lexical_weight,
            self.config.rerank_heading_weight,
            self.config.rerank_table_weight,
            self.config.rerank_amount_weight,
            self.config.rerank_code_weight,
            self.config.rerank_length_weight,
        )

    @property
    def reranker(self) -> Reranker:
        """설정별 리랭커를 지연 생성하고 스레드 안전하게 재사용한다."""
        key = self._current_reranker_key()
        reranker = self._reranker
        if reranker is not None and self._reranker_cache_key == key:
            return reranker
        with self._reranker_lock:
            key = self._current_reranker_key()
            if self._reranker is None or self._reranker_cache_key != key:
                self._reranker = build_reranker(self.config)
                self._reranker_cache_key = key
            return self._reranker

    def search(
        self,
        query: str,
        top_k: int = 8,
        search_mode: str = "hybrid",
        fusion: str = "rrf",
        fiscal_year: int | None = None,
        filters: Mapping[str, FilterValue] | None = None,
    ) -> list[SearchResult]:
        return self.search_with_diagnostics(
            query,
            top_k=top_k,
            search_mode=search_mode,
            fusion=fusion,
            fiscal_year=fiscal_year,
            filters=filters,
        ).results

    def search_with_diagnostics(
        self,
        query: str,
        top_k: int = 8,
        search_mode: str = "hybrid",
        fusion: str = "rrf",
        fiscal_year: int | None = None,
        filters: Mapping[str, FilterValue] | None = None,
    ) -> SearchOutcome:
        # 컬렉션이 없는 미이관 문서는 KURE 연산 전에 명확히 실패시킨다.
        self.store.require_collection()
        notices = ()
        if search_mode == "sparse" and self.config.min_dense_score > 0:
            notices = (
                "sparse 검색에서는 dense 벡터를 사용하지 않아 "
                f"RAG_MIN_DENSE_SCORE={self.config.min_dense_score} 설정을 "
                "적용하지 않았습니다.",
            )
        dense_vec = None
        sparse_vec = None
        if search_mode in ("dense", "hybrid"):
            dense_vec = self.backend.embed_query(query)
        if search_mode in ("sparse", "hybrid"):
            sparse_vec = to_sparse_query(query)

        q_sparse_idx = set(sparse_vec[0]) if sparse_vec else set()
        candidate_k = (
            max(top_k, self.config.rerank_fetch_k)
            if self.config.reranker != "off"
            else top_k
        )
        points = self.store.query(
            dense_vec, sparse_vec, top_k=candidate_k, search_mode=search_mode,
            fusion=fusion, fiscal_year=fiscal_year, filters=filters,
        )
        candidates_before_filter = len(points)
        min_dense_score = self.config.min_dense_score
        rel_score_ratio = self.config.rel_score_ratio

        dense_scores: dict[str | int, float] = {}
        if (
            (min_dense_score > 0 or rel_score_ratio > 0)
            and search_mode in ("dense", "hybrid")
        ):
            if search_mode == "dense":
                dense_scores = {point.id: float(point.score) for point in points}
            else:
                dense_scores = self.store.dense_scores_for_points(
                    dense_vec or [],
                    [point.id for point in points],
                )

        if search_mode in ("dense", "hybrid"):
            top_relative_score = max(dense_scores.values(), default=0.0)
        else:
            top_relative_score = max(
                (float(point.score) for point in points),
                default=0.0,
            )
        relative_threshold = (
            top_relative_score * rel_score_ratio
            if top_relative_score > 0
            else top_relative_score
        )
        kept: list[ScoredPoint] = []
        filtered_by_dense = 0
        filtered_by_relative = 0
        for point in points:
            if (
                min_dense_score > 0
                and search_mode in ("dense", "hybrid")
                and dense_scores.get(point.id, -1.0) < min_dense_score
            ):
                filtered_by_dense += 1
                continue
            if (
                rel_score_ratio > 0
                and (
                    dense_scores.get(point.id, -1.0)
                    if search_mode in ("dense", "hybrid")
                    else float(point.score)
                )
                < relative_threshold
            ):
                filtered_by_relative += 1
                continue
            kept.append(point)

        converted = [
            self._to_result(point, search_mode, q_sparse_idx)
            for point in kept
        ]
        reranked = self.reranker.rerank(query, converted)[:top_k]
        return SearchOutcome(
            results=reranked,
            candidates_before_filter=candidates_before_filter,
            filtered_by_dense=filtered_by_dense,
            filtered_by_relative=filtered_by_relative,
            notices=notices,
        )

    def _to_result(self, p: ScoredPoint, search_mode: str, q_sparse_idx: set[int]) -> SearchResult:
        payload = p.payload or {}
        matched_by: list[str] = []
        if search_mode in ("dense", "hybrid"):
            # dense는 근사 표기: dense 신호가 동작한 검색이면 항상 포함(개별 청크 기여도는 미산정).
            # sparse는 아래에서 질의·문서 토큰 겹침을 실제로 확인한다.
            matched_by.append("dense")
        if search_mode in ("sparse", "hybrid"):
            # 질의 sparse 토큰이 청크 텍스트와 겹치면 sparse 기여로 표기
            doc_idx, _ = to_sparse_query(payload.get("text", ""))
            if q_sparse_idx & set(doc_idx):
                matched_by.append("sparse")

        return SearchResult(
            chunk_id=payload.get("chunk_id", str(p.id)),
            text=payload.get("text", ""),
            score=float(p.score),
            matched_by=matched_by,
            source=_source_from_payload(payload),
        )

    def get_chunk(self, chunk_id: str) -> SearchResult | None:
        """chunk_id로 단건 조회 (point id는 chunk_id의 uuid5)."""
        rec = self.store.retrieve_chunk(chunk_id)
        if rec is None:
            return None
        payload = rec.payload or {}
        return SearchResult(
            chunk_id=chunk_id, text=payload.get("text", ""), score=1.0,
            matched_by=[], source=_source_from_payload(payload),
        )
