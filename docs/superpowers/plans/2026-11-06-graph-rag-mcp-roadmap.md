# Graph RAG MCP Implementation Plan — 1차 직접 구현 초안 (보류)

> **동기화 검증 갱신:** 이 초안 작성 뒤 기존 rag-mcp 전체 테스트를 별도 실행 환경에서 다시 확인하여 313 passed, 9 skipped를 확인했다. 본문 끝의 미완료 테스트 기록은 초기 샌드박스 실행 당시의 관찰이다. Graph RAG 자체의 설치·실행 검증 결과는 아니다.

> **2026-11-06 수정:** 사용자가 기존 Qdrant와 코드의 최대 재사용을 기준으로 비교를 요청하여 [LightRAG·LlamaIndex 재사용 기준 비교](2026-11-06-lightrag-vs-llamaindex-reuse.md)를 작성했다. 현재 권고는 **기존 Retriever/Qdrant를 유지하고 LlamaIndex Property Graph를 부분 도입**하는 것이다. 아래 Qdrant-only 그래프 직접 구현 내용과 7~12일 추정은 대안 기록이며, 현재 채택된 구현 사양이 아니다. 후보 조사 기록은 [공개 코드 비교](2026-11-06-graph-rag-open-source-review.md)에 보관한다.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax. 사용자 요청 범위는 현재 계획 수립이며, 저장소 생성·코드 구현·운영 데이터 변경은 별도 실행 요청 이후에 시작한다.

**Goal:** 기존 RAG MCP 코드를 바탕으로 별도 `graph-rag-mcp` 프로젝트를 만들고, 한 문서 안의 본문·표·서식·명시된 절차를 근거와 함께 연결하여 검색한다.

**Architecture:** 기존 PDF 분석, 청크, Kiwi, KURE, Qdrant dense+sparse 검색을 재사용한다. 새 프로젝트의 Qdrant 컬렉션에 노드·관계·구축 상태를 저장하고 Python에서 제한된 관계 탐색을 수행한다. 기존 검색 도구와 그래프 검색 도구를 함께 제공한다.

**Tech Stack:** uv, Python 3.11 기준, Pydantic 2, MCP SDK 2, qdrant-client 1.18.0 기준, 기존 sentence-transformers·Kiwi·OpenDataLoader·PyMuPDF. 첫 버전의 추가 DB·LLM API 의존성은 없다.

## Global Constraints

- 기존 GitHub `jang-hoil/rag-mcp`의 이력·브랜치·remote를 변경하지 않는다.
- 새 프로젝트의 출발점은 로컬 커밋 `6d2bf61`이다. 미커밋 파일을 자동으로 포함하지 않는다.
- `data`는 운영 자료, `work`는 임시 기록이다. 원본 `data`, PDF, Qdrant 컬렉션을 실험 쓰기 대상으로 사용하지 않는다.
- 표 셀은 JSON에서 추출하고 표 청크를 중간 분할하지 않는다. 이미지 서식의 내용을 제목만 보고 추정하지 않는다.
- BM25는 Qdrant sparse를 이용한다. 한국어는 Kiwi로 토큰화하고 코드·금액 토큰을 보존한다.
- dense+sparse 점수 직접 가산 금지. 기존 RRF/DBSF를 유지한다.
- 저장·검색 임베딩 모델은 동일해야 한다. 문서별 컬렉션 및 모델 구분을 유지한다.
- Qdrant local 모드는 serve와 CLI ingest가 같은 경로를 동시에 열지 않는다. 실제 운영은 server 모드를 이용한다.
- 테스트 먼저 작성 → 실패 확인 → 구현 → 관련 테스트 및 전체 `uv run pytest -q` 통과 → 모듈별 커밋 순서를 지킨다.
- 같은 테스트가 두 번 실패하면 근본 원인을 분석하고, 네 번 연속 실패하면 중지하여 보고한다. 테스트를 약화하지 않는다.
- 기존 스펙 §1의 검증 사실은 재검증 대상으로 삼지 않는다.
- 모든 관계는 문서 버전과 출처를 가진다. 근거 없는 관계와 해소하지 못한 참조는 검색 근거로 승격하지 않는다.
- 첫 버전은 한 문서 내부의 연결에 집중한다. 문서 간 연결, 문서 전체 주제 요약, 웹 화면은 후속 범위다.

---

## 1. 사용자 의도와 이번 계획의 가정

첨부 대화에서 확인한 요구사항은 기존 저장소 보존, 기존 코드에 기반한 별도 프로젝트, 로컬 최신 커밋 이용, 문서 내부의 조항·표·절차 관계 검색, Qdrant 재사용이다.

첨부의 “2,3을 동시에”는 답변이 없는 동안 **관계 저장과 검색 연결을 하나의 작동하는 시제품으로 묶는다**는 의미로 가정한다. 뜻을 확인하는 질문은 도구로 제시했다. 문서 내부 관계 검색과 전체 주제 요약을 함께 원한다는 답변이 오면 요약을 별도 단계로 추가한다.

