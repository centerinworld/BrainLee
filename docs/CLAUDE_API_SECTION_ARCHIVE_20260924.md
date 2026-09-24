

## 섹션 3 API 수기 목록 원문

## 3. API 엔드포인트 전체 목록

### main.py 직접 정의 엔드포인트
```
GET  /api/realtime/prices                  # KIS 실시간 주가 캐시
GET  /api/realtime/macro                   # 거시지표 실시간
GET  /api/dashboard/market-info/{code}     # 종목 시장정보 (sector, mktcap, 순위)
GET  /api/dashboard/chart/{code}           # 주가 차트 데이터
GET  /api/dashboard/sectors                # 섹터 목록
GET  /api/dashboard/screening/triple       # 3단계 스크리닝
GET  /api/dashboard/screening/logic        # 로직 스크리닝
GET  /api/dashboard/financial-table/{code} # 재무제표 테이블
GET  /api/dashboard/cashflow/{code}        # 현금흐름
GET  /api/dashboard/disclosures/{code}     # 공시 목록
GET  /api/dashboard/fundamentals/{code}    # PER/PBR (Naver 스크래핑 포함, 비동기 캐시)
GET  /api/dashboard/macro                  # 거시지표 캐시
GET  /api/dashboard/stats                  # 시스템 통계
GET  /api/search                           # 종목 검색
POST /api/reports/generate/{code}          # AI 리포트 생성
GET  /api/reports/latest/{code}            # 최신 AI 리포트
GET  /api/reports/ready                    # 리포트 준비 상태
POST /api/commands/refresh-cashflow/{code}
POST /api/commands/refresh-annual/{code}
POST /api/commands/monthly-bulk-update
POST /api/commands/daily-disclosure-check
POST /api/commands/screener-refresh
POST /api/commands/analyze/{stock_name}
GET  /api/commands/collect-status/{code}
GET  /api/commands/watchlist
DELETE /api/commands/watchlist/{code}
POST /api/commands/batch-float-shares
GET  /api/commands/batch-float-shares/status
GET  /api/kiwoom/status
POST /api/kiwoom/token/refresh
POST /api/kiwoom/realtime/snapshot
POST /api/kiwoom/foreign-flow
POST /api/kiwoom/investor/collect   # ka10059: 종목별 투자자 일별 수급
POST /api/kiwoom/stock-info/update  # ka10001: 종목 PER/PBR/ROE/유동주식수
POST /api/kiwoom/stock-universe/bulk-update  # ka10001 배치: 전종목 갱신
GET  /api/kiwoom/investor/status    # kiwoom_investor_daily 적재 현황
GET  /api/kiwoom/data-status
```

### routes/signals.py → /api/signals
```
GET  /market             # 시장 시그널 (캐시키: 'market', TTL 1800초)
GET  /market-regime      # 5단계 시장국면 점수 + 강제하향 + AI 브리핑
POST /market-regime/briefing # 시장국면 AI 브리핑 수동 생성
GET  /stock/{code}       # 종목 시그널 (캐시키: 'stock_{code}')
GET  /trend-candidates   # 추세 후보 (캐시키: 'trend')
GET  /value-candidates   # 가치 후보 (캐시키: 'value')
GET  /combo-candidates   # AI 콤보 후보 (캐시키: 'combo_candidates')
GET  /fin-screener       # 재무 스크리너
GET  /trigger-ranking    # 트리거 20 (캐시키: 'trigger')
GET  /kiwoom-conditions  # 키움조건식 5가지 퀀트 전략 (params: strategy=all|value_blue|supply_momentum|growth_garp|high52_break|contrarian, 캐시키: 'kiwoom_cond_{strategy}', TTL 1h)
GET  /meta               # 스크리너 메타정보
GET  /config             # 시그널 설정
PUT  /config/{id}        # 설정 수정
POST /config             # 설정 추가
DELETE /config/{id}      # 설정 삭제
POST /manual/{id}        # 수동 실행
GET  /overheat-risk      # 60일수익률+100%초과 과열종목 (캐시 30분) ★2026-07 신규(문서 누락 소급기재)
GET  /consensus-revisions # 컨센서스 목표주가 상향조정 종목 (params: days=60, limit=60) ★신규(2026-08-23)
```

