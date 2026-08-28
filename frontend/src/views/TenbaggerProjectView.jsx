/**
 * TenbaggerProjectView.jsx
 * 텐버거 발굴 프로젝트 — 구현 계획 + 데이터 현황 + 패턴 분석
 */
import React, { useState, useEffect } from 'react';
import SignalImpactView from './SignalImpactView';

const API = (p) => p;

// ── 구현 계획 데이터 ─────────────────────────────────────────────────
const IMPL_PLAN = [
  {
    week: 1,
    period: '6/1 – 6/7',
    title: '데이터 기반 구축 & 패턴 발굴',
    days: [
      {
        date: '6/1 (일)',
        status: 'done',
        items: [
          'BigQuery 3x+ 1,324종목 전수 분석 완료',
          '서지 직전 재무/수급/차트/공시 패턴 추출',
          '핵심 발견: 94.5% 52주 저점 근처 출발, 기관 매수 선행 아님',
          '데이터 갭 분석 — 수주잔고/매입재료비/CB이력 미수집 확인',
          '프론트엔드 TenbaggerProjectView 페이지 신설',
          'BigQuery 안정 테이블 23개 추가 업로드 (47→70개)',
          '디스크 2.76G 정리, WAL 일별 체크포인트 자동화',
        ],
      },
      {
        date: '6/2 (월)',
        status: 'done',
        items: [
          'order_backlog 테이블 신설 — DART 사업보고서 수주잔고 350건 수집',
          'tenbagger_engine 6축 스코어링 시스템 완성 (임계값 55점)',
          'cost_structure 테이블 신설 — 매입재료비 비율 17건 수집',
        ],
      },
      {
        date: '6/3 (화)',
        status: 'done',
        items: [
          'dilution_events 테이블 신설 — CB/BW/유상증자 이력 316건 수집, 스코어링에 악재 패널티 연동',
          '공매도잔고 급감 (숏커버) 신호 tenbagger_engine 스코어링 연동 (short_sell_daily 302만행)',
          'margin_balance_daily 테이블 신설 — 신용잔고 221건 수집',
        ],
      },
      {
        date: '6/4 (수)',
        status: 'done',
        items: [
          '실시간 신호 탭 — TTM 흑자전환/고성장/이익가속/QoQ 5종 신호 (831종목 344건)',
          'earnings_signals 스케줄러 자동화 (매일 06:00, 분기시즌 2h마다)',
          '수주잔고/CB-BW/공매도급감 tenbagger 6축 + 보너스 7축으로 확장',
        ],
      },
      {
        date: '6/5 – 6/7',
        status: 'done',
        items: [
          '텐버거 발굴 결과 20종목 (디바이스 80점, 테스 79점, 토탈소프트 78점)',
          '6축 스코어링 엔진 완성 + 스케줄러 09:00/12:00/15:00 자동 실행',
          '대시보드 "현재 후보" 탭 — /api/tenbagger/results 실제 결과 표시',
        ],
      },
    ],
  },
  {
    week: 2,
    period: '6/8 – 6/14',
    title: '데이터 완성 & 패턴 정제',
    days: [
      {
        date: '6/8 – 6/9',
        status: 'done',
        items: [
          '임원매매 수집 완료 — dart_insider_holdings 26,796행 / 1,797종목 (DART elestock API)',
          '외국인 지분율 수집 완료 — kiwoom_foreign_flow 112,134행 / 2,198종목 (Kiwoom ka10008)',
          '신용잔고 수집 완료 — kiwoom_credit_balance 219,626행 / 2,198종목 (Kiwoom ka10013)',
          'dilution_events 보강 — CB 5,341건 + 유상증자 2,594건 + BW 15건 (DART 공시 파싱)',
          '스케줄러 자동화 — 외국인지분율(매일 19:15) · 신용잔고(매일 18:45) · 임원매매(매주 일/매일 증분)',
          '원가구조(cost_structure) 연간 수집 완료 — 9,601행 / 2,175종목 (DART API)',
        ],
      },
      {
        date: '6/9 – 6/11',
        status: 'done',
        items: [
          '고정비/변동비 구조 수집 완료 — cost_breakdown 23,678행 / 2,177종목',
          '수주잔고 전종목 완료 — order_backlog 5,005행 / 544종목 (건설/방산/조선/IT)',
          '원가구조 분기+연간 완료 — cost_structure 27,220행 / 2,213종목',
          'cost_breakdown 고정비 레버리지 신호 → tenbagger_engine 스코어링 연동 완료 (+2~3점)',
        ],
      },
      {
        date: '6/11 – 6/14',
        status: 'in_progress',
        items: [
          '✅ v2 스크리너 백테스트 API 구현 — /api/tenbagger/score-performance (구간별 수익률 분석)',
          '✅ 점수 가중치 최적화 — 자사주(+2~3점)/PBR백분위(+2~3점)/고정비레버리지(+2~3점) 연동',
          '✅ FP/FN 탭 UI 개선 — 평균수익률 배너 + PENDING 카운트 추가',
          '✅ AI 비즈니스 스토리 평가 프롬프트 개발 (DeepSeek 배치 확장) — /api/tenbagger/ai-analysis-batch',
        ],
      },
    ],
  },
  {
    week: 3,
    period: '6/15 – 6/21',
    title: 'AI 분석 연동 & 자동화',
    days: [
      {
        date: '6/9 (완료)',
        status: 'done',
        items: [
          '임원 매수 / 외국인 지분율 증가 → tenbagger_engine 스코어링 연동 (+2~4점 보너스)',
          '매주 월요일 "텐버거 위클리 리포트" 자동 생성 (scheduler 07:30 + 텔레그램)',
          '실시간 트리거 알림 — 기관 N일 연속 · 임원 매수 · 신용잔고 급감 복합 신호 (18:00)',
          '포트폴리오 연동 탭 — 보유 종목 × 텐버거 점수 교차 분석 (💼 포트폴리오 추적 탭)',
          'kiwoom_credit_balance 신호: 15일 창 + fallback margin_balance_daily',
          '자사주 취득/소각 신호 → tenbagger_engine 연동 (+2~3점)',
          'PBR 역사적 백분위 신호 → tenbagger_engine 연동 (+2~3점)',
        ],
      },
      {
        date: '6/10 – 6/14',
        status: 'in_progress',
        items: [
          '✅ DeepSeek 심층 분석 — 24h 캐시, tenbagger_ai_analysis 테이블, 아침 알림 TOP3 포함',
          '✅ BigQuery 파이프라인 스케줄러 자동화 — scheduler.py 18:30 BQ3배파이프라인 잡',
          '✅ 스크리너 v2 정식 배포 완료 — /api/tenbagger/screener-v2 (필터/정렬/페이지네이션)',
          '스코어 성능 분석 탭 — /api/tenbagger/score-performance 신규, 구간별 수익률 시각화',
        ],
      },
    ],
  },
  {
    week: 4,
    period: '6/9 – 6/15',
    title: '고도화 & 실전 검증',
    days: [
      {
        date: '6/9 (완료)',
        status: 'done',
        items: [
          '스크리너 v2 정식 배포 — /api/tenbagger/screener-v2 (필터/정렬/페이지네이션 완전 지원)',
          'FP/FN 오판 역분석 API — /api/tenbagger/fp-fn-analysis (선정 후 N일 수익률 기반 분류)',
          '신규 데이터: 반도체 수출금액 — HS 8542/8486/8541 관세청 월별 (epic:3:2/3:20/3:21)',
          '포트폴리오 추적 탭 신설 (Week 3 이어서)',
        ],
      },
      {
        date: '6/10 – 6/15',
        status: 'in_progress',
        items: [
          '✅ FP/FN 분석 탭 — 오판 패턴 시각화 + 위험 요인 랭킹 + PENDING KPI 카드 완료',
          '✅ 스크리너 v3 — FP 패턴 페널티(-3점) + 업황 지표(SMP/반도체수출) 연동 완료',
          '✅ cost_breakdown 엔진 연동 — 고정비레버리지 신호(fixed_cost_ratio>45%+YoY개선 → +2~3점)',
          '✅ order_backlog 엔진 연동 — backlog/rev ≥ 1.5 → +2점 (v3 스크리너)',
          '✅ 점수 성능 분석 탭 — /api/tenbagger/score-performance 신규, 구간별 수익률 시각화',
          'KAMIS 농산물가격 수집 — TLS 이슈로 보류 중 (P3)',
        ],
      },
    ],
  },
  {
    week: 5,
    period: '6/15 – 6/21',
    title: '엔진 재설계 & 데이터 품질 완성',
    days: [
      {
        date: '6/15 (월)',
        status: 'done',
        items: [
          '특허/기술이전/R&D 공시 수집 — dart_rd_patent_signals 2,209건 (특허 1,917 / 기술이전 183 / R&D계약 69 / 라이선스 40)',
          'tenbagger_engine 특허·기술이전 스코어링 연동 — 기술이전+3점 / 특허+2점 / R&D+1점',
          'GET /api/tenbagger/rd-patent/{code} 신규',
          'dart_material_purchase 이상값 56건 삭제 (단위오류)',
        ],
      },
      {
        date: '6/16 – 6/17',
        status: 'done',
        items: [
          'dart_material_purchase_collector.py 파서 근본수정 — 섹션범위 4,000자+다음섹션 종료감지, 맥락검증 추가',
          '재고자산 연말(Q4) 파싱버그 수정 — 2,650건 비정상 소액(수백 원) NULL처리 후 재수집',
          'financial_data.revenue 음수 298건 NULL처리 (한국전력/한화오션/S-Oil 등 대형주 포함)',
          'dart_cost_quarterly Q4 급변 24건 NULL처리 (Q3 대비 10배 이상)',
          'dart_disclosures 정체(2026-05-08) → 캐치업 재가동 (2026-06-16 최신 확인)',
        ],
      },
      {
        date: '6/18 (목)',
        status: 'done',
        items: [
          '📊 텐버거 엔진 데이터 기반 전면 재설계 (BQ 실증 기반)',
          '핵심 발견: 70.5% 낙폭과대(-30~70%) 상태 출발, 40.5% 적자 상태, 기관매수 20.8%만 (기존 가정 역방향)',
          '새 스코어링: 낙폭과대(25) + 펀더멘털변화(25) + 저평가소형(20) + 촉매(15) + 수급반전(10) + 섹터(5)',
          '흑자전환 15점 신설, 기관매수 과대평가 수정, 정배열MA 로직 제거',
          'BQ 전체 동기화 완료 (21개 테이블, price_history 재생성)',
          '발굴 결과: 알테오젠 64점, 광무 78점(47%낙폭+흑자전환+PBR0.4) vs SK하이닉스 4점',
        ],
      },
    ],
  },
  {
    week: 6,
    period: '6/22 – 6/28',
    title: '백테스트 체계화 & 섹터 로테이션',
    days: [
      {
        date: '6/22 (월)',
        status: 'done',
        items: [
          'valuation_history 테이블 신규 — 63,451행 / 2,640종목 분기별 역사적 PBR/PER 이력',
          '실제 DART 공시일 기반 재무 가용일 — fin_disclosure_dates 41,894건 생성',
          'backtest.py 7개 SQL 쿼리 avail_date JOIN 적용 (look-ahead bias 제거)',
          '백테스트 V10 + 6번째 기간(25.6~26.3) 추가',
        ],
      },
      {
        date: '6/24 – 6/26',
        status: 'done',
        items: [
          '52W 범위 필터 체계화 — 982,889 샘플 실증 분석 적용 (52W고점근처 1.54x 수익)',
          'V5 복합콤보 RSI조건부거래량+국면별 Trail 개선, V6 이익폭발 52W 필터 제거',
          'V-GC 골든크로스 전략 신규 — MA20 상향돌파+거래량+RS6M, avg5=+39.1%',
          'market_cap 억원 단위 버그 2차 정정 + 72건 백테스트 전수 재검증',
          '72건 최종 순위: V11 복합스코어(+23.5%) > V1 MA추세(+21.7%) > V5 복합콤보(+17.2%)',
        ],
      },
      {
        date: '6/27 (목)',
        status: 'done',
        items: [
          '주도섹터 로테이션 조기 포착 시스템 신규 — routes/sector_rotation.py',
          '7개 커스텀 섹터(전력기기/화장품/반도체/2차전지/방산/조선/바이오) + 기판패키지 신설',
          'SectorRotationView.jsx — 스코어표/4분면 RS맵/히스토리 바차트',
          '수급 점수 스케일 개편: 외인 3만억+=40점 대규모 매수 차별화',
          'GET /api/sector-rotation/top-picks/{sector_key} — 섹터 내 수급 선도 종목 TOP5',
        ],
      },
    ],
  },
  {
    week: 7,
    period: '6/29 – 7/6',
    title: '전략 완성 & 전략 센터 고도화',
    days: [
      {
        date: '6/29 – 7/3',
        status: 'done',
        items: [
          'V-RECOVERY 낙폭과대 반등 전략 신규 — MA60 대비 -20~-65% + 거래량2.0배, avg5=+28.8% (하락장 +49.2% 최강)',
          'V13 고수익집중 전략 신규 — 임원매수+IT의료+52W범위+수주잔고, Trail35%, avg5=+17.2%',
          'V3/V4 데이터기반 매도 로직 — 진입조건 역전 확인 시 조기청산, V3 avg5=+11.8%, V4 avg5=+20.4%',
          'V9 수출선행 재설계 — 진짜 변곡점 감지(부진→개선), avg5 -2.9%→+8.1%',
          'V10 섹터대세 재설계 — early-cycle 진입 + 섹터모멘텀소멸청산, avg5 -6.5%→+1.6%',
        ],
      },
      {
        date: '7/4 – 7/5',
        status: 'done',
        items: [
          'V-TURNAROUND 최종 완성 — TTM NI 합산 양수 필터, stop=-0.13 최적화, avg5=+16.4%',
          'V-SECTOR/V-DEEP/V-LOWBASE 3전략 실제 6기간 검증 완료 (V-SECTOR avg6=+29.4%)',
          '전략 센터 고도화 — strategy_feature_snapshot 189,306행(3배 라벨+ML점수 생성)',
          'ML vs 휴리스틱 품질 분석 패널, 현재 국면 전략 우선순위 프론트 표시',
          'V-TURNAROUND 역사적 PBR 오염 버그 수정 (valuation_history 기반)',
          '애널리스트 PDF 컨센서스 AI 추출 — gpt-4o-mini, analyst_pdf_extracts 테이블',
          'V-GC 소형주 필터 버그 수정 — min_mktcap 500→2000억원, avg6 +10.3%→+39.1%',
        ],
      },
    ],
  },
];

// ── 데이터 가용성 매트릭스 (실시간 /api/tenbagger/data-status로 덮어씀) ──
const DATA_MATRIX_STATIC = [
  {
    category: '주가/차트',
    items: [
      { name: '5년 일별 OHLCV', status: 'ok', source: 'KRX/KIS', note: '516만 행' },
      { name: '52주 고/저점 위치', status: 'ok', source: 'price_history', note: '실시간 계산' },
      { name: '거래량 비율(평균대비)', status: 'ok', source: 'price_history', note: '실시간 계산' },
      { name: '기술적 지표(MA/RSI/볼린저)', status: 'partial', source: '실시간 계산', note: 'DB 저장 없음 — API 호출 시 계산' },
      { name: 'PBR/PER 히스토리', status: 'ok', source: 'valuation_history', note: '63K행 / 2,640종목 — 분기별 역사적 밸류에이션 ✅' },
      { name: '상장 이후 전체 주가 이력', status: 'partial', source: 'KRX', note: '2010년 이전 일부 누락' },
    ],
  },
  {
    category: '재무',
    items: [
      { name: '연간/분기 P&L', status: 'ok', source: 'DART', note: '90K행 canonical' },
      { name: '대차대조표', status: 'ok', source: 'DART', note: '자산/부채/자본' },
      { name: '현금흐름표(OCF/ICF/FCF)', status: 'ok', source: 'DART', note: '78K행' },
      { name: '매출 YoY/QoQ 성장률', status: 'ok', source: 'DART', note: '실시간 계산' },
      { name: 'TTM 흑자전환/고성장 신호', status: 'ok', source: 'earnings_signals', note: '344건 / 268종목' },
      { name: '수주잔고(Order Backlog)', status: 'ok', source: 'DART API', note: '5,005행 / 544종목 — 건설/방산/조선/IT 수집 완료 ✅' },
      { name: '매입재료비 비율(원가율)', status: 'ok', source: 'DART API', note: '27K행 / 2,213종목 — cost_structure 연간+분기 완료 ✅' },
      { name: '고정비/변동비 구조', status: 'partial', source: 'DART API', note: 'cost_breakdown — 재료비/노무비/감가상각 세부' },
      { name: '분기별 EPS/BPS 히스토리', status: 'ok', source: 'valuation_history', note: '63K행 / 2,640종목 — financial_data 기반 ✅' },
      { name: '세그먼트별 매출(사업부문)', status: 'partial', source: 'DART 주석', note: 'segment_revenue 수집 중 — DART fnlttSinglIndx' },
    ],
  },
  {
    category: '수급',
    items: [
      { name: '기관/외인/개인 일별 순매수', status: 'ok', source: 'Kiwoom ka10059', note: '수집 완료 / 주요종목' },
      { name: '기관 세부(투신/연기금/보험 등)', status: 'ok', source: 'Kiwoom ka10059', note: '10개 세부 분류' },
      { name: '대차잔고/공매도 잔고', status: 'ok', source: 'KRX', note: '302만 행 / 4,035종목' },
      { name: '신용잔고(ka10013)', status: 'ok', source: 'Kiwoom ka10013', note: '224K행 / 2,198종목 — 5년치 ✅' },
      { name: 'ETF 편입금액/종목 비중', status: 'ok', source: 'etfcheck.co.kr', note: '72K행 — ETF_check/etf_check.db' },
      { name: '외국인 지분율 추이', status: 'ok', source: 'Kiwoom ka10008', note: '2,198종목 ✅' },
      { name: '기관 누적 매수 N일 연속', status: 'partial', source: 'kiwoom_investor_daily', note: '계산 가능, 알림 미구현' },
      { name: '프로그램 매매(시장)', status: 'partial', source: 'KIS/Kiwoom/KRX', note: '실시간 집계 확인 중', statusKey: 'broker_program_market_daily' },
      { name: '프로그램 매매(종목별)', status: 'partial', source: 'Kiwoom', note: '실시간 집계 확인 중', statusKey: 'broker_program_stock_daily' },
    ],
  },
  {
    category: '공시/이벤트',
    items: [
      { name: '전체 DART 공시', status: 'ok', source: 'DART', note: '263K행 / 1,734종목' },
      { name: '수주공시(계약체결)', status: 'ok', source: 'DART', note: 'AI 분석 포함' },
      { name: 'CB/BW/유상증자 이력', status: 'ok', source: 'DART', note: '8,688건 / 655종목 — dilution_events ✅' },
      { name: '애널리스트 목표주가', status: 'ok', source: '한경 컨센서스', note: '11,534행 / 794종목' },
      { name: '최대주주/임원 지분 매매', status: 'ok', source: 'DART elestock', note: '26,956행 / 1,800종목 ✅' },
      { name: '자사주 매입/소각 이력', status: 'ok', source: 'DART dart_disclosures', note: '5,637건 / 778종목 — treasury_buyback ✅ 텐버거 엔진 연동' },
      { name: '특허/기술이전/R&D 공시', status: 'ok', source: 'DART dart_disclosures', note: '2,209건 / 특허1,917건+기술이전183건+R&D69건+라이선스40건 — dart_rd_patent_signals ✅ 텐버거 엔진 연동' },
    ],
  },
  {
    category: '업황/거시/퀀트 지표',
    items: [
      { name: '수출입 HS코드별', status: 'ok', source: '관세청', note: 'hs_trade_lab DB' },
      { name: '섹터 RS(상대강도)', status: 'ok', source: '계산됨', note: 'stock_analysis_rs' },
      { name: '시장국면(5단계)', status: 'ok', source: 'signal_engine', note: '매일 07:00 브리핑' },
      { name: '한국 기준금리/CPI/PPI/무역수지', status: 'ok', source: '한국은행 ECOS', note: '15개 거시지표 — 325~420행 ✅' },
      { name: '원/달러 환율', status: 'ok', source: 'price_history', note: '2023-03~ 월평균 40건 ✅' },
      { name: '자동차 판매 (현대/기아/KAMA)', status: 'ok', source: 'KAMA+IR 직접', note: '15개 지표 — 모델별/회사별/점유율' },
      { name: '원자재 가격(철강/구리/유가 등)', status: 'partial', source: 'Yahoo Finance', note: 'price_history에 일부 수집됨' },
      { name: '전력/SMP 가격', status: 'ok', source: 'KPX', note: '51행 / 2025-01~2026-05' },
      { name: 'DRAM/반도체 가격 지수', status: 'partial', source: '관세청 HS8542 수출단가', note: '125개월 (2016-2026) — 반도체 수출단가 proxy ✅ 퀀트지표 연동' },
      { name: '농산물/식품원료 가격', status: 'partial', source: 'ECOS PPI', note: 'PPI 농산물 세부지수 316행 (대리지표)' },
    ],
  },
  {
    category: 'AI 분석 / BigQuery',
    items: [
      { name: 'DeepSeek 텐버거 심층 분석', status: 'ok', source: 'DeepSeek API', note: '24h 캐시, tenbagger_ai_analysis' },
      { name: 'BigQuery 3배주 패턴(stock.db 동기화)', status: 'ok', source: 'BQ triple_pipeline', note: 'triple_pattern_daily stock.db 동기화 완료 ✅' },
      { name: 'BigQuery 복합신호 뷰', status: 'ok', source: 'BQ v_tenbagger_composite_week2', note: '실시간 조회 가능' },
      { name: '텐버거 발굴 결과', status: 'ok', source: 'tenbagger_engine', note: '6축 스코어링 + 스케줄러 자동화' },
      { name: 'TTM/QoQ 실시간 신호', status: 'ok', source: 'earnings_signals', note: '344건 / 268종목' },
      { name: '아침 알림(07:30 텔레그램)', status: 'ok', source: 'tenbagger_morning_alert.py', note: '상위15 + DeepSeek TOP3' },
    ],
  },
  {
    category: '소셜/모멘텀',
    items: [
      { name: '텔레그램 언급 추이', status: 'ok', source: 'telegram_stock_mentions', note: '일별 언급수' },
      { name: 'HS무역 텔레그램 연계', status: 'ok', source: 'hs_trade_lab', note: 'telegram_company_hs_flow_map 37K행' },
      { name: '외신/영문 뉴스', status: 'missing', source: 'Reuters/Bloomberg API', note: '미수집 — 유료 API 필요' },
    ],
  },
];

// ── 패턴 분석 결과 (BigQuery 분석 2026-06-01 기준) ───────────────────
const PATTERN_FINDINGS = {
  // 2026-06-18 재분석: BigQuery price_history 기반 1,991개 3배 달성 종목 역산
  universe: { cnt: 1991, avg_ratio: 4.8, avg_months: 11.0, cnt_5x: 312, cnt_10x: 89 },
  financial: {
    opm_positive_pct: 59.5,   // 59.5%가 흑자 상태
    opm_loss_pct: 40.5,        // 40.5%가 적자 상태에서 출발 (기존 41% → 재확인)
    med_rev_억: 680,
    med_rev_growth: 2.8,       // 매출 성장률 중앙값 2.8% (충격)
    insight: '40.5%가 적자 상태에서 출발 — 적자 패널티는 오히려 역방향',
  },
  supply: {
    inst_positive_pct: 20.8,   // 20.8%만 기관 순매수 (기존 10.3% → 재확인)
    inst_sell_pct: 36.5,       // 36.5% 기관 순매도
    inst_neutral_pct: 42.7,    // 42.7% 중립
    insight: '79.2%는 기관 비매수/매도 상태 — 기관 선매수 선행 아님',
  },
  chart: {
    avg_pct_from_high: -46,    // 52주 고가 대비 평균 -46% 낙폭
    pct_30to70_drawdown: 70.5, // -30~-70% 구간에서 출발한 비율
    pct_gt70_drawdown: 8.6,    // -70% 이상 극심 낙폭
    med_mktcap_억: 1580,       // 시가총액 중앙값 1,580억 (소형주)
    insight: '70.5%가 52주 고가 대비 -30~-70% 낙폭 구간에서 출발',
  },
  disclosure: {
    with_contract_pct: 14.9,
    avg_ratio_with: 3.67,
    avg_ratio_without: 5.34,
    insight: '수주공시 있을 때 오히려 배율 낮음 — 공시 시점엔 이미 선반영',
  },
};

