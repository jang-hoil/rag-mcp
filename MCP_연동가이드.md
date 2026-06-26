# RAG MCP — Claude Desktop 연동 가이드

회계·예산 지침서 RAG 서버를 **Claude Desktop**(및 동일 방식의 MCP 클라이언트)에
stdio 방식으로 등록하는 절차. 아래 내용은 실제 stdio 핸드셰이크로 검증됨
(`initialize` → `tools/list` 7개 → `collection_status` 정상 반환 확인).

## 1. 동작 방식 (전제)
- 서버는 `FastMCP("rag-mcp").run()` → **기본 transport = stdio**.
  클라이언트가 서버 프로세스를 직접 띄우고 stdin/stdout으로 JSON-RPC를 주고받는다.
- stdio에서 **stdout은 프로토콜 전용 채널**. 로그·진단 출력은 반드시 stderr로만
  (cli.py의 시작 메시지는 `file=sys.stderr`로 출력 — 의도된 안전 설계).
- 무거운 KURE 임베딩 모델은 lazy 로딩 → **서버 시작은 즉시**, 모델은 첫 검색 도구
  호출 때 로드(이미 `~/.cache/huggingface`에 캐시됨).

## 2. 사전 요구사항
- `uv` + Python 3.11 `.venv` 구성 완료 (`uv run pytest -q` 통과 상태).
- KURE-v1 모델 캐시 존재(없으면 첫 검색 시 다운로드 — 정부망은 truststore가 자동 처리).
- 실행 파일 절대경로(이 PC 기준):
  - uv: `C:\Users\Owner\AppData\Local\Programs\Python\Python313\Scripts\uv.exe`
  - 프로젝트: `C:\Users\Owner\Desktop\RAG MCP`

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
1. 재시작 후 채팅 입력창의 도구/플러그 아이콘에서 `rag-mcp` 도구 7개가 보이는지 확인.
2. CLI 사전 점검(서버가 stdio로 정상 응답하는지 코드로 확인):
   ```
   uv --directory "C:\Users\Owner\Desktop\RAG MCP" run rag-mcp status
   ```
   → 컬렉션 상태 JSON(points/dimension 등)이 나오면 정상.
3. JSON 유효성(편집 후 깨지면 모든 MCP 서버가 로드 실패):
   ```
   python -c "import json; json.load(open(r'%APPDATA%\Claude\claude_desktop_config.json', encoding='utf-8')); print('OK')"
   ```

## 5. 노출되는 도구 7개
| 도구 | 용도 |
|---|---|
| `search_documents` | 하이브리드(dense+sparse RRF) 검색. `fiscal_year`로 연도 한정 |
| `ingest_pdf` | PDF 색인. 연도·문서명 자동 추출 시도 |
| `get_chunk` | `chunk_id` 단건 조회 |
| `list_documents` | 색인된 문서 목록(연도·청크수·상태) |
| `delete_document` | 문서 삭제(`confirm=True`만 실제 실행) |
| `reindex_document` | 재색인(기본 `reparse=False`로 parsed 재사용) |
| `collection_status` | 컬렉션·연도별 문서수·차원·sparse 여부 |

## 6. 주의사항 (중요)
- **파일락**: Qdrant local path 모드는 다중 프로세스 접근 불가.
  Claude Desktop에서 서버가 떠 있는 동안(`serve`) **별도 터미널에서 `rag-mcp ingest`
  /`status`를 실행하지 말 것** → 파일락 충돌. 색인은 MCP의 `ingest_pdf` 도구로 하거나,
  서버를 잠깐 내리고 CLI로 한다.
- 설정 백업: 본 작업 시 `claude_desktop_config.json.bak` 생성됨(문제 시 복구용).

## 7. 문제 해결
- 도구가 안 보임 → Claude Desktop 완전 종료 후 재시작. config JSON 유효성 재확인.
- `uv`/모듈 못 찾음 → command가 uv.exe **절대경로**인지, `--directory` 경로가 맞는지.
- 검색 시 한글 깨짐 → cli는 stdout UTF-8 강제(처리됨). 클라이언트 측 표시 문제는 별개.
- 첫 검색이 느림 → KURE 모델 lazy 로딩(최초 1회). 이후 캐시로 빨라짐.