이 문서는 검토 가능한 실행 순서와 완료 기준을 정한 초안이다. 승인된 구현 설계로 간주하거나 실제 개발이 시작됐다고 기록하지 않는다.

## 2. 2026-11-06에 확인한 출발점

| 항목 | 확인 내용 | 근거 |
|---|---|---|
| 기존 원격 저장소 | `jang-hoil/rag-mcp`, public, 기본 브랜치 master | GitHub MCP `get_repo` |
| 실제 원격 master | `1c2cf4e11989b2f779aa373705bc6e34b22fd707` | GitHub MCP `fetch_commit(master)` |
| 로컬 HEAD | `6d2bf61`, 원격보다 5개 커밋 앞섬 | `git log`, `git status` |
| 미커밋 항목 | 수정된 PROGRESS.md와 미추적 data/, work/, 일부 색인목록 | `git status --short --branch` |
| 운영 색인 | 12문서·4,393청크 | 실제 RAG MCP `list_documents` |
| 현재 검색 범위 | `document_id` 필수인 한 문서 검색 | `service.py`, `retrieval.py` |
| 현재 출처 | 청크 ID, PDF 쪽수, 제목 경로, 원본 경로, 이미지 정보 | `models.py` |
| 현재 청크 ID | `문서ID::c순번` | `chunking.py` |
| 현재 컬렉션 | `rag_{model}_{문서명정규화}_{해시}` | `config.py` |
| 목록 조회 주의 | 매니페스트에 없어도 `rag_{model}_` 컬렉션을 발견하여 표시 | `service.py:list_documents` |
| 잠금 | 같은 data_dir의 변경 작업에 프로세스 간 잠금 적용 | `service.py`, `mutation_lock.py` |
| 평가 한계 | 여러 정답 중 첫 적중 결과 중심. 필요한 모든 근거의 동시 회수는 평가하지 않음 | `evaluation.py` |
| lockfile | mcp 2.0.0, qdrant-client 1.18.0, Kiwi 0.23.2, sentence-transformers 5.6.0 | `uv.lock` |
| 현재 가상환경 | CPython 3.12.10 | `.venv/pyvenv.cfg` |

운영 규칙의 기준 환경은 Python 3.11이다. 새 프로젝트는 3.11로 준비하되, 기존 3.12 가상환경을 그대로 복사하지 않는다. 기존 환경에서의 테스트 결과와 새 프로젝트 3.11에서의 결과를 구분해 기록한다.

참조된 `D:/User/Desktop/RAG_MCP_구현스펙_ClaudeCode.md`는 지정 위치에서 찾지 못했다. 이번 계획은 전달된 운영 규칙, PROGRESS, 현재 코드, 첨부 대화, 공식 문서에 근거한다. 누락된 스펙 내용을 추측해서 추가하지 않는다.

## 3. 저장 방식 선택

| 방식 | 장점 | 부담 | 판단 |
|---|---|---|---|
| 청크 payload에 연결 ID 배열 | 가장 작은 실험을 빨리 만들 수 있음 | 역방향 탐색·관계별 근거·버전·중복 참조 관리가 청크 갱신과 얽힘 | 짧은 비교 실험용 |
| **Qdrant 내 별도 그래프 컬렉션** | 기존 DB 재사용, 관계별 조회·검증·교체 가능, 벡터를 다시 만들 필요 없음 | Python에 탐색·무결성 검사가 필요 | **첫 버전 권고** |
| 별도 SQLite/그래프 DB | 복잡한 관계 질의에 별도 장점이 있을 수 있음 | 이중 저장소, 추가 운영, 현재 파이프라인과의 결합 비용 | 요구·측정 결과가 생긴 후 재평가 |

Qdrant 공식 문서는 JSON payload, ID 조회, 벡터가 없는 컬렉션을 지원한다고 설명한다. 따라서 관계마다 임베딩을 만들거나 SQLite를 설치할 필요 없이 그래프 데이터를 저장하는 설계가 가능하다. 이는 저장 기능을 바탕으로 한 **설계 판단**이다. 연결 추출과 여러 단계 탐색은 이번에 만드는 Python 코드가 수행한다.

## 4. 프로젝트와 데이터 분리

예정 로컬 프로젝트는 `D:/User/Desktop/graph-rag-mcp`, 새 GitHub 저장소 이름은 `graph-rag-mcp`로 한다. 실제 이름이 이미 사용 중이면 생성 단계에서 확인하여 충돌을 처리한다. 새 저장소의 초기 가시성은 private를 권고한다.

원본 프로젝트에서 커밋된 코드와 이력만 새 독립 Git 저장소로 가져온다. 실행 시 Codex에 연결할 작업 공간은 앱의 작업 공간/워크트리 관리 규칙을 따른다. 기존 저장소와 `.git`을 공유하는 작업트리만으로 독립 저장소 분리를 완료했다고 보지 않는다.

