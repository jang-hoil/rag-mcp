# 기존 RAG MCP 재사용 기준의 LightRAG·LlamaIndex 비교

확인일: 2026-11-06. 검토 범위: 현재 checkout의 실제 코드·lockfile와 upstream 공식 소스/문서. 패키지 설치·실행 벤치마크 전의 통합 설계 판단이다.

## 추천

**기존 Qdrant 데이터와 코드를 최대한 활용한다는 기준에서는 LlamaIndex Property Graph의 부분 도입을 추천한다.** 현재 Retriever와 VectorStore를 계속 사용하고 그래프 추출·저장·탐색을 추가한다. LlamaIndex의 전체 색인·검색 파이프라인으로 교체할 필요는 없다.

이전 LightRAG 우선 추천은 공개된 그래프 엔진을 한 묶음으로 가져오는 관점이었다. 이번에는 기존 컬렉션·벡터·Kiwi sparse·RRF/DBSF·페이지 출처·MCP를 유지하는 비중을 우선하므로 추천을 LlamaIndex 부분 도입으로 구체화한다. 이는 한국어 검색 성능의 실측 우열 판정이 아니다.

## 현재 시스템에서 실제로 보존할 것

| 항목 | 현재 구현 |
|---|---|
| PDF/표 처리 | `pipeline.parse_and_chunk`, `chunking.build_chunks`와 JSON 표 처리 |
| 저장된 청크 | `Indexer.load_chunks`로 chunks.jsonl 재사용 가능 |
| Qdrant 포인트 | `point_id_for(chunk_id)`의 UUIDv5, 모델·문서별 컬렉션 |
| 벡터 | 이름이 dense/sparse인 두 벡터, KURE 1024차원과 Kiwi 기반 sparse |
| 검색 | `Retriever.search_with_diagnostics` → `VectorStore.query`, RRF/DBSF·연도/metadata 필터·reranker |
| 출처 | chunk_id, document_id, page, heading_path, source_path, table/image 플래그 |
| 원문 조회 | `RagService.get_chunk`와 `get_page_image` |
| 갱신 | manifest, Indexer의 교체/복구 경로, 변경 잠금과 작업 상태 |
| MCP | 기존 search_documents와 도구 반환 구조 |

lockfile: qdrant-client 1.18.0, mcp 2.0.0, Pydantic 2.13.4, Kiwi 0.23.2, sentence-transformers 5.6.0. 현재 프로젝트는 Python >=3.11,<3.13을 명시한다.

## 같은 기준으로 비교

| 기준 | LlamaIndex 부분 도입 | LightRAG 기본 구성 |
|---|---|---|
| Qdrant 서버 계속 사용 | 가능 | 가능 |
| 현재 청크 컬렉션·벡터 사용 | 기존 Retriever/VectorStore를 호출하면 포맷 변경 없이 읽는 구조로 설계 가능 | 기본 엔진은 자체 청크·개체·관계의 ID/벡터/상태를 관리. 기존 컬렉션을 바로 연결하는 포맷은 아님 |
| 기존 청크 재임베딩 회피 | PropertyGraphIndex의 embed_kg_nodes=False와 사용자 정의 retriever를 이용하는 설계 가능 | 기본 조회에는 개체·관계·청크 벡터 인덱스를 이용. 기존 벡터 재사용에는 별도 storage/입력 매핑 검증 필요 |
| Kiwi sparse + RRF/DBSF | 기존 Retriever를 그대로 검색 입구로 사용 | 기존 검색을 별도 경로로 유지하거나 더 깊이 연결해야 함. LightRAG hybrid는 local/global 결합으로 현재 dense+sparse 융합과 다른 개념 |
| PDF 본문·표 청크 | 기존 Chunk를 TextNode로 직접 변환해 재분할 없이 입력 | 파싱 결과를 입력할 수 있으나 자체 ingestion/청크/source 체계에 맞추는 작업 필요 |
| 쪽수·제목 경로 | 원래 chunk_id와 metadata를 매핑하여 기존 원문 조회를 계속 사용 | source ID·파일 경로와 기존 페이지 metadata의 연결 코드를 추가 |
| 관계 유형 제어 | SchemaLLMPathExtractor 또는 사용자 정의 TransformComponent를 선택 | 추출 프롬프트·프로필·검증 hook/custom KG로 조정 가능 |
| 재색인·삭제 | 기존 manifest/잠금에 그래프 버전 관리 추가 | 기존 manifest와 LightRAG 문서 상태·캐시·그래프/벡터 갱신의 연결을 설계 |
| 별도 DB 서버 설치 | SimplePropertyGraphStore 파일 저장으로 소규모 시험 가능 | NetworkXStorage+파일 KV로 소규모 시험 가능 |
| 장점이 커지는 조건 | 이미 작동하는 검색·출처 체계를 보존하며 관계 기능 확장 | 자체 ingestion·graph/vector query·서비스 기능을 한 묶음으로 도입 |

