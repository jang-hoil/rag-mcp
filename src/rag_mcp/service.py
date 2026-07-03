"""RAG 서비스 계층 — MCP 도구 7개의 비즈니스 로직. 스펙 §5.

server.py(FastMCP)·cli.py·테스트가 공통으로 사용한다(MCP 프레임워크 비의존).
embedding_model이 컬렉션을 결정(저장·검색 모델 일치 강제 — 스펙 §11).
"""
from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Optional

from .config import Config, load_config
from .embeddings import EmbeddingBackend
from .indexer import Indexer
from .jobs import JobStore
from .manifest import ManifestStore
from .models import Chunk
from .request_models import DocumentMetadata, JsonValue, SearchFilters
from .retrieval import Retriever
from .vector_store import VectorStore

# 검색 상한 (MCP는 LLM이 인자를 채우므로 과도한 limit 방어)
_MAX_TOP_K = 100
# filters로 허용하는 payload 키 (임의 키 주입 방지 — Chunk.payload 필드 중 필터 의미 있는 것)
_ALLOWED_FILTER_KEYS = frozenset(
    {"fiscal_year", "document_id", "doc_name", "is_table", "has_amount", "has_code", "needs_image", "page"}
)

# 파일명에서만 4자리 연도를 자명하게 추출(PDF 내용은 열지 않음). review_before_ingest 전용.
_FILENAME_YEAR_RE = re.compile(r"(?:19|20)\d{2}")


