/**
 * StrategyCenterView.jsx
 * 전략센터 / 가상매매 (구 PeakView) — App.jsx에서 분리 (2026-09-03, 토큰 최적화)
 * 원본은 App.jsx 12434~13380줄에 인라인으로 정의되어 있던 컴포넌트를 그대로 이관.
 * 로직/JSX는 변경 없음 — import 경로만 module-level 의존성(API, isKRMarketOpen)을
 * ../utils에서 가져오도록 정리했다.
 *
 * [2026-09-04 수정] 통합 가상매매 요약 카드/US 포지션 표시에서 `fmtPctUs(...)`를 호출하는데,
 * fmtPctUs는 원래 App.jsx의 USStocksView 컴포넌트에 지역 선언된 함수라 PeakView와는
 * 형제 스코프 — 분리 전부터 참조 불가능했다(ReferenceError, combinedVirtual 존재 시에만 발현).
 * App.jsx와 동일한 구현을 `frontend/src/utils.js`의 공용 헬퍼로 옮기고 여기서 import.
 */
import React from 'react';
import { TrendingUp } from 'lucide-react';
import { API, isKRMarketOpen, fmtPctUs } from '../utils';

  const PeakView = ({ changeStock, changeTab }) => {
    const [peakData, setPeakData]     = React.useState({ holdings: [], exits: [] });
    const [summary,  setSummary]      = React.useState(null);
    const [trades,   setTrades]       = React.useState([]);
    const [peakTab,  setPeakTab]      = React.useState('holdings');
    const [loading,  setPeakLoading]  = React.useState(true);
    const [lastSync, setLastSync]     = React.useState('');
    // 첫 진입은 전체 장부로 고정한다. 개별 전략의 보유 0건 때문에 가상매매 전체가 빈 것으로
    // 오인하지 않도록, 실제 보유·매도 이력을 한 화면에서 먼저 확인할 수 있게 한다.
    const [strategy, setStrategy]     = React.useState('all');
    const [virtualPerformance, setVirtualPerformance] = React.useState({});
    const [strategyCenterStrategies, setStrategyCenterStrategies] = React.useState([]);

    // AI 추천 탭 전용 state — 최상위에 위치해야 hooks 규칙 준수
    const [aiHoldings, setAiHoldings] = React.useState([]);
    const [aiLoading,  setAiLoading]  = React.useState(false);
    const [aiSubTab,   setAiSubTab]   = React.useState('holdings'); // AI탭 서브탭
    const [v18Data, setV18Data] = React.useState(null);
    const [turnoverData, setTurnoverData] = React.useState(null);
    const [turnoverAuto, setTurnoverAuto] = React.useState(null);
    // 2026-07-23: 전략센터 병합조합 4종 가상매매 (각 1억원 시드)
    const [comboData, setComboData] = React.useState(null);
    const [comboExecuting, setComboExecuting] = React.useState(false);
    const [usPaper, setUsPaper] = React.useState(null);
    const [usCandidates, setUsCandidates] = React.useState(null);
    const [combinedVirtual, setCombinedVirtual] = React.useState(null);
    const [usExecuting, setUsExecuting] = React.useState(false);
    const [usMsg, setUsMsg] = React.useState('');

    const loadPeak = async () => {
      setPeakLoading(true);
      try {
        const [hRes, tRes, sRes, pRes, scRes] = await Promise.all([
          fetch(API('/api/trend/holdings')),
          fetch(API('/api/trend/trades')),
          fetch(API('/api/trend/summary')),
          fetch(API('/api/trend/performance')),
          fetch(API('/api/trend/strategy-center/top-five')),
        ]);
        const all    = hRes.ok ? await hRes.json() : [];
        const active = all.filter(h => h.is_active);
        const exited = all.filter(h => !h.is_active);
        setPeakData({ holdings: active, exits: exited });
        if (tRes.ok) setTrades(await tRes.json());
        if (sRes.ok) setSummary(await sRes.json());
        if (pRes.ok) {
          const performance = await pRes.json();
          setVirtualPerformance(performance?.strategies || {});
        }
        if (scRes.ok) {
          const strategyCenter = await scRes.json();
          const selected = (strategyCenter?.strategies || []).map((item, index) => ({
            key: item.strategy,
            label: `${item.rank}. ${item.label}`,
            color: ['#facc15', '#38bdf8', '#c084fc', '#94a3b8', '#2dd4bf'][index] || '#a78bfa',
            rank: item.rank,
            averageReturn: item.average_return_pct,
            tier: item.tier,
            performance: item.performance || {},
          }));
          setStrategyCenterStrategies(selected);
          // 전략센터 후보는 별도 sc_* 장부다. 기존 Stock Easy/독립 가상계좌 탭을
          // 전략센터 첫 후보로 덮어쓰면 보유 0건인 화면만 보이므로 현재 선택을 유지한다.
          setStrategy(current => current || 'all');
        }
        setLastSync(new Date().toLocaleTimeString('ko-KR'));
      } catch(e) { console.error(e); }
      finally { setPeakLoading(false); }
    };

    const loadAiHoldings = () => {
      setAiLoading(true);
      fetch(API('/api/trend/ai-holdings'))
        .then(r => r.ok ? r.json() : [])
        .then(d => setAiHoldings(d))
        .catch(() => {})
        .finally(() => setAiLoading(false));
    };

    React.useEffect(() => {
      loadPeak();
      // 2026-07-23: ai_rec/turnover 전략 삭제로 loadAiHoldings/loadTurnover(Auto) 상시호출 제거 (죽은 엔드포인트 폴링 방지)
      // 장중에만 10분 폴링 (장외·주말엔 데이터 변화 없음)
      const iv = isKRMarketOpen() ? setInterval(loadPeak, 600000) : null;
      return () => { if (iv) clearInterval(iv); };
    }, []);

    const loadV18 = async () => {
      try {
        const r = await fetch(API('/api/trend/v18/recommendations'));
        if (r.ok) setV18Data(await r.json());
      } catch (e) { console.error(e); }
    };
    const loadTurnover = async () => {
      try {
        const r = await fetch(API('/api/trend/turnover/recommendations'));
        if (r.ok) setTurnoverData(await r.json());
      } catch (e) { console.error(e); }
    };
    const loadTurnoverAuto = async () => {
      try {
        const r = await fetch(API('/api/trend/turnover/auto/status'));
        if (r.ok) setTurnoverAuto(await r.json());
      } catch (e) { console.error(e); }
    };
    const loadCombo = async (key) => {
      try {
        const r = await fetch(API(`/api/trend/combo/${key}/status`));
        if (r.ok) setComboData(await r.json());
      } catch (e) { console.error(e); }
    };
    const executeCombo = async (key) => {
      setComboExecuting(true);
      try {
        await fetch(API(`/api/trend/combo/${key}/execute`), { method: 'POST' });
        await Promise.all([loadPeak(), loadCombo(key)]);
      } catch (e) { console.error(e); }
      finally { setComboExecuting(false); }
    };
    const loadUsVirtual = async () => {
      try {
        const [pRes, cRes, oRes, mRes] = await Promise.all([
          fetch(API('/api/us-virtual/positions')),
          fetch(API('/api/us-virtual/candidates?limit=20')),
          fetch(API('/api/us-virtual/orders?limit=30')),
          fetch(API('/api/us-virtual/combined-summary')),
        ]);
        const next = {};
        if (pRes.ok) Object.assign(next, await pRes.json());
        if (oRes.ok) next.orders = await oRes.json();
        setUsPaper(next);
        if (cRes.ok) setUsCandidates(await cRes.json());
        if (mRes.ok) setCombinedVirtual(await mRes.json());
      } catch (e) { console.error(e); }
    };
    const executeUsCandidates = async () => {
      setUsExecuting(true);
      setUsMsg('');
      try {
        const r = await fetch(API('/api/us-virtual/execute-candidates?limit=5&allocation_usd=10000'), { method: 'POST' });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.detail || `HTTP ${r.status}`);
        setUsMsg(`미국 후보 ${d.executed_count || 0}종목 가상매수 완료`);
        await loadUsVirtual();
      } catch (e) {
        setUsMsg(`미국 가상매매 실패: ${e?.message || e}`);
      } finally {
        setUsExecuting(false);
      }
    };
    React.useEffect(() => {
      if (strategy.startsWith('combo_')) loadCombo(strategy);
      if (strategy === 'us_virtual') loadUsVirtual();
    }, [strategy]);

    const fp = (v) => v != null ? Math.round(v).toLocaleString('ko-KR') : '-';
    const money = (v, currency='KRW') => {
      if (v == null) return '-';
      const n = Number(v);
      if (!Number.isFinite(n)) return '-';
      if (currency === 'USD') return `$${n.toLocaleString('en-US', { maximumFractionDigits: 2 })}`;
      return `${Math.round(n).toLocaleString('ko-KR')}원`;
    };
    const pc = (v) => v > 0 ? '#ef4444' : v < 0 ? '#3b82f6' : 'rgba(255,255,255,0.35)';
    const pf = (v) => v == null ? '-' : (v >= 0 ? '+' : '') + fp(v);
    const compactWon = (v) => {
      if (v == null) return '-';
      const n = Number(v);
      if (!Number.isFinite(n)) return '-';
      const sign = n < 0 ? '-' : '';
      const a = Math.abs(n);
      if (a >= 1e12) return `${sign}${(a / 1e12).toFixed(2)}조`;
      if (a >= 1e8) return `${sign}${(a / 1e8).toFixed(1)}억`;
      return `${sign}${Math.round(a).toLocaleString('ko-KR')}원`;
    };

    const tabBtn = (key, label, count) => (
      <button key={key} onClick={() => setPeakTab(key)} style={{
        padding: '0.35rem 0.9rem', borderRadius: '7px', fontSize: '0.82rem',
        cursor: 'pointer', fontWeight: peakTab === key ? 700 : 400,
        border:     peakTab === key ? '1px solid var(--accent-purple)' : '1px solid var(--glass-border)',
        background: peakTab === key ? 'rgba(167,139,250,0.15)' : 'transparent',
        color:      peakTab === key ? 'var(--accent-purple)' : 'var(--text-secondary)',
        display: 'flex', alignItems: 'center', gap: '0.4rem',
      }}>
        {label}
        {count != null && (
          <span style={{ fontSize: '0.7rem', padding: '0.05rem 0.4rem',
            background: 'rgba(167,139,250,0.2)', borderRadius: '10px' }}>{count}</span>
        )}
      </button>
    );

    // 현재 전략에 해당하는 보유/이탈 종목만 필터링
    // ai_rec 탭 선택 시 → AI 탭 전용 (ai_combo strategy)
    // 그 외 전략 → 해당 strategy 종목만 표시 (ai_combo 제외)
    const baseHoldings = strategy === 'us_virtual'
      ? []
      : strategy === 'all' ? peakData.holdings : peakData.holdings.filter(h => h.strategy === strategy);
    const curExits = strategy === 'us_virtual'
      ? []
      : strategy === 'all' ? peakData.exits : peakData.exits.filter(h => h.strategy === strategy);
    const curTrades = strategy === 'all' ? trades : trades.filter(t => t.strategy === strategy);

    const strategyLabel = (key) => ({
      peak: 'Peak Easy', momentum: '모멘텀 Easy', value: '벨류 Easy',
      v_gc: 'V12 골든크로스', v_recovery: 'V-RECOVERY',
      v_contract_momentum: 'V-수주모멘텀', combo_605: '전략조합 605%',
      combo_546: '전략조합 546%', combo_539: '전략조합 539%', combo_510: '전략조합 510%',
      ai_combo: 'AI 조합', gpt_v18: 'GPT V18', turnover_100m: '거래대금 1억',
    }[key] || key);
    const holdingKey = (h) => h.stock_code && h.stock_code !== 'None'
      ? `code:${h.stock_code}` : `name:${h.stock_name}`;
    // 전체 장부에서 같은 종목이 동시에 보유된 모든 전략을 한 번에 보여 준다.
    const activeStrategyMap = peakData.holdings.reduce((map, h) => {
      const key = holdingKey(h);
      if (!map[key]) map[key] = new Set();
      map[key].add(h.strategy);
      return map;
    }, {});
    const holdingStrategies = (h) => [...(activeStrategyMap[holdingKey(h)] || new Set([h.strategy]))];
    // 전체 장부는 여러 전략이 동시에 선택한 종목을 먼저 보여 준다.
    const curHoldings = strategy === 'all'
      ? [...baseHoldings].sort((a, b) => {
          const strategyGap = holdingStrategies(b).length - holdingStrategies(a).length;
          if (strategyGap) return strategyGap;
          return String(b.entry_date || '').localeCompare(String(a.entry_date || ''));
        })
      : baseHoldings;

    // 요약 카드 — 현재 전략 기준 집계
    const SummaryCards = () => {
      if (strategy === 'us_virtual') {
        const s = usPaper?.summary || {};
        return (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', gap: '0.75rem', marginBottom: '1rem' }}>
            {[
              { label: 'USD 현금', val: money(usPaper?.cash_usd, 'USD'), color: 'inherit' },
              { label: '보유 평가액', val: money(s.total_eval_usd, 'USD'), color: 'inherit' },
              { label: '총자산', val: money(s.equity_usd, 'USD'), color: '#93c5fd' },
              { label: '평가 손익', val: money(s.unrealized_pnl_usd, 'USD'), color: pc(s.unrealized_pnl_usd || 0) },
              { label: '실현 손익', val: money(s.realized_pnl_usd, 'USD'), color: pc(s.realized_pnl_usd || 0) },
              { label: '보유 종목', val: `${s.position_count || 0}종목`, color: 'var(--accent-purple)' },
            ].map(({ label, val, color }) => (
              <div key={label} className="glass-panel" style={{ padding: '0.9rem 1rem', minWidth: 0 }}>
                <p style={{ fontSize: '0.7rem', color: 'var(--text-secondary)', marginBottom: '0.3rem', whiteSpace:'nowrap' }}>{label}</p>
                <p style={{ fontSize: '0.9rem', fontWeight: 700, color, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>{val}</p>
              </div>
            ))}
          </div>
        );
      }
      const realProfit  = curExits.reduce((s,h)=>s+(h.profit||0),0);
      const wins        = curExits.filter(h=>(h.profit||0)>0).length;
      const winRate     = curExits.length > 0 ? Math.round(wins/curExits.length*100) : null;
      const winTrades   = curExits.filter(h=>(h.profit||0)>0);
      const lossTrades  = curExits.filter(h=>(h.profit||0)<0);
      const avgWin      = winTrades.length ? winTrades.reduce((s,h)=>s+(h.profit||0),0)/winTrades.length : 0;
      const avgLoss     = lossTrades.length ? Math.abs(lossTrades.reduce((s,h)=>s+(h.profit||0),0)/lossTrades.length) : 0;
      const plRatio     = (winTrades.length && lossTrades.length && avgLoss>0) ? (avgWin/avgLoss) : null;
      const totalValue  = curHoldings.reduce((s,h)=>s+(h.total_value||(h.buy_price||0)*(h.quantity||0)),0);
      const totalProfit = curHoldings.reduce((s,h)=>s+(h.profit||0),0);
      const costBasis   = totalValue - totalProfit;
      const roi         = costBasis > 0 ? (totalProfit / costBasis * 100) : null;
      const currencies   = [...new Set(curHoldings.map(h => h.currency || 'KRW'))];
      const currency     = currencies.length === 0 ? 'KRW' : (currencies.length === 1 ? currencies[0] : 'MIXED');
      const mixedNote    = currency === 'MIXED';
      return (
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(120px, 1fr))', gap: '0.75rem', marginBottom: '1rem' }}>
        {[
          { label: '투입원금',      val: mixedNote ? '통화혼합' : money(costBasis, currency), color: 'inherit' },
          { label: '보유 총액',     val: mixedNote ? '통화혼합' : money(totalValue, currency), color: 'inherit' },
          { label: '평가 손익',     val: mixedNote ? '통화혼합' : money(totalProfit, currency), color: pc(totalProfit) },
          { label: '수익률',        val: roi != null ? (roi>=0?'+':'')+roi.toFixed(1)+'%' : '-', color: pc(roi||0) },
          { label: '누적 실현 손익', val: pf(realProfit)+'원',                          color: pc(realProfit||0) },
          { label: '승률',          val: winRate != null ? `${winRate}%` : '-',         color: 'var(--accent-purple)' },
          { label: '손익비',        val: plRatio != null ? `${plRatio.toFixed(2)}` : '-', color: 'var(--accent-purple)',
            title: plRatio != null ? `평균 수익 ${pf(avgWin)}원 ÷ 평균 손실 ${pf(avgLoss)}원 (1.0 초과 = 이길 때 잃을 때보다 크게 번다)` : '완결된 승/패 거래가 모두 있어야 계산됩니다' },
        ].map(({ label, val, color, title }) => (
          <div key={label} className="glass-panel" style={{ padding: '0.9rem 1rem', minWidth: 0 }} title={title}>
            <p style={{ fontSize: '0.7rem', color: 'var(--text-secondary)', marginBottom: '0.3rem', whiteSpace:'nowrap' }}>{label}</p>
            <p style={{ fontSize: '0.9rem', fontWeight: 700, color, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>{val}</p>
          </div>
        ))}
      </div>
      );
    };

    const CORE_STRATEGIES = [
      { key:'v_gc', label:'V12 골든크로스', color:'#f97316' },
      { key:'v_recovery', label:'V-RECOVERY', color:'#22c55e' },
      { key:'v_contract_momentum', label:'V-수주모멘텀', color:'#38bdf8' },
      { key:'combo_605', label:'전략조합 605%', color:'#fbbf24' },
    ];
    const STRATEGIES = [
      // 첫 화면에서 실제 가상계좌 전체를 보여 준다. 개별 전략은 아래 탭에서 계속 분리 확인한다.
      { key:'all',      label:'전체 가상계좌', color:'#e2e8f0' },
      // 기존 Stock Easy 가상매매는 전략센터 상위 5개와 별도로 계속 운용·표시한다.
      { key:'peak',     label:'Peak Easy',    color:'#a78bfa' },
      { key:'momentum', label:'모멘텀 Easy',  color:'#34d399' },
      { key:'value',    label:'벨류 Easy',    color:'#60a5fa' },
      ...CORE_STRATEGIES,
      // 전략센터 순위는 백테스트 검증 정보다. 실제 가상계좌가 아닌 sc_* 항목을
      // 포트폴리오 탭으로 노출하면 매도/보유 0건의 빈 화면으로 혼동되므로 아래 안내 배지로만 보여 준다.
      { key:'us_virtual', label:'미국 가상매매', color:'#93c5fd' },
    ];

    return (
      <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        {/* 안내 배너 */}
        <div style={{padding:'0.4rem 0.9rem',background:'rgba(251,191,36,0.07)',
          border:'1px solid rgba(251,191,36,0.25)',borderRadius:'8px',
          fontSize:'0.7rem',color:'rgba(251,191,36,0.85)',lineHeight:1.4}}>
          클릭 가능한 탭은 실제 가상계좌입니다. 전략센터 순위는 아래 정보 배지로만 제공하며, 종목이나 별도 보유계좌가 아닙니다.
        </div>
        {/* 헤더 */}
        <div className="glass-panel" style={{ padding: '1rem 1.4rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap:'wrap', gap:'0.5rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
            {STRATEGIES.map(s => {
              const isActive = strategy === s.key;
              const performance = s.performance || virtualPerformance[s.key];
              return (
                <button key={s.key} onClick={() => {
                  setStrategy(s.key);
                  // 전략 전환 시 보유종목 탭으로 초기화
                  if (peakTab === 'history') setPeakTab('holdings');
                }} style={{
                  padding: '0.35rem 0.9rem', borderRadius: '7px', fontSize: '0.82rem', cursor: 'pointer',
                  fontWeight: isActive ? 700 : 500,
                  border:     `1px solid ${s.color}${isActive ? 'cc' : '55'}`,
                  background: isActive ? `${s.color}33` : `${s.color}11`,
                  color:      isActive ? '#ffffff' : s.color,
                }}>
                  {s.label}
                  {s.key.startsWith('sc_') && s.averageReturn != null && (
                    <span style={{marginLeft:'0.35rem', color: s.averageReturn >= 0 ? '#fca5a5' : '#93c5fd'}}>
                      BT {s.averageReturn >= 0 ? '+' : ''}{s.averageReturn.toFixed(1)}%
                    </span>
                  )}
                  {s.key.startsWith('combo_') && performance?.return_pct != null && (
                    <span style={{marginLeft:'0.35rem', color: performance.return_pct >= 0 ? '#fca5a5' : '#93c5fd'}}>
                      {performance.return_pct >= 0 ? '+' : ''}{performance.return_pct.toFixed(1)}%
                    </span>
                  )}
                </button>
              );
            })}
          </div>
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap:'wrap' }}>
            {strategy === 'us_virtual' ? (
              <>
                {tabBtn('holdings', '미국 장부', usPaper?.positions?.length || 0)}
                {tabBtn('history',  '후보/주문 내역', usPaper?.orders?.length || 0)}
              </>
            ) : (
              <>
                {tabBtn('holdings', '보유 종목', curHoldings.length)}
                {tabBtn('exits',    '이탈 종목', curExits.length)}
                {tabBtn('history',  '매매 내역', null)}
              </>
            )}
          </div>
          <div style={{ display:'flex', alignItems:'center', gap:'0.5rem' }}>
            <span style={{ fontSize: '0.7rem', color: 'var(--text-secondary)' }}>마지막: {lastSync || '로딩중...'}</span>
            <button onClick={loadPeak} style={{
              padding: '0.35rem 0.9rem', borderRadius: '7px', fontSize: '0.8rem',
              background: 'rgba(167,139,250,0.15)', border: '1px solid rgba(167,139,250,0.35)',
              color: 'var(--accent-purple)', cursor: 'pointer',
            }}>새로고침</button>
          </div>
        </div>
        {strategyCenterStrategies.length > 0 && (
          <div style={{ display:'flex', alignItems:'center', gap:'0.4rem', flexWrap:'wrap', marginTop:'-0.5rem', fontSize:'0.7rem', color:'var(--text-secondary)' }}>
            <span>전략센터 검증 순위 (정보용):</span>
            {strategyCenterStrategies.map(item => (
              <span key={item.key} style={{ padding:'0.12rem 0.4rem', borderRadius:'10px', background:'rgba(148,163,184,0.1)', color:'#cbd5e1' }}>
                {item.label}
              </span>
            ))}
          </div>
        )}
        {strategy.startsWith('sc_') && (() => {
          const selected = strategyCenterStrategies.find(item => item.key === strategy);
          const perf = selected?.performance || {};
          return (
            <div style={{fontSize:'0.72rem',color: perf.last_run_status === 'error' ? '#fca5a5' : 'var(--text-secondary)', marginTop:'-0.55rem'}}>
              전략센터 순위 {selected?.rank || '-'}위 · 백테스트 평균수익률 {selected?.averageReturn ?? '-'}% · 가상계좌 수익률 {perf.return_pct == null ? '-' : `${perf.return_pct >= 0 ? '+' : ''}${perf.return_pct.toFixed(2)}%`} · 최종 실행 {perf.last_run_at || '대기 중'}
              {perf.last_run_status === 'error'
                ? ` · 실행 실패: ${perf.last_message || '원인 확인 필요'}`
                : ` · 편입 ${perf.last_bought || 0} / 편출 ${perf.last_sold || 0}`}
            </div>
          );
        })()}
        {/* 요약 카드 */}
        <SummaryCards />

        {strategy === 'us_virtual' && combinedVirtual && (
          <div className="glass-panel" style={{padding:'0.85rem 1rem', border:'1px solid rgba(147,197,253,0.22)', background:'rgba(15,23,42,0.76)'}}>
            <div style={{display:'flex', justifyContent:'space-between', alignItems:'center', gap:'0.8rem', flexWrap:'wrap', marginBottom:'0.65rem'}}>
              <div>
                <div style={{fontSize:'0.95rem', fontWeight:900, color:'#93c5fd'}}>🌐 통합 가상매매 총괄</div>
                <div style={{fontSize:'0.72rem', color:'var(--text-secondary)', marginTop:'0.16rem'}}>
                  국내 가상보유 + 미국 USD 장부를 원화로 환산한 요약입니다.
                </div>
              </div>
              <div style={{fontSize:'0.72rem', color:'var(--text-secondary)'}}>
                USD/KRW {Number(combinedVirtual.fx?.rate || 0).toLocaleString('ko-KR', {maximumFractionDigits:2})} · {combinedVirtual.fx?.date || '-'} · {combinedVirtual.fx?.source || '-'}
              </div>
            </div>
            <div style={{display:'grid', gridTemplateColumns:'repeat(auto-fit, minmax(150px, 1fr))', gap:'0.65rem'}}>
              {[
                {label:'통합 총자산', val:compactWon(combinedVirtual.total?.equity_krw), sub:`${combinedVirtual.total?.position_count || 0}종목`, color:'#e5e7eb'},
                {label:'통합 평가손익', val:compactWon(combinedVirtual.total?.unrealized_pnl_krw), sub:`${fmtPctUs(combinedVirtual.total?.unrealized_pct_on_cost)}`, color:pc(combinedVirtual.total?.unrealized_pnl_krw || 0)},
                {label:'국내 평가액', val:compactWon(combinedVirtual.kr?.eval_krw), sub:`${combinedVirtual.kr?.position_count || 0}종목 · 가격누락 ${combinedVirtual.kr?.stale_price_count || 0}`, color:'#facc15'},
                {label:'미국 총자산', val:compactWon(combinedVirtual.us?.equity_krw), sub:`${money(combinedVirtual.us?.equity_usd, 'USD')} · 현금 ${money(combinedVirtual.us?.cash_usd, 'USD')}`, color:'#93c5fd'},
              ].map(card => (
                <div key={card.label} style={{padding:'0.7rem 0.8rem', border:'1px solid rgba(148,163,184,0.18)', background:'rgba(2,6,23,0.28)', borderRadius:'7px'}}>
                  <div style={{fontSize:'0.68rem', color:'var(--text-secondary)', marginBottom:'0.22rem'}}>{card.label}</div>
                  <div style={{fontSize:'1rem', fontWeight:900, color:card.color}}>{card.val}</div>
                  <div style={{fontSize:'0.68rem', color:'var(--text-secondary)', marginTop:'0.18rem'}}>{card.sub}</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {loading && (
          <div className="glass-panel" style={{ padding: '2rem', textAlign: 'center', color: 'var(--accent-purple)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.6rem' }}>
              <div style={{ width: '14px', height: '14px', borderRadius: '50%', border: '2px solid var(--accent-purple)', borderTopColor: 'transparent', animation: 'spin 0.8s linear infinite' }} />
              Peak 데이터 로딩 중...
            </div>
          </div>
        )}

        {!loading && strategy === 'us_virtual' && (
          <div style={{display:'flex', flexDirection:'column', gap:'0.8rem'}}>
            <div className="glass-panel" style={{padding:'0.85rem 1rem', border:'1px solid rgba(147,197,253,0.24)', background:'rgba(15,23,42,0.72)'}}>
              <div style={{display:'flex', justifyContent:'space-between', alignItems:'center', gap:'0.6rem', flexWrap:'wrap'}}>
                <div>
                  <div style={{fontSize:'0.95rem', fontWeight:900, color:'#93c5fd'}}>🇺🇸 미국 주식 가상매매</div>
                  <div style={{fontSize:'0.72rem', color:'var(--text-secondary)', marginTop:'0.18rem'}}>
                    미국 후보군을 별도 USD 장부로 운용하고, 위 통합 총괄에서 USD/KRW 기준으로 국내 가상매매와 합산합니다.
                  </div>
                </div>
                <div style={{display:'flex', gap:'0.45rem', alignItems:'center', flexWrap:'wrap'}}>
                  <button onClick={loadUsVirtual} style={{
                    padding:'0.34rem 0.75rem', borderRadius:'7px', cursor:'pointer',
                    border:'1px solid rgba(147,197,253,0.35)', background:'rgba(147,197,253,0.1)',
                    color:'#93c5fd', fontSize:'0.74rem', fontWeight:800
                  }}>새로고침</button>
                  <button disabled={usExecuting} onClick={executeUsCandidates} style={{
                    padding:'0.34rem 0.75rem', borderRadius:'7px', cursor:usExecuting?'wait':'pointer',
                    border:'1px solid rgba(34,197,94,0.36)', background:'rgba(34,197,94,0.14)',
                    color:'#4ade80', fontSize:'0.74rem', fontWeight:800, opacity:usExecuting?0.6:1
                  }}>{usExecuting ? '매수 실행 중...' : '상위 후보 5종목 가상매수'}</button>
                </div>
              </div>
              {usMsg && (
                <div style={{marginTop:'0.55rem', fontSize:'0.74rem', color:usMsg.includes('완료')?'#4ade80':'#fbbf24'}}>
                  {usMsg}
                </div>
              )}
            </div>

            <div className="glass-panel" style={{overflow:'clip'}}>
              <div style={{padding:'0.65rem 1rem', borderBottom:'1px solid var(--glass-border)', display:'flex', justifyContent:'space-between', gap:'0.6rem', flexWrap:'wrap'}}>
                <span style={{fontWeight:800, color:'#93c5fd'}}>보유 종목</span>
                <span style={{fontSize:'0.72rem', color:'var(--text-secondary)'}}>기준 통화 USD · 현금 {money(usPaper?.cash_usd, 'USD')}</span>
              </div>
              {(usPaper?.positions || []).length === 0 ? (
                <div style={{padding:'2rem', textAlign:'center', color:'var(--text-secondary)', fontSize:'0.82rem'}}>
                  현재 미국 가상 보유종목이 없습니다. 상위 후보 실행 버튼으로 테스트 포지션을 편입할 수 있습니다.
                </div>
              ) : (
                <table className="premium-table">
                  <thead><tr>
                    <th>티커</th><th>종목명</th><th>섹터</th>
                    <th style={{textAlign:'right'}}>수량</th>
                    <th style={{textAlign:'right'}}>평균가</th>
                    <th style={{textAlign:'right'}}>현재가</th>
                    <th style={{textAlign:'right'}}>평가액</th>
                    <th style={{textAlign:'right'}}>손익</th>
                    <th style={{textAlign:'right'}}>기준일</th>
                  </tr></thead>
                  <tbody>
                    {(usPaper?.positions || []).map(p => (
                      <tr key={p.ticker}>
                        <td style={{fontWeight:900, color:'#67e8f9'}}>{p.ticker}</td>
                        <td style={{fontWeight:700}}>{p.name || p.ticker}</td>
                        <td style={{color:'var(--text-secondary)', fontSize:'0.74rem'}}>{p.sector || '-'}</td>
                        <td style={{textAlign:'right'}}>{Number(p.qty || 0).toLocaleString('en-US')}</td>
                        <td style={{textAlign:'right'}}>{money(p.avg_price, 'USD')}</td>
                        <td style={{textAlign:'right'}}>{money(p.current_price, 'USD')}</td>
                        <td style={{textAlign:'right'}}>{money(p.market_value_usd, 'USD')}</td>
                        <td style={{textAlign:'right', color:pc(p.unrealized_pnl_usd || 0), fontWeight:800}}>
                          {money(p.unrealized_pnl_usd, 'USD')} · {fmtPctUs(p.unrealized_pct)}
                        </td>
                        <td style={{textAlign:'right', color:'var(--text-secondary)', fontSize:'0.72rem'}}>{p.price_as_of || '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>

            <div className="glass-panel" style={{overflow:'clip'}}>
              <div style={{padding:'0.65rem 1rem', borderBottom:'1px solid var(--glass-border)', display:'flex', justifyContent:'space-between', gap:'0.6rem', flexWrap:'wrap'}}>
                <span style={{fontWeight:800, color:'#4ade80'}}>매수 후보</span>
                <span style={{fontSize:'0.72rem', color:'var(--text-secondary)'}}>기준일 {usCandidates?.as_of || '-'}</span>
              </div>
              <table className="premium-table">
                <thead><tr>
                  <th>티커</th><th>종목명</th><th>섹터</th>
                  <th style={{textAlign:'right'}}>현재가</th>
                  <th style={{textAlign:'right'}}>시총</th>
                  <th style={{textAlign:'right'}}>3M</th>
                  <th style={{textAlign:'right'}}>RS</th>
                  <th style={{textAlign:'right'}}>점수</th>
                </tr></thead>
                <tbody>
                  {(usCandidates?.candidates || []).slice(0, 20).map(c => (
                    <tr key={c.ticker}>
                      <td style={{fontWeight:900, color:'#67e8f9'}}>{c.ticker}</td>
                      <td style={{fontWeight:700}}>{c.name || c.ticker}</td>
                      <td style={{color:'var(--text-secondary)', fontSize:'0.74rem'}}>{c.sector || '-'}</td>
                      <td style={{textAlign:'right'}}>{money(c.price, 'USD')}</td>
                      <td style={{textAlign:'right'}}>{money(c.market_cap, 'USD')}</td>
                      <td style={{textAlign:'right', color:pc(c.return_3m || 0), fontWeight:700}}>{fmtPctUs(c.return_3m)}</td>
                      <td style={{textAlign:'right', color:pc(c.rs_score || 0), fontWeight:700}}>{fmtPctUs(c.rs_score)}</td>
                      <td style={{textAlign:'right', color:'#93c5fd', fontWeight:800}}>{c.total_score != null ? Number(c.total_score).toFixed(1) : '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="glass-panel" style={{overflow:'clip'}}>
              <div style={{padding:'0.65rem 1rem', borderBottom:'1px solid var(--glass-border)', fontWeight:800, color:'var(--text-secondary)'}}>최근 주문</div>
              <table className="premium-table">
                <thead><tr>
                  <th>시각</th><th>티커</th><th>구분</th>
                  <th style={{textAlign:'right'}}>수량</th>
                  <th style={{textAlign:'right'}}>체결가</th>
                  <th style={{textAlign:'right'}}>금액</th>
                  <th style={{textAlign:'right'}}>가격일</th>
                </tr></thead>
                <tbody>
                  {(usPaper?.orders || []).slice(0, 20).map(o => (
                    <tr key={o.id}>
                      <td style={{color:'var(--text-secondary)', fontSize:'0.72rem'}}>{o.ts || '-'}</td>
                      <td style={{fontWeight:900, color:'#67e8f9'}}>{o.ticker}</td>
                      <td style={{color:o.side === 'buy' ? '#ef4444' : '#3b82f6', fontWeight:800}}>{o.side === 'buy' ? '매수' : '매도'}</td>
                      <td style={{textAlign:'right'}}>{Number(o.qty || 0).toLocaleString('en-US')}</td>
                      <td style={{textAlign:'right'}}>{money(o.fill_price, 'USD')}</td>
                      <td style={{textAlign:'right'}}>{money(o.amount_usd, 'USD')}</td>
                      <td style={{textAlign:'right', color:'var(--text-secondary)', fontSize:'0.72rem'}}>{o.price_as_of || '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* ══ 보유 종목 탭 ══ */}
        {!loading && strategy !== 'us_virtual' && peakTab === 'holdings' && (
          curHoldings.length === 0 ? (
            <div className="glass-panel" style={{ padding: '3rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
              <TrendingUp size={40} style={{ margin: '0 auto 1rem', display: 'block', opacity: 0.3 }} />
              <p style={{ fontSize: '1rem', fontWeight: 600, color: 'rgba(255,255,255,0.5)' }}>{strategy.startsWith('sc_') ? '현재 전략센터 선택 로직의 보유 종목이 없습니다.' : '현재 Stock Easy 전략의 보유 종목이 없습니다.'}</p>
              <p style={{ fontSize: '0.8rem', marginTop: '0.6rem' }}>{strategy.startsWith('sc_') ? '다음 평일 일봉 확정 후 전략 신호에 따라 자동 편입됩니다.' : 'Stock Easy 전략의 매수 시그널 발생 시 자동으로 편입됩니다.'}</p>
              <p style={{ fontSize: '0.75rem', marginTop: '0.3rem', color: 'rgba(255,255,255,0.25)' }}>이탈 종목 {curExits.length}건</p>
            </div>
          ) : (
            <div className="glass-panel" style={{ overflow: 'clip' }}>
              <div style={{ padding: '0.6rem 1rem', borderBottom: '1px solid var(--glass-border)', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                updated {lastSync}  ·  가상매수 한도: 1,000만원/종목
              </div>
              <table className="premium-table">
                <thead><tr>
                  <th>종목명</th>
                  {strategy === 'all' && <th>소속 전략</th>}
                  {strategy === 'all' && <th style={{ textAlign: 'right' }}>포함 전략</th>}
                  <th style={{ textAlign: 'right' }}>매수가</th>
                  <th style={{ textAlign: 'right' }}>현재가</th>
                  <th style={{ textAlign: 'right' }}>편입일</th>
                  <th style={{ textAlign: 'right' }}>보유일</th>
                  <th style={{ textAlign: 'right' }}>수익률</th>
                  <th style={{ textAlign: 'right' }}>수익금</th>
                  <th style={{ textAlign: 'right' }}>평가액</th>
                </tr></thead>
                <tbody>
                  {curHoldings.map(h => {
                    const pct = h.profit_pct || 0;
                    const strategies = holdingStrategies(h);
                    return (
                      <tr key={h.id}>
                        <td style={{ fontWeight: 700 }}>
                          {h.stock_name}
                          {strategy === 'ai_rec' && h.stock_code && h.stock_code !== 'None' && (
                            <button onClick={()=>{changeStock(h.stock_code);changeTab('analysis');}}
                              style={{marginLeft:'0.4rem',padding:'0.1rem 0.35rem',borderRadius:'4px',border:'none',
                                background:'rgba(45,212,191,0.12)',color:'var(--accent-mint)',cursor:'pointer',fontSize:'0.65rem'}}>
                              분석
                            </button>
                          )}
                        </td>
                        {strategy === 'all' && (
                          <td style={{ maxWidth: '220px' }}>
                            <div style={{ display:'flex', gap:'0.25rem', flexWrap:'wrap' }}>
                              {strategies.map(strategyKey => (
                                <span key={strategyKey} style={{ padding:'0.1rem 0.38rem', borderRadius:'10px',
                                  background:'rgba(147,197,253,0.12)', color:'#93c5fd', fontSize:'0.68rem', whiteSpace:'nowrap' }}>
                                  {strategyLabel(strategyKey)}
                                </span>
                              ))}
                            </div>
                          </td>
                        )}
                        {strategy === 'all' && (
                          <td style={{ textAlign:'right', fontWeight:800, color:strategies.length > 1 ? '#fbbf24' : 'var(--text-secondary)' }}>
                            {strategies.length}개
                          </td>
                        )}
                        <td style={{ textAlign: 'right' }}>{money(h.buy_price, h.currency || 'KRW')}</td>
                        <td style={{ textAlign: 'right', color: pc(h.daily_change_pct || 0), fontWeight: 600 }}>
                          {money(h.current_price, h.currency || 'KRW')}
                          <span style={{marginLeft:'0.35rem',fontSize:'0.72rem',color:pc(h.daily_change_pct || 0)}}>
                            {(h.daily_change_pct || 0) >= 0 ? '+' : ''}{(h.daily_change_pct || 0).toFixed(1)}%
                          </span>
                        </td>
                        <td style={{ textAlign: 'right', fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                          {h.entry_date ? h.entry_date.slice(5).replace('-','/') : '-'}
                        </td>
                        <td style={{ textAlign: 'right' }}>
                          <span style={{ padding: '0.1rem 0.5rem', borderRadius: '12px',
                            background: 'rgba(167,139,250,0.12)', fontSize: '0.78rem', color: 'var(--accent-purple)' }}>
                            {h.entry_date
                              ? Math.floor((Date.now() - new Date(h.entry_date).getTime()) / 86400000)
                              : (h.hold_days ?? 0)}일
                          </span>
                        </td>
                        <td style={{ textAlign: 'right', color: pc(pct), fontWeight: 700 }}>
                          {pct >= 0 ? '+' : ''}{pct.toFixed(1)}%
                        </td>
                        <td style={{ textAlign: 'right', color: pc(h.profit), fontWeight: 600 }}>
                          {money(h.profit, h.currency || 'KRW')}
                        </td>
                        <td style={{ textAlign: 'right' }}>{money(h.total_value, h.currency || 'KRW')}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )
        )}



        {/* ══ 이탈 종목 탭 ══ */}
        {!loading && strategy !== 'us_virtual' && peakTab === 'exits' && (
          curExits.length === 0 ? (
            <div className="glass-panel" style={{ padding: '3rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
              <p>이탈 종목이 없습니다.</p>
            </div>
          ) : (
            <div className="glass-panel" style={{ overflow: 'clip' }}>
              <table className="premium-table">
                <thead><tr>
                  <th>종목명</th>
                  <th style={{ textAlign: 'right' }}>매수가</th>
                  <th style={{ textAlign: 'right' }}>매도가</th>
                  <th style={{ textAlign: 'right' }}>편입일</th>
                  <th style={{ textAlign: 'right' }}>보유일</th>
                  <th style={{ textAlign: 'right' }}>수익률</th>
                  <th style={{ textAlign: 'right' }}>수익금</th>
                  <th style={{ textAlign: 'right' }}>이탈시각</th>
                </tr></thead>
                <tbody>
                  {curExits.map(h => {
                    const pct = h.profit_pct || 0;
                    const profitRaw = ((h.sell_price||h.buy_price||0) - (h.buy_price||0)) * (h.quantity||0);
                    const profit = (h.currency || 'KRW') === 'USD' ? Number(profitRaw.toFixed(2)) : Math.round(profitRaw);
                    return (
                      <tr key={h.id}>
                        <td><span style={{ fontSize: '0.72rem', color: 'var(--text-secondary)' }}>{h.sector}</span></td>
                        <td style={{ fontWeight: 700 }}>{h.stock_name}</td>
                        <td style={{ textAlign: 'right' }}>{money(h.buy_price, h.currency || 'KRW')}</td>
                        <td style={{ textAlign: 'right', fontWeight: 600 }}>{money(h.sell_price, h.currency || 'KRW')}</td>
                        <td style={{ textAlign: 'right', fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                          {h.entry_date ? h.entry_date.slice(5).replace('-','/') : '-'}
                        </td>
                        <td style={{ textAlign: 'right' }}>
                          <span style={{ padding: '0.1rem 0.5rem', borderRadius: '12px',
                            background: 'rgba(255,255,255,0.06)', fontSize: '0.78rem' }}>
                            {h.entry_date
                              ? Math.floor((Date.now() - new Date(h.entry_date).getTime()) / 86400000)
                              : (h.hold_days ?? 0)}일
                          </span>
                        </td>
                        <td style={{ textAlign: 'right', color: pc(pct), fontWeight: 700 }}>
                          {pct >= 0 ? '+' : ''}{pct.toFixed(1)}%
                        </td>
                        <td style={{ textAlign: 'right', color: pc(profit), fontWeight: 600 }}>
                          {money(profit, h.currency || 'KRW')}
                        </td>
                        <td style={{ textAlign: 'right', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                          {h.sold_at || '-'}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )
        )}

        {/* ══ 상태 확인 탭 — 전체 포지션 현황 ══ */}
        {!loading && strategy !== 'us_virtual' && peakTab === 'status' && (() => {
          const allPos = [...peakData.holdings, ...peakData.exits]
            .sort((a,b) => (b.entry_date||'').localeCompare(a.entry_date||''));
          return allPos.length === 0 ? (
            <div className="glass-panel" style={{ padding: '3rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
              <p>포지션 내역이 없습니다.</p>
            </div>
          ) : (
            <div className="glass-panel" style={{ overflow: 'clip' }}>
              <div style={{ padding: '0.6rem 1rem', borderBottom: '1px solid var(--glass-border)', display: 'flex', gap: '1rem', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                <span>전체 {allPos.length}건</span>
                <span style={{ color: '#34d399' }}>보유중 {peakData.holdings.length}건</span>
                <span style={{ color: 'rgba(255,255,255,0.4)' }}>매도완료 {peakData.exits.length}건</span>
              </div>
              <table className="premium-table">
                <thead><tr>
                  <th>상태</th><th>종목명</th>
                  <th style={{ textAlign: 'right' }}>매수가</th>
                  <th style={{ textAlign: 'right' }}>매도가</th>
                  <th style={{ textAlign: 'right' }}>수량</th>
                  <th style={{ textAlign: 'right' }}>수익률</th>
                  <th style={{ textAlign: 'right' }}>수익금</th>
                  <th style={{ textAlign: 'right' }}>편입일</th>
                  <th style={{ textAlign: 'right' }}>이탈/갱신</th>
                </tr></thead>
                <tbody>
                  {allPos.map(h => {
                    const isActive = h.is_active;
                    const pct = h.profit_pct || 0;
                    const profitRaw = isActive
                      ? ((h.current_price||h.buy_price||0) - (h.buy_price||0)) * (h.quantity||0)
                      : ((h.sell_price||h.buy_price||0) - (h.buy_price||0)) * (h.quantity||0);
                    const profit = (h.currency || 'KRW') === 'USD' ? Number(profitRaw.toFixed(2)) : Math.round(profitRaw);
                    return (
                      <tr key={h.id} style={{ opacity: isActive ? 1 : 0.65 }}>
                        <td>
                          <span style={{
                            padding: '0.15rem 0.6rem', borderRadius: '12px', fontSize: '0.72rem', fontWeight: 700,
                            background: isActive ? 'rgba(52,211,153,0.15)' : 'rgba(255,255,255,0.07)',
                            color:      isActive ? '#34d399'               : 'rgba(255,255,255,0.45)',
                            border:     isActive ? '1px solid rgba(52,211,153,0.3)' : '1px solid rgba(255,255,255,0.12)',
                          }}>
                            {isActive ? '보유중' : '매도완료'}
                          </span>
                        </td>
                        <td style={{ fontWeight: 700 }}>{h.stock_name}</td>
                        <td style={{ textAlign: 'right' }}>{money(h.buy_price, h.currency || 'KRW')}</td>
                        <td style={{ textAlign: 'right', color: isActive ? 'rgba(255,255,255,0.3)' : 'inherit' }}>
                          {isActive ? money(h.current_price, h.currency || 'KRW') : money(h.sell_price, h.currency || 'KRW')}
                        </td>
                        <td style={{ textAlign: 'right' }}>{(h.quantity||0).toLocaleString('ko-KR')}주</td>
                        <td style={{ textAlign: 'right', color: pc(pct), fontWeight: 600 }}>
                          {pct >= 0 ? '+' : ''}{pct.toFixed(1)}%
                        </td>
                        <td style={{ textAlign: 'right', color: pc(profit), fontWeight: 600 }}>
                          {money(profit, h.currency || 'KRW')}
                        </td>
                        <td style={{ textAlign: 'right', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                          {h.entry_date ? h.entry_date.slice(5).replace('-','/') : '-'}
                        </td>
                        <td style={{ textAlign: 'right', fontSize: '0.72rem', color: 'var(--text-secondary)' }}>
                          {isActive ? (h.updated_at||'-') : (h.sold_at||'-')}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          );
        })()}

        {/* ══ 병합조합 가상매매 안내 + 실행 패널 (2026-07-23 신규) ══ */}
        {!loading && strategy !== 'us_virtual' && strategy.startsWith('combo_') && (
          <>
          <div style={{padding:'0.65rem 1rem',
            background:'linear-gradient(135deg,rgba(250,204,21,0.10),rgba(56,189,248,0.06))',
            border:'1px solid rgba(250,204,21,0.28)',borderRadius:'8px',
            fontSize:'0.72rem',color:'rgba(255,255,255,0.68)',lineHeight:1.7,
            display:'flex',alignItems:'center',justifyContent:'space-between',flexWrap:'wrap',gap:'0.5rem'}}>
            <span>
              <span style={{fontWeight:700,color:'#facc15',marginRight:'0.4rem'}}>{comboData?.label || '전략센터 병합조합'}</span>
              전략센터 백테스트 검증조합 실측치를 그대로 재현 · 시드 1억원 · 구성전략 today-signal 우선순위 배분
            </span>
            <div style={{display:'flex',gap:'0.45rem'}}>
              <button onClick={()=>loadCombo(strategy)} style={{
                padding:'0.3rem 0.65rem',borderRadius:'6px',fontSize:'0.75rem',cursor:'pointer',
                background:'rgba(255,255,255,0.05)',border:'1px solid var(--glass-border)',color:'var(--text-secondary)',
              }}>후보 갱신</button>
              <button disabled={comboExecuting} onClick={()=>executeCombo(strategy)} style={{
                padding:'0.3rem 0.65rem',borderRadius:'6px',fontSize:'0.75rem',cursor: comboExecuting ? 'wait':'pointer',
                background:'rgba(250,204,21,0.15)',border:'1px solid rgba(250,204,21,0.35)',color:'#facc15',
                opacity: comboExecuting ? 0.6 : 1,
              }}>{comboExecuting ? '실행 중...' : '즉시 실행'}</button>
            </div>
          </div>
          <div style={{padding:'0.8rem 1rem',borderRadius:'10px',background:'rgba(255,255,255,0.02)',border:'1px solid rgba(255,255,255,0.07)',fontSize:'0.71rem',color:'rgba(255,255,255,0.45)',lineHeight:1.75}}>
            <div style={{fontWeight:700,color:'rgba(255,255,255,0.6)',marginBottom:'0.45rem',fontSize:'0.73rem'}}>📋 구성 전략(우선순위순)</div>
            <div style={{display:'flex',flexWrap:'wrap',gap:'0.4rem',marginBottom:'0.5rem'}}>
              {(comboData?.components || []).map((c,i)=>(
                <span key={i} style={{padding:'0.15rem 0.55rem',borderRadius:'12px',background:'rgba(250,204,21,0.08)',border:'1px solid rgba(250,204,21,0.2)',fontSize:'0.68rem'}}>
                  {c.label || c.strategy} <span style={{color:'rgba(255,255,255,0.35)'}}>×{c.priority}</span>
                </span>
              ))}
            </div>
            <div>현재 매수후보: <span style={{color:'#facc15',fontWeight:700}}>{comboData?.buy_candidates?.length ?? 0}종목</span>
              {' · '}매도후보: <span style={{color:'#38bdf8',fontWeight:700}}>{comboData?.sell_candidates?.length ?? 0}종목</span>
              {' · '}보유: <span style={{fontWeight:700}}>{comboData?.active_positions ?? 0}/{comboData?.max_positions ?? '-'}</span>
            </div>
            {comboData?.updated_at && <div style={{marginTop:'0.3rem',color:'rgba(255,255,255,0.25)',fontSize:'0.66rem'}}>최근 신호 갱신: {comboData.updated_at}</div>}
          </div>

          {/* 매도 후보 종목 리스트 */}
          {(comboData?.sell_candidates || []).length > 0 && (
            <div style={{padding:'0.6rem 0.8rem',borderRadius:'8px',background:'rgba(255,255,255,0.02)',border:'1px solid rgba(239,68,68,0.15)'}}>
              <div style={{fontSize:'0.78rem',fontWeight:700,marginBottom:'0.35rem',color:'#ef4444'}}>🔴 매도 후보</div>
              <table className="premium-table" style={{width:'100%'}}>
                <thead><tr><th>종목</th><th style={{textAlign:'right'}}>매수가</th><th style={{textAlign:'right'}}>현재가</th><th style={{textAlign:'right'}}>수익률</th><th>사유</th></tr></thead>
                <tbody>
                  {comboData.sell_candidates.map((r)=>(
                    <tr key={r.stock_code}>
                      <td>{r.stock_name} <span style={{fontSize:'0.68rem',color:'var(--text-secondary)'}}>{r.stock_code}</span></td>
                      <td style={{textAlign:'right'}}>{r.buy_price?.toLocaleString?.()??'-'}</td>
                      <td style={{textAlign:'right'}}>{r.current_price?.toLocaleString?.()??'-'}</td>
                      <td style={{textAlign:'right',color:(r.profit_pct||0)>=0?'#22c55e':'#ef4444'}}>{(r.profit_pct??0).toFixed(2)}%</td>
                      <td style={{fontSize:'0.7rem',color:'rgba(239,68,68,0.8)'}}>{r.reason||'-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* 매수 후보 종목 리스트 */}
          <div style={{padding:'0.6rem 0.8rem',borderRadius:'8px',background:'rgba(255,255,255,0.02)',border:'1px solid rgba(250,204,21,0.15)'}}>
            <div style={{fontSize:'0.78rem',fontWeight:700,marginBottom:'0.35rem',color:'#facc15'}}>🟢 매수 후보 (오늘 신호)</div>
            {(comboData?.buy_candidates || []).length === 0 ? (
              <div style={{padding:'0.8rem',borderRadius:'8px',background:'rgba(255,255,255,0.03)',border:'1px solid rgba(255,255,255,0.06)',fontSize:'0.73rem',color:'rgba(255,255,255,0.35)'}}>
                오늘 신규 매수 신호가 없습니다.
              </div>
            ) : (
              <table className="premium-table" style={{width:'100%'}}>
                <thead><tr><th>종목</th><th style={{textAlign:'right'}}>현재가</th><th>신호 출처</th><th style={{textAlign:'right'}}>우선순위</th></tr></thead>
                <tbody>
                  {comboData.buy_candidates.map((r)=>(
                    <tr key={r.stock_code}>
                      <td>{r.stock_name} <span style={{fontSize:'0.68rem',color:'var(--text-secondary)'}}>{r.stock_code}</span></td>
                      <td style={{textAlign:'right'}}>{r.current_price?.toLocaleString?.()??'-'}</td>
                      <td style={{fontSize:'0.7rem',color:'rgba(250,204,21,0.85)'}}>{r.reason||'-'}</td>
                      <td style={{textAlign:'right'}}>{r.priority}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          </>
        )}

        {/* ══ 매매 내역 탭 ══ */}
        {!loading && strategy !== 'us_virtual' && peakTab === 'history' && (
          <div style={{display:'flex',flexDirection:'column',gap:'0.5rem'}}>
            {/* 삭제 버튼 행 */}
            <div style={{display:'flex',justifyContent:'flex-end'}}>
              <button onClick={async () => {
                if (!window.confirm('매매 내역을 모두 삭제하시겠습니까?')) return;
                await fetch(API('/api/trend/trades/all'), { method: 'DELETE' });
                setTrades([]);
              }} style={{padding:'0.3rem 0.8rem',borderRadius:'6px',fontSize:'0.75rem',cursor:'pointer',
                border:'1px solid rgba(239,68,68,0.35)',background:'rgba(239,68,68,0.08)',color:'#ef4444'}}>
                🗑 전체 삭제
              </button>
            </div>
            {(() => {
              const filteredTrades = strategy === 'ai_rec'
                ? trades.filter(t => t.strategy === 'ai_combo')
                : trades.filter(t => t.strategy === strategy || (!t.strategy && strategy === 'peak'));
              return filteredTrades.length === 0 ? (
                <div className="glass-panel" style={{ padding: '3rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
                  <p>매매 내역이 없습니다.</p>
                </div>
              ) : (
                <div className="glass-panel" style={{ overflow: 'clip' }}>
                  <table className="premium-table">
                    <thead><tr>
                      <th>구분</th><th>종목명</th>
                      <th style={{ textAlign: 'right' }}>가격</th>
                      <th style={{ textAlign: 'right' }}>수량</th>
                      <th style={{ textAlign: 'right' }}>거래금액</th>
                      <th style={{ textAlign: 'right' }}>손익</th>
                      <th style={{ textAlign: 'right' }}>수익률</th>
                      <th style={{ textAlign: 'right' }}>시각</th>
                    </tr></thead>
                    <tbody>
                      {filteredTrades.map(t => (
                        <tr key={t.id}>
                          <td>
                            <span style={{ padding: '0.15rem 0.6rem', borderRadius: '12px', fontSize: '0.75rem', fontWeight: 700,
                              background: t.tx_type === 'buy' ? 'rgba(239,68,68,0.15)' : 'rgba(59,130,246,0.15)',
                              color:      t.tx_type === 'buy' ? '#ef4444' : '#3b82f6' }}>
                              {t.tx_type === 'buy' ? '매수' : '매도'}
                            </span>
                          </td>
                          <td style={{ fontWeight: 600 }}>{t.stock_name}</td>
                          <td style={{ textAlign: 'right' }}>{fp(t.price)}</td>
                          <td style={{ textAlign: 'right' }}>{(t.quantity||0).toLocaleString('ko-KR')}주</td>
                          <td style={{ textAlign: 'right' }}>{fp(t.total_amount)}원</td>
                          <td style={{ textAlign: 'right', color: pc(t.profit||0), fontWeight: t.profit != null ? 700 : 400 }}>
                            {t.profit != null ? pf(t.profit)+'원' : '-'}
                          </td>
                          <td style={{ textAlign: 'right', color: pc(t.profit_pct||0) }}>
                            {t.profit_pct != null ? (t.profit_pct>=0?'+':'')+t.profit_pct.toFixed(1)+'%' : '-'}
                          </td>
                          <td style={{ textAlign: 'right', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>{t.tx_at}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              );
            })()}
          </div>
        )}
      </div>
    );
  };

export default PeakView;
