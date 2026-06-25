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
- **별도 트랙:** `RAG_RUN_MODEL_TESTS=1` 실모델 테스트가 있으면 이제 실제로 돌릴 수 있음(모델 캐시됨).

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
- (해결됨) ~~샘플 PDF 대기~~ → `예산편성_예산부서.pdf` 처리 완료.
- (해결됨) ~~HF SSL 차단~~ → `truststore.inject_into_ssl()`로 Windows 인증서 저장소 사용해 해결.
  KURE-v1 캐시 완료, 실모델 end-to-end 검색 확인. (위 CURRENT STATE 참조)
- 현재 미해결 BLOCKER 없음.