// ── QoQ/TTM 분석 결과 (BigQuery 분석 2026-06-01) ─────────────────────
const GROWTH_FINDINGS = {
  // [A] QoQ 분기별 매출 추세
  qoq: [
    { q: 'Q-1', avg: 381.7, med: 3.0, pos_pct: 54, p10: 259, p20: 177, note: '직전분기' },
    { q: 'Q-2', avg: 200.5, med: 3.0, pos_pct: 56, p10: 238, p20: 144, note: '2분기 전' },
    { q: 'Q-3', avg: 52.2,  med: 2.4, pos_pct: 56, p10: 253, p20: 180, note: '3분기 전' },
    { q: 'Q-4', avg: 144.6, med: 1.6, pos_pct: 53, p10: 234, p20: 155, note: '4분기 전' },
  ],
  qoq_insight: '중앙값 QoQ +3% — 폭발적 성장 없이도 3배 달성. 평균이 높은 이유는 소수 급성장 종목 때문.',

  // [B] 흑자전환 패턴
  inflect: {
    already_profit_pct: 33,
    turning_profit_cnt: 56,
    avg_ratio_inflect: 4.21,
    avg_ratio_steady: 3.83,
    insight: '서지 시작 시 33%만 영업이익 흑자. 흑자전환 시 4.21x > 계속흑자 3.83x',
  },

  // [C] YoY 매출성장률
  yoy: {
    cnt_50plus: 123, pct_50plus: 15,
    cnt_neg: 342, pct_neg: 41,
    med_yoy: 5.7,
    ratio_high_growth: 5.84,
    ratio_neg_growth: 4.45,
    op_inflect_cnt: 87, op_inflect_ratio: 5.64,
    insight: '41%는 매출 역성장 상태. 고성장(+50%) → 5.84x. 영업이익 흑자전환 → 5.64x',
  },

  // [D] TTM 분석 ← 최강 신호
  ttm: {
    cnt_30plus: 163, pct_30plus: 22,
    cnt_neg: 279,
    med_ttm: 6.3,
    ratio_high_ttm: 6.03,
    ratio_neg_ttm: 4.52,
    ttm_op_inflect_cnt: 87, ttm_op_inflect_ratio: 6.14,
    insight: 'TTM 영업이익 흑자전환 → 6.14x (최고 단일 신호). TTM 고성장(30%+) → 6.03x',
  },

  // [E] 거래량 패턴
  volume: {
    vol_3x_pct: 5, vol_2x_pct: 8, vol_1_5x_pct: 12, vol_flat_pct: 80,
    ratio_surge: 5.32, ratio_flat: 3.97,
    insight: '80%는 거래량 정체 상태에서 출발. 거래량 폭발 시 5.32x vs 정체 3.97x — 선택적 신호',
  },

  // [F] Sector RS
  rs: {
    strong_pct: 36, strong_ratio: 4.99,
    med_rs: 13.3,
    insight: '36%가 RS 강세(KOSPI 대비 +20%). 서지 시작 시 이미 RS 형성 중인 경우 多',
  },

  // [G] 복합 패턴 분류 ← 핵심
  patterns: [
    { name: '재무데이터 없는 소형/테마주', key: 'no_fin_data', cnt: 666, ratio: 6.1,
      color: '#f87171', desc: '순수 테마 모멘텀. 재무 검증 불가. 극고위험 극고수익' },
    { name: '기타 (혼합 패턴)', key: 'other', cnt: 517, ratio: 4.58,
      color: '#94a3b8', desc: '위 패턴에 해당 안 되는 다양한 케이스' },
    { name: '영업이익 흑자전환 + 매출 성장', key: 'growth_inflect', cnt: 30, ratio: 4.08,
      color: '#34d399', desc: '매출 +30% + 이전 분기 적자 → 흑자. 검증 가능 최적 패턴' },
    { name: '매출 감소 + 수익성 유지', key: 'rev_decline_profit', cnt: 53, ratio: 4.04,
      color: '#fbbf24', desc: '매출 줄어도 이익률 유지. 구조조정 또는 고마진 전환' },
    { name: '우량 성장주 (이미 알려짐)', key: 'steady_growth', cnt: 58, ratio: 3.48,
      color: '#60a5fa', desc: '매출 성장 + 지속 흑자. 시장이 이미 알고 있어 배율 낮음' },
  ],
  patterns_insight: '우량 성장주(steady growth)가 오히려 배율 최하. 시장에 이미 반영됨.',
};

// 색상 유틸
const STATUS_STYLE = {
  ok:      { bg: 'rgba(16,185,129,0.12)', border: 'rgba(16,185,129,0.3)', color: '#34d399', label: '✅ 수집됨' },
  partial: { bg: 'rgba(251,191,36,0.10)', border: 'rgba(251,191,36,0.3)', color: '#fbbf24', label: '⚠️ 부분' },
  missing: { bg: 'rgba(239,68,68,0.10)',  border: 'rgba(239,68,68,0.25)', color: '#f87171', label: '❌ 미수집' },
};
const DAY_STATUS = {
  done:       { bg: 'rgba(16,185,129,0.15)', border: 'rgba(16,185,129,0.4)',  badge: '완료',   badgeColor: '#34d399' },
  in_progress:{ bg: 'rgba(251,191,36,0.12)', border: 'rgba(251,191,36,0.4)', badge: '진행중', badgeColor: '#fbbf24' },
  planned:    { bg: 'rgba(99,102,241,0.08)', border: 'rgba(99,102,241,0.25)', badge: '예정',  badgeColor: '#a5b4fc' },
  skipped: { bg: 'rgba(100,116,139,0.1)', border: 'rgba(100,116,139,0.2)', badge: '보류', badgeColor: '#94a3b8' },
};

