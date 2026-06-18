"""tokenizer.py + sparse.py 테스트 (스펙 §10: 코드/금액 토큰 보존)."""
from rag_mcp.tokenizer import tokenize
from rag_mcp.sparse import to_sparse, token_to_index


def test_code_preserved_as_single_token():
    """과목코드 201-01이 한 토큰으로 보존."""
    toks = tokenize("201-01 집행 가능 항목")
    assert "201-01" in toks, toks
    # 2,01,01 같은 분해가 아님
    assert "201" not in toks


def test_ratio_preserved():
    """비율 100분의30, 30%가 한 토큰으로 보존."""
    toks = tokenize("기준은 100분의30 이내, 또는 30% 까지")
    assert "100분의30" in toks, toks
    assert "30%" in toks, toks


def test_amount_preserved():
    """금액 50,000,000원이 한 토큰으로 보존."""
    toks = tokenize("한도는 50,000,000원 이다")
    assert "50,000,000원" in toks, toks
    # 숫자코어도 함께 보존(질의 변형 대응)
    assert "50,000,000" in toks, toks


def test_korean_no_whitespace_tokenization():
    """조사가 제거되고 의미 명사가 살아남는다 (공백 토큰화 아님).

    Kiwi는 복합명사 '일상경비'를 '일상'+'경비'로 분해한다(언어적으로 정상,
    BM25 recall에 유리). 핵심 보장은 조사 제거 + 의미 토큰 생존이다.
    """
    toks = tokenize("일상경비의 한도를 본다")
    assert "경비" in toks, toks
    assert "한도" in toks, toks
    # 조사 '의','를'은 제거됨
    assert "의" not in toks and "를" not in toks


def test_same_token_same_index():
    """동일 토큰은 동일 인덱스 (인덱싱·질의 정합 — 실행 무관)."""
    assert token_to_index("201-01") == token_to_index("201-01")
    a = token_to_index("일상경비")
    b = token_to_index("일상경비")
    assert a == b
    assert 0 <= a < 2**32


def test_sparse_query_doc_overlap():
    """질의와 문서가 공유하는 코드 토큰이 같은 sparse 인덱스를 가진다."""
    doc_idx, _ = to_sparse("201-01 일반수용비 한도 50,000,000원")
    q_idx, _ = to_sparse("201-01 한도")
    overlap = set(doc_idx) & set(q_idx)
    assert overlap, "공유 토큰의 인덱스 겹침 없음"
    # 201-01, 한도 두 토큰이 겹쳐야 함
    assert token_to_index("201-01") in overlap
    assert token_to_index("한도") in overlap


def test_sparse_values_are_tf():
    """value는 term frequency (반복 토큰 빈도 반영)."""
    idx, val = to_sparse("한도 한도 한도")
    pos = idx.index(token_to_index("한도"))
    assert val[pos] >= 3.0
