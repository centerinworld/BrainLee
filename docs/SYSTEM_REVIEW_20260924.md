# 시스템 점검 리포트 (2026-09-24, Claude)

범위: ① 주식/수출입/트리거 데이터 오류 ② 시스템 개선·최적화 ③ 매수/매도 전략 오류·수익률 개선.
동시 작업 주의: 같은 시각 다른 세션이 `price_history`·`financial_data`·`corporate_action_events` 보정과
`scheduler.py`(키움 침묵실패)·`price_integrity.py`를 수정 중이어서, 이 세션은 해당 파일/테이블 쓰기를 피했다.

## A. 이번에 수정한 것 (코드)

| # | 파일 | 문제 | 수정 |
|---|------|------|------|
| 1 | `signal_engine.py` V10/V11/V12 | `market_cap BETWEEN 5e9 AND 2e13`, `> 5e10`(원 단위 가정) — 컬럼은 억원 → 0종목. `/api/signals/v10-earnings-explosion`·`v11-turnaround`·`v12-sector-megatrend` 항상 `[]` | 억원 기준(50억~20조 / 500억+)으로 수정, `market_cap_억` 표시값 `/1e8` 제거 → 40/31/40건 반환 확인 |
| 2 | `signal_engine.py` 분기 피벗 | `financial_data` 분기행에 CFS·OFS 공존(2,246종목, 64,022 (연,분기)). report_type 구분 없이 ROW_NUMBER → rn=2·3이 같은 분기, rn=5가 전년동기 아님 | `_load_quarterly_pl_pivot()`: 종목별 최신 분기 보유 report_type 1개(동률 CFS)만 사용, 5분기 연속(`span5==4`)일 때만 YoY |
| 3 | `signal_engine.py` V10 수급 | `SUM(...) ... ORDER BY date DESC LIMIT 5` — LIMIT이 집계 1행에 걸려 **상장 이후 전체 누적** 수급이 점수에 들어감 | 최근 5거래일 서브쿼리 |
| 4 | `signal_engine.py` `_load_universe_maps`/`_get_stock_sector` | stock_universe 스냅샷 중 섹터가 빈 2026-09-04 행을 집어 **2,713종목 중 28종목만 섹터 보유** → 섹터 기반 신호 전반 무력화 | `_load_latest_universe_rows()`(최신 행 + 과거 스냅샷 섹터 상속). 섹터 보유 2,646종목 |
| 5 | `stock_universe.py` `update_from_krx` | 월간 갱신이 새 base_date 행을 빈 채로 생성 → 다음 달 또 섹터 없는 스냅샷 | 직전 스냅샷에서 섹터·상장일·결산월 등 정적 필드 상속 |
| 6 | `scripts/build_corporate_action_adjustment_engine.py` | SQLite 전용 `CREATE VIEW IF NOT EXISTS` → PG SyntaxError가 **2026-08-07부터 매일**. 같은 체인의 시장국면(`market_regime_daily` 8/07 정지)·설명형신호·전략센터 전진신호·신호사후성과·전진검증감사까지 전부 미실행. DROP만 성공해 `stock_price_daily_adjusted_v` 뷰 소실 → `basis=confirmed_actions_adjusted` 가격 API 오류 | plain `CREATE VIEW`. 뷰는 트랜잭션+ROLLBACK으로 PG 검증 완료. 오늘 밤 스케줄 실행부터 체인 재개 |
| 7 | 같은 파일 ON CONFLICT | 재가동 시 후속 매칭(terp/ratio_reduction/marcap)이 factor_confirmed로 확정한 행을 review_required로 되돌릴 위험 | 이 스크립트 원행(source=`stock_price_daily_shares[+DART]`)만, 확정→미확정 강등 금지 |
| 8 | 같은 파일 뷰 + `backtest_common._load_corp_action_factors` | 조정계수 이중·다중 적용: ① 같은 날 두 파이프라인이 각각 확정(40건) ② **유상증자 정정공시마다 같은 계수를 날짜만 달리 확정(62종목 277쌍, 예 001140 0.7905×7회 → 누적 0.19)** | 60일 내 동일 계수는 마지막 1건, 같은 날·같은 방향은 DART 우선 1건 |
| 9 | `routes/market_radar.py` 반도체 요약 PSR | `market_cap / 1e8`(이미 억원) → PSR 항상 누락 | 억원 그대로 사용 |
| 10 | `routes/trend.py` 손절 | 분할/병합일 가격 불연속을 손실로 보고 자동손절(코람코더원리츠 417310: 8/28 10,930→2,290, v_gc **-78.6% 손절** 기록) | 보유기간 중 종가비가 ±30%(가격제한폭) 밖이면 공통 하드스탑·GC·CM·REC 자동손절 보류 + `[손절보류]` 경고 |

