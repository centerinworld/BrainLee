# Stock Detail 검증패널 고도화 핸드오프 (2026-05-30)

## 1) 목적
개별종목 페이지의 데이터품질 패널에 **종목별 실제 검증 결과(연도/항목 단위)**가 직접 보이도록 확장.

## 2) 수정 파일
- /Applications/stock_dashboard/main.py
- /Applications/stock_dashboard/frontend/src/App.jsx

## 3) 백엔드 변경사항 (`/api/dashboard/data-quality/{stock_code}`)
기존 등급/아이템 응답에 아래 필드 추가:

- `verification_summary_lines`: 종목 요약 문장
  - 예: `연간 완전검증: 2025, 2024, 2023, 2022`
  - 예: `DART+수식검증: 2021, 2020, 2019, ...`
- `year_item_summary`: 연도별 상태 배열
  - `year`, `source`, `pl_ok`, `bs_ok`, `check_basis`, `note`
- `recent_fixes`: 최근 보정 이력
  - `write_gate_log` + `financial_fix_log` + `cashflow_fix_log` 통합
  - 필드: `ts`, `target`, `reason`

추가 로직:
- 연간 row는 CFS 우선 사용
- P/L(매출/영업이익/순이익) non-null 여부 체크
- B/S 등식 `A-L=E` 수식검증 포함
- 소스분류: `DART`, `DART+FnGuide`, `DART+FnGuide+Naver`

## 4) 프론트 변경사항
데이터품질 패널 상세보기(`dqExpanded`) 상단에 3개 블록 추가:

1. **종목별 검증 요약** (문장형)
2. **연도별 검증표**
   - 연도 / 소스 / 매출·영업·순익 / 자산·부채·자본 등식 / 검증기준
3. **최근 자동/수동 보정 로그**
   - 최신 5건 표시

## 5) 검증 결과 샘플
### ALT (172670)
- 등급: `A`
- 요약: `연간 완전검증: 2025, 2024, 2023, 2022`
- 최근 보정 이력에 2026Q1 CFS `total_equity` 보정 로그 표시됨

### 삼성전자 (005930)
- 등급: `A`
- 요약:
  - `연간 완전검증: 2025, 2024, 2023, 2022`
  - `DART+수식검증: 2021, 2020, 2019, 2018, 2017, 2016`
- 최근 gate/fix 로그 노출 정상

## 6) 실행 검증
- `python3 -m py_compile main.py` 통과
- `npm run build` 통과
- venv python으로 `get_data_quality('172670')`, `get_data_quality('005930')` 호출 확인 완료

## 7) 클로드 재검증 요청 포인트
1. UI 문구 가독성(연도별 표 컬럼명/순서) 미세조정
2. `recent_fixes` reason code를 사용자 친화 문장으로 매핑할지 여부
3. 종목별 `year_item_summary`에 분기(2026Q1 등)도 별도 탭으로 노출할지 검토
