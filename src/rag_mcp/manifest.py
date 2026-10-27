"""색인 매니페스트 (상태/멱등). 스펙 §6.9.

  - data/manifests/{document_id}.json 에 status: parsing→parsed→embedded→done.
  - 쓰기 순서: parsed 저장 → 임베딩 → Qdrant upsert(dense+sparse) → done.
  - 멱등: 같은 document_id 재실행 시 기존 포인트 삭제 후 재삽입. 부분 상태면 재개.
  - atomic write(temp→rename)로 중간 크래시에도 매니페스트 손상 방지.
"""
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import Config
from .models import Manifest


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# WSL에서 색인하면 source_path가 `/mnt/d/...` 형태로 남는다. 같은 파일을 Windows에서는
# `D:\...`로 열어야 해서, 경로만 옛 표기로 남은 문서는 원본이 제자리에 있어도 페이지
# 렌더가 실패한다(2026-08-03 실측: 매니페스트 6건 중 4건이 WSL 경로, 그중 2건은 파일 제자리).
_WSL_MOUNT = re.compile(r"^/mnt/([a-zA-Z])/(.*)$")


def _wsl_to_windows(raw: str) -> Optional[Path]:
    m = _WSL_MOUNT.match(raw)
    if not m:
        return None
    drive, rest = m.group(1).upper(), m.group(2).replace("/", "\\")
    return Path(f"{drive}:\\{rest}")


def resolve_source_path(raw: str | None) -> Optional[Path]:
    """매니페스트에 기록된 원본 PDF 경로를 실제로 열 수 있는 경로로 해석한다.

    기록된 경로가 그대로 존재하면 그것을 쓴다(무조건 우선). 존재하지 않고 WSL 마운트
    형태이면 Windows 경로로 바꿔 보고, 거기 파일이 있으면 그 경로를 돌려준다.
    둘 다 아니면 **기록된 경로를 그대로** 돌려준다 — 호출부의 오류 메시지에 사용자가
    실제로 기록해 둔 값이 보여야 하기 때문이다.
    """
    if not raw:
        return None
    literal = Path(raw)
    if literal.exists():
        return literal
    translated = _wsl_to_windows(raw)
    if translated is not None and translated.exists():
        return translated
    return literal


class ManifestStore:
    def __init__(self, config: Config):
        self.config = config

    def path(self, document_id: str) -> Path:
        return self.config.manifest_path(document_id)

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
        tmp = p.with_name(f".{p.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
            os.replace(tmp, p)  # atomic
        finally:
            if tmp.exists():
                tmp.unlink()
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
