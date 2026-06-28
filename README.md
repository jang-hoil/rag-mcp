# RAG MCP

회계·예산 지침서(한국어, 표·과목코드·금액 중심)를 로컬에서 색인·검색하는 MCP 서버. Qdrant dense+sparse 하이브리드(RRF/DBSF) 검색을 제공한다.

## 설치

Python 3.11 + [uv](https://docs.astral.sh/uv/) 기준. (3.13은 torch 설치 리스크로 회피)

```bash
git clone <repo-url> "RAG MCP"
cd "RAG MCP"
uv sync                 # 의존성 설치 (.venv 구성)
uv run pytest -q        # 설치 검증 (62+ passed / 1 skipped)
```

KURE-v1 임베딩 모델은 첫 검색 시 HuggingFace에서 자동 다운로드되어 `~/.cache/huggingface`에 캐시된다. (정부망 등 MITM 프록시 환경의 SSL 차단은 `truststore`가 런타임에 Windows 인증서 저장소를 사용해 자동 처리)

## 실행 / 연결

MCP 서버는 stdio transport로 동작한다. CLI로 직접 실행하거나(점검용):

```bash
uv run rag-mcp serve      # MCP 서버 (stdio)
uv run rag-mcp status     # 컬렉션 상태 확인
uv run rag-mcp search "일상경비 한도" --top-k 5
```

### Claude Desktop 등록

설정 파일 `%APPDATA%\Claude\claude_desktop_config.json`의 `mcpServers`에 아래 항목을 추가한다. **경로는 이 PC 기준 실값이므로 본인 환경에 맞게 수정**할 것.

```json
"rag-mcp": {
  "command": "C:\\Users\\Owner\\AppData\\Local\\Programs\\Python\\Python313\\Scripts\\uv.exe",
  "args": [
    "--directory",
    "C:\\Users\\Owner\\Desktop\\RAG MCP",
    "run",
    "rag-mcp",
    "serve"
  ]
}
```

- `command`는 `uv.exe` **절대경로**: GUI 앱은 셸 PATH를 상속 못 할 수 있어 `"uv"`만으로는 실패 위험.
- `--directory <프로젝트>`: cwd 미보장 환경에서 프로젝트 위치를 고정.
- env 불필요: SSL은 truststore가 처리(Node 서버의 `NODE_EXTRA_CA_CERTS` 같은 설정 불필요).

수정 후 Claude Desktop을 **완전히 종료 후 재시작**해야 반영된다. 더 자세한 절차·검증·문제 해결은 [`MCP_연동가이드.md`](./MCP_연동가이드.md) 참조.

## 제공 도구

서버가 노출하는 MCP 도구 9개:

| 도구 | 설명 |
|---|---|
| `search_documents` | 하이브리드(dense+sparse, RRF/DBSF) 검색. `fiscal_year`로 연도 한정, `meta.<키>` 필터 지원 |
| `ingest_pdf` | PDF 색인을 **백그라운드로 시작**하고 즉시 `job_id` 반환(비블로킹) |
| `ingest_status` | `ingest_pdf`가 준 `job_id`로 진행 상태 조회(`running`/`done`/`error`) |
| `get_chunk` | `chunk_id`로 단건 청크 조회 |
| `list_documents` | 색인된 문서 목록(연도·청크수·상태) |
| `delete_document` | 문서 삭제 (`confirm=True`일 때만 실제 실행) |
| `reindex_document` | 재색인 (기본 `reparse=False`로 기존 parsed 재사용) |
| `collection_status` | 컬렉션·연도별 문서수·차원·sparse 여부 |
| `review_before_ingest` | **읽기 전용** 검토. 들어올 문서 id/연도(파일명 기반)와 현재 색인된 전체 목록을 함께 반환(삭제·색인·매칭 없음) |

### 비동기 색인 워크플로우

큰 PDF(300p급)는 임베딩에만 수 분이 걸려 동기 색인 시 클라이언트가 타임아웃된다. 그래서 색인은 비동기 job으로 처리한다:

1. `ingest_pdf(path=...)` → `{"ok": true, "job_id": "...", "status": "running"}` 즉시 반환.
2. `ingest_status(job_id)`를 주기적으로 호출 → `done`이면 `result`에 청크수, `error`면 원인.
3. 동시 색인은 **1개만** 허용(Qdrant local 단일 writer). 색인 중에도 검색 등 다른 도구는 정상 응답.

## 주의사항

### 파일락 — 단일 프로세스만 접근

Qdrant local path 모드는 다중 프로세스 접근이 불가하다.

- 서버가 떠 있는 동안(`serve`) **별도 터미널에서 `rag-mcp ingest`/`status`를 실행하지 말 것** → 파일락 충돌. 색인은 MCP의 `ingest_pdf` 도구로 하거나, 서버를 잠깐 내리고 CLI로 한다.
- `serve` 인스턴스도 **1개만**. 중복 실행 시 충돌한다.

### 환경 변수

모든 설정은 환경 변수로 조정한다(`.env.example` 참조, 미설정 시 기본값 사용). **별도의 시크릿/API 키는 필요 없다.**

| 변수 | 기본값 | 설명 |
|---|---|---|
| `RAG_DATA_DIR` | `./data` | 색인 산출물·Qdrant·manifest 저장 루트 |
| `RAG_QDRANT_MODE` | `local` | `local`(path) 또는 `server`(url) |
| `RAG_QDRANT_PATH` | `./data/qdrant` | local 모드 저장 경로 |
| `RAG_QDRANT_URL` | (없음) | server 모드 URL (예: `http://localhost:6333`) |
| `RAG_EMBEDDING_MODEL` | `kure` | `kure`(KURE-v1) 또는 `bge_m3`(BGE-M3). 저장·검색 모델이 일치해야 함 |
| `RAG_RENDER_DPI` | `200` | 표 이미지 렌더 DPI |

### gitignore되는 것 (재생성 가능)

색인 산출물·캐시는 커밋하지 않는다 — 원본 PDF만 있으면 재생성된다.

- `data/qdrant/`, `data/parsed/`, `data/manifests/` — 벡터DB·파싱 결과·manifest
- `*.pdf`, `*.png` — 원본 문서·렌더된 표 이미지
- `.cache/`, `.venv/`, `__pycache__/`, `.pytest_cache/` — 모델 캐시·가상환경·파이썬 캐시
- `.env` — 로컬 환경 설정
