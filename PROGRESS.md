# 진행 상태 (PROGRESS) — 매 세션 시작 시 먼저 읽기

> 이 파일은 토큰 소진/세션 종료 후 **다음 날 이어가기** 위한 단일 상태 파일이다.
> 작업을 멈추기 전에 항상 "현재 상태 / 다음 할 일"을 갱신할 것.

## 재개 방법 (How to resume)
1. 이 파일과 `CLAUDE.md`, `../RAG_MCP_구현스펙_ClaudeCode.md`(스펙)를 읽는다.
2. 아래 **마일스톤 체크리스트**에서 `[ ]` 첫 항목이 현재 작업이다.
3. `git log --oneline`으로 마지막 커밋 확인 → 그 다음부터 진행.
4. `uv run pytest -q`로 현재 통과 상태 확인.
5. 스펙 §12.2 검증 루프(테스트 먼저 → 구현 → pytest 통과 → 커밋)대로 진행.

## 환경 메모
- Python: uv + 3.11 (`.venv`). 명령: `uv run <...>`
- Java 21 OK. pdftoppm 없음 → pymupdf 폴백.
- OS: Windows 11, 쉘 PowerShell/bash 혼용.
- 의존성은 마일스톤별 점진 설치 (torch/sentence-transformers는 무거움).

## 마일스톤 체크리스트 (스펙 §9)
- [x] **0. 프로젝트 뼈대** — pyproject, dirs, CLAUDE.md, PROGRESS.md, venv ✅
- [x] **1. Qdrant 스모크 테스트** — dense+sparse RRF + fiscal_year 필터 ✅ (3 passed)
- [ ] **2. pdf_parser** — OpenDataLoader cluster+html, JSON+MD, parsed 영구저장 ⏳ **샘플 PDF 대기**
- [ ] **3. metadata + chunking + page_render** — 표 atomic, 뭉친표 needs_image, PNG 렌더 ⏳ **샘플 PDF 대기**
- [~] **4. tokenizer + sparse + embeddings** — 코드 완료(13 passed). 실 KURE 임베딩만 SSL로 보류
- [x] **5. vector_store + manifest** — dense+sparse upsert, IDF modifier(local 동작), 멱등성 ✅ (6 passed)
- [x] **6. retrieval + indexer** — hybrid+fiscal_year 필터, reindex(reparse=False) ✅ (6 passed)
- [x] **7. service + server(MCP) + cli** — FastMCP 도구 7개 등록 ✅ (9 passed)

> **검색·색인·MCP 파이프라인 전체 완성. 현재 44 passed, 1 skipped.**
> 남은 것: 마일스톤 2~3(PDF→청크 파싱)만. 사용자 샘플 PDF 도착 시 끼우면 end-to-end 완성.
> ingest_pdf 도구는 `pipeline.parse_and_chunk` 연결 지점만 비어 있음(그 외 모두 동작).

## 현재 상태 (CURRENT STATE)
- **검색/색인/MCP 파이프라인 완성. 44 passed, 1 skipped.** 남은 것은 마일스톤 2~3(PDF 파싱)뿐.
- 다음 할 일 (사용자 샘플 PDF 도착 후, 스펙 §6.1~6.4 그대로):
  1. `uv pip install --python .venv "opendataloader-pdf"` (JVM 래퍼, Java 21 OK).
  2. **pdf_parser.py** — convert(format="markdown,json", table_method="cluster", markdown_with_html=True, quiet=True),
     산출물 `data/parsed/{id}/`에 영구저장. 테스트: 정형 표(토지소유 등) 셀 골든 일치, JSON에 rows/cols/span 존재.
  3. **metadata.py** — fiscal_year(`20\d{2}`)·doc_name·heading_path·page; is_table/has_amount/has_code 플래그.
  4. **chunking.py** — JSON 표 격자 재구성(span 반영) → 표 atomic 청크. 본문 과목·항목 단위(긴 것만 800/overlap120).
     뭉친표 탐지(§7.2: 한 셀에 숫자 2개+ 공백분리 등)→needs_image. 페이지 넘김 표 병합.
  5. **page_render.py** — pymupdf `page.get_pixmap(dpi=200)` → `data/parsed/{id}/pages/p{n}.png` (needs_image 페이지만).
  6. **pipeline.py** — `parse_and_chunk(path, doc_id, config, ...) -> (list[Chunk], meta_dict)`.
     service.ingest_pdf가 이 함수를 import해 호출(현재 ImportError 시 안내 응답). 이거 만들면 ingest_pdf 즉시 동작.
  7. 통합: `uv run rag-mcp ingest <pdf>` → `uv run rag-mcp search "일상경비 한도"` end-to-end 확인.
- **실모델 검증(별도 트랙)**: 허용망에서 KURE-v1 받아 HF_HOME 캐시 반입 → `RAG_RUN_MODEL_TESTS=1` 검증.
  그때 FakeEmbeddingBackend로 색인한 데이터는 실벡터로 재색인 필요.

## 구현된 모듈 지도 (참고)
- `config.py` 모델별 컬렉션/차원 · `models.py` Chunk/SearchResult/Manifest
- `tokenizer.py`(코드/금액 보존)+`sparse.py`(blake2b idx, tf, IDF modifier 전제)
- `embeddings.py`(KURE lazy) · `vector_store.py`(dense+sparse, 각 Prefetch.filter)
- `manifest.py`(atomic·멱등) · `indexer.py`(chunks.jsonl·reindex) · `retrieval.py`(search/get_chunk)
- `service.py`(도구 7개 로직) · `server.py`(FastMCP) · `cli.py`
- 테스트: 모두 FakeEmbeddingBackend(`tests/conftest.py`)로 모델 없이 구동.

## 결정 로그 (DECISIONS)
- 2026-06-18: Python 3.12 부재 → uv+3.11 채택 (torch 호환 안정).
- 2026-06-18: pdftoppm 부재 → page_render는 pymupdf 폴백 우선.
- 2026-06-18: Qdrant local 모드에서 payload 필터는 **각 Prefetch.filter**에 넣어야 동작(top-level query_filter는 융합 후보를 못 거름). retrieval.py 구현 시 이 방식 적용.
- 2026-06-18: payload 인덱스는 local no-op → create_payload_index는 server 모드 대비로만 호출(경고 무시).

## 막힌 것 / 미해결 (BLOCKERS)
- **샘플 PDF 대기**: 마일스톤 2~3 진행에 실제 회계지침 PDF 1개 필요. `data/` 등에 두고 경로 알려주면 진행.
- **HF SSL 차단**: 현 환경에서 `huggingface.co` 다운로드가 SSL 자가서명 인증서 체인으로 실패
  (스펙 §8 정부망 시나리오). KURE-v1 실모델 미검증. → 오프라인 캐시 반입 또는 인증서 설정 필요.
  우회: 모든 테스트는 FakeEmbeddingBackend로 통과 중.
