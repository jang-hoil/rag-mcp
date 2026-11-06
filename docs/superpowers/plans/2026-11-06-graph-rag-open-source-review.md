# Graph RAG 공개 코드 비교와 수정 계획

작성일: 2026-11-06. 상태: 문서·소스 검토 완료, 설치·실행 검증 전.

기존 rag-mcp의 동기화 전 회귀 검증은 이후 313 passed, 9 skipped로 완료했다. 여기서 설치·실행 검증 전이라는 표시는 새 Graph RAG 후보 엔진에 관한 것이다.

> **기존 데이터·코드 재사용을 우선한 최종 추천:** 후속 요청에 맞춰 [LightRAG·LlamaIndex 재사용 기준 비교](2026-11-06-lightrag-vs-llamaindex-reuse.md)를 작성했다. 현재 권고는 **기존 Retriever/Qdrant를 유지하고 LlamaIndex 그래프 부품을 부분 도입**하는 것이다. 아래 내용은 후보 조사와 LightRAG 대안의 기록이며 한국어 실측 순위는 아니다.

## 판단

기존 RAG MCP를 바탕으로 새 Graph RAG를 만들 때 공개된 구현을 먼저 비교한다. 처음 검토에서는 LightRAG를 우선 시험 후보로 추천했으며, 사용자의 추가 비교 요청에 따라 **HKUDS/LightRAG와 LlamaIndex Property Graph를 같은 문서에서 비교하는 방향**으로 범위를 넓혔다. 기존 PDF 처리·표 보존·원문 페이지·MCP를 재사용하고 공개된 그래프 구축·검색 엔진을 연결한다. 한국어 실측 성능 순위는 아직 없다.

이 추천은 현재 코드와 upstream README·의존성·저장소 구현을 비교한 설계 판단이다. 사용자 문서에서 검색 품질이 입증되었다거나 현재 환경에서 바로 실행된다는 뜻은 아니다. 그래프 엔진 전체를 처음부터 직접 작성하는 기존 초안은 대안으로 보류한다.

## 후보 비교

| 후보 | 실제 확인한 특징 | 현재 프로젝트에 대한 판단 |
|---|---|---|
| **HKUDS/LightRAG** | 관계 추출·그래프/벡터 검색, QdrantVectorDBStorage, 사용자 정의 임베딩, 언어 설정, 사용자 정의 청크/그래프 입력 | 가장 먼저 한 문서 시험. 기존 파싱 결과와 출처를 연결하는 어댑터는 필요 |
| **LlamaIndex Property Graph** | PropertyGraphIndex에 그래프/벡터 저장소를 각각 주입 가능. SchemaLLMPathExtractor로 개체·관계 유형과 strict 검증 설정. QdrantVectorStore 구현 제공 | LightRAG와 함께 비교할 핵심 후보. 조항·표·서식 유형을 미리 정의하는 요구에 맞을 가능성. 형식 검증이 관계의 사실성까지 보장하는 것은 아님 |
| datastax/graph-rag | 기존 벡터 저장소의 metadata 관계 탐색, 프레임워크 독립 graph-retriever와 LangChain 통합 | 목표 구조는 잘 맞지만 공식 지원 목록과 현재 adapters 디렉터리에 Qdrant 전용 구현이 없음. 별도 Qdrant 어댑터 작업량을 평가하는 대안 |
| microsoft/graphrag | 개체·관계 추출 및 Local/Global Search, Qdrant 공식 문서에 custom vector store 연동 예시 | 전체 주제 요약의 비교 후보. 현재 upstream README가 maintenance mode, 신규 기능/PR 수용 중단과 버그·의존성 유지보수 방침을 명시하므로 이를 고려 |

GitHub API 조회 기준 LightRAG 최신 release는 v1.5.7(2026-09-02), 기본 브랜치 최신 커밋은 aaebfe1bb8d55deb6a4304d357a3bf4c92fcd40a(2026-10-14)다. DataStax 기본 브랜치 최신 커밋은 bab0f1d6d5525b7497d7f0dc3c6b581911a1662c(2025-05-05)다. 날짜는 조회 시점의 스냅샷이며, 커밋 간격만으로 프로젝트의 품질이나 지원 종료를 단정하지 않는다.

### 추가 후보를 낮은 품질로 판단한 것은 아님

