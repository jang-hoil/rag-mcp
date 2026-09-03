# Qdrant HTTP Server Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the existing local-path Qdrant collections to a localhost Qdrant HTTP server so independent Codex and Claude stdio MCP processes can search the same index concurrently.

**Architecture:** Keep MCP Python SDK v2 and the existing stdio transport unchanged. Install one Qdrant 1.18.x Windows process bound to `127.0.0.1:6333`, copy local points over the Qdrant client API without re-embedding, then point every RAG MCP process at that HTTP endpoint.

**Tech Stack:** Python 3.11, uv, qdrant-client 1.18.0, Qdrant Server 1.18.2 for Windows, pytest, PowerShell, Windows Task Scheduler, MCP Python SDK 2.0.0.

## Global Constraints

- Preserve `D:\User\Desktop\rag mcp\data\qdrant` and create a timestamped backup before migration.
- Never copy the local Qdrant directory into `D:\qdrant\storage`; transfer records through the client APIs.
- Dry-run is the default; writes require `--apply`.
- Preserve point IDs, dense and sparse vectors, payloads, vector schema, and collection names without re-embedding.
- Bind Qdrant to localhost only at `http://127.0.0.1:6333`.
- Keep the RAG MCP transport as stdio and keep all 11 tool interfaces unchanged.
- Preserve unrelated user changes in `PROGRESS.md`, `data/`, and `pdf/색인목록.md`.
- Follow test-first development for Python behavior, stop after four consecutive failures, and commit in small units.
- Run the complete default test suite, gated server tests, actual migration checks, and two-process MCP verification before claiming completion.
- Push the completed `codex/qdrant-http-server` branch to GitHub `origin`.

---

### Task 1: Add the local-to-server migration engine

**Files:**
- Create: `scripts/migrate_local_to_server.py`
- Create: `tests/test_local_to_server_migration.py`

**Interfaces:**
- Consumes: `qdrant_client.QdrantClient`, local path, server URL.
- Produces: `CollectionSummary`, `inspect_collections(client)`, `migrate_collections(source, target, apply, batch_size=128)` and a CLI `main(argv=None) -> int`.

- [ ] **Step 1: Write failing tests for inspection and dry-run**

```python
def test_inspect_collections_reads_exact_counts_and_schema():
    source = FakeClient(collections={"rag_one": [record("p1")]})
    summaries = inspect_collections(source)
    assert summaries[0].name == "rag_one"
    assert summaries[0].points_count == 1


def test_dry_run_never_creates_or_upserts():
    source = FakeClient(collections={"rag_one": [record("p1")]})
    target = FakeClient()
    report = migrate_collections(source, target, apply=False)
    assert report[0].source_count == 1
    assert target.write_calls == []
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `uv run pytest -q tests/test_local_to_server_migration.py`

Expected: collection error because `scripts.migrate_local_to_server` does not exist.

- [ ] **Step 3: Implement inspection and dry-run**

```python
@dataclass(frozen=True)
class CollectionSummary:
    name: str
    points_count: int
    vectors_config: Any
    sparse_vectors_config: Any


def inspect_collections(client) -> list[CollectionSummary]:
    names = sorted(item.name for item in client.get_collections().collections)
    result = []
    for name in names:
        info = client.get_collection(name)
        count = client.count(name, exact=True).count
        result.append(CollectionSummary(
            name=name,
            points_count=count,
            vectors_config=info.config.params.vectors,
            sparse_vectors_config=info.config.params.sparse_vectors,
        ))
    return result
```

`migrate_collections(..., apply=False)` returns source summaries without calling any target write method.

- [ ] **Step 4: Run the focused tests and confirm GREEN**

Run: `uv run pytest -q tests/test_local_to_server_migration.py`

Expected: all current tests pass.

- [ ] **Step 5: Write failing tests for point copying and target validation**

```python
def test_apply_preserves_ids_vectors_payloads_and_adds_filter_indexes():
    source = FakeClient(collections={"rag_one": [record("p1")]})
    target = FakeClient()
    report = migrate_collections(source, target, apply=True, batch_size=1)
    point = target.upserted["rag_one"][0]
    assert point.id == "p1"
    assert point.vector == source.records["rag_one"][0].vector
    assert point.payload == source.records["rag_one"][0].payload
    assert target.indexes["rag_one"] == {
        ("fiscal_year", models.PayloadSchemaType.INTEGER),
        ("document_id", models.PayloadSchemaType.KEYWORD),
    }
    assert report[0].target_count == 1


