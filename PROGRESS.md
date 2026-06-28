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
- [x] **2. pdf_parser** — OpenDataLoader cluster+html, JSON+MD, parsed 영구저장 ✅
- [x] **3. metadata + chunking + page_render** — 표 atomic, 뭉친표 needs_image, PNG 렌더 ✅
- [x] **4. tokenizer + sparse + embeddings** — 실 KURE 임베딩 동작 확인 ✅ (truststore로 SSL 해결)
- [x] **5. vector_store + manifest** — dense+sparse upsert, IDF modifier(local 동작), 멱등성 ✅ (6 passed)
- [x] **6. retrieval + indexer** — hybrid+fiscal_year 필터, reindex(reparse=False) ✅ (6 passed)
- [x] **7. service + server(MCP) + cli** — FastMCP 도구 7개 등록 ✅ (9 passed)

> **전 마일스톤 완료. 실제 KURE로 end-to-end 검색까지 확인. 62 passed, 1 skipped.**

## 현재 상태 (CURRENT STATE) — 2026-06-24
- **end-to-end 완성 확인.** 실제 KURE-v1로 `예산편성_예산부서.pdf`(180 청크) 재색인 →
  `uv run rag-mcp search "일상경비 한도"` 정상 결과(dense+sparse RRF, heading_path/page 메타 정상).
- **SSL 차단 해결(중요):** 정부망 MITM 프록시의 self-signed 루트 CA를 Python(certifi)이 불신 →
  **`truststore`** 로 Windows 인증서 저장소를 SSL에 주입(`truststore.inject_into_ssl()`)하면 HF 다운로드 성공.
  - `embeddings.py._ensure_model`에 주입 코드 추가(미설치 시 무시). `pyproject.toml`에 `truststore>=0.10.0` 추가.
  - KURE-v1 모델은 `~/.cache/huggingface/hub/models--nlpai-lab--KURE-v1`에 캐시됨.
  - 참고: insane-search 플러그인은 "차단된 공개 웹페이지 읽기"용 → 이 SSL/모델 문제와 무관(검증 완료).
- **Windows 콘솔 인코딩:** cli.py에 `stdout/stderr.reconfigure("utf-8")` 추가(cp949가 ∘ 등 출력 실패하던 것 해결).

### ✅ 완료 (2026-06-25) — truststore SSL/UTF-8 변경 커밋됨
1. **커밋 완료:** `embeddings.py`(truststore 주입), `pyproject.toml`+`uv.lock`(truststore 의존),
   `cli.py`(UTF-8 출력), `AGENTS.md`(Codex 운영규칙) → 단일 커밋.
2. **검증 재확인 OK:** `uv run rag-mcp search "201-01 일반수용비" --top-k 1` → PYTHONIOENCODING 없이
   한글 정상 출력, dense+sparse RRF·메타데이터 정상.
3. **pytest:** 62 passed, 1 skipped 유지 확인.
- **별도 트랙 ✅ 실행 완료(2026-06-25):** `RAG_RUN_MODEL_TESTS=1 uv run pytest tests/test_embeddings.py`
  → 캐시된 KURE-v1로 실모델 임베딩 테스트 **3 passed**(차원 1024·query/doc 정규화 검증).

### ✅ 완료 (2026-06-26) — MCP 클라이언트(Claude Desktop) 연동
- **stdio end-to-end 검증:** mcp 1.28.0 ClientSession으로 서버 spawn →
  `initialize`(rag-mcp) → `tools/list` **7개 노출** → `collection_status` 정상(180 points) 확인.
  (스모크 스크립트는 scratchpad `mcp_smoke.py` — 일회성, 리포에는 미포함)
- **Claude Desktop 등록 완료:** `%APPDATA%\Claude\claude_desktop_config.json`의 `mcpServers`에
  `rag-mcp` 항목만 병합(기존 korean-law/opendart/archhub + preferences 12개 보존). 백업 `.bak` 생성.
  - 형태: `command`= uv.exe **절대경로**, `args`= `["--directory","<프로젝트>","run","rag-mcp","serve"]`.
  - 이유: GUI 앱은 셸 PATH 미상속 → 절대경로 / cwd 미보장 → `--directory` / SSL은 truststore가 처리 → env 불필요.
  - JSON 유효성 파싱 검증 OK.
- **가이드 문서:** 루트 `MCP_연동가이드.md` 작성(등록 절차·검증·파일락 주의·도구 7개·문제해결).
- **남은 확인(사용자 몫):** Claude Desktop 재시작 후 도구 7개 노출·실검색 동작 육안 확인.

