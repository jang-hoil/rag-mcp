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
- [x] **0. 프로젝트 뼈대** — pyproject, dirs, CLAUDE.md, PROGRESS.md, venv ✅ (커밋 e52dc91)
- [x] **1. Qdrant 스모크 테스트** — local 모드 dense+sparse upsert→query_points RRF→fiscal_year 필터 ✅ (3 passed, 커밋 e52dc91)
- [ ] **2. pdf_parser** — OpenDataLoader cluster+html, JSON+MD, parsed 영구저장 (테스트용 PDF 필요)
- [ ] **3. metadata + chunking + page_render** — 표 atomic, 뭉친표 needs_image 탐지, PNG 렌더
- [~] **4. tokenizer + sparse + embeddings** — 코드 완료(13 passed). 실제 KURE 임베딩 검증만 SSL로 보류
- [ ] **5. vector_store + manifest** — dense+sparse upsert, payload 인덱스, 멱등성
- [ ] **6. retrieval + reindex** — hybrid+fiscal_year 필터, reindex(reparse=False)
- [ ] **7. server(MCP) + cli** — FastMCP 도구 노출, 통합 테스트

> 마일스톤 4는 스펙 순서보다 먼저 구현(PDF 불요·독립 검증 가능). tokenizer/sparse/embeddings
> 코드와 테스트 통과. **실제 KURE 모델 임베딩만 환경 SSL 이슈로 미검증** (BLOCKERS 참조).

## 현재 상태 (CURRENT STATE)
- 진행 중: **마일스톤 5 (vector_store + manifest)** 가 다음 우선순위 (PDF 불요, 스모크 자산 재사용 가능).
- 완료: 마일스톤0+1, 기반(config/models), 마일스톤4(tokenizer/sparse/embeddings 코드). **18 passed, 1 skipped.**
- 다음 할 일 (우선순위 순):
  1. **vector_store.py** — Qdrant dense+sparse named vector 컬렉션 생성/upsert, payload, 각 Prefetch.filter(스모크 검증 방식).
     sparse 컬렉션은 `models.SparseVectorParams(modifier=models.Modifier.IDF)` 시도(IDF 서버측). local 모드 IDF 동작 여부 스모크로 먼저 확인.
  2. **manifest.py** — data/manifests/{id}.json, status parsing→parsed→embedded→done, 멱등(재실행 시 기존 포인트 삭제 후 재삽입).
     테스트: 중간 실패 후 재실행 시 중복·orphan 없음 (FakeEmbeddingBackend로 모델 없이 검증).
  3. **retrieval.py** — search_documents 본체(hybrid/dense/sparse + fiscal_year 필터, matched_by 산출). RRF 기본.
  4. **pdf_parser/metadata/chunking/page_render (마일스톤 2~3)** — opendataloader-pdf 설치 + **테스트용 PDF 필요**.
     PDF 없으면 pymupdf로 작은 표 PDF 합성해 골든 테스트 가능. 사용자에게 실제 회계지침 PDF 1개 요청 권장.
  5. **server.py(FastMCP)+cli.py (마일스톤 7)** — §5 도구 7개 등록.
- 참고: embeddings 실모델 테스트는 `RAG_RUN_MODEL_TESTS=1`로만 실행. vector_store/manifest/retrieval 테스트는
  FakeEmbeddingBackend(해시 기반 결정적 벡터)로 모델 없이 검증할 것.

## 결정 로그 (DECISIONS)
- 2026-06-18: Python 3.12 부재 → uv+3.11 채택 (torch 호환 안정).
- 2026-06-18: pdftoppm 부재 → page_render는 pymupdf 폴백 우선.
- 2026-06-18: Qdrant local 모드에서 payload 필터는 **각 Prefetch.filter**에 넣어야 동작(top-level query_filter는 융합 후보를 못 거름). retrieval.py 구현 시 이 방식 적용.
- 2026-06-18: payload 인덱스는 local no-op → create_payload_index는 server 모드 대비로만 호출(경고 무시).

## 막힌 것 / 미해결 (BLOCKERS)
- (없음)
