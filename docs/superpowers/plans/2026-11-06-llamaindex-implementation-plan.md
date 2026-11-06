# LlamaIndex 기반 별도 Graph RAG MCP Implementation Plan

> **2026-11-06 코드 기준점 보관:** 이 계획은 처음에는 원본 프로젝트 밖에 작성했으며, 사용자의 기존 저장소 커밋·푸시 요청에 따라 문서 사본을 여기 보관한다. 기존 rag-mcp의 동기화 전 전체 테스트는 313 passed, 9 skipped로 재확인했다. 아래 새 프로젝트의 Python 3.11·LlamaIndex 실행 검증은 구현 단계의 작업이다.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax. 현재 요청은 계획 작성이며 실제 저장소 생성·패키지 설치·색인·코드 구현을 시작하라는 요청으로 해석하지 않는다.

**Goal:** 기존 rag-mcp를 보존하고, 코드와 Qdrant 벡터를 재사용하는 독립 graph-rag-mcp 프로젝트에서 한 문서의 본문·표·서식·명시된 절차 관계를 검색한다.

**Architecture:** 로컬 커밋 6d2bf61에서 독립 저장소를 만든다. 기존 Qdrant 서버 안에 새 프로젝트 전용 grag_ 컬렉션을 만들고 선택 문서의 기존 벡터·청크를 복사한다. 기존 Retriever가 검색한 청크를 LlamaIndex 그래프에 연결한 뒤 원문·PDF 쪽수·관계 근거를 MCP로 반환한다.

**Tech Stack:** uv, Python 3.11, 기존 rag_mcp 코드·MCP SDK 2·qdrant-client·KURE·Kiwi, llama-index-core==0.14.25, Pydantic 2, pytest. 첫 버전에 SQLite·Neo4j·LlamaParse·LlamaIndex Qdrant 어댑터·관계용 새 임베딩을 필수로 추가하지 않는다.

## Global Constraints

- 원본 프로젝트 D:/User/Desktop/rag mcp의 코드·설정·Git 이력·미커밋 파일·운영 문서를 변경하지 않는다.
- 원본 GitHub jang-hoil/rag-mcp에 commit/push/설정 변경을 하지 않는다.
- 기존 Qdrant 서버와 모델은 재사용하지만 원래 rag_ 컬렉션에는 쓰기·삭제·스키마 변경을 하지 않는다.
- 새 프로젝트 경로는 D:/User/Desktop/graph-rag-mcp, 배포·CLI·MCP 이름은 graph-rag-mcp, 개발 브랜치는 codex/llamaindex-graph-rag로 한다.
- 영구 자료는 새 프로젝트 data/, 임시 로그·스크립트는 work/로 분리한다.
- 표 셀은 JSON에서 추출하고 표 청크를 임의 분할하지 않는다. 이미지 서식은 제목만 보고 내용을 생성하지 않는다.
- 한국어 토큰화는 Kiwi, 코드·금액 토큰은 보존한다. BM25 별도 pkl은 만들지 않는다.
- dense+sparse 점수 직접 가산 금지. 기존 RRF/DBSF를 유지한다.
- 저장·검색 임베딩 모델을 동일하게 유지한다. 수입한 벡터의 모델·차원·sparse 설정을 검사한다.
- Python은 uv+3.11로 새 환경을 만든다. 현재 원본 .venv는 3.12.10이므로 복사하지 않는다.
- 각 모듈은 테스트 먼저 → RED 원인 확인 → 구현 → 관련 테스트와 전체 pytest 통과 → 작은 커밋 순서로 진행한다.
- 같은 테스트가 두 번 실패하면 근본 원인을 분석한다. 네 번 연속 실패하면 중지해 보고한다. 테스트를 약화하지 않는다.
- 스펙 §1의 기존 검증 사실은 다시 검증하지 않는다. 확인된 코드 경계와 신규 요구사항을 검증한다.
- Qdrant local 저장소를 운영 서버와 동시에 열지 않는다. 새 앱의 운영 접속은 명시한 server URL만 사용한다.
- 사용자 문서는 공개 Git에 포함하지 않는다. 공개 fixture·예시는 합성 문서로 작성한다.
- 기본 검색 결과와 그래프 관계의 출처·문서·버전을 모두 검증한다. 불확실한 연결은 unresolved로 반환한다.

---

## 1. 확정한 범위와 개발 경계

사용자가 선택한 방향은 LlamaIndex, 기존 프로젝트 보존, 별도 프로젝트, 기존 Qdrant·코드의 최대 재사용이다. 첫 완성 단위는 한 문서의 관계 저장과 검색 연결을 함께 제공하는 MCP다.

첫 버전은 제목 소속·명시 참조·명시된 단계 순서를 처리한다. 기존 검색이 찾은 문단에서 한두 단계 떨어진 표·서식을 함께 반환한다. 전체 문서 주제 요약, 여러 문서 간 개체 통합, 자유로운 자연어 관계의 LLM 추출, 그래프 웹 화면은 후속 범위다.

관계 추출의 기본값은 explicit이다. 명시 규칙과 검증된 앵커를 LlamaIndex의 TransformComponent/개체·관계 모델로 변환하므로 첫 버전에는 외부 LLM 키가 필요하지 않다. 문맥 관계를 추출하는 SchemaLLMPathExtractor는 모델과 비용 조건을 정한 뒤 별도 실험한다.

현재 지정 위치의 상위 RAG_MCP_구현스펙_ClaudeCode.md는 이전 확인에서 찾지 못했다. 이 계획은 전달된 운영 규칙, 현재 checkout, 기존 비교 문서와 공식 소스에 근거한다. 실제 실행 전 해당 스펙이 제공되면 신규 범위의 충돌만 확인한다.

## 2. 별도 저장소와 재사용 방법

### 2.1 코드의 출발점

확인한 원본 로컬 HEAD는 6d2bf61이며 origin/master보다 5커밋 앞서 있다. 원격 저장소의 코드만 복사하면 최근 개선이 빠지므로 로컬 커밋을 기준으로 만든다. 미커밋 PROGRESS.md, data/, work/, PDF 목록은 자동 복사하지 않는다.

실행 시 사용할 독립 복제 명령은 다음과 같다. 기존 Git 저장소와 .git을 공유하는 worktree만으로 분리를 완료했다고 보지 않는다. 목표 폴더가 이미 존재하면 덮어쓰지 않고 내용과 remote를 확인해 재사용 가능한지 먼저 판단한다.

```powershell
git clone --no-hardlinks --no-checkout --origin source-base 'D:\User\Desktop\rag mcp' 'D:\User\Desktop\graph-rag-mcp'
git -C 'D:\User\Desktop\graph-rag-mcp' switch -c codex/llamaindex-graph-rag 6d2bf61
git -C 'D:\User\Desktop\graph-rag-mcp' remote set-url --push source-base DISABLED
```

새 GitHub 저장소 이름은 graph-rag-mcp, 초기 가시성은 private를 기본 제안으로 한다. 실제 생성은 구현 단계에서 진행한다. origin은 새 저장소만 가리키며 원래 저장소에는 push하지 않는다. 프로젝트 실행·Codex 연결은 새 checkout을 사용한다.

