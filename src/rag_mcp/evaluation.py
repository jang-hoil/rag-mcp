"""검색 품질 평가 하니스 (읽기 전용 골든셋 채점). 스펙 §12 측정 보조.

리랭커/표 재추출 같은 무거운 기능을 넣기 전에 "정말 필요한가"를 데이터로 판정한다.
- 순위 품질: recall@k, MRR → 정답 청크가 top-k에서 밀려나는지(리랭커 필요 신호).
- 숫자 표면화: 금액·코드 질의에서 기대 문자열이 검색된 청크 텍스트에 실제로 나오는지
  (표 추출 손실 신호 → needs_image 재추출 필요 여부).

색인을 수정하지 않는다. 기존 service.search_documents만 호출한다(read-only, 부작용 없음).

골든셋 형식(JSONL — 한 줄에 한 케이스, '#' 줄·빈 줄 무시):
  {"id":"q1","query":"201-01 일반수용비 한도","type":"amount",
   "expect_substrings":["201-01","50,000,000"],"match":"all","fiscal_year":2026}
필드:
  query(필수). type: general|code|amount(기본 general). expect_substrings: 청크 텍스트에
  포함돼야 할 문자열. match: all|any(기본 all). relevant_chunk_ids/expect_document_id/
  expect_page: 있으면 해당 일치도 hit로 인정. fiscal_year: 질의 시 연도 한정.
한 결과라도 위 조건을 충족하면 그 케이스는 'hit'.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


@dataclass
class EvalCase:
    query: str
    id: str = ""
    type: str = "general"  # general|code|amount
    expect_substrings: list[str] = field(default_factory=list)
    match: str = "all"  # all|any
    relevant_chunk_ids: list[str] = field(default_factory=list)
    expect_document_id: Optional[str] = None
    expect_page: Optional[int] = None
    fiscal_year: Optional[int] = None


def load_cases(path: str | Path) -> list[EvalCase]:
    allowed = set(EvalCase.__dataclass_fields__)
    cases: list[EvalCase] = []
    for i, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        obj = json.loads(line)
        kw = {k: v for k, v in obj.items() if k in allowed}
        if not kw.get("query"):
            raise ValueError(f"{path}:{i} query 누락: {line}")
        kw.setdefault("id", kw["query"][:30])
        cases.append(EvalCase(**kw))
    return cases


def result_is_hit(result: dict, case: EvalCase) -> bool:
    """검색 결과 1건이 케이스의 기대(청크id/문서/부분문자열) 중 하나라도 충족하면 hit."""
    src = result.get("source") or {}
    # 1) chunk_id 정확 일치
    if case.relevant_chunk_ids and result.get("chunk_id") in case.relevant_chunk_ids:
        return True
    # 2) document_id(+page) 일치
    if case.expect_document_id and src.get("document_id") == case.expect_document_id:
        if case.expect_page is None or src.get("page") == case.expect_page:
            return True
    # 3) 텍스트 부분일치 (match=all|any)
    if case.expect_substrings:
        text = result.get("text") or ""
        present = sum(1 for s in case.expect_substrings if s in text)
        if case.match == "any":
            return present > 0
        return present == len(case.expect_substrings)
    return False


def first_hit_rank(results: list[dict], case: EvalCase) -> Optional[int]:
    """hit인 첫 결과의 1-based 순위. 없으면 None."""
    for rank, r in enumerate(results, start=1):
        if result_is_hit(r, case):
            return rank
    return None


def numeric_surfaced(results: list[dict], case: EvalCase) -> Optional[bool]:
    """type이 amount|code일 때, 기대 문자열이 '어떤' 청크 텍스트에든 나오면 True.

    순위(rank)와 무관하게 '값 자체가 검색 텍스트에 존재하는가'를 본다 → 표 추출 손실 진단.
    숫자형 케이스가 아니면 None(측정 제외).
    """
    if case.type not in ("amount", "code") or not case.expect_substrings:
        return None
    blob = "\n".join((r.get("text") or "") for r in results)
    if case.match == "any":
        return any(s in blob for s in case.expect_substrings)
    return all(s in blob for s in case.expect_substrings)


def evaluate(
    search_fn: Callable[[str, int, Optional[int]], list[dict]],
    cases: list[EvalCase],
    fetch_k: int = 50,
    ks: tuple[int, ...] = (1, 3, 5, 10, 20, 50),
) -> dict:
    """각 케이스를 fetch_k개까지 검색해 순위/표면화 지표를 집계.

    search_fn(query, top_k, fiscal_year) → list[dict] 형태(서비스 주입). 테스트는 fake 주입.
    """
    ks = tuple(k for k in ks if k <= fetch_k)
    per_case = []
    for c in cases:
        results = search_fn(c.query, fetch_k, c.fiscal_year) or []
        rank = first_hit_rank(results, c)
        hit_src = (results[rank - 1].get("source") or {}) if rank else {}
        per_case.append({
            "id": c.id,
            "type": c.type,
            "query": c.query,
            "first_hit_rank": rank,
            "num_results": len(results),
            "numeric_surfaced": numeric_surfaced(results, c),
            "hit_is_table": bool(hit_src.get("is_table")) if rank else None,
            "hit_needs_image": bool(hit_src.get("needs_image")) if rank else None,
        })

    n = len(per_case) or 1
    recall = {
        k: sum(1 for p in per_case if p["first_hit_rank"] and p["first_hit_rank"] <= k) / n
        for k in ks
    }
    mrr = sum(1.0 / p["first_hit_rank"] for p in per_case if p["first_hit_rank"]) / n
    numeric = [p for p in per_case if p["numeric_surfaced"] is not None]
    numeric_rate = (
        sum(1 for p in numeric if p["numeric_surfaced"]) / len(numeric) if numeric else None
    )
    rerank_gap = (
        recall[max(ks)] - recall[5] if (5 in ks and max(ks) > 5) else None
    )
    return {
        "n_cases": len(per_case),
        "fetch_k": fetch_k,
        "recall_at": recall,
        "mrr": mrr,
        "rerank_gap": rerank_gap,  # recall@max − recall@5
        "numeric_cases": len(numeric),
        "numeric_surfaced_rate": numeric_rate,
        "per_case": per_case,
    }


def format_report(
    summary: dict, rerank_gap_threshold: float = 0.15, numeric_threshold: float = 0.9
) -> str:
    """사람이 읽는 요약 + 두 기능(리랭커·표 재추출)에 대한 휴리스틱 판정. 임계값은 조정 가능."""
    L = [f"평가 케이스: {summary['n_cases']}개 (fetch_k={summary['fetch_k']})", ""]

    L.append("[순위 품질]")
    for k, v in summary["recall_at"].items():
        L.append(f"  recall@{k:<2} = {v:.2f}")
    L.append(f"  MRR        = {summary['mrr']:.3f}")
    gap = summary["rerank_gap"]
    if gap is not None:
        verdict = (
            "리랭커 검토 가치 있음(정답이 상위에서 밀림)"
            if gap >= rerank_gap_threshold
            else "리랭커 불필요 신호(상위 순위 양호)"
        )
        L.append(f"  rerank_gap = {gap:+.2f} (recall@max−recall@5) → {verdict}")
    L.append("")

    L.append("[숫자/코드 표면화] (type=amount|code 케이스)")
    nr = summary["numeric_surfaced_rate"]
    if nr is None:
        L.append("  (해당 케이스 없음 — type을 amount|code로 표기하면 측정됨)")
    else:
        verdict = (
            "표 추출 손실 의심 → needs_image 재추출 검토"
            if nr < numeric_threshold
            else "표면화 양호 → 표 재추출 불필요 신호"
        )
        L.append(f"  surfaced_rate = {nr:.2f} ({summary['numeric_cases']}건) → {verdict}")
    L.append("")

    miss = [
        p for p in summary["per_case"]
        if p["first_hit_rank"] is None or p["numeric_surfaced"] is False
    ]
    L.append(f"[미적중/표면화 실패] {len(miss)}건")
    for p in miss:
        rank = p["first_hit_rank"] if p["first_hit_rank"] else "—"
        L.append(f"  [{p['id']}] rank={rank} surfaced={p['numeric_surfaced']}  {p['query']}")
    return "\n".join(L)


def run(goldset_path: str, fetch_k: int = 50, embedding_model: str = "kure") -> dict:
    """골든셋을 실제 색인에 대해 평가(read-only). RagService.search_documents만 호출한다."""
    from .service import RagService

    svc = RagService()
    cases = load_cases(goldset_path)

    def search_fn(query: str, top_k: int, fiscal_year: Optional[int] = None) -> list[dict]:
        return svc.search_documents(
            query, top_k=top_k, fiscal_year=fiscal_year, embedding_model=embedding_model
        )

    return evaluate(search_fn, cases, fetch_k=fetch_k)
