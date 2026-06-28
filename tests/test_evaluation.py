"""평가 하니스(evaluation.py) 순수 채점 로직 테스트 — 모델 불필요(search_fn 주입)."""
from rag_mcp.evaluation import (
    EvalCase,
    evaluate,
    first_hit_rank,
    format_report,
    load_cases,
    numeric_surfaced,
    result_is_hit,
)


def _r(chunk_id, text, document_id="d1", page=1, is_table=False, needs_image=False):
    return {
        "chunk_id": chunk_id,
        "text": text,
        "score": 1.0,
        "source": {
            "document_id": document_id, "page": page,
            "is_table": is_table, "needs_image": needs_image,
        },
    }


def test_result_is_hit_substring_all_any():
    c_all = EvalCase(query="q", expect_substrings=["201-01", "50,000,000"], match="all")
    assert result_is_hit(_r("c0", "201-01 한도 50,000,000원"), c_all)
    assert not result_is_hit(_r("c1", "201-01 한도"), c_all)  # 일부만 → all 실패
    c_any = EvalCase(query="q", expect_substrings=["201-01", "999"], match="any")
    assert result_is_hit(_r("c2", "...201-01..."), c_any)


def test_first_hit_rank():
    c = EvalCase(query="q", expect_substrings=["명시이월"])
    results = [_r("a", "무관"), _r("b", "명시이월 요건"), _r("c", "x")]
    assert first_hit_rank(results, c) == 2
    assert first_hit_rank([_r("a", "무관")], c) is None


def test_chunk_id_and_document_hit():
    c = EvalCase(query="q", relevant_chunk_ids=["gold"])
    assert first_hit_rank([_r("x", "t"), _r("gold", "t")], c) == 2
    c2 = EvalCase(query="q", expect_document_id="d9", expect_page=3)
    assert result_is_hit(_r("z", "t", document_id="d9", page=3), c2)
    assert not result_is_hit(_r("z", "t", document_id="d9", page=4), c2)  # page 불일치


def test_numeric_surfaced():
    c = EvalCase(query="q", type="amount", expect_substrings=["50,000,000"])
    assert numeric_surfaced([_r("a", "x"), _r("b", "...50,000,000...")], c) is True
    assert numeric_surfaced([_r("a", "x")], c) is False
    c_gen = EvalCase(query="q", type="general", expect_substrings=["x"])
    assert numeric_surfaced([_r("a", "x")], c_gen) is None  # 숫자형 아님 → 측정 제외


def test_evaluate_aggregate():
    cases = [
        EvalCase(id="q1", query="명시이월", expect_substrings=["명시이월"]),
        EvalCase(id="q2", query="없는것", expect_substrings=["절대안나옴"]),
        EvalCase(id="q3", query="금액", type="amount", expect_substrings=["50,000,000"]),
    ]
    canned = {
        "명시이월": [_r("a", "무관"), _r("b", "명시이월")],            # rank 2
        "없는것": [_r("a", "무관"), _r("b", "무관2")],                 # miss
        "금액": [_r("a", "x")] * 5 + [_r("z", "50,000,000원")],        # rank 6, surfaced True
    }

    def fake_search(query, top_k, fiscal_year=None):
        return canned[query][:top_k]

    s = evaluate(fake_search, cases, fetch_k=10, ks=(1, 3, 5, 10))
    assert s["n_cases"] == 3
    assert s["recall_at"][1] == 0.0
    assert round(s["recall_at"][3], 3) == round(1 / 3, 3)   # 명시이월(rank2)만
    assert round(s["recall_at"][5], 3) == round(1 / 3, 3)   # 금액(rank6)은 아직 밖
    assert round(s["recall_at"][10], 3) == round(2 / 3, 3)  # 명시이월(2)+금액(6)
    assert round(s["rerank_gap"], 3) == round(1 / 3, 3)     # recall@10 − recall@5
    assert s["numeric_cases"] == 1
    assert s["numeric_surfaced_rate"] == 1.0
    assert "순위 품질" in format_report(s)


def test_load_cases(tmp_path):
    p = tmp_path / "g.jsonl"
    p.write_text(
        '# 주석\n\n'
        '{"query":"a","type":"amount","expect_substrings":["1"]}\n'
        '{"id":"x","query":"b"}\n',
        encoding="utf-8",
    )
    cs = load_cases(p)
    assert len(cs) == 2  # 주석·빈 줄 무시
    assert cs[0].type == "amount" and cs[0].expect_substrings == ["1"]
    assert cs[0].id == "a"  # id 미지정 → query에서 파생
    assert cs[1].id == "x"
