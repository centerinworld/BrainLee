# 퀀트 주요지표 근본 소스 및 기준 핸드오프 (Codex → Claude)

- 작성일: 2026-06-14
- 대상 DB: `/Applications/stock_dashboard/stock.db`
- 핵심 코드: `/Applications/stock_dashboard/scripts/ops/sync_quant_major_indicators.py`
- 운영 래퍼: `/Applications/stock_dashboard/scripts/ops/quant_indicators_cron.py`
- 전체 인벤토리 CSV: `/Applications/stock_dashboard/scratch/epic/quant_major_indicator_inventory_20260607.csv`
- 현재 상태: new_collector_needed 1, ready_existing 89, ready_existing_partial 45
- 적재 시계열: 106,445 rows / 140 indicator keys / latest update 2026-06-14 15:26:39

## 1. Claude 검토 결론부터

현재 퀀트 주요지표는 EPIC 스타일의 메뉴/지표명과 공개 보강 지표를 기준으로 `quant_major_indicator_catalog`에 135개를 관리한다. 이 중 89개는 공식 원천 또는 명확한 산식으로 연결된 `ready_existing`, 45개는 공개 원천이 있으나 EPIC 원지표와 완전히 같지 않은 `ready_existing_partial`, 1개는 무료 공개 exact 원천을 찾지 못한 `new_collector_needed`다.

가장 중요한 기준은 “연결했다”와 “정확히 같은 지표다”를 분리하는 것이다. 부분 연결 지표는 화면과 분석에서 반드시 proxy/partial로 표시해야 하며, 투자 로직에서 exact 지표처럼 쓰면 안 된다.

## 2. 상태 정의

| status | 의미 | 투자/화면 사용 기준 |
| --- | --- | --- |
| ready_existing | 공식 원천 exact 또는 공식 원천 값에서 명확한 산식으로 파생된 지표 | 투자 참고 가능. 단 source_detail과 기준일 표시 필수 |
| ready_existing_partial | 공식 또는 공개 원천이지만 EPIC 원지표와 빈도/범위/정의가 다른 proxy | 보조 신호로만 사용. exact처럼 표현 금지 |
| new_collector_needed | 무료 공개 exact 자동 원천을 못 찾았거나 유료/구독 원천만 확인됨 | 수집대기 표시. 단발 기사/임의 보간 적재 금지 |

## 3. DB 스키마와 저장 기준

- `quant_major_indicator_catalog`: 지표의 이름, EPIC 분류, 주기, 단위, 연결 상태, 원천, 수집기, exactness, notes를 관리한다.
- `quant_major_indicator_series`: 실제 시계열 값이다. unique key는 `(indicator_key, period, series_name, source_name)`이다.
- `epic_indicator_replacement_plan`: EPIC 원지표 후보/대체계획의 seed 테이블이다. 단, 이미 연결된 지표는 `seed_catalog()`가 `ready_existing`/`ready_existing_partial` 상태를 보존한다.
- 모든 값은 `source_name`, `source_detail`, `quality`, `unit`을 남긴다. 특히 partial/proxy는 `quality`와 `exactness`를 통해 화면에서 구분되어야 한다.
- 삭제 후 재수집 대상은 수집기에서 명시된 indicator_key만 한정한다. 다른 AI/수동 적재값을 전체 truncate하지 않는다.

## 4. 운영 주기

| mode | 권장 시간 | 주요 수집 대상 | 주의 |
| --- | --- | --- | --- |
| daily | 평일 19:30 | 시장폭/거래량, 대차잔고, 기준금리, DART 카지노, 파라다이스 세그먼트 드롭액 | 장마감 후 기준일 표시. DART/IR 정정 시 latest 값 갱신 |
| weekly | 월요일 08:00 | K-Line 건화물, SteelBenchmarker, SunSirs 중국 철강 | SunSirs는 최근 7일 내외 공개값만 가능 |
| monthly | 매월 12일 05:00 | KAMA, KOSIS, KPX, 관광, HS/관세청, 철도/지하철, ECOS, WorldBank, EIA 등 | KOSIS/HS는 proxy 정의를 exact로 승격 금지 |
| annual | 매년 1월 20일 05:00 | HIRA 의료, ITSTAT IPTV | 연간 proxy를 월간 exact로 표시 금지 |

## 5. 원천 패밀리별 기준

| 원천 패밀리 | 근본 소스 | 적용 지표 | 기준/주의 |
| --- | --- | --- | --- |
| KAMA 자동차통계월보 | KAMA 월보 Excel/HTML | 자동차 판매/점유율/모델별 판매 | 협회 공식 월보. 회사별 판매량은 exact, 점유율은 동일 원천 판매량 합계로 산식 파생. 외부 기사값으로 보정 금지. |
| 현대차/기아 IR 및 뉴스룸 | 회사 공식 IR/뉴스룸 | 현대/기아 모델별·지역별 판매 | 회사 공식 배포값. KAMA와 기간/내수·수출 범위가 다르면 source_detail에 기준을 유지. |
| KOSIS/통계청 | 공식 통계표 | 온라인쇼핑, 소매업태, 서비스업 생산, 일부 소비 프록시 | 공식이지만 EPIC 원지표와 항목이 다르면 partial. 카드 결제액 exact로 승격 금지. |
| ECOS/한국은행 | ECOS API | 기준금리, 유동성, 환율, 거시지표 | 공식 API. 시계열 코드와 단위를 catalog notes/source_detail에 남긴다. |
| 관세청/HS Trade Lab | 관세청 수출입·customs DB | 품목·국가별 수출입, 반도체/조선/철강/화장품/의약품 프록시 | HS/성질분류 기준. 회사 매출 exact가 아니라 품목·국가/산업 프록시임을 표시. |
| DART 영업잠정실적 | DART 공정공시/영업잠정실적 | 카지노 매출/드롭액/홀드율 총액 | 공시 원문 기반 exact. 같은 월 중복공시·정정은 첫 정기공시 우선 및 source_detail 유지. |
| 파라다이스 Monthly IR Pack | 회사 공식 IR Excel | 국적/세그먼트별 드롭액 | Segment 시트 CN VIP/JP VIP/Other VIP/Mass/Total. Total은 DART 총 드롭액과 0.0001% 미만 차이로 검증. |
| GKL 공공데이터 | 공공데이터포털 daily CSV | GKL 방문객 | 일별 지점/성별/국적 자료를 월별로 집계. 월별 collapse 기준을 유지. |
| 롯데관광개발 IR Pack | 회사 공식 IR Excel | 드림타워 카지노 입장객 | IR Pack 표 기반 exact. PDF 그래프 OCR 값으로 대체 금지. |
| K-Line | K-Line IR chart republication | BDI/BCI/BPI/BSI 주간 | 제3자 재공표 주간 지수. Baltic Exchange 원천 exact가 아니므로 partial. |
| SteelBenchmarker/SunSirs/Yahoo steel proxy | 공개 최신값/시장 프록시 | 중국 철강 가격 | 최근 공개 테이블 또는 선물/프록시. 장기 exact로 표시 금지. |
| World Bank Pink Sheet | World Bank commodity monthly | 철광석 월간 가격 | 공식 월간 상품가격. EPIC의 주간 중국 수입가격 exact가 아니므로 partial. |
| EIA/Baker Hughes | EIA 또는 Baker Hughes 공개 파일 | 리그카운트 | 미국/북미 리그카운트 기준을 분리. 주간 exact가 아닐 경우 partial. |
| KTO/Data.go.kr | 한국관광공사·공공데이터 | 관광객/출국 수요 | 회사별 송출객 exact가 아니면 국민해외관광 전체 프록시로 표시. |
| HIRA/건강보험심사평가원 | 공공데이터포털 HIRA | 피부과/성형외과/치과 의료 수요 | 카드 결제액이 아니라 연간 진료/급여비 proxy. exact 카드 데이터로 표시 금지. |
| ITSTAT | ICT통계포털 | IPTV 가입자 | 현재 공개 원천은 연간 terminal-count. 월간 exact로 표시 금지. |
| MTRACE | 축산물품질평가원 | 돼지고기 도매가격 | 월별 all-grade/all-market 경락가격 프록시. 일별 exact로 승격 금지. |
| local price_history/short_sell_daily | 로컬 DB 파생 | 시장폭, 거래량, 대차잔고 | KIS/키움/내부 수집값 기반 파생. 데이터 기준일과 source_name을 반드시 유지. |

## 6. 카테고리별 연결 현황

| cat | 카테고리 | ready | partial | pending | total |
| --- | --- | --- | --- | --- | --- |
| 0 | 자동차 · 타이어 | 12 | 0 | 0 | 12 |
| 1 | 철강 | 0 | 8 | 0 | 8 |
| 2 | 유통 · 소비재 · 렌탈 | 3 | 5 | 0 | 8 |
| 3 | 미디어 · 엔터테인먼트 | 1 | 6 | 0 | 7 |
| 4 | 건설 · 부동산 · 건자재 | 0 | 1 | 0 | 1 |
| 6 | 유틸리티/인프라 | 1 | 1 | 0 | 2 |
| 7 | 운송 | 0 | 6 | 0 | 6 |
| 8 | 화장품 | 2 | 0 | 0 | 2 |
| 9 | 카지노 | 13 | 0 | 0 | 13 |
| 10 | 통신서비스 | 0 | 1 | 0 | 1 |
| 11 | 음식료 · 담배 | 0 | 4 | 0 | 4 |
| 12 | 패션 · 명품 | 2 | 1 | 0 | 3 |
| 13 | 교육 | 0 | 3 | 0 | 3 |
| 15 | Tech | 0 | 1 | 0 | 1 |
| 16 | 건강관리 | 0 | 4 | 0 | 4 |
| 17 | 조선 | 0 | 1 | 0 | 1 |
| 19 | 에너지 · 정유화학 | 0 | 2 | 1 | 3 |
| 20 | 금융 | 20 | 0 | 0 | 20 |
| 21 |  | 4 | 0 | 0 | 4 |
| 22 | 환경 | 1 | 1 | 0 | 2 |
| 23 |  | 10 | 0 | 0 | 10 |

