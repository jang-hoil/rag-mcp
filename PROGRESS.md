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

## 현재 상태 (CURRENT STATE) — 2026-07-29
- `codex/rag-mcp-safety-hardening` 11커밋을 master에 충돌 없이 머지했다.
- Qdrant 컬렉션을 `rag_<model>_<정규화 document_id>_<blake2b 6자리>` 형식으로
  문서별 분리했다. `search_documents`는 `document_id`가 필수이며, 누락 시 사용 가능한
  ID 목록을 한국어 오류에 포함한다.
- `list_documents`와 `collection_status`는 실제 컬렉션 목록을 기준으로 동작하고,
  `delete_document(confirm=True)`는 해당 문서 컬렉션 전체를 삭제한다.
- `.claude/settings.json` Stop 훅이 `src/rag_mcp/*.py` 변경에 README.md가 빠진 경우
  종료를 차단한다.
- `scripts/migrate_to_per_doc_collections.py` 작성 완료. 기본 dry-run이고 `--apply`에서만
  기존 dense/sparse 벡터를 그대로 복사한다. **스크립트는 아직 실행하지 않았다.**
- 전체 테스트: **152 passed, 8 skipped**.
- 다음 작업: 사용자가 Claude Desktop/MCP 서버를 종료한 뒤 `data/qdrant`를 백업하고,
  마이그레이션 스크립트를 dry-run으로 실행해 669/370/38청크를 확인한 다음 `--apply`.

## 검색 정확도 개선 (CURRENT STATE) — 2026-07-30
- 항목 4(BM25 TF 포화): 문서 sparse는 문서별 `avgdl`과 `k1=1.2`, `b=0.75`로
  TF를 포화하고, 질의 sparse는 raw TF를 유지하도록 분리했다. `avgdl`은 매니페스트에
  저장되어 일반 재색인에서 그대로 복원된다.
- `RAG_BM25_K1`, `RAG_BM25_B`로 계수를 조정할 수 있다.
- 항목 3(관련도 임계값): `RAG_MIN_DENSE_SCORE=0`, `RAG_REL_SCORE_RATIO=0`을
  추가했다. 기본은 비활성이며, dense 절대 게이트는 RRF 점수가 아닌 별도 코사인
  유사도를 사용한다. 상대 낙폭은 1위 융합 점수의 비율로 적용한다.
- 임계값으로 후보가 전부 제거되면 빈 배열 대신 필터 전 후보 수와 적용값을 담은
  한국어 `diagnostic`을 반환한다.
- 항목 2(리랭커): `RAG_RERANKER=off|lexical|cross` 교체 인터페이스와
  `rerank.py`를 추가했다. 기본 `off`는 기존 순서를 유지한다. `lexical`은 Kiwi
  토큰 겹침, heading, 표·금액·코드 의도, 청크 길이를 설정 가능한 가중치로 결합한다.
- `cross`는 `RAG_CROSS_RERANKER_MODEL`의 로컬 경로만 사용하며 모델 부재 시
  자동 다운로드 없이 한국어 오류를 낸다. 기본 경로는 추가 메모리를 사용하지 않는다.
- 항목 1(문장 경계 청킹): 본문 800자를 목표치로 바꾸고 기본 ±160자
  (`RAG_CHUNK_BOUNDARY_WINDOW`) 안의 가장 가까운 한국어 문장·목록 경계에서
  분할한다. 경계가 없으면 기존 문자 절단으로 폴백하며 1.5배 절대 상한을 지킨다.
  overlap도 가능한 경우 문장 경계에서 시작하고, 표 atomic 원칙은 그대로다.
- 저장값이 바뀌지만 이번 작업에서는 **재색인하지 않는다.** 현재 3개 문서 1,077청크는
  기존 sparse 값을 유지하며, 신규 색인부터 새 BM25 값이 적용된다.
- 항목 1(문장 경계 청킹)도 청크 경계를 바꾸므로 완료 뒤 신규 색인과 기존 색인의
  청킹 결과가 다르다. 원본 PDF 2건의 경로가 끊겨 있어 기존 색인은 그대로 뒀으며,
  신규 색인부터 문장 경계 청킹과 BM25 sparse 값이 함께 적용된다.

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

### ✅ 완료 (2026-07-01) — 서버 중복 실행 근본 차단
- **증상(재발)**: Claude Desktop이 `rag-mcp serve`를 동시에 2개 spawn(부모 PID 동일 = Claude Desktop) →
  Qdrant local 파일락 충돌로 색인·검색(쓰기/Qdrant 접근) 멈춤. `list_documents`(manifest만 읽음)는 동작.
