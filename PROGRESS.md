# 진행 상태 (PROGRESS) — 매 세션 시작 시 먼저 읽기

> 현재 상태와 다음 할 일만 둔다. 끝난 작업 기록은 `docs/history/PROGRESS-history.md`로 옮긴다.
> 작업을 멈추기 전에 항상 "현재 상태 / 다음 할 일"을 갱신할 것.

## 재개 방법 (How to resume)
1. 이 파일과 `CLAUDE.md`(또는 `AGENTS.md`), 필요하면 `../RAG_MCP_구현스펙_ClaudeCode.md`(스펙)를 읽는다.
2. `git log --oneline`으로 마지막 커밋을 확인한다.
3. `uv run --extra dev pytest -q`, `uv run --extra dev ruff check .`로 현재 통과 상태를 확인한다.
4. 검증 루프(테스트 먼저 → 구현 → pytest 통과 → 작은 커밋)대로 진행한다.

## 현재 상태 (CURRENT STATE)
- 스펙 §9 마일스톤 0~7 전부 완료. 이후 기능 개선·운영 안정화 단계다.
- MCP 서버: MCP Python SDK v2 `MCPServer`, 도구 **11개**(`search_documents`, `ingest_pdf`,
  `ingest_status`, `get_chunk`, `get_page_image`, `list_documents`, `delete_document`,
  `reindex_document`, `collection_status`, `set_source_path`, `review_before_ingest`).
- 저장소: Qdrant **server 모드**(localhost HTTP) 운영, 문서별 컬렉션 분리. local path 모드도 지원.
- 서버 로그: `data/logs/server.log`(시각 포함, 5MB 회전).
- CI: GitHub Actions(Windows, uv + ruff + pytest — 가짜 임베딩으로 모델 없이 실행).
- 개선점 검토 리포트 반영 진행 중: 코드·문서 항목은 이 브랜치에서, 데이터 정리 항목
  (2·5·6·13번)은 본 저장소에서 별도로 진행.

## 다음 할 일 (NEXT)
- 개선점 검토 리포트 남은 항목 반영 후 사용자 확인 → master 병합 여부 결정.
- 별도 Graph RAG MCP 프로젝트: 계획 문서만 있음(`docs/superpowers/plans/2026-11-06-*`).
  사용자 검토 후 1문서 PoC부터.

## 환경 메모
- Python: uv + 3.11 (`.venv`). 명령: `uv run <...>`
- Java 21 OK. pdftoppm 없음 → pymupdf 폴백.
- OS: Windows 11, 쉘 PowerShell/bash 혼용.

## 구현된 모듈 지도 (참고)
- `config.py` 모델별 컬렉션/차원 · `models.py` Chunk/SearchResult/Manifest
- `tokenizer.py`(코드/금액 보존)+`sparse.py`(blake2b idx, tf, IDF modifier 전제)
- `embeddings.py`(KURE lazy) · `rerank.py`(off|lexical|cross — lexical은 모델 불필요) · `vector_store.py`(dense+sparse, 각 Prefetch.filter)
- `manifest.py`(atomic·멱등) · `indexer.py`(chunks.jsonl·reindex) · `retrieval.py`(search/get_chunk)
- `jobs.py`(비동기 ingest job·JobStore) · `service.py`(도구 11개 로직·submit_ingest/ingest_status·review_before_ingest·get_page_image)
- `server.py`(MCPServer·MCP SDK v2, async+anyio.to_thread) · `cli.py`(동기 ingest 유지)
- 테스트: 모두 FakeEmbeddingBackend(`tests/conftest.py`)로 모델 없이 구동.

## 결정 로그 (DECISIONS)
- 2026-06-18: Python 3.12 부재 → uv+3.11 채택 (torch 호환 안정).
- 2026-06-18: pdftoppm 부재 → page_render는 pymupdf 폴백 우선.
- 2026-06-18: Qdrant local 모드에서 payload 필터는 **각 Prefetch.filter**에 넣어야 동작(top-level query_filter는 융합 후보를 못 거름). retrieval.py 구현 시 이 방식 적용.
- 2026-06-18: payload 인덱스는 local no-op → create_payload_index는 server 모드 대비로만 호출(경고 무시).
- 2026-08-22: MCP Python SDK v1 FastMCP → v2 `MCPServer` 전환(도구·동작 동일).
- 2026-09-03: Qdrant local → server 모드 전환(파일락 없이 여러 MCP 클라이언트 동시 사용).

## 막힌 것 / 미해결 (BLOCKERS)
- 현재 미해결 BLOCKER 없음.
