"""RAG 서비스 계층 — MCP 도구 7개의 비즈니스 로직. 스펙 §5.

server.py(FastMCP)·cli.py·테스트가 공통으로 사용한다(MCP 프레임워크 비의존).
embedding_model이 컬렉션을 결정(저장·검색 모델 일치 강제 — 스펙 §11).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .config import Config, load_config
from .embeddings import EmbeddingBackend
from .indexer import Indexer
from .manifest import ManifestStore
from .models import Chunk
from .retrieval import Retriever
from .vector_store import VectorStore


class RagService:
    def __init__(
        self,
        config: Optional[Config] = None,
        backend: Optional[EmbeddingBackend] = None,
    ):
        self.config = config or load_config()
        self._backend = backend  # 테스트 주입(Fake) 또는 None(실모델 lazy)
        self._retrievers: dict[str, Retriever] = {}
        self._indexers: dict[str, Indexer] = {}
        self.manifests = ManifestStore(self.config)

    def _retriever(self, model: str) -> Retriever:
        if model not in self._retrievers:
            store = VectorStore(self.config, model)
            self._retrievers[model] = Retriever(self.config, model, backend=self._backend, store=store)
        return self._retrievers[model]

    def _indexer(self, model: str) -> Indexer:
        if model not in self._indexers:
            store = self._retriever(model).store  # 같은 컬렉션/클라이언트 공유
            self._indexers[model] = Indexer(self.config, model, backend=self._backend, store=store)
        return self._indexers[model]

    # --- 도구 ---
    def search_documents(
        self, query: str, top_k: int = 8, search_mode: str = "hybrid",
        embedding_model: str = "kure", fusion: str = "rrf",
        fiscal_year: int | None = None, filters: dict | None = None,
    ) -> list[dict]:
        if not query or not query.strip():
            return []
        if search_mode not in ("hybrid", "dense", "sparse"):
            raise ValueError(f"search_mode는 hybrid|dense|sparse: {search_mode}")
        results = self._retriever(embedding_model).search(
            query, top_k=top_k, search_mode=search_mode, fusion=fusion,
            fiscal_year=fiscal_year, filters=filters,
        )
        return [r.model_dump() for r in results]

    def get_chunk(self, chunk_id: str, embedding_model: str = "kure") -> dict:
        r = self._retriever(embedding_model).get_chunk(chunk_id)
        if r is None:
            return {"ok": False, "error": f"청크 없음: {chunk_id}"}
        return {"ok": True, **r.model_dump()}

    def list_documents(self) -> list[dict]:
        out = []
        for m in self.manifests.list_all():
            out.append({
                "document_id": m.document_id,
                "doc_name": m.doc_name,
                "fiscal_year": m.fiscal_year,
                "num_chunks": m.num_chunks,
                "status": m.status,
                "embedding_model": m.embedding_model,
            })
        return out

    def delete_document(self, document_id: str, confirm: bool = False) -> dict:
        if not confirm:
            return {"ok": False, "error": "confirm=True 필요(안전 가드)", "document_id": document_id}
        m = self.manifests.read(document_id)
        model = m.embedding_model if m else self.config.embedding_model
        return self._indexer(model).delete_document(document_id)

    def reindex_document(self, document_id: str, reparse: bool = False) -> dict:
        m = self.manifests.read(document_id)
        if m is None:
            return {"ok": False, "error": f"문서 없음: {document_id}"}
        return self._indexer(m.embedding_model).reindex_document(document_id, reparse=reparse)

    def collection_status(self) -> dict:
        docs = self.list_documents()
        by_year: dict[str, int] = {}
        for d in docs:
            key = str(d.get("fiscal_year"))
            by_year[key] = by_year.get(key, 0) + 1
        # 모델별 컬렉션 상태
        collections = {}
        for model in {d.get("embedding_model", "kure") for d in docs} or {"kure"}:
            collections[model] = self._retriever(model).store.status()
        return {
            "documents": len(docs),
            "by_fiscal_year": by_year,
            "collections": collections,
        }

    def ingest_chunks(
        self, document_id: str, chunks: list[Chunk], doc_name: str | None = None,
        fiscal_year: int | None = None, source_path: str | None = None,
        embedding_model: str = "kure",
    ) -> dict:
        """이미 추출된 청크를 색인 (파서 파이프라인의 종단·테스트용 진입점)."""
        m = self._indexer(embedding_model).index_chunks(
            document_id, chunks, doc_name=doc_name, fiscal_year=fiscal_year, source_path=source_path,
        )
        return {"ok": True, "document_id": document_id, "num_chunks": m.num_chunks, "status": m.status}

    def ingest_pdf(
        self, path: str, document_id: str | None = None, fiscal_year: int | None = None,
        doc_name: str | None = None, metadata: dict | None = None, embedding_model: str = "kure",
    ) -> dict:
        """PDF 색인. 파서·청킹 파이프라인(마일스톤2~3) 연결 지점."""
        if not Path(path).exists():
            return {"ok": False, "error": f"PDF 없음: {path}"}
        try:
            from .pipeline import parse_and_chunk  # 마일스톤2~3에서 제공
        except ImportError:
            return {
                "ok": False,
                "error": "PDF 파서 파이프라인 미구현(마일스톤2~3, 샘플 PDF 대기). "
                         "이미 추출된 청크는 ingest_chunks 사용.",
            }
        doc_id = document_id or Path(path).stem
        chunks, meta = parse_and_chunk(path, doc_id, self.config, fiscal_year=fiscal_year, doc_name=doc_name)
        return self.ingest_chunks(
            doc_id, chunks, doc_name=meta.get("doc_name", doc_name),
            fiscal_year=meta.get("fiscal_year", fiscal_year), source_path=path,
            embedding_model=embedding_model,
        )
