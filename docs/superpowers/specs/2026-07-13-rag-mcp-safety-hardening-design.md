# RAG MCP 데이터 안전성 강화 설계

작성일: 2026-07-13

## 목표

기존 사용 흐름과 검색 구조는 유지하면서, 확인된 데이터 손실·오색인·중복 모델 로드 위험만 제거한다.
새 기능을 넓게 추가하거나 검색 알고리즘을 바꾸지 않는다.

## 범위

이번 변경에 포함한다.

1. 새 `ingest_pdf` 요청은 같은 `document_id`의 옛 JSON을 선택하지 않고 입력 PDF를 새로 파싱한다.
2. 빈 청크는 manifest·`chunks.jsonl`·Qdrant를 건드리기 전에 거부한다.
3. Qdrant 교체는 새 포인트를 먼저 저장하고, 성공한 뒤 사라진 옛 포인트만 제거한다.
4. `chunks.jsonl`은 임시 파일 작성 후 `os.replace`로 원자 교체한다.
5. `document_id`의 경로 구분자·상위 경로·절대경로·제어문자를 중앙에서 거부한다.
6. 색인·삭제·재색인 쓰기 작업은 한 프로세스에서 동시에 실행하지 않고 즉시 `busy` 응답한다. 검색은 계속 허용한다.
7. Retriever와 Indexer는 모델별로 같은 임베딩 백엔드와 같은 VectorStore를 공유한다.
8. `RAG_QDRANT_PATH`가 없으면 `RAG_DATA_DIR/qdrant`를 사용하고, Qdrant 모드 오타를 거부한다.
9. `RAG_EMBEDDING_MODEL`을 검색·색인의 실제 기본 모델로 사용한다.

이번 변경에서 제외한다.

- 신규 PC 모델 다운로드 자동화와 `prepare-model` 명령
- 색인 퍼센트·단계별 진행률
- 본문 문장 경계 청킹, sparse 가중치, 관련도 임계값 변경
- `matched_by` 계산 방식과 검색 결과 스키마 확장
- 페이지를 넘긴 표의 복수 이미지 반환
- 기존 색인 데이터의 자동 재색인

## 선택한 접근

세 가지 접근을 비교했다.

- 최소 핫픽스: 옛 파싱 캐시와 빈 청크만 막는다. 변경은 작지만 upsert 실패·경합 위험이 남아 선택하지 않는다.
- 수술식 안전성 수정: 확인된 데이터 안전성과 중복 모델 문제만 관련 모듈 안에서 고친다. 이번에 선택한다.
- 광범위 사용성 개편: 설치 자동화, 진행률, 검색 품질까지 함께 바꾼다. 검증 범위가 지나치게 커져 제외한다.

## 설계

### 문서 ID 경계

`Config`가 모든 문서 경로를 만들기 전에 `document_id`를 검증한다. 빈 값, `.`·`..`, `/`·`\\`, `:`, 제어문자를 거부한다. 한글, 공백, 괄호, 하이픈, 밑줄은 기존처럼 허용한다. 최종 경로는 `resolve()` 후 해당 데이터 하위인지 다시 확인한다.

### 새 PDF 파싱

`ingest_pdf`는 `parse_and_chunk(..., force=True)`를 사용한다. `parse_pdf`는 변환 전 JSON 상태를 기록하고, 변환 뒤 새로 생성되거나 변경된 JSON만 선택한다. 변환이 실패하거나 새 JSON을 식별하지 못하면 기존 캐시를 임의로 선택하지 않고 실패한다. `reindex(reparse=False)`의 기존 청크 재사용 의미는 바꾸지 않는다.

### 빈 문서와 청크 저장

`Indexer.index_chunks`는 빈 리스트와 본문이 전부 공백인 청크를 첫 단계에서 거부한다. `save_chunks`는 같은 디렉터리의 임시 파일에 전체 JSONL을 쓴 뒤 `os.replace`한다. 직렬화·쓰기 실패 시 기존 파일을 유지하고 임시 파일을 정리한다.

### Qdrant 문서 교체

기존 문서 포인트 ID를 조회한 뒤 새 포인트를 먼저 upsert한다. upsert가 성공하면 새 ID 집합에 없는 옛 포인트만 ID로 삭제한다. 따라서 임베딩 또는 upsert 실패 전에 기존 문서를 전량 삭제하지 않는다. stale 삭제 실패는 manifest를 `error`로 남겨 재시도 가능하게 한다.

### 쓰기 작업 조정

`RagService`에 단일 비차단 mutation lease를 둔다. 백그라운드 ingest는 job 생성 직후 쓰기 예약 상태가 되며 완료·실패 시 해제한다. `delete_document`와 `reindex_document`는 예약 중이면 저장소를 건드리지 않고, 가능한 경우 현재 `job_id`, 그 외에는 진행 중인 작업 종류를 포함한 `busy` 응답을 반환한다. 검색과 상태 조회는 lease를 사용하지 않는다.

### 임베딩·설정 일관성

Indexer 생성 시 같은 모델의 Retriever가 가진 backend와 store를 재사용한다. 설정에서 모델 인자가 생략되면 `Config.embedding_model`을 사용한다. Qdrant local 경로는 명시적 `RAG_QDRANT_PATH`가 우선이고, 없을 때만 `data_dir / "qdrant"`로 파생한다. 모드는 `local|server`만 허용한다.

## 오류 처리

- 잘못된 `document_id`, Qdrant 모드, 빈 청크는 변경 전에 `ValueError`로 실패한다.
- MCP 쓰기 경합은 예외 대신 `ok=False`, `status="busy"`와 복구 안내를 반환한다.
- 파싱·임베딩·Qdrant 오류는 기존 job/manifest 오류 기록 방식을 유지한다.
- 기존 벡터를 지우기 전 새 upsert 성공을 요구한다.

## 테스트

각 수정은 실패하는 회귀 테스트를 먼저 추가한다.

1. 같은 ID의 다른 PDF를 연속 ingest하면 두 번째 변환 산출물을 선택한다.
2. 빈 청크 재색인은 기존 manifest·JSONL·검색 포인트를 보존한다.
3. upsert 예외를 주입하면 기존 포인트가 남는다.
4. 새 청크 수가 줄면 성공 후 stale 포인트만 제거된다.
5. JSONL 쓰기 예외 시 기존 파일이 그대로 로드된다.
6. 경로 이탈 ID는 거부되고 데이터 루트 밖 sentinel은 유지된다.
7. ingest 중 delete/reindex는 즉시 busy이며 검색은 동작한다.
8. warmup 뒤 ingest가 같은 backend 인스턴스를 사용한다.
9. 데이터 루트 파생 경로, 명시 override, mode 오타, 기본 임베딩 모델을 검증한다.
10. 모듈별 테스트 통과 후 전체 `pytest -q`를 실행한다.

## 커밋 전략

테스트가 통과하는 작은 단위로 커밋한다.

1. 문서 ID·설정 검증
2. fresh parse·빈 청크·원자 JSONL
3. Qdrant 안전 교체·mutation lease
4. backend 공유·기본 모델 일관성
5. 문서와 전체 검증 결과 갱신

사용자가 만든 기존 `uv.lock` 변경은 어떤 커밋에도 포함하지 않는다.