### 2.2 코드 재사용 경계

| 영역 | 처리 |
|---|---|
| src/rag_mcp/ | 커밋된 코드를 새 저장소에 복제. Python import 이름 유지 |
| parser·chunker·표·OCR·페이지 이미지 | 기존 동작 재사용 |
| KURE·Kiwi·sparse·reranker | 기존 구현과 모델 정책 유지 |
| VectorStore·Retriever | 기본 검색을 계속 담당 |
| Config·RagService의 컬렉션 목록 처리 | 새 저장소에서만 prefix 지원을 최소 수정 |
| 원본 src/rag_mcp/server.py | 새 MCP 서버에서 사용하지 않는 기존 진입점. 기존 11개 도구 회귀 테스트는 보존 |
| src/graph_rag_mcp/ | 새 설정·가져오기·LlamaIndex 그래프·수명주기·MCP 연결 구현 |

새 pyproject의 project.name과 CLI는 graph-rag-mcp로 지정하고 wheel에 rag_mcp와 graph_rag_mcp 두 패키지를 포함한다. 기존 모든 모듈을 이름 변경하는 작업은 하지 않는다. 새 서버는 전역 rag_mcp.server.service() 대신 명시한 Config로 만든 RagService 인스턴스를 호출한다.

새 pyproject의 프로젝트·의존성·빌드 섹션은 다음 내용으로 설정한다. 기존 optional dev/ocr와 pytest 설정은 함께 유지한다.

```toml
[project]
name = "graph-rag-mcp"
version = "0.1.0"
description = "기존 RAG 검색과 LlamaIndex 관계 탐색을 결합한 MCP 서버"
requires-python = ">=3.11,<3.13"
dependencies = [
  "mcp>=2,<3",
  "anyio>=4.0.0",
  "qdrant-client>=1.12.0",
  "sentence-transformers>=3.0.0",
  "kiwipiepy>=0.18.0",
  "pydantic>=2.0.0",
  "opendataloader-pdf>=0.1.0",
  "pymupdf>=1.24.0",
  "truststore>=0.10.0",
  "llama-index-core==0.14.25",
]

[project.scripts]
graph-rag-mcp = "graph_rag_mcp.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/rag_mcp", "src/graph_rag_mcp"]
```

## 3. Qdrant·파일·설정 분리

### 3.1 데이터 위치

```text
D:/User/Desktop/rag mcp/                 원본: 운영 그대로 유지
D:/User/Desktop/graph-rag-mcp/           신규 독립 저장소
  src/rag_mcp/                          기존 기반 코드
  src/graph_rag_mcp/                    새 그래프 기능
  data/
    parsed/                            복사한 파싱·청크·이미지
    manifests/                         새 프로젝트 매니페스트
    sources/                           가져온 문서의 PDF 사본
    imports/                           source→target 대조·복구 기록
    catalog/                           문서 상태·데이터 epoch
    graphs/<doc-key>/<graph-revision>/
      graph.json                       LlamaIndex property graph
      provenance.json                  관계별 근거·chunk→anchor 역색인
      build.json                       빌드 버전·개수·검증 결과
    graph-active/                      문서별 활성 빌드 포인터
    .locks/                            새 프로젝트 변경 잠금
  tests/                               복제한 회귀 테스트와 신규 테스트
  eval/                                합성 예제와 비공개 평가셋
  work/                                임시 자료
```

Qdrant 프로세스는 기존 것을 사용한다. 기존 컬렉션은 rag_, 새 컬렉션은 grag_로 구분한다. 문서별·모델별 컬렉션 이름의 나머지 생성 규칙은 재사용한다. 그래프는 첫 버전에서 SimplePropertyGraphStore의 별도 파일로 저장한다. SQLite 설치는 필요하지 않다.

새 GraphSettings는 아래 변수를 읽어 Config의 data_dir/qdrant_mode/qdrant_url/embedding_model/collection_prefix를 명시적으로 구성한다. 현재 RAG_DATA_DIR 같은 전역 설정이 새 프로젝트 경로를 덮어쓰지 못하게 한다.

```dotenv
GRAPH_RAG_DATA_DIR=D:/User/Desktop/graph-rag-mcp/data
GRAPH_RAG_QDRANT_URL=http://127.0.0.1:6333
GRAPH_RAG_COLLECTION_PREFIX=grag
GRAPH_RAG_EMBEDDING_MODEL=kure
GRAPH_RAG_SOURCE_DATA_DIR=D:/User/Desktop/rag mcp/data
GRAPH_RAG_SOURCE_QDRANT_URL=http://127.0.0.1:6333
GRAPH_RAG_EXTRACTOR=explicit
GRAPH_RAG_MAX_HOPS=1
```

URL은 현재 운영 접속값을 실행 시 확인하여 명시한다. 빈 URL에서 local 모드로 자동 전환하지 않는다. 새 data_dir가 원본과 같거나 원본의 내부/상위 경로인 경우 시작하지 않는다. 링크를 resolve한 실제 경로로 확인한다.

복제한 Config에 collection_prefix 필드를 추가하되 기반 코드의 기본값 rag는 기존 테스트를 위해 유지한다. 새 GraphSettings에서 grag만 허용한다. RagService.list_documents의 rag_ 하드코딩도 config prefix를 따르도록 새 저장소에서만 수정한다. 쓰기 함수는 최종 컬렉션이 grag_인지 다시 검사한다.

### 3.2 기존 벡터를 가져오는 방법

live 원본 컬렉션을 새 앱의 변경 대상으로 연결하지 않는다. 첫 시험 문서 한 건을 새 컬렉션에 복사하며 재임베딩하지 않는다. 이는 서버·모델·벡터·본문을 재사용하고 운영 컬렉션을 분리하는 선택이다.

가져오기는 다음 순서를 지킨다.

1. dry-run으로 source manifest, source collection, 모델·차원·dense/sparse 설정, 포인트/청크 수, PDF/이미지 경로와 대상 이름을 보고한다.
2. source의 기존 mutation.lock이 존재함을 확인하고 같은 DataMutationLock 규약으로 잠금을 획득한다. 변경 작업이 있으면 busy를 반환하고 즉시 종료한다. 원본 콘텐츠나 DB를 쓰지 않고 일관된 읽기를 위한 잠금만 사용한다.
3. source 잠금 → target 잠금 순서로 비블로킹 획득한다. 실패하면 이미 획득한 잠금도 finally에서 해제한다. 데이터/벡터/파일의 일관된 원본 snapshot을 새 data/imports의 임시 영역으로 읽는다. snapshot 읽기가 끝나면 source 잠금을 즉시 해제하고 target 작업을 이어간다.
4. source는 읽기 전용 클라이언트 경계를 둔다. 허용 연산은 describe/list/scroll/retrieve이며 create/upsert/delete/update alias를 노출하지 않는다. 원본 RagService를 인스턴스화하여 warmup/쓰기를 유발하지 않는다.
5. 대상 catalog를 importing으로 표시한다. 모든 포인트를 with_vectors=True로 읽어 ID·dense·sparse를 대상 grag_ 컬렉션에 저장한다. 같은 원문 chunk_id와 point UUID는 유지한다.
6. PDF·파싱 결과·사용하는 이미지 캐시를 새 경로로 복사한다. source_path/page_image만 검증된 새 경로로 고치고 원문·쪽수·제목·금액·코드·사용자 meta는 유지한다. 원래 경로와 해시는 imports 기록에 남긴다.
7. 로컬 chunks와 Qdrant payload가 같은 경로 변환 규칙을 사용했는지 확인한다. sparse 값은 완전 일치, dense는 COSINE float32 재정규화 절대오차 1e-7 이내로 대조한다. 개수만 맞는 것으로 통과시키지 않는다.
8. manifest·content_revision·origin 정보를 저장한 뒤 catalog를 ready로 게시한다. 가져오다 멈춘 컬렉션은 목록·검색에서 노출하지 않는다.

