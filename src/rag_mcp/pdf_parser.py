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
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

from .config import Config

_MULTI_SPACE_RE = re.compile(r" {2,}")


def normalize_text(s: str) -> str:
    """추출 원문에서 PUA(심볼폰트 불릿)·제어문자(NUL 등)를 제거한다.

    - PUA(U+E000–F8FF, 보충 PUA)는 유니코드 의미가 없는 Wingdings/Symbol 글리프(불릿·체크박스)로
      뷰어에서 두부(□)로 표시되므로 제거한다.
    - NUL 등 C0/C1 제어문자는 표/본문 구분자(TAB, LF)만 남기고 제거한다.
    한글·금액·과목코드 등 일반 문자와 표 구분자는 보존하고, 제거로 생긴 이중 공백만 하나로 정리한다.
    """
    if not s:
        return s
    out: list[str] = []
    for ch in s:
        if ch == "\t" or ch == "\n":
            out.append(ch)
            continue
        o = ord(ch)
        if o < 0x20 or o == 0x7F or 0x80 <= o <= 0x9F:
            continue  # C0/C1 제어(탭·개행 제외)
        if 0xE000 <= o <= 0xF8FF or 0xF0000 <= o <= 0xFFFFD or 0x100000 <= o <= 0x10FFFD:
            continue  # PUA
        out.append(ch)
    return _MULTI_SPACE_RE.sub(" ", "".join(out))


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
    return normalize_text(" ".join(p.strip() for p in parts if p and p.strip())).strip()


def _find_output(out_dir: Path, ext: str) -> Optional[Path]:
    hits = sorted(glob.glob(str(out_dir / f"*.{ext}")))
    return Path(hits[0]) if hits else None


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

        from .ocr_triage import document_needs_ocr

        before_json = _output_signatures(out_dir, "json")

        convert_kw: dict = dict(
            input_path=str(pdf_path),
            output_dir=str(out_dir),
            format="markdown,json",
            table_method="cluster",
            markdown_with_html=True,
            quiet=True,
        )
        # 스캔 PDF 의심 + hybrid 설정 시 문서 단위 OCR(OpenDataLoader hybrid)
        if config.ocr_mode != "off" and config.odl_hybrid != "off":
            use_hybrid = config.ocr_mode == "force" or document_needs_ocr(
                pdf_path, config.ocr_min_chars_per_page
            )
            if use_hybrid:
                convert_kw["hybrid"] = config.odl_hybrid
                convert_kw["hybrid_hancom_ai_ocr_strategy"] = "auto"
                convert_kw["hybrid_fallback"] = True
                if config.odl_hybrid_url:
                    convert_kw["hybrid_url"] = config.odl_hybrid_url

        opendataloader_pdf.convert(**convert_kw)
        if force:
            json_path = _find_changed_output(out_dir, "json", before_json)
            if json_path is None:
                raise RuntimeError(f"현재 변환의 JSON 산출물을 찾지 못함: {out_dir}")
        else:
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
