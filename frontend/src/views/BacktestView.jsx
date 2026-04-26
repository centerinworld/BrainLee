import React from 'react';
import { Activity } from 'lucide-react';

const API = (path) => path;

const BacktestView = () => {
  const STRATEGIES = [
    { id:'v5', label:'Logic #1', name:'AI 콤보 v5',     color:'#f59e0b',
      desc:'눌림목+가치+절대수급 스나이퍼 (MA정배열·RSI<50·Graham·수급강도 교집합)' },
    { id:'v6', label:'Logic #2', name:'국면 적응형',     color:'#a78bfa',
      desc:'KOSPI 4단계 regime별 매수기준·익절·손절 자동전환 (강세/중립/약세/대하락)' },
    { id:'v7', label:'Logic #3', name:'멀티팩터',        color:'#34d399',
      desc:'해외동조(25%)+기술(30%)+가치(17%)+HS수출(15%)+고용(10%) 가중 합산' },
    { id:'v8', label:'Logic #4', name:'눌림목(Pullback)', color:'#fb923c',
      desc:'MA60>MA120>MA200 정배열·MA20±2% 눌림목·RSI<50·절대수급 필수' },
    { id:'v9', label:'Logic #5', name:'텐배거 헌터',    color:'#ec4899',
      desc:'MA200 상방·52주고가88%·거래량×3·영업흑자 OR 매출YoY+30%·시총1.5조이하 / 수익극대화 청산' },
  ];
  const PERIODS = [
    { id:'bear_2022',    label:'① 하락장',    badge:'2022 하락·1년',   start:'2022-01-01', end:'2022-12-31',
      desc:'대세 하락장 — 손절 로직과 MDD 방어력 테스트', color:'#f87171' },
    { id:'sideways_2023',label:'② 박스피',    badge:'2023~24 횡보·1년', start:'2023-08-01', end:'2024-07-31',
      desc:'횡보장 — 추적손절·익절이 노이즈에 털리지 않는지 테스트', color:'#facc15' },
    { id:'bull_2023',    label:'③ 상승장',    badge:'2023 상승·7개월', start:'2023-01-01', end:'2023-07-31',
      desc:'테마 주도 대세 상승장 — 주도주 포착 수익 극대화 테스트', color:'#34d399' },
    { id:'volatile_2024',label:'④ 고변동성',  badge:'2024H2 변동·5개월', start:'2024-08-01', end:'2024-12-31',
      desc:'극심한 변동성 속 손익비(R/R) 방어 테스트', color:'#fb923c' },
    { id:'value_2024',   label:'⑤ 가치주 장세', badge:'2024 가치·1년', start:'2024-01-01', end:'2024-12-31',
      desc:'기업 밸류업 구간 — 실적·가치주 주도 장세 테스트', color:'#a78bfa' },
  ];

  const [list,        setList]        = React.useState([]);
  const [detail,      setDetail]      = React.useState(null);
  const [running,     setRunning]     = React.useState(false);
  const [runAllState, setRunAllState] = React.useState(null); // {total, done, ids}
  const [activeTab,   setActiveTab2]  = React.useState('config'); // config | matrix
  const [form, setForm] = React.useState({
    start_date: '2023-04-01',
    end_date:   '2025-12-31',
    per_stock:  10000000,
    strategy:   'v5',
    name:       '',
  });

  const loadList = async () => {
    try {
      const r = await fetch(API('/api/backtest/list'));
      if (r.ok) setList(await r.json());
    } catch(e) {}
  };

  React.useEffect(() => { loadList(); }, []);

  const applyPeriod = (p) => setForm(f => ({...f, start_date: p.start, end_date: p.end}));

  const startBacktest = async () => {
    setRunning(true);
    try {
      const strat = STRATEGIES.find(s => s.id === form.strategy);
      const r = await fetch(API('/api/backtest/run'), {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          start_date: form.start_date,
          end_date:   form.end_date,
          per_stock:  Number(form.per_stock),
          strategy:   form.strategy,
          name:       form.name || `[${strat?.label} ${strat?.name}] ${form.start_date.slice(0,7)}~${form.end_date.slice(0,7)}`,
        }),
      });
      if (!r.ok) { setRunning(false); return; }
      const { run_id } = await r.json();
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
    } catch(e) { setRunning(false); }
  };

  const startRunAll = async () => {
    try {
      const r = await fetch(API('/api/backtest/run-all'), {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ per_stock: Number(form.per_stock) }),
      });
      if (!r.ok) return;
      const { run_ids, total } = await r.json();
      setRunAllState({ total, done: 0, ids: run_ids });
      setActiveTab2('matrix');
      const iv = setInterval(async () => {
        await loadList();
        const freshList = await fetch(API('/api/backtest/list')).then(r => r.json()).catch(() => []);
        const done = freshList.filter(x => run_ids.includes(x.run_id) && x.status === 'done').length;
        const errs = freshList.filter(x => run_ids.includes(x.run_id) && x.status === 'error').length;
        setRunAllState(p => p ? { ...p, done } : null);
        if (done + errs >= total) {
          clearInterval(iv);
          setList(freshList);
        }
      }, 4000);
    } catch(e) {}
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

  // ── 전체 실행 결과 매트릭스 ────────────────────────────────────
  const MatrixView = () => {
    const allRunIds = runAllState?.ids || [];
    const matrixItems = list.filter(x => allRunIds.includes(x.run_id));

    return (
      <div style={{display:'flex',flexDirection:'column',gap:'0.75rem'}}>
        {runAllState && (
          <div style={{padding:'0.6rem 1rem',background:'rgba(245,158,11,0.08)',
            border:'1px solid rgba(245,158,11,0.25)',borderRadius:'8px',
            fontSize:'0.78rem',color:'#fbbf24'}}>
            ⏳ 전체 실행 진행: {runAllState.done} / {runAllState.total} 완료
            <div style={{marginTop:'0.4rem',height:'4px',background:'rgba(255,255,255,0.1)',borderRadius:'2px'}}>
              <div style={{height:'100%',borderRadius:'2px',background:'#f59e0b',
                width:`${(runAllState.done/Math.max(runAllState.total,1))*100}%`,
                transition:'width 0.5s'}} />
            </div>
          </div>
        )}
        <div style={{overflowX:'auto'}}>
          <table className="premium-table" style={{minWidth:'900px'}}>
            <thead>
              <tr>
                <th>기간</th>
                {STRATEGIES.map(s => (
                  <th key={s.id} style={{textAlign:'center',color:s.color}}>
                    {s.label}<br/><span style={{fontSize:'0.65rem',fontWeight:400}}>{s.name}</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {PERIODS.map(p => (
                <tr key={p.id}>
                  <td>
                    <div style={{fontWeight:700,fontSize:'0.8rem',color:p.color}}>{p.label}</div>
                    <div style={{fontSize:'0.65rem',color:'var(--text-secondary)'}}>{p.badge}</div>
                  </td>
                  {STRATEGIES.map(s => {
                    const nameKey = `${s.label} ${s.name}`;
                    const row = matrixItems.find(x =>
                      x.name && x.name.includes(s.label) && x.name.includes(p.label)
                    );
                    if (!row) return (
                      <td key={s.id} style={{textAlign:'center',color:'rgba(255,255,255,0.2)'}}>
                        {(runAllState?.done < runAllState?.total) ? '⏳' : '-'}
                      </td>
                    );
                    if (row.status === 'error') return <td key={s.id} style={{textAlign:'center',color:'#f87171',fontSize:'0.7rem'}}>오류</td>;
                    if (row.status !== 'done') return <td key={s.id} style={{textAlign:'center',color:'#fbbf24',fontSize:'0.7rem'}}>실행중</td>;
                    const ret = row.total_return_pct;
                    const mdd = row.max_drawdown_pct;
                    return (
                      <td key={s.id} style={{textAlign:'center',cursor:'pointer'}} onClick={() => loadDetail(row.run_id)}>
                        <div style={{fontWeight:800,fontSize:'0.88rem',color:clr(ret)}}>
                          {ret != null ? (ret>=0?'+':'')+ret+'%' : '-'}
                        </div>
                        <div style={{fontSize:'0.65rem',color:'rgba(255,255,255,0.45)'}}>
                          MDD {mdd != null ? mdd+'%' : '-'} · {row.win_rate != null ? row.win_rate+'%승' : ''}
                        </div>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    );
  };

  return (
    <div className="fade-in" style={{display:'flex',flexDirection:'column',gap:'1rem'}}>
      {/* 헤더 */}
      <div className="glass-panel" style={{padding:'0.8rem 1.2rem'}}>
        <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',flexWrap:'wrap',gap:'0.5rem'}}>
          <div style={{display:'flex',alignItems:'center',gap:'0.6rem'}}>
            <Activity size={18} color="#f59e0b" />
            <h2 style={{fontSize:'1rem',fontWeight:700}}>📊 백테스트</h2>
          </div>
          <div style={{display:'flex',gap:'0.4rem'}}>
            {['config','matrix'].map(t => (
              <button key={t} onClick={() => setActiveTab2(t)} style={{
                padding:'0.3rem 0.8rem',borderRadius:'6px',fontSize:'0.75rem',cursor:'pointer',
                background: activeTab === t ? 'rgba(245,158,11,0.2)' : 'transparent',
                border:`1px solid ${activeTab === t ? 'rgba(245,158,11,0.5)' : 'var(--glass-border)'}`,
                color: activeTab === t ? '#f59e0b' : 'var(--text-secondary)',
              }}>
                {t === 'config' ? '⚙️ 개별 설정' : '📈 전체 매트릭스'}
              </button>
            ))}
          </div>
        </div>
        <div style={{marginTop:'0.5rem',padding:'0.4rem 0.8rem',background:'rgba(251,191,36,0.07)',
          border:'1px solid rgba(251,191,36,0.2)',borderRadius:'6px',
          fontSize:'0.7rem',color:'rgba(251,191,36,0.85)',lineHeight:1.5}}>
          ⚠️ 과거 데이터 시뮬레이션 — <strong>미래 수익 보장 안 함</strong> ·
          Logic#1(v5): 스나이퍼 · Logic#2(v6): 국면적응 · Logic#3(v7): 멀티팩터 · Logic#4(v8): 눌림목
        </div>
      </div>

      {activeTab === 'matrix' && <MatrixView />}

      {activeTab === 'config' && (
      <div style={{display:'flex',flexDirection:'column',gap:'0.75rem'}}>
        {/* 전략 선택 */}
        <div className="glass-panel" style={{padding:'1rem 1.2rem'}}>
          <div style={{fontWeight:700,marginBottom:'0.7rem',fontSize:'0.82rem',color:'var(--accent-mint)'}}>
            🎯 전략 선택
          </div>
          <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(220px,1fr))',gap:'0.5rem'}}>
            {STRATEGIES.map(s => (
              <button key={s.id} onClick={() => setForm(p => ({...p, strategy: s.id}))} style={{
                padding:'0.7rem 0.9rem', borderRadius:'8px', cursor:'pointer', textAlign:'left',
                background: form.strategy === s.id ? `rgba(${s.id==='v5'?'245,158,11':s.id==='v6'?'167,139,250':s.id==='v7'?'52,211,153':'251,146,60'},0.15)` : 'rgba(255,255,255,0.04)',
                border:`1px solid ${form.strategy === s.id ? s.color : 'var(--glass-border)'}`,
                transition:'all 0.15s',
              }}>
                <div style={{display:'flex',alignItems:'center',gap:'0.5rem',marginBottom:'0.2rem'}}>
                  <span style={{fontSize:'0.78rem',fontWeight:800,color:s.color}}>{s.label}</span>
                  <span style={{fontSize:'0.78rem',fontWeight:700,color:'#fff'}}>{s.name}</span>
                  {form.strategy === s.id && <span style={{marginLeft:'auto',fontSize:'0.65rem',color:s.color}}>✓ 선택됨</span>}
                </div>
                <div style={{fontSize:'0.68rem',color:'var(--text-secondary)',lineHeight:1.4}}>{s.desc}</div>
              </button>
            ))}
          </div>
        </div>

        {/* 기간 프리셋 */}
        <div className="glass-panel" style={{padding:'1rem 1.2rem'}}>
          <div style={{fontWeight:700,marginBottom:'0.7rem',fontSize:'0.82rem',color:'#a78bfa'}}>
            📅 테스트 기간 선택
          </div>
          <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(190px,1fr))',gap:'0.5rem',marginBottom:'0.75rem'}}>
            {PERIODS.map(p => {
              const isActive = form.start_date === p.start && form.end_date === p.end;
              return (
                <button key={p.id} onClick={() => applyPeriod(p)} style={{
                  padding:'0.65rem 0.9rem', borderRadius:'8px', cursor:'pointer', textAlign:'left',
                  background: isActive ? `rgba(167,139,250,0.12)` : 'rgba(255,255,255,0.04)',
                  border:`1px solid ${isActive ? p.color : 'var(--glass-border)'}`,
                  transition:'all 0.15s',
                }}>
                  <div style={{display:'flex',alignItems:'center',gap:'0.4rem',marginBottom:'0.15rem'}}>
                    <span style={{fontSize:'0.78rem',fontWeight:800,color:p.color}}>{p.label}</span>
                    {isActive && <span style={{marginLeft:'auto',fontSize:'0.63rem',color:p.color}}>✓</span>}
                  </div>
                  <div style={{fontSize:'0.68rem',color:'rgba(255,255,255,0.55)',marginBottom:'0.1rem'}}>{p.badge}</div>
                  <div style={{fontSize:'0.65rem',color:'var(--text-secondary)',lineHeight:1.3}}>{p.desc}</div>
                </button>
              );
            })}
          </div>
          {/* 커스텀 날짜 입력 */}
          <div style={{display:'flex',gap:'0.6rem',flexWrap:'wrap',alignItems:'center'}}>
            <div>
              <div style={{fontSize:'0.7rem',color:'var(--text-secondary)',marginBottom:'0.2rem'}}>시작일</div>
              <input type="date" value={form.start_date}
                onChange={e => setForm(p => ({...p, start_date: e.target.value}))}
                style={{...inputS}} />
            </div>
            <div>
              <div style={{fontSize:'0.7rem',color:'var(--text-secondary)',marginBottom:'0.2rem'}}>종료일</div>
              <input type="date" value={form.end_date}
                onChange={e => setForm(p => ({...p, end_date: e.target.value}))}
                style={{...inputS}} />
            </div>
            <div>
              <div style={{fontSize:'0.7rem',color:'var(--text-secondary)',marginBottom:'0.2rem'}}>종목당 투자금(원)</div>
              <input type="number" value={form.per_stock}
                onChange={e => setForm(p => ({...p, per_stock: e.target.value}))}
                style={{...inputS, width:'140px'}} />
            </div>
            <div>
              <div style={{fontSize:'0.7rem',color:'var(--text-secondary)',marginBottom:'0.2rem'}}>실행명(선택)</div>
              <input type="text" value={form.name} placeholder="예: 2023년 전략 테스트"
                onChange={e => setForm(p => ({...p, name: e.target.value}))}
                style={{...inputS, width:'200px'}} />
            </div>
          </div>
        </div>

        {/* 실행 버튼 */}
        <div style={{display:'flex',gap:'0.6rem',flexWrap:'wrap',alignItems:'center'}}>
          <button onClick={startBacktest} disabled={running} style={{
            padding:'0.55rem 1.6rem', borderRadius:'8px', fontWeight:700,
            cursor: running ? 'not-allowed' : 'pointer',
            background: running ? 'rgba(100,116,139,0.2)' : 'rgba(245,158,11,0.2)',
            border: `1px solid ${running ? 'rgba(100,116,139,0.3)' : 'rgba(245,158,11,0.5)'}`,
            color: running ? 'var(--text-secondary)' : '#f59e0b', fontSize:'0.88rem',
          }}>
            {running ? '⏳ 실행 중...' : '▶ 개별 백테스트 실행'}
          </button>
          <button onClick={startRunAll} disabled={running} style={{
            padding:'0.55rem 1.6rem', borderRadius:'8px', fontWeight:700,
            cursor: running ? 'not-allowed' : 'pointer',
            background:'rgba(167,139,250,0.15)',
            border:'1px solid rgba(167,139,250,0.45)',
            color:'#a78bfa', fontSize:'0.88rem',
          }}>
            ⚡ 전체 실행 (4전략 × 5기간 = 20회)
          </button>
          {running && (
            <span style={{fontSize:'0.72rem',color:'rgba(255,255,255,0.4)'}}>
              전 종목 스캔 중 — 전략별 수십 초~수 분 소요
            </span>
          )}
        </div>
      </div>
      )}

      {/* 결과 목록 */}
      {list.length > 0 && (
        <div className="glass-panel" style={{overflow:'auto'}}>
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
              <th style={{textAlign:'right'}}>MDD</th>
              <th>상태</th>
              <th></th>
            </tr></thead>
            <tbody>
              {list.map(r => (
                <tr key={r.run_id} style={{cursor:'pointer'}} onClick={() => loadDetail(r.run_id)}>
                  <td style={{fontWeight:600,maxWidth:'260px',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{r.name}</td>
                  <td style={{fontSize:'0.72rem',color:'var(--text-secondary)'}}>{r.start_date?.slice(0,7)} ~ {r.end_date?.slice(0,7)}</td>
                  <td style={{textAlign:'right',fontWeight:700,color:clr(r.total_return_pct||0)}}>
                    {r.total_return_pct != null ? (r.total_return_pct>=0?'+':'')+r.total_return_pct+'%' : '-'}
                  </td>
                  <td style={{textAlign:'right',color:clr(r.cagr??r.ann_return_pct??0)}}>
                    {r.cagr != null ? (r.cagr>=0?'+':'')+r.cagr+'%' : r.ann_return_pct != null ? (r.ann_return_pct>=0?'+':'')+r.ann_return_pct+'%' : '-'}
                  </td>
                  <td style={{textAlign:'right'}}>{r.win_rate != null ? r.win_rate+'%' : '-'}</td>
                  <td style={{textAlign:'right',color:'#a78bfa'}}>
                    {r.pl_ratio != null ? r.pl_ratio+'배' : '-'}
                  </td>
                  <td style={{textAlign:'right',color:r.sharpe>=1?'#22c55e':r.sharpe>=0?'#f59e0b':'#f87171'}}>
                    {r.sharpe != null ? r.sharpe : '-'}
                  </td>
                  <td style={{textAlign:'right'}}>{r.total_trades ?? '-'}건</td>
                  <td style={{textAlign:'right',color:'#f87171'}}>
                    {r.max_drawdown_pct != null ? r.max_drawdown_pct+'%' : '-'}
                  </td>
                  <td>
                    <span style={{padding:'0.1rem 0.4rem',borderRadius:'4px',fontSize:'0.68rem',
                      background: r.status==='done' ? 'rgba(34,197,94,0.15)' :
                                  r.status==='running'||r.status==='queued' ? 'rgba(251,191,36,0.15)' : 'rgba(239,68,68,0.15)',
                      color:      r.status==='done' ? '#22c55e' :
                                  r.status==='running'||r.status==='queued' ? '#fbbf24' : '#ef4444'}}>
                      {r.status==='done' ? '완료' : r.status==='running' ? '실행중' : r.status==='queued' ? '대기' : '오류'}
                    </span>
                  </td>
                  <td onClick={e => { e.stopPropagation(); deleteRun(r.run_id); }}
                    style={{cursor:'pointer',color:'#ef4444',fontSize:'0.8rem',padding:'0.3rem 0.6rem'}}>
                    ✕
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 상세 결과 */}
      {detail && detail.status === 'done' && (
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
            <div className="glass-panel" style={{overflow:'auto'}}>
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
                        {t.profit_pct>=0?'+':''}{t.profit_pct}%
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
    </div>
  );
};

export default BacktestView;
