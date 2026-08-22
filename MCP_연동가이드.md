# RAG MCP — Claude Desktop 연동 가이드

회계·예산 지침서 RAG 서버를 **Claude Desktop**(및 동일 방식의 MCP 클라이언트)에
stdio 방식으로 등록하는 절차. 아래 내용은 실제 stdio 핸드셰이크로 검증됨
(`initialize` → `tools/list` 11개 → `collection_status` 정상 반환 확인).

## 1. 동작 방식 (전제)
- 서버는 MCP Python SDK v2의 `MCPServer("rag-mcp").run(transport="stdio")`로 실행된다.
  클라이언트가 서버 프로세스를 직접 띄우고 stdin/stdout으로 JSON-RPC를 주고받는다.
- stdio에서 **stdout은 프로토콜 전용 채널**. 로그·진단 출력은 반드시 stderr로만
  (cli.py의 시작 메시지는 `file=sys.stderr`로 출력 — 의도된 안전 설계).
- 기동 순서는 **preflight(동기) → warmup(백그라운드 스레드) → `mcp.run(transport="stdio")`** 이다.
  - preflight는 Qdrant 저장소를 열어 **중복 인스턴스를 걸러낸다.** 다른 프로세스가 이미
    쥐고 있으면 `StorageBusyError`로 조용히 종료한다(파일락 충돌 방지).
  - KURE 임베딩 모델 적재는 **백그라운드에서 진행**되므로 서버는 즉시 `initialize`에 응답한다.
    워밍업이 끝나기 전에 검색을 호출하면 그 호출이 모델 적재를 기다린다.

