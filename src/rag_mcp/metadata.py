"""메타데이터 추출 — fiscal_year / doc_name 및 청크 플래그(has_amount/has_code). 스펙 §6.2.

플래그는 검색 필터/가중(표·금액·코드 중심 질의)을 돕는다.
  - fiscal_year: 본문 초반에서 `20\\d{2}` (회계연도). caller override 우선.
  - has_amount: 콤마구분 큰 숫자/`원`/`천원`·`백만원` 단위 금액.
  - has_code: 과목코드 `\\d{3}-\\d{2}` 또는 시스템 화면코드 `(15080)` 류.
"""
from __future__ import annotations

import re
from typing import Any, Optional

_FISCAL_RE = re.compile(r"20\d{2}")

# 금액: 50,000,000 / 1,234원 / 50,000천원 / 3억원 / 100백만원
_AMOUNT_RE = re.compile(
    r"\d{1,3}(?:,\d{3})+"          # 콤마 구분 큰 숫자
    r"|\d+\s*(?:원|천원|만원|백만원|억원|조원|억|만)"  # 단위 금액
)

# 코드: 과목코드 201-01(-1) / 괄호 화면코드 (15080) / 세목 따위 4~6자리 화면코드
_CODE_RE = re.compile(
    r"\d{3}-\d{2}(?:-\d+)?"        # 과목코드
    r"|\(\s*\d{4,6}\s*\)"          # 괄호 화면코드 (15080)
)


def extract_fiscal_year(text: str) -> Optional[int]:
    """텍스트에서 첫 `20\\d{2}` 4자리 연도를 정수로 반환(없으면 None)."""
    if not text:
        return None
    m = _FISCAL_RE.search(text)
    return int(m.group()) if m else None


def has_amount(text: str) -> bool:
    return bool(text) and _AMOUNT_RE.search(text) is not None


def has_code(text: str) -> bool:
    return bool(text) and _CODE_RE.search(text) is not None


def extract_doc_meta(
    parsed: Any,
    *,
    doc_name: Optional[str] = None,
    fiscal_year: Optional[int] = None,
    max_scan_blocks: int = 40,
) -> dict[str, Any]:
    """ParsedDoc에서 문서 단위 메타(doc_name, fiscal_year)를 결정. caller override 우선.

    fiscal_year는 제목·초반 heading/paragraph에서 `20\\d{2}`를 찾는다(본예산 연도).
    """
    name = doc_name or parsed.title or parsed.document_id

    fy = fiscal_year
    if fy is None:
        # 제목 → 초반 블록 순으로 첫 연도 탐색
        if parsed.title:
            fy = extract_fiscal_year(parsed.title)
        if fy is None:
            for i, block in enumerate(parsed.iter_blocks()):
                if i >= max_scan_blocks:
                    break
                if block.get("type") in ("heading", "paragraph"):
                    fy = extract_fiscal_year(str(block.get("content") or ""))
                    if fy is not None:
                        break

    return {"doc_name": name, "fiscal_year": fy}