데이터 쓰기: `stock_universe` 2026-09-04 스냅샷 2,666행의 NULL 섹터/stock_type을 같은 종목 직전 스냅샷 값으로 채움
(`data_fix_log` run_id `su_sector_inherit_20260924_135156`, 비-NULL 값은 덮지 않음).

테스트: `tests/test_price_integrity.py`, `test_suspension_gap_gate.py`, 전략/백테스트/리스크게이트 관련 8개 파일 — 110 passed.
**서버 재시작 전까지 1~5·9·10은 미반영**(6~8의 스크립트는 subprocess라 오늘 밤 자동 반영).

## B. 발견했지만 수정하지 않은 것 (사용자 판단/조치 필요)

### B1. crontab 잡 전체 중단 (2026-08-29경~) — 최우선
`/var/mail/brainlee`: 모든 크론 잡이 `Operation not permitted`(외장 볼륨 `/Volumes/Realtek_NVME` 쓰기 거부, macOS TCC).
영향: 퀀트지표 daily/weekly/monthly(`logs/quant_daily.log` 8/28 정지), HS 수출입 daily_refresh(8/29 정지),
telegram_collector/monitor, detailed_analysis 멀티채널 갱신, cron_3am, weekly_revalidation, ETF 재시도 3종, gemini_gems_worker.
조치(보안 설정이라 사용자가 직접): 시스템 설정 → 개인정보 보호 및 보안 → 전체 디스크 접근 권한 → `/usr/sbin/cron` 추가.
대안: 크론 잡을 LaunchAgent로 이전(서버 LaunchAgent는 볼륨 접근 정상).
부수: HS daily_refresh 요약의 `csv_dir`가 금지 경로 `/Applications/stock_dashboard/hs_trade_lab/market_radar_exports`.

### B2. 데이터 공백·오염
- `price_history` 투자자 수급 2026-09-14~09-18: 거래일당 ~2,300/2,641종목 `inst/frn/ind_net_buy_amt` NULL
  (대형주 일부만 오늘 채워짐). `kiwoom_investor_daily`에 같은 기간 2,764종목 금액(백만원, orgn/frgnr_invsr/ind_invsr)이 있어 백필 가능.
  수량 컬럼은 원천 없음. 다른 세션이 price_history를 쓰는 중이라 미실행.
- `price_history` 9/14부터 종목 수 2,685→2,641 (SFA반도체·한국첨단소재·카프로 등 44종목 수집 누락).
- `stock_universe`: 종목당 2~3행(스냅샷). 237개 파일이 base_date 없이 `WHERE stock_code=?`/JOIN → 중복 행·임의 행 선택.
  근본안: 과거 스냅샷을 `stock_universe_history`로 옮기고 본 테이블은 종목당 1행.
  `market_cap`도 3월 값이 유지돼 stale(예: 삼성전자 저장 1,526조 vs 현재가×주식수 ~1,669조).
- `sector_large`에 KOSDAQ 소속부 값 혼입(우량기업부 37·중견기업부 48·벤처기업부 20·기술성장기업부 12 등 ~130종목).
- 멈춘 테이블: `market_regime_daily` 8/07(→A6로 재개), `triple_pattern_daily` 7/27, `signal_result` 8/10,
  `foreign_holding_daily` **6/08**(CLAUDE.md는 "정상 적재 중"으로 기재 — 실제는 3.5개월 정지), `valuation_history` 2026Q1까지,
  `strategy_feature_snapshot` 7/24(8월말 스냅샷 없음). `tenbagger_daily_alerts` 7/28은 `ENABLE_BQ_MORNING_ALERT` 미설정(의도).
- `corporate_action_events` TERP 매칭 품질: 001140은 2022~2025 확정 계수 16건(0.54~0.82)으로 여전히 과다 — A8은 완전 동일 계수만 병합.
  권리락일 실제 가격 하락과 계수를 대조하는 검증이 필요(다른 세션 작업 영역).
- `peak_trade` 두 스키마 혼용(main.py: amount/tx_date, trend.py: total_amount/tx_at), `peak_holding.hold_days` 대부분 0/이상치(33,852 등).

## C. 매수/매도 전략 진단 (가상매매 `peak_holding` 청산 307건)

전체: 승률 24.1%, 평균 -3.36%. 평균 이익 +14% / 평균 손실 -6~8% → 손익비 ~2:1에서 손익분기 승률 ~33% 필요.

| 진입일 KOSPI | 건수 | 평균 | 승률 |
|---|--:|--:|--:|
| MA60 위 | 154 | -0.59% | 31.8% |
| MA60 아래 | 152 | **-5.68%** | 16.4% |

