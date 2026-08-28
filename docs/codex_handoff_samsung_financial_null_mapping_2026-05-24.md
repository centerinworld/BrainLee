# Codex Handoff — Samsung Financial Table Gaps & Mapping Issues (2026-05-24)

## 1) 사용자 이슈 재현
- 페이지: 개별종목 > 삼성전자(005930) > 연간/분기 재무표
- 증상:
  - 2016/2017 연간에서 `자산/부채/자본/자본금/EPS` 공란
  - 분기 일부(특히 Q4) 동일 필드 공란
  - 일부 연도에서 자산/부채/자본 값의 관계가 비정상

## 2) DB 확인 결과 (CFS 기준)
- 테이블: `financial_data`
- 핵심 확인:
  - 삼성전자 연간 2016/2017 CFS 레코드(`data_source=dart_redownload`)는
    `total_assets/total_liabilities/total_equity/capital_stock/eps`가 NULL
  - 삼성전자 분기 2016Q4, 2017Q4, 2018Q4는 위 필드가 NULL
  - 2019~2021 다수 행에서
    - `total_assets == total_liabilities` (비정상)
    - 또는 `total_assets == total_equity` (비정상)

## 3) 전체 품질 지표 (CFS)
- annual (`is_annual=1`):
  - 총 19,641행
  - `total_assets == total_liabilities`: 2,183행
  - `total_assets == total_equity`: 2,803행
- quarter (`is_annual=0`):
  - 총 89,827행
  - `total_assets == total_liabilities`: 17,780행
  - `total_assets == total_equity`: 21,179행

이 값들은 단순 일부 종목 이슈가 아니라 **대규모 매핑 오류/원천 결측 혼재**를 시사.

## 4) 이것이 이전 Codex 검토에서 있었던 사항인가?
- **부분적으로는 예, 이번에 더 구체적으로 확인됨**
  - 과거 핸드오프에서 `source NULL`, `Q4 음수`, `커버리지 결손`은 반복 보고됨
  - 하지만 이번처럼 `자산=부채`/`자산=자본` 대량 패턴은 삼성 예시를 통해 더 명확히 식별됨

## 5) 클로드 작업 우선순위 (권장)
1. **매핑 검증 우선**
   - DART raw 계정 매핑에서 `total_assets/total_liabilities/total_equity` 매핑키 재검증
   - `자산 = 부채 + 자본` 회계 항등식 검사 추가(저장 전)
2. **연간/분기 결측 보강 규칙 분리**
   - 연간 필드 결측 시: 동년 Q4(또는 가장 최근 분기)에서 안전 보강
   - 단, 보강값은 `data_source`에 `derived_*`로 명시
3. **Q4 특이치/공란 재수집 루틴 분리**
   - 2016~2018 구간 재수집 큐(삼성 포함) 별도 구성
4. **프론트 표시 보호장치**
   - 연간에 공란이 많은 연도는 `⚠ 원천결측` 뱃지 표기
   - 잘못된 항등식(`자산 != 부채+자본`)이면 `신뢰도 하락` 표시

## 6) 즉시 실행 SQL (재현용)
```sql
-- 삼성전자 연간/분기 결측 확인
SELECT year, quarter, is_annual, report_type,
       total_assets, total_liabilities, total_equity, capital_stock, eps, data_source
FROM financial_data
WHERE stock_code='005930' AND report_type='CFS'
ORDER BY is_annual DESC, year, quarter;

-- 전체 CFS에서 항등식 이상/동치 이상치 개수
SELECT is_annual,
       COUNT(*) AS n,
       SUM(CASE WHEN total_assets IS NOT NULL AND total_liabilities IS NOT NULL
                 AND ABS(total_assets-total_liabilities)<1 THEN 1 ELSE 0 END) AS assets_eq_liab,
       SUM(CASE WHEN total_assets IS NOT NULL AND total_equity IS NOT NULL
                 AND ABS(total_assets-total_equity)<1 THEN 1 ELSE 0 END) AS assets_eq_equity
FROM financial_data
WHERE report_type='CFS'
GROUP BY is_annual;
```
