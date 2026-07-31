"""데이터 폴더 결정 규칙 — 실행 위치(cwd)에 휘둘리지 않아야 한다.

배경(2026-07-31 실장애): data_dir 기본값이 `./data`(= 서버가 실행된 폴더 기준 상대경로)라,
코덱스가 자기 설치 폴더에서 서버를 띄우자 `C:\\Program Files\\...\\app\\data`를 만들려다
PermissionError로 서버가 즉사했다. 쓰기 가능한 엉뚱한 폴더였다면 더 나빴다 — 에러 없이
**빈 인덱스를 새로 만들어** 색인해둔 문서가 사라진 것처럼 보인다.

규칙: RAG_DATA_DIR가 있으면 그것, 없으면 **프로젝트 루트**(pyproject.toml이 있는 폴더)의
data. 프로젝트 루트를 못 찾으면(패키지만 설치된 경우) 예전처럼 cwd 기준으로 물러선다.
"""
import os
from pathlib import Path

import pytest

from rag_mcp import config as config_mod
from rag_mcp.config import Config

PROJECT_ROOT = Path(config_mod.__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("RAG_DATA_DIR", raising=False)
    monkeypatch.delenv("RAG_QDRANT_PATH", raising=False)


def test_data_dir_ignores_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # 엉뚱한 폴더에서 실행
    assert Config().data_dir == (PROJECT_ROOT / "data").resolve()


def test_qdrant_path_ignores_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert Config().qdrant_path == (PROJECT_ROOT / "data" / "qdrant").resolve()


def test_parsed_and_manifests_follow_data_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = Config()
    assert config.parsed_dir == (PROJECT_ROOT / "data" / "parsed").resolve()
    assert config.manifests_dir == (PROJECT_ROOT / "data" / "manifests").resolve()


def test_env_var_still_wins(tmp_path, monkeypatch):
    # 운영에서 config.toml로 절대경로를 못 박는 방식이 계속 동작해야 한다
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RAG_DATA_DIR", str(tmp_path / "elsewhere"))
    config = Config()
    assert config.data_dir == (tmp_path / "elsewhere").resolve()
    assert config.qdrant_path == (tmp_path / "elsewhere" / "qdrant").resolve()


def test_explicit_qdrant_path_still_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_QDRANT_PATH", str(tmp_path / "q"))
    assert Config().qdrant_path == (tmp_path / "q").resolve()


def test_find_project_root_uses_pyproject_marker(tmp_path):
    root = tmp_path / "proj"
    (root / "src" / "pkg").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]", encoding="utf-8")

    assert config_mod.find_project_root(root / "src" / "pkg") == root


def test_find_project_root_returns_none_without_marker(tmp_path):
    # 마커가 없으면(패키지만 설치된 배포 형태) None → 호출부가 cwd로 폴백
    lonely = tmp_path / "no_marker" / "deep"
    lonely.mkdir(parents=True)

    assert config_mod.find_project_root(lonely) is None


def test_falls_back_to_cwd_when_project_root_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config_mod, "find_project_root", lambda start=None: None)

    assert Config().data_dir == (tmp_path / "data").resolve()