새 checkout에서는 다음을 명시적으로 구성한다.

```text
프로젝트/배포 이름: graph-rag-mcp
Python import: rag_mcp 유지 (첫 버전에서 일괄 이름 변경 제외)
CLI: graph-rag-mcp
MCP 서버 식별 이름: graph-rag-mcp
RAG_DATA_DIR: D:/User/Desktop/graph-rag-mcp/data
RAG_QDRANT_MODE: server
RAG_QDRANT_URL: 현재 로컬 서버 주소를 실행 시 확인하여 명시
RAG_COLLECTION_PREFIX: grag
문서 벡터 컬렉션: grag_{model}_{문서명정규화}_{해시}
그래프 컬렉션: grag_graph_v1
```

`RAG_COLLECTION_PREFIX`는 **새로 구현할 설정**이다. 현재 코드에 이미 존재하는 변수로 오해하지 않는다. 새 프로젝트에서 `grag`만 허용하는 초기 정책을 두고, 원래 `rag_` 컬렉션에 쓰거나 삭제하지 못하게 한다. 목록·상태·검색·삭제·이관 스크립트의 prefix 하드코딩을 함께 점검한다.

기존 Qdrant 서버 프로세스를 함께 쓰되 컬렉션을 분리한다. 운영 서버 주소가 비어 있으면 기존 DB로 추정하여 접속하지 않고 명시적으로 실패시킨다. 새 프로젝트와 운영 프로젝트가 같은 data_dir를 사용해도 시작하지 못하게 한다.

첫 시험용 문서만 읽기 전용 원본에서 별도 데이터 폴더로 복사한다. 벡터 복사를 쓸 때는 모델·차원·벡터 이름·sparse 설정·문서 내용 해시의 일치를 검사하고 dense/sparse와 payload를 대조한다. 맞지 않으면 새 저장소에서 해당 문서만 다시 색인한다. 원본 Qdrant local 폴더를 서버 storage로 복사하는 방식은 사용하지 않는다.

`.gitignore`는 새 프로젝트에서 data/, work/, 모델 캐시, 실제 평가셋, PDF, 로컬 설정을 제외하도록 보강한다. 기존 공개 이력을 이어받으므로 공개 이력의 민감 자료 포함 여부도 공개 전 점검 대상이다. 샘플 fixture는 합성 문서로 만든다.

## 5. 첫 문서와 관계 범위

첫 후보는 `2025년+공중보건의사제도+운영지침`이다. 현재 224청크, PDF 160쪽이며 제목·본문·서식 위치를 확인한 색인목록이 있다. 작은 IBMS 가이드는 두 번째 호환성 확인 문서로 이용한다. 문서 내용의 현행 법적 효력 판단은 이 개발 계획의 범위가 아니다.

우선 다음 연결을 만든다.

| 관계 | 만들 수 있는 근거 | 피할 추정 |
|---|---|---|
| CONTAINS | 파싱한 장·절·제목 경로와 본문 소속 | 같은 쪽이라는 이유만으로 소속 결정 |
| REFERS_TO | 본문에 적힌 참조 대상이 해당 소속 범위에서 하나로 확인됨 | 번호가 같다는 이유로 다른 부분의 항목에 연결 |
| HAS_TABLE | 제목·표 번호·명시 참조로 표 식별 | 가까운 표를 자동으로 적용 표라고 판단 |
| HAS_FORM | 명시된 서식 참조와 대상 서식의 확인된 위치 | 이미지의 제목만 보고 기재항목 추정 |
| NEXT_STEP | 번호가 있는 동일 절차 또는 명시된 선후 관계 | PDF 읽기 순서를 업무 처리 순서로 간주 |

숫자 하나의 전역 식별자는 쓰지 않는다. 같은 문서에도 운영지침 서식과 수록 규칙 서식이 함께 있으므로 참조 대상 키는 최소한 `(문서 버전, 소속 부분, 대상 유형, 정규화한 번호)`다. 후보가 둘 이상이거나 소속 부분을 정하지 못하면 `unresolved`에 남긴다.

LLM이 문맥을 해석하는 관계는 첫 버전의 기본 기능에서 제외한다. 명시 참조 회수율이 부족한 경우에만 동일 평가셋에서 별도 실험하고, 검증된 관계와 후보 관계를 구분한다.

## 6. 데이터 계약

새 구현은 `src/rag_mcp/graph/` 아래에 모은다. 사용자용 `Chunk.meta` 안에 내부 그래프 상태를 숨기지 않는다.

### 식별자와 버전

