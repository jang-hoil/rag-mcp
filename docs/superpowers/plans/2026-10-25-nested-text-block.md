# Nested Text Block Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 중첩 text block 안의 본문을 신규 파싱·청킹과 메타데이터 추출에서 누락하지 않는다.

**Architecture:** `ParsedDoc.iter_blocks()`만 공통 구조 해석 지점으로 보완한다.
text block만 펼치고 표·목록을 원자적으로 유지한다. 원본을 수정하지 않으며
자식에게 없는 페이지 정보만 상위 컨테이너에서 상속한다.

**Tech Stack:** Python 3.11, uv, pytest, 기존 ParsedDoc/build_chunks/extract_doc_meta.

## Global Constraints

- 표 셀은 **JSON에서 추출**한다. table/list 내부를 전역 본문으로 펼치지 않는다.
- 같은 테스트가 2회 이상 실패하면 근본 원인 분석, 4회 연속 실패 시 멈추고 보고한다.
- Python: **uv + 3.11** (.venv).
- 테스트 먼저 작성 → 구현 → `uv run pytest -q` 통과 후 작은 단위로 커밋한다.
- 운영 data·Qdrant·설정·임베딩 모델·검색 융합 방식은 수정하지 않는다.
- 사용자가 요청한 orchestration의 실제 Orca Run/Task/Dispatch로 작업자를 감독한다.
- 시작 전부터 존재한 PROGRESS.md 변경과 미추적 파일은 보존한다.

---

### Task 1: 공통 블록 순회 및 회귀 검증

**Files:**
- Modify: `src/rag_mcp/pdf_parser.py`, `ParsedDoc.iter_blocks()`와 관련 설명만.
- Create: `tests/test_nested_text_blocks.py`.
- Report: `work/orchestration_20261025/implementation.md`.

**Interfaces:**
- Consumes: OpenDataLoader JSON의 `type`, `kids`, `content`, `page number`.
- Produces: 기존 `ParsedDoc.iter_blocks() -> Iterator[dict[str, Any]]`.
- Consumers verified: `build_chunks(parsed, ...) -> list[Chunk]`,
  `extract_doc_meta(parsed, ...) -> dict[str, Any]`.

- [x] **Step 1: 누락을 잡는 실제 실패 테스트 작성.**

다음과 같이 외부 PDF·Java·모델·DB 없이 실제 ParsedDoc을 생성한다.

```python
from copy import deepcopy
from pathlib import Path

import pytest

from rag_mcp.chunking import build_chunks
from rag_mcp.metadata import extract_doc_meta
from rag_mcp.pdf_parser import ParsedDoc


def doc(tree):
    return ParsedDoc("nested", tree, Path("unused.json"), None, Path("."))


def test_nested_body_and_metadata_are_available():
    parsed = doc({"kids": [{"type": "text block", "page number": 7, "kids": [
        {"type": "heading", "heading level": 1, "content": "2026년 예산"},
        {"type": "text block", "kids": [
            {"type": "paragraph", "content": "중첩 본문 고유 문구 201-01 50,000원"}
        ]},
    ]}]})
    chunks = build_chunks(parsed)
    assert len(chunks) == 1
    assert "중첩 본문 고유 문구" in chunks[0].text
    assert chunks[0].page == 7
    assert chunks[0].heading_path == ["2026년 예산"]
    assert extract_doc_meta(parsed)["fiscal_year"] == 2026


@pytest.mark.parametrize("child_page,expected", [(None, 7), (9, 9)])
def test_page_inheritance_does_not_mutate_source(child_page, expected):
    paragraph = {"type": "paragraph", "content": "본문"}
    if child_page is not None:
        paragraph["page number"] = child_page
    tree = {"kids": [{"type": "text block", "page number": 7,
                      "kids": [paragraph]}]}
    before = deepcopy(tree)
    assert list(doc(tree).iter_blocks())[0]["page number"] == expected
    assert tree == before
```