def test_apply_rejects_nonempty_target_before_any_write():
    source = FakeClient(collections={"rag_one": [record("p1")]})
    target = FakeClient(collections={"rag_one": [record("existing")]})
    with pytest.raises(RuntimeError, match="비어 있지"):
        migrate_collections(source, target, apply=True)
    assert target.write_calls == []


def test_apply_fails_when_final_count_differs():
    source = FakeClient(collections={"rag_one": [record("p1")]})
    target = FakeClient(drop_upserts=True)
    with pytest.raises(RuntimeError, match="포인트 수 불일치"):
        migrate_collections(source, target, apply=True)
```

- [ ] **Step 6: Run the new tests and confirm RED**

Run: `uv run pytest -q tests/test_local_to_server_migration.py`

Expected: failures because target validation, point copying, indexes, and count verification are not implemented.

- [ ] **Step 7: Implement minimal safe APPLY behavior**

For each collection, reject a non-empty existing target, create a missing collection using the source dense and sparse configs, copy scroll pages with `with_payload=True` and `with_vectors=True`, create the two filter indexes, and compare exact counts. Do not delete source or target collections.

```python
for batch in iter_points(source, summary.name, batch_size):
    target.upsert(
        collection_name=summary.name,
        points=[models.PointStruct(id=p.id, vector=p.vector, payload=p.payload) for p in batch],
        wait=True,
    )
target.create_payload_index(summary.name, "fiscal_year", models.PayloadSchemaType.INTEGER)
target.create_payload_index(summary.name, "document_id", models.PayloadSchemaType.KEYWORD)
actual = target.count(summary.name, exact=True).count
if actual != summary.points_count:
    raise RuntimeError(
        f"{summary.name}: 포인트 수 불일치(source={summary.points_count}, target={actual})"
    )
```

- [ ] **Step 8: Run tests and commit Task 1**

Run: `uv run pytest -q tests/test_local_to_server_migration.py`

Expected: all migration tests pass.

Commit:

```powershell
git add -- scripts/migrate_local_to_server.py tests/test_local_to_server_migration.py
git commit -m "feat: add safe local-to-server Qdrant migration"
```

---

### Task 2: Add CLI guardrails and real-client integration coverage

**Files:**
- Modify: `scripts/migrate_local_to_server.py`
- Modify: `tests/test_local_to_server_migration.py`

**Interfaces:**
- Consumes: Task 1 migration engine.
- Produces: `build_parser()`, validated `main(argv=None) -> int`, and a scratch local-to-local integration test that proves vector and payload preservation without touching live data.

- [ ] **Step 1: Write failing CLI tests**

```python
def test_parser_defaults_to_dry_run():
    args = build_parser().parse_args([
        "--source-path", "source", "--target-url", "http://127.0.0.1:6333"
    ])
    assert args.apply is False


def test_main_rejects_source_path_equal_to_server_storage(tmp_path, capsys):
    result = main([
        "--source-path", str(tmp_path),
        "--server-storage-path", str(tmp_path),
        "--target-url", "http://127.0.0.1:6333",
        "--apply",
    ])
    assert result == 2
    assert "같을 수 없습니다" in capsys.readouterr().err
