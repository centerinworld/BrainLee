# CEO Briefing Hub MVP

설치 없이 브라우저에서 바로 열어볼 수 있는 로컬 프로토타입입니다.

## 실행 방법

1. `C:\Users\LEE\Documents\codex\ceo-briefing-app\index.html` 파일을 브라우저로 엽니다.
2. 좌측 역할 선택에서 `CEO`, `관리자`, `실무자`를 바꿔가며 권한별 페이지 노출을 확인합니다.
3. `Mock Sync`, 승인 버튼, APP 전송 버튼으로 동작 흐름을 테스트합니다.

## 현재 포함된 기능

- 역할별 페이지 접근 제어
- 페이지별 테이블/정보 노출 제어
- 페이지#1 CEO 일정 화면
- 페이지#2 회사 주요일정 + 수정 요청 승인 흐름
- 페이지#3 회사 주요 기사 선별/전송 화면
- 페이지#4 타회사 동향 선별/전송 화면
- 페이지 추가를 고려한 페이지 레지스트리 구조

## 아직 실제 연동되지 않은 부분

- Google Calendar 실시간 읽기/쓰기
- RSS / Python / AI 기반 실제 수집기
- 관리자 승인 API / 사용자 인증
- DB 저장

## 권장 다음 단계

실운영 앱으로 가려면 아래 설치가 필요합니다.

- Node.js LTS
- Python 3.11+

이후 추천 스택:

- Frontend: React + Vite
- Backend: FastAPI
- Storage: SQLite 또는 PostgreSQL
- Sync: Google Calendar API
- Parsing: Python RSS/Scraper Worker
