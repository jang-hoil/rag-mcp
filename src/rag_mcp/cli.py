"""CLI — ingest / search / status / serve. 스펙 §6.10.

주의: serve(MCP)와 ingest 동시 실행 금지 — Qdrant local path 파일락(스펙 §1.3).
"""
from __future__ import annotations

import argparse
import json
import sys


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rag-mcp", description="회계·예산 지침서 RAG MCP")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ing = sub.add_parser("ingest", help="PDF 색인")
    p_ing.add_argument("pdf")
    p_ing.add_argument("--document-id")
    p_ing.add_argument("--fiscal-year", type=int)
    p_ing.add_argument("--doc-name")
    p_ing.add_argument("--embedding-model", default="kure")

    p_search = sub.add_parser("search", help="검색")
    p_search.add_argument("query")
    p_search.add_argument("--top-k", type=int, default=8)
    p_search.add_argument("--mode", default="hybrid", choices=["hybrid", "dense", "sparse"])
    p_search.add_argument("--fusion", default="rrf", choices=["rrf", "dbsf"])
    p_search.add_argument("--fiscal-year", type=int)
    p_search.add_argument("--embedding-model", default="kure")

    sub.add_parser("status", help="컬렉션 상태")
    sub.add_parser("serve", help="MCP 서버 실행 (ingest와 동시 실행 금지)")

    args = parser.parse_args(argv)

    # serve는 MCP 의존만 로딩
    if args.cmd == "serve":
        from .server import main as serve_main
        print("[rag-mcp] MCP 서버 시작 (ingest 동시 실행 금지 — local 파일락)", file=sys.stderr)
        serve_main()
        return 0

    from .service import RagService
    svc = RagService()

    if args.cmd == "ingest":
        _print(svc.ingest_pdf(
            args.pdf, document_id=args.document_id, fiscal_year=args.fiscal_year,
            doc_name=args.doc_name, embedding_model=args.embedding_model,
        ))
    elif args.cmd == "search":
        _print(svc.search_documents(
            args.query, top_k=args.top_k, search_mode=args.mode, fusion=args.fusion,
            fiscal_year=args.fiscal_year, embedding_model=args.embedding_model,
        ))
    elif args.cmd == "status":
        _print(svc.collection_status())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
