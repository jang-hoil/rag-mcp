# RAG MCP 프로젝트 — Codex 운영 규칙

회계·예산 지침서(한국어, 표·과목코드·금액 중심) RAG MCP 서버. 구현 스펙은
`../RAG_MCP_구현스펙_ClaudeCode.md` 참조.

## 작업 방식 (스펙 §12 — 검증 루프)
- **각 모듈은 테스트 먼저 작성 → 구현 → `pytest -q` 통과까지 루프.** 통과 전 다음 모듈로 넘어가지 말 것.
- **테스트를 약화시켜 억지로 통과시키지 말 것.** 통과가 안 되면 구현을 고치거나 스펙 충돌을 보고.
- 같은 테스트가 2회 이상 실패하면 `ultrathink`로 근본 원인 분석. **4회 연속 실패 시 멈추고 보고.**
- 아키텍처 결정·반복 실패·난해한 버그에서만 `ultrathink` 사용. 평상시 기본 effort.
- **각 모듈 통과 시 작은 단위로 git 커밋** (되돌림 지점 확보).
- 스펙 **§1 검증 사실은 전제로 사용하고 재검증하지 말 것.**

## 핵심 기술 결정 (전제)
- 표 셀은 **JSON에서 추출** (markdown은 평면화됨). borderless 뭉친 표는 **이미지 폴백**.
- BM25 별도 pkl 만들지 말 것 → **Qdrant sparse로 단일 저장**.
- 한국어 토큰화는 **Kiwi** (공백 분리 금지). 코드·금액은 토큰 보존.
- dense+sparse 점수 **직접 가산 금지** → RRF/DBSF 융합만.
- 저장·검색 임베딩 모델 동일해야 함 → `embedding_model`이 컬렉션 결정.
- Qdrant **local path 모드 + serve 중 CLI ingest 동시 실행 금지** (파일락).

## 환경
- Python: **uv + 3.11** (.venv). 3.13은 torch 설치 리스크로 회피.
- Java 21 (OpenDataLoader JVM). pdftoppm 없음 → **pymupdf 폴백** 사용.
- 명령: `uv run pytest -q`, `uv run rag-mcp ...`

## 진행 상태
**매 세션 시작 시 `PROGRESS.md`를 먼저 읽을 것.** 현재 마일스톤·다음 할 일·재개 방법이 거기 있다.
