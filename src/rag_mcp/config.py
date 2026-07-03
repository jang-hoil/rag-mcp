"""설정 (환경변수 기반). 데이터 경로·Qdrant 모드·임베딩 모델을 한 곳에서 관리한다.

스펙 §4 디렉터리 구조, §6.7 컬렉션 모델별 분리를 따른다.
경량 유지를 위해 pydantic-settings 대신 os.environ + 기본값을 사용한다.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


# 임베딩 모델별 Qdrant 컬렉션 (저장·검색 모델 일치 강제 — 스펙 §6.7, §11)
COLLECTION_BY_MODEL: dict[str, str] = {
    "kure": "rag_kure_chunks",
    "bge_m3": "rag_bge_m3_chunks",
}

# 모델별 dense 차원
DIMENSION_BY_MODEL: dict[str, int] = {
    "kure": 1024,
    "bge_m3": 1024,
}

# 모델별 HuggingFace 식별자
HF_ID_BY_MODEL: dict[str, str] = {
    "kure": "nlpai-lab/KURE-v1",
    "bge_m3": "BAAI/bge-m3",
}


def _env(key: str, default: str) -> str:
    val = os.environ.get(key, "").strip()
    return val if val else default


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path(_env("RAG_DATA_DIR", "./data")).resolve())
    qdrant_mode: str = field(default_factory=lambda: _env("RAG_QDRANT_MODE", "local"))
    qdrant_path: Path = field(default_factory=lambda: Path(_env("RAG_QDRANT_PATH", "./data/qdrant")).resolve())
    qdrant_url: str = field(default_factory=lambda: _env("RAG_QDRANT_URL", ""))
    embedding_model: str = field(default_factory=lambda: _env("RAG_EMBEDDING_MODEL", "kure"))
    render_dpi: int = field(default_factory=lambda: int(_env("RAG_RENDER_DPI", "200")))
    # OCR: off | auto(스캔 PDF hybrid 후보·needs_image 구간) | force(needs_image 전부)
    ocr_mode: str = field(default_factory=lambda: _env("RAG_OCR", "auto"))
    ocr_min_chars_per_page: int = field(
        default_factory=lambda: int(_env("RAG_OCR_MIN_CHARS", "30"))
    )
    ocr_lang: str = field(default_factory=lambda: _env("RAG_OCR_LANG", "kor+eng"))
    # 스캔 PDF 문서 단위 OpenDataLoader hybrid (off면 페이지 OCR만; hybrid 서버 별도 기동)
    odl_hybrid: str = field(default_factory=lambda: _env("RAG_ODL_HYBRID", "off"))
    odl_hybrid_url: str = field(default_factory=lambda: _env("RAG_ODL_HYBRID_URL", ""))

    # 파생 경로
    @property
    def parsed_dir(self) -> Path:
        return self.data_dir / "parsed"

    @property
    def manifests_dir(self) -> Path:
        return self.data_dir / "manifests"

    def parsed_doc_dir(self, document_id: str) -> Path:
        return self.parsed_dir / document_id

    def pages_dir(self, document_id: str) -> Path:
        return self.parsed_doc_dir(document_id) / "pages"

    def manifest_path(self, document_id: str) -> Path:
        return self.manifests_dir / f"{document_id}.json"

    def collection_name(self, embedding_model: str | None = None) -> str:
        model = embedding_model or self.embedding_model
        if model not in COLLECTION_BY_MODEL:
            raise ValueError(f"알 수 없는 임베딩 모델: {model} (가능: {list(COLLECTION_BY_MODEL)})")
        return COLLECTION_BY_MODEL[model]

    def dimension(self, embedding_model: str | None = None) -> int:
        model = embedding_model or self.embedding_model
        return DIMENSION_BY_MODEL[model]

def load_config() -> Config:
    return Config()