def _fiscal_year_from_filename(stem: str) -> Optional[int]:
    """파일명 stem에서 연도를 추출. 여러 개면 마지막(파일명 끝의 연도 관례). 없으면 None."""
    matches = _FILENAME_YEAR_RE.findall(stem)
    return int(matches[-1]) if matches else None


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
        # retriever/indexer 캐시 빌드 보호. 백그라운드 ingest 스레드와 메인 검색 스레드가
        # 같은 모델의 VectorStore(Qdrant local client)를 동시에 두 개 열어 파일락이 충돌하는 것을 막는다.
        # _indexer가 락을 쥔 채 _retriever를 재호출하므로 재진입 가능한 RLock을 쓴다.
        self._resource_lock = threading.RLock()

    def _retriever(self, model: str) -> Retriever:
        with self._resource_lock:
            if model not in self._retrievers:
                store = VectorStore(self.config, model)
                self._retrievers[model] = Retriever(self.config, model, backend=self._backend, store=store)
            return self._retrievers[model]

    def preflight(self, model: str | None = None) -> None:
        """Qdrant local 저장소를 즉시 열어 단일 인스턴스 여부를 판정한다(락 선점).

        server 기동 초반에 메인 스레드에서 동기 호출한다. 다른 프로세스가 이미 local
        저장소를 쥐고 있으면 VectorStore.__init__가 StorageBusyError를 던지며, server.py는
        이를 받아 중복 인스턴스를 조용히 종료한다. 무거운 임베딩 로드는 하지 않으므로
        (backend는 lazy) 밀리초 단위로 끝나 startup을 막지 않는다. 여기서 캐시된 store는
        이후 warmup·검색이 그대로 재사용한다(같은 Qdrant client 공유 — 중복 open 없음).
        """
        self._retriever(model or self.config.embedding_model)

    def warmup(self, model: str | None = None) -> None:
        """임베딩 모델·토크나이저를 미리 메모리에 올린다(첫 검색의 콜드 스타트 제거).

        서버 기동(serve) 시 호출해 모델 로딩 비용을 startup으로 옮긴다. 이렇게 하면
        첫 search_documents가 모델 로딩(정부망 캐시 로드로 수십 초)으로 MCP 타임아웃되지 않는다.
        캐시되는 retriever의 backend를 그대로 데우므로 이후 실제 검색이 같은 인스턴스를 쓴다.
        """
        model = model or self.config.embedding_model
        # 더미 임베딩으로 SentenceTransformer 실제 로드를 강제(lazy 트리거).
        self._retriever(model).backend.embed_query("워밍업")
        # Kiwi 토크나이저도 첫 호출 시 로딩되므로 함께 데운다(sparse 검색 콜드 스타트 제거).
        from .sparse import to_sparse

        to_sparse("워밍업")

    def _indexer(self, model: str) -> Indexer:
        with self._resource_lock:
            if model not in self._indexers:
                store = self._retriever(model).store  # 같은 컬렉션/클라이언트 공유
                self._indexers[model] = Indexer(self.config, model, backend=self._backend, store=store)
            return self._indexers[model]

    # --- 도구 ---
    def search_documents(
        self, query: str, top_k: int = 8, search_mode: str = "hybrid",
        embedding_model: str = "kure", fusion: str = "rrf",
        fiscal_year: int | None = None, filters: Mapping[str, JsonValue] | SearchFilters | None = None,
    ) -> list[dict]:
        if not query or not query.strip():
            return []
        if search_mode not in ("hybrid", "dense", "sparse"):
            raise ValueError(f"search_mode는 hybrid|dense|sparse: {search_mode}")
        # 오타(예: 'rff')가 조용히 RRF로 동작하지 않게 명시 검증
        if fusion not in ("rrf", "dbsf"):
            raise ValueError(f"fusion은 rrf|dbsf: {fusion}")
        # bool은 int 서브클래스라 True/False가 1/0으로 새는 것을 명시 차단
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not (1 <= top_k <= _MAX_TOP_K):
            raise ValueError(f"top_k는 1~{_MAX_TOP_K} 정수: {top_k!r}")
        parsed_filters = SearchFilters.from_raw(filters)
        parsed_filters.ensure_allowed(_ALLOWED_FILTER_KEYS)
        results = self._retriever(embedding_model).search(
            query, top_k=top_k, search_mode=search_mode, fusion=fusion,
            fiscal_year=fiscal_year, filters=parsed_filters.to_qdrant(),
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

    def review_before_ingest(self, pdf_path: str) -> dict:
        """새 PDF 색인 전 검토용(읽기 전용). 삭제·색인·시리즈 매칭을 일절 하지 않는다.

        들어올 문서 식별자(파일명 stem)와 파일명에서 자명한 연도, 그리고 현재 색인된 전체
        문서 목록을 함께 돌려준다. 어떤 구버전을 지울지는 사람이 목록을 보고 직접 판단한다.
        PDF 내용은 열지 않으며 파일 존재 여부도 확인하지 않는다.
        """
        stem = Path(pdf_path).stem
        return {
            "ok": True,
            "incoming": {
                "document_id": stem,
                "fiscal_year": _fiscal_year_from_filename(stem),
                "source_path": pdf_path,
            },
            "indexed_documents": self.list_documents(),  # 기존 헬퍼 재사용, 가공 없음
            "note": "이 도구는 삭제/색인을 하지 않습니다. 목록을 보고 직접 delete_document → ingest_pdf 하세요.",
        }

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
        metadata: Mapping[str, JsonValue] | DocumentMetadata | None = None, embedding_model: str = "kure",
    ) -> dict:
        """이미 추출된 청크를 색인 (파서 파이프라인의 종단·테스트용 진입점)."""
        m = self._indexer(embedding_model).index_chunks(
            document_id, chunks, doc_name=doc_name, fiscal_year=fiscal_year,
            source_path=source_path, metadata=metadata,
        )
        return {"ok": True, "document_id": document_id, "num_chunks": m.num_chunks, "status": m.status}

    def ingest_pdf(
        self, path: str, document_id: str | None = None, fiscal_year: int | None = None,
        doc_name: str | None = None, metadata: Mapping[str, JsonValue] | DocumentMetadata | None = None, embedding_model: str = "kure",
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
        parsed_metadata = DocumentMetadata.from_raw(metadata)
        metadata_values = dict(parsed_metadata.values)
        if meta.get("ocr"):
            metadata_values["ocr"] = meta["ocr"]
        return self.ingest_chunks(
            doc_id, chunks, doc_name=meta.get("doc_name", doc_name),
            fiscal_year=meta.get("fiscal_year", fiscal_year), source_path=path,
            metadata=DocumentMetadata(values=metadata_values), embedding_model=embedding_model,
        )

    def submit_ingest(
        self, path: str, document_id: str | None = None, fiscal_year: int | None = None,
        doc_name: str | None = None, metadata: Mapping[str, JsonValue] | DocumentMetadata | None = None, embedding_model: str = "kure",
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
