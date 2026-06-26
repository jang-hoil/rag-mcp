"""데이터 모델 (pydantic). 모듈 간 계약.

스펙 §5(검색 결과 스키마), §6.9(manifest 상태)를 따른다.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    """색인 단위. 표는 atomic 청크(절대 중간 분할 금지 — 스펙 §6.3)."""

    chunk_id: str
    document_id: str
    text: str
    page: Optional[int] = None
    heading_path: list[str] = Field(default_factory=list)

    # 메타 플래그 (스펙 §6.2)
    is_table: bool = False
    has_amount: bool = False
    has_code: bool = False

    # 이미지 폴백 (스펙 §7.2)
    needs_image: bool = False
    page_image: Optional[str] = None

    # 문서 메타 (payload에 함께 저장)
    fiscal_year: Optional[int] = None
    doc_name: Optional[str] = None
    source_path: Optional[str] = None

    # 사용자 지정 추가 메타데이터 (부서·작성자·분류 등). 예약 필드 충돌 방지 위해 중첩 dict로 격리.
    # Qdrant는 meta.<key> 형태로 필터 가능 → 검색 정확도용 메타 필터 지원.
    meta: dict[str, Any] = Field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        """Qdrant payload용 dict (스펙 §6.7)."""
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "text": self.text,
            "page": self.page,
            "heading_path": self.heading_path,
            "is_table": self.is_table,
            "has_amount": self.has_amount,
            "has_code": self.has_code,
            "needs_image": self.needs_image,
            "page_image": self.page_image,
            "fiscal_year": self.fiscal_year,
            "doc_name": self.doc_name,
            "source_path": self.source_path,
            "meta": self.meta,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "Chunk":
        return cls(**{k: v for k, v in payload.items() if k in cls.model_fields})


class SearchSource(BaseModel):
    """검색 결과의 source 블록 (스펙 §5 스키마)."""

    document_id: str
    doc_name: Optional[str] = None
    fiscal_year: Optional[int] = None
    source_path: Optional[str] = None
    page: Optional[int] = None
    heading_path: list[str] = Field(default_factory=list)
    is_table: bool = False
    has_amount: bool = False
    needs_image: bool = False
    page_image: Optional[str] = None
    meta: dict[str, Any] = Field(default_factory=dict)


class SearchResult(BaseModel):
    """search_documents 결과 항목 (스펙 §5 스키마)."""

    chunk_id: str
    text: str
    score: float
    matched_by: list[str] = Field(default_factory=list)  # ["dense", "sparse"]
    source: SearchSource


class DocMeta(BaseModel):
    """list_documents 항목."""

    document_id: str
    doc_name: Optional[str] = None
    fiscal_year: Optional[int] = None
    source_path: Optional[str] = None
    num_chunks: int = 0
    embedding_model: str = "kure"
    status: str = "unknown"


ManifestStatus = Literal["parsing", "parsed", "embedded", "done", "error"]


class Manifest(BaseModel):
    """색인 상태/멱등 (스펙 §6.9). 쓰기 순서: parsing→parsed→embedded→done."""

    document_id: str
    status: ManifestStatus = "parsing"
    doc_name: Optional[str] = None
    fiscal_year: Optional[int] = None
    source_path: Optional[str] = None
    embedding_model: str = "kure"
    num_chunks: int = 0
    parsed_dir: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
