"""FastMCP 진입점 — §5 도구 7개 등록. 스펙 §6.10.

도구는 service.RagService에 위임한다(로직 단일 출처). 입력 검증·실패 응답은 service가 담당.
실행: `uv run rag-mcp serve` (cli.py) 또는 `python -m rag_mcp.server`.
"""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .service import RagService

mcp = FastMCP("rag-mcp")
_service: RagService | None = None


def service() -> RagService:
    global _service
    if _service is None:
        _service = RagService()
    return _service


@mcp.tool()
def search_documents(
    query: str, top_k: int = 8, search_mode: str = "hybrid", embedding_model: str = "kure",
    fusion: str = "rrf", fiscal_year: int | None = None, filters: dict | None = None,
) -> list[dict]:
    """회계·예산 지침서 하이브리드 검색. embedding_model이 컬렉션 결정.
    fiscal_year로 해당 연도만 검색. 결과가 needs_image면 page_image 경로 포함."""
    return service().search_documents(
        query, top_k=top_k, search_mode=search_mode, embedding_model=embedding_model,
        fusion=fusion, fiscal_year=fiscal_year, filters=filters,
    )


@mcp.tool()
def ingest_pdf(
    path: str, document_id: str | None = None, fiscal_year: int | None = None,
    doc_name: str | None = None, metadata: dict | None = None, embedding_model: str = "kure",
) -> dict:
    """PDF를 색인. fiscal_year/doc_name 미지정 시 파일명·표지에서 자동 추출 시도."""
    return service().ingest_pdf(
        path, document_id=document_id, fiscal_year=fiscal_year, doc_name=doc_name,
        metadata=metadata, embedding_model=embedding_model,
    )


@mcp.tool()
def get_chunk(chunk_id: str, embedding_model: str = "kure") -> dict:
    """chunk_id로 단건 조회."""
    return service().get_chunk(chunk_id, embedding_model=embedding_model)


@mcp.tool()
def list_documents() -> list[dict]:
    """색인된 문서 목록(연도·청크수·상태)."""
    return service().list_documents()


@mcp.tool()
def delete_document(document_id: str, confirm: bool = False) -> dict:
    """문서 삭제. confirm=True만 실제 실행."""
    return service().delete_document(document_id, confirm=confirm)


@mcp.tool()
def reindex_document(document_id: str, reparse: bool = False) -> dict:
    """재색인. 기본은 기존 parsed 청크 재사용(reparse=False)."""
    return service().reindex_document(document_id, reparse=reparse)


@mcp.tool()
def collection_status() -> dict:
    """컬렉션·연도별 문서수·차원·sparse 여부."""
    return service().collection_status()


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
