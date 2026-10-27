# PDF filename defaults implementation plan

**Goal:** 신규 PDF 색인의 기본 문서 ID와 표시 이름을 확장자 없는 PDF 파일명으로 통일한다.

**Architecture:** ID는 기존 서비스의 filename stem 기본값을 유지하고, 표시명은 공통 PDF 파이프라인에서 파일명 기본값을 명시한다. PDF 내부 제목은 회계연도 추출용으로 보존한다.

**Tech Stack:** Python, pathlib, pytest, 기존 fake 임베딩과 Qdrant local 테스트.

- [x] `tests/test_pdf_filename_defaults.py`에 기본값·명시적 인자·동기/백그라운드 전체 저장 및 검색 경로 테스트를 작성한다.
- [x] RED를 확인한다: `pytest tests/test_pdf_filename_defaults.py -o addopts='' -q --basetemp <새 격리 경로>` 결과 6 failed, 5 passed.
- [x] `pipeline.py`의 메타 추출 호출을 다음과 같이 변경하고 MCP 설명·CLI 도움말을 맞춘다.

```python
meta = extract_doc_meta(
    parsed, doc_name=doc_name or Path(pdf_path).stem, fiscal_year=fiscal_year,
)
```

- [x] 새 테스트 11건과 전체 pytest를 실행해 기본값과 기존 호환을 검증한다.
- [x] README·연동/운영 가이드와 PROGRESS에 기본값·검증 결과를 기록하고 독립 검토를 받는다.
- [x] 기존 사용자 변경을 제외한 이번 수정만 커밋한다.
