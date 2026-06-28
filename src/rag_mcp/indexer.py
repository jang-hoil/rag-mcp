"""색인 오케스트레이션 (ingest / reindex). 스펙 §6.9, §7.4.

쓰기 순서: parsed 청크 저장(chunks.jsonl) → 임베딩 → Qdrant upsert(dense+sparse) → manifest done.
멱등: 같은 document_id 재실행 시 기존 포인트 삭제 후 재삽입.
reindex(reparse=False): 기존 chunks.jsonl 재사용 → 재임베딩·재upsert (PDF 불요).
"""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from .config import Config
from .embeddings import EmbeddingBackend, get_backend
from .manifest import ManifestStore
from .models import Chunk, Manifest
from .request_models import DocumentMetadata, JsonValue
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
        metadata: Mapping[str, JsonValue] | DocumentMetadata | None = None,
    ) -> Manifest:
        """청크 리스트를 색인 (parsed 청크는 이미 추출된 상태로 전달받음).

        metadata: 문서 단위 추가 메타. 각 청크 meta에 병합되어 payload·검색결과·필터에 반영.
        """
        parsed_metadata = DocumentMetadata.from_raw(metadata)
        metadata_payload = parsed_metadata.to_payload()
        if metadata_payload:
            for c in chunks:
                c.meta = {**c.meta, **metadata_payload}
        manifest_fields = dict(
            status="parsed", doc_name=doc_name, fiscal_year=fiscal_year,
            source_path=source_path, embedding_model=self.embedding_model,
            parsed_dir=str(self.config.parsed_doc_dir(document_id)),
        )
        if metadata_payload:
            manifest_fields["meta"] = metadata_payload  # reparse 시 복원용으로 manifest에 보존
        self.manifests.update(document_id, **manifest_fields)
        self.save_chunks(document_id, chunks)

        # 실패 안전: 임베딩을 먼저 수행(여기서 실패해도 기존 색인은 보존).
        # 임베딩 성공 후에야 기존 포인트 삭제 → 재삽입(멱등). 삭제~삽입 창은 로컬 동기라 짧다.
        try:
            vecs = self.backend.embed_documents([c.text for c in chunks])
            self.manifests.update(document_id, status="embedded", num_chunks=len(chunks))
            self.store.delete_document(document_id)
            self.store.upsert_chunks(chunks, vecs)
        except Exception:
            # 색인 실패를 manifest에 남겨 추적 가능하게(기존 데이터는 위에서 보존됨)
            self.manifests.update(document_id, status="error")
            raise
        return self.manifests.update(document_id, status="done", num_chunks=len(chunks))

    def reindex_document(self, document_id: str, reparse: bool = False) -> dict:
        """기존 parsed 청크로 재색인. reparse=True는 파서 재실행(마일스톤2~3 연결 지점)."""
        m = self.manifests.read(document_id)
        if m is None:
            return {"ok": False, "error": f"매니페스트 없음: {document_id}"}

        if reparse:
            # PDF 재파싱: 원본 경로가 manifest에 있어야 하고 파일이 실제로 존재해야 함
            if not m.source_path or not Path(m.source_path).exists():
                return {"ok": False, "error": f"원본 PDF 없음(reparse 불가): {m.source_path}"}
            from .pipeline import parse_and_chunk

            chunks, meta = parse_and_chunk(
                m.source_path, document_id, self.config,
                fiscal_year=m.fiscal_year, doc_name=m.doc_name,
            )
            # 사용자 metadata는 PDF에 없으므로 manifest에 보존된 값을 복원(없으면 None)
            self.index_chunks(
                document_id, chunks, doc_name=meta.get("doc_name", m.doc_name),
                fiscal_year=meta.get("fiscal_year", m.fiscal_year),
                source_path=m.source_path, metadata=(m.meta or None),
            )
            return {"ok": True, "document_id": document_id, "num_chunks": len(chunks), "reparse": True}

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