### routes/trend.py → /api/trend (가상매매)
```
GET    /holdings              # 보유종목 (현재가: price_history 최신 close)
POST   /buy                   # 매수
POST   /sell                  # 매도
POST   /update                # 현재가/수익률 업데이트
GET    /trades                # 거래내역
GET    /summary               # 요약 (승률, 수익)
DELETE /trades/all            # 전체 삭제
GET    /gc/recommendations    # V12 골든크로스 가상매매 추천 (strategy='v_gc') ★신규(2026-07-08)
POST   /gc/execute            # V12 골든크로스 즉시 실행 ★신규(2026-07-08)
GET    /rec/recommendations   # V-RECOVERY 낙폭반등 가상매매 추천 (strategy='v_recovery')
POST   /rec/execute           # V-RECOVERY 즉시 실행
GET    /combo/{key}/status    # 병합조합 가상매매 현황(구성전략/매수후보/매도후보) ★신규(2026-07-23)
POST   /combo/{key}/execute   # 병합조합 가상매매 즉시 실행(매도→매수) ★신규(2026-07-23)
```
- **⛔ 2026-07-23 삭제(저효율 확인)**: `ai-combo/execute`(strategy='ai_combo', 승률23%·누적-20.7M)/`v18/recommendations`·`v18/execute`(strategy='gpt_v18', 승률27%·누적-8.6M)/`turnover/*`(strategy='turnover_100m'·'turnover_auto_100m', 1건뿐 또는 0건) — 엔드포인트 코드는 남아있으나 스케줄러 루프(`_loop_v14_10m`) 비활성화, 프론트 STRATEGIES 버튼 제거. 오픈포지션 1건(gpt_v18, 안국약품)은 +7.22%에 청산 후 종료. 대체: 아래 병합조합 4종.
- **V12 골든크로스**: strategy='v_gc', MA20↑MA60(15일내)+거래량1.2x+RS6M>-20%+시총2000억+, Trail-25%/손절-12%/300일, 1억원 예산/종목당1000만원/최대8종목. 20분 주기 장중 자동실행. avg6=+47.6%, 6/6기간 양수.
- **⛔ 병합조합 가상매매(2026-07-23 신규, 2026-08-23 이후 사실상 중단)**: 전략센터 "전략 조합" 탭에서 `persist_merged_run`으로 등록된 4개 검증조합(605.05%/539.18%/510.12%/473.87%)을 각각 독립 1억원 가상계좌(`combo_605`/`combo_539`/`combo_510`/`combo_474`)로 실행하던 구조. `scheduler.py`의 `_loop_combo_daily`/`_job_combo_daily`는 스레드 이름은 유지한 채 내부 구현이 아래 "전략센터 상위5 가상매매"로 교체됐고("과거 고정 병합조합은 더 이상 스케줄하지 않으며" — 코드 주석), 이 4개 combo_* 계좌는 **2026-08-23 09:37 마지막 실행 이후 한 번도 재실행되지 않아 포지션이 그대로 방치돼 있다**(2026-09-07 확인: 시세만 오늘 기준으로 평가돼 -2.9%~+8.82%로 보이지만 살아있는 검증이 아님). 이 전환이 언제·왜 결정됐는지, combo_* 4개 계좌를 정리(청산/보존)할지는 아직 미정 — `/api/backtest/combinations/list`의 4개 등록조합 자체도 2026-09-07 재검토 결과 605.05%/671.8%/601.0%×2가 동점 타이브레이크 안정성 미검증 상태였음(`research_outputs/merged_account_tiebreak_review_20260907.md` 참조).
- **전략센터 상위5 가상매매(2026-08-26 신규, 위 병합조합 대체)**: 고정 조합 대신 전략센터 매트릭스에서 그날 상위 5(현재는 sc_golden_cross/sc_sector_focus/sc_v2/sc_v5/sc_v8/sc_v10/sc_contract_momentum 중 상황에 따라 선정)를 매일 재선정해 각각 독립 1억원 가상계좌로 실행(`routes/trend.py execute_strategy_center_top_five_now`/`_execute_strategy_center_paper`, `STRATEGY_CENTER_PAPER_ENGINES`). 매일 18:35 `_loop_combo_daily`(스레드명 유지) 자동 실행.
  ⚠️ **2026-09-07 발견·수정: 6개 계좌 전부 설정 이후(8/26~28) 3주 가까이 매수 0건이던 근본 원인 2가지, 모두 리스크게이트 실행계층 버그(전략 신호 로직은 정상이었음)**:
  1. `routes/trend.py _execute_strategy_center_paper`: 고정 매수단위(`STRATEGY_CENTER_PAPER_TICKET_KRW`=1천만원)가 `_gate_volatility_sizing`의 종목당 리스크한도(자본×1.2%÷가정손절20%=자본의 6%, 1억원 계좌 기준 600만원)를 항상 구조적으로 초과 → 게이트 판정이 매번 최선이어도 `SIZE_REDUCED`(전액매수 불가·축소는 가능)인데, 실행 루프가 정확히 `BUY_ALLOWED`만 받아들여 SIZE_REDUCED를 매수거부로 취급하고 있었음. 게이트가 제시한 한도로 수량을 줄여 재검증하도록 수정.
  2. `routes/kis_trading.py _gate_sector_concentration`: KOSPI/KOSDAQ의 절반(2,686/5,379종목)이 `stock_universe.sector_large` NULL → "섹터 판단불가"가 뜨는데, `evaluate_risk_gates(strict_for_execution=True)`는 판단불가 게이트가 하나라도 있으면 BUY_ALLOWED를 WAIT_CONFIRM으로 강등하는 정책이라 이것만으로도 매수가 막혔음. 안전정책은 그대로 두고, 이미 DB에 있는 `stockeasy_sector_membership`(94% 커버)을 폴백으로 추가.
  검증: golden_cross 2026-09-02 신호(8종목) 재실행 시 수정 전 8/8 거부 → 수정 후 4/8 BUY_ALLOWED(나머지 4개는 수급이탈·갭위험 등 진짜 리스크 사유로 정당하게 차단 — 안전정책 우회 아님). `scripts/safe_restart_backend.sh`로 반영 완료.
  ⚠️ **v8 별도 원인(2026-09-07 수정)**: 위 게이트 버그와 무관하게 `hs_trade_lab.db`의 `trade_series_cache`(수출YoY 집계)가 2026-03에서 멈춰있었음 — 원본 `customs_monthly_record`는 실제로 2026-07까지 있었는데 집계 스크립트(`hs_trade_lab/scripts/backfill_trade_series_cache.py`)가 재실행 안 됨. `_date_to_ym`의 2개월 지연 설계와 겹쳐 6월부터 v8 신호가 전부 죽었던 것 — 재실행(멱등, `ON CONFLICT...DO UPDATE`)으로 2026-07까지 갱신, v8 재검증 결과 8월 거래 재개 확인.
  ⚠️ **HS 무역통계 다중매핑 중복계상 버그(2026-09-07 발견, 09-07 중 2차례 수정, 사용자 질문/피드백으로 발견)**: 사용자 지적("유니드는 독점이지만 필러는 성남에 여러 업체") 확인 결과, `hs_code_company_map`은 HS코드 313개 중 193개(62%)가 2개 이상 기업에 매핑돼 있음(최대 33개사 — 반도체 웨이퍼장비 HS '848620'). 기업별 실제 점유율을 담는 `market_share_pct` 컬럼이 존재하지만 941건 전부 미입력 상태였는데도, `backtest_common.py _load_trade_signals()`는 매핑된 각 기업에게 해당 HS코드 수출액 "전액"을 그대로 부여하고 있었음(예: 필러 HS '300190' 5개사 전부에게 같은 총액을 중복 부여) — 실제 수출 규모가 과다계상돼 v8의 수출YoY 신호가 왜곡돼 있었음.
  1차 수정(매핑 기업 수로 균등분할)에 대해 사용자가 "균등 분할도 위험하다 — 33개사가 똑같이 1/33씩 수출하는 게 아니다"고 재지적. 2차 수정: `_load_company_revenue_map()` 신설, HS그룹 내 기업들의 **최근 연간 매출액(`financial_data.revenue`, CFS 우선 최신연도 1건) 비례**로 가중치 산정 — 매핑 대상 398개사 중 384개(96.5%)에서 매출 데이터 확인. 매출 데이터 없는 개별 기업은 같은 그룹 내 매출 확인된 기업들의 평균값으로 대체(imputation)해 그룹 가중치 합이 항상 1이 되도록 하고, 그룹 전체에 매출 데이터가 없는 예외적 경우만 기존 균등분할로 폴백. 기업 "전체" 매출 비중이지 해당 HS 품목만의 매출 비중은 아니므로 여전히 근사치이나, 균등분할보다 기업 규모 차이를 반영하는 훨씬 나은 proxy. market_share_pct가 채워지면 항상 최우선.
  이 작업 중 `financial_data`에서 **CFS/OFS 중복 미제거로 매출액이 실제로 두 배로 잡히는 것**을 재확인(예: 메디톡스 2025년 revenue가 CFS 247,290,289,368 / OFS 222,046,566,421 두 행 다 IN절 조회에 걸림) — se_momentum.py에서 발견된 것과 동일 부류의 버그. `_load_company_revenue_map()`은 `ORDER BY stock_code, year DESC, CASE report_type WHEN 'CFS' THEN 0 ELSE 1 END` + 종목당 1건만 채택하는 방식으로 dedup 처리. 같은 부류 버그가 다른 곳에도 더 있는지 서브에이전트로 전수 감사(아래 "CFS/OFS 미탈리브레이크 감사" 항목 참조) — 6곳 추가 발견·미수정(감사만, 수정은 별도 승인 필요).
  ⚠️ **3차 수정(2026-09-08, 사용자 재지적 — "매출비례도 좋지만 상관계수 등도 고려", "균둥분할 같은 게 다른 곳에도 더 있을것")**: 두 건 추가 발견·수정.
  (a) **조인 불일치로 인한 무응답 버그(더 찾기 어려운 유형 — "틀린 값"이 아니라 에러 없이 조용히 신호가 0으로 비어있는 형태)**: `hs_code_company_map`이 hs_code를 4/6/10자리 뒤섞어 저장하는데 `trade_series_cache`는 관세청 원본대로 10자리로만 존재 — 문자열 완전일치 조인이라 4/6자리 축약 항목(69건)은 절대 매칭 불가, 10자리 항목 중 35건도 세번코드 뒷자리 표기가 실제 관세데이터와 미세하게 달라(예: map '2849201000' vs 실제 '2849200000') 매칭 실패. 매핑 313개 HS코드 중 113개(36%)가 이 문제로 단 한 번도 매칭 안 됐고, 그 결과 S-Oil/SK이노베이션/DB하이텍/LX세미콘/티씨케이 등 398개사 중 **97개사(24%)가 애초에 무역 신호를 영구히 못 받고 있었음**. 확인해보니 113개 전부 앞 6자리(HS 6단위, 국제표준)는 원본 데이터에 존재 — 데이터가 없는 게 아니라 표기 정밀도 문제였음. 조치: ① `hs_trade_lab/scripts/backfill_trade_series_cache.py --all-hs` 재실행으로 원본 관세데이터 전체(10자리 125만행 — 기존엔 hs_code_company_map과 정확히 일치하는 코드만 22,123건 담고 있었고, 그마저 `mapping_status='confirmed'` 필터가 실제로는 한 번도 쓰인 적 없는 값이라 무의미했으며 'provisional' 103건까지 암묵적으로 배제되고 있었음)를 캐시에 반영, ② `_load_trade_signals()` 조인을 "hs_code 완전일치" → "6자리 접두사 일치(4자리 map항목은 4자리 접두사)"로 변경. 검증: 위 97개사 전부 신호 복원 확인.
  (b) **상관계수 필터 추가(재설계)**: 최초엔 상관계수를 매출비례 가중치에 곱하는 "완만한 confidence"로 넣었으나, 사용자가 "수출이 늘어도 실제 매출로 이어지는 기업도 있고 아닌 기업도 있으니 상관계수가 충분히 높은 기업만 적용하고 관련 없는 기업엔 아예 적용하지 말라"고 재지적 — 가중치를 깎는 방식에서 **완전 제외(하드 필터)** 방식으로 재설계. `_load_company_quarterly_revenue_yoy()`(CFS우선 dedup)로 각사 분기매출YoY를 구해 그룹 수출YoY와의 상관계수를 계산(`_MIN_CORR_SAMPLES`=8분기 미만이면 "관련없다는 증거 부족"으로 중립 포함), `_MIN_CORR`=0.2 이하인 기업은 해당 HS그룹 신호에서 완전히 제외하고 생존 기업끼리만 매출비례로 재분배. 실측: 필러그룹(HS '300190') 6개사 중 휴젤(corr 0.47)·바이오플러스(0.17)·069620(0.07, 그룹에 새로 포착된 대형사)만 생존, 메디톡스(-0.08)·휴메딕스(-0.01)·파마리서치(-0.07)는 이 그룹에서 제외(단, 파마리서치는 다른 매핑 HS코드에서는 여전히 신호 보유). 전체 효과: 398개 매핑기업 중 신호를 받는 기업이 193개로 감소(상관계수 낮은 기업은 의도적으로 신호 없음 — 버그 아니라 설계). v8 두 구간(24.06_25.05, 22.11_23.10) 재실행 크래시 없음 확인, 콤보 재검증은 미완료.
  ⚠️ **sector_focus 추가 버그(2026-09-07 수정)**: 위와 별개로 `backtest_strategies/sector.py`의 영업이익 YoY 스코어 컴포넌트(최대 25점)가 `cur_yr`을 거래일의 달력연도로 고정해서, 그 해 사업보고서가 아직 공시 전인 연중 대부분(1~11월) 항상 0점 처리되고 있었음 — 실제 공시된 최신 연도를 찾도록 수정, 6기간 재검증 avg6 23.80%→29.53%(5/6기간 양수)로 개선 확인(sector_focus+v2 콤보 성과도 같이 개선될 전망, 콤보 재검증은 미완료).
  ⚠️ **4차 수정(2026-09-08, 사용자 지적 — "DDR메모리는 삼성전자·하이닉스 2개사가 다인데 어떻게 처리했어?" + "매핑 비율이 너무 낮은데 개선 방법 없나")**: 두 축으로 작업.
  (a) **상관계수 필터의 실제 결함 발견·수정**: DDR메모리(HS '854232')를 실측한 결과, SK하이닉스(corr=0.11)가 상관계수 필터에 걸려 제외되고 군소 팹리스 제주반도체(corr=0.321, 매출 3천억대)가 살아남는 역설을 확인 — 대기업은 전사매출에 다른 사업(낸드/파운드리 등)이 섞여 좁은 HS카테고리 하나와의 상관계수가 오히려 희석되는 구조적 약점이었음. `hs_company_market_share`(애널리스트 실측 점유율 12건, 삼성/하이닉스 등)를 `hs_code_company_map.market_share_pct`에 반영(누락된 쌍 1건은 신규 INSERT)하고, 가중치 로직을 **"그룹 전원이 점유율값을 가져야 적용"(all-or-nothing, 사실상 사문화돼 있었음) → "점유율값이 있는 회사부터 개별 우선 적용, 나머지는 잔여비중(1-알려진점유율합)만 상관계수필터+매출비례로 배분"**으로 재설계 — 검증 결과 삼성(58%)+하이닉스(42%)=100%로 이미 꽉 차 나머지 3개사(제주반도체/해성디에스/한미반도체[장비사])는 이 그룹에서 자동으로 0이 되어 실제 시장구조와 일치하게 됨.
  (b) **커버리지 확장(398→404개사, 7.3%→7.4%)**: `hs_company_market_share`(12건 백필), `regional_company_mapping_evidence`(evidence_score≥0.71 또는 [≥0.65 AND post_count≥2] 기준으로 84쌍 승격 — 명백히 깨진 지역데이터[예: "경기도 기장군"(기장군은 부산 소재), "경상남도 성북구"(성북구는 서울 소재)]는 자동으로 임계값 미달 처리돼 배제됨, 단 이 소스는 기존에 이미 알려진 398개사의 HS코드 커버리지만 넓혔고 신규 기업은 0개), `segment_revenue`(DART 사업부문별 매출, 2,561개사 — 커버리지 확장의 진짜 지렛대가 될 것으로 기대) 세 소스를 검토. segment_revenue는 세그먼트명↔HS설명 키워드 자동매칭을 시도했으나 **한국어 사업분야 용어의 동음이의/과잉일반화로 실측 오탐률 약 50%** 확인(예: KCC의 "실리콘" 사업부문이 반도체용 실리콘 웨이퍼로 오매칭됐지만 KCC는 실리콘[규소]이 아닌 실리콘[폴리머 밀폐제] 화학회사; 파크시스템스의 "산업용 자동화 원자현미경" HS설명에서 "자동화"만 추출돼 POSCO홀딩스/현대엘리베이터/뉴로메카 등 무관 8개사에 오매칭; 한화의 "가성소다(USD/톤)" 세그먼트는 매출이 아니라 톤당 가격 참조행[revenue=4.15]이었음) — 자동화 매칭은 폐기하고 33건 후보를 전부 수동 검토해 11쌍(6개 신규기업: 종근당홀딩스/코아스템켐온/한미사이언스/한미약품/대한유화/이수화학, +송원산업/SNT에너지/DN오토모티브/세방전지/LS의 신규 HS링크)만 provisional로 커밋. 결론: 세그먼트명 기반 완전자동매칭은 정밀도가 낮아 대량 확장에는 부적합 — 유의미한 커버리지 확대는 (i) 수동/반자동 검토를 곁들인 점진적 확장이거나 (ii) 향후 LLM 기반 의미매칭(단순 부분문자열이 아닌 문맥 판단)이 필요, 미해결 과제로 남김. v8 재검증 크래시 없음 확인.
  ⚠️ **5차 수정(2026-09-08, 사용자 지시 — "dart는 후행, hs code는 선행/실시간", "상관계수를 전수조사해", "그 회사의 매출규모가 반드시 고려돼야", "hs 월별실적과 dart 분기실적이 고려된 정보여야")**: 상관계수 필터 자체의 구조적 결함을 전수조사로 확인·재설계.
  (a) **월별-분기 주기 불일치 수정**: 기존 상관계수 계산이 DART 분기매출YoY(3개월 누적)를, HS 수출데이터는 분기말 딱 한 달(3/6/9/12월)의 월별YoY만 뽑아 비교하고 있었음(주기 불일치로 왜곡). `_quarterly_export_yoy_series()` 신설 — HS 월별 수출액을 분기(1~3월=Q1 등, 3개월 전부 있는 완전한 분기만) 합산 후 YoY 계산해 DART와 동일 주기로 정렬. 단 실제 v8 매매신호(`_get_export_yoy`)는 손대지 않음 — 그건 월별 그대로 유지해야 "선행지표"로서의 존재 이유(HS가 DART보다 빠르다는 점)가 유지됨.
  (b) **전수조사로 상관계수 필터의 구조적 편향 발견**: 2개사 이상 매핑된 모든 HS그룹×기업 쌍(722건) 상관계수를 계산한 결과 — corr 분포가 mean=0.107/median=0.085로 애초에 약함에도, 기존 "corr>0.2 이상만 포함"(대칭적 진입장벽) 기준을 쓰면 **80곳 이상의 그룹에서 그 그룹의 명백한 실제 1위 사업자(삼성전자/현대차/LG화학/POSCO홀딩스/삼성바이오로직스/SK이노베이션/현대제철/대한항공/LG전자/롯데칠성 등)가 부당하게 제외되고, 대신 매출 규모가 수백억원대에 불과한 군소기업이 짧은 표본(28~40분기)의 통계적 잡음만으로 살아남는 역전 현상**이 광범위하게 확인됨 — 대기업일수록 전사매출에 여러 사업이 섞여 좁은 HS카테고리 하나와의 상관계수가 구조적으로 희석되는 게 원인. DDR메모리 사례는 이 문제의 극히 일부였을 뿐, 전사적으로 퍼져있던 구조적 결함이었음.
  (c) **재설계**: 상관계수를 "진입하려면 넘어야 하는 대칭적 문턱"에서 **"이미 매핑된 근거를 뒤집을 만큼 뚜렷한 반증(corr<-0.3)이 있을 때만 배제하는 비대칭 필터"**로 전환 — 기본은 포함(매출비례 가중치가 규모를 자연스럽게 반영하도록 맡김). 재검증 결과 대기업 오제외 사례가 80여건→5건(대상홀딩스/서흥/삼성SDI/현대제철/LS ELECTRIC — corr -0.33~-0.48의 뚜렷한 역상관, 정당한 제외로 판단)으로 감소. DDR 검증: 제주반도체/해성디에스/한미반도체가 '854232' 그룹에서는 market_share_pct 우선 로직으로 여전히 0을 받고, 이들이 매핑된 *다른* HS그룹에서 받는 신호를 전부 합산해도 삼성전자 대비 0.01~0.16% 수준(1% 미만) — 사용자 지적(점 6) 그대로 정량 확인됨.
  ⚠️ **점 5(지역정보 교집합) 조사 결과 — 인프라 부재로 보류**: `customs_monthly_record`에 시도(광역단체)별 수출데이터가 있는지 확인한 결과, 실제 시도×품목 교차 데이터를 담은 `sidoitemtrade` 엔드포인트(18,048건)가 존재하긴 하나 hs_code 필드값이 `'0000000001'`류의 알 수 없는 플레이스홀더로 깨져 있고 period_ym도 `'2016'`/`'총계'` 등 비정상값이 섞여 있어 **우리가 추적 중인 313개 HS코드와 교집합이 0건** — 사실상 미가공/방치 상태 데이터로 확인됨. `regional_company_mapping_evidence`(텔레그램 근거 기반 스냅샷, 499건)는 이미 활용 중이나 이건 시계열이 아니라 단발 증거이며, 시군구 단위의 체계적 월별 품목별 무역통계는 관세청 공개데이터 자체에 원래 없음(시도 단위까지만 존재). 결론: 이 아이디어를 제대로 구현하려면 `sidoitemtrade` 원본을 관세청에서 올바른 HS코드 라벨로 재수집하는 별도 작업이 선행돼야 함 — 미해결 과제로 남김.
  ⚠️ **알파벳 섞인 종목코드 시세공백(2026-09-07 재조사·수정)**: 앞선 조사에서 "923개, 어느 수집기도 담당 안 함"으로 잘못 결론지었던 것을 사용자가 정정("최근 상장 종목이다, 무시하면 안 됨"). 재확인 결과 실제 stock_universe 내 알파벳 포함 코드는 81개(예: `0007J0`=인벤테라, `0011A0`=액스비스, `0126Z0`=삼성에피스홀딩스[시총 8.9조] — 신규상장 스팩·일반주 및 우선주), KIS API로 직접 조회 시 오늘(2026-09-07)까지 정상 시세 확인 — 실존하는 활성 상장종목이 맞았음. 원인은 `collect_kis_ohlcv.py`의 전종목 대상 쿼리가 원래 `GLOB '[0-9]*'`(숫자로 시작하는 6자리 — 알파벳코드 포함)였는데, 2026-09-07 진행 중이던 Postgres 마이그레이션 작업 중 `GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'`(순수 숫자 6자리만)로 조용히 강화되어 있었음(미커밋 상태, 그날 밤 정기수집부터 반영될 뻔함) — 원래 필터로 되돌림(3곳: 종목선정 쿼리 + 검증로그 쿼리 2곳). 2026-06-05~09-07 구간 81개 종목 전체 즉시 백필 완료(5,192건, 에러 0). 다만 이 되돌린 필터가 적용되기 전, 즉 6월 5일부터 이 되돌리기 전까지 왜 정지돼 있었는지(그 시점엔 아직 이 강화된 필터가 존재하지 않았음)는 로그 부족으로 완전히 특정 못함 — 2026-09-08 이후 정기수집(18:35)이 계속 정상 작동하는지 확인 필요.
  ⚠️ **옛 병합조합 4계좌(combo_605/539/510/474) — 사용자 결정: 동결보전**(2026-09-07). 청산하지 않고 현재 포지션 그대로 유지, 추가 리밸런싱·정리 작업 불필요.
  ⚠️ **6차: LLM 기반 HS매핑 커버리지 확장 파이프라인(2026-09-08, 사용자 지시 — "애널리스트 리포트나 DART 원문 비교 등 다양한 방법으로, OpenAI 대신 DeepSeek으로")**: 커버리지 398→404(세그먼트명 수동매칭)에서 **478개사(전체 5,459개사의 8.8%)**로 추가 확장.
  - **신규 인프라**(`hs_trade_lab/scripts/`): `llm_hs_matcher.py`(공용 매칭 코어 — `config.get_ai_client()`로 DeepSeek 사용, hs_codes 324개 카테고리 전체를 컨텍스트로 제공해 목록 밖 카테고리 환각 방지), `build_hs_candidates_from_analyst_pdfs.py`(애널리스트 PDF `report_files` 23,727건/1,456개사에서 사업설명 추출), `build_hs_candidates_from_dart_filings.py`(DART Open API document.xml로 사업보고서 "사업의 내용" 원문 추출, segment_revenue 2,561개사 중 미매핑 기업 대상), `promote_llm_hs_candidates.py`(안전기준 통과분만 승격).
  - **파일럿에서 발견·수정한 버그들**(전부 실측 검증): (1) `report_files.stock_name`이 파일명 파싱 잔여물로 깨져있어(예: "오킨스전자［］ Hyundai+Moto") LLM에 그대로 넘기면 자기검열로 매칭이 0건까지 떨어짐 — `stock_universe` 정식 종목명으로 교체. (2) 증권사 발간 리포트의 stock_code 라벨이 실제 분석대상이 아닌 발행 증권사로 잘못 붙는 사례(상상인증권 파일이 실제로는 삼성SDI 분석) 확인. (3) **지주/그룹 대표종목의 자회사 실적 자기귀속** 대량 확인 — 롯데지주/SK스퀘어/한화/SK/GS/효성/에코프로/CJ/대웅 등이 종목명에 "지주"가 없어도(SK스퀘어, 한화, GS, SK, 효성처럼) 사업보고서/리포트 원문이 그룹 전체를 서술하다 보니 자회사(SK하이닉스, 한화에어로스페이스, GS칼텍스, 효성티앤씨 등) 실적이 모회사 종목코드로 매칭됨 — `mentions_subsidiary()`(회사명+추가글자 패턴, "자회사"/"계열사" 키워드) 신설로 검출. (4) LLM이 reason에 "정확히 일치하는 코드가 없어 억지로 끼워맞춤"을 스스로 인정하고도 confidence 0.8~0.9를 부여한 사례(씨엠티엑스: 실리콘 부품을 "실리콘카바이드"로 근사매칭) 및 reason에 "매칭하지 않음"이라고 명시하고도 matches 배열에 포함시킨 자기모순 사례(서울반도체 LED, 오리온 라면류)까지 확인 — `has_hedge_language()` 회피표현 필터 신설.
  - **최종 안전장치 3중**: `is_sector_plausible`(증권/은행/보험/지주/홀딩스 종목명·섹터 배제) + `mentions_subsidiary`(자회사 귀속 의심 배제) + `has_hedge_language`(강제매칭/자기모순 배제) + confidence≥0.8. 800개사 파일럿(analyst_pdf 400 + dart_filing 200 + 초기 테스트 200)에서 최종 127건(74개사)만 승격 통과 — 무작위 15건 재검토 결과 전부 타당, provisional 상태로 반영.
  - **미해결**: 코드베이스에 "지주회사 여부" 플래그가 없어 `mentions_subsidiary`의 이름패턴 방식은 완전 일반화가 안 됨(예: KG케미칼↔KG스틸처럼 형제회사 간 이름이 겹치지 않는 경우는 못 잡음) — 이런 잔여 사례는 provisional 상태이므로 후속 리뷰에서 걸러짐. `analyst_pdf_extracts`(기존 OpenAI/Gemini 기반, 목표주가 추출용, 비용 문제로 비활성)와는 별개 파이프라인.
  ⚠️ **CFS/OFS 미탈리브레이크 전수감사 및 수정 완료(2026-09-08)**: HS매핑 버그 조사 중 발견한 "financial_data에 report_type 타이브레이크 없이 SUM/AVG/positional-index하면 CFS+OFS 이중계상·비결정성" 부류 버그를 서브에이전트로 전수감사, se_momentum/megatrend/peak_easy/`_load_company_revenue_map` 외에 8곳 더 확인 — 사용자 지시("남기지말고 모두 고쳐")로 전부 기존 확립 패턴(`ROW_NUMBER() OVER(...ORDER BY CASE report_type WHEN 'CFS' THEN 0 ELSE 1 END)` 서브쿼리로 종목·연도·분기당 1행만 남긴 뒤 집계)으로 수정 완료, 전 파일 컴파일+실제 쿼리/백테스트 재실행 검증 완료:
  1. `routes/market_radar.py`(3곳: 반도체 밸류스트림 TTM매출 2곳 + financial-detail TTM 1곳) — 종목 000020 TTM매출 445.3B(버그) → 513.2B(수정 후, 검증됨)로 13% 상향.
  2. `routes/tenbagger.py`(`/custom-filter` 재무 JOIN 2곳 + `_quarterly_rows`) — 종목 226340 2026Q1 영업이익이 CFS -2.09B/OFS +2.33B 사이에서 비결정적으로 뒤집히던 것을 CFS -2.09B(적자)로 고정 확인.
  3. `routes/sector_rotation.py`(`_get_sector_earnings_yoy` AVG + top-picks QoQ `q_rows`) — 전력기기 섹터 실측 재확인(earnings_yoy=73.25%, 정상 동작).
  4. `routes/trend.py`(`_rec_is_turnaround`, V-RECOVERY 라이브 가상매매) — 기존엔 OFS+4분기+dart_ofs_backfill 특수케이스만 걸렀는데 일반적인 CFS/OFS 중복도 걸러지도록 일반화(감사에서 "잔여 리스크"로 플래그됐던 부분).
  5. `backtest_strategies/golden_cross.py`(섹터 영업이익YoY SUM), `backtest_strategies/sector.py`(2곳: op_rows_s/op_prev_s 딕셔너리 비결정성, turnaround ni_rows 흑자전환 판정) — 백테스트 전용, golden_cross/sector 재검증 실행(pick_ta_bonus 경로 포함) 크래시 없음 확인.
  전체 콤보/6기간 재검증은 미완료 — 위 수정들이 실전략 성과에 미치는 영향은 다음 정기 재검증 때 반영.
