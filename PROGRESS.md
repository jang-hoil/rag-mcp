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
- [ ] **0. 프로젝트 뼈대** — pyproject, dirs, CLAUDE.md, PROGRESS.md, venv
- [ ] **1. Qdrant 스모크 테스트** — local 모드 dense+sparse upsert→query_points RRF→fiscal_year 필터 [리스크1, 전제]
- [ ] **2. pdf_parser** — OpenDataLoader cluster+html, JSON+MD, parsed 영구저장
- [ ] **3. metadata + chunking + page_render** — 표 atomic, 뭉친표 needs_image 탐지, PNG 렌더
- [ ] **4. tokenizer + sparse + embeddings** — Kiwi 코드/금액 보존, BM25 sparse, KURE dim=1024
- [ ] **5. vector_store + manifest** — dense+sparse upsert, payload 인덱스, 멱등성
- [ ] **6. retrieval + reindex** — hybrid+fiscal_year 필터, reindex(reparse=False)
- [ ] **7. server(MCP) + cli** — FastMCP 도구 노출, 통합 테스트

## 현재 상태 (CURRENT STATE)
- 진행 중: **마일스톤 0 (프로젝트 뼈대)**
- 완료: pyproject.toml, .gitignore, .env.example, CLAUDE.md, PROGRESS.md 작성. git init 완료.
- 다음 할 일: 디렉터리 구조 + 모듈 스텁 생성 → uv venv(3.11) 생성 → qdrant-client+pytest 설치 → 마일스톤1 스모크 테스트 작성/실행.

## 결정 로그 (DECISIONS)
- 2026-06-18: Python 3.12 부재 → uv+3.11 채택 (torch 호환 안정).
- 2026-06-18: pdftoppm 부재 → page_render는 pymupdf 폴백 우선.

## 막힌 것 / 미해결 (BLOCKERS)
- (없음)