- **근본 원인 2가지(코드 확정)**:
  1. `server.main()`이 `warmup()`(모델 수 GB 로드, 10초~수 분)을 **끝낸 뒤에야** `mcp.run()` 호출 →
     그동안 MCP initialize 미응답 → Claude Desktop이 "죽었다" 판단하고 기존 프로세스 남긴 채 추가 spawn.
  2. 나중 인스턴스가 warmup에서 락 에러를 만나도 "워밍업 실패"로 삼키고 **좀비로 생존** → 락 경합 유지.
- **수정(테스트 먼저 → 전체 통과 → 커밋)**:
  1. `vector_store.py`: `StorageBusyError(RuntimeError)` 신규 — 락 충돌을 타입으로 감지(문자열 매칭 대신).
     기존 `except RuntimeError`·`test_local_storage_lock_friendly_error`와 호환(서브클래스).
  2. `service.py`: `RagService.preflight()` — Qdrant local을 즉시 open해 단일 인스턴스 판정(임베딩 로드 없이 빠름).
  3. `server.py`: main()을 **preflight(동기·메인스레드) → StorageBusyError면 exit(0) → warmup은 백그라운드
     스레드 → mcp.run()** 순으로 재작성. startup 비블로킹으로 중복 spawn 트리거 제거 + 중복 시 자진 종료.
- **테스트 2건 추가**(preflight 중복 감지 / main이 busy 시 SystemExit(0)). README 운영·워밍업 서술 갱신.
- **즉시 조치**: 관찰된 중복 트리(나중 것, uv PID 28508) `taskkill /T /F`로 정리 — serve 트리 1개만 남김.

### ✅ 완료 (2026-07-03) — 전체 코드 정독 리뷰 + 버그 5건 수정 (테스트 먼저 → 106 passed/8 skipped)
- **환경 주의**: 이 세션은 WSL(다른 머신, `.git`·`.venv`·`data/` 없음) → **커밋 못 함**.
  원 저장소가 있는 머신에서 이 변경들을 커밋할 것. 테스트는 WSL 스크래치 venv(3.14)로 전체 통과 확인.
- 수정 5건 (심각도순, 각각 회귀 테스트 추가):
  1. **[높음] 페이지 넘김 표 병합 시 앞 페이지 행 유실** (`table_chunking._merge_tables`):
     뒤 표 셀 row number(1부터 재시작)를 오프셋 안 해 격자에서 앞 표 행을 덮어씀 — 실행으로 실증.
     수정: b 셀 row number에 a의 행 수를 더해 재부여(원본 블록 비변형).
     **⚠️ 기존 색인 중 페이지 걸친 표가 있는 문서는 `reindex(reparse=True)` 필요** (색인 보유 머신에서).
  2. **[높음] `reindex(reparse=True)`가 실제 재파싱 안 함** (`indexer.py`): `parse_and_chunk`에
     `force=True` 미전달 → parsed JSON 캐시 재사용. 수정: force=True 배선.
  3. **[중간] 임베딩 모델 중복 로드 경쟁**: 리뷰에서 발견했으나 **원격 0f298a3에서 이미 동일하게
     수정돼 있었음**(싱글톤 락) — 원격 버전 채택, server.py의 낡은 주석("RLock 보호")만 정정.
  4. **[낮음] server 모드 + URL 빈값이 조용히 local 폴백** (`vector_store.py`): ValueError로 명시.
  5. **[낮음] `fusion` 미검증** (`service.py`): rrf|dbsf 외 거부 (기존 "미처리" 항목 해소).
- **미수정·보고만**: qdrant_path가 RAG_DATA_DIR를 안 따라감 / 병합 표 page_image 첫 페이지 고정 /
  `20\d{2}` 연도 오인("2000원") / doctor TimeoutExpired 미포착 / table_grid_text span 미확장(docstring 과장) /
  본문 800자 고정 절단(문장 경계 무시) / sparse tf 비포화(BM25 k1/b 없음) / matched_by dense 항상 표기.

### ✅ 완료 (2026-07-05) — 전체 검증 세션 + 추출 텍스트 정규화(PUA·NUL 제거)
- **환경 주의**: WSL 머신, `~/.venvs/rag-mcp`(무거운 의존성 보유)로 검증. `PYTHONPATH=src`로 소스 실행.
  Claude Desktop의 serve가 `data/qdrant` 락 점유 → **원본 색인을 scratch로 복사**해 CLI 검색 검증(비파괴).
