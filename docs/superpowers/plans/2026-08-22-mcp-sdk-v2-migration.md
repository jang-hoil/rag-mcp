# MCP Python SDK v2 Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the local RAG MCP server from MCP Python SDK v1 FastMCP to SDK v2 `MCPServer` without changing its eleven tools, RAG service behavior, or stdio client configuration.

**Architecture:** Keep the high-level decorator server. Replace only the v1 SDK imports and class name with v2 `MCPServer`, retain the `RagService` delegation and worker-thread boundary, and make stdio explicit at startup. Tests will assert the v2 class, the v2 `CallToolResult` image shape, an in-memory v2 client exchange, and the existing real-stdio regression path.

**Tech Stack:** Python 3.11, uv, MCP Python SDK 2.x, anyio, pytest, Qdrant local test fixture.

## Global Constraints

- Runtime MCP dependency is exactly `mcp>=2,<3`; use `uv`, never `pip`.
- Use `MCPServer`, not low-level `Server`; retain `@mcp.tool()` and all eleven existing tool names and signatures.
- Keep stdio transport, Qdrant behavior, durable `data`, and service-layer return values unchanged.
- Keep `log_level="WARNING"` and the log-file redirection that protect Windows stderr pipes from blocking.
- Test first for every behavior change; observe the intended failure before production edits.
- Do not overwrite, stage, or commit pre-existing user changes in `PROGRESS.md`, `uv.lock`, `data/`, `output/`, `outputs/`, `tmp/`, or `work/`.

---

## File Structure

| File | Responsibility in this migration |
|---|---|
| `pyproject.toml` | Declares the SDK v2-only runtime requirement. |
| `uv.lock` | Resolves the v2 package graph after `uv lock`; only migration-owned hunks may be staged. |
| `src/rag_mcp/server.py` | Creates the v2 high-level server, exposes the existing tool functions, and runs stdio. |
| `src/rag_mcp/service.py` | Updates the framework name in its module contract; service logic remains framework-independent. |
| `tests/test_mcp_tools.py` | Verifies v2 `MCPServer`, the complete tool listing, in-memory v2 client discovery, and explicit stdio startup. |
| `tests/test_page_image_tool.py` | Verifies the v2 `CallToolResult` wrapper and snake_case image type field. |
| `tests/test_server_logging.py` | Retains the stderr-pipe regression defense while naming the v2 server accurately. |
| `MCP_연동가이드.md` | Documents the v2 `MCPServer` stdio entrypoint without changing client configuration. |
| `PROGRESS.md` | Records only the completed SDK migration and the current verified test result. |

### Task 1: Lock the SDK v2 server contract with failing tests

**Files:**
- Modify: `tests/test_mcp_tools.py:1,818-862`
- Modify: `tests/test_page_image_tool.py:169-210`
- Modify: `tests/test_server_logging.py:1-30`

**Interfaces:**
- Consumes: module-global `rag_mcp.server.mcp` and its `list_tools()`, `call_tool(name, arguments)`, and `run(transport=...)` methods.
- Produces: v2-focused tests that require `MCPServer`, eleven named tools, `CallToolResult.content`, snake_case `ImageContent.mime_type`, in-memory `Client`, and explicit `stdio` transport.

- [ ] **Step 1: Write the failing v2 registration test**

Replace `test_server_imports_and_registers_tools` with this test. It is written before the SDK or server code changes, so the current v1 class name must fail.

```python
def test_mcp_sdk_v2_server_registers_the_eleven_tools():
    """SDK v2 MCPServer가 공개 도구 11개를 모두 등록한다."""
    import asyncio

    from rag_mcp import server

    assert type(server.mcp).__name__ == "MCPServer"
    names = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    assert names == {
        "search_documents", "ingest_pdf", "ingest_status", "get_chunk",
        "get_page_image", "list_documents", "delete_document",
        "reindex_document", "collection_status", "set_source_path",
        "review_before_ingest",
    }
```

- [ ] **Step 2: Run the registration test and verify RED**

Run:

```powershell
uv run pytest tests/test_mcp_tools.py::test_mcp_sdk_v2_server_registers_the_eleven_tools -q
```

