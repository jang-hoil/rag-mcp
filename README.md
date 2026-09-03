# RAG MCP

**지침서 PDF를 내 PC에 넣어두고, Claude에게 말로 물어보면 근거 문단과 페이지를 찾아주는 도구입니다.**

```
나:     재산등록 의무자 범위 알려줘

Claude: 2026년 공직윤리 실무과정 351쪽에 이렇게 나와 있습니다.
        "..."
        (목차 위치: 7. 퇴직공직자 행위제한제도 > 재산등록 > 제2장 신고서관리)
```

---

## 왜 필요한가

**지침서는 두껍고, Ctrl+F로는 잘 안 찾아집니다.**
수백 쪽짜리 회계·감사·공직윤리 지침에서 조항을 찾으려면 문서에 적힌 단어를 **정확히 똑같이** 입력해야 합니다. "출장비"로 검색하면 "여비"라고 쓰인 조항은 안 나옵니다. 이 도구는 **뜻이 비슷한 문장까지** 찾습니다.

**챗봇에 PDF를 통째로 올리는 건 한계가 있습니다.**
분량이 크면 잘리고, 여러 지침을 놓고 비교하기 어렵고, 매번 다시 올려야 합니다. 이 도구는 **한 번 색인해두면** 이후에는 질문만 하면 됩니다.

**문서가 밖으로 나가지 않습니다.**
색인과 검색이 **전부 내 PC 안에서** 돌아갑니다. API 키가 필요 없고, 모델을 한 번 받은 뒤에는 인터넷도 필요 없습니다. 대외비 지침을 외부 서비스에 올릴 수 없는 환경을 위해 이렇게 만들었습니다.

**답의 근거를 확인할 수 있습니다.**
몇 쪽인지, 목차 어디에 속하는지 함께 알려줍니다. 원본 페이지를 그림으로 띄워 눈으로 확인할 수도 있습니다.

---

## 무엇을 할 수 있나

| | |
|---|---|
| **말로 검색** | "일상경비 한도 찾아줘" — 정확한 단어를 몰라도 됩니다 |
| **근거 표시** | "근거 페이지도 알려줘"라고 하면 몇 쪽인지, 목차 어디인지 표시합니다 |
| **원본 페이지 보기** | 해당 쪽을 그림으로 띄워 확인이 가능합니다 |
| **표 보존** | 표를 중간에 자르지 않고 통째로 다룹니다 |
| **문서별 검색** | 지침 하나만 지정해 찾습니다. 다른 문서가 섞이지 않습니다 |
| **스캔 문서 대응** | 글자가 그림으로 된 페이지는 OCR로 읽습니다 |

---

## 설치 (Windows)

명령을 **한 줄씩** 복사해 실행하세요.

### 1. 도구 3개 설치

**cmd 창**을 엽니다. 윈도우 키를 누르고 `cmd`를 입력한 뒤 Enter를 치면 검은 창이 뜹니다.

> 이 문서의 명령은 **cmd 기준**입니다. PowerShell(파란 창)에서는 일부 명령이 동작하지 않습니다.

여기에 아래를 한 줄씩 붙여넣으세요.

```bat
winget install --id Git.Git -e
winget install --id Python.Python.3.11 -e
winget install --id astral-sh.uv -e
```

> 끝나면 **cmd 창을 닫고 새로 여세요.** 그래야 방금 설치한 명령을 인식합니다.

### 2. 코드 받기

```bat
cd /d "%USERPROFILE%\Desktop"
git clone https://github.com/jang-hoil/rag-mcp.git "RAG MCP"
cd "RAG MCP"
```

바탕화면에 **`RAG MCP` 폴더가 자동으로 생깁니다.** 미리 만들 필요 없습니다.

### 3. 설치

```bat
uv sync
```

이 한 줄이 전부입니다. 처음에는 검색용 모델(약 2GB)을 받느라 몇 분 걸립니다.

### 4. PDF 넣기

받은 폴더 안에 **`pdf` 폴더가 이미 들어 있습니다.** 여기에 지침서를 넣으세요.

```
RAG MCP\
├─ pdf\      ← 여기에 PDF를 넣습니다
├─ data\     ← 색인 결과가 쌓입니다 (자동 생성)
└─ src\
```

