"""PDF 파일명에 맞춰 문서 이름을 이관한다. 기본은 dry-run, 벡터 재임베딩 없음.

관리 작업 전 모든 MCP를 중지한다. 실패 시 원래 파일·컬렉션으로 복구한다.
백업은 원래 manifests/chunks, 전체 벡터·payload, 이동 전 parsed 폴더를 보존한다.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from qdrant_client import QdrantClient, models

from rag_mcp.config import Config, validate_document_id
from rag_mcp.manifest import ManifestStore, resolve_source_path
from rag_mcp.mutation_lock import DataMutationLock
from rag_mcp.vector_store import point_id_for


def _hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def build_plan(config, client, pdf_dir):
    local_pdfs = sorted(Path(pdf_dir).glob("*.pdf"))
    hashes = {}
    manifests = ManifestStore(config).list_all()
    plan, targets = [], set()
    for manifest in manifests:
        source = resolve_source_path(manifest.source_path)
        if source is None:
            raise ValueError(f"source_path missing: {manifest.document_id}")
        local = [pdf for pdf in local_pdfs if pdf.name == source.name]
        # 이름이 같은 파일도 내용이 다르면 원본 경로를 유지한다.
        if local and source.is_file() and _hash(source) != _hash(local[0]):
            local = []
        if not local and source.is_file():
            digest = _hash(source)
            for pdf in local_pdfs:
                hashes.setdefault(pdf, _hash(pdf))
                if hashes[pdf] == digest:
                    local.append(pdf)
        if len(local) > 1:
            raise ValueError(f"multiple identical PDF candidates: {manifest.document_id}")
        selected = (local[0] if local else source).resolve()
        new_id = validate_document_id(selected.stem)
        if new_id.casefold() in targets:
            raise ValueError(f"duplicate target document: {new_id}")
        targets.add(new_id.casefold())
        old_id = manifest.document_id
        active = client.collection_exists(config.collection_name(manifest.embedding_model, old_id))
        if new_id == old_id and manifest.doc_name == new_id and manifest.source_path == str(selected):
            continue
        if new_id != old_id:
            if config.parsed_doc_dir(new_id).exists() or config.manifest_path(new_id).exists():
                raise ValueError(f"target files already exist: {new_id}")
            if client.collection_exists(config.collection_name(manifest.embedding_model, new_id)):
                raise ValueError(f"target collection already exists: {new_id}")
        plan.append({
            "old_id": old_id, "new_id": new_id, "source_path": str(selected),
            "source_exists": selected.is_file(), "active": active,
            "embedding_model": manifest.embedding_model, "num_chunks": manifest.num_chunks,
        })
    return plan


def _records(client, collection):
    result, offset = [], None
    while True:
        points, offset = client.scroll(
            collection, limit=256, offset=offset, with_payload=True, with_vectors=True,
        )
        result.extend(points)
        if offset is None:
            return result


def _upsert(client, collection, records):
    for start in range(0, len(records), 128):
        client.upsert(collection_name=collection, points=[
            models.PointStruct(id=p.id, payload=p.payload, vector=p.vector)
            for p in records[start:start + 128]
        ], wait=True)


def _same_vectors(before, after):
    if before.keys() != after.keys():
        return False
    for name, vector in before.items():
        other = after[name]
        if isinstance(vector, list):
            # COSINE storage normalizes float32 vectors again on copy (observed <1e-8).
            if len(vector) != len(other) or any(abs(a - b) > 1e-7 for a, b in zip(vector, other)):
                return False
        elif vector != other:
            return False
    return True


def _create(client, collection, info):
    client.create_collection(
        collection_name=collection, vectors_config=info.config.params.vectors,
        sparse_vectors_config=info.config.params.sparse_vectors,
        hnsw_config=models.HnswConfigDiff(**info.config.hnsw_config.model_dump()),
        optimizers_config=models.OptimizersConfigDiff(**info.config.optimizer_config.model_dump()),
        on_disk_payload=info.config.params.on_disk_payload,
    )
    for field, schema in info.payload_schema.items():
        client.create_payload_index(collection, field, field_schema=schema.data_type, wait=True)


def _inside(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError(f"unsafe filesystem target: {path}")
    return path


def _payload(payload, change, config):
    out = deepcopy(payload)
    old, new = change["old_id"], change["new_id"]
    chunk_id = out.get("chunk_id", "")
    if not chunk_id.startswith(old + "::"):
        raise ValueError(f"unexpected chunk ID: {chunk_id}")
    out.update(document_id=new, doc_name=new, source_path=change["source_path"],
               chunk_id=new + chunk_id[len(old):])
    if out.get("page_image"):
        normalized = out["page_image"].replace("\\", "/")
        marker = "/parsed/" + old + "/"
        if marker in normalized:
            suffix = normalized.split(marker, 1)[1]
            first, separator, rest = suffix.partition("/")
            if first.endswith("_images"):
                suffix = new + "_images" + separator + rest
            destination = config.parsed_doc_dir(new) / suffix
            out["page_image"] = str(_inside(destination, config.parsed_doc_dir(new)))
    return out


def _atomic(path, text):
    # Keep temp names short: document titles and Windows parent paths can be long.
    descriptor, name = tempfile.mkstemp(prefix=".rn-", dir=path.parent)
    temp = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _prepare_folder(folder, change, config):
    """원본 parsed JSON/MD와 이미지 폴더의 이름·리소스 경로만 변경한다."""
    new, old = change["new_id"], change["old_id"]
    replacements = [(str(config.parsed_doc_dir(old)), str(config.parsed_doc_dir(new)))]
    image_dirs = [path for path in folder.iterdir() if path.is_dir() and path.name.endswith("_images")]
    if len(image_dirs) > 1:
        raise ValueError(f"ambiguous image outputs: {old}")
    if image_dirs:
        image_dir = image_dirs[0]
        replacements.append((image_dir.name, new + "_images"))
        if image_dir.name != new + "_images":
            source = _inside(image_dir, folder)
            destination = _inside(folder / (new + "_images"), folder)
            source.rename(destination)

    def rewrite(value):
        if isinstance(value, str):
            for before, after in replacements:
                value = value.replace(before, after).replace(before.replace("\\", "/"), after.replace("\\", "/"))
            return value
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, dict):
            return {key: rewrite(item) for key, item in value.items()}
        return value

    for extension in ("json", "md"):
        outputs = list(folder.glob(f"*.{extension}"))
        if len(outputs) > 1:
            raise ValueError(f"ambiguous parser outputs: {old}")
        for source in outputs:
            target = _inside(folder / f"{new}.{extension}", folder)
            if extension == "json":
                tree = rewrite(json.loads(source.read_text(encoding="utf-8")))
                if "file name" in tree:
                    tree["file name"] = Path(change["source_path"]).name
                text = json.dumps(tree, ensure_ascii=False)
            else:
                text = rewrite(source.read_text(encoding="utf-8"))
            _atomic(source, text)
            if source != target:
                _inside(source, folder).rename(target)


def migrate(config, client, pdf_dir, backup_dir, *, apply=False):
    lock = DataMutationLock(config.data_dir)
    if apply and not lock.try_acquire():
        raise RuntimeError("another write is active")
    try:
        plan = build_plan(config, client, pdf_dir)
        if not apply or not plan:
            return plan
        backup_dir = Path(backup_dir).resolve()
        backup_dir.mkdir(parents=True, exist_ok=False)
        (backup_dir / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        states, created = [], []
        manifests = ManifestStore(config)
        # All originals must be backed up before any live files/points are changed.
        for index, change in enumerate(plan):
            old, new, model = change["old_id"], change["new_id"], change["embedding_model"]
            before = manifests.read(old)
            source_dir = _inside(config.parsed_doc_dir(old), config.parsed_dir)
            target_dir = _inside(config.parsed_doc_dir(new), config.parsed_dir)
            folder = backup_dir / str(index)
            folder.mkdir()
            shutil.copy2(manifests.path(old), folder / "manifest.json")
            shutil.copy2(source_dir / "chunks.jsonl", folder / "chunks.jsonl")
            old_collection, new_collection = config.collection_name(model, old), config.collection_name(model, new)
            points = _records(client, old_collection) if change["active"] else []
            if change["active"] and len(points) != before.num_chunks:
                raise ValueError(f"point count mismatch: {old}")
            info = client.get_collection(old_collection) if change["active"] else None
            if info is not None:
                (folder / "collection.json").write_text(info.model_dump_json(), encoding="utf-8")
                with gzip.open(folder / "points.jsonl.gz", "wt", encoding="utf-8") as stream:
                    for point in points:
                        stream.write(point.model_dump_json() + "\n")
            chunks = [json.loads(line) for line in (source_dir / "chunks.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
            if len(chunks) != before.num_chunks:
                raise ValueError(f"saved chunk count mismatch: {old}")
            states.append(dict(change=change, before=before, source=source_dir, target=target_dir,
                               folder=folder, points=points, info=info, old_collection=old_collection,
                               new_collection=new_collection, chunks=chunks, stage=None))
        try:
            # First stage renamed folders and copy/verify vectors, retaining all old collections.
            for state in states:
                change = state["change"]
                if state["source"] != state["target"]:
                    state["stage"] = _inside(config.parsed_dir / (".rename-" + uuid.uuid4().hex), config.parsed_dir)
                    shutil.copytree(state["source"], state["stage"])
                    _prepare_folder(state["stage"], change, config)
                if change["active"]:
                    transformed = []
                    for point in state["points"]:
                        payload = _payload(point.payload, change, config)
                        transformed.append(models.Record(id=point_id_for(payload["chunk_id"]), payload=payload, vector=point.vector))
                    if state["old_collection"] != state["new_collection"]:
                        created.append(state["new_collection"])
                        _create(client, state["new_collection"], state["info"])
                    _upsert(client, state["new_collection"], transformed)
                    actual = {p.id: p for p in _records(client, state["new_collection"])}
                    if len(actual) != len(transformed) or any(
                        p.id not in actual or actual[p.id].payload != p.payload
                        or not _same_vectors(p.vector, actual[p.id].vector)
                        for p in transformed
                    ):
                        raise RuntimeError(f"vector/payload verification failed: {change['new_id']}")
            for state in states:
                change = state["change"]
                chunks = [_payload(chunk, change, config) for chunk in state["chunks"]]
                output = state["stage"] or state["source"]
                _atomic(output / "chunks.jsonl", "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in chunks))
                if state["stage"]:
                    stash = _inside(state["folder"] / "parsed-original", backup_dir)
                    _inside(state["source"], config.parsed_dir).rename(stash)
                    state["stage"].rename(_inside(state["target"], config.parsed_dir))
                updated = state["before"].model_copy(update={
                    "document_id": change["new_id"], "doc_name": change["new_id"],
                    "source_path": change["source_path"], "parsed_dir": str(state["target"]),
                })
                manifests.write(updated)
                if change["old_id"] != change["new_id"]:
                    manifests.delete(change["old_id"])
            # Old collections are removed only after all replacement data has been verified.
            for state in states:
                if state["change"]["active"] and state["old_collection"] != state["new_collection"]:
                    client.delete_collection(state["old_collection"])
            (backup_dir / "complete.json").write_text(json.dumps({"completed_at": datetime.now(timezone.utc).isoformat(), "documents": len(plan)}), encoding="utf-8")
            return plan
        except Exception as primary:
            # Restore disk first and independently: a DB outage must not strand moved files.
            errors = []
            for state in reversed(states):
                try:
                    stash = state["folder"] / "parsed-original"
                    if stash.exists():
                        if state["target"].exists():
                            shutil.rmtree(_inside(state["target"], config.parsed_dir))
                        _inside(stash, backup_dir).rename(_inside(state["source"], config.parsed_dir))
                    elif state["source"] == state["target"]:
                        shutil.copy2(state["folder"] / "chunks.jsonl", state["source"] / "chunks.jsonl")
                    shutil.copy2(state["folder"] / "manifest.json", manifests.path(state["change"]["old_id"]))
                    if state["change"]["old_id"] != state["change"]["new_id"]:
                        manifests.delete(state["change"]["new_id"])
                    if state["stage"] and state["stage"].exists():
                        shutil.rmtree(_inside(state["stage"], config.parsed_dir))
                except Exception as exc:
                    errors.append(f"disk {state['change']['old_id']}: {exc!r}")
            for state in states:
                if state["change"]["active"]:
                    try:
                        if not client.collection_exists(state["old_collection"]):
                            _create(client, state["old_collection"], state["info"])
                        _upsert(client, state["old_collection"], state["points"])
                    except Exception as exc:
                        errors.append(f"vectors {state['old_collection']}: {exc!r}")
            for collection in created:
                try:
                    if client.collection_exists(collection):
                        client.delete_collection(collection)
                except Exception as exc:
                    errors.append(f"cleanup {collection}: {exc!r}")
            if errors:
                raise RuntimeError(f"Migration failed: {primary!r}; backup={backup_dir}; rollback errors: {errors}") from primary
            raise
    finally:
        if apply:
            lock.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--pdf-dir", type=Path, required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from urllib.parse import urlparse
    if urlparse(args.url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("only localhost Qdrant is supported")
    config = Config(data_dir=args.data_dir.resolve(), qdrant_mode="server", qdrant_url=args.url)
    client = QdrantClient(url=args.url, timeout=60)
    try:
        plan = migrate(config, client, args.pdf_dir, args.backup_dir, apply=args.apply)
    finally:
        client.close()
    print(json.dumps({"apply": args.apply, "changes": plan}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
