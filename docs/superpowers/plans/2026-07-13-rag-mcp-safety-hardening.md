# RAG MCP Safety Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent stale-PDF indexing, path escape, empty-document deletion, failed replacement data loss, concurrent mutation races, and duplicate embedding-model loads without changing retrieval algorithms.

**Architecture:** Keep the existing Config → Service → Indexer → VectorStore boundaries. Add validation at Config path boundaries, safe replacement primitives in VectorStore, a non-blocking mutation lease in RagService, and a per-model backend cache shared by Retriever and Indexer.

**Tech Stack:** Python 3.11/3.12, pytest 8, Pydantic 2, Qdrant local, FastMCP, uv.

## Global Constraints

- Preserve the existing `uv.lock` working-tree change and never stage it.
- Do not add dependencies or change retrieval fusion, tokenization, chunk size, or result schema.
- Keep Qdrant local single-writer behavior; searches must remain available while an in-process mutation is reserved.
- Use `apply_patch` for source and test edits.
- For every task: write the regression test first, observe the expected failure, implement the smallest fix, run the focused tests, then commit only that task's files.
- Install pytest into the existing environment without updating the lockfile: `uv pip install --python .\.venv\Scripts\python.exe "pytest>=8.0.0"`.

---

### Task 1: Document ID and configuration boundaries

**Files:**
- Modify: `src/rag_mcp/config.py`
- Test: `tests/test_config_models.py`

**Interfaces:**
- Produces: `validate_document_id(document_id: str) -> str`
- Produces: `Config.__post_init__() -> None`
- Produces: Qdrant path default derived from `RAG_DATA_DIR` when `RAG_QDRANT_PATH` is absent

- [ ] **Step 1: Write failing validation and path tests**

```python
@pytest.mark.parametrize("bad", ["", ".", "..", "../escape", r"..\escape", "C:escape", "bad\x00id"])
def test_document_id_rejects_path_escape(tmp_path, bad):
    cfg = Config(data_dir=tmp_path / "data")
    with pytest.raises(ValueError, match="document_id"):
        cfg.parsed_doc_dir(bad)
    with pytest.raises(ValueError, match="document_id"):
        cfg.manifest_path(bad)


def test_document_id_allows_korean_spaces_and_parentheses(tmp_path):
    cfg = Config(data_dir=tmp_path / "data")
    assert cfg.parsed_doc_dir("2026 예산지침(최종)").is_relative_to(cfg.parsed_dir)


def test_qdrant_path_follows_data_dir_when_not_overridden(monkeypatch, tmp_path):
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path / "custom"))
    monkeypatch.delenv("RAG_QDRANT_PATH", raising=False)
    cfg = Config()
    assert cfg.qdrant_path == cfg.data_dir / "qdrant"


def test_qdrant_path_explicit_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path / "custom"))
    monkeypatch.setenv("RAG_QDRANT_PATH", str(tmp_path / "vectors"))
    assert Config().qdrant_path == (tmp_path / "vectors").resolve()


def test_invalid_qdrant_mode_fails_before_store_open():
    with pytest.raises(ValueError, match="local|server"):
        Config(qdrant_mode="sever")
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_config_models.py -q`

Expected: FAIL because path-like IDs are accepted, Qdrant path does not follow the data root, and invalid mode does not raise.

- [ ] **Step 3: Implement central validation and derived defaults**

```python
def validate_document_id(document_id: str) -> str:
    if not isinstance(document_id, str) or not document_id or document_id in {".", ".."}:
        raise ValueError(f"안전하지 않은 document_id: {document_id!r}")
    if any(ch in document_id for ch in "/\\:") or any(ord(ch) < 32 for ch in document_id):
        raise ValueError(f"안전하지 않은 document_id: {document_id!r}")
    return document_id


def _default_qdrant_path() -> Path:
    explicit = os.environ.get("RAG_QDRANT_PATH", "").strip()
    if explicit:
        return Path(explicit).resolve()
    return (Path(_env("RAG_DATA_DIR", "./data")).resolve() / "qdrant").resolve()
```

Use `_default_qdrant_path` as the `qdrant_path` factory. In `Config.__post_init__`, reject modes outside `{"local", "server"}` and embedding models outside `COLLECTION_BY_MODEL`. Call `validate_document_id` from `parsed_doc_dir` and `manifest_path`, then verify the resolved target remains below its base directory with `Path.is_relative_to`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_config_models.py tests/test_manifest.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```powershell
git add -- src/rag_mcp/config.py tests/test_config_models.py
git commit -m "fix: 문서 ID와 Qdrant 설정 경계 검증"
```

---