- **철저한 검증 결과(전부 통과)**: pytest 106→115 passed/8 skipped(신규 9), 실모델 임베딩 6 passed.
  goldset recall@1=MRR=1.0. MCP stdio 도구 9개 정상. 엣지케이스(top_k경계·fusion·mode·model·필터allowlist·없는PDF) 방어.
  기존 4문서 1665 points, fresh ingest 재현성(재산등록 38청크 동일, OCR p19/82/84 일치).
- **표/한글 무결성 정밀 스캔(47.2만 자)**: `�`(U+FFFD) **0건** = 한글 손상 없음. JAMO 646은 정상(아래아ㆍ·원형불릿ㅇ).
  표 셀 탭 구조 보존·needs_image 폴백·금액/코드 플래그 정상. 페이지 넘김 표 병합 회귀테스트 통과.
- **수정 1건 (테스트 먼저 → 115 passed → 커밋)**: 추출 텍스트에 ① **PUA 심볼폰트 불릿**(U+F0FC✓·F0A7▪·
  F06D□ 등 ~160자, 2025 온라인용 PDF 2종, text·heading_path에 노출→뷰어 □) ② **NUL 바이트**(U+0000 547개,
  칠곡군 목차/제목) 혼입 발견. `pdf_parser.normalize_text()` 신설(PUA·C0/C1 제어 제거, TAB/LF·한글·금액·코드
  보존, 이중공백 정리) → `cell_text`·`chunking.block_plain_text`에 배선. 실데이터 재빌드로 PUA/NUL **0** 실증
  (칠곡군 857청크·재산등록 38청크). 검색 품질엔 무영향(불릿은 의미없는 마커).
  **⚠️ 기존 색인 4문서는 옛 텍스트 보유 → 정리하려면 `reindex(reparse=True)` 필요**(색인 보유 머신에서).
- **미수정·보고만(이전 항목 유지)**: 검색 관련도 임계값 없음(무관 쿼리도 top_k 반환, 표준 동작) /
  qdrant_path가 RAG_DATA_DIR 미추종 / 본문 800자 고정 절단 / sparse tf 비포화 등.

### ✅ 완료 (2026-07-13) — 안전성 보강 문서 반영
- `RAG_QDRANT_PATH` 미설정 시 `<RAG_DATA_DIR>/qdrant`를 기본 경로로 사용한다.
- `ingest_pdf`는 전달된 PDF를 새로 파싱해 색인하며, 색인 중 변경 작업은 `status="busy"`로 거부한다.
- README와 MCP 연동 가이드의 MCP 도구 수(9개) 및 운영 동작을 갱신했다.

### ✅ 완료 (2026-07-31) — 코덱스 실행 폴더(cwd)에 따라 서버가 죽던 문제 차단
- **증상**: 코덱스 창에서 rag-mcp 도구가 아예 안 보이거나, 색인한 문서가 사라진 것처럼 보임.
- **근본 원인(코덱스 로그로 확정)**: `config.py:56` `data_dir`이 `RAG_DATA_DIR` 미설정 시
  `./data` = **서버가 실행된 폴더 기준 상대경로**. `~/.codex/config.toml`의
  `[mcp_servers.rag_mcp]`에 `cwd`도 `RAG_DATA_DIR`도 없어 코덱스가 서 있던 폴더를 그대로 물려받음.
  2026-07-31 08:43:51 실제로 코덱스 설치 폴더에서 기동 →
  `PermissionError [WinError 5] 'C:\Program Files\WindowsApps\OpenAI.Codex_...\app\data'`
  (`vector_store.py:48` mkdir ← `service.py:173` preflight) → 서버 즉사 →
  코덱스 로그 `MCP server tools unavailable server_name=rag_mcp`.
  - 조용한 실패 경로가 더 위험: 쓰기 가능한 엉뚱한 폴더면 **빈 인덱스를 새로 만들고 에러 없이 동작**.
  - Claude Desktop 설정은 `uv --directory <프로젝트>`로 cwd를 고정해 이 문제가 없었음(코덱스 이관 시 누락).