| 후보 | 검토할 가치 | 이번 프로젝트의 추가 고려사항 |
|---|---|---|
| Cognee | 문서·대화·프로젝트 맥락을 지속적인 그래프 기억으로 구성. Qdrant 커뮤니티 어댑터 제공 | 현재 단일 문서 근거 검색보다 범위가 넓다. 추가 그래프/관계형 저장과 어댑터 버전의 조합을 확인해야 함 |
| Fast GraphRAG | personalized PageRank를 이용하는 그래프 탐색 | 현재 pyproject는 igraph·hnswlib에 의존. 현재 Qdrant·쪽수 출처 구조와의 연결 작업을 별도로 평가해야 함. README의 영문 자료 비용 예시를 한국어 성능/비용으로 일반화하지 않음 |
| FalkorDB GraphRAG SDK | 스키마 지정과 원문 청크를 추적하는 MENTIONED_IN 관계 제공 | 별도 FalkorDB 서버가 필요하므로 설치·운영 변경 범위가 커짐. SDK의 자체 벤치마크를 이번 한국어 자료의 성능으로 간주하지 않음 |
| RAG-Anything | LightRAG 위에 이미지·표·수식 처리와 페이지 정보를 연결하는 확장 | 독립된 대체 그래프 엔진이 아니라 LightRAG 조합 후보. 현재 JSON 표 구조와 페이지 앵커를 보존하는지 확인. VLM·파싱 의존성과 비용을 추가 평가 |

LlamaIndex의 기본 SimplePropertyGraphStore는 별도 그래프 DB 서버 없이 소규모 시험할 수 있는 선택지다. Qdrant 연결이 존재하더라도 원래 rag-mcp 컬렉션을 그대로 읽거나 모든 그래프를 Qdrant에 저장한다는 의미는 아니다. 기존 문서·노드 metadata와 새로운 그래프/벡터 표현 사이의 매핑은 두 후보 모두 검증한다.

수정된 선택 기준은 엔진의 인기도가 아니라 같은 한국어 문서에서의 명시 참조 정확도, 전체 근거 회수, PDF 쪽수, 표/금액 보존, 재색인 무결성, 지연·비용이다. 아래 구체적인 저장소/버전 설명과 실행 절차는 LightRAG 시험안이며 LlamaIndex에도 그대로 적용되는 설정으로 해석하지 않는다.

## Qdrant와 SQLite에 관한 정정

LightRAG를 채택해도 **SQLite를 필수로 설치할 필요는 없다.** 그러나 LightRAG 표준 구조에서 Qdrant가 모든 그래프 자료까지 저장하는 것은 아니다. 다음 역할을 구분한다.

- 벡터: QdrantVectorDBStorage.
- 그래프: 첫 소규모 시험은 NetworkXStorage. Python 라이브러리가 메모리에 그래프를 두고 GraphML 파일로 저장한다.
- 문서·캐시·처리 상태: 시험에서는 JsonKVStorage와 JsonDocStatusStorage 등 파일 저장.

이는 별도 그래프 DB 서버 없이 시험할 수 있다는 뜻이다. 그래프와 상태도 모두 Qdrant 안에 둔다는 뜻은 아니다. LightRAG 공식 문서는 기본 파일 저장소를 소규모 시험·평가용으로 설명하며, NetworkX에는 단일 작성자 제약이 있다. 실제 상시 운영의 저장소와 동시 실행 구조는 시험 이후 결정한다.

모든 자료를 Qdrant에만 저장해야 한다는 요구가 확정된다면 LightRAG 표준 구성을 그대로 쓰는 안은 맞지 않는다. 그 경우 그래프 저장 인터페이스를 추가 구현하는 비용과 DataStax의 Qdrant 어댑터 비용을 비교한다.

## 기존 코드와 연결 가능한 부분

현재 프로젝트는 Python >=3.11,<3.13, Pydantic 2, MCP SDK 2, Kiwi와 sentence-transformers를 이용한다. 잠긴 qdrant-client는 1.18.0이다.

LightRAG v1.5.7의 pyproject는 Python >=3.10을 요구하며, Qdrant 의존 범위는 >=1.11.0,<2.0.0이다. Python/Qdrant의 명시 버전 범위가 겹치는 것은 확인했다. 나머지 전체 의존성 해결과 Windows 실행은 별도 가상환경에서 확인해야 한다.