### Task 2: Fresh PDF parsing, empty-chunk guard, and atomic JSONL

**Files:**
- Modify: `src/rag_mcp/pdf_parser.py`
- Modify: `src/rag_mcp/service.py`
- Modify: `src/rag_mcp/indexer.py`
- Test: `tests/test_pdf_parser.py`
- Test: `tests/test_mcp_tools.py`

**Interfaces:**
- Produces: force parsing that selects only a JSON created or changed by the current conversion
- Produces: `Indexer.index_chunks` precondition that every input chunk has nonblank text
- Produces: atomic `Indexer.save_chunks`

- [ ] **Step 1: Write failing fresh-parse and empty-input tests**

```python
def test_force_parse_selects_json_changed_by_current_conversion(monkeypatch, tmp_path):
    cfg = Config(data_dir=tmp_path / "data", ocr_mode="off")
    pdf = tmp_path / "new.pdf"
    pdf.write_bytes(b"%PDF")
    out = cfg.parsed_doc_dir("same")
    out.mkdir(parents=True)
    (out / "old.json").write_text('{"title":"old","kids":[]}', encoding="utf-8")

    def fake_convert(**kwargs):
        target = Path(kwargs["output_dir"]) / "new.json"
        target.write_text('{"title":"new","kids":[]}', encoding="utf-8")

    monkeypatch.setattr("opendataloader_pdf.convert", fake_convert)
    parsed = parse_pdf(pdf, "same", cfg, force=True)
    assert parsed.title == "new"
    assert parsed.json_path.name == "new.json"


def test_ingest_pdf_forces_fresh_parse(svc, monkeypatch, tmp_path):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF")
    seen = []

    def fake_parse(path, doc_id, config, *, fiscal_year=None, doc_name=None, force=False):
        seen.append(force)
        return [Chunk(chunk_id=f"{doc_id}::c0", document_id=doc_id, text="new")], {}

    monkeypatch.setattr("rag_mcp.pipeline.parse_and_chunk", fake_parse)
    assert svc.ingest_pdf(str(pdf), document_id="doc")["ok"] is True
    assert seen == [True]


def test_empty_reindex_preserves_existing_document(svc):
    _seed(svc)
    old_manifest = svc.manifests.read("d1")
    old_chunks = (svc.config.parsed_doc_dir("d1") / "chunks.jsonl").read_bytes()
    with pytest.raises(ValueError, match="청크"):
        svc.ingest_chunks("d1", [])
    assert svc.get_chunk("d1::c0")["ok"] is True
    assert svc.manifests.read("d1") == old_manifest
    assert (svc.config.parsed_doc_dir("d1") / "chunks.jsonl").read_bytes() == old_chunks
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_pdf_parser.py::test_force_parse_selects_json_changed_by_current_conversion tests/test_mcp_tools.py::test_ingest_pdf_forces_fresh_parse tests/test_mcp_tools.py::test_empty_reindex_preserves_existing_document -q`

Expected: FAIL because force output selection is alphabetical, fresh ingest passes `force=False`, and empty input reaches deletion.

- [ ] **Step 3: Implement current-conversion output selection**

```python
def _output_signatures(out_dir: Path, ext: str) -> dict[Path, tuple[int, int]]:
    return {
        path.resolve(): (path.stat().st_mtime_ns, path.stat().st_size)
        for path in out_dir.glob(f"*.{ext}")
    }


def _find_changed_output(
    out_dir: Path, ext: str, before: dict[Path, tuple[int, int]]
) -> Optional[Path]:
    changed = []
    for path in out_dir.glob(f"*.{ext}"):
        signature = (path.stat().st_mtime_ns, path.stat().st_size)
        if before.get(path.resolve()) != signature:
            changed.append(path)
    return max(changed, key=lambda path: path.stat().st_mtime_ns) if changed else None
```

Capture signatures before `opendataloader_pdf.convert`. When `force=True`, require `_find_changed_output(..., "json", before)` and raise a clear `RuntimeError` when no current output exists. Pass `force=True` from fresh `RagService.ingest_pdf`.

- [ ] **Step 4: Implement empty guard and atomic JSONL**

At the first line of `index_chunks`, raise `ValueError("색인할 유효 청크가 없습니다")` when the list is empty or any chunk text is blank. Replace direct JSONL writing with:

```python
tmp = p.with_suffix(p.suffix + ".tmp")
try:
    with tmp.open("w", encoding="utf-8") as stream:
        for chunk in chunks:
            stream.write(chunk.model_dump_json() + "\n")
    os.replace(tmp, p)
finally:
    if tmp.exists():
        tmp.unlink()
```