비교표의 난이도와 적합성은 아래 코드 계약을 바탕으로 한 추론이다. LightRAG로 기존 코드를 재사용할 수 없다는 뜻은 아니다. 두 후보 모두 확장 코드를 작성하면 재사용 범위가 늘어난다. 이번에는 공식 확장 지점과 기존 코드의 경계가 더 잘 맞는 쪽을 선택한다.

## 판단을 바꾼 구체적인 코드 근거

### 1. LlamaIndex는 기존 검색기를 연결할 공식 확장 지점이 있다

`CustomPGRetriever`는 `custom_retrieve(query_str)`를 구현하도록 제공한다. `PropertyGraphIndex.as_retriever(sub_retrievers=...)`는 사용자 retriever를 주입받는다. 기존 Retriever를 호출해 얻은 chunk_id와 출처를 그래프 조회에 연결할 수 있다.

기본 LLMSynonymRetriever/VectorContextRetriever를 무조건 실행하는 구성이 아니라 사용할 retriever를 명시적으로 지정한다. 연도·metadata·문서 범위를 기존 검색에서 적용한 후 그래프 대상과 근거에서도 다시 검사한다.

### 2. 그래프와 새 벡터 색인을 분리할 수 있다

`PropertyGraphIndex`는 `property_graph_store`, `vector_store`, `kg_extractors`, `embed_kg_nodes`를 따로 받는다. `_insert_nodes`는 embed_kg_nodes가 참일 때 청크와 KG 노드의 임베딩을 만든다. 거짓이면 해당 경로를 실행하지 않는다.

따라서 첫 버전은 기존 벡터 검색으로 출발점을 찾고 그래프는 ID/참조 관계로 탐색하도록 설계할 수 있다. 그래프를 만들기 위해 기존 청크를 일괄 재임베딩하는 일을 필수로 넣지 않는다. 추후 그래프 개체의 의미 검색을 추가하면 별도 개체 임베딩 비용이 생긴다.

### 3. LightRAG Qdrant 지원은 현재 저장 포맷의 재사용 보장이 아니다

`LightRAG.__post_init__`는 entities_vdb, relationships_vdb, chunks_vdb를 각각 만든다. `QdrantVectorDBStorage`는 workspace·자체 ID·단일 dense vector와 content/source payload를 관리한다. 현재 rag-mcp는 문서별 컬렉션, dense/sparse 이름, chunk_id UUIDv5와 다른 payload를 쓴다.

LightRAG 공식 문서에는 NoopVectorDBStorage를 통한 임베딩 없는 graph-only ingestion도 있다. 다만 이 설정에서는 bypass만 제공하고 local/global/hybrid/mix/naive 조회에는 벡터 인덱스가 필요하다고 명시한다. 따라서 임베딩만 끄면 현재 검색기를 LightRAG 그래프 검색으로 그대로 활용할 수 있다고 판단하지 않는다.

현재 `ainsert_custom_chunks` 구현은 deprecated 주석과 자체 복구 journal·상태를 가진다. 기존 표 청크를 입력할 수 있다는 사실과 기존 청크/벡터/페이지를 무변환으로 사용하는 것은 구분한다. 실제 도입 시 고정한 버전의 유지되는 입력 API를 선택해야 한다.

### 4. LlamaIndex Qdrant 어댑터도 무설정 호환은 아니다

QdrantVectorStore의 기본 벡터 이름은 text-dense/text-sparse-new이며 현재 이름은 dense/sparse다. source payload 복원과 point UUID → 원래 chunk_id 매핑도 필요하다. 기존 컬렉션의 text를 읽는 fallback 구현은 있지만 그것만으로 모든 metadata, 필터, 검색 점수가 현재와 동일하다고 보장할 수 없다.