- `doc_revision`: 정렬된 청크 ID·본문·쪽수·제목 경로·구조 정보 및 사용한 원문/페이지 앵커의 정규 JSON에 대한 SHA-256. 원문이 있으면 PDF 해시도 포함한다.
- `build_id`: doc_revision, graph_schema_version, extractor_version 및 명시적인 앵커 입력의 해시로 결정한다.
- `node_id`: UUIDv5. 문서 버전, 노드 유형, 소속 부분, 출처 앵커를 포함한다.
- `edge_id`: UUIDv5. build_id, source_node_id, relation, target_node_id, 근거 앵커로 결정한다.
- 청크 ID만으로 오래된 관계를 다시 연결하지 않는다. 텍스트가 바뀌어 `c12`가 다른 내용이 되면 다른 doc_revision이다.

### Qdrant 그래프 레코드

벡터 없는 `grag_graph_v1`에 `record_type=node|edge|build|document_state`를 저장한다. 공통 범위는 project namespace, document_id, embedding_model, doc_revision, build_id다. document_state는 문서당 안정 ID를 가지고 현재 유효한 빌드와 상태를 가리킨다.

- **node:** node_id, kind(document/section/chunk/table/form/step), label, reference_scope, chunk_ids, PDF 쪽수/블록 앵커.
- **edge:** source_node_id, target_node_id, relation, evidence, extraction_kind(structural/explicit/manual), rule_id.
- **evidence:** 문서 버전, chunk_id와 해당 본문의 start/end offset 또는 확인된 PDF 페이지 앵커, 짧은 원문 인용.
- **build:** schema/extractor 버전, 상태(building/ready/error), node/edge/unresolved 수, 오류와 생성 시각.
- **document_state:** 현재 문서 버전, active_build_id, 상태(absent/building/ready/stale/deleting/error).

chunk 근거의 offset은 청크 문자열 기준이며 실제 부분문자열 일치를 검증한다. 이미지 페이지의 근거는 PDF 물리 쪽수와 실제 페이지 이미지로 검증한다. 텍스트가 없는 서식은 `chunk_ids=[]`인 페이지 앵커 노드를 허용하되, 확인된 표제·위치만 반환하고 본문 내용은 생성하지 않는다. 이를 위한 수동 앵커는 새 프로젝트의 비공개 자료에 별도 기록한다.

현재 병합 표는 시작 쪽수 하나만 보존할 수 있다. 끝 쪽수를 복원하지 못하면 시작 쪽으로 표시하며, 정확한 범위를 가진 것처럼 만들지 않는다. 그래프 추출을 위해 청크 본문이나 기존 청크 ID 생성 규칙을 바꾸지 않는다.

payload index 후보는 record_type, document_id, embedding_model, doc_revision, build_id, source_node_id, target_node_id, relation이다. 실제 사용한 필드만 만든다.

## 7. 검색 계약과 범위 제한

기존 `search_documents`는 기존 형식을 유지한다. 그래프 검색은 별도 MCP 도구로 추가한다.

```text
search_graph_documents(query, document_id, top_k=8, max_hops=1, max_related=8,
                       fiscal_year=None, filters=None, embedding_model=None)
get_document_relations(document_id, chunk_id, max_hops=1, limit=20)
build_document_graph(document_id, force=False)
graph_status(document_id)
```

위 이름과 인자는 **추가할 인터페이스**다. build_document_graph는 기존 작업 관리자를 이용해 job_id를 반환하고, 기존 ingest_status에서 단계를 확인한다. 구축 후 수동 활성화가 아니라 검증 성공 시에만 자동 활성화한다.

검색 흐름은 다음과 같다.

1. 기존 Retriever로 문서·모델·연도·metadata 조건을 적용하여 검색한다.
2. 해당 청크가 현재 문서 버전의 활성 그래프에 속하는지 검사한다.
3. 명시 참조와 제한된 소속 관계를 따라간다. 초기 max_hops=1, 허용 최대 2, max_related 최대 16으로 제한한다.
4. 조회를 단계별로 묶고 방문 노드를 기억한다. 관계 레코드 확인 상한 100, 방문 노드 상한 64를 둔다. 구조 부모를 거쳐 문서 전체로 퍼지는 탐색은 금지한다.
5. 연결 대상·모든 근거에도 같은 문서/버전/모델 및 원래 필터를 검사한다. metadata 필터를 판정할 수 없는 이미지 노드는 그 필터가 있을 때 제외한다.
6. 원문·PDF 쪽수·연결 경로·어느 검색 결과에서 출발했는지를 반환한다.

응답은 `baseline_results`, `related_results`, `graph_status`, `diagnostics`로 구분한다. baseline_results는 기존 검색 결과 그대로다. related_results는 원문/페이지 앵커, 관계 경로, seed_chunk_ids, hop_count를 포함한다. 그래프로 찾았다는 이유로 임의의 유사도 점수를 만들지 않는다. related 결과는 홉 수 → 명시 관계 우선 → 출발 결과 순위 → 안정 ID 순으로 정렬한다.