Expected: FAIL because the current server class is named `FastMCP`, not `MCPServer`.

- [ ] **Step 3: Write the failing v2 in-memory client test**

Add this immediately after the registration test. It verifies v2's public `Client` against the real server object without starting Qdrant or a subprocess.

```python
def test_mcp_sdk_v2_client_discovers_server_tools():
    """SDK v2 Client가 최신 프로토콜로 서버의 도구 목록을 읽는다."""
    import asyncio

    from mcp import Client
    from rag_mcp import server

    async def list_tool_names() -> set[str]:
        async with Client(server.mcp) as client:
            listing = await client.list_tools()
            return {tool.name for tool in listing.tools}

    names = asyncio.run(list_tool_names())
    assert "list_documents" in names
    assert "get_page_image" in names
```

- [ ] **Step 4: Run the v2 client test and verify RED**

Run:

```powershell
uv run pytest tests/test_mcp_tools.py::test_mcp_sdk_v2_client_discovers_server_tools -q
```

Expected: FAIL at `from mcp import Client` because the installed v1 SDK does not expose v2's first-class client.

- [ ] **Step 5: Write the failing v2 page-image response test**

Rename `test_fastmcp_get_page_image_returns_text_and_actual_image_content` to `test_mcpserver_get_page_image_returns_text_and_actual_image_content`. Retain its fake service and PNG fixture, but replace the call and assertions with:

```python
    result = asyncio.run(
        server.mcp.call_tool(
            "get_page_image",
            {"document_id": "문서", "pages": 7},
        )
    )

    assert result.is_error is False
    content = result.content
    assert len(content) == 2
    assert isinstance(content[0], TextContent)
    assert "document_id: 문서" in content[0].text
    assert "page: 7" in content[0].text
    assert "캐시 사용: 예" in content[0].text
    assert "실제 파일 경로:" in content[0].text
    assert isinstance(content[1], ImageContent)
    assert content[1].mime_type == "image/png"
    assert base64.b64decode(content[1].data) == png_bytes
```

- [ ] **Step 6: Run the page-image test and verify RED**

Run:

```powershell
uv run pytest tests/test_page_image_tool.py::test_mcpserver_get_page_image_returns_text_and_actual_image_content -q
```

Expected: FAIL because v1 `FastMCP.call_tool()` returns a content list, without the v2 `CallToolResult.content` wrapper.

- [ ] **Step 7: Write the failing explicit-stdio startup test**

Add this after `test_server_main_exits_when_storage_busy`. It bypasses file logging, prevents a real background thread, and makes the missing `transport="stdio"` keyword observable.

```python
def test_server_main_runs_mcpserver_over_stdio(monkeypatch):
    """서버 진입점은 SDK v2의 stdio transport를 명시한다."""
    import threading
    from types import SimpleNamespace

    from rag_mcp import server

    calls: list[str] = []

    class ReadyService:
        config = SimpleNamespace()

        def preflight(self):
            return None

        def warmup(self):
            return None

    class ImmediateThread:
        def __init__(self, *, target, **_kwargs):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(server, "service", lambda: ReadyService())
    monkeypatch.setattr(server, "redirect_logs_to_file", lambda _config: None)
    monkeypatch.setattr(threading, "Thread", ImmediateThread)
    monkeypatch.setattr(server.mcp, "run", lambda *, transport: calls.append(transport))

    server.main()

    assert calls == ["stdio"]
```

- [ ] **Step 8: Run the stdio test and verify RED**

Run:

```powershell
uv run pytest tests/test_mcp_tools.py::test_server_main_runs_mcpserver_over_stdio -q
```

Expected: FAIL with a missing keyword-only `transport` argument because the current entrypoint calls `mcp.run()` without it.

- [ ] **Step 9: Retain the stderr guard while updating its v2 wording**

In `tests/test_server_logging.py`, keep the assertion and replace its docstring only:

```python
def test_request_logging_is_not_written_per_call():
    """MCPServer 기본 INFO 로그가 stderr를 막지 않도록 WARNING으로 고정한다."""
    assert server_mod.mcp.settings.log_level == "WARNING"
```