- [ ] **Step 5: Add atomic-write failure test and run GREEN**

Patch `Chunk.model_dump_json` to raise while writing a replacement, then assert the prior `chunks.jsonl` bytes remain unchanged. Run:

`.\.venv\Scripts\python.exe -m pytest tests/test_pdf_parser.py tests/test_mcp_tools.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add -- src/rag_mcp/pdf_parser.py src/rag_mcp/service.py src/rag_mcp/indexer.py tests/test_pdf_parser.py tests/test_mcp_tools.py
git commit -m "fix: 신규 PDF와 청크 캐시를 안전하게 교체"
```

---

### Task 3: Preserve old Qdrant points until replacement succeeds

**Files:**
- Modify: `src/rag_mcp/vector_store.py`
- Modify: `src/rag_mcp/indexer.py`
- Test: `tests/test_vector_store.py`
- Test: `tests/test_mcp_tools.py`

**Interfaces:**
- Produces: `VectorStore.point_ids_by_document(document_id: str) -> set[str | int]`
- Produces: `VectorStore.delete_point_ids(point_ids: set[str | int]) -> None`

- [ ] **Step 1: Write failing replacement tests**

```python
def test_upsert_failure_preserves_existing_points(svc, monkeypatch):
    _seed(svc)
    store = svc._indexer("kure").store
    monkeypatch.setattr(store, "upsert_chunks", lambda chunks, vecs: (_ for _ in ()).throw(RuntimeError("upsert")))
    with pytest.raises(RuntimeError, match="upsert"):
        svc.ingest_chunks("d1", [Chunk(chunk_id="d1::c0", document_id="d1", text="replacement")])
    assert svc.get_chunk("d1::c0")["ok"] is True
    assert svc.get_chunk("d1::c1")["ok"] is True


def test_successful_replacement_deletes_only_stale_points(svc):
    _seed(svc)
    svc.ingest_chunks("d1", [Chunk(chunk_id="d1::c0", document_id="d1", text="replacement")])
    assert svc.get_chunk("d1::c0")["text"] == "replacement"
    assert svc.get_chunk("d1::c1")["ok"] is False
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_mcp_tools.py::test_upsert_failure_preserves_existing_points tests/test_mcp_tools.py::test_successful_replacement_deletes_only_stale_points -q`

Expected: first test FAIL because old points are deleted before the injected upsert error.

- [ ] **Step 3: Implement point listing and targeted deletion**

Use Qdrant `scroll` under the existing RLock to collect point IDs for the `document_id` filter. Delete only an explicit nonempty ID set:

```python
def point_ids_by_document(self, document_id: str) -> set[str | int]:
    with self._lock:
        if not self.client.collection_exists(self.collection):
            return set()
        found: set[str | int] = set()
        offset = None
        while True:
            points, offset = self.client.scroll(
                collection_name=self.collection,
                scroll_filter=models.Filter(
                    must=[models.FieldCondition(
                        key="document_id", match=models.MatchValue(value=document_id)
                    )]
                ),
                limit=256,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            found.update(point.id for point in points)
            if offset is None:
                return found


def delete_point_ids(self, point_ids: set[str | int]) -> None:
    if not point_ids:
        return
    with self._lock:
        if not self.client.collection_exists(self.collection):
            return
        self.client.delete(
            collection_name=self.collection,
            points_selector=models.PointIdsList(points=list(point_ids)),
        )
```

- [ ] **Step 4: Change Indexer replacement order**

```python
old_point_ids = self.store.point_ids_by_document(document_id)
vecs = self.backend.embed_documents([chunk.text for chunk in chunks])
self.manifests.update(document_id, status="embedded", num_chunks=len(chunks))
self.store.upsert_chunks(chunks, vecs)
new_point_ids = {point_id_for(chunk.chunk_id) for chunk in chunks}
self.store.delete_point_ids(old_point_ids - new_point_ids)
```

Import `point_id_for` from `vector_store` and remove the pre-upsert `delete_document` call.

