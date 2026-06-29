"""임베딩 백엔드 (KURE / BGE-M3 공통 인터페이스). 스펙 §6.6, §1.3.

  - KURE-v1: nlpai-lab/KURE-v1, BGE-M3 기반, 1024차원, 최대 8192 토큰, dense 전용.
  - 저장·검색 모델 일치 강제 → 컬렉션이 모델로 결정됨 (config.COLLECTION_BY_MODEL).
  - SentenceTransformer는 무겁고 모델 다운로드가 필요하므로 **lazy 로딩**한다.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod

from .config import DIMENSION_BY_MODEL, HF_ID_BY_MODEL


def _apply_offline_env() -> bool:
    """HF 허브 네트워크 조회를 끄고 로컬 캐시만 쓰도록 강제(정부망 MITM 프록시 회피).

    오프라인 모드가 아니면 SentenceTransformer는 캐시가 있어도 모델 구성 파일마다
    huggingface.co에 etag 확인 요청을 보낸다(modules.json·config·weights·tokenizer 등 10여 개).
    프록시에서 각 요청이 수십 초씩 멈추면 검색이 수 분간 블로킹돼 MCP 클라이언트 타임아웃(약 4분)을
    넘긴다. 모델은 최초 1회만 받으면 되므로 기본값을 오프라인으로 둔다.

    최초 다운로드 등 온라인이 필요하면 `RAG_HF_OFFLINE=0`. import 시점에 상수로 고정되는
    huggingface_hub 특성상 sentence_transformers import **이전에** 호출해야 효과가 있다.
    적용 여부를 반환한다.
    """
    if os.environ.get("RAG_HF_OFFLINE", "1").strip() == "0":
        return False
    # 이미 명시 설정된 값은 존중(setdefault)
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    return True


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
            import sys
            import time

            # HF 네트워크 조회 차단(캐시만 사용) — sentence_transformers import 전에 적용해야 한다.
            offline = _apply_offline_env()
            # 정부망 MITM 프록시(self-signed 인증서)에서 HF 다운로드 SSL 검증 실패 우회:
            # Windows 인증서 저장소(프록시 루트 CA 등록됨)를 Python SSL에 주입한다.
            # truststore 미설치 시 무시(오프라인 캐시만으로 동작 가능).
            try:
                import truststore

                truststore.inject_into_ssl()
            except ImportError:
                pass

            from sentence_transformers import SentenceTransformer

            # GPU 없는 환경 확정 → device="cpu" 명시(불필요한 CUDA 초기화 시도 제거).
            print(
                f"[rag-mcp] 임베딩 모델 로딩 시작: {self.name} ({self._hf_id}) "
                f"device=cpu HF오프라인={offline}",
                file=sys.stderr,
                flush=True,
            )
            t0 = time.monotonic()
            self._model = SentenceTransformer(self._hf_id, device="cpu")
            # 차원 검증 (저장·검색 정합 보장)
            got = self._model.get_sentence_embedding_dimension()
            if got != self.dimension:
                raise RuntimeError(
                    f"{self.name} 차원 불일치: 기대 {self.dimension}, 실제 {got}"
                )
            print(
                f"[rag-mcp] 임베딩 모델 로딩 완료: {self.name} ({time.monotonic() - t0:.1f}s)",
                file=sys.stderr,
                flush=True,
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