**첫 버전은 QdrantVectorStore 어댑터로 현재 검색기를 교체하지 않는다.** 기존 VectorStore가 Qdrant를 계속 읽고 LlamaIndex는 그래프 관련 부품을 담당한다. 추후 교체할 실익이 생길 때만 별도 호환 테스트를 진행한다.

## 추천하는 연결 구조

검색 경로는 `기존 MCP → 기존 Retriever → 기존 Qdrant 검색 → 검색 청크에 대응하는 그래프 관계 탐색 → 기존 get_chunk/get_page_image로 출처 복원 → MCP 반환`으로 한다.

그래프 구성 경로는 `기존 chunks.jsonl/파싱 결과 → TextNode·관계 변환 → LlamaIndex 그래프 저장`으로 한다. 직접 TextNode를 제공하며 표를 문장 분할기로 다시 나누지 않는다.

1. **기존 부분 유지:** PDF parser, table chunking, KURE, Kiwi sparse, RRF/DBSF, reranker, 기본 검색·원문/이미지 도구.
2. **LlamaIndex에서 사용:** PropertyGraphIndex/PropertyGraphStore, 개체·관계 모델, 필요한 extractor, CustomPGRetriever 또는 BasePGRetriever.
3. **첫 저장:** 문서 버전별 SimplePropertyGraphStore. SQLite 설치는 필요하지 않다. 파일 publish는 기존 변경 잠금을 이용해 임시 파일→교체로 감싸고 단일 작성자로 운용한다. 기본 persist 자체에 트랜잭션·프로세스 잠금이 있다고 가정하지 않는다.
4. **우리 쪽 추가 코드:** Chunk→TextNode 변환, 검색 seed→그래프 앵커 매핑, 명시 참조 식별/검증, 원문 복원, 버전·필터·상한 적용, 오류 시 기본 검색 fallback.
5. **처음에는 새 개체 임베딩 제외:** embed_kg_nodes=False와 명시적 custom retriever를 사용한다. 기본 LLM synonym 경로를 자동 실행하지 않는다.

원래 GitHub 저장소와 운영 자료는 보존한다. 새 프로젝트가 읽는 기존 청크와 Qdrant 컬렉션은 읽기 전용으로 다루고, 신규 색인 시험은 별도 컬렉션/사본에서 수행한다. 재사용 실험을 위해 운영 Qdrant 데이터를 변환하거나 덮어쓰지 않는다.

## 출처와 문서 갱신에 필요한 추가 설계

- 원래 chunk_id와 point UUID를 서로 혼동하지 않는다. 그래프에는 원래 문서/청크 ID와 doc_revision을 함께 보존한다.
- 개체 ID는 문서·버전·소속 범위를 포함한다. LlamaIndex EntityNode의 기본 ID가 name이므로 제1호 서식 같은 동명 항목을 그대로 전역 키로 쓰지 않는다.
- graph source의 한 필드가 모든 원문 근거의 목록을 보장한다고 가정하지 않는다. 관계별 evidence refs에 원래 청크 ID·쪽수·원문 구간을 따로 보존한다.
- SchemaLLMPathExtractor의 strict는 유형/구조를 제한하는 기능이다. 관계가 사실이라는 보증이 아니므로 원문 근거 검사와 unresolved 처리는 별도로 둔다.
- 문서 삭제·재색인 시 활성 그래프 버전을 먼저 무효화하고, 새 청크/그래프가 일치할 때만 활성화한다. 저장소 두 곳의 분산 트랜잭션을 가정하지 않는다.
- 그래프 탐색에서도 문서·모델·버전·metadata 필터, 최대 홉/결과/시간 상한을 적용한다.

## 비용과 설치 범위

초기 필수 검토 패키지는 llama-index-core다. 실제 LLM을 사용한 추출을 선택하면 해당 제공자 패키지를 추가한다. Qdrant는 기존 코드로 호출하므로 첫 단계에 llama-index-vector-stores-qdrant를 반드시 추가할 필요는 없다. LlamaParse나 별도 그래프 DB 서버도 필수 구성에 넣지 않는다.