- **조치**: `~/.codex/config.toml`의 `[mcp_servers.rag_mcp.env]`에
  `RAG_DATA_DIR = 'D:\User\Desktop\rag mcp\data'` 추가(백업 `config.toml.bak-20260731-093922`).
  - 검증: cwd를 `C:\Windows`로 두고 실행 시 미설정=`C:\Windows\data\qdrant`(재현) →
    설정 후 `D:\User\Desktop\rag mcp\data\qdrant`(정상). TOML 파싱·타 MCP 서버 항목 보존 확인.
- **미해결(별건)**: 2026-07-30 17:13 세션 `ingest_pdf` 300초 타임아웃. 그 세션은 경로가 정상이었고
  색인도 완주(1,064청크 done)했으므로 cwd 문제와 무관. `ingest_pdf`는 웜/콜드 서버 모두
  0.04초에 job_id를 반환함을 실측했으므로 **설계가 아니라 응답 배달 실패**. 원인 미확정.
- **(2026-07-31 해결)** 아래 "데이터 경로를 cwd에서 분리" 항목 참조 — 설정 한 줄 의존을 없앴다.

### ✅ 완료 (2026-07-31) — 색인 진행 상황 보고 (ingest_pdf 안내문 + ingest_status 진척도)
- **문제**: `ingest_pdf`는 `job_id`만, `ingest_status`는 `running` 한 단어만 반환 → MCP
  클라이언트가 사용자에게 "파싱 중이고 몇 분 걸린다"를 말할 재료가 없어 침묵하거나 실패로 오해.
- **구현(테스트 먼저 → 203 passed/8 skipped → 커밋)**:
  1. `jobs.py`: `Job`에 `stage`(대기/파싱/임베딩/저장/완료/실패)·`processed`·`total` 추가.
     `JobStore.progress()` 신설(끝난 job은 갱신 안 함). `to_dict()`가 `percent`·
     `elapsed_seconds`·한국어 `message`까지 만들어 준다(클라이언트는 그대로 전달만).
  2. `indexer.py`: `index_chunks(on_progress=...)` 선택 인자. 임베딩을 `EMBED_BATCH_SIZE=64`로
     쪼개 배치마다 보고(벡터 결과는 한 번에 넘길 때와 동일 — 정규화가 텍스트 단위).
     콜백 없으면 기존대로 한 번에 임베딩(CLI 경로 무변경).
  3. `service.py`: `_ingest_pdf_unlocked`/`_ingest_chunks_unlocked`에 `on_progress` 배선,
     파싱 진입 시 단계 보고. `submit_ingest`가 `pages`·`estimated_minutes`·
     `poll_after_seconds`·`next_action`·`message`를 함께 반환.
  4. `server.py`: ingest_pdf/ingest_status docstring에 **"message를 사용자에게 전달하라"** 명시.
- **소요시간 추정**: `_SECONDS_PER_PAGE = 3.0` (2026-07-30 실측 340쪽/17.5분 ≈ 쪽당 3.1초).
  어림값이라 "대략 N분"으로만 제시. 쪽수를 못 읽으면 추정을 생략하되 안내는 반드시 나간다.
- **검증**: 실제 MCP stdio 서버로 `ingest_pdf` **0.14초** 반환 + 안내문 정상, `ingest_status`가
  `stage=파싱` → 진행 보고. 신규 테스트 8건(안내문 3·job 진척 2·indexer 콜백 2·전체 체인 1).
- **주의(테스트 환경)**: pymupdf로 만든 합성 PDF는 OpenDataLoader가 청크를 0개 만든다
  (`parse_and_chunk` 직접 호출로 확인 — 코드 문제 아님). 파서 포함 e2e는 실 PDF가 필요하다.

### ✅ 완료 (2026-07-31) — 300초 무응답 근본 원인 확정: stderr 파이프 블로킹
- **증상(2026-07-30 17:13)**: `ingest_pdf`가 300초 무응답 → 코덱스 타임아웃. 색인 자체는
  뒤에서 완주(1,064청크 done). 직전 `review_before_ingest` 0.23초, 이후 `list_documents`
  0.05초로 **그 호출 하나만** 유실되는 모양.
- **재현 시도(서버 조건)**: 콜드 서버 + 실제 425쪽 PDF + 기존 색인 데이터 복사본으로 실행 →
  `ingest_pdf` **0.24초** 반환, 13분 만에 1,064청크 완주. **재현 안 됨.**
  색인 중 `list_documents` 최대 지연 7.77초(Qdrant 저장 단계의 store 락 대기, 임베딩 중엔 0.03~0.05초).