### Task 2: Implement the minimum SDK v2 conversion and make the focused tests green

**Files:**
- Modify: `pyproject.toml:6-17`
- Modify: `uv.lock` (only SDK v2 resolution hunks)
- Modify: `src/rag_mcp/server.py:1-28,249`
- Modify: `src/rag_mcp/service.py:1-5`
- Modify: `tests/test_mcp_tools.py`
- Modify: `tests/test_page_image_tool.py`
- Modify: `tests/test_server_logging.py`

**Interfaces:**
- Consumes: the tests from Task 1 and the v2 high-level API `MCPServer`, `Image`, and `Client`.
- Produces: the same `rag_mcp.server.mcp` module global, now an `MCPServer` with identical tool names and an explicit stdio run call.

- [ ] **Step 1: Change the declared dependency to the v2-only range**

In `pyproject.toml`, replace the existing MCP requirement with:

```toml
    "mcp>=2,<3",
```

- [ ] **Step 2: Resolve and install only through uv**

Run:

```powershell
uv lock
uv sync
uv run python -c "import importlib.metadata as md; print(md.version('mcp'))"
```

Expected: the final command prints a `2.x` SDK version. Review `git diff -- uv.lock` before staging: preserve all pre-existing user hunks and stage only the v2 migration's package and project-requirement hunks.

- [ ] **Step 3: Replace the v1 imports and high-level class**

In `src/rag_mcp/server.py`, make these exact replacements:

```python
# Before
from mcp.server.fastmcp import FastMCP, Image

mcp = FastMCP("rag-mcp", log_level="WARNING")

# After
from mcp.server.mcpserver import Image, MCPServer

mcp = MCPServer("rag-mcp", log_level="WARNING")
```

Do not modify any `@mcp.tool()` function signature or its `_run(service()....)` delegation.

- [ ] **Step 4: Make the retained stdio transport explicit**

At the bottom of `server.main()`, replace the final call with:

```python
    mcp.run(transport="stdio")
```

Keep the preflight, `redirect_logs_to_file`, and warmup ordering exactly as it is.

- [ ] **Step 5: Correct v1 naming in module comments only**

Make these limited wording replacements without changing behavior:

```python
# src/rag_mcp/server.py module docstring
"""MCPServer(SDK v2) 진입점 — §5 도구 등록. 스펙 §6.10.

# src/rag_mcp/server.py concurrency explanation
동시성: MCPServer의 비동기 도구는 이벤트 루프에서 실행된다.

# src/rag_mcp/service.py module docstring
server.py(MCPServer)·cli.py·테스트가 공통으로 사용한다(MCP 프레임워크 비의존).
```

- [ ] **Step 6: Run every Task 1 test and verify GREEN**

Run:

```powershell
uv run pytest tests/test_mcp_tools.py::test_mcp_sdk_v2_server_registers_the_eleven_tools tests/test_mcp_tools.py::test_mcp_sdk_v2_client_discovers_server_tools tests/test_mcp_tools.py::test_server_main_runs_mcpserver_over_stdio tests/test_page_image_tool.py::test_mcpserver_get_page_image_returns_text_and_actual_image_content tests/test_server_logging.py::test_request_logging_is_not_written_per_call -q
```

Expected: PASS. The v2 client reaches the actual decorator server in memory, the image call returns `CallToolResult.content`, and the main entrypoint invokes only `stdio`.

- [ ] **Step 7: Run the affected test modules**

Run:

```powershell
uv run pytest tests/test_mcp_tools.py tests/test_page_image_tool.py tests/test_server_logging.py -q
```

Expected: PASS, with the existing environment-gated real-server test skipped unless `RAG_RUN_SERVER_TESTS=1` is set.

- [ ] **Step 8: Commit only the SDK conversion**

First inspect the staged boundary:

```powershell
git diff --cached --check
git diff --cached --stat
```

Stage `pyproject.toml`, the three test files, `src/rag_mcp/server.py`, and `src/rag_mcp/service.py`. Stage only the v2 migration hunks in `uv.lock`; do not include a pre-existing unrelated lock-file removal. Then commit:

