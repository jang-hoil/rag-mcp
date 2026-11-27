# 진행 상태 (PROGRESS) — 매 세션 시작 시 먼저 읽기

## 2026-11-27 — 문서 P RAG 등록

- 원본 `<원본 경로>\문서 P.pdf`를 MCP 색인 완료: 문서 ID `문서 P`, 2021년, KURE 1,013청크. 기존 13문서에 추가하여 문서 목록 14건.
- 실제 MCP 검색으로 본문 주요 항목(PDF 98·182·203·211·350·351쪽)을 확인. `get_page_image` 98·350쪽 반환 확인. 자동 OCR이 적용된 일부 페이지의 검색결과에는 인식 오류가 섞여 있어 표·이미지와 수치는 원본 확인 필요. 시스템 화면과 법령 설명은 2021년판 기준이다.

## 2026-11-27 — 문서 P 색인목록

- `<원본 경로>\문서 P_색인목록.md` 작성. 원본 432쪽, 원문 목차 PDF 5~19쪽의 항목을 정리했다. 인쇄 쪽수와 PDF 쪽수를 함께 기재했으며 본문은 PDF 쪽수 = 인쇄 쪽수 + 20. 주요 항목 12곳을 원문 페이지로 대조했다.
- 목록 작성 당시 이 원본은 RAG에 등록되지 않았으나, 이후 같은 날 위 기록과 같이 등록 완료했다. 재현 스크립트는 `<작업 폴더>`에 보관. 2021년판 자료이므로 현재 법령·시스템 화면과 차이가 있을 수 있다.

## 2026-11-26 — 문서 Q OCR·표 검색 개선

- 기존 문서 ID `문서 Q_OCR_검색용`을 유지하고 `<원본 경로>\문서 Q_검색개선.pdf`로 MCP 재색인 완료: KURE 104청크, 2021년. 문서 목록 13건·총 4,497청크. 기존의 Tesseract 검색용 PDF와 원본도 보존.
- Windows 로컬 한국어 OCR 65쪽 처리. 7·12·61쪽 표를 원본 대조하여 행과 열을 서술하고, 15·60쪽 흐름도와 13·41·57쪽 본문도 원본을 대조해 교정했다. 62~64쪽 연혁표는 행·열을 분리해 자동 OCR하되 일부 날짜와 숫자만 대조했으므로 여전히 원본 확인 필요. 다른 쪽도 오인식·표 배열 오류가 남아 있다.
- 격리 파싱에서 65쪽 입력 중 59쪽에 104개 본문 청크 생성(나머지 표지·구분지). 검토한 11쪽 텍스트 보존율은 입력 OCR 대비 100%(OCR 원본 정확도 수치가 아님). MCP 검색에서 12·61·41·13쪽 항목 확인. 매니페스트를 원본 PDF에 재연결하고 원본 이미지 12·61쪽 생성 확인. 검색결과 `source_path`는 화면에 표시 가능한 개선 검색용 PDF이며, 이미지 도구는 원본을 사용.
- 재생성 스크립트와 Windows OCR·원문 대조 보완자료는 `<작업 폴더>`에 보관. `reparse=True`는 매니페스트의 스캔 원본을 읽으므로 그대로 실행하지 말 것. 코드 구현은 변경하지 않음.

## 2026-11-26 — 문서 Q 색인목록

- `<원본 경로>\문서 Q_색인목록.md` 작성. 원본은 2021년 11월판, 이미지형 PDF 65쪽이며 목차와 실제 PDF 쪽수가 같다. 목차·본문 시작 쪽 및 참고자료를 이미지로 대조하고 2021년판 위치 안내임을 명시했다.
- 후속 요청으로 2026-11-26 RAG 등록 완료: `문서 Q_OCR_검색용`, 2021년, KURE, 107청크. 기존 12문서·4,393청크와 함께 총 13문서·4,500청크. 원본은 OCR이 없는 스캔본이어서 Tesseract kor+eng 결과를 쪽별 평문으로 만든 `<원본 경로>\문서 Q_OCR_검색용.pdf`를 ingest했다. 이미지 사본 `문서 Q_OCR.pdf`와 원본도 보존.
- 검증: 65쪽 대조, 별도 파싱에서 107청크 및 표본 6·13·41·55쪽 텍스트 보존 확인. MCP 검색으로 41·57·61쪽 항목 검색 확인. `set_source_path`로 매니페스트를 65쪽 원본 PDF에 연결하고 `get_page_image` 41쪽 실제 렌더 확인.
- 운영 주의: 검색 결과의 청크 `source_path`는 여전히 화면이 빈 검색용 PDF를 가리킨다. `get_page_image`는 매니페스트의 원본을 사용한다. OCR 오인식과 표 순서 오류가 있으므로 반드시 원문 확인. `reparse=True`는 원본 스캔 PDF를 다시 읽어 OCR 검색 결과를 잃을 수 있으므로 그대로 실행하지 말 것. 검색용 PDF와 재현 스크립트는 `<작업 폴더>`에 보존. 파서의 중첩 list kids 본문 누락도 격리 시험에서 확인했으며 검색용 평문 입력으로 회피했다.

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
- 서버 로그: `data/logs/server.log`(줄마다 시각, 기동 시에만 `RAG_LOG_MAX_BYTES` 초과분 회전 — 0이면 끔, 보관 `RAG_LOG_BACKUPS`개). 여러 인스턴스가 같은 파일에 이어 쓰며, 쓰기 실패는 삼킨다.
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
- `page_images.py`(get_page_image 캐시·렌더·PNG 변환) · `mutation_lock.py`(쓰기 잠금 MutationGuard·OS 파일락)
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