- **근본 원인(실측으로 확정)**: mcp 저수준 서버가 요청마다
  `logger.info("Processing request of type ...")`를 **이벤트 루프 스레드에서** stderr로 쓴다
  (`mcp/server/lowlevel/server.py:733`, FastMCP 기본 log_level=INFO).
  클라이언트가 stderr를 읽어가지 않으면 파이프 버퍼가 차고 그 write가 블록되며 **서버 전체가 정지**한다.
  - 계량: 기동 79바이트 + 호출당 162바이트 → 25번째 호출에서 누적 약 4,129바이트
    = Windows 파이프 기본 버퍼 4,096바이트. **실제로 25번째 호출에서 무응답 확인.**
  - 대조군(stderr를 읽어가는 클라이언트): 400회 전부 정상(64,879바이트 소비).
  - 2026-07-30 코덱스 로그 DB의 rag-mcp stderr 기록이 17:11~17:47 비어 있는 것과 정합적.
- **수정(테스트 먼저 → 206 passed/9 skipped → 커밋)**:
  1. `server.py`: `FastMCP("rag-mcp", log_level="WARNING")` — 요청마다 나가던 stderr 쓰기 제거.
  2. `server.py`: `redirect_logs_to_file(config)` 신설. preflight **통과 후·mcp.run() 직전**에
     stderr를 `<data_dir>/logs/server.log`로 전환해 파이프를 아예 쓰지 않는다.
     기동 검사 실패는 전환 전이라 클라이언트가 그대로 본다. 파일 못 열면 None 반환 후 stderr 유지.
     `sys.__stderr__`가 None이면 print를 생략(file=None이면 stdout으로 새어 JSON-RPC가 깨짐).
- **회귀 테스트**: `test_server_survives_unread_stderr` — stderr를 한 바이트도 안 읽는 클라이언트로
  60회 호출. 수정 전 25회에서 정지, 수정 후 통과. 무거워서 `RAG_RUN_SERVER_TESTS=1` 게이트.
- **남은 관찰**: Qdrant 저장 단계에서 store 락 때문에 다른 도구가 최대 7.77초 대기(1,064청크 기준).
  현재는 허용 범위지만 문서가 더 커지면 재검토 대상.

### ✅ 완료 (2026-07-31) — 데이터 경로를 cwd에서 분리 (근본 수정)
- **배경**: `config.toml`에 `RAG_DATA_DIR` 절대경로를 넣어 급한 불은 껐지만, 코드는 여전히
  `./data`(cwd 상대경로)를 기본값으로 써서 설정이 빠지면 같은 함정에 다시 빠졌다.
- **수정**: `config.py`에 `find_project_root()` 신설 — `pyproject.toml`이 있는 상위 폴더를 찾는다.
  `_default_data_dir()`: `RAG_DATA_DIR` > **프로젝트 루트/data** > (마커 없으면) `./data` 폴백.
  `_default_qdrant_path()`도 같은 함수를 쓰도록 통일(중복 제거).
- **우선순위 보존**: `RAG_DATA_DIR`·`RAG_QDRANT_PATH` 환경변수가 여전히 최우선이라
  운영의 config.toml 방식은 그대로 동작한다. 패키지만 설치된 배포 형태(마커 없음)는 기존 동작 유지.
- **검증**: 테스트 8건 추가(214 passed/9 skipped). `C:\Windows`에서 실행해도
  `data_dir=D:\User\Desktop\rag mcp\data`로 해석되는 것을 실측(수정 전 `C:\Windows\data`).
- **주의**: 프로젝트 폴더 자체를 옮기면 데이터도 함께 따라간다(루트 기준이므로). 데이터를
  다른 위치에 두려면 `RAG_DATA_DIR`를 명시할 것.

## 구현된 모듈 지도 (참고)
- `config.py` 모델별 컬렉션/차원 · `models.py` Chunk/SearchResult/Manifest
- `tokenizer.py`(코드/금액 보존)+`sparse.py`(blake2b idx, tf, IDF modifier 전제)
- `embeddings.py`(KURE lazy) · `rerank.py`(off/lexical/cross) · `vector_store.py`(dense+sparse, 각 Prefetch.filter)
- `manifest.py`(atomic·멱등) · `indexer.py`(chunks.jsonl·reindex) · `retrieval.py`(search/get_chunk)
- `jobs.py`(비동기 ingest job·JobStore) · `service.py`(도구 10개 로직·submit_ingest/ingest_status·review_before_ingest·get_page_image)
- `rerank.py`(off|lexical|cross 리랭커 — lexical은 모델 불필요)
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
