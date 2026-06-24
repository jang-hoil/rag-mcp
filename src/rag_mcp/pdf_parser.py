"""PDF 파서 — OpenDataLoader로 PDF→(markdown, json) 변환 + parsed 영구저장. 스펙 §6.1.

핵심 결정(전제):
- 표 셀은 **JSON에서 추출**한다(markdown은 평면화되어 격자/스팬이 사라짐).
- borderless(뭉친) 표는 cluster 방식으로 최대한 살리고, 그래도 뭉치면 이미지 폴백(청킹 단계).
- 산출물(json/md/images)은 `data/parsed/{document_id}/`에 **영구 저장**해 재파싱 비용을 피한다.
  (reindex reparse=False는 이 산출물 대신 chunks.jsonl을 재사용한다.)

JSON 트리 구조(전제, 샘플 PDF로 확인):
- 루트 dict: `number of pages`, `kids`(reading-order 평면 블록 리스트).
- 블록 타입: heading/paragraph/list/table/image/caption.
- heading: `heading level`(Doctitle|Subtitle), `content`, `page number`.
- table: `number of rows/columns`, `rows`[].`cells`[] (각 셀 `row/column number`, `row/column span`, `kids`에 내용).
"""
from __future__ import annotations

import glob
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

from .config import Config


@dataclass
class ParsedDoc:
    """파싱 산출물 핸들. tree는 OpenDataLoader JSON 트리(dict)."""

    document_id: str
    tree: dict[str, Any]
    json_path: Path
    md_path: Optional[Path]
    parsed_dir: Path

    @property
    def num_pages(self) -> Optional[int]:
        return self.tree.get("number of pages")

    @property
    def title(self) -> Optional[str]:
        return self.tree.get("title")

    def iter_blocks(self) -> Iterator[dict[str, Any]]:
        """루트 kids를 reading order대로 순회 (표/리스트는 atomic 블록으로 그대로 yield)."""
        for node in self.tree.get("kids", []) or []:
            if isinstance(node, dict):
                yield node


def cell_text(cell: dict[str, Any]) -> str:
    """표 셀(table cell)의 내부 content를 모두 모아 한 문자열로 (kids 재귀)."""
    parts: list[str] = []

    def collect(n: Any) -> None:
        if isinstance(n, dict):
            c = n.get("content")
            if c:
                parts.append(str(c))
            for k, v in n.items():
                if k != "content":
                    collect(v)
        elif isinstance(n, list):
            for x in n:
                collect(x)

    collect(cell)
    return " ".join(p.strip() for p in parts if p and p.strip())


def _find_output(out_dir: Path, ext: str) -> Optional[Path]:
    hits = sorted(glob.glob(str(out_dir / f"*.{ext}")))
    return Path(hits[0]) if hits else None


def parse_pdf(
    pdf_path: str | Path,
    document_id: str,
    config: Config,
    *,
    force: bool = False,
) -> ParsedDoc:
    """PDF를 OpenDataLoader로 변환하고 ParsedDoc을 반환.

    이미 `parsed/{document_id}/*.json`이 있으면 재변환 없이 로드(force=True면 재변환).
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF 없음: {pdf_path}")

    out_dir = config.parsed_doc_dir(document_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    json_path = _find_output(out_dir, "json")
    if force or json_path is None:
        import opendataloader_pdf

        opendataloader_pdf.convert(
            input_path=str(pdf_path),
            output_dir=str(out_dir),
            format="markdown,json",
            table_method="cluster",      # border + cluster (borderless 표 대응)
            markdown_with_html=True,     # markdown 표를 HTML로(평면화 완화)
            quiet=True,
        )
        json_path = _find_output(out_dir, "json")

    if json_path is None:
        raise RuntimeError(f"JSON 산출물을 찾지 못함: {out_dir}")

    tree = json.loads(json_path.read_text(encoding="utf-8"))
    md_path = _find_output(out_dir, "md")
    return ParsedDoc(
        document_id=document_id,
        tree=tree,
        json_path=json_path,
        md_path=md_path,
        parsed_dir=out_dir,
    )
