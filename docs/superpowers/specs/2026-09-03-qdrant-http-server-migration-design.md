# Qdrant HTTP 서버 전환 설계

**작성일:** 2026-09-03
**상태:** 사용자 승인된 방향 — 작성된 설계 검토 대기

## 목표

현재 `data/qdrant`를 직접 여는 Qdrant local-path 구성을 Windows에서 실행되는 하나의
Qdrant HTTP 서버로 전환한다. Codex와 Claude Desktop은 각각 기존 `rag-mcp serve`
stdio 프로세스를 유지하되, 두 프로세스가 `http://127.0.0.1:6333`의 같은 Qdrant 서버에
동시에 접속해 동일한 RAG 색인을 검색할 수 있어야 한다.

## 현재 상태

- RAG MCP는 MCP Python SDK v2의 `MCPServer`를 사용하지만 transport는 stdio다.
- `RAG_QDRANT_MODE` 기본값은 `local`이며 현재 실행 설정도 local이다.
- local 저장소는 `D:\User\Desktop\rag mcp\data\qdrant`이고 한 프로세스만 열 수 있다.
- `open_qdrant_client()`는 이미 `RAG_QDRANT_MODE=server`와 `RAG_QDRANT_URL`을 지원한다.
- `D:\qdrant`에는 아직 Qdrant 서버가 설치되어 있지 않다.
- Python `qdrant-client` 잠금 버전은 1.18.0이다.

## 범위

1. 공식 Windows Qdrant 바이너리를 `D:\qdrant`에 설치한다.
2. 서버 저장소를 `D:\qdrant\storage`로 고정한다.
3. 로그인 시 숨김 창으로 Qdrant를 실행하는 Windows 작업 스케줄러 작업을 등록한다.
4. local 컬렉션을 HTTP 서버로 복사하는 `scripts/migrate_local_to_server.py`를 테스트 우선으로 작성한다.
5. dry-run과 명시적인 `--apply`를 구분하고, 원본과 대상의 컬렉션별 포인트 수를 대조한다.
6. Codex, Claude Desktop 및 CLI 환경을 Qdrant server 모드로 전환한다.
7. 전체 자동 테스트와 실제 stdio MCP 동시 사용·검색 회귀를 검증한다.
8. 관련 운영 문서와 `PROGRESS.md`를 갱신하고 단계별 커밋을 GitHub에 푸시한다.

## 제외 범위

- MCP transport를 stdio에서 Streamable HTTP로 변경하지 않는다.
- dense 또는 sparse 임베딩을 다시 계산하지 않는다.
- 청킹, 검색 융합, 리랭킹 또는 도구 인터페이스를 변경하지 않는다.
- 기존 `data\qdrant`를 삭제하거나 서버 저장소로 파일 복사하지 않는다.
- Qdrant를 외부 네트워크에 공개하지 않는다. 서버는 `127.0.0.1`에서만 사용한다.

## 선택지와 결정

1. **Qdrant만 HTTP 서버로 전환 — 선택.** 각 MCP 클라이언트의 기존 stdio 통합을 유지하면서
   동일 색인의 동시 접근 문제를 해결한다. 현재 코드 변경이 가장 작고 롤백도 단순하다.
2. MCP도 Streamable HTTP로 전환: MCP 프로세스 하나를 공유할 수 있지만 클라이언트 설정,
   인증, 서버 수명 관리까지 바뀐다. 이번 목표보다 범위가 크다.
3. 클라이언트별 local 저장소 복제: 잠금은 피하지만 색인 상태가 서로 달라지므로 채택하지 않는다.

## 아키텍처

```text
Codex  ──> rag-mcp stdio 프로세스 ─┐
                                    ├─HTTP──> Qdrant 127.0.0.1:6333
Claude ──> rag-mcp stdio 프로세스 ─┘          D:\qdrant\storage
```

Qdrant 프로세스만 실제 저장소를 연다. 각 `rag-mcp` 프로세스는 독립적인 `RagService`, 캐시,
비동기 작업 레지스트리를 갖지만 검색 데이터는 Qdrant HTTP 서버를 통해 공유한다. 매니페스트와
파싱 캐시는 계속 프로젝트의 `data` 디렉터리에 둔다.

## 데이터 이관

### 이관 도구

`scripts/migrate_local_to_server.py`는 source local 경로와 target URL을 명시적으로 받는다.
기본 실행은 읽기 전용 dry-run이며, `--apply`가 있을 때만 대상 서버에 컬렉션을 생성하고
포인트를 쓴다. source와 target이 같은 대상이거나 서버가 응답하지 않으면 쓰기 전에 실패한다.

