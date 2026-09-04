/**
 * BacktestView.jsx
 * 백테스트 결과 뷰 — App.jsx에서 분리 (2026-09-03, 토큰 최적화)
 * 원본은 App.jsx에 인라인으로 정의되어 있던 컴포넌트를 그대로 이관. 로직/JSX 변경 없음.
 * 전략센터(StrategyHub)에 내장(externalViewMode='desc')되거나, /backtest 탭에서 단독으로도 쓰인다.
 */
import React from 'react';
import { Activity } from 'lucide-react';
import { API } from '../utils';

// ── BacktestView (module-level: App 재렌더와 무관하게 안정적인 컴포넌트 identity 유지) ──
  const BacktestView = ({ externalViewMode } = {}) => {
    const embedded = !!externalViewMode;  // StrategyHub에 내장 시 헤더·탭 숨김
    const [list,       setList]       = React.useState([]);
    const [detail,     setDetail]     = React.useState(null);
    const [running,    setRunning]    = React.useState(false);
    const [runAllBusy, setRunAllBusy] = React.useState(false);
    const [pollId,     setPollId]     = React.useState(null);
    const [viewMode,   setViewMode]   = React.useState(externalViewMode || 'matrix'); // 'matrix'|'desc'|'list'
    React.useEffect(() => { if (externalViewMode) setViewMode(externalViewMode); }, [externalViewMode]);
    const [matrixData, setMatrixData] = React.useState(null);
    const [catalog,    setCatalog]    = React.useState([]);
    const [form, setForm] = React.useState({
      start_date: '2020-03-01',
      end_date:   '2025-05-31',
      per_stock:  10000000,
      name:       '',
      strategy:   'v4',
    });

    const loadList = async () => {
      try {
        const r = await fetch(API('/api/backtest/list'));
        if (r.ok) setList(await r.json());
      } catch(e) {}
    };
    const loadMatrix = async () => {
      try {
        const r = await fetch(API('/api/backtest/matrix'));
        if (r.ok) setMatrixData(await r.json());
      } catch(e) {}
    };
    const loadCatalog = async () => {
      try {
        const r = await fetch(API('/api/backtest/strategies'));
        if (r.ok) { const d = await r.json(); setCatalog(d.strategies || []); }
      } catch(e) {}
    };

    React.useEffect(() => {
      loadList();
      loadMatrix();
      loadCatalog();
    }, []);

    // V1~V12 전략 정의 (13전략)
    const STRAT_DEFS = [
      { key:'v_trend',        label:'V1 MA추세',           ep:'/api/backtest/run-v1' },
      { key:'v1_value',       label:'V2 가치매수',         ep:'/api/backtest/run-v1-value' },
      { key:'v2',             label:'V3 재무우량',         ep:'/api/backtest/run-v2' },
      { key:'v5',             label:'V4 수급모멘텀',       ep:'/api/backtest/run-v5' },
      { key:'v4',             label:'V5 복합콤보',         ep:'/api/backtest/run' },
      { key:'v10',            label:'V6 이익폭발',         ep:'/api/backtest/run-v10' },
      { key:'v11',            label:'V7 이익가속',         ep:'/api/backtest/run-v11' },
      { key:'vbr',            label:'V8 52W돌파',          ep:'/api/backtest/run-vbr' },
      { key:'v8',             label:'V9 수출선행',         ep:'/api/backtest/run-v8' },
      { key:'v12',            label:'V10 섹터대세',        ep:'/api/backtest/run-v12' },
      { key:'regime_adaptive',label:'Meta-V 레짐 적응형',  ep:'/api/backtest/run-regime-adaptive' },
      { key:'composite',      label:'V11 복합스코어링',    ep:'/api/backtest/run-composite' },
      { key:'golden_cross',    label:'V12 골든크로스',     ep:'/api/backtest/run-golden-cross' },
      { key:'high_profit_compound',label:'V13 고수익집중',    ep:'/api/backtest/run-high-profit-compound' },
      { key:'recovery',        label:'V-RECOVERY 낙폭반등', ep:'/api/backtest/run-recovery' },
      { key:'deep_recovery',       label:'V-DEEP 깊은낙폭집중',   ep:'/api/backtest/run-deep-recovery' },
      { key:'low_base_breakout',   label:'V-LOWBASE 저점기반돌파', ep:'/api/backtest/run-low-base-breakout' },
      { key:'sector_focus',        label:'V-SECTOR 섹터집중',     ep:'/api/backtest/run-sector' },
      { key:'turnaround',          label:'V-TURNAROUND 흑자전환',  ep:'/api/backtest/run-turnaround' },
    ];
    const strategyEndpoints = Object.fromEntries(STRAT_DEFS.map(s => [s.key, s.ep]));

    const startBacktest = async () => {
      setRunning(true);
      try {
        const endpoint = strategyEndpoints[form.strategy];
        if (!endpoint) { alert(`지원하지 않는 전략: ${form.strategy}`); setRunning(false); return; }
        const stratLabel = STRAT_DEFS.find(s => s.key === form.strategy)?.label || form.strategy;
        const r = await fetch(API(endpoint), {
          method: 'POST',
          headers: {'Content-Type':'application/json'},
          body: JSON.stringify({
            start_date: form.start_date,
            end_date:   form.end_date,
            per_stock:  Number(form.per_stock),
            name:       form.name || `${stratLabel} ${form.start_date.slice(0,7)}~${form.end_date.slice(0,7)}`,
          }),
        });
        if (!r.ok) { setRunning(false); return; }
        const { run_id } = await r.json();
        // 완료될 때까지 폴링
        const iv = setInterval(async () => {
          await loadList();
          const r2 = await fetch(API(`/api/backtest/${run_id}`));
          if (r2.ok) {
            const d = await r2.json();
            if (d.status === 'done' || d.status === 'error') {
              clearInterval(iv);
              setRunning(false);
              setDetail(d);
              loadList();
            }
          }
        }, 3000);
        setPollId(iv);
      } catch(e) { setRunning(false); }
    };

    const loadDetail = async (run_id) => {
      const r = await fetch(API(`/api/backtest/${run_id}`));
      if (r.ok) setDetail(await r.json());
    };

    const deleteRun = async (run_id) => {
      await fetch(API(`/api/backtest/${run_id}`), { method: 'DELETE' });
      setList(prev => prev.filter(x => x.run_id !== run_id));
      if (detail && detail.run_id === run_id) setDetail(null);
    };

    const fmtAmt = (v) => v == null ? '-' : (v >= 0 ? '+' : '') + Math.round(v).toLocaleString('ko-KR') + '원';
    const clr = (v) => v > 0 ? '#ef4444' : v < 0 ? '#3b82f6' : 'rgba(255,255,255,0.4)';
    const inputS = {
      padding:'0.35rem 0.7rem', borderRadius:'6px', fontSize:'0.82rem',
      background:'rgba(255,255,255,0.06)', border:'1px solid var(--glass-border)',
      color:'#fff',
    };

    // 매트릭스 셀 색상
    const cellBg = (cagr) => {
      if (cagr == null) return 'rgba(255,255,255,0.02)';
      if (cagr <= 0) return 'rgba(59,130,246,0.1)';
      if (cagr >= 10) return 'rgba(239,68,68,0.2)';
      if (cagr >= 5) return 'rgba(239,68,68,0.12)';
      return 'rgba(239,68,68,0.06)';
    };
    const cellClr = (cagr) => {
      if (cagr == null) return 'rgba(255,255,255,0.2)';
      return cagr > 0 ? '#f87171' : '#60a5fa';
    };

    const runAllMatrix = async () => {
      if (!window.confirm('V1~V12 전체 13전략 × 5기간 (최대 65개) 백테스트를 일괄 실행합니다.\n예산: 전략당 1억원 (종목당 1천만원 기준)\n완료까지 수십 분 소요될 수 있습니다.\n\n계속하시겠습니까?')) return;
      setRunAllBusy(true);
      try {
        const r = await fetch(API('/api/backtest/run-all-matrix'), {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ per_stock: 10000000 }),
        });
        if (r.ok) {
          const d = await r.json();
          alert(`백테스트 ${d.started}개 실행 시작!\n결과는 수십 분 후 매트릭스에 자동 표시됩니다.`);
          setTimeout(loadList, 3000);
        }
      } catch(e) {}
      finally { setRunAllBusy(false); }
    };

    return (
      <div className="fade-in" style={{display:'flex',flexDirection:'column',gap:'1rem'}}>
        {/* 헤더 + 뷰 토글 (standalone 모드만 표시) */}
        {!embedded && (
        <div className="glass-panel" style={{padding:'0.8rem 1.2rem'}}>
          <div style={{display:'flex',alignItems:'center',gap:'0.6rem',marginBottom:'0.5rem',flexWrap:'wrap'}}>
            <Activity size={18} color="#f59e0b" />
            <h2 style={{fontSize:'1rem',fontWeight:700}}>📊 백테스트 & 전략 비교 (V1~V12, 13전략)</h2>
            <div style={{marginLeft:'auto',display:'flex',gap:'0.4rem',flexWrap:'wrap'}}>
              {[
                { key:'matrix', label:'📊 성과 매트릭스' },
                { key:'desc',   label:'📋 전략 상세 설명' },
                { key:'list',   label:'🗒 실행 목록' },
              ].map(({ key, label }) => (
                <button key={key} onClick={() => setViewMode(key)} style={{
                  padding:'0.25rem 0.7rem', borderRadius:'6px', fontSize:'0.75rem',
                  cursor:'pointer', fontWeight: viewMode===key ? 700 : 400,
                  background: viewMode===key ? 'rgba(245,158,11,0.2)' : 'rgba(255,255,255,0.05)',
                  border: `1px solid ${viewMode===key ? 'rgba(245,158,11,0.5)' : 'var(--glass-border)'}`,
                  color: viewMode===key ? '#f59e0b' : 'var(--text-secondary)',
                }}>{label}</button>
              ))}
              <button onClick={runAllMatrix} disabled={runAllBusy} style={{
                padding:'0.25rem 0.9rem', borderRadius:'6px', fontSize:'0.75rem',
                cursor: runAllBusy ? 'not-allowed' : 'pointer', fontWeight:700,
                background: runAllBusy ? 'rgba(100,116,139,0.15)' : 'rgba(16,185,129,0.2)',
                border:`1px solid ${runAllBusy ? 'rgba(100,116,139,0.3)' : 'rgba(16,185,129,0.5)'}`,
                color: runAllBusy ? 'var(--text-secondary)' : '#34d399',
              }}>
                {runAllBusy ? '⏳ 실행 중...' : '▶▶ 전체 백테스트'}
              </button>
            </div>
          </div>
          <div style={{padding:'0.5rem 0.8rem',background:'rgba(251,191,36,0.07)',
            border:'1px solid rgba(251,191,36,0.2)',borderRadius:'6px',
            fontSize:'0.7rem',color:'rgba(251,191,36,0.85)',lineHeight:1.6}}>
            ⚠️ 과거 데이터 기준 시뮬레이션 — <strong>미래 수익 보장 불가</strong>.
            V1~V12 13전략: 2020~2026 실제 데이터 백테스트 (전략당 예산 1억원, 종목당 1천만원)
          </div>
        </div>
        )}

        {/* ══ 전략 비교 매트릭스 뷰 ══ */}
        {viewMode === 'matrix' && matrixData && (() => {
          const periods = matrixData.period_order || [];
          const stratOrder = matrixData.strategy_order || [];
          // 핵심 전략만 표시 (strategy_order에 포함된 것만, 순서대로)
          const strategies = stratOrder
            .map(sk => (matrixData.strategies || []).find(s => s.strategy === sk))
            .filter(Boolean);
          const allPeriodResults = strategies.flatMap(s => Object.values(s.periods || {}));
          const verifiedResultCount = allPeriodResults.filter(p => p.methodology_status === 'verified').length;
          // 2026-07-23: execution_verified_pit_pending(체결검증 완료·PIT미검증)/pit_approx_execution_pending
          // (PIT근사·체결미검증)은 둘 다 specified_unverified의 세분화된 하위등급 — 합산해서 기존 카운트 유지.
          const specifiedResultCount = allPeriodResults.filter(p => [
            'specified_unverified', 'execution_verified_pit_pending', 'pit_approx_execution_pending',
          ].includes(p.methodology_status)).length;
          const legacyResultCount = allPeriodResults.filter(p => p.methodology_status === 'legacy_unversioned').length;

          // 전략 설명
          // 설명은 백엔드 desc 사용 (stratDesc는 fallback용)
          const stratDescFallback = {
            v1: 'Graham 내재가치 할인 + 수급 보조',
            v2: '재무 성장/수익성 스코어링 ★ 장기 최고 CAGR',
            v4: 'AI 적극검토 콤보 (추세+가치+수급+KOSPI필터)',
            v11: '흑자전환 (적자→흑자 2분기 연속) ★2022-23 최고',
            v_trend: 'MA정배열(20>60>120) + RSI42-72 + 거래량×1.3배',
          };

          return (
            <div className="glass-panel" style={{overflow:'clip'}}>
              <div style={{padding:'0.7rem 1rem',borderBottom:'1px solid var(--glass-border)',
                display:'flex',alignItems:'center',gap:'1rem',flexWrap:'wrap'}}>
                <span style={{fontWeight:700,fontSize:'0.88rem'}}>전략 × 기간 성과 매트릭스</span>
                <span style={{fontSize:'0.72rem',color:'var(--text-secondary)'}}>
                  셀: CAGR(연복리) | 괄호: MDD | 빨강=수익 파랑=손실
                </span>
                <button onClick={loadMatrix} style={{marginLeft:'auto',padding:'0.2rem 0.6rem',
                  borderRadius:'5px',fontSize:'0.7rem',cursor:'pointer',
                  border:'1px solid var(--glass-border)',background:'transparent',
                  color:'var(--text-secondary)'}}>↻ 새로고침</button>
              </div>
              {(legacyResultCount > 0 || specifiedResultCount > 0) && (
                <div style={{padding:'0.55rem 1rem',background:'rgba(249,115,22,0.08)',
                  borderBottom:'1px solid rgba(249,115,22,0.22)',fontSize:'0.7rem',
                  color:'#fdba74',lineHeight:1.55}}>
                  검증 완료 {verifiedResultCount}건 · 명세만 등록 {specifiedResultCount}건 · 명세 없는 레거시 {legacyResultCount}건.
                  point-in-time 유니버스와 다음날 시가 체결까지 확인되기 전에는 전략 채택과 순위 산정에 사용하지 않습니다.
                </div>
              )}
              <div style={{overflowX:'auto',overflowY:'clip'}}>
                <table style={{width:'100%',borderCollapse:'collapse',fontSize:'0.78rem'}}>
                  <thead>
                    <tr>
                      <th style={{padding:'0.5rem 0.8rem',background:'rgba(30,58,138,0.4)',
                        borderBottom:'2px solid rgba(59,130,246,0.4)',textAlign:'left',
                        position:'sticky',top:0,zIndex:5,whiteSpace:'nowrap',minWidth:'120px'}}>전략</th>
                      <th style={{padding:'0.5rem 0.8rem',background:'rgba(30,58,138,0.4)',
                        borderBottom:'2px solid rgba(59,130,246,0.4)',
                        position:'sticky',top:0,zIndex:5,fontSize:'0.7rem',
                        color:'rgba(255,255,255,0.5)',textAlign:'left',minWidth:'180px'}}>설명</th>
                      {periods.map(p => (
                        <th key={p} style={{padding:'0.5rem 0.6rem',background:'rgba(30,58,138,0.4)',
                          borderBottom:'2px solid rgba(59,130,246,0.4)',textAlign:'center',
                          position:'sticky',top:0,zIndex:5,whiteSpace:'nowrap',minWidth:'90px'}}>
                          {p}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {strategies.map((s, si) => {
                      // 전략별 최고 CAGR 기간 찾기
                      const cagrVals = periods.map(p => s.periods[p]?.cagr ?? s.periods[p]?.ann_return_pct ?? null);
                      const maxCagr = Math.max(...cagrVals.filter(v => v != null));
                      return (
                        <tr key={s.strategy}
                          style={{background: si%2===0 ? 'transparent' : 'rgba(255,255,255,0.015)'}}
                          onMouseOver={e => e.currentTarget.style.background='rgba(255,255,255,0.04)'}
                          onMouseOut={e => e.currentTarget.style.background= si%2===0 ? 'transparent' : 'rgba(255,255,255,0.015)'}>
                          <td style={{padding:'0.5rem 0.8rem',fontWeight:700,
                            borderBottom:'1px solid rgba(255,255,255,0.04)',
                            color: ['v2','v_trend'].includes(s.strategy) ? '#fbbf24' : '#e2e8f0',
                            whiteSpace:'nowrap'}}>
                            {s.label}
                          </td>
                          <td style={{padding:'0.4rem 0.8rem',fontSize:'0.7rem',
                            color:'rgba(255,255,255,0.45)',
                            borderBottom:'1px solid rgba(255,255,255,0.04)'}}>
                            {s.desc || stratDescFallback[s.strategy] || ''}
                          </td>
                          {periods.map(p => {
                            const pd = s.periods[p];
                            const cagr = pd?.cagr ?? pd?.ann_return_pct ?? null;
                            const mdd = pd?.mdd;
                            const tc = pd?.trade_count;
                            const isVerified = pd?.methodology_status === 'verified';
                            const isBest = isVerified && cagr != null && cagr === maxCagr && cagr > 0;
                            const isZero = tc === 0 || (!pd);
                            return (
                              <td key={p} style={{
                                padding:'0.4rem 0.6rem',
                                textAlign:'center',
                                background: isZero ? 'rgba(255,255,255,0.01)' : cellBg(cagr),
                                borderBottom:'1px solid rgba(255,255,255,0.04)',
                                borderLeft:'1px solid rgba(255,255,255,0.04)',
                              }}>
                                {isZero ? (
                                  <span style={{color:'rgba(255,255,255,0.2)',fontSize:'0.68rem'}}>
                                    {!pd ? '-' : '0건'}
                                  </span>
                                ) : (
                                  <div>
                                    <div style={{
                                      fontWeight: isBest ? 700 : 600,
                                      color: cellClr(cagr),
                                      fontSize: isBest ? '0.85rem' : '0.8rem',
                                    }}>
                                      {isBest && '★ '}
                                      {cagr != null ? (cagr>0?'+':'')+cagr.toFixed(1)+'%' : '-'}
                                    </div>
                                    {mdd != null && (
                                      <div style={{fontSize:'0.65rem',color:'rgba(248,113,113,0.7)',marginTop:'0.1rem'}}>
                                        MDD {mdd.toFixed(1)}%
                                      </div>
                                    )}
                                    {tc != null && (
                                      <div style={{fontSize:'0.62rem',color:'rgba(255,255,255,0.25)'}}>
                                        {tc}건
                                      </div>
                                    )}
                                    {!isVerified && (
                                      <div title={pd?.methodology_warning || ''}
                                        style={{fontSize:'0.6rem',color:'#fb923c',marginTop:'0.12rem'}}>
                                        명세 없음
                                      </div>
                                    )}
                                  </div>
                                )}
                              </td>
                            );
                          })}
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          );
        })()}

        {/* ══ 전략 상세 설명 뷰 ══ */}
        {viewMode === 'desc' && (() => {
          const items = catalog.length > 0 ? catalog : STRAT_DEFS.map(s => ({ key: s.key, label: s.label }));
          const colHdr = { padding:'0.5rem 0.7rem', background:'rgba(30,58,138,0.4)',
            borderBottom:'2px solid rgba(59,130,246,0.4)', fontSize:'0.75rem',
            fontWeight:700, color:'rgba(255,255,255,0.7)', textAlign:'left', whiteSpace:'nowrap' };
          const cellS = (extra={}) => ({
            padding:'0.5rem 0.7rem', borderBottom:'1px solid rgba(255,255,255,0.04)',
            fontSize:'0.75rem', verticalAlign:'top', lineHeight:1.6, ...extra
          });
          return (
            <div className="glass-panel" style={{overflow:'clip'}}>
              <div style={{padding:'0.7rem 1rem',borderBottom:'1px solid var(--glass-border)',fontWeight:700,fontSize:'0.88rem'}}>
                📋 전략별 상세 설명 (V1~V12, 13전략)
              </div>
              <div style={{overflowX:'auto'}}>
                <table style={{width:'100%',borderCollapse:'collapse',fontSize:'0.75rem'}}>
                  <thead>
                    <tr>
                      {['전략','전략 설명','진입 조건','매도 조건','손절선','추가 필터','적합 장세','주의사항'].map(h => (
                        <th key={h} style={colHdr}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((s, si) => (
                      <tr key={s.key}
                        style={{background: si%2===0 ? 'transparent' : 'rgba(255,255,255,0.015)'}}
                        onMouseOver={e => e.currentTarget.style.background='rgba(255,255,255,0.04)'}
                        onMouseOut={e => e.currentTarget.style.background= si%2===0 ? 'transparent' : 'rgba(255,255,255,0.015)'}>
                        <td style={cellS({fontWeight:700,color:'#fbbf24',whiteSpace:'nowrap',minWidth:'90px'})}>{s.label}</td>
                        <td style={cellS({color:'rgba(255,255,255,0.75)',maxWidth:'220px'})}>{s.desc||'-'}</td>
                        <td style={cellS({color:'rgba(255,255,255,0.6)',maxWidth:'200px'})}>{s.entry||'-'}</td>
                        <td style={cellS({color:'rgba(255,255,255,0.6)',maxWidth:'160px'})}>{s.exit||'-'}</td>
                        <td style={cellS({color:'#f87171',textAlign:'center',fontWeight:700})}>{s.stop_loss||'-'}</td>
                        <td style={cellS({color:'rgba(255,255,255,0.5)',maxWidth:'150px'})}>{s.filter||'-'}</td>
                        <td style={cellS({color:'#34d399',maxWidth:'140px'})}>{s.market_fit||'-'}</td>
                        <td style={cellS({color:'rgba(251,191,36,0.7)',maxWidth:'160px'})}>{s.warning||'-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div style={{padding:'0.6rem 1rem',borderTop:'1px solid var(--glass-border)',
                fontSize:'0.7rem',color:'rgba(255,255,255,0.4)'}}>
                * 모든 전략 예산: 1억원/전략, 종목당 1천만원 (최대 10종목 동시 보유)
                — 실제 데이터 확인 가능 시점 기준 백테스트
              </div>
            </div>
          );
        })()}

        {/* 매트릭스/list 공통 신규 실행 패널 */}
        {viewMode === 'list' && (
          <>
        {/* 설정 패널 */}
        <div className="glass-panel" style={{padding:'1rem 1.2rem'}}>
          <div style={{fontWeight:700,marginBottom:'0.7rem',fontSize:'0.85rem',color:'var(--accent-mint)'}}>
            🔧 백테스트 신규 실행
          </div>
          <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(180px,1fr))',gap:'0.6rem',marginBottom:'0.8rem'}}>
            {[
              { label:'시작일', key:'start_date', type:'date' },
              { label:'종료일', key:'end_date',   type:'date' },
              { label:'종목당 투자금(원)', key:'per_stock', type:'number' },
              { label:'실행명(선택)', key:'name', type:'text', placeholder:'예: 2023년 전략 테스트' },
            ].map(({ label, key, type, placeholder }) => (
              <div key={key}>
                <div style={{fontSize:'0.72rem',color:'var(--text-secondary)',marginBottom:'0.25rem'}}>{label}</div>
                <input type={type} value={form[key]}
                  placeholder={placeholder || ''}
                  onChange={e => setForm(p => ({...p, [key]: e.target.value}))}
                  style={{...inputS, width:'100%', boxSizing:'border-box'}} />
              </div>
            ))}
            <div>
              <div style={{fontSize:'0.72rem',color:'var(--text-secondary)',marginBottom:'0.25rem'}}>전략 선택 (V1~V12, 13전략)</div>
              <select value={form.strategy}
                onChange={e => setForm(p => ({...p, strategy: e.target.value}))}
                style={{...inputS, width:'100%', boxSizing:'border-box', cursor:'pointer'}}>
                {STRAT_DEFS.map(s => (
                  <option key={s.key} value={s.key}>{s.label}</option>
                ))}
              </select>
            </div>
          </div>
          <button onClick={startBacktest} disabled={running} style={{
            padding:'0.5rem 1.4rem', borderRadius:'8px', fontWeight:700, cursor: running ? 'not-allowed' : 'pointer',
            background: running ? 'rgba(100,116,139,0.2)' : 'rgba(245,158,11,0.2)',
            border: `1px solid ${running ? 'rgba(100,116,139,0.3)' : 'rgba(245,158,11,0.5)'}`,
            color: running ? 'var(--text-secondary)' : '#f59e0b', fontSize:'0.88rem',
          }}>
            {running ? '⏳ 백테스트 실행 중...' : '▶ 백테스트 실행'}
          </button>
          {running && (
            <div style={{marginTop:'0.5rem',fontSize:'0.72rem',color:'rgba(255,255,255,0.45)'}}>
              전 종목 스캔 중입니다. 수십 초 ~ 수 분 소요될 수 있습니다.
            </div>
          )}
        </div>
          </>
        )}

        {/* 결과 목록 — list 모드만 */}
        {viewMode === 'list' && list.length > 0 && (
          <div className="glass-panel" style={{overflow:'clip'}}>
            <div style={{padding:'0.6rem 1rem',borderBottom:'1px solid var(--glass-border)',
              display:'flex',alignItems:'center',gap:'0.5rem'}}>
              <span style={{fontWeight:700,fontSize:'0.85rem'}}>📋 저장된 백테스트 결과</span>
              <button onClick={loadList} style={{marginLeft:'auto',padding:'0.2rem 0.6rem',borderRadius:'5px',
                fontSize:'0.7rem',cursor:'pointer',border:'1px solid var(--glass-border)',
                background:'transparent',color:'var(--text-secondary)'}}>새로고침</button>
            </div>
            <table className="premium-table">
              <thead><tr>
                <th>실행명</th>
                <th>기간</th>
                <th style={{textAlign:'right'}}>총수익률</th>
                <th style={{textAlign:'right'}}>CAGR</th>
                <th style={{textAlign:'right'}}>승률</th>
                <th style={{textAlign:'right'}}>손익비</th>
                <th style={{textAlign:'right'}}>샤프</th>
                <th style={{textAlign:'right'}}>거래수</th>
                <th style={{textAlign:'right'}}>최대낙폭</th>
                <th>상태</th>
                <th></th>
              </tr></thead>
              <tbody>
                {list.map(r => {
                  const rd = (() => { try { return r.trades_json ? JSON.parse(r.trades_json) : null; } catch { return null; } })();
                  return (
                  <tr key={r.run_id} style={{cursor:'pointer'}} onClick={() => loadDetail(r.run_id)}>
                    <td style={{fontWeight:600}}>{r.name}</td>
                    <td style={{fontSize:'0.75rem',color:'var(--text-secondary)'}}>{r.start_date} ~ {r.end_date}</td>
                    <td style={{textAlign:'right',fontWeight:700,color:clr(r.total_return_pct||0)}}>
                      {r.total_return_pct != null ? (r.total_return_pct>=0?'+':'')+r.total_return_pct+'%' : '-'}
                    </td>
                    <td style={{textAlign:'right',color:clr(rd?.cagr??r.ann_return_pct??0)}}>
                      {rd?.cagr != null ? (rd.cagr>=0?'+':'')+rd.cagr+'%' : r.ann_return_pct != null ? (r.ann_return_pct>=0?'+':'')+r.ann_return_pct+'%' : '-'}
                    </td>
                    <td style={{textAlign:'right'}}>{r.win_rate != null ? r.win_rate+'%' : '-'}</td>
                    <td style={{textAlign:'right',color:'var(--accent-purple)'}}>
                      {rd?.pl_ratio != null ? rd.pl_ratio+'배' : '-'}
                    </td>
                    <td style={{textAlign:'right',color:rd?.sharpe>=1?'#22c55e':rd?.sharpe>=0?'#f59e0b':'#f87171'}}>
                      {rd?.sharpe != null ? rd.sharpe : '-'}
                    </td>
                    <td style={{textAlign:'right'}}>{r.total_trades ?? '-'}건</td>
                    <td style={{textAlign:'right',color:'#f87171'}}>
                      {r.max_drawdown_pct != null ? r.max_drawdown_pct+'%' : '-'}
                    </td>
                    <td>
                      <span style={{padding:'0.1rem 0.4rem',borderRadius:'4px',fontSize:'0.68rem',
                        background: r.status==='done' ? 'rgba(34,197,94,0.15)' :
                                    r.status==='running' ? 'rgba(251,191,36,0.15)' : 'rgba(239,68,68,0.15)',
                        color:      r.status==='done' ? '#22c55e' :
                                    r.status==='running' ? '#fbbf24' : '#ef4444'}}>
                        {r.status==='done' ? '완료' : r.status==='running' ? '실행중' : '오류'}
                      </span>
                    </td>
                    <td onClick={e => { e.stopPropagation(); deleteRun(r.run_id); }}
                      style={{cursor:'pointer',color:'#ef4444',fontSize:'0.8rem',padding:'0.3rem 0.6rem'}}>
                      ✕
                    </td>
                  </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* 상세 결과 — list 모드만 */}
        {viewMode === 'list' && detail && detail.status === 'done' && (
          <div style={{display:'flex',flexDirection:'column',gap:'0.75rem'}}>
            {/* 요약 카드 */}
            <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(150px,1fr))',gap:'0.6rem'}}>
              {[
                { label:'총수익률',     val:(detail.total_return_pct>=0?'+':'')+detail.total_return_pct+'%',  color:clr(detail.total_return_pct) },
                { label:'CAGR(연복리)', val:detail.cagr!=null ? (detail.cagr>=0?'+':'')+detail.cagr+'%' : (detail.ann_return_pct>=0?'+':'')+detail.ann_return_pct+'%', color:clr(detail.cagr??detail.ann_return_pct) },
                { label:'승률',         val:detail.win_rate+'%',          color:'var(--accent-mint)' },
                { label:'손익비',       val:detail.pl_ratio!=null ? detail.pl_ratio+'배' : '-', color:'var(--accent-purple)' },
                { label:'샤프지수',     val:detail.sharpe!=null ? detail.sharpe : '-', color:detail.sharpe>=1?'#22c55e':detail.sharpe>=0?'#f59e0b':'#f87171' },
                { label:'최대낙폭(MDD)',val:detail.max_drawdown_pct+'%',  color:'#f87171' },
                { label:'총 거래수',    val:detail.total_trades+'건',     color:'var(--text-primary)' },
                { label:'총손익',       val:fmtAmt(detail.total_profit_amt), color:clr(detail.total_profit_amt||0) },
              ].map(({ label, val, color }) => (
                <div key={label} className="glass-panel" style={{padding:'0.7rem 0.9rem'}}>
                  <div style={{fontSize:'0.67rem',color:'var(--text-secondary)',marginBottom:'0.2rem'}}>{label}</div>
                  <div style={{fontSize:'0.95rem',fontWeight:700,color}}>{val}</div>
                </div>
              ))}
            </div>

            {/* 월별 손익 */}
            {detail.monthly && detail.monthly.length > 0 && (
              <div className="glass-panel" style={{padding:'0.8rem 1rem'}}>
                <div style={{fontWeight:700,fontSize:'0.82rem',marginBottom:'0.6rem',color:'var(--accent-mint)'}}>
                  📅 월별 손익
                </div>
                <div style={{display:'flex',flexWrap:'wrap',gap:'0.4rem'}}>
                  {detail.monthly.map(m => (
                    <div key={m.month} style={{padding:'0.3rem 0.6rem',borderRadius:'5px',
                      background: m.profit>=0 ? 'rgba(239,68,68,0.12)' : 'rgba(59,130,246,0.12)',
                      border:`1px solid ${m.profit>=0 ? 'rgba(239,68,68,0.25)' : 'rgba(59,130,246,0.25)'}`,
                      textAlign:'center',minWidth:'80px'}}>
                      <div style={{fontSize:'0.65rem',color:'var(--text-secondary)'}}>{m.month}</div>
                      <div style={{fontSize:'0.78rem',fontWeight:700,
                        color: m.profit>=0 ? '#ef4444' : '#3b82f6'}}>
                        {m.profit>=0?'+':''}{Math.round(m.profit/10000).toLocaleString()}만
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* 상위/하위 종목 */}
            <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'0.75rem'}}>
              {[
                { label:'🏆 수익 상위 종목', data: detail.top_winners || [], color:'#ef4444' },
                { label:'💀 손실 종목', data: detail.top_losers || [], color:'#3b82f6' },
              ].map(({ label, data, color }) => (
                <div key={label} className="glass-panel" style={{padding:'0.8rem 1rem'}}>
                  <div style={{fontWeight:700,fontSize:'0.8rem',marginBottom:'0.5rem',color}}>{label}</div>
                  {data.length === 0 ? <div style={{fontSize:'0.75rem',color:'var(--text-secondary)'}}>없음</div> :
                    data.map(d => (
                      <div key={d.name} style={{display:'flex',justifyContent:'space-between',
                        padding:'0.2rem 0',borderBottom:'1px solid rgba(255,255,255,0.04)',
                        fontSize:'0.78rem'}}>
                        <span>{d.name}</span>
                        <span style={{fontWeight:700,color}}>{fmtAmt(d.profit)}</span>
                      </div>
                    ))
                  }
                </div>
              ))}
            </div>

            {/* 매매 내역 테이블 */}
            {detail.trades && detail.trades.length > 0 && (
              <div className="glass-panel" style={{overflow:'clip'}}>
                <div style={{padding:'0.6rem 1rem',borderBottom:'1px solid var(--glass-border)',
                  display:'flex',alignItems:'center',gap:'0.5rem'}}>
                  <span style={{fontWeight:700,fontSize:'0.82rem'}}>📋 매매 내역 (최근 200건)</span>
                  <button onClick={() => {
                    const rows = detail.trades.map(t => ({
                      종목코드:t.stock_code, 종목명:t.stock_name||'',
                      매수일:t.entry_date, 매도일:t.exit_date,
                      매수가:t.entry_price, 매도가:t.exit_price,
                      수량:t.qty, 수익률:t.profit_pct, 손익금:t.profit_amt,
                      매도사유:t.exit_reason
                    }));
                    const BOM='\uFEFF', ks=Object.keys(rows[0]);
                    const csv=BOM+ks.join(',')+'\n'+rows.map(r=>ks.map(k=>r[k]).join(',')).join('\n');
                    const a=document.createElement('a');
                    a.href=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8;'}));
                    a.download='backtest_trades.csv'; a.click();
                  }} style={{marginLeft:'auto',padding:'0.2rem 0.6rem',borderRadius:'5px',fontSize:'0.7rem',
                    cursor:'pointer',border:'1px solid rgba(45,212,191,0.3)',background:'rgba(45,212,191,0.08)',
                    color:'var(--accent-mint)'}}>⬇ CSV</button>
                </div>
                <table className="premium-table">
                  <thead><tr>
                    <th>종목명</th>
                    <th>매수일</th><th>매도일</th>
                    <th style={{textAlign:'right'}}>매수가</th>
                    <th style={{textAlign:'right'}}>매도가</th>
                    <th style={{textAlign:'right'}}>수익률</th>
                    <th style={{textAlign:'right'}}>손익금</th>
                    <th>매도사유</th>
                  </tr></thead>
                  <tbody>
                    {detail.trades.map((t, i) => (
                      <tr key={i}>
                        <td style={{fontWeight:600}}>{t.stock_name || t.stock_code}</td>
                        <td style={{fontSize:'0.75rem',color:'var(--text-secondary)'}}>{t.entry_date}</td>
                        <td style={{fontSize:'0.75rem',color:'var(--text-secondary)'}}>{t.exit_date}</td>
                        <td style={{textAlign:'right'}}>{Math.round(t.entry_price).toLocaleString()}</td>
                        <td style={{textAlign:'right'}}>{Math.round(t.exit_price).toLocaleString()}</td>
                        <td style={{textAlign:'right',fontWeight:700,color:clr(t.profit_pct)}}>
                          {t.profit_pct>=0?'+':''}{Number(t.profit_pct).toFixed(1)}%
                        </td>
                        <td style={{textAlign:'right',color:clr(t.profit_amt)}}>
                          {fmtAmt(t.profit_amt)}
                        </td>
                        <td style={{fontSize:'0.72rem',color:'rgba(255,255,255,0.45)'}}>{t.exit_reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {detail && detail.status === 'error' && (
          <div className="glass-panel" style={{padding:'1.5rem',color:'#f87171'}}>
            ⚠️ 백테스트 오류: {detail.summary_text}
          </div>
        )}

        {viewMode === 'desc' && <div className="glass-panel" style={{padding:'1rem 1.2rem'}}>
	          <div style={{fontWeight:700,fontSize:'0.9rem',marginBottom:'0.8rem',color:'var(--accent-mint)',
	            borderBottom:'1px solid var(--glass-border)',paddingBottom:'0.5rem'}}>
	            📘 전략별 상세 설명 (매수·매도 조건)
	          </div>
	          <div style={{padding:'0.65rem 0.75rem',borderRadius:6,background:'rgba(251,191,36,0.08)',border:'1px solid rgba(251,191,36,0.24)',color:'rgba(255,255,255,0.68)',fontSize:'0.72rem',lineHeight:1.55,marginBottom:'0.85rem'}}>
		            성과와 검증 등급은 선택된 run hash의 API 결과만 표시합니다. 아래 매수·매도 조건은 로직 설명용입니다.
	          </div>
	          {[
            {
              key:'V1 MA추세',
              badge:'V1',
              color:'#f59e0b',
              summary:'Minervini 추세추종 — MA 정배열 + 52주 신고가 근접 + KOSPI 시장필터',
              buy:[
                'MA20 > MA60 > MA120 정배열 (세 이평선 모두 순서대로)',
                '52주 고점 대비 현재가 비율 ≥ 65% (신고가 근접)',
                'RSI 42~88 (과매도/극과매수 제외)',
                '거래량 5일 평균 > 20일 평균 × 1.2배',
                'KOSPI MA120 상단 (하락장 매수 차단)',
                '시총 500억원 이상',
              ],
              sell:[
                '손절: -8%',
                '추적손절: 고점 대비 -10% (최소 +3% 수익, 20일 이후)',
                '초기부진: 20일 후에도 -10% 이하이고 MA20 아래면 청산',
                '보유기간 상한: 180일',
              ],
              note:'성과와 검증 등급은 선택된 run hash의 API 결과만 사용합니다.',
            },
            {
              key:'V2 가치매수',
              badge:'V2',
              color:'#34d399',
              summary:'Graham 내재가치 + 기관·외국인 동반 수급 — 저평가 가치주 발굴',
              buy:[
                'Graham 내재가치(√22.5×EPS×BPS) 대비 25% 이상 할인 OR (PBR<0.7 AND PER<10)',
                '영업이익 > 0 (수익성 확인)',
                '기관 5일 순매수 + 외국인 5일 순매수 합산 > 0 (동반 매수)',
                'MA60 대비 현재가 -5% 이내 (추세 확인)',
                '시총 500억원 이상',
              ],
              sell:[
                '손절: -8%',
                '추적손절: 고점 대비 -10%',
                '초기부진: 20일 후 -10% 이하이고 MA20 아래면 청산',
                '보유기간 상한: 180일',
              ],
              note:'성과와 검증 등급은 선택된 run hash의 API 결과만 사용합니다.',
            },
            {
              key:'V3 재무우량',
              badge:'V3',
              color:'#60a5fa',
              summary:'52주 위치 + 수익성 스코어 + 추세·수급 — 재무 우량 성장주',
              buy:[
                '52주 고점 대비 현재가 비율 ≥ 55%',
                '수익성 스코어 ≥ 2점: ROE>8%(+1), 영업이익률>5%(+1), 순이익률>3%(+1) 중 2개 이상',
                '영업이익 > 0',
                'MA20 > MA60 (추세 상승)',
                '기관 + 외국인 5일 합산 순매수 > 0',
              ],
              sell:[
                '손절: -8%',
                '추적손절: 고점 대비 -10%',
                '초기부진: 20일 후 -10% 이하이고 MA20 아래면 청산',
                '보유기간 상한: 180일',
              ],
              note:'성과와 검증 등급은 선택된 run hash의 API 결과만 사용합니다.',
            },
            {
              key:'V4 수급모멘텀',
              badge:'V4',
              color:'#a78bfa',
              summary:'기관·외국인 5일 동반 순매수 AND 조건 + MA 정배열 — 수급 주도 모멘텀',
              buy:[
                '기관 5일 순매수 > 0 AND 외국인 5일 순매수 > 0 (반드시 동반)',
                'MA20 > MA60 > MA120 정배열',
                '영업이익 > 0',
                '시총 500억원 이상',
              ],
              sell:[
                '손절: -8%',
                '추적손절: 고점 대비 -10%',
                '초기부진: 20일 후 -10% 이하이고 MA20 아래면 청산',
                '보유기간 상한: 180일',
              ],
              note:'성과와 검증 등급은 선택된 run hash의 API 결과만 사용합니다.',
            },
            {
              key:'V5 복합콤보',
              badge:'V5',
              color:'#8b5cf6',
              summary:'Minervini 추세 + Graham 가치 + RS 수급 삼중 필터 + KOSPI 시장필터 — 하락장 손실 제로',
              buy:[
                'MA20 > MA60 > MA120 정배열',
                '52주 고점 대비 현재가 비율 ≥ 70%',
                'RSI ≥ 50',
                '거래량 > 20일 평균 2.0배',
                'Graham 내재가치 25%+ 할인 OR (PBR<0.7 AND PER<10) [가치 조건]',
                'RS 상대강도: 기관 OR 외국인 5일 순매수 중 하나 이상 [수급 조건]',
                'KOSPI MA60 상단 (하락장 매수 원천 차단)',
              ],
              sell:[
                '손절: -6%',
                '추적손절: 고점 대비 -10% (최소 +3% 수익, 5일 이후)',
                '익절: +15%',
                'MA60 붕괴 시 청산',
              ],
              note:'성과와 검증 등급은 선택된 run hash의 API 결과만 사용합니다.',
            },
            {
              key:'V6 이익폭발',
              badge:'V6',
              color:'#fbbf24',
              summary:'영업이익 YoY 50%+ + 매출 YoY 10%+ 2분기 연속 고성장주',
              buy:[
                '영업이익 YoY ≥ 50% (직전 분기 대비 전년동기)',
                '매출 YoY ≥ 10%',
                '2분기 연속 위 조건 충족',
                '현재가 > MA60',
                '거래량 > 10일 평균 1.3배',
                'KOSPI MA120 상단',
              ],
              sell:[
                '손절: -10%',
                '추적손절: 고점 대비 -10%',
                '초기부진: 20일 후 -10% 이하이고 MA20 아래면 청산',
                '보유기간 상한: 180일',
              ],
              note:'성과와 검증 등급은 선택된 run hash의 API 결과만 사용합니다.',
            },
            {
              key:'V7 이익가속',
              badge:'V7',
              color:'#10b981',
              summary:'Earnings Acceleration — 3분기 연속 OP 증가 + 마진 레버리지 + 추세 전환',
              buy:[
                '3분기 연속 영업이익 증가 (Ball & Brown 이익모멘텀)',
                '최근 분기 영업이익 성장률 > 20%',
                '이익성장률 > 매출성장률 (마진 레버리지 확인)',
                'MA60 > MA120 (추세 전환)',
                '기관 OR 외국인 3일 순매수',
                '52주 위치 50~88% (신고가 과열 제외)',
              ],
              sell:[
                '손절: -8%',
                '추적손절: 고점 대비 -10%',
                '초기부진: 20일 후 -10% 이하이고 MA20 아래면 청산',
                '보유기간 상한: 180일',
              ],
              note:'성과와 검증 등급은 선택된 run hash의 API 결과만 사용합니다.',
            },
            {
              key:'V8 52W돌파',
              badge:'V8',
              color:'#fb923c',
              summary:'52주 고점 근접 모멘텀 — 실증 분석 기반 역발상 포기, 모멘텀 팩터 채택',
              buy:[
                '52주 고점 대비 현재가 ≥ 65% (상위 35% 구간)',
                'MA60 > MA120 × 1.02 (추세 확인)',
                '현재가가 MA60 대비 +3%~+25% 범위 (과열 제외)',
                '거래량 5일 평균 > 20일 평균 × 1.3배',
                '10일 모멘텀 양수',
              ],
              sell:[
                '손절: -8%',
                '추적손절: 고점 대비 -10%',
                '초기부진: 20일 후 -10% 이하이고 MA20 아래면 청산',
                '보유기간 상한: 180일',
              ],
              note:'성과와 검증 등급은 선택된 run hash의 API 결과만 사용합니다.',
            },
            {
              key:'V9 수출선행',
              badge:'V9',
              color:'#94a3b8',
              summary:'HS무역통계 월별수출 변곡점 + MA60 — 실제 펀더멘탈 선행지표',
              buy:[
                '수출 YoY ≥ 8% (최근 3개월 평균)',
                '수출 가속도: 최근 YoY가 이전 YoY 대비 +20%p 이상 반등',
                'HS무역통계 발표 2개월 지연 보정 (미래 참조 방지)',
                '현재가 MA60 ± 20% 범위',
                'RSI 42~65',
                '영업이익 > 0',
              ],
              sell:[
                '손절: -8%',
                '수출 YoY < -5% 2개월 연속 시 청산',
                '추적손절: 고점 대비 -10%',
              ],
              note:'성과와 검증 등급은 선택된 run hash의 API 결과만 사용합니다.',
            },
            {
              key:'V10 섹터대세',
              badge:'V10',
              color:'#64748b',
              summary:'섹터 알파 + 강세 종목 진입 — 후행성 주의 ⚠️ 비권장',
              buy:[
                'KOSPI 3개월 대비 섹터 alpha ≥ 15%',
                '해당 섹터 내 개별종목 거래량 폭증',
                '현재가 > MA60',
              ],
              sell:['손절: -8%', '추적손절: 고점 대비 -10%', '섹터 알파 반전 시'],
              note:'성과와 검증 등급은 선택된 run hash의 API 결과만 사용합니다.',
            },
            {
              key:'V11 복합스코어링',
              badge:'V11',
              color:'#22d3ee',
              summary:'100점 다중팩터 종합 스코어링',
              buy:[
                '종합 스코어 ≥ 65점 (100점 만점)',
                '추세 팩터: MA 정배열·52W 위치 (최대 25점)',
                '재무 팩터: 수익성·성장성 스코어 (최대 25점)',
                '수급 팩터: 기관+외국인 합산 순매수 (최대 25점)',
                '절대 모멘텀 필터: 3개월 -15% 또는 1개월 -5% 이상 하락주 진입 금지',
                '기관+외국인 동반 순매수 시 +20점, 한쪽만 +5점',
              ],
              sell:[
                '손절: -8%',
                '추적손절: 고점 대비 -10%',
                '초기부진: 20일 후 -10% 이하이고 MA20 아래면 청산',
                '보유기간 상한: 180일',
              ],
              note:'성과와 검증 등급은 선택된 run hash의 API 결과만 사용합니다.',
            },
            {
              key:'V12 골든크로스',
              badge:'GC',
              color:'#f97316',
              summary:'MA20이 MA60을 상향 돌파(15일 내) — 추세 전환 초기 포착',
              buy:[
                'MA20이 MA60을 상향 돌파 (최근 15일 이내)',
                '돌파 전 MA60 하락 → 돌파 후 전환 확인',
                'RSI 40~75 (과열 제외)',
                '거래량 > 20일 평균 1.2배',
                'KOSPI MA60 상단',
              ],
              sell:[
                '익절: +25% (일반)', '+30% (KOSPI 강세장)',
                '추적손절: 고점 대비 -10% (일반)', '-12% (강세장)',
                '보유기간 상한: 180일',
              ],
              note:'성과와 검증 등급은 선택된 run hash의 API 결과만 사용합니다.',
            },
            {
              key:'Meta-V 레짐 적응형',
              badge:'META',
              color:'#818cf8',
              summary:'BULL/BEAR/NEUTRAL 장세 감지 → 자동 전략 전환 — 하락장 손실 과다 ⚠️ 비권장',
              buy:[
                'KOSPI MA60 대비 위치로 장세 판별: +5%↑=BULL, -5%↓=BEAR, 그 외=NEUTRAL',
                'BULL 장: V5 복합콤보 로직 (모멘텀+수급 혼합)',
                'NEUTRAL 장: V2 가치매수 로직 (Graham 내재가치)',
                'BEAR 장: 현금 100% (신규 매수 중단, 기존 포지션 청산)',
              ],
              sell:[
                '손절: -8%',
                '추적손절: 고점 대비 -10%',
                '보유기간 상한: 180일',
                '장세 BEAR 전환 시 전 포지션 강제 청산',
              ],
              note:'성과와 검증 등급은 선택된 run hash의 API 결과만 사용합니다.',
            },
            {
              key:'V-SECTOR 주도섹터',
              badge:'VS',
              color:'#c084fc',
              summary:'섹터 로테이션과 수급 선도 종목을 추종',
              buy:[
                '7개 커스텀 섹터 그룹(반도체/전력기기/2차전지/화장품/방산/조선/바이오)에서 RS+수급+수출 복합 스코어 55점+',
                '해당 섹터 내 최근 90일 외국인+기관 수급 선도 TOP3 종목 진입',
                '섹터 스코어 = RS12w(30) + 거래량확장(20) + 기관외인수급(30) + 수출YoY(20)',
                'KOSPI 상대강도 기준 섹터 RS 12주 위치 판단',
              ],
              sell:[
                '섹터 스코어 30점 미만으로 하락: 최소 44일 보유 후 EXIT',
                '손절: -8%',
                '추적손절: 고점 대비 -12%',
                '보유기간 상한: 180일',
              ],
              note:'성과와 검증 등급은 선택된 run hash의 API 결과만 사용합니다.',
            },
            {
              key:'V13 고수익집중',
              badge:'V13',
              color:'#22c55e',
              summary:'임원매수 공시 + 성장섹터 + 거래량 급증 + MA20 반등 확인 — 실제 3배 종목 데이터 기반 최적화',
              buy:[
                '최근 180일 임원매수 공시 존재 (dart_insider_holdings)',
                'IT·의료·경기소비재·산업재 성장 섹터 한정',
                '현재가 ≥ MA20 (반등 확인, 낙폭후 회복 신호)',
                '현재가 ≥ 52주 저점 × 1.20 (저점 대비 20%+ 이탈)',
                '진입일 거래량 ≥ 20일 평균 × 1.3배 (거래 급증 확인)',
                '일평균 거래대금 20억원+ (유동성 필터)',
                '계약공시(최근 30일 signal≥2) 또는 수주잔고 존재 (촉매)',
                '시총 200억원 이상',
              ],
              sell:[
                '추적손절: 고점 대비 -35% (이익 10%+ 달성 후 발동)',
                '추적손절 확장: 이익 100%+ 달성 시 고점 대비 -40%',
                '손절: -15%',
                '익절 없음 — Trail로만 청산 (25배 종목 조기청산 방지)',
                '보유기간 상한: 400일 (데이터: 고점 도달 평균 280~424일)',
              ],
              note:'성과와 검증 등급은 선택된 run hash의 API 결과만 사용합니다.',
            },
            {
              key:'V-RECOVERY 낙폭반등',
              badge:'VR',
              color:'#fb7185',
              summary:'MA60 대비 낙폭과대와 거래량 급증 반등을 포착',
              buy:[
                '현재가 ≤ MA60 × 0.80 이하 (MA60 대비 -20%+ 낙폭)',
                '현재가 ≥ MA60 × 0.35 이상 (과도한 폭락 제외)',
                '52주 저점 대비 현재가 ≤ +40% (저점권 확인)',
                '진입일 거래량 ≥ 20일 평균 × 2.0배 (반등 거래량 급증)',
                '최근 3일 중 2일 이상 상승 마감 (반등 확인)',
                '시총 200억원 이상',
              ],
              sell:[
                '추적손절: 고점 대비 -20% (이익 50%+ 달성 시 -25%로 확장)',
                '손절: -12%',
                '익절: +80%',
                '보유기간 상한: 240일',
              ],
              note:'성과와 검증 등급은 선택된 run hash의 API 결과만 사용합니다.',
            },
            {
              key:'공통 규칙',
              badge:'공통',
              color:'rgba(255,255,255,0.5)',
              summary:'모든 전략 공통 적용 사항',
              buy:[
                '시총 500억원 이상 (소형주 제외)',
                '월별 신규 매수 최대 10개 종목 (점수순 우선선택)',
                '동시 보유 최대 10개 종목',
                '재무 데이터 공시 지연 반영 (Q1→5월, Q2→8월, Q3→11월, 연간→익년3월)',
                '수급 데이터: price_history.inst_net_buy_amt 사용 (KIS 검증값)',
              ],
              sell:[
                '강제 청산: 시뮬레이션 종료일에 전 포지션 청산',
                '수익률 정의와 비용 모델은 선택된 run 명세를 따름',
              ],
              note:'데이터 가용기간과 누락 처리 방식은 선택된 run 명세와 검증 아티팩트에서 확인합니다.',
            },
          ].map(s => (
            <div key={s.key} style={{marginBottom:'1rem',paddingBottom:'1rem',
              borderBottom:'1px solid rgba(255,255,255,0.05)'}}>
              <div style={{display:'flex',alignItems:'center',gap:'0.5rem',marginBottom:'0.4rem'}}>
                <span style={{padding:'0.1rem 0.5rem',borderRadius:'4px',fontSize:'0.7rem',fontWeight:700,
                  background:`${s.color}22`,border:`1px solid ${s.color}55`,color:s.color}}>
                  {s.badge.toUpperCase()}
                </span>
                <span style={{fontWeight:700,fontSize:'0.85rem'}}>{s.key}</span>
                <span style={{fontSize:'0.72rem',color:'rgba(255,255,255,0.45)',marginLeft:'0.3rem'}}>
                  — {s.summary}
                </span>
              </div>
              <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'0.5rem',marginBottom:'0.35rem'}}>
                <div>
                  <div style={{fontSize:'0.68rem',color:'rgba(34,197,94,0.7)',fontWeight:600,marginBottom:'0.2rem'}}>
                    📈 매수 조건
                  </div>
                  {s.buy.map((b,i) => (
                    <div key={i} style={{fontSize:'0.7rem',color:'rgba(255,255,255,0.6)',
                      paddingLeft:'0.5rem',marginBottom:'0.12rem'}}>
                      • {b}
                    </div>
                  ))}
                </div>
                <div>
                  <div style={{fontSize:'0.68rem',color:'rgba(248,113,113,0.7)',fontWeight:600,marginBottom:'0.2rem'}}>
                    📉 매도 조건
                  </div>
                  {s.sell.map((sl,i) => (
                    <div key={i} style={{fontSize:'0.7rem',color:'rgba(255,255,255,0.6)',
                      paddingLeft:'0.5rem',marginBottom:'0.12rem'}}>
                      • {sl}
                    </div>
                  ))}
                </div>
              </div>
              {s.note && (
                <div style={{fontSize:'0.68rem',color:'rgba(251,191,36,0.75)',
                  padding:'0.2rem 0.5rem',background:'rgba(251,191,36,0.06)',
                  borderRadius:'4px',borderLeft:'2px solid rgba(251,191,36,0.3)'}}>
                  💡 {s.note}
                </div>
              )}
            </div>
          ))}
        </div>}
      </div>
    );
  };

export default BacktestView;