### ✅ 완료 (2026-06-26) — Codex 코드리뷰 1차 반영
- **Codex로 src/rag_mcp 리뷰** 수행 후 옥석 검증(스펙 전제 모르는 일반론·1건 오독 제외).
  실제 반영한 3건(테스트 먼저 → 구현 → 66 passed/1 skipped → 커밋):
  1. **top_k 경계 검증**(`service.search_documents`): `1≤top_k≤100` 아니면 ValueError. bool은 int 새는 함정이라 명시 차단.
  2. **필터 allowlist**: `filters` 키를 `_ALLOWED_FILTER_KEYS`로 제한(임의 payload 키 주입 방지).
  3. **죽은 `metadata` 인자 제거**: `ingest_pdf`에서 선언만 되고 안 쓰이던 인자 삭제. → **아래에서 실구현으로 전환됨.**
- 보류(맥락상 부적합/우선순위 낮음): payload-index 예외(=의도된 local no-op), 파일락 코드가드(=운영규칙으로 회피),
  컬렉션 생성 경합(=단일 writer 전제), 책임분리/typed모델(=YAGNI), matched_by 오독 지적.

### ✅ 완료 (2026-06-26) — metadata 실구현 (제거 → 기능화 전환)
- **사용자 요구**: "메타정보가 있어야 정확한 검색" → 죽은 인자 제거(위 3번)를 되돌려 **저장+표시+필터**로 구현.
- **설계**: 예약 payload 필드 충돌 방지 위해 **중첩 dict** `Chunk.meta`로 격리. Qdrant `meta.<키>` 중첩 필터 사용.
  - `models.py`: `Chunk.meta`/`SearchSource.meta` 필드 + `payload()`에 meta 포함.
  - `indexer.index_chunks(metadata=...)`: 문서 단위 메타를 각 청크 meta에 병합.
  - `service.ingest_chunks/ingest_pdf` + `server.ingest_pdf`: metadata 인자 복원·전달.
  - `retrieval._to_result/get_chunk`: 검색결과 `source.meta` 노출.
  - `service.search_documents`: 필터 allowlist에 `meta.<키>` 허용.
- **검증된 사실(중요):** local Qdrant가 **중첩 키 `meta.부서` 필터를 지원**함(test_metadata_filter_narrows_search 통과).
- 테스트 3건 추가, **68 passed/1 skipped**.

### ✅ 완료 (2026-06-26) — 2차 코드리뷰 반영 (데이터 안전 우선)
- 2차 리뷰 검증 후 심각도순 3건 처리(테스트 먼저 → 구현 → 전체 통과 → 커밋):
  1. **색인 실패 안전성**(`indexer.index_chunks`): 재색인 중 임베딩 실패 시 기존 데이터 유실되던 버그.
     순서를 **임베딩 먼저 → 삭제 → 재삽입**으로 변경, 실패 시 `manifest.status="error"`. (커밋 7ad9ea6)
  2. **파일락 가드**(`vector_store`): Qdrant local 이중 오픈 영어 에러 → 친절 한국어 안내.
     Qdrant 자체 락 활용(별도 lock 파일 X). (커밋 c04e4f4)
  3. **스키마 일관성**: `SearchSource.has_code` 누락 노출. (커밋 d00b765)
- **검증된 사실:** Qdrant local 이중 오픈 시 `RuntimeError("...already accessed...")` 발생(친절 변환함).
  `ManifestStatus`에 `error` 추가됨.
- 미처리(우선순위 낮음): matched_by 표기/비용, fusion/model 검증, fiscal_year 이중경로 문서화.

### ✅ 완료 (2026-06-26) — reindex(reparse=True) 연결
- **PDF 재파싱 재색인 구현**(`indexer.reindex_document`): manifest의 `source_path`로 `pipeline.parse_and_chunk`
  재실행 → 새 청크로 재색인. 원본 PDF 없으면 친절 에러.
- **metadata 유실 방지(중요):** reparse는 PDF를 다시 파싱하므로 사용자 수동 metadata(부서 등)가 날아갈 위험 →
  `Manifest.meta` 필드 추가 + `index_chunks`가 metadata를 manifest에 보존 + reparse 시 복원.
- 테스트 2건(source 없음 에러 / 재파싱+meta 복원), **73 passed/1 skipped**.

### ✅ 완료 (2026-06-26) — ingest 비동기 job화 + 동시성 안전(타임아웃 근본 해결)
- **증상**: 314p PDF(칠곡군) MCP `ingest_pdf` 호출이 4분 타임아웃, 뒤이은 `list_documents`도 멈춤.
- **진단(코드 확정)**: FastMCP는 동기 `@mcp.tool()` 함수를 **이벤트 루프에서 직접 실행**
  (`mcp/.../func_metadata.py:95` `return fn(...)`). 색인이 ~10분간 루프 점유 → 모든 도구 대기.
  실제로 색인은 서버에서 끝까지 진행돼 **1067청크 done**(클라이언트만 포기). manifest로 확인됨.