- [ ] **Step 5: Run focused and vector-store tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_vector_store.py tests/test_mcp_tools.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add -- src/rag_mcp/vector_store.py src/rag_mcp/indexer.py tests/test_vector_store.py tests/test_mcp_tools.py
git commit -m "fix: Qdrant 문서 교체 실패 시 기존 포인트 보존"
```

---

### Task 4: Non-blocking mutation lease

**Files:**
- Modify: `src/rag_mcp/service.py`
- Test: `tests/test_jobs.py`
- Test: `tests/test_mcp_tools.py`

**Interfaces:**
- Produces: `_reserve_mutation(operation: str, job_id: str | None = None) -> tuple[str | None, dict | None]`
- Produces: `_release_mutation(token: str) -> None`
- Produces: busy response with `ok=False`, `status="busy"`, active `operation`, and optional `job_id`

- [ ] **Step 1: Write failing concurrency tests**

Start a background ingest whose internal ingest function waits on an Event. While it is waiting, assert mutation tools are busy and search still works:

```python
def test_active_ingest_rejects_delete_and_reindex_but_allows_search(svc, tmp_path, monkeypatch):
    _seed(svc)
    pdf = tmp_path / "slow.pdf"
    pdf.write_bytes(b"%PDF")
    started = threading.Event()
    release = threading.Event()

    def slow_ingest(*args, **kwargs):
        started.set()
        assert release.wait(timeout=5)
        return {"ok": True, "document_id": "slow", "num_chunks": 1, "status": "done"}

    monkeypatch.setattr(svc, "_ingest_pdf_unlocked", slow_ingest)
    submitted = svc.submit_ingest(str(pdf), document_id="slow")
    assert started.wait(timeout=2)
    assert svc.delete_document("d1", confirm=True)["status"] == "busy"
    assert svc.reindex_document("d1")["status"] == "busy"
    assert svc.search_documents("일반수용비")
    assert svc.manifests.read("d1") is not None
    release.set()
    assert _poll(svc, submitted["job_id"])["status"] == "done"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_jobs.py -q`

Expected: FAIL because delete and reindex currently execute during the ingest job.

- [ ] **Step 3: Implement the mutation reservation**

Store `_active_mutation: dict | None` behind `_mutation_state_lock`. Reservation creates a UUID token only when no active mutation exists. Release clears state only when the token matches:

```python
def _reserve_mutation(
    self, operation: str, job_id: str | None = None
) -> tuple[str | None, dict | None]:
    with self._mutation_state_lock:
        if self._active_mutation is not None:
            active = {
                key: value for key, value in self._active_mutation.items()
                if key != "token" and value is not None
            }
            return None, {
                "ok": False,
                "status": "busy",
                "error": "다른 쓰기 작업이 진행 중입니다. 완료 후 다시 시도하세요.",
                **active,
            }
        token = uuid.uuid4().hex
        self._active_mutation = {
            "token": token,
            "operation": operation,
            "job_id": job_id,
        }
        return token, None


def _attach_mutation_job_id(self, token: str, job_id: str) -> None:
    with self._mutation_state_lock:
        if self._active_mutation and self._active_mutation["token"] == token:
            self._active_mutation["job_id"] = job_id


def _release_mutation(self, token: str) -> None:
    with self._mutation_state_lock:
        if self._active_mutation and self._active_mutation["token"] == token:
            self._active_mutation = None
```

Split public mutation methods into guarded wrappers and private unguarded implementations:

```python
def ingest_pdf(...):
    token, busy = self._reserve_mutation("ingest_pdf")
    if busy:
        return busy
    try:
        return self._ingest_pdf_unlocked(...)
    finally:
        self._release_mutation(token)
```

Use the same wrapper pattern for `ingest_chunks`, `delete_document`, and `reindex_document`. The private PDF implementation calls the private chunks implementation to avoid nested reservation. `submit_ingest` reserves before starting its thread; its worker calls `_ingest_pdf_unlocked` and releases in `finally`:

```python
with self._submit_lock:
    token, busy = self._reserve_mutation("ingest_pdf")
    if busy:
        return busy
    try:
        job = self.jobs.create(document_id=doc_id)
        self._attach_mutation_job_id(token, job.job_id)
    except Exception:
        self._release_mutation(token)
        raise
