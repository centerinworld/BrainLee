/**
 * StrategyHub.jsx  (nav label: "전략 센터")
 * 전략센터 — AI 종목 발굴 + 백테스트 통합 뷰. App.jsx에서 분리 (2026-09-03, 토큰 최적화)
 * 원본은 App.jsx에 인라인으로 정의되어 있던 컴포넌트를 그대로 이관. 로직/JSX 변경 없음.
 * ExperimentLedgerPanel("🧪 검증 이력" 탭)은 이 파일에서만 쓰이므로 별도 파일로 분리하지 않고
 * 그대로 함께 옮겼다.
 */
import React from 'react';
import { API } from '../utils';
import BacktestView from './BacktestView';
import Screener from './Screener';

const STRATEGY_HUB_STRATEGIES = [
  { key:'v_trend', label:'V1 MA추세', screenTab:'trend', comboLogic:null, color:'#f59e0b' },
  { key:'v1_value', label:'V2 가치매수', screenTab:'value', comboLogic:null, color:'#34d399' },
  { key:'v2', label:'V3 재무우량', screenTab:'ai', comboLogic:null, color:'#60a5fa' },
  { key:'v5', label:'V4 수급모멘텀', screenTab:'combo', comboLogic:'v2', color:'#a78bfa' },
  { key:'v4', label:'V5 복합콤보', screenTab:'combo', comboLogic:'v1', color:'#8b5cf6' },
  { key:'v10', label:'V6 이익폭발', screenTab:'ai', comboLogic:null, color:'#fbbf24' },
  { key:'v11', label:'V7 이익가속', screenTab:'trigger', comboLogic:null, color:'#10b981' },
  { key:'vbr', label:'V8 52W돌파', screenTab:'trend', comboLogic:null, color:'#fb923c' },
  { key:'v8', label:'V9 수출선행', screenTab:'trend', comboLogic:null, color:'#94a3b8' },
  { key:'v12', label:'V10 섹터대세', screenTab:'combo', comboLogic:'v1', color:'#64748b' },
  { key:'regime_adaptive', label:'Meta-V 레짐 적응형', screenTab:'combo', comboLogic:'v1', color:'#818cf8' },
  { key:'composite', label:'V11 복합스코어링', screenTab:'combo', comboLogic:'v1', color:'#22d3ee' },
  { key:'golden_cross', label:'V12 골든크로스', screenTab:'trend', comboLogic:null, color:'#f97316' },
  { key:'high_profit_compound', label:'V13 고수익집중', screenTab:'high_profit', comboLogic:null, color:'#22c55e' },
  { key:'sector_focus', label:'V-SECTOR 주도섹터', screenTab:'combo', comboLogic:null, color:'#c084fc' },
  { key:'recovery', label:'V-RECOVERY 낙폭반등', screenTab:'trend', comboLogic:null, color:'#fb7185' },
  { key:'deep_recovery', label:'V-DEEP 깊은낙폭집중', screenTab:'trend', comboLogic:null, color:'#e879f9' },
  { key:'low_base_breakout', label:'V-LOWBASE 저점기반돌파', screenTab:'trend', comboLogic:null, color:'#38bdf8' },
  { key:'turnaround', label:'V-TURNAROUND 흑자전환', screenTab:'trend', comboLogic:null, color:'#a78bfa' },
  { key:'extreme_dd_volume', label:'V-EXTREME 초낙폭거래량', screenTab:'trend', comboLogic:null, color:'#f43f5e' },
  { key:'se_momentum', label:'V-SE 주도섹터(스탁이지)', screenTab:'combo', comboLogic:null, color:'#4ade80' },
  { key:'megatrend', label:'V-MEGATREND 구조테마추종', screenTab:'trend', comboLogic:null, color:'#f472b6' },
  { key:'earnings_conviction', label:'V-EARNINGS 실적가속집중배분', screenTab:'combo', comboLogic:null, color:'#facc15' },
  { key:'moonshot_turnaround', label:'V-MOONSHOT 턴어라운드발굴', screenTab:'combo', comboLogic:null, color:'#c026d3' },
  { key:'contract_momentum', label:'V-CONTRACT 해외수주', screenTab:'combo', comboLogic:null, color:'#2dd4bf' },
];
const STRATEGY_HUB_PERIOD_LABELS = ['20.3~21.11\n상승장','21.12~22.10\n하락장','22.11~23.10\n회복장','23.11~24.12\nAI랠리','24.6~25.5\n최근','25.6~26.3\n최신'];
const STRATEGY_HUB_PERIOD_COLORS = ['#f87171','#60a5fa','#fbbf24','#ef4444','#94a3b8','#22d3ee'];
// 2026-07-17 실측: 2020-03-01~2026-03-31 단일계좌 연속운용(독립구간 복리곱 아님, 진짜 연속 백테스트).
// 검증 상태: 스냅샷 격리 실행, run_registry 미등록(참고용) — 정식 selected run 등록 전까지 '실측 참고'로 표기.
// 2026-07-18 전수 실측 완료: 기존 6전략 + 미실측이던 13전략 + 신규 2전략(격리 스냅샷, 동일 조건).
// v2는 chart_confluence=True 채택 후 값(170.3, 구 158.5). v4(V5복합콤보)가 전체 1위.
// 2026-07-27/28: v4/megatrend/earnings_conviction/moonshot_turnaround as-of 시총 리트로핏 후
// 연속운용 재실측(security_master_history 기반, 룩어헤드 제거) — v4/megatrend는 기존값 대비 하락,
// earnings_conviction/moonshot_turnaround는 신규 실측치 반영(이전엔 이 표에 없었음).
const STRATEGY_HUB_CONTINUOUS_RETURNS = {
  v4:           { ret: 139.01, mdd: -18.41, win: 37.6, trades: 1141 },
  earnings_conviction: { ret: 95.72, mdd: null, win: 45.45, trades: 88 },
  moonshot_turnaround: { ret: 76.1, mdd: null, win: 40.55, trades: 217 },
  sector_focus: { ret: 245.02, mdd: null, win: 46.7, trades: 165 },
  golden_cross: { ret: 267.34, mdd: null, win: 29.7, trades: 401 },
  v10:          { ret: 212.1, mdd: -32.88, win: 33.5, trades: 1572 },
  v5:           { ret: 186.9, mdd: -17.66, win: 38.2, trades: 597 },
  v2:           { ret: 170.27, mdd: -12.83, win: 41.9, trades: 599 },
  v11:          { ret: 120.11, mdd: -23.17, win: 35.8, trades: 475 },
  v8:           { ret: 106.38, mdd: -23.24, win: 42.5, trades: 275 },
  v_trend:      { ret: 102.89, mdd: -30.87, win: 36.0, trades: 597 },
  high_profit_compound: { ret: 100.94, mdd: null, win: null, trades: 109 },
  v1_value:     { ret: 86.9,  mdd: -23.9, win: 35.9, trades: 729 },
  recovery:     { ret: 79.9,  mdd: null, win: 33.9, trades: 292 },
  vbr:          { ret: 78.0,  mdd: -45.84, win: 28.3, trades: 491 },
  turnaround:   { ret: 62.05, mdd: null, win: 31.2, trades: 170 },
  se_momentum:  { ret: 75.59, mdd: null, win: 35.4, trades: 1470 },
  regime_adaptive: { ret: 53.22, mdd: -59.44, win: 31.1, trades: 819 },
  v12:          { ret: 33.82, mdd: -44.04, win: 29.5, trades: 543 },
  composite:    { ret: 30.86, mdd: null, win: 28.9, trades: 602 },
  deep_recovery:{ ret: 28.8,  mdd: null, win: 32.1, trades: 287 },
  low_base_breakout: { ret: 28.39, mdd: null, win: 37.5, trades: 224 },
  extreme_dd_volume: { ret: 20.6, mdd: null, win: 43.1, trades: 353 },
  megatrend:    { ret: 42.27, mdd: null, win: 39.61, trades: 154 },
};


