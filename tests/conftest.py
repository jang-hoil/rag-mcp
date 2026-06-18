"""테스트 공용 fixture.

FakeEmbeddingBackend: 실제 KURE 모델(~2GB·SSL 이슈) 없이 vector_store/retrieval/통합
테스트를 구동하기 위한 결정적 임베딩. blake2b 해시로 1024d 단위벡터를 생성한다.
같은 텍스트 → 같은 벡터, 유사 텍스트(공유 토큰)는 어느 정도 가까운 방향을 갖도록 구성.
"""
import hashlib
import math

import pytest

from rag_mcp.embeddings import EmbeddingBackend
from rag_mcp.tokenizer import tokenize


class FakeEmbeddingBackend(EmbeddingBackend):
    name = "kure"
    dimension = 1024

    def _vec(self, text: str) -> list[float]:
        # 토큰별 해시 성분을 더해 의미적 근접을 흉내(공유 토큰 → 가까운 벡터)
        dim = self.dimension
        acc = [0.0] * dim
        toks = tokenize(text) or [text]
        for tok in toks:
            h = hashlib.blake2b(tok.encode("utf-8"), digest_size=16).digest()
            seed = int.from_bytes(h, "big")
            for i in range(dim):
                # 토큰마다 두 성분에 부호 있는 기여
                idx = (seed + i * 2654435761) % dim
                sign = 1.0 if ((seed >> (i % 61)) & 1) else -1.0
                acc[idx] += sign * (1.0 + (i % 3))
        norm = math.sqrt(sum(v * v for v in acc)) or 1.0
        return [v / norm for v in acc]

    def embed_documents(self, texts):
        return [self._vec(t) for t in texts]

    def embed_query(self, text):
        return self._vec(text)


@pytest.fixture
def fake_backend():
    return FakeEmbeddingBackend()