같은 revision의 반복 가져오기는 no-op다. 원본 갱신을 자동 추종하지 않고 source_changed로 보고한다. refresh 요청은 명시적으로 처리한다. 기존 대상이 있으면 새 snapshot을 전부 준비한 뒤 별도 데이터 갱신 절차로 교체한다. 실패·프로세스 종료 시 importing/updating 상태를 유지하여 부분 자료를 조회하지 않고, 새 data/imports에 남긴 이전 대상 백업으로 복구하거나 다시 가져온다.

## 4. 첫 문서와 그래프 계약

첫 후보는 2025년+공중보건의사제도+운영지침이다. 현재 목록상 224청크이고 본문·서식 목차를 함께 활용할 수 있다. 첫 가져오기에서 실제 수량과 내용 해시를 다시 확인한다. 두 번째 호환성 확인에는 작은 IBMS 가이드를 사용한다. 수록 법령의 현행성 판단을 개발 테스트의 결과로 표현하지 않는다.

### 4.1 그래프 자료

| 자료 | 필수 내용 |
|---|---|
| DocumentKey | document_id, embedding_model |
| content_revision | 청크 순서·ID·본문·쪽수·제목 경로·관련 metadata의 정규 JSON SHA-256 |
| graph_revision | content_revision + schema_version + extractor_version + 수동 앵커 해시 + LlamaIndex 버전의 SHA-256 |
| Anchor | anchor_id, kind, scope_id, display_name, chunk_ids, PDF page 앵커 |
| Edge | edge_id, source_anchor_id, relation, target_anchor_id, evidence_ids |
| Evidence | evidence_id, document_id, content_revision, chunk_id/페이지 앵커, 원문 offset·짧은 인용, rule_id |
| Provenance | chunk→anchor, edge→evidence 역색인과 원래 chunk/point ID 대응 |
| BuildState | revision, building/ready/error, node/edge/unresolved 개수, 검증 결과 |
| CatalogState | absent/importing/ready/updating/error/deleting, data_epoch, content_revision |
| UnresolvedReference | 문서/revision/scope, 원문 chunk·offset, 참조 문자열, 후보 anchor ID, 미확정 사유 |

content_revision에서 바뀔 수 있는 로컬 경로·시각·벡터의 float 표현은 제외한다. 페이지/본문/제목·scope가 바뀌면 반드시 다른 revision이 된다. chunk_id가 c12 그대로여도 원문이 바뀌면 옛 관계를 쓰지 않는다.

anchor_id는 문서·모델·content_revision·종류·소속·원문 앵커에 대한 UUIDv5로 만든다. LlamaIndex EntityNode의 기본 id는 name이므로 name에는 이 scoped ID를 넣고 사람이 읽는 이름은 properties.display_name에 둔다. 제1호 서식이라는 표시명만 전역 ID로 사용하지 않는다.

LlamaIndex의 단일 source 속성만 모든 근거라고 간주하지 않는다. 같은 관계의 여러 근거를 먼저 묶어 provenance에 저장하고 각 edge.properties에 evidence_ids를 보존한다. 자동 include_text에 기대지 않고 기존 get_chunk와 provenance를 이용해 원문을 복원한다.

### 4.2 첫 관계 유형

- CONTAINS: 문서/장·절/제목의 실제 소속.
- REFERS_TO: 본문에 명시된 대상.
- HAS_TABLE: 번호·표제·명시 참조로 확인된 표.
- HAS_FORM: 소속 범위까지 식별한 서식 참조.
- NEXT_STEP: 같은 절차 안의 명시적 순번·선후 관계.

같은 쪽에 있다는 이유로 적용 관계를 만들지 않는다. PDF 읽기 순서를 업무 처리 순서로 보지 않는다. 참조 키는 revision+소속 부분+종류+번호로 해소한다. 후보가 여러 개이거나 소속 부분을 판단할 수 없으면 unresolved로 남긴다.

이미지 서식은 검증된 수동 앵커 JSON으로 표제·PDF 물리 쪽수·이미지 해시를 등록할 수 있다. 실제 페이지를 확인하지 않은 위치를 자동 확정하지 않는다. 그래프가 이미지 노드를 반환할 때는 확인된 표제/위치와 이미지 조회 정보를 제공하고 서식 내용을 생성하지 않는다. PDF가 없는 문서는 텍스트 청크 범위에서만 동작한다.

### 4.3 데이터 타입과 함수 계약

아래 이름을 각 작업의 공개 인터페이스로 고정한다. 구현할 파일에서 실제 타입을 정의한다.

```text
settings.py
  GraphSettings.load() -> GraphSettings
  GraphSettings.make_rag_config() -> rag_mcp.config.Config

snapshot.py
  ImportRequest(document_id: str, force: bool = False)
  ImportResult(status: str, document_id: str, content_revision: str,
               collection: str, num_chunks: int, reused_vectors: int)
  SnapshotImporter.preview(request: ImportRequest) -> dict
  SnapshotImporter.run(request: ImportRequest) -> ImportResult

identity.py
  content_revision(chunks: Sequence[Chunk], embedding_model: str) -> str
  anchor_id(document_id: str, revision: str, kind: str,
            scope_id: str, local_key: str) -> str

extraction.py
  ExplicitRelationExtractor.extract(chunks: Sequence[Chunk], revision: str,
                                    manual_anchors: Sequence[Anchor]) -> GraphBundle
  GraphBundle(anchors: list[Anchor], edges: list[Edge],
              evidence: list[Evidence], unresolved: list[UnresolvedReference])

llama_adapter.py
  to_text_nodes(chunks: Sequence[Chunk], revision: str) -> list[TextNode]
  build_property_graph(nodes: Sequence[TextNode], bundle: GraphBundle)
      -> SimplePropertyGraphStore

graph_store.py
  GraphRepository.publish(document_id: str, revision: str,
                          graph: SimplePropertyGraphStore, bundle: GraphBundle) -> str
  GraphRepository.open_active(document_id: str, content_revision: str) -> GraphSnapshot | None
  GraphSnapshot(graph_revision: str, store: SimplePropertyGraphStore,
                provenance: Provenance)

retrieval.py
  GraphRetriever.search(request: GraphSearchRequest) -> GraphSearchResponse
  GraphSearchRequest(query: str, document_id: str, embedding_model: str = 'kure',
                     top_k: int = 8, max_hops: int = 1, max_related: int = 8,
                     fiscal_year: int | None = None, filters: dict | None = None)
  GraphSearchResponse(baseline_results: list[dict], related_results: list[GraphEvidence],
                      graph_status: str, diagnostics: list[dict])
  GraphEvidence(source: dict, text: str | None, kind: str, edge_ids: list[str],
                evidence_ids: list[str], seed_chunk_ids: list[str], hop_count: int)
```