```

- [ ] **Step 2: Confirm RED, implement CLI validation, and confirm GREEN**

Run before implementation: `uv run pytest -q tests/test_local_to_server_migration.py -k "parser or main"`

Expected: missing parser options and validation failure.

The CLI requires absolute `--source-path`, `--target-url`, and `--server-storage-path`; `--apply` remains optional. It checks that source exists, source and server storage resolve to different paths, and target `/healthz` is reachable before opening the local client for APPLY.

Run after implementation: `uv run pytest -q tests/test_local_to_server_migration.py`

Expected: all migration tests pass.

- [ ] **Step 3: Add a real qdrant-client scratch integration test**

Create `QdrantClient(path=str(tmp_path / "source"))` and
`QdrantClient(path=str(tmp_path / "target"))`. Add one source collection with named dense and
sparse vectors, one payload, and one point; run `migrate_collections(..., apply=True)` and assert
the target count, vector values, and payload. This real-client test stays in the default suite and
does not open the live data directory.

- [ ] **Step 4: Run migration tests and commit Task 2**

Run: `uv run pytest -q tests/test_local_to_server_migration.py`

Expected: all tests pass with any explicitly gated HTTP test skipped when no server URL is supplied.

Commit:

```powershell
git add -- scripts/migrate_local_to_server.py tests/test_local_to_server_migration.py
git commit -m "test: harden Qdrant server migration workflow"
```

---

### Task 3: Document server-mode operation and rollback

**Files:**
- Modify: `README.md`
- Modify: `MCP_연동가이드.md`

**Interfaces:**
- Consumes: Task 2 CLI syntax and fixed runtime paths.
- Produces: installation, health check, migration, configuration, dual-client verification, and rollback commands.

- [ ] **Step 1: Add exact operator commands**

Document these values consistently:

```text
Qdrant executable: D:\qdrant\qdrant.exe
Qdrant config: D:\qdrant\config\production.yaml
Qdrant storage: D:\qdrant\storage
Qdrant URL: http://127.0.0.1:6333
Task Scheduler name: rag-mcp-qdrant
```

Include the migration command:

```powershell
uv run python scripts/migrate_local_to_server.py `
  --source-path 'D:\User\Desktop\rag mcp\data\qdrant' `
  --server-storage-path 'D:\qdrant\storage' `
  --target-url 'http://127.0.0.1:6333'
```

and the same command with `--apply`. State that all `rag-mcp serve` processes must be stopped while reading local data.

- [ ] **Step 2: Add rollback instructions**

Document restoring the timestamped Codex and Claude config backups, setting `RAG_QDRANT_MODE=local`, removing `RAG_QDRANT_URL`, stopping the scheduled task, and starting only one stdio MCP process against the preserved local directory.

- [ ] **Step 3: Verify documentation consistency and commit**

Run:

```powershell
rg -n "127\.0\.0\.1:6333|migrate_local_to_server|rag-mcp-qdrant|RAG_QDRANT_MODE" README.md MCP_연동가이드.md
git diff --check
```

Expected: both documents contain matching paths and commands; `git diff --check` has no output.

Commit:

```powershell
git add -- README.md MCP_연동가이드.md
git commit -m "docs: add Qdrant server operations guide"
```

---

### Task 4: Install and register Qdrant 1.18.2

**Files outside Git:**
- Create: `D:\qdrant\qdrant.exe`
- Create: `D:\qdrant\config\production.yaml`
- Create: Windows scheduled task `rag-mcp-qdrant`

- [ ] **Step 1: Download the official Windows release asset**

Use GitHub's official `qdrant/qdrant` v1.18.2 release API to select
`qdrant-x86_64-pc-windows-msvc.zip`. Compare the downloaded SHA-256 with the API asset digest when present, then expand it into `D:\qdrant`.

- [ ] **Step 2: Write a localhost-only config**

```yaml
storage:
  storage_path: D:\qdrant\storage
service:
  host: 127.0.0.1
  http_port: 6333
  grpc_port: 6334
log_level: INFO
```

- [ ] **Step 3: Start and verify Qdrant manually**

Start hidden with `--config-path D:\qdrant\config\production.yaml`, redirect stdout and stderr to `D:\qdrant\logs`, and verify both `GET /healthz` and `GET /` report a live 1.18.2 server.

- [ ] **Step 4: Register the scheduled task**

Register `rag-mcp-qdrant` for the current user at logon with `qdrant.exe` as the direct action,
hidden execution, `MultipleInstances=IgnoreNew`, restart-on-failure, and working directory
`D:\qdrant`. Do not use a PowerShell wrapper because stopping the task can leave its child process
running. Start and stop the task once, then verify only one `qdrant.exe` process listens on port 6333.

---

### Task 5: Back up and migrate the live collections

**Files outside Git:**
- Create at execution time: `D:\User\Desktop\rag mcp\data\qdrant.bak-$(Get-Date -Format yyyyMMdd-HHmmss)`
- Write through HTTP: `D:\qdrant\storage`

- [ ] **Step 1: Stop only verified RAG MCP process trees**

Resolve exact `rag-mcp.exe serve` parent and child PIDs with `Get-CimInstance Win32_Process`; stop those PIDs without closing unrelated Python processes. Confirm no process command line still contains this workspace's `rag-mcp.exe serve`.

