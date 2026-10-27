# Cross-process ingest safety implementation plan

**Goal:** 같은 데이터 폴더를 사용하는 MCP/CLI의 동시 변경 요청이 파일과 DB 상태를 섞지 않도록 한다.

**Architecture:** OS 파일 잠금을 기존 서비스의 쓰기 예약/해제에 연결하고, 임시 파일명을 UUID로 구분한다. 외부 요청은 `busy`로 즉시 반환하며 자동 대기하지 않는다.

**Tech Stack:** Python 표준 라이브러리 `msvcrt`/`fcntl`, pytest, subprocess. 새 의존성 없음.

## 제약

운영 색인과 사용자 변경 `PROGRESS.md`를 보존한다. fake 임베딩과 격리된 저장소만 사용한다. 파싱 시작부터 최종 상태 저장까지 잠근다. 잠금 파일은 삭제하지 않는다.

## 작업 1: 공용 잠금과 작업별 임시 파일

- [x] `tests/test_cross_process_mutations.py`에서 독립 서비스·실제 프로세스의 동시 획득, 비동기 유지/해제, 오류 복구, 독립 폴더, 고유 임시 파일을 검증하는 실패 테스트를 작성한다.
- [x] `uv run --offline --no-sync pytest tests/test_cross_process_mutations.py -o addopts='' -q --basetemp <프로젝트 밖의 새 임시 경로>`로 RED를 확인한다.
- [x] `src/rag_mcp/mutation_lock.py`에 `DataMutationLock.try_acquire() -> bool`, `release() -> None`을 구현한다. 잠금 경합만 False로 처리하고 파일 열기·권한 등 다른 오류는 전파한다.
- [x] `service.py`의 `_reserve_mutation`과 `_release_mutation`에서 이 잠금을 얻고 해제한다. 기존 token과 job 수명, 실패 복구 경로를 유지한다.
- [x] `indexer.py`, `manifest.py`의 임시 파일에 UUID를 붙이고 자기 임시 파일을 finally에서 정리한다.
- [x] 새 회귀 테스트와 전체 pytest를 실행한다. 서버 소켓이 필요한 전체 검증은 샌드박스 제약이 없는 실행을 사용한다.
- [x] README·MCP 연동 가이드·운영 가이드에 저장소 전체의 쓰기 1개 제한, 후발 `busy`, 동일 data 경로와 재시작 요구를 기록한다.
- [x] 코드 검토 후 수정 범위만 커밋하고 PROGRESS에 실제 검증 결과를 추가한다.
