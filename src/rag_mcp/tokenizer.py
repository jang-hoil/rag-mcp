r"""한국어 토크나이저 (Kiwi 형태소 + 코드/금액 보존).

스펙 §5.2, §6.5:
  - 공백 토큰화 금지(조사 문제) → Kiwi 형태소 분석.
  - 전처리로 과목코드·금액·비율을 **한 토큰으로 보존**:
    과목코드 `\d{3}-\d{2}`, 금액 `[\d,]+원?`, 비율 `100분의\d+`·`\d+%`.
  - **인덱싱·질의에 동일 토크나이저** 사용 (이 모듈이 단일 출처).
"""
from __future__ import annotations

import re
from functools import lru_cache

# 보존 패턴 (우선순위 순서 — 더 구체적/긴 것 먼저, 비중첩으로 매칭)
_PROTECT_PATTERNS: list[re.Pattern] = [
    re.compile(r"\d{3}-\d{2}(?:-\d+)?"),   # 과목코드 201-01, 201-01-1
    re.compile(r"100분의\s?\d+"),           # 비율 100분의30
    re.compile(r"\d+(?:\.\d+)?\s?%"),       # 비율 30%
    re.compile(r"[\d,]+\s?원"),             # 금액 50,000,000원
    re.compile(r"\d{1,3}(?:,\d{3})+"),       # 콤마 포함 큰 숫자 50,000,000
]

# Kiwi 형태소 중 BM25 의미 토큰으로 채택할 품사 prefix.
# N*: 체언, V*: 용언(원형), MA*: 부사, SL: 외국어, SH: 한자, SN: 숫자, XR: 어근
_KEEP_TAG_PREFIX = ("N", "V", "MA", "SL", "SH", "SN", "XR")


@lru_cache(maxsize=1)
def _kiwi():
    from kiwipiepy import Kiwi

    return Kiwi()


def _normalize_protected(tok: str) -> str:
    """보존 토큰 정규화: 공백 제거 (예: '100분의 30' → '100분의30', '50,000 원' → '50,000원')."""
    return re.sub(r"\s+", "", tok)


def _kiwi_tokens(text: str) -> list[str]:
    """보존 구간이 아닌 일반 텍스트를 Kiwi로 형태소 분해 (의미 품사만)."""
    text = text.strip()
    if not text:
        return []
    out: list[str] = []
    for t in _kiwi().tokenize(text):
        if t.tag.startswith(_KEEP_TAG_PREFIX):
            out.append(t.form)
    return out


def tokenize(text: str) -> list[str]:
    """텍스트 → 토큰 리스트. 코드/금액/비율은 한 토큰으로 보존, 나머지는 Kiwi 형태소."""
    if not text:
        return []

    matched = [False] * len(text)
    protected: list[tuple[int, int, str]] = []
    for pat in _PROTECT_PATTERNS:
        for m in pat.finditer(text):
            if any(matched[m.start():m.end()]):
                continue  # 이미 더 우선 패턴에 잡힌 구간
            protected.append((m.start(), m.end(), _normalize_protected(m.group())))
            for i in range(m.start(), m.end()):
                matched[i] = True
    protected.sort()

    tokens: list[str] = []
    cursor = 0
    for s, e, tok in protected:
        if s > cursor:
            tokens.extend(_kiwi_tokens(text[cursor:s]))
        tokens.append(tok)
        # 금액은 숫자코어도 함께 보존 (질의 '50,000,000' ↔ 문서 '50,000,000원' 매칭)
        core = re.sub(r"[원%]|100분의", "", tok)
        if core and core != tok and re.search(r"\d", core):
            tokens.append(core)
        cursor = e
    if cursor < len(text):
        tokens.extend(_kiwi_tokens(text[cursor:]))
    return tokens
