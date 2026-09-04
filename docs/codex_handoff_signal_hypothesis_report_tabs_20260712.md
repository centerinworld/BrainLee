# Codex → Claude 핸드오프: 시그널 방향성 분석 가설 리포트 탭

작성일: 2026-07-12

## 사용자 요구

- 분석한 가설과 실제 백테스트 결과를 `시그널 방향성 분석` 페이지에 리포트 형태로 표시한다.
- 연구가 추가될 때마다 같은 페이지에 별도 탭을 계속 추가한다.
- 한 탭을 선택하면 그 리포트만 표시한다.

## 구현 내용

### 보고서 레지스트리 API

- 파일: `main.py`
- API: `GET /api/dashboard/hypothesis-reports`
- 현재 등록 리포트: `deep_drawdown_recovery_5y`
- 원본 데이터: `research_outputs/deep_drawdown_recovery_5y/summary.json`
- 원본 본문: `research_outputs/deep_drawdown_recovery_5y/report.md`
- API는 JSON 파일을 요청 시 읽으므로 연구 스크립트를 재실행하면 프론트 수치도 자동 갱신된다.
- 반환 계약:
  - `id`, `title`, `short_title`
  - `hypothesis`
  - `verdict`, `verdict_label`
  - `updated_at`
  - `summary`
  - `methodology`
  - `report_path`

### 프론트엔드

- 파일: `frontend/src/views/SignalImpactView.jsx`
- 기존 전체 리포트 탭: `signal_event_study`
- 신규 탭: `deep_drawdown_recovery_5y`
- API의 `reports` 배열을 순회해 탭을 자동 생성한다.
- 탭을 선택하면 선택한 리포트만 표시한다.
- 낙폭과대 리포트 구성:
  - 가설 및 기각 판정
  - 표본·252일 수익·플러스 비율·추가 하락 등 핵심 카드
  - 낙폭 구간별 실제 성과 테이블
  - 반등 확인 시 동반 신호별 성과
  - 실전 판정과 제외 조건
- PDF 저장은 현재 선택된 탭의 `reportRef` 영역을 대상으로 한다.

## 현재 연구 결론

- 7,965건 / 3,177종목
- 진입 후 252거래일 중앙수익 `-4.23%`
- 252거래일 플러스 비율 `43.76%`
- 종목당 첫 사건만 사용 시 중앙수익 `-8.67%`
- 저점 낙폭 `-80% 이하`의 252일 플러스 비율 `9.8%`
- 결론: 낙폭만으로 매수하는 가설 기각

## 다음 리포트 추가 방법

1. `research_outputs/<report_id>/summary.json`과 필요 시 `report.md`를 생성한다.
2. `main.py`의 hypothesis report registry에 새 report object를 추가한다.
3. 공통 카드·표 계약으로 표현 가능하면 기존 컴포넌트를 재사용한다.
4. 고유 차트가 필요할 때만 리포트별 컴포넌트를 추가한다.
5. `id`는 폴더명과 동일하게 유지한다.
6. `verdict`는 최소 `supported`, `rejected`, `inconclusive` 중 하나로 통일한다.

## Claude 재검증 요청

1. `시그널 이벤트 연구`와 `낙폭과대 회복` 탭의 내용이 동시에 노출되지 않는지 확인한다.
2. 모바일에서 7열 낙폭 구간 테이블이 가로 스크롤되고 페이지 폭을 깨뜨리지 않는지 확인한다.
3. PDF 저장 시 현재 선택 탭만 인쇄되는지 확인한다.
4. `summary.json` 재생성 후 새 수치가 새로고침만으로 반영되는지 확인한다.
5. 향후 리포트가 5개 이상일 때 탭 줄바꿈과 가독성을 점검한다.

## 검증 결과

- `python3 -m py_compile main.py`: 통과
- `npm run build`: 통과
- `GET /api/dashboard/hypothesis-reports`: HTTP 200
- 응답 검증: report id, 가설 기각 판정, 7,965건, 플러스 비율 43.76%, 낙폭 구간 7개 확인
- 프론트 `http://127.0.0.1:5173`: HTTP 200
- 백엔드 재시작 완료

## 주의사항

- 작업 트리에는 기존 사용자·Claude 변경이 다수 존재한다. 관련 없는 변경을 되돌리지 말 것.
- 현재 API 레지스트리는 첫 리포트만 등록되어 있다. 새 연구 산출물의 스키마가 다르면 무리하게 같은 표에 끼워 넣지 말고 전용 뷰를 추가한다.
- 30% 반등률은 투자 승률이 아니므로 프론트에서도 이를 구분해 표시한다.