- **부수 발견/정리**: rag-mcp serve 프로세스 **중복 실행 2개+** → Qdrant 단일 락 충돌. 중복 종료함.
- **수정(테스트 먼저 → 81 passed/1 skipped → 커밋)**:
  1. `jobs.py` 신규: `JobStore`(스레드 안전), `submit_ingest`가 백그라운드 스레드로 색인 + 즉시
     `job_id` 반환, `ingest_status`로 폴링. 동시 ingest 1개 제한.
  2. `vector_store.py`: `threading.RLock`로 upsert/delete/query/count/status/retrieve 직렬화
     (QdrantLocal은 thread-safe 아님 — 백그라운드 색인 ↔ 검색 동시 접근 보호). `retrieve_chunk` 추가.
  3. `server.py`: 모든 도구를 **async + `anyio.to_thread`** 오프로딩(이벤트 루프 비점유).
     `ingest_pdf`→`submit_ingest`, `ingest_status` 도구 추가(총 **8개**). `pyproject`에 `anyio` 직접 의존 명시.
- **검증된 사실**: 실데이터(1247 points)로 async 서버 도구 in-process 동작 확인(list/search/status/ingest_status).
  무거운 임베딩은 Qdrant 락을 안 잡으므로 색인 중에도 검색 거의 안 막힘.
- **운영 메모**: CLI `ingest`는 동기 유지(별도 프로세스라 타임아웃 무관). MCP_연동가이드.md 도구 8개·비동기 워크플로우 반영.

### ✅ 완료 (2026-06-28) — review_before_ingest 도구 추가 (도구 8→9개)
- **목적**: 교체(replace-on-update) 워크플로우 — 새 지침 색인 전 현재 색인 목록을 먼저 보고
  사람이 직접 구버전을 골라 삭제하도록 돕는 **읽기 전용** 도구.
- **설계(사용자 확정)**: 정규화·유사도·시리즈 자동매칭 **일절 없음**. PDF 미오픈·파일존재 미확인.
  들어올 문서 id(파일명 stem)+파일명에서만 추출한 연도 + `list_documents()` 전체 목록을 함께 반환.
- **구현(테스트 먼저 → 82 passed/1 skipped → 커밋 `e3570d5`)**:
  1. `service.review_before_ingest(pdf_path)` + 모듈 헬퍼 `_fiscal_year_from_filename`(`(?:19|20)\d{2}`, 마지막 매치).
  2. `server.py`: `@mcp.tool() review_before_ingest` 등록(9번째, 기존 `_run` 위임 패턴).
  3. 테스트 1건(2025·2026 색인 → incoming id/연도 + 두 문서 모두 반환 검증).
- **하드룰 준수**: 삭제·색인·파이프라인 미수정. `list_documents` 헬퍼 재사용(중복 없음).
- **문서 반영(커밋 `985a050`)**: README.md·MCP_연동가이드.md 도구 표 8→9개.

## 구현된 모듈 지도 (참고)
- `config.py` 모델별 컬렉션/차원 · `models.py` Chunk/SearchResult/Manifest
- `tokenizer.py`(코드/금액 보존)+`sparse.py`(blake2b idx, tf, IDF modifier 전제)
- `embeddings.py`(KURE lazy) · `vector_store.py`(dense+sparse, 각 Prefetch.filter)
- `manifest.py`(atomic·멱등) · `indexer.py`(chunks.jsonl·reindex) · `retrieval.py`(search/get_chunk)
- `jobs.py`(비동기 ingest job·JobStore) · `service.py`(도구 9개 로직·submit_ingest/ingest_status·review_before_ingest)
- `server.py`(FastMCP, async+anyio.to_thread) · `cli.py`(동기 ingest 유지)
- 테스트: 모두 FakeEmbeddingBackend(`tests/conftest.py`)로 모델 없이 구동.

## 결정 로그 (DECISIONS)
- 2026-06-18: Python 3.12 부재 → uv+3.11 채택 (torch 호환 안정).
- 2026-06-18: pdftoppm 부재 → page_render는 pymupdf 폴백 우선.
- 2026-06-18: Qdrant local 모드에서 payload 필터는 **각 Prefetch.filter**에 넣어야 동작(top-level query_filter는 융합 후보를 못 거름). retrieval.py 구현 시 이 방식 적용.
- 2026-06-18: payload 인덱스는 local no-op → create_payload_index는 server 모드 대비로만 호출(경고 무시).

## 막힌 것 / 미해결 (BLOCKERS)
- (해결됨) ~~샘플 PDF 대기~~ → `예산편성_예산부서.pdf` 처리 완료.
- (해결됨) ~~HF SSL 차단~~ → `truststore.inject_into_ssl()`로 Windows 인증서 저장소 사용해 해결.
  KURE-v1 캐시 완료, 실모델 end-to-end 검색 확인. (위 CURRENT STATE 참조)
- 현재 미해결 BLOCKER 없음.
