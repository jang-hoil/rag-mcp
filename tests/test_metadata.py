"""metadata 테스트 — fiscal_year/플래그 추출."""
from __future__ import annotations

from rag_mcp.metadata import (
    extract_doc_meta,
    extract_fiscal_year,
    has_amount,
    has_code,
)


def test_extract_fiscal_year():
    assert extract_fiscal_year("2022 예산편성(예산부서) 매뉴얼") == 2022
    assert extract_fiscal_year("작성일 2026/05/12") == 2026
    assert extract_fiscal_year("연도 정보 없음") is None
    assert extract_fiscal_year("") is None


def test_has_amount():
    assert has_amount("한도 50,000,000원 이내")
    assert has_amount("3억원 편성")
    assert has_amount("1,234천원")
    assert not has_amount("일상경비 한도 관리")
    assert not has_amount("")


def test_has_code():
    assert has_code("일상경비 201-01 항목")
    assert has_code("세출조정의무재량지출관리(15080) 화면")
    assert has_code("과목코드 201-01-1 세목")
    assert not has_code("2022 예산편성")  # 연도는 코드 아님
    assert not has_code("일반 본문 텍스트")


class _FakeParsed:
    def __init__(self, title, blocks, document_id="doc1"):
        self.title = title
        self.document_id = document_id
        self._blocks = blocks

    def iter_blocks(self):
        return iter(self._blocks)


def test_extract_doc_meta_override_우선():
    p = _FakeParsed("2022 매뉴얼", [])
    meta = extract_doc_meta(p, doc_name="회계지침", fiscal_year=2025)
    assert meta == {"doc_name": "회계지침", "fiscal_year": 2025}


def test_extract_doc_meta_본문에서_연도():
    p = _FakeParsed(
        None,
        [
            {"type": "image"},
            {"type": "paragraph", "content": "차세대 지방재정"},
            {"type": "heading", "content": "2022"},
        ],
        document_id="예산편성",
    )
    meta = extract_doc_meta(p)
    assert meta["doc_name"] == "예산편성"  # title 없으면 document_id
    assert meta["fiscal_year"] == 2022
