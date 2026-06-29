"""embeddings.py 테스트.

실제 KURE 모델(~2GB 다운로드)이 필요한 테스트는 opt-in이다:
  - 환경: `RAG_RUN_MODEL_TESTS=1` 그리고 sentence-transformers 설치 시에만 실행.
  - 정부망/SSL 인터셉션 환경에서는 HF 다운로드가 막힐 수 있음(스펙 §8) → 기본 skip.
인터페이스/팩토리 계약은 모델 없이 항상 검증한다.
"""
import importlib.util
import os

import pytest

from rag_mcp.embeddings import EmbeddingBackend, _apply_offline_env, get_backend


def test_apply_offline_env_default_on(monkeypatch):
    """기본값은 오프라인 — HF 네트워크 조회를 끈다(정부망 4분 멈춤 방지)."""
    monkeypatch.delenv("RAG_HF_OFFLINE", raising=False)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    assert _apply_offline_env() is True
    assert os.environ["HF_HUB_OFFLINE"] == "1"
    assert os.environ["TRANSFORMERS_OFFLINE"] == "1"


def test_apply_offline_env_opt_out(monkeypatch):
    """RAG_HF_OFFLINE=0이면 온라인 허용(최초 다운로드 등) — env를 건드리지 않는다."""
    monkeypatch.setenv("RAG_HF_OFFLINE", "0")
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    assert _apply_offline_env() is False
    assert "HF_HUB_OFFLINE" not in os.environ


def test_factory_unknown_model_raises():
    with pytest.raises(ValueError):
        get_backend("gpt")


def test_factory_returns_backend_with_dim():
    """모델 다운로드 없이 백엔드 메타(이름·차원)는 즉시 확정."""
    b = get_backend("kure")
    assert isinstance(b, EmbeddingBackend)
    assert b.name == "kure"
    assert b.dimension == 1024
    # lazy: 아직 모델 미로딩
    assert b._model is None


_HAS_ST = importlib.util.find_spec("sentence_transformers") is not None
_RUN_MODEL = os.environ.get("RAG_RUN_MODEL_TESTS") == "1"


@pytest.mark.skipif(
    not (_HAS_ST and _RUN_MODEL),
    reason="무거운 모델 테스트: RAG_RUN_MODEL_TESTS=1 + sentence-transformers 필요 (정부망 SSL 시 §8)",
)
def test_kure_real_embedding_dim():
    """[무거움] 실제 KURE 임베딩 차원/정규화 검증."""
    b = get_backend("kure")
    vecs = b.embed_documents(["일상경비 한도", "201-01 집행 항목"])
    assert len(vecs) == 2
    assert len(vecs[0]) == 1024
    q = b.embed_query("일상경비 한도는 얼마인가")
    assert len(q) == 1024
