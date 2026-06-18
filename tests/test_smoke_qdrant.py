"""마일스톤1 스모크 테스트 (스펙 §9.1, §12.3).

Qdrant **local(path) 모드**에서 다음이 실제로 동작하는지 검증한다 (리스크1, 전제):
  - named dense + named sparse 벡터를 한 포인트에 동시 upsert
  - query_points의 prefetch(dense+sparse) + FusionQuery(RRF) 서버측 융합
  - payload 필터(fiscal_year)가 기대대로 결과를 거름

이 테스트가 통과해야 이후 모든 마일스톤의 전제가 성립한다.
막히면 Docker 서버 모드를 재검토한다 (스펙 §1.3 주의).
"""
import math

from qdrant_client import QdrantClient, models


DENSE_DIM = 4
COLLECTION = "smoke_chunks"


def _make_client(tmp_path) -> QdrantClient:
    """local path 모드 클라이언트."""
    return QdrantClient(path=str(tmp_path / "qdrant"))


def _create_collection(client: QdrantClient) -> None:
    """named dense + named sparse 벡터 컬렉션 생성."""
    client.create_collection(
        collection_name=COLLECTION,
        vectors_config={
            "dense": models.VectorParams(size=DENSE_DIM, distance=models.Distance.COSINE),
        },
        sparse_vectors_config={
            "sparse": models.SparseVectorParams(),
        },
    )
    # 필터용 payload 인덱스
    client.create_payload_index(
        collection_name=COLLECTION,
        field_name="fiscal_year",
        field_schema=models.PayloadSchemaType.INTEGER,
    )


def _seed_points(client: QdrantClient) -> None:
    """테스트 포인트 4개 upsert.

    - 1, 2, 4: fiscal_year=2026
    - 3:       fiscal_year=2025 (필터로 걸러져야 함)
    sparse 인덱스 100 = '201-01'(과목코드) 같은 정확매칭 토큰을 모사.
    """
    points = [
        models.PointStruct(
            id=1,
            vector={
                "dense": [0.9, 0.1, 0.0, 0.0],
                "sparse": models.SparseVector(indices=[100, 101], values=[2.5, 1.0]),
            },
            payload={"text": "201-01 일반수용비 한도", "fiscal_year": 2026},
        ),
        models.PointStruct(
            id=2,
            vector={
                "dense": [0.1, 0.9, 0.0, 0.0],
                "sparse": models.SparseVector(indices=[100], values=[3.0]),  # 코드 강매칭
            },
            payload={"text": "201-01 집행 가능 항목", "fiscal_year": 2026},
        ),
        models.PointStruct(
            id=3,
            vector={
                "dense": [0.9, 0.1, 0.0, 0.0],  # dense는 1과 동일하게 가깝지만 2025라 필터됨
                "sparse": models.SparseVector(indices=[100], values=[3.0]),
                # 노트: 음수 인덱스/값 회피
            },
            payload={"text": "2025년 단가 (혼입 금지)", "fiscal_year": 2025},
        ),
        models.PointStruct(
            id=4,
            vector={
                "dense": [0.0, 0.0, 1.0, 0.0],
                "sparse": models.SparseVector(indices=[200], values=[1.0]),
            },
            payload={"text": "무관한 본문", "fiscal_year": 2026},
        ),
    ]
    client.upsert(collection_name=COLLECTION, points=points)


def _hybrid_query(client, dense_vec, sparse_vec, top_k=10, fiscal_year=None):
    """dense+sparse prefetch → RRF 융합 + (선택) fiscal_year 필터.

    중요(local 모드 검증 결과): top-level query_filter는 prefetch 융합 후보 풀을
    거르지 못한다. 필터는 **각 Prefetch 안에** 넣어 후보 단계에서 걸러야 한다.
    """
    query_filter = None
    if fiscal_year is not None:
        query_filter = models.Filter(
            must=[
                models.FieldCondition(
                    key="fiscal_year",
                    match=models.MatchValue(value=fiscal_year),
                )
            ]
        )
    return client.query_points(
        collection_name=COLLECTION,
        prefetch=[
            models.Prefetch(query=dense_vec, using="dense", limit=20, filter=query_filter),
            models.Prefetch(query=sparse_vec, using="sparse", limit=30, filter=query_filter),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=top_k,
        with_payload=True,
    ).points


def test_hybrid_rrf_no_filter(tmp_path):
    """RRF 융합이 dense+sparse 양쪽 신호를 합쳐 결과를 반환한다."""
    client = _make_client(tmp_path)
    _create_collection(client)
    _seed_points(client)

    # 질의: 과목코드 '201-01'(sparse idx 100) + dense는 포인트1 방향
    dense_q = [0.9, 0.1, 0.0, 0.0]
    sparse_q = models.SparseVector(indices=[100], values=[1.0])

    points = _hybrid_query(client, dense_q, sparse_q, top_k=10)
    ids = [p.id for p in points]

    # 코드매칭(1,2,3)이 무관 본문(4)보다 위
    assert set(ids) >= {1, 2, 3}, ids
    assert 4 in ids
    assert ids.index(4) > ids.index(1), f"무관 본문 4가 코드매칭보다 위: {ids}"
    # 점수는 양수 (RRF)
    assert all(p.score > 0 for p in points)


def test_fiscal_year_filter_excludes_other_year(tmp_path):
    """fiscal_year=2026 필터가 2025 포인트(id=3)를 제외한다."""
    client = _make_client(tmp_path)
    _create_collection(client)
    _seed_points(client)

    dense_q = [0.9, 0.1, 0.0, 0.0]
    sparse_q = models.SparseVector(indices=[100], values=[1.0])

    points = _hybrid_query(client, dense_q, sparse_q, top_k=10, fiscal_year=2026)
    ids = [p.id for p in points]

    assert 3 not in ids, f"2025 포인트가 2026 필터를 통과함: {ids}"
    assert set(ids) <= {1, 2, 4}
    assert {1, 2}.issubset(set(ids))


def test_sparse_only_exact_code_match(tmp_path):
    """sparse 단독 검색에서 코드 토큰(idx 100)이 강매칭 포인트를 끌어온다."""
    client = _make_client(tmp_path)
    _create_collection(client)
    _seed_points(client)

    # dense는 일부러 무관 방향, sparse만으로 코드매칭 확인
    points = client.query_points(
        collection_name=COLLECTION,
        query=models.SparseVector(indices=[100], values=[1.0]),
        using="sparse",
        limit=10,
        with_payload=True,
    ).points
    ids = [p.id for p in points]

    # idx 100을 가진 1,2,3만, 무관 본문 4는 제외
    assert set(ids) == {1, 2, 3}, ids
    # 가중치 큰 2,3(3.0)이 1(2.5)보다 위
    assert ids.index(2) < ids.index(1)