| 진입일 등락 | 건수 | 평균 | 승률 |
|---|--:|--:|--:|
| ≥+15% | 36 | -1.83% | 30.6% |
| +8~15% | 57 | +2.94% | 29.8% |
| +3~8% | 83 | -1.34% | 27.7% |
| 0~+3% | 80 | -6.27% | 18.8% |
| 하락일 | 50 | -8.85% | 16.0% |

오류:
1. 시장국면 게이트 부재 — 가상매매 전략은 국면을 보지 않고, KIS 페이퍼 리스크게이트도 KOSPI<MA120×0.85(패닉)에서만 차단.
   7~8월 KOSPI -25%/KOSDAQ -35% 구간에 신규 진입이 계속됨. 게다가 국면 테이블은 8/07부터 갱신 정지(A6).
2. 기업행위 불연속을 손실로 처리(A10). 3. value 전략은 손절 없이 -24~-36%까지 보유 후 8/26 일괄 청산.
4. 동일 종목 다중 전략 중복 보유(현재 8종목에 25포지션, 한미사이언스 8/24 급등일 3개 전략 동시 매수 → 익일 -18%).
5. V10/V11/V12(sc_v10 등 보유 중)는 신호 소스가 몇 주간 `[]`였고 YoY 비교 기준도 틀렸음(A1~A3) — 기존 보유분 진입 근거 재검토 필요.

수익률 개선안(우선순위, 모두 백테스트로 검증 후 적용 권장):
1. **국면 필터**: KOSPI<MA60(또는 `market_regime_daily.market_regime` 약세)일 때 모멘텀/돌파 계열 신규 진입 중단 또는 비중 50%.
   위 표 기준 손실 거래의 대부분이 이 구간.
2. **확인형 진입**: 진입일 등락 0~3%/하락일 진입이 가장 나쁨 → 모멘텀 전략은 당일 +3% 이상·거래량 동반 또는 익일 시가 확인 진입.
3. **손익 비대칭 개선**: +10~15% 도달 후 본전 스톱(breakeven)으로 올리기. GC는 +5%~+40% 구간에서 -12% 손절까지 이익 전부 반납 가능.
4. **종목 단위 노출 한도**: 전략 합산 1종목 최대 1~2포지션, 섹터 합산 한도(리스크게이트 sector_concentration을 가상매매에도).
5. **value 전략 손절/재평가**: 공통 하드스탑(-10%) 적용 여부 확인, 밸류트랩 필터(이익 추정 하향·수급 이탈).
6. 백테스트 조정계수 이중 적용(A8)이 turnaround/regime_adaptive/composite 결과를 왜곡했으므로 해당 전략 재백테스트 후 채택 여부 재판정.

## D. 2차 점검 (같은 날 오후, 다른 세션 작업 영역 제외)

### D1. PostgreSQL 이관 시 컬럼 DEFAULT 유실 — 366/382개 컬럼, 172개 테이블
레거시 SQLite DDL의 `DEFAULT CURRENT_TIMESTAMP / 0 / 1 / 'CFS' / 'PAPER' …`가 PG에 하나도 안 넘어와,
컬럼을 생략한 INSERT가 NULL을 저장해 왔다. 피해가 확인된 곳:
- `earnings_signals`: 8월 이후 1,629행 `is_active/telegram_sent/created_at` 전부 NULL → 실적신호 화면(`is_active=1`)·
  텔레그램 발송(`created_at>=cutoff AND telegram_sent=0`)에서 **신규 신호가 전혀 안 보이고 알림도 0건**이었다.
- `dart_contracts.telegram_sent` 355, `order_contracts.verified` 472, `global_macro_categories.is_active` 27,
  `dart_insider_holdings.is_significant` 17, `sector_posts.telegram_sent` 3.
- 타임스탬프 NULL: `financial_data.created_at` 269(→ 실적신호 스캔이 신규 DART 행을 못 봄), `backtest_runs` 3,118,
  `backtest_run_specs` 3,258, `price_history` 206만 등.
조치: 360개 컬럼 `ALTER ... SET DEFAULT` 복구(lock_timeout 1.5s, 실패 0). 타임스탬프는 로컬시각 텍스트
`to_char(now(),'YYYY-MM-DD HH24:MI:SS')`. **`price_history`의 수급/거래대금 숫자 컬럼은 제외**(NULL=결측 의미 보존).
플래그 NULL 백필(data_fix_log `restore_null_defaults_20260924_175758`): telegram_sent→1(과거분 재발송 방지),
is_active는 earnings_signals만 수정된 탐지기로 재판정(유효 844/무효 785), global_macro_categories 1, verified·is_significant 0.