- [ ] **Step 2: Create and verify the backup**

Resolve the source and backup absolute paths and confirm both remain under `D:\User\Desktop\rag mcp\data`. Copy the complete local store, including `meta.json` and `collection\*`. Compare recursive file count and total bytes between source and backup.

- [ ] **Step 3: Run dry-run**

Run the documented command without `--apply` and record every collection name, vector schema, and exact source count. Confirm the target has no collections and the command reports no writes.

- [ ] **Step 4: Run APPLY and verify counts**

Run the same command with `--apply`. Query Qdrant HTTP for its collection list and exact point counts, and compare every collection with the dry-run report. Do not change MCP configuration if any count or schema differs.

---

### Task 6: Switch Codex, Claude, and CLI configuration

**Files outside Git:**
- Modify with timestamped backup: `C:\Users\User\.codex\config.toml`
- Modify with timestamped backup: `%APPDATA%\Claude\claude_desktop_config.json`
- Modify: current user's `RAG_QDRANT_MODE` and `RAG_QDRANT_URL` environment variables

- [ ] **Step 1: Back up and update Codex TOML**

Preserve all existing sections and add only these keys beneath `[mcp_servers.rag_mcp.env]`:

```toml
RAG_QDRANT_MODE = "server"
RAG_QDRANT_URL = "http://127.0.0.1:6333"
```

Parse the result with `tomllib` and confirm all pre-existing MCP server names remain.

- [ ] **Step 2: Back up and update Claude JSON**

Preserve all existing top-level preferences and MCP servers. Merge these keys into `mcpServers["rag-mcp"].env`:

```json
{
  "RAG_QDRANT_MODE": "server",
  "RAG_QDRANT_URL": "http://127.0.0.1:6333"
}
```

Parse the result with `json.loads` and confirm all pre-existing MCP server names remain.

- [ ] **Step 3: Set and verify user environment variables**

Set `RAG_QDRANT_MODE=server` and `RAG_QDRANT_URL=http://127.0.0.1:6333` for the current Windows user. Start fresh child processes and confirm `load_config()` reports server mode and the expected URL.

---

### Task 7: Run full verification, record status, and push

**Files:**
- Modify only task-owned lines if safely separable: `PROGRESS.md`

- [ ] **Step 1: Run the complete automated suite**

Run: `uv run pytest -q`

Expected: zero failures.

- [ ] **Step 2: Run gated stdio regression tests**

Run: `$env:RAG_RUN_SERVER_TESTS='1'; uv run pytest -q tests/test_server_logging.py; Remove-Item Env:RAG_RUN_SERVER_TESTS`

Expected: zero failures and no unread-stderr stall.

- [ ] **Step 3: Verify two independent stdio MCP processes**

Start two `rag-mcp.exe serve` processes with server-mode environment variables. For each process, send `initialize`, `notifications/initialized`, `tools/list`, `list_documents`, `collection_status`, and the fixed search query. Verify both expose 11 tools, see identical document/collection counts, return relevant “수지개선 효과” evidence in the same chunk order, and produce no local lock errors. Do not require the local-mode top amount because server-side sparse IDF changes ranking.

- [ ] **Step 4: Verify scheduled-start behavior**

Stop and start the scheduled task once, wait for `/healthz`, and confirm the migrated collection counts remain unchanged. Verify a second task start does not create another Qdrant process.

- [ ] **Step 5: Update status without absorbing unrelated edits**

If `PROGRESS.md` has pre-existing user changes that cannot be staged independently, leave it untouched and record the final operational facts in the Git-tracked documentation instead. Otherwise add the completion date, installed version, backup path, counts, config backup paths, task name, test totals, and rollback command.

- [ ] **Step 6: Final repository verification**

Run:

```powershell
git diff --check
git status --short
git log --oneline --decorate -8
```

Confirm only task-owned files are staged or committed.

- [ ] **Step 7: Commit final task-owned documentation and push**

```powershell
git add -- README.md MCP_연동가이드.md scripts/migrate_local_to_server.py tests/test_local_to_server_migration.py
git commit -m "chore: complete Qdrant server migration"
git push -u origin codex/qdrant-http-server
```

If every task-owned change is already committed, skip the empty commit and push the existing commits.