## 5. LlamaIndex 적용 범위

사용할 공개 구현은 PropertyGraphIndex, SimplePropertyGraphStore, TextNode/EntityNode/Relation, TransformComponent, CustomPGRetriever 또는 BasePGRetriever다. 새 프로젝트에서 사용하는 규칙 추출기를 TransformComponent로 감싸 KG_NODES_KEY/KG_RELATIONS_KEY를 채운다.

기존 청크를 TextNode로 직접 제공하며 기본 문장 분할을 다시 적용하지 않는다. embed_kg_nodes=False, use_async=False와 명시적인 비어 있지 않은 kg_extractors 목록을 사용한다. 기본 LLM synonym retriever도 자동 선택하지 않고 custom retriever를 전달한다. 이 구성에서는 그래프 색인 때문에 임베딩 모델이나 LLM이 자동 호출되지 않아야 한다.

핵심 연결의 최소 검증 코드는 다음과 같다. 실제 공개 함수는 작업 4에서 이 계약을 구현한다.

```python
from collections.abc import Sequence
from llama_index.core import PropertyGraphIndex
from llama_index.core.graph_stores.simple_labelled import SimplePropertyGraphStore
from llama_index.core.schema import BaseNode, TransformComponent


def build_store(nodes: Sequence[BaseNode], extractor: TransformComponent):
    store = SimplePropertyGraphStore()
    PropertyGraphIndex(
        nodes=list(nodes),
        property_graph_store=store,
        kg_extractors=[extractor],
        transformations=[],
        embed_kg_nodes=False,
        use_async=False,
    )
    return store
```

위 코드의 extractor는 검증된 GraphBundle을 LlamaIndex KG metadata로 변환하는 어댑터다. 각 개체·관계를 대표하는 원문 청크 한 곳에 배정하되 전체 원문 근거는 별도 provenance에 보존한다. 본문은 한국어 그대로 저장한다.

신규 그래프 개체의 의미 검색은 첫 버전에 넣지 않는다. 기존 검색이 찾은 출발 청크와 관계 ID를 이용해 탐색한다. 이후 개체 임베딩이 유용하다는 평가 결과가 있으면 기존 KURE 인터페이스를 연결한 별도 색인으로 추가한다.

## 6. 검색·원문 복원·상태 확인

검색 처리 순서는 다음과 같다.

1. GraphService가 대상 문서 catalog가 ready인지 검사하고 data_epoch/content_revision을 캡처한다.
2. 기존 RagService.search_documents를 요청당 한 번 호출하여 기존 dense/sparse/필터/reranker의 결과를 그대로 받는다. diagnostic 항목과 실제 결과를 구분한다. custom retriever에는 이 seed 결과를 넘겨 KURE 질의를 중복 실행하지 않는다.
3. 캡처한 revision과 일치하는 활성 GraphSnapshot을 연다. 없으면 baseline_results만 반환한다.
4. provenance의 chunk→anchor 역색인으로 출발 노드를 찾고 LlamaIndex graph store의 조회 기능을 사용해 허용된 관계를 탐색한다.
5. 출발·대상·관계의 증거를 모두 원문으로 검증한다. 연결 대상의 청크는 기존 get_chunk로, 이미지 앵커는 새 프로젝트의 get_page_image로 복원한다.
6. 정상·fallback을 포함한 모든 반환 경로에서 catalog의 epoch/revision을 다시 확인한다. 도중 변경이 시작됐으면 그래프 결과를 버리고 busy/retry 진단을 반환한다. 부분 교체된 기본 결과도 사용자에게 반환하지 않는다.

custom retriever는 요청마다 새 인스턴스를 만들고 request/seed/GraphSnapshot을 고정해서 전달한다. 다른 요청의 마지막 검색 결과를 공유 필드에 저장하지 않는다. 반환은 source/edge/evidence 식별자가 담긴 NodeWithScore를 사용하며 그래프 결과의 score는 None으로 둔다. 이를 GraphEvidence로 변환하고 baseline의 실제 점수와 합산하지 않는다. 두 문서·다른 필터의 동시 요청이 섞이지 않는 회귀 테스트를 포함한다.

기본 top_k=8, max_hops=1, max_related=8로 시작한다. 허용 범위는 top_k 1~100, max_hops 1~2, max_related 0~16이다. 관계 검사 100건·방문 노드 64개·그래프 추가 처리 500ms를 초기 상한으로 둔다. 각 이웃 조회도 작은 batch/limit로 나누어 시간 상한을 확인한다. 큼직한 동기 호출 하나에 모든 탐색을 맡겨 hard timeout을 보장한다고 표현하지 않는다.

구조 부모로 올라가 문서 전체로 내려오는 확장은 기본으로 금지한다. 현재 frontier ID와 이미 방문한 ID를 구분해 순환을 종료한다. 원문에 적힌 NEXT_STEP의 방향과 원래 관계 방향은 보존하고, 역방향으로 따라간 경우 path에 traversal_direction을 표시한다.

search_graph_documents의 기본 탐색 관계는 REFERS_TO/HAS_TABLE/HAS_FORM/NEXT_STEP이다. CONTAINS는 소속 판정·표시용으로 저장하고 검색에서 문서의 모든 자식을 확장하는 데 사용하지 않는다. 관계 확인 도구는 같은 상한 안에서 소속 관계도 표시할 수 있다.

연도와 metadata 필터는 연결 대상 및 증거에도 적용한다. 필터를 판정할 수 없는 이미지 노드는 해당 필터가 있을 때 제외한다. 그래프 결과에 임의 유사도 점수를 만들지 않고 별도의 related_results로 반환한다. 정렬은 홉 수→명시 관계 우선→seed 순위→안정 ID 순서다.

그래프 없음·빌드 중·실패·revision 불일치에서는 정상 baseline 결과를 제공한다. Qdrant 기본 검색 자체의 오류는 그래프 fallback으로 숨기지 않는다. 데이터가 updating/importing이면 정상 snapshot이 없으므로 명시적인 busy 상태를 제공한다.

## 7. 게시·재색인·삭제·복구

### 그래프 빌드

- 새 data_dir의 DataMutationLock을 획득한다. 원문이 같은 그래프 재빌드 동안 기본 검색과 기존 ready 그래프는 계속 사용할 수 있다.
- graph_revision별 임시 디렉터리에 graph.json/provenance.json/build.json을 저장한다.
- 관계 endpoint 존재, evidence 본문 offset, scope·revision, checksum과 개수를 검증한다.
- 파일 flush/fsync 후 같은 볼륨의 최종 빌드 디렉터리로 게시하고 활성 pointer JSON을 os.replace로 교체한다.
- SimplePropertyGraphStore.persist 자체에 원자성/프로세스 잠금이 있다고 가정하지 않는다. 활성 빌드 디렉터리는 불변으로 취급한다.
- 중단된 임시 빌드는 비활성으로 남고 기존 빌드는 유지된다. 정리 작업은 자기 data_dir의 inactive/staging 경로만 대상으로 한다.

