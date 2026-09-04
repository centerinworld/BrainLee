# Codex → Claude 핸드오프: 개별종목 주가 차트 자본행위 마커

작성일: 2026-07-12  
작업 경로: `/Applications/stock_dashboard`

## 1. 사용자 요청

액면분할·주식분할, 주식병합, 무상증자, 유상증자처럼 가격 시계열에 큰 단절을 만들 수 있는 자본행위를 개별 종목 주가 차트에 날짜별로 표시한다.

## 2. 구현 완료 내용

### 백엔드

- 파일: `main.py`
- API: `GET /api/dashboard/corporate-actions/{stock_code}?days=365`
- 위치: `main.py:1389`
- 조회 기간: 최소 30일, 최대 3,650일
- 데이터 소스:
  - `dart_disclosures`: 결정 공시와 권리락
  - `stock_base_info_changes`: KRX 상장주식수 실제 변경 기록
- 반환 유형:
  - `stock_split`: 주식분할·액면분할
  - `stock_merge`: 주식병합·액면병합
  - `bonus_issue`: 무상증자
  - `rights_issue`: 유상증자
  - `shares_change`: KRX 상장주식수 변경
- 응답 필드: `date`, `event_type`, `label`, `title`, `source`, `url`

### 중복·오탐 방지 규칙

- `[기재정정]`, `[첨부정정]`처럼 `[`로 시작하는 정정공시는 제외한다.
- 증자 청약결과·발행결과·1차 발행가액 결정은 제외한다.
- 최초 결정 공시와 권리락만 DART 차트 이벤트로 사용한다.
- 종속회사 자본행위는 해당 상장사 주가 이벤트에서 제외한다.
- 같은 날짜·같은 이벤트 유형은 한 번만 반환한다.
- 유무상증자 결정은 `rights_issue`와 `bonus_issue` 두 이벤트로 반환한다.

### 프론트엔드

- 파일: `frontend/src/App.jsx`
- 상태·API 조회: `App.jsx:14675`, `App.jsx:14703`
- 차트 마커 렌더링: `App.jsx:15787` 이후
- 차트에 세로 점선, 삼각 마커, 짧은 유형 라벨을 표시한다.
- 유형별 색상:
  - 분할: cyan
  - 병합: purple
  - 무상증자: green
  - 유상증자: orange
  - 상장주식수 변경: yellow
- 마우스 오버 시 날짜·공시명·출처를 툴팁으로 표시한다.
- DART URL이 있으면 마커 클릭 시 원문을 새 창으로 연다.
- 공시일이 비거래일이면 해당 날짜 이후 첫 거래일 위치에 마커를 배치한다.
- 현재 선택한 차트 기간 밖의 이벤트는 표시하지 않는다.

## 3. 검증 결과

- `python3 -m py_compile main.py`: 통과
- `frontend/npm run build`: 통과
- 프론트: `http://127.0.0.1:5173` HTTP 200
- 백엔드: `http://127.0.0.1:8000/docs` HTTP 200
- 백엔드 재시작 완료
- API 실데이터 확인:
  - `101730`: 유상증자 결정, 유상증자 권리락, 후속 유상증자 결정 반환 확인
  - `276730`: 2026-07-01 주식분할결정 반환 확인
- 정정공시와 청약·발행결과가 중복 마커로 나오지 않는 것을 확인했다.

## 4. Claude 재검증 요청

1. 분할·병합·무상증자·유상증자 사례를 유형별 최소 3종목씩 열어 마커 위치와 DART 원문을 대조한다.
2. `stock_base_info_changes`의 `shares_issued` 날짜가 실제 변경상장일인지 표본 검증한다.
3. 유무상증자 한 공시에 두 마커가 같은 위치로 표시될 때 라벨 충돌이 없는지 데스크톱·모바일에서 확인한다.
4. 3년·10년 차트에서 마커가 많은 종목의 라벨 밀집도를 확인한다.
5. 권리락일과 공시일을 구분해 툴팁 문구를 더 명확하게 만들 필요가 있는지 검토한다.
6. 기존 가격 데이터가 수정주가인지 비수정주가인지 종목별로 점검한다. 마커는 가격 단절의 설명이지만 가격 자체를 보정하지 않는다.

## 5. 남은 한계와 권장 후속 작업

- DART 목록의 `rcept_dt`는 공시일이며 실제 신주배정기준일·변경상장일과 다를 수 있다.
- 정확한 효력일을 얻으려면 DART 원문에서 다음 필드를 구조화해야 한다.
  - 신주배정기준일
  - 권리락일
  - 신주 상장예정일
  - 분할·병합 효력발생일
  - 변경상장일
- 권장 테이블:

```sql
CREATE TABLE corporate_action_events (
  stock_code TEXT NOT NULL,
  event_type TEXT NOT NULL,
  decision_date TEXT,
  ex_right_date TEXT,
  record_date TEXT,
  effective_date TEXT,
  listing_date TEXT,
  old_shares REAL,
  new_shares REAL,
  ratio REAL,
  rcept_no TEXT,
  source TEXT,
  confidence REAL,
  PRIMARY KEY (stock_code, event_type, rcept_no)
);
```

- 장기적으로는 DART 원문 파서와 KRX 변경상장 정보를 결합하고, 차트 마커는 `effective_date > ex_right_date > listing_date > decision_date` 우선순위로 배치하는 것이 바람직하다.
- 현재 마커의 목적은 가격 급변 원인 설명이다. 수정주가 산출·과거 OHLC 재보정은 별도 로직으로 구현해야 한다.

## 6. 작업 트리 주의사항

- 작업 시작 전부터 `frontend/src/App.jsx`, `main.py`를 포함한 다수 파일에 사용자·Claude 변경이 존재했다.
- 관련 없는 변경을 되돌리거나 파일 전체를 HEAD 기준으로 복원하지 말 것.
- 이 작업의 핵심 변경 범위는 `main.py`의 corporate-actions API와 `App.jsx`의 자본행위 조회·마커 렌더링 부분이다.