```

- [ ] **Step 4: Update existing job test monkeypatches**

Patch `_ingest_pdf_unlocked` rather than `ingest_pdf` in tests that simulate background work. Keep the public CLI path covered by existing service tests.

- [ ] **Step 5: Run focused tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_jobs.py tests/test_mcp_tools.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```powershell
git add -- src/rag_mcp/service.py tests/test_jobs.py tests/test_mcp_tools.py
git commit -m "fix: 색인 중 쓰기 작업 경합 차단"
```

---

### Task 5: Shared embedding backend and configured default model

**Files:**
- Modify: `src/rag_mcp/service.py`
- Modify: `src/rag_mcp/server.py`
- Modify: `src/rag_mcp/cli.py`
- Test: `tests/test_mcp_tools.py`
- Test: `tests/test_config_models.py`

**Interfaces:**
- Produces: `_embedding_backend(model: str) -> EmbeddingBackend`
- Changes: all optional service/server/CLI embedding-model inputs use `Config.embedding_model` when omitted

- [ ] **Step 1: Write failing backend-sharing and default-model tests**

Monkeypatch `rag_mcp.service.get_backend` with a factory that records calls. Build Retriever and Indexer for `kure` and assert one factory call and object identity for both backends. Build a service with `Config(embedding_model="bge_m3")`, omit model arguments during ingest/search, and assert the manifest uses `bge_m3`:

```python
def test_service_shares_backend_per_model(tmp_path, monkeypatch, fake_backend):
    created = []

    def factory(model):
        created.append(model)
        return fake_backend

    monkeypatch.setattr("rag_mcp.service.get_backend", factory)
    cfg = Config(data_dir=tmp_path, qdrant_path=tmp_path / "qdrant")
    svc = RagService(cfg)
    retriever = svc._retriever("kure")
    indexer = svc._indexer("kure")
    assert created == ["kure"]
    assert retriever.backend is indexer.backend


def test_configured_embedding_model_is_default(tmp_path, fake_backend):
    cfg = Config(
        data_dir=tmp_path,
        qdrant_path=tmp_path / "qdrant",
        embedding_model="bge_m3",
    )
    svc = RagService(cfg, backend=fake_backend)
    svc.ingest_chunks("model-default", [
        Chunk(chunk_id="model-default::c0", document_id="model-default", text="본문")
    ])
    assert svc.manifests.read("model-default").embedding_model == "bge_m3"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_mcp_tools.py::test_service_shares_backend_per_model tests/test_mcp_tools.py::test_configured_embedding_model_is_default -q`

Expected: FAIL because Retriever and Indexer construct separate backends and service signatures default to `kure`.

- [ ] **Step 3: Cache and inject backends**

Import `get_backend` in `service.py`, add `_backends: dict[str, EmbeddingBackend]`, and create each production backend once under `_resource_lock`. Preserve the existing injected fake backend behavior. Pass `_embedding_backend(model)` explicitly to both Retriever and Indexer.

- [ ] **Step 4: Resolve optional model arguments**

Change service and server signatures from `embedding_model: str = "kure"` to `embedding_model: str | None = None`. At each service boundary use:

```python
model = embedding_model or self.config.embedding_model
```

Pass `model` to Retriever/Indexer. Change CLI `--embedding-model` defaults to `None` so the environment-backed Config value is not overwritten.

- [ ] **Step 5: Run focused and MCP contract tests**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_config_models.py tests/test_mcp_tools.py tests/test_jobs.py -q`

Expected: PASS and exactly nine MCP tools remain registered.

- [ ] **Step 6: Commit**

```powershell
git add -- src/rag_mcp/service.py src/rag_mcp/server.py src/rag_mcp/cli.py tests/test_mcp_tools.py tests/test_config_models.py
git commit -m "fix: 임베딩 백엔드와 기본 모델 설정 일관화"
```

---

### Task 6: Documentation and final verification

**Files:**
- Modify: `README.md`
- Modify: `PROGRESS.md`
- Modify only if current counts are stale: `MCP_연동가이드.md`

**Interfaces:**
- Documents: derived Qdrant path, fresh-ingest behavior, busy mutation behavior, and nine-tool contract

- [ ] **Step 1: Update only behavior changed by Tasks 1–5**

State that `RAG_QDRANT_PATH` defaults to `<RAG_DATA_DIR>/qdrant`, fresh `ingest_pdf` reparses the supplied PDF, and mutation tools return busy during an active ingest. Do not add the deferred prepare-model or progress features.

- [ ] **Step 2: Run the entire test suite**

Run: `.\.venv\Scripts\python.exe -m pytest -q`

Expected: all non-environment tests PASS; environment/model tests may remain explicitly skipped under their existing markers.

- [ ] **Step 3: Run targeted structural checks**

Run: `git diff --check`

Expected: no whitespace errors in task files. Ignore only the pre-existing `uv.lock` line-ending warning.

Run: `git status --short`

Expected: only documentation files for this task plus the pre-existing unstaged `uv.lock` before the final docs commit.

- [ ] **Step 4: Commit documentation**

```powershell
git add -- README.md PROGRESS.md MCP_연동가이드.md docs/superpowers/plans/2026-07-13-rag-mcp-safety-hardening.md
git commit -m "docs: 데이터 안전성 변경과 검증 결과 반영"
```

- [ ] **Step 5: Review and publish**

Use `requesting-code-review` and `verification-before-completion`. Confirm `git diff HEAD^..HEAD` and the full branch diff exclude `uv.lock`, then push the current branch to `origin` as requested.