| 재사용할 것 | 연결 시 검증할 내용 |
|---|---|
| pdf_parser.py, chunking.py의 PDF 본문·표 | 이미 잘린 표를 다시 나누지 않고 사용자 정의 청크 입력으로 넘길 수 있는지 |
| models.py의 문서 ID·청크 ID·쪽수·제목 경로 | LightRAG source/chunk ID와 원래 출처를 명시적으로 매핑하고 중복 본문도 잘못된 쪽으로 합쳐지지 않는지 |
| embeddings.py의 KURE backend | 사용자 정의 embedding_func로 연결할 수 있는지, 문서/질의 임베딩 정책과 차원·토큰 상한이 일치하는지 |
| Qdrant 서버 | 새 컬렉션/namespace/workspace만 사용하고 원본 rag_ 컬렉션을 수정하지 않는지 |
| retrieval.py의 Kiwi sparse+dense 및 RRF/DBSF | 기존 검색을 기준선·fallback으로 제공. LightRAG의 hybrid를 동일한 의미의 BM25+dense라고 간주하지 않음 |
| MCP 도구와 페이지 이미지 반환 | LightRAG의 검색 context를 기존 출처 형식으로 변환하여 Codex가 근거를 확인할 수 있는지 |

Qdrant 지원은 현재 rag-mcp의 컬렉션 포맷을 LightRAG가 즉시 그대로 읽는다는 뜻이 아니다. LightRAG는 청크 외에 개체·관계 벡터와 별도 식별 체계를 사용하므로 새 색인과 추가 임베딩 작업량을 측정한다. 기존 벡터의 무조건 재사용이나 재임베딩 비용 0을 약속하지 않는다.

## 먼저 사용할 공개 코드

- `HKUDS/LightRAG`의 Python 패키지 `lightrag-hku`: 그래프 구축·조회 코어.
- `lightrag/kg/qdrant_impl.py`: 기존 Qdrant 서버와 연결할 벡터 저장 구현.
- `lightrag/kg/networkx_impl.py`: 소규모 시험의 파일 기반 그래프 구현.
- `docs/ProgramingWithCore.md`의 사용자 정의 임베딩·청크/그래프·context-only 조회 인터페이스.

우선 v1.5.7을 고정해 시험한다. main에만 있는 기능을 사용해야 한다면 해당 커밋과 이유를 기록하고 기준 버전을 다시 정한다. 전체 offline-storage extra를 무조건 설치하지 않고 필요한 저장소와 선택한 언어모델 제공자의 의존성만 포함하도록 설치안을 검토한다.

우리 쪽에 필요한 코드는 엔진 전체의 복제가 아니라 다음 연결·검증 코드다.

1. 기존 청크와 페이지/제목 경로를 LightRAG 입력 및 출처로 매핑.
2. KURE와 사용자 정의 임베딩 인터페이스 연결.
3. 문서별 검색 범위, 재색인 버전, 원문 인용의 검증.
4. LightRAG context를 기존 MCP 응답으로 반환하고 기본 검색 fallback 제공.
5. 명시된 조항·표·서식 참조를 보완할 검증 규칙. 자동 추출 관계를 그대로 확정하지 않음.

## 수정된 실행 순서

1. **외부 엔진 시험 준비:** 독립 프로젝트/가상환경, LightRAG 고정 버전, 필요한 의존성만 해결. 기존 코드의 전체 테스트 기준선과 Windows asyncio 대기 문제를 먼저 구분한다.
2. **한 문서 연결:** 기존 파싱 결과를 시험 데이터로 복사하여 custom chunk/graph 입력과 KURE adapter를 검증한다. 표·본문의 재분할과 출처 손실을 먼저 확인한다.
3. **그래프 구성:** Qdrant는 벡터, NetworkX는 시험용 그래프, 파일 KV는 상태/캐시로 분리한다. 한 작성자만 색인하고 운영 원본은 읽기 전용으로 보존한다.
4. **질문 비교:** 기존 검색 A, 주변 문단 B, LightRAG C를 같은 문서·최종 근거 예산에서 비교한다. 반환 source와 원본 쪽수가 정확한지도 채점한다.
5. **채택 판단:** 한국어 관계 추출, 모든 필요 근거 회수, 표/금액 보존, 지연·LLM 비용, 재색인·삭제·복구를 검증한 뒤 운영 저장소·MCP 연동 방식을 결정한다.

