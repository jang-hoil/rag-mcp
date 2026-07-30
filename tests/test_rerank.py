"""모델 없는 lexical 리랭커와 선택적 cross 리랭커 테스트."""
from pathlib import Path

import pytest

from rag_mcp.config import Config
from rag_mcp.models import SearchResult, SearchSource
from rag_mcp.rerank import build_reranker


def _result(
    chunk_id: str,
    text: str,
    *,
    heading_path: list[str] | None = None,
    is_table: bool = False,
    has_amount: bool = False,
    has_code: bool = False,
) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id,
        text=text,
        score=0.5,
        source=SearchSource(
            document_id="d1",
            heading_path=heading_path or [],
            is_table=is_table,
            has_amount=has_amount,
            has_code=has_code,
        ),
    )


def test_lexical_reranker_changes_order_using_payload_signals():
    unrelated = _result("unrelated", "휴가 복무 일반 안내")
    relevant = _result(
        "relevant",
        "201-01 일반수용비 한도 50,000,000원",
        heading_path=["세출예산", "201-01 일반수용비"],
        is_table=True,
        has_amount=True,
        has_code=True,
    )
    reranker = build_reranker(Config(reranker="lexical"))

    reranked = reranker.rerank("201-01 일반수용비 한도 표", [unrelated, relevant])

    assert [result.chunk_id for result in reranked] == ["relevant", "unrelated"]


def test_off_reranker_preserves_order_exactly():
    first = _result("first", "무관한 후보")
    second = _result("second", "201-01 한도", has_code=True)
    reranker = build_reranker(Config(reranker="off"))

    assert reranker.rerank("201-01 한도", [first, second]) == [first, second]


def test_cross_reranker_missing_local_model_has_clear_korean_error(tmp_path):
    missing = Path(tmp_path) / "없는-cross-모델"
    reranker = build_reranker(
        Config(reranker="cross", cross_reranker_model=str(missing))
    )

    with pytest.raises(RuntimeError, match="로컬.*모델.*자동 다운로드"):
        reranker.rerank("질의", [_result("candidate", "후보 본문")])