### 데이터 변경

- 새 프로젝트의 ingest/reindex/delete/import-refresh는 모두 GraphService를 거쳐 같은 변경 잠금을 사용한다.
- 데이터 수정 전 catalog를 updating/deleting으로 원자 교체하고 data_epoch를 증가시킨다. 활성 그래프는 stale 처리한다.
- 기존 Indexer의 복구 경로를 재사용하되 새 프로젝트 자료/컬렉션만 대상으로 한다. 새 자료의 content_revision을 계산한다.
- 데이터 성공 후 ready로 게시한다. 그래프 자동 빌드가 실패해도 기본 검색은 새 revision으로 제공하고 그래프 상태는 error로 표시한다.
- 문서 삭제는 catalog를 삭제 진행 상태로 먼저 표시한다. DB/파일 정리 중 실패해도 문서가 목록·검색에 다시 나타나지 않게 한다.
- 프로세스 종료 후 catalog가 updating/importing인 문서는 doctor가 복구 필요로 보고한다. 이전 대상 snapshot과 복구 journal을 검증하고 재시도 또는 되돌림을 진행한다. 중간 결과를 done으로 승격하지 않는다.

자동으로 원본 문서나 원본 컬렉션의 변경을 추종하지 않는다. 새 프로젝트의 snapshot 갱신은 명시적 요청으로 처리한다.

## 8. 신규 파일의 책임

아래 모든 경로는 만들 예정인 D:/User/Desktop/graph-rag-mcp를 기준으로 한다. 실제 구현은 이 새 checkout에서만 한다.

| 파일 | 책임 |
|---|---|
| src/graph_rag_mcp/settings.py | GRAPH_RAG_* 설정·경로·namespace 검사와 기반 Config 생성 |
| src/graph_rag_mcp/source_reader.py | 원본 metadata/벡터 읽기 전용 접근 |
| src/graph_rag_mcp/snapshot.py | dry-run·벡터/파일 복사·대조·import journal |
| src/graph_rag_mcp/models.py | Anchor/Edge/Evidence/Provenance/응답/상태 타입 |
| src/graph_rag_mcp/identity.py | revision·scoped ID 생성 |
| src/graph_rag_mcp/extraction.py | 소속·정의·명시 참조·순서 판정과 unresolved |
| src/graph_rag_mcp/llama_adapter.py | 청크/GraphBundle을 LlamaIndex 자료로 변환 |
| src/graph_rag_mcp/graph_store.py | 불변 그래프 빌드 게시·활성 pointer·읽기 |
| src/graph_rag_mcp/retrieval.py | 기존 검색 seed를 이용한 제한 탐색·필터·원문 복원 |
| src/graph_rag_mcp/catalog.py | 문서 epoch·ready/updating 등 durable 상태 |
| src/graph_rag_mcp/lifecycle.py | 복사·갱신·삭제·그래프 작업의 순서와 복구 |
| src/graph_rag_mcp/service.py | 기반 RagService를 명시적으로 감싸는 새 서비스 |
| src/graph_rag_mcp/server.py | 별도 MCPServer와 도구 등록 |
| src/graph_rag_mcp/cli.py | CLI 명령·실행 초기화 |
| src/graph_rag_mcp/evaluation.py | 여러 필요 근거·관계·쪽수의 평가 |
| src/graph_rag_mcp/doctor.py | 설치·namespace·graph revision·복구 상태 진단 |
| tests/graph/ | 합성 fixture·회귀·실제 저장소/stdio 검증 |

기반 복사본의 수정 범위는 src/rag_mcp/config.py와 service.py의 prefix/문서 목록 경계, 필요한 공통 재사용 지점으로 제한한다. 그 외 수정이 필요하면 원인과 기존 회귀 영향부터 기록한다.

## 9. 구현 작업과 검증 순서

### 작업 0 — 독립 checkout·의존성·기준선

Files: pyproject.toml, uv.lock, .gitignore, README.md, docs/BASELINE.md, src/graph_rag_mcp/__init__.py, tests/graph/test_package.py.

- [ ] 원본 HEAD·remote·미커밋 경로·운영 문서 목록을 읽기 전용으로 기록한다.
- [ ] 독립 복제와 새 브랜치를 만들고 원본 remote로 push할 수 없게 한다.
- [ ] project/CLI 이름과 두 package 빌드를 설정한다. .gitignore에서 data/, work/, PDF, 실제 eval, local 설정을 제외한다.
- [ ] 새 package import와 배포 이름의 테스트를 작성하고 최소 __init__.py를 추가한다. 새 CLI 기능 구현은 작업 7에서 진행한다.
- [ ] Python 3.11 환경에 먼저 복제한 lockfile의 기존 의존성을 준비하고 원본 회귀를 실행한다. 이전 전체 실행의 Windows asyncio socketpair/accept 대기는 별도 진단 대상으로 기록하고 환경·실제 결함을 구분한다.
- [ ] llama-index-core==0.14.25를 추가해 새 uv.lock을 만들고 변경된 의존성 목록을 검토한다. 전체 upgrade를 수행하지 않는다. 원본 .venv와 uv.lock은 수정하지 않는다.
- [ ] 새 환경의 전체 테스트와 현재 stdio 11개 도구 기준선을 다시 확인한 뒤 커밋한다. 기반 코드 단계와 신규 의존성 추가 단계의 결과를 따로 남긴다.

검증 명령: `uv run pytest -q`, `uv lock --check`. 기대: 전체 PASS와 의도된 skip 사유 확인. 프로젝트 내부 --basetemp는 조상 탐색 테스트를 깨뜨리므로 사용하지 않는다.

### 작업 1 — 경로·Qdrant namespace·기본 검색 분리

Files: src/graph_rag_mcp/settings.py, src/rag_mcp/config.py, src/rag_mcp/service.py, tests/graph/test_settings.py, tests/graph/test_namespace.py.

- [ ] 같은 서버에 rag_와 grag_가 있을 때 새 앱은 grag_만 목록·검색·삭제하는 RED 테스트를 작성한다.
- [ ] 원본과 같거나 중첩된 data_dir, 빈 server URL, rag prefix 쓰기 요청, 경로 traversal을 거부하는 테스트를 작성한다.
- [ ] GraphSettings와 기반 Config의 prefix 필드/문서 목록 처리를 구현한다.
- [ ] 기존 RAG_DATA_DIR 환경이 있어도 새 앱의 명시 경로를 바꾸지 못하는지 확인한다.
- [ ] 기존 prefix 기본값과 기반 테스트를 유지하고 전체 테스트 통과 후 커밋한다.

Interface: GraphSettings.make_rag_config()를 뒤 작업의 모든 target 접근에 사용한다.

### 작업 2 — 한 문서 snapshot 가져오기와 벡터 재사용

Files: source_reader.py, snapshot.py, catalog.py, tests/graph/test_snapshot.py, tests/graph/test_source_readonly.py.

