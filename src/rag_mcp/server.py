"""FastMCP 진입점 — §5 도구 등록. 스펙 §6.10.

도구는 service.RagService에 위임한다(로직 단일 출처). 입력 검증·실패 응답은 service가 담당.
실행: `uv run rag-mcp serve` (cli.py) 또는 `python -m rag_mcp.server`.

동시성: FastMCP는 동기 도구 함수를 이벤트 루프에서 직접 실행한다(func_metadata). 무거운
service 호출이 루프를 점유하면 다른 도구 호출(list_documents 등)까지 전부 대기·타임아웃한다.
→ 모든 도구를 async로 두고 `anyio.to_thread`로 워커 스레드에 오프로딩해 루프를 비운다.
색인은 submit_ingest로 백그라운드 job화되어 즉시 job_id를 반환한다(ingest_status로 폴링).
"""
from __future__ import annotations

from functools import partial

import anyio
from mcp.server.fastmcp import FastMCP

from .service import RagService

mcp = FastMCP("rag-mcp")
_service: RagService | None = None


def service() -> RagService:
    global _service
    if _service is None:
        _service = RagService()
    return _service


async def _run(fn, *args, **kwargs):
    """블로킹 service 호출을 워커 스레드에서 실행(이벤트 루프 비점유)."""
    return await anyio.to_thread.run_sync(partial(fn, *args, **kwargs))


@mcp.tool()
async def search_documents(
    query: str, top_k: int = 8, search_mode: str = "hybrid", embedding_model: str = "kure",
    fusion: str = "rrf", fiscal_year: int | None = None, filters: dict | None = None,
) -> list[dict]:
    """회계·예산 지침서 하이브리드 검색. embedding_model이 컬렉션 결정.
    fiscal_year로 해당 연도만 검색. 결과가 needs_image면 page_image 경로 포함."""
    return await _run(
        service().search_documents, query, top_k=top_k, search_mode=search_mode,
        embedding_model=embedding_model, fusion=fusion, fiscal_year=fiscal_year, filters=filters,
    )


@mcp.tool()
async def ingest_pdf(
    path: str, document_id: str | None = None, fiscal_year: int | None = None,
    doc_name: str | None = None, metadata: dict | None = None, embedding_model: str = "kure",
) -> dict:
    """PDF 색인을 백그라운드로 시작하고 즉시 job_id를 반환(비블로킹).

    큰 PDF는 임베딩에만 수 분이 걸려 동기 색인 시 클라이언트가 타임아웃된다. 진행 상황은
    ingest_status(job_id)로 폴링한다(status: running|done|error). 동시 색인은 1개만 허용.
    fiscal_year/doc_name 미지정 시 파일명·표지에서 자동 추출 시도. metadata(부서·작성자 등)는
    검색결과에 표시되고 meta.<키> 필터로 검색을 좁힐 수 있다."""
    return await _run(
        service().submit_ingest, path, document_id=document_id, fiscal_year=fiscal_year,
        doc_name=doc_name, metadata=metadata, embedding_model=embedding_model,
    )


@mcp.tool()
async def ingest_status(job_id: str) -> dict:
    """ingest_pdf로 시작한 백그라운드 색인 작업의 진행 상태(running|done|error)."""
    return await _run(service().ingest_status, job_id)


@mcp.tool()
async def get_chunk(chunk_id: str, embedding_model: str = "kure") -> dict:
    """chunk_id로 단건 조회."""
    return await _run(service().get_chunk, chunk_id, embedding_model=embedding_model)


@mcp.tool()
async def list_documents() -> list[dict]:
    """색인된 문서 목록(연도·청크수·상태)."""
    return await _run(service().list_documents)


@mcp.tool()
async def delete_document(document_id: str, confirm: bool = False) -> dict:
    """문서 삭제. confirm=True만 실제 실행."""
    return await _run(service().delete_document, document_id, confirm=confirm)


@mcp.tool()
async def reindex_document(document_id: str, reparse: bool = False) -> dict:
    """재색인. 기본은 기존 parsed 청크 재사용(reparse=False)."""
    return await _run(service().reindex_document, document_id, reparse=reparse)


@mcp.tool()
async def collection_status() -> dict:
    """컬렉션·연도별 문서수·차원·sparse 여부."""
    return await _run(service().collection_status)


@mcp.tool()
async def review_before_ingest(pdf_path: str) -> dict:
    """새 PDF 색인 전 검토(읽기 전용). 들어올 문서 id/연도(파일명 기반)와 현재 색인된 전체
    목록을 함께 반환한다. 삭제·색인·시리즈 매칭을 하지 않는다 — 어떤 구버전을 지울지는 사람이 판단."""
    return await _run(service().review_before_ingest, pdf_path)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