그래프 없음·구축 중·버전 불일치·그래프 timeout이면 baseline_results를 그대로 돌려주고 짧은 진단을 남긴다. 그래프 예산은 초기 500ms로 두고 실측 후 조정한다. 기존 기본 검색 오류는 그래프 fallback으로 감추지 않는다.

## 8. 문서 변경과 장애 처리

첫 시제품부터 문서 버전 확인은 필수다. 전체 자동 갱신은 뒤 단계에서 연결하더라도 오래된 관계는 사용하지 않는다.

1. 같은 data_dir의 기존 변경 잠금을 획득한다.
2. 내용 변경/삭제 작업은 document_state를 stale/deleting으로 먼저 바꾸고 완료를 확인한다.
3. 기존 경로로 청크를 교체하거나 삭제한다. 새 청크 payload와 로컬 저장 청크/manifest에 doc_revision을 일치시킨다.
4. 새 그래프를 별도 build_id로 stage한다. 관계와 노드 존재, 근거 문자열, 버전, scope, 개수를 검증한다.
5. 쓰기 완료를 확인한 뒤 document_state의 활성 포인터를 새 빌드로 바꾼다.
6. 이번 문서의 비활성 빌드를 정리한다. 삭제 중 실패한 잔여 그래프는 deleting 상태에서 계속 제외하고 재시도한다.

Qdrant 여러 컬렉션과 파일이 한 트랜잭션으로 갱신된다고 가정하지 않는다. `wait=True`는 해당 DB 작업의 완료 확인에만 사용한다. 문서 변경 중 들어온 검색은 시작과 끝의 document_state 및 각 대상의 doc_revision을 검사하고 달라지면 그래프 결과를 버린다.

같은 원문에서 그래프만 재구축하다 실패하면 기존 ready 빌드를 유지한다. 원문이 바뀐 뒤 그래프 구축이 실패하면 새 원문에 대한 기본 검색을 제공하고 그래프는 error/stale로 남긴다. 강제 종료 후 building이 남아도 검색에 사용하지 않으며, 재시작은 미완료 작업을 재시도 가능 상태로 보고한다.

## 9. 파일별 책임과 실행 작업

아래 경로는 별도 생성할 `D:/User/Desktop/graph-rag-mcp`를 기준으로 한다. 원래 프로젝트의 src/와 tests/에 기능을 구현하지 않는다.

| 파일 | 책임 |
|---|---|
| `src/rag_mcp/graph/models.py` | 노드·관계·근거·상태·응답 데이터 계약 |
| `src/rag_mcp/graph/identity.py` | doc_revision, build_id, UUID 생성 |
| `src/rag_mcp/graph/store.py` | 범위 제한 Qdrant 읽기/쓰기와 상태 활성화 |
| `src/rag_mcp/graph/extract.py` | 제목 소속·명시 참조·순서 관계 추출 및 unresolved 보고 |
| `src/rag_mcp/graph/retrieve.py` | 제한 탐색, 필터·출처·버전 확인, fallback |
| `src/rag_mcp/graph/lifecycle.py` | 구축·갱신·삭제·복구 순서 |
| `src/rag_mcp/graph/evaluation.py` | 필요한 모든 근거와 관계 경로의 평가 |
| `src/rag_mcp/config.py`, `service.py`, `server.py`, `cli.py` | prefix, 서비스 위임, 도구/CLI 등록 |
| `src/rag_mcp/models.py`, `indexer.py` | doc_revision 전파와 갱신 시점 연결 |
| `tests/fixtures/graph.example.json` | 공개 가능한 합성 문서·정답 관계 |

### 작업 0 — 독립 프로젝트와 기존 검색 기준선

- [ ] 로컬 출발 커밋, 원격 커밋, 미커밋 파일 목록을 새 프로젝트 기록으로 남긴다.
- [ ] 독립 Git 저장소·새 가상환경·MCP/CLI 이름·데이터 경로를 준비한다.
- [ ] `tests/test_graph_namespace.py`를 먼저 작성한다. 같은 서버에 rag_/grag_가 함께 있어도 새 앱은 grag_만 목록·조회·삭제하며, 잘못된 prefix와 동일 data_dir는 시작 단계에서 실패해야 한다.
- [ ] config와 목록/상태/삭제의 하드코딩을 수정하고 위 테스트의 실패→통과를 확인한다.
- [ ] 첫 문서 사본을 준비하고 기존 검색·페이지 이미지·MCP 도구 호출을 확인한다. 벡터 재사용과 재임베딩 여부를 기록한다.
- [ ] 기존 원본의 전체 검증이 이번 환경에서 완료되지 않은 점을 고려해 Windows asyncio 소켓 대기를 먼저 진단한다. 실행 환경 문제와 원본 결함을 구분하고 새 환경의 기준선 결과를 확보한다.
- [ ] 새 Python 3.11 환경의 전체 테스트를 통과시키고 작은 커밋을 만든다.