// ────────────────────────────────────────────────────────────────────
export default function TenbaggerProjectView({ megatrendView = null }) {
  const [activeTab, setActiveTab] = useState('plan');
  const [candidates, setCandidates] = useState([]);
  const [candidateMeta, setCandidateMeta] = useState(null);
  const [loading, setLoading] = useState(false);
  const [runTriggered, setRunTriggered] = useState(false);
  const [signals, setSignals] = useState([]);
  const [sigStats, setSigStats] = useState(null);
  const [sigFilter, setSigFilter] = useState('ALL');
  const [sigDays, setSigDays] = useState(90);
  const [sigLoading, setSigLoading] = useState(false);
  const [scanRunning, setScanRunning] = useState(false);
  // 낙폭과대 회복탄력주 탭 state
  const [recov, setRecov] = useState([]);
  const [recovMeta, setRecovMeta] = useState(null);
  const [recovLoading, setRecovLoading] = useState(false);
  const [recovDays, setRecovDays] = useState(10);
  const [recovDrop, setRecovDrop] = useState(8);
  // BQ Week2 복합 신호 탭 state
  const [bqComposite, setBqComposite] = useState([]);
  const [bqSectors, setBqSectors] = useState([]);
  const [bqLoading, setBqLoading] = useState(false);
  const [bqView, setBqView] = useState('stocks'); // 'stocks' | 'sectors'

  // AI 심층 분석 state
  const [aiPanel, setAiPanel] = useState(null);   // { stock_code, stock_name }
  const [aiResult, setAiResult] = useState(null); // 분석 결과
  const [aiLoading, setAiLoading] = useState(false);

  // AI 배치 분석 state
  const [aiList, setAiList] = useState([]);
  const [aiListLoading, setAiListLoading] = useState(false);
  const [batchStatus, setBatchStatus] = useState(null); // 배치 실행 결과
  const [batchRunning, setBatchRunning] = useState(false);
  const [batchTopN, setBatchTopN] = useState(20);
  const [batchMinScore, setBatchMinScore] = useState(55);
  const [batchForce, setBatchForce] = useState(false);

  // 일별 알림 탭 state
  const [dailyAlerts, setDailyAlerts] = useState(null);
  const [dailyDate, setDailyDate] = useState('');
  const [dailyLoading, setDailyLoading] = useState(false);

  const loadDailyAlerts = (date = '') => {
    setDailyLoading(true);
    const q = date ? `?date=${date}` : '';
    fetch(`/api/tenbagger/daily-alerts${q}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { setDailyAlerts(d); if (d?.date) setDailyDate(d.date); })
      .catch(() => {})
      .finally(() => setDailyLoading(false));
  };

  // 포트폴리오 추적 탭 state
  const [portfolio, setPortfolio] = useState([]);
  const [portfolioTracking, setPortfolioTracking] = useState([]);
  const [pfLoading, setPfLoading] = useState(false);

  // 스크리너 v2 state
  const [sv2Results, setSv2Results] = useState([]);
  const [sv2Meta, setSv2Meta] = useState(null);
  const [sv2Loading, setSv2Loading] = useState(false);
  const [sv2Filters, setSv2Filters] = useState({ min_score: 55, market: 'ALL', sector: 'ALL', max_per: 0, max_pbr: 0, sort: 'total_score', q: '' });
  const [sv2Page, setSv2Page] = useState(1);

  // ── Screener v3 state ──
  const [sv3Results, setSv3Results] = useState([]);
  const [sv3Meta, setSv3Meta] = useState(null);
  const [sv3Loading, setSv3Loading] = useState(false);
  const [sv3Filters, setSv3Filters] = useState({ min_v3_score: 50, min_score: 40, market: 'ALL', sector: 'ALL', q: '' });
  const [sv3Page, setSv3Page] = useState(1);

  // FP/FN 분석 state
  const [fpfn, setFpfn] = useState(null);
  const [fpfnLoading, setFpfnLoading] = useState(false);
  const [fpfnDays, setFpfnDays] = useState(7);

  // 데이터 현황 실시간 통계
  const [dataStatus, setDataStatus] = useState(null);

  const [scorePerf, setScorePerf] = useState(null);
  const [scorePerfLoading, setScorePerfLoading] = useState(false);
  const [scorePerfDays, setScorePerfDays] = useState(7);

  // 매매신호 state
  const [actionData, setActionData] = useState(null);
  const [actionLoading, setActionLoading] = useState(false);

  // 퀀트 맥락 패널
  const [quantCtxCode, setQuantCtxCode] = useState(null);
  const [quantCtx, setQuantCtx] = useState(null);
  const [quantCtxLoading, setQuantCtxLoading] = useState(false);

  const loadQuantContext = async (code) => {
    setQuantCtxCode(code);
    setQuantCtx(null);
    setQuantCtxLoading(true);
    try {
      const r = await fetch(API(`/api/tenbagger/quant-context/${code}?months=13`));
      if (r.ok) setQuantCtx(await r.json());
    } catch (e) { console.error(e); }
    setQuantCtxLoading(false);
  };

  const loadSignals = async (days = sigDays, type = sigFilter) => {
    setSigLoading(true);
    try {
      const params = new URLSearchParams({ days, limit: 100 });
      if (type !== 'ALL') params.set('signal_type', type);
      const [r1, r2] = await Promise.all([
        fetch(API(`/api/earnings-signals/latest?${params}`)),
        fetch(API('/api/earnings-signals/stats')),
      ]);
      if (r1.ok) { const d = await r1.json(); setSignals(d.signals || []); }
      if (r2.ok) { const d = await r2.json(); setSigStats(d); }
    } catch {}
    setSigLoading(false);
  };

  const triggerScan = async () => {
    setScanRunning(true);
    try {
      await fetch(API('/api/earnings-signals/scan?days_back=7'), { method: 'POST' });
      await loadSignals();
    } catch {}
    setScanRunning(false);
  };

  const loadCandidates = async () => {
    setLoading(true);
    try {
      const r = await fetch(API('/api/tenbagger/results?limit=30'));
      const d = await r.json();
      setCandidates(d.results || []);
      setCandidateMeta({ run_time: d.run_time, count: d.count });
    } catch {}
    setLoading(false);
  };

  const triggerRun = async () => {
    setRunTriggered(true);
    try {
      await fetch(API('/api/tenbagger/run'), { method: 'POST' });
      await new Promise(r => setTimeout(r, 3000));
      await loadCandidates();
    } catch {}
    setRunTriggered(false);
  };

  const loadRecov = async (days = recovDays, drop = recovDrop) => {
    setRecovLoading(true);
    try {
      const p = new URLSearchParams({ days, drop_min: drop, limit: 50 });
      const r = await fetch(API(`/api/tenbagger/recovery-candidates?${p}`));
      const d = await r.json();
      setRecov(d.results || []);
      setRecovMeta(d.meta || null);
    } catch {}
    setRecovLoading(false);
  };

  const loadAiAnalysis = async (stock_code, stock_name, force = false) => {
    setAiPanel({ stock_code, stock_name });
    setAiResult(null);
    setAiLoading(true);
    try {
      const url = API(`/api/tenbagger/ai-analysis/${stock_code}${force ? '?force=true' : ''}`);
      const r = await fetch(url);
      const d = await r.json();
      setAiResult(d);
    } catch (e) {
      setAiResult({ error: e.message });
    }
    setAiLoading(false);
  };

  const loadBqComposite = async () => {
    setBqLoading(true);
    try {
      const [r1, r2] = await Promise.allSettled([
        fetch(API('/api/tenbagger/bq-composite?limit=100')).then(r => r.json()),
        fetch(API('/api/tenbagger/bq-sector')).then(r => r.json()),
      ]);
      if (r1.status === 'fulfilled' && r1.value.results) setBqComposite(r1.value.results);
      if (r2.status === 'fulfilled' && r2.value.sectors) setBqSectors(r2.value.sectors);
    } catch (e) { console.error(e); }
    setBqLoading(false);
  };

  const loadScreenerV2 = async (filters = sv2Filters, page = sv2Page) => {
    setSv2Loading(true);
    try {
      const p = new URLSearchParams({
        min_score: filters.min_score, market: filters.market,
        sector: filters.sector, sort: filters.sort,
        page, page_size: 30,
        ...(filters.q ? { q: filters.q } : {}),
        ...(filters.max_per > 0 ? { max_per: filters.max_per } : {}),
        ...(filters.max_pbr > 0 ? { max_pbr: filters.max_pbr } : {}),
      });
      const r = await fetch(API(`/api/tenbagger/screener-v2?${p}`));
      if (r.ok) {
        const d = await r.json();
        setSv2Results(d.results || []);
        setSv2Meta({ total: d.total, total_pages: d.total_pages, run_time: d.run_time, sectors: d.sectors || [] });
      }
    } catch (e) { console.error(e); }
    setSv2Loading(false);
  };

  const loadScreenerV3 = async (filters = sv3Filters, page = sv3Page) => {
    setSv3Loading(true);
    try {
      const p = new URLSearchParams({
        min_v3_score: filters.min_v3_score, min_score: filters.min_score,
        market: filters.market, sector: filters.sector,
        page, page_size: 50, sort: 'v3_score',
        ...(filters.q ? { q: filters.q } : {}),
      });
      const r = await fetch(API(`/api/tenbagger/screener-v3?${p}`));
      if (r.ok) {
        const d = await r.json();
        setSv3Results(d.results || []);
        setSv3Meta({ total: d.total, total_pages: d.total_pages, run_time: d.run_time,
                     sectors: d.sectors || [], indicator_map: d.indicator_map || {} });
      }
    } catch (e) { console.error(e); }
    setSv3Loading(false);
  };

  const loadFpFn = async (days = fpfnDays) => {
    setFpfnLoading(true);
    try {
      const r = await fetch(API(`/api/tenbagger/fp-fn-analysis?days_after=${days}&limit=300`));
      if (r.ok) setFpfn(await r.json());
    } catch (e) { console.error(e); }
    setFpfnLoading(false);
  };

  const loadScorePerf = async (days = scorePerfDays) => {
    setScorePerfLoading(true);
    try {
      const r = await fetch(API(`/api/tenbagger/score-performance?days_after=${days}&limit=500`));
      if (r.ok) setScorePerf(await r.json());
    } catch (e) { console.error(e); }
    setScorePerfLoading(false);
  };

  const loadAiList = async () => {
    setAiListLoading(true);
    try {
      const r = await fetch(API('/api/tenbagger/ai-analysis-list?limit=50'));
      if (r.ok) { const d = await r.json(); setAiList(d.items || []); }
    } catch (e) { console.error(e); }
    setAiListLoading(false);
  };

  const runBatch = async (topN = 20, minScore = 55, force = false) => {
    setBatchRunning(true);
    setBatchStatus(null);
    try {
      const r = await fetch(API(`/api/tenbagger/ai-analysis-batch?top_n=${topN}&min_score=${minScore}&force=${force}`), { method: 'POST' });
      const d = await r.json();
      setBatchStatus(d);
      // 3초 후 목록 새로고침
      setTimeout(() => loadAiList(), 3000);
    } catch (e) { setBatchStatus({ status: 'error', message: String(e) }); }
    setBatchRunning(false);
  };

  const loadPortfolioTracking = async () => {
    setPfLoading(true);
    try {
      const [r1, r2] = await Promise.allSettled([
        fetch(API('/api/portfolio')).then(r => r.json()),
        fetch(API('/api/tenbagger/results?limit=200')).then(r => r.json()),
      ]);
      const pfData = r1.status === 'fulfilled' ? (r1.value.holdings || r1.value || []) : [];
      const tenData = r2.status === 'fulfilled' ? (r2.value.results || r2.value || []) : [];
      setPortfolio(pfData);
      // 포트폴리오 종목과 텐버거 후보 교집합 + 스코어 매핑
      const tenMap = {};
      tenData.forEach(t => { tenMap[t.stock_code] = t; });
      const tracked = pfData.map(pf => ({
        ...pf,
        tenbagger_score: tenMap[pf.stock_code]?.total_score || null,
        tenbagger_reasons: tenMap[pf.stock_code]?.reasons || [],
      })).sort((a, b) => (b.tenbagger_score || 0) - (a.tenbagger_score || 0));
      setPortfolioTracking(tracked);
    } catch (e) { console.error(e); }
    setPfLoading(false);
  };

  const loadActionSignals = async () => {
    setActionLoading(true);
    try {
      const r = await fetch(API('/api/tenbagger/action-signals?limit=50'));
      if (r.ok) setActionData(await r.json());
    } catch (e) { console.error(e); }
    setActionLoading(false);
  };

  useEffect(() => {
    if (activeTab === 'daily') loadDailyAlerts();
    if (activeTab === 'screen') loadCandidates();
    if (activeTab === 'signal') loadSignals();
    if (activeTab === 'recovery') loadRecov();
    if (activeTab === 'data') {
      fetch(API('/api/tenbagger/data-status')).then(r => r.json()).then(d => setDataStatus(d)).catch(()=>{});
    }
    if (activeTab === 'bq_week2') loadBqComposite();
    if (activeTab === 'portfolio') loadPortfolioTracking();
    if (activeTab === 'screener2') loadScreenerV2();
    if (activeTab === 'screener_v3') loadScreenerV3();
    if (activeTab === 'fpfn') loadFpFn();
    if (activeTab === 'scoreperf') loadScorePerf();
    if (activeTab === 'ai_batch') loadAiList();
    if (activeTab === 'action') loadActionSignals();
  }, [activeTab]);

  useEffect(() => {
    if (activeTab === 'signal') loadSignals(sigDays, sigFilter);
  }, [sigDays, sigFilter]);

  const TABS = [
    { key: 'action',    label: '📈 매매신호' },
    { key: 'insights',  label: '💡 데이터 인사이트' },
    { key: 'daily',     label: '📅 오늘의 알림' },
    { key: 'signal',    label: '🔔 실시간 신호' },
    { key: 'megatrend', label: '📈 대세종목 발굴' },
    { key: 'screen',    label: '🎯 현재 후보' },
    { key: 'screener2', label: '🔍 스크리너 v2' },
    { key: 'screener_v3', label: '🏭 스크리너v3' },
    { key: 'fpfn',      label: '🔬 FP/FN 오판 분석' },
    { key: 'scoreperf', label: '📈 점수 성능 분석' },
    { key: 'recovery',  label: '📉 낙폭과대 반등주' },
    { key: 'bq_week2',  label: '🔮 BQ 복합신호' },
    { key: 'ai_batch',  label: '🧠 AI 배치 분석' },
    { key: 'portfolio', label: '💼 포트폴리오 추적' },
    { key: 'pattern',   label: '📊 패턴 분석' },
    { key: 'plan',          label: '📅 구현 계획' },
    { key: 'data',          label: '🗂 데이터 현황' },
    { key: 'signal_impact', label: '🔬 시그널 영향성 리포트' },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>

      {/* 헤더 */}
      <div style={{ background: 'linear-gradient(135deg,rgba(99,102,241,0.15),rgba(45,212,191,0.10))',
          borderRadius: '14px', padding: '1.2rem 1.5rem',
          border: '1px solid rgba(99,102,241,0.25)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.7rem', marginBottom: '0.5rem' }}>
          <span style={{ fontSize: '1.6rem' }}>💎</span>
          <h2 style={{ margin: 0, fontSize: '1.2rem', fontWeight: 800, color: '#f1f5f9' }}>
            텐버거 발굴 프로젝트
          </h2>
          <span style={{ marginLeft: 'auto', fontSize: '0.72rem', padding: '0.2rem 0.6rem',
              borderRadius: '20px', background: 'rgba(99,102,241,0.2)',
              border: '1px solid rgba(99,102,241,0.4)', color: '#a5b4fc' }}>
            2026.06.01 – 2026.06.30
          </span>
        </div>
        <p style={{ margin: 0, fontSize: '0.8rem', color: '#94a3b8', lineHeight: 1.7 }}>
          2019년 이후 <b style={{color:'#fbbf24'}}>1,991종목</b>의 3배+ 상승 패턴을 BigQuery 역산 분석.
          기존 가정 4개가 모두 데이터와 <b style={{color:'#f87171'}}>역방향</b>으로 판명됨.
          <b style={{color:'#34d399'}}> 데이터 기반 재설계</b> 완료 (2026-06-18).
        </p>
        {/* 핵심 KPI */}
        <div style={{ display: 'flex', gap: '0.8rem', marginTop: '0.8rem', flexWrap: 'wrap' }}>
          {[
            { label: '역산 분석 대상', value: '1,991종목', color: '#fbbf24' },
            { label: '낙폭과대 출발', value: '70.5%', color: '#f87171' },
            { label: '적자 상태 출발', value: '40.5%', color: '#fb923c' },
            { label: '기관 순매수 비율', value: '20.8%', color: '#60a5fa' },
          ].map(k => (
            <div key={k.label} style={{ background: 'rgba(255,255,255,0.05)',
                border: '1px solid rgba(255,255,255,0.1)', borderRadius: '8px',
                padding: '0.4rem 0.8rem', textAlign: 'center', minWidth: '90px' }}>
              <div style={{ fontSize: '1rem', fontWeight: 800, color: k.color }}>{k.value}</div>
              <div style={{ fontSize: '0.65rem', color: '#94a3b8' }}>{k.label}</div>
            </div>
          ))}
        </div>
      </div>

      {/* 탭 네비게이션 */}
      <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
        {TABS.map(t => (
          <button key={t.key} onClick={() => setActiveTab(t.key)}
            style={{ padding: '0.5rem 1rem', borderRadius: '8px', border: '1px solid',
              cursor: 'pointer', fontSize: '0.82rem', fontWeight: activeTab === t.key ? 700 : 400,
              background: activeTab === t.key ? 'rgba(99,102,241,0.25)' : 'rgba(255,255,255,0.04)',
              borderColor: activeTab === t.key ? '#6366f1' : 'rgba(255,255,255,0.1)',
              color: activeTab === t.key ? '#a5b4fc' : '#94a3b8' }}>
            {t.label}
          </button>
        ))}
      </div>

      {/* ── 탭: 대세종목 발굴 (V10/V11/V12) ─────────────────────────── */}
      {activeTab === 'megatrend' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>
          {megatrendView || (
            <div style={{ padding: '2rem', textAlign: 'center', color: '#94a3b8',
                background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)',
                borderRadius: '10px' }}>
              대세종목 발굴 화면을 불러오지 못했습니다.
            </div>
          )}
        </div>
      )}

      {/* ── 탭: 매매신호 ────────────────────────────────────────────── */}
      {activeTab === 'action' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          {/* 백테스트 성능 배너 */}
          <div style={{ background: 'rgba(99,102,241,0.12)', border: '1px solid rgba(99,102,241,0.3)', borderRadius: '0.75rem', padding: '1rem 1.2rem' }}>
            <div style={{ fontSize: '0.85rem', color: '#a5b4fc', fontWeight: 600, marginBottom: '0.4rem' }}>
              📊 백테스트 기반 전략 (2019-2024, 6년) — 데이터 역산 v3 (주간리밸런싱 + 거래량60일2배)
            </div>
            <div style={{ fontSize: '0.72rem', color: '#64748b', marginBottom: '0.5rem', lineHeight: 1.6 }}>
              ※ 실제 3배+종목 n=37 역산 결과: 기관/외국인 매도 상태 84% → 수급조건 제거 · 저점후 거래량 평균 9.1배(57%가 2배+) → 60일평균 2배+ 적용 · 월별 손절집행 → 실제 -36~58% 손실 → 주간체크 전환
            </div>
            <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap' }}>
              {[
                { label: '누적 수익률', val: '+110.4%', color: '#4ade80' },
                { label: 'KOSPI 대비', val: '5.70배', color: '#4ade80' },
                { label: 'MDD', val: '-38.8%', color: '#f87171' },
                { label: '양수연도', val: '4/6년', color: '#fbbf24' },
                { label: '매수조건', val: '낙폭-30~85% / 거래량60일2배+ / 점수≥50 / 시총≤3천억', color: '#94a3b8' },
                { label: '매도조건', val: '손절-25%(주간) / 영업적자2Q / 60일고점-35% / 최대730일', color: '#94a3b8' },
              ].map(({ label, val, color }) => (
                <div key={label}>
                  <div style={{ fontSize: '0.72rem', color: '#64748b' }}>{label}</div>
                  <div style={{ fontSize: '0.95rem', fontWeight: 700, color }}>{val}</div>
                </div>
              ))}
            </div>
          </div>

          {/* 새로고침 버튼 */}
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <button onClick={loadActionSignals} disabled={actionLoading}
              style={{ background: 'rgba(99,102,241,0.2)', border: '1px solid rgba(99,102,241,0.4)', borderRadius: '0.5rem', padding: '0.4rem 1rem', color: '#a5b4fc', cursor: 'pointer', fontSize: '0.85rem' }}>
              {actionLoading ? '계산 중…' : '🔄 신호 새로고침'}
            </button>
          </div>

          {actionLoading ? (
            <div style={{ textAlign: 'center', color: '#64748b', padding: '2rem' }}>신호 계산 중…</div>
          ) : actionData ? (
            <>
              {/* 매수 신호 */}
              <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: '0.75rem', padding: '1rem' }}>
                <div style={{ fontSize: '0.9rem', fontWeight: 700, color: '#4ade80', marginBottom: '0.75rem' }}>
                  🟢 매수 신호 ({(actionData.buy_signals || []).length}건)
                </div>
                {(actionData.buy_signals || []).length === 0 ? (
                  <div style={{ color: '#64748b', fontSize: '0.85rem' }}>현재 매수 신호 없음 (조건 미충족)</div>
                ) : (
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
                      <thead>
                        <tr style={{ color: '#64748b', borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
                          {['종목명', '점수', '낙폭%', '거래량배수', '시총(억)', '강도', '사유'].map(h => (
                            <th key={h} style={{ padding: '0.4rem 0.6rem', textAlign: 'left', fontWeight: 500 }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {(actionData.buy_signals || []).map((r, i) => (
                          <tr key={i} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)', background: i % 2 === 0 ? 'rgba(255,255,255,0.02)' : 'transparent' }}>
                            <td style={{ padding: '0.45rem 0.6rem', color: '#e2e8f0', fontWeight: 600 }}>{r.stock_name || r.stock_code}</td>
                            <td style={{ padding: '0.45rem 0.6rem', color: '#4ade80', fontWeight: 700 }}>{r.total_score}</td>
                            <td style={{ padding: '0.45rem 0.6rem', color: '#f87171' }}>{r.from_high_pct != null ? r.from_high_pct.toFixed(1) : '-'}%</td>
                            <td style={{ padding: '0.45rem 0.6rem', color: '#94a3b8' }}>{r.vol_ratio != null ? r.vol_ratio.toFixed(2) : '-'}x</td>
                            <td style={{ padding: '0.45rem 0.6rem', color: '#94a3b8' }}>{r.market_cap != null ? r.market_cap.toLocaleString() : '-'}</td>
                            <td style={{ padding: '0.45rem 0.6rem' }}>
                              <span style={{ background: r.buy_strength === '강' ? 'rgba(74,222,128,0.2)' : r.buy_strength === '중' ? 'rgba(251,191,36,0.2)' : 'rgba(148,163,184,0.15)', color: r.buy_strength === '강' ? '#4ade80' : r.buy_strength === '중' ? '#fbbf24' : '#94a3b8', borderRadius: '0.3rem', padding: '0.1rem 0.4rem', fontSize: '0.78rem' }}>
                                {r.buy_strength || '-'}
                              </span>
                            </td>
                            <td style={{ padding: '0.45rem 0.6rem', color: '#64748b', fontSize: '0.78rem', maxWidth: '200px' }}>{(r.buy_reasons || []).filter(x => x.startsWith('✅')).join(' · ')}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              {/* 관망 종목 */}
              {(actionData.watch_signals || []).length > 0 && (
                <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)', borderRadius: '0.75rem', padding: '1rem' }}>
                  <div style={{ fontSize: '0.9rem', fontWeight: 700, color: '#fbbf24', marginBottom: '0.75rem' }}>
                    🟡 관망 후보 ({(actionData.watch_signals || []).length}건 — 점수 충족, 나머지 조건 미완)
                  </div>
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
                      <thead>
                        <tr style={{ color: '#64748b', borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
                          {['종목명', '점수', '낙폭%', '거래량배수', '시총(억)', '미충족 조건'].map(h => (
                            <th key={h} style={{ padding: '0.4rem 0.6rem', textAlign: 'left', fontWeight: 500 }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {(actionData.watch_signals || []).slice(0, 20).map((r, i) => (
                          <tr key={i} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)', background: i % 2 === 0 ? 'rgba(255,255,255,0.02)' : 'transparent' }}>
                            <td style={{ padding: '0.45rem 0.6rem', color: '#e2e8f0' }}>{r.stock_name || r.stock_code}</td>
                            <td style={{ padding: '0.45rem 0.6rem', color: '#fbbf24', fontWeight: 700 }}>{r.total_score}</td>
                            <td style={{ padding: '0.45rem 0.6rem', color: '#94a3b8' }}>{r.from_high_pct != null ? r.from_high_pct.toFixed(1) : '-'}%</td>
                            <td style={{ padding: '0.45rem 0.6rem', color: '#94a3b8' }}>{r.vol_ratio != null ? r.vol_ratio.toFixed(2) : '-'}x</td>
                            <td style={{ padding: '0.45rem 0.6rem', color: '#94a3b8' }}>{r.market_cap != null ? r.market_cap.toLocaleString() : '-'}</td>
                            <td style={{ padding: '0.45rem 0.6rem', color: '#f87171', fontSize: '0.78rem' }}>{(r.buy_failed || []).join(', ')}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* 파라미터 요약 */}
              <div style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid rgba(255,255,255,0.06)', borderRadius: '0.75rem', padding: '0.75rem 1rem', fontSize: '0.78rem', color: '#64748b' }}>
                <strong style={{ color: '#94a3b8' }}>적용 파라미터</strong>{' '}
                {JSON.stringify(actionData.params || {})}
                {actionData.backtest && (
                  <span style={{ marginLeft: '1rem', color: '#6366f1' }}>
                    백테스트 ({actionData.backtest.period}): {actionData.backtest.total_ret} / KOSPI {actionData.backtest.kospi_ratio} / MDD {actionData.backtest.mdd}
                  </span>
                )}
              </div>
            </>
          ) : (
            <div style={{ textAlign: 'center', color: '#64748b', padding: '2rem' }}>신호를 로드하려면 새로고침을 클릭하세요.</div>
          )}
        </div>
      )}

      {/* ── 탭: 오늘의 알림 ────────────────────────────────────────── */}
      {activeTab === 'daily' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>
          {/* 날짜 선택 + 요약 */}
          <div style={{ background: 'rgba(251,191,36,0.08)', border: '1px solid rgba(251,191,36,0.2)', borderRadius: '10px', padding: '0.8rem 1.2rem', display: 'flex', alignItems: 'center', gap: '1rem', flexWrap: 'wrap' }}>
            <span style={{ fontSize: '0.85rem', fontWeight: 700, color: '#fbbf24' }}>📅 텐버거 아침 알림 이력</span>
            {dailyAlerts?.available_dates?.length > 0 && (
              <select value={dailyDate} onChange={e => { setDailyDate(e.target.value); loadDailyAlerts(e.target.value); }}
                style={{ background: 'rgba(0,0,0,0.4)', border: '1px solid rgba(251,191,36,0.3)', borderRadius: '6px', color: '#fbbf24', padding: '0.25rem 0.5rem', fontSize: '0.8rem' }}>
                {dailyAlerts.available_dates.map(d => <option key={d} value={d}>{d}</option>)}
              </select>
            )}
            {dailyAlerts && (
              <div style={{ display: 'flex', gap: '1rem', marginLeft: 'auto' }}>
                <span style={{ fontSize: '0.8rem', color: '#fbbf24' }}>전체 <b>{dailyAlerts.total}</b>종목</span>
                <span style={{ fontSize: '0.8rem', color: '#34d399' }}>신규 <b>{dailyAlerts.new_count}</b>종목</span>
              </div>
            )}
          </div>

          {dailyLoading && <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}>로딩 중…</div>}
          {!dailyLoading && dailyAlerts?.alerts?.length === 0 && (
            <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)', fontSize: '0.9rem' }}>
              저장된 알림 없음 — tenbagger_morning_alert.py 실행 후 저장됩니다
            </div>
          )}
          {!dailyLoading && dailyAlerts?.alerts?.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
              {dailyAlerts.alerts.map((a, i) => (
                <div key={a.stock_code} style={{ background: a.is_new ? 'rgba(52,211,153,0.08)' : 'rgba(255,255,255,0.03)', border: `1px solid ${a.is_new ? 'rgba(52,211,153,0.3)' : 'rgba(255,255,255,0.08)'}`, borderRadius: '10px', padding: '0.8rem 1rem' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '0.3rem' }}>
                    <span style={{ fontSize: '0.75rem', fontWeight: 700, color: '#94a3b8', minWidth: '24px' }}>{i+1}</span>
                    {a.is_new && <span style={{ fontSize: '0.65rem', background: 'rgba(52,211,153,0.2)', border: '1px solid rgba(52,211,153,0.4)', borderRadius: '4px', padding: '0.1rem 0.35rem', color: '#34d399', fontWeight: 700 }}>신규</span>}
                    <span style={{ fontWeight: 700, color: '#f1f5f9' }}>{a.stock_name}</span>
                    <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>({a.stock_code})</span>
                    <span style={{ fontSize: '0.72rem', color: '#94a3b8' }}>{a.sector_large || '-'}</span>
                    <span style={{ marginLeft: 'auto', fontSize: '0.85rem', fontWeight: 800, color: (a.total_score||0) >= 70 ? '#fbbf24' : '#a5b4fc' }}>{a.total_score||0}점</span>
                    {a.per && <span style={{ fontSize: '0.72rem', color: 'var(--text-secondary)' }}>PER {a.per}</span>}
                    {a.pbr && <span style={{ fontSize: '0.72rem', color: 'var(--text-secondary)' }}>PBR {a.pbr}</span>}
                  </div>
                  {a.best_reason && (
                    <div style={{ fontSize: '0.75rem', color: '#cbd5e1', lineHeight: 1.5, paddingLeft: '30px' }}>
                      📌 {a.best_reason}
                    </div>
                  )}
                  {a.reasons?.length > 0 && (
                    <div style={{ marginTop: '0.25rem', paddingLeft: '30px', display: 'flex', flexWrap: 'wrap', gap: '0.3rem' }}>
                      {a.reasons.slice(0,4).map((r,j) => <span key={j} style={{ fontSize: '0.65rem', background: 'rgba(165,180,252,0.1)', border: '1px solid rgba(165,180,252,0.2)', borderRadius: '4px', padding: '0.1rem 0.35rem', color: '#a5b4fc' }}>{r}</span>)}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── 탭 0: 실시간 신호 ──────────────────────────────────────── */}
      {activeTab === 'signal' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>

          {/* 상단 KPI 카드 */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(130px,1fr))', gap: '0.6rem' }}>
            {[
              { key: 'TTM_BOTH',        label: '흑자전환+고성장', color: '#f472b6', ratio: '6.5x' },
              { key: 'TTM_OP_INFLECT',  label: 'TTM 흑자전환',    color: '#34d399', ratio: '6.14x' },
              { key: 'TTM_REV_30',      label: 'TTM 매출 +30%',   color: '#60a5fa', ratio: '6.03x' },
              { key: 'TTM_OP_ACCEL',    label: 'TTM 이익 가속',   color: '#fbbf24', ratio: '5.5x' },
              { key: 'QOQ_REV_20_2CON', label: 'QoQ +20% 2연속',  color: '#a78bfa', ratio: '5.0x' },
            ].map(s => {
              const cnt = sigStats?.by_type?.[s.key]?.cnt || 0;
              return (
                <div key={s.key}
                  onClick={() => setSigFilter(sigFilter === s.key ? 'ALL' : s.key)}
                  style={{ background: sigFilter === s.key ? `${s.color}22` : 'rgba(255,255,255,0.04)',
                      border: `1px solid ${sigFilter === s.key ? s.color : 'rgba(255,255,255,0.08)'}`,
                      borderRadius: '10px', padding: '0.6rem 0.8rem', cursor: 'pointer',
                      transition: 'all .15s' }}>
                  <div style={{ fontSize: '1.4rem', fontWeight: 800, color: s.color }}>{cnt}</div>
                  <div style={{ fontSize: '0.65rem', color: '#94a3b8', marginTop: '0.15rem' }}>{s.label}</div>
                  <div style={{ fontSize: '0.7rem', color: s.color, fontWeight: 700 }}>평균 {s.ratio}</div>
                </div>
              );
            })}
          </div>

          {/* 컨트롤 바 */}
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
            <select value={sigDays} onChange={e => setSigDays(Number(e.target.value))}
              style={{ background: 'var(--bg-dark)', border: '1px solid rgba(255,255,255,0.15)',
                borderRadius: '6px', color: 'var(--text-primary)', padding: '0.3rem 0.6rem', fontSize: '0.8rem' }}>
              {[7,30,60,90,180].map(d => <option key={d} value={d}>최근 {d}일</option>)}
            </select>
            <select value={sigFilter} onChange={e => setSigFilter(e.target.value)}
              style={{ background: 'var(--bg-dark)', border: '1px solid rgba(255,255,255,0.15)',
                borderRadius: '6px', color: 'var(--text-primary)', padding: '0.3rem 0.6rem', fontSize: '0.8rem' }}>
              <option value="ALL">전체 신호</option>
              <option value="TTM_BOTH">💎 흑자전환+고성장</option>
              <option value="TTM_OP_INFLECT">🔄 TTM 흑자전환</option>
              <option value="TTM_REV_30">🚀 TTM 매출 +30%</option>
              <option value="TTM_OP_ACCEL">⚡ TTM 이익 가속</option>
              <option value="QOQ_REV_20_2CON">📈 QoQ 2연속</option>
            </select>
            <button onClick={() => loadSignals(sigDays, sigFilter)} disabled={sigLoading}
              style={{ padding: '0.3rem 0.7rem', borderRadius: '6px', cursor: 'pointer', fontSize: '0.8rem',
                background: 'rgba(99,102,241,0.15)', border: '1px solid rgba(99,102,241,0.3)', color: '#a5b4fc' }}>
              {sigLoading ? '⏳' : '🔄'} 새로고침
            </button>
            <button onClick={triggerScan} disabled={scanRunning}
              style={{ padding: '0.3rem 0.8rem', borderRadius: '6px', cursor: 'pointer', fontSize: '0.8rem',
                background: 'rgba(16,185,129,0.15)', border: '1px solid rgba(16,185,129,0.3)', color: '#34d399' }}>
              {scanRunning ? '⏳ 스캔 중...' : '▶ 즉시 스캔'}
            </button>
            <span style={{ marginLeft: 'auto', fontSize: '0.72rem', color: '#64748b' }}>
              {signals.length}건 | 매일 06:00 + 분기시즌 2h마다 자동
            </span>
          </div>

          {/* 시스템 설명 */}
          <div style={{ background: 'rgba(16,185,129,0.07)', borderRadius: '8px',
              border: '1px solid rgba(16,185,129,0.2)', padding: '0.6rem 0.9rem',
              fontSize: '0.72rem', color: '#94a3b8', lineHeight: 1.7 }}>
            <b style={{color:'#34d399'}}>자동 감지 로직</b>: DART 분기보고서 수집 → financial_data 업데이트 →
            TTM(최근 4분기 합산) 재계산 → 신호 조건 충족 시 저장 + 텔레그램 발송<br/>
            <b style={{color:'#fbbf24'}}>분기보고서 시즌</b>: 3월(사업보고서), 5월(1분기), 8월(반기), 11월(3분기) →
            해당 월엔 낮 시간 2시간마다 증분 스캔
          </div>

          {/* 신호 목록 */}
          {sigLoading ? (
            <div style={{textAlign:'center',padding:'2rem',color:'#94a3b8'}}>⏳ 신호 로딩 중...</div>
          ) : signals.length === 0 ? (
            <div style={{textAlign:'center',padding:'2rem',color:'#94a3b8'}}>
              신호 없음 — "즉시 스캔" 버튼으로 스캔하거나 기간을 늘려보세요
            </div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
              {signals.map((s, i) => {
                const isPrime = s.signal_type === 'TTM_BOTH';
                return (
                  <div key={i} style={{
                      background: isPrime ? 'rgba(244,114,182,0.08)' : 'rgba(255,255,255,0.03)',
                      border: `1px solid ${isPrime ? 'rgba(244,114,182,0.3)' : 'rgba(255,255,255,0.07)'}`,
                      borderRadius: '8px', padding: '0.6rem 0.8rem',
                      display: 'flex', gap: '0.7rem', alignItems: 'flex-start' }}>
                    {/* 신호 배지 */}
                    <div style={{ minWidth: '32px', textAlign: 'center',
                        fontSize: '1.3rem', lineHeight: 1 }}>
                      {s.signal_emoji}
                    </div>
                    {/* 종목 정보 */}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem',
                          marginBottom: '0.2rem', flexWrap: 'wrap' }}>
                        <span style={{ fontWeight: 700, fontSize: '0.85rem', color: '#f1f5f9' }}>
                          {s.stock_name}
                        </span>
                        <span style={{ fontSize: '0.68rem', color: '#64748b', fontFamily: 'monospace' }}>
                          {s.stock_code}
                        </span>
                        <span style={{ fontSize: '0.65rem', padding: '0.1rem 0.4rem',
                            borderRadius: '4px', background: `${s.signal_color}22`,
                            border: `1px solid ${s.signal_color}44`,
                            color: s.signal_color, fontWeight: 700 }}>
                          {s.signal_label}
                        </span>
                        {isPrime && (
                          <span style={{ fontSize: '0.65rem', padding: '0.1rem 0.4rem',
                              borderRadius: '4px', background: 'rgba(244,114,182,0.2)',
                              color: '#f472b6', fontWeight: 700 }}>
                            🏆 최강신호
                          </span>
                        )}
                        <span style={{ fontSize: '0.65rem', color: '#94a3b8', marginLeft: 'auto' }}>
                          {s.year}년 {s.quarter}Q
                        </span>
                      </div>
                      <div style={{ fontSize: '0.72rem', color: '#94a3b8', marginBottom: '0.2rem' }}>
                        {s.detail}
                      </div>
                      <div style={{ display: 'flex', gap: '0.8rem', flexWrap: 'wrap',
                          fontSize: '0.68rem', color: '#64748b' }}>
                        <span>섹터: {s.sector_large || '-'}</span>
                        <span>시총: {s.mktcap_억 ? (s.mktcap_억 >= 10000 ? (s.mktcap_억/10000).toFixed(1)+'조' : s.mktcap_억+'억') : '-'}</span>
                        {s.current_price && <span>현재가: {s.current_price.toLocaleString()}원</span>}
                        {s.return_since_signal != null && (
                          <span style={{ color: s.return_since_signal >= 0 ? '#ef4444' : '#3b82f6',
                              fontWeight: 700 }}>
                            신호 후: {s.return_since_signal >= 0 ? '+' : ''}{s.return_since_signal}%
                          </span>
                        )}
                        <span style={{ color: s.signal_color, fontWeight: 600 }}>
                          역사적 평균 {s.avg_ratio_hist}배
                        </span>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* ── 탭 1: 구현 계획 ────────────────────────────────────────── */}
      {activeTab === 'plan' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          {IMPL_PLAN.map(week => (
            <div key={week.week} style={{ background: 'rgba(255,255,255,0.03)',
                border: '1px solid rgba(255,255,255,0.08)', borderRadius: '12px', overflow: 'hidden' }}>
              <div style={{ padding: '0.7rem 1rem',
                  background: `linear-gradient(90deg,rgba(99,102,241,0.15),transparent)`,
                  borderBottom: '1px solid rgba(255,255,255,0.07)',
                  display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
                <span style={{ background: '#6366f1', color: '#fff', borderRadius: '6px',
                    padding: '0.15rem 0.5rem', fontSize: '0.72rem', fontWeight: 700 }}>
                  Week {week.week}
                </span>
                <span style={{ fontSize: '0.82rem', color: '#e2e8f0', fontWeight: 600 }}>
                  {week.title}
                </span>
                <span style={{ marginLeft: 'auto', fontSize: '0.72rem', color: '#94a3b8' }}>
                  {week.period}
                </span>
              </div>
              <div style={{ padding: '0.6rem' }}>
                {week.days.map((day, di) => {
                  const ds = DAY_STATUS[day.status];
                  return (
                    <div key={di} style={{ marginBottom: '0.5rem', padding: '0.6rem 0.8rem',
                        borderRadius: '8px', border: `1px solid ${ds.border}`,
                        background: ds.bg }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem',
                          marginBottom: '0.4rem' }}>
                        <span style={{ fontSize: '0.78rem', fontWeight: 700, color: '#e2e8f0' }}>
                          {day.date}
                        </span>
                        <span style={{ fontSize: '0.65rem', padding: '0.1rem 0.4rem',
                            borderRadius: '4px', background: `${ds.badgeColor}22`,
                            border: `1px solid ${ds.badgeColor}55`, color: ds.badgeColor }}>
                          {ds.badge}
                        </span>
                      </div>
                      <ul style={{ margin: 0, paddingLeft: '1.1rem',
                          listStyleType: day.status === 'done' ? 'none' : 'disc' }}>
                        {day.items.map((item, ii) => (
                          <li key={ii} style={{ fontSize: '0.75rem', color: '#cbd5e1',
                              lineHeight: 1.7, paddingLeft: day.status === 'done' ? '0' : '0' }}>
                            {day.status === 'done' && <span style={{color:'#34d399',marginRight:'0.3rem'}}>✓</span>}
                            {day.status === 'in_progress' && <span style={{color:'#fbbf24',marginRight:'0.3rem'}}>⟳</span>}
                            {item}
                          </li>
                        ))}
                      </ul>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── 탭: 데이터 인사이트 (충격적 발견 리포트) ──────────────────── */}
      {activeTab === 'insights' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1.2rem' }}>
          {/* 섹션 헤더 */}
          <div style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.25)',
              borderRadius: '10px', padding: '0.8rem 1rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.3rem' }}>
              <span style={{ fontSize: '0.7rem', fontWeight: 700, padding: '0.15rem 0.5rem',
                  borderRadius: '4px', background: 'rgba(239,68,68,0.2)', color: '#f87171' }}>
                ⚠ 데이터 기반 역분석 결과
              </span>
            </div>
            <div style={{ fontSize: '0.85rem', fontWeight: 700, color: '#f1f5f9', marginBottom: '0.2rem' }}>
              기존 엔진의 4가지 가정이 모두 데이터와 역방향으로 판명됨
            </div>
            <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>
              BigQuery price_history 기반 실제 3배 달성 1,991개 종목 역산 분석 (2026-06-18)
            </div>
          </div>

          {/* 핵심 수치 5개 */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: '0.6rem' }}>
            {[
              { n: '발견 1', val: '70.5%', color: '#f87171', label: '52주 고가 대비 -30~70%\n낙폭과대 구간에서 출발' },
              { n: '발견 2', val: '40.5%', color: '#fb923c', label: '출발 시점에\n영업이익이 적자 상태' },
              { n: '발견 3', val: '20.8%', color: '#fbbf24', label: '저점에서 기관 순매수\n(79.2%는 기관 매도/중립)' },
              { n: '발견 4', val: '2.8%',  color: '#60a5fa', label: '저점 직전 매출 성장률\n중앙값 (폭발 성장 아님)' },
              { n: '발견 5', val: '1,580억', color: '#34d399', label: '시가총액 중앙값\n(소형주 편향 뚜렷)' },
            ].map(f => (
              <div key={f.n} style={{ background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)',
                  borderRadius: '8px', padding: '0.75rem 0.8rem' }}>
                <div style={{ fontSize: '0.62rem', color: '#64748b', marginBottom: '3px' }}>{f.n}</div>
                <div style={{ fontSize: '1.4rem', fontWeight: 800, color: f.color, lineHeight: 1.1, marginBottom: '4px' }}>{f.val}</div>
                <div style={{ fontSize: '0.65rem', color: '#94a3b8', lineHeight: 1.5, whiteSpace: 'pre-line' }}>{f.label}</div>
              </div>
            ))}
          </div>

          {/* 낙폭 분포 바차트 */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.8rem' }}>
            <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)',
                borderRadius: '10px', padding: '1rem' }}>
              <div style={{ fontSize: '0.78rem', fontWeight: 700, color: '#e2e8f0', marginBottom: '0.2rem' }}>
                낙폭 분포 — 3배 달성 종목의 출발점
              </div>
              <div style={{ fontSize: '0.65rem', color: '#64748b', marginBottom: '0.8rem' }}>
                52주 고가 대비 하락률 기준 · 빨간 구간 = 황금지대
              </div>
              {[
                { label: '신고가권 (-5% 이내)', pct: 4.2, hot: false },
                { label: '-5% ~ -15%',          pct: 8.1, hot: false },
                { label: '-15% ~ -30%',          pct: 17.2, hot: false },
                { label: '-30% ~ -50%',          pct: 41.3, hot: true },
                { label: '-50% ~ -70%',          pct: 29.2, hot: true },
                { label: '-70% 이하',            pct: 8.6, hot: false },
              ].map(b => (
                <div key={b.label} style={{ marginBottom: '5px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between',
                      fontSize: '0.67rem', color: b.hot ? '#f1f5f9' : '#64748b', marginBottom: '2px' }}>
                    <span>{b.hot ? '★ ' : ''}{b.label}</span>
                    <span style={{ fontWeight: b.hot ? 700 : 400 }}>{b.pct}%</span>
                  </div>
                  <div style={{ height: '10px', background: 'rgba(255,255,255,0.06)', borderRadius: '3px', overflow: 'hidden' }}>
                    <div style={{ width: `${b.pct / 45 * 100}%`, height: '100%', borderRadius: '3px',
                        background: b.hot ? '#ef4444' : 'rgba(148,163,184,0.3)' }} />
                  </div>
                </div>
              ))}
            </div>

            {/* 기관 순매수 분포 */}
            <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)',
                borderRadius: '10px', padding: '1rem' }}>
              <div style={{ fontSize: '0.78rem', fontWeight: 700, color: '#e2e8f0', marginBottom: '0.2rem' }}>
                기관 순매수 상태 분포
              </div>
              <div style={{ fontSize: '0.65rem', color: '#64748b', marginBottom: '1rem' }}>
                저점 직전 20일 기관 평균 순매수 기준
              </div>
              {[
                { label: '순매도', pct: 36.5, color: '#ef4444', desc: '기관이 팔고 있었다' },
                { label: '중립 (±0)',  pct: 42.7, color: '#94a3b8', desc: '거의 무관심 상태' },
                { label: '순매수', pct: 20.8, color: '#10b981', desc: '기관도 사고 있었다' },
              ].map(d => (
                <div key={d.label} style={{ marginBottom: '0.8rem' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between',
                      fontSize: '0.7rem', color: '#e2e8f0', marginBottom: '3px' }}>
                    <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <span style={{ width: '8px', height: '8px', borderRadius: '2px',
                          background: d.color, display: 'inline-block' }} />
                      {d.label}
                    </span>
                    <span style={{ fontWeight: 700, color: d.color }}>{d.pct}%</span>
                  </div>
                  <div style={{ height: '18px', background: 'rgba(255,255,255,0.06)', borderRadius: '4px', overflow: 'hidden' }}>
                    <div style={{ width: `${d.pct}%`, height: '100%', borderRadius: '4px', background: d.color, opacity: 0.8 }} />
                  </div>
                  <div style={{ fontSize: '0.62rem', color: '#64748b', marginTop: '2px' }}>{d.desc}</div>
                </div>
              ))}
              <div style={{ marginTop: '0.5rem', padding: '0.4rem 0.6rem', background: 'rgba(239,68,68,0.08)',
                  borderRadius: '6px', fontSize: '0.68rem', color: '#fca5a5', lineHeight: 1.5 }}>
                💡 결론: 기관 순매수는 텐버거 확인 신호가 아니다.<br/>
                기관이 팔 때 개인/테마 주도로 출발하는 게 실제 패턴.
              </div>
            </div>
          </div>

          {/* 가정 vs 실제 비교 테이블 */}
          <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)',
              borderRadius: '10px', overflow: 'hidden' }}>
            <div style={{ padding: '0.7rem 1rem', borderBottom: '1px solid rgba(255,255,255,0.07)',
                fontSize: '0.82rem', fontWeight: 700, color: '#e2e8f0' }}>
              기존 엔진의 가정 vs 실제 데이터 — 왜 백테스트가 실패했나
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.72rem' }}>
              <thead>
                <tr style={{ background: 'rgba(255,255,255,0.04)' }}>
                  {['점수 항목', '기존 엔진 가정', '실제 데이터', '오류 방향', '점수 변화'].map(h => (
                    <th key={h} style={{ padding: '0.5rem 0.7rem', textAlign: 'left',
                        color: '#64748b', fontWeight: 600, borderBottom: '1px solid rgba(255,255,255,0.07)' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {[
                  {
                    item: '기술적 추세 (MA정배열)',
                    old: 'MA정배열 = 매수 신호 (15점)',
                    real: '평균 출발점 52주 고가 -46%\n정배열 = 이미 상승 완료 = 늦은 진입',
                    dir: '❌ 완전 역방향', dirColor: '#f87171',
                    change: '15pt → 낙폭과대 25pt로 교체'
                  },
                  {
                    item: '영업이익 성장',
                    old: '적자 패널티 적용 (20점)',
                    real: '40.5%가 적자 상태에서 출발\n흑자전환이 오히려 최고 신호',
                    dir: '❌ 역방향 패널티', dirColor: '#fb923c',
                    change: '적자 패널티 → 흑자전환 +15pt'
                  },
                  {
                    item: '수급 (기관 순매수)',
                    old: '기관 순매수 = 핵심 확인 신호 (15점)',
                    real: '79.2%가 기관 비매수/매도 상태\n기관 저점 매도 = 실제 바닥 신호',
                    dir: '❌ 심각한 과대평가', dirColor: '#f87171',
                    change: '15pt → 수급반전 10pt (저점 감지)'
                  },
                  {
                    item: '매출 성장',
                    old: '15%+ 성장 = 강한 신호 (20점)',
                    real: '3배 종목 매출 성장 중앙값 2.8%\n33.7%만 15%+ 성장',
                    dir: '⚠ 과대평가', dirColor: '#fbbf24',
                    change: '20pt → 펀더멘털변화 25pt 내 통합'
                  },
                  {
                    item: '밸류에이션',
                    old: 'PER/PBR 기준 (15점)',
                    real: '소형주 편향 (중앙 시총 1,580억)\n저PBR+소형주 조합이 핵심',
                    dir: '✓ 방향 맞음', dirColor: '#34d399',
                    change: '15pt → 저평가+소형주 20pt 강화'
                  },
                ].map((r, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
                    <td style={{ padding: '0.55rem 0.7rem', color: '#cbd5e1', fontWeight: 600 }}>{r.item}</td>
                    <td style={{ padding: '0.55rem 0.7rem', color: '#f87171' }}>{r.old}</td>
                    <td style={{ padding: '0.55rem 0.7rem', color: '#94a3b8', whiteSpace: 'pre-line', lineHeight: 1.5 }}>{r.real}</td>
                    <td style={{ padding: '0.55rem 0.7rem', color: r.dirColor, fontWeight: 700 }}>{r.dir}</td>
                    <td style={{ padding: '0.55rem 0.7rem', color: '#34d399', fontSize: '0.68rem' }}>{r.change}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* 새 점수 구조 */}
          <div style={{ background: 'rgba(16,185,129,0.06)', border: '1px solid rgba(16,185,129,0.2)',
              borderRadius: '10px', padding: '1rem' }}>
            <div style={{ fontSize: '0.78rem', fontWeight: 700, color: '#34d399', marginBottom: '0.8rem' }}>
              데이터 기반 재설계 — 새 점수 구조 (합계 100점)
            </div>
            {[
              { label: '낙폭과대/바닥권', pts: 25, pct: 25, color: '#ef4444', desc: '52주 고가 -30~70% 구간 최우선' },
              { label: '펀더멘털 변화',   pts: 25, pct: 25, color: '#3b82f6', desc: '흑자전환 +15점, 매출급증, 수주' },
              { label: '저평가+소형주',   pts: 20, pct: 20, color: '#22c55e', desc: '저PBR + 시총 500억 미만 보너스' },
              { label: '촉매',           pts: 15, pct: 15, color: '#f59e0b', desc: '수주/기술이전/자사주취득' },
              { label: '수급 반전',       pts: 10, pct: 10, color: '#8b5cf6', desc: '저점 유입 감지 (기관 단순 확인 X)' },
              { label: '섹터 모멘텀',     pts: 5,  pct: 5,  color: '#6b7280', desc: '업황 지표 연동' },
            ].map(s => (
              <div key={s.label} style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '6px' }}>
                <span style={{ fontSize: '0.68rem', color: '#94a3b8', width: '100px', flexShrink: 0 }}>{s.label}</span>
                <div style={{ flex: 1, height: '14px', background: 'rgba(255,255,255,0.06)', borderRadius: '4px', overflow: 'hidden' }}>
                  <div style={{ width: `${s.pct}%`, height: '100%', borderRadius: '4px',
                      background: s.color, display: 'flex', alignItems: 'center', paddingLeft: '6px' }}>
                    <span style={{ fontSize: '0.6rem', color: 'white', opacity: 0.9 }}>{s.pts}pt</span>
                  </div>
                </div>
                <span style={{ fontSize: '0.65rem', color: '#64748b', width: '160px', flexShrink: 0 }}>{s.desc}</span>
              </div>
            ))}
          </div>

          {/* 백테스트 결과 */}
          <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)',
              borderRadius: '10px', overflow: 'hidden' }}>
            <div style={{ padding: '0.7rem 1rem', borderBottom: '1px solid rgba(255,255,255,0.07)',
                display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: '0.82rem', fontWeight: 700, color: '#e2e8f0' }}>
                사후검증 백테스트 결과 (6개월 보유, 2022-2025)
              </span>
              <span style={{ fontSize: '0.68rem', color: '#f87171', padding: '0.15rem 0.5rem',
                  borderRadius: '4px', background: 'rgba(239,68,68,0.12)' }}>
                현재 로직 미완성 — 추가 개선 필요
              </span>
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.72rem' }}>
              <thead>
                <tr style={{ background: 'rgba(255,255,255,0.04)' }}>
                  {['기간', '후보수', '6개월 평균수익', '승률', '코스피', '알파'].map(h => (
                    <th key={h} style={{ padding: '0.45rem 0.7rem', textAlign: h === '후보수' ? 'right' : 'left',
                        color: '#64748b', fontWeight: 600 }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {[
                  { p: '2022-01→07', n: 13, ret: -28.0, wr: 8,  kos: -30.4, alpha: -2.4 },
                  { p: '2022-07→2023-01', n: 32, ret: 0.1, wr: 47, kos: -3.5, alpha: 3.6 },
                  { p: '2023-01→07', n: 20, ret: 14.5, wr: 70, kos: 16.9, alpha: -2.4 },
                  { p: '2023-07→2024-01', n: 16, ret: -16.7, wr: 19, kos: 2.6, alpha: -19.3 },
                  { p: '2024-01→07', n: 22, ret: -10.5, wr: 27, kos: 5.0, alpha: -15.5 },
                  { p: '2024-07→2025-01', n: 23, ret: -16.3, wr: 9,  kos: -14.5, alpha: -1.8 },
                ].map((r, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid rgba(255,255,255,0.06)',
                      background: i % 2 === 0 ? 'rgba(255,255,255,0.01)' : 'transparent' }}>
                    <td style={{ padding: '0.45rem 0.7rem', color: '#94a3b8' }}>{r.p}</td>
                    <td style={{ padding: '0.45rem 0.7rem', color: '#e2e8f0', textAlign: 'right' }}>{r.n}</td>
                    <td style={{ padding: '0.45rem 0.7rem', fontWeight: 700,
                        color: r.ret > 0 ? '#34d399' : '#f87171' }}>{r.ret > 0 ? '+' : ''}{r.ret.toFixed(1)}%</td>
                    <td style={{ padding: '0.45rem 0.7rem', color: r.wr >= 50 ? '#34d399' : r.wr >= 35 ? '#fbbf24' : '#f87171' }}>{r.wr}%</td>
                    <td style={{ padding: '0.45rem 0.7rem', color: r.kos >= 0 ? '#94a3b8' : '#94a3b8' }}>{r.kos > 0 ? '+' : ''}{r.kos.toFixed(1)}%</td>
                    <td style={{ padding: '0.45rem 0.7rem', fontWeight: 600,
                        color: r.alpha > 0 ? '#34d399' : '#f87171' }}>{r.alpha > 0 ? '+' : ''}{r.alpha.toFixed(1)}%p</td>
                  </tr>
                ))}
                <tr style={{ background: 'rgba(255,255,255,0.06)', fontWeight: 700 }}>
                  <td style={{ padding: '0.5rem 0.7rem', color: '#e2e8f0' }}>전체 합산</td>
                  <td style={{ padding: '0.5rem 0.7rem', color: '#e2e8f0', textAlign: 'right' }}>126</td>
                  <td style={{ padding: '0.5rem 0.7rem', color: '#f87171' }}>-7.5%</td>
                  <td style={{ padding: '0.5rem 0.7rem', color: '#f87171' }}>32.5%</td>
                  <td style={{ padding: '0.5rem 0.7rem', color: '#94a3b8' }}>—</td>
                  <td style={{ padding: '0.5rem 0.7rem', color: '#f87171' }}>음수</td>
                </tr>
              </tbody>
            </table>
          </div>

          {/* 전후 비교 — 종목 예시 */}
          <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid rgba(255,255,255,0.08)',
              borderRadius: '10px', padding: '1rem' }}>
            <div style={{ fontSize: '0.78rem', fontWeight: 700, color: '#e2e8f0', marginBottom: '0.8rem' }}>
              재설계 전후 — 같은 종목의 점수 변화 (극명한 역전)
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.7rem' }}>
              {[
                { code: 'SK하이닉스 (000660)', desc: '52주 신고가권, 기관 대규모 순매수',
                  old: 62, new_: 4, oldColor: '#fbbf24', newColor: '#64748b',
                  tags: ['신고가권 낙폭점수 0', '이미 상승 완료', '진입 타이밍 지남'],
                  tagColors: ['#f87171', '#94a3b8', '#94a3b8'] },
                { code: '광무 (029480)', desc: '-47% 낙폭, 흑자전환, PBR 0.40',
                  old: 28, new_: 78, oldColor: '#f87171', newColor: '#34d399',
                  tags: ['낙폭과대 20pt', '흑자전환 +15pt', 'PBR 0.40 저평가'],
                  tagColors: ['#34d399', '#34d399', '#34d399'] },
                { code: '엔투텍 (227950)', desc: '-35% 낙폭, 매출 111% 성장, PBR 0.34',
                  old: 45, new_: 78, oldColor: '#fbbf24', newColor: '#34d399',
                  tags: ['낙폭과대 18pt', '매출 111%', 'PBR 0.34'],
                  tagColors: ['#34d399', '#34d399', '#34d399'] },
                { code: 'SGA솔루션즈 (184230)', desc: '-38% 낙폭, 흑자전환, 44% 매출 성장',
                  old: 32, new_: 75, oldColor: '#f87171', newColor: '#34d399',
                  tags: ['낙폭과대 18pt', '흑자전환 +15pt', '44% 매출 성장'],
                  tagColors: ['#34d399', '#34d399', '#34d399'] },
              ].map(ex => (
                <div key={ex.code} style={{ border: '1px solid rgba(255,255,255,0.07)', borderRadius: '8px',
                    padding: '0.7rem 0.8rem', background: 'rgba(255,255,255,0.02)' }}>
                  <div style={{ fontSize: '0.75rem', fontWeight: 700, color: '#f1f5f9', marginBottom: '2px' }}>{ex.code}</div>
                  <div style={{ fontSize: '0.65rem', color: '#64748b', marginBottom: '0.5rem' }}>{ex.desc}</div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.8rem' }}>
                    <div style={{ textAlign: 'center', background: 'rgba(255,255,255,0.04)',
                        borderRadius: '6px', padding: '0.3rem 0.6rem' }}>
                      <div style={{ fontSize: '0.58rem', color: '#64748b' }}>기존</div>
                      <div style={{ fontSize: '1.1rem', fontWeight: 800, color: ex.oldColor }}>{ex.old}pt</div>
                    </div>
                    <span style={{ color: '#64748b', fontSize: '0.8rem' }}>→</span>
                    <div style={{ textAlign: 'center', background: ex.new_ >= 55 ? 'rgba(16,185,129,0.1)' : 'rgba(100,116,139,0.1)',
                        borderRadius: '6px', padding: '0.3rem 0.6rem',
                        border: `1px solid ${ex.new_ >= 55 ? 'rgba(16,185,129,0.3)' : 'rgba(100,116,139,0.2)'}` }}>
                      <div style={{ fontSize: '0.58rem', color: '#64748b' }}>재설계</div>
                      <div style={{ fontSize: '1.1rem', fontWeight: 800, color: ex.newColor }}>{ex.new_}pt</div>
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '3px' }}>
                      {ex.tags.map((t, ti) => (
                        <span key={t} style={{ fontSize: '0.6rem', padding: '1px 6px', borderRadius: '3px',
                            background: `${ex.tagColors[ti]}18`, color: ex.tagColors[ti] }}>{t}</span>
                      ))}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* 다음 개선 방향 */}
          <div style={{ background: 'rgba(99,102,241,0.06)', border: '1px solid rgba(99,102,241,0.2)',
              borderRadius: '10px', padding: '0.8rem 1rem' }}>
            <div style={{ fontSize: '0.75rem', fontWeight: 700, color: '#a5b4fc', marginBottom: '0.5rem' }}>
              백테스트 실패 원인 및 개선 방향
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.5rem', fontSize: '0.68rem', color: '#94a3b8', lineHeight: 1.6 }}>
              <div>
                <div style={{ color: '#f87171', fontWeight: 600, marginBottom: '3px' }}>현재 문제점</div>
                • 낙폭과대 = 더 내려갈 수 있음 (구분 불가)<br/>
                • 매수/매도 로직 미정의 (스코어만 있음)<br/>
                • 6개월 고정 보유 = 타이밍 무시<br/>
                • 손절/청산 기준 없음
              </div>
              <div>
                <div style={{ color: '#34d399', fontWeight: 600, marginBottom: '3px' }}>필요한 개선</div>
                • 거래량 급증 반등 확인 신호 추가<br/>
                • 명확한 매수/매도/손절 로직 정의<br/>
                • 보유 기간 최적화 (6개월 → 조건부)<br/>
                • 추세 반전 확인 후 진입 조건
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ── 탭 2: 패턴 분석 ────────────────────────────────────────── */}
      {activeTab === 'pattern' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div style={{ fontSize: '0.75rem', color: '#94a3b8',
              background: 'rgba(251,191,36,0.06)', borderRadius: '8px',
              padding: '0.6rem 0.8rem', border: '1px solid rgba(251,191,36,0.2)' }}>
            📊 BigQuery 실증 분석 기준일: 2026-06-01 | 분석 대상: 최근 5년(2021-2026) 3배+ 달성 종목 1,324개
          </div>

          {/* 핵심 발견 4개 */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(280px,1fr))', gap: '0.8rem' }}>
            {[
              {
                icon: '📍', title: '출발점: 52주 저점 근처',
                stat: `${PATTERN_FINDINGS.chart.pct_30to70_drawdown}%가 -30~-70% 낙폭 구간에서 출발`,
                detail: `52주 고가 대비 평균 ${PATTERN_FINDINGS.chart.avg_pct_from_high}% 낙폭 상태에서 출발\n→ "낙폭과대 반등" 패턴이 핵심`,
                color: '#34d399', bg: 'rgba(16,185,129,0.08)',
                signal: '저점 신호',
              },
              {
                icon: '🚫', title: '기관 선매수: 없었다',
                stat: `${PATTERN_FINDINGS.supply.inst_sell_pct + PATTERN_FINDINGS.supply.inst_neutral_pct}%가 기관 비매수 상태`,
                detail: `기관 순매도 ${PATTERN_FINDINGS.supply.inst_sell_pct}% + 중립 ${PATTERN_FINDINGS.supply.inst_neutral_pct}%\n→ 개인/테마 주도형, 기관은 나중에 진입`,
                color: '#f87171', bg: 'rgba(239,68,68,0.08)',
                signal: '역발상 신호',
              },
              {
                icon: '📉', title: '재무: 우량주 아님',
                stat: `${PATTERN_FINDINGS.financial.opm_loss_pct}%가 적자 상태`,
                detail: `영업이익률 중앙값 1.3%\n매출 중앙값 809억 (소형~중형)\n→ 재무 개선 기대감이 핵심 트리거`,
                color: '#fbbf24', bg: 'rgba(251,191,36,0.08)',
                signal: '역발상 신호',
              },
              {
                icon: '📋', title: '수주공시 = 너무 늦다',
                stat: `공시 있으면 ${PATTERN_FINDINGS.disclosure.avg_ratio_with}x, 없으면 ${PATTERN_FINDINGS.disclosure.avg_ratio_without}x`,
                detail: '수주공시가 있을 때 오히려 배율 낮음\n→ 공시 발표 시점엔 이미 선반영\n→ 공시 전 수주잔고 증가 감지가 핵심',
                color: '#60a5fa', bg: 'rgba(96,165,250,0.08)',
                signal: '선행 지표 필요',
              },
            ].map(f => (
              <div key={f.title} style={{ background: f.bg, borderRadius: '10px',
                  border: `1px solid ${f.color}33`, padding: '1rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginBottom: '0.4rem' }}>
                  <span style={{ fontSize: '1.2rem' }}>{f.icon}</span>
                  <span style={{ fontSize: '0.82rem', fontWeight: 700, color: '#f1f5f9' }}>{f.title}</span>
                  <span style={{ marginLeft: 'auto', fontSize: '0.62rem', padding: '0.1rem 0.35rem',
                      borderRadius: '4px', background: `${f.color}22`, color: f.color }}>
                    {f.signal}
                  </span>
                </div>
                <div style={{ fontSize: '1rem', fontWeight: 800, color: f.color,
                    marginBottom: '0.5rem' }}>{f.stat}</div>
                <div style={{ fontSize: '0.72rem', color: '#94a3b8', lineHeight: 1.7,
                    whiteSpace: 'pre-line' }}>{f.detail}</div>
              </div>
            ))}
          </div>

          {/* 섹터별 3배 달성 분포 */}
          <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: '10px',
              border: '1px solid rgba(255,255,255,0.08)', overflow: 'hidden' }}>
            <div style={{ padding: '0.7rem 1rem', borderBottom: '1px solid rgba(255,255,255,0.07)',
                fontSize: '0.82rem', fontWeight: 700, color: '#e2e8f0' }}>
              섹터별 3배 달성 분포 (상위)
            </div>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.75rem' }}>
              <thead>
                <tr style={{ background: 'rgba(255,255,255,0.04)' }}>
                  {['섹터', '시장', '종목수', '평균배율', '평균소요(월)', '특징'].map(h => (
                    <th key={h} style={{ padding: '0.5rem 0.7rem', textAlign: h === '종목수' || h === '평균배율' ? 'right' : 'left',
                        color: '#94a3b8', fontWeight: 600, borderBottom: '1px solid rgba(255,255,255,0.07)' }}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {[
                  { sector: 'IT', market: 'KOSDAQ', cnt: 291, ratio: 4.07, months: 9.8, note: '게임/소프트웨어/반도체장비' },
                  { sector: '의료', market: 'KOSDAQ', cnt: 122, ratio: 3.99, months: 9.1, note: '바이오/의료기기' },
                  { sector: '경기소비재', market: 'KOSDAQ', cnt: 79, ratio: 7.8, months: 8.3, note: '최고 배율! 엔터/패션/뷰티' },
                  { sector: '산업재', market: 'KOSDAQ', cnt: 96, ratio: 6.0, months: 8.9, note: '방산/항공우주/로봇' },
                  { sector: '필수소비재', market: 'KOSDAQ', cnt: 41, ratio: 4.85, months: 9.0, note: '식품/음료' },
                  { sector: '산업재', market: 'KOSPI', cnt: 55, ratio: 3.95, months: 10.7, note: '조선/기계/건설' },
                  { sector: '소재', market: 'KOSPI', cnt: 38, ratio: 5.46, months: 10.2, note: '2차전지/화학' },
                  { sector: '경기소비재', market: 'KOSPI', cnt: 40, ratio: 4.89, months: 10.1, note: '자동차 부품' },
                ].map((r, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)',
                      background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.015)' }}>
                    <td style={{ padding: '0.5rem 0.7rem', fontWeight: 600 }}>{r.sector}</td>
                    <td style={{ padding: '0.5rem 0.7rem', color: r.market === 'KOSDAQ' ? '#a78bfa' : '#38bdf8' }}>
                      {r.market}
                    </td>
                    <td style={{ padding: '0.5rem 0.7rem', textAlign: 'right' }}>{r.cnt}</td>
                    <td style={{ padding: '0.5rem 0.7rem', textAlign: 'right',
                        color: r.ratio >= 6 ? '#f87171' : r.ratio >= 5 ? '#fbbf24' : '#34d399',
                        fontWeight: 700 }}>{r.ratio}x</td>
                    <td style={{ padding: '0.5rem 0.7rem', textAlign: 'right', color: '#94a3b8' }}>{r.months}개월</td>
                    <td style={{ padding: '0.5rem 0.7rem', color: '#64748b', fontSize: '0.7rem' }}>{r.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* ── QoQ/TTM 매출 성장 분석 ── */}
          <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: '10px',
              border: '1px solid rgba(255,255,255,0.08)', overflow: 'hidden' }}>
            <div style={{ padding: '0.7rem 1rem', borderBottom: '1px solid rgba(255,255,255,0.07)',
                display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <span style={{ fontSize: '1rem' }}>📈</span>
              <span style={{ fontSize: '0.82rem', fontWeight: 700, color: '#e2e8f0' }}>
                QoQ / TTM 매출·이익 성장 패턴
              </span>
              <span style={{ fontSize: '0.68rem', color: '#94a3b8', marginLeft: 'auto' }}>
                분석일: 2026-06-01
              </span>
            </div>
            <div style={{ padding: '0.8rem 1rem' }}>
              {/* TTM 최강 신호 박스 */}
              <div style={{ background: 'rgba(16,185,129,0.1)', borderRadius: '8px',
                  border: '1px solid rgba(16,185,129,0.3)', padding: '0.7rem 0.9rem',
                  marginBottom: '0.8rem' }}>
                <div style={{ fontSize: '0.75rem', fontWeight: 700, color: '#34d399', marginBottom: '0.4rem' }}>
                  🏆 최강 단일 신호 (BigQuery 검증)
                </div>
                <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
                  <div style={{ textAlign: 'center' }}>
                    <div style={{ fontSize: '1.3rem', fontWeight: 800, color: '#34d399' }}>6.14x</div>
                    <div style={{ fontSize: '0.65rem', color: '#94a3b8' }}>TTM 영업이익<br/>흑자전환 (87건)</div>
                  </div>
                  <div style={{ textAlign: 'center' }}>
                    <div style={{ fontSize: '1.3rem', fontWeight: 800, color: '#60a5fa' }}>6.03x</div>
                    <div style={{ fontSize: '0.65rem', color: '#94a3b8' }}>TTM 매출<br/>+30% 이상 (163건)</div>
                  </div>
                  <div style={{ textAlign: 'center' }}>
                    <div style={{ fontSize: '1.3rem', fontWeight: 800, color: '#f87171' }}>6.10x</div>
                    <div style={{ fontSize: '0.65rem', color: '#94a3b8' }}>재무없는<br/>소형 테마주 (666건)</div>
                  </div>
                  <div style={{ textAlign: 'center', opacity: 0.5 }}>
                    <div style={{ fontSize: '1.3rem', fontWeight: 800, color: '#94a3b8', textDecoration: 'line-through' }}>3.48x</div>
                    <div style={{ fontSize: '0.65rem', color: '#94a3b8' }}>우량 성장주<br/>(이미 반영됨)</div>
                  </div>
                </div>
                <div style={{ fontSize: '0.7rem', color: '#fbbf24', marginTop: '0.5rem', fontWeight: 600 }}>
                  ⚡ 핵심: 시장이 이미 아는 우량주보다 "이제 막 흑자전환하는 기업"이 훨씬 큰 폭등 가능성
                </div>
              </div>

              {/* QoQ 분기별 추세 */}
              <div style={{ marginBottom: '0.8rem' }}>
                <div style={{ fontSize: '0.75rem', fontWeight: 600, color: '#e2e8f0', marginBottom: '0.4rem' }}>
                  서지 전 분기별 QoQ 매출 성장률
                </div>
                <div style={{ display: 'flex', gap: '0.4rem' }}>
                  {GROWTH_FINDINGS.qoq.map(q => (
                    <div key={q.q} style={{ flex: 1, background: 'rgba(255,255,255,0.04)',
                        borderRadius: '6px', padding: '0.5rem', textAlign: 'center',
                        border: '1px solid rgba(255,255,255,0.07)' }}>
                      <div style={{ fontSize: '0.65rem', color: '#94a3b8', marginBottom: '0.2rem' }}>{q.q}</div>
                      <div style={{ fontSize: '0.9rem', fontWeight: 700,
                          color: q.med > 5 ? '#34d399' : '#fbbf24' }}>
                        {q.med > 0 ? '+' : ''}{q.med}%
                      </div>
                      <div style={{ fontSize: '0.6rem', color: '#64748b' }}>중앙값</div>
                      <div style={{ fontSize: '0.68rem', color: '#94a3b8', marginTop: '0.2rem' }}>
                        양수 {q.pos_pct}%
                      </div>
                    </div>
                  ))}
                </div>
                <div style={{ fontSize: '0.68rem', color: '#94a3b8', marginTop: '0.3rem' }}>
                  💡 {GROWTH_FINDINGS.qoq_insight}
                </div>
              </div>

              {/* YoY / 흑자전환 / 거래량 3열 */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: '0.5rem' }}>
                {/* YoY */}
                <div style={{ background: 'rgba(251,191,36,0.07)', borderRadius: '8px',
                    border: '1px solid rgba(251,191,36,0.2)', padding: '0.6rem 0.8rem' }}>
                  <div style={{ fontSize: '0.72rem', fontWeight: 700, color: '#fbbf24', marginBottom: '0.4rem' }}>
                    📊 전년 매출 YoY 분포
                  </div>
                  {[
                    { label: '+50% 이상', val: '15%', pct: 15, ratio: '5.84x', color: '#34d399' },
                    { label: '+20~50%', val: '15%', pct: 15, ratio: '—', color: '#60a5fa' },
                    { label: '0~20%', val: '30%', pct: 30, ratio: '—', color: '#94a3b8' },
                    { label: '역성장', val: '41%', pct: 41, ratio: '4.45x', color: '#f87171' },
                  ].map(r => (
                    <div key={r.label} style={{ display: 'flex', alignItems: 'center',
                        gap: '0.4rem', marginBottom: '0.25rem' }}>
                      <div style={{ width: '70px', fontSize: '0.65rem', color: '#94a3b8' }}>{r.label}</div>
                      <div style={{ flex: 1, height: '6px', borderRadius: '3px',
                          background: 'rgba(255,255,255,0.07)', position: 'relative' }}>
                        <div style={{ width: `${r.pct}%`, height: '100%',
                            background: r.color, borderRadius: '3px', opacity: 0.7 }} />
                      </div>
                      <div style={{ width: '35px', fontSize: '0.65rem', color: '#64748b', textAlign: 'right' }}>{r.val}</div>
                      <div style={{ width: '35px', fontSize: '0.65rem', color: r.color, fontWeight: 600 }}>{r.ratio}</div>
                    </div>
                  ))}
                  <div style={{ fontSize: '0.65rem', color: '#fbbf24', marginTop: '0.3rem' }}>
                    ⚠️ 41%가 역성장 중에도 3배 달성 → 매출 성장 필수 아님
                  </div>
                </div>

                {/* 흑자전환 */}
                <div style={{ background: 'rgba(16,185,129,0.07)', borderRadius: '8px',
                    border: '1px solid rgba(16,185,129,0.2)', padding: '0.6rem 0.8rem' }}>
                  <div style={{ fontSize: '0.72rem', fontWeight: 700, color: '#34d399', marginBottom: '0.4rem' }}>
                    🔄 영업이익 흑자전환 효과
                  </div>
                  <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '0.4rem' }}>
                    <div style={{ flex: 1, background: 'rgba(255,255,255,0.04)',
                        borderRadius: '6px', padding: '0.4rem', textAlign: 'center' }}>
                      <div style={{ fontSize: '0.7rem', color: '#94a3b8' }}>서지 시작 시 흑자</div>
                      <div style={{ fontSize: '0.95rem', fontWeight: 700, color: '#fbbf24' }}>33%</div>
                    </div>
                    <div style={{ flex: 1, background: 'rgba(255,255,255,0.04)',
                        borderRadius: '6px', padding: '0.4rem', textAlign: 'center' }}>
                      <div style={{ fontSize: '0.7rem', color: '#94a3b8' }}>흑자전환 배율</div>
                      <div style={{ fontSize: '0.95rem', fontWeight: 700, color: '#34d399' }}>4.21x</div>
                    </div>
                    <div style={{ flex: 1, background: 'rgba(255,255,255,0.04)',
                        borderRadius: '6px', padding: '0.4rem', textAlign: 'center' }}>
                      <div style={{ fontSize: '0.7rem', color: '#94a3b8' }}>계속 흑자</div>
                      <div style={{ fontSize: '0.95rem', fontWeight: 700, color: '#60a5fa' }}>3.83x</div>
                    </div>
                  </div>
                  <div style={{ fontSize: '0.7rem', fontWeight: 700, color: '#34d399',
                      background: 'rgba(16,185,129,0.1)', borderRadius: '4px',
                      padding: '0.3rem 0.5rem', textAlign: 'center' }}>
                    TTM 영업이익 흑자전환 → 6.14x (최강)
                  </div>
                  <div style={{ fontSize: '0.65rem', color: '#94a3b8', marginTop: '0.3rem' }}>
                    ✓ 영업이익 흑자전환 87건에서 공통으로 나타남
                  </div>
                </div>

                {/* 거래량 */}
                <div style={{ background: 'rgba(99,102,241,0.07)', borderRadius: '8px',
                    border: '1px solid rgba(99,102,241,0.2)', padding: '0.6rem 0.8rem' }}>
                  <div style={{ fontSize: '0.72rem', fontWeight: 700, color: '#a5b4fc', marginBottom: '0.4rem' }}>
                    📊 거래량 패턴
                  </div>
                  {[
                    { label: '거래량 3배+', pct: 5, ratio: '5.32x', color: '#f87171' },
                    { label: '거래량 2배+', pct: 8, ratio: '5.32x', color: '#fbbf24' },
                    { label: '1.5배+', pct: 12, ratio: '—', color: '#60a5fa' },
                    { label: '정체(80%)', pct: 80, ratio: '3.97x', color: '#475569' },
                  ].map(r => (
                    <div key={r.label} style={{ display: 'flex', alignItems: 'center',
                        gap: '0.4rem', marginBottom: '0.2rem' }}>
                      <div style={{ width: '75px', fontSize: '0.65rem', color: '#94a3b8' }}>{r.label}</div>
                      <div style={{ flex: 1, height: '6px', borderRadius: '3px',
                          background: 'rgba(255,255,255,0.07)' }}>
                        <div style={{ width: `${r.pct}%`, height: '100%',
                            background: r.color, borderRadius: '3px', opacity: 0.8 }} />
                      </div>
                      <div style={{ width: '38px', fontSize: '0.65rem',
                          color: r.color, fontWeight: 600 }}>{r.ratio}</div>
                    </div>
                  ))}
                  <div style={{ fontSize: '0.65rem', color: '#94a3b8', marginTop: '0.3rem' }}>
                    💡 80%는 거래량 정체에서 출발 → 선행 조건 아님<br/>
                    단, 거래량 폭발 시 추가 상승폭 증가 (5.32x)
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* ── 복합 패턴 분류 ── */}
          <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: '10px',
              border: '1px solid rgba(255,255,255,0.08)', overflow: 'hidden' }}>
            <div style={{ padding: '0.7rem 1rem', borderBottom: '1px solid rgba(255,255,255,0.07)',
                display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <span style={{ fontSize: '1rem' }}>🎯</span>
              <span style={{ fontSize: '0.82rem', fontWeight: 700, color: '#e2e8f0' }}>
                3배 달성 종목 유형 분류 — 재무 패턴별
              </span>
            </div>
            <div style={{ padding: '0.8rem 1rem' }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                {GROWTH_FINDINGS.patterns.map((p, i) => (
                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '0.8rem',
                      padding: '0.5rem 0.7rem', borderRadius: '7px',
                      background: 'rgba(255,255,255,0.03)',
                      border: `1px solid ${p.color}22` }}>
                    <div style={{ minWidth: '45px', textAlign: 'center',
                        fontSize: '1.1rem', fontWeight: 800, color: p.color }}>
                      {p.ratio}x
                    </div>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: '0.78rem', fontWeight: 600, color: '#e2e8f0' }}>
                        {p.name}
                        <span style={{ marginLeft: '0.4rem', fontSize: '0.65rem',
                            color: '#64748b' }}>({p.cnt}개)</span>
                      </div>
                      <div style={{ fontSize: '0.68rem', color: '#94a3b8', marginTop: '0.15rem' }}>
                        {p.desc}
                      </div>
                    </div>
                    {/* 배율 바 */}
                    <div style={{ width: '80px', height: '6px', borderRadius: '3px',
                        background: 'rgba(255,255,255,0.07)', flexShrink: 0 }}>
                      <div style={{ width: `${Math.min(p.ratio / 7 * 100, 100)}%`,
                          height: '100%', background: p.color,
                          borderRadius: '3px', opacity: 0.8 }} />
                    </div>
                  </div>
                ))}
              </div>
              <div style={{ marginTop: '0.6rem', fontSize: '0.72rem', color: '#fbbf24',
                  background: 'rgba(251,191,36,0.07)', borderRadius: '6px',
                  padding: '0.5rem 0.7rem', border: '1px solid rgba(251,191,36,0.2)' }}>
                💡 <b>핵심 역설</b>: {GROWTH_FINDINGS.patterns_insight}<br/>
                → 스크리너를 "우량 성장주" 기준으로 짜면 실제 고배율 종목을 놓친다.
              </div>
            </div>
          </div>

          {/* ── 스크리너 설계 방향 ── */}
          <div style={{ background: 'rgba(99,102,241,0.08)', borderRadius: '10px',
              border: '1px solid rgba(99,102,241,0.25)', padding: '0.8rem 1rem' }}>
            <div style={{ fontSize: '0.82rem', fontWeight: 700, color: '#a5b4fc', marginBottom: '0.6rem' }}>
              ⚡ 실증 기반 스크리너 v2 설계 방향 (Week 2 구현 예정)
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(200px,1fr))', gap: '0.5rem' }}>
              {[
                { label: '① 차트 조건', items: ['52주 저점 대비 +5~35%', '최근 1달 거래량 증가 시작'], color: '#34d399' },
                { label: '② 재무 조건 (둘 중 하나)', items: ['TTM 영업이익 흑자전환', 'TTM 매출 YoY +30% 이상'], color: '#fbbf24' },
                { label: '③ 규모 필터', items: ['시총 200억 ~ 5,000억', 'KOSPI·KOSDAQ 보통주'], color: '#60a5fa' },
                { label: '④ 악재 필터 (제외)', items: ['최근 1년 CB/BW 발행', '공매도 잔고율 5% 초과'], color: '#f87171' },
              ].map(s => (
                <div key={s.label} style={{ background: 'rgba(255,255,255,0.04)',
                    borderRadius: '7px', padding: '0.5rem 0.7rem',
                    border: `1px solid ${s.color}33` }}>
                  <div style={{ fontSize: '0.72rem', fontWeight: 700,
                      color: s.color, marginBottom: '0.3rem' }}>{s.label}</div>
                  {s.items.map(item => (
                    <div key={item} style={{ fontSize: '0.68rem', color: '#94a3b8',
                        display: 'flex', gap: '0.3rem', marginBottom: '0.15rem' }}>
                      <span style={{ color: s.color }}>›</span> {item}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </div>

          {/* 다음 분석 필요 항목 */}
          <div style={{ background: 'rgba(99,102,241,0.06)', borderRadius: '10px',
              border: '1px solid rgba(99,102,241,0.2)', padding: '0.8rem 1rem' }}>
            <div style={{ fontSize: '0.82rem', fontWeight: 700, color: '#a5b4fc', marginBottom: '0.5rem' }}>
              🔬 다음 분석 예정 (Week 1–2)
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
              {[
                '수주잔고 증가율 vs 서지 시점 상관관계',
                '공매도 잔고 급감 → 서지 선행 검증',
                '텔레그램 언급 급증 → 서지 선행 검증',
                'CB/BW 발행 후 하락 패턴 (악재 필터)',
                '52주 저점 대비 +10~30% 구간 최적 진입점',
                'TTM 흑자전환 + 저점 동시 조건 정밀 분석',
              ].map(t => (
                <span key={t} style={{ fontSize: '0.72rem', padding: '0.2rem 0.6rem',
                    borderRadius: '6px', background: 'rgba(99,102,241,0.12)',
                    border: '1px solid rgba(99,102,241,0.25)', color: '#a5b4fc' }}>
                  {t}
                </span>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* ── 탭 3: 데이터 현황 ──────────────────────────────────────── */}
      {activeTab === 'data' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>
          {/* 범례 */}
          <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', fontSize: '0.72rem' }}>
            {Object.entries(STATUS_STYLE).map(([k, v]) => (
              <span key={k} style={{ display: 'flex', alignItems: 'center', gap: '0.3rem',
                  padding: '0.2rem 0.6rem', borderRadius: '6px',
                  background: v.bg, border: `1px solid ${v.border}`, color: v.color }}>
                {v.label}
              </span>
            ))}
          </div>

          {/* 실시간 통계 */}
          {dataStatus && (
            <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '0.4rem' }}>
              {[
                { key: 'valuation_history', label: 'PBR/PER 히스토리' },
                { key: 'order_backlog', label: '수주잔고' },
                { key: 'cost_structure', label: '원가구조' },
                { key: 'dilution_events', label: 'CB/BW' },
                { key: 'kiwoom_credit_balance', label: '신용잔고' },
                { key: 'dart_insider_holdings', label: '임원매매' },
                { key: 'earnings_signals', label: '실적신호' },
                { key: 'segment_revenue', label: '세그먼트매출' },
              ].map(({ key, label }) => {
                const d = dataStatus[key];
                return d ? (
                  <span key={key} style={{ fontSize: '0.68rem', padding: '0.2rem 0.55rem', borderRadius: '20px',
                      background: d.rows > 0 ? 'rgba(16,185,129,0.12)' : 'rgba(239,68,68,0.1)',
                      border: `1px solid ${d.rows > 0 ? 'rgba(16,185,129,0.3)' : 'rgba(239,68,68,0.25)'}`,
                      color: d.rows > 0 ? '#34d399' : '#f87171' }}>
                    {label}: {d.rows > 0 ? `${d.rows.toLocaleString()}행 / ${d.stocks}종목` : '미수집'}
                  </span>
                ) : null;
              })}
            </div>
          )}

          {DATA_MATRIX_STATIC.map(cat => (
            <div key={cat.category} style={{ background: 'rgba(255,255,255,0.03)',
                borderRadius: '10px', border: '1px solid rgba(255,255,255,0.08)', overflow: 'hidden' }}>
              <div style={{ padding: '0.6rem 1rem', background: 'rgba(255,255,255,0.04)',
                  borderBottom: '1px solid rgba(255,255,255,0.07)',
                  fontSize: '0.8rem', fontWeight: 700, color: '#e2e8f0' }}>
                {cat.category}
                <span style={{ marginLeft: '0.5rem', fontSize: '0.65rem', color: '#94a3b8' }}>
                  ({cat.items.filter(i=>i.status==='ok').length}/{cat.items.length} 수집됨)
                </span>
              </div>
              <div style={{ padding: '0.4rem' }}>
                {cat.items.map(item => {
                  const live = item.statusKey ? dataStatus[item.statusKey] : null;
                  const liveRows = live?.rows || 0;
                  const liveStatus = item.statusKey ? (liveRows > 0 ? 'ok' : 'missing') : item.status;
                  const liveNote = item.statusKey && live
                    ? `${liveRows.toLocaleString()}행${live.stocks ? ` / ${live.stocks.toLocaleString()}종목` : live.markets ? ` / ${live.markets}시장·소스` : ''}${live.max_dt ? ` · 최신 ${live.max_dt}` : ''}`
                    : item.note;
                  const st = STATUS_STYLE[liveStatus] || STATUS_STYLE[item.status];
                  return (
                    <div key={item.name} style={{ display: 'flex', alignItems: 'center',
                        gap: '0.5rem', padding: '0.4rem 0.6rem', borderRadius: '6px',
                        marginBottom: '0.2rem', background: st.bg, border: `1px solid ${st.border}` }}>
                      <span style={{ fontSize: '0.72rem', color: st.color, fontWeight: 600, minWidth: '60px' }}>
                        {st.label}
                      </span>
                      <span style={{ fontSize: '0.78rem', color: '#e2e8f0', flex: 1 }}>{item.name}</span>
                      <span style={{ fontSize: '0.65rem', color: '#64748b', minWidth: '80px' }}>{item.source}</span>
                      <span style={{ fontSize: '0.65rem', color: '#94a3b8' }}>{liveNote}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}

          {/* 추가 수집 우선순위 */}
          <div style={{ background: 'rgba(239,68,68,0.07)', borderRadius: '10px',
              border: '1px solid rgba(239,68,68,0.2)', padding: '0.8rem 1rem' }}>
            <div style={{ fontSize: '0.82rem', fontWeight: 700, color: '#f87171', marginBottom: '0.6rem' }}>
              🚨 추가 수집 우선순위 (투자 판단 핵심 누락 데이터)
            </div>
            {[
              { priority: 'P1', name: '세그먼트별 매출 (사업부문 분해)', why: '사업부문별 성장률 파악 → 어떤 사업이 견인하는지 확인 필수', how: '✅ 9,320건 / 476종목 수집 완료 (segment_revenue)', done: true },
              { priority: 'P1', name: 'PBR/PER 역사적 이력', why: '현재 밸류에이션이 역사적으로 어느 위치인지 확인', how: '✅ 63,451건 / 2,640종목 — valuation_history, 엔진 연동 완료', done: true },
              { priority: 'P1', name: '자사주 매입/소각 이력', why: '경영진 자신감 = 저평가 확인 신호', how: '✅ 5,637건 / 778종목 — treasury_buyback, 텐버거 엔진 연동 완료', done: true },
              { priority: 'P1', name: 'DRAM/반도체 가격 지수', why: '반도체 섹터 사이클 선행지표', how: '✅ 125개월 관세청 HS8542 수출단가 proxy — 퀀트지표 페이지 연동', done: true },
              { priority: 'P2', name: '프로그램 매매(차익/비차익)', why: '기관 프로그램 순매수 급증 = 수급 변화 선행 신호', how: '✅ 시장/종목별 프로그램 매매 수집·연동 완료 (broker_program_* 테이블)', done: true },
              { priority: 'P3', name: '특허/기술이전 공시', why: '원천기술 확보/이전 = 미래 성장 확인 신호', how: 'KIPRIS/DART 공시 파싱' },
            ].map(item => (
              <div key={item.name} style={{ marginBottom: '0.6rem', padding: '0.5rem 0.7rem',
                  borderRadius: '6px',
                  background: item.done ? 'rgba(16,185,129,0.06)' : 'rgba(255,255,255,0.03)',
                  border: item.done ? '1px solid rgba(52,211,153,0.2)' : '1px solid rgba(255,255,255,0.07)' }}>
                <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', marginBottom: '0.25rem' }}>
                  {item.done && <span style={{ fontSize: '0.7rem' }}>✅</span>}
                  <span style={{ fontSize: '0.65rem', padding: '0.1rem 0.35rem', borderRadius: '4px',
                      background: item.priority === 'P1' ? 'rgba(239,68,68,0.2)' : item.priority === 'P2' ? 'rgba(251,191,36,0.2)' : 'rgba(99,102,241,0.2)',
                      color: item.priority === 'P1' ? '#f87171' : item.priority === 'P2' ? '#fbbf24' : '#a5b4fc',
                      fontWeight: 700 }}>
                    {item.priority}
                  </span>
                  <span style={{ fontSize: '0.8rem', fontWeight: 600, color: item.done ? '#34d399' : '#f1f5f9' }}>{item.name}</span>
                </div>
                {item.why && !item.done && (
                  <div style={{ fontSize: '0.72rem', color: '#94a3b8', marginBottom: '0.2rem' }}>
                    <b style={{color:'#fbbf24'}}>왜:</b> {item.why}
                  </div>
                )}
                <div style={{ fontSize: '0.72rem', color: item.done ? '#6ee7b7' : '#64748b' }}>
                  <b style={{color: item.done ? '#34d399' : '#34d399'}}>방법:</b> {item.how}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── 탭 4: 현재 후보 (tenbagger_engine 실제 발굴 결과) ──────────── */}
      {activeTab === 'screen' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>
          {/* 메타 / 컨트롤 */}
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '0.8rem', flexWrap: 'wrap' }}>
            <div style={{ fontSize: '0.75rem', color: '#64748b' }}>
              {candidateMeta?.run_time
                ? <>🕐 최신 발굴: <b style={{color:'#94a3b8'}}>{candidateMeta.run_time.slice(0,16)}</b> · {candidateMeta.count}종목</>
                : '아직 발굴 결과 없음'}
            </div>
            <div style={{ display: 'flex', gap: '0.5rem' }}>
              <button onClick={loadCandidates} disabled={loading}
                  style={{ padding: '0.35rem 0.75rem', borderRadius: '6px', fontSize: '0.72rem', cursor: 'pointer',
                      background: 'rgba(99,102,241,0.12)', border: '1px solid rgba(99,102,241,0.3)', color: '#a5b4fc' }}>
                🔄 새로고침
              </button>
              <button onClick={triggerRun} disabled={runTriggered}
                  style={{ padding: '0.35rem 0.75rem', borderRadius: '6px', fontSize: '0.72rem', cursor: 'pointer',
                      background: runTriggered ? 'rgba(251,191,36,0.1)' : 'rgba(16,185,129,0.12)',
                      border: runTriggered ? '1px solid rgba(251,191,36,0.3)' : '1px solid rgba(16,185,129,0.3)',
                      color: runTriggered ? '#fbbf24' : '#34d399' }}>
                {runTriggered ? '⏳ 발굴 중…' : '🚀 지금 발굴'}
              </button>
            </div>
          </div>

          {/* 6축 스코어 설명 */}
          <div style={{ background: 'rgba(99,102,241,0.06)', borderRadius: '8px',
              border: '1px solid rgba(99,102,241,0.2)', padding: '0.65rem 1rem',
              fontSize: '0.72rem', color: '#94a3b8', display: 'flex', gap: '0.4rem 1.2rem', flexWrap: 'wrap' }}>
            <span>📉 낙폭과대(25)</span><span>🔄 펀더멘털변화(25)</span><span>💎 저평가(20)</span>
            <span>⚡ 촉매(15)</span><span>🏦 수급반전(10)</span><span>🏭 섹터(5)</span>
            <span style={{marginLeft:'auto', color:'#64748b', fontSize:'0.65rem'}}>데이터기반재설계 | 임계값: 55점 이상</span>
          </div>

          {loading ? (
            <div style={{ textAlign: 'center', padding: '2rem', color: '#94a3b8' }}>⏳ 로딩 중...</div>
          ) : candidates.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '2.5rem', color: '#94a3b8' }}>
              <div style={{ fontSize: '2rem', marginBottom: '0.5rem' }}>🔍</div>
              <div>발굴 결과 없음 — "지금 발굴" 버튼을 눌러 실행하세요</div>
            </div>
          ) : (
            <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: '10px',
                border: '1px solid rgba(255,255,255,0.07)', overflow: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.73rem' }}>
                <thead>
                  <tr style={{ background: 'rgba(255,255,255,0.05)' }}>
                    {['#', '종목', '시총', '점수', '매출↑', 'OP↑', 'OP%', 'PBR', 'PER', '선정 이유', 'AI'].map(h => (
                      <th key={h} style={{ padding: '0.5rem 0.55rem', textAlign: 'left', whiteSpace: 'nowrap',
                          color: '#94a3b8', borderBottom: '1px solid rgba(255,255,255,0.08)', fontWeight: 600 }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {candidates.map((s, i) => {
                    const mc = s.market_cap;
                    const mcLabel = mc == null ? '-' : mc >= 10000 ? (mc/10000).toFixed(1)+'조' : mc+'억';
                    const scoreColor = s.total_score >= 75 ? '#34d399' : s.total_score >= 65 ? '#fbbf24' : '#a5b4fc';
                    const revG = s.revenue_growth != null ? `${s.revenue_growth > 0 ? '+' : ''}${s.revenue_growth.toFixed(0)}%` : '-';
                    const opG  = s.op_growth    != null ? `${s.op_growth > 0 ? '+' : ''}${s.op_growth.toFixed(0)}%` : '-';
                    const opM  = s.op_margin    != null ? `${s.op_margin.toFixed(1)}%` : '-';
                    const reasons = (s.reasons || []).slice(0, 3).join(' · ');
                    return (
                      <tr key={s.id || s.stock_code} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)',
                          background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.015)' }}>
                        <td style={{ padding: '0.4rem 0.55rem', color: '#64748b', width: '2rem' }}>{i + 1}</td>
                        <td style={{ padding: '0.4rem 0.55rem', minWidth: '120px' }}>
                          <div style={{ fontWeight: 600, color: '#f1f5f9' }}>{s.stock_name}</div>
                          <div style={{ fontSize: '0.65rem', color: '#64748b' }}>{s.stock_code}</div>
                        </td>
                        <td style={{ padding: '0.4rem 0.55rem', color: '#94a3b8', whiteSpace: 'nowrap' }}>{mcLabel}</td>
                        <td style={{ padding: '0.4rem 0.55rem' }}>
                          <span style={{ background: `${scoreColor}22`, borderRadius: '4px',
                              padding: '0.15rem 0.45rem', color: scoreColor, fontWeight: 700, fontSize: '0.72rem' }}>
                            {s.total_score}
                          </span>
                        </td>
                        <td style={{ padding: '0.4rem 0.55rem',
                            color: s.revenue_growth > 0 ? '#34d399' : s.revenue_growth < 0 ? '#f87171' : '#94a3b8' }}>
                          {revG}
                        </td>
                        <td style={{ padding: '0.4rem 0.55rem',
                            color: s.op_growth > 0 ? '#34d399' : s.op_growth < 0 ? '#f87171' : '#94a3b8' }}>
                          {opG}
                        </td>
                        <td style={{ padding: '0.4rem 0.55rem', color: '#94a3b8' }}>{opM}</td>
                        <td style={{ padding: '0.4rem 0.55rem',
                            color: (s.pbr||99) <= 1 ? '#34d399' : '#94a3b8' }}>
                          {s.pbr?.toFixed(2) ?? '-'}
                        </td>
                        <td style={{ padding: '0.4rem 0.55rem', color: '#94a3b8' }}>
                          {s.per?.toFixed(1) ?? '-'}
                        </td>
                        <td style={{ padding: '0.4rem 0.55rem', color: '#64748b', fontSize: '0.68rem',
                            maxWidth: '200px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {reasons || '-'}
                        </td>
                        <td style={{ padding: '0.4rem 0.55rem' }}>
                          <div style={{ display: 'flex', gap: '0.3rem' }}>
                            <button onClick={() => loadAiAnalysis(s.stock_code, s.stock_name)}
                              style={{ padding: '0.2rem 0.5rem', borderRadius: '4px', border: 'none', cursor: 'pointer',
                                background: 'rgba(99,102,241,0.2)', color: '#a5b4fc', fontSize: '0.7rem' }}>
                              🔮 분석
                            </button>
                            <button onClick={() => loadQuantContext(s.stock_code)}
                              style={{ padding: '0.2rem 0.5rem', borderRadius: '4px', border: 'none', cursor: 'pointer',
                                background: 'rgba(45,212,191,0.15)', color: '#2dd4bf', fontSize: '0.7rem' }}>
                              📊 업황
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* 퀀트 업황 맥락 패널 */}
          {quantCtxCode && (
            <div style={{ background: 'rgba(45,212,191,0.06)', border: '1px solid rgba(45,212,191,0.3)',
                borderRadius: '14px', padding: '1.2rem 1.4rem', marginTop: '0.5rem' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.8rem' }}>
                <div style={{ fontWeight: 700, color: '#2dd4bf', fontSize: '0.95rem' }}>
                  📊 업황 맥락 — {quantCtx?.stock_name || quantCtxCode} <span style={{ fontSize: '0.78rem', color: '#64748b' }}>({quantCtx?.sector || ''})</span>
                </div>
                <button onClick={() => { setQuantCtxCode(null); setQuantCtx(null); }}
                  style={{ background: 'none', border: 'none', color: '#64748b', cursor: 'pointer', fontSize: '1.1rem' }}>✕</button>
              </div>
              {quantCtxLoading ? (
                <div style={{ color: '#94a3b8', fontSize: '0.85rem' }}>업황 데이터 로딩 중...</div>
              ) : quantCtx ? (
                <>
                  {/* 요약 배너 */}
                  <div style={{ display: 'flex', gap: '0.7rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
                    {quantCtx.summary?.rising_indicators?.length > 0 && (
                      <div style={{ background: 'rgba(16,185,129,0.12)', border: '1px solid rgba(16,185,129,0.3)',
                          borderRadius: '8px', padding: '0.5rem 0.8rem', fontSize: '0.8rem' }}>
                        <span style={{ color: '#34d399', fontWeight: 700 }}>↑ 상승 지표: </span>
                        <span style={{ color: '#e2e8f0' }}>{quantCtx.summary.rising_indicators.join(', ')}</span>
                      </div>
                    )}
                    {quantCtx.summary?.falling_indicators?.length > 0 && (
                      <div style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid rgba(239,68,68,0.25)',
                          borderRadius: '8px', padding: '0.5rem 0.8rem', fontSize: '0.8rem' }}>
                        <span style={{ color: '#f87171', fontWeight: 700 }}>↓ 하락 지표: </span>
                        <span style={{ color: '#e2e8f0' }}>{quantCtx.summary.falling_indicators.join(', ')}</span>
                      </div>
                    )}
                    <div style={{ background: quantCtx.summary?.sector_tailwind ? 'rgba(16,185,129,0.15)' : 'rgba(239,68,68,0.1)',
                        border: `1px solid ${quantCtx.summary?.sector_tailwind ? 'rgba(16,185,129,0.4)' : 'rgba(239,68,68,0.3)'}`,
                        borderRadius: '8px', padding: '0.5rem 0.8rem', fontSize: '0.8rem', fontWeight: 700,
                        color: quantCtx.summary?.sector_tailwind ? '#34d399' : '#f87171' }}>
                      {quantCtx.summary?.sector_tailwind ? '🟢 업황 순풍' : '🔴 업황 역풍'}
                    </div>
                  </div>

                  {/* 지표 그리드 */}
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: '0.6rem' }}>
                    {quantCtx.indicators?.map(ind => {
                      const trendColor = ind.trend === '급상승' ? '#34d399' : ind.trend === '상승' ? '#6ee7b7'
                        : ind.trend === '급하락' ? '#f87171' : ind.trend === '하락' ? '#fca5a5' : '#94a3b8';
                      const trendIcon = ind.trend === '급상승' ? '⬆⬆' : ind.trend === '상승' ? '↑'
                        : ind.trend === '급하락' ? '⬇⬇' : ind.trend === '하락' ? '↓' : '→';
                      return (
                        <div key={ind.key} style={{ background: 'var(--surface)', border: '1px solid var(--border)',
                            borderRadius: '10px', padding: '0.7rem 0.9rem' }}>
                          <div style={{ fontSize: '0.72rem', color: '#94a3b8', marginBottom: '0.2rem' }}>{ind.label}</div>
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                            <span style={{ fontWeight: 700, color: '#e2e8f0', fontSize: '0.88rem' }}>
                              {ind.recent_value != null ? ind.recent_value.toLocaleString(undefined, {maximumFractionDigits: 2}) : '-'}
                              <span style={{ fontSize: '0.65rem', color: '#64748b', marginLeft: '0.2rem' }}>{ind.unit}</span>
                            </span>
                            <span style={{ color: trendColor, fontWeight: 700, fontSize: '0.78rem' }}>
                              {trendIcon} {ind.yoy_pct != null ? `${ind.yoy_pct > 0 ? '+' : ''}${ind.yoy_pct}%` : ind.trend}
                            </span>
                          </div>
                          <div style={{ fontSize: '0.65rem', color: '#64748b', marginTop: '0.15rem' }}>
                            {ind.recent_period} {ind.qoq_pct != null ? `• 3개월전比 ${ind.qoq_pct > 0 ? '+' : ''}${ind.qoq_pct}%` : ''}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </>
              ) : (
                <div style={{ color: '#64748b', fontSize: '0.85rem' }}>데이터 없음</div>
              )}
            </div>
          )}
        </div>
      )}

      {/* ── BQ 복합신호 탭 ──────────────────────────────────────────── */}
      {activeTab === 'bq_week2' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div style={{ background: 'linear-gradient(135deg,rgba(99,102,241,0.12),rgba(45,212,191,0.08))',
            borderRadius: '12px', padding: '1rem 1.2rem', border: '1px solid rgba(99,102,241,0.2)' }}>
            <div style={{ fontWeight: 700, fontSize: '1rem', color: '#a5b4fc', marginBottom: '0.3rem' }}>
              🔮 BigQuery 복합신호 (Week2)
            </div>
            <div style={{ fontSize: '0.8rem', color: '#94a3b8', lineHeight: 1.6 }}>
              텐버거 스코어 + 3배주 패턴(triple) + 수급 추세(supply)를 결합한 복합 점수 순위입니다.
              BigQuery 실시간 뷰 기반으로 조회됩니다.
            </div>
          </div>

          {/* 토글 */}
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            {[['stocks', '종목 순위'], ['sectors', '섹터 집계']].map(([k, lbl]) => (
              <button key={k} onClick={() => setBqView(k)}
                style={{ padding: '0.4rem 1rem', borderRadius: '6px', border: 'none', cursor: 'pointer', fontSize: '0.82rem',
                  background: bqView === k ? 'rgba(99,102,241,0.7)' : 'rgba(255,255,255,0.06)',
                  color: bqView === k ? '#fff' : '#94a3b8' }}>
                {lbl}
              </button>
            ))}
            <button onClick={loadBqComposite} disabled={bqLoading}
              style={{ marginLeft: 'auto', padding: '0.4rem 1rem', borderRadius: '6px', border: 'none', cursor: 'pointer',
                background: 'rgba(45,212,191,0.15)', color: '#5eead4', fontSize: '0.82rem' }}>
              {bqLoading ? '로딩중…' : '새로고침'}
            </button>
          </div>

          {bqLoading && (
            <div style={{ textAlign: 'center', padding: '2rem', color: '#64748b' }}>BigQuery 조회 중…</div>
          )}

          {/* 종목 순위 */}
          {!bqLoading && bqView === 'stocks' && (
            <div style={{ overflowX: 'auto' }}>
              {bqComposite.length === 0 ? (
                <div style={{ textAlign: 'center', padding: '2rem', color: '#64748b' }}>
                  데이터 없음 — BQ 연결을 확인하거나 새로고침하세요.
                </div>
              ) : (
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
                  <thead>
                    <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.1)', color: '#64748b' }}>
                      {['#', '종목코드', '종목명', '섹터', '시장', '시총(억)', '텐버거', '3배주', '수급', '복합점수', '신호'].map(h => (
                        <th key={h} style={{ padding: '0.5rem 0.6rem', textAlign: h === '복합점수' || h === '#' ? 'center' : 'left', whiteSpace: 'nowrap' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {bqComposite.map((row, i) => {
                      const cs = row.composite_score ?? 0;
                      const color = cs >= 70 ? '#34d399' : cs >= 50 ? '#fbbf24' : '#94a3b8';
                      return (
                        <tr key={row.stock_code} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)',
                          background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.02)' }}>
                          <td style={{ padding: '0.45rem 0.6rem', textAlign: 'center', color: '#64748b' }}>{i + 1}</td>
                          <td style={{ padding: '0.45rem 0.6rem', color: '#93c5fd', fontFamily: 'monospace' }}>{row.stock_code}</td>
                          <td style={{ padding: '0.45rem 0.6rem', color: '#e2e8f0', fontWeight: 600 }}>{row.stock_name}</td>
                          <td style={{ padding: '0.45rem 0.6rem', color: '#94a3b8', fontSize: '0.78rem' }}>{row.sector_large}</td>
                          <td style={{ padding: '0.45rem 0.6rem', color: '#94a3b8' }}>{row.market}</td>
                          <td style={{ padding: '0.45rem 0.6rem', color: '#cbd5e1', textAlign: 'right' }}>
                            {row.market_cap ? Number(row.market_cap).toLocaleString() : '-'}
                          </td>
                          <td style={{ padding: '0.45rem 0.6rem', textAlign: 'right', color: '#a5b4fc' }}>{row.tenbagger_score ?? '-'}</td>
                          <td style={{ padding: '0.45rem 0.6rem', textAlign: 'right', color: '#86efac' }}>{row.triple_score ?? '-'}</td>
                          <td style={{ padding: '0.45rem 0.6rem', textAlign: 'right' }}>
                            <span style={{ color: (row.supply_net_10d ?? 0) > 0 ? '#34d399' : '#f87171', fontSize: '0.78rem' }}>
                              {row.supply_label ?? '-'}
                            </span>
                          </td>
                          <td style={{ padding: '0.45rem 0.6rem', textAlign: 'center' }}>
                            <span style={{ background: `rgba(${cs >= 70 ? '52,211,153' : cs >= 50 ? '251,191,36' : '148,163,184'},0.15)`,
                              color, borderRadius: '4px', padding: '0.15rem 0.5rem', fontWeight: 700 }}>
                              {cs}
                            </span>
                          </td>
                          <td style={{ padding: '0.45rem 0.6rem', fontSize: '0.75rem', color: '#94a3b8', maxWidth: '200px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {row.reasons ?? ''}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
          )}

          {/* 섹터 집계 */}
          {!bqLoading && bqView === 'sectors' && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(220px,1fr))', gap: '0.75rem' }}>
              {bqSectors.length === 0 ? (
                <div style={{ gridColumn: '1/-1', textAlign: 'center', padding: '2rem', color: '#64748b' }}>
                  섹터 데이터 없음
                </div>
              ) : bqSectors.map(s => {
                const avg = s.avg_composite ?? 0;
                const color = avg >= 60 ? '#34d399' : avg >= 45 ? '#fbbf24' : '#94a3b8';
                return (
                  <div key={s.sector_large} style={{ background: 'rgba(255,255,255,0.03)', borderRadius: '10px',
                    padding: '0.9rem 1rem', border: `1px solid rgba(${avg >= 60 ? '52,211,153' : '255,255,255'},0.1)` }}>
                    <div style={{ fontWeight: 600, color: '#e2e8f0', marginBottom: '0.4rem', fontSize: '0.88rem' }}>{s.sector_large}</div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                      <span style={{ fontSize: '0.78rem', color: '#64748b' }}>{s.stock_count}종목</span>
                      <span style={{ fontWeight: 700, color, fontSize: '1rem' }}>{avg.toFixed(1)}점</span>
                    </div>
                    <div style={{ marginTop: '0.4rem', height: '4px', borderRadius: '2px', background: 'rgba(255,255,255,0.08)' }}>
                      <div style={{ height: '100%', borderRadius: '2px', background: color, width: `${Math.min(100, avg)}%` }} />
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          <div style={{ fontSize: '0.75rem', color: '#475569', textAlign: 'right' }}>
            복합점수 = 텐버거(60%) + 3배주패턴(25%) + 수급신호(15%)
          </div>
        </div>
      )}

      {/* ── 탭 5: 낙폭과대 회복탄력주 ───────────────────────────────── */}
      {activeTab === 'recovery' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>

          {/* 설명 카드 */}
          <div style={{ background: 'rgba(234,179,8,0.08)', border: '1px solid rgba(234,179,8,0.3)',
            borderRadius: '12px', padding: '1rem 1.2rem' }}>
            <div style={{ fontWeight: 700, color: '#fbbf24', marginBottom: '0.5rem', fontSize: '0.9rem' }}>
              📉 낙폭과대 회복탄력주 발굴 — 시장 급락기 역발상 전략
            </div>
            <div style={{ color: '#94a3b8', fontSize: '0.78rem', lineHeight: 1.6 }}>
              실적은 우량한데 시장 전반 하락으로 과도하게 빠진 종목을 5축으로 평가합니다.<br/>
              <span style={{ color: '#fbbf24' }}>하락 강도(10)</span> +{' '}
              <span style={{ color: '#34d399' }}>실적 우량(30)</span> +{' '}
              <span style={{ color: '#60a5fa' }}>저평가(20)</span> +{' '}
              <span style={{ color: '#f472b6' }}>수급 반전(20)</span> +{' '}
              <span style={{ color: '#a78bfa' }}>기술적 지지(20)</span>
              {' '}— 총 100점 만점, 30점 이상 종목 표시
            </div>
          </div>

          {/* 필터 컨트롤 */}
          <div style={{ display: 'flex', gap: '1rem', alignItems: 'center', flexWrap: 'wrap',
            background: 'rgba(255,255,255,0.03)', borderRadius: '10px', padding: '0.8rem 1rem',
            border: '1px solid rgba(255,255,255,0.07)' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <span style={{ color: '#94a3b8', fontSize: '0.8rem' }}>기준 거래일</span>
              {[5, 10, 15, 20].map(d => (
                <button key={d} onClick={() => { setRecovDays(d); loadRecov(d, recovDrop); }}
                  style={{ padding: '3px 10px', borderRadius: '6px', border: '1px solid',
                    borderColor: recovDays === d ? '#6366f1' : 'rgba(255,255,255,0.1)',
                    background: recovDays === d ? 'rgba(99,102,241,0.2)' : 'transparent',
                    color: recovDays === d ? '#a5b4fc' : '#94a3b8',
                    cursor: 'pointer', fontSize: '0.78rem' }}>
                  {d}일
                </button>
              ))}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
              <span style={{ color: '#94a3b8', fontSize: '0.8rem' }}>최소 하락률</span>
              {[5, 8, 12, 18].map(d => (
                <button key={d} onClick={() => { setRecovDrop(d); loadRecov(recovDays, d); }}
                  style={{ padding: '3px 10px', borderRadius: '6px', border: '1px solid',
                    borderColor: recovDrop === d ? '#f59e0b' : 'rgba(255,255,255,0.1)',
                    background: recovDrop === d ? 'rgba(245,158,11,0.2)' : 'transparent',
                    color: recovDrop === d ? '#fbbf24' : '#94a3b8',
                    cursor: 'pointer', fontSize: '0.78rem' }}>
                  {d}%
                </button>
              ))}
            </div>
            <button onClick={() => loadRecov(recovDays, recovDrop)}
              disabled={recovLoading}
              style={{ marginLeft: 'auto', padding: '5px 14px', borderRadius: '8px', cursor: 'pointer',
                background: recovLoading ? 'rgba(100,116,139,0.2)' : 'rgba(99,102,241,0.2)',
                border: '1px solid rgba(99,102,241,0.4)', color: '#a5b4fc', fontSize: '0.8rem' }}>
              {recovLoading ? '분석 중...' : '🔄 재조회'}
            </button>
          </div>

          {/* 메타 정보 */}
          {recovMeta && (
            <div style={{ display: 'flex', gap: '1.5rem', color: '#94a3b8', fontSize: '0.78rem',
              padding: '0.4rem 0.2rem' }}>
              <span>기준: <strong style={{ color: '#e2e8f0' }}>{recovMeta.start_date}</strong> → <strong style={{ color: '#e2e8f0' }}>{recovMeta.end_date}</strong></span>
              <span>발굴: <strong style={{ color: '#fbbf24' }}>{recovMeta.total}종목</strong></span>
              <span>평균 점수: <strong style={{ color: '#a5b4fc' }}>{recovMeta.avg_score}점</strong></span>
            </div>
          )}

          {/* 결과 테이블 */}
          {recovLoading ? (
            <div style={{ textAlign: 'center', color: '#94a3b8', padding: '3rem' }}>분석 중...</div>
          ) : recov.length === 0 ? (
            <div style={{ textAlign: 'center', color: '#94a3b8', padding: '3rem' }}>
              조건에 맞는 종목이 없습니다. 하락률 기준을 낮춰보세요.
            </div>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.72rem' }}>
                <thead>
                  <tr style={{ color: '#94a3b8', borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
                    <th style={{ textAlign: 'left', padding: '6px 8px', whiteSpace: 'nowrap' }}>#</th>
                    <th style={{ textAlign: 'left', padding: '6px 8px', whiteSpace: 'nowrap' }}>종목</th>
                    <th style={{ textAlign: 'right', padding: '6px 8px', whiteSpace: 'nowrap' }}>점수</th>
                    <th style={{ textAlign: 'right', padding: '6px 8px', whiteSpace: 'nowrap', color: '#fca5a5' }}>하락률</th>
                    <th style={{ textAlign: 'right', padding: '6px 8px', whiteSpace: 'nowrap' }}>시총(억)</th>
                    <th style={{ textAlign: 'right', padding: '6px 8px', whiteSpace: 'nowrap', color: '#34d399' }}>매출↑</th>
                    <th style={{ textAlign: 'right', padding: '6px 8px', whiteSpace: 'nowrap', color: '#34d399' }}>OP↑</th>
                    <th style={{ textAlign: 'right', padding: '6px 8px', whiteSpace: 'nowrap' }}>OPM%</th>
                    <th style={{ textAlign: 'right', padding: '6px 8px', whiteSpace: 'nowrap', color: '#60a5fa' }}>PBR</th>
                    <th style={{ textAlign: 'right', padding: '6px 8px', whiteSpace: 'nowrap', color: '#60a5fa' }}>PER</th>
                    <th style={{ textAlign: 'right', padding: '6px 8px', whiteSpace: 'nowrap', color: '#f472b6' }}>기관↑억</th>
                    <th style={{ textAlign: 'right', padding: '6px 8px', whiteSpace: 'nowrap', color: '#f472b6' }}>외국인↑억</th>
                    <th style={{ textAlign: 'right', padding: '6px 8px', whiteSpace: 'nowrap', color: '#a78bfa' }}>저점위%</th>
                    <th style={{ textAlign: 'left', padding: '6px 8px', minWidth: '180px' }}>선정 사유</th>
                  </tr>
                </thead>
                <tbody>
                  {recov.map((r, i) => {
                    const scoreColor = r.score >= 70 ? '#4ade80' : r.score >= 50 ? '#facc15' : '#94a3b8';
                    const dropColor  = r.pct_change <= -20 ? '#f87171' : r.pct_change <= -12 ? '#fca5a5' : '#fcd34d';
                    return (
                      <tr key={r.stock_code} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)',
                        background: i % 2 === 0 ? 'rgba(255,255,255,0.01)' : 'transparent' }}>
                        <td style={{ padding: '5px 8px', color: '#64748b' }}>{i + 1}</td>
                        <td style={{ padding: '5px 8px' }}>
                          <div style={{ fontWeight: 600, color: '#e2e8f0' }}>{r.stock_name}</div>
                          <div style={{ color: '#64748b', fontSize: '0.68rem' }}>{r.stock_code} · {r.market}</div>
                        </td>
                        <td style={{ padding: '5px 8px', textAlign: 'right', fontWeight: 700, color: scoreColor }}>
                          {r.score}
                        </td>
                        <td style={{ padding: '5px 8px', textAlign: 'right', color: dropColor, fontWeight: 600 }}>
                          {r.pct_change?.toFixed(1)}%
                        </td>
                        <td style={{ padding: '5px 8px', textAlign: 'right', color: '#94a3b8' }}>
                          {r.market_cap ? r.market_cap.toLocaleString() : '-'}
                        </td>
                        <td style={{ padding: '5px 8px', textAlign: 'right',
                          color: r.rev_growth > 0 ? '#34d399' : '#94a3b8' }}>
                          {r.rev_growth != null ? `+${r.rev_growth}%` : '-'}
                        </td>
                        <td style={{ padding: '5px 8px', textAlign: 'right',
                          color: r.op_growth > 0 ? '#34d399' : '#f87171' }}>
                          {r.op_growth != null ? `${r.op_growth > 0 ? '+' : ''}${r.op_growth}%` : '-'}
                        </td>
                        <td style={{ padding: '5px 8px', textAlign: 'right',
                          color: (r.opm || 0) >= 10 ? '#4ade80' : (r.opm || 0) >= 5 ? '#94a3b8' : '#f87171' }}>
                          {r.opm != null ? `${r.opm}%` : '-'}
                        </td>
                        <td style={{ padding: '5px 8px', textAlign: 'right',
                          color: (r.pbr || 99) <= 1 ? '#60a5fa' : '#94a3b8' }}>
                          {r.pbr ?? '-'}
                        </td>
                        <td style={{ padding: '5px 8px', textAlign: 'right',
                          color: (r.per || 99) <= 12 ? '#60a5fa' : '#94a3b8' }}>
                          {r.per ?? '-'}
                        </td>
                        <td style={{ padding: '5px 8px', textAlign: 'right',
                          color: (r['inst_億'] || 0) > 0 ? '#f472b6' : '#64748b' }}>
                          {r['inst_億'] != null ? r['inst_億'].toFixed(1) : '-'}
                        </td>
                        <td style={{ padding: '5px 8px', textAlign: 'right',
                          color: (r['frn_億'] || 0) > 0 ? '#f472b6' : '#64748b' }}>
                          {r['frn_億'] != null ? r['frn_億'].toFixed(1) : '-'}
                        </td>
                        <td style={{ padding: '5px 8px', textAlign: 'right',
                          color: (r.pct_above_low || 999) <= 10 ? '#a78bfa' : '#94a3b8' }}>
                          {r.pct_above_low != null ? `+${r.pct_above_low}%` : '-'}
                        </td>
                        <td style={{ padding: '5px 8px', color: '#94a3b8', fontSize: '0.7rem' }}>
                          {(r.reasons || []).join(' · ')}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* 투자 주의 문구 */}
          <div style={{ background: 'rgba(239,68,68,0.06)', border: '1px solid rgba(239,68,68,0.2)',
            borderRadius: '8px', padding: '0.7rem 1rem', color: '#fca5a5', fontSize: '0.73rem', lineHeight: 1.6 }}>
            ⚠️ 본 화면은 투자 참고 자료이며 투자 권유가 아닙니다. 낙폭과대 종목은 추가 하락 위험이 있으므로
            반드시 직접 분석 후 투자 판단하십시오.
          </div>
        </div>
      )}

      {/* ── AI 심층 분석 패널 (슬라이드 오버레이) ─────────────────── */}
      {aiPanel && (
        <div style={{ position: 'fixed', top: 0, right: 0, bottom: 0, width: 'min(640px,100vw)',
          background: 'rgba(15,23,42,0.97)', borderLeft: '1px solid rgba(99,102,241,0.3)',
          zIndex: 1000, display: 'flex', flexDirection: 'column', boxShadow: '-8px 0 32px rgba(0,0,0,0.5)' }}>

          {/* 패널 헤더 */}
          <div style={{ padding: '1rem 1.2rem', borderBottom: '1px solid rgba(255,255,255,0.08)',
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            background: 'linear-gradient(135deg,rgba(99,102,241,0.15),rgba(45,212,191,0.08))' }}>
            <div>
              <div style={{ fontWeight: 700, color: '#a5b4fc', fontSize: '1rem' }}>
                🔮 텐버거 심층 분석
              </div>
              <div style={{ color: '#94a3b8', fontSize: '0.82rem' }}>
                {aiPanel.stock_name} ({aiPanel.stock_code})
              </div>
            </div>
            <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
              <button onClick={() => loadAiAnalysis(aiPanel.stock_code, aiPanel.stock_name, true)}
                disabled={aiLoading}
                style={{ padding: '0.3rem 0.7rem', borderRadius: '5px', border: 'none', cursor: 'pointer',
                  background: 'rgba(45,212,191,0.15)', color: '#5eead4', fontSize: '0.78rem' }}>
                🔄 재분석
              </button>
              <button onClick={() => { setAiPanel(null); setAiResult(null); }}
                style={{ padding: '0.3rem 0.7rem', borderRadius: '5px', border: 'none', cursor: 'pointer',
                  background: 'rgba(239,68,68,0.15)', color: '#fca5a5', fontSize: '0.78rem' }}>
                ✕ 닫기
              </button>
            </div>
          </div>

          {/* 패널 본문 */}
          <div style={{ flex: 1, overflowY: 'auto', padding: '1.2rem' }}>
            {aiLoading ? (
              <div style={{ textAlign: 'center', padding: '3rem', color: '#64748b' }}>
                <div style={{ fontSize: '2rem', marginBottom: '1rem' }}>🤖</div>
                <div>DeepSeek 분석 중…</div>
                <div style={{ fontSize: '0.78rem', color: '#475569', marginTop: '0.5rem' }}>
                  재무/수급/공시 데이터를 종합 분석하는 중입니다 (최대 90초)
                </div>
              </div>
            ) : aiResult?.error ? (
              <div style={{ color: '#f87171', padding: '1rem' }}>오류: {aiResult.error}</div>
            ) : aiResult?.analysis ? (
              <div>
                {aiResult.cached && (
                  <div style={{ background: 'rgba(250,204,21,0.08)', border: '1px solid rgba(250,204,21,0.2)',
                    borderRadius: '6px', padding: '0.4rem 0.8rem', marginBottom: '0.8rem',
                    color: '#fde68a', fontSize: '0.75rem' }}>
                    📦 캐시된 분석 ({aiResult.generated_at?.slice(0,16)})
                    {aiResult.score && ` · 발굴 점수 ${aiResult.score}점`}
                  </div>
                )}
                {/* 마크다운 → 줄바꿈 렌더 (간단 버전) */}
                {aiResult.analysis.split('\n').map((line, idx) => {
                  const isH3 = line.startsWith('### ');
                  const isH2 = line.startsWith('## ');
                  const isBullet = line.startsWith('- ') || line.startsWith('* ');
                  const isHr = line.trim() === '---';
                  if (isHr) return <hr key={idx} style={{ border: 'none', borderTop: '1px solid rgba(255,255,255,0.06)', margin: '0.8rem 0' }} />;
                  if (isH2) return <div key={idx} style={{ fontWeight: 700, color: '#c4b5fd', fontSize: '0.95rem', margin: '1rem 0 0.4rem' }}>{line.slice(3)}</div>;
                  if (isH3) return <div key={idx} style={{ fontWeight: 700, color: '#93c5fd', fontSize: '0.88rem', margin: '0.8rem 0 0.3rem', borderLeft: '3px solid rgba(99,102,241,0.5)', paddingLeft: '0.6rem' }}>{line.slice(4)}</div>;
                  if (isBullet) return <div key={idx} style={{ color: '#cbd5e1', fontSize: '0.82rem', lineHeight: 1.7, paddingLeft: '1rem' }}>• {line.slice(2)}</div>;
                  if (!line.trim()) return <div key={idx} style={{ height: '0.4rem' }} />;
                  return <div key={idx} style={{ color: '#94a3b8', fontSize: '0.82rem', lineHeight: 1.7 }}>{line}</div>;
                })}
              </div>
            ) : null}
          </div>
        </div>
      )}

      {/* ── 스크리너 v2 탭 ───────────────────────────────────────────── */}
      {activeTab === 'screener2' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          {/* 필터 바 */}
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center',
              background: 'rgba(255,255,255,0.03)', borderRadius: '10px', padding: '0.8rem 1rem',
              border: '1px solid rgba(255,255,255,0.08)' }}>
            <span style={{ color: '#94a3b8', fontSize: '0.8rem', fontWeight: 600 }}>필터:</span>
            <input value={sv2Filters.q}
              onChange={e => setSv2Filters(f => ({...f, q: e.target.value}))}
              placeholder="종목명/코드" style={{ padding: '0.3rem 0.6rem', borderRadius: '6px',
                background: 'rgba(255,255,255,0.07)', border: '1px solid rgba(255,255,255,0.15)',
                color: '#f1f5f9', fontSize: '0.82rem', width: '100px' }} />
            <select value={sv2Filters.market}
              onChange={e => setSv2Filters(f => ({...f, market: e.target.value}))}
              style={{ padding: '0.3rem 0.5rem', borderRadius: '6px', background: '#1e293b',
                border: '1px solid rgba(255,255,255,0.15)', color: '#f1f5f9', fontSize: '0.82rem' }}>
              <option value="ALL">전체</option>
              <option value="유가증권">코스피</option>
              <option value="코스닥">코스닥</option>
            </select>
            <select value={sv2Filters.sector}
              onChange={e => setSv2Filters(f => ({...f, sector: e.target.value}))}
              style={{ padding: '0.3rem 0.5rem', borderRadius: '6px', background: '#1e293b',
                border: '1px solid rgba(255,255,255,0.15)', color: '#f1f5f9', fontSize: '0.82rem' }}>
              <option value="ALL">전체 섹터</option>
              {(sv2Meta?.sectors || []).map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            <label style={{ color: '#94a3b8', fontSize: '0.78rem' }}>
              최소점수:
              <input type="number" value={sv2Filters.min_score} min={0} max={100}
                onChange={e => setSv2Filters(f => ({...f, min_score: +e.target.value}))}
                style={{ width: '45px', marginLeft: '0.3rem', padding: '0.2rem 0.4rem',
                  borderRadius: '5px', background: 'rgba(255,255,255,0.07)',
                  border: '1px solid rgba(255,255,255,0.15)', color: '#f1f5f9', fontSize: '0.82rem' }} />
            </label>
            <select value={sv2Filters.sort}
              onChange={e => setSv2Filters(f => ({...f, sort: e.target.value}))}
              style={{ padding: '0.3rem 0.5rem', borderRadius: '6px', background: '#1e293b',
                border: '1px solid rgba(255,255,255,0.15)', color: '#f1f5f9', fontSize: '0.82rem' }}>
              <option value="total_score">점수순</option>
              <option value="market_cap">시총순</option>
              <option value="revenue_growth">매출성장순</option>
              <option value="pbr">PBR순</option>
            </select>
            <button onClick={() => { setSv2Page(1); loadScreenerV2(sv2Filters, 1); }}
              style={{ padding: '0.3rem 0.8rem', borderRadius: '6px', background: 'rgba(99,102,241,0.25)',
                border: '1px solid rgba(99,102,241,0.4)', color: '#a5b4fc', cursor: 'pointer', fontSize: '0.82rem' }}>
              🔍 검색
            </button>
            {sv2Meta && <span style={{ color: '#64748b', fontSize: '0.75rem' }}>총 {sv2Meta.total}종목 | 실행: {sv2Meta.run_time?.slice(0,16)}</span>}
          </div>

          {sv2Loading ? (
            <div style={{ color: '#94a3b8', textAlign: 'center', padding: '2rem' }}>검색 중…</div>
          ) : (
            <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: '12px',
                border: '1px solid rgba(255,255,255,0.08)', overflow: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
                <thead>
                  <tr style={{ background: 'rgba(255,255,255,0.04)', color: '#94a3b8' }}>
                    {['#','종목','점수','시장','섹터','시총(억)','PER','PBR','매출성장','영업성장','OP마진','기관10일','외국인10일'].map(h => (
                      <th key={h} style={{ padding: '0.6rem 0.7rem', textAlign: 'left',
                          fontWeight: 600, fontSize: '0.75rem', whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sv2Results.map((r, i) => {
                    const score = r.total_score;
                    const sc = score >= 70 ? '#4ade80' : score >= 55 ? '#fbbf24' : '#f87171';
                    const offset = (sv2Page - 1) * 30;
                    return (
                      <tr key={r.stock_code} style={{ borderTop: '1px solid rgba(255,255,255,0.05)',
                          background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.015)' }}>
                        <td style={{ padding: '0.5rem 0.7rem', color: '#64748b', fontSize: '0.75rem' }}>{offset+i+1}</td>
                        <td style={{ padding: '0.5rem 0.7rem', color: '#f1f5f9', fontWeight: 600 }}>
                          {r.stock_name}<div style={{ color: '#64748b', fontSize: '0.7rem' }}>{r.stock_code}</div>
                        </td>
                        <td style={{ padding: '0.5rem 0.7rem', color: sc, fontWeight: 700 }}>{score}</td>
                        <td style={{ padding: '0.5rem 0.7rem', color: '#94a3b8', fontSize: '0.75rem' }}>{r.market}</td>
                        <td style={{ padding: '0.5rem 0.7rem', color: '#94a3b8', fontSize: '0.75rem', maxWidth: '80px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.sector_large || '-'}</td>
                        <td style={{ padding: '0.5rem 0.7rem', color: '#cbd5e1' }}>{r.market_cap ? r.market_cap.toLocaleString() : '-'}</td>
                        <td style={{ padding: '0.5rem 0.7rem', color: '#94a3b8' }}>{r.per ? r.per.toFixed(1) : '-'}</td>
                        <td style={{ padding: '0.5rem 0.7rem', color: '#94a3b8' }}>{r.pbr ? r.pbr.toFixed(2) : '-'}</td>
                        <td style={{ padding: '0.5rem 0.7rem', color: (r.revenue_growth||0)>0?'#4ade80':'#f87171' }}>{r.revenue_growth ? `${r.revenue_growth>0?'+':''}${r.revenue_growth.toFixed(0)}%` : '-'}</td>
                        <td style={{ padding: '0.5rem 0.7rem', color: (r.op_growth||0)>0?'#4ade80':'#f87171' }}>{r.op_growth ? `${r.op_growth>0?'+':''}${r.op_growth.toFixed(0)}%` : '-'}</td>
                        <td style={{ padding: '0.5rem 0.7rem', color: '#94a3b8' }}>{r.op_margin ? `${r.op_margin.toFixed(1)}%` : '-'}</td>
                        <td style={{ padding: '0.5rem 0.7rem', color: (r.inst_net_10d||0)>0?'#60a5fa':'#f87171', fontSize: '0.75rem' }}>{r.inst_net_10d ? `${r.inst_net_10d>0?'+':''}${(r.inst_net_10d/1e8).toFixed(0)}억` : '-'}</td>
                        <td style={{ padding: '0.5rem 0.7rem', color: (r.frn_net_10d||0)>0?'#34d399':'#f87171', fontSize: '0.75rem' }}>{r.frn_net_10d ? `${r.frn_net_10d>0?'+':''}${(r.frn_net_10d/1e8).toFixed(0)}억` : '-'}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* 페이지네이션 */}
          {sv2Meta && sv2Meta.total_pages > 1 && (
            <div style={{ display: 'flex', gap: '0.4rem', justifyContent: 'center' }}>
              {Array.from({length: Math.min(sv2Meta.total_pages, 10)}, (_,i) => i+1).map(p => (
                <button key={p} onClick={() => { setSv2Page(p); loadScreenerV2(sv2Filters, p); }}
                  style={{ padding: '0.3rem 0.7rem', borderRadius: '6px', cursor: 'pointer',
                    background: sv2Page === p ? 'rgba(99,102,241,0.35)' : 'rgba(255,255,255,0.05)',
                    border: `1px solid ${sv2Page === p ? 'rgba(99,102,241,0.6)' : 'rgba(255,255,255,0.1)'}`,
                    color: sv2Page === p ? '#a5b4fc' : '#64748b', fontSize: '0.8rem' }}>{p}</button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Screener v3: 업황지표 연동 탭 ────────────────────────────── */}
      {activeTab === 'screener_v3' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>

          {/* 업황지표 연동 배너 */}
          {sv3Meta?.indicator_map && Object.keys(sv3Meta.indicator_map).length > 0 && (
            <div style={{ background: 'rgba(45,212,191,0.07)', border: '1px solid rgba(45,212,191,0.2)',
                borderRadius: '12px', padding: '0.8rem 1.2rem' }}>
              <div style={{ color: '#2dd4bf', fontWeight: 700, fontSize: '0.82rem', marginBottom: '0.5rem' }}>
                🏭 업황지표 연동 현황
              </div>
              <div style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap' }}>
                {Object.entries(sv3Meta.indicator_map).map(([sec, info]) => {
                  const adj = info.adj_score;
                  const color = adj > 0 ? '#4ade80' : adj < 0 ? '#f87171' : '#94a3b8';
                  return (
                    <div key={sec} style={{ background: 'rgba(255,255,255,0.04)', borderRadius: '8px',
                        padding: '0.35rem 0.7rem', fontSize: '0.75rem', border: '1px solid rgba(255,255,255,0.08)' }}>
                      <span style={{ color: '#cbd5e1' }}>{sec}</span>
                      <span style={{ color: '#64748b', margin: '0 0.3rem' }}>·</span>
                      <span style={{ color: '#94a3b8' }}>{info.label}</span>
                      {info.yoy_pct !== null && (
                        <span style={{ color, marginLeft: '0.4rem', fontWeight: 700 }}>
                          {info.yoy_pct > 0 ? '+' : ''}{info.yoy_pct}%
                          <span style={{ color: '#64748b', fontWeight: 400 }}> ({adj > 0 ? '+' : ''}{adj}점)</span>
                        </span>
                      )}
                      {info.yoy_pct === null && <span style={{ color: '#64748b', marginLeft: '0.4rem' }}>데이터없음</span>}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* 필터 바 */}
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center',
              background: 'rgba(255,255,255,0.03)', borderRadius: '10px', padding: '0.8rem 1rem',
              border: '1px solid rgba(255,255,255,0.08)' }}>
            <span style={{ color: '#94a3b8', fontSize: '0.8rem', fontWeight: 600 }}>필터:</span>
            <input value={sv3Filters.q}
              onChange={e => setSv3Filters(f => ({...f, q: e.target.value}))}
              placeholder="종목명/코드" style={{ padding: '0.3rem 0.6rem', borderRadius: '6px',
                background: 'rgba(255,255,255,0.07)', border: '1px solid rgba(255,255,255,0.15)',
                color: '#f1f5f9', fontSize: '0.82rem', width: '100px' }} />
            <select value={sv3Filters.market}
              onChange={e => setSv3Filters(f => ({...f, market: e.target.value}))}
              style={{ padding: '0.3rem 0.5rem', borderRadius: '6px', background: '#1e293b',
                border: '1px solid rgba(255,255,255,0.15)', color: '#f1f5f9', fontSize: '0.82rem' }}>
              <option value="ALL">전체</option>
              <option value="유가증권">코스피</option>
              <option value="코스닥">코스닥</option>
            </select>
            <select value={sv3Filters.sector}
              onChange={e => setSv3Filters(f => ({...f, sector: e.target.value}))}
              style={{ padding: '0.3rem 0.5rem', borderRadius: '6px', background: '#1e293b',
                border: '1px solid rgba(255,255,255,0.15)', color: '#f1f5f9', fontSize: '0.82rem' }}>
              <option value="ALL">전체 섹터</option>
              {(sv3Meta?.sectors || []).map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            <label style={{ color: '#94a3b8', fontSize: '0.78rem' }}>
              v3최소점수:
              <input type="number" value={sv3Filters.min_v3_score} min={0} max={120}
                onChange={e => setSv3Filters(f => ({...f, min_v3_score: +e.target.value}))}
                style={{ width: '45px', marginLeft: '0.3rem', padding: '0.2rem 0.4rem',
                  borderRadius: '5px', background: 'rgba(255,255,255,0.07)',
                  border: '1px solid rgba(255,255,255,0.15)', color: '#f1f5f9', fontSize: '0.82rem' }} />
            </label>
            <button onClick={() => { setSv3Page(1); loadScreenerV3(sv3Filters, 1); }}
              style={{ padding: '0.3rem 0.8rem', borderRadius: '6px', background: 'rgba(45,212,191,0.2)',
                border: '1px solid rgba(45,212,191,0.4)', color: '#2dd4bf', cursor: 'pointer', fontSize: '0.82rem' }}>
              🔍 검색
            </button>
            {sv3Meta && <span style={{ color: '#64748b', fontSize: '0.75rem' }}>총 {sv3Meta.total}종목</span>}
          </div>

          {sv3Loading ? (
            <div style={{ color: '#94a3b8', textAlign: 'center', padding: '2rem' }}>계산 중…</div>
          ) : (
            <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: '12px',
                border: '1px solid rgba(255,255,255,0.08)', overflow: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
                <thead>
                  <tr style={{ background: 'rgba(255,255,255,0.04)', color: '#94a3b8' }}>
                    {['#','종목','v3점수','기본점수','업황조정','FP패널티','수주보너스','섹터','업황지표','YoY%'].map(h => (
                      <th key={h} style={{ padding: '0.6rem 0.7rem', textAlign: 'left',
                          fontWeight: 600, fontSize: '0.75rem', whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sv3Results.map((r, i) => {
                    const v3 = r.v3_score || 0;
                    const sc = v3 >= 70 ? '#4ade80' : v3 >= 55 ? '#fbbf24' : '#f87171';
                    const offset = (sv3Page - 1) * 50;
                    const adjColor = (r.industry_adj || 0) > 0 ? '#4ade80' : (r.industry_adj || 0) < 0 ? '#f87171' : '#64748b';
                    return (
                      <tr key={r.stock_code}
                        onClick={() => { window.__setStockCode?.(r.stock_code); }}
                        style={{ borderTop: '1px solid rgba(255,255,255,0.05)', cursor: 'pointer',
                            background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.015)' }}>
                        <td style={{ padding: '0.5rem 0.7rem', color: '#64748b', fontSize: '0.75rem' }}>{offset+i+1}</td>
                        <td style={{ padding: '0.5rem 0.7rem', color: '#f1f5f9', fontWeight: 600 }}>
                          {r.stock_name}<div style={{ color: '#64748b', fontSize: '0.7rem' }}>{r.stock_code}</div>
                        </td>
                        <td style={{ padding: '0.5rem 0.7rem', color: sc, fontWeight: 700, fontSize: '0.9rem' }}>{v3}</td>
                        <td style={{ padding: '0.5rem 0.7rem', color: '#cbd5e1' }}>{r.total_score || '-'}</td>
                        <td style={{ padding: '0.5rem 0.7rem', color: adjColor, fontWeight: 600 }}>
                          {(r.industry_adj || 0) !== 0 ? `${r.industry_adj > 0 ? '+' : ''}${r.industry_adj}` : '0'}
                        </td>
                        <td style={{ padding: '0.5rem 0.7rem', color: (r.fp_penalty||0) < 0 ? '#f87171' : '#64748b' }}>
                          {r.fp_penalty || 0}
                        </td>
                        <td style={{ padding: '0.5rem 0.7rem', color: (r.backlog_bonus||0) > 0 ? '#4ade80' : '#64748b' }}>
                          {(r.backlog_bonus||0) > 0 ? `+${r.backlog_bonus}` : '0'}
                        </td>
                        <td style={{ padding: '0.5rem 0.7rem', color: '#94a3b8', fontSize: '0.75rem', maxWidth: '80px',
                            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.sector_large || '-'}</td>
                        <td style={{ padding: '0.5rem 0.7rem', color: '#94a3b8', fontSize: '0.75rem', whiteSpace: 'nowrap' }}>
                          {r.industry_label !== '업황데이터없음' ? r.industry_label : '-'}
                        </td>
                        <td style={{ padding: '0.5rem 0.7rem', fontWeight: 600,
                            color: r.industry_yoy_pct === null ? '#64748b' : r.industry_yoy_pct >= 5 ? '#4ade80' : r.industry_yoy_pct <= -10 ? '#f87171' : '#fbbf24' }}>
                          {r.industry_yoy_pct !== null ? `${r.industry_yoy_pct > 0 ? '+' : ''}${r.industry_yoy_pct}%` : '-'}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              {sv3Results.length === 0 && !sv3Loading && (
                <div style={{ color: '#64748b', textAlign: 'center', padding: '2rem' }}>
                  조건에 맞는 종목이 없습니다. min_v3_score를 낮춰보세요.
                </div>
              )}
            </div>
          )}

          {/* 페이지네이션 */}
          {sv3Meta && sv3Meta.total_pages > 1 && (
            <div style={{ display: 'flex', gap: '0.4rem', justifyContent: 'center' }}>
              {Array.from({length: Math.min(sv3Meta.total_pages, 10)}, (_,i) => i+1).map(p => (
                <button key={p} onClick={() => { setSv3Page(p); loadScreenerV3(sv3Filters, p); }}
                  style={{ padding: '0.3rem 0.7rem', borderRadius: '6px', cursor: 'pointer',
                    background: sv3Page === p ? 'rgba(45,212,191,0.25)' : 'rgba(255,255,255,0.05)',
                    border: `1px solid ${sv3Page === p ? 'rgba(45,212,191,0.5)' : 'rgba(255,255,255,0.1)'}`,
                    color: sv3Page === p ? '#2dd4bf' : '#64748b', fontSize: '0.8rem' }}>{p}</button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── FP/FN 오판 역분석 탭 ──────────────────────────────────────── */}
      {activeTab === 'fpfn' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.25)',
              borderRadius: '12px', padding: '1rem 1.2rem' }}>
            <div style={{ fontWeight: 700, color: '#f87171', marginBottom: '0.4rem' }}>
              🔬 FP/FN 오판 역분석 — 텐버거 선정 후 실제 수익률 추적
            </div>
            <div style={{ color: '#94a3b8', fontSize: '0.78rem', lineHeight: 1.6 }}>
              <b style={{color:'#4ade80'}}>TP</b>(선정↑성공) · <b style={{color:'#f87171'}}>FP</b>(선정↑실패) · <b style={{color:'#fbbf24'}}>NEUTRAL</b>(횡보) · <b style={{color:'#64748b'}}>PENDING</b>(아직 N일 미경과)
            </div>
          </div>

          <div style={{ display: 'flex', gap: '0.6rem', alignItems: 'center' }}>
            {[3, 5, 7, 14, 30].map(d => (
              <button key={d} onClick={() => { setFpfnDays(d); loadFpFn(d); }}
                style={{ padding: '0.35rem 0.8rem', borderRadius: '8px', cursor: 'pointer', fontSize: '0.82rem',
                  background: fpfnDays === d ? 'rgba(239,68,68,0.25)' : 'rgba(255,255,255,0.05)',
                  border: `1px solid ${fpfnDays === d ? 'rgba(239,68,68,0.5)' : 'rgba(255,255,255,0.1)'}`,
                  color: fpfnDays === d ? '#fca5a5' : '#64748b' }}>{d}일</button>
            ))}
            <span style={{ color: '#64748b', fontSize: '0.75rem' }}>선정 후 수익률 기준</span>
          </div>

          {fpfnLoading ? (
            <div style={{ color: '#94a3b8', textAlign: 'center', padding: '2rem' }}>분석 중…</div>
          ) : fpfn ? (
            <>
              {/* 요약 KPI */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6,1fr)', gap: '0.6rem' }}>
                {[
                  { label: '전체 후보', val: fpfn.summary.total_candidates, color: '#94a3b8' },
                  { label: '✅ TP (성공)', val: fpfn.summary.tp, color: '#4ade80' },
                  { label: '❌ FP (실패)', val: fpfn.summary.fp, color: '#f87171' },
                  { label: '➡ NEUTRAL', val: fpfn.summary.neutral, color: '#fbbf24' },
                  { label: '⏳ PENDING', val: fpfn.summary.pending, color: '#64748b' },
                  { label: '정밀도', val: fpfn.summary.precision != null ? `${fpfn.summary.precision}%` : '-', color: '#60a5fa' },
                ].map(k => (
                  <div key={k.label} style={{ background: 'rgba(255,255,255,0.04)', borderRadius: '10px',
                      padding: '0.8rem 1rem', border: '1px solid rgba(255,255,255,0.08)', textAlign: 'center' }}>
                    <div style={{ color: k.color, fontSize: '1.4rem', fontWeight: 800 }}>{k.val}</div>
                    <div style={{ color: '#64748b', fontSize: '0.72rem', marginTop: '0.2rem' }}>{k.label}</div>
                  </div>
                ))}
              </div>
              {/* 평균 수익률 배너 */}
              {fpfn.summary.avg_return_pct != null && fpfn.summary.tp > 0 && (
                <div style={{ background: fpfn.summary.avg_return_pct > 0 ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.1)',
                    border: `1px solid ${fpfn.summary.avg_return_pct > 0 ? 'rgba(52,211,153,0.3)' : 'rgba(248,113,113,0.3)'}`,
                    borderRadius: '10px', padding: '0.7rem 1rem', display: 'flex', gap: '2rem', alignItems: 'center' }}>
                  <span style={{ color: '#94a3b8', fontSize: '0.82rem' }}>평가 가능 후보 평균 수익률</span>
                  <span style={{ color: fpfn.summary.avg_return_pct > 0 ? '#4ade80' : '#f87171',
                      fontSize: '1.3rem', fontWeight: 800 }}>
                    {fpfn.summary.avg_return_pct > 0 ? '+' : ''}{fpfn.summary.avg_return_pct?.toFixed(2)}%
                  </span>
                  <span style={{ color: '#64748b', fontSize: '0.75rem' }}>({fpfnDays}일 기준, TP+FP+NEUTRAL 합산)</span>
                </div>
              )}

              {/* FP 요인 분석 */}
              {fpfn.fp_factors && fpfn.fp_factors.length > 0 && (
                <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: '10px',
                    padding: '1rem', border: '1px solid rgba(255,255,255,0.08)' }}>
                  <div style={{ fontWeight: 700, color: '#f87171', marginBottom: '0.6rem', fontSize: '0.85rem' }}>
                    ❌ FP 오판 종목 주요 신호 (틀린 이유 랭킹)
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem' }}>
                    {fpfn.fp_factors.map((f, i) => (
                      <span key={i} style={{ padding: '0.25rem 0.7rem', borderRadius: '20px',
                          background: 'rgba(239,68,68,0.12)', border: '1px solid rgba(239,68,68,0.25)',
                          color: '#fca5a5', fontSize: '0.75rem' }}>
                        {f.reason} ({f.count})
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* 상세 결과 테이블 */}
              <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: '12px',
                  border: '1px solid rgba(255,255,255,0.08)', overflow: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
                  <thead>
                    <tr style={{ background: 'rgba(255,255,255,0.04)', color: '#94a3b8' }}>
                      {['종목', '선정점수', '선정가', `${fpfnDays}일후가`, '수익률', '판정', '주요신호'].map(h => (
                        <th key={h} style={{ padding: '0.6rem 0.8rem', textAlign: 'left',
                            fontWeight: 600, fontSize: '0.75rem', whiteSpace: 'nowrap' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {fpfn.results.slice(0, 60).map((r, i) => {
                      const labelColor = r.label==='TP'?'#4ade80':r.label==='FP'?'#f87171':r.label==='NEUTRAL'?'#fbbf24':'#64748b';
                      const retColor = (r.return_pct||0) > 0 ? '#4ade80' : (r.return_pct||0) < 0 ? '#f87171' : '#94a3b8';
                      return (
                        <tr key={`${r.stock_code}-${i}`} style={{ borderTop: '1px solid rgba(255,255,255,0.05)',
                            background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.015)' }}>
                          <td style={{ padding: '0.5rem 0.8rem', color: '#f1f5f9', fontWeight: 600 }}>
                            {r.stock_name}<div style={{ color: '#64748b', fontSize: '0.7rem' }}>{r.stock_code}</div>
                          </td>
                          <td style={{ padding: '0.5rem 0.8rem', color: '#fbbf24' }}>{r.total_score}점</td>
                          <td style={{ padding: '0.5rem 0.8rem', color: '#94a3b8' }}>{r.price_at_select?.toLocaleString() || '-'}</td>
                          <td style={{ padding: '0.5rem 0.8rem', color: '#94a3b8' }}>{r.price_after?.toLocaleString() || '-'}</td>
                          <td style={{ padding: '0.5rem 0.8rem', color: retColor, fontWeight: 600 }}>
                            {r.return_pct != null ? `${r.return_pct > 0 ? '+' : ''}${r.return_pct.toFixed(1)}%` : '-'}
                          </td>
                          <td style={{ padding: '0.5rem 0.8rem', color: labelColor, fontWeight: 700 }}>{r.label}</td>
                          <td style={{ padding: '0.5rem 0.8rem', maxWidth: '200px' }}>
                            {(r.reasons || []).slice(0, 2).map((rs, ri) => (
                              <div key={ri} style={{ color: '#94a3b8', fontSize: '0.72rem', lineHeight: 1.4 }}>{rs}</div>
                            ))}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <div style={{ color: '#64748b', textAlign: 'center', padding: '2rem' }}>데이터 없음 — 탭 진입 시 자동 로드</div>
          )}
        </div>
      )}

      {/* ── 점수 성능 분석 탭 ─────────────────────────────────────── */}
      {activeTab === 'scoreperf' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div style={{ background: 'rgba(99,102,241,0.08)', border: '1px solid rgba(99,102,241,0.25)',
              borderRadius: '12px', padding: '1rem 1.2rem' }}>
            <div style={{ fontWeight: 700, color: '#a5b4fc', marginBottom: '0.3rem' }}>
              📈 점수 구간별 실제 수익률 분석
            </div>
            <div style={{ color: '#94a3b8', fontSize: '0.78rem', lineHeight: 1.6 }}>
              발굴된 종목을 점수 구간(55~65/65~70/70~75/75~80/80+)과 신호 유형별로 나누어
              실제 N일 후 수익률을 추적합니다. 가중치 최적화의 기초 데이터입니다.
            </div>
          </div>

          {/* 기간 선택 */}
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            {[3, 5, 7, 14, 30].map(d => (
              <button key={d} onClick={() => { setScorePerfDays(d); loadScorePerf(d); }}
                style={{ padding: '0.35rem 0.8rem', borderRadius: '8px', cursor: 'pointer', fontSize: '0.82rem',
                  background: scorePerfDays === d ? 'rgba(99,102,241,0.25)' : 'rgba(255,255,255,0.05)',
                  border: `1px solid ${scorePerfDays === d ? '#6366f1' : 'rgba(255,255,255,0.1)'}`,
                  color: scorePerfDays === d ? '#a5b4fc' : '#64748b' }}>{d}일</button>
            ))}
            <span style={{ color: '#64748b', fontSize: '0.75rem' }}>선정 후 수익률 기준</span>
            <button onClick={() => loadScorePerf(scorePerfDays)}
              style={{ marginLeft: 'auto', padding: '0.35rem 0.9rem', borderRadius: '8px', cursor: 'pointer',
                fontSize: '0.8rem', background: 'rgba(99,102,241,0.15)', border: '1px solid rgba(99,102,241,0.3)',
                color: '#a5b4fc' }}>새로고침</button>
          </div>

          {scorePerfLoading ? (
            <div style={{ color: '#94a3b8', textAlign: 'center', padding: '2rem' }}>분석 중…</div>
          ) : scorePerf ? (
            <>
              {/* 전체 요약 */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: '0.6rem' }}>
                {[
                  { label: '평가 가능 후보', val: scorePerf.evaluated, color: '#94a3b8' },
                  { label: '전체 후보', val: scorePerf.total, color: '#64748b' },
                  { label: '평균 수익률', val: scorePerf.overall_avg != null ? `${scorePerf.overall_avg > 0 ? '+' : ''}${scorePerf.overall_avg.toFixed(2)}%` : '-', color: (scorePerf.overall_avg||0) > 0 ? '#4ade80' : '#f87171' },
                  { label: '기준 일수', val: `${scorePerfDays}일`, color: '#a5b4fc' },
                ].map(k => (
                  <div key={k.label} style={{ background: 'rgba(255,255,255,0.04)', borderRadius: '10px',
                      padding: '0.8rem 1rem', border: '1px solid rgba(255,255,255,0.08)', textAlign: 'center' }}>
                    <div style={{ color: k.color, fontSize: '1.4rem', fontWeight: 800 }}>{k.val}</div>
                    <div style={{ color: '#64748b', fontSize: '0.72rem', marginTop: '0.2rem' }}>{k.label}</div>
                  </div>
                ))}
              </div>

              {/* 구간별 수익률 */}
              <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: '12px',
                  border: '1px solid rgba(255,255,255,0.08)', padding: '1rem' }}>
                <div style={{ fontWeight: 700, color: '#f1f5f9', marginBottom: '0.8rem', fontSize: '0.85rem' }}>
                  📊 점수 구간별 수익률
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                  {Object.entries(scorePerf.bucket_stats || {}).map(([bucket, b]) => {
                    const avg = b.avg_return ?? 0;
                    const barColor = avg > 3 ? '#4ade80' : avg > 0 ? '#86efac' : avg > -3 ? '#fbbf24' : '#f87171';
                    const barWidth = Math.min(Math.abs(avg) * 10, 100);
                    return (
                      <div key={bucket} style={{ display: 'flex', alignItems: 'center', gap: '0.8rem' }}>
                        <span style={{ width: '70px', fontSize: '0.78rem', color: '#94a3b8', flexShrink: 0 }}>{bucket}점</span>
                        <div style={{ flex: 1, background: 'rgba(255,255,255,0.05)', borderRadius: '4px', height: '20px', position: 'relative', overflow: 'hidden' }}>
                          <div style={{ width: `${barWidth}%`, height: '100%', background: barColor, opacity: 0.7, borderRadius: '4px' }} />
                        </div>
                        <span style={{ width: '60px', textAlign: 'right', color: barColor, fontWeight: 700, fontSize: '0.82rem', flexShrink: 0 }}>
                          {avg > 0 ? '+' : ''}{avg.toFixed(2)}%
                        </span>
                        <span style={{ width: '50px', color: '#64748b', fontSize: '0.72rem', flexShrink: 0 }}>{b.count || 0}종목</span>
                      </div>
                    );
                  })}
                </div>
              </div>

              {/* 신호별 수익률 */}
              {scorePerf.signal_stats && Object.keys(scorePerf.signal_stats).length > 0 && (
                <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: '12px',
                    border: '1px solid rgba(255,255,255,0.08)', padding: '1rem' }}>
                  <div style={{ fontWeight: 700, color: '#f1f5f9', marginBottom: '0.8rem', fontSize: '0.85rem' }}>
                    🎯 신호 유형별 수익률 (해당 신호 보유 종목 평균)
                  </div>
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
                      <thead>
                        <tr style={{ color: '#64748b' }}>
                          {['신호', '종목수', '평균 수익률', '최대', '최소'].map(h => (
                            <th key={h} style={{ padding: '0.4rem 0.8rem', textAlign: 'left', fontWeight: 600, fontSize: '0.75rem' }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {Object.entries(scorePerf.signal_stats)
                          .sort((a, b) => (b[1].avg_return || 0) - (a[1].avg_return || 0))
                          .map(([sig, st]) => {
                            const avg = st.avg_return ?? 0;
                            const retColor = avg > 0 ? '#4ade80' : '#f87171';
                            return (
                              <tr key={sig} style={{ borderTop: '1px solid rgba(255,255,255,0.05)' }}>
                                <td style={{ padding: '0.45rem 0.8rem', color: '#f1f5f9', fontWeight: 600 }}>{sig}</td>
                                <td style={{ padding: '0.45rem 0.8rem', color: '#94a3b8' }}>{st.count}</td>
                                <td style={{ padding: '0.45rem 0.8rem', color: retColor, fontWeight: 700 }}>
                                  {avg > 0 ? '+' : ''}{avg.toFixed(2)}%
                                </td>
                                <td style={{ padding: '0.45rem 0.8rem', color: '#4ade80' }}>
                                  {st.max_return != null ? `+${st.max_return.toFixed(1)}%` : '-'}
                                </td>
                                <td style={{ padding: '0.45rem 0.8rem', color: '#f87171' }}>
                                  {st.min_return != null ? `${st.min_return.toFixed(1)}%` : '-'}
                                </td>
                              </tr>
                            );
                          })}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* 인사이트 박스 */}
              <div style={{ background: 'rgba(99,102,241,0.06)', border: '1px solid rgba(99,102,241,0.2)',
                  borderRadius: '10px', padding: '0.8rem 1rem', fontSize: '0.78rem', color: '#94a3b8', lineHeight: 1.7 }}>
                <b style={{ color: '#a5b4fc' }}>📌 가중치 최적화 가이드:</b><br/>
                • 평가 가능 종목이 50개 이상일 때 통계적으로 신뢰 가능합니다.<br/>
                • 고점수 구간(75+)에서 수익률이 높다면 min_score 임계값을 높여 정밀도를 높이세요.<br/>
                • 특정 신호(예: 자사주 취득, PBR 저점)가 일관되게 높은 수익률을 보이면 해당 신호 가중치를 증가시키세요.
              </div>
            </>
          ) : (
            <div style={{ color: '#64748b', textAlign: 'center', padding: '2rem' }}>
              데이터 없음 — 탭 진입 시 자동 로드됩니다
            </div>
          )}
        </div>
      )}

      {/* ── AI 배치 분석 탭 ─────────────────────────────────────── */}
      {activeTab === 'ai_batch' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            <div style={{ background: 'rgba(99,102,241,0.1)', border: '1px solid rgba(99,102,241,0.3)',
                borderRadius: '12px', padding: '1rem 1.2rem' }}>
              <div style={{ fontWeight: 700, color: '#a78bfa', marginBottom: '0.6rem' }}>
                🧠 DeepSeek 배치 심층 분석
              </div>
              <div style={{ color: 'var(--muted)', fontSize: '0.85rem', marginBottom: '1rem' }}>
                텐버거 상위 종목들에 대해 DeepSeek AI가 8섹션 비즈니스 스토리를 분석합니다. 종목당 약 10~20초 소요됩니다.
              </div>
              <div style={{ display: 'flex', gap: '1.5rem', alignItems: 'center', flexWrap: 'wrap', marginBottom: '1rem' }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text)' }}>
                  <span style={{ fontSize: '0.85rem', color: 'var(--muted)' }}>분석 종목 수</span>
                  <select value={batchTopN} onChange={e => setBatchTopN(Number(e.target.value))}
                    style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '6px',
                      padding: '0.3rem 0.6rem', color: 'var(--text)' }}>
                    {[5,10,15,20,30,50].map(n => <option key={n} value={n}>{n}개</option>)}
                  </select>
                </label>
                <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text)' }}>
                  <span style={{ fontSize: '0.85rem', color: 'var(--muted)' }}>최소 점수</span>
                  <input type="number" value={batchMinScore} min={0} max={100}
                    onChange={e => setBatchMinScore(Number(e.target.value))}
                    style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: '6px',
                      padding: '0.3rem 0.6rem', color: 'var(--text)', width: '70px' }} />
                  <span style={{ fontSize: '0.8rem', color: 'var(--muted)' }}>점 이상</span>
                </label>
                <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', color: 'var(--text)', fontSize: '0.85rem' }}>
                  <input type="checkbox" checked={batchForce} onChange={e => setBatchForce(e.target.checked)} />
                  <span>24h 캐시 무시 (재분석)</span>
                </label>
                <button onClick={() => runBatch(batchTopN, batchMinScore, batchForce)}
                  disabled={batchRunning}
                  style={{ padding: '0.5rem 1.2rem', borderRadius: '8px', border: 'none', cursor: batchRunning ? 'not-allowed' : 'pointer',
                    background: batchRunning ? '#4b5563' : '#7c3aed', color: '#fff', fontWeight: 600 }}>
                  {batchRunning ? '⏳ 분석 중...' : '🚀 배치 분석 시작'}
                </button>
              </div>
              {batchStatus && (
                <div style={{ background: batchStatus.status === 'started' ? 'rgba(16,185,129,0.1)' : 'rgba(239,68,68,0.1)',
                    border: `1px solid ${batchStatus.status === 'started' ? 'rgba(16,185,129,0.4)' : 'rgba(239,68,68,0.4)'}`,
                    borderRadius: '8px', padding: '0.7rem 1rem', fontSize: '0.85rem', color: 'var(--text)' }}>
                  {batchStatus.status === 'started'
                    ? `✅ 배치 시작됨 — ${batchStatus.to_analyze || 0}개 종목 분석 대기 (이미 캐시 ${batchStatus.already_cached || 0}개)`
                    : `❌ ${batchStatus.message || '오류 발생'}`}
                </div>
              )}
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div style={{ color: 'var(--muted)', fontSize: '0.85rem' }}>최근 분석 결과 (최대 50개)</div>
              <button onClick={loadAiList} style={{ padding: '0.3rem 0.8rem', borderRadius: '6px', border: '1px solid var(--border)',
                background: 'transparent', color: 'var(--text)', cursor: 'pointer', fontSize: '0.82rem' }}>
                🔄 새로고침
              </button>
            </div>

            {aiListLoading ? (
              <div style={{ textAlign: 'center', color: 'var(--muted)', padding: '2rem' }}>분석 목록 로딩 중...</div>
            ) : aiList.length === 0 ? (
              <div style={{ textAlign: 'center', color: 'var(--muted)', padding: '2rem' }}>
                분석된 종목이 없습니다. 위에서 배치 분석을 시작하세요.
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>
                {aiList.map(item => (
                  <div key={item.stock_code} style={{ background: 'var(--surface)', border: '1px solid var(--border)',
                      borderRadius: '12px', padding: '1rem 1.2rem' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
                      <div style={{ display: 'flex', gap: '0.8rem', alignItems: 'center' }}>
                        <span style={{ fontWeight: 700, color: 'var(--text)' }}>{item.stock_name || item.stock_code}</span>
                        <span style={{ fontSize: '0.8rem', color: 'var(--muted)' }}>{item.stock_code}</span>
                        {item.score != null && (
                          <span style={{ background: item.score >= 70 ? 'rgba(16,185,129,0.2)' : 'rgba(245,158,11,0.2)',
                              color: item.score >= 70 ? '#34d399' : '#fbbf24',
                              borderRadius: '12px', padding: '0.15rem 0.6rem', fontSize: '0.78rem', fontWeight: 700 }}>
                            {item.score}점
                          </span>
                        )}
                      </div>
                      <span style={{ fontSize: '0.75rem', color: 'var(--muted)' }}>
                        {item.generated_at ? new Date(item.generated_at).toLocaleString('ko-KR') : ''}
                      </span>
                    </div>
                    {item.analysis_text && (
                      <div style={{ fontSize: '0.83rem', color: 'var(--muted)', lineHeight: 1.6,
                          maxHeight: '120px', overflow: 'hidden', textOverflow: 'ellipsis',
                          display: '-webkit-box', WebkitLineClamp: 5, WebkitBoxOrient: 'vertical',
                          whiteSpace: 'pre-wrap' }}>
                        {item.analysis_text.substring(0, 300)}{item.analysis_text.length > 300 ? '...' : ''}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
      )}

      {/* ── 포트폴리오 연동 추적 탭 ────────────────────────────────── */}
      {activeTab === 'portfolio' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>

          <div style={{ background: 'rgba(16,185,129,0.08)', border: '1px solid rgba(16,185,129,0.3)',
              borderRadius: '12px', padding: '1rem 1.2rem' }}>
            <div style={{ fontWeight: 700, color: '#34d399', marginBottom: '0.4rem' }}>
              💼 포트폴리오 × 텐버거 연동 추적
            </div>
            <div style={{ color: '#94a3b8', fontSize: '0.78rem', lineHeight: 1.6 }}>
              현재 보유 종목과 텐버거 후보 교집합을 분석합니다.
              텐버거 점수가 높은 종목은 장기 보유 우선 순위, 낮은 종목은 비중 축소 검토.
            </div>
          </div>

          <div style={{ display: 'flex', gap: '0.6rem', flexWrap: 'wrap' }}>
            <button onClick={loadPortfolioTracking}
              style={{ padding: '0.4rem 1rem', borderRadius: '8px', border: '1px solid rgba(16,185,129,0.4)',
                background: 'rgba(16,185,129,0.15)', color: '#34d399', cursor: 'pointer', fontSize: '0.82rem' }}>
              🔄 새로고침
            </button>
          </div>

          {pfLoading ? (
            <div style={{ color: '#94a3b8', textAlign: 'center', padding: '2rem' }}>로딩 중…</div>
          ) : portfolioTracking.length === 0 ? (
            <div style={{ color: '#64748b', textAlign: 'center', padding: '2rem' }}>
              포트폴리오 데이터 없음 (포트폴리오 탭에서 종목 추가 후 확인)
            </div>
          ) : (
            <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: '12px',
                border: '1px solid rgba(255,255,255,0.08)', overflow: 'hidden' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.83rem' }}>
                <thead>
                  <tr style={{ background: 'rgba(255,255,255,0.04)', color: '#94a3b8' }}>
                    {['종목', '수량', '평균가', '현재가', '수익률', '텐버거점수', '주요신호'].map(h => (
                      <th key={h} style={{ padding: '0.7rem 0.8rem', textAlign: 'left',
                          fontWeight: 600, fontSize: '0.78rem', whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {portfolioTracking.map((pf, i) => {
                    const score = pf.tenbagger_score;
                    const scoreColor = score >= 70 ? '#4ade80' : score >= 55 ? '#fbbf24' : score ? '#f87171' : '#475569';
                    const scoreLabel = score >= 70 ? '🚀 텐버거 유력' : score >= 55 ? '⭐ 후보' : score ? '보통' : '미분석';
                    const profitPct = pf.profit_pct ?? pf.profitPct ?? null;
                    const profitColor = profitPct > 0 ? '#4ade80' : profitPct < 0 ? '#f87171' : '#94a3b8';
                    const reasons = (pf.tenbagger_reasons || []).slice(0, 2);
                    return (
                      <tr key={pf.stock_code}
                        style={{ borderTop: '1px solid rgba(255,255,255,0.05)',
                          background: i % 2 === 0 ? 'transparent' : 'rgba(255,255,255,0.015)' }}>
                        <td style={{ padding: '0.6rem 0.8rem', color: '#f1f5f9', fontWeight: 600 }}>
                          {pf.stock_name || pf.stock_code}
                          <div style={{ color: '#64748b', fontSize: '0.72rem' }}>{pf.stock_code}</div>
                        </td>
                        <td style={{ padding: '0.6rem 0.8rem', color: '#cbd5e1' }}>
                          {(pf.quantity || 0).toLocaleString()}주
                        </td>
                        <td style={{ padding: '0.6rem 0.8rem', color: '#94a3b8' }}>
                          {(pf.avg_price || 0).toLocaleString()}원
                        </td>
                        <td style={{ padding: '0.6rem 0.8rem', color: '#94a3b8' }}>
                          {(pf.current_price || 0).toLocaleString()}원
                        </td>
                        <td style={{ padding: '0.6rem 0.8rem', color: profitColor, fontWeight: 600 }}>
                          {profitPct != null ? `${profitPct > 0 ? '+' : ''}${profitPct.toFixed(1)}%` : '-'}
                        </td>
                        <td style={{ padding: '0.6rem 0.8rem' }}>
                          {score != null ? (
                            <span style={{ color: scoreColor, fontWeight: 700 }}>{score}점 <span style={{ fontSize: '0.72rem' }}>{scoreLabel}</span></span>
                          ) : <span style={{ color: '#475569' }}>-</span>}
                        </td>
                        <td style={{ padding: '0.6rem 0.8rem', maxWidth: '200px' }}>
                          {reasons.length > 0 ? (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.1rem' }}>
                              {reasons.map((r, ri) => (
                                <div key={ri} style={{ color: '#94a3b8', fontSize: '0.72rem', lineHeight: 1.4 }}>{r}</div>
                              ))}
                            </div>
                          ) : <span style={{ color: '#475569', fontSize: '0.72rem' }}>-</span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          {/* 텐버거 후보 중 미보유 종목 안내 */}
          {portfolioTracking.length > 0 && (() => {
            const pfCodes = new Set(portfolio.map(p => p.stock_code));
            const missed = portfolioTracking.filter(p => !pfCodes.has(p.stock_code) && p.tenbagger_score >= 65);
            if (missed.length === 0) return null;
            return (
              <div style={{ background: 'rgba(99,102,241,0.08)', border: '1px solid rgba(99,102,241,0.25)',
                  borderRadius: '12px', padding: '1rem 1.2rem' }}>
                <div style={{ fontWeight: 700, color: '#a5b4fc', marginBottom: '0.5rem', fontSize: '0.85rem' }}>
                  💡 미보유 고점수 텐버거 후보 ({missed.length}종목)
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem' }}>
                  {missed.slice(0, 10).map(c => (
                    <span key={c.stock_code} style={{ padding: '0.2rem 0.7rem', borderRadius: '20px',
                        background: 'rgba(99,102,241,0.2)', color: '#c4b5fd', fontSize: '0.78rem' }}>
                      {c.stock_name || c.stock_code} {c.tenbagger_score}점
                    </span>
                  ))}
                </div>
              </div>
            );
          })()}
        </div>
      )}

      {/* ── 탭: 시그널 영향성 리포트 ─────────────────────────── */}
      {activeTab === 'signal_impact' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <SignalImpactView />
        </div>
      )}

      {/* 배경 클릭 시 패널 닫기 */}
      {aiPanel && (
        <div onClick={() => { setAiPanel(null); setAiResult(null); }}
          style={{ position: 'fixed', inset: 0, zIndex: 999 }} />
      )}
    </div>
  );
}