- 메타시뮬레이터 기반: backtest_runs DB의 AUTO 런 목록 블렌딩 → 상위 종목 추출

### routes/portfolio.py → /api/portfolio
```
GET    /                 # 포트폴리오 + 현재가 + 수익
POST   /sync-kis         # KIS 체결 동기화
PATCH  /{code}/bought-at # 매수일 수정
GET    /transactions     # 거래내역
POST   /transaction      # 거래 추가
POST   /kakao-parse      # 카카오뱅크 문자 파싱
PUT    /{code}           # 종목 수정
DELETE /{code}           # 종목 삭제
GET    /export/excel     # 엑셀 내보내기
POST   /import/excel     # 엑셀 가져오기
```

### routes/buy_candidates.py → /api/buy-candidates
```
GET    /                 # 매수 후보 + 현재가
POST   /                 # 추가
PATCH  /{code}           # 메모/목표가 수정
DELETE /{code}           # 삭제
GET    /short-sell/{code}# 대차잔고 조회
```

### routes/market_indicators.py → /api/market-indicators ★신규(2026-04)
```
GET  /investor-top       # 투자자별 순매수 상위 (params: date, limit=20)
GET  /turnover-top       # 회전율 상위 (params: date, market=ALL, limit=20)
GET  /investor-trend     # 수급 추이 차트 (params: market=kospi, days=60)
GET  /market-summary     # KOSPI/KOSDAQ 요약 + 오늘 수급
GET  /index-investor     # 지수 투자자 일별 (params: days=20)
GET  /available-dates    # 수급 데이터 있는 영업일 목록
```