다른 폴더에 둬도 동작하지만 **여기를 권합니다.** 색인할 때 PDF 경로가 기록되어서, 나중에 파일을 옮기면 재색인과 페이지 보기가 안 됩니다. PDF 파일은 GitHub에 올라가지 않습니다.

### 5. Claude Desktop에 연결

설정에 넣을 경로가 **두 개** 필요합니다. 2번에서 쓰던 cmd 창에서 확인하세요.

**첫 번째 — uv.exe 경로**

```bat
where uv
```

화면에 나온 줄이 그 경로입니다.

```text
C:\Users\Owner\AppData\Local\Programs\Python\Python311\Scripts\uv.exe
```

**두 번째 — RAG MCP 폴더 경로**

명령을 칠 필요도 없습니다. **커서 왼쪽에 이미 적혀 있습니다.**

```text
C:\Users\Owner\Desktop\RAG MCP>
└────────── 이 부분 ──────────┘
```

2번에서 `cd "RAG MCP"`로 이 폴더에 들어왔기 때문입니다. `>` 앞까지를 복사하세요.

> 창을 닫았다면 다시 열고 아래를 실행하면 됩니다.
> ```bat
> cd /d "%USERPROFILE%\Desktop\RAG MCP"
> ```
> 파일 탐색기 주소창을 클릭해 복사해도 됩니다.

이제 Claude Desktop → **Settings → Developer → Edit Config**를 열고 아래를 붙여넣습니다. **경로 두 곳만** 방금 확인한 값으로 바꾸세요.

```json
{
  "mcpServers": {
    "rag-mcp": {
      "command": "여기에 where uv 결과를 붙여넣기",
      "args": [
        "--directory",
        "여기에 cd 결과를 붙여넣기",
        "run",
        "rag-mcp",
        "serve"
      ]
    }
  }
}
```

> **주의:** JSON에서는 `\`를 **두 번** 씁니다.
> `C:\Users\Owner\Desktop\RAG MCP` → `C:\\Users\\Owner\\Desktop\\RAG MCP`

### 6. Claude Desktop 껐다 켜기 → 끝

완전히 종료한 뒤 다시 실행하고 이렇게 물어보세요.

```
collection_status 실행해서 상태 확인해줘
```

<details>
<summary>Codex · Claude Code에서 쓰려면</summary>

**Codex** — `~/.codex/config.toml`

```toml
[mcp_servers.rag_mcp]
command = 'C:\Users\Owner\Desktop\RAG MCP\.venv\Scripts\rag-mcp.exe'
args = ["serve"]
startup_timeout_sec = 120
```

**Claude Code** — 프로젝트 폴더에서

```bat
claude mcp add rag-mcp -- "C:\Users\Owner\Desktop\RAG MCP\.venv\Scripts\rag-mcp.exe" serve
```

</details>

---

## Codex와 Claude에서 동시에 사용하기

Qdrant local-path 모드는 한 프로세스만 `data\qdrant`를 열 수 있습니다. Codex와 Claude
Desktop이 각각 `rag-mcp serve`를 실행하면서 같은 색인을 사용하려면 Qdrant를 localhost
HTTP 서버로 실행합니다. MCP 연결 자체는 계속 stdio이며 도구 11개의 사용법도 바뀌지 않습니다.

### 고정 운영값

| 항목 | 값 |
|---|---|
| Qdrant 실행파일 | `D:\qdrant\qdrant.exe` |
| 설정파일 | `D:\qdrant\config\production.yaml` |
| 서버 저장소 | `D:\qdrant\storage` |
| 접속 URL | `http://127.0.0.1:6333` |
| 작업 스케줄러 | `rag-mcp-qdrant` |

서버 상태는 다음 명령으로 확인합니다.

```powershell
Invoke-RestMethod 'http://127.0.0.1:6333/healthz'
```

### 기존 local 색인 이관

`data\qdrant`를 `D:\qdrant\storage`로 파일 복사하면 안 됩니다. 두 저장 방식의 디스크
포맷이 다르므로 서버를 먼저 실행한 다음 포인트를 Qdrant API로 복사합니다. 이관하는 동안에는
원본 파일락이 풀리도록 모든 `rag-mcp serve` 프로세스를 종료해야 합니다.

