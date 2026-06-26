"""RAG 서비스 계층 — MCP 도구 7개의 비즈니스 로직. 스펙 §5.

server.py(FastMCP)·cli.py·테스트가 공통으로 사용한다(MCP 프레임워크 비의존).
embedding_model이 컬렉션을 결정(저장·검색 모델 일치 강제 — 스펙 §11).
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from .config import Config, load_config
from .embeddings import EmbeddingBackend
from .indexer import Indexer
from .jobs import JobStore
from .manifest import ManifestStore
from .models import Chunk
from .retrieval import Retriever
from .vector_store import VectorStore

# 검색 상한 (MCP는 LLM이 인자를 채우므로 과도한 limit 방어)
_MAX_TOP_K = 100
# filters로 허용하는 payload 키 (임의 키 주입 방지 — Chunk.payload 필드 중 필터 의미 있는 것)
_ALLOWED_FILTER_KEYS = frozenset(
    {"fiscal_year", "document_id", "doc_name", "is_table", "has_amount", "has_code", "needs_image", "page"}
)


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
        # 비동기 ingest: 백그라운드 job 추적 + 동시 ingest 1개 제한(Qdrant local 단일 writer 전제)
        self.jobs = JobStore()
        self._submit_lock = threading.Lock()

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
        # bool은 int 서브클래스라 True/False가 1/0으로 새는 것을 명시 차단
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not (1 <= top_k <= _MAX_TOP_K):
            raise ValueError(f"top_k는 1~{_MAX_TOP_K} 정수: {top_k!r}")
        if filters:
            # 예약 필드 또는 사용자 메타(meta.<key>)만 허용 — 임의 payload 키 주입 방지
            unknown = {k for k in filters if k not in _ALLOWED_FILTER_KEYS and not k.startswith("meta.")}
            if unknown:
                raise ValueError(
                    f"허용되지 않은 필터 키: {sorted(unknown)} "
                    f"(허용: {sorted(_ALLOWED_FILTER_KEYS)} 또는 meta.<키>)"
                )
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
        metadata: dict | None = None, embedding_model: str = "kure",
    ) -> dict:
        """이미 추출된 청크를 색인 (파서 파이프라인의 종단·테스트용 진입점)."""
        m = self._indexer(embedding_model).index_chunks(
            document_id, chunks, doc_name=doc_name, fiscal_year=fiscal_year,
            source_path=source_path, metadata=metadata,
        )
        return {"ok": True, "document_id": document_id, "num_chunks": m.num_chunks, "status": m.status}

    def ingest_pdf(
        self, path: str, document_id: str | None = None, fiscal_year: int | None = None,
        doc_name: str | None = None, metadata: dict | None = None, embedding_model: str = "kure",
    ) -> dict:
        """PDF 색인. 파서·청킹 파이프라인(마일스톤2~3) 연결 지점.

        metadata: 부서·작성자·분류 등 문서 단위 추가 메타. 검색결과 표시·meta.<키> 필터에 사용.
        """
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
            metadata=metadata, embedding_model=embedding_model,
        )

    def submit_ingest(
        self, path: str, document_id: str | None = None, fiscal_year: int | None = None,
        doc_name: str | None = None, metadata: dict | None = None, embedding_model: str = "kure",
    ) -> dict:
        """PDF 색인을 백그라운드 스레드로 던지고 즉시 job_id를 반환(비블로킹).

        큰 PDF의 동기 색인은 임베딩만으로 수 분이 걸려 MCP 클라이언트 타임아웃을 넘긴다.
        진행 상황은 ingest_status(job_id)로 폴링한다. Qdrant local 단일 writer 전제상
        동시 색인은 1개만 허용한다.
        """
        if not Path(path).exists():
            return {"ok": False, "error": f"PDF 없음: {path}"}
        doc_id = document_id or Path(path).stem
        # 동시 ingest 차단은 검사+생성을 원자적으로(두 호출이 동시에 통과하지 않게)
        with self._submit_lock:
            running = self.jobs.running()
            if running:
                j = running[0]
                return {
                    "ok": False,
                    "error": (f"이미 색인 작업이 진행 중입니다(job_id={j.job_id}, "
                              f"document_id={j.document_id}). ingest_status로 확인하세요."),
                    "job_id": j.job_id,
                }
            job = self.jobs.create(document_id=doc_id)

        def _run() -> None:
            try:
                res = self.ingest_pdf(
                    path, document_id=document_id, fiscal_year=fiscal_year,
                    doc_name=doc_name, metadata=metadata, embedding_model=embedding_model,
                )
                if res.get("ok"):
                    self.jobs.finish(job.job_id, res)
                else:
                    self.jobs.fail(job.job_id, res.get("error", "알 수 없는 색인 오류"))
            except Exception as e:  # 스레드 예외는 삼켜지므로 job에 기록
                self.jobs.fail(job.job_id, f"{type(e).__name__}: {e}")

        threading.Thread(target=_run, name=f"ingest-{doc_id}", daemon=True).start()
        return {"ok": True, "job_id": job.job_id, "status": "running", "document_id": doc_id}

    def ingest_status(self, job_id: str) -> dict:
        """submit_ingest로 시작한 색인 작업의 진행 상태."""
        j = self.jobs.get(job_id)
        if j is None:
            return {
                "ok": False,
                "error": (f"작업 없음: {job_id} (서버 재시작 시 진행 중 작업 정보는 사라집니다. "
                          "list_documents로 색인 결과를 확인하세요.)"),
            }
        return {"ok": True, **j.to_dict()}