## 2. 사전 요구사항
- `uv` + Python 3.11 `.venv` 구성 완료 (`uv run pytest -q` 통과 상태).
- KURE-v1 모델 캐시 존재(없으면 첫 색인·검색 시 다운로드 — 정부망은 truststore가 자동 처리).
- 색인할 PDF는 프로젝트 안 `pdf\` 폴더에 두기를 권장(경로가 매니페스트에 기록되므로).
- 아래 두 절대경로를 미리 확인해 둔다. 확인 방법은 [README.md](README.md)의 설치 5단계 참고.
  - `uv.exe` 경로 — `where uv`
  - 프로젝트 폴더 경로 — cmd 프롬프트의 커서 왼쪽에 표시됨

## 3. Claude Desktop 등록
설정 파일: `%APPDATA%\Claude\claude_desktop_config.json`
(= `C:\Users\Owner\AppData\Roaming\Claude\claude_desktop_config.json`)

`mcpServers` 객체에 **아래 항목만 추가**(기존 서버·preferences는 보존):

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

### 왜 이 형태인가
- **uv 절대경로**: Claude Desktop은 GUI 앱이라 로그인 셸의 PATH를 상속하지 못할 수
  있다 → `"command": "uv"`는 "찾을 수 없음" 실패 위험. 절대경로로 회피.
- **`--directory <프로젝트>`**: cwd 의존 없이 어디서 실행돼도 프로젝트를 찾게 함
  (Claude Desktop이 cwd를 보장하지 않으므로). `cwd` 키 대신 이 방식이 견고.
- **env 불필요**: SSL은 truststore가 런타임에 Windows 인증서 저장소를 사용하므로
  `NODE_EXTRA_CA_CERTS` 같은 추가 설정이 필요 없다(Node 서버와 다른 점).
- **`cmd /c` 래퍼 불필요**: npx(.cmd) 기반 서버와 달리 uv.exe를 직접 실행.

수정 후 Claude Desktop을 **완전히 종료 후 재시작**해야 반영된다.

## 4. 검증
1. 재시작 후 채팅 입력창의 도구/플러그 아이콘에서 `rag-mcp` 도구 11개가 보이는지 확인.
2. CLI 사전 점검(서버가 stdio로 정상 응답하는지 코드로 확인):
   ```
   uv --directory "C:\Users\Owner\Desktop\RAG MCP" run rag-mcp status
   ```
   → 컬렉션 상태 JSON(points/dimension 등)이 나오면 정상.
3. OCR 사전 점검(선택):
   ```
   uv --directory "C:\\Users\\Owner\\Desktop\\RAG MCP" run rag-mcp doctor
   ```
   → `ok=false`면 Tesseract 실행파일 또는 `kor+eng` 언어팩을 확인.
4. JSON 유효성(편집 후 깨지면 모든 MCP 서버가 로드 실패):
   ```
   python -c "import json; json.load(open(r'%APPDATA%\Claude\claude_desktop_config.json', encoding='utf-8')); print('OK')"
   ```

## 5. 노출되는 도구 11개
| 도구 | 용도 |
|---|---|
| `search_documents` | 하이브리드(dense+sparse RRF) 검색. **`document_id` 필수** — 문서별 컬렉션이라 어느 지침을 볼지 지정해야 한다. 누락 시 사용 가능한 목록을 담은 오류를 돌려준다. `fiscal_year`로 연도 한정 |
| `get_page_image` | 원본 페이지를 **실제 이미지(PNG)** 로 반환. 한 번에 최대 5쪽. 이미 렌더된 페이지는 캐시에서 꺼내므로 원본 PDF가 없어도 된다 |
| `ingest_pdf` | PDF 색인을 **백그라운드로 시작**하고 즉시 `job_id` 반환(비블로킹) |
| `ingest_status` | `ingest_pdf`가 준 `job_id`로 진행 상태 조회(`running`/`done`/`error`) |
| `get_chunk` | `chunk_id` 단건 조회 |
| `list_documents` | 색인된 문서 목록(연도·청크수·상태) |
| `delete_document` | 문서 삭제(`confirm=True`만 실제 실행) |
| `reindex_document` | 재색인(기본 `reparse=False`로 parsed 재사용) |
| `collection_status` | 컬렉션·연도별 문서수·차원·sparse 여부 |
| `set_source_path` | 원본 PDF를 옮겼을 때 기록된 경로만 갱신한다. PDF 내용이 바뀐 경우에는 `reindex_document(reparse=True)`를 사용한다 |
| `review_before_ingest` | **읽기 전용** 검토. 들어올 문서 id/연도(파일명 기반)와 현재 색인된 전체 목록을 함께 반환. 삭제·색인·매칭 없음 — 교체(replace-on-update) 워크플로우에서 사람이 직접 구버전 선별용 |

### 비동기 색인 워크플로우 (큰 PDF 필수)
314p급 PDF는 KURE 임베딩만으로 수 분이 걸려 **동기 색인은 클라이언트 4분 타임아웃**을
넘긴다. 그래서 `ingest_pdf`는 색인을 백그라운드 스레드로 던지고 즉시 `job_id`를 돌려준다.

1. `ingest_pdf(path=...)` → `{"ok": true, "job_id": "...", "status": "running"}` 즉시 반환.
2. `ingest_status(job_id)` 를 주기적으로 호출 → `status`가 `done`이면 `result`에 청크수,
   `error`면 `error`에 원인.
3. 동시 색인은 **1개만** 허용(Qdrant local 단일 writer). 진행 중 또 호출하면 거부되며
   현재 `job_id`를 알려준다.
4. job 상태는 인메모리 → 서버 재시작 시 사라지지만, 색인 결과 자체는 manifest에
   영구 저장되므로 `list_documents`로 최종 결과를 확인할 수 있다.

`ingest_pdf`는 전달한 PDF를 매번 새로 파싱한 뒤 색인한다. 색인 중에는 다른 변경 작업
(`ingest_pdf`, `ingest_chunks`, `delete_document`, `reindex_document`)이 실행되지 않고
`status="busy"` 응답을 반환하므로, 현재 작업이 끝난 뒤 다시 호출한다. 검색·조회 도구는
색인 중에도 계속 사용할 수 있다.

> 색인 중에도 `search_documents`·`list_documents` 등 다른 도구는 정상 응답한다(서버 도구는
> 이벤트 루프를 점유하지 않도록 워커 스레드로 오프로딩되며, Qdrant 접근은 락으로 직렬화됨).


### OCR 운영 메모
- 기본 `RAG_OCR=auto`는 전체 문서 OCR이 아니라 필요 구간 OCR이다.
- 스캔 PDF 의심 + `RAG_ODL_HYBRID` 설정 시에만 OpenDataLoader hybrid OCR 후보가 된다.
- 깨진 표/뭉친 표는 `needs_image=True` 청크의 page image만 Tesseract OCR로 보강한다.
- OCR 적용/skip 사유는 manifest와 검색 결과의 `meta.ocr`에 남는다.
- 로컬 Tesseract가 없으면 OCR은 건너뛰고 `page_image`만 유지된다. 실행 전 `uv run rag-mcp doctor`로 확인한다.
## 6. 주의사항 (중요)
- **파일락 / 단일 서버**: Qdrant local path 모드는 다중 프로세스 접근 불가.
  - Claude Desktop에서 서버가 떠 있는 동안(`serve`) **별도 터미널에서 `rag-mcp ingest`
    /`status`를 실행하지 말 것** → 파일락 충돌. 색인은 MCP의 `ingest_pdf` 도구로 하거나,
    서버를 잠깐 내리고 CLI로 한다.
  - **serve 인스턴스도 1개만.** 수동으로 `rag-mcp serve`를 또 띄우면 Claude Desktop이
    띄운 것과 충돌한다. 중복 의심 시 `Get-CimInstance Win32_Process | ? CommandLine -like '*rag-mcp*'`
    로 확인하고 여분을 종료.
- 설정 백업: 본 작업 시 `claude_desktop_config.json.bak` 생성됨(문제 시 복구용).

## 7. 문제 해결
- 도구가 안 보임 → Claude Desktop 완전 종료 후 재시작. config JSON 유효성 재확인.
- `uv`/모듈 못 찾음 → command가 uv.exe **절대경로**인지, `--directory` 경로가 맞는지.
- 검색 시 한글 깨짐 → cli는 stdout UTF-8 강제(처리됨). 클라이언트 측 표시 문제는 별개.
- 첫 검색이 느림 → 기동 직후에는 백그라운드 워밍업이 끝나기 전일 수 있다. 잠시 뒤 다시 시도.
- `search_documents`가 오류 → `document_id`를 지정했는지 확인. 문서별 컬렉션이라 필수다.
  오류 메시지에 사용 가능한 `document_id` 목록이 함께 온다.
- 검색 결과가 비어 있고 진단 정보만 옴 → 관련도 기준선(`RAG_MIN_DENSE_SCORE`)에 걸린 것.
  무관한 근거를 쓰지 않도록 막는 정상 동작이다. 값 조정은 [운영가이드.md](운영가이드.md) 참고.
- 페이지 이미지가 안 나옴 → 캐시에 없고 원본 PDF 경로도 끊긴 경우. 오류 메시지에 기대 경로가
  나오므로 그 자리에 PDF를 두면 된다.