완료 기준: 두 MCP를 함께 실행해도 문서 목록과 쓰기 대상이 분리되고, 새 프로젝트에서 기존 검색이 동작한다.

### 작업 1 — 데이터 계약과 Qdrant 그래프 저장

- [ ] `tests/test_graph_identity.py`, `tests/test_graph_store.py`를 먼저 작성한다. 동일 입력의 ID 안정성, 본문/쪽수 변경의 revision 변경, 중복 upsert, 다른 문서/버전 조회 차단, 벡터 없는 저장을 검증한다.
- [ ] models/identity/store를 구현한다. 모든 쿼리와 삭제에 namespace/document/model/revision 범위를 지정한다.
- [ ] 임시 local Qdrant에서 저장·정방향/역방향 조회·부분 실패를 검증한다.
- [ ] 운영 컬렉션을 건드리지 않는 임시 server 컬렉션에서도 벡터 없는 저장·필터·ID 조회 호환성을 확인하고 자기 컬렉션만 정리한다.
- [ ] 관련 테스트와 전체 테스트를 통과시키고 커밋한다.

완료 기준: 관계 하나를 저장·조회하면 연결된 두 항목과 근거 및 문서 버전이 복원된다.

### 작업 2 — 관계 추출과 검색 연결을 함께 완성

- [ ] 합성 문서 fixture에 `신청 절차 → 별지 제1호 서식`, 다른 소속의 동명 서식, 참조 없는 인접 표, 명시한 1→2 단계, 순환 참조를 넣는다.
- [ ] `tests/test_graph_extraction.py`, `tests/test_graph_retrieval.py`를 먼저 작성한다. 정답 연결 회수, 중복 번호 미확정 처리, 인접 관계 오인 방지, 출처 offset 일치, 1/2홉 상한·순환 종료·필터 상속·fallback을 검증한다.
- [ ] extract/retrieve와 최소 service 위임을 한 기능 단위로 구현한다. 저장 코드만 끝내고 실제 검색이 없는 상태를 이 작업의 완료로 보지 않는다.
- [ ] `tests/test_graph_mcp_tools.py`에서 검색·관계조회·구축·상태 도구 스키마와 기존 도구 호환성을 확인한다.
- [ ] 첫 시험 문서에서 명시 참조를 점검한다. 텍스트로 판정하지 못한 서식은 unresolved로 남기거나 검증된 페이지 앵커를 추가한다.
- [ ] 관련 테스트와 전체 테스트를 통과시키고 커밋한다.

완료 기준: 실제 질문 하나로 기본 검색 결과와 연결된 표/서식의 원문 또는 페이지 이미지를 함께 확인한다.

### 작업 3 — 재색인·삭제·강제 종료 복구

- [ ] `tests/test_graph_lifecycle.py`를 먼저 작성한다. 같은 c순번에 다른 본문, 그래프 구축 중 종료, 본문 교체 성공/그래프 실패, 상태 활성화 실패, 삭제 중 실패, 검색/갱신 경합을 재현한다.
- [ ] lifecycle과 indexer/jobs의 연결을 구현하고 문서 버전을 payload·manifest·로컬 청크에 동일하게 저장한다.
- [ ] graph 전용 작업도 기존 mutation 잠금과 job 상태를 사용하도록 연결한다.
- [ ] 두 프로세스의 겹친 구축/재색인은 하나만 변경하며 후발 요청은 busy를 받는지 확인한다.
- [ ] 실패 후 기본 검색과 안전한 재시도, 잔여 그래프의 검색 제외를 확인한다.
- [ ] 관련 테스트와 전체 테스트를 통과시키고 커밋한다.

완료 기준: 변경·삭제·오류 후에도 오래된 원문을 가리키는 관계가 반환되지 않는다.

### 작업 4 — 비교 평가와 채택 판단

- [ ] 첫 문서의 60문항을 만든다. 단일 사실/표/금액 20, 본문↔표/서식 20, 절차 연결 10, 모호·없는 관계 10으로 나눈다.
- [ ] 각 유형을 절반씩 나누어 개발용 30/고정 평가용 30으로 분리한다. 이미지를 포함한 정답 근거와 올바른 경로를 사람이 확인한다.
- [ ] `tests/test_graph_evaluation.py`를 먼저 작성한다. 근거 하나만 찾으면 전체 정답으로 채점하는 오류와 다른 쪽의 동명 항목을 정답으로 보는 오류를 방지한다.
- [ ] 기존 검색 A, 주변 문단을 추가한 B, 그래프 연결을 추가한 C를 같은 모델·문서·질문·reranker 설정에서 비교한다.
- [ ] 최종 8개/16개 근거 예산별로 비교한다. B/C는 절반을 상위 seed로 고정하고 남은 자리를 각각 인접/관계 근거로 채운 뒤 빈자리는 기본 검색으로 보충한다. A는 같은 개수의 상위 결과를 사용한다.
- [ ] 세 방식 모두 16,000자 예산을 적용한다. 표는 자르지 않고 하나만으로 초과하면 overflow를 기록하여 별도 비교한다. 이미지 근거 수/크기도 함께 보고한다.
- [ ] 후보·규칙·상한을 개발용에서 정한 뒤 고정 평가용을 한 번 실행하고 결과·소요시간·실패 사례를 보관한다.
- [ ] 관련 테스트와 전체 테스트를 통과시키고 평가 보고서를 커밋한다. 실제 문항·문서 내용은 공개 커밋에서 제외한다.

