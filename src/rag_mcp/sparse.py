"""BM25 sparse 벡터 (Kiwi 토큰 → Qdrant sparse).

스펙 §6.5, §11:
  - 토큰 → (인덱스, 가중치)로 변환해 Qdrant sparse에 저장. **별도 BM25 pkl 금지**.
  - IDF는 Qdrant 서버측(Modifier.IDF)으로 계산한다.
  - 문서 value는 BM25 TF 포화값, 질의 value는 raw TF를 사용한다(표준 BM25 비대칭).
  - 인덱스는 토큰의 안정 해시(blake2b) → uint32. Python hash()는 실행마다 달라져 금지.
"""
from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable

from .tokenizer import tokenize

# Qdrant sparse 인덱스는 uint32. 해시를 이 공간으로 매핑.
_INDEX_SPACE = 2**32


def token_to_index(token: str) -> int:
    """토큰 → 안정적 uint32 인덱스 (실행·세션 무관 동일)."""
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, "big") % _INDEX_SPACE


def _by_index(counts: Counter[str], weight) -> tuple[list[int], list[float]]:
    """토큰 빈도를 안정 해시 인덱스로 합친다."""
    by_index: dict[int, float] = {}
    for token, term_frequency in counts.items():
        index = token_to_index(token)
        by_index[index] = by_index.get(index, 0.0) + weight(term_frequency)
    indices = list(by_index)
    return indices, [by_index[index] for index in indices]


def to_sparse_query(text: str) -> tuple[list[int], list[float]]:
    """질의 → raw TF sparse 벡터.

    표준 BM25는 문서 TF만 포화하므로 질의에는 길이 정규화나 포화를 적용하지 않는다.
    """
    tokens = tokenize(text)
    if not tokens:
        return [], []
    counts: Counter[str] = Counter(tokens)
    return _by_index(counts, lambda term_frequency: float(term_frequency))


def to_sparse_document(
    text: str,
    *,
    avgdl: float,
    k1: float = 1.2,
    b: float = 0.75,
) -> tuple[list[int], list[float]]:
    """문서 청크 → BM25 TF 포화 sparse 벡터."""
    tokens = tokenize(text)
    if not tokens:
        return [], []
    if avgdl <= 0:
        raise ValueError("avgdl은 토큰이 있는 문서에서 0보다 커야 합니다")
    if k1 <= 0:
        raise ValueError("k1은 0보다 커야 합니다")
    if not 0 <= b <= 1:
        raise ValueError("b는 0 이상 1 이하여야 합니다")

    document_length = len(tokens)
    counts: Counter[str] = Counter(tokens)

    def saturated(term_frequency: int) -> float:
        denominator = term_frequency + k1 * (
            1 - b + b * document_length / avgdl
        )
        return term_frequency * (k1 + 1) / denominator

    return _by_index(counts, saturated)


def average_document_length(texts: Iterable[str]) -> float:
    """문서 내 청크들의 평균 토큰 수를 계산한다."""
    lengths = [len(tokenize(text)) for text in texts]
    return sum(lengths) / len(lengths) if lengths else 0.0
