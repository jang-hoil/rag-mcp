# PDF Page Accuracy Implementation Plan

> **For agentic workers:** Use executing-plans to implement this plan task-by-task in the current checkout.

**Goal:** 본문의 실제 페이지를 보존하고 재파싱한 PDF에서 예전 페이지 그림을 반환하지 않는다.

**Architecture:** 본문 버퍼는 단일 페이지에만 속하도록 한다. PDF 변환 결과를 정상적으로 읽은 시점에 해당 문서의 페이지 PNG만 무효화하고 기존 렌더 경로로 다시 생성한다.

**Tech Stack:** Python, pytest, PyMuPDF, 기존 ParsedDoc/Chunk/RagService.

## Global Constraints

- 테스트 먼저 작성 → 구현 → 전체 pytest 통과 → 작은 커밋.
- 운영 data/Qdrant와 사용자의 미커밋 변경은 보존한다.
- 모델 다운로드, 운영 재색인, 새 의존성 없이 임시 데이터로 검증한다.

### Task 1: 본문 페이지 경계

**Files:** `src/rag_mcp/chunking.py`, `tests/test_chunk_page_provenance.py`.

**Interfaces:** `build_chunks(parsed, config=...) -> list[Chunk]`와 `Chunk.page`를 유지한다.

- [x] 다른 페이지의 paragraph/list/caption, 페이지 누락, 같은 페이지 합치기, 제목 경로, 긴 다음 페이지의 회귀 테스트를 작성한다. 핵심 기대값은 `[(c.text, c.page) for c in chunks] == [("앞쪽", 2), ("뒤쪽", 3)]`이다.
- [x] `.venv/Scripts/python.exe -m pytest tests/test_chunk_page_provenance.py -q --basetemp <새 임시 경로>`로 잘못된 페이지를 재현한다. RED: 10 failed.
- [x] 본문 추가 직전에 `if buffer and page != buffer_page:` 조건으로 기존 `_flush_body`를 호출하고 버퍼·페이지를 초기화한다. 새 버퍼의 페이지는 현재 블록의 값으로 결정한다.
- [x] 회귀 테스트와 전체 pytest를 실행하고 결과를 확인한다. 관련 36 passed, 1 skipped; 전체 298 passed, 9 skipped, 기존 Qdrant 경고 1건.
- [x] 관련 소스·테스트·설계/계획만 커밋한다. `62e6fdc`.

### Task 2: 재파싱 이미지 무효화

**Files:** `src/rag_mcp/page_render.py`, `src/rag_mcp/pdf_parser.py`, `src/rag_mcp/service.py`, `tests/test_pdf_image_cache.py`.

**Interfaces:** `invalidate_page_cache(document_id: str, config: Config) -> None`는 해당 문서 `pages` 안의 `p숫자.png`만 제거한다. `parse_pdf`의 공개 인자는 유지한다.

- [x] 임시 실제 PDF의 내용 변경 전후 PNG/서비스 이미지가 달라지는 회귀 테스트를 작성한다. 모든 캐시 페이지, 줄어든 쪽수, 다른 문서 보존, 재사용·실패 시 보존을 검증한다.
- [x] 새 테스트를 실행해 예전 PNG 재사용을 재현한다. RED 4 failed, 4 passed; 수정 후 관련 31 passed, 5 skipped.
- [x] `parse_pdf`에 `converted = force or json_path is None`을 기록하고, 새 tree를 정상적으로 읽은 뒤에만 `invalidate_page_cache(document_id, config)`를 호출한다.
- [x] 독립 검토의 교차 실행 재현을 추가한다. 같은/다른 인스턴스의 이전 PDF PNG 재생성, 캐시 히트 유지, 실제 파일 삭제 경쟁을 검증한다. RED 3 failed, 1 passed; PyMuPDF 예외 래핑 경로 추가 RED 1 failed 확인.
- [x] 서비스의 캐시 미스 렌더에 공용 OS 잠금을 적용한다. 잠금 획득 뒤 캐시·매니페스트 재조회, 바이트 읽기까지 유지, finally 해제, 삭제된 캐시의 재시도 및 작업 중 안내를 구현한다.
- [x] Windows에서 실제 PNG 읽기 핸들을 열어 무효화 공유 위반 RED 1 failed를 재현한다. WinError 32에만 20ms 간격·총 0.1초 한도의 삭제 재시도를 적용한다.
- [x] 새 테스트, 전체 pytest, 코드 검토를 완료하고 운영 안내·PROGRESS를 갱신한다. 이미지 회귀 15 passed; 최종 전체 313 passed, 9 skipped, 기존 Qdrant 경고 1건. 독립 재검토에서 추가 조치할 결함 없음.
- [x] 두 번째 수정과 검증 기록을 커밋한다. 사용자 미커밋 PROGRESS의 기존 22줄은 포함하지 않는다.