## 7. 원천별 연결 현황

| source_system | status | indicator count | row group count |
| --- | --- | --- | --- |
| 통계청/KOSIS | ready_existing_partial | 13 | 13 |
| 관세청 수출입 HS Trade DB | ready_existing | 10 | 10 |
| DART 영업잠정실적 공정공시 | ready_existing | 9 | 9 |
| KAMA 자동차통계월보 | ready_existing | 9 | 9 |
| ECOS | ready_existing | 8 | 8 |
| 통계청/KOSIS | ready_existing | 7 | 7 |
| 한국은행 ECOS | ready_existing | 7 | 7 |
| K-Line Shipping Market Information | ready_existing_partial | 4 | 4 |
| SteelBenchmarker | ready_existing_partial | 4 | 4 |
| local price_history derived | ready_existing | 4 | 4 |
| price_history | ready_existing | 4 | 4 |
| 공공데이터포털/HIRA | ready_existing_partial | 3 | 3 |
| SunSirs | ready_existing_partial | 2 | 2 |
| 관세청 customs | ready_existing_partial | 2 | 2 |
| 한국관광공사/data.go.kr | ready_existing_partial | 2 | 2 |
| 현대차 IR | ready_existing | 2 | 2 |
| Baker Hughes NA Rig Count Monthly Excel | ready_existing_partial | 1 | 1 |
| DICJ Macau | ready_existing | 1 | 1 |
| EIA | ready_existing_partial | 1 | 1 |
| Enterprise Singapore StatLink / 유료 원천 후보 | new_collector_needed | 1 | 1 |
| ICT통계포털/ITSTAT | ready_existing_partial | 1 | 1 |
| KRX/공공데이터 | ready_existing | 1 | 1 |
| Kia America Newsroom | ready_existing | 1 | 1 |
| World Bank Pink Sheet | ready_existing_partial | 1 | 1 |
| Yahoo Finance / ICE Newcastle Coal Futures | ready_existing_partial | 1 | 1 |
| Yahoo Finance BDRY | ready_existing_partial | 1 | 1 |
| hs_trade_lab | ready_existing | 1 | 1 |
| hs_trade_lab | ready_existing_partial | 1 | 1 |
| yahoo_hrc_futures | ready_existing_partial | 1 | 1 |
| 공공데이터포털/GKL | ready_existing | 1 | 1 |
| 관세청 HS Trade Lab | ready_existing_partial | 1 | 1 |
| 관세청 수출입/HS Trade Lab | ready_existing_partial | 1 | 1 |
| 롯데관광개발 IR Pack | ready_existing | 1 | 1 |
| 모두투어 보도자료/뉴스와이어 | ready_existing_partial | 1 | 1 |
| 서울 열린데이터광장 | ready_existing | 1 | 1 |
| 서울 열린데이터광장 | ready_existing_partial | 1 | 1 |
| 전력거래소(KPX) | ready_existing | 1 | 1 |
| 철도산업정보센터/KRIC | ready_existing_partial | 1 | 1 |
| 축산물품질평가원/MTRACE | ready_existing_partial | 1 | 1 |
| 파라다이스 Monthly IR Pack | ready_existing | 1 | 1 |
| 한국관광공사 DataLab | ready_existing_partial | 1 | 1 |

## 8. 최근 Codex가 직접 확정한 핵심 연결

| indicator_key | 내용 | 소스/기준 | 검증 |
| --- | --- | --- | --- |
| epic:9:24 | 파라다이스 월별 드롭액, 국적/세그먼트별 | 공식 Monthly IR Pack Excel `Segment` 시트. CN VIP/JP VIP/Other VIP/Mass/Total, 단위 KRW mn=백만원 | DART 총 드롭액 `epic:9:23`과 Total 비교, 최신 월 오차 0.0001% 미만 |
| epic:0:4 | 한국 자동차 시장점유율 | KAMA 공식 판매량에서 회사별 점유율 산식 파생 | 기존 시계열 882행 확인 후 ready_existing 정정 |
| epic:1:28 | 중국 G.I 가격 | SunSirs 최근 공개 daily spot table | 최근 공개 범위만 partial. 장기 exact 아님 |
| epic:1:30 | 중국 Wire Rod 가격 | SunSirs 최근 공개 daily spot table | 최근 공개 범위만 partial. 장기 exact 아님 |
| epic:12:10 | 베트남 의류/신발 수출 proxy | 관세청 국가×성질분류, 한국 기준 베트남향 수출 | 베트남 글로벌 수출이 아니므로 partial |
| epic:15:11 | 베트남 IT제품 수출 proxy | 관세청 국가×성질분류, 한국 기준 베트남향 수출 | 베트남 글로벌 수출이 아니므로 partial |
| epic:9:37 | 드림타워 카지노 월별 입장객 | 롯데관광개발 공식 IR Pack Excel | company_ir_excel_exact |
| epic:9:19 | GKL 외국인 방문객 | 공공데이터포털 daily branch/gender/nationality CSV 월별 집계 | official_publicdata_daily_aggregate |

## 9. 남은 수집대기

| indicator_key | 지표 | 주기 | 단위 | 원천 후보 | exactness | 보류 사유 |
| --- | --- | --- | --- | --- | --- | --- |
| epic:19:104 | 싱가포르 석유제품 재고 추이 (주) | Weekly | 천배럴 | Enterprise Singapore StatLink / 유료 원천 후보 | paid_or_subscription_exact_candidate | Enterprise Singapore StatLink에는 weekly oil stock levels 관련 상품이 있으나 subscription/cart 기반이며, 확인 가능한 공개 메뉴는 Monthly Oil Statistics(월간 석유 무역통계)이다. S&P/CEIC/QCIntel 등도 유료 재판매 형태라 무료 공개 exact 자동 원천 미확정. 월간 무역통계나 기사값을 weekly petroleum stock으로 프록시 적재 금지. |

### 싱가포르 석유제품 주간 재고 정책

`epic:19:104`는 Enterprise Singapore StatLink가 weekly oil stock levels를 보유하는 것으로 확인되지만, StatLink가 subscription/cart 기반이다. CEIC/S&P/Argus/QCIntel 등은 재판매 또는 기사 인용 형태라 무료 구조화 시계열이 아니다. 월간 석유 무역통계나 기사 단발값을 weekly petroleum stock으로 넣으면 오염이므로 금지한다.

## 10. Claude 검증 SQL

```sql
-- 1) 상태 카운트
select status, count(*) as cnt
from quant_major_indicator_catalog
group by status
order by status;

-- 2) ready인데 시계열이 0개인 지표 점검
select c.indicator_key, c.epic_indicator_name, c.status, c.source_system
from quant_major_indicator_catalog c
left join quant_major_indicator_series s on s.indicator_key=c.indicator_key
where c.status in ('ready_existing','ready_existing_partial')
group by c.indicator_key
having count(s.id)=0
order by c.indicator_key;

-- 3) partial 지표 전체 목록
select indicator_key, epic_indicator_name, frequency, base_unit, source_system, exactness, notes
from quant_major_indicator_catalog
where status='ready_existing_partial'
order by epic_category_code, epic_sub_code;

-- 4) source/quality 누락 점검
select indicator_key, count(*) as bad_rows
from quant_major_indicator_series
where coalesce(source_name,'')='' or coalesce(quality,'')=''
group by indicator_key
order by bad_rows desc;

-- 5) 파라다이스 세그먼트 드롭액 검증: IR Total vs DART total
WITH dart AS (
  SELECT period, value AS dart_total
  FROM quant_major_indicator_series
  WHERE indicator_key='epic:9:23'
), ir AS (
  SELECT period, value AS ir_total
  FROM quant_major_indicator_series
  WHERE indicator_key='epic:9:24' AND series_name='Total 드롭액'
)
SELECT dart.period, dart.dart_total, ir.ir_total,
       ROUND(ir.ir_total-dart.dart_total, 3) AS diff,
       ROUND((ir.ir_total-dart.dart_total)/dart.dart_total*100, 6) AS diff_pct
FROM dart JOIN ir USING(period)
ORDER BY dart.period DESC LIMIT 12;
```

## 11. Claude가 특히 봐야 할 체크포인트

- `ready_existing_partial` 지표가 프론트엔드에서 “부분/프록시/수집기준”으로 명확히 보이는지 확인한다.
- `epic:19:104`가 임의 proxy로 채워지지 않았는지 확인한다.
- `quant_indicators_cron.py --mode daily/weekly/monthly/annual`가 중복 PID/DB busy_timeout/WAL 기준으로 안전하게 동작하는지 확인한다.
- `source_name`, `source_detail`, `quality` 누락이 없는지 SQL 4번으로 확인한다.
- 파라다이스 `epic:9:24`는 DART total 검증 쿼리로 최신 겹치는 월을 확인한다.
- SunSirs/SteelBenchmarker/K-Line 같은 제3자 재공표 지표는 투자 판단 시 exact official로 승격하지 않는다.
- KOSIS/HIRA/ITSTAT/KTO 지표는 대부분 공식 proxy이므로 EPIC 원지표와 정의가 다른 경우 notes를 화면에 노출한다.

## 12. 금지 규칙

