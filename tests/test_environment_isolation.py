"""운영 Qdrant 환경변수가 기본 테스트 저장소로 새지 않는지 확인한다."""

from rag_mcp.config import Config


def test_default_test_config_uses_local_qdrant():
    config = Config()

    assert config.qdrant_mode == "local"
    assert config.qdrant_url == ""
