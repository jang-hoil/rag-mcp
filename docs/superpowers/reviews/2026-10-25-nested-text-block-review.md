# 독립 코드 리뷰 — 248f871 (base 759029b)

대상: `src/rag_mcp/pdf_parser.py` (`ParsedDoc.iter_blocks`), `tests/test_nested_text_blocks.py`
읽은 자료: PROGRESS.md 2026-10-25 항목, `docs/superpowers/specs/2026-10-25-nested-text-block-design.md`,
`work/orchestration_20261025/review-248f871.patch`, 소비자 `chunking.py`(`build_chunks`, `block_plain_text`),
`metadata.py`(`extract_doc_meta`), `table_chunking.py`(`block_page`, `merge_cross_page_tables`),
`pipeline.py`, `service.py` ingest 예외 처리.

## 결론: **Ready (병합 가능)** — 차단 결함 없음

요구사항을 모두 구현에서 확인했다. 아래 항목은 차단 사유가 아니며, 대부분 설계 계약 질문이거나 테스트 보강 제안이다.

## 요구사항 대조

| 요구사항 | 구현 (pdf_parser.py) | 판정 |
| --- | --- | --- |
| text block만 읽기 순서대로 펼침 | L76 역순 스택 + L90 `reversed(kids)` → DFS 전위 순서 유지 | OK |
| table/list 원자 유지 | text block이 아닌 노드는 kids 탐색 없이 그대로 yield (L93-95) | OK |
| 가장 가까운 컨테이너 페이지 상속(없음/None일 때만), 자식 명시 페이지 우선 | L81-83, L90에서 상속 페이지 전달, L93 `is None`일 때만 덮어씀(0쪽도 보존) | OK |
| 원본 불변 | `{**node, ...}` 얕은 복사만 생성. 하위 dict는 공유하지만 소비자(`_merge_tables` 포함)가 복사 후 갱신 | OK |
| content만 있으면 paragraph | L92 | OK |
| content+kids → ValueError | L87-88 | OK (아래 F2 참고) |
| 빈/비-dict 자식 무시 | L79-80, L85 `or []` | OK (kids가 비-리스트 스칼라면 예외, F3) |
| 평면 입력 호환 | 비-text block 노드는 페이지 상속값이 없으면 원본 객체 그대로 yield | OK (테스트로 동일성 확인) |
| 중첩 본문·제목·회계연도가 소비자에 도달 | `build_chunks`/`extract_doc_meta` 모두 `iter_blocks()`만 사용 → 자동 반영 | OK |

## 발견 사항

### F1. [Low, 동작 변화 / 설계 계약 질문] `extract_doc_meta`의 40블록 탐색 창이 펼친 블록 기준으로 줄어든다
- 위치: `src/rag_mcp/metadata.py:63-65` (`max_scan_blocks=40`) + `pdf_parser.py:90`.
- 기존에는 text block 하나가 1블록으로 셌다. 이제 text block 안의 자식이 각각 1블록이 된다. 그래서 루트 앞쪽에 큰 text block이 있으면 그 뒤에 있는 루트 heading의 연도를 놓칠 수 있다.
- 재현(읽기 전용, 실행 확인):
  ```python
  doc([{"type":"text block","page number":1,
        "kids":[{"type":"paragraph","content":f"p{i}"} for i in range(45)]},
       {"type":"heading","content":"2026년 예산"}])
  extract_doc_meta(d)["fiscal_year"]  # 248f871: None / 759029b: 2026
  ```
- 실데이터 영향: 낮음. 보정된 IBMS·민방위 데이터가 이미 펼친 구조이고, 골든 재현도 그 구조와 일치했다. caller가 `fiscal_year`를 넘기면 이 탐색은 일어나지 않는다. 다만 기존 루트 기준 의미와 달라졌으므로 의도한 변화인지 정해야 한다. 지금처럼 펼친 블록 기준으로 두거나, 루트 블록 수 기준으로 셀 수 있다. 테스트는 이 경계를 다루지 않는다.