- 기사 단발 숫자, PDF 그래프 OCR 추정값, 유료 샘플값을 시계열로 보간하지 않는다.
- 월간 데이터를 주간 데이터처럼 보이게 하지 않는다.
- 회사별 exact 지표가 없을 때 산업 전체 proxy를 회사 exact처럼 쓰지 않는다.
- 부분 연결을 `ready_existing`으로 승격하려면 원천 정의와 단위, 빈도, 범위가 EPIC 원지표와 같아야 한다.
- 수집기가 실패했을 때 기존 정상 데이터를 삭제하지 않는다. 실패 로그를 남기고 다음 스케줄에서 재시도한다.

## 13. 전체 지표 Appendix

아래 표는 Claude가 카탈로그와 시계열 적재 여부를 빠르게 대조하기 위한 스냅샷이다. 더 긴 notes 원문은 DB와 CSV를 우선한다.

| key | cat | category | indicator | freq | unit | status | source | exactness | rows | min | max | series | updated | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| epic:0:1 | 0 | 자동차 · 타이어 | 글로벌 자동차 판매: 국가별 (월) | Monthly | 대 | ready_existing | KAMA 자동차통계월보 | official_association_exact | 1532 | 2015-01 | 2026-04 | 14 | 2026-06-13 20:17:59 | KAMA 10-2 주요국신차등록 시트의 국가별 계(총합) 컬럼에서 월간 신차등록을 직접 수집. |
| epic:0:2 | 0 | 자동차 · 타이어 | 한국 자동차 판매: 회사별 (월) | Monthly | 대 | ready_existing | KAMA 자동차통계월보 | official_association_exact | 763 | 2015-01 | 2026-01 | 7 | 2026-06-13 20:17:59 | KAMA 자동차통계월보 1-3 업체별 총괄 시트에서 7개 국내 완성차사의 월별 판매계를 직접 수집. |
| epic:0:4 | 0 | 자동차 · 타이어 | 한국 자동차 시장 점유율: 회사별 (월) | Monthly | % | ready_existing | KAMA 자동차통계월보 | derived_from_official_sales | 763 | 2015-01 | 2026-01 | 7 | 2026-06-13 20:17:59 | KAMA 자동차통계월보 업체별 내수 판매를 합산해 회사별 월간 시장점유율로 계산. 원천 판매량이 공식 협회 데이터이므로 파생 지표로 사용 가능. |
| epic:0:14 | 0 | 자동차 · 타이어 | 현대차 내수 판매: 모델별 (월) | Monthly | 대 | ready_existing | 현대차 IR | official_exact | 4567 | 2016-01 | 2026-04 | 84 | 2026-06-13 20:17:59 | 현대차 IR 월간 Sales by Model 엑셀에서 국내 모델별 판매량 직접 수집. |
| epic:0:17 | 0 | 자동차 · 타이어 | 기아차 내수 판매: 모델별 (월) | Monthly | 대 | ready_existing | KAMA 자동차통계월보 | official_association_exact | 810 | 2016-06 | 2026-04 | 38 | 2026-06-13 20:17:59 | KAMA 1-4 업체별·모델별 생산·내수·수출 시트의 모델 패밀리 합계행에서 기아 국내 모델별 판매를 월 단위로 수집. |
| epic:0:19 | 0 | 자동차 · 타이어 | KG모빌리티 내수 ∙ 수출 판매 (월) | Monthly | 대 | ready_existing | KAMA 자동차통계월보 | official_association_exact | 327 | 2015-01 | 2026-01 | 3 | 2026-06-13 20:17:59 | KAMA 업체별 총괄에서 KG모빌리티의 판매계/내수/수출을 월별 수집. |
| epic:0:20 | 0 | 자동차 · 타이어 | 르노코리아 내수 ∙ 수출 판매 (월) | Monthly | 대 | ready_existing | KAMA 자동차통계월보 | official_association_exact | 327 | 2015-01 | 2026-01 | 3 | 2026-06-13 20:17:59 | KAMA 업체별 총괄에서 르노코리아(구 르노삼성)의 판매계/내수/수출을 월별 수집. |
| epic:0:21 | 0 | 자동차 · 타이어 | 한국GM 내수 ∙ 수출 판매 (월) | Monthly | 대 | ready_existing | KAMA 자동차통계월보 | official_association_exact | 327 | 2015-01 | 2026-01 | 3 | 2026-06-13 20:17:59 | KAMA 업체별 총괄에서 한국GM(구 한국지엠)의 판매계/내수/수출을 월별 수집. |
| epic:0:55 | 0 | 자동차 · 타이어 | 현대차 미국 판매: 모델별 (월) | Monthly | 대 | ready_existing | 현대차 IR | official_exact | 1909 | 2016-01 | 2026-04 | 32 | 2026-06-13 20:17:59 | 현대차 IR 월간 US Retail Sales 엑셀에서 미국 모델별 소매판매 직접 수집. |
| epic:0:57 | 0 | 자동차 · 타이어 | 기아차 미국 판매: 모델별 (월) | Monthly | 대 | ready_existing | Kia America Newsroom | official_exact | 1326 | 2017-01 | 2026-05 | 17 | 2026-06-13 20:17:59 | Kia America 월간 Sales By Model export 엑셀에서 미국 모델별 판매량 직접 수집. |
| epic:0:112 | 0 | 자동차 · 타이어 | KG모빌리티 내수 판매: 모델별 (월) | Monthly | 대 | ready_existing | KAMA 자동차통계월보 | official_association_exact | 263 | 2016-06 | 2026-04 | 12 | 2026-06-13 20:17:59 | KAMA 1-4 모델 패밀리 합계행에서 KG모빌리티 국내 모델별 판매를 월 단위로 수집. |
| epic:0:113 | 0 | 자동차 · 타이어 | KG모빌리티 수출 판매: 모델별 (월) | Monthly | 대 | ready_existing | KAMA 자동차통계월보 | official_association_exact | 263 | 2016-06 | 2026-04 | 12 | 2026-06-13 20:17:59 | KAMA 1-4 모델 패밀리 합계행에서 KG모빌리티 수출 모델별 판매를 월 단위로 수집. |
| epic:1:28_proxy | 1 |  | China HRC Steel Price (US HRC Proxy) | monthly | USD/short_ton | ready_existing_partial | yahoo_hrc_futures | us_hrc_futures_proxy | 126 | 2016-01 | 2026-06 | 1 | 2026-06-13 00:18:23 | CME US HRC선물 월평균. 중국 HRC/후판 proxy. |
| epic:1:25 | 1 | 철강 | China: Steel Price, HRC (일) | Daily | 위안/톤 | ready_existing_partial | SteelBenchmarker | public_report_latest_only | 1 | 2026-06-10 | 2026-06-10 | 1 | 2026-06-13 20:17:59 | SteelBenchmarker 공개 PDF에서 최신 Mainland China China HRB/HRC 가격 1포인트를 수집. 장기 시계열은 PDF history 표 파서 검증 후 확장 필요. |
| epic:1:26 | 1 | 철강 | China: Steel Price, CRC (일) | Daily | 위안/톤 | ready_existing_partial | SteelBenchmarker | public_report_latest_only | 1 | 2026-06-10 | 2026-06-10 | 1 | 2026-06-13 20:17:59 | SteelBenchmarker 공개 PDF에서 최신 Mainland China China CRC 가격 1포인트를 수집. 장기 시계열은 PDF history 표 파서 검증 후 확장 필요. |
| epic:1:27 | 1 | 철강 | China: Steel Price, Heavy Plate (일) | Daily | 위안/톤 | ready_existing_partial | SteelBenchmarker | public_report_latest_only | 1 | 2026-06-10 | 2026-06-10 | 1 | 2026-06-13 20:17:59 | SteelBenchmarker 공개 PDF에서 최신 Mainland China China Standard Plate 가격 1포인트를 수집. 장기 시계열은 PDF history 표 파서 검증 후 확장 필요. |
| epic:1:28 | 1 | 철강 | China: Steel Price, G.I (일) | Daily | 위안/톤 | ready_existing_partial | SunSirs | third_party_recent_public_daily_price | 6 | 2026-06-08 | 2026-06-13 | 1 | 2026-06-13 21:45:22 | SunSirs 공개 China Commodity Data Group 페이지에서 China Galvanized sheet(G.I), HDG/DX51D+Z/1.0*1250*C 최근 일별 spot price를 수집.... |
| epic:1:29 | 1 | 철강 | China: Steel Price, Rebar (일) | Daily | 위안/톤 | ready_existing_partial | SteelBenchmarker | public_report_latest_only | 1 | 2026-06-10 | 2026-06-10 | 1 | 2026-06-13 20:17:59 | SteelBenchmarker 공개 PDF에서 최신 Mainland China China Rebar 가격 1포인트를 수집. 장기 시계열은 PDF history 표 파서 검증 후 확장 필요. |
| epic:1:30 | 1 | 철강 | China: Steel Price, Wire Rod (일) | Daily | 위안/톤 | ready_existing_partial | SunSirs | third_party_recent_public_daily_price | 6 | 2026-06-08 | 2026-06-13 | 1 | 2026-06-13 21:45:22 | SunSirs 공개 China Commodity Data Group 페이지에서 China Wire Rod, HPB235/Φ8 최근 일별 spot price를 수집. 로그인 없이 공개되는 최근 7일 내외만 적재 ... |
| epic:1:37 | 1 | 철강 | China: Iron Ore Import Price (주) | Weekly | 달러/톤 | ready_existing_partial | World Bank Pink Sheet | official_proxy_monthly | 797 | 1960-01 | 2026-05 | 1 | 2026-06-13 20:17:59 | World Bank Commodity Price Data의 Iron ore, cfr spot 월간 가격을 수집. EPIC 원 지표의 주간 China import price와 exact는 아니므로 공식 월간 프록... |
| epic:2:22 | 2 | 유통 · 소비재 · 렌탈 | 인터넷 쇼핑 상품군별 판매액 (월) | Monthly | 백만원 | ready_existing | 통계청/KOSIS | official_exact | 2840 | 2017-01 | 2026-04 | 26 | 2026-06-13 20:17:59 | KOSIS DT_1KE10071 공식 statHtml 표에서 판매매체=인터넷쇼핑, 상품군별 거래액을 월별 직접 수집. |
| epic:2:23 | 2 | 유통 · 소비재 · 렌탈 | 모바일 쇼핑 상품군별 판매액 (월) | Monthly | 백만원 | ready_existing | 통계청/KOSIS | official_exact | 2840 | 2017-01 | 2026-04 | 26 | 2026-06-13 20:17:59 | KOSIS DT_1KE10071 공식 statHtml 표에서 판매매체=모바일쇼핑, 상품군별 거래액을 월별 직접 수집. |
| epic:2:93 | 2 | 유통 · 소비재 · 렌탈 | 카드 결제액 추정치: 백화점 (월) | Monthly | 억원 | ready_existing_partial | 통계청/KOSIS | official_retail_sales_proxy | 76 | 2020-01 | 2026-04 | 1 | 2026-06-13 20:17:59 | EPIC 카드결제액 추정치 exact 원천은 미확정. 대체지표로 KOSIS DT_1K41003 소매업태별 판매액의 백화점 판매액을 월별 공식 프록시로 수집. |
| epic:2:94 | 2 | 유통 · 소비재 · 렌탈 | 카드 결제액 추정치: 할인점, 슈퍼마켓 (월) | Monthly | 억원 | ready_existing_partial | 통계청/KOSIS | official_retail_sales_proxy | 76 | 2020-01 | 2026-04 | 1 | 2026-06-13 20:17:59 | EPIC 카드결제액 추정치 exact 원천은 미확정. 대체지표로 KOSIS DT_1K41003 소매업태별 판매액의 대형마트+슈퍼마켓 및 잡화점 판매액 합산을 월별 공식 프록시로 수집. |
| epic:2:95 | 2 | 유통 · 소비재 · 렌탈 | 카드 결제액 추정치: 편의점 (월) | Monthly | 억원 | ready_existing_partial | 통계청/KOSIS | official_retail_sales_proxy | 76 | 2020-01 | 2026-04 | 1 | 2026-06-13 20:17:59 | EPIC 카드결제액 추정치 exact 원천은 미확정. 대체지표로 KOSIS DT_1K41003 소매업태별 판매액의 편의점 판매액을 월별 공식 프록시로 수집. |
| epic:2:96 | 2 | 유통 · 소비재 · 렌탈 | 카드 결제액 추정치: 면세점 (월) | Monthly | 억원 | ready_existing_partial | 통계청/KOSIS | official_retail_sales_proxy | 76 | 2020-01 | 2026-04 | 1 | 2026-06-13 20:17:59 | EPIC 카드결제액 추정치 exact 원천은 미확정. 대체지표로 KOSIS DT_1K41003 소매업태별 판매액의 면세점 판매액을 월별 공식 프록시로 수집. |
| epic:2:97 | 2 | 유통 · 소비재 · 렌탈 | 카드 결제액 추정치: 온라인 (월) | Monthly | 억원 | ready_existing_partial | 통계청/KOSIS | official_retail_sales_proxy | 76 | 2020-01 | 2026-04 | 1 | 2026-06-13 20:17:59 | EPIC 카드결제액 추정치 exact 원천은 미확정. 대체지표로 KOSIS DT_1K41003 소매업태별 판매액의 무점포 소매 판매액을 월별 공식 프록시로 수집. |
| epic:2:98 | 2 | 유통 · 소비재 · 렌탈 | 온라인 ∙ 모바일 쇼핑 거래액 (월) | - | - | ready_existing | 통계청/KOSIS | official_exact | 112 | 2017-01 | 2026-04 | 1 | 2026-06-13 20:17:58 | KOSIS 경제상황판 내부 API(selectDetailDataList)에서 온라인쇼핑 거래액 전국 월별 시계열을 직접 수집. |
| epic:3:34 | 3 | 미디어 · 엔터테인먼트 | 모두투어 송출객 현황 (월) | Monthly | 명 | ready_existing_partial | 한국관광공사/data.go.kr | official_outbound_travel_demand_proxy_not_modetour_exact | 144 | 2023-08 | 2024-07 | 12 | 2026-06-13 20:17:59 | EPIC 원 지표는 모두투어 월별 송출객 현황이나 회사별 exact 원천은 미확정. 공식 대체지표로 한국관광공사_국민 해외관광객 교통수단별 월별 집계 2023-08~2024-07의 전체/공항/항구/출국장별 국민... |
| epic:3:36 | 3 | 미디어 · 엔터테인먼트 | 모두투어 해외 패키지 송출객 (월) | Monthly | 명 | ready_existing_partial | 모두투어 보도자료/뉴스와이어 | company_press_release_partial_exact | 1 | 2024-05 | 2024-05 | 1 | 2026-06-13 20:44:36 | 모두투어 보도자료에서 월별 해외 패키지 송출객 숫자가 명확히 기재된 달만 수집. 누락 월은 보간하지 않으며, 회사 보도자료 텍스트 파싱 기반이라 IR 원표 exact 전체 히스토리로 해석 금지. |
| epic:3:70 | 3 | 미디어 · 엔터테인먼트 | 한국 관광 방문자 추이 (월) | Monthly | 천명 | ready_existing_partial | 한국관광공사/data.go.kr | official_partial | 72 | 2023-08 | 2024-07 | 6 | 2026-06-13 20:17:59 | 공공데이터포털 한국관광공사_방한 외래관광객 목적별 월별 집계 CSV에서 2023-08~2024-07 구간을 부분 수집. 장기/최신 구간은 한국관광데이터랩 또는 ODCloud 정상 인증키 필요. |
| epic:3:71 | 3 | 미디어 · 엔터테인먼트 | 한국 지역별 관광 방문자 추이 (월) | Monthly | 천명 | ready_existing_partial | 한국관광공사 DataLab | official_datalab_trend | 1309 | 2020-01 | 2026-05 | 17 | 2026-06-13 20:17:59 | 한국관광공사 DataLab 지역별 관광현황 LN_01_01_016에서 월별 17개 시도 방문자수 추세를 수집. DataLab 안내상 총량보다 추세 분석 지표로 활용 권장. |
| epic:3:97 | 3 | 미디어 · 엔터테인먼트 | 영상 구독 서비스 지출건수 및 지출금액 (월) | Monthly | %, (2020.01=100) | ready_existing_partial | 통계청/KOSIS | official_online_content_spending_proxy | 112 | 2017-01 | 2026-04 | 1 | 2026-06-13 20:17:59 | EPIC 원 지표는 영상 구독 서비스 지출건수/금액이나 exact 카드 결제 원천은 미확정. 대체지표로 KOSIS DT_1KE10071 온라인쇼핑 문화 및 레저서비스 거래액(인터넷+모바일)을 월별 공식 프록시로... |
| epic:3:98 | 3 | 미디어 · 엔터테인먼트 | 음원 구독 서비스 지출건수 및 지출금액 (월) | Monthly | %, (2020.01=100) | ready_existing_partial | 통계청/KOSIS | official_online_content_spending_proxy_not_music_exact | 112 | 2017-01 | 2026-04 | 1 | 2026-06-13 20:44:36 | EPIC 원 지표는 음원 구독 서비스 지출건수/금액이나 exact 카드/음원 플랫폼 원천은 미확정. 대체지표로 KOSIS DT_1KE10071 온라인쇼핑 문화 및 레저서비스 거래액(인터넷+모바일)을 월별 공식 ... |
| epic:3:semi | 3 |  | 한국 반도체 수출액(월) | Monthly | USD 백만달러 | ready_existing | hs_trade_lab |  | 123 | 2016-01 | 2026-03 | 1 | 2026-06-11 14:29:01 |  |
| epic:4:96 | 4 | 건설 · 부동산 · 건자재 | 동북아시아 유연탄 가격 (주) | Weekly | 달러/톤 | ready_existing_partial | Yahoo Finance / ICE Newcastle Coal Futures | market_proxy_monthly | 120 | 2016-01 | 2025-12 | 1 | 2026-06-13 00:26:40 | 동북아 유연탄 exact 주간 원천은 미확정. 현재는 ICE Newcastle coal futures(MTF=F) 월평균 proxy를 적재(2016-01~2025-12, 120행). 월간 방향성 참고용 부분연결. |
| epic:semi:dram_proxy | 6 |  | 한국 반도체(집적회로) 수출단가 (DRAM 대리지표) | monthly | USD/kg | ready_existing_partial | hs_trade_lab |  | 125 | 2016-01 | 2026-05 | 1 | 2026-06-12 10:11:14 | 관세청 HS8542 집적회로 수출금액/중량. DRAM 스팟가격 대리변수. 2016-2026 125개월. |
| epic:6:18 | 6 | 유틸리티/인프라 | 계통한계가격(SMP): 일 가중평균 SMP (일) | Daily | 원/kWh | ready_existing | 전력거래소(KPX) | official_exact | 51 | 2025-01 | 2026-05 | 3 | 2026-06-13 20:17:58 | 전력거래소 월별 계통한계가격(SMP) 표에서 육지/제주/통합 SMP를 월별로 직접 수집. |
| epic:7:14 | 7 | 운송 | Freight Index: BDI(Baltic Dry Index) (일) | Daily | 지수 | ready_existing_partial | K-Line Shipping Market Information | third_party_weekly_index_republication | 516 | 2016-05-06 | 2026-05-22 | 1 | 2026-06-13 20:17:59 | EPIC 원 지표는 Baltic Exchange BDI(Baltic Dry Index) 일별 지수이나 공식 licensed feed는 미연결. K-Line IR 공개 차트에 포함된 주간 Friday label ... |
| epic:7:14_proxy | 7 | 운송 | Freight Index: BDI proxy (BDRY ETF, 월) | Monthly | USD | ready_existing_partial | Yahoo Finance BDRY | official_etf_proxy | 60 | 2021-07 | 2026-06 | 1 | 2026-06-12 15:56:29 | BDRY ETF = Breakwave Dry Bulk Shipping ETF. BDI 직접 데이터 미수집, ETF 주가를 대리 지표로 사용. 조선/해운 업황 참고용. |
| epic:7:15 | 7 | 운송 | Freight Index: BCI(Baltic Capesize Index) (일) | Daily | 지수 | ready_existing_partial | K-Line Shipping Market Information | third_party_weekly_index_republication | 519 | 2016-05-06 | 2026-05-22 | 1 | 2026-06-13 20:17:59 | EPIC 원 지표는 Baltic Exchange BCI(Baltic Capesize Index) 일별 지수이나 공식 licensed feed는 미연결. K-Line IR 공개 차트에 포함된 주간 Friday l... |
| epic:7:16 | 7 | 운송 | Freight Index: BPI(Baltic Panamax Index) (일) | Daily | 지수 | ready_existing_partial | K-Line Shipping Market Information | third_party_weekly_index_republication | 455 | 2017-08-04 | 2026-05-22 | 1 | 2026-06-13 20:17:59 | EPIC 원 지표는 Baltic Exchange BPI(Baltic Panamax Index) 일별 지수이나 공식 licensed feed는 미연결. K-Line IR 공개 차트에 포함된 주간 Friday la... |
| epic:7:17 | 7 | 운송 | Freight Index: BSI(Baltic Supramax Index) (일) | Daily | 지수 | ready_existing_partial | K-Line Shipping Market Information | third_party_weekly_index_republication | 519 | 2016-05-06 | 2026-05-22 | 1 | 2026-06-13 20:17:59 | EPIC 원 지표는 Baltic Exchange BSI(Baltic Supramax Index) 일별 지수이나 공식 licensed feed는 미연결. K-Line IR 공개 차트에 포함된 주간 Friday l... |
| epic:7:36 | 7 | 운송 | 노선별 철도 여객인원 수 (월) | Monthly | 명 | ready_existing_partial | 철도산업정보센터/KRIC | official_korail_general_rail_partial | 12116 | 2022-01 | 2026-04 | 312 | 2026-06-13 20:17:59 | 철도산업정보센터 노선별 여객수송(월) HTML 표에서 한국철도공사 일반철도 노선별/열차종별 수송인원을 월별 수집. 도시철도·민자 전체 철도까지 포함한 완전 커버리지는 아니므로 부분연결로 표시. |
| epic:8:14 | 8 | 화장품 | 인터넷 쇼핑 상품군별 판매액 - 화장품 (월) | Monthly | 백만원 | ready_existing | 통계청/KOSIS | official_exact | 112 | 2017-01 | 2026-04 | 1 | 2026-06-13 20:17:59 | KOSIS DT_1KE10071 공식 statHtml 표에서 인터넷쇼핑/화장품 거래액을 월별 직접 수집. |
| epic:8:15 | 8 | 화장품 | 모바일 쇼핑 상품군별 판매액 - 화장품 (월) | Monthly | 백만원 | ready_existing | 통계청/KOSIS | official_exact | 112 | 2017-01 | 2026-04 | 1 | 2026-06-13 20:17:59 | KOSIS DT_1KE10071 공식 statHtml 표에서 모바일쇼핑/화장품 거래액을 월별 직접 수집. |
| epic:9:13 | 9 | 카지노 | Macao: Gross Revenue from Gaming (월) | Monthly | 백만MOP | ready_existing | DICJ Macau | official_exact | 197 | 2010-01 | 2026-05 | 1 | 2026-06-13 20:17:59 | 마카오 Gaming Inspection and Coordination Bureau(DICJ) 공식 XML에서 월별 Gross Revenue from Games of Fortune을 수집. |
| epic:9:18 | 9 | 카지노 | 파라다이스 월별 매출액 (월) | Monthly | 백만원 | ready_existing | DART 영업잠정실적 공정공시 | official_disclosure_exact | 9 | 2025-09 | 2026-05 | 1 | 2026-06-13 15:28:53 | 파라다이스 월별 카지노 매출액을 DART 영업잠정실적 공시 원문에서 월별 수집. 입장객은 해당 공시 본문에 없어 별도 IR/공공데이터 소스로 분리 수집. |
| epic:9:19 | 9 | 카지노 | GKL 월별 입장객 현황 (월) | Monthly | 명 | ready_existing | 공공데이터포털/GKL | official_publicdata_daily_aggregate | 1103 | 2018-11 | 2026-03 | 13 | 2026-06-13 20:17:59 | 공공데이터포털 그랜드코리아레저(주)_국적별 입장객 수 CSV를 내려받아 영업일 기준 완성 월만 월별 합산. 전체/영업장별/국적별 입장객을 저장하며, 월중 부분 데이터는 제외. |
| epic:9:20 | 9 | 카지노 | GKL 월별 매출액 (월) | Monthly | 백만원 | ready_existing | DART 영업잠정실적 공정공시 | official_disclosure_exact | 8 | 2025-09 | 2026-04 | 1 | 2026-06-13 15:28:53 | GKL 월별 카지노 매출액을 DART 영업잠정실적 공시 원문에서 월별 수집. 입장객은 해당 공시 본문에 없어 별도 IR/공공데이터 소스로 분리 수집. |
| epic:9:21 | 9 | 카지노 | GKL 월별 드롭액 (월) | Monthly | 백만원 | ready_existing | DART 영업잠정실적 공정공시 | official_disclosure_exact | 8 | 2025-09 | 2026-04 | 1 | 2026-06-13 15:28:53 | GKL 월별 테이블 드롭액을 DART 영업잠정실적 공시 원문에서 월별 수집. 입장객은 해당 공시 본문에 없어 별도 IR/공공데이터 소스로 분리 수집. |
| epic:9:22 | 9 | 카지노 | GKL 월별 홀드율 (월) | Monthly | % | ready_existing | DART 영업잠정실적 공정공시 | derived_from_official_disclosure | 8 | 2025-09 | 2026-04 | 1 | 2026-06-13 15:28:53 | GKL 월별 홀드율=카지노매출액/테이블드롭액을 DART 영업잠정실적 공시 원문에서 월별 수집. 입장객은 해당 공시 본문에 없어 별도 IR/공공데이터 소스로 분리 수집. |
| epic:9:23 | 9 | 카지노 | 파라다이스 월별 드롭액, 카지노별 (월) | Monthly | 백만원 | ready_existing | DART 영업잠정실적 공정공시 | official_disclosure_exact | 9 | 2025-09 | 2026-05 | 1 | 2026-06-13 15:28:53 | 파라다이스 월별 테이블 드롭액을 DART 영업잠정실적 공시 원문에서 월별 수집. 입장객은 해당 공시 본문에 없어 별도 IR/공공데이터 소스로 분리 수집. |
| epic:9:24 | 9 | 카지노 | 파라다이스 월별 드롭액, 국적별 (월) | Monthly | 백만원 | ready_existing | 파라다이스 Monthly IR Pack | company_ir_excel_exact_segment_drop | 195 | 2023-01 | 2026-03 | 5 | 2026-06-13 22:05:47 | 파라다이스 공식 Monthly IR Pack 엑셀 Segment 시트에서 CN VIP/JP VIP/Other VIP/Mass/Total 월별 드롭액(KRW mn=백만원)을 직접 수집. DART 공시의 총 드롭액... |
| epic:9:25 | 9 | 카지노 | 파라다이스 월별 홀드율 (월) | Monthly | % | ready_existing | DART 영업잠정실적 공정공시 | derived_from_official_disclosure | 9 | 2025-09 | 2026-05 | 1 | 2026-06-13 15:28:53 | 파라다이스 월별 홀드율=카지노매출액/테이블드롭액을 DART 영업잠정실적 공시 원문에서 월별 수집. 입장객은 해당 공시 본문에 없어 별도 IR/공공데이터 소스로 분리 수집. |
| epic:9:35 | 9 | 카지노 | 드림타워 카지노 월별 매출액 (월) | Monthly | 백만원 | ready_existing | DART 영업잠정실적 공정공시 | official_disclosure_exact | 9 | 2025-09 | 2026-05 | 1 | 2026-06-13 15:28:53 | 드림타워 카지노 월별 카지노 매출액을 DART 영업잠정실적 공시 원문에서 월별 수집. 입장객은 해당 공시 본문에 없어 별도 IR/공공데이터 소스로 분리 수집. |
| epic:9:36 | 9 | 카지노 | 드림타워 카지노 월별 드롭액 (월) | Monthly | 백만원 | ready_existing | DART 영업잠정실적 공정공시 | official_disclosure_exact | 9 | 2025-09 | 2026-05 | 1 | 2026-06-13 15:28:53 | 드림타워 카지노 월별 테이블 드롭액을 DART 영업잠정실적 공시 원문에서 월별 수집. 입장객은 해당 공시 본문에 없어 별도 IR/공공데이터 소스로 분리 수집. |
| epic:9:37 | 9 | 카지노 | 드림타워 카지노 월별 입장객 (월) | Monthly | 명 | ready_existing | 롯데관광개발 IR Pack | company_ir_excel_exact | 60 | 2021-06 | 2026-05 | 1 | 2026-06-13 21:20:20 | 롯데관광개발 IR 자료실의 최신 드림타워카지노 IR Pack 엑셀 DreamtowerCasino 시트에서 카지노 방문객 월별 값을 직접 수집. 최신 파일이 과거 전체 테이블을 포함하므로 최신 파일 우선, 과거 ... |
| epic:9:38 | 9 | 카지노 | 드림타워 카지노 월별 홀드율 (월) | Monthly | % | ready_existing | DART 영업잠정실적 공정공시 | derived_from_official_disclosure | 9 | 2025-09 | 2026-05 | 1 | 2026-06-13 15:28:53 | 드림타워 카지노 월별 홀드율=카지노매출액/테이블드롭액을 DART 영업잠정실적 공시 원문에서 월별 수집. 입장객은 해당 공시 본문에 없어 별도 IR/공공데이터 소스로 분리 수집. |
| epic:10:11 | 10 | 통신서비스 | IPTV 가입자 수 (월) | Monthly | 명 | ready_existing_partial | ICT통계포털/ITSTAT | official_annual_partial | 16 | 2009 | 2024 | 1 | 2026-06-13 20:17:59 | EPIC 원 지표는 월별 IPTV 가입자 수이나, 무료 공개 공식 원천으로 ICT통계포털 DT_164_27 유료방송 가입자(단자기준)의 IPTV 소계 연간 시계열을 우선 수집. 월간 exact로 해석 금지. |
| epic:11:69 | 11 | 음식료 · 담배 | 가다랑어 어가추이 (월) | Monthly | 달러/톤 | ready_existing_partial | 관세청 수출입/HS Trade Lab | official_customs_unit_price_proxy | 206 | 2016-01 | 2026-05 | 3 | 2026-06-13 20:17:59 | EPIC 원 지표는 가다랑어 어가추이나 무료 공식 어가 원천은 미확정. 대체지표로 HS 0303430000(냉동 가다랑어) 및 0302330000(신선/냉장 가다랑어)의 월별 수입금액/수입중량 단가(USD/kg... |
| epic:11:105 | 11 | 음식료 · 담배 | 한국 돼지고기 도매가격 (일) | Daily | 원/kg | ready_existing_partial | 축산물품질평가원/MTRACE | official_monthly_proxy | 75 | 2020-01 | 2026-04 | 1 | 2026-06-13 20:17:59 | EPIC 원 지표는 일별 돼지고기 도매가격이나, 공개 공식 원천으로 MTRACE DT_APGS_016 돼지도체 도매시장별 등급별 경락가격의 전체 등급/전체 도매시장 월별 경락가격(원/kg)을 부분 프록시로 수집. |
| epic:11:155 | 11 | 음식료 · 담배 | 카드 결제액 추정치: 제과/커피/패스트푸드 (월) | Monthly | 억원 | ready_existing_partial | 통계청/KOSIS | official_online_food_service_proxy | 112 | 2017-01 | 2026-04 | 1 | 2026-06-13 20:17:59 | EPIC 원 지표는 제과/커피/패스트푸드 카드 결제액이나 exact 카드 업종 원천은 미확정. 대체지표로 KOSIS DT_1KE10071 온라인쇼핑 음식서비스 거래액(인터넷+모바일)을 월별 공식 프록시로 수집.... |
| epic:11:156 | 11 | 음식료 · 담배 | 카드 결제액 추정치: 외식 (월) | Monthly | 억원 | ready_existing_partial | 통계청/KOSIS | official_quarterly_service_index_proxy | 714 | 2021-Q1 | 2026-Q1 | 34 | 2026-06-13 20:17:59 | EPIC 카드결제액 exact 원천은 미확정. 대체지표로 KOSIS DT_1KC2023 시도별 서비스업생산지수의 숙박 및 음식점업 경상/불변지수를 분기 공식 프록시로 수집. |
| epic:12:5 | 12 | 패션 · 명품 | 인터넷 쇼핑 의류, 패션 관련 판매액 (월) | Monthly | 백만원 | ready_existing | 통계청/KOSIS | official_derived_sum | 112 | 2017-01 | 2026-04 | 1 | 2026-06-13 20:17:59 | KOSIS 공식 상품군 의복+신발+가방+패션용품 및 액세서리를 인터넷쇼핑 채널별 월 단위 합산. |
| epic:12:6 | 12 | 패션 · 명품 | 모바일 쇼핑 의류, 패션 관련 판매액 (월) | Monthly | 백만원 | ready_existing | 통계청/KOSIS | official_derived_sum | 112 | 2017-01 | 2026-04 | 1 | 2026-06-13 20:17:59 | KOSIS 공식 상품군 의복+신발+가방+패션용품 및 액세서리를 모바일쇼핑 채널별 월 단위 합산. |
| epic:12:10 | 12 | 패션 · 명품 | 베트남 의류, 신발 수출 금액 (월) | Monthly | 천달러 | ready_existing_partial | 관세청 customs | proxy_close | 375 | 2016-01 | 2026-05 | 3 | 2026-06-13 21:12:46 | 국가×품목 무역통계로 대체 가능하나 EPIC 정의와 품목 바구니 차이 검증 필요. |
| epic:13:20 | 13 | 교육 | 카드 결제액 추정치: 학원 (월) | Monthly | 억원 | ready_existing_partial | 통계청/KOSIS | official_quarterly_service_index_proxy | 714 | 2021-Q1 | 2026-Q1 | 34 | 2026-06-13 20:17:59 | EPIC 카드결제액 exact 원천은 미확정. 대체지표로 KOSIS DT_1KC2023 시도별 서비스업생산지수의 교육 서비스업 경상/불변지수를 분기 공식 프록시로 수집. |
| epic:13:21 | 13 | 교육 | 카드 결제액 추정치: 유아교육 (월) | Monthly | 억원 | ready_existing_partial | 통계청/KOSIS | official_quarterly_education_service_proxy_not_child_card_exact | 714 | 2021-Q1 | 2026-Q1 | 34 | 2026-06-13 20:49:41 | EPIC 원 지표는 유아교육 카드 결제액이나 exact 카드 업종 원천은 미확정. 대체지표로 KOSIS DT_1KC2023 교육 서비스업 생산지수를 분기 공식 프록시로 수집. 유아교육 또는 카드 결제 exact... |
| epic:13:22 | 13 | 교육 | 카드 결제액 추정치: 교육용품 (월) | Monthly | 억원 | ready_existing_partial | 통계청/KOSIS | official_online_education_goods_proxy | 112 | 2017-01 | 2026-04 | 1 | 2026-06-13 20:17:59 | EPIC 원 지표는 교육용품 카드 결제액이나 exact 카드 업종 원천은 미확정. 대체지표로 KOSIS DT_1KE10071 온라인쇼핑 서적+사무·문구 거래액(인터넷+모바일)을 월별 공식 프록시로 수집. 교육용... |
| epic:15:11 | 15 | Tech | 베트남 IT제품 수출 금액 (월) | Monthly | 천달러 | ready_existing_partial | 관세청 customs | proxy_close | 375 | 2016-01 | 2026-05 | 3 | 2026-06-13 21:12:46 | 국가×품목 무역통계로 대체 가능. HS 매핑 룰 설계 필요. |
| epic:16:110 | 16 | 건강관리 | 카드 결제액 추정치: 피부과 (월) | Monthly | 억원 | ready_existing_partial | 공공데이터포털/HIRA | official_annual_medical_proxy_not_card_exact | 5 | 2024 | 2024 | 5 | 2026-06-13 20:17:59 | EPIC 원 지표는 피부과 월별 카드 결제액 추정치이나 exact 카드사 원천은 미확정. 공식 대체지표로 건강보험심사평가원 진료과목별 진료 현황 2024년 연간 환자수/청구건수/입내원일수/보험자부담금/요양급여비... |
| epic:16:111 | 16 | 건강관리 | 카드 결제액 추정치: 성형외과 (월) | Monthly | 억원 | ready_existing_partial | 공공데이터포털/HIRA | official_annual_medical_proxy_not_card_exact | 5 | 2024 | 2024 | 5 | 2026-06-13 20:17:59 | EPIC 원 지표는 성형외과 월별 카드 결제액 추정치이나 exact 카드사 원천은 미확정. 공식 대체지표로 건강보험심사평가원 진료과목별 진료 현황 2024년 연간 환자수/청구건수/입내원일수/보험자부담금/요양급여... |
| epic:16:112 | 16 | 건강관리 | 카드 결제액 추정치: 치과 (월) | Monthly | 억원 | ready_existing_partial | 공공데이터포털/HIRA | official_annual_medical_proxy_not_card_exact | 5 | 2024 | 2024 | 5 | 2026-06-13 20:17:59 | EPIC 원 지표는 치과 월별 카드 결제액 추정치이나 exact 카드사 원천은 미확정. 공식 대체지표로 건강보험심사평가원 진료과목별 진료 현황 2024년 연간 환자수/청구건수/입내원일수/보험자부담금/요양급여비용... |
| epic:16:113 | 16 | 건강관리 | 카드 결제액 추정치: 약국 (월) | Monthly | 억원 | ready_existing_partial | 통계청/KOSIS | official_medicine_retail_sales_proxy_not_pharmacy_card_exact | 76 | 2020-01 | 2026-04 | 1 | 2026-06-13 20:48:09 | EPIC 원 지표는 약국 카드 결제액이나 exact 카드 업종 원천은 미확정. 대체지표로 KOSIS DT_1K41002 재별 및 상품군별 판매액의 의약품 월별 판매액을 공식 프록시로 수집. 약국 카드 결제액 e... |
| epic:17:17 | 17 | 조선 | 한국 후판가격 (주) | Weekly | 원/kg | ready_existing_partial | 관세청 HS Trade Lab | official_customs_unit_price_proxy | 250 | 2016-01 | 2026-05 | 2 | 2026-06-13 00:16:49 | 한국 후판가격 exact 주간 원천은 미확정. 현재는 관세청 itemtrade HS7208/7225 열연후판 수출/수입 단가를 USD/ton 월간 proxy로 적재(2016-01~2026-05, 250행). 투... |
| epic:19:50 | 19 | 에너지 · 정유화학 | United States Rig Count (주) | Weekly | 개 | ready_existing_partial | EIA | official_monthly_proxy | 640 | 1973-01 | 2026-04 | 1 | 2026-06-13 20:17:59 | Baker Hughes 주간 rig count exact 원천은 접속 제한/타임아웃으로 미연결. EIA NG_ENR_DRILL_S1_M 월간 U.S. rotary rigs in operation을 공식 월간 프... |
| epic:19:51 | 19 | 에너지 · 정유화학 | Canada Rig Count (주) | Weekly | 개 | ready_existing_partial | Baker Hughes NA Rig Count Monthly Excel | official_monthly_partial | 162 | 2013-01 | 2026-06 | 1 | 2026-06-13 00:25:41 | EPIC 원 지표는 Baker Hughes 주간 Canada Rig Count이나 현재 DB는 Baker Hughes North America 월간 Excel 기반 Canada active rigs 월간값(20... |
| epic:19:104 | 19 | 에너지 · 정유화학 | 싱가포르 석유제품 재고 추이 (주) | Weekly | 천배럴 | new_collector_needed | Enterprise Singapore StatLink / 유료 원천 후보 | paid_or_subscription_exact_candidate | 0 |  |  | 0 |  | Enterprise Singapore StatLink에는 weekly oil stock levels 관련 상품이 있으나 subscription/cart 기반이며, 확인 가능한 공개 메뉴는 Monthly Oil ... |
| epic:20:cpi | 20 | 금융 | 소비자물가지수 전체 | M | 2020=100 | ready_existing | ECOS |  | 317 | 2000-01 | 2026-05 | 1 | 2026-06-11 14:37:25 |  |
| epic:20:cpi_food | 20 |  | 소비자물가 식료품 | M | 2020=100 | ready_existing | ECOS |  | 329 | 1999-01 | 2026-05 | 1 | 2026-06-11 14:40:08 |  |
| epic:20:ppi | 20 |  | 생산자물가지수 총지수 | M | 2020=100 | ready_existing | ECOS |  | 316 | 2000-01 | 2026-04 | 1 | 2026-06-11 14:38:27 |  |
| epic:20:ppi_steel | 20 |  | 생산자물가지수 철강금속 | M | 2020=100 | ready_existing | ECOS |  | 316 | 2000-01 | 2026-04 | 1 | 2026-06-11 14:38:28 |  |
| epic:20:export_price | 20 |  | 수출물가지수 | M | 2015=100 | ready_existing | ECOS |  | 316 | 2000-01 | 2026-04 | 1 | 2026-06-11 14:39:03 |  |
| epic:20:trade_bal | 20 |  | 무역수지(백만달러) | M | 백만달러 | ready_existing | ECOS |  | 316 | 2000-01 | 2026-04 | 1 | 2026-06-11 14:38:30 |  |
| epic:20:exports | 20 |  | 수출액 | M | 백만달러 | ready_existing | ECOS |  | 328 | 1999-01 | 2026-04 | 1 | 2026-06-11 14:40:11 |  |
| epic:20:unemploy | 20 |  | 실업률 | M | % | ready_existing | ECOS |  | 316 | 2000-01 | 2026-04 | 1 | 2026-06-11 14:39:05 |  |
| epic:20:usdkrw | 20 |  | 원/달러 환율(월평균) | M | 원 | ready_existing | price_history |  | 40 | 2023-03 | 2026-06 | 1 | 2026-06-11 14:46:30 |  |
| epic:20:jpykrw | 20 |  | 원/엔 환율(월평균) | M | 원 | ready_existing | price_history |  | 198 | 2010-01 | 2026-06 | 1 | 2026-06-13 10:51:05 |  |
| epic:20:eurkrw | 20 |  | 원/유로 환율(월평균) | M | 원 | ready_existing | price_history |  | 198 | 2010-01 | 2026-06 | 1 | 2026-06-13 10:51:05 |  |
| epic:20:bond10y | 20 |  | 미국 국채(10년) | M | 연% | ready_existing | price_history |  | 26 | 2024-05 | 2026-06 | 1 | 2026-06-11 14:46:30 |  |
| epic:20:1 | 20 |  | 한국은행 기준금리 (월) | Monthly | % | ready_existing | 한국은행 ECOS | exact | 325 | 1999-05 | 2026-05 | 1 | 2026-06-13 20:17:58 | 기존 수집/표시 로직 존재. |
| epic:20:99 | 20 |  | 국내 주식시장 투자자 예탁금, 신용공여 추이 (월) | - | - | ready_existing | 한국은행 ECOS | exact_or_close | 424 | 2008-10 | 2026-05 | 2 | 2026-06-13 20:17:58 | 현재 ECOS/네이버 폴백 로직 존재. EPIC 대체 가능. |
| epic:20:22 | 20 |  | 국내 주식시장 대차잔고 (월) | Monthly | 백만원 | ready_existing | KRX/공공데이터 | exact_or_close | 114 | 2021-04 | 2026-06 | 2 | 2026-06-13 20:17:58 | 이미 일별/장기 수집 및 UI 사용 중. |
| public:20:101 | 20 |  | 소비자심리지수 CSI | Monthly | 지수/% | ready_existing | 한국은행 ECOS | official_ecos_exact | 197 | 2010-01 | 2026-05 | 1 | 2026-06-13 20:17:59 | ECOS 소비자동향조사 소비자심리지수. 소비/내수 업종과 시장 위험선호 보조 지표. |
| public:20:102 | 20 |  | 경제심리지수 ESI 순환변동치 | Monthly | 지수/% | ready_existing | 한국은행 ECOS | official_ecos_exact | 197 | 2010-01 | 2026-05 | 1 | 2026-06-13 20:17:59 | ECOS 경제심리지수 순환변동치. 경기 방향성 및 시장 레짐 보조 지표. |
| public:20:103 | 20 |  | 제조업 BSI: 업황/신규수주/전망 | Monthly | 지수/% | ready_existing | 한국은행 ECOS | official_ecos_exact | 592 | 2010-01 | 2026-06 | 3 | 2026-06-13 20:17:59 | ECOS 기업경기조사 제조업 업황실적·신규수주실적·업황전망 BSI. |
| public:20:104 | 20 |  | 제조업 재고율 | Monthly | 지수/% | ready_existing | 한국은행 ECOS | official_ecos_exact | 196 | 2010-01 | 2026-04 | 1 | 2026-06-13 20:17:59 | ECOS 제조업 재고율. 재고 부담과 제조업 사이클 판단 보조 지표. |
| public:20:105 | 20 |  | 전산업생산지수 SA | Monthly | 지수/% | ready_existing | 한국은행 ECOS | official_ecos_exact | 196 | 2010-01 | 2026-04 | 1 | 2026-06-13 20:17:59 | ECOS 전산업생산지수(농림어업 제외) 계절조정. 국내 경기 흐름 보조 지표. |
| public:21:1 | 21 |  | KOSPI 시장폭: 상승/하락/보합 종목수 | Daily | 종목/%/억원 | ready_existing | local price_history derived | derived_from_local_price_history | 9289 | 2021-01-05 | 2026-06-05 | 7 | 2026-06-13 20:17:59 | KOSPI 보통주 가격이력에서 상승종목수·하락종목수·상승종목비율·중앙수익률을 계산. 커버 500종목 미만 불완전 수집일은 제외. |
| public:21:2 | 21 |  | KOSDAQ 시장폭: 상승/하락/보합 종목수 | Daily | 종목/%/억원 | ready_existing | local price_history derived | derived_from_local_price_history | 9289 | 2021-01-05 | 2026-06-05 | 7 | 2026-06-13 20:17:59 | KOSDAQ 보통주 가격이력에서 상승종목수·하락종목수·상승종목비율·중앙수익률을 계산. 커버 500종목 미만 불완전 수집일은 제외. |
| public:21:3 | 21 |  | KOSPI 거래량 확산: 신고가/신저가/3배 거래량 | Daily | 종목/%/억원 | ready_existing | local price_history derived | derived_from_local_price_history | 7962 | 2021-01-05 | 2026-06-05 | 6 | 2026-06-13 20:17:59 | KOSPI 보통주 가격이력에서 20일 신고가수·20일 신저가수·거래량 3배 종목수·총거래대금을 계산. |
| public:21:4 | 21 |  | KOSDAQ 거래량 확산: 신고가/신저가/3배 거래량 | Daily | 종목/%/억원 | ready_existing | local price_history derived | derived_from_local_price_history | 7962 | 2021-01-05 | 2026-06-05 | 6 | 2026-06-13 20:17:59 | KOSDAQ 보통주 가격이력에서 20일 신고가수·20일 신저가수·거래량 3배 종목수·총거래대금을 계산. |
| epic:22:9 | 22 | 환경 | 대중교통 이용현황 (월) | Monthly | 명 | ready_existing_partial | 서울 열린데이터광장 | official_partial_subway_only | 117 | 2023-01 | 2026-05 | 3 | 2026-06-13 20:17:59 | 대중교통 전체 지표 중 지하철 승하차 합계만 서울 열린데이터광장 OA-12914로 우선 수집. 버스/기타 교통수단은 별도 공식 소스 연결 필요. |
| epic:22:10 | 22 | 환경 | 지하철 노선별 이용현황 (월) | Monthly | 명 | ready_existing | 서울 열린데이터광장 | official_exact | 3141 | 2023-01 | 2026-05 | 81 | 2026-06-13 20:17:59 | 서울 열린데이터광장 OA-12914 지하철호선별 역별 승하차 인원 CSV를 월별/노선별로 합산 수집. 2023-01~최신 공개월 커버. |
| public:23:1 | 23 |  | 자동차 완성차 수출입 | Monthly | 백만달러/USD/kg | ready_existing | 관세청 수출입 HS Trade DB | official_customs_hs_prefix_aggregate | 625 | 2016-01 | 2026-05 | 5 | 2026-06-13 20:17:59 | 관세청 HS 8703 월별 합산. 완성차 수출액·수입액·무역수지·단가를 섹터 총량으로 제공. 기업별 배분값이 아니라 HS 접두어 기준 섹터 총량이다. |
| public:23:2 | 23 |  | 자동차 부품 수출입 | Monthly | 백만달러/USD/kg | ready_existing | 관세청 수출입 HS Trade DB | official_customs_hs_prefix_aggregate | 625 | 2016-01 | 2026-05 | 5 | 2026-06-13 20:17:59 | 관세청 HS 8708 월별 합산. 자동차부품 수출입 업황 보조 지표. 기업별 배분값이 아니라 HS 접두어 기준 섹터 총량이다. |
| public:23:3 | 23 |  | 이차전지 리튬이온 수출입 | Monthly | 백만달러/USD/kg | ready_existing | 관세청 수출입 HS Trade DB | official_customs_hs_prefix_aggregate | 625 | 2016-01 | 2026-05 | 5 | 2026-06-13 20:17:59 | 관세청 HS 850760 월별 합산. 배터리 셀/모듈 수출입 사이클 보조 지표. 기업별 배분값이 아니라 HS 접두어 기준 섹터 총량이다. |
| public:23:4 | 23 |  | 메모리 반도체 수출입 | Monthly | 백만달러/USD/kg | ready_existing | 관세청 수출입 HS Trade DB | official_customs_hs_prefix_aggregate | 625 | 2016-01 | 2026-05 | 5 | 2026-06-13 20:17:59 | 관세청 HS 854232 월별 합산. DRAM/NAND/HBM 포함 메모리 반도체 수출입 지표. 기업별 배분값이 아니라 HS 접두어 기준 섹터 총량이다. |
| public:23:5 | 23 |  | 시스템 반도체 수출입 | Monthly | 백만달러/USD/kg | ready_existing | 관세청 수출입 HS Trade DB | official_customs_hs_prefix_aggregate | 625 | 2016-01 | 2026-05 | 5 | 2026-06-13 20:17:59 | 관세청 HS 854231 월별 합산. 비메모리/시스템 반도체 수출입 지표. 기업별 배분값이 아니라 HS 접두어 기준 섹터 총량이다. |
| public:23:6 | 23 |  | 반도체 제조장비 수출입 | Monthly | 백만달러/USD/kg | ready_existing | 관세청 수출입 HS Trade DB | official_customs_hs_prefix_aggregate | 625 | 2016-01 | 2026-05 | 5 | 2026-06-13 20:17:59 | 관세청 HS 8486 월별 합산. 반도체 장비 사이클 보조 지표. 기업별 배분값이 아니라 HS 접두어 기준 섹터 총량이다. |
| public:23:7 | 23 |  | 조선 상선 수출입 | Monthly | 백만달러/USD/kg | ready_existing | 관세청 수출입 HS Trade DB | official_customs_hs_prefix_aggregate | 625 | 2016-01 | 2026-05 | 5 | 2026-06-13 20:17:59 | 관세청 HS 8901 월별 합산. 선박 인도/수출 사이클 보조 지표. 기업별 배분값이 아니라 HS 접두어 기준 섹터 총량이다. |
| public:23:8 | 23 |  | 철강 72/73류 수출입 | Monthly | 백만달러/USD/kg | ready_existing | 관세청 수출입 HS Trade DB | official_customs_hs_prefix_aggregate | 625 | 2016-01 | 2026-05 | 5 | 2026-06-13 20:17:59 | 관세청 HS 72+73류 월별 합산. 철강 제품 수출입과 단가 보조 지표. 기업별 배분값이 아니라 HS 접두어 기준 섹터 총량이다. |
| public:23:9 | 23 |  | 화장품 수출입 | Monthly | 백만달러/USD/kg | ready_existing | 관세청 수출입 HS Trade DB | official_customs_hs_prefix_aggregate | 625 | 2016-01 | 2026-05 | 5 | 2026-06-13 20:17:59 | 관세청 HS 3304 월별 합산. 화장품 수출 업황 보조 지표. 기업별 배분값이 아니라 HS 접두어 기준 섹터 총량이다. |
| public:23:10 | 23 |  | 의약품 수출입 | Monthly | 백만달러/USD/kg | ready_existing | 관세청 수출입 HS Trade DB | official_customs_hs_prefix_aggregate | 625 | 2016-01 | 2026-05 | 5 | 2026-06-13 20:17:59 | 관세청 HS 3004 월별 합산. 완제의약품 수출입 업황 보조 지표. 기업별 배분값이 아니라 HS 접두어 기준 섹터 총량이다. |


## 13-1. 2026-06-14 추가 확장: 관세청 HS 섹터 지표 20개 보강

### 반영 결과

- 코드: `/Applications/stock_dashboard/scripts/ops/sync_quant_major_indicators.py`
- 자동수집 래퍼: `/Applications/stock_dashboard/scripts/ops/quant_indicators_cron.py` 월간 모드가 `CUSTOMS_SECTOR_QUANT_SPECS` 전체를 순회하도록 수정 완료
- DB 반영: `quant_major_indicator_catalog`, `quant_major_indicator_series`
- 신규/확장 키: `public:23:11`~`public:23:30`
- 총 관세청 섹터 지표: `public:23:1`~`public:23:30`, 30개
- 적재 결과: 30개 지표, 18,598행, 대부분 2016-01~2026-05 월별 커버
- 최신 전체 상태: `ready_existing 89 / ready_existing_partial 45 / new_collector_needed 1`
- 최신 전체 시계열: 106,445행 / 140 indicator keys

### 추가한 지표

| key | 지표 | HS prefix | 성격 |
| --- | --- | --- | --- |
| public:23:11 | OLED/평판디스플레이 모듈 수출입 | 8524, 901380 | 디스플레이 섹터 총량 |
| public:23:12 | PCB 인쇄회로 수출입 | 8534 | PCB/기판 섹터 총량 |
| public:23:13 | MLCC/다층세라믹콘덴서 수출입 | 853224 | MLCC 섹터 총량 |
| public:23:14 | 정유 석유제품 수출입 | 2710 | 정유/석유제품 섹터 총량 |
| public:23:15 | 원유 수입 | 2709 | 원유 수입액/단가 중심 |
| public:23:16 | LNG 수입 | 271111 | LNG 수입액/단가 중심 |
| public:23:17 | 석유화학 합성수지 수출입 | 3901~3907 | 석유화학 섹터 총량 |
| public:23:18 | 구리 원재료 수출입 | 7403, 7408 | 비철 구리 섹터 총량 |
| public:23:19 | 알루미늄 원재료 수출입 | 7601, 7606 | 비철 알루미늄 섹터 총량 |
| public:23:20 | 후판/열연강판 수출입 | 7208, 7225 | 조선/철강 후판·열연 보조 |
| public:23:21 | 선박용 디젤엔진 수출입 | 840810 | 조선 기자재 보조 |
| public:23:22 | 공작기계 수출입 | 8456~8466 | 설비투자/기계 업황 보조 |
| public:23:23 | 산업용 로봇 수출입 | 847950 | 자동화/로봇 업황 보조 |
| public:23:24 | 의료기기 수출입 | 9018 | 의료기기 섹터 총량 |
| public:23:25 | 진단시약 수출입 | 3822 | 진단/바이오 보조 |
| public:23:26 | 백신/바이오의약품 수출입 | 3002 | 바이오의약품 보조 |
| public:23:27 | 타이어 수출입 | 4011 | 자동차 부품/타이어 보조 |
| public:23:28 | 비료 수출입 | 3102~3105 | 화학/비료 보조 |
| public:23:29 | 의류 수출입 | 61, 62 | 소비재/의류 보조 |
| public:23:30 | 식품 가공품 수출입 | 1905, 2106 | 음식료 보조 |

### Claude 검토 포인트

- 모든 `public:23:*` 값은 기업별 매출 배분값이 아니라 관세청 HS 접두어 기준 섹터 총량이다.
- 원유/LNG처럼 수입 중심 품목은 수출액 0 또는 낮은 값이 정상일 수 있다. 이 경우 화면에서는 수입액/수입단가 중심으로 해석해야 한다.
- HS prefix가 넓은 지표(예: 공작기계 8456~8466, 합성수지 3901~3907)는 exact 기업 지표가 아니라 업황 proxy로만 사용해야 한다.
- 신규 20개 지표는 `ready_existing`이지만 “공식 섹터 총량”이라는 의미의 ready이며, 특정 기업의 실적 신호로 쓸 때는 별도 기업-HS 매핑 confidence를 반드시 함께 봐야 한다.

## 14. 검증 명령

```bash
cd /Applications/stock_dashboard
python3 -m py_compile scripts/ops/sync_quant_major_indicators.py scripts/ops/quant_indicators_cron.py
python3 - <<'PY'
import sqlite3
conn=sqlite3.connect('stock.db'); conn.row_factory=sqlite3.Row
for r in conn.execute("select status,count(*) c from quant_major_indicator_catalog group by status order by status"):
    print(dict(r))
PY
```