### routes/reports.py → /api/reports
```
GET  /stock/{code}       # 종목 리포트 목록
GET  /download/{id}      # 파일 다운로드
GET  /sectors            # 섹터 목록
GET  /sector/{sector}    # 섹터별 리포트
POST /extract/{id}       # PDF → gpt-4o-mini 컨센서스 추출 (캐싱) ★신규(2026-07-05)
GET  /extracts/{code}    # 종목별 추출 결과 목록 ★신규(2026-07-05)
```

### routes/telegram.py → /api/telegram
```
GET    /channels         # 채널 목록
POST   /channels         # 채널 추가
DELETE /channels/{id}    # 채널 삭제
POST   /collect          # 즉시 수집
GET    /mentions/daily   # 일별 언급
GET    /mentions/weekly  # 주별 언급
GET    /mentions/monthly # 월별 언급
```

### routes/backtest.py → /api/backtest
```
POST   /run              # 백테스트 실행
GET    /list             # 결과 목록
GET    /{run_id}         # 결과 상세
DELETE /{run_id}         # 삭제
GET    /monthly-picks    # 월별 추천 종목 백테스트 리포트
GET    /strategies       # 전략 카탈로그
GET    /strategy-research/summary  # 전략 연구 요약 + 현재 장세 기준 전략 우선순위 ★신규(2026-07-05)
POST   /strategy-research/rebuild  # 전략 연구 데이터셋/요약 JSON 재생성 ★신규(2026-07-05)
```

