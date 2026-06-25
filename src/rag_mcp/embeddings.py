"""임베딩 백엔드 (KURE / BGE-M3 공통 인터페이스). 스펙 §6.6, §1.3.

  - KURE-v1: nlpai-lab/KURE-v1, BGE-M3 기반, 1024차원, 최대 8192 토큰, dense 전용.
  - 저장·검색 모델 일치 강제 → 컬렉션이 모델로 결정됨 (config.COLLECTION_BY_MODEL).
  - SentenceTransformer는 무겁고 모델 다운로드가 필요하므로 **lazy 로딩**한다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from .config import DIMENSION_BY_MODEL, HF_ID_BY_MODEL


class EmbeddingBackend(ABC):
    """공통 인터페이스."""

    name: str
    dimension: int

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    @abstractmethod
    def embed_query(self, text: str) -> list[float]: ...


class _SentenceTransformerBackend(EmbeddingBackend):
    """SentenceTransformer 기반 dense 백엔드 (KURE/BGE-M3 공통)."""

    def __init__(self, name: str):
        self.name = name
        self.dimension = DIMENSION_BY_MODEL[name]
        self._hf_id = HF_ID_BY_MODEL[name]
        self._model = None  # lazy

    def _ensure_model(self):
        if self._model is None:
            # 정부망 MITM 프록시(self-signed 인증서)에서 HF 다운로드 SSL 검증 실패 우회:
            # Windows 인증서 저장소(프록시 루트 CA 등록됨)를 Python SSL에 주입한다.
            # truststore 미설치 시 무시(오프라인 캐시만으로 동작 가능).
            try:
                import truststore

                truststore.inject_into_ssl()
            except ImportError:
                pass

            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._hf_id)
            # 차원 검증 (저장·검색 정합 보장)
            got = self._model.get_sentence_embedding_dimension()
            if got != self.dimension:
                raise RuntimeError(
                    f"{self.name} 차원 불일치: 기대 {self.dimension}, 실제 {got}"
                )
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._ensure_model()
        vecs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [v.tolist() for v in vecs]

    def embed_query(self, text: str) -> list[float]:
        model = self._ensure_model()
        vec = model.encode([text], normalize_embeddings=True, show_progress_bar=False)[0]
        return vec.tolist()


def get_backend(embedding_model: str = "kure") -> EmbeddingBackend:
    """임베딩 모델명 → 백엔드. (kure | bge_m3)"""
    if embedding_model not in HF_ID_BY_MODEL:
        raise ValueError(
            f"알 수 없는 임베딩 모델: {embedding_model} (가능: {list(HF_ID_BY_MODEL)})"
        )
    return _SentenceTransformerBackend(embedding_model)
