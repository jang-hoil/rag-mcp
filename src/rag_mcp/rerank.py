"""교체 가능한 검색 리랭커.

기본 ``off``와 ``lexical``은 추가 모델이 필요 없다. ``cross``는 사용자가 지정한
로컬 모델만 열며 네트워크 다운로드를 시도하지 않는다.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

from .config import Config
from .models import SearchResult
from .tokenizer import tokenize


class Reranker(Protocol):
    """검색 후보 재정렬 인터페이스."""

    def rerank(self, query: str, results: list[SearchResult]) -> list[SearchResult]:
        """후보를 관련도순으로 재정렬한다."""


class OffReranker:
    """입력 순서를 그대로 보존한다."""

    def rerank(self, query: str, results: list[SearchResult]) -> list[SearchResult]:
        return list(results)


_TABLE_INTENT = {"표", "목록", "내역", "현황", "기준표"}
_AMOUNT_INTENT = {"금액", "한도", "단가", "예산", "비용", "원"}
_CODE_INTENT = {"코드", "과목", "항목"}
_CODE_PATTERN = re.compile(r"\d{3}-\d{2}(?:-\d+)?")
_AMOUNT_PATTERN = re.compile(r"(?:[\d,]+\s?원|\d+(?:\.\d+)?\s?%)")


class LexicalReranker:
    """Kiwi 토큰과 payload 신호를 선형 결합하는 모델 없는 리랭커."""

    def __init__(self, config: Config):
        self.config = config

    def _score(
        self,
        query: str,
        query_tokens: set[str],
        result: SearchResult,
    ) -> float:
        document_tokens = tokenize(result.text)
        document_token_set = set(document_tokens)
        denominator = len(query_tokens) or 1
        lexical_overlap = len(query_tokens & document_token_set) / denominator

        heading_tokens = set(tokenize(" ".join(result.source.heading_path)))
        heading_overlap = len(query_tokens & heading_tokens) / denominator

        table_intent = bool(query_tokens & _TABLE_INTENT)
        amount_intent = bool(query_tokens & _AMOUNT_INTENT) or bool(
            _AMOUNT_PATTERN.search(query)
        )
        code_intent = bool(query_tokens & _CODE_INTENT) or bool(
            _CODE_PATTERN.search(query)
        )
        # 지나치게 긴 청크보다 짧고 집중된 청크를 아주 약하게 선호한다.
        length_signal = 1.0 / (1.0 + len(document_tokens) / 200.0)

        return (
            self.config.rerank_lexical_weight * lexical_overlap
            + self.config.rerank_heading_weight * heading_overlap
            + self.config.rerank_table_weight
            * float(table_intent and result.source.is_table)
            + self.config.rerank_amount_weight
            * float(amount_intent and result.source.has_amount)
            + self.config.rerank_code_weight
            * float(code_intent and result.source.has_code)
            + self.config.rerank_length_weight * length_signal
        )

    def rerank(self, query: str, results: list[SearchResult]) -> list[SearchResult]:
        query_tokens = set(tokenize(query))
        # 파이썬 정렬은 안정 정렬이므로 동점이면 기존 융합 순위를 보존한다.
        return sorted(
            results,
            key=lambda result: self._score(query, query_tokens, result),
            reverse=True,
        )


class CrossEncoderReranker:
    """명시된 로컬 CrossEncoder만 사용하는 선택적 리랭커."""

    def __init__(self, config: Config):
        self.model_path = config.cross_reranker_model
        self._model = None

    def _ensure_model(self):
        path = Path(self.model_path).expanduser() if self.model_path else None
        if path is None or not path.exists():
            raise RuntimeError(
                "cross 리랭커용 로컬 모델이 없습니다. "
                "RAG_CROSS_RERANKER_MODEL에 이미 내려받은 모델 경로를 지정하세요. "
                "오프라인 안전을 위해 자동 다운로드하지 않습니다."
            )
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder

                self._model = CrossEncoder(str(path), local_files_only=True)
            except Exception as error:
                raise RuntimeError(
                    "로컬 cross 리랭커 모델을 열지 못했습니다. "
                    "오프라인 안전을 위해 자동 다운로드하지 않습니다: "
                    f"{error}"
                ) from error
        return self._model

    def rerank(self, query: str, results: list[SearchResult]) -> list[SearchResult]:
        if not results:
            return []
        model = self._ensure_model()
        scores = model.predict([(query, result.text) for result in results])
        ranked = sorted(
            zip(results, scores),
            key=lambda item: float(item[1]),
            reverse=True,
        )
        return [result for result, _ in ranked]


def build_reranker(config: Config) -> Reranker:
    """설정에 맞는 리랭커를 만든다."""
    if config.reranker == "off":
        return OffReranker()
    if config.reranker == "lexical":
        return LexicalReranker(config)
    if config.reranker == "cross":
        return CrossEncoderReranker(config)
    raise ValueError(f"지원하지 않는 리랭커: {config.reranker}")
