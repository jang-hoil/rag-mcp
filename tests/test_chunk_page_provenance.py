"""본문 청크가 실제 PDF 페이지와 다른 번호로 인용되지 않도록 검증한다."""
from copy import deepcopy

import pytest

from rag_mcp.chunking import BODY_MAX, build_chunks
from rag_mcp.config import Config
from rag_mcp.pdf_parser import ParsedDoc
from rag_mcp.service import RagService


def _parsed(tmp_path, blocks):
    return ParsedDoc("manual", {"kids": blocks}, tmp_path / "manual.json", None, tmp_path)


def _body(kind, text, page):
    block = {"type": kind, "page number": page}
    if kind == "list":
        block["list items"] = [{"content": text}]
    else:
        block["content"] = text
    return block


@pytest.mark.parametrize("kind", ["paragraph", "list", "caption"])
def test_body_on_next_page_has_its_own_page(tmp_path, kind):
    parsed = _parsed(tmp_path, [
        _body("paragraph", "첫 페이지의 집행 기준", 2),
        _body(kind, "다음 페이지의 별도 절차", 3),
    ])
    original = deepcopy(parsed.tree)

    chunks = build_chunks(parsed)

    assert [(c.text, c.page) for c in chunks] == [
        ("첫 페이지의 집행 기준", 2), ("다음 페이지의 별도 절차", 3),
    ]
    assert [c.chunk_id for c in chunks] == ["manual::c0", "manual::c1"]
    assert parsed.tree == original


@pytest.mark.parametrize("pages", [(2, None), (None, 3), (2, 8)])
def test_missing_or_nonconsecutive_page_is_not_attributed_to_neighbor(tmp_path, pages):
    chunks = build_chunks(_parsed(tmp_path, [
        _body("paragraph", "앞쪽 본문", pages[0]),
        _body("paragraph", "뒤쪽 본문", pages[1]),
    ]))

    assert [(c.text, c.page) for c in chunks] == [
        ("앞쪽 본문", pages[0]), ("뒤쪽 본문", pages[1]),
    ]


def test_same_page_body_still_combines_and_keeps_heading(tmp_path):
    parsed = _parsed(tmp_path, [
        {"type": "heading", "heading level": 1, "content": "제1장", "page number": 1},
        _body("paragraph", "같은 쪽 문단", 2),
        _body("list", "같은 쪽 목록", 2),
        _body("caption", "같은 쪽 설명", 2),
        _body("paragraph", "다음 쪽 문단", 3),
    ])

    chunks = build_chunks(parsed, doc_name="업무편람", fiscal_year=2026)

    assert [(c.text, c.page) for c in chunks] == [
        ("같은 쪽 문단\n같은 쪽 목록\n같은 쪽 설명", 2), ("다음 쪽 문단", 3),
    ]
    assert all(c.heading_path == ["제1장"] for c in chunks)
    assert all(c.doc_name == "업무편람" and c.fiscal_year == 2026 for c in chunks)


def test_split_long_next_page_never_inherits_previous_page(tmp_path):
    chunks = build_chunks(_parsed(tmp_path, [
        _body("paragraph", "앞쪽의 짧은 본문", 2),
        _body("paragraph", "나" * (BODY_MAX * 2 + 50), 3),
    ]))

    assert (chunks[0].text, chunks[0].page) == ("앞쪽의 짧은 본문", 2)
    assert len(chunks) >= 3
    assert all(c.page == 3 and set(c.text) == {"나"} for c in chunks[1:])


def test_page_inherited_from_nested_container_is_used_at_boundary(tmp_path):
    chunks = build_chunks(_parsed(tmp_path, [
        {"type": "text block", "page number": page, "kids": [
            {"type": "paragraph", "content": text},
        ]}
        for page, text in [(4, "상속된 앞쪽 본문"), (5, "상속된 뒤쪽 본문")]
    ]))

    assert [(c.text, c.page) for c in chunks] == [
        ("상속된 앞쪽 본문", 4), ("상속된 뒤쪽 본문", 5),
    ]


def test_search_and_saved_chunks_cite_the_actual_later_page(tmp_path, fake_backend):
    config = Config(data_dir=tmp_path / "data", qdrant_path=tmp_path / "qdrant")
    service = RagService(config, backend=fake_backend)
    chunks = build_chunks(_parsed(tmp_path, [
        _body("paragraph", "일반수용비 집행 기준", 2),
        _body("paragraph", "민방위 비상급수시설 점검 절차", 3),
    ]))
    try:
        assert service.ingest_chunks("manual", chunks)["ok"]
        result = service.search_documents("비상급수시설", document_id="manual", search_mode="sparse")[0]
        assert result["source"]["page"] == 3
        assert "일반수용비" not in result["text"]
        saved = service._indexer("kure", "manual").load_chunks("manual")
        assert [(c.text, c.page) for c in saved] == [(c.text, c.page) for c in chunks]
    finally:
        if service._qdrant_client is not None:
            service._qdrant_client.close()