### D2. 실적 트리거(`collectors/earnings_signal_detector.py`) 오류 3건
1) TTM = `LIMIT 4`에 report_type 조건 없음 → CFS+OFS 두 분기를 두 번씩 합산(기아 TTM 영업이익 3.49조 vs 실제 8.27조).
2) `TTM_OP_INFLECT`(흑자전환)가 `_get_yoy_ttm(year-1)` 이중 감산으로 **2년 전** TTM과 비교.
3) 3개 분기 합도 TTM 인정, QoQ 매출이 CFS/OFS 임의 선택.
수정 후 재판정: 활성 1,428건 중 1,029건(72%)이 조건 불충족 → is_active=0
(data_fix_log `earnings_signal_revalidate_20260924_141447`, id 목록 `/Volumes/Realtek_NVME/stock_dashboard/logs/data_fix/`).
스캔 대상 선정도 `created_at` → `COALESCE(updated_at, created_at)`.

### D3. CFS/OFS·연간 중복행 혼재 (같은 부류의 나머지 소비처)
분기 2,246종목, 연간 1,185종목이 같은 기간에 CFS·OFS 공존. 연간은 추가로 `quarter=0`(FnGuide)/`4`(DART)로 이중 저장
(12,537 그룹, 3,081 그룹은 매출이 다름). 수정:
- `tenbagger_engine`: 매출/영업이익/EPS/D&A QoQ(같은 분기 CFS vs OFS를 'QoQ'로 계산 — 삼성전자 2026Q1 가짜 +22%),
  연간 성장률(`_fetch_financials`), 원가율 YoY(`LIMIT 4`라 전년동기가 결과에 없어 **한 번도 계산 안 됨**) → LIMIT 8.
- `signal_engine`: `_calc_financial`, `calc_stockeasy_trend_candidates`, `_calc_earnings_quality`, `calc_combo_v2` QoQ 보너스.
- `screener.py` 8분기 스코어, `routes/tenbagger.py` 종목 브리프(분기·연간·현금흐름), `scripts/tenbagger_morning_alert.py`.
- D&A QoQ는 누적/분기 혼재 컬럼 `depreciation` 대신 `depreciation_q` 사용.

### D4. 자사주 트리거 `treasury_buyback`
- PG 전환 후 적재 경로가 없어 2026-07-10에서 정지 → `scripts/ops/sync_treasury_buyback_from_dart.py` 신설,
  `refresh_dart_disclosures_recent.py`(하루 4회) 끝에서 자동 호출. 공백기 590건 적재.
- event_type 어휘 혼재(취득결정/acquisition/trust/기타)로 텐버거 엔진이 신탁매입·영문 라벨을 누락 →
  정규 분류 `event_class` 컬럼 추가(취득결정·취득결과·신탁체결·신탁해지·처분결정·처분결과·소각·기타), 라이브 소비처 전환.
  event_type·과거 누락분(약 3,300건, `--backfill-history`)은 백테스트 재현성 때문에 손대지 않음.
- `rcept_dt` 'YYYY.MM.DD' 4건 정규화.
- `dart_contracts.disclosed_at`(YYYYMMDD) vs `date('now','-365 days')`(YYYY-MM-DD) 문자열 비교로 1년 창이 ~1.7년 → 파라미터화.

### D5. 기타
- `bigquery_sync.py` 분석 뷰 12곳 시총 단위(억원을 원으로 가정) 수정(현재 `ENABLE_BIGQUERY_DAILY` 비활성).
- 최신가 상관 서브쿼리(전 종목 스캔) → LATERAL: `/api/earnings-signals/stats` 10.0s→0.08s, `/latest` 5.0s→0.04s,
  `scripts/tenbagger_trigger_alert.py`. **`scheduler.py` 5660행대 실적신호 텔레그램 쿼리에 같은 패턴 잔존(다른 세션 편집 중이라 미수정).**

### D6. 발견만 (다른 세션 영역)
- `cash_flow_data.depreciation_q` 이상치: 삼성전자 10.7→15.4→11.0→6.5→**30.7조**(2025Q1~2026Q1).
- `financial_data` 042700 2026Q1 매출 509억(정수 반올림값, `dart_document_fallback`) vs 전후 분기 830억/2,512억 — 단위 의심.
- DB 락 적체: `check_and_backfill_daily_coverage.py`가 트랜잭션을 연 채 대기(idle in transaction 2분) + `price_integrity.ensure_schema`의
  `CREATE OR REPLACE FUNCTION guard_historical_price_write`가 매 실행 price_history 락을 요구 → 뒤의 모든 price_history 조회가 줄서며
  API가 statement timeout. DDL은 변경 시에만 실행하고 `lock_timeout`을 두는 것을 권장.
