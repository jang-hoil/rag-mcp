# PDF 페이지 정확성 독립 검토 기록

- 범위: 기준 `725682b`, 본문 수정 `62e6fdc`, 후속 PDF 이미지 캐시 변경.
- 검토자: 독립 `review_page_accuracy` 에이전트. 운영 자료·Qdrant 미접근, 코드 수정 및 전체 테스트 재실행 없음.
- 본문 페이지 경계와 변환/JSON 실패 시 캐시 보존에서 조치할 결함 없음.

## 검토에서 발견·해결한 경로

1. 새 PDF 파싱으로 캐시를 삭제한 직후, 새 manifest 저장 전 다른 인스턴스의 이미지 요청이 이전 PDF 경로로 PNG를 되살릴 수 있음. 임시 실제 PDF로 새 본문·새 manifest와 예전 PNG가 공존하는 순서를 재현함. 캐시 미스 렌더에 공용 OS 변경 잠금, 잠금 뒤 캐시·manifest 재조회, 이미지 바이트 읽기까지 잠금 유지로 해결.
2. 캐시 히트 확인 직후 삭제되는 PNG는 잠금 경로로 다시 확인하도록 처리. PyMuPDF 예외 래핑을 거치기 전 실제 파일 바이트를 읽어 파일 없음과 손상 이미지를 구분함.
3. Windows의 짧은 캐시 읽기 핸들과 삭제가 겹치면 WinError 32 발생. 실제 열린 PNG 파일로 재현하고 공유 위반만 총 0.1초 한도로 재시도. 일반 권한 오류·지속 점유는 전파.

최종 읽기 전용 재검토 결과: 추가로 조치할 결함이나 차단 사항 없음.

## 부모 에이전트의 검증

- 수정 전 기존 전체: 288 passed, 9 skipped.
- 본문 신규: RED 10 failed → 관련 36 passed, 1 skipped → 전체 298 passed, 9 skipped.
- 이미지 최초: RED 4 failed, 4 passed → 관련 31 passed, 5 skipped.
- 이미지 교차 실행: RED 3 failed, 1 passed. 실제 파일 삭제 경로 추가 RED 1 failed, Windows 열린 파일 공유 위반 RED 1 failed 확인 후 수정.
- 최종 이미지 신규: 15 passed.
- 최종 전체: **313 passed, 9 skipped**, 기존 Qdrant local payload-index 경고 1건.
- 합성 파싱 트리·임시 PDF·실제 PNG·임시 Qdrant를 사용함. 외부 OpenDataLoader 변환은 테스트에서 대체하므로 실제 JVM 변환을 새로 실행한 검증으로 해석하지 않음.

기존 운영 색인은 자동 수정하지 않음. 서버 재시작 후 해당 문서를 `reparse=True`로 재색인해야 저장된 본문 페이지와 교체 PDF 이미지에 반영됨.