추가 테스트는 각각 독립 동작을 검증한다: 형제 블록 읽기 순서,
table/list 내부 paragraph가 별도 전역 블록으로 생기지 않음, 평면 입력 호환,
content만 있는 컨테이너의 문단 생성, content+kids의 명시적 ValueError,
빈 컨테이너와 dict가 아닌 항목 무시. page number가 명시적으로 None인 자식도
누락된 페이지와 같이 상속하는 사례를 포함한다.

- [x] **Step 2: 실패를 관찰.**

Run: `uv run pytest tests/test_nested_text_blocks.py -q`.
Expected: 중첩 본문/페이지/메타데이터 테스트에서 현재 구현의 누락을 보여주는 FAIL.
보고서에 명령, 종료 코드, 실패 이유를 기록한다. import 등 환경 오류를 RED로 세지 않는다.

- [x] **Step 3: 최소 구현.**

다음 형태의 반복 순회로 읽기 순서를 유지한다. 필요한 경우 이름과 배치만 기존 스타일에 맞춘다.

```python
def iter_blocks(self) -> Iterator[dict[str, Any]]:
    pending = [(node, None) for node in reversed(self.tree.get("kids", []) or [])]
    while pending:
        node, inherited_page = pending.pop()
        if not isinstance(node, dict):
            continue
        page = node.get("page number")
        if page is None:
            page = inherited_page
        if node.get("type") == "text block":
            kids = node.get("kids", []) or []
            has_content = bool(str(node.get("content") or "").strip())
            if has_content and kids:
                raise ValueError("text block has both content and kids")
            if not has_content:
                pending.extend((child, page) for child in reversed(kids))
                continue
            node = {**node, "type": "paragraph"}
        if page is not None and node.get("page number") is None:
            node = {**node, "page number": page}
        yield node
```

- [x] **Step 4: GREEN과 통합 확인.**

Run: `uv run pytest tests/test_nested_text_blocks.py -q`.
Expected: 새 회귀 테스트 전체 PASS. 이어서 `uv run pytest -q` 전체 PASS.
원본 백업 JSON과 메모리 안에서 text block만 풀어 만든 기준 구조를 비교해
청크 텍스트·표·페이지·제목 경로·연도 값의 동등성을 확인한다. 운영 파일 쓰기 금지.

- [x] **Step 5: 검토와 커밋.**

구현 작업자는 변경 파일과 RED/GREEN/전체 테스트 근거를 보고하고 git 조작은 하지 않는다.
조정자가 diff 및 독립 검토를 확인하고 필요한 수정은 구현 작업자에게 전달한다.
전체 검증 통과 후 정확한 변경 파일만 stage하고 작은 단위로 커밋한다.
PROGRESS.md에는 이번 완료 기록만 추가하며 기존 사용자 변경을 함께 stage하지 않는다.

## 진행 기록

- [x] 설계 승인: 사용자가 “이 설계로 구현 진행” 선택.
- [x] 변경 전 전체 테스트: `uv run pytest -q`, exit 0.
- [x] Task 1 구현 및 RED/GREEN: 8 failed/4 passed → 12 passed.
- [x] diff 직접 검토. 독립 작업자 검토는 Codex 준비 실패·Claude 사용량 제한으로 수행 불가.
- [x] IBMS 원본 백업 두 종 비교, 민방위 현재 평면 보정본 호환성, 최종 전체 253 passed/9 skipped.
- [x] 문서 갱신과 커밋. 구현 체크포인트에서 이번 변경만 기록.

Orca 작업자 세 종 모두 실제 세션 사용량 제한 응답 후 종료·정리했으며,
사용자가 승인한 범위는 조정자가 직접 구현했다. 비교 스크립트는 처음에 원본 백업
세 종을 기대했으나, 민방위 백업 JSON이 트리가 아닌 매니페스트임을 확인했다.
IBMS 원본 두 종의 누락 복구와 민방위 현재 평면 입력의 호환성을 분리해 검증했다.