첫 시험은 Core API를 이용하는 소규모 검증으로 제한한다. 공식 문서는 일반 서비스 통합에 REST API를 권장하므로, 상시 운영은 별도 LightRAG 서비스와 MCP의 연결도 비교한다. REST 경로가 기존 청크·출처 입력 계약을 충족하는지도 확인한 후 정한다.

관계 자동 추출을 실행하려면 언어모델이 필요하다. 외부 API를 쓰면 문서 전송과 호출 비용을, 로컬 모델이면 PC 자원·한국어 품질을 확인한다. 현재 사용 중인 KURE 임베딩만으로 관계 추출용 언어모델까지 충족된다고 보지 않는다. API 키·모델이 아직 선택되지 않았으므로 비용 수치나 추출 품질을 확정하지 않는다.

## 채택 조건

기존 계획의 60문항/개발·평가 분리, 모든 필요 근거 충족률, 단일 사실·표·금액 회귀 방지, 문서/버전/필터 혼입 0건 목표를 유지한다. 이미지 서식, 동명·동번호 항목과 원문 없는 관계는 별도 실패 유형으로 평가한다.

LightRAG의 citation 기능이 PDF 쪽수까지 보장한다고 가정하지 않는다. 출처 매핑을 유지하지 못하거나 명시 참조 검색에서 주변 문단 확장보다 나아지지 않으면 채택하지 않는다. 모델/저장소가 결정되지 않은 상태에서 이전 직접 개발안의 7~12일 추정을 그대로 사용하지 않는다.

## 확인 범위와 자료

GitHub MCP로 upstream README, pyproject, Qdrant 저장소 구현, DataStax adapters 디렉터리, release 및 커밋 정보를 조회했다. 공식 사이트·upstream 웹 문서와 현재 로컬 설정도 대조했다. 설치·모델 다운로드·API 호출·색인·테스트·운영 DB 변경은 하지 않았다. 결론은 소스/문서 검토에 근거한 **우선 시험 후보 추천**이다.

- [LightRAG upstream](https://github.com/HKUDS/LightRAG)
- [LightRAG v1.5.7](https://github.com/HKUDS/LightRAG/releases/tag/v1.5.7)
- [v1.5.7 의존성](https://github.com/HKUDS/LightRAG/blob/v1.5.7/pyproject.toml)
- [v1.5.7 Core 문서](https://github.com/HKUDS/LightRAG/blob/v1.5.7/docs/ProgramingWithCore.md)
- [v1.5.7 Qdrant 구현](https://github.com/HKUDS/LightRAG/blob/v1.5.7/lightrag/kg/qdrant_impl.py)
- [NetworkX 저장 계약](https://github.com/HKUDS/LightRAG/blob/main/docs/design/NetworkXSingleWriterContract.md)
- [DataStax Graph RAG](https://github.com/datastax/graph-rag)
- [DataStax 지원 adapters](https://datastax.github.io/graph-rag/guide/adapters/)
- [Microsoft GraphRAG와 유지보수 방침](https://github.com/microsoft/graphrag)
- [Qdrant의 Microsoft GraphRAG 연동 설명](https://qdrant.tech/documentation/frameworks/microsoft-graphrag/)
- [LlamaIndex Property Graph 안내](https://developers.llamaindex.ai/python/framework/module_guides/indexing/lpg_index_guide/)
- [LlamaIndex PropertyGraphIndex 구현](https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/indices/property_graph/base.py)
- [LlamaIndex 스키마 기반 추출 구현](https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/indices/property_graph/transformations/schema_llm.py)
- [LlamaIndex Qdrant 구현](https://github.com/run-llama/llama_index/blob/main/llama-index-integrations/vector_stores/llama-index-vector-stores-qdrant/llama_index/vector_stores/qdrant/base.py)
- [Cognee](https://github.com/topoteretes/cognee)
- [Cognee Qdrant 어댑터](https://github.com/topoteretes/cognee-community/tree/main/packages/vector/qdrant)
- [Fast GraphRAG](https://github.com/circlemind-ai/fast-graphrag)
- [FalkorDB GraphRAG SDK](https://github.com/FalkorDB/GraphRAG-SDK)
- [RAG-Anything](https://github.com/HKUDS/RAG-Anything)