- [ ] 임시 Qdrant에 합성 원본 컬렉션을 만들고 dense/sparse/금액/표/쪽수/metadata를 가진 포인트를 준비한다.
- [ ] source에는 mutation 메서드가 없는 클라이언트를 주입하고 target 포인트·payload·vectors 대조 테스트를 먼저 작성한다.
- [ ] 임베딩 backend에 호출 시 실패하는 spy를 두고 가져오기 중 호출 0회를 검증한다.
- [ ] dry-run, 잠금 busy, 파일 경로 변환, 실제 scroll/upsert와 import journal을 구현한다.
- [ ] 중간 DB/파일 쓰기 실패·프로세스 중단·동일 revision 재시도·source 변경을 재현한다.
- [ ] catalog ready 이전의 문서가 노출되지 않는지 확인하고 전체 테스트 통과 후 커밋한다.

기대: point ID 유지, sparse 완전 일치, dense 오차 한도 통과, 바뀐 경로 외 payload 일치, source mutation 0회.

### 작업 3 — ID·근거 계약과 청크 변환

Files: models.py, identity.py, llama_adapter.py, tests/graph/test_identity.py, tests/graph/test_nodes.py.

- [ ] 동일 입력의 ID 안정성과 본문·페이지·제목 변경 시 revision 변경을 RED로 확인한다.
- [ ] 동일 번호의 다른 scope, 다른 문서, 다른 revision의 anchor가 서로 달라야 한다.
- [ ] Chunk→TextNode 변환에서 본문·표 하나의 원자성·원래 ID·metadata가 유지되는지 테스트한다.
- [ ] 원문 offset 밖의 Evidence, 없는 target, 다른 revision 연결은 validation error로 처리한다.
- [ ] 소스/대상 point UUID와 chunk_id의 대응을 구현하고 전체 테스트 통과 후 커밋한다.

합성 데이터는 `연습 문서`의 절차·표·서식으로 만들며 실제 법령·업무 판단을 fixture에 넣지 않는다.

### 작업 4 — 명시 관계 추출과 실제 LlamaIndex 그래프 구성

Files: extraction.py, llama_adapter.py, graph_store.py, tests/graph/test_extraction.py, tests/graph/test_llama_index.py, tests/graph/test_graph_publish.py.

- [ ] 아래 합성 사례의 정답과 unresolved를 고정하고 테스트를 먼저 작성한다.
- [ ] 규칙 추출기를 TransformComponent로 연결하고 GraphBundle을 KG node/relation 자료로 변환한다.
- [ ] embed_kg_nodes=False와 비어 있지 않은 custom extractor로 실제 PropertyGraphIndex를 구성한다.
- [ ] 임베딩/LLM resolver를 호출하면 실패하도록 주입하여 자동 호출이 없음을 검증한다.
- [ ] 파일 그래프를 저장·재로딩하고 관계·근거·원문 대응이 유지되는지 실제 SimplePropertyGraphStore로 확인한다.
- [ ] publish 단계마다 오류를 주입하고 이전 active pointer가 유지되는지 확인한다. 전체 테스트 후 커밋한다.

| 합성 입력 | 기대 결과 |
|---|---|
| A절의 절차가 A절 별지 제1호 서식을 명시 | A절 서식으로 HAS_FORM |
| 같은 문서 B절에도 제1호 서식 존재 | B절로 잘못 연결하지 않음 |
| 소속을 특정하지 못한 제1호 참조 | unresolved |
| 본문 옆에 표가 있으나 참조/소속 근거 없음 | HAS_TABLE 생성 안 함 |
| 같은 절차에 명시된 1→2→3 | NEXT_STEP 두 건 |
| 서로 다른 절차의 연속 페이지 | 단계 관계 생성 안 함 |
| 한 관계를 두 문단에서 언급 | evidence 두 건 보존 |
| 확인된 이미지 서식 앵커 | 위치·이미지로 연결, 내용 생성 안 함 |

### 작업 5 — 기존 검색과 그래프 탐색을 한 경로로 연결

Files: retrieval.py, service.py, tests/graph/test_retrieval.py, tests/graph/test_graph_filters.py.

- [ ] 기존 Retriever 결과와 GraphSearchResponse.baseline_results가 정확히 같은 테스트를 먼저 만든다.
- [ ] CustomPGRetriever에 이미 얻은 seed·anchor 매핑·LlamaIndex 이웃 조회를 연결한다. 기준 검색을 다시 호출하지 않는지 spy로 검증한다.
- [ ] 1/2홉·순환·역방향 path 표기·방문/관계/시간/결과 상한을 구현한다.
- [ ] graph 없음/오류/stale/timeout의 fallback, 기본 검색 오류 전파를 검증한다.
- [ ] metadata/연도/문서/버전 필터가 target과 evidence에도 적용되는지 확인한다.
- [ ] GraphEvidence에 실제 원문·쪽수·경로가 포함되는지 확인하고 전체 테스트 후 커밋한다.

완료 기준: 합성 fixture뿐 아니라 가져온 첫 문서에서 실제 기본 검색→관계→표/서식 근거 반환이 동작한다. 관계 저장만 된 상태를 이 작업 완료로 보지 않는다.

### 작업 6 — 새 문서 ingest·재색인·삭제·재시작의 일관성

Files: lifecycle.py, catalog.py, service.py, tests/graph/test_lifecycle.py, tests/graph/test_concurrency.py.

- [ ] 같은 c순번에 다른 본문, 검색 중 교체, 빌드 중 강제 종료, 실패한 refresh, 삭제 중 예외를 먼저 테스트한다.
- [ ] 새 프로젝트의 변경 경로를 전부 GraphService로 통과시키고 data_epoch의 시작/끝 검사를 연결한다.
- [ ] 기존 Indexer·DataMutationLock·JobStore를 재사용하여 변경 작업 하나만 허용한다.
- [ ] DB 성공/그래프 실패이면 새 기본 검색과 graph error를 제공하고, DB 부분 변경이면 ready로 표시하지 않는다.
- [ ] 재시작 후 importing/updating/불완전 빌드를 진단하고 자기 백업으로 복구하는 경로를 검증한다.
- [ ] 두 프로세스 중 한 작업만 변경하고 후발 작업은 busy인지 확인한다. 전체 테스트 후 커밋한다.

원본 mutation.lock과 target 잠금의 획득 순서 및 finally 해제를 테스트한다. 원본의 변경 작업을 자동 중지하거나 프로세스를 종료하지 않는다.

### 작업 7 — 새 MCP·CLI·doctor

Files: server.py, cli.py, doctor.py, service.py, tests/graph/test_mcp_tools.py, tests/graph/test_stdio.py, tests/graph/test_doctor.py.

- [ ] 별도 MCP 이름, 도구 목록, 인자 검증·오류·기존 응답 호환 테스트를 먼저 작성한다.
- [ ] 기반 11개 도구는 새 target 설정과 수명주기 경계를 통해 위임한다.
- [ ] 신규 5개 도구를 등록한다: import_existing_document, build_document_graph, graph_status, get_document_relations, search_graph_documents.
- [ ] import/build는 job_id를 반환하고 같은 JobStore·ingest_status로 진행을 조회한다. 새 operation/stage를 응답에 명시한다.
- [ ] stdout을 JSON-RPC 전용으로 유지하고 로그는 새 프로젝트 로그 파일 또는 stderr로 보낸다.
- [ ] doctor는 namespace·source/target 경로·원문/그래프 revision·불완전 작업을 확인하되 모델 다운로드 없이 동작한다.
- [ ] 실제 stdio initialize→tools/list→읽기/그래프 도구 호출과 서버 종료를 확인한다. 전체 테스트 후 커밋한다.

