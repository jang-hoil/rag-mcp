"""색인 오케스트레이션 (ingest / reindex). 스펙 §6.9, §7.4.

쓰기 순서: parsed 청크 저장(chunks.jsonl) → 임베딩 → Qdrant upsert(dense+sparse) → manifest done.
멱등: 같은 document_id 재실행 시 기존 포인트 삭제 후 재삽입.
reindex(reparse=False): 기존 chunks.jsonl 재사용 → 재임베딩·재upsert (PDF 불요).
"""
from __future__ import annotations

import os
import math
import warnings
from collections.abc import Mapping
from pathlib import Path
from typing import Callable

from .config import Config
from .embeddings import EmbeddingBackend, get_backend
from .manifest import ManifestStore, resolve_source_path
from .models import Chunk, Manifest
from .request_models import DocumentMetadata, JsonValue
from .sparse import average_document_length
from .vector_store import VectorStore

# (단계, 처리수, 전체수) — 색인 진척 보고 콜백
ProgressCallback = Callable[[str, int, int], None]

# 임베딩을 이 크기로 쪼개 진척 보고 지점을 만든다(모델 내부 배치와는 별개).
EMBED_BATCH_SIZE = 64


class Indexer:
    def __init__(
        self,
        config: Config,
        embedding_model: str = "kure",
        document_id: str | None = None,
        backend: EmbeddingBackend | None = None,
        store: VectorStore | None = None,
    ):
        self.config = config
        self.embedding_model = embedding_model
        if document_id is None:
            raise ValueError("Indexer를 만들려면 document_id가 필요합니다")
        self.document_id = document_id
        self._backend = backend
        self.store = store or VectorStore(config, embedding_model, document_id)
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
        tmp = p.with_suffix(p.suffix + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as stream:
                for chunk in chunks:
                    stream.write(chunk.model_dump_json() + "\n")
            os.replace(tmp, p)
        finally:
            if tmp.exists():
                tmp.unlink()
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
        avgdl: float | None = None,
        bm25_k1: float | None = None,
        bm25_b: float | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> Manifest:
        """청크 리스트를 색인 (parsed 청크는 이미 추출된 상태로 전달받음).

        metadata: 문서 단위 추가 메타. 각 청크 meta에 병합되어 payload·검색결과·필터에 반영.
        on_progress: (단계, 처리수, 전체수) 콜백. 색인이 수 분 걸리는 동안 호출자가
            사용자에게 진척을 보여줄 수 있게 한다. 미지정이면 아무 것도 하지 않는다(CLI 등).
        """
        if document_id != self.document_id:
            raise ValueError(
                f"Indexer document_id({self.document_id})와 요청 document_id({document_id})가 다릅니다"
            )
        if not chunks or any(not chunk.text.strip() for chunk in chunks):
            raise ValueError("색인할 유효 청크가 없습니다")

        parsed_metadata = DocumentMetadata.from_raw(metadata)
        metadata_payload = parsed_metadata.to_payload()
        if metadata_payload:
            for c in chunks:
                c.meta = {**c.meta, **metadata_payload}
        effective_avgdl = (
            average_document_length(chunk.text for chunk in chunks)
            if avgdl is None
            else avgdl
        )
        effective_k1 = self.config.bm25_k1 if bm25_k1 is None else bm25_k1
        effective_b = self.config.bm25_b if bm25_b is None else bm25_b
        manifest_fields = dict(
            status="parsed", doc_name=doc_name, fiscal_year=fiscal_year,
            source_path=source_path, embedding_model=self.embedding_model,
            avgdl=effective_avgdl,
            bm25_k1=effective_k1,
            bm25_b=effective_b,
            parsed_dir=str(self.config.parsed_doc_dir(document_id)),
        )
        if metadata_payload:
            manifest_fields["meta"] = metadata_payload  # reparse 시 복원용으로 manifest에 보존
        staged_chunks = self._stage_chunks(document_id, chunks)

        # 실패 안전: 임베딩을 먼저 수행(여기서 실패해도 기존 색인은 보존).
        # 임베딩 성공 뒤 Qdrant 교체 전체를 store 잠금 안에서 수행한다.
        try:
            vecs = self._embed_chunks(chunks, on_progress)
            if on_progress is not None:
                on_progress("저장", len(chunks), len(chunks))
            self.store.replace_document(
                document_id,
                chunks,
                vecs,
                avgdl=effective_avgdl,
                bm25_k1=effective_k1,
                bm25_b=effective_b,
            )
        except Exception:
            # 색인 실패를 manifest에 남겨 추적 가능하게(기존 데이터는 위에서 보존됨)
            if staged_chunks.exists():
                staged_chunks.unlink()
            raise
        os.replace(staged_chunks, self._chunks_path(document_id))
        committed_fields = {**manifest_fields, "status": "embedded", "num_chunks": len(chunks)}
        self.manifests.update(document_id, **committed_fields)
        return self.manifests.update(document_id, status="done", num_chunks=len(chunks))

    def _embed_chunks(
        self, chunks: list[Chunk], on_progress: ProgressCallback | None
    ) -> list[list[float]]:
        """청크를 배치로 임베딩하며 진척을 보고한다.

        배치로 쪼개는 이유는 진척 보고 지점을 만들기 위해서다. 결과 벡터는 한 번에 넘길
        때와 동일하다(정규화가 텍스트 단위라 배치 경계의 영향이 없음).
        """
        total = len(chunks)
        if on_progress is None:
            return self.backend.embed_documents([c.text for c in chunks])

        on_progress("임베딩", 0, total)
        vecs: list[list[float]] = []
        for start in range(0, total, EMBED_BATCH_SIZE):
            batch = chunks[start:start + EMBED_BATCH_SIZE]
            vecs.extend(self.backend.embed_documents([c.text for c in batch]))
            on_progress("임베딩", len(vecs), total)
        return vecs

    def _stage_chunks(self, document_id: str, chunks: list[Chunk]) -> Path:
        d = self.config.parsed_doc_dir(document_id)
        d.mkdir(parents=True, exist_ok=True)
        tmp = self._chunks_path(document_id).with_suffix(".jsonl.stage")
        try:
            with tmp.open("w", encoding="utf-8") as stream:
                for chunk in chunks:
                    stream.write(chunk.model_dump_json() + "\n")
            return tmp
        except Exception:
            if tmp.exists():
                tmp.unlink()
            raise

    def _bm25_settings_for_reindex(self, manifest: Manifest) -> tuple[float, float]:
        """저장된 BM25 설정을 우선하고 현재 환경과 다르면 경고한다."""
        effective_k1 = (
            self.config.bm25_k1
            if manifest.bm25_k1 is None
            else manifest.bm25_k1
        )
        effective_b = (
            self.config.bm25_b
            if manifest.bm25_b is None
            else manifest.bm25_b
        )
        mismatched = (
            manifest.bm25_k1 is not None
            and not math.isclose(manifest.bm25_k1, self.config.bm25_k1)
        ) or (
            manifest.bm25_b is not None
            and not math.isclose(manifest.bm25_b, self.config.bm25_b)
        )
        if mismatched:
            warnings.warn(
                "매니페스트에 저장된 BM25 설정과 현재 설정이 다릅니다. "
                f"재색인 멱등성을 위해 저장된 값 "
                f"(k1={effective_k1}, b={effective_b})을 사용합니다. "
                f"현재 설정은 k1={self.config.bm25_k1}, b={self.config.bm25_b}입니다.",
                RuntimeWarning,
                stacklevel=2,
            )
        return effective_k1, effective_b

    def reindex_document(self, document_id: str, reparse: bool = False) -> dict:
        """기존 parsed 청크로 재색인. reparse=True는 파서 재실행(마일스톤2~3 연결 지점)."""
        m = self.manifests.read(document_id)
        if m is None:
            return {"ok": False, "error": f"매니페스트 없음: {document_id}"}
        bm25_k1, bm25_b = self._bm25_settings_for_reindex(m)

        if reparse:
            # PDF 재파싱: 원본 경로가 manifest에 있어야 하고 파일이 실제로 존재해야 함.
            # 옛 WSL 경로로 기록됐어도 파일이 제자리면 resolve_source_path가 되살린다.
            source = resolve_source_path(m.source_path)
            if source is None or not source.exists():
                return {"ok": False, "error": f"원본 PDF 없음(reparse 불가): {m.source_path}"}
            from .pipeline import parse_and_chunk

            # force=True: parsed JSON 캐시를 버리고 파서를 실제 재실행(캐시 재사용이면 reparse 무의미)
            chunks, meta = parse_and_chunk(
                str(source), document_id, self.config,
                fiscal_year=m.fiscal_year, doc_name=m.doc_name, force=True,
            )
            # 되살린 경로를 매니페스트에 굳혀 다음부터 변환 없이 바로 열리게 한다.
            self.index_chunks(
                document_id, chunks, doc_name=meta.get("doc_name", m.doc_name),
                fiscal_year=meta.get("fiscal_year", m.fiscal_year),
                source_path=str(source), metadata=(m.meta or None),
                bm25_k1=bm25_k1, bm25_b=bm25_b,
            )
            return {"ok": True, "document_id": document_id, "num_chunks": len(chunks), "reparse": True}

        chunks = self.load_chunks(document_id)
        if not chunks:
            return {"ok": False, "error": f"저장된 청크 없음(parsed 재사용 불가): {document_id}"}
        self.index_chunks(
            document_id, chunks, doc_name=m.doc_name, fiscal_year=m.fiscal_year,
            source_path=m.source_path, avgdl=m.avgdl,
            bm25_k1=bm25_k1, bm25_b=bm25_b,
        )
        return {"ok": True, "document_id": document_id, "num_chunks": len(chunks), "reparse": False}

    def delete_document(self, document_id: str) -> dict:
        self.store.delete_document(document_id)
        self.manifests.delete(document_id)
        return {"ok": True, "document_id": document_id}