초기 채택 목표는 합의 전의 제안값이다.

| 지표 | 제안 기준 |
|---|---|
| 전체 필요 근거 충족률 | 관계형 고정 문항에서 A/B 중 나은 쪽보다 +10%p 이상 |
| 표본 관계 정확도 | 검토한 관계에서 95% 이상; 표본 수와 오류 유형 함께 공개 |
| 필수 근거의 페이지/이미지 앵커 | 고정 평가에서 잘못된 출처 0건 |
| 버전·문서·필터 혼입 | 자동 회귀 및 평가에서 0건 |
| 단일 사실/금액·코드 | 해당 고정 문항에서 A 대비 추가 실패 0건 |
| 그래프 추가 지연 | 모델 warm 상태에서 p95 500ms 이하; 기본 검색 시간과 별도 보고 |
| 실패 시 동작 | 정상 기본 검색이 있으면 그대로 반환, 오래된 관계 0건 |

총 필요 근거 중 찾은 비율도 별도로 계산한다. 예를 들어 필요한 세 근거 중 하나를 찾으면 evidence recall은 1/3이고 전체 근거 충족 여부는 실패다. 기존 evaluation.py의 단일 적중 지표와 이름을 혼용하지 않는다. 소표본 결과는 분자/분모를 함께 적고 일반화된 성능 보장으로 표현하지 않는다.

### 작업 5 — 운영 문서와 새 저장소 정리

- [ ] README와 `docs/graph-rag-operations.md`에 설정, 도구, 재구축, 장애, fallback, 데이터 격리, 이미지 한계를 적는다.
- [ ] doctor에 prefix·data_dir·그래프 상태·스키마 버전 검사를 연결하고 관련 테스트를 추가한다.
- [ ] 새 환경의 전체 테스트, 실제 stdio 도구 목록·검색·이미지·graph 상태 호출을 확인한다.
- [ ] 원본 MCP의 목록/청크 수가 작업 전과 같고 새 앱에서 원본 컬렉션 쓰기 요청이 없었음을 확인한다.
- [ ] 새 원격 저장소만 연결하여 변경 내역과 포함 파일을 검토하고 새 프로젝트만 push한다.

완료 기준: 기존 RAG와 새 Graph RAG를 독립적으로 실행하고 새 문서의 색인·검색·갱신·복구 절차를 재현할 수 있다.

## 10. 실행 시 공통 검증 명령

다음 명령은 **새 프로젝트** 작업 폴더에서 실행한다. 현재 원본 프로젝트에서 구현을 시작하라는 뜻이 아니다. 테스트 fixture는 tmp_path/in-memory store를 쓰고 운영 주소와 데이터 폴더를 상속하지 않는다.

```powershell
# uv 캐시 쓰기가 제한된 환경에서는 새 프로젝트의 work를 사용한다.
$env:UV_CACHE_DIR = Join-Path (Get-Location) 'work\uv-cache'
uv run --offline --no-sync pytest tests/test_graph_namespace.py -q
uv run --offline --no-sync pytest tests/test_graph_identity.py tests/test_graph_store.py -q
uv run --offline --no-sync pytest tests/test_graph_extraction.py tests/test_graph_retrieval.py tests/test_graph_mcp_tools.py -q
uv run --offline --no-sync pytest tests/test_graph_lifecycle.py -q
uv run --offline --no-sync pytest tests/test_graph_evaluation.py -q
uv run --offline --no-sync pytest -q
```

의존성이 새로 필요한 최초 설치는 준비 단계에서 lockfile로 수행한다. offline/no-sync는 준비된 환경의 반복 테스트용이다. 각 명령은 해당 작업을 구현한 뒤 실행하며, RED 확인 시 해당 동작의 실패인지 먼저 확인한다. 모듈 누락·환경 권한 문제를 구현 결함과 섞지 않는다. `test_find_project_root_returns_none_without_marker`는 부모 디렉터리를 탐색하므로 `--basetemp`를 프로젝트 아래에 두면 안 된다. 기본 임시 폴더 쓰기가 제한되면 프로젝트 밖의 허용된 임시 경로를 지정한다.

## 11. 순서와 작업량

