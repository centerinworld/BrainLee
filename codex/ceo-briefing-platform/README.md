# CEO Briefing Platform

프로토타입 다음 단계로 만든 실행형 구조입니다.

## 폴더 구성

- `backend`: FastAPI API 서버
- `frontend`: 백엔드와 연결되는 브라우저 UI
- `local-console`: 페이지#3, 페이지#4용 로컬 선별 콘솔
- `docs`: 오프라인 우선 작업 문서

## 1. Python 설치

권장:

- Python 3.11+

설치 후 확인:

```powershell
python --version
```

참고:

- 2026-04-07 기준 설치에 사용된 Python 3.11.9 Windows 설치 파일 다운로드 크기는 약 `25.0 MB`였습니다.

## 2. 백엔드 실행

```powershell
cd C:\Users\LEE\Documents\codex\ceo-briefing-platform\backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

기본 주소:

- API: `http://127.0.0.1:8000`
- Swagger: `http://127.0.0.1:8000/docs`

## 3. 프론트엔드 실행

현재 프론트엔드는 정적 파일이라 별도 Node.js 없이도 열 수 있습니다.

```powershell
start C:\Users\LEE\Documents\codex\ceo-briefing-platform\frontend\index.html
```

브라우저에서 상단 API 주소를 `http://127.0.0.1:8000`로 두고 사용하면 됩니다.

## 4. 로컬 선별 콘솔 실행

설치 없이 바로 열 수 있습니다.

```powershell
start C:\Users\LEE\Documents\codex\ceo-briefing-platform\local-console\company-news.html
start C:\Users\LEE\Documents\codex\ceo-briefing-platform\local-console\competitor-watch.html
```

용도:

- 페이지#3, 페이지#4의 로컬 프론트엔드 선별 화면
- 파싱 결과 후보 중 APP으로 전달할 항목만 선택
- 현재는 `localStorage` 기반 오프라인 흐름으로 동작

## 현재 구현 범위

- 역할별 페이지 접근 제어 API
- CEO 일정 / 회사 주요일정 API
- 수정 요청 등록 / 승인 / 반려 API
- 기사 / 경쟁사 동향의 대기열 / 게시 API
- 확장 가능한 페이지 레지스트리 UI
- SQLite DB 초기화 스크립트
- RSS/Atom 파싱 준비 스크립트
- 샘플 XML 피드 기반 오프라인 파싱 테스트

## DB 및 파싱 준비

추가 패키지 없이 Python 표준 라이브러리만으로 먼저 준비했습니다.

DB 초기화:

```powershell
C:\Users\LEE\AppData\Local\Programs\Python\Python311\python.exe C:\Users\LEE\Documents\codex\ceo-briefing-platform\scripts\init_db.py
```

샘플 RSS 파싱:

```powershell
C:\Users\LEE\AppData\Local\Programs\Python\Python311\python.exe C:\Users\LEE\Documents\codex\ceo-briefing-platform\scripts\fetch_rss.py
```

DB 확인:

```powershell
C:\Users\LEE\AppData\Local\Programs\Python\Python311\python.exe C:\Users\LEE\Documents\codex\ceo-briefing-platform\scripts\inspect_db.py
```

생성 파일:

- `data/ceo_briefing.db`

설정 파일:

- `backend/config/rss_sources.json`
- `.env.example`

## 다음 확장 추천

1. Google Calendar API OAuth 연동
2. RSS / Python 스크래퍼 워커 분리
3. SQLite 또는 PostgreSQL 저장
4. 사용자 로그인과 관리자 승인 이력 저장
5. React 또는 Next.js로 프론트엔드 고도화