### routes/strategy_data_lab.py → /api/strategy-data-lab ★신규(2026-08-29)
```
GET    /overview  # 전략센터 데이터 역할(진입/확인/촉매/위험제거)·신선도·다중확인 연구후보
```
- `V-CATALYST`/`V-REVISION`/`V-QUALITY-ROUTE`는 검증 전 연구 후보로만 반환한다. 기존 실행 검증에서 품질·수주 지표를 매수랭킹에 가산하면 악화됐으므로, 성과 매트릭스·자동매매에는 포함하지 않는다.

### routes/us_13f.py → /api/us-13f ★신규(2026-08-29)
```
GET    /summary          # SEC 13F-HR 최신/직전 보고서 비교 (force=true: 12시간 캐시 무시)
GET    /buffett-cash     # Berkshire 10-Q/10-K 현금·단기투자자산 시계열
```
- 20개 13F 운용사와 Nancy Pelosi의 House PTR을 출처·기준일을 분리해 표시한다. PTR은 보유포트폴리오가 아닌 거래 신고이며, 13F는 분기 지연 롱 포지션 공시로 옵션·공매도·공시 후 거래를 포함하지 않는다. 자동주문 또는 단독 매수/매도 근거로 사용하지 않는다.

### routes/kis_trading.py → /api/kis-trading (2026-07-23 최초 문서화 — main.py에는 등록돼 있었으나 CLAUDE.md 누락)
```
GET  /status                    # 거래모드(PAPER/LIVE)·리스크한도 조회
GET  /account/summary           # KIS 실계좌 스냅샷(보유/잔고/당일체결) — LIVE 조회 전용, 매매 아님
POST /paper/order                # 페이퍼 주문 실행 (A2 리스크게이트 통과 필요)
GET  /paper/orders               # 페이퍼 주문 이력(구 스키마, 하위호환 유지)
GET  /paper/positions            # 페이퍼 보유 포지션 + 평가손익
GET  /paper/pnl                  # 페이퍼 당일/누적 실현손익
POST /live/order                 # 항상 403 차단(실전주문 미승인 상태, 명시적 승인 절차 전까지 유지)
GET  /risk-gates/check           # ★신규(2026-07-23) 주문 없이 리스크게이트만 사전점검
GET  /risk-gates/recent          # ★신규(2026-07-23) 최근 게이트 판정 이력(BUY_ALLOWED/BLOCKED_* 등)
GET  /orders/lifecycle           # ★신규(2026-07-23) live_orders 기반 주문 목록(신규 스키마)
GET  /orders/{order_id}          # ★신규(2026-07-23) 주문+이벤트+체결 상세
GET  /cash-ledger                # ★신규(2026-07-23) 페이퍼 현금원장(잔고 이력)
```
- **PAPER 주문 흐름(2026-07-23 이후, 2026-07-23(2차) 3개 게이트 추가)**: `place_paper_order()`가 먼저 `evaluate_risk_gates()`로 **9개 게이트**(데이터신선도/갭리스크/유동성/희석위험/수급역풍/장세위험/**신용잔고급증/변동성기반사이징/섹터집중한도**)를 평가 → `BLOCKED_STALE_DATA`/`BLOCKED_RISK`(희석·수급역풍·장세위험·신용급증·섹터집중 위반)는 400 거부, `WAIT_CONFIRM`(갭+7%↑)은 `override_wait_confirm=true` 재요청 전까지 409 거부, `SIZE_REDUCED`(유동성 3%↑ 또는 종목당 리스크한도 초과)는 두 한도 중 더 보수적인 쪽까지 수량 자동 축소 후 진행. 통과 시 **기존 `kis_paper_orders/positions/realized`(하위호환) + 신규 `live_orders/live_order_events/live_fills/live_cash_ledger`(생애주기 상세) 양쪽에 병행 기록**.
- 매도(side=sell)는 데이터신선도만 확인하고 나머지 게이트는 통과시킴 — 리스크 축소 행위인 매도를 막으면 오히려 위험하다는 원칙.
- **변동성기반 사이징 가정**: 종목당 손실한도=계좌자본×1.2%, 가정손절폭=-20%(이 세션에서 가장 흔히 쓰인 기본값) — 전략별 실제 손절폭(-8%~-35%)과 다를 수 있어 "최소한 이 이상은 넘지 말자"는 보수적 하한으로만 기능. 섹터집중한도(35%)는 `kis_paper_positions`+`stock_universe.sector_large` 기준 계산, 한도 초과 시 부분축소가 아니라 전체 차단(단순화, 정직하게 명시).
- **신용잔고급증**: `kiwoom_credit_balance.credit_ratio` 기준 8%↑ & 20일전 대비 50%↑ 급등 시에만 경고(V-SMARTFLOW의 "신용잔고<3%가 좋은 신호" 임계와는 별개 — 여기서는 "급격한 증가" 자체를 위험신호로 봄). 데이터 45일↑ 오래되면 판단보류.

전체 검증 세부는 섹션 11 변경이력 참조.

### routes/ingest.py → /api/ingest
```
POST /fundamentals       # 재무 데이터 저장
POST /market-price       # 시장가 저장 (장중만)
POST /sectors            # 섹터 저장
POST /investor-trends    # 투자자 동향 저장
```

### routes/market_radar.py → /api/market-radar ★등록(2026-05)
```
GET  /all                         # 전체 섹터 RS 데이터
GET  /sector/{sector}/detail      # 섹터 상세 (섹터 지표 페이지)
POST /init-semiconductor          # 반도체 초기화
POST /refresh-cache               # 캐시 강제 갱신
GET  /export-csv                  # CSV 내보내기
POST /import-csv                  # CSV 가져오기
GET  /semiconductor/valuestream   # 반도체 밸류체인 (SemiconductorView)
POST /semiconductor/valuestream/refresh
GET  /semiconductor/megatrend     # 메가트렌드 탐지 스크리너 ★신규(2026-07-20)
```

### routes/sector_rotation.py → /api/sector-rotation ★신규(2026-06-27), 문서 소급기재(2026-09-08)
```
GET  /scores                      # 섹터별 활성도 스코어(0~100, 수급+수출+실적+거래량+RS)
GET  /leadership                  # 주도섹터·주도주·진입단계(ENTRY_NOW/EARLY_WATCH/HOLD_LEADER/WAIT/AVOID) 통합
GET  /history/{sector_key}        # 섹터별 월별 RS 히스토리
GET  /rotation-map                # 4주/12주 RS 4분면 맵
GET  /top-picks/{sector_key}      # 섹터 내 급등 후보 종목
GET  /dashboard-summary           # ★신규(2026-09-08) 메인페이지(macro 탭) 카드용 — 전략1(추세추종 집중/탈출)+전략2(낙폭과대 반등) 통합
POST /refresh-cache               # 캐시 수동 재계산
GET  /flow-signal-validation      # ka10051 업종별투자자순매수 신호 검증 현황
```
- `SECTOR_GROUPS`(10개 커스텀 섹터: 전력기기/원자력/화장품뷰티/의료기기미용/반도체/기판패키지/2차전지/방산/조선/바이오)는 파일 상단에 하드코딩. 캐시는 `sector_rotation_cache` 테이블(장중 1시간/장마감 기준 자동 갱신).
- `/dashboard-summary`: 전략1은 기존 `_entry_stage` ENTRY_NOW 중 최고점 1개를 `primary_focus`(집중)로 스포트라이트, 나머지는 `secondary`. 12주RS 양호했다가 4주RS가 막 꺾인 섹터는 `exit_alerts`(추세이탈 경보)로 별도 표시. 전략2(`_score_bottom_reversal`)는 낙폭과대(30)+반전조짐(30, 단기RS가 장기RS보다 개선됐는지)+수급전환(25, 최근30일 대 이전60일)+밸류트로프(15)로 점수화, `BOTTOM_ENTRY`는 반전조짐 컴포넌트가 실제로 점수를 받았을 때만 승격(낙폭+수급만으로는 승격 안 함 — 전략1의 "가격 확인 없이는 BUY 승격 금지" 원칙과 동일 취지). 전략1 ENTRY_NOW인 섹터는 전략2 목록에서 중복 제외.

### routes/global_foreign_flow.py → /api/global-foreign-flow ★신규(2026-09-08), TIC 대미 흐름 확장(2026-09-08 2차)
```
GET  /summary                     # ①국가별 자국시장 외국인 순매수(한국/대만/일본/중국/인도) + ②TIC 국가별 대미 주식 순매수(16개국) + observations(상관관계 관찰) + DXY/VIX/UST10Y
GET  /history?days=180            # ① 국가별 자국시장 시계열(라인차트용)
GET  /us-inbound-history?months=24 # ② TIC 전세계/아시아/유럽/국가별 월별 시계열(라인차트용)
```
- 데이터는 `routes/global_macro.py`의 `global_macro_data`(범용 시계열 저장소, indicator_code+date)를 그대로 재사용 — 신규 테이블 없음.
- **① 자국시장 외국인 순매수** 신뢰도(2026-09-09 기준): **한국**(`KR_FOREIGN_FLOW_USD`, price_history 집계) HIGH / **인도**(`IN_FPI_FLOW_USD`, NSDL `fpi.nsdl.co.in/Reports/Latest.aspx` HTML 파싱) HIGH / **일본**(`JP_FOREIGN_FLOW_USD`, MOF `week.csv` 직접 CSV) HIGH — e-Stat 대신 재무성 공개 CSV로 전환(섹션 9 참조) / **대만**(`TW_FOREIGN_FLOW_USD`) **BLOCKED**(TWSE WAF가 `/rwd/`·`/exchangeReport/` 경로를 307로 차단, Playwright도 동일) / **중국 북향자금**(`CN_NORTHBOUND_FLOW_USD`) PENDING(HKEX net-flow 엔드포인트 미확정).
- **② TIC(미 재무부) 국가별 대미 주식 양자간 흐름** ★신규(2026-09-08 2차, `collectors/tic_bilateral_flow_collector.py`) — 사용자가 "유럽/미주 등 시총 상위 국가 전체로 확장"을 요청해, ①과는 반대 방향("그 나라 투자자가 미국 주식을 얼마나 순매수했는가")을 측정하는 FRED 미러링 TIC 데이터(`FORLTEQTYNET*` 시리즈)를 추가 — 20개 국가/지역 시리즈(전세계·아시아합계·유럽합계·유로존 + 중국/일본/홍콩/대만/한국/인도/싱가포르(아시아), 영국/독일/프랑스/이탈리아/스위스(유럽), 캐나다/호주/사우디/브라질) 전부 실측 확인됨(월별, USD 백만, 공식발표상 약 2~3개월 지연). 기존 KRX 스타일 개별 거래소 스크래핑과 달리 **차단 리스크 없이 한 번의 API로 시총 상위 대부분 국가를 커버** — TWSE/HKEX처럼 개별 국가 거래소를 일일이 뚫는 것보다 훨씬 안정적임을 확인, 향후 유사 요청은 이 방식을 우선 검토할 것.
- `observations` 필드: ①이 마이너스(자국 이탈)이면서 ②아시아합계가 플러스(미국 유입)일 때 "방향 일치" 관찰 문구를 자동 생성 — 인과관계 단정은 하지 않고 상관관계 관찰로만 표현(문구에 명시).
- 프론트: `frontend/src/views/GlobalForeignFlowView.jsx`(`global_foreign_flow` 탭) — 섹션①(자국시장)+섹션②(대미 TIC, 국가별 랭킹바+지역합계+월별추이) 2단 구성. 데이터 없는 나라는 "0"이 아니라 "데이터 없음"으로 표시.
- 수집: 별도 스케줄러 잡 없음 — 기존 `_loop_global_macro_daily`(매일 06:45)가 실행하는 `scripts/ops/collect_global_macro_daily.py`에 `asia_foreign_flow`/`india_fpi_flow`/`jp_foreign_flow`/`tic_bilateral_flow` 4단계 추가(TIC는 FRED와 같은 API키 재사용, `fred` 스텝 바로 다음에 실행).

### routes/sector_define.py → /api/sector-define ★등록(2026-05)
```
GET  /posts                       # Hot 섹터 포스트 목록
GET  /post/{id}                   # 포스트 상세
POST /parse                       # 포스트 파싱
```

### routes/extra_signals.py → /api/extra-signals ★신규(2026-05)
```
GET  /extra-signals/{code}  # 추가 시그널 (고용/수출/섹터트렌드/수급/ETF편입/ETF비중)
```
응답 구조: `{employment, exports, sector_trend, supply, etf_ratio, etf_inclusion}`
- sector_trend: sector_large 기준 분류 (sector_mid 아님)
- etf_inclusion/etf_ratio: etf_count=0 & etf_amount=0 인 날(수집실패)은 건너뜀

### ETF_check/routes_etf.py → /api/etf-check ★신규(2026-05)
```
GET  /tab1              # ETF 편입액 기준 (KOSPI/KOSDAQ 상위)
GET  /tab2              # ETF 편입액 증감 (1일/5일 전 대비)
GET  /tab3              # 시총대비 증감%
GET  /tab4              # 시총대비 비중%
GET  /search            # 종목명/코드 검색 (유효 날짜만 사용)
GET  /etf-list/{code}   # 종목 편입 ETF 목록 (KRX PDF + KIS CU 직접 수집)
```
- etf_amount=0인 날(수집실패)은 get_available_dates에서 자동 제외
- 2026-09-19부터 ETF Check 외부 사이트는 운영 수집/조회 경로에서 사용하지 않는다. `ENABLE_ETFCHECK_VALIDATION=1`은 수동 회귀 비교가 꼭 필요할 때만 명시적으로 사용한다.

### routes/stock_analysis_rs.py → /api/stock-analysis-rs ★성능개선(2026-05)
```
GET  /dashboard-data    # 요약만 반환: benchmarks, sector_rs, metadata (rs_list 없음)
GET  /dashboard-rows    # RS 행 서버 페이지네이션: ?page=&page_size=&sort=&q=&sector=&cap_min=&market=&sector_mode=
GET  /high52-data       # 52주 메타데이터만 반환
GET  /high52-rows       # 52주 행 서버 페이지네이션: ?page=&page_size=&sort=&q=&sector=&high_filter=
POST /precompute        # 캐시 강제 재계산 (스케줄러 18:30 호출)
```
- 초기 전송량: 2.3MB → 수십KB (rs_list/high52_list 제거)
- 캐시: scratch/stock_analysis_rs_cache.json (장중 10분, 장외 24시간 TTL)
- 동시 요청 시 double-check locking (compute는 락 밖에서 수행)

---

