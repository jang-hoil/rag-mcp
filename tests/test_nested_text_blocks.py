"""중첩 text block의 본문 누락과 출처 정보 회귀 테스트. 외부 PDF/DB 불필요."""
from copy import deepcopy
from pathlib import Path

import pytest

from rag_mcp.chunking import build_chunks
from rag_mcp.metadata import extract_doc_meta
from rag_mcp.pdf_parser import ParsedDoc


def parsed_doc(nodes):
    return ParsedDoc("nested", {"kids": nodes}, Path("unused.json"), None, Path("."))


def test_nested_body_keeps_heading_page_and_fiscal_year():
    parsed = parsed_doc([
        {"type": "text block", "page number": 7, "kids": [
            {"type": "heading", "heading level": 1, "content": "2026년 예산"},
            {"type": "text block", "kids": [
                {"type": "paragraph", "content": "중첩 본문 고유 문구 201-01 50,000원"},
            ]},
        ]},
    ])

    chunks = build_chunks(parsed)

    assert len(chunks) == 1
    assert chunks[0].text == "중첩 본문 고유 문구 201-01 50,000원"
    assert chunks[0].page == 7
    assert chunks[0].heading_path == ["2026년 예산"]
    assert chunks[0].has_code and chunks[0].has_amount
    assert extract_doc_meta(parsed)["fiscal_year"] == 2026


def test_reading_order_keeps_table_and_list_atomic():
    before = {"type": "paragraph", "content": "앞"}
    table = {"type": "table", "rows": [{"cells": [{"kids": [
        {"type": "paragraph", "content": "표 셀"},
    ]}]}]}
    listing = {"type": "list", "kids": [
        {"type": "paragraph", "content": "목록 항목"},
    ]}
    after = {"type": "caption", "content": "뒤"}
    parsed = parsed_doc([
        before,
        {"type": "text block", "kids": [
            table, {"type": "text block", "kids": [listing]},
        ]},
        after,
    ])

    assert list(parsed.iter_blocks()) == [before, table, listing, after]


@pytest.mark.parametrize("child_fields,nested_fields,expected", [
    ({}, {}, 7),
    ({"page number": None}, {}, 7),
    ({}, {"page number": 8}, 8),
    ({"page number": 9}, {"page number": 8}, 9),
])
def test_page_inheritance_uses_nearest_container_without_mutation(
    child_fields, nested_fields, expected,
):
    parsed = parsed_doc([
        {"type": "text block", "page number": 7, "kids": [
            {"type": "text block", **nested_fields, "kids": [
                {"type": "paragraph", "content": "본문", **child_fields},
            ]},
        ]},
    ])
    original = deepcopy(parsed.tree)

    blocks = list(parsed.iter_blocks())

    assert len(blocks) == 1
    assert blocks[0]["page number"] == expected
    assert parsed.tree == original
    assert list(parsed.iter_blocks()) == blocks


def test_inherited_page_preserves_table_cells_and_list_items():
    table = {"type": "table", "number of rows": 1, "number of columns": 1,
             "rows": [{"cells": [{"row number": 1, "column number": 1,
                                  "row span": 1, "column span": 1,
                                  "kids": [{"type": "paragraph", "content": "50,000원"}]}]}]}
    listing = {"type": "list", "list items": [{"content": "목록의 본문"}]}
    parsed = parsed_doc([
        {"type": "text block", "page number": 4, "kids": [table, listing]},
    ])
    original = deepcopy(parsed.tree)

    chunks = build_chunks(parsed)

    assert [(c.text, c.page, c.is_table) for c in chunks] == [
        ("50,000원", 4, True), ("목록의 본문", 4, False),
    ]
    assert parsed.tree == original


def test_direct_content_is_a_paragraph_without_changing_source():
    parsed = parsed_doc([
        {"type": "text block", "page number": 2, "kids": [
            {"type": "text block", "content": "직접 본문", "kids": []},
        ]},
    ])
    original = deepcopy(parsed.tree)

    blocks = list(parsed.iter_blocks())

    assert [(b["type"], b["content"], b["page number"]) for b in blocks] == [
        ("paragraph", "직접 본문", 2),
    ]
    assert build_chunks(parsed)[0].text == "직접 본문"
    assert parsed.tree == original


def test_content_and_children_fail_explicitly_instead_of_losing_text():
    parsed = parsed_doc([
        {"type": "text block", "content": "상위 본문", "kids": [
            {"type": "paragraph", "content": "하위 본문"},
        ]},
    ])

    with pytest.raises(ValueError, match="text block.*content.*kids"):
        list(parsed.iter_blocks())


def test_flat_input_is_unchanged():
    nodes = [
        {"type": "heading", "content": "제목", "page number": 1},
        {"type": "paragraph", "content": "본문"},
        {"type": "list", "kids": [{"content": "항목"}]},
    ]

    assert list(parsed_doc(nodes).iter_blocks()) == nodes


def test_empty_containers_and_non_dictionary_children_are_ignored():
    paragraph = {"type": "paragraph", "content": "유효 본문"}
    parsed = parsed_doc([
        None, "invalid", 12,
        {"type": "text block", "kids": None},
        {"type": "text block", "content": " \n", "kids": [None, paragraph]},
    ])

    assert list(parsed.iter_blocks()) == [paragraph]


def test_container_page_does_not_leak_to_following_root_sibling():
    parsed = parsed_doc([
        {"type": "text block", "page number": 3, "kids": [
            {"type": "paragraph", "content": "안"},
        ]},
        {"type": "paragraph", "content": "밖"},
    ])

    inside, outside = list(parsed.iter_blocks())

    assert inside["page number"] == 3
    assert outside.get("page number") is None