각 컬렉션은 source에서 컬렉션 설정과 모든 포인트를 `scroll(with_payload=True,
with_vectors=True)`로 읽는다. dense·sparse 벡터, payload, 포인트 ID를 그대로 upsert하므로
임베딩 모델을 로드하거나 재임베딩하지 않는다. 대상 컬렉션이 이미 존재하면서 비어 있지 않으면
기본적으로 거부해 부분 덮어쓰기를 피한다.

### 일관성 검증

이관 전후에 다음을 컬렉션별로 기록하고 대조한다.

- 컬렉션 이름
- source 포인트 수
- target 포인트 수
- dense 벡터 크기와 거리 함수
- sparse 벡터 설정

포인트 수나 스키마가 하나라도 다르면 이관을 성공으로 처리하지 않는다. 이관 중 source local
저장소의 파일락을 피하기 위해 기존 `rag-mcp serve` 프로세스를 먼저 종료한다.

## 설치와 운영 설정

Qdrant 서버는 현재 `qdrant-client`와 호환되는 공식 Windows 릴리스를 사용한다. 설치 전 공식
릴리스와 체크섬 또는 배포 자산을 확인한다. 서버의 storage 경로와 바인딩 주소는 명시적 설정으로
고정하고, `http://127.0.0.1:6333/healthz`가 성공한 뒤에만 데이터를 이관한다.

설정 변경 대상은 다음과 같다.

- `~/.codex/config.toml`의 `rag_mcp` 환경 변수
- `%APPDATA%\Claude\claude_desktop_config.json`의 `rag-mcp` 환경 변수
- CLI용 사용자 환경 변수

적용 값은 `RAG_QDRANT_MODE=server`와
`RAG_QDRANT_URL=http://127.0.0.1:6333`이다. 기존 `RAG_DATA_DIR`과 다른 MCP 설정은
보존한다. 각 설정 파일은 변경 전에 타임스탬프가 붙은 백업을 만든다.

작업 스케줄러는 Windows 로그인 시 Qdrant를 숨김 창으로 실행한다. 중복 실행을 피하고 서버가
이미 정상 응답하면 새 프로세스를 만들지 않는 시작 스크립트를 사용한다. 등록 이름, 실행 명령,
로그 위치, 수동 시작·중지·해제 방법을 운영 문서에 기록한다.

## 오류 처리와 롤백

- 원본 `data\qdrant`는 수정·삭제하지 않고 별도 타임스탬프 백업도 만든다.
- 이관 실패 시 대상 서버 데이터를 성공한 것으로 간주하지 않고 설정을 전환하지 않는다.
- 설정 변경 후 검증 실패 시 Codex·Claude 설정과 사용자 환경 변수를 이전 백업으로 복원한다.
- `RAG_QDRANT_MODE`를 `local`로 되돌리거나 제거하면 기존 `data\qdrant`를 즉시 다시 사용할 수 있다.
- 롤백 시 local 저장소를 여는 MCP 프로세스는 하나만 실행한다.

## 테스트와 검증 기준

### 자동 테스트

1. 마이그레이션 도구 테스트를 먼저 작성하고 미구현 상태의 예상 실패를 확인한다.
2. dry-run은 대상 서버에 쓰지 않는다.
3. `--apply`는 dense·sparse 벡터와 payload를 보존한다.
4. 비어 있지 않은 대상 컬렉션, 포인트 수 불일치, 서버 연결 실패를 명시적으로 거부한다.
5. `uv run pytest -q` 전체 테스트가 통과한다.
6. `RAG_RUN_SERVER_TESTS=1` 실제 stdio 회귀 테스트가 통과한다.

### 실제 운영 검증

1. Qdrant `/healthz`가 응답한다.
2. local과 server의 모든 컬렉션별 포인트 수와 스키마가 일치한다.
3. 실제 MCP `initialize`와 `tools/list`에서 11개 도구가 노출된다.
4. Codex용과 Claude용 두 개의 독립 stdio MCP 프로세스를 동시에 연결한다.
5. 양쪽에서 `list_documents`와 `collection_status`가 같은 결과를 반환한다.
6. 양쪽에서 기존 회귀 질의인 “칠곡군 타당성검토에서 수지개선 효과”를 검색해
   “5년간 15억 3,700만 원” 근거가 반환되는지 확인한다.
7. 한쪽 프로세스가 실행 중일 때 다른 프로세스도 파일락 없이 검색할 수 있어야 한다.

## Git 전달

코드와 문서는 관련 단위별로 작게 커밋한다. 사용자 작업인 기존 `PROGRESS.md` 변경,
`data/`, `pdf/색인목록.md`는 의도적으로 덮어쓰거나 함께 커밋하지 않는다. 구현 완료 후 전체 diff와
테스트 결과를 다시 확인하고 `codex/qdrant-http-server` 브랜치를 GitHub `origin`에 푸시한다.
운영체제 설정과 `D:\qdrant` 설치물은 Git 커밋 대상이 아니므로 최종 보고에서 별도로 열거한다.