신규 도구 계약:

```text
import_existing_document(document_id: str, force: bool = False) -> job_id/state
build_document_graph(document_id: str, force: bool = False) -> job_id/state
graph_status(document_id: str) -> revision/status/counts/unresolved/errors
get_document_relations(document_id: str, chunk_id: str,
                       max_hops: int = 1, limit: int = 20) -> paths/evidence
search_graph_documents(query: str, document_id: str, top_k: int = 8,
                       max_hops: int = 1, max_related: int = 8,
                       fiscal_year: int | None = None, filters: dict | None = None,
                       embedding_model: str | None = None)
                       -> GraphSearchResponse
```

CLI 명령은 serve, doctor, import-existing, build-graph, graph-status, search-graph, evaluate-graph와 필요한 기존 데이터 관리 명령으로 구성한다. source 경로·URL은 사용자 입력 도구 인자로 임의 노출하지 않고 설정에서 읽는다.

### 작업 8 — 실제 문서 비교 평가

Files: evaluation.py, eval/graph.example.jsonl, tests/graph/test_evaluation.py, docs/graph-evaluation.md. 실제 질문/정답은 eval/graph-private.jsonl에 보관하고 Git에서 제외한다.

- [ ] 단일 사실·표·금액 20, 본문↔표/서식 20, 절차 10, 모호/없는 연결 10의 60문항을 작성한다.
- [ ] 유형별 절반을 개발용 30, 나머지를 고정 평가용 30으로 분리하고 사람이 정답 원문·쪽수·관계를 대조한다.
- [ ] 여러 근거 중 하나만 찾으면 전체 정답이 되는 평가 오류를 방지하는 테스트를 먼저 만든다.
- [ ] A 기존 검색, B 주변 문단 추가, C 그래프 탐색을 같은 모델·문서·reranker·질문으로 실행한다.
- [ ] 최종 8개/16개 근거 예산을 동일하게 적용한다. B/C는 절반을 상위 seed로 고정하고 나머지를 추가 근거로 채운 뒤 빈자리는 A 결과로 보충한다.
- [ ] 세 방식 모두 16,000자 한도를 적용하되 표는 중간 절단하지 않는다. 단일 표가 한도를 넘으면 overflow로 별도 보고한다. 이미지 반환 수·총 크기도 기록한다.
- [ ] 설정은 개발용에서 고정하고 고정 평가를 실행한다. cold/warm, 전체/그래프 추가 지연, 정상/미연결 사례를 나눠 보고한다.
- [ ] 작은 두 번째 문서로 hardcoding 여부와 기존 원문/표 검색 회귀를 확인한다. 전체 테스트 후 커밋한다.

제안하는 초기 합격 목표는 성능 보장이 아니다.

| 항목 | 초기 합격 목표 |
|---|---|
| 운영 원본 변경 | 0건 |
| 가져오기 중 재임베딩 | 0회 |
| 원문·문서·쪽수·revision 혼입 | 회귀/고정 평가에서 0건 |
| 관계형 전체 필요 근거 충족률 | A/B 중 좋은 쪽 대비 +10%p 이상 |
| 검토한 관계 정확도 | 95% 이상, 분자/분모·오류 유형 함께 보고 |
| 단일 사실/표/금액 회귀 | 고정 문항에서 A 대비 추가 실패 0건 |
| 그래프 추가 warm p95 | 500ms 이하를 목표로 실측 |
| 그래프 실패 | 정상 기본 검색 유지, 오래된 관계 반환 0건 |

### 작업 9 — 새 저장소 운영 문서와 배포 준비

Files: README.md, docs/operations.md, docs/import-existing.md, docs/restore.md, docs/BASELINE.md, .gitignore, 새 프로젝트 PROGRESS.md.

- [ ] 설치/설정/가져오기/검색/갱신/복구를 한 문서의 재현 절차로 작성한다.
- [ ] 새 MCP를 추가 등록하고 기존 rag-mcp 설정 항목은 유지한다. 전역 기본값을 바꾸지 않는다.
- [ ] 원본 Git 상태, source 컬렉션 포인트 수·payload/벡터 해시, 정상 검색 결과를 작업 전 기록과 대조한다.
- [ ] private 새 저장소를 생성하거나 이미 있는 목적 저장소를 확인하고 새 origin을 연결한다.
- [ ] 실제 문서·백업·원본 경로·로컬 설정·키가 새 커밋에 포함되지 않았는지 검토한다.
- [ ] 전체 회귀·실제 stdio·가져오기·그래프·복구 검증 결과와 알려진 한계를 정리하고 새 저장소만 push한다.

완료 기준: 기존 RAG와 새 Graph RAG가 동시에 독립 실행되고, 원본 운영 자료를 유지하며 새 프로젝트의 문서 등록·검색·관계 갱신·복구를 재현할 수 있다.

## 10. 공통 실행·검증 명령

다음 명령은 구현을 요청받은 뒤 새 checkout에서 실행한다. 각 모듈의 테스트 파일이 만들어진 작업 시점에 해당 명령을 사용한다.

```powershell
Set-Location -LiteralPath 'D:\User\Desktop\graph-rag-mcp'
$env:UV_CACHE_DIR = Join-Path (Get-Location) 'work\uv-cache'
uv python pin 3.11
uv sync --extra dev
uv lock --check
uv run pytest -q
uv run pytest tests/graph/test_settings.py tests/graph/test_namespace.py -q
uv run pytest tests/graph/test_snapshot.py tests/graph/test_source_readonly.py -q
uv run pytest tests/graph/test_identity.py tests/graph/test_nodes.py -q
uv run pytest tests/graph/test_extraction.py tests/graph/test_llama_index.py tests/graph/test_graph_publish.py -q
uv run pytest tests/graph/test_retrieval.py tests/graph/test_graph_filters.py -q
uv run pytest tests/graph/test_lifecycle.py tests/graph/test_concurrency.py -q
uv run pytest tests/graph/test_mcp_tools.py tests/graph/test_stdio.py tests/graph/test_doctor.py -q
uv run pytest tests/graph/test_evaluation.py -q
uv run graph-rag-mcp doctor
```

준비된 환경의 반복 테스트는 `uv run --offline --no-sync pytest`와 해당 테스트 경로로 실행할 수 있다. 기본 임시 폴더를 쓸 수 없으면 프로젝트 밖의 허용된 임시 폴더를 --basetemp로 지정한다. base/project root 탐색 테스트 때문에 프로젝트 내부 임시 폴더는 사용하지 않는다.

## 11. 핵심 계약의 구체적인 테스트 예시

아래 테스트는 새 프로젝트에서 먼저 추가할 내용이다. 현재 계획 작성 과정에서 실행한 결과가 아니다. 작업 3의 identity 구현과 작업 4의 실제 LlamaIndex 연결에 대한 최소 회귀이며, 각 작업의 나머지 사례도 같은 원칙으로 추가한다.