```powershell
git commit -m "feat: migrate server to MCP SDK v2"
```

Expected: one commit whose changed files all trace to the SDK v2 transition.

### Task 3: Update operator documentation and verify real stdio behavior

**Files:**
- Modify: `MCP_연동가이드.md:1-16`
- Modify: `PROGRESS.md` (only the SDK migration status and latest verification result)

**Interfaces:**
- Consumes: Task 2's `rag-mcp serve` entrypoint and the unchanged client configuration.
- Produces: v2-accurate setup guidance and documented evidence that a legacy stdio client can still discover and call the server.

- [ ] **Step 1: Update the MCP connection guide's server description**

Replace the opening transport sentence with:

```markdown
- 서버는 MCP Python SDK v2의 `MCPServer("rag-mcp").run(transport="stdio")`로 실행된다.
  클라이언트가 서버 프로세스를 직접 띄우고 stdin/stdout으로 JSON-RPC를 주고받는다.
```

Replace the startup-order mention with:

```markdown
- 기동 순서는 **preflight(동기) → warmup(백그라운드 스레드) → `mcp.run(transport="stdio")`** 이다.
```

Keep the absolute-path client examples unchanged. Correct the guide's stale tool count to 11 and add the currently omitted `set_source_path` row without changing any tool signature.

Use this heading and table row:

```markdown
## 5. 노출되는 도구 11개
| `set_source_path` | 원본 PDF를 옮겼을 때 기록된 경로만 갱신한다. PDF 내용이 바뀐 경우에는 `reindex_document(reparse=True)`를 사용한다 |
```

- [ ] **Step 2: Run the real unread-stderr stdio regression test**

Run:

```powershell
$env:RAG_RUN_SERVER_TESTS = "1"
uv run pytest tests/test_server_logging.py::test_server_survives_unread_stderr -q
Remove-Item Env:RAG_RUN_SERVER_TESTS
```

Expected: PASS. The test starts `rag-mcp serve`, completes a legacy `initialize` handshake, and receives 60 `list_documents` responses while its stderr pipe remains unread.

- [ ] **Step 3: Run the full project suite**

Run:

```powershell
uv run pytest -q
```

Expected: PASS, aside from documented skips and the pre-existing local-Qdrant payload-index warning.

- [ ] **Step 4: Check the final diff and document the milestone**

Run:

```powershell
git diff --check
git status --short
```

In `PROGRESS.md`, add a dated completed entry containing all of the following facts:

```markdown
- MCP Python SDK v2 전환 완료: `FastMCP`를 `MCPServer`로 교체했고 `mcp>=2,<3`을 사용한다.
- 도구 11개와 `rag-mcp serve`의 stdio 실행 방식은 유지했다.
- SDK v2 in-memory Client, 이미지 콘텐츠, 읽히지 않는 stderr의 실제 stdio 회귀, 전체 pytest를 검증했다.
```

Do not alter the separate Qdrant server-migration next task or any earlier user-authored progress entry.

- [ ] **Step 5: Commit only migration documentation and progress state**

Run:

```powershell
git add MCP_연동가이드.md PROGRESS.md
git diff --cached --check
git commit -m "docs: document MCP SDK v2 migration"
```

Expected: one documentation commit. If `PROGRESS.md` contains an unrelated pre-existing modification that cannot be separated cleanly, do not commit it; report that boundary instead of including user work.

## Final Verification Checklist

- [ ] `uv run python -c "import importlib.metadata as md; print(md.version('mcp'))"` prints `2.x`.
- [ ] `uv run pytest -q` passes.
- [ ] `RAG_RUN_SERVER_TESTS=1` stdio regression passes.
- [ ] The server reports exactly eleven tools through both `MCPServer.list_tools()` and v2 `Client.list_tools()`.
- [ ] `get_page_image` returns one text block and one `ImageContent` whose Python field is `mime_type`.
- [ ] No data, Qdrant store, manifest, or pre-existing unrelated workspace change was modified or committed.
