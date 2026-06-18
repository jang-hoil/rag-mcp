"""BM25 sparse 벡터 (Kiwi 토큰 → Qdrant sparse).

스펙 §6.5, §11:
  - 토큰 → (인덱스, 가중치)로 변환해 Qdrant sparse에 저장. **별도 BM25 pkl 금지**.
  - IDF는 Qdrant 서버측(Modifier.IDF)으로 계산하므로 value는 **term frequency**를 넣는다.
  - 인덱스는 토큰의 안정 해시(blake2b) → uint32. Python hash()는 실행마다 달라져 금지.
"""
from __future__ import annotations

import hashlib
from collections import Counter

from .tokenizer import tokenize

# Qdrant sparse 인덱스는 uint32. 해시를 이 공간으로 매핑.
_INDEX_SPACE = 2**32


def token_to_index(token: str) -> int:
    """토큰 → 안정적 uint32 인덱스 (실행·세션 무관 동일)."""
    digest = hashlib.blake2b(token.encode("utf-8"), digest_size=4).digest()
    return int.from_bytes(digest, "big") % _INDEX_SPACE


def to_sparse(text: str) -> tuple[list[int], list[float]]:
    """텍스트 → (indices, values). value = term frequency.

    같은 토큰이 같은 인덱스로 매핑되므로 인덱싱·질의가 같은 토크나이저를 쓰면 정합.
    """
    tokens = tokenize(text)
    if not tokens:
        return [], []
    counts: Counter[str] = Counter(tokens)
    # 인덱스 충돌(다른 토큰 같은 인덱스) 시 빈도 합산
    by_index: dict[int, float] = {}
    for tok, tf in counts.items():
        idx = token_to_index(tok)
        by_index[idx] = by_index.get(idx, 0.0) + float(tf)
    indices = list(by_index.keys())
    values = [by_index[i] for i in indices]
    return indices, values