### F2. [Low, 설계 계약 질문] content가 있고 kids가 전부 무효(None 등)일 때도 ValueError가 난다
- 위치: `pdf_parser.py:85-88`. `kids`가 truthy인지만 보고, dict 자식이 있는지는 확인하지 않는다.
- 재현(실행 확인): `{"type":"text block","content":"본문","kids":[None]}` → `ValueError: text block has both content and kids`.
- "비-dict 자식은 무시한다"는 규칙을 먼저 적용하면, 이 노드는 content만 있는 블록이므로 문단이 되어야 한다. 실데이터에서 확인된 사례가 없으므로 결함이라기보다 계약을 명확히 할 부분이다. 해결안은 `kids = [k for k in kids if isinstance(k, dict)]` 한 줄이다.
- 같은 위치의 진단성 문제: 오류 메시지에 페이지나 위치 정보가 없다. `service.py:827`이 이 예외를 job 실패로 기록하더라도, 운영자는 PDF의 어느 블록이 문제인지 알 수 없다. `page number`를 메시지에 넣을 것을 권장한다.
- 영향: 이 오류가 나면 문서 전체 ingest가 실패한다. 의도된 fail-loud 동작이며 설계 문서와 일치한다.

### F3. [Info, 견고성] kids가 리스트가 아닌 스칼라이면 TypeError가 난다
- 재현(실행 확인): `{"type":"text block","kids":5}` → `TypeError: 'int' object is not reversible`.
- kids가 dict나 str이면 `reversed`가 키나 문자를 순회한다. 이 항목들은 비-dict라서 결국 무시된다. OpenDataLoader 출력에서는 나올 가능성이 매우 낮으므로 조치는 선택 사항이다.

### F4. [Info, 의도된 부수효과로 판단] 서로 다른 text block에 있던 인접 표가 이제 쪽 넘김 병합 대상이 된다
- `merge_cross_page_tables`(`table_chunking.py:73-96`)는 펼친 목록을 입력으로 받는다. 그래서 text block 경계를 넘어 연속 쪽(p, p+1)에 있고 열 수가 같은 표가 병합된다. 상속받은 페이지도 병합 판단에 쓰인다.
- 재현: 3쪽 text block 안의 표 "A"와 4쪽 text block 안의 표 "B" → 청크 1개 `('A\nB', 3)`.
- 쪽 넘김으로 나뉜 표를 복원하는 것이 이 함수의 목적이고, 이전 문서별 보정 데이터도 같은 결과를 낳았다. 따라서 결함이 아니다. 다만 이 경로를 고정하는 회귀 테스트는 없다.

### 구현 결함: 없음
읽기 순서, 페이지 상속 범위를 확인했다. 상속 페이지는 형제로 새지 않는다(L90에서 자식에게만 전달). 원본 불변성, 반복 호출 결과의 동일성도 코드와 테스트 모두에서 확인했다. 재귀 대신 명시적 스택을 써서 깊은 중첩에서도 RecursionError가 나지 않는다. 내용 없는 text block은 `type`을 바꾸지 않고 펼치기만 하며, 문단으로 바뀐 블록에 남는 `kids: []`는 `block_plain_text`가 사용하지 않는다.

## 회귀 테스트 커버리지 평가

충분히 다루는 부분: 2단 중첩, 형제 순서, table/list 원자성, 페이지 상속 4가지 조합, 원본 불변, 직접 content, content+kids 오류, 평면 입력 동일성, 빈 컨테이너와 비-dict 무시, 루트 형제로의 페이지 누수 방지. 청커(text·page·heading_path·has_code/has_amount)와 메타데이터(fiscal_year) 소비자 경로도 end-to-end로 검증한다. 일부 테스트가 `==` 비교만 쓰는 점은 문제없다. 평면 입력 테스트가 `==`로도 통과하는 이유는 객체를 그대로 반환하기 때문이다.

추가하면 좋을 테스트(비차단):
1. F1의 `max_scan_blocks` 경계. 어느 의미를 택하든 그 결정을 고정한다.
2. F2의 content와 무효 kids 조합.
3. F4의 text block 경계를 넘는 표 병합.
4. 3단 이상 중첩과 중간 노드가 page를 갖는 혼합 순서. 현재는 2단까지만 검증한다.

## 소비자 호환성

- `build_chunks`: 펼친 블록이 기존 타입(heading/paragraph/list/caption/table)으로만 도착한다. 알 수 없는 타입(image 등)은 기존처럼 무시된다. 호환된다.
- `extract_doc_meta`: 호환된다. 탐색 창의 의미 변화는 F1 참고.
- `merge_cross_page_tables` / `_merge_tables`: 행과 셀을 복사한 뒤 갱신하므로 공유된 하위 dict를 변경하지 않는다. 호환된다.
- IBMS 백업 120/116 골든 재현과 전체 테스트 253 통과(조정자 보고)는 위 분석과 모순되지 않는다. 이번 리뷰에서 전체 테스트는 다시 실행하지 않았다. 위 재현 4건만 scratchpad 스크립트로 읽기 전용 실행했으며, 소스·데이터·Qdrant는 변경하지 않았다.
