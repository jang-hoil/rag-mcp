"""색인 오케스트레이션 (ingest / reindex). 스펙 §6.9, §7.4.

쓰기 순서: parsed 청크 저장(chunks.jsonl) → 임베딩 → Qdrant upsert(dense+sparse) → manifest done.
멱등: 같은 document_id 재실행 시 기존 포인트 삭제 후 재삽입.
reindex(reparse=False): 기존 chunks.jsonl 재사용 → 재임베딩·재upsert (PDF 불요).
"""
from __future__ import annotations

import json
from pathlib import Path

from .config import Config
from .embeddings import EmbeddingBackend, get_backend
from .manifest import ManifestStore
from .models import Chunk, Manifest
from .vector_store import VectorStore


class Indexer:
    def __init__(
        self,
        config: Config,
        embedding_model: str = "kure",
        backend: EmbeddingBackend | None = None,
        store: VectorStore | None = None,
    ):
        self.config = config
        self.embedding_model = embedding_model
        self._backend = backend
        self.store = store or VectorStore(config, embedding_model)
        self.manifests = ManifestStore(config)

    @property
    def backend(self) -> EmbeddingBackend:
        if self._backend is None:
            self._backend = get_backend(self.embedding_model)
        return self._backend

    def _chunks_path(self, document_id: str) -> Path:
        return self.config.parsed_doc_dir(document_id) / "chunks.jsonl"

    def save_chunks(self, document_id: str, chunks: list[Chunk]) -> Path:
        d = self.config.parsed_doc_dir(document_id)
        d.mkdir(parents=True, exist_ok=True)
        p = self._chunks_path(document_id)
        with p.open("w", encoding="utf-8") as f:
            for c in chunks:
                f.write(c.model_dump_json() + "\n")
        return p

    def load_chunks(self, document_id: str) -> list[Chunk]:
        p = self._chunks_path(document_id)
        if not p.exists():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(Chunk.model_validate_json(line))
        return out

    def index_chunks(
        self,
        document_id: str,
        chunks: list[Chunk],
        doc_name: str | None = None,
        fiscal_year: int | None = None,
        source_path: str | None = None,
    ) -> Manifest:
        """청크 리스트를 색인 (parsed 청크는 이미 추출된 상태로 전달받음)."""
        self.manifests.update(
            document_id, status="parsed", doc_name=doc_name, fiscal_year=fiscal_year,
            source_path=source_path, embedding_model=self.embedding_model,
            parsed_dir=str(self.config.parsed_doc_dir(document_id)),
        )
        self.save_chunks(document_id, chunks)

        # 멱등: 기존 포인트 삭제 후 재삽입
        self.store.delete_document(document_id)
        vecs = self.backend.embed_documents([c.text for c in chunks])
        self.manifests.update(document_id, status="embedded", num_chunks=len(chunks))
        self.store.upsert_chunks(chunks, vecs)
        return self.manifests.update(document_id, status="done", num_chunks=len(chunks))

    def reindex_document(self, document_id: str, reparse: bool = False) -> dict:
        """기존 parsed 청크로 재색인. reparse=True는 파서 재실행(마일스톤2~3 연결 지점)."""
        m = self.manifests.read(document_id)
        if m is None:
            return {"ok": False, "error": f"매니페스트 없음: {document_id}"}
        if reparse:
            return {"ok": False, "error": "reparse=True는 pdf_parser 연결 후 지원(마일스톤2~3)"}
        chunks = self.load_chunks(document_id)
        if not chunks:
            return {"ok": False, "error": f"저장된 청크 없음(parsed 재사용 불가): {document_id}"}
        self.index_chunks(
            document_id, chunks, doc_name=m.doc_name, fiscal_year=m.fiscal_year,
            source_path=m.source_path,
        )
        return {"ok": True, "document_id": document_id, "num_chunks": len(chunks), "reparse": False}

    def delete_document(self, document_id: str) -> dict:
        self.store.delete_document(document_id)
        self.manifests.delete(document_id)
        return {"ok": True, "document_id": document_id}