먼저 쓰기가 없는 dry-run을 실행합니다.

```powershell
uv run python scripts/migrate_local_to_server.py `
  --source-path 'D:\User\Desktop\rag mcp\data\qdrant' `
  --server-storage-path 'D:\qdrant\storage' `
  --target-url 'http://127.0.0.1:6333'
```

컬렉션 이름·포인트 수·dense/sparse 스키마를 확인한 뒤 실제 복사를 실행합니다.

```powershell
uv run python scripts/migrate_local_to_server.py `
  --source-path 'D:\User\Desktop\rag mcp\data\qdrant' `
  --server-storage-path 'D:\qdrant\storage' `
  --target-url 'http://127.0.0.1:6333' `
  --apply
```

스크립트는 포인트 ID, dense/sparse 벡터와 payload를 그대로 옮기며 재임베딩하지 않습니다.
대상 컬렉션이 비어 있지 않거나 최종 포인트 수가 다르면 오류로 중단합니다.

server 모드에서는 sparse IDF가 실제 적용됩니다(local 모드에서는 무시됨). 따라서 같은 질문도
sparse 점수와 상위 결과 순서가 달라질 수 있으며, 이는 데이터 손상이 아니라 검색 계산 방식이
정상적으로 달라진 결과입니다.

### MCP 설정

Codex의 `~/.codex/config.toml`에서는 `[mcp_servers.rag_mcp.env]`에 다음 값을 둡니다.

```toml
RAG_QDRANT_MODE = "server"
RAG_QDRANT_URL = "http://127.0.0.1:6333"
```

Claude Desktop의 `mcpServers["rag-mcp"]`에는 다음 `env`를 병합합니다.

```json
"env": {
  "RAG_QDRANT_MODE": "server",
  "RAG_QDRANT_URL": "http://127.0.0.1:6333"
}
```

Microsoft Store판 Claude는 설정 파일이 `%APPDATA%`가 아니라
`%LOCALAPPDATA%\Packages\Claude_<패키지ID>\LocalCache\Roaming\Claude` 아래에 있을 수
있습니다. Claude의 **Settings → Developer → Edit Config**로 열린 파일을 기준으로 수정합니다.

설정 변경 후 Codex와 Claude Desktop을 완전히 종료했다 다시 실행합니다. 양쪽에서
`list_documents`와 `collection_status` 결과가 같고 동시에 검색되는지 확인합니다.

### 자동 실행과 롤백

예약 작업 `rag-mcp-qdrant`는 `qdrant.exe`를 직접 실행하고 `IgnoreNew`로 중복 기동을
차단합니다. PowerShell 래퍼를 두면 작업 중지 시 자식 Qdrant가 남을 수 있으므로 사용하지
않습니다.

```powershell
Start-ScheduledTask -TaskName 'rag-mcp-qdrant'
Stop-ScheduledTask -TaskName 'rag-mcp-qdrant'
```

문제가 생기면 Codex·Claude 설정 백업을 복원하고 `RAG_QDRANT_MODE`를 `local`로 되돌리거나
제거한 뒤 `RAG_QDRANT_URL`을 제거합니다. `rag-mcp-qdrant` 작업을 중지하면 보존된
`data\qdrant`로 즉시 돌아갈 수 있습니다. local 모드에서는 MCP 서버를 하나만 실행합니다.

---

## 사용하기

### 색인하기

```
pdf 폴더의 2026년 공직윤리 실무과정.pdf 를 색인해줘
```

큰 PDF는 시간이 걸립니다. 색인을 시작하면 **쪽수와 대략적인 예상 소요시간**을 함께 알려줍니다.

```
'2026년 공직윤리 실무과정' 색인을 시작했습니다(340쪽, 대략 17분 예상).
파싱 → 임베딩 → 저장 순으로 진행됩니다.
```

진행 상황을 물으면 지금 어느 단계인지, 얼마나 됐는지 알려줍니다.

```
방금 그 job_id로 진행 상태 확인해줘
→ 임베딩 진행 중 320/1,064 (30%) — 경과 4분 5초
```

예상 시간은 쪽당 약 3초로 잡은 **어림값**입니다(340쪽 문서 실측 기준). 표가 많거나 스캔
문서면 더 걸립니다.

### 검색하기

문서를 지정해 물어봅니다.

```
2026년 공직윤리 실무과정에서 재산등록 의무자 범위를 찾아줘
```

문서 이름이 기억나지 않으면:

```
색인된 문서 목록 보여줘
```

### 원본 페이지 보기

```
그 문서 351쪽을 이미지로 보여줘
```

### 원본 PDF를 옮겼을 때

색인해둔 PDF를 다른 폴더로 옮기면 **검색은 그대로 되는데** 아직 캐시되지 않은 페이지
이미지만 못 엽니다. 이때는 재색인(수십 분) 대신 경로만 고치면 됩니다.

```
그 문서 원본 경로를 D:\User\Downloads\2026년 공직윤리 실무과정.pdf 로 바꿔줘
```

쪽수가 색인 당시와 다르면 다른 문서일 수 있어 거부합니다. 같은 문서가 맞으면 `force=True`로
넘길 수 있습니다. **PDF 내용 자체가 바뀐 경우**에는 경로 수정이 아니라 재색인이 필요합니다.

### 삭제하기

```
이 문서 삭제해줘, confirm=True로
```

실수를 막기 위해 `confirm=True`를 함께 말해야 실행됩니다.

---

## 어떻게 동작하나

```
PDF ─► 문단·표 단위로 잘라 저장 ─► 질문 ─► 비슷한 뜻 + 같은 단어 둘 다 검색 ─► 근거 문단
```

- **두 방식으로 동시에 찾습니다.** 뜻이 비슷한 문장을 찾는 방식과 같은 단어를 찾는 방식을 함께 쓰고 결과를 합칩니다. 한쪽만 쓰면 놓치는 게 생깁니다.
- **한국어에 맞춰져 있습니다.** 한국어 형태소 분석기(Kiwi)와 한국어 임베딩 모델(KURE)을 씁니다.
- **문서마다 따로 보관합니다.** 지침 A를 검색할 때 지침 B의 단어 빈도가 영향을 주지 않습니다.
- **서버 로그는 파일로 남습니다.** 기동 검사까지는 화면(stderr)에 나오고, 그 뒤로는
  `data/logs/server.log`에 기록합니다. 로그를 화면으로 계속 내보내면, 이를 읽어가지 않는
  클라이언트를 만났을 때 출력 버퍼가 차면서 서버가 통째로 멈춥니다(실측: 25번째 호출).
- **데이터는 항상 프로젝트 폴더 안에 둡니다.** 색인·파싱 결과는 `pyproject.toml`이 있는
  폴더의 `data/`에 저장합니다. 서버를 **어느 폴더에서 켜든 같은 곳**을 보므로, 실행 위치에
  따라 문서가 사라진 것처럼 보이는 일이 없습니다. 다른 위치에 두려면 `RAG_DATA_DIR`
  환경변수에 절대경로를 지정하면 그쪽이 우선합니다.

---

## 문제가 생기면

| 증상 | 확인할 것 |
|---|---|
| 도구가 안 보임 | Claude Desktop을 **완전히 종료**했다 다시 켰는지 |
| 경로 오류 | JSON에서 `\`를 두 번 썼는지 |
| 색인이 안 끝남 | 큰 PDF는 수십 분 걸립니다. `ingest_status`로 단계·진척률 확인 |
| 도구 하나가 무응답 | 서버 로그(`data/logs/server.log`)를 확인 |
| 두 MCP 앱을 같이 쓰면 파일락 오류 | `RAG_QDRANT_MODE=server`와 Qdrant `/healthz` 확인 |
| 검색 결과가 이상함 | 문서를 지정했는지 |

더 자세한 내용:

- [운영가이드.md](운영가이드.md) — 환경 변수, 운영 주의사항, 전체 구조, 도구 10개 상세
- [MCP_연동가이드.md](MCP_연동가이드.md) — 연동 상세와 문제 해결

---

## 요구 사항

| | |
|---|---|
| OS | Windows (Linux·macOS도 동작) |
| Python | 3.11 |
| 디스크 | 모델 약 2GB + 문서당 수십 MB |
| 메모리 | 4GB 이상 권장 |
| 인터넷 | 최초 설치 때만 |

## 라이선스

MIT