### tests/graph/test_identity.py

```python
from rag_mcp.models import Chunk
from graph_rag_mcp.identity import content_revision, anchor_id


def test_revision_tracks_provenance_but_not_file_relocation():
    chunk = Chunk(
        chunk_id="sample::c0", document_id="sample", text="연습 문서 본문",
        page=3, heading_path=["A절"], source_path="D:/old/sample.pdf",
    )
    original = content_revision([chunk], "kure")
    moved = chunk.model_copy(update={"source_path": "D:/new/sample.pdf"})
    other_page = chunk.model_copy(update={"page": 4})
    assert content_revision([moved], "kure") == original
    assert content_revision([other_page], "kure") != original


def test_same_form_number_is_scoped():
    a = anchor_id("sample", "revision-1", "FORM", "A절", "별지1")
    b = anchor_id("sample", "revision-1", "FORM", "B절", "별지1")
    c = anchor_id("sample", "revision-2", "FORM", "A절", "별지1")
    assert len({a, b, c}) == 3
    assert a == anchor_id("sample", "revision-1", "FORM", "A절", "별지1")
```

### tests/graph/test_llama_index.py

```python
from llama_index.core import PropertyGraphIndex
from llama_index.core.graph_stores.simple_labelled import SimplePropertyGraphStore
from llama_index.core.graph_stores.types import KG_NODES_KEY, KG_RELATIONS_KEY
from llama_index.core.schema import TextNode, TransformComponent


class EmptyRelations(TransformComponent):
    def __call__(self, nodes, **kwargs):
        for node in nodes:
            node.metadata[KG_NODES_KEY] = []
            node.metadata[KG_RELATIONS_KEY] = []
        return nodes


def test_disabled_graph_embeddings_preserve_source_node(monkeypatch):
    def unexpected_embedding(*args, **kwargs):
        raise AssertionError("그래프 색인이 임베딩을 요청했습니다")

    monkeypatch.setattr(
        "llama_index.core.indices.property_graph.base.resolve_embed_model",
        unexpected_embedding,
    )
    node = TextNode(
        id_="sample::c0", text="분할하지 않은 연습 표",
        metadata={"document_id": "sample", "page": 3, "is_table": True},
    )
    store = SimplePropertyGraphStore()
    PropertyGraphIndex(
        nodes=[node], property_graph_store=store,
        kg_extractors=[EmptyRelations()], transformations=[],
        embed_kg_nodes=False, use_async=False,
    )
    saved = store.get(ids=["sample::c0"])
    assert len(saved) == 1
    assert saved[0].text == "분할하지 않은 연습 표"
    assert saved[0].properties["page"] == 3
    assert saved[0].properties["is_table"] is True
```

가져오기·조회·수명주기 테스트는 실제 임시 Qdrant와 파일을 사용한다. 외부 PDF 변환·모델 호출만 결정적 fake로 대체하고, 복사·필터·파일 게시·삭제 경로까지 mock으로 우회하지 않는다. 매 RED 단계는 미구현 동작의 실패인지 확인하며 단순 import/설치 오류를 기능 회귀 검증이라고 주장하지 않는다.

## 12. 구현 순서·예상 작업량

| 묶음 | 작업 | 예상 집중 작업량 |
|---|---|---|
| 기반과 분리 | 0~1 | 1~2일 |
| 기존 벡터 가져오기 | 2 | 1~2일 |
| 관계 저장+검색 시제품 | 3~5 | 3~4일 |
| 갱신·복구·MCP | 6~7 | 2~3일 |
| 실제 평가·운영 문서 | 8~9 | 2~3일 |

합계 9~14일은 계획상 추정이며 자동 실행 시간이나 납기를 보장하는 값이 아니다. 첫 판단 지점은 작업 5다. 이때 한 문서에서 관계 검색과 출처 복원이 실제로 동작하는지 확인하고 남은 작업량을 다시 추정한다. Python 환경/asyncio 기준선 문제의 해결 범위가 커지면 먼저 그 영향을 보고한다.

## 13. 자체 검토와 재개 안내

- 기존 프로젝트 보존 → 독립 clone, 원본 remote push 차단, 현재 계획은 원본 외부에 저장.
- Qdrant 재사용 → 동일 서버 + 새 prefix + 원래 벡터/청크 사본, 원본 쓰기 경계 0회 검사.
- 코드 재사용 → rag_mcp 유지, graph_rag_mcp에 연결 기능 추가, 전체 이름 변경 제외.
- LlamaIndex 사용 → 실제 PropertyGraphIndex·SimplePropertyGraphStore·custom retriever를 시험, 자동 embedding/LLM 호출 검사.
- 관계 저장과 검색 연결 → 작업 4/5를 연속 시제품 단위로 확인.
- 출처·version·scope → 모델 계약과 negative tests, 관계별 복수 evidence, epoch fence.
- 장애/동시성 → source/target 잠금 순서, 불변 빌드 게시, catalog 복구, 실패 단계 테스트.
- 개선 증명 → A/B/C 같은 예산 비교, 개발/고정 평가 분리.

다음 실행 요청에서는 이 문서를 새 프로젝트 docs/superpowers/plans/에 복사하고 작업 0부터 시작한다. 실행 상태는 새 프로젝트 PROGRESS.md에 기록한다. 이 계획 작성 중에는 원본 PROGRESS.md나 기존 계획 문서를 수정하지 않았다.

## 14. 이번 확인 범위와 공식 근거

현재 코드·lockfile·Git 상태를 읽고, llama-index-core 0.14.25의 PyPI 공개 및 v0.14.25 소스의 PropertyGraphIndex/CustomPGRetriever/SimplePropertyGraphStore를 확인했다. 계획 문서만 작성했으며 패키지 설치·새 저장소 생성·새 프로젝트 코드 작성·Qdrant 데이터 변경은 하지 않았다.

- [고정할 Core 버전](https://pypi.org/project/llama-index-core/0.14.25/)
- [공식 Property Graph 안내](https://developers.llamaindex.ai/python/framework/module_guides/indexing/lpg_index_guide/)
- [고정 버전 PropertyGraphIndex](https://github.com/run-llama/llama_index/blob/v0.14.25/llama-index-core/llama_index/core/indices/property_graph/base.py)
- [고정 버전 custom retriever](https://github.com/run-llama/llama_index/blob/v0.14.25/llama-index-core/llama_index/core/indices/property_graph/sub_retrievers/custom.py)
- [고정 버전 파일 그래프 저장소](https://github.com/run-llama/llama_index/blob/v0.14.25/llama-index-core/llama_index/core/graph_stores/simple_labelled.py)

계획 작성 당시 원본 전체 테스트는 샌드박스의 Windows asyncio socketpair/accept 대기로 완료되지 않았다. 이후 코드 동기화 검증에서는 환경을 분리해 기존 Python 3.12.10에서 313 passed, 9 skipped를 확인했다. 이 결과를 새 Python 3.11·LlamaIndex 구성의 테스트 통과로 표현하지 않으며 작업 0의 새 환경 기준선 확보는 유지한다.