확인한 upstream core의 Python 조건은 >=3.10,<4.0, Pydantic은 >=2.8.0이다. 현재 Python 명시 범위와 Pydantic 2.13.4가 이 조건을 만족한다. 선택적 Qdrant 어댑터의 qdrant-client 조건은 >=1.16.0이며 현재 1.18.0이 범위에 든다. 이는 일부 버전 조건의 교집합 확인이며 전체 의존성 해결·Windows 실행의 검증 결과는 아니다.

기존 청크 벡터를 유지해도 그래프 구축·저장·검증 비용은 발생한다. 명시 참조는 규칙 기반 변환기로 시작할 수 있고, 문맥 관계를 LLM으로 추출하려면 모델·API 또는 로컬 실행 비용을 따로 측정한다. KURE 임베딩 모델을 관계 추출용 언어모델로 간주하지 않는다.

## 첫 실험의 합격 기준

- 기존 검색을 켠/끈 그래프 모드와 관계없이 기본 Retriever의 결과 ID·순서·본문·출처가 유지됨.
- 운영 컬렉션에 쓰기·삭제·포맷 변경이 없고 기존 청크 벡터 재임베딩 호출이 없음.
- 본문→표/서식의 명시 참조를 올바른 문서·소속·버전에서 찾고 PDF 쪽수를 복원함.
- 동일 이름/번호의 개체를 잘못 합치지 않음.
- 그래프 실패·버전 불일치에서는 기본 검색 결과를 그대로 제공함.
- 같은 근거 예산의 기존 검색/주변 문단 확장과 비교하여 필요한 근거 전체를 찾는 비율을 보고함.
- 원문 교체·삭제·재시작·동시 요청에서 오래된 관계가 노출되지 않음.

이 확인을 통과하면 LlamaIndex 안으로 진행한다. 실패 원인이 extractor 품질인지 프레임워크 경계인지 구분하고, 후자의 통합 비용이 예상보다 크면 LightRAG 시험안을 비교한다. 기존 결과의 보존과 한국어 관계 검색의 개선은 다른 검증 항목이다.

## 검증 상태와 출처

이번 비교 작업은 소스/문서 검토와 계획 수정이다. 새 패키지 설치, 두 엔진 실행, 한국어 검색 성능 비교, 데이터 변경은 수행하지 않았다. 비교 작성 당시 중단됐던 기존 pytest는 이후 정상 임시 폴더·소켓 실행 환경에서 313 passed, 9 skipped로 통과했다. 이 결과는 기존 rag-mcp의 기준선이며, 새 후보 엔진의 런타임 호환성 검증은 별도로 진행한다.

- [LlamaIndex Property Graph 공식 안내](https://developers.llamaindex.ai/python/framework/module_guides/indexing/lpg_index_guide/)
- [PropertyGraphIndex: embed_kg_nodes·extractor·retriever 설정](https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/indices/property_graph/base.py)
- [CustomPGRetriever 구현](https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/indices/property_graph/sub_retrievers/custom.py)
- [그래프 노드·원문 매핑](https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/graph_stores/types.py)
- [SimplePropertyGraphStore 저장·조회](https://github.com/run-llama/llama_index/blob/main/llama-index-core/llama_index/core/graph_stores/simple_labelled.py)
- [LlamaIndex Qdrant 벡터명·payload 처리](https://github.com/run-llama/llama_index/blob/main/llama-index-integrations/vector_stores/llama-index-vector-stores-qdrant/llama_index/vector_stores/qdrant/base.py)
- [LlamaIndex core 의존성](https://github.com/run-llama/llama_index/blob/main/llama-index-core/pyproject.toml)
- [LightRAG 저장소·조회 모드·graph-only 조건](https://github.com/HKUDS/LightRAG/blob/main/docs/ProgramingWithCore.md)
- [LightRAG 코어: 개체/관계/청크 벡터와 입력 경로](https://github.com/HKUDS/LightRAG/blob/main/lightrag/lightrag.py)
- [LightRAG Qdrant 구현](https://github.com/HKUDS/LightRAG/blob/main/lightrag/kg/qdrant_impl.py)
- 현재 로컬 근거: src/rag_mcp/{models,pipeline,chunking,indexer,retrieval,vector_store,service,config}.py, pyproject.toml, uv.lock.