// ── StrategyHub (module-level: App 재렌더와 무관하게 안정적인 컴포넌트 identity 유지) ──
  // 모듈 레벨 캐시: StrategyHub가 예기치 않게 반복 마운트/언마운트되어도(예: 부모 재렌더에 의한
  // remount storm) fetch가 매번 취소되어 데이터가 영원히 null로 굳는 문제를 방지한다.
  // 2026-07-17: 성과 매트릭스가 계속 비어 보이는 버그의 근본 원인 — 캐시로 재요청 없이 즉시 표시.
  let _strategyHubMatrixCache = null;
  const StrategyHub = ({ changeStock, changeTab }) => {
    const [hubTab, setHubTab]             = React.useState('matrix');
    const [selectedStrat, setSelectedStrat] = React.useState('high_profit_compound');
    const [stratSort, setStratSort] = React.useState({ key: null, dir: 'desc' });  // 매트릭스 정렬 (avg|cum)
    const [marketRegime, setMarketRegime] = React.useState(null);
    const [strategyResearch, setStrategyResearch] = React.useState(null);
    const [strategyDataLab, setStrategyDataLab] = React.useState(null);
    const [backtestMatrix, setBacktestMatrix] = React.useState(() => _strategyHubMatrixCache);
    const [backtestMatrixError, setBacktestMatrixError] = React.useState('');
    // 병합계좌 실측 run 목록 (2026-07-18 — 가중평균 목업 대체)
    const [comboRuns, setComboRuns] = React.useState([]);
    React.useEffect(() => {
      fetch(API('/api/backtest/combinations/list'))
        .then(r => r.ok ? r.json() : null)
        .then(d => setComboRuns(Array.isArray(d?.combinations) ? d.combinations : []))
        .catch(() => setComboRuns([]));
    }, []);
    // 연속운용 실측 결과 (2026-08-30 — 사용자 지시 "백테스트가 돌고나면 자동으로
    // 프론트엔드가 수정되도록해": 하드코딩 STRATEGY_HUB_CONTINUOUS_RETURNS를 폴백으로
    // 두고, /api/backtest/continuous-returns가 반환하는 최신 실측값으로 덮어씀 — 이제
    // 정기 재검증(전략센터주간재검증, 매주 일요일 01:30)이나 수동 실험이 새 연속운용
    // run을 저장하면 App.jsx를 손으로 고치지 않아도 다음 새로고침에 자동 반영된다.
    const [contReturns, setContReturns] = React.useState(STRATEGY_HUB_CONTINUOUS_RETURNS);
    React.useEffect(() => {
      fetch(API('/api/backtest/continuous-returns'))
        .then(r => r.ok ? r.json() : null)
        .then(d => {
          if (d && d.strategies) {
            setContReturns(prev => ({ ...STRATEGY_HUB_CONTINUOUS_RETURNS, ...d.strategies }));
          }
        })
        .catch(() => {});
    }, []);

    React.useEffect(() => {
      fetch(API('/api/market-regime'))
        .then(r => r.json())
        .then(d => setMarketRegime(d))
        .catch(() => {});
    }, []);

    React.useEffect(() => {
      fetch(API('/api/backtest/strategy-research/summary'))
        .then(r => r.json())
        .then(d => setStrategyResearch(d))
        .catch(() => {});
    }, []);

    React.useEffect(() => {
      fetch(API('/api/strategy-data-lab/overview'))
        .then(r => r.ok ? r.json() : null)
        .then(d => setStrategyDataLab(d))
        .catch(() => setStrategyDataLab(null));
    }, []);

    React.useEffect(() => {
      // 캐시가 있으면(예: remount) 재요청 없이 즉시 사용 — 아래에서 백그라운드로 최신화만 시도.
      // 백테스트 동시 실행(다른 세션/스크립트)으로 인한 DB 일시 잠금·빈 응답 대비 재시도.
      // strategies가 비어 있으면 응답이 완전하지 않은 것으로 보고 짧은 지연 후 재조회한다.
      const load = (attempt = 0) => {
        fetch(API('/api/backtest/matrix'))
          .then(r => {
            if (!r.ok) throw new Error(`HTTP ${r.status}`);
            return r.json();
          })
          .then(d => {
            if ((!d?.strategies || d.strategies.length === 0) && attempt < 3) {
              setTimeout(() => load(attempt + 1), 1500 * (attempt + 1));
              return;
            }
            _strategyHubMatrixCache = d;
            setBacktestMatrix(d);
            setBacktestMatrixError('');
          })
          .catch(e => {
            if (attempt < 3) {
              setTimeout(() => load(attempt + 1), 1500 * (attempt + 1));
              return;
            }
            if (!_strategyHubMatrixCache) {
              setBacktestMatrix(null);
              setBacktestMatrixError(e.message || '백테스트 결과를 불러오지 못했습니다.');
            }
          });
      };
      load();
    }, []);

    // STRATEGY_HUB_STRATEGIES/PERIOD_LABELS/PERIOD_COLORS/CONTINUOUS_RETURNS는
    // module-level로 이동됨 (2026-07-17, 매 렌더 재생성 방지)
    // 화면 성과는 선택 registry의 run_hash가 지정된 API 매트릭스만 사용한다.
    const matrixPeriodOrder = backtestMatrix?.period_order || [];
    const PERIOD_RETURNS = Object.fromEntries(
      (backtestMatrix?.strategies || []).map(item => [
        item.strategy,
        matrixPeriodOrder.map(period => item.periods?.[period]?.total_return_pct ?? null),
      ]),
    );
    const matrixResults = (backtestMatrix?.strategies || []).flatMap(item => Object.values(item.periods || {}));
    const matrixVerified = matrixResults.length > 0 && matrixResults.every(item =>
      item.methodology_status === 'verified' && item.methodology?.run_hash
    );
    const strategyMethodology = Object.fromEntries(
      (backtestMatrix?.strategies || []).map(item => {
        const results = Object.values(item.periods || {});
        const verified = results.length > 0 && results.every(result =>
          result.methodology_status === 'verified' && result.methodology?.run_hash
        );
        const statuses = [...new Set(results.map(result => result.verification_status || 'legacy'))];
        const status = statuses.length === 1 ? statuses[0] : 'mixed';
        const labels = {
          forward_validated:'전방 검증', point_in_time_verified:'PIT 검증',
          point_in_time_approx:'PIT 근사', execution_strict:'체결 검증',
          legacy:'레거시', mixed:'혼합 차단',
        };
        return [item.strategy, { verified, results, status, label: labels[status] || '미검증' }];
      }),
    );
    const strategyGovernance = Object.fromEntries(
      (backtestMatrix?.strategies || []).map(item => [item.strategy, item.governance || {}]),
    );
    const visibleTiers = new Set(['live_eligible', 'paper_core', 'offensive_satellite']);
    const availableStrategies = STRATEGY_HUB_STRATEGIES.filter(strategy =>
      (strategyMethodology[strategy.key]?.results || []).length > 0
      && visibleTiers.has(strategyGovernance[strategy.key]?.tier)
    );
    React.useEffect(() => {
      if (availableStrategies.length > 0 && !availableStrategies.some(strategy => strategy.key === selectedStrat)) {
        setSelectedStrat(availableStrategies[0].key);
      }
    }, [backtestMatrix, selectedStrat]);
    const strategySummary = (strategy) => {
      const values = (PERIOD_RETURNS[strategy.key] || []).filter(v => v != null);
      if (!values.length) return 'API 백테스트 결과 없음';
      const best = Math.max(...values);
      const worst = Math.min(...values);
      const avg = values.reduce((sum, value) => sum + value, 0) / values.length;
      return `API 기준 평균 ${avg >= 0 ? '+' : ''}${avg.toFixed(1)}% · 최고 ${best >= 0 ? '+' : ''}${best.toFixed(1)}% · 최저 ${worst >= 0 ? '+' : ''}${worst.toFixed(1)}%`;
    };
    const sel = availableStrategies.find(s => s.key === selectedStrat) || availableStrategies[0] || STRATEGY_HUB_STRATEGIES[4];
    const clrRet = v => v > 0 ? '#f87171' : v < 0 ? '#60a5fa' : 'rgba(255,255,255,0.4)';
    const fmtRet = v => v === 0.0 ? '0%' : (v > 0 ? '+' : '') + v.toFixed(1) + '%';

    const regime = marketRegime?.regime || null;
    const currentMlTop = Array.isArray(strategyResearch?.current_rankings?.ml_top20)
      ? strategyResearch.current_rankings.ml_top20.slice(0, 5)
      : [];
    const currentHeuristicTop = Array.isArray(strategyResearch?.current_rankings?.heuristic_top20)
      ? strategyResearch.current_rankings.heuristic_top20.slice(0, 5)
      : [];
    const currentStrategyRanks = Array.isArray(strategyResearch?.strategy_rankings?.top)
      ? strategyResearch.strategy_rankings.top.slice(0, 5)
      : [];
    const researchEval = strategyResearch?.evaluation || {};
    const qualityValidationRows = Array.isArray(strategyResearch?.quality_factor_validation?.rows)
      ? strategyResearch.quality_factor_validation.rows
      : [];
    const qualityRankingRows = Array.isArray(strategyResearch?.quality_factor_validation?.ranking_rows)
      ? strategyResearch.quality_factor_validation.ranking_rows
      : [];
    const qualityOverlaySweepRuns = Array.isArray(strategyResearch?.quality_overlay_sweep?.runs)
      ? strategyResearch.quality_overlay_sweep.runs
      : [];
    const qualityOverlayBtSummaries = Array.isArray(strategyResearch?.quality_overlay_monthly_backtest?.summaries)
      ? strategyResearch.quality_overlay_monthly_backtest.summaries
      : [];
    const qualityOverlayTop10 = Array.isArray(strategyResearch?.current_rankings?.quality_overlay_top10)
      ? strategyResearch.current_rankings.quality_overlay_top10.slice(0, 5)
      : [];
    const qualityMetric = (name) => qualityValidationRows.find(r => r.name === name) || {};
    const qualityRankMetric = (name) => qualityRankingRows.find(r => r.name === name) || {};
    const qualitySweepRun = (topN) => qualityOverlaySweepRuns.find(r => Number(r.top_n) === topN) || {};
    const qualityBtMetric = (name) => qualityOverlayBtSummaries.find(r => r.name === name) || {};
    const qBaseTop20 = qualityRankMetric('monthly_top20_model');
    const qBalancedTop20 = qualityRankMetric('monthly_top20_quality_balanced');
    const qSweepTop10Base = qualitySweepRun(10).baseline_test || {};
    const qSweepTop10Best = (qualitySweepRun(10).robust_candidates || [])[0] || {};
    const qBtModelTop10 = qualityBtMetric('test_2024h2_2026_model_top10');
    const qBtOverlayTop10 = qualityBtMetric('test_2024h2_2026_overlay_top10');
    const qAdvance = qualityMetric('advance_good');
    const qOrder = qualityMetric('order_recent');
    const sectorFocusUpdates = [
      {
        title: '수급 데이터 보정',
        body: 'research/전략 데이터셋에서 inst_net_buy_amt·frn_net_buy_amt가 비어 있으면 수량 순매수 × 종가로 금액을 환산합니다. 2020~2022 수량 보강분이 전략/섹터 수급에 반영됩니다.',
      },
      {
        title: '월간 후보 로직 개선',
        body: '기본축+저변동성 RS에서 수주잔고/수주 트리거 + 시장 또는 강한 섹터 + RS/저변동 조합으로 변경했습니다. KOSPI 절대 레벨이 높다는 이유만으로 매수 신호를 끄지 않습니다.',
      },
      {
        title: '주도섹터 매매 로직',
        body: '섹터 점수 55점 이상이면 BUY 후보로 보고, 섹터 내 3개월 RS 리더와 기관집중도 우수 종목 TOP3를 매수합니다. 섹터 점수 30점 미만은 최소 44일 보유 후 EXIT합니다.',
      },
      {
        title: '매핑/데이터 정합성',
        body: 'StockEasy 2026-06-28 스냅샷으로 전수 대조했습니다. 두산에너빌리티는 원자력으로 분리, 솔브레인홀딩스→솔브레인, 인터플렉스→이수페타시스, 펄어비스/091990 바이오 오염 제거, 현대건설→HD현대중공업 교정을 반영했습니다.',
      },
    ];

    const hubTabStyle = (key) => ({
      padding:'0.35rem 1rem', borderRadius:'8px', cursor:'pointer', fontSize:'0.82rem',
      fontWeight: hubTab===key ? 700 : 400,
      border: `1px solid ${hubTab===key ? 'rgba(245,158,11,0.55)' : 'var(--glass-border)'}`,
      background: hubTab===key ? 'rgba(245,158,11,0.14)' : 'transparent',
      color: hubTab===key ? '#f59e0b' : 'var(--text-secondary)',
    });

    return (
      <div className="fade-in" style={{display:'flex',flexDirection:'column',gap:'0.75rem'}}>

        {/* ── 헤더 (제목만) ── */}
        <div style={{display:'flex',alignItems:'center',gap:'0.5rem'}}>
          <span style={{fontSize:'0.95rem',fontWeight:800,color:'var(--accent-mint)'}}>⚗️ 전략 센터</span>
          <span style={{fontSize:'0.72rem',color:'var(--text-secondary)'}}>실시간 추천 종목 + 과거 백테스트 성과</span>
        </div>

        {/* ── 현재 시장 국면 ── */}
	        {marketRegime && (() => {
          const diff = marketRegime.diff_pct || 0;
          const regimeColor = regime==='BULL' ? '#f87171' : regime==='BEAR' ? '#60a5fa' : '#c084fc';
          const regimeBg    = regime==='BULL' ? 'rgba(239,68,68,0.06)' : regime==='BEAR' ? 'rgba(59,130,246,0.06)' : 'rgba(168,85,247,0.06)';
          const regimeBorder= regime==='BULL' ? 'rgba(239,68,68,0.25)' : regime==='BEAR' ? 'rgba(59,130,246,0.25)' : 'rgba(168,85,247,0.25)';
          const regimeEmoji = regime==='BULL' ? '📈' : regime==='BEAR' ? '📉' : '↔️';
          const regimeName  = regime==='BULL' ? '상승장 (BULL)' : regime==='BEAR' ? '하락장 (BEAR)' : '횡보장 (NEUTRAL)';

          // 장세별 PERIOD_RETURNS 인덱스 (순서: 상승장/하락장/회복장/AI랠리/최근/최신)
          const periodIdx = regime==='BULL' ? 0 : regime==='BEAR' ? 1 : null;

          // 동적 장세 이유 (KOSPI vs MA120 데이터 기반)
          const absD = Math.abs(diff).toFixed(1);
          const signD = diff >= 0 ? `+${diff.toFixed(1)}` : diff.toFixed(1);
          const regimeReason = regime==='BULL'
            ? diff > 25
              ? `KOSPI가 MA120 대비 +${absD}% 위 — 강한 상승 추세. 모멘텀·추세추종 전략이 역사적으로 최고 수익을 냈습니다.`
              : diff > 10
                ? `KOSPI가 MA120 대비 +${absD}% 위 — 상승 추세 진행 중. 추세추종·이익가속 전략이 유리합니다.`
                : `KOSPI가 MA120 대비 +${absD}% 위 — 상승 초입 구간. 재무우량·추세 전략을 주목하세요.`
            : regime==='BEAR'
              ? diff < -15
                ? `KOSPI가 MA120 대비 ${absD}% 아래 — 강한 하락 추세. 진입 자동차단 전략과 방어적 접근이 필요합니다.`
                : `KOSPI가 MA120 대비 ${absD}% 아래 — 하락 추세. 하락장 진입 자동차단 전략이 손실을 최소화합니다.`
              : `KOSPI가 MA120 대비 ${signD}% — 방향성 불분명한 횡보 구간. 평균 수익률이 높은 전략을 우선하세요.`;

          // 현재 장세 상위 3 전략 (PERIOD_RETURNS 동적 계산, 하드코딩 없음)
          const top3 = matrixVerified ? [...STRATEGY_HUB_STRATEGIES]
            .filter(s => PERIOD_RETURNS[s.key] !== undefined)
            .sort((a, b) => {
              if (periodIdx !== null) return (PERIOD_RETURNS[b.key][periodIdx] ?? -999) - (PERIOD_RETURNS[a.key][periodIdx] ?? -999);
              const avg = (key) => {
                const values = (PERIOD_RETURNS[key] || []).filter(v => v != null);
                return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : -999;
              };
              return avg(b.key) - avg(a.key);
            })
            .slice(0, 3) : [];

          return (
            <div className="glass-panel" style={{
              padding:'0.75rem 1.1rem',background:regimeBg,border:`1px solid ${regimeBorder}`,
              display:'flex',flexDirection:'column',gap:'0.45rem',
            }}>
              {/* 행 1: 현재 국면 + KOSPI 수치 */}
              <div style={{display:'flex',alignItems:'center',gap:'0.6rem',flexWrap:'wrap'}}>
                <span style={{fontSize:'1.05rem'}}>{regimeEmoji}</span>
                <span style={{fontWeight:800,fontSize:'0.9rem',color:regimeColor}}>현재: {regimeName}</span>
                <span style={{fontSize:'0.68rem',color:'var(--text-secondary)'}}>
                  KOSPI {marketRegime.kospi?.toLocaleString()} / MA120 {marketRegime.ma120?.toFixed(0)} ({signD}%) · {marketRegime.today}
                </span>
              </div>
              {/* 행 2: 추천 전략 */}
              <div style={{display:'flex',alignItems:'center',gap:'0.4rem',flexWrap:'wrap'}}>
                <span style={{fontSize:'0.7rem',color:'var(--text-secondary)',fontWeight:600,whiteSpace:'nowrap'}}>
                  {matrixVerified ? '추천 전략' : '추천 보류'}
                </span>
                {top3.map((s, i) => {
                  const values = (PERIOD_RETURNS[s.key] || []).filter(v => v != null);
                  const ret = periodIdx !== null
                    ? PERIOD_RETURNS[s.key][periodIdx]
                    : values.reduce((sum, value) => sum + value, 0) / Math.max(1, values.length);
                  return (
                    <button key={s.key}
                      onClick={() => setSelectedStrat(s.key)}
                      style={{
                        padding:'0.22rem 0.65rem',borderRadius:'6px',cursor:'pointer',fontSize:'0.75rem',fontWeight:700,
                        border:`1px solid ${regimeColor}${i===0 ? '88' : '44'}`,
                        background: i===0 ? `${regimeColor}28` : 'rgba(255,255,255,0.04)',
                        color: i===0 ? regimeColor : 'rgba(255,255,255,0.7)',
                      }}>
                      {i+1}. {s.label} ({ret >= 0 ? '+' : ''}{ret?.toFixed(1)}%)
                    </button>
                  );
                })}
                {!matrixVerified && (
                  <span style={{fontSize:'0.7rem',color:'#fbbf24'}}>
                    실행 명세와 run hash가 검증된 결과가 없어 자동 추천을 표시하지 않습니다.
                  </span>
                )}
              </div>
              {/* 행 3: 이유 설명 */}
              <div style={{fontSize:'0.71rem',color:'rgba(255,255,255,0.45)',lineHeight:1.6}}>
                {regimeReason}
              </div>
            </div>
          );
	        })()}

        {strategyResearch && (
          <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(320px,1fr))',gap:'0.75rem'}}>
            <div className="glass-panel" style={{padding:'0.85rem 1rem',display:'flex',flexDirection:'column',gap:'0.55rem'}}>
              <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',gap:'0.6rem',flexWrap:'wrap'}}>
                <strong style={{fontSize:'0.84rem',color:'#93c5fd'}}>3배 라벨 연구 요약</strong>
                <span style={{fontSize:'0.66rem',color:'var(--text-secondary)'}}>
                  최신 스냅샷 {strategyResearch.latest_snapshot_date || '-'}
                </span>
              </div>
              <div style={{display:'grid',gridTemplateColumns:'repeat(2,minmax(0,1fr))',gap:'0.45rem'}}>
                {[
                  { label:'스냅샷 행수', value: Number(strategyResearch?.dataset?.snapshot_rows || 0).toLocaleString() },
                  { label:'커버 종목', value: Number(strategyResearch?.dataset?.stocks_covered || 0).toLocaleString() },
                  { label:'12개월 라벨', value: Number(strategyResearch?.dataset?.label_3x_12m_rows || 0).toLocaleString() },
                  { label:'월 스냅샷 수', value: Number(strategyResearch?.dataset?.snapshot_months || 0).toLocaleString() },
                ].map(item => (
                  <div key={item.label} style={{padding:'0.5rem 0.6rem',borderRadius:8,background:'rgba(255,255,255,0.03)',border:'1px solid rgba(255,255,255,0.05)'}}>
                    <div style={{fontSize:'0.64rem',color:'var(--text-secondary)',marginBottom:3}}>{item.label}</div>
                    <div style={{fontSize:'0.92rem',fontWeight:900,color:'#e2e8f0'}}>{item.value}</div>
                  </div>
                ))}
              </div>
              <div style={{fontSize:'0.69rem',lineHeight:1.6,color:'rgba(255,255,255,0.52)'}}>
                월말 기준 snapshot에서 6개월/12개월 최대 상승률 라벨을 다시 만들고, 휴리스틱 점수와 ML 확률을 함께 비교합니다.
              </div>
            </div>

            <div className="glass-panel" style={{padding:'0.85rem 1rem',display:'flex',flexDirection:'column',gap:'0.55rem'}}>
              <strong style={{fontSize:'0.84rem',color:'#fcd34d'}}>휴리스틱 vs ML 상위 추천 품질</strong>
              <div style={{display:'grid',gridTemplateColumns:'repeat(2,minmax(0,1fr))',gap:'0.45rem'}}>
                {[
                  { label:'휴리스틱 Top10 정밀도', value:`${(((researchEval?.heuristic_top10?.precision || 0) * 100)).toFixed(1)}%`, tone:'#f59e0b' },
                  { label:'ML Top10 정밀도', value:`${(((researchEval?.ml_top10?.precision || 0) * 100)).toFixed(1)}%`, tone:'#34d399' },
                  { label:'휴리스틱 Top20 평균수익', value:`${(((researchEval?.heuristic_top20?.avg_forward_ret_12m || 0) * 100)).toFixed(1)}%`, tone:'#f59e0b' },
                  { label:'ML Top20 평균수익', value:`${(((researchEval?.ml_top20?.avg_forward_ret_12m || 0) * 100)).toFixed(1)}%`, tone:'#34d399' },
                ].map(item => (
                  <div key={item.label} style={{padding:'0.5rem 0.6rem',borderRadius:8,background:'rgba(255,255,255,0.03)',border:'1px solid rgba(255,255,255,0.05)'}}>
                    <div style={{fontSize:'0.64rem',color:'var(--text-secondary)',marginBottom:3}}>{item.label}</div>
                    <div style={{fontSize:'0.9rem',fontWeight:900,color:item.tone}}>{item.value}</div>
                  </div>
                ))}
              </div>
              <div style={{fontSize:'0.69rem',lineHeight:1.6,color:'rgba(255,255,255,0.52)'}}>
                목표는 평균 수익률보다 월별 상위 추천의 적중률을 높이는 것입니다. 이 패널은 12개월 내 3배 달성 라벨 기준으로 상위 추천 품질을 비교합니다.
              </div>
            </div>

            {strategyResearch?.quality_factor_validation && (
              <div className="glass-panel" style={{padding:'0.85rem 1rem',display:'flex',flexDirection:'column',gap:'0.55rem'}}>
                <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',gap:'0.6rem',flexWrap:'wrap'}}>
                  <strong style={{fontSize:'0.84rem',color:'#38bdf8'}}>수주·품질 지표 검증</strong>
                  <span style={{fontSize:'0.66rem',color:'var(--text-secondary)'}}>
                    PIT 검증 · {strategyResearch.quality_factor_validation.as_of_cutoff || '-'} 컷오프
                  </span>
                </div>
                <div style={{display:'grid',gridTemplateColumns:'repeat(2,minmax(0,1fr))',gap:'0.45rem'}}>
                  {[
                    { label:'선수금/계약부채', value:`+${((qAdvance.avg_12m || 0) * 100).toFixed(1)}%`, sub:`3배 ${Number(qAdvance.triple_12m || 0).toFixed(1)}%`, tone:'#34d399' },
                    { label:'최근 수주계약', value:`+${((qOrder.avg_12m || 0) * 100).toFixed(1)}%`, sub:`3배 ${Number(qOrder.triple_12m || 0).toFixed(1)}%`, tone:'#34d399' },
                    { label:'Top20 기본', value:`+${((qBaseTop20.avg_12m || 0) * 100).toFixed(1)}%`, sub:`3배 ${Number(qBaseTop20.triple_12m || 0).toFixed(1)}%`, tone:'#e2e8f0' },
                    { label:'라벨 Top10 보조', value:`+${((qSweepTop10Best.test_avg12 || 0) * 100).toFixed(1)}%`, sub:`기본 +${((qSweepTop10Base.avg_12m || 0) * 100).toFixed(1)}%`, tone:(qSweepTop10Best.test_avg12 || 0) >= (qSweepTop10Base.avg_12m || 0) ? '#34d399' : '#f87171' },
                    { label:'실행 Top10 보조', value:`${Number(qBtOverlayTop10.total_return_pct || 0).toFixed(1)}%`, sub:`기본 ${Number(qBtModelTop10.total_return_pct || 0).toFixed(1)}%`, tone:(qBtOverlayTop10.total_return_pct || 0) >= (qBtModelTop10.total_return_pct || 0) ? '#34d399' : '#f87171' },
                  ].map(item => (
                    <div key={item.label} style={{padding:'0.5rem 0.6rem',borderRadius:8,background:'rgba(255,255,255,0.03)',border:'1px solid rgba(255,255,255,0.05)'}}>
                      <div style={{fontSize:'0.64rem',color:'var(--text-secondary)',marginBottom:3}}>{item.label}</div>
                      <div style={{fontSize:'0.9rem',fontWeight:900,color:item.tone}}>{item.value}</div>
                      <div style={{fontSize:'0.62rem',color:'rgba(255,255,255,0.48)',marginTop:2}}>{item.sub}</div>
                    </div>
                  ))}
                </div>
                <div style={{fontSize:'0.69rem',lineHeight:1.6,color:'rgba(255,255,255,0.55)'}}>
                  결론: 라벨 검증에서는 Top10 보조조합이 좋아 보였지만, 다음 거래일 시가 진입·월별 리밸런싱 실행 백테스트에서는 기본 ML Top10보다 크게 낮았습니다. 이 지표들은 매수 랭킹으로 채택하지 않고, 후보 설명용 촉매/주의 근거로만 봅니다.
                </div>
                {qualityOverlayTop10.length > 0 && (
                  <div style={{display:'flex',flexDirection:'column',gap:'0.32rem'}}>
                    <div style={{fontSize:'0.68rem',fontWeight:800,color:'#f87171'}}>현재 보조랭킹 Top5 · 실행검증 실패로 매수랭킹 미채택</div>
                    {qualityOverlayTop10.map(item => (
                      <button key={`qov-${item.stock_code}`}
                        onClick={() => { changeStock(item.stock_code); changeTab('analysis'); }}
                        style={{display:'flex',alignItems:'center',justifyContent:'space-between',gap:'0.55rem',padding:'0.45rem 0.55rem',borderRadius:8,cursor:'pointer',background:'rgba(255,255,255,0.03)',border:'1px solid rgba(255,255,255,0.05)',color:'rgba(255,255,255,0.82)'}}>
                        <span style={{fontSize:'0.7rem',fontWeight:800}}>{item.stock_name} <span style={{fontSize:'0.62rem',color:'var(--text-secondary)'}}>{item.stock_code}</span></span>
                        <span style={{fontSize:'0.65rem',color:'#94a3b8'}}>
                          보조 {(Number(item.quality_overlay_score || 0) * 100).toFixed(1)} · 수주{item.order_recent ? '✓' : '-'} · 현금{item.cash_good ? '✓' : '-'} · 재고{item.inventory_good ? '감점' : '-'}
                        </span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}

            <div className="glass-panel" style={{padding:'0.85rem 1rem',display:'flex',flexDirection:'column',gap:'0.55rem'}}>
              <strong style={{fontSize:'0.84rem',color:'#86efac'}}>현재 국면 전략 우선순위</strong>
              {matrixVerified ? <div style={{display:'flex',flexDirection:'column',gap:'0.42rem'}}>
                {currentStrategyRanks.map((item, idx) => (
                  <button key={item.strategy}
                    onClick={() => { setSelectedStrat(item.strategy); setHubTab('stocks'); }}
                    style={{display:'flex',alignItems:'center',justifyContent:'space-between',gap:'0.65rem',padding:'0.5rem 0.65rem',borderRadius:8,cursor:'pointer',background:idx===0?'rgba(34,197,94,0.12)':'rgba(255,255,255,0.03)',border:`1px solid ${idx===0?'rgba(34,197,94,0.28)':'rgba(255,255,255,0.06)'}`,color:'rgba(255,255,255,0.82)'}}>
                    <span style={{fontSize:'0.74rem',fontWeight:700}}>{idx + 1}. {item.label}</span>
                    <span style={{fontSize:'0.8rem',fontWeight:900,color:item.avg_ret >= 0 ? '#34d399' : '#60a5fa'}}>{item.avg_ret >= 0 ? '+' : ''}{Number(item.avg_ret || 0).toFixed(1)}%</span>
                  </button>
                ))}
              </div> : (
                <div style={{fontSize:'0.72rem',color:'#fbbf24',lineHeight:1.6}}>
                  실행 명세가 검증되지 않아 국면별 전략 순위를 표시하지 않습니다.
                </div>
              )}
              <div style={{fontSize:'0.69rem',lineHeight:1.6,color:'rgba(255,255,255,0.52)'}}>
                {matrixVerified
                  ? `현재 장세는 ${strategyResearch?.strategy_rankings?.regime?.regime || '-'}로 판정되었고, 같은 계열 기간의 검증 수익률 평균으로 우선순위를 계산합니다.`
                  : '시장 국면은 표시하되 백테스트 검증이 끝날 때까지 전략 추천에는 연결하지 않습니다.'}
              </div>
            </div>

            <div className="glass-panel" style={{padding:'0.85rem 1rem',display:'flex',flexDirection:'column',gap:'0.55rem'}}>
              <strong style={{fontSize:'0.84rem',color:'#c4b5fd'}}>현재 3배 후보 비교</strong>
              <div style={{display:'grid',gridTemplateColumns:'repeat(2,minmax(0,1fr))',gap:'0.6rem'}}>
                <div>
                  <div style={{fontSize:'0.68rem',fontWeight:800,color:'#34d399',marginBottom:'0.35rem'}}>ML 상위 5</div>
                  <div style={{display:'flex',flexDirection:'column',gap:'0.32rem'}}>
                    {currentMlTop.map(item => (
                      <div key={`ml-${item.stock_code}`} style={{padding:'0.42rem 0.5rem',borderRadius:8,background:'rgba(255,255,255,0.03)',border:'1px solid rgba(255,255,255,0.05)'}}>
                        <div style={{fontSize:'0.72rem',fontWeight:800,color:'#e2e8f0'}}>{item.stock_name} <span style={{fontSize:'0.64rem',color:'var(--text-secondary)'}}>{item.stock_code}</span></div>
                        <div style={{fontSize:'0.64rem',color:'rgba(255,255,255,0.56)',marginTop:2}}>
                          {item.sector_large || '-'} · ML {(Number(item.model_score_12m || 0) * 100).toFixed(1)}% · PBR {item.pbr ? Number(item.pbr).toFixed(2) : '-'}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
                <div>
                  <div style={{fontSize:'0.68rem',fontWeight:800,color:'#f59e0b',marginBottom:'0.35rem'}}>휴리스틱 상위 5</div>
                  <div style={{display:'flex',flexDirection:'column',gap:'0.32rem'}}>
                    {currentHeuristicTop.map(item => (
                      <div key={`heur-${item.stock_code}`} style={{padding:'0.42rem 0.5rem',borderRadius:8,background:'rgba(255,255,255,0.03)',border:'1px solid rgba(255,255,255,0.05)'}}>
                        <div style={{fontSize:'0.72rem',fontWeight:800,color:'#e2e8f0'}}>{item.stock_name} <span style={{fontSize:'0.64rem',color:'var(--text-secondary)'}}>{item.stock_code}</span></div>
                        <div style={{fontSize:'0.64rem',color:'rgba(255,255,255,0.56)',marginTop:2}}>
                          {item.sector_large || '-'} · 점수 {Number(item.heuristic_score || 0).toFixed(1)} · 60일수익 {item.ret_60d != null ? `${(Number(item.ret_60d) * 100).toFixed(1)}%` : '-'}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {(backtestMatrixError || (backtestMatrix && !matrixVerified)) && (
          <div style={{padding:'0.65rem 0.9rem',borderRadius:6,
            background:'rgba(249,115,22,0.08)',border:'1px solid rgba(249,115,22,0.3)',
            color:'#fdba74',fontSize:'0.72rem',lineHeight:1.55}}>
            {backtestMatrixError
              ? `백테스트 API 오류: ${backtestMatrixError}`
              : backtestMatrix?.selection_required
                ? '선택 run registry가 비어 있어 성과와 추천을 표시하지 않습니다. 실행 명세와 검증 artifact가 연결된 run hash를 먼저 선택해야 합니다.'
                : `선택된 백테스트 ${matrixResults.length}건이 검증 게이트를 통과하지 못했습니다. 성과와 추천은 자동 차단됩니다.`}
          </div>
        )}

        {backtestMatrix?.governance && (
          <div style={{padding:'0.7rem 0.9rem',borderRadius:8,
            background:'rgba(15,118,110,0.08)',border:'1px solid rgba(45,212,191,0.25)',
            color:'#99f6e4',fontSize:'0.72rem',lineHeight:1.6}}>
            실전 승인 {backtestMatrix.governance.counts?.live_eligible || 0}개 · 종이운용 핵심 {backtestMatrix.governance.counts?.paper_core || 0}개 · 공격 위성 {backtestMatrix.governance.counts?.offensive_satellite || 0}개 · 검증 대기 {backtestMatrix.governance.counts?.validation_queue || 0}개 · 퇴역 {backtestMatrix.governance.counts?.retired || 0}개. 자동매매는 비활성화 상태입니다.
          </div>
        )}

	        {/* ── 3. 탭 + 로직 선택 드롭다운 (1개 행) ── */}
	        <div style={{display:'flex',gap:'0.4rem',alignItems:'center',flexWrap:'wrap'}}>
	          {[
	            { key:'matrix', label:'📊 성과 매트릭스' },
	            { key:'continuous', label:'💰 1억원 연속운용' },
	            { key:'desc',   label:'📘 전략 설명' },
	            { key:'ledger', label:'🧪 검증 이력' },
	            { key:'data-lab', label:'🧭 데이터 라우팅' },
	          ].map(t => (
            <button key={t.key}
              onClick={() => setHubTab(t.key)}
              style={hubTabStyle(t.key)}>
              {t.label}
            </button>
          ))}
          <span style={{width:'1px',height:'22px',background:'var(--glass-border)',flexShrink:0}} />
          <select
            value={selectedStrat}
            onChange={e => { setSelectedStrat(e.target.value); setHubTab('stocks'); }}
            style={{padding:'0.28rem 0.65rem',borderRadius:'8px',fontSize:'0.8rem',
              background:'rgba(245,158,11,0.1)',color:'rgba(255,255,255,0.85)',
              border:'1px solid rgba(245,158,11,0.35)',cursor:'pointer',outline:'none',minWidth:'195px'}}
          >
            {availableStrategies.map(s => (
              <option key={s.key} value={s.key}>{s.label}</option>
            ))}
          </select>
        </div>

        {hubTab === 'desc' && selectedStrat === 'sector_focus' && (
          <div className="glass-panel" style={{
            padding:'0.85rem 1rem',
            border:'1px solid rgba(192,132,252,0.28)',
            background:'rgba(192,132,252,0.06)',
          }}>
            <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',gap:'0.8rem',flexWrap:'wrap',marginBottom:'0.6rem'}}>
              <strong style={{fontSize:'0.86rem',color:'#d8b4fe'}}>V-SECTOR 주도섹터 전략</strong>
              <span style={{fontSize:'0.72rem',color:'var(--text-secondary)'}}>
                run {strategyAudit.sector_focus?.results?.[0]?.methodology?.run_hash || '선택 필요'}
              </span>
            </div>
            <div style={{display:'grid',gridTemplateColumns:'repeat(6,minmax(78px,1fr))',gap:'0.35rem',marginBottom:'0.75rem'}}>
              {STRATEGY_HUB_PERIOD_LABELS.map((label, i) => {
                const v = PERIOD_RETURNS.sector_focus[i];
                return (
                  <div key={label} style={{padding:'0.45rem 0.5rem',borderRadius:6,background:'rgba(0,0,0,0.18)',border:'1px solid rgba(255,255,255,0.06)'}}>
                    <div style={{whiteSpace:'pre-line',fontSize:'0.62rem',color:'var(--text-secondary)',lineHeight:1.25}}>{label}</div>
                    <div style={{fontSize:'0.9rem',fontWeight:900,color:clrRet(v),marginTop:3}}>{fmtRet(v)}</div>
                  </div>
                );
              })}
            </div>
            <div style={{display:'grid',gridTemplateColumns:'repeat(2,minmax(0,1fr))',gap:'0.45rem'}}>
              {sectorFocusUpdates.map(item => (
                <div key={item.title} style={{padding:'0.55rem 0.65rem',borderRadius:6,background:'rgba(15,23,42,0.45)',border:'1px solid rgba(255,255,255,0.06)'}}>
                  <div style={{fontSize:'0.74rem',fontWeight:800,color:'#e9d5ff',marginBottom:3}}>{item.title}</div>
                  <div style={{fontSize:'0.7rem',color:'rgba(255,255,255,0.62)',lineHeight:1.55}}>{item.body}</div>
                </div>
              ))}
            </div>
          </div>
        )}

	        {hubTab === 'matrix' && (() => {
	          const thSt = {padding:'0.45rem 0.6rem',background:'rgba(30,58,138,0.4)',
	            borderBottom:'2px solid rgba(59,130,246,0.35)',position:'sticky',top:0,zIndex:5,
	            color:'rgba(255,255,255,0.75)',fontWeight:600};
          return (
            <React.Fragment>
            <div className="glass-panel" style={{overflow:'clip'}}>
              <div style={{padding:'0.65rem 1rem',borderBottom:'1px solid var(--glass-border)',
                display:'flex',alignItems:'center',gap:'1rem',flexWrap:'wrap'}}>
                <span style={{fontWeight:700,fontSize:'0.88rem'}}>전략 × 기간 성과 매트릭스</span>
                <span style={{fontSize:'0.7rem',color:'var(--text-secondary)'}}>
                  백테스트 API 수익률(%) · 클릭 → 추천종목
                </span>
                <span style={{fontSize:'0.66rem',color:'#f59e0b',fontWeight:600}}>
                  실행 명세·run hash가 없는 결과는 레거시 참고값이며 전략 추천과 순위 산정에 사용하지 않습니다.
                </span>
              </div>
              <div style={{overflowX:'auto'}}>
                <table style={{width:'100%',borderCollapse:'collapse',fontSize:'0.78rem'}}>
                  <thead>
                    <tr>
                      <th style={{...thSt,minWidth:'140px',textAlign:'left'}}>전략</th>
                      {STRATEGY_HUB_PERIOD_LABELS.map((p,i) => (
                        <th key={i} style={{...thSt,minWidth:'82px',whiteSpace:'pre-line',textAlign:'center',fontSize:'0.66rem',lineHeight:1.3}}>
                          {p}
                        </th>
                      ))}
	                      <th style={{...thSt,minWidth:'64px',textAlign:'center',fontSize:'0.66rem',cursor:'pointer',userSelect:'none'}}
	                        title="클릭: 평균수익 정렬 (오름/내림 토글)"
	                        onClick={() => setStratSort(p => ({ key:'avg', dir: p.key==='avg' && p.dir==='desc' ? 'asc' : 'desc' }))}
	                      >avg {stratSort.key==='avg' ? (stratSort.dir==='desc' ? '▼' : '▲') : '↕'}<br/><span style={{fontSize:'0.58rem',opacity:0.6}}>(전기간 평균)</span></th>
	                      <th style={{...thSt,minWidth:'64px',textAlign:'center',fontSize:'0.66rem'}}>양수 구간</th>
                      <th style={{...thSt,minWidth:'76px',textAlign:'center',fontSize:'0.66rem',background:'rgba(16,185,129,0.18)',borderLeft:'2px solid rgba(16,185,129,0.4)',cursor:'pointer',userSelect:'none'}}
	                        title="클릭: 누적수익 정렬 (오름/내림 토글)"
	                        onClick={() => setStratSort(p => ({ key:'cum', dir: p.key==='cum' && p.dir==='desc' ? 'asc' : 'desc' }))}
	                      >연속운용 실측 {stratSort.key==='cum' ? (stratSort.dir==='desc' ? '▼' : '▲') : '↕'}<br/><span style={{fontSize:'0.55rem',opacity:0.7}}>2020.03~2026.03 단일계좌, 참고용(미등록)</span></th>
                      <th style={{...thSt,minWidth:'130px',textAlign:'left',fontSize:'0.66rem'}}>강점 요약</th>
                    </tr>
                  </thead>
                  <tbody>
                    {availableStrategies.map(s => {
                        // 평균/누적을 PERIOD_RETURNS에서 직접 계산 (하드코딩 avgRet 불일치 방지, 2026-07-11 재검증)
                        const rets0 = PERIOD_RETURNS[s.key] || [];
                        const valid0 = rets0.filter(v => v != null);
                        const dynAvg = valid0.length ? Math.round(valid0.reduce((a,b)=>a+b,0)/valid0.length*10)/10 : 0;
                        // 2026-07-17: 독립구간 복리곱(가짜 연속운용) 폐기 → 진짜 단일계좌 연속백테스트 실측치로 교체.
                        const dynCum = contReturns[s.key]?.ret ?? null;
                        return { ...s, dynAvg, dynCum };
                      })
                      .sort((a,b) => {
                        if (!stratSort.key) return 0;
                        const va = stratSort.key==='avg' ? a.dynAvg : a.dynCum;
                        const vb = stratSort.key==='avg' ? b.dynAvg : b.dynCum;
                        return stratSort.dir==='desc' ? vb-va : va-vb;
                      })
                      .map((s, si) => {
                      const rets = PERIOD_RETURNS[s.key] || [];
                      const validRets = rets.filter(v => v != null);
                      const maxRet = validRets.length ? Math.max(...validRets) : null;
                      const isSelected = selectedStrat === s.key;
                      return (
                        <tr key={s.key}
                          style={{background: isSelected ? 'rgba(245,158,11,0.07)' : si%2===0 ? 'transparent' : 'rgba(255,255,255,0.013)', cursor:'pointer'}}
                          onMouseOver={e => e.currentTarget.style.background='rgba(255,255,255,0.045)'}
                          onMouseOut={e => e.currentTarget.style.background= isSelected ? 'rgba(245,158,11,0.07)' : si%2===0 ? 'transparent' : 'rgba(255,255,255,0.013)'}
                          onClick={() => { setSelectedStrat(s.key); setHubTab('stocks'); }}
                        >
                          <td style={{padding:'0.42rem 0.75rem',fontWeight:isSelected?900:700,
	                            borderBottom:'1px solid rgba(255,255,255,0.04)',
	                            color: isSelected ? s.color : '#e2e8f0',whiteSpace:'nowrap',
	                            borderLeft: isSelected ? `3px solid ${s.color}` : '3px solid transparent'}}>
	                            {s.label}
	                            {strategyMethodology[s.key] && (
	                              <span style={{
	                                display:'inline-block',
	                                marginLeft:'0.45rem',
	                                padding:'0.08rem 0.38rem',
	                                borderRadius:'999px',
	                                border:`1px solid ${strategyMethodology[s.key].verified ? '#22c55e55' : '#f59e0b55'}`,
	                                background:strategyMethodology[s.key].verified ? '#22c55e18' : '#f59e0b18',
	                                color:strategyMethodology[s.key].verified ? '#22c55e' : '#f59e0b',
	                                fontSize:'0.58rem',
	                                fontWeight:800,
	                                verticalAlign:'middle',
	                              }}>
		                                {strategyMethodology[s.key].label}
	                              </span>
	                            )}
	                          </td>
                          {rets.map((v, i) => {
                            const isBest = v != null && v === maxRet && v > 0;
                            return (
                              <td key={i} style={{
                                padding:'0.35rem 0.4rem',textAlign:'center',
                                borderBottom:'1px solid rgba(255,255,255,0.04)',
                                background: isBest ? 'rgba(239,68,68,0.25)' : v > 0 ? 'rgba(239,68,68,0.07)' : v < 0 ? 'rgba(59,130,246,0.09)' : 'transparent',
                                color: v > 0 ? '#f87171' : v < 0 ? '#60a5fa' : 'rgba(255,255,255,0.25)',
                                fontWeight: isBest ? 900 : 400,
                              }}>
                                {v == null ? '-' : (v >= 0 ? '+' : '') + v.toFixed(1) + '%'}
                              </td>
                            );
                          })}
                          <td style={{padding:'0.35rem 0.4rem',textAlign:'center',
                            borderBottom:'1px solid rgba(255,255,255,0.04)',
                            color: s.dynAvg >= 20 ? '#f87171' : s.dynAvg >= 10 ? '#fbbf24' : s.dynAvg >= 0 ? 'rgba(255,255,255,0.65)' : '#60a5fa',
                            fontWeight: s.dynAvg >= 20 ? 800 : 600}}>
                            {s.dynAvg >= 0 ? '+' : ''}{s.dynAvg}%
                          </td>
                          {(() => {
                            const total = rets.filter(v => v != null).length;
                            const wins = rets.filter(v => v != null && v > 0).length;
                            const winRate = total > 0 ? wins / total : 0;
                            // 2026-07-17: '연속운용 실측' = 2020.03~2026.03 단일계좌 진짜 연속 백테스트(스냅샷 격리 실행).
                            // 기존 '구간곱 참고'(6개 독립리셋 구간을 곱셈으로 이어붙인 근사치)는 오해 소지가 커 폐기.
                            const cumRet = s.dynCum;
                            const cumTrades = contReturns[s.key]?.trades;
                            return (
                              <React.Fragment>
                                <td style={{padding:'0.35rem 0.4rem',textAlign:'center',
                                  borderBottom:'1px solid rgba(255,255,255,0.04)',
                                  color: winRate >= 0.8 ? '#f87171' : winRate >= 0.6 ? '#fbbf24' : winRate >= 0.4 ? 'rgba(255,255,255,0.65)' : '#60a5fa',
                                  fontWeight: winRate >= 0.8 ? 800 : 600,
                                  fontSize:'0.75rem'}}>
                                  {wins}/{total}
                                </td>
                                <td style={{padding:'0.35rem 0.4rem',textAlign:'center',
                                  borderBottom:'1px solid rgba(255,255,255,0.04)',
                                  borderLeft:'2px solid rgba(16,185,129,0.25)',
                                  background: cumRet == null ? 'transparent' : cumRet > 200 ? 'rgba(16,185,129,0.15)' : cumRet > 50 ? 'rgba(16,185,129,0.07)' : cumRet < 0 ? 'rgba(59,130,246,0.08)' : 'transparent',
                                  color: cumRet == null ? 'rgba(255,255,255,0.25)' : cumRet > 200 ? '#34d399' : cumRet > 50 ? '#6ee7b7' : cumRet >= 0 ? 'rgba(255,255,255,0.6)' : '#60a5fa',
                                  fontWeight: cumRet != null && cumRet > 100 ? 800 : 600,
                                  fontSize:'0.8rem'}}
                                  title={cumTrades ? `거래 ${cumTrades}건, 승률 ${contReturns[s.key]?.win}%` : ''}>
                                  {cumRet == null ? '미실측' : (cumRet >= 0 ? '+' : '') + cumRet + '%'}
                                </td>
                              </React.Fragment>
                            );
                          })()}
                          <td style={{padding:'0.35rem 0.6rem',fontSize:'0.67rem',
                            color:'rgba(255,255,255,0.38)',lineHeight:1.4,
                            borderBottom:'1px solid rgba(255,255,255,0.04)',maxWidth:'180px',overflow:'hidden',
                            whiteSpace:'nowrap',textOverflow:'ellipsis'}}>
                            {strategySummary(s)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <div style={{padding:'0.45rem 0.9rem',fontSize:'0.67rem',color:'rgba(255,255,255,0.3)',borderTop:'1px solid var(--glass-border)'}}>
                API에서 불러온 독립 구간 백테스트 참고값입니다. 실행 명세가 검증되기 전에는 실제 연속운용 수익이나 전략 순위로 해석하지 않습니다.
              </div>
            </div>

            {/* ── 전략 조합 — 병합계좌 실측 결과 (2026-07-18 실데이터 전환) ── */}
            {(() => {
              // 가중평균은 계좌 백테스트가 아니므로 사용하지 않는다.
              // /api/backtest/combinations/list — persist_merged_run으로 등록된
              // 검증 게이트(구성 run 전부 execution 아티팩트 보유) 통과 run만 표시.
              // 2026-07-23: 여러 차례 조합 실험(437%→510%→539%→605%)이 전부 개별 run으로 남아
              // API가 다 반환하므로, 총수익률 내림차순 정렬 + 최고기록만 강조 배지를 달아
              // "현재 채택된 최고 조합"과 "지나간 실험 기록"을 구분되게 표시.
              const rows = [...(comboRuns || [])].sort((a, b) => (b.total_return_pct ?? 0) - (a.total_return_pct ?? 0));
              return (
                <div className="glass-panel" style={{marginTop:'1rem', overflow:'hidden'}}>
                  <div style={{padding:'0.65rem 1rem', borderBottom:'1px solid var(--glass-border)', display:'flex', alignItems:'center', gap:'0.75rem', flexWrap:'wrap'}}>
                    <span style={{fontWeight:700, fontSize:'0.88rem'}}>전략 조합 — 병합계좌 실측</span>
                    <span style={{fontSize:'0.7rem', color:'var(--text-secondary)'}}>
                      여러 전략의 주문을 하나의 1억원 계좌에서 실제 병합 체결 (가중평균 아님 · 구성 run 검증 게이트 통과분만)
                    </span>
                  </div>
                  {rows.length === 0 ? (
                    <div style={{padding:'1rem', fontSize:'0.75rem', color:'var(--text-secondary)'}}>
                      등록된 병합 run이 없습니다.
                    </div>
                  ) : (
                    <div style={{overflowX:'auto'}}>
                      <table style={{width:'100%', borderCollapse:'collapse', fontSize:'0.76rem'}}>
                        <thead>
                          <tr style={{color:'var(--text-secondary)', textAlign:'left'}}>
                            <th style={{padding:'0.45rem 1rem'}}>구성 전략</th>
                            <th style={{padding:'0.45rem 0.6rem'}}>기간</th>
                            <th style={{padding:'0.45rem 0.6rem', textAlign:'right'}}>총수익률</th>
                            <th style={{padding:'0.45rem 0.6rem', textAlign:'right'}}>승률</th>
                            <th style={{padding:'0.45rem 0.6rem', textAlign:'right'}}>최대낙폭(MDD)</th>
                            <th style={{padding:'0.45rem 0.6rem', textAlign:'right'}}>거래</th>
                            <th style={{padding:'0.45rem 0.6rem'}}>검증</th>
                          </tr>
                        </thead>
                        <tbody>
                          {rows.map((run, idx) => (
                            <tr key={run.run_id} style={{borderTop:'1px solid rgba(255,255,255,0.05)',
                              opacity: idx === 0 ? 1 : 0.55}}>
                              <td style={{padding:'0.45rem 1rem'}}>
                                {idx === 0 && (
                                  <span style={{display:'inline-block', margin:'0.1rem 0.4rem 0.1rem 0',
                                    padding:'0.1rem 0.5rem', borderRadius:'999px', fontSize:'0.66rem', fontWeight:700,
                                    background:'rgba(251,191,36,0.18)', border:'1px solid rgba(251,191,36,0.5)', color:'#fbbf24'}}>
                                    ★ 현재 최고
                                  </span>
                                )}
                                {(run.components || []).map((c, i) => (
                                  <span key={i} style={{display:'inline-block', margin:'0.1rem 0.25rem 0.1rem 0',
                                    padding:'0.1rem 0.5rem', borderRadius:'999px', fontSize:'0.68rem',
                                    background:'rgba(99,102,241,0.15)', border:'1px solid rgba(99,102,241,0.4)', color:'#a5b4fc'}}>
                                    {c.label || c.strategy}
                                  </span>
                                ))}
                              </td>
                              <td style={{padding:'0.45rem 0.6rem', whiteSpace:'nowrap', color:'var(--text-secondary)', fontSize:'0.7rem'}}>
                                {run.start_date?.slice(0,7)} ~ {run.end_date?.slice(0,7)}
                              </td>
                              <td style={{padding:'0.45rem 0.6rem', textAlign:'right', fontWeight:800,
                                color: (run.total_return_pct ?? 0) > 0 ? '#f87171' : '#60a5fa', fontSize:'0.85rem'}}>
                                {(run.total_return_pct ?? 0) >= 0 ? '+' : ''}{(run.total_return_pct ?? 0).toFixed(1)}%
                              </td>
                              <td style={{padding:'0.45rem 0.6rem', textAlign:'right', color:'rgba(255,255,255,0.7)'}}>
                                {run.win_rate != null ? run.win_rate.toFixed(1) + '%' : '-'}
                              </td>
                              <td style={{padding:'0.45rem 0.6rem', textAlign:'right', color:'#fca5a5'}}>
                                {run.max_drawdown_pct != null ? run.max_drawdown_pct.toFixed(1) + '%' : '-'}
                              </td>
                              <td style={{padding:'0.45rem 0.6rem', textAlign:'right', color:'rgba(255,255,255,0.55)'}}>
                                {run.total_trades?.toLocaleString() ?? '-'}건
                              </td>
                              <td style={{padding:'0.45rem 0.6rem', whiteSpace:'nowrap'}}>
                                {idx === 0 ? (
                                  <span style={{padding:'0.1rem 0.45rem', borderRadius:'4px', fontSize:'0.64rem',
                                    background:'rgba(16,185,129,0.14)', border:'1px solid rgba(16,185,129,0.4)', color:'#34d399'}}>
                                    병합체결 검증
                                  </span>
                                ) : (
                                  <span style={{padding:'0.1rem 0.45rem', borderRadius:'4px', fontSize:'0.64rem',
                                    background:'rgba(255,255,255,0.06)', border:'1px solid rgba(255,255,255,0.15)', color:'rgba(255,255,255,0.5)'}}>
                                    이전 실험(참고용)
                                  </span>
                                )}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      <div style={{padding:'0.5rem 1rem', fontSize:'0.66rem', color:'var(--text-secondary)', borderTop:'1px solid rgba(255,255,255,0.05)'}}>
                        원리: 전략별 신호 공백기의 유휴자본을 다른 전략이 재활용. 2026-07-28 재검증: v4/V-EARNINGS/V-MOONSHOT의 시총 필터가 현재시총 기준(룩어헤드)이었던 버그를 as-of로 고치자, 이 세 전략을 포함한 5~7전략 조합은 전부 큰 폭으로 하락했고, <b>V3 재무우량 + V-SECTOR 주도섹터 2개만 남긴 조합</b>이 최고 기록으로 올라섬.
                        2026-07-29 추가검증: 이 655.6%는 측정기간이 2026-03-31에서 멈춰있던 것으로, 최신 거래일까지 다시 계산하면 <b>+612.9%(MDD -34.6%)</b>가 정직한 현재 수치. 이어서 v4/megatrend/V-EARNINGS/V-MOONSHOT/V10/V-RECOVERY 6개를 신선한(2026-07-28 기준) 소스로 하나씩·여러개씩 추가해봤으나 <b>전부 수익률이 크게 하락(324~486%)</b>했고, 포지션수·티켓크기·섹터집중한도를 바꾼 안정성(MDD) 튜닝도 수익을 깎지 않고 낙폭만 줄이는 조합은 찾지 못함 — 현재 2전략 구성이 탐색된 범위 내 최선.
                        ⚠️ 소스 신호는 각 전략 단독 1억 운용 가정에서 생성된 1차 근사 — 공유자본 신호 재생성(완전 결합 시뮬)은 후속 과제. 이 표는 2026-07-25 merged_simulator.py 일별 마킹버그 수정 이후 등록분 중 <b>가장 최신 데이터까지 측정된 1건</b>만 표시.
                      </div>
                    </div>
                  )}
                </div>
              );
            })()}
            </React.Fragment>
          );
        })()}

		        {hubTab === 'continuous' && (
	          <div className="glass-panel" style={{padding:'1.25rem',border:'1px solid rgba(249,115,22,0.3)',color:'#fdba74',fontSize:'0.78rem',lineHeight:1.65}}>
		            선택 registry에 연속운용 run hash가 등록되지 않아 표시를 중단했습니다. 동일한 1억원 현금원장, 동적 슬롯, 체결 시점과 비용이 포함된 API 결과만 표시합니다.
	          </div>
	        )}

		        {hubTab === 'continuous' && false && (() => {
	          const rows = [...STRATEGY_HUB_STRATEGIES]
	            .filter(s => contReturns[s.key])
	            .map(s => {
	              const c = contReturns[s.key];
	              // 시작자본 1억원(=1억 단위) 기준. final/profit은 저장값이 아니라 ret(%)에서 직접 계산한다.
	              return { ...s, ...c, final: 1 + c.ret / 100, profit: c.ret / 100 };
	            })
	            .sort((a, b) => b.ret - a.ret);
	          const avgRet = rows.reduce((sum, r) => sum + r.ret, 0) / Math.max(1, rows.length);
	          const medianRet = rows.length ? [...rows].sort((a, b) => a.ret - b.ret)[Math.floor(rows.length / 2)].ret : 0;
	          const top = rows[0];
	          const thSt = {
	            padding:'0.45rem 0.6rem',
	            background:'rgba(20,83,45,0.36)',
	            borderBottom:'2px solid rgba(34,197,94,0.32)',
	            color:'rgba(255,255,255,0.74)',
	            fontWeight:700,
	            position:'sticky',
	            top:0,
	            zIndex:5,
	          };
	          return (
	            <div className="glass-panel" style={{overflow:'clip'}}>
	              <div style={{padding:'0.8rem 1rem',borderBottom:'1px solid var(--glass-border)',display:'flex',alignItems:'center',justifyContent:'space-between',gap:'0.8rem',flexWrap:'wrap'}}>
	                <div>
	                  <div style={{fontWeight:800,fontSize:'0.9rem',color:'#86efac'}}>1억원 고정 10슬롯 연속운용 백테스트</div>
	                  <div style={{fontSize:'0.7rem',color:'var(--text-secondary)',marginTop:3}}>
	                    2020-03-01~2026-07-03 · 시작자본 1억원 · 1천만원 단위 최대 10종목 · 동적 슬롯 확장 미반영
	                  </div>
	                </div>
	                <div style={{display:'flex',gap:'0.45rem',flexWrap:'wrap'}}>
	                  {[
	                    {label:'예산 방식', value:'고정 10슬롯', tone:'#fbbf24'},
	                    {label:'최고 전략', value:`${top?.label} ${top?.ret >= 0 ? '+' : ''}${top?.ret.toFixed(1)}%`, tone:'#34d399'},
	                    {label:'전체 평균', value:`${avgRet >= 0 ? '+' : ''}${avgRet.toFixed(1)}%`, tone:'#fbbf24'},
	                    {label:'중앙값', value:`${medianRet >= 0 ? '+' : ''}${medianRet.toFixed(1)}%`, tone:'#38bdf8'},
	                  ].map(item => (
	                    <div key={item.label} style={{padding:'0.45rem 0.65rem',borderRadius:6,background:'rgba(0,0,0,0.18)',border:'1px solid rgba(255,255,255,0.06)',minWidth:120}}>
	                      <div style={{fontSize:'0.62rem',color:'var(--text-secondary)',marginBottom:2}}>{item.label}</div>
	                      <div style={{fontSize:'0.84rem',fontWeight:900,color:item.tone}}>{item.value}</div>
	                    </div>
	                  ))}
	                </div>
	              </div>
	              <div style={{overflowX:'auto'}}>
	                <table style={{width:'100%',borderCollapse:'collapse',fontSize:'0.78rem'}}>
	                  <thead>
	                    <tr>
	                      <th style={{...thSt,minWidth:44,textAlign:'center'}}>순위</th>
	                      <th style={{...thSt,minWidth:165,textAlign:'left'}}>전략</th>
	                      <th style={{...thSt,minWidth:82,textAlign:'right'}}>총수익률</th>
	                      <th style={{...thSt,minWidth:90,textAlign:'right'}}>최종금액</th>
	                      <th style={{...thSt,minWidth:90,textAlign:'right'}}>수익금</th>
	                      <th style={{...thSt,minWidth:70,textAlign:'right'}}>거래수</th>
	                      <th style={{...thSt,minWidth:64,textAlign:'right'}}>승률</th>
	                      <th style={{...thSt,minWidth:170,textAlign:'left'}}>판단</th>
	                    </tr>
	                  </thead>
	                  <tbody>
	                    {rows.map((r, i) => {
	                      const isSelected = selectedStrat === r.key;
	                      return (
	                        <tr key={r.key}
	                          onClick={() => { setSelectedStrat(r.key); setHubTab('stocks'); }}
	                          style={{cursor:'pointer',background:isSelected ? 'rgba(34,197,94,0.08)' : i%2 ? 'rgba(255,255,255,0.012)' : 'transparent'}}
	                        >
	                          <td style={{padding:'0.42rem 0.55rem',textAlign:'center',borderBottom:'1px solid rgba(255,255,255,0.04)',color:i<3 ? '#86efac' : 'rgba(255,255,255,0.55)',fontWeight:800}}>{i+1}</td>
	                          <td style={{padding:'0.42rem 0.65rem',borderBottom:'1px solid rgba(255,255,255,0.04)',color:isSelected ? r.color : '#e2e8f0',fontWeight:800,borderLeft:isSelected ? `3px solid ${r.color}` : '3px solid transparent',whiteSpace:'nowrap'}}>{r.label}</td>
	                          <td style={{padding:'0.42rem 0.6rem',textAlign:'right',borderBottom:'1px solid rgba(255,255,255,0.04)',color:clrRet(r.ret),fontWeight:900}}>{fmtRet(r.ret)}</td>
	                          <td style={{padding:'0.42rem 0.6rem',textAlign:'right',borderBottom:'1px solid rgba(255,255,255,0.04)',color:'rgba(255,255,255,0.78)',fontWeight:700}}>{r.final.toFixed(2)}억</td>
	                          <td style={{padding:'0.42rem 0.6rem',textAlign:'right',borderBottom:'1px solid rgba(255,255,255,0.04)',color:clrRet(r.profit),fontWeight:700}}>{r.profit >= 0 ? '+' : ''}{r.profit.toFixed(2)}억</td>
	                          <td style={{padding:'0.42rem 0.6rem',textAlign:'right',borderBottom:'1px solid rgba(255,255,255,0.04)',color:'rgba(255,255,255,0.58)'}}>{r.trades}</td>
	                          <td style={{padding:'0.42rem 0.6rem',textAlign:'right',borderBottom:'1px solid rgba(255,255,255,0.04)',color:'rgba(255,255,255,0.58)'}}>{r.win == null ? '-' : r.win.toFixed(1)+'%'}</td>
	                          <td style={{padding:'0.42rem 0.7rem',borderBottom:'1px solid rgba(255,255,255,0.04)',fontSize:'0.68rem',color:'rgba(255,255,255,0.5)',lineHeight:1.4}}>{r.note}</td>
	                        </tr>
	                      );
	                    })}
	                  </tbody>
	                </table>
	              </div>
	              <div style={{padding:'0.55rem 0.9rem',fontSize:'0.68rem',color:'rgba(255,255,255,0.36)',borderTop:'1px solid var(--glass-border)',lineHeight:1.55}}>
	                이 표는 종목당 1억원을 투입한 결과는 아니지만, 자산 증가에 따라 11번째·12번째 종목으로 확장되는 복리형 계좌 결과도 아닙니다. 현재 값은 시작자본 1억원을 1천만원 단위 최대 10종목으로 고정한 비교표입니다. 동적 슬롯 확장형 결과는 재백테스트 후 교체해야 합니다.
	              </div>
	            </div>
	          );
	        })()}

	        {hubTab === 'desc' && (
	          <BacktestView externalViewMode='desc' />
	        )}

	        {hubTab === 'ledger' && <ExperimentLedgerPanel />}

        {hubTab === 'data-lab' && (
          <div style={{display:'flex',flexDirection:'column',gap:'0.75rem'}}>
            <div className="glass-panel" style={{padding:'0.85rem 1rem',border:'1px solid rgba(45,212,191,0.28)'}}>
              <div style={{display:'flex',justifyContent:'space-between',gap:'0.75rem',alignItems:'center',flexWrap:'wrap'}}>
                <div>
                  <div style={{fontSize:'0.9rem',fontWeight:800,color:'#99f6e4'}}>데이터 역할 기반 전략 설계</div>
                  <div style={{fontSize:'0.7rem',lineHeight:1.55,color:'var(--text-secondary)',marginTop:3}}>
                    같은 데이터를 점수에 한 번 더 얹는 대신, 진입·확인·촉매·위험제거 역할로 나눠 조합합니다.
                  </div>
                </div>
                <span style={{fontSize:'0.68rem',color:'#fbbf24',padding:'0.25rem 0.5rem',borderRadius:5,border:'1px solid rgba(251,191,36,0.3)'}}>연구 후보: 자동매매 차단</span>
              </div>
              {strategyDataLab?.disclaimer && <div style={{fontSize:'0.66rem',lineHeight:1.5,color:'rgba(255,255,255,0.48)',marginTop:'0.65rem'}}>{strategyDataLab.disclaimer}</div>}
            </div>

            <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(220px,1fr))',gap:'0.6rem'}}>
              {(strategyDataLab?.sources || []).map(source => {
                const tones = { fresh:'#34d399', periodic:'#60a5fa', stale:'#fbbf24', unavailable:'#f87171' };
                const color = tones[source.freshness] || '#94a3b8';
                const freshnessLabel = { fresh:'최신', periodic:'분기 기준', stale:'갱신 필요', unavailable:'사용 불가' }[source.freshness] || source.freshness;
                return (
                  <div key={source.key} className="glass-panel" style={{padding:'0.7rem 0.75rem',border:`1px solid ${color}35`}}>
                    <div style={{display:'flex',justifyContent:'space-between',gap:'0.5rem',alignItems:'center'}}>
                      <strong style={{fontSize:'0.78rem',color:'#e2e8f0'}}>{source.label}</strong>
                      <span style={{fontSize:'0.62rem',color,padding:'0.13rem 0.36rem',border:`1px solid ${color}55`,borderRadius:4}}>{freshnessLabel}</span>
                    </div>
                    <div style={{fontSize:'0.68rem',color:'#99f6e4',marginTop:5}}>{source.role === 'entry' ? '진입 신호' : source.role === 'confirmation' ? '확인 신호' : source.role === 'risk_gate' ? '위험 제거' : '촉매'}</div>
                    <div style={{fontSize:'0.65rem',lineHeight:1.48,color:'var(--text-secondary)',marginTop:4}}>{source.description}</div>
                    <div style={{fontSize:'0.62rem',color:'rgba(255,255,255,0.42)',marginTop:6}}>종목 {Number(source.stocks || 0).toLocaleString()} · 기준 {source.as_of || '-'}</div>
                  </div>
                );
              })}
              {!strategyDataLab && <div className="glass-panel" style={{padding:'0.8rem',fontSize:'0.72rem',color:'var(--text-secondary)'}}>데이터 역할 정보를 불러오는 중입니다.</div>}
            </div>

            <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(260px,1fr))',gap:'0.6rem'}}>
              {(strategyDataLab?.strategies || []).map(strategy => (
                <div key={strategy.key} className="glass-panel" style={{padding:'0.8rem 0.9rem',border:'1px solid rgba(129,140,248,0.28)'}}>
                  <div style={{fontSize:'0.8rem',fontWeight:800,color:'#c7d2fe'}}>{strategy.label}</div>
                  <div style={{fontSize:'0.68rem',lineHeight:1.5,color:'rgba(255,255,255,0.68)',marginTop:6}}>진입: {strategy.entry}</div>
                  <div style={{fontSize:'0.68rem',lineHeight:1.5,color:'#fbbf24',marginTop:3}}>차단: {strategy.risk_gate}</div>
                  <div style={{fontSize:'0.63rem',lineHeight:1.45,color:'var(--text-secondary)',marginTop:6}}>{strategy.note}</div>
                </div>
              ))}
            </div>

            <div className="glass-panel" style={{padding:'0.85rem 0.95rem'}}>
              <div style={{fontSize:'0.82rem',fontWeight:800,color:'#a7f3d0',marginBottom:'0.6rem'}}>V-CATALYST 다중확인 후보</div>
              {(strategyDataLab?.candidates?.catalyst || []).length ? (
                <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(210px,1fr))',gap:'0.45rem'}}>
                  {strategyDataLab.candidates.catalyst.map(row => (
                    <button key={row.stock_code} onClick={() => changeStock(row.stock_code)} style={{textAlign:'left',padding:'0.55rem 0.65rem',borderRadius:6,cursor:'pointer',background:'rgba(255,255,255,0.025)',border:'1px solid rgba(255,255,255,0.08)',color:'inherit'}}>
                      <div style={{display:'flex',justifyContent:'space-between',gap:6}}><strong style={{fontSize:'0.73rem'}}>{row.stock_name}</strong><span style={{fontSize:'0.65rem',color:'#86efac'}}>점수 {row.score}</span></div>
                      <div style={{fontSize:'0.62rem',lineHeight:1.5,color:'var(--text-secondary)',marginTop:3}}>{(row.signals || []).join(' · ')}</div>
                    </button>
                  ))}
                </div>
              ) : <div style={{fontSize:'0.7rem',color:'var(--text-secondary)'}}>현재 다중확인 조건을 충족한 후보가 없습니다. 데이터가 부족한 경우에는 억지로 후보를 만들지 않습니다.</div>}
            </div>
          </div>
        )}

        {(hubTab === 'stocks' || (!hubTab)) && (
          <Screener
            key={sel.key}
            defaultTab={sel.screenTab || 'combo'}
            defaultComboLogic={sel.comboLogic || 'v1'}
            hideTabBar={true}
            changeStock={changeStock}
            changeTab={changeTab}
          />
        )}
      </div>
    );
  };

// 2026-07-28 신규: 전략센터 "🧪 검증 이력" 탭 — 사용자 지시("수익률 개선을 위해 검증했던
// 내용을 기록해달라, 오늘 시험한 건 효과 없다 등등, 각 로직의 의미와 효과를 표시할 것")에
// 따라 signal_experiment_ledger(가설검증 원장, /api/backtest/experiment-ledger)를 그대로
// 노출. module-level 컴포넌트(외부 state 의존 없음, closure 문제 방지).
const ExperimentLedgerPanel = () => {
  const [items, setItems] = React.useState([]);
  const [strategyKeys, setStrategyKeys] = React.useState([]);
  const [filterKey, setFilterKey] = React.useState('');
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState('');
  const [expanded, setExpanded] = React.useState(null);

  const load = React.useCallback((key) => {
    setLoading(true);
    setError('');
    const qs = key ? `?strategy_key=${encodeURIComponent(key)}` : '';
    fetch(API(`/api/backtest/experiment-ledger${qs}`))
      .then(r => r.json())
      .then(d => {
        setItems(d.items || []);
        setStrategyKeys(d.strategy_keys || []);
      })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  React.useEffect(() => { load(filterKey); }, [filterKey, load]);

  return (
    <div className="glass-panel" style={{padding:'1rem'}}>
      <div style={{marginBottom:'0.9rem'}}>
        <div style={{fontWeight:800,fontSize:'0.92rem',color:'#e2e8f0',marginBottom:'0.3rem'}}>
          🧪 수익률 개선 검증 이력
        </div>
        <div style={{fontSize:'0.75rem',color:'rgba(255,255,255,0.55)',lineHeight:1.6}}>
          전략센터에서 시도한 모든 가설·실험을 기록한 원장입니다. 채택된 것보다 <b>기각된 것이 훨씬 많습니다</b> —
          이는 정상입니다(대부분의 아이디어는 실전 검증을 통과하지 못합니다). "라벨정정(로직불변)"은 수치 표기 오류만
          바로잡은 것으로 실제 로직·수익률과는 무관합니다.
        </div>
      </div>
      <div style={{display:'flex',gap:'0.4rem',flexWrap:'wrap',marginBottom:'0.9rem'}}>
        <button onClick={() => setFilterKey('')}
          style={{padding:'0.3rem 0.7rem',borderRadius:'7px',fontSize:'0.72rem',cursor:'pointer',
            border: filterKey==='' ? '1px solid #f59e0b' : '1px solid var(--glass-border)',
            background: filterKey==='' ? 'rgba(245,158,11,0.15)' : 'transparent',
            color: filterKey==='' ? '#fbbf24' : 'rgba(255,255,255,0.7)'}}>
          전체
        </button>
        {strategyKeys.map(k => (
          <button key={k} onClick={() => setFilterKey(k)}
            style={{padding:'0.3rem 0.7rem',borderRadius:'7px',fontSize:'0.72rem',cursor:'pointer',
              border: filterKey===k ? '1px solid #f59e0b' : '1px solid var(--glass-border)',
              background: filterKey===k ? 'rgba(245,158,11,0.15)' : 'transparent',
              color: filterKey===k ? '#fbbf24' : 'rgba(255,255,255,0.7)'}}>
            {k}
          </button>
        ))}
      </div>
      {loading && <div style={{fontSize:'0.8rem',color:'rgba(255,255,255,0.5)'}}>불러오는 중...</div>}
      {error && <div style={{fontSize:'0.8rem',color:'#f87171'}}>오류: {error}</div>}
      {!loading && !error && items.length === 0 && (
        <div style={{fontSize:'0.8rem',color:'rgba(255,255,255,0.5)'}}>기록된 검증 이력이 없습니다.</div>
      )}
      <div style={{display:'flex',flexDirection:'column',gap:'0.5rem'}}>
        {items.map(it => {
          const isOpen = expanded === it.id;
          return (
            <div key={it.id} style={{
              border:'1px solid var(--glass-border)', borderRadius:'10px',
              padding:'0.7rem 0.85rem', cursor:'pointer',
              background: isOpen ? 'rgba(255,255,255,0.03)' : 'transparent',
            }} onClick={() => setExpanded(isOpen ? null : it.id)}>
              <div style={{display:'flex',alignItems:'center',gap:'0.55rem',flexWrap:'wrap'}}>
                <span style={{fontSize:'0.68rem',padding:'0.15rem 0.5rem',borderRadius:'999px',
                  background:`${it.color}22`, color:it.color, fontWeight:700, whiteSpace:'nowrap'}}>
                  {it.badge}
                </span>
                <span style={{fontSize:'0.72rem',color:'#94a3b8',fontWeight:600}}>{it.strategy_key}</span>
                <span style={{fontSize:'0.8rem',color:'#e2e8f0',fontWeight:600}}>{it.experiment_name}</span>
                {(it.baseline_avg6 != null || it.treatment_avg6 != null) && (
                  <span style={{fontSize:'0.72rem',color:'rgba(255,255,255,0.6)',marginLeft:'auto'}}>
                    {it.baseline_avg6 != null ? `${it.baseline_avg6}%(${it.baseline_pos ?? '?'}/6)` : '—'}
                    {' → '}
                    {it.treatment_avg6 != null ? `${it.treatment_avg6}%(${it.treatment_pos ?? '?'}/6)` : '—'}
                  </span>
                )}
                <span style={{fontSize:'0.7rem',color:'rgba(255,255,255,0.35)'}}>
                  {(it.tested_at || '').slice(0, 10)}
                </span>
                <span style={{fontSize:'0.7rem',color:'rgba(255,255,255,0.4)'}}>{isOpen ? '▲' : '▼'}</span>
              </div>
              {isOpen && (
                <div style={{marginTop:'0.55rem',fontSize:'0.78rem',lineHeight:1.65,color:'rgba(255,255,255,0.78)'}}>
                  <div style={{marginBottom:'0.4rem'}}>
                    <b style={{color:'#facc15'}}>검증한 내용(가설): </b>{it.hypothesis || '(기록 없음)'}
                  </div>
                  <div>
                    <b style={{color:'#34d399'}}>결과·효과: </b>{it.detail || '(기록 없음)'}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default StrategyHub;
