import React from 'react';
import { ComposedChart, Bar, Cell, Line, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend, ReferenceLine } from 'recharts';
import { API } from '../utils.js';

const MarketIndicatorsView = React.memo(({ onChangeStock, onChangeTab }) => {
  const [miTab, setMiTab]             = React.useState('investor');
  const [investorData, setInvestorData] = React.useState(null);
  const [turnoverData, setTurnoverData] = React.useState(null);
  const [trendData, setTrendData]     = React.useState(null);
  const [summary, setSummary]         = React.useState(null);
  const [availDates, setAvailDates]   = React.useState([]);
  const [selDate, setSelDate]         = React.useState('');
  const [selMkt, setSelMkt]           = React.useState('ALL');
  const [trendMkt, setTrendMkt]       = React.useState('kospi');
  const [trendDays, setTrendDays]     = React.useState(60);
  const [loading, setLoading]         = React.useState(false);
  const [invSubTab, setInvSubTab]     = React.useState('both_buy');
  const [invMktTab, setInvMktTab]     = React.useState('kospi');
  const [cumDays, setCumDays]         = React.useState(60);
  // 대차정보 state
  const [shortDates, setShortDates]   = React.useState([]);
  const [shortDate, setShortDate]     = React.useState('');
  const [shortRank, setShortRank]     = React.useState(null);
  const [shortSortBy, setShortSortBy] = React.useState('lnb_rman_stck_cnt');
  const [shortHisCode, setShortHisCode] = React.useState('');
  const [shortHisName, setShortHisName] = React.useState('');
  const [shortHistory, setShortHistory] = React.useState(null);
  const [shortForeign, setShortForeign] = React.useState(null);
  const [shortMonthly, setShortMonthly] = React.useState(null);
  const [marketCash, setMarketCash] = React.useState(null);
  const [cashRange, setCashRange] = React.useState(1095);

  React.useEffect(() => {
    fetch(API('/api/market-indicators/available-dates?limit=30'))
      .then(r => r.ok ? r.json() : [])
      .then(dates => {
        setAvailDates(dates);
        if (dates.length > 0) setSelDate(dates[0]);
      });
    fetch(API('/api/market-indicators/market-summary'))
      .then(r => r.ok ? r.json() : null)
      .then(setSummary);
  }, []);

  React.useEffect(() => {
    if (!selDate) return;
    setLoading(true);
    Promise.all([
      fetch(API(`/api/market-indicators/investor-top?date=${selDate}&limit=20`)).then(r => r.ok ? r.json() : null),
      fetch(API(`/api/market-indicators/turnover-top?date=${selDate}&market=${selMkt}&limit=20`)).then(r => r.ok ? r.json() : null),
    ]).then(([inv, turn]) => {
      setInvestorData(inv);
      setTurnoverData(turn);
      setLoading(false);
    });
  }, [selDate, selMkt]);

  React.useEffect(() => {
    fetch(API(`/api/market-indicators/investor-trend?market=${trendMkt}&days=${trendDays}`))
      .then(r => r.ok ? r.json() : null)
      .then(setTrendData);
  }, [trendMkt, trendDays]);

  React.useEffect(() => {
    fetch(API(`/api/market-indicators/market-cash?days=${cashRange}`))
      .then(r => r.ok ? r.json() : null)
      .then(setMarketCash);
  }, [cashRange]);

  // 대차 날짜 목록 + 내외국인대차 + 월별대차 초기 로드
  React.useEffect(() => {
    fetch(API('/api/market-indicators/short-dates?limit=30'))
      .then(r => r.ok ? r.json() : [])
      .then(dates => {
        setShortDates(dates);
        if (dates.length > 0) setShortDate(dates[0]);
      });
    fetch(API('/api/market-indicators/short-foreign?days=120'))
      .then(r => r.ok ? r.json() : null)
      .then(setShortForeign);
    fetch(API('/api/market-indicators/short-monthly?months=24'))
      .then(r => r.ok ? r.json() : null)
      .then(setShortMonthly);
  }, []);

  // 대차종목순위 — 날짜/정렬 변경 시
  React.useEffect(() => {
    if (!shortDate) return;
    fetch(API(`/api/market-indicators/short-rank?date=${shortDate}&limit=50&sort_by=${shortSortBy}`))
      .then(r => r.ok ? r.json() : null)
      .then(setShortRank);
  }, [shortDate, shortSortBy]);

  const fmtAmt = (v) => {
    if (v == null || v === 0) return '-';
    const abs = Math.abs(v);
    const sign = v < 0 ? '-' : '+';
    if (abs >= 10000) return `${sign}${(abs/10000).toLocaleString('ko-KR', {maximumFractionDigits:1})}조`;
    if (abs < 1)      return `${sign}${abs.toFixed(1)}억`;
    return `${sign}${Math.round(abs).toLocaleString('ko-KR')}억`;
  };

  // 누적 차트용 데이터 (cumDays 기간 기준으로 재계산)
  const cumData = React.useMemo(() => {
    if (!trendData?.data?.length) return [];
    const slice = trendData.data.slice(-cumDays);
    let ci = 0, cf = 0;
    return slice.map(item => {
      ci += (item.inst_amt || 0);
      cf += (item.frn_amt || 0);
      return { ...item, cum_inst: Math.round(ci), cum_frn: Math.round(cf) };
    });
  }, [trendData, cumDays]);
  const fmtChg = (v) => {
    if (!v && v !== 0) return { txt: '-', color: 'var(--text-secondary)' };
    return { txt: fmtAmt(v), color: v >= 0 ? '#f87171' : '#60a5fa' };
  };
  const fmtPct = (v) => {
    if (!v && v !== 0) return '-';
    return `${v > 0 ? '+' : ''}${Number(v).toFixed(1)}%`;
  };

  const miTabBtn = (key, label) => (
    <button key={key} onClick={() => setMiTab(key)} style={{
      padding: '0.4rem 1rem', borderRadius: '6px', border: 'none',
      cursor: 'pointer', fontSize: '0.82rem', fontWeight: 600,
      background: miTab === key ? 'var(--accent-mint)' : 'var(--glass-bg)',
      color: miTab === key ? '#000' : 'var(--text-primary)',
    }}>{label}</button>
  );

  const INV_TYPES = [
    { key: 'both_buy',  label: '외인+기관 합계', color: '#a78bfa' },
    { key: 'both_sell', label: '외인+기관 매도', color: '#c084fc' },
    { key: 'frn_buy',   label: '외국인 순매수', color: '#fbbf24' },
    { key: 'inst_buy',  label: '기관 순매수',   color: '#f87171' },
    { key: 'ind_buy',   label: '개인 순매수',   color: '#60a5fa' },
    { key: 'frn_sell',  label: '외국인 순매도', color: '#fbbf24' },
    { key: 'inst_sell', label: '기관 순매도',   color: '#f87171' },
    { key: 'ind_sell',  label: '개인 순매도',   color: '#60a5fa' },
  ];

  const renderInvestorTable = (rows, amtKey, selDate) => {
    if (!rows || rows.length === 0) return <p style={{color:'var(--text-secondary)',padding:'1rem'}}>데이터 없음</p>;
    // 당일 가격 컬럼 표시 여부
    const hasTodayPrice = rows.some(r => r.today_close);
    return (
      <div style={{overflowX:'auto', overflowY:'clip'}}>
        <table style={{width:'100%',borderCollapse:'collapse',fontSize:'0.8rem'}}>
          <thead>
            <tr style={{borderBottom:'1px solid var(--glass-border)'}}>
              <th style={{padding:'0.4rem 0.5rem',textAlign:'left',color:'var(--text-secondary)'}}>종목</th>
              <th style={{padding:'0.4rem 0.5rem',textAlign:'right',color:'var(--text-secondary)'}}>
                수급기준일 종가
                {selDate && <span style={{fontSize:'0.65rem',color:'rgba(255,255,255,0.3)',display:'block'}}>{selDate}</span>}
              </th>
              {hasTodayPrice && (
                <th style={{padding:'0.4rem 0.5rem',textAlign:'right',color:'var(--text-secondary)'}}>
                  당일 주가
                  <span style={{fontSize:'0.65rem',color:'rgba(45,212,191,0.6)',display:'block'}}>
                    {rows.find(r=>r.today_date)?.today_date || '최신'}
                  </span>
                </th>
              )}
              <th style={{padding:'0.4rem 0.5rem',textAlign:'right',color:'var(--text-secondary)'}}>순매수(억)</th>
              <th style={{padding:'0.4rem 0.5rem',textAlign:'right',color:'var(--text-secondary)'}}>수량</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0,20).map((r, i) => {
              const amt    = r[amtKey] || 0;
              const qtyKey = amtKey.replace('_amt', '_qty');
              const qty    = r[qtyKey] || 0;
              const c      = amt >= 0 ? '#f87171' : '#60a5fa';
              const chg    = r.today_chg_pct;
              const chgC   = chg == null ? 'var(--text-secondary)' : chg >= 0 ? '#f87171' : '#60a5fa';
              return (
                <tr key={i} style={{borderBottom:'1px solid rgba(255,255,255,0.04)'}}
                  onMouseEnter={e=>e.currentTarget.style.background='rgba(255,255,255,0.04)'}
                  onMouseLeave={e=>e.currentTarget.style.background='transparent'}>
                  <td style={{padding:'0.35rem 0.5rem', whiteSpace:'nowrap', maxWidth:'110px', overflow:'hidden'}}>
                    <button onClick={()=>{onChangeStock(r.stock_code);onChangeTab('analysis');}}
                      style={{background:'none',border:'none',color:'var(--text-primary)',cursor:'pointer',fontWeight:600,fontSize:'0.8rem',padding:0,
                        maxWidth:'80px',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',display:'block'}}>
                      {r.stock_name}
                    </button>
                    <span style={{fontSize:'0.68rem',color:'var(--text-secondary)'}}>{r.stock_code}</span>
                  </td>
                  <td style={{padding:'0.35rem 0.5rem',textAlign:'right',color:'rgba(255,255,255,0.55)',fontSize:'0.75rem',whiteSpace:'nowrap'}}>
                    {r.close?.toLocaleString()}원
                  </td>
                  {hasTodayPrice && (
                    <td style={{padding:'0.35rem 0.5rem',textAlign:'right'}}>
                      {r.today_close ? (
                        <div style={{display:'flex',flexDirection:'column',alignItems:'flex-end',gap:'1px'}}>
                          <span style={{fontWeight:700,fontSize:'0.82rem'}}>{r.today_close.toLocaleString()}원</span>
                          {chg != null && (
                            <span style={{fontSize:'0.7rem',fontWeight:600,color:chgC}}>
                              {chg >= 0 ? '▲' : '▼'}{Math.abs(chg).toFixed(1)}%
                            </span>
                          )}
                        </div>
                      ) : <span style={{color:'rgba(255,255,255,0.2)',fontSize:'0.72rem'}}>-</span>}
                    </td>
                  )}
                  <td style={{padding:'0.35rem 0.5rem',textAlign:'right',color:c,fontWeight:700}}>{fmtAmt(amt)}</td>
                  <td style={{padding:'0.35rem 0.5rem',textAlign:'right',color:'var(--text-secondary)'}}>{qty ? qty.toLocaleString() : '-'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  };

  const renderTurnoverTable = (rows) => {
    if (!rows || rows.length === 0) return <p style={{color:'var(--text-secondary)',padding:'1rem'}}>데이터 없음</p>;
    return (
      <div style={{overflowX:'auto', overflowY:'clip'}}>
        <table style={{width:'100%',borderCollapse:'collapse',fontSize:'0.8rem'}}>
          <thead>
            <tr style={{borderBottom:'1px solid var(--glass-border)'}}>
              <th style={{padding:'0.4rem 0.5rem',textAlign:'left',color:'var(--text-secondary)'}}>종목</th>
              <th style={{padding:'0.4rem 0.5rem',textAlign:'left',color:'var(--text-secondary)'}}>시장</th>
              <th style={{padding:'0.4rem 0.5rem',textAlign:'right',color:'var(--text-secondary)'}}>현재가</th>
              <th style={{padding:'0.4rem 0.5rem',textAlign:'right',color:'var(--text-secondary)'}}>등락률</th>
              <th style={{padding:'0.4rem 0.5rem',textAlign:'right',color:'var(--text-secondary)'}}>회전율(%)</th>
              <th style={{padding:'0.4rem 0.5rem',textAlign:'right',color:'var(--text-secondary)'}}>거래량</th>
              <th style={{padding:'0.4rem 0.5rem',textAlign:'right',color:'var(--text-secondary)'}}>기관(억)</th>
              <th style={{padding:'0.4rem 0.5rem',textAlign:'right',color:'var(--text-secondary)'}}>외인(억)</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const mktShort = r.market?.includes('유가') || r.market?.toLowerCase().includes('kospi') ? 'KOSPI' :
                               r.market?.includes('코스닥') ? 'KOSDAQ' : r.market || '-';
              const instC = !r.inst_net_buy_amt ? 'var(--text-secondary)' : r.inst_net_buy_amt >= 0 ? '#f87171' : '#60a5fa';
              const frnC  = !r.frn_net_buy_amt  ? 'var(--text-secondary)' : r.frn_net_buy_amt  >= 0 ? '#f87171' : '#60a5fa';
              const chgC  = r.chg_pct == null ? 'var(--text-secondary)' : r.chg_pct >= 0 ? '#f87171' : '#60a5fa';
              return (
                <tr key={i} style={{borderBottom:'1px solid rgba(255,255,255,0.04)'}}
                  onMouseEnter={e=>e.currentTarget.style.background='rgba(255,255,255,0.04)'}
                  onMouseLeave={e=>e.currentTarget.style.background='transparent'}>
                  <td style={{padding:'0.35rem 0.5rem', whiteSpace:'nowrap', maxWidth:'110px', overflow:'hidden'}}>
                    <button onClick={()=>{onChangeStock(r.stock_code);onChangeTab('analysis');}}
                      style={{background:'none',border:'none',color:'var(--text-primary)',cursor:'pointer',fontWeight:600,fontSize:'0.8rem',padding:0,
                        maxWidth:'80px',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap',display:'block'}}>
                      {r.stock_name}
                    </button>
                    <span style={{fontSize:'0.68rem',color:'var(--text-secondary)'}}>{r.stock_code}</span>
                  </td>
                  <td style={{padding:'0.35rem 0.5rem', whiteSpace:'nowrap'}}>
                    <span style={{fontSize:'0.7rem',padding:'0.1rem 0.4rem',borderRadius:'4px',
                      background: mktShort==='KOSPI'?'rgba(248,113,113,0.15)':'rgba(96,165,250,0.15)',
                      color: mktShort==='KOSPI'?'#f87171':'#60a5fa'}}>
                      {mktShort}
                    </span>
                  </td>
                  <td style={{padding:'0.35rem 0.5rem',textAlign:'right',fontWeight:600,whiteSpace:'nowrap'}}>{r.close?.toLocaleString()}원</td>
                  <td style={{padding:'0.35rem 0.5rem',textAlign:'right',fontWeight:700,color:chgC,whiteSpace:'nowrap'}}>
                    {r.chg_pct != null ? `${r.chg_pct >= 0 ? '▲' : '▼'}${Math.abs(r.chg_pct).toFixed(1)}%` : '-'}
                  </td>
                  <td style={{padding:'0.35rem 0.5rem',textAlign:'right',fontWeight:700,color:'#fbbf24'}}>{r.turnover_pct?.toFixed(1)}%</td>
                  <td style={{padding:'0.35rem 0.5rem',textAlign:'right',color:'var(--text-secondary)'}}>{r.volume?.toLocaleString()}</td>
                  <td style={{padding:'0.35rem 0.5rem',textAlign:'right',color:instC,fontWeight:600}}>{fmtAmt(r.inst_net_buy_amt)}</td>
                  <td style={{padding:'0.35rem 0.5rem',textAlign:'right',color:frnC,fontWeight:600}}>{fmtAmt(r.frn_net_buy_amt)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  };

  return (
    <div style={{maxWidth:'1400px',margin:'0 auto'}}>
      {/* 헤더 + 시장요약 */}
      <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:'1rem',flexWrap:'wrap',gap:'0.5rem'}}>
        <h2 style={{fontSize:'1.1rem',fontWeight:700,margin:0}}>📊 시장 지표</h2>
        <div style={{display:'flex',gap:'0.5rem',flexWrap:'wrap'}}>
          {summary && Object.entries(summary).map(([mkt, d]) => d ? (
            <div key={mkt} className="glass-panel" style={{padding:'0.4rem 0.8rem',display:'flex',gap:'0.6rem',alignItems:'center'}}>
              <span style={{fontWeight:700,fontSize:'0.85rem'}}>{mkt}</span>
              <span style={{fontWeight:800,fontSize:'0.95rem'}}>{d.close?.toLocaleString()}</span>
              <span style={{fontSize:'0.8rem',color:d.change_rate>=0?'#f87171':'#60a5fa',fontWeight:600}}>
                {d.change_rate>=0?'▲':'▼'}{Number(Math.abs(d.change_rate)).toFixed(1)}%
              </span>
              <span style={{fontSize:'0.72rem',color:'var(--text-secondary)'}}>{d.date}</span>
            </div>
          ) : null)}
        </div>
      </div>

      {/* 탭 */}
      <div style={{display:'flex',gap:'0.5rem',marginBottom:'1rem',flexWrap:'wrap'}}>
        {miTabBtn('investor',    '📊 투자자별 순매수')}
        {miTabBtn('turnover',    '🔄 회전율 상위')}
        {miTabBtn('trend',       '📈 수급 추이')}
        {miTabBtn('cash',        '💰 예탁금 추이')}
        {miTabBtn('short_rank',  '🏅 대차종목순위')}
        {miTabBtn('short_his',   '📉 대차거래현황')}
        {miTabBtn('short_forg',  '🌐 내외국인대차')}
      </div>

      {/* ── 예탁금 추이 탭 ── */}
      {miTab === 'cash' && (
        <div>
          <div style={{display:'flex',gap:'0.5rem',marginBottom:'1rem',alignItems:'center',flexWrap:'wrap'}}>
            <span style={{fontSize:'0.82rem',color:'var(--text-secondary)'}}>기간:</span>
            {[[30,'30일'],[183,'6개월'],[365,'1년'],[1095,'3년']].map(([d,l])=> (
              <button key={d} onClick={()=>setCashRange(d)} style={{
                padding:'0.25rem 0.7rem',borderRadius:'6px',border:'none',cursor:'pointer',fontSize:'0.78rem',
                background:cashRange===d?'var(--accent-mint)':'var(--glass-bg)',
                color:cashRange===d?'#000':'var(--text-secondary)',fontWeight:cashRange===d?700:500,
              }}>{l}</button>
            ))}
            <span style={{fontSize:'0.72rem',color:'var(--text-secondary)'}}>
              출처: 네이버 증시자금동향 · 단위: 억원
            </span>
          </div>

          {marketCash?.rows?.length > 0 ? (
            <div style={{display:'flex',flexDirection:'column',gap:'1rem'}}>
              <div className="glass-panel" style={{padding:'1rem'}}>
                <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:'0.8rem',flexWrap:'wrap',gap:'0.4rem'}}>
                  <h3 style={{margin:0,fontSize:'0.9rem',fontWeight:700}}>국내 주식시장 예탁금/신용잔고 추이</h3>
                  <span style={{fontSize:'0.72rem',color:'var(--text-secondary)'}}>
                    최신기준일: {marketCash.latest_date || '-'} · 업데이트: {marketCash.updated_at || '-'}
                  </span>
                </div>
                <ResponsiveContainer width="100%" height={280}>
                  <ComposedChart data={marketCash.rows} margin={{top:5,right:10,bottom:5,left:10}}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                    <XAxis dataKey="date" tick={{fontSize:10,fill:'#94a3b8'}} tickFormatter={d=>d?.slice(5)} interval="preserveStartEnd" />
                    <YAxis tick={{fontSize:10,fill:'#94a3b8'}} tickFormatter={(v)=>`${Math.round(v).toLocaleString()}`} />
                    <Tooltip
                      contentStyle={{background:'var(--bg-dark)',border:'1px solid var(--glass-border)',fontSize:'0.78rem'}}
                      formatter={(v,n)=>[`${Number(v||0).toLocaleString()}억`, n==='customer_deposit_100m' ? '고객예탁금' : '신용잔고']}
                      labelFormatter={l=>`날짜: ${l}`}
                    />
                    <Legend formatter={(v)=> v==='customer_deposit_100m' ? '고객예탁금' : '신용잔고'} />
                    <Area type="monotone" dataKey="customer_deposit_100m" name="customer_deposit_100m" stroke="#2dd4bf" fill="rgba(45,212,191,0.16)" strokeWidth={2} />
                    <Line type="monotone" dataKey="credit_balance_100m" name="credit_balance_100m" stroke="#f59e0b" dot={false} strokeWidth={2} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            </div>
          ) : (
            <div className="glass-panel" style={{padding:'2rem',textAlign:'center',color:'var(--text-secondary)'}}>
              데이터 로딩 중이거나 수집 데이터가 없습니다.
            </div>
          )}
        </div>
      )}

      {/* ── 투자자별 순매수 탭 ── */}
      {miTab === 'investor' && (
        <div>
          {/* 날짜 선택 */}
          <div style={{display:'flex',gap:'0.5rem',marginBottom:'1rem',alignItems:'center',flexWrap:'wrap'}}>
            <label style={{fontSize:'0.82rem',color:'var(--text-secondary)'}}>기준일:</label>
            <select value={selDate} onChange={e=>setSelDate(e.target.value)}
              style={{background:'var(--glass-bg)',border:'1px solid var(--glass-border)',color:'var(--text-primary)',
                borderRadius:'6px',padding:'0.3rem 0.5rem',fontSize:'0.82rem'}}>
              {availDates.map(d => <option key={d} value={d}>{d}</option>)}
            </select>
            <span style={{fontSize:'0.75rem',color:'var(--text-secondary)'}}>※ KIS 수집 데이터 기준</span>
          </div>

          {loading ? (
            <div style={{textAlign:'center',padding:'2rem',color:'var(--text-secondary)'}}>데이터 로딩 중...</div>
          ) : (
            <div>
              {/* ── 요약 섹션 (상단 배치) ── */}
              {['kospi','kosdaq'].map(mktKey => (
                <div key={mktKey} style={{marginBottom:'2.5rem'}}>
                  <div style={{display:'flex', alignItems:'baseline', gap:'0.7rem', marginBottom:'0.8rem', borderBottom:'2px solid rgba(255,255,255,0.1)', paddingBottom:'0.4rem'}}>
                    <h3 style={{fontSize:'1.3rem', fontWeight:800, margin:0, color:'#fff'}}>{mktKey.toUpperCase()}</h3>
                    <span style={{fontSize:'0.85rem', color:'var(--text-secondary)'}}>전체 요약 (Top 5)</span>
                  </div>
                  
                  <div style={{display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(280px, 1fr))', gap:'1rem'}}>
                    {['both','frn','inst','ind'].map(inv => {
                      const typeKey = `${inv}_buy`;
                      const amtKey  = `${inv}_amt`;
                      const label   = inv==='both'?'외인+기관':inv==='inst'?'기관':inv==='frn'?'외국인':'개인';
                      const color   = inv==='both'?'#a78bfa':inv==='inst'?'#f87171':inv==='frn'?'#fbbf24':'#60a5fa';
                      const rows    = investorData?.[mktKey]?.[typeKey]?.slice(0,5) || [];
                      return (
                        <div key={`${mktKey}-${inv}`} className="glass-panel" style={{padding:'1rem', borderTop:`3px solid ${color}`}}>
                          <div style={{display:'flex',justifyContent:'space-between',marginBottom:'0.7rem',alignItems:'center'}}>
                            <span style={{fontWeight:800,fontSize:'0.9rem',color}}>{label} 합계</span>
                            <button onClick={()=>{setInvMktTab(mktKey);setInvSubTab(typeKey); window.scrollTo({top: document.getElementById('main-table-anchor').offsetTop - 100, behavior:'smooth'});}}
                              style={{fontSize:'0.72rem',color:'var(--text-secondary)',background:'none',border:'none',cursor:'pointer',textDecoration:'underline'}}>
                              상위 20 전체보기
                            </button>
                          </div>
                          {rows.length===0 ? <p style={{color:'var(--text-secondary)',fontSize:'0.78rem',margin:0}}>데이터 없음</p> : (
                            <div style={{display:'flex',flexDirection:'column',gap:'0.4rem'}}>
                              {rows.map((r,i)=>{
                                const amt = r[amtKey]||0;
                                return (
                                  <div key={i} style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
                                    <div style={{display:'flex', alignItems:'center', gap:'0.4rem', overflow:'hidden', flex:1}}>
                                      <span style={{fontSize:'0.75rem', color:'rgba(255,255,255,0.3)', width:'12px'}}>{i+1}</span>
                                      <button onClick={()=>{onChangeStock(r.stock_code);onChangeTab('analysis');}}
                                        style={{background:'none',border:'none',color:'var(--text-primary)',cursor:'pointer',
                                          fontSize:'0.8rem',padding:0,textAlign:'left',fontWeight:600, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>
                                        {r.stock_name}
                                      </button>
                                    </div>
                                    <span style={{fontSize:'0.82rem',color:amt>=0?'#f87171':'#60a5fa',fontWeight:800, marginLeft:'0.5rem'}}>{fmtAmt(amt)}</span>
                                  </div>
                                );
                              })}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}

              <div id="main-table-anchor" style={{height:'1px'}} />

              {/* ── 상세 테이블 섹션 ── */}
              <div className="glass-panel" style={{padding:'1.2rem'}}>
                <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:'1.2rem',flexWrap:'wrap',gap:'0.8rem',borderBottom:'1px solid rgba(255,255,255,0.06)',paddingBottom:'0.8rem'}}>
                  <div style={{display:'flex', alignItems:'center', gap:'1rem'}}>
                    <h3 style={{margin:0,fontSize:'1.1rem',fontWeight:800, color:'var(--accent-mint)'}}>
                      {invMktTab.toUpperCase()} 상세 분석
                    </h3>
                    {/* 시장 선택 칩 */}
                    <div style={{display:'flex',gap:'0.4rem',background:'rgba(0,0,0,0.2)',padding:'0.2rem',borderRadius:'8px'}}>
                      {[['kospi','KOSPI'],['kosdaq','KOSDAQ']].map(([k,l]) => (
                        <button key={k} onClick={()=>setInvMktTab(k)} style={{
                          padding:'0.3rem 0.8rem',borderRadius:'6px',border:'none',cursor:'pointer',fontSize:'0.75rem',
                          background:invMktTab===k?'var(--accent-mint)':'transparent',
                          color:invMktTab===k?'#000':'var(--text-secondary)',
                          fontWeight:invMktTab===k?700:400,
                        }}>{l}</button>
                      ))}
                    </div>
                  </div>
                  {/* 투자자 유형 탭 (스크롤 가능하게) */}
                  <div style={{display:'flex',gap:'0.3rem', overflowX:'auto', paddingBottom:'2px'}}>
                    {INV_TYPES.map(t => (
                      <button key={t.key} onClick={()=>setInvSubTab(t.key)} style={{
                        padding:'0.3rem 0.7rem',borderRadius:'6px',border:'none',cursor:'pointer',fontSize:'0.75rem',
                        background:invSubTab===t.key?t.color:'rgba(255,255,255,0.05)',
                        color:invSubTab===t.key?'#000':'var(--text-secondary)',
                        fontWeight:invSubTab===t.key?700:400,
                        whiteSpace:'nowrap'
                      }}>{t.label}</button>
                    ))}
                  </div>
                </div>
                
                <div style={{display:'flex', justifyContent:'space-between', marginBottom:'0.6rem'}}>
                  <span style={{fontSize:'0.85rem', fontWeight:600}}>{INV_TYPES.find(t=>t.key===invSubTab)?.label} 상위 20</span>
                  <span style={{fontSize:'0.72rem',color:'var(--text-secondary)'}}>{selDate} 기준</span>
                </div>

                {investorData && renderInvestorTable(
                  investorData[invMktTab]?.[invSubTab],
                  invSubTab.replace('_buy','_amt').replace('_sell','_amt'),
                  selDate
                )}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── 회전율 탭 ── */}
      {miTab === 'turnover' && (
        <div>
          <div style={{display:'flex',gap:'0.5rem',marginBottom:'1rem',alignItems:'center',flexWrap:'wrap'}}>
            <label style={{fontSize:'0.82rem',color:'var(--text-secondary)'}}>기준일:</label>
            <select value={selDate} onChange={e=>setSelDate(e.target.value)}
              style={{background:'var(--glass-bg)',border:'1px solid var(--glass-border)',color:'var(--text-primary)',
                borderRadius:'6px',padding:'0.3rem 0.5rem',fontSize:'0.82rem'}}>
              {availDates.map(d => <option key={d} value={d}>{d}</option>)}
            </select>
            <label style={{fontSize:'0.82rem',color:'var(--text-secondary)'}}>시장:</label>
            <select value={selMkt} onChange={e=>setSelMkt(e.target.value)}
              style={{background:'var(--glass-bg)',border:'1px solid var(--glass-border)',color:'var(--text-primary)',
                borderRadius:'6px',padding:'0.3rem 0.5rem',fontSize:'0.82rem'}}>
              <option value="ALL">전체</option>
              <option value="KOSPI">KOSPI</option>
              <option value="KOSDAQ">KOSDAQ</option>
            </select>
          </div>
          <div className="glass-panel" style={{padding:'1rem'}}>
            <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:'0.7rem'}}>
              <h3 style={{margin:0,fontSize:'0.9rem',fontWeight:700}}>
                🔄 회전율 상위 20 ({selMkt === 'ALL' ? '전체' : selMkt})
              </h3>
              <span style={{fontSize:'0.72rem',color:'var(--text-secondary)'}}>
                {selDate} · 회전율 = 거래량 ÷ 상장주식수 × 100
              </span>
            </div>
            {loading ? (
              <div style={{textAlign:'center',padding:'2rem',color:'var(--text-secondary)'}}>로딩 중...</div>
            ) : renderTurnoverTable(turnoverData?.data)}
          </div>
        </div>
      )}

      {/* ── 수급 추이 탭 ── */}
      {miTab === 'trend' && (
        <div>
          <div style={{display:'flex',gap:'0.5rem',marginBottom:'1rem',alignItems:'center',flexWrap:'wrap'}}>
            <label style={{fontSize:'0.82rem',color:'var(--text-secondary)'}}>지수:</label>
            <select value={trendMkt} onChange={e=>setTrendMkt(e.target.value)}
              style={{background:'var(--glass-bg)',border:'1px solid var(--glass-border)',color:'var(--text-primary)',
                borderRadius:'6px',padding:'0.3rem 0.5rem',fontSize:'0.82rem'}}>
              <option value="kospi">KOSPI</option>
              <option value="kosdaq">KOSDAQ</option>
            </select>
            <label style={{fontSize:'0.82rem',color:'var(--text-secondary)'}}>기간:</label>
            <select value={trendDays} onChange={e=>setTrendDays(Number(e.target.value))}
              style={{background:'var(--glass-bg)',border:'1px solid var(--glass-border)',color:'var(--text-primary)',
                borderRadius:'6px',padding:'0.3rem 0.5rem',fontSize:'0.82rem'}}>
              <option value={20}>1개월(20일)</option>
              <option value={60}>3개월(60일)</option>
              <option value={120}>6개월</option>
              <option value={250}>1년</option>
            </select>
          </div>

          {trendData && trendData.data?.length > 0 ? (
            <div style={{display:'flex',flexDirection:'column',gap:'1rem'}}>
              {/* 지수 + 기관/외인 순매수 차트 */}
              <div className="glass-panel" style={{padding:'1rem'}}>
                <h3 style={{margin:'0 0 1rem',fontSize:'0.9rem',fontWeight:700}}>
                  {trendData.market} 지수 추이
                </h3>
                <ResponsiveContainer width="100%" height={200}>
                  <ComposedChart data={trendData.data} margin={{top:5,right:10,bottom:5,left:10}}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                    <XAxis dataKey="date" tick={{fontSize:10,fill:'#94a3b8'}} tickFormatter={d=>d?.slice(5)} interval="preserveStartEnd" />
                    <YAxis tick={{fontSize:10,fill:'#94a3b8'}} domain={['auto','auto']} />
                    <Tooltip contentStyle={{background:'var(--bg-dark)',border:'1px solid var(--glass-border)',fontSize:'0.78rem'}}
                      formatter={(v,n) => [v?.toLocaleString(), n]} labelFormatter={l=>`날짜: ${l}`} />
                    <Line type="monotone" dataKey="close" stroke="#2dd4bf" dot={false} strokeWidth={2} name="지수" />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>

              {/* 일별 순매수 바 차트 — has_supply 있는 날만 */}
              {(() => {
                const barData = trendData.data.filter(r => r.has_supply !== false && (r.inst_amt != null || r.frn_amt != null));
                return (
                <div className="glass-panel" style={{padding:'1rem'}}>
                  <h3 style={{margin:'0 0 0.8rem',fontSize:'0.9rem',fontWeight:700}}>
                    일별 투자자 순매수 (억원)
                    <span style={{fontSize:'0.72rem',color:'var(--text-secondary)',marginLeft:'0.7rem',fontWeight:400}}>
                      ▶ 빨강=순매수 / 파랑=순매도
                    </span>
                  </h3>
                  {barData.length === 0 ? (
                    <div style={{textAlign:'center',color:'var(--text-secondary)',padding:'2rem',fontSize:'0.82rem'}}>수급 데이터 없음</div>
                  ) : (
                  <ResponsiveContainer width="100%" height={260}>
                    <ComposedChart data={barData} margin={{top:5,right:10,bottom:5,left:10}}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                      <XAxis dataKey="date" tick={{fontSize:10,fill:'#94a3b8'}} tickFormatter={d=>d?.slice(5)} interval="preserveStartEnd" />
                      <YAxis tick={{fontSize:10,fill:'#94a3b8'}} />
                      <Tooltip contentStyle={{background:'var(--bg-dark)',border:'1px solid var(--glass-border)',fontSize:'0.78rem'}}
                        formatter={(v,n) => [`${v != null ? v.toLocaleString() : 0}억`, n]}
                        labelFormatter={l=>`날짜: ${l}`} />
                      <ReferenceLine y={0} stroke="rgba(255,255,255,0.35)" strokeWidth={1.5} />
                      <Bar dataKey="inst_amt" name="기관" maxBarSize={16}>
                        {barData.map((entry, i) => (
                          <Cell key={i} fill={(entry.inst_amt||0) >= 0 ? '#f87171' : '#60a5fa'} opacity={0.85} />
                        ))}
                      </Bar>
                      <Bar dataKey="frn_amt" name="외국인" maxBarSize={16}>
                        {barData.map((entry, i) => (
                          <Cell key={i} fill={(entry.frn_amt||0) >= 0 ? '#fbbf24' : '#6366f1'} opacity={0.85} />
                        ))}
                      </Bar>
                      <Legend wrapperStyle={{fontSize:'0.78rem',color:'var(--text-secondary)'}}
                        formatter={(v) => <span style={{color: v==='기관'?'#f87171':'#fbbf24'}}>{v}</span>} />
                    </ComposedChart>
                  </ResponsiveContainer>
                  )}
                </div>
                );
              })()}

              {/* 누적 순매수 라인 차트 */}
              <div className="glass-panel" style={{padding:'1rem'}}>
                <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:'0.8rem',flexWrap:'wrap',gap:'0.4rem'}}>
                  <h3 style={{margin:0,fontSize:'0.9rem',fontWeight:700}}>누적 순매수 추이 (기관/외국인)</h3>
                  <div style={{display:'flex',gap:'0.3rem'}}>
                    {[[20,'1개월'],[60,'3개월'],[90,'6개월'],[250,'1년']].map(([d,l])=>(
                      <button key={d} onClick={()=>setCumDays(d)} style={{
                        padding:'0.2rem 0.55rem',borderRadius:'5px',border:'none',cursor:'pointer',fontSize:'0.72rem',
                        background:cumDays===d?'var(--accent-mint)':'rgba(255,255,255,0.07)',
                        color:cumDays===d?'#000':'var(--text-secondary)',fontWeight:cumDays===d?700:400,
                      }}>{l}</button>
                    ))}
                  </div>
                </div>
                <ResponsiveContainer width="100%" height={220}>
                  <ComposedChart data={cumData} margin={{top:5,right:10,bottom:5,left:10}}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                    <XAxis dataKey="date" tick={{fontSize:10,fill:'#94a3b8'}} tickFormatter={d=>d?.slice(5)} interval="preserveStartEnd" />
                    <YAxis tick={{fontSize:10,fill:'#94a3b8'}} />
                    <Tooltip contentStyle={{background:'var(--bg-dark)',border:'1px solid var(--glass-border)',fontSize:'0.78rem'}}
                      formatter={(v,n) => [`${v?.toLocaleString()}억`, n]}
                      labelFormatter={l=>`날짜: ${l}`} />
                    <ReferenceLine y={0} stroke="rgba(255,255,255,0.35)" strokeWidth={1.5} />
                    <Line type="monotone" dataKey="cum_inst" stroke="#f87171" dot={false} strokeWidth={2} name="기관 누적" />
                    <Line type="monotone" dataKey="cum_frn"  stroke="#fbbf24" dot={false} strokeWidth={2} name="외국인 누적" />
                    <Legend wrapperStyle={{fontSize:'0.78rem',color:'var(--text-secondary)'}} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>

              {/* 최근 10일 테이블 */}
              <div className="glass-panel" style={{padding:'1rem'}}>
                <h3 style={{margin:'0 0 0.8rem',fontSize:'0.9rem',fontWeight:700}}>최근 데이터 (일별)</h3>
                <div style={{overflowX:'auto', overflowY:'clip'}}>
                  <table style={{width:'100%',borderCollapse:'collapse',fontSize:'0.8rem'}}>
                    <thead>
                      <tr style={{borderBottom:'1px solid var(--glass-border)'}}>
                        {['날짜','지수','기관(억)','외국인(억)','개인(억)'].map(h=>(
                          <th key={h} style={{padding:'0.4rem 0.6rem',textAlign:h==='날짜'?'left':'right',
                            color:'var(--text-secondary)',fontWeight:600}}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {[...trendData.data].reverse().slice(0,15).map((r,i)=>(
                        <tr key={i} style={{borderBottom:'1px solid rgba(255,255,255,0.04)'}}>
                          <td style={{padding:'0.35rem 0.6rem'}}>{r.date}</td>
                          <td style={{padding:'0.35rem 0.6rem',textAlign:'right'}}>{r.close?.toLocaleString()}</td>
                          <td style={{padding:'0.35rem 0.6rem',textAlign:'right',
                            color:(r.inst_amt||0)>=0?'#f87171':'#60a5fa',fontWeight:600}}>
                            {fmtAmt(r.inst_amt)}
                          </td>
                          <td style={{padding:'0.35rem 0.6rem',textAlign:'right',
                            color:(r.frn_amt||0)>=0?'#f87171':'#60a5fa',fontWeight:600}}>
                            {fmtAmt(r.frn_amt)}
                          </td>
                          <td style={{padding:'0.35rem 0.6rem',textAlign:'right',
                            color:(r.ind_amt||0)>=0?'#f87171':'#60a5fa',fontWeight:600}}>
                            {fmtAmt(r.ind_amt)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          ) : (
            <div className="glass-panel" style={{padding:'2rem',textAlign:'center',color:'var(--text-secondary)'}}>
              <p>수급 추이 데이터가 없습니다.</p>
              <p style={{fontSize:'0.78rem',marginTop:'0.5rem'}}>
                KIS 데이터 수집 후 price_history 에 기록된 데이터가 표시됩니다.
              </p>
            </div>
          )}
        </div>
      )}

      {/* ── 대차종목순위 탭 ── */}
      {miTab === 'short_rank' && (
        <div>
          <div style={{display:'flex',gap:'0.8rem',marginBottom:'1rem',alignItems:'center',flexWrap:'wrap'}}>
            <label style={{fontSize:'0.82rem',color:'var(--text-secondary)'}}>기준일:</label>
            {shortDates.length === 0 ? (
              <span style={{fontSize:'0.82rem',color:'#fbbf24'}}>데이터 수집 중... 잠시 후 새로고침</span>
            ) : (
              <select value={shortDate} onChange={e=>setShortDate(e.target.value)}
                style={{background:'var(--glass-bg)',border:'1px solid var(--glass-border)',color:'var(--text-primary)',
                  borderRadius:'6px',padding:'0.3rem 0.5rem',fontSize:'0.82rem'}}>
                {shortDates.map(d=><option key={d} value={d}>{d}</option>)}
              </select>
            )}
            <label style={{fontSize:'0.82rem',color:'var(--text-secondary)'}}>정렬:</label>
            <select value={shortSortBy} onChange={e=>setShortSortBy(e.target.value)}
              style={{background:'var(--glass-bg)',border:'1px solid var(--glass-border)',color:'var(--text-primary)',
                borderRadius:'6px',padding:'0.3rem 0.5rem',fontSize:'0.82rem'}}>
              <option value="lnb_rman_stck_cnt">대차잔여주식수</option>
              <option value="lnb_bal">대차잔액</option>
              <option value="lnb_ccl_stck_cnt">대차체결주식수</option>
            </select>
            <span style={{fontSize:'0.75rem',color:'var(--text-secondary)'}}>※ 금융위원회 V2 API</span>
          </div>
          <div className="glass-panel" style={{padding:'1rem'}}>
            <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:'0.7rem'}}>
              <h3 style={{margin:0,fontSize:'0.9rem',fontWeight:700}}>🏅 대차종목순위 상위 50</h3>
              <span style={{fontSize:'0.72rem',color:'var(--text-secondary)'}}>{shortDate} · 주식 종목만 표시</span>
            </div>
            {!shortRank ? (
              <div style={{textAlign:'center',padding:'2rem',color:'var(--text-secondary)'}}>
                {shortDates.length === 0 ? '데이터가 없습니다. 스케줄러가 매일 수집합니다.' : '로딩 중...'}
              </div>
            ) : (
              <div style={{overflowX:'auto',overflowY:'clip'}}>
                <table style={{width:'100%',borderCollapse:'collapse',fontSize:'0.8rem'}}>
                  <thead>
                    <tr style={{borderBottom:'2px solid var(--glass-border)'}}>
                      {['순위','종목명','시장','섹터','대차잔여(주)','대차잔액(억)','체결(주)','상환(주)'].map(h=>(
                        <th key={h} style={{padding:'0.4rem 0.5rem',textAlign:h==='종목명'||h==='섹터'?'left':'right',
                          color:'var(--text-secondary)',fontWeight:600,whiteSpace:'nowrap'}}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {(shortRank.rows||[]).map((r,i)=>(
                      <tr key={i} style={{borderBottom:'1px solid rgba(255,255,255,0.04)',
                        background:i%2===0?'transparent':'rgba(255,255,255,0.01)'}}>
                        <td style={{padding:'0.35rem 0.5rem',textAlign:'right',color:'var(--text-secondary)'}}>{i+1}</td>
                        <td style={{padding:'0.35rem 0.5rem'}}>
                          {r.stock_code ? (
                            <button onClick={()=>{onChangeStock(r.stock_code);onChangeTab('analysis');}}
                              style={{background:'none',border:'none',color:'var(--text-primary)',cursor:'pointer',
                                fontSize:'0.8rem',padding:0,fontWeight:600}}>
                              {r.stock_name}
                            </button>
                          ) : <span style={{color:'var(--text-secondary)'}}>{r.stock_name}</span>}
                        </td>
                        <td style={{padding:'0.35rem 0.5rem',textAlign:'right',fontSize:'0.72rem',
                          color:'var(--text-secondary)'}}>{r.market||'-'}</td>
                        <td style={{padding:'0.35rem 0.5rem',fontSize:'0.72rem',color:'var(--text-secondary)',
                          maxWidth:'100px',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}
                          title={r.sector}>{r.sector||'-'}</td>
                        <td style={{padding:'0.35rem 0.5rem',textAlign:'right',fontWeight:700,color:'#fbbf24'}}>
                          {r.lnb_rman_stck_cnt!=null?(r.lnb_rman_stck_cnt/1e4).toLocaleString('ko-KR',{maximumFractionDigits:1})+'만':'-'}
                        </td>
                        <td style={{padding:'0.35rem 0.5rem',textAlign:'right',color:'#f87171'}}>
                          {r.lnb_bal!=null?Math.round(r.lnb_bal/1e8).toLocaleString('ko-KR')+'억':'-'}
                        </td>
                        <td style={{padding:'0.35rem 0.5rem',textAlign:'right',color:'var(--text-secondary)'}}>
                          {r.lnb_ccl_stck_cnt!=null?(r.lnb_ccl_stck_cnt/1e4).toLocaleString('ko-KR',{maximumFractionDigits:1})+'만':'-'}
                        </td>
                        <td style={{padding:'0.35rem 0.5rem',textAlign:'right',color:'var(--text-secondary)'}}>
                          {r.rdpt_stck_cnt!=null?(r.rdpt_stck_cnt/1e4).toLocaleString('ko-KR',{maximumFractionDigits:1})+'만':'-'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── 대차거래현황 탭 ── */}
      {miTab === 'short_his' && (() => {
        const doSearch = () => {
          if (!shortHisCode && !shortHisName) return;
          const q = shortHisCode || shortHisName;
          const isCode = /^\d{6}$/.test(q);
          const url = isCode
            ? `/api/market-indicators/short-history?code=${q}&days=120`
            : `/api/market-indicators/short-history?name=${encodeURIComponent(q)}&days=120`;
          fetch(API(url)).then(r=>r.ok?r.json():null).then(setShortHistory);
        };
        const maxBal = shortHistory?.history?.length
          ? Math.max(...shortHistory.history.map(h=>h.borrow_bal_qty||0)) : 1;
        return (
          <div>
            <div style={{display:'flex',gap:'0.5rem',marginBottom:'1rem',alignItems:'center',flexWrap:'wrap'}}>
              <input value={shortHisCode||shortHisName}
                onChange={e=>{const v=e.target.value;/^\d/.test(v)?setShortHisCode(v)||setShortHisName(''):setShortHisName(v)||setShortHisCode('');}}
                onKeyDown={e=>e.key==='Enter'&&doSearch()}
                placeholder="종목코드(6자리) 또는 종목명"
                style={{background:'var(--glass-bg)',border:'1px solid var(--glass-border)',color:'var(--text-primary)',
                  borderRadius:'6px',padding:'0.3rem 0.6rem',fontSize:'0.82rem',width:'200px'}}/>
              <button onClick={doSearch} style={{padding:'0.3rem 0.8rem',borderRadius:'6px',border:'none',
                background:'var(--accent-mint)',color:'#000',fontWeight:700,cursor:'pointer',fontSize:'0.82rem'}}>조회</button>
              <span style={{fontSize:'0.75rem',color:'var(--text-secondary)'}}>※ 최근 120일 대차잔고 추이</span>
            </div>
            {shortHistory && (
              <div className="glass-panel" style={{padding:'1rem'}}>
                <h3 style={{margin:'0 0 1rem',fontSize:'0.9rem',fontWeight:700}}>
                  {shortHistory.stock_name} 대차거래현황
                  <span style={{fontSize:'0.72rem',color:'var(--text-secondary)',fontWeight:400,marginLeft:'0.7rem'}}>
                    최근 {shortHistory.history?.length||0}일
                  </span>
                </h3>
                {shortHistory.history?.length > 0 ? (
                  <>
                    <ResponsiveContainer width="100%" height={200}>
                      <ComposedChart data={shortHistory.history} margin={{top:5,right:10,bottom:5,left:10}}>
                        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                        <XAxis dataKey="date" tick={{fontSize:9,fill:'#94a3b8'}}
                          tickFormatter={d=>d?.slice(4)} interval="preserveStartEnd"/>
                        <YAxis tick={{fontSize:9,fill:'#94a3b8'}}
                          tickFormatter={v=>`${(v/1e4).toFixed(0)}만`}/>
                        <Tooltip contentStyle={{background:'var(--bg-dark)',border:'1px solid var(--glass-border)',fontSize:'0.75rem'}}
                          formatter={(v,n)=>[`${(v/1e4).toFixed(1)}만주`,n]}/>
                        <Area type="monotone" dataKey="borrow_bal_qty" name="대차잔고"
                          fill="rgba(251,191,36,0.15)" stroke="#fbbf24" strokeWidth={2} dot={false}/>
                        <Bar dataKey="short_qty" name="체결주식수" fill="rgba(248,113,113,0.6)" maxBarSize={8}/>
                      </ComposedChart>
                    </ResponsiveContainer>
                    <div style={{overflowX:'auto',overflowY:'clip',marginTop:'0.5rem'}}>
                      <table style={{width:'100%',borderCollapse:'collapse',fontSize:'0.78rem'}}>
                        <thead>
                          <tr style={{borderBottom:'1px solid var(--glass-border)'}}>
                            {['날짜','대차잔고(주)','체결(주)'].map(h=>(
                              <th key={h} style={{padding:'0.35rem 0.5rem',textAlign:h==='날짜'?'left':'right',
                                color:'var(--text-secondary)',fontWeight:600}}>{h}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {[...shortHistory.history].reverse().slice(0,20).map((r,i)=>(
                            <tr key={i} style={{borderBottom:'1px solid rgba(255,255,255,0.04)'}}>
                              <td style={{padding:'0.3rem 0.5rem'}}>{r.date}</td>
                              <td style={{padding:'0.3rem 0.5rem',textAlign:'right',color:'#fbbf24',fontWeight:600}}>
                                {r.borrow_bal_qty!=null?(r.borrow_bal_qty/1e4).toLocaleString('ko-KR',{maximumFractionDigits:1})+'만':'-'}
                              </td>
                              <td style={{padding:'0.3rem 0.5rem',textAlign:'right',color:'#f87171'}}>
                                {r.short_qty!=null?(r.short_qty/1e4).toLocaleString('ko-KR',{maximumFractionDigits:1})+'만':'-'}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </>
                ) : (
                  <div style={{textAlign:'center',padding:'2rem',color:'var(--text-secondary)'}}>
                    데이터가 없습니다. 종목코드 또는 종목명을 입력하세요.
                  </div>
                )}
              </div>
            )}
            {!shortHistory && (
              <div className="glass-panel" style={{padding:'2rem',textAlign:'center',color:'var(--text-secondary)'}}>
                <p>종목코드(예: 005930) 또는 종목명(예: 삼성전자)을 입력 후 조회하세요.</p>
              </div>
            )}
          </div>
        );
      })()}

      {/* ── 내외국인대차 탭 ── */}
      {miTab === 'short_forg' && (
        <div style={{display:'flex',flexDirection:'column',gap:'1rem'}}>
          {/* 잔고비교 차트 */}
          <div className="glass-panel" style={{padding:'1rem'}}>
            <h3 style={{margin:'0 0 0.8rem',fontSize:'0.9rem',fontWeight:700}}>내외국인 대차잔고 비교 (최근 120일)</h3>
            {shortForeign?.balance?.length > 0 ? (
              <ResponsiveContainer width="100%" height={220}>
                <ComposedChart data={shortForeign.balance} margin={{top:5,right:10,bottom:5,left:10}}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                  <XAxis dataKey="bas_dt" tick={{fontSize:9,fill:'#94a3b8'}}
                    tickFormatter={d=>d?.slice(4)} interval="preserveStartEnd"/>
                  <YAxis yAxisId="amt" tick={{fontSize:9,fill:'#94a3b8'}}
                    tickFormatter={v=>`${(v/1e6).toFixed(0)}조`}/>
                  <YAxis yAxisId="pct" orientation="right" tick={{fontSize:9,fill:'#94a3b8'}}
                    tickFormatter={v=>`${v?.toFixed(0)}%`}/>
                  <Tooltip contentStyle={{background:'var(--bg-dark)',border:'1px solid var(--glass-border)',fontSize:'0.75rem'}}
                    formatter={(v,n)=>n.includes('%')?[`${v?.toFixed(1)}%`,n]:[`${(v/1e6).toFixed(1)}조`,n]}
                    labelFormatter={l=>`날짜: ${l}`}/>
                  <Legend wrapperStyle={{fontSize:'0.75rem'}}/>
                  <Area yAxisId="amt" type="monotone" dataKey="ntiv_brw_bal" name="내국인차입"
                    fill="rgba(96,165,250,0.15)" stroke="#60a5fa" strokeWidth={2} dot={false}/>
                  <Area yAxisId="amt" type="monotone" dataKey="forg_brw_bal" name="외국인차입"
                    fill="rgba(248,113,113,0.15)" stroke="#f87171" strokeWidth={2} dot={false}/>
                  <Line yAxisId="pct" type="monotone" dataKey="brw_bal_forg_rto" name="외국인비율%"
                    stroke="#fbbf24" strokeWidth={1.5} dot={false} strokeDasharray="4 2"/>
                </ComposedChart>
              </ResponsiveContainer>
            ) : (
              <div style={{textAlign:'center',padding:'2rem',color:'var(--text-secondary)'}}>
                내외국인 대차잔고 데이터가 없습니다. 스케줄러가 수집 후 표시됩니다.
              </div>
            )}
          </div>
          {/* 거래량 차트 */}
          <div className="glass-panel" style={{padding:'1rem'}}>
            <h3 style={{margin:'0 0 0.8rem',fontSize:'0.9rem',fontWeight:700}}>내외국인 대차체결 거래량</h3>
            {shortForeign?.trade?.length > 0 ? (
              <ResponsiveContainer width="100%" height={180}>
                <ComposedChart data={shortForeign.trade} margin={{top:5,right:10,bottom:5,left:10}}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                  <XAxis dataKey="bas_dt" tick={{fontSize:9,fill:'#94a3b8'}}
                    tickFormatter={d=>d?.slice(4)} interval="preserveStartEnd"/>
                  <YAxis tick={{fontSize:9,fill:'#94a3b8'}}
                    tickFormatter={v=>`${(v/1e4).toFixed(0)}만`}/>
                  <Tooltip contentStyle={{background:'var(--bg-dark)',border:'1px solid var(--glass-border)',fontSize:'0.75rem'}}
                    formatter={(v,n)=>[`${(v/1e4).toFixed(1)}만주`,n]}
                    labelFormatter={l=>`날짜: ${l}`}/>
                  <Legend wrapperStyle={{fontSize:'0.75rem'}}/>
                  <Bar dataKey="forg_lnb_ccl_stck_cnt" name="외국인체결" fill="rgba(248,113,113,0.7)" maxBarSize={12}/>
                  <Bar dataKey="ntiv_lnb_ccl_stck_cnt" name="내국인체결" fill="rgba(96,165,250,0.7)" maxBarSize={12}/>
                </ComposedChart>
              </ResponsiveContainer>
            ) : (
              <div style={{textAlign:'center',padding:'2rem',color:'var(--text-secondary)'}}>
                데이터가 없습니다. 스케줄러 수집 후 표시됩니다.
              </div>
            )}
          </div>
          {/* 월별 집계 */}
          {shortMonthly?.rows?.length > 0 && (
            <div className="glass-panel" style={{padding:'1rem'}}>
              <h3 style={{margin:'0 0 0.8rem',fontSize:'0.9rem',fontWeight:700}}>월별 대차거래 집계</h3>
              <div style={{overflowX:'auto',overflowY:'clip'}}>
                <table style={{width:'100%',borderCollapse:'collapse',fontSize:'0.78rem'}}>
                  <thead>
                    <tr style={{borderBottom:'2px solid var(--glass-border)'}}>
                      {['기준월','대차잔여(만주)','대차잔액(억)','체결(만주)','상환(만주)'].map(h=>(
                        <th key={h} style={{padding:'0.35rem 0.6rem',textAlign:h==='기준월'?'left':'right',
                          color:'var(--text-secondary)',fontWeight:600}}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {[...shortMonthly.rows].reverse().slice(0,18).map((r,i)=>(
                      <tr key={i} style={{borderBottom:'1px solid rgba(255,255,255,0.04)'}}>
                        <td style={{padding:'0.35rem 0.6rem'}}>{r.bas_dt}</td>
                        <td style={{padding:'0.35rem 0.6rem',textAlign:'right',color:'#fbbf24',fontWeight:600}}>
                          {r.lnb_rman_stck_cnt!=null?(r.lnb_rman_stck_cnt/1e4).toLocaleString('ko-KR',{maximumFractionDigits:1})+'만':'-'}
                        </td>
                        <td style={{padding:'0.35rem 0.6rem',textAlign:'right',color:'#f87171'}}>
                          {r.lnb_bal!=null?Math.round(r.lnb_bal/100).toLocaleString('ko-KR')+'억':'-'}
                        </td>
                        <td style={{padding:'0.35rem 0.6rem',textAlign:'right',color:'var(--text-secondary)'}}>
                          {r.lnb_ccl_stck_cnt!=null?(r.lnb_ccl_stck_cnt/1e4).toLocaleString('ko-KR',{maximumFractionDigits:1})+'만':'-'}
                        </td>
                        <td style={{padding:'0.35rem 0.6rem',textAlign:'right',color:'var(--text-secondary)'}}>
                          {r.lnb_rdpt_stck_cnt!=null?(r.lnb_rdpt_stck_cnt/1e4).toLocaleString('ko-KR',{maximumFractionDigits:1})+'만':'-'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
});

export default MarketIndicatorsView;
