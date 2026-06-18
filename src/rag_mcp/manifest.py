"""색인 매니페스트 (상태/멱등). 스펙 §6.9.

  - data/manifests/{document_id}.json 에 status: parsing→parsed→embedded→done.
  - 쓰기 순서: parsed 저장 → 임베딩 → Qdrant upsert(dense+sparse) → done.
  - 멱등: 같은 document_id 재실행 시 기존 포인트 삭제 후 재삽입. 부분 상태면 재개.
  - atomic write(temp→rename)로 중간 크래시에도 매니페스트 손상 방지.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import Config
from .models import Manifest


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ManifestStore:
    def __init__(self, config: Config):
        self.config = config

    def path(self, document_id: str) -> Path:
        return self.config.manifest_path(document_id)

    def exists(self, document_id: str) -> bool:
        return self.path(document_id).exists()

    def read(self, document_id: str) -> Optional[Manifest]:
        p = self.path(document_id)
        if not p.exists():
            return None
        return Manifest.model_validate_json(p.read_text(encoding="utf-8"))

    def write(self, manifest: Manifest) -> Manifest:
        self.config.manifests_dir.mkdir(parents=True, exist_ok=True)
        if manifest.created_at is None:
            manifest.created_at = _now()
        manifest.updated_at = _now()
        p = self.path(manifest.document_id)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, p)  # atomic
        return manifest

    def update(self, document_id: str, **fields) -> Manifest:
        m = self.read(document_id) or Manifest(document_id=document_id)
        for k, v in fields.items():
            setattr(m, k, v)
        return self.write(m)

    def list_all(self) -> list[Manifest]:
        d = self.config.manifests_dir
        if not d.exists():
            return []
        out = []
        for f in sorted(d.glob("*.json")):
            try:
                out.append(Manifest.model_validate_json(f.read_text(encoding="utf-8")))
            except Exception:
                continue
        return out

    def delete(self, document_id: str) -> None:
        p = self.path(document_id)
        if p.exists():
            p.unlink()
