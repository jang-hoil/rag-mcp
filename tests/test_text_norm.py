"""추출 텍스트 정규화 — PUA 심볼폰트 불릿·NUL/제어문자 제거 (한글·표 무결성).

배경: 온라인용 PDF가 Wingdings/Symbol 폰트로 그린 불릿·체크박스가 유니코드 매핑 없이
PUA(U+E000-F8FF)로 추출되어 뷰어에서 두부(box)로 보이고, 일부 목차/제목에는 NUL(U+0000)이
공백 자리에 삽입된다. 정규화는 이들만 제거하고 한글·금액·과목코드·표 구분자(TAB, LF)는 보존한다.
"""
from rag_mcp.pdf_parser import normalize_text, cell_text
from rag_mcp.chunking import block_plain_text
from rag_mcp.table_chunking import table_grid_text

CHECK = ""   # Wingdings 체크표시로 추출된 PUA
BULLET = ""  # 사각 불릿으로 추출된 PUA
SQ = ""      # 헤딩 앞 사각 불릿 PUA


def test_removes_pua_symbol_bullets():
    out = normalize_text(f"{BULLET} 항목 {CHECK} 값")
    assert CHECK not in out and BULLET not in out
    assert "항목" in out and "값" in out


def test_removes_null_and_control_chars():
    out = normalize_text("제Ⅰ장\x00 회계\x07제도")
    assert "\x00" not in out and "\x07" not in out
    assert out == "제Ⅰ장 회계제도"


def test_preserves_tab_newline_korean_amount_code():
    s = "구 분\t금액 50,000,000\n코드 802-01"
    assert normalize_text(s) == s


def test_collapses_spaces_left_by_removal():
    # PUA 2개 제거 후 남은 이중 공백은 하나로 정리
    assert normalize_text(f"a{CHECK}{CHECK}  b") == "a b"


def test_plain_text_returns_input_unchanged_when_clean():
    s = "일반수용비 과목코드 201-01"
    assert normalize_text(s) == s


def test_block_plain_text_strips_pua_in_heading():
    block = {"type": "heading", "heading level": "Subtitle", "content": f"{SQ} 신규"}
    assert block_plain_text(block) == "신규"


def test_block_plain_text_list_items_cleaned():
    block = {"type": "list", "kids": [
        {"content": f"{CHECK} 최초신고"},
        {"content": f"{CHECK} 변동신고\x00"},
    ]}
    out = block_plain_text(block)
    assert CHECK not in out and "\x00" not in out
    assert out == "최초신고\n변동신고"


def test_cell_text_strips_pua():
    cell = {"kids": [{"content": f"{CHECK} 값"}]}
    assert CHECK not in cell_text(cell)
    assert "값" in cell_text(cell)


def test_table_grid_text_clean_and_tab_structured():
    table = {
        "type": "table",
        "number of rows": 1,
        "number of columns": 2,
        "rows": [
            {"cells": [
                {"row number": 1, "column number": 1, "kids": [{"content": f"{BULLET} 구분"}]},
                {"row number": 1, "column number": 2, "kids": [{"content": "내용\x00"}]},
            ]}
        ],
    }
    txt = table_grid_text(table)
    assert BULLET not in txt and "\x00" not in txt
    assert txt == "구분\t내용"