| 순서 | 결과물 | 예상 집중 작업량 |
|---|---|---|
| 0 | 독립 프로젝트와 검색 기준선 | 0.5~1일 |
| 1 | Qdrant 그래프 저장·출처 계약 | 1~2일 |
| 2 | 관계 저장+검색 연결 시제품 | 2~3일 |
| 3 | 갱신·삭제·오류 복구 | 1~2일 |
| 4 | 60문항 작성·검토·비교 평가 | 2~3일 |
| 5 | 운영 문서·연결·정리 | 0.5~1일 |

합계 7~12일은 한 명이 집중하는 경우의 계획상 추정이다. 모델/환경 설치와 이미지 문서의 수동 확인에 따라 달라진다. 시제품을 먼저 확인한 뒤 남은 작업량을 다시 추정한다.

## 12. 후속 확장 조건

- **문서 전체 주제 요약:** 사용자가 원하면 동일 Qdrant 안의 요약 레코드와 원문 근거 연결을 별도 설계한다. Microsoft GraphRAG Global Search의 커뮤니티 요약 개념은 비교 대상이며, 한 문서 내 참조 검색의 완성 조건과 구분한다.
- **문서 간 연결:** 여러 문서 검색 라우팅, 문서 식별/발행연도/개정 버전과 동명 항목 해소를 설계한 뒤 진행한다.
- **LLM 관계 추출:** 명시 참조 방식의 누락 사례가 쌓였을 때만 검토한다. 근거 검증과 비용·문서 전송 범위를 포함해 비교한다.
- **별도 그래프 DB:** 수백~수천 관계의 제한 탐색으로 충족하지 못하는 실제 질의, 측정된 지연·메모리 부담, 복잡한 경로 요구가 확인되면 비교한다. 관계 개수만으로 자동 전환하지 않는다.
- **그래프 화면:** 근거를 검토할 실질적 필요가 생기면 별도 화면을 설계한다. 첫 MCP 버전의 필수 항목에 포함하지 않는다.

## 13. 근거 자료

- [Qdrant Payload: JSON 자료와 필터](https://qdrant.tech/documentation/manage-data/payload/)
- [Qdrant Collections: 벡터 없는 컬렉션](https://qdrant.tech/documentation/manage-data/collections/)
- [Qdrant Points: ID 조회와 작업 완료 대기](https://qdrant.tech/documentation/manage-data/points/)
- [Microsoft GraphRAG Query Engine: Local/Global Search 구분](https://microsoft.github.io/graphrag/query/overview/)
- [기존 GitHub 저장소](https://github.com/jang-hoil/rag-mcp)
- 로컬 근거: PROGRESS.md, AGENTS.md, pyproject.toml, uv.lock, src/rag_mcp/{models,config,chunking,pipeline,indexer,vector_store,retrieval,service,server,evaluation}.py, data/문서목록.md, 공중보건의사 운영지침 색인목록.

## 14. 이번 계획 작업의 확인 결과

원격 정보와 운영 문서 목록을 읽기 전용으로 확인했다. 새 저장소 생성, 프로젝트 분리, 기능 구현, 재색인, 운영 DB 변경은 수행하지 않았다. 원본 소스/테스트를 바꾸지 않고 이 계획 문서와 PROGRESS의 계획 기록만 추가한다.

이번 검증의 관찰 결과는 다음과 같다.

- 첫 uv 호출은 기본 캐시 경로의 쓰기 권한 때문에 실행되지 않았다. 작업 폴더의 UV_CACHE_DIR를 지정한 호출은 실행됐다.
- 전체 pytest를 프로젝트 내부 basetemp로 실행해 경로 테스트 실패 1건을 관찰했고, 이후 진행 정지로 실행을 중단했다. 경로 실패는 임시 폴더의 조상에 프로젝트 표식이 존재해서 발생한 검증 위치 오류다. 원본 소스를 고치지 않았다.
- 프로젝트 밖의 허용된 임시 경로로 `tests/test_config_paths.py`를 실행하여 **8 passed, exit 0**을 확인했다.
- MCP 도구 테스트의 별도 실행에서 20초 faulthandler 기록을 확보했다. `test_mcp_sdk_v2_server_registers_the_eleven_tools`가 `asyncio.run` → Windows proactor `_make_self_pipe` → socketpair fallback의 `accept`에서 대기했다. 이 호출을 중단했으며, MCP 도구 전체 통과를 주장하지 않는다. 이 기록만으로 SDK 또는 서버의 결함이라고 단정하지 않는다.
- 전체 테스트의 이번 통과 수는 확정되지 않았다. 기존 PROGRESS의 **313 passed/9 skipped**는 이전 기록이며 이번 실행 결과가 아니다. 첫 구현 단계에 새 Python 3.11 환경의 기준선 재확인을 포함했다.

자체 검토에서는 저장소 분리, Qdrant 재사용, 관계 저장+검색 연결, 출처, 중복 번호, 이미지 한계, 문서 버전, 오류 복구, 공정한 비교 평가가 각 실행 작업과 연결되는지 확인했다. 계획의 숫자는 관측 성능과 목표/추정치를 구분하여 적었다.
