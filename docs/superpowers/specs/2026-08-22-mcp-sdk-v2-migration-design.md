# MCP Python SDK v2 전환 설계

**작성일:** 2026-08-22
**상태:** 사용자 승인된 설계 — 구현 계획 작성 전 검토 대기

## 목표

로컬 RAG MCP 서버를 MCP Python SDK v1(`mcp 1.28.0`)에서 v2로 전환한다.
기존 FastMCP식 도구 선언을 저수준 프로토콜 구현으로 바꾸지 않는다. SDK v2의
고수준 서버 클래스인 `MCPServer`를 사용해 현재의 도구·서비스 경계를 유지한다.

## 범위

- 런타임 의존성을 `mcp>=2,<3`으로 올리고 잠금 파일을 v2 의존성 그래프로 갱신한다.
- `server.py`의 v1 `FastMCP`/`Image` import와 인스턴스를 v2 `MCPServer` 경로로 바꾼다.
- 현재의 11개 `@mcp.tool()` 함수, `RagService` 위임, `anyio.to_thread` 오프로딩,
  preflight·백그라운드 warmup·로그 파일 리다이렉션을 그대로 보존한다.
- v2에서 Python 속성명이 snake_case가 된 MCP 타입을 반영해 이미지 콘텐츠 검증 테스트를
  갱신한다.
- 안내 문서에서 FastMCP v1이라는 설명을 SDK v2 `MCPServer` 설명으로 교체한다.

## 제외 범위

- Qdrant local/server 전환, 데이터·색인·매니페스트 변경
- 도구 이름·인자·서비스 반환값 변경
- stdio에서 Streamable HTTP/SSE로의 transport 전환
- v1과 v2를 함께 지원하는 import shim

## 선택지와 결정

1. **v2 완전 전환 — 선택**: 이 로컬 프로젝트의 실행 환경을 v2로 고정한다. 코드가 가장
   단순하고, 2026-07-28 MCP 사양을 지원하면서도 SDK가 기존 `initialize` 클라이언트와의
   호환을 처리한다.
2. v1/v2 호환 shim: 배포 패키지에서 두 SDK 세대를 모두 지원할 때만 적합하다. 이 프로젝트는
   로컬 단일 실행 환경이므로 테스트 매트릭스와 조건부 코드만 늘어난다.
3. v1 유지: `<2` 상한을 추가해 기존 동작을 고정하는 방식이다. 사용자가 요청한 새 SDK 전환과
   반대이므로 채택하지 않는다.

## 설계

### 서버 API

`mcp.server.fastmcp.FastMCP`를
`mcp.server.mcpserver.MCPServer`로 교체한다. `Image`도 같은 v2 서버 패키지에서
가져온다. 서버 이름은 계속 `rag-mcp`이며, `log_level="WARNING"` 설정을 유지해
요청마다 stderr에 로그가 쌓이는 기존 Windows 파이프 블로킹 회귀를 막는다.

도구 등록 방식은 변경하지 않는다. v2 `MCPServer`는 현재의 `@mcp.tool()` 데코레이터,
타입 힌트 기반 JSON Schema 생성, `dict`/`list`/이미지 콘텐츠 반환 처리를 그대로 제공한다.
`get_page_image`는 여전히 텍스트 블록과 실제 이미지 블록을 함께 반환한다.

서버는 명시적으로 `mcp.run(transport="stdio")`를 호출한다. 기존 Codex/Claude 설정의
`rag-mcp serve` 실행 진입점과 stdin/stdout JSON-RPC 연결 방식은 바뀌지 않는다.

### 타입 및 테스트

SDK v2의 MCP Pydantic 타입은 Python 속성에 snake_case를 사용한다. 따라서 이미지 결과 검증은
`mimeType` 대신 `mime_type`처럼 v2 모델 API를 사용한다. v2 `MCPServer`가 11개 도구를
등록하고 도구 호출에서 텍스트·이미지 콘텐츠를 유지한다는 테스트를 먼저 작성하거나 갱신한다.

기존 stderr 회귀 테스트는 유지한다. SDK v2의 stdio transport가 프로토콜 스트림을 사설 파일
디스크립터로 분리하더라도, 읽히지 않는 stderr 때문에 서버가 멈추지 않는 현재의 운영 보장은
계속 검증한다. 기본 전체 테스트 뒤에는 실제 stdio 서버 테스트 게이트도 실행한다.

### 문서와 의존성

`pyproject.toml`에는 v2 전용 범위 `mcp>=2,<3`을 기록한다. `uv lock`으로 생성한 잠금 파일만
사용하며 pip는 사용하지 않는다. `MCP_연동가이드.md`의 서버 설명도 `MCPServer`와 v2 stdio
동작에 맞춘다.

현재 작업트리에 이미 존재하는 `uv.lock` 및 `PROGRESS.md` 변경은 이 전환보다 앞선 사용자
변경으로 간주한다. 구현 중 해당 파일을 갱신해야 할 경우에도 관련 변경만 명확히 구분하고,
기존 내용을 되돌리거나 덮어쓰지 않는다.

## 검증 기준

1. 새 테스트가 v1 import로는 실패하고 v2 `MCPServer` 구현 후 통과한다.
2. `uv run pytest -q` 전체가 통과한다.
3. `RAG_RUN_SERVER_TESTS=1` 실제 stdio 회귀 테스트가 통과한다.
4. 실제 stdio 프로세스에 대해 `initialize`/`tools/list`와 최소 한 개의 읽기 전용 도구 호출을
   수행해, 기존 클라이언트와의 연결을 확인한다.
5. 서버는 계속 11개 도구를 노출하고, `get_page_image`는 이미지 콘텐츠를 반환한다.

## 롤백

문제가 생기면 `pyproject.toml`의 MCP 버전을 v1 범위로 복원하고 v1 호환 잠금 파일로 되돌린다.
이번 전환은 데이터·Qdrant·문서 매니페스트를 변경하지 않으므로 런타임 코드와 의존성만 되돌리면
된다.
