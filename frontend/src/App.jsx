import React, { useState, useEffect, useCallback } from 'react';
import {
  AreaChart, Area, ComposedChart, Bar, Cell, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend, ReferenceLine
} from 'recharts';
import {
  TrendingUp, Search, Cpu, Activity,
  LayoutDashboard, Database, Globe, BarChart3,
  Star, StarOff, Trash2, Plus, Eye, FileText, Target,
  Newspaper, Send, FlaskConical, Ship, Wallet, Settings, Server
} from 'lucide-react';

// ──────────────────────────────────────────────────────────────
// [버그 ① 수정] API_BASE를 절대경로(포트 하드코딩)에서 상대경로로 변경.
// vite.config.js의 proxy 설정이 /api/* 요청을 백엔드로 전달하므로
// 직접 :8000 포트를 지정하면 proxy를 우회하고 CORS 오류가 발생.
// ──────────────────────────────────────────────────────────────
const API = (path) => path;

const isKRMarketOpen = () => {
  const now = new Date();
  const day = now.getDay();
  if (day===0||day===6) return false;
  const kst = new Date(now.toLocaleString('en-US',{timeZone:'Asia/Seoul'}));
  const t = kst.getHours()*100+kst.getMinutes();
  return t>=900 && t<=1535;
};
const isUSMarketOpen = () => {
  const now = new Date();
  const est = new Date(now.toLocaleString('en-US',{timeZone:'America/New_York'}));
  const day = est.getDay();
  if (day===0||day===6) return false;
  const t = est.getHours()*100+est.getMinutes();
  return t>=930 && t<=1600;
};
const anyMarketOpen = () => isKRMarketOpen()||isUSMarketOpen();

// 공시 조회 가능 시간: 평일 08:00~20:00 KST (장 마감 후 공시 포함)
const isDisclosureTime = () => {
  const now = new Date();
  const kst = new Date(now.toLocaleString('en-US', { timeZone: 'Asia/Seoul' }));
  const day  = kst.getDay();
  if (day === 0 || day === 6) return false;
  const t = kst.getHours() * 100 + kst.getMinutes();
  return t >= 800 && t <= 2000;
};

const useIsMobile = () => {
  const [isMobile, setIsMobile] = useState(window.innerWidth < 768);
  useEffect(() => {
    const fn = () => setIsMobile(window.innerWidth < 768);
    window.addEventListener('resize', fn);
    return () => window.removeEventListener('resize', fn);
  }, []);
  return isMobile;
};

// ── localStorage 헬퍼 (새로고침 후 상태 복원) ──────────────────────────
const _lsGet = (key, fallback) => { try { const v = localStorage.getItem(key); return v !== null ? JSON.parse(v) : fallback; } catch { return fallback; } };
const _lsSet = (key, val) => { try { localStorage.setItem(key, JSON.stringify(val)); } catch {} };


// ── 시장 지표 뷰 ──────────────────────────────────────────────
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

  const fmtAmt = (v) => {
    if (!v && v !== 0) return '-';
    const abs = Math.abs(v);
    const sign = v < 0 ? '-' : '+';
    if (abs >= 100000) return `${sign}${(abs/100000).toFixed(1)}조`;
    if (abs >= 10000)  return `${sign}${Math.round(abs/1000)}천억`;
    if (abs >= 1000)   return `${sign}${(abs/1000).toFixed(1)}천억`;
    return `${sign}${Math.round(abs)}억`;
  };
  const fmtChg = (v) => {
    if (!v && v !== 0) return { txt: '-', color: 'var(--text-secondary)' };
    return { txt: fmtAmt(v), color: v >= 0 ? '#f87171' : '#60a5fa' };
  };
  const fmtPct = (v) => {
    if (!v && v !== 0) return '-';
    return `${v > 0 ? '+' : ''}${v}%`;
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
      <div style={{overflowX:'auto'}}>
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
                  <td style={{padding:'0.35rem 0.5rem'}}>
                    <button onClick={()=>{onChangeStock(r.stock_code);onChangeTab('analysis');}}
                      style={{background:'none',border:'none',color:'var(--text-primary)',cursor:'pointer',fontWeight:600,fontSize:'0.8rem',padding:0}}>
                      {r.stock_name}
                    </button>
                    <span style={{fontSize:'0.7rem',color:'var(--text-secondary)',marginLeft:'0.3rem'}}>{r.stock_code}</span>
                  </td>
                  <td style={{padding:'0.35rem 0.5rem',textAlign:'right',color:'rgba(255,255,255,0.55)',fontSize:'0.75rem'}}>
                    {r.close?.toLocaleString()}원
                  </td>
                  {hasTodayPrice && (
                    <td style={{padding:'0.35rem 0.5rem',textAlign:'right'}}>
                      {r.today_close ? (
                        <div style={{display:'flex',flexDirection:'column',alignItems:'flex-end',gap:'1px'}}>
                          <span style={{fontWeight:700,fontSize:'0.82rem'}}>{r.today_close.toLocaleString()}원</span>
                          {chg != null && (
                            <span style={{fontSize:'0.7rem',fontWeight:600,color:chgC}}>
                              {chg >= 0 ? '▲' : '▼'}{Math.abs(chg).toFixed(2)}%
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
      <div style={{overflowX:'auto'}}>
        <table style={{width:'100%',borderCollapse:'collapse',fontSize:'0.8rem'}}>
          <thead>
            <tr style={{borderBottom:'1px solid var(--glass-border)'}}>
              <th style={{padding:'0.4rem 0.5rem',textAlign:'left',color:'var(--text-secondary)'}}>종목</th>
              <th style={{padding:'0.4rem 0.5rem',textAlign:'left',color:'var(--text-secondary)'}}>시장</th>
              <th style={{padding:'0.4rem 0.5rem',textAlign:'right',color:'var(--text-secondary)'}}>현재가</th>
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
              const instC = (r.inst_net_buy_amt || 0) >= 0 ? '#f87171' : '#60a5fa';
              const frnC  = (r.frn_net_buy_amt  || 0) >= 0 ? '#f87171' : '#60a5fa';
              return (
                <tr key={i} style={{borderBottom:'1px solid rgba(255,255,255,0.04)'}}
                  onMouseEnter={e=>e.currentTarget.style.background='rgba(255,255,255,0.04)'}
                  onMouseLeave={e=>e.currentTarget.style.background='transparent'}>
                  <td style={{padding:'0.35rem 0.5rem'}}>
                    <button onClick={()=>{onChangeStock(r.stock_code);onChangeTab('analysis');}}
                      style={{background:'none',border:'none',color:'var(--text-primary)',cursor:'pointer',fontWeight:600,fontSize:'0.8rem',padding:0}}>
                      {r.stock_name}
                    </button>
                    <span style={{fontSize:'0.7rem',color:'var(--text-secondary)',marginLeft:'0.3rem'}}>{r.stock_code}</span>
                  </td>
                  <td style={{padding:'0.35rem 0.5rem'}}>
                    <span style={{fontSize:'0.7rem',padding:'0.1rem 0.4rem',borderRadius:'4px',
                      background: mktShort==='KOSPI'?'rgba(248,113,113,0.15)':'rgba(96,165,250,0.15)',
                      color: mktShort==='KOSPI'?'#f87171':'#60a5fa'}}>
                      {mktShort}
                    </span>
                  </td>
                  <td style={{padding:'0.35rem 0.5rem',textAlign:'right'}}>{r.close?.toLocaleString()}원</td>
                  <td style={{padding:'0.35rem 0.5rem',textAlign:'right',fontWeight:700,color:'#fbbf24'}}>{r.turnover_pct?.toFixed(2)}%</td>
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
                {d.change_rate>=0?'▲':'▼'}{Math.abs(d.change_rate)}%
              </span>
              <span style={{fontSize:'0.72rem',color:'var(--text-secondary)'}}>{d.date}</span>
            </div>
          ) : null)}
        </div>
      </div>

      {/* 탭 */}
      <div style={{display:'flex',gap:'0.5rem',marginBottom:'1rem',flexWrap:'wrap'}}>
        {miTabBtn('investor',  '📊 투자자별 순매수')}
        {miTabBtn('turnover',  '🔄 회전율 상위')}
        {miTabBtn('trend',     '📈 수급 추이')}
      </div>

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

              {/* 일별 순매수 바 차트 */}
              <div className="glass-panel" style={{padding:'1rem'}}>
                <h3 style={{margin:'0 0 1rem',fontSize:'0.9rem',fontWeight:700}}>
                  일별 투자자 순매수 (억원)
                </h3>
                <ResponsiveContainer width="100%" height={250}>
                  <ComposedChart data={trendData.data} margin={{top:5,right:10,bottom:5,left:10}}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                    <XAxis dataKey="date" tick={{fontSize:10,fill:'#94a3b8'}} tickFormatter={d=>d?.slice(5)} interval="preserveStartEnd" />
                    <YAxis tick={{fontSize:10,fill:'#94a3b8'}} />
                    <Tooltip contentStyle={{background:'var(--bg-dark)',border:'1px solid var(--glass-border)',fontSize:'0.78rem'}}
                      formatter={(v,n) => [`${v?.toLocaleString()}억`, n]} />
                    <ReferenceLine y={0} stroke="rgba(255,255,255,0.2)" />
                    <Bar dataKey="inst_amt" fill="#f87171" name="기관" opacity={0.8} />
                    <Bar dataKey="frn_amt"  fill="#fbbf24" name="외국인" opacity={0.8} />
                    <Bar dataKey="ind_amt"  fill="#60a5fa" name="개인" opacity={0.8} />
                    <Legend wrapperStyle={{fontSize:'0.78rem',color:'var(--text-secondary)'}} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>

              {/* 누적 순매수 라인 차트 */}
              <div className="glass-panel" style={{padding:'1rem'}}>
                <h3 style={{margin:'0 0 1rem',fontSize:'0.9rem',fontWeight:700}}>
                  누적 순매수 추이 (기관/외국인)
                </h3>
                <ResponsiveContainer width="100%" height={220}>
                  <ComposedChart data={trendData.data} margin={{top:5,right:10,bottom:5,left:10}}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                    <XAxis dataKey="date" tick={{fontSize:10,fill:'#94a3b8'}} tickFormatter={d=>d?.slice(5)} interval="preserveStartEnd" />
                    <YAxis tick={{fontSize:10,fill:'#94a3b8'}} />
                    <Tooltip contentStyle={{background:'var(--bg-dark)',border:'1px solid var(--glass-border)',fontSize:'0.78rem'}}
                      formatter={(v,n) => [`${v?.toLocaleString()}억`, n]} />
                    <ReferenceLine y={0} stroke="rgba(255,255,255,0.2)" />
                    <Line type="monotone" dataKey="cum_inst" stroke="#f87171" dot={false} strokeWidth={2} name="기관 누적" />
                    <Line type="monotone" dataKey="cum_frn"  stroke="#fbbf24" dot={false} strokeWidth={2} name="외국인 누적" />
                    <Legend wrapperStyle={{fontSize:'0.78rem',color:'var(--text-secondary)'}} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>

              {/* 최근 10일 테이블 */}
              <div className="glass-panel" style={{padding:'1rem'}}>
                <h3 style={{margin:'0 0 0.8rem',fontSize:'0.9rem',fontWeight:700}}>최근 데이터 (일별)</h3>
                <div style={{overflowX:'auto'}}>
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
    </div>
  );
});


const App = () => {
  const isMobile = useIsMobile();
  const [activeTab, setActiveTab] = useState(() => _lsGet('sd_activeTab', 'macro'));
  const [portfolioAuth, setPortfolioAuth] = useState(false);
  const [selectedStock, setSelectedStock] = useState(() => _lsGet('sd_selectedStock', '005930'));
  const [shortData, setShortData]         = React.useState(null); // 대차잔고
  const [watchlist, setWatchlist] = useState([]);
  const [chartData, setChartData] = useState([]);
  const [finTable, setFinTable] = useState([]);
  const [summStats, setSummStats] = useState(null);
  const [aiReport, setAiReport] = useState(null);
  const [macroData, setMacroData] = useState(null);
  const [sysStats, setSysStats] = useState(null);
  const [loading, setLoading]         = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = React.useState([]);
  const [showSearchDrop, setShowSearchDrop] = React.useState(false);
  const [chartDays, setChartDays]     = useState(() => _lsGet('sd_chartDays', 30));
  const [quarterTable, setQuarterTable] = useState([]);
  const [cfAnnual, setCfAnnual]       = useState([]);   // 연간 현금흐름표
  const [cfQuarter, setCfQuarter]     = useState([]);   // 분기 현금흐름표
  const cfPollRef = React.useRef(null);   // 현금흐름 폴링 타이머
  const [collecting, setCollecting]   = useState(false);
  const [selectedStockName, setSelectedStockName] = useState(""); // 종목명 (watchlist 없어도 표시)

  // localStorage 동기화 (탭/종목 전환 시 저장 → 새로고침 후 복원)
  const changeTab = React.useCallback((tab) => {
    _lsSet('sd_activeTab', tab);
    setActiveTab(tab);
  }, []);

  const changeStock = React.useCallback((code) => {
    _lsSet('sd_selectedStock', code);
    setSelectedStock(code);
  }, []);

  const changeChartDays = React.useCallback((d) => {
    _lsSet('sd_chartDays', d);
    setChartDays(d);
  }, []);

  const fetchWatchlist = useCallback(async () => {
    try {
      const res = await fetch(API('/api/commands/watchlist'));
      if (res.ok) setWatchlist(await res.json());
    } catch (e) { console.error("Watchlist fetch error", e); }
  }, []);

  const fetchMacro = useCallback(async () => {
    try {
      // /api/realtime/macro: 장중이면 Yahoo 즉시 갱신 후 반환, 장외면 DB 최신값 반환
      const res = await fetch(API('/api/realtime/macro'));
      if (res.ok) {
        const data = await res.json();
        setMacroData(data);
      }
    } catch (e) { console.error("Macro fetch error", e); }
  }, []);


  const fetchSystem = useCallback(async () => {
    try {
      const res = await fetch(API('/api/dashboard/stats'));
      if (res.ok) setSysStats(await res.json());
    } catch (e) { console.error("System fetch error", e); }
  }, []);

  // 차트 기간 변경 — 보유 데이터보다 긴 기간 요청 시 재fetch
  const handleChartDaysChange = (days) => {
    changeChartDays(days);
    // 현재 로드된 데이터가 요청 기간보다 짧으면 재fetch
    if (days > chartData.length) {
      fetchStockDetail(days);
    }
  };

  // 전체 데이터 로드 (종목/탭 변경 시)
  const [marketInfo, setMarketInfo] = React.useState({});
  const fetchIdRef = React.useRef(0);

  const fetchStockDetail = useCallback(async (days) => {
    if (!selectedStock || selectedStock === 'None') return;

    // 이번 fetch의 고유 ID — 종목이 바뀌면 이 ID가 outdated됨
    const myId = ++fetchIdRef.current;
    const isStale = () => fetchIdRef.current !== myId;  // 종목이 바뀌었으면 true

    const d = days !== undefined ? days : chartDays;
    setLoading(true); setChartData([]); setFinTable([]); setQuarterTable([]);
    setCfAnnual([]); setCfQuarter([]);
    setSummStats(null); setAiReport(null); setCollecting(false);
    setMarketInfo({}); setSelectedStockName(''); setShortData(null);
    if (cfPollRef.current) { clearInterval(cfPollRef.current); cfPollRef.current = null; }

    // fetch 대상 종목코드를 지역변수로 고정 (클로저 내 stale 방지)
    const code = selectedStock;

    try {
      // ① 온디맨드 수집 트리거 + 종목명 취득
      fetch(API(`/api/commands/analyze/${code}`), { method: 'POST' })
        .then(r => r.ok ? r.json() : null)
        .then(data => { if (!isStale() && data?.stock_name) setSelectedStockName(data.stock_name); })
        .catch(() => {});

      // ① 시장정보 fetch
      fetch(API(`/api/dashboard/market-info/${code}`))
        .then(r => r.ok ? r.json() : null)
        .then(data => { if (!isStale() && data) setMarketInfo(data); })
        .catch(() => {});

      // ② 대차잔고 별도 fetch
      fetch(API(`/api/buy-candidates/short-sell/${code}`))
        .then(r => r.ok ? r.json() : null)
        .then(data => { if (!isStale()) setShortData(data); })
        .catch(() => { if (!isStale()) setShortData(null); });

      // 요청 기간 이상 항상 확보 (최소 365일, 10년 탭도 대응)
      const fetchDays = Math.max(d, 365);
      const [chartRes, tableRes, quarterRes, summRes, aiRes, cfARes, cfQRes] = await Promise.all([
        fetch(API(`/api/dashboard/chart/${code}?days=${fetchDays}`)),
        fetch(API(`/api/dashboard/financial-table/${code}?type=annual`)),
        fetch(API(`/api/dashboard/financial-table/${code}?type=quarter`)),
        fetch(API(`/api/dashboard/fundamentals/${code}`)),
        fetch(API(`/api/reports/latest/${code}`)),
        fetch(API(`/api/dashboard/cashflow/${code}?type=annual`)),
        fetch(API(`/api/dashboard/cashflow/${code}?type=quarter`)),
      ]);

      if (isStale()) return;  // 종목 전환됨 → 결과 버림

      if (chartRes.ok)   setChartData(await chartRes.json());
      if (tableRes.ok)   setFinTable(await tableRes.json());
      if (quarterRes.ok) setQuarterTable(await quarterRes.json());
      if (aiRes.ok)      setAiReport(await aiRes.json());

      const cfAData = cfARes.ok ? await cfARes.json() : [];
      const cfQData = cfQRes.ok ? await cfQRes.json() : [];
      if (!isStale()) { setCfAnnual(cfAData); setCfQuarter(cfQData); }

      // 현금흐름 백그라운드 수집 중 → 15초 간격으로 최대 8회 폴링
      if (cfAData.length === 0 && /^\d{6}$/.test(code)) {
        if (cfPollRef.current) clearInterval(cfPollRef.current);
        let cfTry = 0;
        cfPollRef.current = setInterval(async () => {
          if (isStale() || cfTry >= 8) { clearInterval(cfPollRef.current); return; }
          cfTry++;
          try {
            const [ra, rq] = await Promise.all([
              fetch(API(`/api/dashboard/cashflow/${code}?type=annual`)),
              fetch(API(`/api/dashboard/cashflow/${code}?type=quarter`)),
            ]);
            if (isStale()) { clearInterval(cfPollRef.current); return; }
            const da = ra.ok ? await ra.json() : [];
            const dq = rq.ok ? await rq.json() : [];
            if (da.length > 0 || dq.length > 0) {
              setCfAnnual(da); setCfQuarter(dq);
              clearInterval(cfPollRef.current);
            }
          } catch {}
        }, 15000);
      }

      if (summRes.ok) {
        const sData = await summRes.json();
        if (!isStale()) setSummStats(sData);

        // PBR/PER가 null이면 백그라운드 스크래핑 중 → 5초 후 재조회
        if (sData && sData.pbr === null && sData.per === null && /^\d{6}$/.test(code)) {
          setTimeout(async () => {
            if (isStale()) return;
            try {
              const r2 = await fetch(API(`/api/dashboard/fundamentals/${code}`));
              if (r2.ok && !isStale()) {
                const d2 = await r2.json();
                if (d2?.pbr !== null || d2?.per !== null) setSummStats(d2);
              }
            } catch {}
          }, 5000);
        }

        if (sData?.collecting) {
          if (!isStale()) setCollecting(true);
          let pollCount = 0;
          const poll = async () => {
            if (isStale()) return;  // ★ 종목 바뀌면 폴링 즉시 중단
            pollCount++;
            if (pollCount > 24) { if (!isStale()) setCollecting(false); return; }
            await new Promise(r => setTimeout(r, 10000));
            if (isStale()) return;  // ★ 대기 후 다시 체크
            try {
              const [c2, t2, q2, s2, cf2a, cf2q] = await Promise.all([
                fetch(API(`/api/dashboard/chart/${code}?days=365`)),
                fetch(API(`/api/dashboard/financial-table/${code}?type=annual`)),
                fetch(API(`/api/dashboard/financial-table/${code}?type=quarter`)),
                fetch(API(`/api/dashboard/fundamentals/${code}`)),
                fetch(API(`/api/dashboard/cashflow/${code}?type=annual`)),
                fetch(API(`/api/dashboard/cashflow/${code}?type=quarter`)),
              ]);
              if (isStale()) return;
              if (c2.ok) { const cd = await c2.json(); if (!isStale() && cd.length > 0) setChartData(cd); }
              if (t2.ok) { const td = await t2.json(); if (!isStale() && td.length > 0) setFinTable(td); }
              if (q2.ok) { const qd = await q2.json(); if (!isStale() && qd.length > 0) setQuarterTable(qd); }
              if (cf2a.ok) { const d = await cf2a.json(); if (!isStale() && d.length > 0) setCfAnnual(d); }
              if (cf2q.ok) { const d = await cf2q.json(); if (!isStale() && d.length > 0) setCfQuarter(d); }
              if (s2.ok) {
                const s2d = await s2.json();
                if (!isStale()) {
                  setSummStats(s2d);
                  if (s2d?.collecting) poll();
                  else setCollecting(false);
                }
              } else { if (!isStale()) setCollecting(false); }
            } catch { if (!isStale()) setCollecting(false); }
          };
          poll();
        } else {
          // 데이터가 없으면 10초 후 1회 재시도
          const hasNoData = !(await chartRes.json().catch(()=>[])).length;
          if (hasNoData) {
            setTimeout(async () => {
              if (isStale()) return;  // ★ 타이머 발동 전에 종목 바뀌면 취소
              try {
                const [c3, t3, q3] = await Promise.all([
                  fetch(API(`/api/dashboard/chart/${code}?days=365`)),
                  fetch(API(`/api/dashboard/financial-table/${code}?type=annual`)),
                  fetch(API(`/api/dashboard/financial-table/${code}?type=quarter`)),
                ]);
                if (isStale()) return;
                if (c3.ok) { const cd = await c3.json(); if (!isStale() && cd.length > 0) setChartData(cd); }
                if (t3.ok) { const td = await t3.json(); if (!isStale() && td.length > 0) setFinTable(td); }
                if (q3.ok) { const qd = await q3.json(); if (!isStale() && qd.length > 0) setQuarterTable(qd); }
              } catch {}
            }, 10000);
          }
        }
      }
    } catch (e) { console.error("Detail load error", e); }
    finally { if (!isStale()) setLoading(false); }
  }, [selectedStock]);

  useEffect(() => { fetchWatchlist(); fetchSystem(); }, []);

  // ── 매크로 300초 폴링 ────────────────────────────────────────
  // 앱 마운트 시 즉시 1회 + 이후 300초마다 자동 갱신
  useEffect(() => {
    fetchMacro(); // 마운트 즉시 1회
    const interval = anyMarketOpen()?300000:null;
    const iv = interval?setInterval(fetchMacro,interval):null;
    return ()=>{if(iv)clearInterval(iv);};
  }, [fetchMacro]);

  useEffect(() => {
    if (activeTab === "analysis" || activeTab === "insight") fetchStockDetail();
  }, [selectedStock, activeTab]);

  const handleSearch = async (e, overrideCode = null) => {
    if (e) e.preventDefault();
    const q = overrideCode || searchQuery.trim();
    if (!q) return;
    setShowSearchDrop(false); setSearchResults([]);
    setLoading(true);
    try {
      const res = await fetch(API(`/api/commands/analyze/${encodeURIComponent(q)}`), { method: 'POST' });
      if (res.ok) {
        const data = await res.json();
        changeStock(data.stock_code);
        if (data.stock_name) setSelectedStockName(data.stock_name);
        setSearchQuery("");
        fetchWatchlist();
        changeTab("analysis");
      }
    } catch (e) { console.error("Search error", e); }
    finally { setLoading(false); }
  };

  // 헤더 검색 자동완성
  React.useEffect(() => {
    if (!searchQuery.trim()) { setSearchResults([]); setShowSearchDrop(false); return; }
    const t = setTimeout(async () => {
      try {
        const res = await fetch(API(`/api/search?q=${encodeURIComponent(searchQuery.trim())}`));
        if (res.ok) {
          const data = await res.json();
          setSearchResults(data);
          setShowSearchDrop(data.length > 0);
        }
      } catch {}
    }, 200);
    return () => clearTimeout(t);
  }, [searchQuery]);

  // ── 전역 포맷터 ─────────────────────────────────────────────────────
  // fmtWonRaw: 원화 원값(원 단위)을 사람이 읽기 쉬운 단위로 변환
  const formatWon = (val) => {
    if (val == null || val === "" || val === "N/A") return "-";
    const n = Number(val); if (isNaN(n)) return "-";
    const abs = Math.abs(n), sign = n < 0 ? "-" : "";
    if (abs >= 1e12) return sign + (abs/1e12).toLocaleString('ko-KR',{maximumFractionDigits:1}) + "조원";
    if (abs >= 1e8)  return sign + Math.round(abs/1e8).toLocaleString('ko-KR') + "억원";
    if (abs >= 1e4)  return sign + Math.round(abs/1e4).toLocaleString('ko-KR') + "만원";
    return sign + Math.round(abs).toLocaleString('ko-KR') + "원";
  };
  // fmtPct: % 값 소수점 1자리 표시
  const fmtPct = (v, showSign = false) => {
    if (v == null) return '-';
    const n = Number(v); if (isNaN(n)) return '-';
    const sign = showSign ? (n >= 0 ? '+' : '') : '';
    return sign + n.toFixed(1) + '%';
  };
  // fmtUkWon: 억원 단위 입력값 → 조원/억원 표시 (재무제표용)
  const fmtUkWon = (v) => {
    if (v == null) return '-';
    const n = Number(v); if (isNaN(n)) return '-';
    const abs = Math.abs(n), sign = n < 0 ? '-' : '';
    if (abs >= 10000) return sign + (abs / 10000).toLocaleString('ko-KR', { maximumFractionDigits: 1 }) + '조원';
    return sign + Math.round(abs).toLocaleString('ko-KR') + '억원';
  };
  // fmtNum: 정수 콤마 표시 (소수점 없음)
  const fmtNum = (v) => {
    if (v == null) return '-';
    const n = Number(v); if (isNaN(n)) return '-';
    return Math.round(n).toLocaleString('ko-KR');
  };
  // ──────────────────────────────────────────────────────────────────

  const handleRemoveWatchlist = async (stock_code) => {
    try {
      const res = await fetch(API(`/api/commands/watchlist/${stock_code}`), { method: 'DELETE' });
      if (res.ok) setWatchlist(prev => prev.filter(i => i.stock_code !== stock_code));
    } catch (e) { console.error("Watchlist delete error", e); }
  };

  // ── 탭별 타이틀 ─────────────────────────────────────────────
  const TAB_TITLES = {
    macro:          "Global Market Overview",
    analysis:       "개별 종목",
    semiconductor_sector: "반도체 섹터",
    watchlist:      "관심종목 리스트",
    buy_candidates: "📋 매수 후보 시그널 보드",
    portfolio:      "계좌현황",
    settings:       "시스템 설정",
    screener:       "AI 종목 스크리너",
    trend:          "가상 매매 Leading",
    reports:        "섹터 보고서",
    insight:        "AI Analysis Deep Insight",
    system:         "Database Management",
    telegram:       "텔레그램 종목 언급 순위",
    hs_trade:       "수출입분석",
    hs_trade2:      "수출입분석",
  };

  // ── 매수후보 시그널 보드 ────────────────────────────────────────
  const BuyCandidateView = () => {
    const [candidates, setCandidates]   = React.useState([]);
    const [loading,    setLoading]      = React.useState(true);
    const [editId,     setEditId]       = React.useState(null);
    const [editForm,   setEditForm]     = React.useState({});
    const [addQuery,   setAddQuery]     = React.useState('');
    const [searchRes,  setSearchRes]    = React.useState([]);
    const [showDrop,   setShowDrop]     = React.useState(false);
    const [adding,     setAdding]       = React.useState(false);
    const [refDate1Label, setRefDate1Label] = React.useState('2026-01-01');
    const [refDate2Label, setRefDate2Label] = React.useState('2025-10-01');
    const [editRefDate1, setEditRefDate1]   = React.useState(false);
    const [editRefDate2, setEditRefDate2]   = React.useState(false);

    const load = () => {
      setLoading(true);
      fetch(API('/api/buy-candidates')).then(r=>r.ok?r.json():[]).then(d=>{
        // 정렬: 1) 목표가 도달(현재가<=목표가) 2) 매수신호(strong_buy>buy) 3) 기준일1 상승률
        const sorted = [...d].sort((a,b) => {
          const aReached = a.target_price && a.current_price && a.current_price <= a.target_price;
          const bReached = b.target_price && b.current_price && b.current_price <= b.target_price;
          if(aReached && !bReached) return -1;
          if(!aReached && bReached) return 1;
          const sigOrder = {add_buy:0,strong_buy:0,hold:1,hold_value:2,take_profit:3,caution:3,sell:4,real_sell:4,cut_loss:5,strong_sell:5};
          const aSig = sigOrder[a.trade_signal] ?? 2;
          const bSig = sigOrder[b.trade_signal] ?? 2;
          if(aSig !== bSig) return aSig - bSig;
          return (b.ref_chg1||0) - (a.ref_chg1||0);
        });
        setCandidates(sorted);
        setLoading(false);
      }).catch(()=>setLoading(false));
    };
    React.useEffect(()=>{ load(); }, []);

    // 종목 검색
    React.useEffect(()=>{
      if(!addQuery.trim()){ setSearchRes([]); setShowDrop(false); return; }
      const t = setTimeout(async()=>{
        const res = await fetch(API(`/api/search?q=${encodeURIComponent(addQuery)}`));
        if(res.ok){ setSearchRes(await res.json()); setShowDrop(true); }
      }, 300);
      return ()=>clearTimeout(t);
    }, [addQuery]);

    const addCandidate = async (code, name) => {
      setAdding(true); setShowDrop(false); setAddQuery('');
      await fetch(API('/api/buy-candidates'), {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({stock_code:code, stock_name:name}),
      });
      setAdding(false); load();
    };

    const deleteCandidate = async (code) => {
      if(!window.confirm(`${code} 매수후보에서 삭제하시겠습니까?`)) return;
      await fetch(API(`/api/buy-candidates/${code}`), {method:'DELETE'});
      load();
    };

    const saveEdit = async (code) => {
      await fetch(API(`/api/buy-candidates/${code}`), {
        method:'PATCH', headers:{'Content-Type':'application/json'},
        body: JSON.stringify(editForm),
      });
      setEditId(null); setEditForm({}); load();
    };

    const SIG = {
      strong_buy:  {emoji:'🟢', label:'강매수',  color:'#22c55e', bg:'rgba(34,197,94,0.15)'},
      buy:         {emoji:'🟢', label:'매수',    color:'#22c55e', bg:'rgba(34,197,94,0.08)'},
      hold:        {emoji:'🟡', label:'대기',    color:'#fbbf24', bg:'rgba(251,191,36,0.1)'},
      caution:     {emoji:'🟠', label:'주의',    color:'#f97316', bg:'rgba(249,115,22,0.12)'},
      sell:        {emoji:'🔴', label:'진입불가', color:'#ef4444', bg:'rgba(239,68,68,0.1)'},
      strong_sell: {emoji:'🔴', label:'강진입불가',color:'#dc2626',bg:'rgba(220,38,38,0.15)'},
    };

    const fp = (v) => v ? Math.round(v).toLocaleString('ko-KR') : '-';
    const pc = (v) => !v ? 'rgba(255,255,255,0.4)' : v>0?'#ef4444':'#3b82f6';
    const pctStr = (v) => v==null?'-':(v>=0?'+':'')+Number(v).toFixed(1)+'%';
    const fmtMkt = (v) => {
      if(!v) return '-';
      // stock_universe.market_cap 단위: 원(KRW)
      if(v >= 1e12) return (v/1e12).toLocaleString('ko-KR',{maximumFractionDigits:1})+'조원';
      if(v >= 1e8)  return Math.round(v/1e8).toLocaleString('ko-KR')+'억원';
      return Math.round(v/1e4).toLocaleString('ko-KR')+'만원';
    };

    const inputSt = {padding:'0.25rem 0.5rem',borderRadius:'5px',background:'rgba(255,255,255,0.08)',
      border:'1px solid rgba(255,255,255,0.2)',color:'#fff',fontSize:'0.78rem',width:'100%'};

    return (
      <div className="fade-in" style={{display:'flex',flexDirection:'column',gap:'1rem'}}>
        {/* 헤더 */}
        <div className="glass-panel" style={{padding:'1rem 1.2rem',display:'flex',alignItems:'center',justifyContent:'space-between',flexWrap:'wrap',gap:'0.75rem',position:'relative'}}>
          <div style={{display:'flex',alignItems:'center',gap:'0.6rem'}}>
            <Target size={20} color="#f59e0b"/>
            <h2 style={{fontSize:'1rem',fontWeight:700}}>매수 후보 시그널 보드</h2>
            <span style={{padding:'0.15rem 0.6rem',background:'rgba(245,158,11,0.15)',borderRadius:'20px',fontSize:'0.72rem',color:'#f59e0b'}}>
              {candidates.length}종목
            </span>
          </div>
          <div style={{display:'flex',gap:'0.5rem',position:'relative'}}>
            <input value={addQuery} onChange={e=>setAddQuery(e.target.value)}
              placeholder="종목명 검색 후 추가..."
              style={{padding:'0.4rem 0.8rem',borderRadius:'8px',background:'rgba(255,255,255,0.06)',
                border:'1px solid var(--glass-border)',color:'#fff',fontSize:'0.82rem',width:'200px'}}/>
            {showDrop && searchRes.length>0 && (
              <div style={{position:'absolute',top:'100%',left:0,right:0,marginTop:'3px',
                background:'rgba(20,20,35,0.97)',border:'1px solid var(--glass-border)',
                borderRadius:'8px',zIndex:50,overflow:'hidden',boxShadow:'0 8px 24px rgba(0,0,0,0.5)'}}>
                {searchRes.slice(0,8).map((item,i)=>(
                  <div key={i} onClick={()=>addCandidate(item.code,item.name)}
                    style={{padding:'0.5rem 0.8rem',cursor:'pointer',display:'flex',justifyContent:'space-between',
                      borderBottom:'1px solid rgba(255,255,255,0.05)',fontSize:'0.82rem'}}
                    onMouseEnter={e=>e.currentTarget.style.background='rgba(245,158,11,0.1)'}
                    onMouseLeave={e=>e.currentTarget.style.background='transparent'}>
                    <span style={{fontWeight:600}}>{item.name}</span>
                    <span style={{color:'var(--text-secondary)'}}>{item.code}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div style={{fontSize:'0.72rem',color:'rgba(255,255,255,0.35)'}}>
            💡 행 더블클릭 → 목표가/기준일 수정 가능
          </div>
        </div>

        {/* 테이블 */}
        {loading ? (
          <div style={{textAlign:'center',padding:'3rem',color:'var(--text-secondary)'}}>로딩 중...</div>
        ) : candidates.length === 0 ? (
          <div className="glass-panel" style={{padding:'3rem',textAlign:'center',color:'var(--text-secondary)'}}>
            <Target size={40} style={{margin:'0 auto 1rem',display:'block',opacity:0.3}}/>
            <p>매수 후보 종목을 추가해주세요.</p>
          </div>
        ) : (
          <div className="glass-panel" style={{overflow:'auto'}}>
            <table className="premium-table" style={{width:'100%',minWidth:'1100px'}}>
              <thead><tr>
                <th style={{minWidth:'110px'}}>기업명</th>
                <th style={{textAlign:'right',minWidth:'80px'}}>시총</th>
                <th style={{textAlign:'center',minWidth:'65px'}}>매수신호</th>
                <th style={{textAlign:'right',minWidth:'80px'}}>현재가</th>
                <th style={{textAlign:'right',minWidth:'70px'}}>변동(%)</th>
                <th style={{textAlign:'right',minWidth:'85px'}}>목표매수가</th>
                <th style={{textAlign:'center',minWidth:'130px',cursor:'pointer'}}>
                  {editRefDate1 ? (
                    <input value={refDate1Label} onChange={e=>setRefDate1Label(e.target.value)}
                      onBlur={()=>{setEditRefDate1(false);}}
                      onKeyDown={e=>{ if(e.key==='Enter') setEditRefDate1(false); }}
                      autoFocus style={{width:'100px',padding:'2px 4px',borderRadius:'4px',
                        background:'rgba(255,255,255,0.1)',border:'1px solid #f59e0b',
                        color:'#fff',fontSize:'0.72rem',textAlign:'center'}}/>
                  ) : (
                    <span onClick={()=>setEditRefDate1(true)}
                      title="클릭하여 날짜 변경"
                      style={{color:'#f59e0b',cursor:'pointer',textDecoration:'underline dotted'}}>
                      {refDate1Label} 대비 ✎
                    </span>
                  )}
                </th>
                <th style={{textAlign:'center',minWidth:'130px',cursor:'pointer'}}>
                  {editRefDate2 ? (
                    <input value={refDate2Label} onChange={e=>setRefDate2Label(e.target.value)}
                      onBlur={()=>setEditRefDate2(false)}
                      onKeyDown={e=>{ if(e.key==='Enter') setEditRefDate2(false); }}
                      autoFocus style={{width:'100px',padding:'2px 4px',borderRadius:'4px',
                        background:'rgba(255,255,255,0.1)',border:'1px solid #f59e0b',
                        color:'#fff',fontSize:'0.72rem',textAlign:'center'}}/>
                  ) : (
                    <span onClick={()=>setEditRefDate2(true)}
                      title="클릭하여 날짜 변경"
                      style={{color:'#f59e0b',cursor:'pointer',textDecoration:'underline dotted'}}>
                      {refDate2Label} 대비 ✎
                    </span>
                  )}
                </th>
                <th style={{textAlign:'left',minWidth:'180px'}}>추세추종 / 차트신호</th>
                <th style={{minWidth:'70px'}}></th>
              </tr></thead>
              <tbody>
                {candidates.map(h => {
                  const sig = SIG[h.trade_signal] || SIG.hold;
                  const isEdit = editId === h.stock_code;
                  return (
                    <tr key={h.stock_code}
                      onDoubleClick={()=>{ setEditId(h.stock_code); setEditForm({
                        target_price: h.target_price||'',
                        ref_date1: h.ref_date1||'', ref_price1: h.ref_price1||'',
                        ref_date2: h.ref_date2||'', ref_price2: h.ref_price2||'',
                        memo: h.memo||'',
                      }); }}
                      style={{cursor:'pointer',background:isEdit?'rgba(245,158,11,0.05)':undefined}}>

                      {/* 기업명 */}
                      <td onClick={()=>{changeStock(h.stock_code);changeTab('analysis');}}>
                        <div style={{fontWeight:700,fontSize:'0.85rem',color:'var(--text-primary)',cursor:'pointer'}}>
                          {h.stock_name}
                        </div>
                        <div style={{fontSize:'0.68rem',color:'var(--text-secondary)'}}>{h.stock_code}</div>
                      </td>

                      {/* 시총 */}
                      <td style={{textAlign:'right',fontSize:'0.8rem',color:'var(--text-secondary)'}}>{fmtMkt(h.mktcap)}</td>

                      {/* 매수 신호등 */}
                      <td style={{textAlign:'center'}}>
                        <div title={h.trade_reason} style={{display:'inline-flex',flexDirection:'column',alignItems:'center',
                          padding:'2px 6px',borderRadius:'6px',background:sig.bg,cursor:'help'}}>
                          <span style={{fontSize:'1rem',lineHeight:1}}>{sig.emoji}</span>
                          <span style={{fontSize:'0.58rem',color:sig.color,fontWeight:700}}>{sig.label}</span>
                        </div>
                      </td>

                      {/* 현재가 */}
                      <td style={{textAlign:'right',fontWeight:700,fontSize:'0.88rem'}}>
                        {h.current_price ? fp(h.current_price)+'원' : '-'}
                      </td>

                      {/* 등락률 */}
                      <td style={{textAlign:'right',fontWeight:600,color:pc(h.change_pct)}}>
                        {pctStr(h.change_pct)}
                      </td>

                      {/* 목표매수가 */}
                      <td style={{textAlign:'right'}}>
                        {isEdit ? (
                          <input value={editForm.target_price} onChange={e=>setEditForm(p=>({...p,target_price:e.target.value}))}
                            style={inputSt} placeholder="목표가"/>
                        ) : (
                          <span style={{fontSize:'0.85rem',color:'#f59e0b',fontWeight:700}}>
                            {h.target_price ? fp(h.target_price)+'원' : <span style={{color:'rgba(255,255,255,0.3)',fontSize:'0.75rem'}}>미설정</span>}
                          </span>
                        )}
                        {h.target_price && h.current_price && !isEdit && (() => {
                          const diff = ((h.current_price - h.target_price) / h.target_price * 100);
                          const reached = h.current_price <= h.target_price;
                          return (
                            <div style={{fontSize:'0.65rem',marginTop:'2px',fontWeight:600,
                              color:reached?'#22c55e':'rgba(255,255,255,0.5)'}}>
                              {reached
                                ? '✓ 목표가 ▼'+Math.abs(diff).toFixed(1)+'%'
                                : '▲ '+diff.toFixed(1)+'% 위'}
                            </div>
                          );
                        })()}
                      </td>

                      {/* 기준일1 대비 */}
                      <td style={{textAlign:'center'}}>
                        {isEdit ? (
                          <div style={{display:'flex',flexDirection:'column',gap:'2px'}}>
                            <input value={editForm.ref_date1} onChange={e=>setEditForm(p=>({...p,ref_date1:e.target.value}))}
                              style={inputSt} placeholder="2026-01-01"/>
                            <input value={editForm.ref_price1} onChange={e=>setEditForm(p=>({...p,ref_price1:e.target.value}))}
                              style={inputSt} placeholder="기준가"/>
                          </div>
                        ) : h.ref_price1 ? (
                          <div style={{textAlign:'center'}}>
                            <div style={{fontSize:'0.7rem',color:'var(--text-secondary)'}}>{fp(h.ref_price1)}원</div>
                            <div style={{fontSize:'0.85rem',fontWeight:700,color:pc(h.ref_chg1)}}>{pctStr(h.ref_chg1)}</div>
                          </div>
                        ) : <span style={{color:'rgba(255,255,255,0.2)',fontSize:'0.7rem'}}>미설정</span>}
                      </td>

                      {/* 기준일2 대비 */}
                      <td style={{textAlign:'center'}}>
                        {isEdit ? (
                          <div style={{display:'flex',flexDirection:'column',gap:'2px'}}>
                            <input value={editForm.ref_date2} onChange={e=>setEditForm(p=>({...p,ref_date2:e.target.value}))}
                              style={inputSt} placeholder="2025-10-01"/>
                            <input value={editForm.ref_price2} onChange={e=>setEditForm(p=>({...p,ref_price2:e.target.value}))}
                              style={inputSt} placeholder="기준가"/>
                          </div>
                        ) : h.ref_price2 ? (
                          <div style={{textAlign:'center'}}>
                            <div style={{fontSize:'0.7rem',color:'var(--text-secondary)'}}>{fp(h.ref_price2)}원</div>
                            <div style={{fontSize:'0.85rem',fontWeight:700,color:pc(h.ref_chg2)}}>{pctStr(h.ref_chg2)}</div>
                          </div>
                        ) : <span style={{color:'rgba(255,255,255,0.2)',fontSize:'0.7rem'}}>미설정</span>}
                      </td>

                      {/* 추세추종 / 차트신호 */}
                      <td>
                        <div title={h.trade_reason} style={{fontSize:'0.72rem',color:sig.color,lineHeight:1.4,cursor:'help'}}>
                          {h.trade_reason ? h.trade_reason.split('[')[0].trim() : '-'}
                        </div>
                        {h.trade_reason && h.trade_reason.includes('[') && (
                          <div style={{fontSize:'0.62rem',color:'rgba(255,255,255,0.3)',marginTop:'2px'}}>
                            {h.trade_reason.match(/\[.*?\]/)?.[0]}
                          </div>
                        )}
                      </td>

                      {/* 액션 버튼 */}
                      <td>
                        {isEdit ? (
                          <div style={{display:'flex',flexDirection:'column',gap:'3px'}}>
                            <button onClick={()=>saveEdit(h.stock_code)}
                              style={{padding:'0.2rem 0.5rem',borderRadius:'4px',border:'none',
                                background:'#f59e0b',color:'#000',cursor:'pointer',fontSize:'0.72rem',fontWeight:700}}>저장</button>
                            <button onClick={()=>{setEditId(null);setEditForm({});}}
                              style={{padding:'0.2rem 0.5rem',borderRadius:'4px',
                                border:'1px solid var(--glass-border)',background:'transparent',
                                color:'var(--text-secondary)',cursor:'pointer',fontSize:'0.72rem'}}>취소</button>
                          </div>
                        ) : (
                          <button onClick={()=>deleteCandidate(h.stock_code)}
                            style={{padding:'0.2rem 0.45rem',borderRadius:'4px',border:'none',
                              background:'rgba(239,68,68,0.12)',color:'#ef4444',cursor:'pointer',fontSize:'0.72rem'}}>삭제</button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* 범례 */}
        <div className="glass-panel" style={{padding:'0.75rem 1rem',display:'flex',gap:'1rem',flexWrap:'wrap',alignItems:'center'}}>
          <span style={{fontSize:'0.72rem',color:'rgba(255,255,255,0.4)',fontWeight:600}}>신호 기준:</span>
          {Object.entries(SIG).map(([k,v])=>(
            <span key={k} style={{fontSize:'0.7rem',color:v.color}}>
              {v.emoji} {v.label}
            </span>
          ))}
          <span style={{fontSize:'0.68rem',color:'rgba(255,255,255,0.3)',marginLeft:'auto'}}>
            더블클릭 → 목표가/기준일/기준가 수정
          </span>
        </div>
      </div>
    );
  };


  // ── 관심종목 ─────────────────────────────────────────────────
  const WatchlistView = () => {
    const [addQuery, setAddQuery] = React.useState("");
    const [adding, setAdding] = React.useState(false);
    const [searchResults, setSearchResults] = React.useState([]);
    const [showDropdown, setShowDropdown] = React.useState(false);

    useEffect(() => {
      if (!addQuery.trim()) { setSearchResults([]); setShowDropdown(false); return; }
      const t = setTimeout(async () => {
        try {
          const res = await fetch(API(`/api/search?q=${encodeURIComponent(addQuery)}`));
          if (res.ok) { setSearchResults(await res.json()); setShowDropdown(true); }
        } catch {}
      }, 300);
      return () => clearTimeout(t);
    }, [addQuery]);

    const handleAdd = async (query = addQuery, e = null) => {
      if (e) e.preventDefault();
      if (!query.trim()) return;
      setAdding(true); setShowDropdown(false);
      try {
        const res = await fetch(API(`/api/commands/analyze/${encodeURIComponent(query.trim())}`), { method: 'POST' });
        if (res.ok) { setAddQuery(""); fetchWatchlist(); }
        else { const d = await res.json(); alert(`종목 추가 실패: ${d.detail || '알 수 없는 오류'}`); }
      } catch { alert('네트워크 오류가 발생했습니다.'); }
      finally { setAdding(false); }
    };

    return (
      <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <div className="glass-panel" style={{ padding: '1.2rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between', position: 'relative' }}>
          <div className="section-title" style={{ marginBottom: 0 }}>
            <Star size={20} color="var(--accent-purple)" />
            <h2 style={{ fontSize: '1.1rem' }}>관심종목 리스트</h2>
            <span style={{ marginLeft: '0.5rem', padding: '0.2rem 0.7rem', background: 'rgba(167,139,250,0.15)', borderRadius: '20px', fontSize: '0.75rem', color: 'var(--accent-purple)' }}>
              {watchlist.length}종목
            </span>
          </div>
          <form onSubmit={(e) => handleAdd(addQuery, e)} style={{ display: 'flex', gap: '0.5rem' }}>
            <input
              type="text" placeholder="종목명 입력 (ex. 삼성전자)"
              value={addQuery} onChange={e => setAddQuery(e.target.value)}
              onFocus={() => { if (searchResults.length > 0) setShowDropdown(true); }}
              style={{ padding: '0.45rem 0.9rem', borderRadius: '8px', background: 'rgba(255,255,255,0.06)', border: '1px solid var(--glass-border)', color: '#fff', fontSize: '0.85rem', width: '220px' }}
            />
            <button type="submit" disabled={adding} style={{ padding: '0.45rem 1rem', borderRadius: '8px', background: adding ? 'rgba(167,139,250,0.3)' : 'var(--accent-purple)', border: 'none', color: '#fff', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '0.4rem', fontWeight: 600 }}>
              <Plus size={15} />{adding ? '추가 중...' : '추가'}
            </button>
          </form>
          {showDropdown && searchResults.length > 0 && (
            <div style={{ position: 'absolute', top: '100%', right: '1.2rem', width: '300px', background: 'rgba(20,20,35,0.95)', backdropFilter: 'blur(10px)', border: '1px solid var(--glass-border)', borderRadius: '8px', marginTop: '4px', zIndex: 50, boxShadow: '0 10px 30px rgba(0,0,0,0.5)' }}>
              {searchResults.map((item, idx) => (
                <div key={idx} onClick={() => handleAdd(item.code)}
                  style={{ padding: '0.75rem 1rem', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', borderBottom: idx === searchResults.length - 1 ? 'none' : '1px solid rgba(255,255,255,0.05)' }}
                  onMouseOver={e => e.currentTarget.style.background = 'rgba(255,255,255,0.1)'}
                  onMouseOut={e => e.currentTarget.style.background = 'transparent'}>
                  <span style={{ fontWeight: 600 }}>{item.name}</span>
                  <span style={{ color: 'var(--text-secondary)' }}>{item.code}</span>
                </div>
              ))}
            </div>
          )}
        </div>
        {watchlist.length === 0 ? (
          <div className="glass-panel" style={{ padding: '3rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
            <StarOff size={40} style={{ margin: '0 auto 1rem', display: 'block', opacity: 0.4 }} />
            <p>등록된 관심종목이 없습니다.</p>
            <p style={{ fontSize: '0.8rem', marginTop: '0.4rem' }}>위 검색상자에 종목명을 입력해 주세요.</p>
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: '0.75rem' }}>
            {watchlist.map((item, idx) => (
              <div key={item.stock_code} className="glass-panel"
                style={{ padding: '1.2rem 1.4rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between', border: selectedStock === item.stock_code ? '1px solid var(--accent-mint)' : '1px solid var(--glass-border)', animation: `fadeIn 0.3s ease ${idx * 0.04}s both` }}>
                <div style={{ flex: 1, cursor: 'pointer' }} onClick={() => { changeStock(item.stock_code); changeTab('analysis'); }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.3rem' }}>
                    <span style={{ fontSize: '0.65rem', padding: '0.15rem 0.5rem', background: 'rgba(45,212,191,0.1)', borderRadius: '4px', color: 'var(--accent-mint)' }}>{item.stock_code}</span>
                    {selectedStock === item.stock_code && <span style={{ fontSize: '0.65rem', color: 'var(--accent-mint)' }}>● 선택중</span>}
                  </div>
                  <p style={{ fontWeight: 700, fontSize: '0.95rem' }}>{item.stock_name || item.stock_code}</p>
                </div>
                <div style={{ display: 'flex', gap: '0.4rem' }}>
                  <button onClick={() => { changeStock(item.stock_code); changeTab('analysis'); }} style={{ padding: '0.4rem 0.6rem', borderRadius: '6px', border: 'none', background: 'rgba(45,212,191,0.15)', color: 'var(--accent-mint)', cursor: 'pointer' }}><Eye size={15} /></button>
                  <button onClick={() => handleRemoveWatchlist(item.stock_code)} style={{ padding: '0.4rem 0.6rem', borderRadius: '6px', border: 'none', background: 'rgba(251,113,133,0.12)', color: 'var(--accent-red)', cursor: 'pointer' }}><Trash2 size={15} /></button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    );
  };


  // ── 시그널 보드 컴포넌트 ────────────────────────────────────
  // ── 프론트엔드 시그널 캐시 (1시간) ─────────────────────────
  const _signalFrontCache = React.useRef({});

  const SignalBoard = ({ scope, stockCode = '' }) => {
    const [signals,  setSignals]  = React.useState([]);
    const [loading,  setLoading]  = React.useState(true);
    const [expanded, setExpanded] = React.useState(false);
    const [showGuide, setShowGuide] = React.useState(false); // 로직 가이드 토글

    React.useEffect(() => {
      if (!stockCode && scope !== 'market') return;
      const cacheKey = scope === 'market' ? 'market' : stockCode;
      const cached = _signalFrontCache.current[cacheKey];
      const now = Date.now();
      // 장중 1시간 / 장외 4시간 캐시 — 백엔드와 동일
      const frontTtl = isKRMarketOpen() ? 3600000 : 14400000;
      if (cached && (now - cached.at) < frontTtl) {
        setSignals(cached.data);
        setLoading(false);
        return;
      }
      setLoading(true);
      const url = scope === 'market'
        ? API('/api/signals/market')
        : API(`/api/signals/stock/${stockCode}`);
      fetch(url)
        .then(r => r.ok ? r.json() : [])
        .then(d => {
          const sigs = Array.isArray(d) ? d : (d?.signals || []);
          _signalFrontCache.current[cacheKey] = { data: sigs, at: Date.now() };
          setSignals(sigs);
          setLoading(false);
        })
        .catch(() => setLoading(false));
    }, [scope, stockCode]);

    const C = {
      green:  { bg:'rgba(34,197,94,0.12)',  border:'rgba(34,197,94,0.4)',   text:'#22c55e', dot:'#22c55e',  light:'rgba(34,197,94,0.06)' },
      yellow: { bg:'rgba(251,191,36,0.12)', border:'rgba(251,191,36,0.4)',  text:'#fbbf24', dot:'#fbbf24',  light:'rgba(251,191,36,0.06)' },
      red:    { bg:'rgba(239,68,68,0.12)',  border:'rgba(239,68,68,0.4)',   text:'#ef4444', dot:'#ef4444',  light:'rgba(239,68,68,0.06)' },
      gray:   { bg:'rgba(255,255,255,0.04)',border:'var(--glass-border)',   text:'#64748b', dot:'#475569',  light:'rgba(255,255,255,0.02)' },
    };

    // ── 시그널별 상세 로직 설명 ──────────────────────────────────
    const SIGNAL_GUIDE = {
      // 시장 시그널
      supply_flow:    { basis:'외국인+기관 3일 누적 순매수', criteria:'양방향 동반 매수=🟢 / 동반 매도=🔴', action:'동반 매수 시 시장 상승 신뢰도 높음' },
      vix_trend:      { basis:'VIX 현재가 vs 20일 이동평균', criteria:'MA20 아래=🟢 Risk-On / MA20 위 10%+=🔴 Risk-Off', action:'절대값(30) 기준 폐기 — 추세 방향이 핵심', note:'뉴노멀 환경에서 절대 기준은 무의미' },
      usd_krw_trend:  { basis:'원/달러 현재가 vs 20일 이동평균', criteria:'MA20 아래=🟢 안정 / MA20 위=🔴 위험', action:'환율 상승 추세 = 외국인 자금 이탈 위험 신호', note:'1400원 고정 기준 대신 추세 방향 확인' },
      nasdaq_ma200:   { basis:'나스닥 현재가 vs 200일 이동평균선', criteria:'MA200 위=🟢 매매 허가 / MA200 아래=🔴 매매 금지', action:'MA200 아래일 때 주식 비중 최소화 — 폴 튜더 존스 Rule', note:'★ 기계적 매매의 제1원칙 — 200일선이 절대 필터' },
      sp500_ma200:    { basis:'S&P500 현재가 vs 200일 이동평균선', criteria:'MA200 위=🟢 / MA200 아래=🔴', action:'글로벌 위험자산 선호도 판단 기준', note:'나스닥과 함께 양방향 통과 시 더 강한 매매 신호' },
      kospi_ma200:    { basis:'KOSPI 현재가 vs 200일 이동평균선', criteria:'MA200 위=🟢 / MA200 아래=🔴', action:'미국 지수와 디커플링 대비 — 국내 독자 필터', note:'미국은 OK여도 KOSPI 아래면 국내주 매매 자제' },
      kospi_ma_align: { basis:'KOSPI MA20 > MA60 정배열 여부', criteria:'정배열=🟢 상승추세 / 역배열=🔴 하락추세', action:'단기보다 장기 추세 방향 확인용 필터', note:'MA20이 MA60 위 = 단기가 장기보다 강함' },
      adr_kospi:      { basis:'KOSPI 20일 평균 등락비율 (상승종목수/하락종목수×100)', criteria:'ADR<75=🟢 과매도 극단 / ADR>120=🔴 과매수 극단', action:'지수가 대형주로 왜곡될 때 시장 온기 실제 파악', note:'ADR 75 이하는 공황적 매도 — 역발상 매수 기회' },
      fear_greed:     { basis:'CNN Fear&Greed Index (수동 입력)', criteria:'>55=🟢 탐욕 / <30=🔴 공포', action:'극단적 공포 = 매수 기회, 극단적 탐욕 = 매도 신호', note:'수동 입력 필요 — 설정에서 업데이트' },
      // 종목 시그널
      frn_supply:     { basis:'외국인 5일 순매수 누적금액', criteria:'양수=🟢 매수 / 음수=🔴 매도', action:'Step3 진입 트리거 — 외국인 유입 시 매수 고려' },
      inst_supply:    { basis:'기관 5일 순매수 누적금액', criteria:'양수=🟢 매수 / 음수=🔴 매도', action:'Step3 진입 트리거 — 기관+외국인 동반 유입이 가장 강한 신호' },
      financials:     { basis:'분기 영업이익 흑자 + YoY 매출 성장률', criteria:'흑자+성장 5%+=🟢 / 적자=🔴', action:'Step2 종목 필터 — 재무 미통과 시 매수 보류' },
      value:          { basis:'MA60 대비 현재가 + 52주 모멘텀 AND 조건', criteria:'MA60 위+고점-20%이내=🟢 / MA60 아래=🔴', action:'가치함정 방지 — 가치만 좋고 추세 없으면 Value Trap', note:'★ 핵심: 가치지표 단독 사용 금지 — 반드시 추세와 AND 조건' },
      rs_score:       { basis:'3개월 주가상승률 - KOSPI 상승률 = RS 초과수익률', criteria:'>+5%=🟢 주도주 / <-5%=🔴 약세주', action:'Step2 종목 필터 — 시장보다 강한 주도주만 매수 대상', note:'★ 추세추종 핵심 — 하락장에서 덜 빠지거나 오르는 종목' },
      ma_align:       { basis:'현재가 > MA5 > MA20 > MA60 정배열', criteria:'완전 정배열=🟢 / 역배열=🔴', action:'Step2 종목 필터 + MACD/RSI 필터의 기준선', note:'역배열에서의 매수 신호는 신뢰도 낮음 — 무시' },
      macd_signal:    { basis:'MACD 골든크로스 + 이평선 정배열 필터', criteria:'정배열+골든크로스+0선위=🟢 강함 / 역배열+골든크로스=🟡 노이즈', action:'Step3 진입 트리거 — 정배열 상태에서만 유효 매수신호', note:'★ 박스권/역배열에서의 MACD 골든크로스는 무시' },
      rsi_signal:     { basis:'RSI + 이평선 정배열 필터', criteria:'정배열+RSI 50 돌파=🟢 / 역배열+RSI 30=🟡 추가하락 위험', action:'Step3 진입 트리거 — 정배열 상태 RSI 50 돌파만 유효', note:'강세장에서 RSI 70+ 과매수도 계속 오름 — 추세 먼저 확인' },
      trend52w:       { basis:'52주 고점 -15% 이내 + 최근 1개월 거래량 증가율', criteria:'고점근접+거래량급증=🟢 / 고점-30%+=🔴', action:'Step3 진입 트리거 — 신고가 돌파 직전 거래량 급증이 핵심', note:'윌리엄 오닐 CAN SLIM — 신고가에 가까울수록 강한 종목' },
      atr_stop:       { basis:'ATR(14일) × 2 = 기계적 손절 범위', criteria:'ATR 낮음=🟢 리스크 작음 / ATR 높음=🔴 변동성 큼', action:'Step4 청산 트리거 — 매수가 - 2×ATR 이탈 시 무조건 손절', note:'★ 감정 배제의 핵심 — 손절가 미리 계산해 기계적 실행' },
      vol_price:      { basis:'당일 거래량 vs 20일 평균 거래량 비율', criteria:'상승+2배이상=🟢 / 하락+2배이상=🔴', action:'거래량 없는 상승은 가짜 — 신뢰도 낮음' },
      short_sell:     { basis:'대차잔고비율 + 증가 추세 여부', criteria:'2%이하=🟢 / 5%이상 or 증가추세=🔴', action:'대차 급증 = 공매도 세력 유입 — 주가 하락 압력' },
      system_judgment: { basis:'추세(MA정배열+트리거) + 가치(Graham할인) + 섹터회복 3-트랙 독립 판정', criteria:'추세 OR 가치 통과=🟢 / 트리거 대기=🟡 / 모두 미충족=🔴', action:'시장이 나빠도 Graham 30%+ 할인+흑자 재무면 가치 매수 green 가능', note:'★ 가치 트랙은 시장 하락과 독립 — 시장 위험은 경고 문구만 추가(차단 안 함)' },
      // Graham 가치투자 트랙
      graham_value:    { basis:'Graham 내재가치 = sqrt(22.5 × EPS × BPS)', criteria:'30%+ 할인=🟢 강력저평가 / 15%+ 할인=🟢 저평가 / 고평가=🔴', action:'안전마진 30% 이상일 때 가치 매수 검토', note:'★ 벤저민 그레이엄 공식 — EPS+BPS 모두 양수일 때만 유효' },
      macd_divergence: { basis:'MACD 강세 다이버전스 (0선 아래)', criteria:'가격 신저점 + MACD 저점 상승=🟢 반전신호', action:'바닥 형성 감지 — 가치 종목과 결합 시 강력한 진입 신호', note:'0선 아래에서의 다이버전스가 핵심 — 추세 반전 조짐' },
      smart_money:     { basis:'최근 5거래일 중 기관+외국인 동반 순매수 일수', criteria:'3일 이상=🟢 유입 / 2일=🟡 / 1일 이하=🔴 부재', action:'저평가 구간에서 스마트머니 진입 = 반등 신호', note:'가격이 바닥권일 때만 의미 있음 — 고점에서의 수급은 별도 판단' },
      ma20_slope:      { basis:'MA20 최근 5봉 대비 현재 기울기 (%)', criteria:'기울기 -0.5%~+0.5%=🟢 완만 / 가파른 하락=🔴', action:'MA20 기울기 완만화 = 하락 모멘텀 소진 신호', note:'완전 반전 전에 먼저 기울기가 완만해지는 선행 신호' },
      value_turnaround:{ basis:'Graham + PBR/PER + MACD다이버전스 + MA20기울기 + 스마트머니 종합점수 (9점)', criteria:'6+점=🟢 강력 / 4+점=🟢 / 2+점=🟡 관심 / 미달=🔴', action:'가치 + 반전 조건 종합 점수 — 높을수록 매수 우선순위', note:'★ 가치 트랙 최종 판정 — 이 신호가 🟢(4점+)면 가치 매수 검토' },
    };

    // ── 그룹 구성 (새 시그널 포함) ──────────────────────────────
    const MARKET_GROUPS = [
      { key:'regime',  label:'🏛 Market Regime (Step1 — 시장 환경 필터)', names:['nasdaq_ma200','sp500_ma200','kospi_ma200','kospi_ma_align'] },
      { key:'risk',    label:'⚠ 위험 지표 (추세 기반)',                   names:['vix_trend','usd_krw_trend','adr_kospi'] },
      { key:'supply',  label:'📊 시장 수급',                              names:['supply_flow','fear_greed'] },
    ];

    const STOCK_GROUPS = [
      { key:'judgment', label:'🎯 종합 판정 (추세 + 가치 병렬)',                       names:['system_judgment'] },
      { key:'step2',    label:'📈 [추세] Step2 — 종목 필터',                          names:['ma_align','rs_score','financials','value'] },
      { key:'step3',    label:'🚀 [추세] Step3 — 진입 트리거',                         names:['frn_supply','inst_supply','macd_signal','rsi_signal','trend52w'] },
      { key:'step4',    label:'🛡 [추세] Step4 — 리스크 관리',                         names:['atr_stop','vol_price','short_sell'] },
      { key:'value',    label:'💎 [가치] Graham 가치투자 트랙',                         names:['graham_value','macd_divergence','smart_money','ma20_slope','value_turnaround'] },
    ];

    const GROUPS = scope === 'market' ? MARKET_GROUPS : STOCK_GROUPS;

    if (loading) return (
      <div style={{padding:'0.6rem 1rem',display:'flex',alignItems:'center',gap:'0.5rem',
        fontSize:'0.78rem',color:'var(--text-secondary)',background:'rgba(255,255,255,0.02)',
        borderRadius:'8px',border:'1px solid var(--glass-border)'}}>
        <div style={{width:'10px',height:'10px',borderRadius:'50%',border:'2px solid var(--accent-mint)',
          borderTopColor:'transparent',animation:'spin 0.8s linear infinite'}}/>
        시그널 계산 중...
      </div>
    );
    if (!signals.length) return null;

    // 4단계 판정 시그널 분리
    const judgmentSig = signals.find(s => s.name === 'system_judgment');
    const normalSigs  = signals.filter(s => s.name !== 'system_judgment');

    const active  = normalSigs.filter(s => s.signal !== 'gray');
    const greens  = active.filter(s => s.signal === 'green').length;
    const reds    = active.filter(s => s.signal === 'red').length;
    const yellows = active.filter(s => s.signal === 'yellow').length;
    const total   = active.length;  // 전체 active(green+red+yellow)

    // 종합 신호: 백엔드 system_judgment 신뢰 (3-트랙 독립 판정)
    // 가치 트랙은 추세 적신호와 무관하게 green 가능 → 프론트 강제 보정 최소화
    const rawJudgment = judgmentSig?.signal;
    let overall;
    if (total === 0) {
      overall = 'gray';
    } else {
      const redRatio   = reds / total;
      const greenRatio = greens / total;
      if (rawJudgment) {
        // 추세 Track: 적신호 70%+ (대부분 추세 시그널이 나쁨) + 가치 판정도 green이면 → yellow 보정
        // → 단, 가치 트랙 green은 추세 적신호와 독립이므로 완화된 기준 적용
        if (rawJudgment === 'green' && redRatio >= 0.6) {
          overall = 'yellow';  // 압도적 적신호 시에만 보정 (35% → 60%로 완화)
        } else {
          overall = rawJudgment;
        }
      } else {
        overall = greenRatio >= 0.6 ? 'green' : redRatio >= 0.6 ? 'red' : 'yellow';
      }
    }
    const oc = C[overall];

    // 레이블: system_judgment detail에서 핵심 문구 추출
    const judgeDetail = judgmentSig?.detail || '';
    const getStockLabel = () => {
      if (overall === 'green') {
        if (judgeDetail.includes('추세+가치')) return '추세+가치 최강 매수';
        if (judgeDetail.includes('[가치]') || judgeDetail.includes('가치]')) return '가치 분할매수 검토';
        return '추세 매수 가능';
      }
      if (overall === 'yellow') {
        if (judgeDetail.includes('시장 약세') || judgeDetail.includes('극위험')) return '가치우수 — 시장위험 소량';
        if (judgeDetail.includes('가치 우수')) return '가치우수 — 시장위험 소량';
        if (judgeDetail.includes('트리거')) return '진입 트리거 대기 중';
        if (judgeDetail.includes('가치 관심')) return '가치 관심 구간';
        return '조건 준비 중';
      }
      return '매수 보류';
    };
    const OVERALL_LABEL = {
      green:  { emoji:'🟢', label: scope==='stock' ? getStockLabel() : '시장 우호적' },
      yellow: { emoji:'🟡', label: scope==='stock' ? getStockLabel() : '시장 혼조' },
      red:    { emoji:'🔴', label: scope==='stock' ? '매수 보류' : '시장 위험' },
      gray:   { emoji:'⚪', label:'데이터 부족' },
    };
    const ovl = OVERALL_LABEL[overall];

    const renderSignalCard = (s) => {
      const c    = C[s.signal] || C.gray;
      const EMOJI= { green:'🟢', yellow:'🟡', red:'🔴', gray:'⚪' };
      const guide= SIGNAL_GUIDE[s.name];
      const isJudgment = s.name === 'system_judgment';

      return (
        <div key={s.id || s.name} style={{
          padding: isJudgment ? '0.7rem 1rem' : '0.5rem 0.75rem',
          borderRadius:'8px',
          background: isJudgment ? oc.bg : c.light,
          border:`${isJudgment?'2px':'1px'} solid ${isJudgment?oc.border:c.border}`,
        }}>
          {/* 시그널 헤더 */}
          <div style={{display:'flex',alignItems:'flex-start',justifyContent:'space-between',marginBottom:'0.25rem'}}>
            <div style={{display:'flex',alignItems:'center',gap:'0.4rem'}}>
              <span style={{fontSize:'0.8rem'}}>{EMOJI[s.signal]}</span>
              <span style={{fontSize: isJudgment?'0.85rem':'0.78rem', fontWeight:700, color:c.text}}>
                {s.label}
              </span>
            </div>
          </div>

          {/* 현재 계산값 + 상세 */}
          <p style={{fontSize:'0.7rem',color:'var(--text-primary)',lineHeight:1.5,marginBottom:'0.2rem',fontWeight:500}}>
            {s.detail || '-'}
          </p>

          {/* 로직 가이드 (showGuide 활성화 시) */}
          {showGuide && guide && (
            <div style={{marginTop:'0.4rem',paddingTop:'0.4rem',
              borderTop:'1px solid rgba(255,255,255,0.07)',
              display:'flex',flexDirection:'column',gap:'0.2rem'}}>
              <div style={{display:'flex',gap:'0.3rem',flexWrap:'wrap'}}>
                <span style={{fontSize:'0.62rem',padding:'0.1rem 0.4rem',borderRadius:'4px',
                  background:'rgba(56,189,248,0.12)',color:'#38bdf8',whiteSpace:'nowrap'}}>
                  📐 {guide.basis}
                </span>
              </div>
              <p style={{fontSize:'0.65rem',color:'rgba(255,255,255,0.55)',lineHeight:1.4}}>
                <span style={{color:'rgba(255,255,255,0.3)'}}>기준: </span>{guide.criteria}
              </p>
              <p style={{fontSize:'0.65rem',color:'rgba(255,255,255,0.55)',lineHeight:1.4}}>
                <span style={{color:'rgba(255,255,255,0.3)'}}>활용: </span>{guide.action}
              </p>
              {guide.note && (
                <p style={{fontSize:'0.63rem',color:'#f59e0b',lineHeight:1.4,
                  padding:'0.15rem 0.4rem',background:'rgba(245,158,11,0.08)',borderRadius:'4px'}}>
                  ⚡ {guide.note}
                </p>
              )}
            </div>
          )}
        </div>
      );
    };

    return (
      <div style={{borderRadius:'10px',border:`1px solid ${oc.border}`,
        background:'rgba(255,255,255,0.02)',marginBottom:'0.75rem',overflow:'hidden'}}>

        {/* 헤더 */}
        <div style={{padding:'0.6rem 1.1rem',background:oc.bg,
          display:'flex',alignItems:'center',justifyContent:'space-between',
          cursor:'pointer'}} onClick={()=>setExpanded(v=>!v)}>
          <div style={{display:'flex',alignItems:'center',gap:'0.6rem',flexWrap:'wrap'}}>
            {/* 신호등 1개 */}
            <span style={{fontSize:'1.3rem'}}>{ovl.emoji}</span>
            {/* 1줄 요약 */}
            <div style={{display:'flex',alignItems:'center',gap:'0.5rem',flexWrap:'wrap'}}>
              <span style={{fontSize:'0.88rem',fontWeight:800,color:oc.text}}>
                {ovl.label}
              </span>
              {judgmentSig && (
                <span style={{fontSize:'0.72rem',color:'rgba(255,255,255,0.5)',
                  borderLeft:'1px solid rgba(255,255,255,0.2)',paddingLeft:'0.5rem'}}>
                  {judgmentSig.detail.replace(/[✅🟡🔴]\s*/g,'').split('—')[1]?.trim() || judgmentSig.detail.split('—')[0]?.trim()}
                </span>
              )}
            </div>
            {/* 시그널 카운트: 🟢green / 🔴red / 🟡yellow / 전체 */}
            <div style={{display:'flex',gap:'0.25rem',marginLeft:'0.3rem',alignItems:'center'}}>
              <span style={{padding:'0.12rem 0.45rem',borderRadius:'20px',fontSize:'0.7rem',fontWeight:700,
                background:'rgba(34,197,94,0.2)',color:'#22c55e',border:'1px solid rgba(34,197,94,0.4)',
                display:'flex',alignItems:'center',gap:'0.2rem'}}>
                <span style={{width:'7px',height:'7px',borderRadius:'50%',background:'#22c55e',display:'inline-block'}}/>
                {greens}
              </span>
              <span style={{padding:'0.12rem 0.45rem',borderRadius:'20px',fontSize:'0.7rem',fontWeight:700,
                background:'rgba(239,68,68,0.2)',color:'#ef4444',border:'1px solid rgba(239,68,68,0.4)',
                display:'flex',alignItems:'center',gap:'0.2rem'}}>
                <span style={{width:'7px',height:'7px',borderRadius:'50%',background:'#ef4444',display:'inline-block'}}/>
                {reds}
              </span>
              {yellows > 0 && (
                <span style={{padding:'0.12rem 0.45rem',borderRadius:'20px',fontSize:'0.7rem',fontWeight:700,
                  background:'rgba(251,191,36,0.15)',color:'#fbbf24',border:'1px solid rgba(251,191,36,0.35)',
                  display:'flex',alignItems:'center',gap:'0.2rem'}}>
                  <span style={{width:'7px',height:'7px',borderRadius:'50%',background:'#fbbf24',display:'inline-block'}}/>
                  {yellows}
                </span>
              )}
              <span style={{padding:'0.12rem 0.45rem',borderRadius:'20px',fontSize:'0.68rem',
                background:'rgba(255,255,255,0.06)',color:'#94a3b8',border:'1px solid rgba(255,255,255,0.1)'}}>
                /{total}
              </span>
            </div>
          </div>
          <div style={{display:'flex',alignItems:'center',gap:'0.5rem'}}>
            {/* 로직 설명 토글 버튼 */}
            <button onClick={(e)=>{e.stopPropagation();setShowGuide(v=>!v);setExpanded(true);}}
              style={{padding:'0.2rem 0.55rem',borderRadius:'6px',fontSize:'0.68rem',fontWeight:600,
                border:`1px solid ${showGuide?'#38bdf8':'rgba(255,255,255,0.2)'}`,
                background: showGuide?'rgba(56,189,248,0.15)':'transparent',
                color: showGuide?'#38bdf8':'rgba(255,255,255,0.4)',cursor:'pointer',
                whiteSpace:'nowrap'}}>
              {showGuide ? '📖 설명 ON' : '📖 설명'}
            </button>
            <span style={{fontSize:'0.7rem',color:'rgba(255,255,255,0.4)'}}>
              {scope==='market'?'📊 시장':'🔍 종목'} {expanded?'▲':'▼'}
            </span>
          </div>
        </div>

        {/* 4단계 시스템 흐름도 (시장 시그널일 때는 숨김) */}
        {expanded && scope === 'stock' && (
          <div style={{padding:'0.6rem 1rem',background:'rgba(0,0,0,0.2)',
            borderBottom:'1px solid var(--glass-border)',
            display:'flex',flexDirection:'column',gap:'0.5rem'}}>
            {/* Track A: 추세 */}
            <div style={{display:'flex',alignItems:'center',gap:'0.3rem',overflowX:'auto',flexWrap:'wrap'}}>
              <span style={{fontSize:'0.62rem',color:'#38bdf8',fontWeight:700,minWidth:'52px',flexShrink:0}}>
                📈 추세
              </span>
              {[
                {step:'Step1',label:'마켓 필터',desc:'200일선+VIX',color:'#38bdf8'},
                {step:'→'},
                {step:'Step2',label:'MA 정배열',desc:'5>20>60 정배열',color:'#a78bfa'},
                {step:'→'},
                {step:'Step3',label:'진입 트리거',desc:'수급+MACD/RSI',color:'#22c55e'},
                {step:'→'},
                {step:'Step4',label:'ATR 리스크',desc:'2×ATR 손절선',color:'#f59e0b'},
              ].map((item, i) => item.step === '→' ? (
                <span key={i} style={{color:'rgba(255,255,255,0.3)',fontSize:'0.9rem'}}>→</span>
              ) : (
                <div key={i} style={{padding:'0.25rem 0.6rem',borderRadius:'6px',
                  background:'rgba(255,255,255,0.04)',border:`1px solid ${item.color}33`,
                  textAlign:'center',minWidth:'75px'}}>
                  <div style={{fontSize:'0.62rem',color:item.color,fontWeight:700}}>{item.step}</div>
                  <div style={{fontSize:'0.72rem',color:'var(--text-primary)',fontWeight:600}}>{item.label}</div>
                  <div style={{fontSize:'0.6rem',color:'var(--text-secondary)'}}>{item.desc}</div>
                </div>
              ))}
            </div>
            {/* Track B: 가치 (독립) */}
            <div style={{display:'flex',alignItems:'center',gap:'0.3rem',overflowX:'auto',flexWrap:'wrap'}}>
              <span style={{fontSize:'0.62rem',color:'#f59e0b',fontWeight:700,minWidth:'52px',flexShrink:0}}>
                💎 가치
              </span>
              {[
                {step:'Graham',label:'내재가치 할인',desc:'≥30% 할인 필수',color:'#f59e0b'},
                {step:'+'},
                {step:'재무',label:'영업흑자',desc:'적자 시 제외',color:'#f59e0b'},
                {step:'+'},
                {step:'반전신호',label:'MACD 다이버전스',desc:'바닥 반전 감지',color:'#f59e0b'},
                {step:'→'},
                {step:'시장위험',label:'시장 위험도',desc:'경고 추가(차단 안 함)',color:'#94a3b8'},
              ].map((item, i) => item.step === '+' || item.step === '→' ? (
                <span key={i} style={{color:'rgba(255,255,255,0.3)',fontSize:'0.9rem'}}>{item.step}</span>
              ) : (
                <div key={i} style={{padding:'0.25rem 0.6rem',borderRadius:'6px',
                  background: item.color==='#94a3b8' ? 'rgba(148,163,184,0.06)' : 'rgba(245,158,11,0.06)',
                  border:`1px solid ${item.color}33`,
                  textAlign:'center',minWidth:'75px'}}>
                  <div style={{fontSize:'0.62rem',color:item.color,fontWeight:700}}>{item.step}</div>
                  <div style={{fontSize:'0.72rem',color:'var(--text-primary)',fontWeight:600}}>{item.label}</div>
                  <div style={{fontSize:'0.6rem',color:'var(--text-secondary)'}}>{item.desc}</div>
                </div>
              ))}
            </div>
            {/* Track C: 섹터 회복 */}
            <div style={{display:'flex',alignItems:'center',gap:'0.3rem',flexWrap:'wrap'}}>
              <span style={{fontSize:'0.62rem',color:'#34d399',fontWeight:700,minWidth:'52px',flexShrink:0}}>
                🏭 섹터
              </span>
              <div style={{padding:'0.2rem 0.6rem',borderRadius:'6px',
                background:'rgba(52,211,153,0.06)',border:'1px solid rgba(52,211,153,0.2)'}}>
                <span style={{fontSize:'0.68rem',color:'#34d399'}}>
                  섹터 주도주 50%+ 52주선 상회 시 가치 신호 강화 (탑다운 보너스)
                </span>
              </div>
            </div>
            <div style={{fontSize:'0.65rem',color:'rgba(255,255,255,0.35)',paddingTop:'0.2rem',borderTop:'1px solid rgba(255,255,255,0.06)'}}>
              💡 추세·가치 트랙은 독립 판정 — 가치 매수는 MA 역배열·시장 하락과 무관하게 green 가능
            </div>
          </div>
        )}

        {/* 시그널 상세 */}
        {expanded && (
          <div style={{padding:'0.8rem 1rem'}}>
            {GROUPS.map(g => {
              const grpSigs = signals.filter(s => g.names.includes(s.name));
              if (!grpSigs.length) return null;
              return (
                <div key={g.key} style={{marginBottom:'1rem'}}>
                  <p style={{fontSize:'0.72rem',fontWeight:700,color:'var(--text-secondary)',
                    marginBottom:'0.4rem',letterSpacing:'0.04em',
                    borderLeft:'3px solid rgba(255,255,255,0.2)',paddingLeft:'0.5rem'}}>
                    {g.label}
                  </p>
                  <div style={{display:'grid',
                    gridTemplateColumns: g.key==='judgment' ? '1fr' : 'repeat(auto-fill,minmax(220px,1fr))',
                    gap:'0.4rem'}}>
                    {grpSigs.map(renderSignalCard)}
                  </div>
                </div>
              );
            })}

            {/* 설명 꺼져있을 때 안내 */}
            {!showGuide && (
              <div style={{marginTop:'0.5rem',padding:'0.4rem 0.7rem',borderRadius:'6px',
                background:'rgba(56,189,248,0.06)',border:'1px solid rgba(56,189,248,0.15)',
                display:'flex',alignItems:'center',gap:'0.4rem'}}>
                <span style={{fontSize:'0.68rem',color:'rgba(56,189,248,0.7)'}}>
                  📖 각 시그널의 계산 기준과 활용법을 보려면 우측 상단 <b>설명</b> 버튼을 클릭하세요
                </span>
              </div>
            )}
          </div>
        )}
      </div>
    );
  };

    // ── 매크로 대시보드 ──────────────────────────────────────────
  const MacroDashboard = React.memo(() => {
    const [lastUpdated, setLastUpdated] = React.useState(() => new Date().toLocaleTimeString('ko-KR'));
    const [kospiTab,   setKospiTab]   = React.useState('90');
    const [kosdaqTab,  setKosdaqTab]  = React.useState('90');
    const [nasdaqTab,  setNasdaqTab]  = React.useState('90');
    const [sp500Tab,   setSp500Tab]   = React.useState('90');
    React.useEffect(() => {
      const iv = setInterval(() => setLastUpdated(new Date().toLocaleTimeString('ko-KR')), 300000);
      return () => clearInterval(iv);
    }, []);

    // 구버전({KOSPI:...}) / 신버전({index:{KOSPI:...}}) 정규화
    const norm = (data) => {
      if (!data) return { idx:{}, vix:{}, comm:{} };
      if (data.index && typeof data.index === 'object')
        return { idx: data.index||{}, vix: data.vix||{}, comm: data.commodities||{} };
      return {
        idx:  { KOSPI: data.KOSPI||{}, KOSDAQ: data.KOSDAQ||{} },
        vix:  data.VIX || {},
        comm: { 'USD/KRW': data['USD/KRW']||{}, GOLD: data.GOLD||{}, OIL: data.OIL||{} },
      };
    };
    const { idx, vix, comm } = norm(macroData);
    const hasData = !!(idx.KOSPI?.value || idx.KOSDAQ?.value || comm['USD/KRW']?.value);

    // ── 포맷 헬퍼 ──
    const fv = (v, dec=0) => (v == null ? '-' : Number(v).toLocaleString('ko-KR', {maximumFractionDigits: dec}));
    // fq: 지수 수급 — 항상 억원 단위 고정, 천단위 콤마, 소수점 없음
    const fq = (v) => {
      if (v == null) return '-';
      const n = Number(v);
      if (n === 0) return '-';
      const sign = n > 0 ? '+' : '-';
      return sign + Math.round(Math.abs(n)).toLocaleString('ko-KR') + '억';
    };
    const pc  = (v) => v > 0 ? '#ef4444' : v < 0 ? '#3b82f6' : 'rgba(255,255,255,0.35)';
    const arr = (v) => v >= 0 ? '▲' : '▼';

    // VIX 단계 색상
    const vixColor = (v) =>
      !v     ? '#34d399' :
      v >= 40 ? '#ef4444' :
      v >= 20 ? '#fb923c' : '#34d399';
    const vixLabel = (v) =>
      !v     ? '-' :
      v >= 40 ? '극심한 공포' :
      v >= 20 ? '주의단계' : '안정적';

    // VIX 전용 차트 컴포넌트 (IIFE 대신 컴포넌트로 분리)
    const VixChart = ({ data, color }) => {
      if (!data || data.length < 2) return (
        <div style={{ height:'100%', display:'flex', alignItems:'center', justifyContent:'center', color:'var(--text-secondary)', fontSize:'0.75rem' }}>
          VIX 히스토리 수집 중...
        </div>
      );
      const vvals  = data.map(d => d.close).filter(v => v != null);
      const vMin   = Math.min(...vvals);
      const vMax   = Math.max(...vvals);
      const vPad   = (vMax - vMin) * 0.08 || 1;
      const vTicks = [0, Math.floor(data.length / 2), data.length - 1]
                     .map(i => data[i]?.date).filter(Boolean);
      return (
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top:4, right:24, left:2, bottom:0 }}>
            <defs>
              <linearGradient id="vixG" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor={color} stopOpacity={0.35} />
                <stop offset="95%" stopColor={color} stopOpacity={0}    />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
            <XAxis dataKey="date" ticks={vTicks}
              tick={{ fontSize:9, fill:'rgba(255,255,255,0.3)' }}
              tickFormatter={v => v?.slice(5)} axisLine={false} tickLine={false} />
            <YAxis domain={[vMin - vPad, vMax + vPad]}
              tick={{ fontSize:9, fill:'rgba(255,255,255,0.3)' }}
              tickFormatter={v => v.toFixed(1)} axisLine={false} tickLine={false}
              width={28} tickCount={4} />
            <Tooltip
              contentStyle={{ background:'rgba(15,15,25,0.95)', border:'1px solid rgba(255,255,255,0.1)', borderRadius:'6px', fontSize:'0.72rem' }}
              formatter={v => [Number(v).toFixed(1), 'VIX']}
              labelFormatter={v => v?.slice(5)}
            />
            <ReferenceLine y={20} stroke="rgba(251,146,60,0.5)" strokeDasharray="4 2" />
            <ReferenceLine y={40} stroke="rgba(239,68,68,0.5)"  strokeDasharray="4 2" />
            <Area type="monotone" dataKey="close" stroke={color} strokeWidth={1.5} fill="url(#vixG)" dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      );
    };

    // 미니 스파크라인 (X/Y축 포함, 최솟값-최댓값 기반 도메인)
    const MiniChart = ({ data, color, height = 70, dec = 2 }) => {
      if (!data || data.length < 2) return (
        <div style={{ height, display:'flex', alignItems:'center', justifyContent:'center', color:'rgba(255,255,255,0.2)', fontSize:'0.7rem' }}>
          히스토리 수집 중
        </div>
      );
      const vals = data.map(d => d.close).filter(v => v != null && !isNaN(v));
      const minV = Math.min(...vals);
      const maxV = Math.max(...vals);
      const pad  = (maxV - minV) * 0.08 || maxV * 0.01;
      const domMin = minV - pad;
      const domMax = maxV + pad;

      // X축: 처음·중간·마지막 날짜만 표시
      const tickIdxs = [0, Math.floor(data.length / 2), data.length - 1];
      const xTicks   = tickIdxs.map(i => data[i]?.date).filter(Boolean);

      const fmtDate = (d) => d ? d.slice(5) : ''; // "MM-DD"

      return (
        <ResponsiveContainer width="100%" height={height}>
          <AreaChart data={data} margin={{ top: 4, right: 8, left: 2, bottom: 0 }}>
            <defs>
              <linearGradient id={`grad_${color.replace('#','')}`} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%"  stopColor={color} stopOpacity={0.3} />
                <stop offset="95%" stopColor={color} stopOpacity={0}   />
              </linearGradient>
            </defs>
            <XAxis
              dataKey="date"
              ticks={xTicks}
              tickFormatter={fmtDate}
              tick={{ fontSize: 9, fill: 'rgba(255,255,255,0.35)' }}
              axisLine={false}
              tickLine={false}
              interval="preserveStartEnd"
            />
            <YAxis
              domain={[domMin, domMax]}
              tick={{ fontSize: 9, fill: 'rgba(255,255,255,0.35)' }}
              tickFormatter={v => Number(v).toLocaleString('ko-KR', { maximumFractionDigits: dec })}
              axisLine={false}
              tickLine={false}
              width={40}
              tickCount={3}
            />
            <Tooltip
              contentStyle={{ background:'rgba(15,15,25,0.95)', border:'1px solid rgba(255,255,255,0.1)', borderRadius:'6px', fontSize:'0.72rem' }}
              formatter={(v) => [Number(v).toLocaleString('ko-KR', { maximumFractionDigits: dec }), '']}
              labelFormatter={fmtDate}
            />
            <Area type="monotone" dataKey="close" stroke={color} strokeWidth={1.5}
              fill={`url(#grad_${color.replace('#','')})`} dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      );
    };

    if (!hasData) return (
      <div className="fade-in glass-panel" style={{ padding:'3rem', textAlign:'center', color:'var(--text-secondary)' }}>
        <div style={{ display:'flex', alignItems:'center', justifyContent:'center', gap:'0.6rem', marginBottom:'0.8rem' }}>
          <div style={{ width:'14px', height:'14px', borderRadius:'50%', border:'2px solid var(--accent-mint)', borderTopColor:'transparent', animation:'spin 0.8s linear infinite' }} />
          <p style={{ fontWeight:600, color:'var(--accent-mint)' }}>매크로 데이터 조회 중...</p>
        </div>
        <p style={{ fontSize:'0.78rem' }}>
          {macroData ? '데이터를 파싱하는 중입니다.' : 'data_collector.py 실행 후 300초 이내에 자동으로 채워집니다.'}
        </p>
        <button onClick={fetchMacro} style={{ marginTop:'1rem', padding:'0.4rem 1rem', borderRadius:'6px', border:'1px solid var(--accent-mint)', background:'transparent', color:'var(--accent-mint)', cursor:'pointer', fontSize:'0.8rem' }}>
          새로고침
        </button>
      </div>
    );

    const card  = { padding:'1.2rem 1.4rem' };
    const lbl   = { fontSize:'0.7rem', color:'var(--text-secondary)', fontWeight:600, letterSpacing:'0.05em', marginBottom:'0.4rem' };
    const big   = { fontSize:'1.5rem', fontWeight:700, lineHeight:1.2 };

    return (
    <div className="fade-in" style={{ display:'flex', flexDirection:'column', gap:'1.1rem' }}>

      {/* 시장 시그널 보드 */}
      <SignalBoard scope="market" />

      {/* 갱신 상태 */}
      <div style={{ display:'flex', alignItems:'center', gap:'0.5rem', fontSize:'0.72rem', color:'var(--text-secondary)' }}>
        <span style={{ width:'6px', height:'6px', borderRadius:'50%', background:'var(--accent-mint)', flexShrink:0 }} />
        300초 자동 갱신 (KIS 실시간) — 마지막: {lastUpdated}
        <button onClick={fetchMacro} style={{ marginLeft:'auto', padding:'0.15rem 0.6rem', borderRadius:'4px', border:'1px solid rgba(255,255,255,0.15)', background:'transparent', color:'rgba(255,255,255,0.4)', cursor:'pointer', fontSize:'0.7rem' }}>새로고침</button>
      </div>

      {/* ══ PARA 1: KOSPI / KOSDAQ ══ */}
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'1rem' }}>
        {[['KOSPI','#34d399',kospiTab,setKospiTab],['KOSDAQ','#60a5fa',kosdaqTab,setKosdaqTab]].map(([name,idxColor,idxTab,setIdxTab]) => {
          const d = idx[name] || {};
          const noSupply = !d.frn_net_buy && !d.inst_net_buy;
          const histData = idxTab==='90'?(d.history_90||[]):idxTab==='365'?(d.history_365||[]):(d.history_1095||[]);
          // 탭별 기간 수익률 계산
          const calcPeriodReturn = (hist) => {
            if (!hist || hist.length < 2) return null;
            const first = hist[0]?.close; const last = hist[hist.length-1]?.close;
            if (!first || !last || first === 0) return null;
            return ((last - first) / first * 100);
          };
          const ret90   = calcPeriodReturn(d.history_90   || []);
          const ret365  = calcPeriodReturn(d.history_365  || []);
          const ret1095 = calcPeriodReturn(d.history_1095 || []);
          const retMap  = {'90': ret90, '365': ret365, '1095': ret1095};
          const currRet = retMap[idxTab];
          return (
            <div key={name} className="glass-panel" style={{ padding:'1.2rem 1.4rem' }}>
              <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'1rem', marginBottom:'0.9rem' }}>
                <div>
                  <p style={lbl}>{name}</p>
                  <div style={{ display:'flex', alignItems:'baseline', gap:'0.5rem', flexWrap:'wrap' }}>
                    <span style={big}>{fv(d.value)}</span>
                    {d.value != null && d.change != null && (() => {
                      const diff = d.value - d.value / (1 + (d.change||0) / 100);
                      const clr = pc(d.change);
                      return (
                        <span style={{ display:'flex', alignItems:'baseline', gap:'0.3rem' }}>
                          <span style={{ fontSize:'0.9rem', fontWeight:700, color:clr }}>
                            {arr(d.change||0)} {diff >= 0 ? '+' : '-'}{Math.round(Math.abs(diff)).toLocaleString('ko-KR')}
                          </span>
                          <span style={{ fontSize:'0.8rem', fontWeight:600, color:clr }}>
                            ({Math.abs(d.change||0).toFixed(1)}%)
                          </span>
                        </span>
                      );
                    })()}
                  </div>
                  <p style={{ fontSize:'0.67rem', color:'var(--text-secondary)', marginTop:'0.2rem' }}>{d.date||'-'}</p>
                </div>
                <div style={{ borderLeft:'1px solid var(--glass-border)', paddingLeft:'1rem' }}>
                  <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:'0.3rem' }}>
                    <span style={{ fontSize:'0.67rem', color:'var(--text-secondary)', fontWeight:600 }}>당일 수급</span>
                    {noSupply && <span style={{ fontSize:'0.6rem', color:'rgba(255,255,255,0.25)' }}>수집 중</span>}
                  </div>
                  {[{label:'외국인',val:d.frn_net_buy},{label:'기관',val:d.inst_net_buy},{label:'개인',val:d.ind_net_buy}].map(({label,val})=>(
                    <div key={label} style={{ display:'flex', justifyContent:'space-between', padding:'0.12rem 0' }}>
                      <span style={{ fontSize:'0.72rem', color:'var(--text-secondary)' }}>{label}</span>
                      <span style={{ fontSize:'0.78rem', fontWeight:700, color:val>0?'#ef4444':val<0?'#3b82f6':'rgba(255,255,255,0.3)' }}>{fq(val)}</span>
                    </div>
                  ))}
                </div>
              </div>
              <div style={{ borderTop:'1px solid var(--glass-border)', paddingTop:'0.7rem' }}>
                <div style={{ display:'flex', alignItems:'center', gap:'0.4rem', marginBottom:'0.5rem', flexWrap:'wrap' }}>
                  {[['90','3개월'],['365','1년'],['1095','3년']].map(([val,label])=>(
                    <button key={val} onClick={()=>setIdxTab(val)} style={{
                      padding:'0.15rem 0.55rem', borderRadius:'4px', fontSize:'0.68rem', cursor:'pointer',
                      fontWeight:idxTab===val?700:400,
                      border:idxTab===val?`1px solid ${idxColor}`:'1px solid rgba(255,255,255,0.12)',
                      background:idxTab===val?`${idxColor}22`:'transparent',
                      color:idxTab===val?idxColor:'rgba(255,255,255,0.4)',
                    }}>{label}</button>
                  ))}
                  {currRet != null && (
                    <span style={{
                      fontSize:'0.78rem', fontWeight:700,
                      color: currRet >= 0 ? '#ef4444' : '#3b82f6',
                      marginLeft:'0.2rem',
                    }}>
                      {currRet >= 0 ? '▲' : '▼'} {Math.abs(currRet).toFixed(1)}%
                    </span>
                  )}
                </div>
                <MiniChart data={histData} color={idxColor} height={90} dec={2} />
              </div>
            </div>
          );
        })}
      </div>

      {/* ══ PARA 1-b: 나스닥 / S&P500 ══ */}
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:'1rem' }}>
        {[['NASDAQ','#a78bfa',nasdaqTab,setNasdaqTab],['S&P500','#fb923c',sp500Tab,setSp500Tab]].map(([name,idxColor,idxTab,setIdxTab]) => {
          const d = idx[name] || {};
          const histData = idxTab==='90'?(d.history_90||[]):idxTab==='365'?(d.history_365||[]):(d.history_1095||[]);
          const calcPeriodReturn = (hist) => {
            if (!hist || hist.length < 2) return null;
            const first = hist[0]?.close; const last = hist[hist.length-1]?.close;
            if (!first || !last || first === 0) return null;
            return ((last - first) / first * 100);
          };
          const retMap = {'90': calcPeriodReturn(d.history_90||[]), '365': calcPeriodReturn(d.history_365||[]), '1095': calcPeriodReturn(d.history_1095||[])};
          const currRet = retMap[idxTab];
          return (
            <div key={name} className="glass-panel" style={{ padding:'1.2rem 1.4rem' }}>
              <div style={{ marginBottom:'0.9rem' }}>
                <p style={lbl}>{name}</p>
                <div style={{ display:'flex', alignItems:'baseline', gap:'0.5rem', flexWrap:'wrap' }}>
                  <span style={big}>{fv(d.value, 2)}</span>
                  {d.value != null && d.change != null && (() => {
                    const diff = d.value - d.value / (1 + (d.change||0) / 100);
                    const clr = pc(d.change);
                    return (
                      <span style={{ display:'flex', alignItems:'baseline', gap:'0.3rem' }}>
                        <span style={{ fontSize:'0.9rem', fontWeight:700, color:clr }}>
                          {arr(d.change||0)} {diff >= 0 ? '+' : '-'}{Math.abs(diff).toFixed(1)}
                        </span>
                        <span style={{ fontSize:'0.8rem', fontWeight:600, color:clr }}>
                          ({Math.abs(d.change||0).toFixed(1)}%)
                        </span>
                      </span>
                    );
                  })()}
                </div>
                <p style={{ fontSize:'0.67rem', color:'var(--text-secondary)', marginTop:'0.2rem' }}>{d.date||'-'}</p>
              </div>
              <div style={{ borderTop:'1px solid var(--glass-border)', paddingTop:'0.7rem' }}>
                <div style={{ display:'flex', alignItems:'center', gap:'0.4rem', marginBottom:'0.5rem', flexWrap:'wrap' }}>
                  {[['90','3개월'],['365','1년'],['1095','3년']].map(([val,label])=>(
                    <button key={val} onClick={()=>setIdxTab(val)} style={{
                      padding:'0.15rem 0.55rem', borderRadius:'4px', fontSize:'0.68rem', cursor:'pointer',
                      fontWeight:idxTab===val?700:400,
                      border:idxTab===val?`1px solid ${idxColor}`:'1px solid rgba(255,255,255,0.12)',
                      background:idxTab===val?`${idxColor}22`:'transparent',
                      color:idxTab===val?idxColor:'rgba(255,255,255,0.4)',
                    }}>{label}</button>
                  ))}
                  {currRet != null && (
                    <span style={{
                      fontSize:'0.78rem', fontWeight:700,
                      color: currRet >= 0 ? '#ef4444' : '#3b82f6',
                      marginLeft:'0.2rem',
                    }}>
                      {currRet >= 0 ? '▲' : '▼'} {Math.abs(currRet).toFixed(1)}%
                    </span>
                  )}
                </div>
                <MiniChart data={histData} color={idxColor} height={90} dec={2} />
              </div>
            </div>
          );
        })}
      </div>

      {/* ══ PARA 2: VIX + 30일 그래프 ══ */}
      <div className="glass-panel" style={card}>
        <p style={lbl}>VIX — 공포지수 (CBOE Volatility Index)</p>
        <div style={{ display:'grid', gridTemplateColumns:'180px 1fr', gap:'1.5rem', alignItems:'flex-start' }}>
          {/* 좌: 수치 + 배지 */}
          <div>
            <div style={{ display:'flex', alignItems:'baseline', gap:'0.5rem' }}>
              <span style={{ ...big, color: vixColor(vix.value) }}>{vix.value ?? '-'}</span>
              {vix.change != null && (
                <span style={{ fontSize:'0.82rem', fontWeight:600, color: pc(vix.change) }}>
                  {arr(vix.change)} {Math.abs(vix.change).toFixed(1)}%
                </span>
              )}
            </div>
            <p style={{ fontSize:'0.67rem', color:'var(--text-secondary)', marginTop:'0.2rem' }}>{vix.date||'-'}</p>
            <div style={{
              display:'inline-block', marginTop:'0.7rem',
              padding:'0.2rem 0.8rem', borderRadius:'20px',
              fontSize:'0.72rem', fontWeight:700,
              background: vix.value >= 40 ? 'rgba(239,68,68,0.15)'  : vix.value >= 20 ? 'rgba(251,146,60,0.15)' : 'rgba(52,211,153,0.15)',
              color:       vix.value >= 40 ? '#ef4444'               : vix.value >= 20 ? '#fb923c'              : '#34d399',
              border: `1px solid ${vix.value>=40?'rgba(239,68,68,0.35)':vix.value>=20?'rgba(251,146,60,0.35)':'rgba(52,211,153,0.35)'}`,
            }}>
              {vixLabel(vix.value)}
            </div>
            {/* 임계선 범례 */}
            <div style={{ marginTop:'0.7rem', display:'flex', flexDirection:'column', gap:'0.2rem' }}>
              {[['#34d399','< 20','안정적'],['#fb923c','20~40','주의단계'],['#ef4444','≥ 40','극심한 공포']].map(([color,range,desc])=>(
                <div key={range} style={{ display:'flex', alignItems:'center', gap:'0.35rem', fontSize:'0.67rem', color:'var(--text-secondary)' }}>
                  <span style={{ width:'8px', height:'8px', borderRadius:'50%', background:color, flexShrink:0 }} />
                  <span style={{ color }}>{range}</span>
                  <span>{desc}</span>
                </div>
              ))}
            </div>
          </div>
          {/* 우: 30일 차트 */}
          <div>
            <p style={{ fontSize:'0.67rem', color:'var(--text-secondary)', marginBottom:'0.3rem' }}>30일 추이</p>
            <div style={{ height:'130px' }}>
              <VixChart data={vix.history||[]} color={vixColor(vix.value)} />
            </div>
          </div>
        </div>
      </div>

      {/* ══ PARA 3: 원달러·금·유가 + 미니차트 ══ */}
      <div style={{ display:'grid', gridTemplateColumns:'repeat(3, 1fr)', gap:'1rem' }}>
        {[
          { key:'USD/KRW', label:'원/달러 환율', unit:'원',      color:'#60a5fa', dec:2 },
          { key:'GOLD',    label:'금 (XAU/USD)', unit:'USD/oz',  color:'#fbbf24', dec:1 },
          { key:'OIL',     label:'WTI 원유',     unit:'USD/bbl', color:'#f97316', dec:2 },
        ].map(({ key, label, unit, color, dec }) => {
          const d = comm[key] || {};
          const [commTab, setCommTab] = React.useState('90');
          const commHistory = d.history || [];
          // 탭별 기간 수익률
          const calcCommReturn = (days) => {
            if (commHistory.length < 2) return null;
            const sorted = [...commHistory].sort((a,b) => a.date > b.date ? 1 : -1);
            const cutoff = new Date(); cutoff.setDate(cutoff.getDate() - days);
            const filtered = sorted.filter(r => new Date(r.date) >= cutoff);
            if (filtered.length < 2) return null;
            const first = filtered[0]?.close; const last = filtered[filtered.length-1]?.close;
            if (!first || first === 0) return null;
            return ((last - first) / first * 100);
          };
          const commRetMap = {'90': calcCommReturn(90), '365': calcCommReturn(365), '1095': calcCommReturn(1095)};
          const commCurrRet = commRetMap[commTab];
          const commHistFiltered = (() => {
            if (!commHistory.length) return [];
            const sorted = [...commHistory].sort((a,b) => a.date > b.date ? 1 : -1);
            const days = commTab==='90'?90:commTab==='365'?365:1095;
            const cutoff = new Date(); cutoff.setDate(cutoff.getDate() - days);
            return sorted.filter(r => new Date(r.date) >= cutoff);
          })();
          return (
            <div key={key} className="glass-panel" style={card}>
              <p style={lbl}>{label}</p>
              <div style={{ display:'flex', alignItems:'baseline', gap:'0.4rem' }}>
                <span style={{ fontSize:'1.3rem', fontWeight:700 }}>{fv(d.value, dec)}</span>
                <span style={{ fontSize:'0.68rem', color:'var(--text-secondary)' }}>{unit}</span>
              </div>
              <div style={{ display:'flex', alignItems:'center', gap:'0.4rem', marginTop:'0.25rem' }}>
                <span style={{ fontSize:'0.8rem', fontWeight:600, color: pc(d.change) }}>
                  {arr(d.change||0)} {Math.abs(d.change||0).toFixed(1)}%
                </span>
              </div>
              <p style={{ fontSize:'0.65rem', color:'var(--text-secondary)', marginTop:'0.15rem', marginBottom:'0.4rem' }}>{d.date||'-'}</p>
              {/* 탭 버튼 + 기간 수익률 */}
              <div style={{ display:'flex', alignItems:'center', gap:'0.3rem', marginBottom:'0.4rem', flexWrap:'wrap' }}>
                {[['90','3개월'],['365','1년'],['1095','3년']].map(([val,lbl2])=>(
                  <button key={val} onClick={()=>setCommTab(val)} style={{
                    padding:'0.1rem 0.45rem', borderRadius:'4px', fontSize:'0.65rem', cursor:'pointer',
                    fontWeight:commTab===val?700:400,
                    border:commTab===val?`1px solid ${color}`:'1px solid rgba(255,255,255,0.12)',
                    background:commTab===val?`${color}22`:'transparent',
                    color:commTab===val?color:'rgba(255,255,255,0.4)',
                  }}>{lbl2}</button>
                ))}
                {commCurrRet != null && (
                  <span style={{
                    fontSize:'0.75rem', fontWeight:700,
                    color: commCurrRet >= 0 ? '#ef4444' : '#3b82f6',
                    marginLeft:'0.1rem',
                  }}>
                    {commCurrRet >= 0 ? '▲' : '▼'} {Math.abs(commCurrRet).toFixed(1)}%
                  </span>
                )}
              </div>
              <MiniChart data={commHistFiltered.length ? commHistFiltered : commHistory} color={color} height={70} dec={dec} />
            </div>
          );
        })}
      </div>

    </div>
    );
  });

  // ── 개별 종목 분석 ───────────────────────────────────────────
  const StockAnalysis = () => {
    const displayChartData = React.useMemo(() => {
      if (!chartData.length) return [];
      return chartData.slice(-chartDays);
    }, [chartData, chartDays]);
    const isMobile = useIsMobile();
    // 종목별 보고서
    const [stockReports, setStockReports] = React.useState([]);
    React.useEffect(() => {
      if (!selectedStock) return;
      setStockReports([]);
      fetch(API(`/api/reports/stock/${selectedStock}`))
        .then(r => r.ok ? r.json() : [])
        .then(d => setStockReports(d || []))
        .catch(() => {});
    }, [selectedStock]);

    // ── DART 공시 조회 (5분 폴링 / 장일 08:00~20:00 KST) ────────────
    const [disclosures, setDisclosures] = React.useState([]);
    const [disclosureLoading, setDisclosureLoading] = React.useState(false);
    const [showAllDisclosures, setShowAllDisclosures] = React.useState(false);
    const DISCLOSURE_PREVIEW = 5; // 기본 표시 건수

    const fetchDisclosures = React.useCallback(async () => {
      if (!selectedStock) return;
      // 국내 종목만 (6자리 숫자)
      if (!/^\d{6}$/.test(selectedStock)) return;
      try {
        setDisclosureLoading(true);
        const res = await fetch(API(`/api/dashboard/disclosures/${selectedStock}`));
        if (res.ok) setDisclosures(await res.json());
      } catch {}
      finally { setDisclosureLoading(false); }
    }, [selectedStock]);

    React.useEffect(() => {
      if (!selectedStock) return;
      setDisclosures([]);
      setShowAllDisclosures(false);
      fetchDisclosures();
      // 공시 가능 시간(평일 08:00~20:00)에만 5분 폴링
      if (!isDisclosureTime()) return;
      const iv = setInterval(fetchDisclosures, 300000);
      return () => clearInterval(iv);
    }, [selectedStock, fetchDisclosures]);

    const numColor = (v) => (v != null && Number(v) < 0) ? 'var(--accent-red)' : 'inherit';

    const tableRows = [
      { label:'매출액',    key:'revenue',     fmt:fmtUkWon },
      { label:'영업이익',  key:'op_profit',   fmt:fmtUkWon },
      { label:'순이익',    key:'net_income',  fmt:fmtUkWon },
      { label:'영업이익률', key:'opm',        fmt:v => v!=null ? Number(v).toFixed(1)+'%' : '-' },
      { label:'자산',      key:'assets',      fmt:fmtUkWon },
      { label:'부채',      key:'liabilities', fmt:fmtUkWon },
      { label:'자본',      key:'equity',      fmt:fmtUkWon },
      { label:'자본금',    key:'capital',     fmt:fmtUkWon },
      { label:'EPS(원)',   key:'eps',         fmt:fmtNum },
    ];

    // watchlist → selectedStockName (analyze 응답) → 종목코드 순으로 fallback
    const stockName = watchlist.find(i => i.stock_code === selectedStock)?.stock_name
                   || selectedStockName
                   || selectedStock;
    const latestClose = chartData.length > 0 ? chartData[chartData.length-1].close : null;

    return (
      <div className="fade-in" style={{ display:'flex', flexDirection:'column', gap:'1rem' }}>

        {/* 수집 중 배너 */}
        {collecting && (
          <div style={{ padding:'0.75rem 1.2rem', background:'rgba(45,212,191,0.09)', border:'1px solid rgba(45,212,191,0.3)', borderRadius:'8px', display:'flex', alignItems:'center', gap:'0.75rem' }}>
            <div style={{ width:'14px', height:'14px', borderRadius:'50%', border:'2px solid var(--accent-mint)', borderTopColor:'transparent', animation:'spin 0.8s linear infinite', flexShrink:0 }}/>
            <div style={{ fontSize:'0.83rem' }}>
              <span style={{ fontWeight:700, color:'var(--accent-mint)' }}>📡 실시간 데이터 수집 중</span>
              <span style={{ marginLeft:'0.5rem', color:'rgba(45,212,191,0.75)' }}>
                — Yahoo Finance · KIS · DART에서 주가 1년치 및 재무데이터를 수집 중입니다. 10초마다 자동 업데이트 (최대 4분).
              </span>
            </div>
          </div>
        )}
        {/* 재무 없음 경고 (수집 완료 후에도 재무 없는 경우) */}
        {!collecting && summStats !== null && summStats.revenue === null && chartData.length > 0 && (
          <div style={{ padding:'0.65rem 1.2rem', background:'rgba(251,191,36,0.08)', border:'1px solid rgba(251,191,36,0.25)', borderRadius:'8px', fontSize:'0.82rem', color:'#fbbf24', display:'flex', alignItems:'center', gap:'0.6rem' }}>
            <span style={{ fontWeight:700 }}>⚠</span>
            <span>재무제표 없음 — DART 미등록 종목이거나 아직 공시 전입니다. 매일 자정 공시 기준으로 자동 업데이트됩니다.</span>
          </div>
        )}

        {/* 헤더 */}
        <header className="glass-panel" style={{ display:'flex', justifyContent:'space-between', alignItems:'center', padding:'1rem 1.5rem' }}>
          <div style={{ display:'flex', alignItems:'center', gap:'1rem', flexWrap:'wrap' }}>
            {/* 종목명 + 시장정보 블록 */}
            <div>
              <div style={{ display:'flex', alignItems:'baseline', gap:'0.6rem' }}>
                <h2 style={{ fontSize:'1.3rem' }}>{stockName} <span style={{ fontSize:'0.9rem', color:'var(--text-secondary)' }}>({selectedStock})</span></h2>
              </div>
              {/* 종목명 아래: 시장구분·시총·순위 */}
              <div style={{ display:'flex', alignItems:'center', gap:'0.6rem', marginTop:'0.25rem', flexWrap:'wrap' }}>
                {marketInfo.market && (
                  <span style={{ fontSize:'0.7rem', padding:'0.1rem 0.55rem', borderRadius:'20px', fontWeight:700,
                    background: marketInfo.market === 'KOSPI' ? 'rgba(45,212,191,0.15)' : 'rgba(167,139,250,0.15)',
                    color:      marketInfo.market === 'KOSPI' ? 'var(--accent-mint)'    : 'var(--accent-purple)',
                    border:     marketInfo.market === 'KOSPI' ? '1px solid rgba(45,212,191,0.3)' : '1px solid rgba(167,139,250,0.3)',
                  }}>{marketInfo.market}</span>
                )}
                {marketInfo.mktcap && (
                  <span style={{ fontSize:'0.75rem', color:'var(--text-secondary)' }}>
                    시총 <span style={{ color:'var(--text-primary)', fontWeight:600 }}>
                      {fmtUkWon(marketInfo.mktcap)}
                    </span>
                  </span>
                )}
                {marketInfo.mktcap_rank && (
                  <span style={{ fontSize:'0.75rem', color:'var(--text-secondary)' }}>
                    시총순위 <span style={{ color:'var(--text-primary)', fontWeight:600 }}>{marketInfo.mktcap_rank}위</span>
                  </span>
                )}
              </div>
            </div>
            {/* 현재가 */}
            {latestClose && (
              <span style={{ fontSize:'1.6rem', fontWeight:700, color:'var(--accent-mint)' }}>
                {latestClose.toLocaleString('ko-KR')}원
              </span>
            )}
            {/* 당일 변동률 + 변동금액 + 유통주식수 */}
            {chartData.length >= 1 && (() => {
              const last = chartData[chartData.length-1];
              let chg = last?.change_rate ?? null;
              let vs  = last?.vs ?? null;
              if (chg == null && chartData.length >= 2) {
                const prev = chartData[chartData.length-2]?.close;
                const curr = last?.close;
                if (prev && curr && prev !== 0) {
                  chg = (curr - prev) / prev * 100;
                  vs  = curr - prev;
                }
              }
              if (chg == null) return null;
              const clr = chg > 0 ? '#ef4444' : chg < 0 ? '#3b82f6' : 'rgba(255,255,255,0.4)';

              // 유통주식수 포맷 (주 단위)
              const fmtShares = (v) => {
                if (!v) return null;
                if (v >= 1e8)  return (v/1e8).toFixed(1)+'억주';
                if (v >= 1e4)  return Math.round(v/1e4).toLocaleString('ko-KR')+'만주';
                return Math.round(v).toLocaleString('ko-KR')+'주';
              };
              const floatStr  = fmtShares(summStats?.float_shares);
              const totalStr  = fmtShares(summStats?.shares_outstanding);
              const floatRatio = (summStats?.float_shares && summStats?.shares_outstanding)
                ? ((summStats.float_shares / summStats.shares_outstanding) * 100).toFixed(1)
                : null;

              return (
                <div style={{ display:'flex', flexDirection:'column', alignItems:'flex-start', gap:'0.1rem' }}>
                  <span style={{ fontSize:'1rem', fontWeight:700, color: clr }}>
                    {chg > 0 ? '▲' : chg < 0 ? '▼' : '▶'} {Math.abs(chg).toFixed(1)}%
                  </span>
                  {vs != null && vs !== 0 && (
                    <span style={{ fontSize:'0.75rem', color: clr, opacity:0.8 }}>
                      ({vs > 0 ? '+' : ''}{Math.round(vs).toLocaleString('ko-KR')}원)
                    </span>
                  )}
                  {/* 유통주식수 배지 */}
                  {floatStr && (
                    <div title={`총발행주식 ${totalStr||'-'} 중 유통주식 ${floatStr} (대주주·임원 제외)`}
                      style={{ marginTop:'0.2rem', display:'inline-flex', alignItems:'center', gap:'0.4rem',
                        padding:'0.25rem 0.7rem', borderRadius:'6px', cursor:'help',
                        background:'rgba(255,255,255,0.07)', border:'1px solid rgba(255,255,255,0.15)' }}>
                      <span style={{ fontSize:'0.72rem', color:'rgba(255,255,255,0.45)' }}>유통</span>
                      <span style={{ fontSize:'0.85rem', fontWeight:700, color:'rgba(255,255,255,0.85)' }}>{floatStr}</span>
                      {floatRatio && (
                        <span style={{ fontSize:'0.75rem', color:'rgba(255,255,255,0.5)' }}>({floatRatio}%)</span>
                      )}
                    </div>
                  )}
                </div>
              );
            })()}
          </div>
          {/* 구분선 + 수급 + 대차잔고 */}
          {chartData.length > 0 && (() => {
            const recent5 = chartData.slice(-5);
            const today   = chartData[chartData.length-1];
            // 당일
            const inst1 = today?.inst_net_buy || 0;
            const frn1  = today?.frn_net_buy  || 0;
            const ind1  = -(inst1 + frn1);
            // 5일 누적 (수량)
            const inst5 = recent5.reduce((s,d)=>s+(d.inst_net_buy||0),0);
            const frn5  = recent5.reduce((s,d)=>s+(d.frn_net_buy||0),0);
            const ind5  = -(inst5 + frn5);
            // 5일 누적 금액 (백만원→억원)
            const inst5a = recent5.reduce((s,d)=>s+(d.inst_net_buy_amt||0),0);
            const frn5a  = recent5.reduce((s,d)=>s+(d.frn_net_buy_amt||0),0);
            const ind5a  = -(inst5a + frn5a);

            const fmtQty = (v) => {
              if (!v) return '-';
              const sg=v>0?'+':'-', a=Math.abs(v);
              if(a>=10000) return sg+Math.round(a/10000).toLocaleString('ko-KR')+'만주';
              return sg+Math.round(a).toLocaleString('ko-KR')+'주';
            };
            const fmtAmt = (v) => {
              if(!v) return null;
              const sg=v>0?'+':'-', a=Math.abs(v);
              if(a>=100) return sg+Math.round(a/100).toLocaleString('ko-KR')+'억원';
              if(a>=1)   return sg+Math.round(a).toLocaleString('ko-KR')+'백만원';
              return null;
            };
            if (inst1 === 0 && frn1 === 0) return null;

            const supplyData = [
              {lbl:'외국인', val1:frn1,  amt1:today?.frn_net_buy_amt,  val5:frn5,  amt5:frn5a},
              {lbl:'기관',   val1:inst1, amt1:today?.inst_net_buy_amt, val5:inst5, amt5:inst5a},
              {lbl:'개인',   val1:ind1,  amt1:today?.ind_net_buy_amt,  val5:ind5,  amt5:ind5a},
            ];

            return (
              <>
                <div style={{ width:'1px', height:'60px', background:'rgba(255,255,255,0.15)', margin:'0 0.5rem' }} />
                <div style={{ display:'flex', gap:'1.2rem', fontSize:'0.8rem', alignItems:'flex-start' }}>
                  {supplyData.map(({lbl,val1,amt1,val5,amt5}) => (
                    <div key={lbl} style={{ textAlign:'center', minWidth:'70px' }}>
                      <p style={{ color:'var(--text-secondary)', fontSize:'0.68rem', marginBottom:'0.2rem', letterSpacing:'0.03em' }}>
                        {lbl}
                      </p>
                      {/* 당일 */}
                      <p style={{ fontWeight:700, fontSize:'0.82rem', color: val1>0?'#ef4444':val1<0?'#3b82f6':'rgba(255,255,255,0.35)' }}>{fmtQty(val1)}</p>
                      <p style={{ fontSize:'0.65rem', color: (amt1||0)>0?'rgba(239,68,68,0.65)':'rgba(59,130,246,0.65)' }}>
                        {fmtAmt(amt1) ?? '-'}
                      </p>
                      {/* 5일 누적 */}
                      <div style={{marginTop:'0.2rem',paddingTop:'0.2rem',borderTop:'1px solid rgba(255,255,255,0.08)'}}>
                        <p style={{fontSize:'0.6rem',color:'rgba(255,255,255,0.3)',marginBottom:'0.1rem'}}>5일누적</p>
                        <p style={{ fontWeight:600, fontSize:'0.75rem', color: val5>0?'rgba(239,68,68,0.8)':val5<0?'rgba(59,130,246,0.8)':'rgba(255,255,255,0.25)' }}>{fmtQty(val5)}</p>
                        <p style={{ fontSize:'0.62rem', color: (amt5||0)>0?'rgba(239,68,68,0.5)':'rgba(59,130,246,0.5)' }}>
                          {fmtAmt(amt5) ?? '-'}
                        </p>
                      </div>
                    </div>
                  ))}

                  {/* 대차잔고 — 신호등 + 수량 완전 분리 */}
                  {shortData && (() => {
                    const fmtBal = (v) => {
                      if(!v) return '-';
                      if(v >= 100000000) return (v/100000000).toFixed(1) + '억주';
                      if(v >= 10000000)  return (v/10000000).toFixed(1)  + '천만주';
                      if(v >= 10000)     return (v/10000).toFixed(1)     + '만주';
                      return Math.round(v).toLocaleString('ko-KR') + '주';
                    };
                    const lights = [
                      {label:'금일', val:shortData.today, sig:shortData.today_signal},
                      {label:'5일평균', val:shortData.avg5, sig:shortData.week_signal},
                    ];
                    return (
                      <>
                        <div style={{width:'1px',height:'70px',background:'rgba(255,255,255,0.15)',margin:'0 0.3rem'}}/>
                        <div style={{textAlign:'center'}}>
                          <p style={{color:'var(--text-secondary)',fontSize:'0.68rem',marginBottom:'0.35rem',letterSpacing:'0.03em'}}>
                            대차잔고
                          </p>
                          <div style={{display:'flex',gap:'5px'}}>
                            {lights.map(({label,val,sig})=>{
                              const color = sig==='green' ? '#22c55e' : '#ef4444';
                              return (
                                <div key={label} style={{display:'flex',flexDirection:'column',alignItems:'center',
                                  padding:'3px 7px',borderRadius:'5px',minWidth:'56px',
                                  background:`${color}12`,border:`1px solid ${color}35`}}>
                                  <span style={{fontSize:'0.58rem',color:'rgba(255,255,255,0.4)',marginBottom:'1px'}}>{label}</span>
                                  <span style={{fontSize:'0.88rem',fontWeight:700,color,lineHeight:1.1}}>{sig==='green'?'▼':'▲'}</span>
                                  <span style={{fontSize:'0.65rem',color:'rgba(255,255,255,0.6)',marginTop:'2px',fontWeight:500}}>
                                    {fmtBal(val)}
                                  </span>
                                </div>
                              );
                            })}
                          </div>
                        </div>
                      </>
                    );
                  })()}
                </div>
              </>
            );
          })()}
        </header>

        {/* 재무 지표 4개 */}
        <div style={{ display:'grid', gridTemplateColumns:'repeat(4,1fr)', gap:'0.75rem' }}>
          {[
            { label:'매출액',  val: formatWon(summStats?.revenue) },
            { label:'영업이익', val: formatWon(summStats?.operating_profit) },
            { label:'OPM',    val: summStats?.opm != null ? Number(summStats.opm).toFixed(1)+'%' : '-' },
            { label:'ROE',    val: summStats?.roe != null ? Number(summStats.roe).toFixed(1)+'%' : '-' },
          ].map(({label,val}) => (
            <div key={label} className="glass-panel" style={{ padding:'0.9rem 1rem' }}>
              <p style={{ fontSize:'0.75rem', color:'var(--text-secondary)', marginBottom:'0.3rem' }}>{label}</p>
              <h3 style={{ fontSize:'1.1rem' }}>{val || '-'}</h3>
            </div>
          ))}
        </div>

        {/* 밸류에이션 + 52주 고저가 */}
        <div style={{ display:'grid', gridTemplateColumns:'repeat(5,1fr)', gap:'0.75rem' }}>
          {[
            { label:'PBR', val: summStats?.pbr != null ? Number(summStats.pbr).toFixed(1)+'x' : '-',
              sub: summStats?.pbr != null ? (summStats.source||'네이버금융') : (collecting ? '📡 조회 중...' : '자정 업데이트 후 표시'), dim: summStats?.pbr==null, color:'var(--accent-purple)' },
            { label:'PER (TTM)', val: summStats?.per != null ? Number(summStats.per).toFixed(1)+'x' : '-',
              sub: summStats?.trailing_eps != null ? `EPS ${fmtNum(summStats.trailing_eps)}원` : (collecting ? '📡 조회 중...' : '자정 업데이트 후 표시'), dim: summStats?.per==null, color:'var(--accent-purple)' },
            { label:'Forward PER', val: summStats?.forward_per != null ? Number(summStats.forward_per).toFixed(1)+'x' : '-',
              sub: '한국 종목 미제공', dim: summStats?.forward_per==null, color:'var(--accent-purple)' },
            { label:'52주 최고가', val: summStats?.high52 != null ? summStats.high52.toLocaleString('ko-KR')+'원' : '-',
              sub: summStats?.high52 && latestClose ? `현재 ${((latestClose/summStats.high52-1)*100).toFixed(1)}%` : '52주 최고가',
              dim: summStats?.high52==null,
              color: summStats?.high52 && latestClose && latestClose >= summStats.high52 * 0.95 ? '#22c55e' : '#ef4444' },
            { label:'52주 최저가', val: summStats?.low52 != null ? summStats.low52.toLocaleString('ko-KR')+'원' : '-',
              sub: summStats?.low52 && latestClose ? `현재 +${((latestClose/summStats.low52-1)*100).toFixed(1)}%` : '52주 최저가',
              dim: summStats?.low52==null, color:'#fbbf24' },
          ].map(({label,val,sub,dim,color}) => (
            <div key={label} className="glass-panel" style={{ padding:'0.9rem 1rem' }}>
              <p style={{ fontSize:'0.75rem', color:'var(--text-secondary)', marginBottom:'0.3rem' }}>{label}</p>
              <h3 style={{ fontSize:'1rem', color: dim ? 'var(--text-secondary)' : color }}>{val}</h3>
              <p style={{ fontSize:'0.65rem', color:'var(--text-secondary)', marginTop:'0.2rem' }}>{sub}</p>
            </div>
          ))}
        </div>

        {/* 차트 영역 */}
        <div style={{ display:'flex', flexDirection:'column', gap:'0.75rem' }}>
          {/* 종목 시그널 보드 */}
          <SignalBoard scope="stock" stockCode={selectedStock} key={selectedStock} />

          {/* 기간 버튼 */}
          <div style={{ display:'flex', gap:'0.4rem', justifyContent:'flex-end' }}>
            {[{label:'30일',days:30},{label:'180일',days:180},{label:'1년',days:365},{label:'3년',days:1095},{label:'10년',days:3650}].map(({label,days}) => (
              <button key={days} onClick={() => handleChartDaysChange(days)} style={{
                padding:'0.3rem 0.75rem', borderRadius:'6px', fontSize:'0.78rem', cursor:'pointer', fontWeight:600,
                border: chartDays===days ? '1px solid var(--accent-mint)' : '1px solid var(--glass-border)',
                background: chartDays===days ? 'rgba(45,212,191,0.15)' : 'transparent',
                color: chartDays===days ? 'var(--accent-mint)' : 'var(--text-secondary)',
              }}>{label}</button>
            ))}
          </div>

          {/* candle chart */}
          <div className="glass-panel" style={{ padding:'1rem' }}>
            <div style={{ display:'flex', alignItems:'center', gap:'1rem', marginBottom:'0.6rem', flexWrap:'wrap' }}>
              <p style={{ fontSize:'0.8rem', color:'var(--text-secondary)' }}>주가 차트 ({chartDays>=3650?'10년':chartDays>=1095?'3년':chartDays===365?'1년':chartDays+'일'})</p>
              {chartData.length > 0 && (
                <div style={{ display:'flex', gap:'0.8rem', fontSize:'0.68rem' }}>
                  {[['MA5','#facc15'],['MA20','#f97316'],['MA60','#a78bfa']].map(([lb,cl]) => (
                    <span key={lb} style={{ display:'flex', alignItems:'center', gap:'0.25rem' }}>
                      <span style={{ display:'inline-block', width:'16px', height:'2px', background:cl }}/><span style={{ color:'var(--text-secondary)' }}>{lb}</span>
                    </span>
                  ))}
                  {[['양봉','#ef4444'],['음봉','#3b82f6']].map(([lb,cl]) => (
                    <span key={lb} style={{ display:'flex', alignItems:'center', gap:'0.3rem' }}>
                      <span style={{ display:'inline-block', width:'8px', height:'10px', background:cl, borderRadius:'1px' }}/><span style={{ color:'var(--text-secondary)' }}>{lb}</span>
                    </span>
                  ))}
                </div>
              )}
            </div>
            {chartData.length === 0 ? (
              <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', height:'260px', gap:'0.6rem' }}>
                {collecting ? <><div style={{width:'22px',height:'22px',borderRadius:'50%',border:'2px solid var(--accent-mint)',borderTopColor:'transparent',animation:'spin 0.8s linear infinite'}}/><span style={{color:'var(--accent-mint)',fontSize:'0.8rem'}}>주가 수집 중...</span></> : <span style={{color:'var(--text-secondary)',fontSize:'0.8rem'}}>주가 데이터 없음</span>}
              </div>
            ) : (() => {
              const mc=(arr,n)=>arr.map((_,i)=>{if(i<n-1)return null;return arr.slice(i-n+1,i+1).reduce((s,d)=>s+(d.close||0),0)/n;});
              const ma5=mc(displayChartData,5),ma20=mc(displayChartData,20),ma60=mc(displayChartData,60);
              const W=900,HC=220,HV=55,PL=58,PR=8,PT=10,PB=20,N=displayChartData.length;
              const minP=Math.min(...displayChartData.map(d=>d.low||d.close||0))*0.998;
              const maxP=Math.max(...displayChartData.map(d=>d.high||d.close||0))*1.002;
              const maxV=Math.max(...displayChartData.map(d=>d.volume||0))||1;
              const xs=(W-PL-PR)/N,xp=i=>PL+(i+0.5)*xs;
              const yp=v=>PT+(1-(v-minP)/(maxP-minP))*(HC-PT-PB);
              const yv=v=>HV-2-(v/maxV)*(HV-4);
              const pt=Array.from({length:4},(_,i)=>minP+(maxP-minP)*i/3);
              const fp=v=>v>=100000?(v/10000).toFixed(0)+"만":Math.round(v).toLocaleString("ko-KR");
              const xt=Array.from({length:5},(_,i)=>Math.floor(i*(N-1)/4));
              const [tip,setTip]=React.useState(null);
              const cw=Math.max(1,xs*0.6);
              return (
                <div style={{position:"relative"}}>
                  <svg viewBox={`0 0 ${W} ${HC+HV+8}`} style={{width:"100%",height:"auto",cursor:"crosshair"}} onMouseLeave={()=>setTip(null)}>
                    {pt.map((v,i)=>(<g key={i}><line x1={PL} x2={W-PR} y1={yp(v)} y2={yp(v)} stroke="rgba(255,255,255,0.05)" strokeWidth="1"/><text x={PL-4} y={yp(v)+4} textAnchor="end" fontSize="9" fill="rgba(100,116,139,0.8)">{fp(v)}</text></g>))}
                    <line x1={PL} x2={W-PR} y1={HC-PB} y2={HC-PB} stroke="rgba(255,255,255,0.08)" strokeWidth="1"/>
                    <line x1={PL} x2={W-PR} y1={HC+2} y2={HC+2} stroke="rgba(255,255,255,0.06)" strokeWidth="1"/>
                    {xt.map(i=>(<text key={i} x={xp(i)} y={HC-2} textAnchor="middle" fontSize="9" fill="rgba(100,116,139,0.8)">{displayChartData[i]?.date?.slice(5).replace("-","/")}</text>))}
                    {displayChartData.map((d,i)=>{const u=(d.close||0)>=(d.open||d.close||0);const vh=HV-2-yv(d.volume||0);return <rect key={i} x={xp(i)-cw/2} y={HC+2+yv(d.volume||0)} width={cw} height={Math.max(1,vh)} fill={u?"rgba(239,68,68,0.35)":"rgba(59,130,246,0.35)"}/>;}) }
                    {displayChartData.map((d,i)=>{const o=d.open||d.close||0,h=d.high||d.close||0,l=d.low||d.close||0,c=d.close||0,u=c>=o,cl=u?"#ef4444":"#3b82f6",bT=yp(Math.max(o,c)),bH=Math.max(1,Math.abs(yp(o)-yp(c))),cx=xp(i);return(<g key={i} onMouseEnter={()=>setTip({i,x:cx,d,ma5:ma5[i],ma20:ma20[i],ma60:ma60[i]})}><line x1={cx} x2={cx} y1={yp(h)} y2={bT} stroke={cl} strokeWidth="1"/><rect x={cx-cw/2} y={bT} width={cw} height={bH} fill={u?cl:"none"} stroke={cl} strokeWidth="1"/><line x1={cx} x2={cx} y1={bT+bH} y2={yp(l)} stroke={cl} strokeWidth="1"/></g>);}) }
                    {(()=>{let p="";displayChartData.forEach((d,i)=>{if(d.close!=null)p+=p===''?`M${xp(i)},${yp(d.close)}`:`L${xp(i)},${yp(d.close)}`;});return <path d={p} fill="none" stroke="var(--accent-mint)" strokeWidth="1.5" opacity="0.7"/>;})()}
                    {[{ma:ma5,cl:"#facc15",w:1.2,da:"4 3"},{ma:ma20,cl:"#f97316",w:1.5,da:"5 3"},{ma:ma60,cl:"#a78bfa",w:1.5,da:"6 3"}].map(({ma,cl,w,da},li)=>{let p="";ma.forEach((v,i)=>{if(v!=null)p+=ma[i-1]!=null?` L${xp(i)},${yp(v)}`:`M${xp(i)},${yp(v)}`;});return <path key={li} d={p} fill="none" stroke={cl} strokeWidth={w} strokeDasharray={da}/>;}) }
                    {tip&&<line x1={tip.x} x2={tip.x} y1={PT} y2={HC-PB} stroke="rgba(255,255,255,0.2)" strokeWidth="1" strokeDasharray="4 2"/>}
                  </svg>
                  {tip&&(()=>{const d=tip.d,u=(d.close||0)>=(d.open||d.close||0),chg=d.open?((d.close-d.open)/d.open*100):0;return(<div style={{position:"absolute",top:8,left:tip.x>W*0.6?"5%":"55%",background:"rgba(10,10,20,0.97)",border:"1px solid rgba(255,255,255,0.12)",borderRadius:"8px",padding:"0.6rem 0.8rem",fontSize:"0.72rem",lineHeight:1.8,minWidth:"140px",pointerEvents:"none"}}><div style={{fontWeight:700,color:"var(--text-primary)",marginBottom:"0.2rem"}}>{d.date}</div>{[["시가",d.open],["고가",d.high],["저가",d.low],["종가",d.close]].map(([lb,v])=>(<div key={lb} style={{display:"flex",justifyContent:"space-between",gap:"1rem"}}><span style={{color:"var(--text-secondary)"}}>{lb}</span><span style={{color:u?"#ef4444":"#3b82f6",fontWeight:600}}>{Math.round(v||0).toLocaleString("ko-KR")}</span></div>))}<div style={{display:"flex",justifyContent:"space-between",gap:"1rem"}}><span style={{color:"var(--text-secondary)"}}>{"등락"}</span><span style={{color:u?"#ef4444":"#3b82f6",fontWeight:600}}>{chg>=0?"+":""}{chg.toFixed(1)}%</span></div><div style={{display:"flex",justifyContent:"space-between",gap:"1rem"}}><span style={{color:"var(--text-secondary)"}}>{"거래량"}</span><span style={{color:"rgba(255,255,255,0.8)"}}>{Math.round(d.volume||0).toLocaleString("ko-KR")}</span></div><div style={{borderTop:"1px solid rgba(255,255,255,0.08)",marginTop:"0.3rem",paddingTop:"0.3rem"}}>{[["MA5",tip.ma5,"#facc15"],["MA20",tip.ma20,"#f97316"],["MA60",tip.ma60,"#a78bfa"]].map(([lb,v,cl])=>v!=null&&(<div key={lb} style={{display:"flex",justifyContent:"space-between",gap:"1rem"}}><span style={{color:cl}}>{lb}</span><span style={{color:cl}}>{Math.round(v).toLocaleString("ko-KR")}</span></div>))}</div></div>);})()}
                </div>
              );
            })()}
          </div>

          {/* 수급 차트 */}
          {(() => {
            const [showInstBar, setShowInstBar] = React.useState(false);
            const [showFrnBar,  setShowFrnBar]  = React.useState(false);
            const supLabel = chartDays>=3650?'10년':chartDays>=1095?'3년':chartDays===365?'1년':chartDays+'일';
            const btnStyle = (on, color) => ({
              padding:'0.15rem 0.55rem', borderRadius:'5px', fontSize:'0.68rem', cursor:'pointer', fontWeight:600,
              border: `1px solid ${on ? color : 'rgba(255,255,255,0.12)'}`,
              background: on ? `${color}22` : 'transparent',
              color: on ? color : 'rgba(255,255,255,0.4)',
            });
            return (
          <div className="glass-panel" style={{ padding:'1rem' }}>
            <div style={{ display:'flex', alignItems:'center', gap:'0.5rem', marginBottom:'0.5rem', flexWrap:'wrap' }}>
              <p style={{ fontSize:'0.8rem', color:'var(--text-secondary)', marginRight:'0.3rem' }}>순매수 ({supLabel})</p>
              {/* 누적선 범례 (항상 표시) */}
              <span style={{ fontSize:'0.68rem', color:'#fca5a5' }}>- - 기관누적</span>
              <span style={{ fontSize:'0.68rem', color:'#dc2626' }}>— 외국인누적</span>
              {/* 바차트 토글 버튼 */}
              <div style={{ marginLeft:'auto', display:'flex', gap:'0.3rem' }}>
                <button style={btnStyle(showInstBar,'#fca5a5')} onClick={()=>setShowInstBar(v=>!v)}>
                  기관 일별 {showInstBar?'숨기기':'표시'}
                </button>
                <button style={btnStyle(showFrnBar,'#dc2626')} onClick={()=>setShowFrnBar(v=>!v)}>
                  외국인 일별 {showFrnBar?'숨기기':'표시'}
                </button>
              </div>
            </div>
            {chartData.length===0 ? (
              <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', height:'130px', gap:'0.6rem' }}>
                {collecting
                  ? <><div style={{width:'22px',height:'22px',borderRadius:'50%',border:'2px solid var(--accent-mint)',borderTopColor:'transparent',animation:'spin 0.8s linear infinite'}}/><span style={{color:'var(--accent-mint)',fontSize:'0.8rem'}}>수급 수집 중...</span></>
                  : <span style={{color:'var(--text-secondary)',fontSize:'0.8rem'}}>수급 데이터 없음 — 조회 시 KIS API로 자동 수집됩니다</span>}
              </div>
            ) : (() => {
              let ci=0, cf=0;
              const d2 = displayChartData.map(d => { ci+=d.inst_net_buy||0; cf+=d.frn_net_buy||0; return {...d,inst_cum:ci,frn_cum:cf}; });
              // 수급 데이터 존재 여부 (모두 0이면 KIS 미수집 상태)
              const hasSupplyData = d2.some(d => d.inst_net_buy !== 0 || d.frn_net_buy !== 0);
              return (
                <>
                {!hasSupplyData && (
                  <div style={{textAlign:'center',padding:'0.4rem',fontSize:'0.72rem',color:'rgba(100,116,139,0.8)',background:'rgba(251,191,36,0.05)',borderRadius:'6px',marginBottom:'0.4rem',border:'1px solid rgba(251,191,36,0.15)'}}>
                    ⚠ 기관/외국인 수급 데이터 없음 — KIS API 수집 대기 중 (자정 배치 또는 data_collector.py 실행 필요)
                  </div>
                )}
                <ResponsiveContainer width="100%" height={180}>
                  <ComposedChart data={d2} margin={{top:5,right:5,bottom:0,left:0}} barCategoryGap="20%">
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
                    <XAxis dataKey="date" tick={{fontSize:10,fill:'#64748b'}} tickLine={false} interval="preserveStartEnd"
                      tickFormatter={d => d ? d.slice(5).replace('-','/') : ''} />
                    <YAxis yAxisId="bar" domain={[dataMin => Math.min(0, dataMin), dataMax => Math.max(0, dataMax)]} tick={{fontSize:9,fill:'#64748b'}} tickLine={false} axisLine={false} width={62}
                      tickFormatter={v=>{const a=Math.abs(v),s=v<0?'-':'';if(a>=10000)return s+(a/10000).toFixed(1)+'만주';if(a>=1000)return s+(a/1000).toFixed(0)+'천주';return s+a.toLocaleString('ko-KR')+'주';}}/>
                    <YAxis yAxisId="line" orientation="right" domain={[dataMin => Math.min(0, dataMin), dataMax => Math.max(0, dataMax)]} tick={{fontSize:9,fill:'#64748b'}} tickLine={false} axisLine={false} width={62}
                      tickFormatter={v=>{const a=Math.abs(v),s=v<0?'-':'';if(a>=10000)return s+(a/10000).toFixed(1)+'만';if(a>=1000)return s+(a/1000).toFixed(0)+'천';return s+a.toLocaleString('ko-KR');}}/>
                    <ReferenceLine yAxisId="bar" y={0} stroke="rgba(255,255,255,0.45)" strokeWidth={1.5} strokeDasharray="4 2"/>
                    <ReferenceLine yAxisId="line" y={0} stroke="rgba(255,255,255,0.15)" strokeWidth={1}/>
                    <Tooltip contentStyle={{background:'rgba(15,15,25,0.95)',border:'1px solid rgba(255,255,255,0.12)',borderRadius:'8px',fontSize:'0.78rem'}}
                      formatter={(v,n)=>{const a=Math.abs(v),s=v>=0?'+':'-';return[a>=10000?s+(a/10000).toFixed(1)+'만주':s+a.toLocaleString('ko-KR')+'주',n];}}/>
                    {showInstBar && <Bar yAxisId="bar" dataKey="inst_net_buy" name="기관(일)" barSize={chartDays<=30?10:chartDays<=180?4:chartDays<=365?2:1}>
                      {d2.map((e,i)=><Cell key={i} fill={e.inst_net_buy>=0?'#fca5a5':'#93c5fd'} fillOpacity={0.9}/>)}
                    </Bar>}
                    {showFrnBar && <Bar yAxisId="bar" dataKey="frn_net_buy" name="외국인(일)" barSize={chartDays<=30?10:chartDays<=180?4:chartDays<=365?2:1}>
                      {d2.map((e,i)=><Cell key={i} fill={e.frn_net_buy>=0?'#dc2626':'#1d4ed8'} fillOpacity={0.9}/>)}
                    </Bar>}
                    <Line yAxisId="line" type="monotone" dataKey="inst_cum" name="기관(누적)" stroke="#fca5a5" dot={false} strokeWidth={1.5} strokeDasharray="5 3"/>
                    <Line yAxisId="line" type="monotone" dataKey="frn_cum" name="외국인(누적)" stroke="#dc2626" dot={false} strokeWidth={2}/>
                  </ComposedChart>
                </ResponsiveContainer>
                </>
              );
            })()}
          </div>
            );
          })()}
        </div>

        {/* ── DART 공시 정보 ────────────────────────────────────── */}
        {/^\d{6}$/.test(selectedStock) && (
          <section className="glass-panel">
            <div style={{ padding:'0.6rem 1rem', borderBottom:'1px solid var(--glass-border)',
              display:'flex', alignItems:'center', justifyContent:'space-between' }}>
              <span style={{ fontSize:'0.8rem', fontWeight:600, color:'var(--accent-yellow, #facc15)' }}>
                📢 최근 공시 (최근 1년 / 최대 100건)
              </span>
              <div style={{ display:'flex', alignItems:'center', gap:'0.5rem' }}>
                {disclosureLoading && (
                  <span style={{ width:'10px', height:'10px', borderRadius:'50%',
                    border:'2px solid var(--accent-yellow, #facc15)',
                    borderTopColor:'transparent', display:'inline-block',
                    animation:'spin 0.8s linear infinite' }} />
                )}
                <button onClick={fetchDisclosures}
                  style={{ padding:'0.15rem 0.5rem', borderRadius:'4px', fontSize:'0.7rem',
                    background:'rgba(250,204,21,0.1)', border:'1px solid rgba(250,204,21,0.3)',
                    color:'#facc15', cursor:'pointer' }}>
                  새로고침
                </button>
              </div>
            </div>
            {disclosures.length === 0 ? (
              <div style={{ padding:'1.2rem', textAlign:'center', fontSize:'0.82rem',
                color:'var(--text-secondary)' }}>
                {disclosureLoading
                  ? 'DART 공시 조회 중...'
                  : '최근 1년 내 공시가 없습니다'}
              </div>
            ) : (() => {
              const visible = showAllDisclosures
                ? disclosures
                : disclosures.slice(0, DISCLOSURE_PREVIEW);
              const hasMore = disclosures.length > DISCLOSURE_PREVIEW;
              return (
                <div style={{ display:'flex', flexDirection:'column' }}>
                  {visible.map((d, i) => (
                    <div key={d.rcept_no || i}
                      style={{ display:'flex', alignItems:'flex-start', gap:'0.75rem',
                        padding:'0.55rem 1rem',
                        borderBottom: '1px solid rgba(255,255,255,0.05)',
                        background: i % 2 === 0 ? 'rgba(255,255,255,0.01)' : 'transparent' }}>
                      {/* 날짜 */}
                      <span style={{ fontSize:'0.72rem', color:'var(--text-secondary)',
                        whiteSpace:'nowrap', flexShrink:0, marginTop:'0.1rem' }}>
                        {d.rcept_dt}
                      </span>
                      {/* 보고서명 */}
                      <div style={{ flex:1, minWidth:0 }}>
                        {d.dart_url ? (
                          <a href={d.dart_url} target="_blank" rel="noopener noreferrer"
                            style={{ fontSize:'0.82rem', color:'var(--text-primary)',
                              textDecoration:'none', display:'block',
                              overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}
                            title={d.report_nm}>
                            {d.report_nm}
                          </a>
                        ) : (
                          <span style={{ fontSize:'0.82rem',
                            overflow:'hidden', textOverflow:'ellipsis', display:'block',
                            whiteSpace:'nowrap' }}>
                            {d.report_nm}
                          </span>
                        )}
                        {d.flr_nm && d.flr_nm !== d.corp_name && (
                          <span style={{ fontSize:'0.68rem', color:'var(--text-secondary)' }}>
                            제출: {d.flr_nm}
                          </span>
                        )}
                      </div>
                      {/* 원문 링크 */}
                      {d.dart_url && (
                        <a href={d.dart_url} target="_blank" rel="noopener noreferrer"
                          style={{ padding:'0.2rem 0.5rem', borderRadius:'4px', fontSize:'0.68rem',
                            background:'rgba(250,204,21,0.1)', border:'1px solid rgba(250,204,21,0.25)',
                            color:'#facc15', textDecoration:'none', whiteSpace:'nowrap',
                            flexShrink:0, alignSelf:'center' }}>
                          원문
                        </a>
                      )}
                    </div>
                  ))}
                  {/* 더 보기 / 접기 버튼 */}
                  {hasMore && (
                    <button onClick={() => setShowAllDisclosures(v => !v)}
                      style={{ width:'100%', padding:'0.55rem', border:'none',
                        borderTop:'1px solid rgba(255,255,255,0.05)',
                        background:'rgba(255,255,255,0.02)',
                        color:'var(--text-secondary)', fontSize:'0.78rem',
                        cursor:'pointer', display:'flex', alignItems:'center',
                        justifyContent:'center', gap:'0.3rem' }}>
                      {showAllDisclosures
                        ? `▲ 접기`
                        : `▼ 더 보기 (${disclosures.length - DISCLOSURE_PREVIEW}건 더)`}
                    </button>
                  )}
                </div>
              );
            })()}
          </section>
        )}

                {/* 연간 재무 테이블 */}
        <section className="glass-panel" style={{ overflow:'auto' }}>
          <div style={{ padding:'0.6rem 1rem', borderBottom:'1px solid var(--glass-border)' }}>
            <span style={{ fontSize:'0.8rem', fontWeight:600, color:'var(--accent-mint)' }}>연간 실적</span>
          </div>
          {finTable.length === 0 ? (
            <div style={{ padding:'1.5rem', textAlign:'center', fontSize:'0.85rem' }}>
              {collecting
                ? <span style={{display:'inline-flex',alignItems:'center',gap:'0.5rem',color:'var(--accent-mint)'}}>
                    <span style={{width:'12px',height:'12px',borderRadius:'50%',border:'2px solid var(--accent-mint)',borderTopColor:'transparent',display:'inline-block',animation:'spin 0.8s linear infinite'}}/>
                    DART에서 재무제표 수집 중...
                  </span>
                : <span style={{color:'var(--text-secondary)'}}>연간 재무데이터 없음 — 매일 자정 DART 공시 기준으로 자동 업데이트됩니다</span>}
            </div>
          ) : (
            <table className="premium-table" style={{ width:'100%' }}>
              <thead><tr>
                <th style={{ minWidth:'90px', position:'sticky', left:0, background:'rgba(15,15,25,0.97)' }}>기간</th>
                {finTable.map((t,i) => <th key={i} style={{ textAlign:'right', minWidth:'70px' }}>{t.period}</th>)}
              </tr></thead>
              <tbody>{tableRows.map(row => (
                <tr key={row.key}>
                  <td style={{ color:'var(--text-secondary)', fontWeight:600, whiteSpace:'nowrap', position:'sticky', left:0, background:'rgba(15,15,25,0.97)' }}>{row.label}</td>
                  {finTable.map((t,i) => <td key={i} style={{ textAlign:'right', color:numColor(t[row.key]), whiteSpace:'nowrap' }}>{row.fmt(t[row.key])}</td>)}
                </tr>
              ))}</tbody>
            </table>
          )}
        </section>

        {/* 분기 재무 테이블 */}
        <section className="glass-panel" style={{ overflow:'auto' }}>
          <div style={{ padding:'0.6rem 1rem', borderBottom:'1px solid var(--glass-border)' }}>
            <span style={{ fontSize:'0.8rem', fontWeight:600, color:'var(--accent-purple)' }}>분기 실적 (최근 8분기)</span>
          </div>
          {quarterTable.length === 0 ? (
            <div style={{ padding:'1.5rem', textAlign:'center', fontSize:'0.85rem' }}>
              {collecting
                ? <span style={{display:'inline-flex',alignItems:'center',gap:'0.5rem',color:'var(--accent-mint)'}}>
                    <span style={{width:'12px',height:'12px',borderRadius:'50%',border:'2px solid var(--accent-mint)',borderTopColor:'transparent',display:'inline-block',animation:'spin 0.8s linear infinite'}}/>
                    분기 실적 수집 중...
                  </span>
                : <span style={{color:'var(--text-secondary)'}}>분기 재무데이터 없음 — 매일 자정 DART 공시 기준으로 자동 업데이트됩니다</span>}
            </div>
          ) : (
            <table className="premium-table" style={{ width:'100%' }}>
              <thead><tr>
                <th style={{ minWidth:'90px', position:'sticky', left:0, background:'rgba(15,15,25,0.97)' }}>기간</th>
                {quarterTable.map((t,i) => <th key={i} style={{ textAlign:'right', minWidth:'70px' }}>{t.period}</th>)}
              </tr></thead>
              <tbody>{tableRows.map(row => (
                <tr key={row.key}>
                  <td style={{ color:'var(--text-secondary)', fontWeight:600, whiteSpace:'nowrap', position:'sticky', left:0, background:'rgba(15,15,25,0.97)' }}>{row.label}</td>
                  {quarterTable.map((t,i) => <td key={i} style={{ textAlign:'right', color:numColor(t[row.key]), whiteSpace:'nowrap' }}>{row.fmt(t[row.key])}</td>)}
                </tr>
              ))}</tbody>
            </table>
          )}
        </section>

        {/* ── 현금흐름표 ── */}
        {(() => {
          const cfRows = [
            { key:'operating_cf', label:'영업활동현금흐름', hint:'영업에서 창출한 현금' },
            { key:'investing_cf', label:'투자활동현금흐름', hint:'설비·투자에 사용한 현금' },
            { key:'financing_cf', label:'재무활동현금흐름', hint:'차입·배당 등 재무활동' },
            { key:'capex',        label:'설비투자(CapEx)',  hint:'유형자산 취득(절대값)' },
            { key:'free_cf',      label:'잉여현금흐름(FCF)',hint:'영업CF - CapEx' },
            { key:'cash_end',     label:'기말현금',         hint:'기말 현금및현금성자산' },
            { key:'depreciation', label:'감가상각비',       hint:'비현금 비용' },
          ];
          const cfColor = (key, val) => {
            if (val == null) return 'rgba(255,255,255,0.5)';
            if (key === 'investing_cf' || key === 'financing_cf') return 'rgba(255,255,255,0.7)';
            if (key === 'capex') return val > 0 ? '#fbbf24' : 'rgba(255,255,255,0.5)';
            return val > 0 ? '#2dd4bf' : '#f87171';
          };
          const fmtCf = v => v == null ? '-' : (v >= 0 ? '+' : '') + v.toLocaleString() + '억';

          return (
            <>
              {/* 연간 현금흐름표 */}
              <section className="glass-panel" style={{ overflow:'auto' }}>
                <div style={{ padding:'0.6rem 1rem', borderBottom:'1px solid var(--glass-border)', display:'flex', alignItems:'center', gap:'0.6rem' }}>
                  <span style={{ fontSize:'0.8rem', fontWeight:600, color:'#34d399' }}>연간 현금흐름표</span>
                  <span style={{ fontSize:'0.7rem', color:'var(--text-secondary)' }}>(억원)</span>
                </div>
                {cfAnnual.length === 0 ? (
                  <div style={{ padding:'1.5rem', textAlign:'center', fontSize:'0.85rem', color:'var(--text-secondary)' }}>
                    연간 현금흐름 데이터 없음 — DART 미공시이거나 수집 전입니다
                  </div>
                ) : (
                  <table className="premium-table" style={{ width:'100%' }}>
                    <thead><tr>
                      <th style={{ minWidth:'110px', position:'sticky', left:0, background:'rgba(15,15,25,0.97)' }}>항목</th>
                      {cfAnnual.map((t,i) => <th key={i} style={{ textAlign:'right', minWidth:'70px' }}>{t.period}</th>)}
                    </tr></thead>
                    <tbody>{cfRows.map(row => (
                      <tr key={row.key}>
                        <td title={row.hint} style={{ color:'var(--text-secondary)', fontWeight:600, whiteSpace:'nowrap', position:'sticky', left:0, background:'rgba(15,15,25,0.97)', cursor:'help' }}>{row.label}</td>
                        {cfAnnual.map((t,i) => (
                          <td key={i} style={{ textAlign:'right', color:cfColor(row.key, t[row.key]), whiteSpace:'nowrap' }}>
                            {fmtCf(t[row.key])}
                          </td>
                        ))}
                      </tr>
                    ))}</tbody>
                  </table>
                )}
              </section>

              {/* 분기 현금흐름표 */}
              <section className="glass-panel" style={{ overflow:'auto' }}>
                <div style={{ padding:'0.6rem 1rem', borderBottom:'1px solid var(--glass-border)', display:'flex', alignItems:'center', gap:'0.6rem' }}>
                  <span style={{ fontSize:'0.8rem', fontWeight:600, color:'#a78bfa' }}>분기 현금흐름표 (최근 8분기)</span>
                  <span style={{ fontSize:'0.7rem', color:'var(--text-secondary)' }}>(억원)</span>
                </div>
                {cfQuarter.length === 0 ? (
                  <div style={{ padding:'1.5rem', textAlign:'center', fontSize:'0.85rem', color:'var(--text-secondary)' }}>
                    분기 현금흐름 데이터 없음 — DART 분기보고서 미공시이거나 수집 전입니다
                  </div>
                ) : (
                  <table className="premium-table" style={{ width:'100%' }}>
                    <thead><tr>
                      <th style={{ minWidth:'110px', position:'sticky', left:0, background:'rgba(15,15,25,0.97)' }}>항목</th>
                      {cfQuarter.map((t,i) => <th key={i} style={{ textAlign:'right', minWidth:'70px' }}>{t.period}</th>)}
                    </tr></thead>
                    <tbody>{cfRows.map(row => (
                      <tr key={row.key}>
                        <td title={row.hint} style={{ color:'var(--text-secondary)', fontWeight:600, whiteSpace:'nowrap', position:'sticky', left:0, background:'rgba(15,15,25,0.97)', cursor:'help' }}>{row.label}</td>
                        {cfQuarter.map((t,i) => (
                          <td key={i} style={{ textAlign:'right', color:cfColor(row.key, t[row.key]), whiteSpace:'nowrap' }}>
                            {fmtCf(t[row.key])}
                          </td>
                        ))}
                      </tr>
                    ))}</tbody>
                  </table>
                )}
              </section>
            </>
          );
        })()}

        {/* 종목 보고서 */}
        {stockReports.length > 0 && (
          <div className="glass-panel" style={{padding:'1.2rem'}}>
            <h3 style={{fontSize:'0.9rem',fontWeight:700,marginBottom:'0.8rem',
              color:'var(--accent-mint)',display:'flex',alignItems:'center',gap:'0.5rem'}}>
              📄 종목 보고서 ({stockReports.length}건)
            </h3>
            <div style={{display:'flex',flexDirection:'column',gap:'0.35rem'}}>
              {stockReports.map(r => (
                <div key={r.id} style={{display:'flex',alignItems:'center',
                  justifyContent:'space-between',padding:'0.5rem 0.75rem',
                  borderRadius:'6px',background:'rgba(255,255,255,0.04)',
                  border:'1px solid var(--glass-border)'}}>
                  <div style={{flex:1,minWidth:0}}>
                    <p style={{fontSize:'0.82rem',fontWeight:600,
                      overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
                      {r.file_name}
                    </p>
                    <p style={{fontSize:'0.7rem',color:'var(--text-secondary)',marginTop:'0.1rem'}}>
                      {r.posted_date || r.report_date} | {r.channel_id}
                      {r.file_size ? ` | ${(r.file_size/1024).toFixed(0)}KB` : ''}
                    </p>
                  </div>
                  <a href={`/api/reports/download/${r.id}`} download={r.saved_name}
                    style={{marginLeft:'0.75rem',padding:'0.3rem 0.7rem',borderRadius:'5px',
                      background:'rgba(45,212,191,0.15)',border:'1px solid rgba(45,212,191,0.3)',
                      color:'var(--accent-mint)',fontSize:'0.75rem',textDecoration:'none',
                      whiteSpace:'nowrap',flexShrink:0}}>
                    ⬇ 다운로드
                  </a>
                </div>
              ))}
            </div>
          </div>
        )}

      </div>
    );
  };

  // ── 스크리너 ─────────────────────────────────────────────────
  const Screener = () => {
    const [screenTab, setScreenTab] = React.useState('combo');  // 기본: AI 적극 검토
    const [trendStocks, setTrendStocks] = React.useState([]);
    const [trendLoading, setTrendLoading] = React.useState(false);
    const [valueCandidates, setValueCandidates] = React.useState([]);
    const [valueLoading, setValueLoading] = React.useState(false);
    const [finStocks, setFinStocks] = React.useState([]);
    const [finLoading, setFinLoading] = React.useState(false);
    const [comboFromServer, setComboFromServer] = React.useState([]);
    const [comboLoading, setComboLoading] = React.useState(false);
    const [showFinLogic, setShowFinLogic] = React.useState(false);
    const [comboFilter, setComboFilter] = React.useState('all'); // 'all'=2개↑, 'triple'=3개
    const [comboLogic, setComboLogic] = React.useState('v1');    // 'v1'=Logic-#1, 'v2'=Logic-#2
    const [comboV2Data, setComboV2Data] = React.useState([]);
    const [comboV2Loading, setComboV2Loading] = React.useState(false);
    const [triggerData, setTriggerData] = React.useState(null);
    const [triggerLoading, setTriggerLoading] = React.useState(false);
    const [screenerMeta, setScreenerMeta] = React.useState(null);
    const stickyRef = React.useRef(null);

    // 스크리너 메타(로직 설명) — signal_logic.py 에서 동적 로드
    React.useEffect(() => {
      fetch(API('/api/screener/meta'))
        .then(r => r.ok ? r.json() : null)
        .then(d => { if(d) setScreenerMeta(d); })
        .catch(() => {});
    }, []);

    // 추세 추종 Leading 스크리너
    const fetchTrendLeading = async () => {
      setTrendLoading(true);
      try {
        const res = await fetch(API('/api/signals/trend-candidates'));
        if (res.ok) setTrendStocks(await res.json());
      } catch(e) { console.error(e); }
      finally { setTrendLoading(false); }
    };

    const fetchValueCandidates = async () => {
      setValueLoading(true);
      try {
        const res = await fetch(API('/api/signals/value-candidates'));
        if (res.ok) setValueCandidates(await res.json());
      } catch(e) { console.error(e); }
      finally { setValueLoading(false); }
    };

    // 재무스크리너: 탭 진입 시 독립 fetch (캐시 우선)
    const fetchFinScreener = async () => {
      setFinLoading(true);
      try {
        // 서버 캐시 우선
        const res = await fetch(API('/api/signals/fin-screener'));
        if (res.ok) {
          const d = await res.json();
          if (d && d.length > 0) { setFinStocks(d); setFinLoading(false); return; }
        }
        // fallback: triple endpoint
        const res2 = await fetch(API('/api/dashboard/screening/triple'));
        if (res2.ok) setFinStocks(await res2.json());
      } catch(e) { console.error(e); }
      finally { setFinLoading(false); }
    };

    // combo: 서버 사전계산 캐시 우선, 없으면 클라이언트 교집합
    const fetchCombo = async () => {
      setComboLoading(true);
      try {
        const res = await fetch(API('/api/signals/combo-candidates'));
        if (res.ok) {
          const data = await res.json();
          if (data && data.length > 0) { setComboFromServer(data); setComboLoading(false); return; }
        }
      } catch(e) {}
      setComboLoading(false);
      // 서버 캐시 없으면 로컬 3종 데이터 모두 로드 (fin 누락 버그 수정)
      if (!trendStocks.length) fetchTrendLeading();
      if (!valueCandidates.length) fetchValueCandidates();
      if (!finStocks.length) fetchFinScreener();
    };

    // Logic-#2: 수급 주도 모멘텀
    const fetchComboV2 = async () => {
      setComboV2Loading(true);
      try {
        const res = await fetch(API('/api/signals/combo-v2'));
        if (res.ok) {
          const data = await res.json();
          if (data) setComboV2Data(data);
        }
      } catch(e) { console.error(e); }
      finally { setComboV2Loading(false); }
    };

    React.useEffect(() => {
      if (screenTab === 'trend' && !trendStocks.length) fetchTrendLeading();
      if (screenTab === 'value' && !valueCandidates.length) fetchValueCandidates();
      if (screenTab === 'ai' && !finStocks.length) fetchFinScreener();
      if (screenTab === 'combo') fetchCombo();
    }, [screenTab]);

    // 초기(combo) 로드
    React.useEffect(() => { fetchCombo(); }, []);

    // 서버 백그라운드 계산 완료 후 자동 재시도 (캐시 cold 상태 대응)
    React.useEffect(() => {
      if (comboFromServer.length > 0) return;
      const timer = setTimeout(async () => {
        try {
          const res = await fetch(API('/api/signals/combo-candidates'));
          if (res.ok) {
            const data = await res.json();
            if (data && data.length > 0) setComboFromServer(data);
          }
        } catch(e) {}
      }, 8000);
      return () => clearTimeout(timer);
    }, []);

    // combo: 서버 사전계산 우선, 없으면 클라이언트 교집합
    const comboStocks = React.useMemo(() => {
      // 서버에서 사전계산된 데이터가 있으면 우선 사용
      if (comboFromServer.length > 0) return comboFromServer;
      // 클라이언트 교집합 계산 (fallback)
      // O(1) lookup maps instead of O(n) .find() per code
      const trendMap2 = Object.fromEntries(trendStocks.map(s => [s.stock_code, s]));
      const valueMap2 = Object.fromEntries(valueCandidates.map(s => [s.stock_code, s]));
      const finMap2   = Object.fromEntries(finStocks.map(s => [s.stock_code, s]));
      const allCodes   = new Set([...Object.keys(trendMap2), ...Object.keys(valueMap2), ...Object.keys(finMap2)]);
      const result = [];
      for (const code of allCodes) {
        const trendS = trendMap2[code];
        const valueS = valueMap2[code];
        const finS   = finMap2[code];
        const cnt = (trendS?1:0) + (valueS?1:0) + (finS?1:0);
        if (cnt < 2) continue;
        const base   = trendS || valueS || finS;
        result.push({
          ...base,
          in_trend: !!trendS, in_value: !!valueS, in_fin: !!finS,
          match_count: cnt,
          trend_score: trendS?.score || 0,
          value_score: valueS?.score || 0,
          fin_score:   finS?.total_score || 0,
          combined_score: (trendS?.score||0)*2 + (valueS?.score||0)*2 + (finS?.total_score||0),
        });
      }
      result.sort((a,b) => b.match_count - a.match_count || b.combined_score - a.combined_score);
      return result;
    }, [comboFromServer, trendStocks, valueCandidates, finStocks]);

    // 진입트리거 TOP20 fetch
    const fetchTriggerRanking = async () => {
      setTriggerLoading(true);
      try {
        const res = await fetch(API('/api/signals/trigger-ranking'));
        if (res.ok) setTriggerData(await res.json());
      } catch(e) { console.error(e); }
      finally { setTriggerLoading(false); }
    };

    React.useEffect(() => {
      if (screenTab === 'trigger' && !triggerData) fetchTriggerRanking();
    }, [screenTab]);

    // CSV 다운로드 헬퍼
    const downloadCSV = (rows, filename) => {
      if (!rows || rows.length === 0) return;
      const BOM = '\uFEFF';
      const keys = Object.keys(rows[0]);
      const header = keys.join(',');
      const body = rows.map(r => keys.map(k => {
        const v = r[k];
        if (v == null) return '';
        const s = String(v);
        return s.includes(',') || s.includes('"') || s.includes('\n') ? `"${s.replace(/"/g,'""')}"` : s;
      }).join(',')).join('\n');
      const blob = new Blob([BOM + header + '\n' + body], { type: 'text/csv;charset=utf-8;' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = filename; a.click();
      URL.revokeObjectURL(url);
    };

    // ── 공통 로직 설명 패널 — signal_logic.py 에서 받은 items 렌더링 ──
    const LogicPanel = ({ metaKey, accentColor, fallbackTitle }) => {
      const meta = screenerMeta?.screeners?.[metaKey];
      const items = meta?.items;
      const color = accentColor || meta?.accent_color || '#f59e0b';
      const title = meta?.title || fallbackTitle || '로직 원리';
      if (!items || items.length === 0) return null;
      return (
        <div style={{marginTop:'0.5rem',padding:'1rem 1.2rem',
          background:`rgba(0,0,0,0.25)`,border:`1px solid ${color}25`,
          borderRadius:'10px',fontSize:'0.72rem',color:'rgba(255,255,255,0.6)',lineHeight:1.9}}>
          <div style={{fontWeight:700,color,marginBottom:'0.5rem',fontSize:'0.78rem'}}>
            📖 {title} — 로직 원리
          </div>
          <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(280px,1fr))',gap:'0.6rem 1.2rem'}}>
            {items.map(({title: t, desc}) => (
              <div key={t}>
                <span style={{color:`${color}cc`,fontWeight:600}}>{t}</span>
                <span style={{color:'rgba(255,255,255,0.5)'}}> — {desc}</span>
              </div>
            ))}
          </div>
        </div>
      );
    };

    const DlBtn = ({ onClick }) => (
      <button onClick={onClick} title="엑셀(CSV)로 다운로드" style={{
        padding:'0.2rem 0.55rem',borderRadius:'5px',fontSize:'0.7rem',cursor:'pointer',
        border:'1px solid rgba(45,212,191,0.3)',background:'rgba(45,212,191,0.08)',
        color:'var(--accent-mint)',display:'inline-flex',alignItems:'center',gap:'3px'
      }}>⬇ CSV</button>
    );

    // 시장구분 + 시총 배지 헬퍼
    const MktBadge = ({ market, mktcap }) => {
      const isKospi = market && market.includes('코스피');
      const isKosdaq = market && market.includes('코스닥');
      const mktColor = isKospi ? '#60a5fa' : isKosdaq ? '#34d399' : '#94a3b8';
      const mktLabel = isKospi ? 'KOSPI' : isKosdaq ? 'KOSDAQ' : (market || '');
      const capFmt = (v) => {
        if (!v || v <= 0) return null;
        // market_cap은 원(₩) 단위로 저장 → 억 단위로 변환 후 표시
        const uk = v >= 1e8 ? v / 1e8 : v;  // 이미 억 단위면 그대로, 원 단위면 변환
        if (uk >= 10000) return (uk / 10000).toFixed(1) + '조';
        return Math.round(uk).toLocaleString('ko-KR') + '억';
      };
      const capStr = capFmt(mktcap);
      if (!mktLabel && !capStr) return null;
      return (
        <span style={{display:'inline-flex',alignItems:'center',gap:'3px',
          padding:'0.1rem 0.45rem',borderRadius:'4px',fontSize:'0.65rem',
          background:`${mktColor}18`,color:mktColor,border:`1px solid ${mktColor}33`}}>
          {mktLabel}{capStr ? ` ${capStr}` : ''}
        </span>
      );
    };

    const tabBtn = (key, label, badge) => (
      <button key={key} onClick={() => setScreenTab(key)} style={{
        padding: '0.4rem 1.1rem', borderRadius: '8px', fontSize: '0.85rem',
        cursor: 'pointer', fontWeight: screenTab === key ? 700 : 400,
        border:      screenTab === key ? '1px solid var(--accent-mint)' : '1px solid var(--glass-border)',
        background:  screenTab === key ? 'rgba(45,212,191,0.15)' : 'transparent',
        color:       screenTab === key ? 'var(--accent-mint)' : 'var(--text-secondary)',
        position: 'relative',
      }}>
        {label}
        {badge > 0 && (
          <span style={{position:'absolute',top:'-5px',right:'-5px',
            background:'#ef4444',color:'#fff',borderRadius:'10px',
            fontSize:'0.6rem',padding:'0.05rem 0.3rem',fontWeight:700,lineHeight:1.4}}>
            {badge}
          </span>
        )}
      </button>
    );

    const SIG_COLOR = { green:'#22c55e', yellow:'#fbbf24', red:'#ef4444', gray:'#64748b' };
    const SIG_EMOJI = { green:'🟢', yellow:'🟡', red:'🔴', gray:'⚪' };

    return (
    <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>

      {/* 헤더 — sticky */}
      <div ref={stickyRef} className="glass-panel" style={{
        padding: '0.7rem 1.2rem', display: 'flex', flexDirection:'column',
        gap:'0.5rem',
        position: 'sticky', top: 0, zIndex: 50,
        backdropFilter: 'blur(20px)', borderRadius: '12px',
      }}>
        <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',flexWrap:'wrap',gap:'0.5rem'}}>
          <div className="section-title" style={{ marginBottom: 0 }}>
            <TrendingUp size={18} color="var(--accent-mint)" />
            <h2 style={{fontSize:'1rem'}}>AI 종목 스크리너</h2>
          </div>
          <div style={{display:'flex',gap:'0.4rem',flexWrap:'wrap'}}>
            {tabBtn('combo', `⭐ AI 적극 검토`, comboStocks.length)}
            {tabBtn('trigger', '🎯 진입트리거 TOP20')}
            {tabBtn('value', '💎 가치매수')}
            {tabBtn('ai',    '📊 재무 스크리너')}
            {tabBtn('trend', '📈 추세 Leading')}
          </div>
        </div>
        {/* 경고 배너 */}
        <div style={{padding:'0.4rem 0.8rem',background:'rgba(251,191,36,0.07)',
          border:'1px solid rgba(251,191,36,0.25)',borderRadius:'6px',
          fontSize:'0.7rem',color:'rgba(251,191,36,0.85)',lineHeight:1.4}}>
          ⚠️ AI가 판단한 각각의 주식투자 기법에 따라 필터링을 통과한 종목으로 검증되지 않았음을 안내 드립니다
        </div>
      </div>

      {/* ══ 진입트리거 TOP20 탭 ══ */}
      {screenTab === 'trigger' && (() => {
        const fmtAmt = (v) => {
          if(v == null) return <span style={{color:'rgba(255,255,255,0.2)'}}>-</span>;
          const c = v > 0 ? '#ef4444' : '#3b82f6';
          return <span style={{color:c,fontWeight:600,fontSize:'0.75rem'}}>{v>0?'+':''}{Math.round(Math.abs(v)).toLocaleString()}억</span>;
        };
        const fmtBal = (v) => {
          if(!v) return '-';
          const man = v / 10000;
          return man >= 1 ? man.toFixed(1)+'만' : Math.round(v).toLocaleString();
        };
        const ScorePill = ({score, max, color, label}) => {
          const pct = Math.min(score/max*100, 100);
          return (
            <div title={`${label}: ${score}/${max}`} style={{display:'flex',flexDirection:'column',alignItems:'center',gap:'1px',minWidth:'44px'}}>
              <span style={{fontSize:'0.6rem',color:'rgba(255,255,255,0.4)'}}>{label}</span>
              <div style={{width:'100%',height:'5px',borderRadius:'3px',background:'rgba(255,255,255,0.08)',overflow:'hidden'}}>
                <div style={{width:`${pct}%`,height:'100%',background:color,borderRadius:'3px'}}/>
              </div>
              <span style={{fontSize:'0.68rem',fontWeight:700,color}}>{score}<span style={{fontSize:'0.55rem',opacity:0.6}}>/{max}</span></span>
            </div>
          );
        };
        const BorCell = ({b5, b5p, b10, b30, b30p}) => {
          const lights = [
            {label:'5일', curr:b5,  prev:b30},
            {label:'10일',curr:b10, prev:b30},
            {label:'30일',curr:b30, prev:b30p},
          ];
          if(!lights.some(l=>l.curr)) return <span style={{color:'rgba(255,255,255,0.2)',fontSize:'0.7rem'}}>-</span>;
          return (
            <div style={{display:'flex',gap:'3px',justifyContent:'center'}}>
              {lights.map(({label,curr,prev})=>{
                const rising = (curr||0) > (prev||0)*1.02;
                const col = rising?'#ef4444':'#22c55e';
                return (
                  <div key={label} title={`${label}평균: ${Math.round(curr||0).toLocaleString()}주`}
                    style={{display:'flex',flexDirection:'column',alignItems:'center',padding:'2px 4px',
                      borderRadius:'4px',background:`${col}18`,border:`1px solid ${col}44`,minWidth:'36px'}}>
                    <span style={{fontSize:'0.52rem',color:'rgba(255,255,255,0.4)'}}>{label}</span>
                    <span style={{fontSize:'0.62rem',fontWeight:700,color:col}}>{fmtBal(curr)}</span>
                    <span style={{fontSize:'0.55rem',color:col}}>{rising?'▲':'▼'}</span>
                  </div>
                );
              })}
            </div>
          );
        };

        if(triggerLoading) return (
          <div className="glass-panel" style={{padding:'3rem',textAlign:'center',color:'var(--text-secondary)'}}>
            <div style={{width:'32px',height:'32px',borderRadius:'50%',border:'3px solid var(--accent-mint)',
              borderTopColor:'transparent',animation:'spin 0.8s linear infinite',margin:'0 auto 1rem'}}/>
            <p>진입트리거 TOP20 계산 중...</p>
          </div>
        );
        if(!triggerData) return (
          <div className="glass-panel" style={{padding:'2rem',textAlign:'center'}}>
            <button onClick={fetchTriggerRanking} style={{padding:'0.6rem 1.4rem',borderRadius:'8px',border:'none',
              background:'var(--accent-mint)',color:'#000',cursor:'pointer',fontWeight:700}}>
              🎯 진입트리거 TOP20 로드
            </button>
          </div>
        );
        const stocks = triggerData.stocks || [];
        const cachedAt = triggerData.cached_at ? new Date(triggerData.cached_at*1000).toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit'}) : '-';

        return (
          <div style={{display:'flex',flexDirection:'column',gap:'0.6rem'}}>
          <div className="glass-panel" style={{overflow:'auto'}}>
            <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',padding:'0.8rem 1rem 0.5rem',flexWrap:'wrap',gap:'0.5rem'}}>
              <div>
                <span style={{fontWeight:700,fontSize:'0.95rem'}}>🎯 3-트랙 종합 진입 TOP20</span>
                <span style={{fontSize:'0.72rem',color:'var(--text-secondary)',marginLeft:'0.8rem'}}>캐시: {cachedAt} (1시간 주기 갱신)</span>
              </div>
              <div style={{display:'flex',gap:'0.4rem'}}>
                <button onClick={fetchTriggerRanking} style={{padding:'0.25rem 0.7rem',borderRadius:'6px',border:'1px solid rgba(45,212,191,0.3)',
                  background:'rgba(45,212,191,0.08)',color:'var(--accent-mint)',cursor:'pointer',fontSize:'0.75rem'}}>
                  🔄 새로고침
                </button>
                <button onClick={async () => {
                  try {
                    await fetch('/api/commands/screener-refresh', {method:'POST'});
                    alert('재계산 시작 — 약 60~120초 후 새로고침 버튼을 눌러주세요.');
                  } catch(e) { alert('오류: ' + e.message); }
                }} style={{padding:'0.25rem 0.7rem',borderRadius:'6px',border:'1px solid rgba(245,158,11,0.4)',
                  background:'rgba(245,158,11,0.08)',color:'#f59e0b',cursor:'pointer',fontSize:'0.75rem'}}>
                  ⚡ 재계산
                </button>
              </div>
            </div>
            {/* 점수 범례 */}
            <div style={{padding:'0.3rem 1rem 0.6rem',display:'flex',gap:'1rem',flexWrap:'wrap',fontSize:'0.68rem',color:'rgba(255,255,255,0.45)'}}>
              <span>📈 Track A 추세 0~4점 (Minervini 완성=4)</span>
              <span>💎 Track B 가치 0~3점 (Graham 40%+ 할인=3)</span>
              <span>🌐 Track C 섹터 0~1점</span>
              <span style={{color:'rgba(45,212,191,0.7)'}}>● 종합점수 = A×2 + B×2 + C</span>
            </div>
            <table className="premium-table" style={{width:'100%',fontSize:'0.78rem'}}>
              <thead><tr>
                <th style={{minWidth:'30px',textAlign:'center'}}>#</th>
                <th style={{minWidth:'90px'}}>종목명</th>
                <th style={{textAlign:'center',minWidth:'60px'}}>시장</th>
                <th style={{textAlign:'right',minWidth:'75px'}}>현재가</th>
                <th style={{textAlign:'right',minWidth:'60px'}}>등락률</th>
                <th style={{textAlign:'right',minWidth:'70px'}}>시총(억)</th>
                <th style={{textAlign:'right',minWidth:'45px'}}>PBR</th>
                <th style={{textAlign:'right',minWidth:'45px'}}>PER</th>
                <th style={{textAlign:'center',minWidth:'100px'}}>당일수급(외/기)</th>
                <th style={{textAlign:'center',minWidth:'100px'}}>5일수급(외/기)</th>
                <th style={{textAlign:'center',minWidth:'120px'}}>대차잔고</th>
                <th style={{textAlign:'center',minWidth:'200px'}}>3-트랙 점수</th>
                <th style={{textAlign:'center',minWidth:'80px'}}>AI그룹</th>
                <th></th>
              </tr></thead>
              <tbody>
                {stocks.map((s,i) => {
                  const isAI = s.combo_count >= 2;
                  return (
                    <tr key={s.stock_code} style={{background: isAI ? 'rgba(45,212,191,0.04)' : undefined}}>
                      <td style={{textAlign:'center',color:'rgba(255,255,255,0.3)',fontSize:'0.72rem'}}>{i+1}</td>
                      <td>
                        <div style={{fontWeight:600}}>{s.stock_name}</div>
                        <div style={{fontSize:'0.68rem',color:'var(--text-secondary)'}}>{s.stock_code}</div>
                      </td>
                      <td style={{textAlign:'center'}}>
                        <span style={{padding:'1px 6px',borderRadius:'4px',fontSize:'0.65rem',fontWeight:700,
                          background: s.market?.includes('코스피') ? 'rgba(59,130,246,0.18)' : 'rgba(34,197,94,0.15)',
                          color:      s.market?.includes('코스피') ? '#60a5fa'                : '#4ade80'}}>
                          {s.market?.includes('코스피') ? 'KOSPI' : 'KOSDAQ'}
                        </span>
                      </td>
                      <td style={{textAlign:'right',fontWeight:600}}>{(s.price||0).toLocaleString()}</td>
                      <td style={{textAlign:'right',color: s.change_pct>=0?'#ef4444':'#3b82f6',fontWeight:600}}>
                        {s.change_pct>=0?'+':''}{s.change_pct?.toFixed(2)}%
                      </td>
                      <td style={{textAlign:'right',color:'var(--text-secondary)',fontSize:'0.72rem'}}>
                        {s.mktcap ? Math.round(s.mktcap/100000000).toLocaleString() : '-'}
                      </td>
                      <td style={{textAlign:'right',color: s.pbr&&s.pbr<1?'#4ade80':'var(--text-secondary)',fontSize:'0.75rem'}}>
                        {s.pbr ? s.pbr.toFixed(1)+'x' : '-'}
                      </td>
                      <td style={{textAlign:'right',color:'var(--text-secondary)',fontSize:'0.75rem'}}>
                        {s.per ? s.per.toFixed(1)+'x' : '-'}
                      </td>
                      <td style={{textAlign:'center'}}>
                        <div style={{display:'flex',flexDirection:'column',gap:'1px',alignItems:'center'}}>
                          <div style={{display:'flex',gap:'3px',alignItems:'center'}}>
                            <span style={{fontSize:'0.58rem',color:'rgba(255,255,255,0.3)'}}>외</span>{fmtAmt(s.frn_today)}
                          </div>
                          <div style={{display:'flex',gap:'3px',alignItems:'center'}}>
                            <span style={{fontSize:'0.58rem',color:'rgba(255,255,255,0.3)'}}>기</span>{fmtAmt(s.inst_today)}
                          </div>
                        </div>
                      </td>
                      <td style={{textAlign:'center'}}>
                        <div style={{display:'flex',flexDirection:'column',gap:'1px',alignItems:'center'}}>
                          <div style={{display:'flex',gap:'3px',alignItems:'center'}}>
                            <span style={{fontSize:'0.58rem',color:'rgba(255,255,255,0.3)'}}>외</span>{fmtAmt(s.frn_5d)}
                          </div>
                          <div style={{display:'flex',gap:'3px',alignItems:'center'}}>
                            <span style={{fontSize:'0.58rem',color:'rgba(255,255,255,0.3)'}}>기</span>{fmtAmt(s.inst_5d)}
                          </div>
                        </div>
                      </td>
                      <td>
                        <BorCell b5={s.bor_5d} b5p={s.bor_5d_prev} b10={s.bor_10d} b30={s.bor_30d} b30p={s.bor_30d_prev}/>
                      </td>
                      <td>
                        <div style={{display:'flex',gap:'6px',alignItems:'flex-end',justifyContent:'center'}}>
                          <ScorePill score={s.track_a||0} max={4} color='#f59e0b' label='추세'/>
                          <ScorePill score={s.track_b||0} max={3} color='#a78bfa' label='가치'/>
                          <ScorePill score={s.sector_bonus||0} max={1} color='#34d399' label='섹터'/>
                        </div>
                        <div style={{fontSize:'0.6rem',color:'rgba(255,255,255,0.35)',textAlign:'center',marginTop:'3px',maxWidth:'200px'}}>
                          {s.detail}
                        </div>
                        <div style={{fontSize:'0.58rem',color:'rgba(255,255,255,0.25)',textAlign:'center',marginTop:'1px'}}>
                          RSI {s.rsi} | 거래량{s.vol_ratio}x | 고점比{s.from_high}%
                        </div>
                      </td>
                      <td style={{textAlign:'center'}}>
                        <div style={{display:'flex',gap:'3px',justifyContent:'center',flexWrap:'wrap'}}>
                          {[
                            {key:'in_trend', label:'추세', color:'#f59e0b'},
                            {key:'in_value', label:'가치', color:'#a78bfa'},
                            {key:'in_fin',   label:'재무', color:'#38bdf8'},
                          ].map(({key,label,color})=>(
                            <span key={key} style={{padding:'1px 5px',borderRadius:'4px',fontSize:'0.6rem',fontWeight:700,
                              background: s[key] ? `${color}22` : 'rgba(255,255,255,0.04)',
                              color:      s[key] ? color        : 'rgba(255,255,255,0.15)',
                              border:`1px solid ${s[key]?color+'44':'rgba(255,255,255,0.06)'}`}}>
                              {label}
                            </span>
                          ))}
                        </div>
                        {isAI && <div style={{fontSize:'0.58rem',color:'var(--accent-mint)',marginTop:'2px',textAlign:'center'}}>★AI</div>}
                      </td>
                      <td>
                        <button onClick={()=>{changeStock(s.stock_code);changeTab('analysis');}}
                          style={{padding:'0.2rem 0.45rem',borderRadius:'4px',border:'none',
                            background:'rgba(45,212,191,0.12)',color:'var(--accent-mint)',
                            cursor:'pointer',fontSize:'0.7rem'}}>분석↗</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <div style={{padding:'0.8rem 1rem',fontSize:'0.68rem',color:'rgba(255,255,255,0.3)'}}>
              ★AI = Track A(추세) + Track B(가치) 동시 충족 OR 재무스크리너 포함 종목 → AI 적극검토 후보
            </div>
          </div>

          <LogicPanel metaKey="trigger" accentColor="#f59e0b" fallbackTitle="진입트리거 TOP20 선별 원리 — 3-트랙 독립 판정" />
        </div>
        );
      })()}

      {/* ══ 가치매수 후보 탭 ══ */}
      {screenTab === 'value' && (
        valueLoading ? (
          <div className="glass-panel" style={{padding:'3rem',textAlign:'center',color:'var(--text-secondary)'}}>
            <div style={{width:'32px',height:'32px',borderRadius:'50%',border:'3px solid var(--accent-mint)',
              borderTopColor:'transparent',animation:'spin 0.8s linear infinite',margin:'0 auto 1rem'}}/>
            <p>Graham 가치 스캔 중...</p>
          </div>
        ) : valueCandidates.length === 0 ? (
          <div className="glass-panel" style={{padding:'3rem',textAlign:'center',color:'var(--text-secondary)'}}>
            <p style={{fontSize:'2rem',marginBottom:'0.5rem'}}>💎</p>
            <p>현재 Graham 가치매수 조건을 충족하는 종목이 없습니다.</p>
            <p style={{fontSize:'0.8rem',marginTop:'0.4rem'}}>EPS·BPS 데이터가 있는 종목 중 저평가 조건을 스캔합니다.</p>
            <button onClick={fetchValueCandidates} style={{marginTop:'1rem',padding:'0.4rem 1rem',borderRadius:'8px',
              background:'rgba(245,158,11,0.15)',border:'1px solid rgba(245,158,11,0.3)',
              color:'#f59e0b',cursor:'pointer',fontSize:'0.8rem'}}>다시 스캔</button>
          </div>
        ) : (
          <div style={{display:'flex',flexDirection:'column',gap:'0.75rem'}}>
            {/* 안내 배너 */}
            <div style={{padding:'0.6rem 1rem',background:'rgba(245,158,11,0.08)',
              border:'1px solid rgba(245,158,11,0.25)',borderRadius:'8px',
              display:'flex',alignItems:'center',gap:'0.6rem',flexWrap:'wrap'}}>
              <span style={{fontSize:'0.75rem',color:'#f59e0b',fontWeight:600}}>
                💎 Graham 가치매수 후보 {valueCandidates.length}종목
              </span>
              <span style={{fontSize:'0.72rem',color:'rgba(255,255,255,0.5)'}}>
                조건: Graham 내재가치(√22.5×EPS×BPS) 대비 현재가 할인 15%+ OR (PBR&lt;1 AND PER&lt;15) — 종합점수 높은 순
              </span>
              <DlBtn onClick={() => downloadCSV(valueCandidates, 'value_candidates.csv')} />
              <button onClick={fetchValueCandidates} style={{marginLeft:'auto',padding:'0.25rem 0.7rem',
                borderRadius:'6px',background:'rgba(245,158,11,0.15)',border:'1px solid rgba(245,158,11,0.3)',
                color:'#f59e0b',cursor:'pointer',fontSize:'0.72rem'}}>새로고침</button>
            </div>

            {/* 종목 카드 목록 */}
            {valueCandidates.map(c => {
              const sc = SIG_COLOR[c.signal] || '#64748b';
              const se = SIG_EMOJI[c.signal] || '⚪';
              const isStrong = c.score >= 6;
              return (
                <div key={c.stock_code} style={{
                  padding:'1rem', borderRadius:'10px',
                  background: isStrong ? 'rgba(245,158,11,0.08)' : 'rgba(255,255,255,0.02)',
                  border: `1px solid ${isStrong ? 'rgba(245,158,11,0.35)' : 'rgba(255,255,255,0.08)'}`,
                }}>
                  {/* 종목 헤더 */}
                  <div style={{display:'flex',alignItems:'center',justifyContent:'space-between',marginBottom:'0.5rem',flexWrap:'wrap',gap:'0.4rem'}}>
                    <div style={{display:'flex',alignItems:'center',gap:'0.6rem'}}>
                      <span style={{fontSize:'1rem'}}>{se}</span>
                      <div>
                        <span style={{fontWeight:700,fontSize:'0.95rem'}}>{c.stock_name}</span>
                        <span style={{marginLeft:'0.5rem',fontSize:'0.72rem',color:'var(--text-secondary)'}}>
                          {c.stock_code}
                        </span>
                        {isStrong && (
                          <span style={{marginLeft:'0.5rem',padding:'0.1rem 0.5rem',borderRadius:'4px',
                            background:'rgba(245,158,11,0.2)',color:'#f59e0b',fontSize:'0.68rem',fontWeight:700}}>
                            강력매수
                          </span>
                        )}
                        <span style={{marginLeft:'0.4rem'}}><MktBadge market={c.market} mktcap={c.mktcap} /></span>
                      </div>
                    </div>
                    <div style={{display:'flex',alignItems:'center',gap:'0.5rem'}}>
                      {/* 점수 배지 */}
                      <div style={{padding:'0.2rem 0.6rem',borderRadius:'6px',
                        background:`rgba(245,158,11,${0.1 + c.score/9*0.3})`,
                        border:'1px solid rgba(245,158,11,0.3)'}}>
                        <span style={{fontSize:'0.72rem',color:'#f59e0b',fontWeight:700}}>
                          Score {c.score}/9
                        </span>
                      </div>
                      <button onClick={() => { changeStock(c.stock_code); changeTab('analysis'); }}
                        style={{padding:'0.3rem 0.7rem',borderRadius:'6px',border:'none',
                          background:'rgba(45,212,191,0.15)',color:'var(--accent-mint)',
                          cursor:'pointer',fontSize:'0.78rem'}}>
                        분석 보기
                      </button>
                    </div>
                  </div>

                  {/* 핵심 지표 */}
                  <div style={{display:'flex',gap:'0.75rem',flexWrap:'wrap',marginBottom:'0.5rem'}}>
                    {[
                      { label:'현재가', val: c.price ? c.price.toLocaleString('ko-KR')+'원' : '-' },
                      { label:'Graham 내재가', val: c.graham_iv ? c.graham_iv.toLocaleString('ko-KR')+'원' : '-', highlight: true },
                      { label:'Graham 할인율', val: c.discount != null ? c.discount.toFixed(1)+'%' : '-', highlight: true },
                      { label:'PBR', val: c.pbr != null ? c.pbr.toFixed(2) : '-' },
                      { label:'PER', val: c.per != null ? c.per.toFixed(1) : '-' },
                      { label:'EPS', val: c.eps ? c.eps.toLocaleString('ko-KR')+'원' : '-' },
                    ].map(({label, val, highlight}) => (
                      <div key={label} style={{textAlign:'center',minWidth:'70px',
                        padding:'0.3rem 0.5rem',borderRadius:'6px',
                        background: highlight ? 'rgba(245,158,11,0.1)' : 'rgba(255,255,255,0.04)',
                        border: `1px solid ${highlight ? 'rgba(245,158,11,0.25)' : 'rgba(255,255,255,0.07)'}`}}>
                        <div style={{fontSize:'0.6rem',color:'var(--text-secondary)',marginBottom:'0.1rem'}}>{label}</div>
                        <div style={{fontSize:'0.78rem',fontWeight:600,color: highlight ? '#f59e0b' : 'var(--text-primary)'}}>{val}</div>
                      </div>
                    ))}
                  </div>

                  {/* 매수 이유 */}
                  <div style={{padding:'0.4rem 0.7rem',background:'rgba(0,0,0,0.2)',borderRadius:'6px',
                    fontSize:'0.72rem',color:'rgba(255,255,255,0.7)',lineHeight:1.5}}>
                    {c.detail}
                  </div>
                </div>
              );
            })}

            <LogicPanel metaKey="value" accentColor="#f59e0b" fallbackTitle="Graham 가치투자 로직 원리" />
          </div>
        )
      )}

      {/* ══ AI 재무 스크리너 탭 ══ */}
      {screenTab === 'ai' && (() => {
        const gradeColor = (g) => g==='강력매수'?'#22c55e':g==='매수'?'#86efac':'#fbbf24';
        const opColor = (t) => t==='흑자전환'?'#22c55e':t==='적자개선'?'#fbbf24':t==='매출급증(적자)'?'#f59e0b':t==='이익 가속'?'#86efac':'rgba(255,255,255,0.5)';
        const fmtB = (v) => v == null ? '-' : Math.abs(v) >= 1e12 ? (v/1e12).toFixed(1)+'조' : Math.abs(v) >= 1e8 ? (v/1e8).toFixed(0)+'억' : (v/1e6).toFixed(0)+'백만';

        if (finLoading) return (
          <div className="glass-panel" style={{padding:'3rem',textAlign:'center',color:'var(--text-secondary)'}}>
            <div style={{width:'32px',height:'32px',borderRadius:'50%',border:'3px solid #a78bfa',
              borderTopColor:'transparent',animation:'spin 0.8s linear infinite',margin:'0 auto 1rem'}}/>
            <p>재무 스크리닝 중...</p>
          </div>
        );

        return finStocks.length === 0 ? (
          <div className="glass-panel" style={{ padding: '3rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
            <TrendingUp size={40} style={{ margin: '0 auto 1rem', display: 'block', opacity: 0.3 }} />
            <p>스크리닝 통과 종목이 없습니다.</p>
            <p style={{ fontSize: '0.8rem', marginTop: '0.4rem' }}>데이터를 불러오는 중이거나 조건에 맞는 종목이 없습니다.</p>
            <button onClick={fetchFinScreener} style={{marginTop:'1rem',padding:'0.4rem 1rem',borderRadius:'8px',
              background:'rgba(45,212,191,0.15)',border:'1px solid rgba(45,212,191,0.3)',
              color:'var(--accent-mint)',cursor:'pointer'}}>스크린 실행</button>
          </div>
        ) : (
          <div style={{display:'flex',flexDirection:'column',gap:'0.6rem'}}>
            {/* 헤더 배너 */}
            <div style={{padding:'0.6rem 1rem',background:'rgba(45,212,191,0.05)',
              border:'1px solid rgba(45,212,191,0.2)',borderRadius:'8px',
              display:'flex',alignItems:'center',gap:'0.6rem',flexWrap:'wrap'}}>
              <span style={{fontSize:'0.75rem',color:'var(--accent-mint)',fontWeight:700}}>
                🔍 {screenerMeta?.screeners?.ai?.title || '소외 턴어라운드 + 성장 기울기 스크리너'}
              </span>
              <span style={{padding:'0.1rem 0.5rem',background:'rgba(34,197,94,0.15)',
                borderRadius:'20px',fontSize:'0.7rem',color:'#22c55e'}}>
                {finStocks.length}종목 발굴
              </span>
              <span style={{fontSize:'0.68rem',color:'rgba(255,255,255,0.4)'}}>
                {screenerMeta?.screeners?.ai?.subtitle || '8축 점수 — 소외도·매출기울기·낙폭·턴어라운드·장기추세·섹터소외·거시기회·실적가속'}
              </span>
              <DlBtn onClick={() => downloadCSV(finStocks, 'fin_screener.csv')} />
              <button onClick={() => setShowFinLogic(v => !v)}
                style={{padding:'0.2rem 0.6rem',borderRadius:'5px',border:'1px solid rgba(245,158,11,0.3)',
                  background:'rgba(245,158,11,0.08)',color:'#f59e0b',cursor:'pointer',fontSize:'0.7rem'}}>
                {showFinLogic ? '로직 접기 ▲' : '📖 로직 원리 보기 ▼'}
              </button>
              <button onClick={fetchFinScreener} style={{marginLeft:'auto',padding:'0.2rem 0.6rem',borderRadius:'5px',
                border:'1px solid rgba(45,212,191,0.3)',background:'rgba(45,212,191,0.1)',
                color:'var(--accent-mint)',cursor:'pointer',fontSize:'0.7rem'}}>새로고침</button>
            </div>

            {/* 로직 원리 패널 */}
            {showFinLogic && (
              <LogicPanel metaKey="ai" accentColor="#a78bfa" fallbackTitle="8축 소외 턴어라운드 + 성장 기울기 전략" />
            )}

            {/* 종목 카드 목록 */}
            {finStocks.map(s => {
              const gc = gradeColor(s.grade);
              const isStrong = s.grade === '강력매수';
              const isBuy    = s.grade === '매수';
              return (
                <div key={s.stock_code} style={{
                  padding:'0.9rem 1rem',borderRadius:'10px',
                  background: isStrong ? 'rgba(34,197,94,0.06)' : isBuy ? 'rgba(134,239,172,0.03)' : 'rgba(255,255,255,0.02)',
                  border:`1px solid ${isStrong ? 'rgba(34,197,94,0.3)' : isBuy ? 'rgba(134,239,172,0.15)' : 'rgba(255,255,255,0.08)'}`,
                }}>
                  {/* 헤더 */}
                  <div style={{display:'flex',alignItems:'center',gap:'0.5rem',marginBottom:'0.5rem',flexWrap:'wrap'}}>
                    {s.topdown_combo && (
                      <span style={{padding:'0.1rem 0.5rem',borderRadius:'4px',fontSize:'0.7rem',fontWeight:700,
                        background:'rgba(251,191,36,0.15)',color:'#fbbf24',border:'1px solid rgba(251,191,36,0.4)'}}>
                        👑 TopDown콤보
                      </span>
                    )}
                    <span style={{padding:'0.1rem 0.5rem',borderRadius:'4px',fontSize:'0.72rem',fontWeight:700,
                      background:`${gc}22`,color:gc,border:`1px solid ${gc}44`}}>
                      {s.grade}
                    </span>
                    <span style={{fontWeight:700,fontSize:'0.92rem'}}>{s.stock_name}</span>
                    <span style={{fontSize:'0.7rem',color:'var(--text-secondary)'}}>{s.stock_code}</span>
                    {s.sector && (
                      <span style={{padding:'0.1rem 0.45rem',borderRadius:'4px',fontSize:'0.67rem',
                        background:'rgba(255,255,255,0.05)',color:'rgba(255,255,255,0.45)',
                        border:'1px solid rgba(255,255,255,0.08)'}}>
                        {s.sector}{s.sector_mid ? ' › '+s.sector_mid : ''}
                      </span>
                    )}
                    {s.op_trend && s.op_trend !== '유지' && (
                      <span style={{padding:'0.1rem 0.45rem',borderRadius:'4px',fontSize:'0.67rem',fontWeight:700,
                        background:`${opColor(s.op_trend)}18`,color:opColor(s.op_trend),
                        border:`1px solid ${opColor(s.op_trend)}33`}}>
                        {s.op_trend}
                      </span>
                    )}
                    {s.smart_money && (
                      <span style={{padding:'0.1rem 0.45rem',borderRadius:'4px',fontSize:'0.67rem',fontWeight:700,
                        background:'rgba(52,211,153,0.12)',color:'#34d399',border:'1px solid rgba(52,211,153,0.3)'}}>
                        🎯 스마트머니
                      </span>
                    )}
                    <MktBadge market={s.market} mktcap={s.mktcap} />
                    <div style={{marginLeft:'auto',display:'flex',alignItems:'center',gap:'0.4rem'}}>
                      <span style={{fontSize:'0.7rem',color:'rgba(255,255,255,0.4)',fontWeight:600}}>
                        {s.total_score}점
                      </span>
                      {/* 8축 + 4필터 미니 점수바 */}
                      <div style={{display:'flex',gap:'2px',alignItems:'center'}}>
                        {[
                          {k:'score_neglect',    label:'소외', color:'#a78bfa'},
                          {k:'score_growth',     label:'성장', color:'#22c55e'},
                          {k:'score_oversold',   label:'낙폭', color:'#f59e0b'},
                          {k:'score_turnaround', label:'전환', color:'#f87171'},
                          {k:'score_longtrend',  label:'장기', color:'#60a5fa'},
                          {k:'score_sector',     label:'섹터', color:'#fbbf24'},
                          {k:'score_macro',      label:'거시', color:'#fb923c'},
                          {k:'score_accel',      label:'가속', color:'#34d399'},
                          {k:'score_ocf',        label:'OCF',  color:'#2dd4bf'},
                          {k:'score_moat',       label:'해자', color:'#818cf8'},
                          {k:'score_catalyst',   label:'촉매', color:'#f472b6'},
                          {k:'score_safety',     label:'안전', color:'#facc15'},
                        ].map(({k, label, color}) => (
                          <div key={k} title={`${label}: ${s[k]||0}`}
                            style={{width:'13px',height:'13px',borderRadius:'2px',
                              background: (s[k]||0) >= 3 ? color : (s[k]||0) >= 2 ? color+'88' : (s[k]||0) >= 1 ? color+'44' : 'rgba(255,255,255,0.08)',
                              border:`1px solid ${color}33`}} />
                        ))}
                      </div>
                      <button onClick={() => { changeStock(s.stock_code); changeTab('analysis'); }}
                        style={{padding:'0.2rem 0.55rem',borderRadius:'5px',border:'none',
                          background:'rgba(45,212,191,0.15)',color:'var(--accent-mint)',
                          cursor:'pointer',fontSize:'0.72rem'}}>
                        분석
                      </button>
                    </div>
                  </div>

                  {/* 핵심 지표 */}
                  <div style={{display:'flex',gap:'0.45rem',flexWrap:'wrap',marginBottom:'0.45rem'}}>
                    {[
                      {label:'현재가',    val: s.price ? s.price.toLocaleString('ko-KR')+'원' : '-'},
                      {label:'고점대비',  val: s.drawdown_pct != null ? s.drawdown_pct+'%' : '-',
                        color: s.drawdown_pct <= -40 ? '#f87171' : s.drawdown_pct <= -25 ? '#fbbf24' : 'rgba(255,255,255,0.6)'},
                      {label:'저점반등',  val: s.from_low_pct != null ? '+'+s.from_low_pct+'%' : '-',
                        color: s.from_low_pct >= 15 ? '#22c55e' : 'rgba(255,255,255,0.6)'},
                      {label:'매출기울기',val: s.revenue_slope_pct != null ? (s.revenue_slope_pct > 0 ? '+' : '')+s.revenue_slope_pct+'%' : '-',
                        color: s.revenue_slope_pct > 15 ? '#22c55e' : s.revenue_slope_pct > 5 ? '#fbbf24' : 'rgba(255,255,255,0.5)'},
                      {label:'매출YoY',  val: s.revenue_yoy_pct != null ? (s.revenue_yoy_pct > 0 ? '+' : '')+s.revenue_yoy_pct+'%' : '-',
                        color: s.revenue_yoy_pct > 30 ? '#22c55e' : s.revenue_yoy_pct > 0 ? '#86efac' : '#f87171'},
                      {label:'최신매출',  val: fmtB(s.latest_revenue)},
                      {label:'영업이익',  val: fmtB(s.latest_profit),
                        color: s.latest_profit > 0 ? '#22c55e' : '#f87171'},
                      {label:'PBR',      val: s.pbr != null ? s.pbr.toFixed(2) : '-',
                        color: s.pbr != null && s.pbr < 1 ? '#f59e0b' : 'rgba(255,255,255,0.6)'},
                      {label:'PER',      val: s.per != null ? s.per.toFixed(1) : '-',
                        color: s.per != null && s.per < 10 ? '#f59e0b' : 'rgba(255,255,255,0.6)'},
                      {label:'Fwd PER',  val: s.forward_per != null ? s.forward_per+'x' : '-',
                        color: s.forward_per != null && s.forward_per < 10 ? '#2dd4bf' : 'rgba(255,255,255,0.5)',
                        title:'선행PER(Forward EPS 추정)'},
                      {label:'PSR',      val: s.psr != null ? s.psr.toFixed(2) : '-',
                        color: s.psr != null && s.psr < 0.5 ? '#facc15' : s.psr != null && s.psr < 1 ? '#fbbf24' : 'rgba(255,255,255,0.5)',
                        title:'주가매출비율(낮을수록 매출 대비 싼 주가)'},
                      {label:'ROE 3y',   val: s.avg_roe_3y != null ? s.avg_roe_3y+'%' : '-',
                        color: s.avg_roe_3y != null && s.avg_roe_3y >= 15 ? '#818cf8' : s.avg_roe_3y != null && s.avg_roe_3y >= 8 ? '#a5b4fc' : 'rgba(255,255,255,0.4)',
                        title:'3년 평균 자기자본이익률(해자 지표)'},
                      {label:'OCF',      val: s.ocf_positive != null ? (s.ocf_positive ? '양(+)' : '음(-)') : '-',
                        color: s.ocf_positive ? '#2dd4bf' : '#f87171',
                        title:'영업활동현금흐름 양/음'},
                      {label:'RS 3M',    val: s.rs3m != null ? (s.rs3m > 0 ? '+' : '')+s.rs3m+'%p' : '-',
                        color: s.rs3m != null && s.rs3m < -10 ? '#fb923c' : s.rs3m != null && s.rs3m > 0 ? '#22c55e' : 'rgba(255,255,255,0.5)'},
                    ].map(({label, val, color, title}) => (
                      <div key={label} title={title||label} style={{textAlign:'center',minWidth:'58px',
                        padding:'0.22rem 0.4rem',borderRadius:'5px',
                        background:'rgba(255,255,255,0.04)',border:'1px solid rgba(255,255,255,0.07)'}}>
                        <div style={{fontSize:'0.57rem',color:'var(--text-secondary)',marginBottom:'0.08rem'}}>{label}</div>
                        <div style={{fontSize:'0.74rem',fontWeight:600,color: color || 'var(--text-primary)'}}>{val}</div>
                      </div>
                    ))}
                  </div>

                  {/* 발굴 태그 */}
                  <div style={{display:'flex',gap:'0.3rem',flexWrap:'wrap'}}>
                    {(s.tags || []).map((t, i) => (
                      <span key={i} style={{padding:'0.15rem 0.45rem',borderRadius:'4px',fontSize:'0.67rem',
                        background:'rgba(255,255,255,0.05)',color:'rgba(255,255,255,0.65)',
                        border:'1px solid rgba(255,255,255,0.08)'}}>
                        {t}
                      </span>
                    ))}
                  </div>
                </div>
              );
            })}

            {/* ── 재무 스크리너 로직 설명 (하단) ── */}
            <div style={{marginTop:'0.5rem',padding:'1rem 1.2rem',
              background:'rgba(139,92,246,0.03)',border:'1px solid rgba(139,92,246,0.12)',
              borderRadius:'10px',fontSize:'0.72rem',color:'rgba(255,255,255,0.6)',lineHeight:1.9}}>
              <div style={{fontWeight:700,color:'#a78bfa',marginBottom:'0.5rem',fontSize:'0.78rem'}}>
                📊 재무 스크리너 원리 — 소외 턴어라운드 + 성장 기울기 전략 (8축 최대 32점)
              </div>
              <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(280px,1fr))',gap:'0.6rem 1.2rem'}}>
                {[
                  ['① 주가 소외도','PBR<0.5(+2), PBR<1(+1), PER<6(+2), PER<12(+1), 소형주(+1). 낮을수록 시장이 외면 중 → 실적 반전 시 "재발견" 급등 기대.'],
                  ['② 매출 성장 기울기','선형회귀 기울기 >20%/분기(+2), >8%(+1) + YoY >50%(+2), >20%(+1). 주가보다 매출이 먼저 달라진다. 속도와 가속도가 핵심 선행지표.'],
                  ['③ 낙폭 과다','52주 고점 대비 -60%(+3), -40%(+2), -25%(+1) + 저점 반등 +15%(+1). 과도 하락 + 바닥 확인 = 턴어라운드 진입 구간.'],
                  ['④ 턴어라운드','과거 적자→최신 흑자 전환(+4 MAX). 적자 3분기 연속 개선(+2). 매출 급증 중 적자(+1). 시장이 가장 늦게 인식하는 구간.'],
                  ['⑤ 장기 실적 추세','연간 매출 기울기 >15%/년(+2), 성장 가속화(+1), 연간 흑자전환(+1). 분기 노이즈 제거 후 구조적 개선 여부 확인.'],
                  ['⑥ 섹터 소외','섹터 리더 52주선 위인데 개별주 -30%(+3). 섹터 순환 시 소외주에 탄력. 리더 먼저, 졸개 나중 패턴.'],
                  ['⑦ 거시 낙폭 기회','KOSPI 대비 RS -15%p(+3), -8%(+2), -3%(+1). 전쟁·금리 등 거시 폭락 시 실적 종목은 부당하게 빠짐 → 최고의 진입 기회.'],
                  ['⑧ 실적 가속','영업이익 3분기 연속 확대(+2), 레버리지 발현(+2). 고정비 커버 후 추가 매출 전액 이익 → EPS 폭발 구간.'],
                ].map(([title, desc]) => (
                  <div key={title}>
                    <span style={{color:'rgba(139,92,246,0.8)',fontWeight:600}}>{title}</span>
                    <span style={{color:'rgba(255,255,255,0.5)'}}> — {desc}</span>
                  </div>
                ))}
              </div>
              <div style={{marginTop:'0.6rem',paddingTop:'0.6rem',borderTop:'1px solid rgba(139,92,246,0.1)',
                color:'rgba(255,255,255,0.35)',fontSize:'0.67rem'}}>
                [등급] 22점↑ 강력매수 | 16점↑ 매수 | 12점↑ 관심 &nbsp;·&nbsp;
                미니 색상 바(헤더 우측 8칸): 보라=소외 / 초록=성장 / 주황=낙폭 / 빨강=전환 / 파랑=장기 / 노랑=섹터 / 주황=거시 / 민트=가속
              </div>
            </div>
          </div>
        );
      })()}

      {/* ══ 추세 추종 Leading 탭 ══ */}
      {screenTab === 'trend' && (
        trendLoading ? (
          <div className="glass-panel" style={{padding:'3rem',textAlign:'center',color:'var(--text-secondary)'}}>
            <div style={{width:'32px',height:'32px',borderRadius:'50%',border:'3px solid var(--accent-mint)',
              borderTopColor:'transparent',animation:'spin 0.8s linear infinite',margin:'0 auto 1rem'}}/>
            <p>추세 스캔 중... (전 종목 스캔, 수십 초 소요)</p>
          </div>
        ) : trendStocks.length === 0 ? (
          <div className="glass-panel" style={{padding:'3rem',textAlign:'center',color:'var(--text-secondary)'}}>
            <p style={{fontSize:'2rem',marginBottom:'0.5rem'}}>📈</p>
            <p>오늘은 3단계 조건을 모두 충족하는 돌파 종목이 없습니다.</p>
            <p style={{fontSize:'0.78rem',marginTop:'0.4rem',color:'rgba(255,255,255,0.4)'}}>
              거래량 2배↑ + BB 상단 돌파는 실제 돌파일에만 발생합니다.<br/>
              시장이 약하거나 박스권 구간에서는 결과가 없는 것이 정상입니다.
            </p>
            <button onClick={fetchTrendLeading} style={{marginTop:'1rem',padding:'0.4rem 1rem',borderRadius:'8px',
              background:'rgba(45,212,191,0.15)',border:'1px solid rgba(45,212,191,0.3)',
              color:'var(--accent-mint)',cursor:'pointer',fontSize:'0.8rem'}}>다시 스캔</button>
          </div>
        ) : (
          <div style={{display:'flex',flexDirection:'column',gap:'0.6rem'}}>
            {/* 안내 배너 */}
            <div style={{padding:'0.6rem 1rem',background:'rgba(45,212,191,0.06)',
              border:'1px solid rgba(45,212,191,0.2)',borderRadius:'8px',
              display:'flex',alignItems:'center',gap:'0.6rem',flexWrap:'wrap'}}>
              <span style={{fontSize:'0.75rem',color:'var(--accent-mint)',fontWeight:600}}>
                📈 {screenerMeta?.screeners?.trend?.title || '미너비니 3단계'} — 오늘의 주도주 {trendStocks.length}종목 (최대 20개)
              </span>
              <span style={{fontSize:'0.72rem',color:'rgba(255,255,255,0.5)'}}>
                {screenerMeta?.screeners?.trend?.subtitle || 'MA120/200 정배열 × RSI60↑ × 거래량2배↑'}
              </span>
              <DlBtn onClick={() => downloadCSV(trendStocks, 'trend_leading.csv')} />
              <button onClick={fetchTrendLeading} style={{marginLeft:'auto',padding:'0.25rem 0.7rem',
                borderRadius:'6px',background:'rgba(45,212,191,0.12)',border:'1px solid rgba(45,212,191,0.25)',
                color:'var(--accent-mint)',cursor:'pointer',fontSize:'0.72rem'}}>새로고침</button>
            </div>

            {/* 200주 개념 설명 */}
            {(() => {
              // 현재 종목들의 weekly_label 에서 최대 주봉 수 파악
              const weekNums = trendStocks
                .map(s => { const m = (s.weekly_label||'').match(/(\d+)주선/); return m ? parseInt(m[1]) : 0; })
                .filter(n => n > 0);
              const maxWeeks = weekNums.length > 0 ? Math.max(...weekNums) : 0;
              const is200w   = maxWeeks >= 200;
              return (
                <div style={{padding:'0.5rem 0.8rem',background: is200w ? 'rgba(34,197,94,0.05)' : 'rgba(245,158,11,0.05)',
                  border:`1px solid ${is200w ? 'rgba(34,197,94,0.2)' : 'rgba(245,158,11,0.15)'}`,borderRadius:'6px',fontSize:'0.7rem',
                  color:'rgba(255,255,255,0.55)',lineHeight:1.6}}>
                  {is200w
                    ? <>✅ <strong style={{color:'rgba(34,197,94,0.9)'}}>주봉 장기이평선</strong> — <strong>{maxWeeks}주(약{Math.round(maxWeeks/52)}년)선</strong> 적용 중.
                       섹터 시총 상위주들이 장기 주봉선 위에 있을 때만 그 섹터 개별주에 진입. 섹터가 침묵 중이면 FOMO 주의.</>
                    : <>⚡ <strong style={{color:'rgba(245,158,11,0.8)'}}>주봉 장기이평선</strong> — 200주(5년)선이 목표이며 현재 DB 기준 <strong>{maxWeeks > 0 ? `${maxWeeks}주선` : '52주선'}</strong> 적용 중.
                       역대 데이터 수집 중이며 완료 시 200주선으로 자동 전환됩니다. 섹터가 침묵 중이면 FOMO 주의.</>
                  }
                </div>
              );
            })()}

            {/* 종목 카드 그리드 */}
            <div style={{display:'flex',flexDirection:'column',gap:'0.5rem'}}>
              {trendStocks.map(s => {
                const isStrong = s.label === '강력매수';
                const isBuy    = s.label === '매수';
                const sc = isStrong ? '#22c55e' : isBuy ? '#86efac' : '#fbbf24';
                const sectorActive = s.sector_act >= 0.6;
                const sectorPart   = s.sector_act >= 0.4 && !sectorActive;
                return (
                  <div key={s.stock_code} style={{
                    padding:'0.85rem 1rem',borderRadius:'10px',
                    background: isStrong ? 'rgba(34,197,94,0.06)' : 'rgba(255,255,255,0.02)',
                    border:`1px solid ${isStrong ? 'rgba(34,197,94,0.3)' : isBuy ? 'rgba(134,239,172,0.2)' : 'rgba(255,255,255,0.08)'}`,
                  }}>
                    {/* 헤더 행 */}
                    <div style={{display:'flex',alignItems:'center',gap:'0.5rem',marginBottom:'0.45rem',flexWrap:'wrap'}}>
                      <span style={{padding:'0.1rem 0.5rem',borderRadius:'4px',fontSize:'0.72rem',
                        fontWeight:700,background:`${sc}22`,color:sc,border:`1px solid ${sc}55`}}>
                        {s.label}
                      </span>
                      <span style={{fontWeight:700,fontSize:'0.92rem'}}>{s.stock_name}</span>
                      <span style={{fontSize:'0.7rem',color:'var(--text-secondary)'}}>
                        {s.stock_code}
                      </span>
                      <MktBadge market={s.market} mktcap={s.mktcap} />
                      {/* 섹터 배지 */}
                      {s.sector && (
                        <span style={{marginLeft:'0.2rem',padding:'0.1rem 0.5rem',borderRadius:'4px',fontSize:'0.68rem',
                          background: sectorActive ? 'rgba(245,158,11,0.2)' : sectorPart ? 'rgba(245,158,11,0.1)' : 'rgba(255,255,255,0.05)',
                          color: sectorActive ? '#f59e0b' : sectorPart ? 'rgba(245,158,11,0.7)' : 'rgba(255,255,255,0.4)',
                          border:`1px solid ${sectorActive ? 'rgba(245,158,11,0.4)' : 'rgba(255,255,255,0.1)'}`}}>
                          {sectorActive ? '🔥' : sectorPart ? '⚡' : ''} {s.sector}{s.sector_mid ? ' › '+s.sector_mid : ''}
                        </span>
                      )}
                      {/* 점수 + 분석 버튼 */}
                      <div style={{marginLeft:'auto',display:'flex',alignItems:'center',gap:'0.4rem'}}>
                        <span style={{padding:'0.1rem 0.5rem',borderRadius:'4px',fontSize:'0.72rem',
                          background:'rgba(45,212,191,0.12)',color:'var(--accent-mint)',fontWeight:700}}>
                          {s.score}점
                        </span>
                        <button onClick={() => { changeStock(s.stock_code); changeTab('analysis'); }}
                          style={{padding:'0.2rem 0.55rem',borderRadius:'5px',border:'none',
                            background:'rgba(45,212,191,0.15)',color:'var(--accent-mint)',
                            cursor:'pointer',fontSize:'0.72rem'}}>
                          분석
                        </button>
                      </div>
                    </div>

                    {/* 핵심 지표 행 */}
                    <div style={{display:'flex',gap:'0.5rem',flexWrap:'wrap',marginBottom:'0.4rem'}}>
                      {[
                        {label:'현재가', val: s.price ? s.price.toLocaleString('ko-KR')+'원' : '-'},
                        {label:'MA20', val: s.ma20 ? s.ma20.toLocaleString('ko-KR') : '-'},
                        {label:'MA60', val: s.ma60 ? s.ma60.toLocaleString('ko-KR') : '-'},
                        {label:'고점대비', val: s.from_high != null ? (s.from_high >= 0 ? '+' : '')+s.from_high+'%' : '-',
                          color: s.from_high >= -5 ? '#22c55e' : s.from_high >= -15 ? '#fbbf24' : 'rgba(255,255,255,0.5)'},
                        {label:'섹터활성', val: s.sector_act != null ? (s.sector_act*100).toFixed(0)+'%' : '-',
                          color: sectorActive ? '#f59e0b' : sectorPart ? 'rgba(245,158,11,0.7)' : 'rgba(255,255,255,0.4)'},
                      ].map(({label,val,color}) => (
                        <div key={label} style={{textAlign:'center',minWidth:'62px',
                          padding:'0.25rem 0.4rem',borderRadius:'5px',
                          background:'rgba(255,255,255,0.04)',border:'1px solid rgba(255,255,255,0.07)'}}>
                          <div style={{fontSize:'0.58rem',color:'var(--text-secondary)',marginBottom:'0.1rem'}}>{label}</div>
                          <div style={{fontSize:'0.75rem',fontWeight:600,color: color || 'var(--text-primary)'}}>{val}</div>
                        </div>
                      ))}
                    </div>

                    {/* 매수 근거 태그들 */}
                    <div style={{display:'flex',gap:'0.3rem',flexWrap:'wrap'}}>
                      {(s.reasons || []).map((r,i) => (
                        <span key={i} style={{padding:'0.15rem 0.45rem',borderRadius:'4px',fontSize:'0.67rem',
                          background:'rgba(255,255,255,0.05)',color:'rgba(255,255,255,0.65)',
                          border:'1px solid rgba(255,255,255,0.08)'}}>
                          {r}
                        </span>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>

            <LogicPanel metaKey="trend" accentColor="#2dd4bf" fallbackTitle="추세추종 로직 원리 — 미너비니(Minervini) 3단계 필터" />
          </div>
        )
      )}

      {/* ══ AI 적극 검토 탭 ══ */}
      {screenTab === 'combo' && (() => {
        const filteredCombo = comboFilter === 'triple'
          ? comboStocks.filter(s => s.match_count >= 3)
          : comboStocks;
        const sigColor = { '강력추천':'#ef4444', '추천':'#f59e0b', '관심':'#22c55e' };
        const sigEmoji = { '강력추천':'🔥', '추천':'⭐', '관심':'👀' };
        return (
        <div style={{display:'flex',flexDirection:'column',gap:'0.6rem'}}>
          {/* 로직 선택 드랍다운 */}
          <div style={{display:'flex',alignItems:'center',gap:'0.75rem',padding:'0.55rem 1rem',
            background:'rgba(0,0,0,0.3)',border:'1px solid rgba(255,255,255,0.08)',
            borderRadius:'10px',flexWrap:'wrap'}}>
            <span style={{fontSize:'0.78rem',color:'rgba(255,255,255,0.55)',fontWeight:600}}>📐 로직 선택:</span>
            <div style={{display:'flex',gap:'0.3rem',background:'rgba(0,0,0,0.2)',borderRadius:'8px',padding:'0.2rem'}}>
              {[
                { key:'v1', label:'Logic-#1', desc:'Minervini 추세 + Graham 가치 복합 스크리너' },
                { key:'v2', label:'Logic-#2', desc:'수급 주도 모멘텀 (기관+외국인 동반매수)' },
              ].map(opt => (
                <button key={opt.key} title={opt.desc} onClick={() => {
                    setComboLogic(opt.key);
                    if (opt.key === 'v2' && comboV2Data.length === 0) fetchComboV2();
                  }}
                  style={{padding:'0.25rem 0.9rem',borderRadius:'6px',fontSize:'0.78rem',cursor:'pointer',
                    fontWeight: comboLogic===opt.key ? 700 : 400, border:'none',
                    background: comboLogic===opt.key
                      ? (opt.key==='v2' ? 'rgba(99,102,241,0.4)' : 'rgba(239,68,68,0.35)')
                      : 'transparent',
                    color: comboLogic===opt.key ? '#fff' : 'rgba(255,255,255,0.45)',
                    transition:'all 0.15s'}}>
                  {opt.label}
                </button>
              ))}
            </div>
            <span style={{fontSize:'0.7rem',color:'rgba(255,255,255,0.35)'}}>
              {comboLogic==='v1'
                ? 'Minervini 추세 + Graham 가치 + 재무스크리너 교집합'
                : '수급 주도 모멘텀 — 기관·외국인 동반순매수 + 추세 + 실적'}
            </span>
          </div>

          {/* ── Logic-#1 렌더링 ── */}
          {comboLogic === 'v1' && (<>
          {/* 안내 배너 */}
          <div style={{padding:'0.7rem 1rem',
            background:'linear-gradient(135deg, rgba(239,68,68,0.08), rgba(245,158,11,0.08))',
            border:'1px solid rgba(239,68,68,0.3)',borderRadius:'8px',
            display:'flex',alignItems:'center',gap:'0.6rem',flexWrap:'wrap'}}>
            <span style={{fontSize:'0.8rem',color:'#ef4444',fontWeight:700}}>
              ⭐ Logic-#1 AI 적극 검토 — {filteredCombo.length}종목
            </span>
            {/* 필터 선택 버튼 */}
            <div style={{display:'flex',gap:'0.3rem',background:'rgba(0,0,0,0.25)',
              borderRadius:'8px',padding:'0.2rem'}}>
              {[
                { key:'all',    label:'2개↑ 충족', desc:'추세·가치·재무 중 2개 이상' },
                { key:'triple', label:'🏆 3개 모두', desc:'추세·가치·재무 모두 충족' },
              ].map(opt => (
                <button key={opt.key} onClick={() => setComboFilter(opt.key)} title={opt.desc}
                  style={{padding:'0.2rem 0.65rem',borderRadius:'6px',fontSize:'0.72rem',
                    cursor:'pointer',fontWeight: comboFilter===opt.key ? 700 : 400,
                    border:'none',
                    background: comboFilter===opt.key
                      ? (opt.key==='triple' ? 'rgba(239,68,68,0.35)' : 'rgba(245,158,11,0.3)')
                      : 'transparent',
                    color: comboFilter===opt.key ? '#fff' : 'rgba(255,255,255,0.45)',
                    transition:'all 0.15s',
                  }}>
                  {opt.label}
                  <span style={{marginLeft:'0.3rem',opacity:0.7,fontSize:'0.65rem'}}>
                    ({opt.key==='triple'
                      ? comboStocks.filter(s=>s.match_count>=3).length
                      : comboStocks.length}개)
                  </span>
                </button>
              ))}
            </div>
            <span style={{fontSize:'0.7rem',color:'rgba(255,255,255,0.4)'}}>
              {comboFilter==='triple'
                ? '추세추종 + 가치매수 + 재무스크리너 3개 전부 충족'
                : '추세추종 + 가치매수 + 재무스크리너 중 2개 이상 충족'}
            </span>
            <DlBtn onClick={() => downloadCSV(filteredCombo, 'ai_combo.csv')} />
          </div>

          {filteredCombo.length === 0 ? (
            <div className="glass-panel" style={{padding:'3rem',textAlign:'center',color:'var(--text-secondary)'}}>
              <p style={{fontSize:'2rem',marginBottom:'0.5rem'}}>
                {comboFilter==='triple' ? '🏆' : '⭐'}
              </p>
              <p>
                {comboFilter==='triple'
                  ? '현재 3개 카테고리를 모두 충족하는 종목이 없습니다.'
                  : '현재 2개 이상 카테고리를 동시 충족하는 종목이 없습니다.'}
              </p>
              <p style={{fontSize:'0.78rem',marginTop:'0.4rem',color:'rgba(255,255,255,0.35)'}}>
                {comboFilter==='triple'
                  ? '"2개↑ 충족" 으로 전환하면 더 많은 종목을 볼 수 있습니다.'
                  : '가치매수·재무스크리너·추세 탭을 각각 로드한 후 다시 확인해 주세요.'}
              </p>
            </div>
          ) : (
            filteredCombo.map(s => {
              const allThree = s.match_count >= 3;
              return (
                <div key={s.stock_code} style={{
                  padding:'1rem',borderRadius:'10px',
                  background: allThree ? 'rgba(239,68,68,0.08)' : 'rgba(245,158,11,0.05)',
                  border:`1px solid ${allThree ? 'rgba(239,68,68,0.35)' : 'rgba(245,158,11,0.25)'}`,
                }}>
                  {/* 헤더 */}
                  <div style={{display:'flex',alignItems:'center',gap:'0.5rem',marginBottom:'0.6rem',flexWrap:'wrap'}}>
                    <span style={{fontSize:'1.1rem'}}>{allThree ? '🏆' : '⭐'}</span>
                    <span style={{fontWeight:700,fontSize:'0.95rem'}}>{s.stock_name || s.stock_code}</span>
                    <span style={{fontSize:'0.7rem',color:'var(--text-secondary)'}}>{s.stock_code}</span>
                    <MktBadge market={s.market} mktcap={s.mktcap} />
                    {allThree && (
                      <span style={{padding:'0.1rem 0.5rem',borderRadius:'4px',fontSize:'0.68rem',fontWeight:700,
                        background:'rgba(239,68,68,0.2)',color:'#ef4444',border:'1px solid rgba(239,68,68,0.4)'}}>
                        3관왕
                      </span>
                    )}
                    <div style={{marginLeft:'auto',display:'flex',gap:'0.4rem',alignItems:'center'}}>
                      {/* 카테고리 배지 */}
                      {s.in_trend && (
                        <span style={{padding:'0.1rem 0.45rem',borderRadius:'4px',fontSize:'0.67rem',fontWeight:600,
                          background:'rgba(45,212,191,0.15)',color:'var(--accent-mint)',border:'1px solid rgba(45,212,191,0.3)'}}>
                          📈 추세
                        </span>
                      )}
                      {s.in_value && (
                        <span style={{padding:'0.1rem 0.45rem',borderRadius:'4px',fontSize:'0.67rem',fontWeight:600,
                          background:'rgba(245,158,11,0.15)',color:'#f59e0b',border:'1px solid rgba(245,158,11,0.3)'}}>
                          💎 가치
                        </span>
                      )}
                      {s.in_fin && (
                        <span style={{padding:'0.1rem 0.45rem',borderRadius:'4px',fontSize:'0.67rem',fontWeight:600,
                          background:'rgba(139,92,246,0.15)',color:'#a78bfa',border:'1px solid rgba(139,92,246,0.3)'}}>
                          📊 재무
                        </span>
                      )}
                      <button onClick={() => { changeStock(s.stock_code); changeTab('analysis'); }}
                        style={{padding:'0.2rem 0.55rem',borderRadius:'5px',border:'none',
                          background:'rgba(45,212,191,0.15)',color:'var(--accent-mint)',
                          cursor:'pointer',fontSize:'0.72rem'}}>
                        분석
                      </button>
                    </div>
                  </div>
                  {/* 점수 행 */}

                  {/* 점수 행 */}
                  <div style={{display:'flex',gap:'0.5rem',flexWrap:'wrap',marginBottom:'0.5rem'}}>
                    {s.in_trend && (
                      <div style={{padding:'0.25rem 0.5rem',borderRadius:'5px',
                        background:'rgba(45,212,191,0.08)',border:'1px solid rgba(45,212,191,0.2)'}}>
                        <span style={{fontSize:'0.67rem',color:'var(--text-secondary)'}}>추세점수 </span>
                        <span style={{fontSize:'0.78rem',fontWeight:700,color:'var(--accent-mint)'}}>{s.trend_score}</span>
                      </div>
                    )}
                    {s.in_value && (
                      <div style={{padding:'0.25rem 0.5rem',borderRadius:'5px',
                        background:'rgba(245,158,11,0.08)',border:'1px solid rgba(245,158,11,0.2)'}}>
                        <span style={{fontSize:'0.67rem',color:'var(--text-secondary)'}}>가치점수 </span>
                        <span style={{fontSize:'0.78rem',fontWeight:700,color:'#f59e0b'}}>{s.value_score}/9</span>
                      </div>
                    )}
                    {s.in_fin && (
                      <div style={{padding:'0.25rem 0.5rem',borderRadius:'5px',
                        background:'rgba(139,92,246,0.08)',border:'1px solid rgba(139,92,246,0.2)'}}>
                        <span style={{fontSize:'0.67rem',color:'var(--text-secondary)'}}>재무점수 </span>
                        <span style={{fontSize:'0.78rem',fontWeight:700,color:'#a78bfa'}}>{s.fin_score}</span>
                      </div>
                    )}
                    {s.price && (
                      <div style={{padding:'0.25rem 0.5rem',borderRadius:'5px',
                        background:'rgba(255,255,255,0.04)',border:'1px solid rgba(255,255,255,0.08)'}}>
                        <span style={{fontSize:'0.67rem',color:'var(--text-secondary)'}}>현재가 </span>
                        <span style={{fontSize:'0.78rem',fontWeight:600}}>{s.price.toLocaleString('ko-KR')}원</span>
                      </div>
                    )}
                    {s.sector && (
                      <div style={{padding:'0.25rem 0.5rem',borderRadius:'5px',
                        background:'rgba(255,255,255,0.04)',border:'1px solid rgba(255,255,255,0.06)'}}>
                        <span style={{fontSize:'0.72rem',color:'rgba(255,255,255,0.45)'}}>{s.sector}</span>
                      </div>
                    )}
                  </div>
                  {/* 근거 태그 */}
                  <div style={{display:'flex',gap:'0.3rem',flexWrap:'wrap'}}>
                    {(s.reasons || s.tags || []).slice(0,6).map((r,i) => (
                      <span key={i} style={{padding:'0.12rem 0.4rem',borderRadius:'4px',fontSize:'0.65rem',
                        background:'rgba(255,255,255,0.05)',color:'rgba(255,255,255,0.6)',
                        border:'1px solid rgba(255,255,255,0.08)'}}>
                        {r}
                      </span>
                    ))}
                  </div>
                </div>
              );
            })
          )}

          {/* ── AI 적극 검토 로직 설명 ── */}
          <div style={{marginTop:'0.5rem',padding:'1rem 1.2rem',
            background:'rgba(239,68,68,0.03)',border:'1px solid rgba(239,68,68,0.12)',
            borderRadius:'10px',fontSize:'0.72rem',color:'rgba(255,255,255,0.6)',lineHeight:1.9}}>
            <div style={{fontWeight:700,color:'#ef4444',marginBottom:'0.5rem',fontSize:'0.78rem'}}>
              ⭐ Logic-#1 선정 원리
            </div>
            <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(280px,1fr))',gap:'0.6rem 1.2rem'}}>
              {[
                ['교집합 원리','추세추종(주봉선·섹터·정배열), 가치매수(Graham·PBR·PER), 재무스크리너(성장기울기·턴어라운드) 3개 카테고리 중 2개 이상에서 독립적으로 조건을 충족한 종목만 수록.'],
                ['확률의 곱셈','개별 카테고리 발굴 확률 50%가 2개 독립 조건을 동시 충족하면 25%로 좁혀짐. 이 구간이 가장 높은 확률의 매수 구간.'],
                ['3관왕 (🏆)','3개 카테고리 모두 충족 = 추세 + 저평가 + 실적 개선. 극히 드문 경우로 최우선 검토 대상. 단, 소형주·유동성 위험은 별도 확인.'],
                ['동적 업데이트','각 탭(추세·가치·재무) 데이터를 로드하면 자동으로 교집합이 재계산됨. 새로고침 없이 실시간 반영.'],
              ].map(([title, desc]) => (
                <div key={title}>
                  <span style={{color:'rgba(239,68,68,0.8)',fontWeight:600}}>{title}</span>
                  <span style={{color:'rgba(255,255,255,0.5)'}}> — {desc}</span>
                </div>
              ))}
            </div>
          </div>
          </>)}

          {/* ── Logic-#2: 수급 주도 모멘텀 ── */}
          {comboLogic === 'v2' && (<>
          <div style={{padding:'0.7rem 1rem',
            background:'linear-gradient(135deg, rgba(99,102,241,0.08), rgba(45,212,191,0.06))',
            border:'1px solid rgba(99,102,241,0.35)',borderRadius:'8px',
            display:'flex',alignItems:'center',gap:'0.6rem',flexWrap:'wrap'}}>
            <span style={{fontSize:'0.8rem',color:'#818cf8',fontWeight:700}}>
              🔥 Logic-#2 수급 주도 모멘텀 — {comboV2Data.length}종목
            </span>
            <span style={{fontSize:'0.7rem',color:'rgba(255,255,255,0.4)'}}>
              기관·외국인 동반순매수 × 추세 × 실적 복합 스코어
            </span>
            {comboV2Data.length > 0 && <DlBtn onClick={() => downloadCSV(comboV2Data, 'combo_v2.csv')} />}
            <button onClick={fetchComboV2} style={{marginLeft:'auto',padding:'0.2rem 0.7rem',
              borderRadius:'6px',border:'1px solid rgba(99,102,241,0.4)',
              background:'rgba(99,102,241,0.1)',color:'#818cf8',cursor:'pointer',fontSize:'0.73rem'}}>
              🔄 새로고침
            </button>
          </div>

          {comboV2Loading ? (
            <div className="glass-panel" style={{padding:'3rem',textAlign:'center',color:'var(--text-secondary)'}}>
              <div style={{width:'32px',height:'32px',borderRadius:'50%',border:'3px solid #818cf8',
                borderTopColor:'transparent',animation:'spin 0.8s linear infinite',margin:'0 auto 1rem'}}/>
              <p>Logic-#2 계산 중... (최초 실행 시 1~2분 소요)</p>
            </div>
          ) : comboV2Data.length === 0 ? (
            <div className="glass-panel" style={{padding:'2.5rem',textAlign:'center'}}>
              <p style={{fontSize:'1.5rem',marginBottom:'0.5rem'}}>📡</p>
              <p style={{color:'var(--text-secondary)',fontSize:'0.85rem'}}>수급 데이터 분석 결과가 없습니다.</p>
              <button onClick={fetchComboV2} style={{marginTop:'1rem',padding:'0.5rem 1.2rem',
                borderRadius:'8px',border:'none',background:'#818cf8',color:'#fff',
                cursor:'pointer',fontWeight:700}}>
                🔥 Logic-#2 분석 실행
              </button>
            </div>
          ) : comboV2Data.map(s => {
            const sc = sigColor[s.signal] || '#94a3b8';
            const em = sigEmoji[s.signal] || '📌';
            const score = s.score || 0;
            const maxScore = 42;
            const pct = Math.min(score / maxScore * 100, 100);
            return (
              <div key={s.stock_code} style={{padding:'1rem',borderRadius:'10px',
                background:`${sc}08`,border:`1px solid ${sc}35`}}>
                {/* 헤더 */}
                <div style={{display:'flex',alignItems:'center',gap:'0.5rem',marginBottom:'0.6rem',flexWrap:'wrap'}}>
                  <span style={{fontSize:'1rem'}}>{em}</span>
                  <span style={{fontWeight:700,fontSize:'0.95rem'}}>{s.stock_name || s.stock_code}</span>
                  <span style={{fontSize:'0.7rem',color:'var(--text-secondary)'}}>{s.stock_code}</span>
                  <MktBadge market={s.market} mktcap={s.mktcap} />
                  <span style={{padding:'0.1rem 0.5rem',borderRadius:'4px',fontSize:'0.68rem',fontWeight:700,
                    background:`${sc}25`,color:sc,border:`1px solid ${sc}55`}}>
                    {s.signal}
                  </span>
                  <div style={{marginLeft:'auto',display:'flex',gap:'0.4rem',alignItems:'center'}}>
                    <button onClick={() => { changeStock(s.stock_code); changeTab('analysis'); }}
                      style={{padding:'0.2rem 0.55rem',borderRadius:'5px',border:'none',
                        background:'rgba(45,212,191,0.15)',color:'var(--accent-mint)',
                        cursor:'pointer',fontSize:'0.72rem'}}>
                      분석
                    </button>
                  </div>
                </div>
                {/* 종합 점수 바 */}
                <div style={{display:'flex',alignItems:'center',gap:'0.6rem',marginBottom:'0.55rem'}}>
                  <span style={{fontSize:'0.68rem',color:'rgba(255,255,255,0.45)',whiteSpace:'nowrap'}}>종합점수</span>
                  <div style={{flex:1,height:'7px',borderRadius:'4px',background:'rgba(255,255,255,0.07)',overflow:'hidden'}}>
                    <div style={{width:`${pct}%`,height:'100%',borderRadius:'4px',
                      background:`linear-gradient(90deg, ${sc}, ${sc}aa)`}}/>
                  </div>
                  <span style={{fontSize:'0.82rem',fontWeight:700,color:sc,whiteSpace:'nowrap'}}>
                    {score}<span style={{fontSize:'0.6rem',opacity:0.6}}>/{maxScore}</span>
                  </span>
                </div>
                {/* 트랙 점수 */}
                <div style={{display:'flex',gap:'0.4rem',flexWrap:'wrap',marginBottom:'0.5rem'}}>
                  {[
                    {key:'track_s', label:'📡 수급', max:18, color:'#818cf8'},
                    {key:'track_t', label:'📈 추세', max:12, color:'#2dd4bf'},
                    {key:'track_q', label:'📊 실적', max:8,  color:'#f59e0b'},
                    {key:'track_r', label:'💪 RS',   max:4,  color:'#22c55e'},
                  ].filter(t => s[t.key] != null).map(({key,label,max,color}) => {
                    const v = s[key] || 0;
                    const p2 = Math.min(v/max*100,100);
                    return (
                      <div key={key} style={{padding:'0.25rem 0.6rem',borderRadius:'6px',
                        background:`${color}10`,border:`1px solid ${color}30`,
                        display:'flex',alignItems:'center',gap:'0.35rem'}}>
                        <span style={{fontSize:'0.65rem',color:'rgba(255,255,255,0.5)'}}>{label}</span>
                        <div style={{width:'40px',height:'4px',borderRadius:'2px',background:'rgba(255,255,255,0.06)'}}>
                          <div style={{width:`${p2}%`,height:'100%',borderRadius:'2px',background:color}}/>
                        </div>
                        <span style={{fontSize:'0.75rem',fontWeight:700,color}}>{v}</span>
                      </div>
                    );
                  })}
                  {s.price && (
                    <div style={{padding:'0.25rem 0.5rem',borderRadius:'5px',
                      background:'rgba(255,255,255,0.04)',border:'1px solid rgba(255,255,255,0.08)',
                      display:'flex',alignItems:'center',gap:'0.3rem'}}>
                      <span style={{fontSize:'0.67rem',color:'var(--text-secondary)'}}>현재가</span>
                      <span style={{fontSize:'0.78rem',fontWeight:600}}>{s.price.toLocaleString('ko-KR')}원</span>
                    </div>
                  )}
                </div>
                {/* 시그널 태그 */}
                <div style={{display:'flex',gap:'0.3rem',flexWrap:'wrap'}}>
                  {(s.reasons || []).slice(0,6).map((r,i) => (
                    <span key={i} style={{padding:'0.12rem 0.4rem',borderRadius:'4px',fontSize:'0.65rem',
                      background:'rgba(255,255,255,0.05)',color:'rgba(255,255,255,0.6)',
                      border:'1px solid rgba(255,255,255,0.08)'}}>
                      {r}
                    </span>
                  ))}
                </div>
              </div>
            );
          })}

          {/* Logic-#2 원리 설명 */}
          {comboV2Data.length > 0 && (
          <div style={{marginTop:'0.5rem',padding:'1rem 1.2rem',
            background:'rgba(99,102,241,0.03)',border:'1px solid rgba(99,102,241,0.12)',
            borderRadius:'10px',fontSize:'0.72rem',color:'rgba(255,255,255,0.6)',lineHeight:1.9}}>
            <div style={{fontWeight:700,color:'#818cf8',marginBottom:'0.5rem',fontSize:'0.78rem'}}>
              📡 Logic-#2 수급 주도 모멘텀 — 로직 원리
            </div>
            <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fill,minmax(280px,1fr))',gap:'0.6rem 1.2rem'}}>
              {[
                ['Track S — 수급 (×3, 최대18점)', '기관+외국인 3일 연속 동반매수(최고등급), 단독 5일, 10일 누적금액 기준으로 수급 강도를 점수화. 가장 높은 가중치.'],
                ['Track T — 추세 (×2, 최대12점)', '일봉 정배열(MA5>20>60>120>240) 4점 + 주봉 MA40/MA80 기준 주봉 정배열 2점. 단기·중기 동시 추세 확인.'],
                ['Track Q — 실적 (×2, 최대8점)', '최근 분기 YoY 영업이익 성장이 연속으로 유지될수록 가산. 턴어라운드 및 지속 성장주 포착.'],
                ['Track R — 상대강도 (×1, 최대4점)', '21일·63일·126일 3구간에서 KOSPI 대비 초과상승 여부 평가. 3구간 모두 우세 시 최고점.'],
                ['강력추천 조건', '총점 28점 이상 + 수급점수 12점↑ + 추세점수 8점↑. 수급과 추세가 동시에 강한 경우만 선정.'],
                ['시장 필터', 'KOSPI MA60 하락장 진입 시 모든 점수 ×0.75 패널티. 하락장에서는 높은 점수에도 자동 등급 하향.'],
              ].map(([title, desc]) => (
                <div key={title}>
                  <span style={{color:'rgba(129,140,248,0.9)',fontWeight:600}}>{title}</span>
                  <span style={{color:'rgba(255,255,255,0.5)'}}> — {desc}</span>
                </div>
              ))}
            </div>
          </div>
          )}
          </>)}

        </div>
        );
      })()}

    </div>
    );
  };

  // ── Peak 전략 뷰 (독립 컴포넌트) ─────────────────────────────
  const PeakView = () => {
    const [peakData, setPeakData]     = React.useState({ holdings: [], exits: [] });
    const [summary,  setSummary]      = React.useState(null);
    const [trades,   setTrades]       = React.useState([]);
    const [peakTab,  setPeakTab]      = React.useState('holdings');
    const [loading,  setPeakLoading]  = React.useState(true);
    const [lastSync, setLastSync]     = React.useState('');
    const [strategy, setStrategy]     = React.useState('peak');

    // AI 추천 탭 전용 state — 최상위에 위치해야 hooks 규칙 준수
    const [aiHoldings, setAiHoldings] = React.useState([]);
    const [aiLoading,  setAiLoading]  = React.useState(false);
    const [aiSubTab,   setAiSubTab]   = React.useState('holdings'); // AI탭 서브탭

    const loadPeak = async () => {
      setPeakLoading(true);
      try {
        const [hRes, tRes, sRes] = await Promise.all([
          fetch(API('/api/trend/holdings')),
          fetch(API('/api/trend/trades')),
          fetch(API('/api/trend/summary')),
        ]);
        const all    = hRes.ok ? await hRes.json() : [];
        const active = all.filter(h => h.is_active);
        const exited = all.filter(h => !h.is_active);
        setPeakData({ holdings: active, exits: exited });
        if (tRes.ok) setTrades(await tRes.json());
        if (sRes.ok) setSummary(await sRes.json());
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
      loadAiHoldings();
      const iv = setInterval(loadPeak, 600000); // 10분 자동 갱신
      return () => clearInterval(iv);
    }, []);

    const fp = (v) => v != null ? Math.round(v).toLocaleString('ko-KR') : '-';
    const pc = (v) => v > 0 ? '#ef4444' : v < 0 ? '#3b82f6' : 'rgba(255,255,255,0.35)';
    const pf = (v) => v == null ? '-' : (v >= 0 ? '+' : '') + fp(v);

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
    const curHoldings = strategy === 'ai_rec'
      ? aiHoldings.filter(h => h.is_active)
      : peakData.holdings.filter(h => h.strategy === strategy);
    const curExits = strategy === 'ai_rec'
      ? aiHoldings.filter(h => !h.is_active)
      : peakData.exits.filter(h => h.strategy === strategy);
    const curTrades = strategy === 'ai_rec'
      ? trades.filter(t => t.strategy === 'ai_combo')
      : trades.filter(t => t.strategy === strategy);

    // 요약 카드 — 현재 전략 기준 집계
    const SummaryCards = () => {
      const realProfit = curExits.reduce((s,h)=>s+(h.profit||0),0);
      const wins = curExits.filter(h=>(h.profit||0)>0).length;
      const winRate = curExits.length > 0 ? Math.round(wins/curExits.length*100) : null;
      return (
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: '0.75rem', marginBottom: '1rem' }}>
        {[
          { label: '보유 종목',   val: `${curHoldings.length}개`,      color: 'var(--accent-mint)' },
          { label: '보유 총액',   val: fp(curHoldings.reduce((s,h)=>s+(h.total_value||(h.buy_price||0)*(h.quantity||0)),0))+'원', color: 'inherit' },
          { label: '평가 손익',   val: pf(curHoldings.reduce((s,h)=>s+(h.profit||0),0))+'원',
            color: pc(curHoldings.reduce((s,h)=>s+(h.profit||0),0)) },
          { label: '누적 실현 손익', val: pf(realProfit)+'원',
            color: pc(realProfit||0) },
          { label: '승률',       val: winRate != null ? `${winRate}%` : '-', color: 'var(--accent-purple)' },
        ].map(({ label, val, color }) => (
          <div key={label} className="glass-panel" style={{ padding: '0.9rem 1rem' }}>
            <p style={{ fontSize: '0.7rem', color: 'var(--text-secondary)', marginBottom: '0.3rem' }}>{label}</p>
            <p style={{ fontSize: '1rem', fontWeight: 700, color }}>{val}</p>
          </div>
        ))}
      </div>
      );
    };

    const STRATEGIES = [
      { key:'peak',     label:'Peak Easy',  color:'#a78bfa' },
      { key:'momentum', label:'모멘텀 Easy', color:'#34d399' },
      { key:'value',    label:'벨류 Easy',   color:'#60a5fa' },
      { key:'ai_rec',   label:'⭐ AI 추천',  color:'#ef4444' },
    ];

    return (
      <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        {/* 안내 배너 */}
        <div style={{padding:'0.4rem 0.9rem',background:'rgba(251,191,36,0.07)',
          border:'1px solid rgba(251,191,36,0.25)',borderRadius:'8px',
          fontSize:'0.7rem',color:'rgba(251,191,36,0.85)',lineHeight:1.4}}>
          ⚠️ Stock Easy 사이트내 전략종목을 파씽해오는 종목임을 안내 드립니다.
        </div>
        {/* 헤더 */}
        <div className="glass-panel" style={{ padding: '1rem 1.4rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap:'wrap', gap:'0.5rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
            {STRATEGIES.map(s => {
              const isAiRec = s.key === 'ai_rec';
              const isActive = strategy === s.key;
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
                }}>{s.label}</button>
              );
            })}
          </div>
          <div style={{ display: 'flex', gap: '0.5rem', flexWrap:'wrap' }}>
            {tabBtn('holdings', '보유 종목', curHoldings.length)}
            {tabBtn('exits',    '이탈 종목', curExits.length)}
            {tabBtn('history',  '매매 내역', null)}
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
        {/* 요약 카드 */}
        <SummaryCards />

        {loading && (
          <div className="glass-panel" style={{ padding: '2rem', textAlign: 'center', color: 'var(--accent-purple)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '0.6rem' }}>
              <div style={{ width: '14px', height: '14px', borderRadius: '50%', border: '2px solid var(--accent-purple)', borderTopColor: 'transparent', animation: 'spin 0.8s linear infinite' }} />
              Peak 데이터 로딩 중...
            </div>
          </div>
        )}

        {/* ══ 보유 종목 탭 ══ */}
        {!loading && peakTab === 'holdings' && (
          curHoldings.length === 0 ? (
            <div className="glass-panel" style={{ padding: '3rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
              <TrendingUp size={40} style={{ margin: '0 auto 1rem', display: 'block', opacity: 0.3 }} />
              <p style={{ fontSize: '1rem', fontWeight: 600, color: 'rgba(255,255,255,0.5)' }}>현재 [{strategy === 'peak' ? 'Peak Easy' : strategy === 'momentum' ? '모멘텀 Easy' : strategy === 'value' ? '벨류 Easy' : 'AI 추천'}] 전략 보유 종목이 없습니다.</p>
              <p style={{ fontSize: '0.8rem', marginTop: '0.6rem' }}>{strategy === 'ai_rec' ? 'AI 자동매매 즉시 실행 후 종목이 등록됩니다.' : '추세 매수 시그널 발생 시 자동으로 등록됩니다.'}</p>
              <p style={{ fontSize: '0.75rem', marginTop: '0.3rem', color: 'rgba(255,255,255,0.25)' }}>이탈 종목 {curExits.length}건</p>
            </div>
          ) : (
            <div className="glass-panel" style={{ overflow: 'auto' }}>
              <div style={{ padding: '0.6rem 1rem', borderBottom: '1px solid var(--glass-border)', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
                updated {lastSync}  ·  가상매수 한도: 1,000만원/종목
              </div>
              <table className="premium-table">
                <thead><tr>
                  <th>종목명</th>
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
                        <td style={{ textAlign: 'right' }}>{fp(h.buy_price)}</td>
                        <td style={{ textAlign: 'right', color: pc(h.current_price - h.buy_price), fontWeight: 600 }}>
                          {fp(h.current_price)}
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
                          {pf(h.profit)}원
                        </td>
                        <td style={{ textAlign: 'right' }}>{fp(h.total_value)}원</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )
        )}



        {/* ══ 이탈 종목 탭 ══ */}
        {!loading && peakTab === 'exits' && (
          curExits.length === 0 ? (
            <div className="glass-panel" style={{ padding: '3rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
              <p>이탈 종목이 없습니다.</p>
            </div>
          ) : (
            <div className="glass-panel" style={{ overflow: 'auto' }}>
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
                    const profit = Math.round(((h.sell_price||h.buy_price||0) - (h.buy_price||0)) * (h.quantity||0));
                    return (
                      <tr key={h.id}>
                        <td><span style={{ fontSize: '0.72rem', color: 'var(--text-secondary)' }}>{h.sector}</span></td>
                        <td style={{ fontWeight: 700 }}>{h.stock_name}</td>
                        <td style={{ textAlign: 'right' }}>{fp(h.buy_price)}</td>
                        <td style={{ textAlign: 'right', fontWeight: 600 }}>{fp(h.sell_price)}</td>
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
                          {pf(profit)}원
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
        {!loading && peakTab === 'status' && (() => {
          const allPos = [...peakData.holdings, ...peakData.exits]
            .sort((a,b) => (b.entry_date||'').localeCompare(a.entry_date||''));
          return allPos.length === 0 ? (
            <div className="glass-panel" style={{ padding: '3rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
              <p>포지션 내역이 없습니다.</p>
            </div>
          ) : (
            <div className="glass-panel" style={{ overflow: 'auto' }}>
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
                    const profit = isActive
                      ? Math.round(((h.current_price||h.buy_price||0) - (h.buy_price||0)) * (h.quantity||0))
                      : Math.round(((h.sell_price||h.buy_price||0) - (h.buy_price||0)) * (h.quantity||0));
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
                        <td style={{ textAlign: 'right' }}>{fp(h.buy_price)}</td>
                        <td style={{ textAlign: 'right', color: isActive ? 'rgba(255,255,255,0.3)' : 'inherit' }}>
                          {isActive ? fp(h.current_price) : fp(h.sell_price)}
                        </td>
                        <td style={{ textAlign: 'right' }}>{(h.quantity||0).toLocaleString('ko-KR')}주</td>
                        <td style={{ textAlign: 'right', color: pc(pct), fontWeight: 600 }}>
                          {pct >= 0 ? '+' : ''}{pct.toFixed(1)}%
                        </td>
                        <td style={{ textAlign: 'right', color: pc(profit), fontWeight: 600 }}>
                          {pf(profit)}원
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

        {/* ══ AI 추천 탭 — 안내 배너 (보유/이탈/매매내역은 공통 탭에서 처리) ══ */}
        {!loading && strategy === 'ai_rec' && (
          <div style={{padding:'0.65rem 1rem',
            background:'linear-gradient(135deg,rgba(239,68,68,0.08),rgba(245,158,11,0.06))',
            border:'1px solid rgba(239,68,68,0.25)',borderRadius:'8px',
            fontSize:'0.72rem',color:'rgba(255,255,255,0.6)',lineHeight:1.7,
            display:'flex',alignItems:'center',justifyContent:'space-between',flexWrap:'wrap',gap:'0.5rem'}}>
            <span>
              <span style={{fontWeight:700,color:'#ef4444',marginRight:'0.4rem'}}>⭐ AI 적극검토 자동매매</span>
              추세추종+가치매수+재무스크리너 <strong>2개↑</strong> 동시충족 →
              <strong>1,000만원</strong> 가상매수 → MA20 2일연속 이탈 / MA60붕괴 / -15%손절 시 매도
            </span>
            <div style={{display:'flex',alignItems:'center',gap:'0.6rem',flexShrink:0}}>
              <button onClick={loadAiHoldings} style={{
                padding:'0.3rem 0.65rem',borderRadius:'6px',fontSize:'0.75rem',cursor:'pointer',
                background:'rgba(255,255,255,0.05)',border:'1px solid var(--glass-border)',color:'var(--text-secondary)',
              }}>🔄 새로고침</button>
            </div>
          </div>
        )}

        {/* ══ 매매 내역 탭 ══ */}
        {!loading && peakTab === 'history' && (
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
                <div className="glass-panel" style={{ overflow: 'auto' }}>
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


  // ── 계좌현황 ─────────────────────────────────────────────
  const PortfolioView = () => {
    const [portfolio, setPortfolio]     = React.useState([]);
    const [transactions, setTransactions] = React.useState([]);
    const [tab, setTab]                 = React.useState("holdings");
    const [kakaoText, setKakaoText]     = React.useState("");
    const [parsedTx, setParsedTx]       = React.useState([]);
    const [showModal, setShowModal]     = React.useState(false);
    // 더블클릭 인라인 편집
    const [editRow, setEditRow]         = React.useState(null);
    const [editForm, setEditForm]       = React.useState({});
    const [editCodePreview, setEditCodePreview] = React.useState(null); // 검색된 새 코드 미리보기
    const [form, setForm] = React.useState({
      stock_code:"", stock_name:"", sector:"", tx_type:"buy",
      quantity:"", price:"", tx_date:"", memo:""
    });

    const load = async () => {
      const [p, t] = await Promise.all([
        fetch(API('/api/portfolio')).then(r=>r.ok?r.json():[]),
        fetch(API('/api/portfolio/transactions')).then(r=>r.ok?r.json():[]),
      ]);
      setPortfolio(p); setTransactions(t);
    };

    // ── 실시간 1분 폴링: /api/realtime/prices 로 현재가·손익만 갱신 ──
    const [realtimeMeta, setRealtimeMeta] = React.useState({ updated_at: '', market_open: false });
    React.useEffect(() => {
      load();

      const applyRealtime = async () => {
        try {
          const res = await fetch(API('/api/realtime/prices'));
          if (!res.ok) return;
          const rt = await res.json();
          setRealtimeMeta({ updated_at: rt.updated_at, market_open: rt.market_open });
          setPortfolio(prev => prev.map(h => {
            const r = rt.holdings[h.stock_code];
            if (!r) return h;
            return { ...h,
              current_price: r.current_price,
              change_pct:    r.change_pct,
              profit:        r.profit,
              profit_pct:    r.profit_pct,
              total_value:   r.total_value,
              buy_total:     r.buy_total,
            };
          }));
        } catch {}
      };

      // 장 시간: 1분 / 장 외: 5분 (초기 1회는 즉시)
      applyRealtime();
      const iv=isKRMarketOpen()?setInterval(applyRealtime,60000):null;
      return ()=>{if(iv)clearInterval(iv);};
    }, []);

    // 섹터 그룹핑
    const groups = React.useMemo(()=>{
      const g = {};
      portfolio.forEach(h=>{ const s=h.sector||"기타"; if(!g[s]) g[s]=[]; g[s].push(h); });
      // 섹터 내 종목: 평가금액 내림차순 정렬
      Object.keys(g).forEach(s => g[s].sort((a,b) => b.total_value - a.total_value));
      // 섹터 자체도 섹터 합계 평가금액 내림차순
      return Object.fromEntries(
        Object.entries(g).sort((a,b) => {
          const sa = a[1].reduce((s,h)=>s+h.total_value,0);
          const sb = b[1].reduce((s,h)=>s+h.total_value,0);
          return sb - sa;
        })
      );
    }, [portfolio]);

    // 총합: realtime API summary 우선, 없으면 portfolio 로컬 합산
    const [rtSummary, setRtSummary] = React.useState(null);
    React.useEffect(() => {
      fetch(API('/api/realtime/prices')).then(r=>r.ok?r.json():null).then(rt=>{
        if (rt?.summary) setRtSummary(rt.summary);
      }).catch(()=>{});
    }, [portfolio]);  // portfolio 바뀔 때마다 재계산

    const totalBuy       = rtSummary?.total_buy    ?? portfolio.reduce((s,h)=>s+h.buy_total, 0);
    const totalVal       = rtSummary?.total_value   ?? portfolio.reduce((s,h)=>s+h.total_value, 0);
    const totalProfit    = rtSummary?.total_profit  ?? portfolio.reduce((s,h)=>s+h.profit, 0);
    const totalProfitPct = rtSummary?.total_profit_pct
      ?? (totalBuy > 0 ? ((totalVal-totalBuy)/totalBuy*100).toFixed(2) : 0);
    // ── [버그 ② 수정] 전일 대비 당일 손익 ──────────────────────
    const dailyProfit    = rtSummary?.daily_profit
      ?? portfolio.reduce((s,h) => s + (h.daily_profit ?? 0), 0);
    const dailyProfitPct = rtSummary?.daily_profit_pct
      ?? (totalVal > 0 && dailyProfit !== 0 ? ((dailyProfit / (totalVal - dailyProfit)) * 100).toFixed(2) : null);
    const hasDailyData   = dailyProfit !== 0 || rtSummary?.daily_profit != null;

    const fp  = v => v!=null ? Math.round(v).toLocaleString('ko-KR') : '-';
    const pct = v => v!=null ? `${v>0?'+':''}${v}%` : '-';
    const pc  = v => v>0?'#ef4444':v<0?'#3b82f6':'inherit';

    // ── 더블클릭 편집 ──────────────────────────────────────────
    const startEdit = (h) => {
      setEditRow(h.stock_code);
      setEditCodePreview(null);
      setEditForm({
        stock_name: h.stock_name,
        sector:     h.sector || "",
        avg_price:  h.avg_price,
        quantity:   h.quantity,
      });
    };

    const cancelEdit = () => { setEditRow(null); setEditForm({}); setEditCodePreview(null); };

    // 종목명 변경 시 티커 미리보기
    const handleNameChange = async (name) => {
      setEditForm(p => ({...p, stock_name: name}));
      if (name.length < 2) { setEditCodePreview(null); return; }
      try {
        const r = await fetch(API(`/api/search?q=${encodeURIComponent(name)}`));
        if (r.ok) {
          const results = await r.json();
          setEditCodePreview(results.length > 0 ? results[0] : null);
        }
      } catch {}
    };

    const saveEdit = async (stock_code) => {
      // PUT API: 종목명 변경 시 ticker_mapper로 새 코드 자동 조회
      const res = await fetch(API(`/api/portfolio/${stock_code}`), {
        method: 'PUT',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({
          stock_name: editForm.stock_name,
          sector:     editForm.sector,
          quantity:   Number(editForm.quantity),
          avg_price:  Number(editForm.avg_price),
        }),
      });
      if (res.ok) {
        const data = await res.json();
        // ── [버그 ① 수정] 저장 즉시 로컬 state 먼저 반영 (load() 재호출로 인한 원복 방지) ──
        const newCode = data.code_changed ? data.new_code : stock_code;
        setPortfolio(prev => prev.map(h => {
          if (h.stock_code !== stock_code) return h;
          const updated = {
            ...h,
            stock_name: data.stock_name || editForm.stock_name,
            sector:     editForm.sector,
            quantity:   Number(editForm.quantity),
            avg_price:  Number(editForm.avg_price),
            stock_code: newCode,
          };
          // 수량 변경 시 buy_total / total_value / profit 재계산
          updated.buy_total   = updated.avg_price * updated.quantity;
          updated.total_value = (h.current_price || h.avg_price) * updated.quantity;
          updated.profit      = updated.total_value - updated.buy_total;
          updated.profit_pct  = updated.buy_total > 0
            ? Number(((updated.total_value - updated.buy_total) / updated.buy_total * 100).toFixed(2))
            : 0;
          return updated;
        }));
        if (data.code_changed) {
          alert(`종목코드가 변경되었습니다: ${stock_code} → ${data.new_code} (${data.stock_name})\n주가 데이터를 백그라운드에서 수집합니다.`);
          fetchWatchlist();
        }
        // 서버 저장 완료 후 1.5초 뒤 재동기화 (즉시 load() 하면 서버 반영 전 원복될 수 있음)
        setTimeout(load, 1500);
      }
      setEditRow(null); setEditForm({}); setEditCodePreview(null);
    };

    // ── 카카오 파싱 ────────────────────────────────────────────
    const handleKakaoParse = async () => {
      const res = await fetch(API('/api/portfolio/kakao-parse'), {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({text: kakaoText}),
      });
      if(res.ok){ const d=await res.json(); setParsedTx(d.parsed||[]); }
    };

    const applyParsed = async (item) => {
      if(!item.valid) return;
      await fetch(API('/api/portfolio/transaction'), {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({stock_code:item.stock_code, stock_name:item.stock_name,
          tx_type:item.tx_type, quantity:item.quantity, price:item.price, memo:item.raw}),
      });
      load(); setParsedTx(prev=>prev.filter(p=>p!==item));
    };

    const saveTx = async () => {
      if(!form.stock_code || !form.quantity || !form.price) {
        alert('종목코드, 수량, 단가는 필수 입력 항목입니다.');
        return;
      }
      const qty   = Number(form.quantity);
      const price = Number(form.price);
      if(qty <= 0 || price <= 0) {
        alert('수량과 단가는 0보다 커야 합니다.');
        return;
      }
      // 매도 시 보유 수량 확인
      if(form.tx_type === 'sell') {
        const holding = portfolio.find(h => h.stock_code === form.stock_code);
        if(!holding) {
          if(!window.confirm(`${form.stock_code} 종목이 포트폴리오에 없습니다. 계속하시겠습니까?`)) return;
        } else if(holding.quantity < qty) {
          if(!window.confirm(`보유 수량(${Math.round(holding.quantity)}주)보다 매도 수량(${qty}주)이 많습니다. 계속하시겠습니까?`)) return;
        }
      }
      try {
        const res = await fetch(API('/api/portfolio/transaction'), {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({...form, quantity: qty, price: price}),
        });
        if(!res.ok) {
          const err = await res.json().catch(()=>({}));
          alert('저장 실패: ' + (err.detail || res.status));
          return;
        }
        setShowModal(false);
        setForm({stock_code:"",stock_name:"",sector:"",tx_type:"buy",quantity:"",price:"",tx_date:"",memo:""});
        load();
      } catch(e) {
        alert('오류 발생: ' + e.message);
      }
    };

    const deleteHolding = async (stock_code) => {
      if(!window.confirm(`${stock_code} 보유종목을 삭제하시겠습니까?`)) return;
      await fetch(API(`/api/portfolio/${stock_code}`), { method:'DELETE' });
      load();
    };

    const inputStyle = {
      padding:'0.25rem 0.4rem', borderRadius:'4px', fontSize:'0.82rem',
      background:'rgba(255,255,255,0.1)', border:'1px solid var(--accent-mint)',
      color:'#fff', width:'100%', textAlign:'right',
    };

    const collectingCount = portfolio.filter(h => h.collecting).length;
    const noDataCount     = portfolio.filter(h => h.has_price === false).length;

    return (
      <div className="fade-in" style={{display:'flex',flexDirection:'column',gap:'1rem'}}>

        {/* 실시간 갱신 상태 뱃지 */}
        <div style={{display:'flex',alignItems:'center',gap:'0.6rem',fontSize:'0.75rem',color:'var(--text-secondary)',paddingLeft:'0.2rem'}}>
          <span style={{
            width:'7px', height:'7px', borderRadius:'50%',
            background: realtimeMeta.market_open ? 'var(--accent-mint)' : '#888',
            display:'inline-block',
            animation: realtimeMeta.market_open ? 'spin 2s linear infinite' : 'none',
          }}/>
          {realtimeMeta.market_open
            ? <span style={{color:'var(--accent-mint)',fontWeight:600}}>장 운영 중 — 1분마다 자동 갱신</span>
            : <span>장 마감 (다음 갱신: 09:00)</span>}
          {realtimeMeta.updated_at && (
            <span style={{marginLeft:'0.3rem'}}>· 마지막 업데이트: {realtimeMeta.updated_at}</span>
          )}
        </div>

        {/* 수집 중 배너 */}
        {(collectingCount > 0 || noDataCount > 0) && (
          <div style={{padding:'0.65rem 1rem',background:'rgba(45,212,191,0.08)',border:'1px solid rgba(45,212,191,0.25)',borderRadius:'8px',display:'flex',alignItems:'center',gap:'0.75rem'}}>
            <div style={{width:'10px',height:'10px',borderRadius:'50%',border:'2px solid var(--accent-mint)',borderTopColor:'transparent',animation:'spin 0.8s linear infinite',flexShrink:0}}/>
            <span style={{fontSize:'0.82rem',color:'var(--accent-mint)',fontWeight:600}}>
              {collectingCount > 0
                ? `${collectingCount}개 종목 실시간 데이터 수집 중... (20초마다 자동 새로고침)`
                : `${noDataCount}개 종목 주가 데이터 없음 — 조회 시 자동 수집됩니다.`}
            </span>
            <button onClick={load} style={{marginLeft:'auto',padding:'0.2rem 0.7rem',borderRadius:'4px',border:'1px solid rgba(45,212,191,0.4)',background:'transparent',color:'var(--accent-mint)',cursor:'pointer',fontSize:'0.75rem',whiteSpace:'nowrap'}}>
              지금 새로고침
            </button>
          </div>
        )}

        {/* 요약 카드 — 스크롤해도 상단 고정 */}
        <div style={{
          position:'sticky', top:0, zIndex:10,
          background:'var(--bg-dark)', paddingBottom:'0.5rem',
          marginBottom:'-0.5rem',
        }}>
        <div style={{display:'grid',gridTemplateColumns:'repeat(3,1fr)',gap:'0.75rem'}}>
          <div className="glass-panel" style={{padding:'1rem'}}>
            <p style={{fontSize:'0.75rem',color:'var(--text-secondary)',marginBottom:'0.3rem'}}>매입 총액</p>
            <h3 style={{fontSize:'1.05rem'}}>{fp(totalBuy)}원</h3>
          </div>
          <div className="glass-panel" style={{padding:'1rem'}}>
            <p style={{fontSize:'0.75rem',color:'var(--text-secondary)',marginBottom:'0.3rem'}}>평가 총액</p>
            <h3 style={{fontSize:'1.05rem',color:pc(totalVal-totalBuy)}}>{fp(totalVal)}원</h3>
          </div>
          <div className="glass-panel" style={{padding:'1rem'}}>
            <p style={{fontSize:'0.75rem',color:'var(--text-secondary)',marginBottom:'0.3rem'}}>총 손익</p>
            <h3 style={{fontSize:'1.05rem',color:pc(totalProfit)}}>
              {totalProfit>=0?'+':''}{fp(totalProfit)}원
            </h3>
            <p style={{fontSize:'0.78rem',color:pc(totalProfit),marginTop:'0.2rem',fontWeight:600}}>
              {Number(totalProfitPct)>=0?'+':''}{totalProfitPct}%
            </p>
            {/* [버그 ② 수정] 전일 대비 당일 손익 표시 */}
            {hasDailyData && (
              <div style={{marginTop:'0.4rem',paddingTop:'0.4rem',borderTop:'1px solid rgba(255,255,255,0.08)'}}>
                <p style={{fontSize:'0.68rem',color:'var(--text-secondary)',marginBottom:'0.15rem'}}>전일 대비</p>
                <p style={{fontSize:'0.82rem',fontWeight:700,color:pc(dailyProfit)}}>
                  {dailyProfit>=0?'+':''}{fp(dailyProfit)}원
                  {dailyProfitPct != null && (
                    <span style={{marginLeft:'0.3rem',fontSize:'0.75rem'}}>
                      ({Number(dailyProfitPct)>=0?'+':''}{dailyProfitPct}%)
                    </span>
                  )}
                </p>
              </div>
            )}
          </div>
        </div>
        </div>{/* sticky wrapper end */}

        {/* 탭 + 버튼 */}
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
          <div style={{display:'flex',gap:'0.4rem'}}>
            {[{k:'holdings',l:'보유종목'},{k:'tx',l:'거래내역'},{k:'kakao',l:'카카오 파싱'}].map(({k,l})=>(
              <button key={k} onClick={()=>setTab(k)} style={{
                padding:'0.35rem 0.9rem',borderRadius:'6px',fontSize:'0.8rem',cursor:'pointer',fontWeight:600,
                border:tab===k?'1px solid var(--accent-mint)':'1px solid var(--glass-border)',
                background:tab===k?'rgba(45,212,191,0.15)':'transparent',
                color:tab===k?'var(--accent-mint)':'var(--text-secondary)',
              }}>{l}</button>
            ))}
          </div>
          <div style={{display:'flex',gap:'0.5rem',alignItems:'center'}}>
            <span style={{fontSize:'0.72rem',color:'var(--text-secondary)'}}>💡 행 더블클릭 = 수정</span>
            {/* 엑셀 다운로드 */}
            <button onClick={()=>{ window.location.href='/api/portfolio/export/excel'; }}
              style={{padding:'0.4rem 0.8rem',borderRadius:'8px',
                background:'rgba(34,197,94,0.15)',border:'1px solid rgba(34,197,94,0.4)',
                color:'#22c55e',cursor:'pointer',fontWeight:600,fontSize:'0.82rem'}}>
              ⬇ 엑셀
            </button>
            {/* 엑셀 업로드 */}
            <label style={{padding:'0.4rem 0.8rem',borderRadius:'8px',
              background:'rgba(251,191,36,0.15)',border:'1px solid rgba(251,191,36,0.4)',
              color:'#fbbf24',cursor:'pointer',fontWeight:600,fontSize:'0.82rem'}}>
              ⬆ 업로드
              <input type="file" accept=".xlsx,.xls" style={{display:'none'}}
                onChange={async(e)=>{
                  const f = e.target.files[0]; if(!f) return;
                  const fd = new FormData(); fd.append('file', f);
                  const r = await fetch(API('/api/portfolio/import/excel'),{method:'POST',body:fd});
                  if(r.ok){
                    const d = await r.json();
                    alert(`업로드 완료\n성공: ${d.success_count}건\n실패: ${d.failed_count}건${d.failed.length>0?'\n실패항목: '+d.failed.map(x=>x.name).join(', '):''}`)
                    load();
                  } else { alert('업로드 실패'); }
                  e.target.value='';
                }}/>
            </label>
            <button onClick={()=>setShowModal(true)} style={{
              padding:'0.4rem 1rem',borderRadius:'8px',background:'var(--accent-purple)',
              border:'none',color:'#fff',cursor:'pointer',fontWeight:600,fontSize:'0.85rem',
            }}>+ 거래 입력</button>
          </div>
        </div>

        {/* 보유종목 탭 */}
        {tab==='holdings' && (
          portfolio.length===0 ? (
            <div className="glass-panel" style={{padding:'3rem',textAlign:'center',color:'var(--text-secondary)'}}>
              <p>보유 종목이 없습니다.</p>
            </div>
          ) : Object.entries(groups).map(([sector, items])=>{
            const sVal    = items.reduce((s,h)=>s+h.total_value,0);
            const sBuy    = items.reduce((s,h)=>s+h.buy_total,0);
            const sProfit = items.reduce((s,h)=>s+h.profit,0);
            const sPct    = sBuy>0?((sVal-sBuy)/sBuy*100).toFixed(1):0;
            return (
              <section key={sector} className="glass-panel" style={{overflow:'auto'}}>
                <div style={{padding:'0.6rem 1rem',borderBottom:'1px solid var(--glass-border)',display:'flex',justifyContent:'space-between',alignItems:'center'}}>
                  <span style={{fontSize:'0.85rem',fontWeight:700,color:'var(--accent-mint)'}}>{sector}</span>
                  <span style={{fontSize:'0.78rem',color:'var(--text-secondary)'}}>
                    평가액 {fp(sVal)}원&nbsp;|&nbsp;
                    <span style={{color:pc(sProfit)}}>
                      손익 {sProfit>=0?'+':''}{fp(sProfit)}원 ({Number(sPct)>=0?'+':''}{sPct}%)
                    </span>
                  </span>
                </div>
                <table className="premium-table" style={{width:'100%'}}>
                  <thead><tr>
                    <th style={{minWidth:'90px'}}>종목명</th>
                    <th style={{textAlign:'center',minWidth:'70px'}}>추세추종 신호</th>
                    <th style={{textAlign:'right',minWidth:'70px'}}>현재가</th>
                    <th style={{textAlign:'right',minWidth:'65px'}}>등락률</th>
                    <th style={{textAlign:'right',minWidth:'70px'}}>매입가</th>
                    <th style={{textAlign:'right',minWidth:'65px'}}>수익률</th>
                    <th style={{textAlign:'right',minWidth:'55px'}}>수량</th>
                    <th style={{textAlign:'right',minWidth:'90px'}}>손익</th>
                    <th style={{textAlign:'right',minWidth:'90px'}}>평가액</th>
                    <th style={{textAlign:'center',minWidth:'110px'}}>5일수급(외/기)</th>
                    <th style={{textAlign:'center',minWidth:'130px'}}>대차잔고</th>
                    <th></th>
                  </tr></thead>
                  <tbody>
                    {items.map(h => editRow===h.stock_code ? (
                      // ── 편집 모드 행 ──────────────────────────
                      <tr key={h.stock_code} style={{background:'rgba(45,212,191,0.07)'}}>
                        <td>
                          <input value={editForm.stock_name}
                            onChange={e => handleNameChange(e.target.value)}
                            style={{...inputStyle,textAlign:'left',width:'100px'}} placeholder="종목명"/>
                          {editCodePreview && editCodePreview.name !== h.stock_name && (
                            <div style={{fontSize:'0.65rem',color:'var(--accent-mint)',marginTop:'2px',whiteSpace:'nowrap'}}>
                              → {editCodePreview.name} ({editCodePreview.code})
                            </div>
                          )}
                          <input value={editForm.sector} onChange={e=>setEditForm(p=>({...p,sector:e.target.value}))}
                            style={{...inputStyle,textAlign:'left',width:'70px',marginTop:'2px'}} placeholder="섹터"/>
                        </td>
                        <td style={{textAlign:'right',color:'var(--text-secondary)'}}>{fp(h.current_price)}</td>
                        <td style={{textAlign:'right',color:pc(h.change_pct)}}>{pct(h.change_pct)}</td>
                        <td style={{textAlign:'right'}}>
                          <input value={editForm.avg_price} onChange={e=>setEditForm(p=>({...p,avg_price:e.target.value}))}
                            style={inputStyle} placeholder="매입가"/>
                        </td>
                        <td style={{textAlign:'right',color:'var(--text-secondary)'}}>-</td>
                        <td style={{textAlign:'right'}}>
                          <input value={editForm.quantity} onChange={e=>setEditForm(p=>({...p,quantity:e.target.value}))}
                            style={inputStyle} placeholder="수량"/>
                        </td>
                        <td colSpan={2} style={{textAlign:'center'}}>
                          <div style={{display:'flex',gap:'0.4rem',justifyContent:'center'}}>
                            <button onClick={()=>saveEdit(h.stock_code)} style={{
                              padding:'0.25rem 0.7rem',borderRadius:'5px',border:'none',
                              background:'var(--accent-mint)',color:'#000',cursor:'pointer',fontWeight:700,fontSize:'0.78rem',
                            }}>저장</button>
                            <button onClick={cancelEdit} style={{
                              padding:'0.25rem 0.7rem',borderRadius:'5px',
                              border:'1px solid var(--glass-border)',background:'transparent',
                              color:'var(--text-secondary)',cursor:'pointer',fontSize:'0.78rem',
                            }}>취소</button>
                          </div>
                        </td>
                        <td>
                          <button onClick={()=>deleteHolding(h.stock_code)} style={{
                            padding:'0.2rem 0.5rem',borderRadius:'4px',border:'none',
                            background:'rgba(251,113,133,0.15)',color:'var(--accent-red)',
                            cursor:'pointer',fontSize:'0.75rem',
                          }}>삭제</button>
                        </td>
                      </tr>
                    ) : (
                      // ── 일반 행 (더블클릭 → 편집 모드) ────────
                      <tr key={h.stock_code}
                        onDoubleClick={()=>startEdit(h)}
                        style={{cursor:'pointer', background: h.collecting ? 'rgba(45,212,191,0.03)' : undefined}}
                        title="더블클릭하면 수정 / 종목명 클릭하면 분석">
                        <td onClick={()=>{changeStock(h.stock_code);changeTab('analysis');}}
                          style={{minWidth:'90px',maxWidth:'130px'}}>
                          <div style={{fontWeight:600,display:'flex',alignItems:'center',gap:'0.4rem',whiteSpace:'nowrap',overflow:'hidden',textOverflow:'ellipsis'}}>
                            {h.stock_name}
                            {h.collecting && (
                              <span style={{display:'inline-flex',alignItems:'center',gap:'3px',fontSize:'0.62rem',color:'var(--accent-mint)',padding:'1px 5px',border:'1px solid rgba(45,212,191,0.35)',borderRadius:'4px',flexShrink:0}}>
                                <span style={{width:'5px',height:'5px',borderRadius:'50%',border:'1.5px solid var(--accent-mint)',borderTopColor:'transparent',display:'inline-block',animation:'spin 0.8s linear infinite'}}/>
                                수집중
                              </span>
                            )}
                          </div>
                          <div style={{fontSize:'0.7rem',color:'var(--text-secondary)'}}>{h.stock_code}</div>
                        </td>
                        {/* 매매 신호 — 4분면(추세×가치) */}
                        <td style={{textAlign:'center'}}>
                          {(() => {
                            const sig = h.trade_signal;
                            if(!sig) return <span style={{color:'rgba(255,255,255,0.2)',fontSize:'0.7rem'}}>-</span>;
                            const cfg = {
                              'add_buy':     {emoji:'💚', label:'추가매수', color:'#22c55e', bg:'rgba(34,197,94,0.18)'},
                              'strong_buy':  {emoji:'💚', label:'추가매수', color:'#22c55e', bg:'rgba(34,197,94,0.18)'},
                              'hold':        {emoji:'🟡', label:'보유유지', color:'#fbbf24', bg:'rgba(251,191,36,0.1)'},
                              'hold_value':  {emoji:'🔵', label:'홀딩유지', color:'#60a5fa', bg:'rgba(96,165,250,0.13)'},
                              'take_profit': {emoji:'🟠', label:'익절고려', color:'#f97316', bg:'rgba(249,115,22,0.14)'},
                              'caution':     {emoji:'🟠', label:'관망',    color:'#f97316', bg:'rgba(249,115,22,0.1)'},
                              'real_sell':   {emoji:'🔴', label:'진매도',  color:'#ef4444', bg:'rgba(239,68,68,0.18)'},
                              'sell':        {emoji:'🔴', label:'매도검토',color:'#ef4444', bg:'rgba(239,68,68,0.14)'},
                              'cut_loss':    {emoji:'⛔', label:'손절',    color:'#dc2626', bg:'rgba(220,38,38,0.22)'},
                              'strong_sell': {emoji:'⛔', label:'손절',    color:'#dc2626', bg:'rgba(220,38,38,0.22)'},
                            }[sig] || {emoji:'⚪', label:'중립', color:'#64748b', bg:'transparent'};
                            const ts = h.trend_score ?? 0;
                            const vs = h.val_score  ?? 0;
                            return (
                              <div title={h.trade_reason||''} style={{display:'flex',flexDirection:'column',alignItems:'center',
                                padding:'3px 5px',borderRadius:'6px',background:cfg.bg,cursor:'help',gap:'2px'}}>
                                <span style={{fontSize:'0.88rem',lineHeight:1}}>{cfg.emoji}</span>
                                <span style={{fontSize:'0.62rem',color:cfg.color,fontWeight:700}}>{cfg.label}</span>
                                <div style={{display:'flex',gap:'3px'}}>
                                  <span style={{fontSize:'0.5rem',padding:'0 3px',borderRadius:'3px',
                                    background: ts>=2?'rgba(34,197,94,0.2)': ts<=-2?'rgba(239,68,68,0.2)':'rgba(255,255,255,0.08)',
                                    color: ts>=2?'#22c55e': ts<=-2?'#ef4444':'rgba(255,255,255,0.4)'}}>
                                    추{ts>=0?'+':''}{ts}
                                  </span>
                                  <span style={{fontSize:'0.5rem',padding:'0 3px',borderRadius:'3px',
                                    background: vs>=2?'rgba(34,197,94,0.2)': vs<=-1?'rgba(239,68,68,0.2)':'rgba(255,255,255,0.08)',
                                    color: vs>=2?'#22c55e': vs<=-1?'#ef4444':'rgba(255,255,255,0.4)'}}>
                                    가{vs>=0?'+':''}{vs}
                                  </span>
                                </div>
                              </div>
                            );
                          })()}
                        </td>
                        <td style={{textAlign:'right',fontSize:'0.85rem',whiteSpace:'nowrap'}}>
                          {h.has_price===false
                            ? <span style={{fontSize:'0.72rem',color:'var(--accent-mint)'}}>{h.collecting?'수집중...':'미수집'}</span>
                            : fp(h.current_price)}
                        </td>
                        <td style={{textAlign:'right',color:h.has_price===false?'var(--text-secondary)':pc(h.change_pct)}}>
                          {h.has_price===false?'-':pct(h.change_pct)}
                        </td>
                        <td style={{textAlign:'right',fontSize:'0.85rem',whiteSpace:'nowrap'}}>{fp(h.avg_price)}</td>
                        <td style={{textAlign:'right',color:h.has_price===false?'var(--text-secondary)':pc(h.profit_pct),fontWeight:600}}>
                          {h.has_price===false?'-':pct(h.profit_pct)}
                        </td>
                        <td style={{textAlign:'right',fontSize:'0.85rem',whiteSpace:'nowrap'}}>{Math.round(h.quantity).toLocaleString('ko-KR')}</td>
                        <td style={{textAlign:'right',fontWeight:600,fontSize:'0.85rem',whiteSpace:'nowrap',color:h.has_price===false?'var(--text-secondary)':pc(h.profit)}}>
                          {h.has_price===false?'-':(h.profit>=0?'+':'')+fp(h.profit)}
                        </td>
                        <td style={{textAlign:'right',fontWeight:600,fontSize:'0.85rem',whiteSpace:'nowrap'}}>{fp(h.total_value)}</td>
                        {/* 수급 컬럼 */}
                        <td style={{textAlign:'center',whiteSpace:'nowrap'}}>
                          {(() => {
                            const frn  = h.frn_net_buy;   // null=데이터없음, 0=있지만0
                            const inst = h.inst_net_buy;
                            if(frn == null && inst == null) return <span style={{color:'rgba(255,255,255,0.2)',fontSize:'0.7rem'}}>-</span>;
                            // 소수점1자리 표시 (소형주 0.1억도 표시)
                            const fmt = (v) => {
                              if(v == null) return <span style={{color:'rgba(255,255,255,0.2)'}}>-</span>;
                              if(v === 0) return <span style={{color:'rgba(255,255,255,0.25)',fontSize:'0.7rem'}}>±0</span>;
                              const abs = Math.abs(v);
                              const disp = abs < 10 ? abs.toFixed(1) : Math.round(abs).toLocaleString();
                              return <span style={{color:v>0?'#ef4444':'#3b82f6',fontSize:'0.72rem',fontWeight:600}}>{v>0?'+':'-'}{disp}억</span>;
                            };
                            return (
                              <div style={{display:'flex',flexDirection:'column',gap:'1px',alignItems:'center'}}>
                                <div style={{display:'flex',gap:'3px',alignItems:'center'}}>
                                  <span style={{fontSize:'0.58rem',color:'rgba(255,255,255,0.3)'}}>외</span>{fmt(frn)}
                                </div>
                                <div style={{display:'flex',gap:'3px',alignItems:'center'}}>
                                  <span style={{fontSize:'0.58rem',color:'rgba(255,255,255,0.3)'}}>기</span>{fmt(inst)}
                                </div>
                              </div>
                            );
                          })()}
                        </td>
                        {/* 대차잔고 — 개별종목(/short-sell)과 동일 형식 */}
                        <td style={{textAlign:'center',whiteSpace:'nowrap'}}>
                          {(() => {
                            const sd = h.short_data;
                            if(!sd) return <span style={{color:'rgba(255,255,255,0.2)',fontSize:'0.7rem'}}>-</span>;
                            const fmtBal = (v) => {
                              const man = (v||0) / 10000;
                              return man >= 1 ? man.toFixed(1)+'만' : Math.round(v||0).toLocaleString();
                            };
                            // today vs avg5 (개별종목 API와 동일 기준)
                            const todayRising = (sd.today||0) > (sd.avg5||0) * 1.02;
                            const weekRising  = (sd.avg5||0)  > (sd.avg5_prev||0) * 1.02;
                            const lights = [
                              {label:'당일', val:sd.today,    signal:sd.today_signal, rising:todayRising},
                              {label:'5일↔', val:sd.avg5,     signal:sd.week_signal,  rising:weekRising},
                              {label:'10일', val:sd.avg10 ?? sd.avg5, signal: (sd.avg10||0) > (sd.avg10_prev||0)*1.02 ? 'red':'green', rising: (sd.avg10||0) > (sd.avg10_prev||0)*1.02},
                            ];
                            return (
                              <div style={{display:'flex',gap:'3px',justifyContent:'center'}}>
                                {lights.map(({label,val,rising})=>{
                                  const color = rising ? '#ef4444' : '#22c55e';
                                  return (
                                    <div key={label}
                                      title={`${label}: ${Math.round(val||0).toLocaleString()}주\n${rising?'증가추세(주의)':'감소추세(양호)'}`}
                                      style={{display:'flex',flexDirection:'column',alignItems:'center',
                                        padding:'2px 4px',borderRadius:'4px',cursor:'help',
                                        background:`${color}18`,border:`1px solid ${color}44`,minWidth:'34px'}}>
                                      <span style={{fontSize:'0.5rem',color:'rgba(255,255,255,0.4)',lineHeight:1.2}}>{label}</span>
                                      <span style={{fontSize:'0.6rem',fontWeight:700,color,lineHeight:1.2}}>{fmtBal(val)}</span>
                                      <span style={{fontSize:'0.52rem',color,lineHeight:1}}>{rising?'▲':'▼'}</span>
                                    </div>
                                  );
                                })}
                              </div>
                            );
                          })()}
                        </td>
                        <td>
                          <button onClick={(e)=>{e.stopPropagation();changeStock(h.stock_code);changeTab('analysis');}}
                            style={{padding:'0.2rem 0.5rem',borderRadius:'4px',border:'none',
                              background:'rgba(45,212,191,0.12)',color:'var(--accent-mint)',
                              cursor:'pointer',fontSize:'0.72rem',whiteSpace:'nowrap'}}>
                            분석↗
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>

                {/* ── 판단 로직 설명 ── */}
                <div style={{marginTop:'1.5rem',padding:'1.2rem',borderRadius:'10px',
                  background:'rgba(255,255,255,0.03)',border:'1px solid rgba(255,255,255,0.08)'}}>
                  <div style={{fontSize:'0.78rem',fontWeight:700,color:'rgba(255,255,255,0.7)',
                    marginBottom:'1rem',display:'flex',alignItems:'center',gap:'0.4rem'}}>
                    🧠 AI 전문가 판단 기준 (추세 × 가치 4분면)
                  </div>

                  {/* 4분면 매트릭스 */}
                  <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'0.6rem',marginBottom:'1rem'}}>
                    {[
                      {sig:'💚 추가매수', cond:'추세정배열 + 저평가',
                       desc:'이동평균 정배열(현재가>MA5>MA20>MA60)이며 PBR·PER 기준 저평가 상태. 기술적 추세와 내재가치가 모두 지지. 분할 추가매수 유효.',
                       bg:'rgba(34,197,94,0.08)',border:'rgba(34,197,94,0.25)'},
                      {sig:'🟡 보유유지', cond:'추세양호 + 적정가치',
                       desc:'추세는 유지되나 가치평가가 적정 수준. 신규 매수보다 기존 보유 유지가 적합. 손절선 이탈 시 매도로 전환.',
                       bg:'rgba(251,191,36,0.08)',border:'rgba(251,191,36,0.25)'},
                      {sig:'🔵 홀딩유지', cond:'추세이탈 + 저평가',
                       desc:'단기 추세가 무너졌으나 PBR/PER 기준 내재가치가 충분. 손실이 크지 않다면 추세 회복을 기다리는 홀딩 전략이 유리. 추가 매수는 분할로.',
                       bg:'rgba(96,165,250,0.08)',border:'rgba(96,165,250,0.25)'},
                      {sig:'🔴 진매도', cond:'추세역배열 + 고평가',
                       desc:'이동평균 역배열이면서 PBR·PER 기준 고평가. 추세와 가치 모두 하락 압력. 수익 중이라면 익절, 손실 중이라면 손절 집행 검토.',
                       bg:'rgba(239,68,68,0.08)',border:'rgba(239,68,68,0.25)'},
                    ].map(item=>(
                      <div key={item.sig} style={{padding:'0.75rem',borderRadius:'8px',
                        background:item.bg,border:`1px solid ${item.border}`}}>
                        <div style={{display:'flex',alignItems:'center',gap:'0.4rem',marginBottom:'0.3rem'}}>
                          <span style={{fontSize:'0.78rem',fontWeight:700}}>{item.sig}</span>
                          <span style={{fontSize:'0.65rem',color:'rgba(255,255,255,0.45)',padding:'0 5px',
                            borderRadius:'3px',background:'rgba(255,255,255,0.06)'}}>{item.cond}</span>
                        </div>
                        <p style={{fontSize:'0.68rem',color:'rgba(255,255,255,0.55)',lineHeight:1.5,margin:0}}>
                          {item.desc}
                        </p>
                      </div>
                    ))}
                  </div>

                  {/* 점수 계산 기준 */}
                  <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'0.8rem',marginBottom:'0.8rem'}}>
                    <div style={{padding:'0.7rem',borderRadius:'8px',background:'rgba(255,255,255,0.03)',
                      border:'1px solid rgba(255,255,255,0.07)'}}>
                      <div style={{fontSize:'0.7rem',fontWeight:700,color:'rgba(255,255,255,0.6)',marginBottom:'0.4rem'}}>
                        📈 추세 점수 계산 (추세 스코어)
                      </div>
                      <div style={{fontSize:'0.65rem',color:'rgba(255,255,255,0.45)',lineHeight:1.8}}>
                        <div><span style={{color:'#22c55e'}}>+4</span> 완전정배열 (현재가 &gt; MA5 &gt; MA20 &gt; MA60 &gt; MA120)</div>
                        <div><span style={{color:'#22c55e'}}>+3</span> 정배열 (현재가 &gt; MA5 &gt; MA20 &gt; MA60)</div>
                        <div><span style={{color:'#22c55e'}}>+2</span> 중기 정배열 (현재가 &gt; MA20 &gt; MA60)</div>
                        <div><span style={{color:'#22c55e'}}>+1</span> 단기 우위 (현재가 &gt; MA20)</div>
                        <div><span style={{color:'#f97316'}}> 0</span> 중립 (혼재)</div>
                        <div><span style={{color:'#ef4444'}}>-1</span> MA20 이탈</div>
                        <div><span style={{color:'#ef4444'}}>-2</span> 중기 역배열 (현재가 &lt; MA20 &lt; MA60)</div>
                        <div><span style={{color:'#ef4444'}}>-3</span> 역배열 (현재가 &lt; MA5 &lt; MA20 &lt; MA60)</div>
                        <div><span style={{color:'#ef4444'}}>-4</span> 완전역배열</div>
                      </div>
                    </div>
                    <div style={{padding:'0.7rem',borderRadius:'8px',background:'rgba(255,255,255,0.03)',
                      border:'1px solid rgba(255,255,255,0.07)'}}>
                      <div style={{fontSize:'0.7rem',fontWeight:700,color:'rgba(255,255,255,0.6)',marginBottom:'0.4rem'}}>
                        💎 가치 점수 계산 (가치 스코어)
                      </div>
                      <div style={{fontSize:'0.65rem',color:'rgba(255,255,255,0.45)',lineHeight:1.8}}>
                        <div><b style={{color:'rgba(255,255,255,0.6)'}}>PBR</b>: ≤0.5 <span style={{color:'#22c55e'}}>+4</span> / ≤1.0 <span style={{color:'#22c55e'}}>+3</span> / ≤2.0 <span style={{color:'#22c55e'}}>+1</span> / ≤4.0 <span style={{color:'#ef4444'}}>-1</span> / &gt;4 <span style={{color:'#ef4444'}}>-2</span></div>
                        <div><b style={{color:'rgba(255,255,255,0.6)'}}>PER</b>: ≤6 <span style={{color:'#22c55e'}}>+4</span> / ≤12 <span style={{color:'#22c55e'}}>+3</span> / ≤20 <span style={{color:'#22c55e'}}>+1</span> / ≤35 <span style={{color:'#ef4444'}}>-1</span> / &gt;35 <span style={{color:'#ef4444'}}>-2</span></div>
                        <div><b style={{color:'rgba(255,255,255,0.6)'}}>ROE</b>: ≥25% <span style={{color:'#22c55e'}}>+3</span> / ≥15% <span style={{color:'#22c55e'}}>+2</span> / ≥8% <span style={{color:'#22c55e'}}>+1</span> / &lt;0% <span style={{color:'#ef4444'}}>-2</span></div>
                        <div><b style={{color:'rgba(255,255,255,0.6)'}}>ROA</b>: ≥10% <span style={{color:'#22c55e'}}>+1</span> / &lt;0% <span style={{color:'#ef4444'}}>-1</span></div>
                        <div style={{marginTop:'0.3rem',color:'rgba(255,255,255,0.3)'}}>
                          ※ 바이오·신성장 종목은 PER 없음 → 가치데이터없음 처리, 추세만으로 판단
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* 보조 지표 */}
                  <div style={{padding:'0.6rem 0.8rem',borderRadius:'7px',background:'rgba(255,255,255,0.02)',
                    border:'1px solid rgba(255,255,255,0.06)',fontSize:'0.65rem',color:'rgba(255,255,255,0.4)',lineHeight:1.8}}>
                    <span style={{color:'rgba(255,255,255,0.5)',fontWeight:600}}>보조 지표 |</span>
                    &nbsp; <b>5일수급</b>: 최근 5거래일 외국인·기관 순매수 합계(억원, KIS amt 기준)
                    &nbsp;·&nbsp; <b>대차잔고</b>: 당일·5일평균·10일평균 차입잔고(주) — 증가(▲) = 공매도 세력 유입 주의
                    &nbsp;·&nbsp; <b>손절기준</b>: ATR(14) × 2 이하 하락 or 손익 -10% 도달
                    &nbsp;·&nbsp; <b>익절고려</b>: 추세양호하나 PBR/PER 고평가 구간 진입 시 또는 수익률 +20% 이상에서 추세 약화
                  </div>
                </div>
              </section>
            );
          })
        )}

        {/* 거래 내역 탭 */}
        {tab==='tx' && (
          <section className="glass-panel" style={{overflow:'auto'}}>
            <table className="premium-table" style={{width:'100%'}}>
              <thead><tr>
                <th>날짜</th><th>종목</th><th>구분</th>
                <th style={{textAlign:'right'}}>수량</th>
                <th style={{textAlign:'right'}}>단가</th>
                <th style={{textAlign:'right'}}>금액</th>
                <th>메모</th>
              </tr></thead>
              <tbody>
                {transactions.map(t=>(
                  <tr key={t.id}>
                    <td style={{fontSize:'0.8rem',color:'var(--text-secondary)'}}>{t.tx_date}</td>
                    <td style={{fontWeight:600}}>{t.stock_name} <span style={{fontSize:'0.7rem',color:'var(--text-secondary)'}}>{t.stock_code}</span></td>
                    <td><span style={{padding:'0.15rem 0.5rem',borderRadius:'4px',fontSize:'0.75rem',
                      background:t.tx_type==='buy'?'rgba(239,68,68,0.15)':'rgba(59,130,246,0.15)',
                      color:t.tx_type==='buy'?'#ef4444':'#3b82f6'}}>
                      {t.tx_type==='buy'?'매수':'매도'}
                    </span></td>
                    <td style={{textAlign:'right'}}>{Math.round(t.quantity).toLocaleString('ko-KR')}</td>
                    <td style={{textAlign:'right'}}>{fp(t.price)}</td>
                    <td style={{textAlign:'right'}}>{fp(t.quantity*t.price)}</td>
                    <td style={{fontSize:'0.75rem',color:'var(--text-secondary)',maxWidth:'180px',overflow:'hidden',textOverflow:'ellipsis'}}>{t.memo}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        )}

        {/* 카카오 파싱 탭 */}
        {tab==='kakao' && (
          <div style={{display:'flex',flexDirection:'column',gap:'1rem'}}>
            <div className="glass-panel" style={{padding:'1.2rem'}}>
              <p style={{fontSize:'0.8rem',color:'var(--text-secondary)',marginBottom:'0.5rem'}}>
                지원형식: [매수] 삼성전자 10주 75,000원 &nbsp;/&nbsp; 에이엘티 매수 50주 @12,000
              </p>
              <textarea value={kakaoText} onChange={e=>setKakaoText(e.target.value)}
                placeholder="카카오톡 메시지를 붙여넣으세요..."
                style={{width:'100%',minHeight:'100px',padding:'0.75rem',borderRadius:'8px',
                  background:'rgba(255,255,255,0.05)',border:'1px solid var(--glass-border)',
                  color:'#fff',fontSize:'0.85rem',resize:'vertical',fontFamily:'inherit'}}/>
              <button onClick={handleKakaoParse} style={{
                marginTop:'0.75rem',padding:'0.5rem 1.2rem',borderRadius:'8px',
                background:'var(--accent-mint)',border:'none',color:'#000',
                cursor:'pointer',fontWeight:700,fontSize:'0.85rem'}}>파싱하기</button>
            </div>
            {parsedTx.length>0 && (
              <section className="glass-panel" style={{overflow:'auto'}}>
                <div style={{padding:'0.6rem 1rem',borderBottom:'1px solid var(--glass-border)'}}>
                  <span style={{fontSize:'0.8rem',fontWeight:600,color:'var(--accent-mint)'}}>파싱 결과 ({parsedTx.length}건)</span>
                </div>
                <table className="premium-table" style={{width:'100%'}}>
                  <thead><tr><th>원문</th><th>구분</th><th>종목</th>
                    <th style={{textAlign:'right'}}>수량</th><th style={{textAlign:'right'}}>단가</th>
                    <th>상태</th><th></th></tr></thead>
                  <tbody>
                    {parsedTx.map((item,i)=>(
                      <tr key={i} style={{opacity:item.valid?1:0.5}}>
                        <td style={{fontSize:'0.75rem',color:'var(--text-secondary)',maxWidth:'140px',overflow:'hidden',textOverflow:'ellipsis'}}>{item.raw}</td>
                        <td><span style={{padding:'0.15rem 0.5rem',borderRadius:'4px',fontSize:'0.75rem',
                          background:item.tx_type==='buy'?'rgba(239,68,68,0.15)':'rgba(59,130,246,0.15)',
                          color:item.tx_type==='buy'?'#ef4444':'#3b82f6'}}>
                          {item.tx_type==='buy'?'매수':'매도'}</span></td>
                        <td style={{fontWeight:600}}>{item.stock_name} <span style={{fontSize:'0.7rem',color:'var(--text-secondary)'}}>{item.stock_code||'미인식'}</span></td>
                        <td style={{textAlign:'right'}}>{item.quantity?.toLocaleString('ko-KR')||'-'}</td>
                        <td style={{textAlign:'right'}}>{fp(item.price)}</td>
                        <td><span style={{fontSize:'0.75rem',color:item.valid?'var(--accent-mint)':'var(--accent-red)'}}>
                          {item.valid?'✓ 확인됨':'✗ 수동입력필요'}</span></td>
                        <td>{item.valid&&<button onClick={()=>applyParsed(item)} style={{
                          padding:'0.25rem 0.6rem',borderRadius:'6px',border:'none',
                          background:'rgba(45,212,191,0.15)',color:'var(--accent-mint)',
                          cursor:'pointer',fontSize:'0.75rem'}}>적용</button>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            )}
          </div>
        )}

        {/* 거래 입력 모달 */}
        {showModal && (() => {
          // 현재 입력한 종목코드로 보유 종목 찾기
          const matchHolding = portfolio.find(h => h.stock_code === form.stock_code);
          return (
          <div style={{position:'fixed',inset:0,background:'rgba(0,0,0,0.6)',display:'flex',alignItems:'center',justifyContent:'center',zIndex:100}}
            onClick={(e)=>{if(e.target===e.currentTarget)setShowModal(false);}}>
            <div className="glass-panel" style={{width:'440px',padding:'1.5rem',display:'flex',flexDirection:'column',gap:'0.75rem'}}
              onClick={e=>e.stopPropagation()}>
              <div style={{display:'flex',justifyContent:'space-between',alignItems:'center'}}>
                <h3 style={{fontSize:'1rem',fontWeight:700}}>거래 입력</h3>
                <button onClick={()=>setShowModal(false)} style={{background:'none',border:'none',color:'rgba(255,255,255,0.4)',cursor:'pointer',fontSize:'1.2rem'}}>×</button>
              </div>

              {/* 구분 선택 (맨 위로) */}
              <div style={{display:'flex',alignItems:'center',gap:'0.5rem'}}>
                <label style={{width:'80px',fontSize:'0.8rem',color:'var(--text-secondary)'}}>구분*</label>
                <div style={{display:'flex',gap:'0.5rem'}}>
                  {['buy','sell'].map(t=>(
                    <button key={t} onClick={()=>setForm(p=>({...p,tx_type:t}))} style={{
                      padding:'0.4rem 1.2rem',borderRadius:'6px',cursor:'pointer',fontWeight:700,fontSize:'0.9rem',
                      border:form.tx_type===t?`1px solid ${t==='buy'?'#ef4444':'#3b82f6'}`:'1px solid var(--glass-border)',
                      background:form.tx_type===t?(t==='buy'?'rgba(239,68,68,0.2)':'rgba(59,130,246,0.2)'):'transparent',
                      color:form.tx_type===t?(t==='buy'?'#ef4444':'#3b82f6'):'var(--text-secondary)',
                    }}>{t==='buy'?'매수':'매도'}</button>
                  ))}
                </div>
              </div>

              {/* 종목명 검색 → 코드 자동완성 */}
              <div style={{display:'flex',alignItems:'flex-start',gap:'0.5rem'}}>
                <label style={{width:'80px',fontSize:'0.8rem',color:'var(--text-secondary)',flexShrink:0,paddingTop:'0.4rem'}}>종목명 검색*</label>
                <div style={{flex:1,position:'relative'}}>
                  <input value={form.stock_name}
                    onChange={e=>{
                      const v = e.target.value;
                      setForm(p=>({...p, stock_name:v, stock_code:'', sector:''}));
                    }}
                    placeholder="종목명 입력 (예: 에이엘티, 삼성전자)"
                    style={{width:'100%',padding:'0.4rem 0.7rem',borderRadius:'6px',
                      background:'rgba(255,255,255,0.07)',border:'1px solid rgba(45,212,191,0.5)',
                      color:'#fff',fontSize:'0.85rem'}}/>
                  {/* 보유종목 드롭다운 */}
                  {form.stock_name && !form.stock_code && (() => {
                    const matches = portfolio.filter(h =>
                      h.stock_name?.includes(form.stock_name) ||
                      h.stock_code?.includes(form.stock_name)
                    ).slice(0,6);
                    if(!matches.length) return null;
                    return (
                      <div style={{position:'absolute',top:'100%',left:0,right:0,marginTop:'2px',
                        borderRadius:'6px',background:'#1a1a2e',border:'1px solid var(--glass-border)',
                        zIndex:20,overflow:'hidden',boxShadow:'0 4px 20px rgba(0,0,0,0.5)'}}>
                        {matches.map(h=>(
                          <div key={h.stock_code}
                            onClick={()=>setForm(p=>({...p,
                              stock_code:h.stock_code,
                              stock_name:h.stock_name,
                              sector:h.sector||'',
                              price: String(Math.round(h.current_price||h.avg_price||0))
                            }))}
                            style={{padding:'0.45rem 0.7rem',cursor:'pointer',fontSize:'0.82rem',
                              borderBottom:'1px solid rgba(255,255,255,0.05)',
                              display:'flex',justifyContent:'space-between',alignItems:'center'}}
                            onMouseEnter={e=>e.currentTarget.style.background='rgba(45,212,191,0.1)'}
                            onMouseLeave={e=>e.currentTarget.style.background='transparent'}>
                            <span>
                              <span style={{fontWeight:700,color:'#fff'}}>{h.stock_name}</span>
                              <span style={{fontSize:'0.7rem',color:'var(--text-secondary)',marginLeft:'0.4rem'}}>{h.stock_code}</span>
                            </span>
                            <span style={{fontSize:'0.72rem',color:'var(--accent-mint)'}}>
                              {Math.round(h.quantity).toLocaleString()}주
                            </span>
                          </div>
                        ))}
                      </div>
                    );
                  })()}
                  {/* 선택된 종목 표시 */}
                  {form.stock_code && (
                    <div style={{marginTop:'4px',padding:'0.3rem 0.6rem',borderRadius:'4px',
                      background:'rgba(45,212,191,0.12)',border:'1px solid rgba(45,212,191,0.3)',
                      fontSize:'0.72rem',color:'var(--accent-mint)',display:'flex',justifyContent:'space-between'}}>
                      <span>✓ {form.stock_name} ({form.stock_code}) — 보유 {Math.round(matchHolding?.quantity||0).toLocaleString()}주 @ {Math.round(matchHolding?.avg_price||0).toLocaleString()}원</span>
                      {form.tx_type==='sell' && matchHolding && (
                        <span style={{color:'#3b82f6',cursor:'pointer',fontWeight:700,marginLeft:'0.5rem'}}
                          onClick={()=>setForm(p=>({...p,quantity:String(Math.round(matchHolding.quantity))}))}>
                          전량↓
                        </span>
                      )}
                    </div>
                  )}
                </div>
              </div>

              {/* 나머지 필드 */}
              {[{label:'날짜',key:'tx_date',ph:'2026-03-31'},
                {label:'수량*',key:'quantity',ph:'100'},
                {label:'단가*',key:'price',ph:'75000'},
                {label:'메모',key:'memo',ph:''}].map(({label,key,ph})=>(
                <div key={key} style={{display:'flex',alignItems:'center',gap:'0.5rem'}}>
                  <label style={{width:'80px',fontSize:'0.8rem',color:'var(--text-secondary)',flexShrink:0}}>{label}</label>
                  <input value={form[key]} onChange={e=>setForm(p=>({...p,[key]:e.target.value}))}
                    placeholder={ph} style={{flex:1,padding:'0.4rem 0.7rem',borderRadius:'6px',
                      background:'rgba(255,255,255,0.07)',border:'1px solid var(--glass-border)',
                      color:'#fff',fontSize:'0.85rem'}}/>
                </div>
              ))}

              {/* 예상 거래금액 */}
              {form.quantity && form.price && (
                <div style={{padding:'0.4rem 0.7rem',borderRadius:'6px',background:'rgba(255,255,255,0.04)',
                  border:'1px solid var(--glass-border)',fontSize:'0.78rem',color:'var(--text-secondary)'}}>
                  예상 {form.tx_type==='buy'?'매수':'매도'}금액:
                  <span style={{color:'#fff',fontWeight:700,marginLeft:'0.4rem'}}>
                    {(Number(form.quantity)*Number(form.price)).toLocaleString('ko-KR')}원
                  </span>
                </div>
              )}

              <div style={{display:'flex',gap:'0.5rem',marginTop:'0.25rem'}}>
                <button onClick={saveTx} style={{flex:1,padding:'0.55rem',borderRadius:'8px',
                  background:form.tx_type==='buy'?'rgba(239,68,68,0.8)':'rgba(59,130,246,0.8)',
                  border:'none',color:'#fff',cursor:'pointer',fontWeight:700,fontSize:'0.95rem'}}>
                  {form.tx_type==='buy'?'매수 저장':'매도 저장'}
                </button>
                <button onClick={()=>{setShowModal(false);setForm({stock_code:"",stock_name:"",sector:"",tx_type:"buy",quantity:"",price:"",tx_date:"",memo:""}); }}
                  style={{flex:1,padding:'0.55rem',borderRadius:'8px',background:'transparent',
                    border:'1px solid var(--glass-border)',color:'var(--text-secondary)',cursor:'pointer'}}>취소</button>
              </div>
            </div>
          </div>
          );
        })()}
      </div>
    );
  };


  // ── 수출입 분석 2 ────────────────────────────────────────────
  const TradeAnalysis2 = () => {
    const HS_API = (path) => `/hs${path}`;
    const isMobile = useIsMobile();
    const fmt  = (v) => v == null ? '-' : Math.round(v).toLocaleString('ko-KR');
    const fmtB = (v) => v == null ? '-' : `$${(v/1e9).toFixed(2)}B`;
    const fmtM = (v) => v == null ? '-' : `$${(v/1e6).toFixed(2)}M`;
    const fmtAxis = (v) => {
      if (v == null) return '-';
      if (v >= 1e9) return `$${(v / 1e9).toFixed(1)}B`;
      if (v >= 1e6) return `$${(v / 1e6).toFixed(0)}M`;
      if (v >= 1e3) return `$${(v / 1e3).toFixed(0)}K`;
      return `$${Math.round(v)}`;
    };
    const fmtKg = (v) => {
      if (v == null) return '-';
      if (v >= 1e6) return `${(v / 1e6).toFixed(2)}M kg`;
      if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K kg`;
      return `${Math.round(v)} kg`;
    };
    const pct  = (v) => v == null ? <span style={{color:'rgba(255,255,255,0.3)'}}>-</span>
                       : <span style={{color: v > 0 ? '#ef4444' : v < 0 ? '#3b82f6' : 'rgba(255,255,255,0.4)', fontWeight:600}}>
                           {v > 0 ? '+' : ''}{v.toFixed(1)}%
                         </span>;
    const formatCompositionLabel = (sectorNames, hsNames) => {
      const sectorParts = String(sectorNames || '')
        .split(',')
        .map((v) => v.trim())
        .filter(Boolean);
      if (sectorParts.length > 0) return sectorParts.join(' / ');
      return String(hsNames || '')
        .split(',')
        .map((v) => v.trim())
        .filter(Boolean)
        .join(' / ');
    };

    const [sectors, setSectors]         = React.useState([]);
    const [selSector, setSelSector]     = React.useState(null);
    const [companies, setCompanies]     = React.useState([]);
    const [selCompany, setSelCompany]   = React.useState(null);
    const [compTrend, setCompTrend]     = React.useState(null);
    const [sectorHs, setSectorHs]       = React.useState(null);
    const [sectorTab, setSectorTab]     = React.useState('trend');
    const [sectorHsLoading, setSectorHsLoading] = React.useState(false);
    const [sectorPeriod, setSectorPeriod] = React.useState('');
    const [companyHs, setCompanyHs]     = React.useState(null);
    const [companyHsLoading, setCompanyHsLoading] = React.useState(false);
    const [companyPeriod, setCompanyPeriod] = React.useState('');
    const [months, setMonths]           = React.useState(24);
    const [loading, setLoading]         = React.useState(false);
    const [compLoading, setCompLoading] = React.useState(false);
    const [error, setError]             = React.useState('');

    // 섹터 데이터 로드
    const loadSectors = async () => {
      setLoading(true); setError('');
      try {
        const r = await fetch(HS_API(`/api/analysis2/sectors?months=${months}`));
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const d = await r.json();
        setSectors(d);
        if (d.length > 0) setSelSector((prev) => prev && d.find((x) => x.sector_key === prev.sector_key) ? d.find((x) => x.sector_key === prev.sector_key) : d[0]);
      } catch(e) { setError('섹터 데이터 로드 실패: ' + e.message); }
      finally { setLoading(false); }
    };

    // 섹터 선택 → 기업 목록 로드
    const loadCompanies = async (sectorKey) => {
      setSelCompany(null); setCompTrend(null); setCompanies([]); setCompanyHs(null); setCompanyPeriod('');
      try {
        const r = await fetch(HS_API(`/api/analysis2/sector/${sectorKey}/companies`));
        const d = await r.json();
        setCompanies(d);
        if (d.length > 0) {
          setSelCompany(d[0].stock_code);
          loadCompanyTrend(d[0].stock_code, sectorKey);
        }
      } catch {}
    };

    const loadSectorHs = async (sectorKey, periodYm = '') => {
      setSectorHsLoading(true);
      try {
        const qs = new URLSearchParams();
        if (periodYm) qs.set('period_ym', periodYm);
        const r = await fetch(HS_API(`/api/analysis2/sector/${sectorKey}/hs-breakdown?${qs.toString()}`));
        const d = await r.json();
        setSectorHs(d);
        setSectorPeriod(d?.period_ym || '');
      } catch {
        setSectorHs(null);
      } finally {
        setSectorHsLoading(false);
      }
    };

    // 기업 추세 로드
    const loadCompanyTrend = async (stockCode, sectorKey = selSector?.sector_key) => {
      setSelCompany(stockCode); setCompLoading(true);
      try {
        const qs = new URLSearchParams({ months: String(months) });
        if (sectorKey) qs.set('sector_key', sectorKey);
        const r = await fetch(HS_API(`/api/analysis2/company/${stockCode}/trend?${qs.toString()}`));
        const d = await r.json();
        setCompTrend(d);
        loadCompanyHs(stockCode, sectorKey, d?.latest_period || '');
      } catch {}
      finally { setCompLoading(false); }
    };

    const loadCompanyHs = async (stockCode, sectorKey = selSector?.sector_key, periodYm = '') => {
      if (!stockCode || !sectorKey) return;
      setCompanyHsLoading(true);
      try {
        const qs = new URLSearchParams({ sector_key: sectorKey });
        if (periodYm) qs.set('period_ym', periodYm);
        const r = await fetch(HS_API(`/api/analysis2/company/${stockCode}/hs-breakdown?${qs.toString()}`));
        const d = await r.json();
        setCompanyHs(d);
        setCompanyPeriod(d?.period_ym || '');
      } catch {
        setCompanyHs(null);
      } finally {
        setCompanyHsLoading(false);
      }
    };

    React.useEffect(() => { loadSectors(); }, [months]);
    React.useEffect(() => {
      if (selSector) {
        setSectorTab('trend');
        setSectorPeriod('');
        loadCompanies(selSector.sector_key);
        loadSectorHs(selSector.sector_key);
      }
    }, [selSector, months]);

    const TabButton = ({ active, onClick, children }) => (
      <button
        onClick={onClick}
        style={{
          padding:'0.35rem 0.8rem',
          borderRadius:'999px',
          fontSize:'0.76rem',
          cursor:'pointer',
          fontWeight: active ? 700 : 500,
          border: active ? '1px solid rgba(167,139,250,0.45)' : '1px solid var(--glass-border)',
          background: active ? 'rgba(167,139,250,0.14)' : 'rgba(255,255,255,0.04)',
          color: active ? 'var(--accent-purple)' : 'var(--text-secondary)',
        }}
      >
        {children}
      </button>
    );

    const BreakdownTable = ({ items, type = 'sector' }) => {
      if (!items || items.length === 0) {
        return (
          <div style={{padding:'2rem', textAlign:'center', color:'var(--text-secondary)'}}>
            선택 기간의 HS 구성 데이터가 없습니다.
          </div>
        );
      }
      return (
        <div style={{overflowX:'auto', maxHeight:'360px', overflowY:'auto'}}>
          <table className="premium-table" style={{minWidth:'1120px'}}>
            <thead>
              <tr>
                <th>HS 코드</th>
                <th>품목명</th>
                <th>매핑 상태</th>
                <th style={{textAlign:'right'}}>수출액</th>
                <th style={{textAlign:'right'}}>수출 비중</th>
                <th style={{textAlign:'right'}}>수출중량</th>
                <th style={{textAlign:'right'}}>수입액</th>
                <th style={{textAlign:'right'}}>수입 비중</th>
                <th style={{textAlign:'right'}}>수입중량</th>
              </tr>
            </thead>
            <tbody>
              {items.map((item) => (
                <tr key={`${type}-${item.hs_code}`}>
                  <td style={{fontFamily:'monospace', fontWeight:700}}>{item.hs_code}</td>
                  <td style={{maxWidth:'320px'}}>{item.hs_name}</td>
                  <td>
                    <span style={{
                      fontSize:'0.68rem',
                      padding:'0.14rem 0.45rem',
                      borderRadius:'999px',
                      background:
                        item.mapping_status === 'exact' ? 'rgba(52,211,153,0.14)' :
                        item.mapping_status === 'composite' ? 'rgba(250,204,21,0.14)' :
                        'rgba(248,113,113,0.14)',
                      color:
                        item.mapping_status === 'exact' ? '#34d399' :
                        item.mapping_status === 'composite' ? '#facc15' :
                        '#f87171',
                      border:'1px solid rgba(255,255,255,0.12)'
                    }}>
                      {item.mapping_status}
                    </span>
                  </td>
                  <td style={{textAlign:'right', fontWeight:700}}>{fmt(item.export_val)}</td>
                  <td style={{textAlign:'right', color:'#facc15'}}>{item.export_share == null ? '-' : `${item.export_share.toFixed(2)}%`}</td>
                  <td style={{textAlign:'right'}}>{fmt(item.export_kg)}</td>
                  <td style={{textAlign:'right', color:'#93c5fd'}}>{fmt(item.import_val)}</td>
                  <td style={{textAlign:'right', color:'#93c5fd'}}>{item.import_share == null ? '-' : `${item.import_share.toFixed(2)}%`}</td>
                  <td style={{textAlign:'right', color:'rgba(255,255,255,0.72)'}}>{fmt(item.import_kg)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    };

    // 미니 바 차트 렌더러
    const SparkBar = ({ monthly, color = '#a78bfa' }) => {
      if (!monthly || monthly.length === 0) return <span style={{color:'rgba(255,255,255,0.2)'}}>-</span>;
      const vals = monthly.map(m => m.export_val);
      const max = Math.max(...vals) || 1;
      const show = vals.slice(-12);
      const smax = Math.max(...show) || 1;
      return (
        <div style={{display:'flex', alignItems:'flex-end', gap:'1px', height:'28px', padding:'2px 0'}}>
          {show.map((v, i) => (
            <div key={i} style={{
              width: '6px', borderRadius: '2px 2px 0 0',
              background: i === show.length-1 ? '#f59e0b' : color,
              height: `${Math.max(3, (v / smax) * 24)}px`,
              opacity: 0.7 + i * 0.025,
            }} />
          ))}
        </div>
      );
    };

    const SectorChart = ({ sector }) => {
      if (!sector || !sector.monthly || sector.monthly.length === 0) {
        return (
          <div style={{padding:'2.5rem', textAlign:'center', color:'rgba(255,255,255,0.3)'}}>
            섹터 데이터를 선택하면 추세 차트가 표시됩니다.
          </div>
        );
      }
      const monthly = sector.monthly.slice(-24);
      const exports = monthly.map((m) => m.export_val || 0);
      const maxExport = Math.max(...exports, 1);
      const unitPrices = monthly.map((m) => (m.export_kg ? (m.export_val / m.export_kg) : null));
      const validUnitPrices = unitPrices.filter((v) => v != null);
      const maxUnitPrice = Math.max(...(validUnitPrices.length ? validUnitPrices : [1]));
      const minUnitPrice = Math.min(...(validUnitPrices.length ? validUnitPrices : [0]));

      return (
        <div style={{display:'flex', flexDirection:'column', gap:'1rem'}}>
          <div style={{display:'grid', gridTemplateColumns:isMobile ? '1fr 1fr' : 'repeat(6, minmax(0, 1fr))', gap:'0.75rem'}}>
            <div className="glass-panel" style={{padding:'0.8rem 1rem'}}>
              <p style={{fontSize:'0.68rem', color:'var(--text-secondary)'}}>선택 섹터</p>
              <p style={{fontSize:'0.95rem', fontWeight:800}}>{sector.label}</p>
            </div>
            <div className="glass-panel" style={{padding:'0.8rem 1rem'}}>
              <p style={{fontSize:'0.68rem', color:'var(--text-secondary)'}}>최신 수출액</p>
              <button
                onClick={() => {
                  setSectorTab('hs');
                  loadSectorHs(sector.sector_key, sector.latest_period || '');
                }}
                style={{fontSize:'0.95rem', fontWeight:800, color:'#a78bfa', background:'transparent', border:'none', padding:0, cursor:'pointer'}}
                title="클릭하면 최신 수출액의 HS 구성표를 확인합니다"
              >
                {fmtB(sector.export_latest)}
              </button>
            </div>
            <div className="glass-panel" style={{padding:'0.8rem 1rem'}}>
              <p style={{fontSize:'0.68rem', color:'var(--text-secondary)'}}>수출 전월 대비</p>
              <p style={{fontSize:'0.95rem', fontWeight:800}}>{pct(sector.export_mom)}</p>
            </div>
            <div className="glass-panel" style={{padding:'0.8rem 1rem'}}>
              <p style={{fontSize:'0.68rem', color:'var(--text-secondary)'}}>수출 전년 동월</p>
              <p style={{fontSize:'0.95rem', fontWeight:800}}>{pct(sector.export_yoy)}</p>
            </div>
            <div className="glass-panel" style={{padding:'0.8rem 1rem'}}>
              <p style={{fontSize:'0.68rem', color:'var(--text-secondary)'}}>최신 수입액</p>
              <p style={{fontSize:'0.95rem', fontWeight:800, color:'#60a5fa'}}>{fmtB(sector.import_latest)}</p>
            </div>
            <div className="glass-panel" style={{padding:'0.8rem 1rem'}}>
              <p style={{fontSize:'0.68rem', color:'var(--text-secondary)'}}>수입 전월 대비</p>
              <p style={{fontSize:'0.95rem', fontWeight:800}}>{pct(sector.import_mom)}</p>
            </div>
          </div>

          <div className="glass-panel" style={{padding:'0.75rem 1rem', display:'flex', alignItems:'center', gap:'0.75rem', flexWrap:'wrap'}}>
            <span style={{display:'inline-flex', alignItems:'center', gap:'0.35rem', fontSize:'0.78rem', color:'#fff'}}>
              <span style={{width:'10px', height:'10px', borderRadius:'3px', background:'rgba(250,204,21,0.52)', display:'inline-block'}} />
              수출 금액
            </span>
            <span style={{display:'inline-flex', alignItems:'center', gap:'0.35rem', fontSize:'0.78rem', color:'#fff'}}>
              <span style={{width:'10px', height:'10px', borderRadius:'3px', background:'rgba(96,165,250,0.42)', display:'inline-block'}} />
              수입 금액
            </span>
            <span style={{display:'inline-flex', alignItems:'center', gap:'0.35rem', fontSize:'0.78rem', color:'#fff'}}>
              <span style={{width:'18px', height:'2px', background:'#93c5fd', display:'inline-block'}} />
              수출 평균단가
            </span>
            <span style={{fontSize:'0.75rem', color:'var(--text-secondary)', marginLeft:'auto'}}>
              수입은 원자재/부품 선행 신호로 분리 표시됩니다
            </span>
          </div>

          <div className="glass-panel" style={{padding:'1rem', overflowX:'auto'}}>
            <svg viewBox={`0 0 ${monthly.length * 28 + 60} 230`} style={{width:'100%', minWidth:`${monthly.length * 28 + 60}px`, height:'230px'}}>
              {[0,1,2,3,4].map((i) => (
                <line key={i} x1="40" x2={monthly.length * 28 + 40} y1={20 + i * 40} y2={20 + i * 40} stroke="rgba(255,255,255,0.05)" strokeWidth="1" />
              ))}
              {monthly.map((m, i) => {
                const exportH = Math.max(4, (m.export_val / maxExport) * 165);
                const importH = Math.max(2, ((m.import_val || 0) / maxExport) * 165);
                const x = 42 + i * 28;
                return (
                  <g key={m.period_ym}>
                    <rect x={x} y={190 - exportH} width={12} height={exportH}
                      fill={i >= monthly.length - 3 ? 'rgba(52,211,153,0.48)' : 'rgba(250,204,21,0.52)'}
                      rx="3">
                      <title>{`${m.period_ym} | 수출액 ${fmt(m.export_val)} | 수출중량 ${fmt(m.export_kg)}kg`}</title>
                    </rect>
                    <rect x={x + 13} y={190 - importH} width={8} height={importH}
                      fill="rgba(96,165,250,0.42)" rx="3">
                      <title>{`${m.period_ym} | 수입액 ${fmt(m.import_val)} | 수입중량 ${fmt(m.import_kg)}kg`}</title>
                    </rect>
                  </g>
                );
              })}
              {monthly.length > 1 && validUnitPrices.length > 0 && (
                <polyline
                  fill="none"
                  stroke="#93c5fd"
                  strokeWidth="2.4"
                  strokeLinejoin="round"
                  strokeLinecap="round"
                  points={monthly.map((m, i) => {
                    const unit = m.export_kg ? (m.export_val / m.export_kg) : minUnitPrice;
                    const y = 190 - (((unit - minUnitPrice) / ((maxUnitPrice - minUnitPrice) || 1)) * 165);
                    return `${42 + i * 28 + 9},${y}`;
                  }).join(' ')}
                />
              )}
              {monthly.filter((_, i) => i % 3 === 0 || i === monthly.length - 1).map((m) => {
                const i = monthly.findIndex((x) => x.period_ym === m.period_ym);
                return (
                  <text key={m.period_ym} x={42 + i * 28 + 9} y={208} fontSize="8" fill="rgba(255,255,255,0.45)" textAnchor="middle">
                    {m.period_ym.slice(2)}
                  </text>
                );
              })}
              {[0,1,2,3,4].map((i) => {
                const value = maxExport - ((maxExport / 4) * i);
                return (
                  <text key={i} x="34" y={24 + i * 40} fontSize="9" fill="rgba(255,255,255,0.42)" textAnchor="end">
                    {fmtAxis(value)}
                  </text>
                );
              })}
              <text x={monthly.length * 28 + 48} y="18" fontSize="9" fill="rgba(255,255,255,0.42)">${maxUnitPrice.toFixed(0)}</text>
              <text x={monthly.length * 28 + 48} y="192" fontSize="9" fill="rgba(255,255,255,0.28)">${minUnitPrice.toFixed(0)}</text>
            </svg>
          </div>
        </div>
      );
    };

    // 기업 라인 차트
    const CompanyChart = ({ data }) => {
      if (!data || !data.monthly || data.monthly.length === 0) {
        return <div style={{padding:'3rem', textAlign:'center', color:'rgba(255,255,255,0.3)'}}>데이터 없음</div>;
      }
      const monthly = data.monthly;
      const exportVals = monthly.map(m => m.export_val || 0);
      const importVals = monthly.map(m => m.import_val || 0);
      const maxV = Math.max(...exportVals, ...importVals) || 1;
      const minV = 0;

      return (
        <div style={{width:'100%'}}>
          <div style={{display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:'0.5rem'}}>
            <div style={{display:'flex', gap:'1rem', flexWrap:'wrap'}}>
              <div className="glass-panel" style={{padding:'0.5rem 1rem', minWidth:'120px'}}>
                <p style={{fontSize:'0.65rem', color:'var(--text-secondary)'}}>최신 수출액</p>
                <p style={{fontSize:'1rem', fontWeight:700, color:'#a78bfa'}}>{fmtB(data.export_latest)}</p>
              </div>
              <div className="glass-panel" style={{padding:'0.5rem 1rem', minWidth:'100px'}}>
                <p style={{fontSize:'0.65rem', color:'var(--text-secondary)'}}>수출 전월 대비</p>
                <p style={{fontSize:'1rem', fontWeight:700}}>{pct(data.export_mom)}</p>
              </div>
              <div className="glass-panel" style={{padding:'0.5rem 1rem', minWidth:'100px'}}>
                <p style={{fontSize:'0.65rem', color:'var(--text-secondary)'}}>수출 전년 동월</p>
                <p style={{fontSize:'1rem', fontWeight:700}}>{pct(data.export_yoy)}</p>
              </div>
              <div className="glass-panel" style={{padding:'0.5rem 1rem', minWidth:'120px'}}>
                <p style={{fontSize:'0.65rem', color:'var(--text-secondary)'}}>관련 수입액</p>
                <p style={{fontSize:'1rem', fontWeight:700, color:'#60a5fa'}}>{fmtM(data.import_latest)}</p>
              </div>
              <div className="glass-panel" style={{padding:'0.5rem 1rem', minWidth:'100px'}}>
                <p style={{fontSize:'0.65rem', color:'var(--text-secondary)'}}>수입 전월 대비</p>
                <p style={{fontSize:'1rem', fontWeight:700}}>{pct(data.import_mom)}</p>
              </div>
              <div className="glass-panel" style={{padding:'0.5rem 1rem'}}>
                <p style={{fontSize:'0.65rem', color:'var(--text-secondary)'}}>관련 HS 섹터</p>
                <p style={{fontSize:'0.75rem', color:'var(--text-secondary)', maxWidth:'280px', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>
                  {data.hs_names || monthly[monthly.length-1]?.hs_names || '-'}
                </p>
              </div>
            </div>
          </div>
          {/* 바+라인 차트 */}
          <div style={{overflowX:'auto'}}>
            <svg viewBox={`0 0 ${monthly.length * 20 + 40} 200`} style={{width:'100%', minWidth:`${monthly.length * 20 + 40}px`, height:'200px'}}>
              {/* 그리드 라인 */}
              {[0,1,2,3].map(i => (
                <line key={i} x1="35" x2={monthly.length * 20 + 35} y1={10 + i*45} y2={10 + i*45}
                  stroke="rgba(255,255,255,0.05)" strokeWidth="1"/>
              ))}
              {/* 바 */}
              {monthly.map((m, i) => {
                const exportH = Math.max(2, ((m.export_val - minV) / (maxV - minV || 1)) * 160);
                const importH = Math.max(0, ((m.import_val - minV) / (maxV - minV || 1)) * 160);
                const x = 35 + i * 20;
                const isLatest = i === monthly.length - 1;
                return (
                  <g key={i}>
                    <rect x={x+1} y={190 - exportH} width={11} height={exportH}
                      fill={isLatest ? 'rgba(245,158,11,0.5)' : 'rgba(167,139,250,0.25)'}
                      rx="2">
                      <title>{`${m.period_ym}: 수출 ${(m.export_val/1e6).toFixed(1)}M`}</title>
                    </rect>
                    <rect x={x+13} y={190 - importH} width={4} height={importH}
                      fill="rgba(96,165,250,0.55)" rx="2">
                      <title>{`${m.period_ym}: 수입 ${(m.import_val/1e6).toFixed(1)}M`}</title>
                    </rect>
                  </g>
                );
              })}
              {/* 라인 */}
              {monthly.length > 1 && (
                <polyline
                  fill="none"
                  stroke="#a78bfa"
                  strokeWidth="2"
                  strokeLinejoin="round"
                  points={monthly.map((m, i) => {
                    const exportH = ((m.export_val - minV) / (maxV - minV || 1)) * 160;
                    return `${35 + i * 20 + 8},${190 - exportH}`;
                  }).join(' ')}
                />
              )}
              {/* X축 라벨 (6개월 간격) */}
              {monthly.filter((_, i) => i % 6 === 0).map((m, idx) => {
                const origI = monthly.findIndex(x => x.period_ym === m.period_ym);
                return (
                  <text key={idx} x={35 + origI * 20 + 8} y={198} fontSize="7" fill="rgba(255,255,255,0.4)" textAnchor="middle">
                    {m.period_ym.slice(2)}
                  </text>
                );
              })}
              {/* Y축 */}
              <text x="30" y="15" fontSize="7" fill="rgba(255,255,255,0.3)" textAnchor="end">
                {(maxV/1e6).toFixed(0)}M
              </text>
              <text x="30" y="190" fontSize="7" fill="rgba(255,255,255,0.3)" textAnchor="end">
                {(minV/1e6).toFixed(0)}M
              </text>
            </svg>
          </div>
        </div>
      );
    };

    const sectorColors = {
      semiconductors: '#a78bfa', autos: '#60a5fa', batteries: '#34d399',
      biotech: '#f87171', consumer: '#fb923c', shipbuilding: '#38bdf8', energy_materials: '#facc15',
    };

    return (
      <div style={{padding:'1.5rem', display:'flex', flexDirection:'column', gap:'1.5rem'}}>
        {/* 헤더 */}
        <div style={{display:'flex', justifyContent:'space-between', alignItems:'center', flexWrap:'wrap', gap:'0.75rem'}}>
          <div>
            <h1 style={{fontSize:'1.4rem', fontWeight:800, color:'#fff', margin:0}}>📦 수출입 분석 II</h1>
            <p style={{fontSize:'0.78rem', color:'var(--text-secondary)', marginTop:'0.2rem'}}>
              섹터별 수출 추세 분석 → 관련 기업 투자 기회 탐색
            </p>
          </div>
          <div style={{display:'flex', alignItems:'center', gap:'0.5rem'}}>
            <span style={{fontSize:'0.75rem', color:'var(--text-secondary)'}}>기간:</span>
            {[12,24,36].map(m => (
              <button key={m} onClick={() => setMonths(m)} style={{
                padding:'0.25rem 0.65rem', borderRadius:'6px', fontSize:'0.75rem', cursor:'pointer',
                fontWeight: months === m ? 700 : 400,
                border: months === m ? '1px solid var(--accent-purple)' : '1px solid var(--glass-border)',
                background: months === m ? 'rgba(167,139,250,0.15)' : 'transparent',
                color: months === m ? 'var(--accent-purple)' : 'var(--text-secondary)',
              }}>{m}개월</button>
            ))}
            <button onClick={loadSectors} disabled={loading}
              style={{padding:'0.25rem 0.65rem', borderRadius:'6px', fontSize:'0.75rem', cursor:'pointer',
                border:'1px solid var(--glass-border)', background:'rgba(255,255,255,0.05)', color:'var(--text-secondary)'}}>
              {loading ? '⏳' : '🔄'}
            </button>
          </div>
        </div>

        {error && (
          <div style={{padding:'0.75rem 1rem', background:'rgba(239,68,68,0.12)', border:'1px solid rgba(239,68,68,0.3)',
            borderRadius:'10px', color:'#f87171', fontSize:'0.8rem'}}>⚠️ {error}</div>
        )}

        {/* ── 상단: 섹터별 수출 추세 표 ── */}
        <div className="glass-panel" style={{overflow:'hidden'}}>
          <div style={{padding:'0.9rem 1.2rem', borderBottom:'1px solid var(--glass-border)', display:'flex', alignItems:'center', gap:'0.5rem'}}>
            <span style={{fontSize:'1rem'}}>🏭</span>
            <h2 style={{margin:0, fontSize:'1rem', fontWeight:700}}>섹터별 수출 추세</h2>
            <span style={{fontSize:'0.72rem', color:'var(--text-secondary)', marginLeft:'auto'}}>
              클릭하면 해당 섹터 기업이 아래에 표시됩니다
            </span>
          </div>
          {loading ? (
            <div style={{padding:'3rem', textAlign:'center', color:'var(--text-secondary)'}}>
              <div style={{width:'28px', height:'28px', border:'2px solid rgba(167,139,250,0.3)', borderTop:'2px solid var(--accent-purple)',
                borderRadius:'50%', animation:'spin 0.8s linear infinite', margin:'0 auto 0.5rem'}} />
              데이터 로딩 중...
            </div>
          ) : (
            <div style={{overflowX:'auto'}}>
              <table className="premium-table" style={{minWidth:'930px'}}>
                <thead><tr>
                  <th>섹터</th>
                  <th style={{textAlign:'right'}}>최신 수출액</th>
                  <th style={{textAlign:'right'}}>최신 수입액</th>
                  <th style={{textAlign:'center'}}>수출 MoM</th>
                  <th style={{textAlign:'center'}}>수입 MoM</th>
                  <th style={{textAlign:'center'}}>수출 YoY</th>
                  <th style={{textAlign:'center'}}>최근 12개월 추세</th>
                </tr></thead>
                <tbody>
                  {sectors.map(s => {
                    const color = sectorColors[s.sector_key] || '#a78bfa';
                    const isSelected = selSector?.sector_key === s.sector_key;
                    return (
                      <tr key={s.sector_key}
                        onClick={() => { setSelSector(s); }}
                        style={{
                          cursor:'pointer',
                          background: isSelected ? 'rgba(167,139,250,0.1)' : undefined,
                          borderLeft: isSelected ? `3px solid ${color}` : '3px solid transparent',
                          transition:'background 0.15s',
                        }}>
                        <td>
                          <div style={{display:'flex', alignItems:'center', gap:'0.5rem'}}>
                            <span style={{width:'8px', height:'8px', borderRadius:'50%', background:color, display:'inline-block', flexShrink:0}} />
                            <span style={{fontWeight:600}}>{s.label}</span>
                          </div>
                        </td>
                        <td style={{textAlign:'right', fontWeight:700, color:'#fff'}}>
                          {fmtB(s.export_latest)}
                        </td>
                        <td style={{textAlign:'right', fontWeight:700, color:'#93c5fd'}}>
                          {fmtB(s.import_latest)}
                        </td>
                        <td style={{textAlign:'center'}}>{pct(s.export_mom)}</td>
                        <td style={{textAlign:'center'}}>{pct(s.import_mom)}</td>
                        <td style={{textAlign:'center'}}>{pct(s.export_yoy)}</td>
                        <td style={{textAlign:'center'}}>
                          <SparkBar monthly={s.monthly} color={color} />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div className="glass-panel" style={{overflow:'hidden'}}>
          <div style={{padding:'0.9rem 1.2rem', borderBottom:'1px solid var(--glass-border)', display:'flex', alignItems:'center', gap:'0.75rem', flexWrap:'wrap'}}>
            <span style={{fontSize:'1rem'}}>📈</span>
            <h2 style={{margin:0, fontSize:'1rem', fontWeight:700}}>
              선택 섹터 상세 분석
              {selSector && <span style={{color:'var(--accent-purple)', marginLeft:'0.5rem'}}>{selSector.label}</span>}
            </h2>
          </div>

          <div style={{padding:'1rem 1.2rem'}}>
            {!selSector ? (
              <div style={{padding:'3rem', textAlign:'center', color:'rgba(255,255,255,0.3)'}}>
                <p style={{fontSize:'2rem', marginBottom:'0.5rem'}}>☝️</p>
                <p>위 섹터 표에서 관심 섹터를 클릭하세요</p>
              </div>
            ) : (
              <div style={{display:'flex', justifyContent:'space-between', alignItems:'center', gap:'0.75rem', flexWrap:'wrap', marginBottom:'1rem'}}>
                <div style={{display:'flex', gap:'0.45rem', flexWrap:'wrap'}}>
                  <TabButton active={sectorTab === 'trend'} onClick={() => setSectorTab('trend')}>월별 추세</TabButton>
                  <TabButton active={sectorTab === 'hs'} onClick={() => {
                    setSectorTab('hs');
                    if (!sectorHs) loadSectorHs(selSector.sector_key, selSector.latest_period || '');
                  }}>HS 구성</TabButton>
                </div>
                {sectorTab === 'hs' && (
                  <div style={{display:'flex', alignItems:'center', gap:'0.5rem'}}>
                    <span style={{fontSize:'0.74rem', color:'var(--text-secondary)'}}>기준월</span>
                    <select
                      value={sectorPeriod || sectorHs?.period_ym || ''}
                      onChange={(e) => loadSectorHs(selSector.sector_key, e.target.value)}
                      style={{
                        padding:'0.3rem 0.65rem', borderRadius:'7px', fontSize:'0.78rem',
                        background:'rgba(255,255,255,0.07)', border:'1px solid var(--glass-border)', color:'#fff'
                      }}
                    >
                      {(sectorHs?.periods || []).map((period) => (
                        <option key={period} value={period} style={{background:'#1a1a2e'}}>{period}</option>
                      ))}
                    </select>
                  </div>
                )}
              </div>
            )}
            {!selSector ? null : sectorTab === 'trend' ? (
              <div style={{display:'flex', flexDirection:'column', gap:'1rem'}}>
                <SectorChart sector={selSector} />
                <div style={{
                  border:'1px solid var(--glass-border)',
                  borderRadius:'12px',
                  background:'rgba(255,255,255,0.03)',
                  overflow:'hidden'
                }}>
                  <div style={{
                    padding:'0.8rem 1rem',
                    borderBottom:'1px solid var(--glass-border)',
                    display:'flex',
                    justifyContent:'space-between',
                    alignItems:'center',
                    gap:'0.75rem',
                    flexWrap:'wrap'
                  }}>
                    <div>
                      <div style={{fontSize:'0.9rem', fontWeight:700, color:'#fff'}}>해당 수출액을 구성하는 HS 상세</div>
                      <div style={{fontSize:'0.74rem', color:'var(--text-secondary)', marginTop:'0.15rem'}}>
                        선택 섹터의 최신 기준월 HS 코드별 수출/수입 비중
                      </div>
                    </div>
                    <button
                      onClick={() => {
                        setSectorTab('hs');
                        if (!sectorHs) loadSectorHs(selSector.sector_key, selSector.latest_period || '');
                      }}
                      style={{
                        padding:'0.35rem 0.75rem',
                        borderRadius:'999px',
                        fontSize:'0.75rem',
                        border:'1px solid rgba(167,139,250,0.35)',
                        background:'rgba(167,139,250,0.12)',
                        color:'var(--accent-purple)',
                        cursor:'pointer'
                      }}
                    >
                      전체 HS 구성 보기
                    </button>
                  </div>
                  {sectorHsLoading ? (
                    <div style={{padding:'1.5rem', textAlign:'center', color:'var(--text-secondary)'}}>HS 상세를 불러오는 중...</div>
                  ) : (
                    <BreakdownTable items={(sectorHs?.items || []).slice(0, 8)} type="sector" />
                  )}
                </div>
              </div>
            ) : sectorHsLoading ? (
              <div style={{padding:'3rem', textAlign:'center', color:'var(--text-secondary)'}}>
                HS 구성 데이터를 불러오는 중...
              </div>
            ) : (
              <BreakdownTable items={sectorHs?.items || []} type="sector" />
            )}
          </div>
        </div>

        {/* ── 하단: 기업별 수출 추세 ── */}
        <div className="glass-panel" style={{overflow:'hidden'}}>
          <div style={{padding:'0.9rem 1.2rem', borderBottom:'1px solid var(--glass-border)', display:'flex', alignItems:'center', gap:'0.75rem', flexWrap:'wrap'}}>
            <span style={{fontSize:'1rem'}}>🏢</span>
            <h2 style={{margin:0, fontSize:'1rem', fontWeight:700}}>
              기업별 수출 추세
              {selSector && <span style={{color:'var(--accent-purple)', marginLeft:'0.5rem'}}>{selSector.label}</span>}
            </h2>
            {/* 기업 드롭다운 */}
            {companies.length > 0 && (
              <select
                value={selCompany || ''}
                onChange={e => loadCompanyTrend(e.target.value)}
                style={{
                  padding:'0.3rem 0.65rem', borderRadius:'7px', fontSize:'0.82rem',
                  background:'rgba(255,255,255,0.07)', border:'1px solid var(--glass-border)',
                  color:'#fff', cursor:'pointer', outline:'none', marginLeft:'auto',
                }}>
                {companies.map(c => (
                  <option key={c.stock_code} value={c.stock_code} style={{background:'#1a1a2e'}}>
                    {c.stock_name} ({c.stock_code}){formatCompositionLabel(c.sector_name, c.hs_names) ? ` - ${formatCompositionLabel(c.sector_name, c.hs_names)}` : ''}
                  </option>
                ))}
              </select>
            )}
          </div>

          <div style={{padding:'1rem 1.2rem'}}>
            {!selSector ? (
              <div style={{padding:'3rem', textAlign:'center', color:'rgba(255,255,255,0.3)'}}>
                <p style={{fontSize:'2rem', marginBottom:'0.5rem'}}>☝️</p>
                <p>위 섹터 표에서 관심 섹터를 클릭하세요</p>
              </div>
            ) : compLoading ? (
              <div style={{padding:'3rem', textAlign:'center', color:'var(--text-secondary)'}}>
                <div style={{width:'28px', height:'28px', border:'2px solid rgba(167,139,250,0.3)', borderTop:'2px solid var(--accent-purple)',
                  borderRadius:'50%', animation:'spin 0.8s linear infinite', margin:'0 auto 0.5rem'}} />
                기업 데이터 로딩 중...
              </div>
            ) : compTrend ? (
              <div>
                <div style={{display:'flex', alignItems:'center', gap:'0.5rem', marginBottom:'1rem'}}>
                  <span style={{fontSize:'1.1rem', fontWeight:800, color:'#fff'}}>{compTrend.stock_name}</span>
                  <span style={{fontSize:'0.75rem', color:'var(--text-secondary)'}}>({compTrend.stock_code})</span>
                  {String(compTrend.sector_name || '')
                    .split(',')
                    .filter(Boolean)
                    .map((sectorName) => (
                      <span key={sectorName} style={{fontSize:'0.72rem', padding:'0.1rem 0.5rem', borderRadius:'10px',
                        background:'rgba(167,139,250,0.15)', color:'var(--accent-purple)', border:'1px solid rgba(167,139,250,0.3)'}}>
                        {sectorName}
                      </span>
                    ))}
                  <span style={{fontSize:'0.68rem', padding:'0.12rem 0.45rem', borderRadius:'999px',
                    background:
                      compTrend.mapping_status === 'exact' ? 'rgba(52,211,153,0.14)' :
                      compTrend.mapping_status === 'composite' ? 'rgba(250,204,21,0.14)' :
                      'rgba(248,113,113,0.14)',
                    color:
                      compTrend.mapping_status === 'exact' ? '#34d399' :
                      compTrend.mapping_status === 'composite' ? '#facc15' :
                      '#f87171',
                    border:'1px solid rgba(255,255,255,0.12)'}}>
                    {compTrend.mapping_status === 'exact' ? 'exact' : compTrend.mapping_status === 'composite' ? 'composite' : 'provisional'}
                  </span>
                </div>
                <div style={{display:'flex', justifyContent:'space-between', alignItems:'center', gap:'0.75rem', flexWrap:'wrap', marginBottom:'1rem'}}>
                  <div style={{display:'flex', gap:'0.45rem', flexWrap:'wrap'}}>
                    {(companyHs?.items || []).slice(0, 4).map((item) => (
                      <span key={item.hs_code} style={{
                        fontSize:'0.72rem',
                        padding:'0.28rem 0.55rem',
                        borderRadius:'999px',
                        background:'rgba(255,255,255,0.05)',
                        border:'1px solid var(--glass-border)',
                        color:'#e5e7eb'
                      }}>
                        {item.hs_name} {item.export_share != null ? `(${item.export_share.toFixed(1)}%)` : ''}
                      </span>
                    ))}
                  </div>
                  <div style={{display:'flex', alignItems:'center', gap:'0.5rem'}}>
                    <span style={{fontSize:'0.74rem', color:'var(--text-secondary)'}}>HS 기준월</span>
                    <select
                      value={companyPeriod || companyHs?.period_ym || ''}
                      onChange={(e) => loadCompanyHs(selCompany, selSector?.sector_key, e.target.value)}
                      style={{
                        padding:'0.3rem 0.65rem', borderRadius:'7px', fontSize:'0.78rem',
                        background:'rgba(255,255,255,0.07)', border:'1px solid var(--glass-border)', color:'#fff'
                      }}
                    >
                      {(companyHs?.periods || []).map((period) => (
                        <option key={period} value={period} style={{background:'#1a1a2e'}}>{period}</option>
                      ))}
                    </select>
                  </div>
                </div>
                <div style={{
                  marginBottom:'1rem',
                  padding:'0.9rem 1rem',
                  borderRadius:'12px',
                  border:'1px solid var(--glass-border)',
                  background:'rgba(255,255,255,0.04)'
                }}>
                  <div style={{fontSize:'0.78rem', color:'var(--text-secondary)', marginBottom:'0.2rem'}}>구성 요약</div>
                  <div style={{fontSize:'0.95rem', fontWeight:700, color:'#fff'}}>
                    {formatCompositionLabel(compTrend.sector_name, compTrend.hs_names) || '-'}
                  </div>
                  <div style={{fontSize:'0.74rem', color:'var(--text-secondary)', marginTop:'0.35rem'}}>
                    {String(compTrend.hs_names || '').split(',').map((v) => v.trim()).filter(Boolean).length > 1
                      ? `현재 선택 기업은 ${String(compTrend.hs_names || '').split(',').map((v) => v.trim()).filter(Boolean).length}개 HS 코드 합산으로 계산됩니다.`
                      : '현재 선택 기업은 단일 HS 코드 기준으로 계산됩니다.'}
                  </div>
                </div>
                <CompanyChart data={compTrend} />
                <div style={{marginTop:'1rem', overflowX:'auto', maxHeight:'260px', overflowY:'auto'}}>
                  <table className="premium-table" style={{fontSize:'0.78rem', minWidth:'980px'}}>
                    <thead><tr>
                      <th>기간</th>
                      <th style={{textAlign:'right'}}>수출액</th>
                      <th style={{textAlign:'right'}}>수입액</th>
                      <th style={{textAlign:'right'}}>수출 MoM</th>
                      <th style={{textAlign:'right'}}>수출 YoY</th>
                      <th style={{textAlign:'right'}}>수입 MoM</th>
                      <th style={{textAlign:'left'}}>주요 HS</th>
                    </tr></thead>
                    <tbody>
                      {[...compTrend.monthly].reverse().map((m, i, arr) => {
                        const prev1  = arr[i + 1];
                        const prev12 = arr[i + 12];
                        const exportMom = prev1  ? (m.export_val - prev1.export_val)  / (prev1.export_val  || 1) * 100 : null;
                        const exportYoy = prev12 ? (m.export_val - prev12.export_val) / (prev12.export_val || 1) * 100 : null;
                        const importMom = prev1  ? (m.import_val - prev1.import_val)  / (prev1.import_val  || 1) * 100 : null;
                        return (
                          <tr key={m.period_ym} style={{opacity: i === 0 ? 1 : 0.9}}>
                            <td style={{fontWeight: i === 0 ? 700 : 400}}>{m.period_ym}</td>
                            <td style={{textAlign:'right', fontWeight: i === 0 ? 700 : 400}}>
                              ${(m.export_val / 1e6).toFixed(2)}M
                            </td>
                            <td style={{textAlign:'right', color:'#93c5fd'}}>
                              ${(m.import_val / 1e6).toFixed(2)}M
                            </td>
                            <td style={{textAlign:'right'}}>{pct(exportMom == null ? null : parseFloat(exportMom.toFixed(1)))}</td>
                            <td style={{textAlign:'right'}}>{pct(exportYoy == null ? null : parseFloat(exportYoy.toFixed(1)))}</td>
                            <td style={{textAlign:'right'}}>{pct(importMom == null ? null : parseFloat(importMom.toFixed(1)))}</td>
                            <td style={{fontSize:'0.72rem', color:'var(--text-secondary)', maxWidth:'280px'}}>{m.hs_names || '-'}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                <div style={{
                  marginTop:'1rem',
                  border:'1px solid var(--glass-border)',
                  borderRadius:'12px',
                  background:'rgba(255,255,255,0.03)',
                  overflow:'hidden'
                }}>
                  <div style={{
                    padding:'0.8rem 1rem',
                    borderBottom:'1px solid var(--glass-border)',
                    display:'flex',
                    justifyContent:'space-between',
                    alignItems:'center',
                    gap:'0.75rem',
                    flexWrap:'wrap'
                  }}>
                    <div>
                      <div style={{fontSize:'0.9rem', fontWeight:700, color:'#fff'}}>기업 HS 비중 상세</div>
                      <div style={{fontSize:'0.74rem', color:'var(--text-secondary)', marginTop:'0.15rem'}}>
                        {compTrend.stock_name}의 선택 섹터 내 HS 코드별 수출/수입 비중
                      </div>
                    </div>
                  </div>
                  {companyHsLoading ? (
                    <div style={{padding:'1.5rem', textAlign:'center', color:'var(--text-secondary)'}}>HS 비중 데이터를 불러오는 중...</div>
                  ) : (
                    <BreakdownTable items={companyHs?.items || []} type="company" />
                  )}
                </div>
              </div>
            ) : companies.length === 0 && selSector ? (
              <div style={{padding:'2rem', textAlign:'center', color:'rgba(255,255,255,0.3)'}}>
                이 섹터에 매핑된 기업이 없습니다.
              </div>
            ) : null}
          </div>
        </div>
      </div>
    );
  };


  // ── 섹터 보고서 페이지 ────────────────────────────────────────
  const SectorReports = () => {
    const isMobile = useIsMobile();
    const [sectors,  setSectors]  = React.useState([]);
    const [selected, setSelected] = React.useState('');
    const [reports,  setReports]  = React.useState([]);
    const [loading,  setLoading]  = React.useState(false);

    React.useEffect(() => {
      fetch(API('/api/reports/sectors'))
        .then(r => r.ok ? r.json() : [])
        .then(d => { setSectors(d); if(d.length>0) setSelected(d[0].sector); })
        .catch(() => {});
    }, []);

    React.useEffect(() => {
      if (!selected) return;
      setLoading(true);
      fetch(API(`/api/reports/sector/${encodeURIComponent(selected)}`))
        .then(r => r.ok ? r.json() : [])
        .then(d => { setReports(d||[]); setLoading(false); })
        .catch(() => setLoading(false));
    }, [selected]);

    const ICONS = {
      '반도체':'💾','IT/전자':'📱','2차전지/EV':'🔋','자동차':'🚗',
      '정유/화학':'🛢️','바이오/제약':'💊','금융':'🏦','통신':'📡',
      '건설/부동산':'🏗️','철강/소재':'⚙️','조선/기계':'🚢','유통/소비재':'🛍️',
      '게임/엔터':'🎮','해운/물류':'📦','전력/신재생':'⚡',
      '코스피시장':'📈','코스닥시장':'📊','해외/글로벌':'🌏',
    };

    return (
      <div className="fade-in" style={{display:'flex',flexDirection:'column',gap:'1rem'}}>
        <div style={{display:'flex',alignItems:'center',justifyContent:'space-between'}}>
          <h2 style={{fontSize:'1rem',fontWeight:700}}>📄 섹터별 보고서</h2>
          <span style={{fontSize:'0.75rem',color:'var(--text-secondary)'}}>
            총 {sectors.reduce((s,x)=>s+x.count,0)}건
          </span>
        </div>
        {/* 섹터 탭 */}
        <div style={{display:'flex',flexWrap:'wrap',gap:'0.4rem'}}>
          {sectors.map(s => (
            <button key={s.sector} onClick={()=>setSelected(s.sector)} style={{
              padding:'0.35rem 0.8rem',borderRadius:'20px',fontSize:'0.78rem',cursor:'pointer',
              fontWeight: selected===s.sector?700:400,
              border: selected===s.sector?'1px solid var(--accent-mint)':'1px solid var(--glass-border)',
              background: selected===s.sector?'rgba(45,212,191,0.2)':'rgba(255,255,255,0.04)',
              color: selected===s.sector?'var(--accent-mint)':'var(--text-secondary)',
            }}>
              {ICONS[s.sector]||'📄'} {s.sector}
              <span style={{marginLeft:'0.3rem',fontSize:'0.7rem',opacity:0.6}}>{s.count}</span>
            </button>
          ))}
        </div>
        {/* 보고서 목록 */}
        <div className="glass-panel" style={{padding:'1rem'}}>
          {loading ? (
            <div style={{textAlign:'center',padding:'2rem',color:'var(--accent-mint)'}}>로딩 중...</div>
          ) : reports.length===0 ? (
            <p style={{color:'var(--text-secondary)',textAlign:'center',padding:'2rem',fontSize:'0.85rem'}}>
              보고서가 없습니다.
            </p>
          ) : (
            <div style={{display:'flex',flexDirection:'column',gap:'0.35rem'}}>
              {reports.map(r => (
                <div key={r.id} style={{display:'flex',alignItems:'center',
                  justifyContent:'space-between',padding:'0.5rem 0.75rem',
                  borderRadius:'6px',background:'rgba(255,255,255,0.03)',
                  border:'1px solid rgba(255,255,255,0.06)'}}>
                  <div style={{flex:1,minWidth:0}}>
                    <p style={{fontSize:'0.82rem',fontWeight:600,
                      overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
                      {r.file_name}
                    </p>
                    <p style={{fontSize:'0.7rem',color:'var(--text-secondary)',marginTop:'0.1rem'}}>
                      {r.report_date} | {r.channel_id}
                      {r.file_size?` | ${(r.file_size/1024).toFixed(0)}KB`:''}
                    </p>
                    {r.caption && (
                      <p style={{fontSize:'0.7rem',color:'rgba(255,255,255,0.4)',
                        overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>
                        {r.caption}
                      </p>
                    )}
                  </div>
                  <a href={API(`/api/reports/download/${r.id}`)} download={r.saved_name}
                    style={{marginLeft:'0.75rem',padding:'0.3rem 0.7rem',borderRadius:'5px',
                      background:'rgba(45,212,191,0.12)',border:'1px solid rgba(45,212,191,0.25)',
                      color:'var(--accent-mint)',fontSize:'0.72rem',textDecoration:'none',
                      whiteSpace:'nowrap',flexShrink:0}}>
                    ⬇
                  </a>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  };


  // ── 시그널 설정 관리 ─────────────────────────────────────────
  const SignalSettings = () => {
    const [configs, setConfigs] = React.useState([]);
    const [editId,  setEditId]  = React.useState(null);
    const [editForm,setEditForm]= React.useState({});
    const [adding,  setAdding]  = React.useState(false);
    const [newForm, setNewForm] = React.useState({
      scope:'stock', label:'', description:'', logic_type:'manual', params:'{}',
    });
    const [manualVals, setManualVals] = React.useState({});

    const load = () => fetch(API('/api/signals/config'))
      .then(r=>r.ok?r.json():[]).then(setConfigs).catch(()=>{});

    React.useEffect(() => { load(); }, []);

    const SCOPE_LABEL = { market:'종합현황', stock:'개별종목' };
    const LOGIC_LABEL = {
      supply_trend:'수급추세', threshold:'임계값', ma_trend:'이평선추세',
      ma_position:'이평선위치', financial:'재무', price_position:'주가위치', manual:'수동입력',
    };
    const SIG_EMOJI = { green:'🟢', yellow:'🟡', red:'🔴' };

    const saveEdit = async () => {
      await fetch(API(`/api/signals/config/${editId}`), {
        method:'PUT', headers:{'Content-Type':'application/json'},
        body: JSON.stringify(editForm),
      });
      setEditId(null); load();
    };

    const toggleActive = async (id, current) => {
      await fetch(API(`/api/signals/config/${id}`), {
        method:'PUT', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({is_active: current ? 0 : 1}),
      });
      load();
    };

    const deleteConfig = async (id) => {
      await fetch(API(`/api/signals/config/${id}`), { method:'DELETE' });
      load();
    };

    const addConfig = async () => {
      await fetch(API('/api/signals/config'), {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify(newForm),
      });
      setAdding(false);
      setNewForm({scope:'stock',label:'',description:'',logic_type:'manual',params:'{}'});
      load();
    };

    const setManual = async (id, sig, val, desc) => {
      await fetch(API(`/api/signals/manual/${id}`), {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({signal:sig, value:parseFloat(val)||0, description:desc}),
      });
    };

    const inputS = {
      padding:'0.3rem 0.5rem', borderRadius:'5px', fontSize:'0.8rem',
      background:'rgba(255,255,255,0.07)', border:'1px solid var(--glass-border)',
      color:'#fff',
    };

    const marketCfgs = configs.filter(c=>c.scope==='market');
    const stockCfgs  = configs.filter(c=>c.scope==='stock');

    const renderGroup = (title, cfgs) => (
      <div className="glass-panel" style={{padding:'1rem',marginBottom:'0.75rem'}}>
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:'0.75rem'}}>
          <h4 style={{fontSize:'0.85rem',fontWeight:700,color:'var(--accent-mint)'}}>{title}</h4>
        </div>
        <div style={{display:'flex',flexDirection:'column',gap:'0.4rem'}}>
          {cfgs.map(c => editId === c.id ? (
            <div key={c.id} style={{padding:'0.6rem',borderRadius:'6px',background:'rgba(45,212,191,0.05)',border:'1px solid rgba(45,212,191,0.2)'}}>
              <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'0.4rem',marginBottom:'0.4rem'}}>
                <input value={editForm.label||''} onChange={e=>setEditForm(p=>({...p,label:e.target.value}))}
                  placeholder="표시명" style={inputS}/>
                <input value={editForm.description||''} onChange={e=>setEditForm(p=>({...p,description:e.target.value}))}
                  placeholder="설명" style={inputS}/>
              </div>
              <input value={editForm.params||''} onChange={e=>setEditForm(p=>({...p,params:e.target.value}))}
                placeholder='파라미터 JSON (예: {"days":5})' style={{...inputS,width:'100%',marginBottom:'0.4rem'}}/>
              {c.logic_type === 'manual' && (
                <div style={{display:'flex',gap:'0.4rem',marginBottom:'0.4rem',alignItems:'center'}}>
                  <span style={{fontSize:'0.75rem',color:'var(--text-secondary)'}}>수동값:</span>
                  <input value={manualVals[c.id]?.val||''} onChange={e=>setManualVals(p=>({...p,[c.id]:{...p[c.id],val:e.target.value}}))}
                    placeholder="값" style={{...inputS,width:'80px'}}/>
                  <input value={manualVals[c.id]?.desc||''} onChange={e=>setManualVals(p=>({...p,[c.id]:{...p[c.id],desc:e.target.value}}))}
                    placeholder="설명" style={{...inputS,flex:1}}/>
                  {['green','yellow','red'].map(s=>(
                    <button key={s} onClick={()=>setManual(c.id,s,manualVals[c.id]?.val||0,manualVals[c.id]?.desc||'')}
                      style={{padding:'0.2rem 0.5rem',borderRadius:'4px',cursor:'pointer',fontSize:'0.75rem',
                        background:'rgba(255,255,255,0.05)',border:'1px solid var(--glass-border)',color:'#fff'}}>
                      {SIG_EMOJI[s]}
                    </button>
                  ))}
                </div>
              )}
              <div style={{display:'flex',gap:'0.4rem'}}>
                <button onClick={saveEdit} style={{padding:'0.25rem 0.7rem',borderRadius:'5px',background:'var(--accent-mint)',border:'none',color:'#000',cursor:'pointer',fontSize:'0.75rem',fontWeight:700}}>저장</button>
                <button onClick={()=>setEditId(null)} style={{padding:'0.25rem 0.7rem',borderRadius:'5px',background:'transparent',border:'1px solid var(--glass-border)',color:'var(--text-secondary)',cursor:'pointer',fontSize:'0.75rem'}}>취소</button>
              </div>
            </div>
          ) : (
            <div key={c.id} style={{display:'flex',alignItems:'center',gap:'0.5rem',padding:'0.4rem 0.6rem',borderRadius:'6px',background:'rgba(255,255,255,0.03)',border:'1px solid var(--glass-border)',opacity:c.is_active?1:0.45}}>
              <span style={{fontSize:'0.78rem',fontWeight:600,flex:1}}>{c.label}</span>
              <span style={{fontSize:'0.68rem',color:'var(--text-secondary)',padding:'0.1rem 0.4rem',background:'rgba(255,255,255,0.05)',borderRadius:'4px'}}>{LOGIC_LABEL[c.logic_type]||c.logic_type}</span>
              <span style={{fontSize:'0.68rem',color:'var(--text-secondary)',maxWidth:'160px',overflow:'hidden',textOverflow:'ellipsis',whiteSpace:'nowrap'}}>{c.description}</span>
              <button onClick={()=>{setEditId(c.id);setEditForm({label:c.label,description:c.description,params:c.params,is_active:c.is_active?1:0});}}
                style={{padding:'0.15rem 0.5rem',borderRadius:'4px',background:'rgba(45,212,191,0.1)',border:'1px solid rgba(45,212,191,0.3)',color:'var(--accent-mint)',cursor:'pointer',fontSize:'0.7rem'}}>수정</button>
              <button onClick={()=>toggleActive(c.id,c.is_active)}
                style={{padding:'0.15rem 0.5rem',borderRadius:'4px',background:c.is_active?'rgba(251,191,36,0.1)':'rgba(255,255,255,0.05)',border:'1px solid rgba(255,255,255,0.15)',color:c.is_active?'#fbbf24':'#64748b',cursor:'pointer',fontSize:'0.7rem'}}>
                {c.is_active?'활성':'비활성'}
              </button>
              <button onClick={()=>deleteConfig(c.id)}
                style={{padding:'0.15rem 0.5rem',borderRadius:'4px',background:'rgba(239,68,68,0.1)',border:'1px solid rgba(239,68,68,0.3)',color:'#ef4444',cursor:'pointer',fontSize:'0.7rem'}}>삭제</button>
            </div>
          ))}
        </div>
      </div>
    );

    return (
      <div className="glass-panel" style={{padding:'1.2rem'}}>
        <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',marginBottom:'1rem'}}>
          <h3 style={{fontSize:'0.9rem',fontWeight:700,color:'var(--accent-purple)',display:'flex',alignItems:'center',gap:'0.5rem'}}>
            📊 시그널 보드 설정
          </h3>
          <button onClick={()=>setAdding(v=>!v)}
            style={{padding:'0.3rem 0.8rem',borderRadius:'6px',background:'rgba(167,139,250,0.15)',border:'1px solid rgba(167,139,250,0.4)',color:'var(--accent-purple)',cursor:'pointer',fontSize:'0.8rem',fontWeight:600}}>
            + 시그널 추가
          </button>
        </div>

        {adding && (
          <div style={{padding:'0.8rem',borderRadius:'8px',background:'rgba(167,139,250,0.05)',border:'1px solid rgba(167,139,250,0.2)',marginBottom:'0.75rem'}}>
            <div style={{display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:'0.4rem',marginBottom:'0.4rem'}}>
              <select value={newForm.scope} onChange={e=>setNewForm(p=>({...p,scope:e.target.value}))}
                style={{...inputS}}>
                <option value="market">종합현황</option>
                <option value="stock">개별종목</option>
              </select>
              <input value={newForm.label} onChange={e=>setNewForm(p=>({...p,label:e.target.value}))}
                placeholder="표시명" style={inputS}/>
              <select value={newForm.logic_type} onChange={e=>setNewForm(p=>({...p,logic_type:e.target.value}))}
                style={inputS}>
                <option value="manual">수동입력</option>
                <option value="threshold">임계값</option>
                <option value="supply_trend">수급추세</option>
                <option value="ma_trend">이평선추세</option>
                <option value="financial">재무</option>
              </select>
            </div>
            <input value={newForm.description} onChange={e=>setNewForm(p=>({...p,description:e.target.value}))}
              placeholder="설명" style={{...inputS,width:'100%',marginBottom:'0.4rem'}}/>
            <input value={newForm.params} onChange={e=>setNewForm(p=>({...p,params:e.target.value}))}
              placeholder='파라미터 JSON' style={{...inputS,width:'100%',marginBottom:'0.4rem'}}/>
            <div style={{display:'flex',gap:'0.4rem'}}>
              <button onClick={addConfig} style={{padding:'0.3rem 0.8rem',borderRadius:'5px',background:'var(--accent-purple)',border:'none',color:'#fff',cursor:'pointer',fontSize:'0.78rem',fontWeight:700}}>추가</button>
              <button onClick={()=>setAdding(false)} style={{padding:'0.3rem 0.8rem',borderRadius:'5px',background:'transparent',border:'1px solid var(--glass-border)',color:'var(--text-secondary)',cursor:'pointer',fontSize:'0.78rem'}}>취소</button>
            </div>
          </div>
        )}

        {renderGroup('📊 종합현황 시그널', marketCfgs)}
        {renderGroup('🔍 개별종목 시그널', stockCfgs)}

        <p style={{fontSize:'0.7rem',color:'rgba(255,255,255,0.3)',marginTop:'0.5rem'}}>
          🟢 매수/보유 &nbsp; 🟡 관망/주의 &nbsp; 🔴 매도/회피 &nbsp;
          수동입력 시그널은 수정 버튼 클릭 후 값 입력
        </p>
      </div>
    );
  };

  // ── AI 리포트 ─────────────────────────────────────────────────
  // [버그 ④ 수정] insight 탭 컴포넌트 구현 및 return에 연결
  const AIInsight = () => {
    const [generating, setGenerating] = React.useState(false);

    const handleGenerate = async () => {
      setGenerating(true);
      try {
        const res = await fetch(API(`/api/reports/generate/${selectedStock}`), { method: 'POST' });
        if (res.ok) setAiReport(await res.json());
      } catch (e) { console.error("Report generate error", e); }
      finally { setGenerating(false); }
    };

    const stockName = watchlist.find(i => i.stock_code === selectedStock)?.stock_name || selectedStock;

    return (
      <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
        <div className="glass-panel" style={{ padding: '1.2rem', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div className="section-title" style={{ marginBottom: 0 }}>
            <Cpu size={20} color="var(--accent-purple)" />
            <h2>AI 분석 리포트</h2>
            <span style={{ marginLeft: '0.5rem', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>{stockName} ({selectedStock})</span>
          </div>
          <button onClick={handleGenerate} disabled={generating}
            style={{ padding: '0.45rem 1rem', borderRadius: '8px', background: generating ? 'rgba(167,139,250,0.3)' : 'var(--accent-purple)', border: 'none', color: '#fff', cursor: 'pointer', fontWeight: 600, fontSize: '0.85rem' }}>
            {generating ? '생성 중...' : '리포트 생성'}
          </button>
        </div>

        {aiReport ? (
          <div className="glass-panel" style={{ padding: '1.5rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '1rem' }}>
              <FileText size={16} color="var(--accent-purple)" />
              <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
                생성일시: {aiReport.report_date ? new Date(aiReport.report_date).toLocaleString('ko-KR') : '-'}
              </p>
            </div>
            <pre style={{ whiteSpace: 'pre-wrap', fontFamily: 'inherit', lineHeight: 1.8, color: 'var(--text-primary)', fontSize: '0.9rem' }}>
              {aiReport.content}
            </pre>
          </div>
        ) : (
          <div className="glass-panel" style={{ padding: '3rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
            <Cpu size={40} style={{ margin: '0 auto 1rem', display: 'block', opacity: 0.3 }} />
            <p>아직 생성된 리포트가 없습니다.</p>
            <p style={{ fontSize: '0.8rem', marginTop: '0.4rem' }}>위 버튼을 눌러 AI 분석 리포트를 생성하세요.</p>
          </div>
        )}
      </div>
    );
  };


  // ── 백테스트 뷰 ───────────────────────────────────────────
  const BacktestView = () => {
    const [list,    setList]    = React.useState([]);
    const [detail,  setDetail]  = React.useState(null);  // 선택된 결과
    const [running, setRunning] = React.useState(false);
    const [pollId,  setPollId]  = React.useState(null);
    const [form, setForm] = React.useState({
      start_date: '2023-04-01',
      end_date:   '2025-12-31',
      per_stock:  10000000,
      name:       '',
    });

    const loadList = async () => {
      try {
        const r = await fetch(API('/api/backtest/list'));
        if (r.ok) setList(await r.json());
      } catch(e) {}
    };

    React.useEffect(() => { loadList(); }, []);

    const startBacktest = async () => {
      setRunning(true);
      try {
        const r = await fetch(API('/api/backtest/run'), {
          method: 'POST',
          headers: {'Content-Type':'application/json'},
          body: JSON.stringify({
            start_date: form.start_date,
            end_date:   form.end_date,
            per_stock:  Number(form.per_stock),
            name:       form.name || `백테스트 ${form.start_date.slice(0,7)}~${form.end_date.slice(0,7)}`,
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

    return (
      <div className="fade-in" style={{display:'flex',flexDirection:'column',gap:'1rem'}}>
        {/* 헤더 */}
        <div className="glass-panel" style={{padding:'0.8rem 1.2rem'}}>
          <div style={{display:'flex',alignItems:'center',gap:'0.6rem',marginBottom:'0.6rem'}}>
            <Activity size={18} color="#f59e0b" />
            <h2 style={{fontSize:'1rem',fontWeight:700}}>📊 백테스트</h2>
          </div>
          <div style={{padding:'0.5rem 0.8rem',background:'rgba(251,191,36,0.07)',
            border:'1px solid rgba(251,191,36,0.2)',borderRadius:'6px',
            fontSize:'0.7rem',color:'rgba(251,191,36,0.85)',lineHeight:1.6}}>
            ⚠️ AI 적극검토(추세+가치+재무 중 2개↑ 동시 충족) 시그널 발생 시 매수, MA20 이탈 또는 -15% 손절 시 매도.
            과거 데이터 기준 시뮬레이션으로 <strong>미래 수익을 보장하지 않습니다.</strong>
            DB 가격 데이터: 2023-04 ~ 현재 (종목당 10만원 단위 가상 투자)
          </div>
        </div>

        {/* 설정 패널 */}
        <div className="glass-panel" style={{padding:'1rem 1.2rem'}}>
          <div style={{fontWeight:700,marginBottom:'0.7rem',fontSize:'0.85rem',color:'var(--accent-mint)'}}>
            🔧 백테스트 설정
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

  // ── 설정 ──────────────────────────────────────────────────
  const SettingsView = () => {
    const [cfg, setCfg] = React.useState({
      price_interval:  60,    // 주가 수집 주기 (초)
      supply_interval: 300,   // 수급 수집 주기 (초)
      supply_after_close: 1800, // 장마감 후 수급 주기 (초)
      kis_enabled:     true,
      naver_enabled:   true,
      dart_enabled:    true,
    });
    const [saved, setSaved] = React.useState(false);
    const [sysInfo, setSysInfo] = React.useState(null);

    React.useEffect(() => {
      fetch(API('/api/system/status')).then(r=>r.ok?r.json():null).then(d=>setSysInfo(d)).catch(()=>{});
    }, []);

    const fmtSec = (s) => s >= 3600 ? (s/3600).toFixed(0)+'시간' : s >= 60 ? (s/60).toFixed(0)+'분' : s+'초';

    const sources = [
      { key:'kis_enabled',   name:'KIS API',    desc:'체결내역·주가 실시간', color:'#facc15' },
      { key:'naver_enabled', name:'네이버 금융', desc:'수급·시장정보·종목정보', color:'#34d399' },
      { key:'dart_enabled',  name:'DART',        desc:'재무제표 (자정 배치)', color:'#60a5fa' },
    ];

    const intervals = [
      { key:'price_interval',       label:'주가 수집 주기',      unit:'초', min:30,  max:300 },
      { key:'supply_interval',      label:'장중 수급 주기',      unit:'초', min:60,  max:600 },
      { key:'supply_after_close',   label:'장마감 후 수급 주기', unit:'초', min:600, max:3600 },
    ];

    return (
      <div className="fade-in" style={{ display:'flex', flexDirection:'column', gap:'1.2rem', maxWidth:'800px' }}>
        <div className="section-title">
          <Database size={20} color="var(--accent-purple)" />
          <h2>시스템 설정</h2>
        </div>

        {/* 데이터 소스 */}
        <div className="glass-panel" style={{ padding:'1.2rem' }}>
          <h3 style={{ fontSize:'0.9rem', fontWeight:700, marginBottom:'1rem', color:'var(--text-secondary)' }}>데이터 소스</h3>
          <div style={{ display:'flex', flexDirection:'column', gap:'0.8rem' }}>
            {sources.map(s => (
              <div key={s.key} style={{ display:'flex', alignItems:'center', justifyContent:'space-between',
                padding:'0.8rem 1rem', borderRadius:'8px', background:'rgba(255,255,255,0.03)',
                border:'1px solid var(--glass-border)' }}>
                <div>
                  <span style={{ fontWeight:700, color:s.color }}>{s.name}</span>
                  <span style={{ fontSize:'0.75rem', color:'var(--text-secondary)', marginLeft:'0.8rem' }}>{s.desc}</span>
                </div>
                <button onClick={() => setCfg(p => ({...p, [s.key]: !p[s.key]}))} style={{
                  width:'44px', height:'24px', borderRadius:'12px', border:'none', cursor:'pointer',
                  background: cfg[s.key] ? 'var(--accent-mint)' : 'rgba(255,255,255,0.15)',
                  transition:'background 0.2s', position:'relative',
                }}>
                  <div style={{ position:'absolute', top:'3px', left: cfg[s.key]?'23px':'3px',
                    width:'18px', height:'18px', borderRadius:'50%', background:'white', transition:'left 0.2s' }}/>
                </button>
              </div>
            ))}
          </div>
        </div>

        {/* 수집 주기 */}
        <div className="glass-panel" style={{ padding:'1.2rem' }}>
          <h3 style={{ fontSize:'0.9rem', fontWeight:700, marginBottom:'1rem', color:'var(--text-secondary)' }}>데이터 수집 주기</h3>
          <div style={{ display:'flex', flexDirection:'column', gap:'1rem' }}>
            {intervals.map(iv => (
              <div key={iv.key} style={{ display:'grid', gridTemplateColumns:'1fr auto auto', alignItems:'center', gap:'1rem' }}>
                <div>
                  <p style={{ fontWeight:600, fontSize:'0.85rem' }}>{iv.label}</p>
                  <p style={{ fontSize:'0.72rem', color:'var(--text-secondary)' }}>현재: {fmtSec(cfg[iv.key])}</p>
                </div>
                <input type="range" min={iv.min} max={iv.max} step={iv.min}
                  value={cfg[iv.key]} onChange={e => setCfg(p => ({...p, [iv.key]: Number(e.target.value)}))}
                  style={{ width:'160px', accentColor:'var(--accent-mint)' }}/>
                <span style={{ fontSize:'0.8rem', color:'var(--accent-mint)', minWidth:'50px', textAlign:'right' }}>
                  {fmtSec(cfg[iv.key])}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* 시스템 현황 요약 */}
        <div className="glass-panel" style={{ padding:'1.2rem' }}>
          <h3 style={{ fontSize:'0.9rem', fontWeight:700, marginBottom:'1rem', color:'var(--text-secondary)' }}>시스템 현황</h3>
          <div style={{ display:'grid', gridTemplateColumns:'repeat(3,1fr)', gap:'0.75rem' }}>
            {[
              { label:'백엔드',    val:'FastAPI (Python 3.11)', color:'var(--accent-mint)' },
              { label:'데이터베이스', val:'SQLite',             color:'#60a5fa' },
              { label:'외부 접속', val:'stock.leanguy.cloud',   color:'#a78bfa' },
              { label:'장 상태',   val: sysInfo?.market_open ? '🟢 장중' : '🔴 장마감', color:'inherit' },
              { label:'주가 수집', val:`${cfg.price_interval}초 주기`, color:'inherit' },
              { label:'수급 수집', val:`${fmtSec(cfg.supply_interval)} 주기`, color:'inherit' },
            ].map(item => (
              <div key={item.label} style={{ padding:'0.8rem', borderRadius:'8px', background:'rgba(255,255,255,0.03)', border:'1px solid var(--glass-border)' }}>
                <p style={{ fontSize:'0.7rem', color:'var(--text-secondary)', marginBottom:'0.3rem' }}>{item.label}</p>
                <p style={{ fontSize:'0.85rem', fontWeight:600, color:item.color }}>{item.val}</p>
              </div>
            ))}
          </div>
        </div>

        <div style={{ display:'flex', justifyContent:'flex-end', gap:'0.5rem' }}>
          {saved && <span style={{ color:'var(--accent-mint)', fontSize:'0.8rem', alignSelf:'center' }}>✓ 저장됨 (다음 재시작 시 적용)</span>}
          <button onClick={() => { setSaved(true); setTimeout(()=>setSaved(false),3000); }}
            style={{ padding:'0.5rem 1.5rem', borderRadius:'8px', background:'rgba(45,212,191,0.2)',
              border:'1px solid var(--accent-mint)', color:'var(--accent-mint)', cursor:'pointer', fontWeight:700 }}>
            설정 저장
          </button>
        </div>
        {/* 추세추종 필터 파라미터 */}
        <div className="glass-panel" style={{ padding:'1.2rem' }}>
          <h3 style={{ fontSize:'0.9rem', fontWeight:700, marginBottom:'1rem', color:'var(--text-secondary)',
            display:'flex', alignItems:'center', gap:'0.5rem' }}>
            <TrendingUp size={16} color="var(--accent-mint)"/> 추세추종 필터 파라미터 (미너비니 3단계)
          </h3>
          <div style={{ display:'flex', flexDirection:'column', gap:'0.5rem' }}>
            {[
              { stage:'[1단계] 유동성',    params:[
                  { label:'시가총액 최소',      value:'1,000억 이상',    desc:'잡주·소형주 제외' },
                  { label:'거래대금 최소',      value:'5일 평균 100억↑', desc:'volume×close 기준' },
              ]},
              { stage:'[2단계] 추세 템플릿', params:[
                  { label:'MA120/200 조건',   value:'현재가 > MA120, MA200', desc:'장기 추세 위' },
                  { label:'장기 정배열',       value:'MA120 > MA200',   desc:'골든크로스 확인' },
                  { label:'신고가 근접',       value:'52주 고점 -20% 이내', desc:'매물대 없는 구간' },
                  { label:'단기 정배열',       value:'현재가>MA5>MA20>MA60', desc:'완전정배열 필수(부분 허용)' },
              ]},
              { stage:'[3단계] 진입 트리거', params:[
                  { label:'RSI(14) 최소',     value:'60 이상 (필수)',   desc:'상승 모멘텀 확인' },
                  { label:'거래량 폭발 기준', value:'20일 평균 × 2.0배', desc:'+3점, 1.5배=+2점' },
                  { label:'BB 스퀴즈',        value:'밴드폭 최소 × 1.5 이내 + BB 상단 돌파', desc:'+3점' },
              ]},
              { stage:'등급 임계점',         params:[
                  { label:'강력매수',         value:'점수 ≥ 20점',     desc:'' },
                  { label:'매수',             value:'점수 ≥ 14점',     desc:'' },
                  { label:'관심',             value:'점수 ≥ 10점',     desc:'' },
              ]},
            ].map(group => (
              <div key={group.stage} style={{ padding:'0.75rem', borderRadius:'8px',
                background:'rgba(255,255,255,0.02)', border:'1px solid var(--glass-border)' }}>
                <p style={{ fontSize:'0.78rem', fontWeight:700, color:'var(--accent-mint)', marginBottom:'0.5rem' }}>
                  {group.stage}
                </p>
                <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fill,minmax(260px,1fr))', gap:'0.3rem 1rem' }}>
                  {group.params.map(p => (
                    <div key={p.label} style={{ display:'flex', alignItems:'baseline', gap:'0.4rem' }}>
                      <span style={{ fontSize:'0.72rem', color:'rgba(255,255,255,0.45)', minWidth:'120px' }}>{p.label}</span>
                      <span style={{ fontSize:'0.72rem', color:'rgba(255,255,255,0.85)', fontWeight:600 }}>{p.value}</span>
                      {p.desc && <span style={{ fontSize:'0.65rem', color:'rgba(255,255,255,0.3)' }}>({p.desc})</span>}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>

        <TelegramSettings />
        <SignalSettings />
      </div>
    );
  };


  // ── 텔레그램 모니터 설정 ─────────────────────────────────────
  const TelegramSettings = () => {
    const [channels,   setChannels]   = React.useState([]);
    const [newChannel, setNewChannel] = React.useState('');
    const [schedule,   setSchedule]   = React.useState({ hour1: 9, hour2: 21 });
    const [apiKeys,    setApiKeys]    = React.useState({ openai_key:'', bot_token:'', chat_id:'' });
    const [loading,    setLoading]    = React.useState(true);
    const [msg,        setMsg]        = React.useState('');

    React.useEffect(() => {
      fetch(API('/api/telegram/settings')).then(r=>r.ok?r.json():null).then(d=>{
        if (d) {
          setChannels(d.channels||[]);
          setSchedule({ hour1: d.hour1??9, hour2: d.hour2??21 });
          setApiKeys({ openai_key:d.openai_key||'', bot_token:d.bot_token||'', chat_id:d.chat_id||'' });
        }
        setLoading(false);
      }).catch(()=>setLoading(false));
    }, []);

    const saveSettings = async () => {
      const res = await fetch(API('/api/telegram/settings'), {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ channels, hour1:schedule.hour1, hour2:schedule.hour2, ...apiKeys }),
      });
      if (res.ok) { setMsg('✓ 저장됨 — cron 자동 업데이트'); setTimeout(()=>setMsg(''),3000); }
      else { setMsg('저장 실패'); setTimeout(()=>setMsg(''),2000); }
    };

    const addChannel = () => {
      let ch = newChannel.trim();
      if (!ch) return;
      if (!ch.startsWith('@')) ch = '@' + ch;
      if (channels.includes(ch)) { setMsg('이미 등록된 채널'); setTimeout(()=>setMsg(''),2000); return; }
      setChannels(prev=>[...prev, ch]);
      setNewChannel('');
    };

    const HOURS = Array.from({length:24},(_,i)=>i);
    if (loading) return null;

    return (
      <div className="glass-panel" style={{padding:'1.2rem'}}>
        <h3 style={{fontSize:'0.9rem',fontWeight:700,marginBottom:'1.2rem',color:'var(--text-secondary)',
          display:'flex',alignItems:'center',gap:'0.5rem'}}>
          <Globe size={16} color="#38bdf8"/> 텔레그램 모니터 설정
        </h3>

        {/* 수집 시간 */}
        <div style={{marginBottom:'1.2rem',paddingBottom:'1.2rem',borderBottom:'1px solid var(--glass-border)'}}>
          <p style={{fontSize:'0.8rem',fontWeight:600,marginBottom:'0.7rem',color:'var(--accent-mint)'}}>📅 수집 시간 (하루 2회)</p>
          <div style={{display:'flex',gap:'1.5rem',alignItems:'center',flexWrap:'wrap'}}>
            {[{label:'1회차',key:'hour1'},{label:'2회차',key:'hour2'}].map(({label,key})=>(
              <div key={key} style={{display:'flex',alignItems:'center',gap:'0.5rem'}}>
                <span style={{fontSize:'0.8rem',color:'var(--text-secondary)',minWidth:'40px'}}>{label}</span>
                <select value={schedule[key]} onChange={e=>setSchedule(p=>({...p,[key]:Number(e.target.value)}))}
                  style={{padding:'0.3rem 0.6rem',borderRadius:'6px',background:'rgba(255,255,255,0.08)',
                    border:'1px solid var(--glass-border)',color:'#fff',fontSize:'0.85rem'}}>
                  {HOURS.map(h=><option key={h} value={h} style={{background:'#1a1a2e'}}>{String(h).padStart(2,'0')}:00</option>)}
                </select>
              </div>
            ))}
            <span style={{fontSize:'0.7rem',color:'rgba(255,255,255,0.3)'}}>※ 저장 시 cron 자동 업데이트</span>
          </div>
        </div>

        {/* 채널 관리 */}
        <div style={{marginBottom:'1.2rem',paddingBottom:'1.2rem',borderBottom:'1px solid var(--glass-border)'}}>
          <p style={{fontSize:'0.8rem',fontWeight:600,marginBottom:'0.7rem',color:'var(--accent-mint)'}}>📡 모니터링 채널</p>
          <div style={{display:'flex',flexWrap:'wrap',gap:'0.4rem',marginBottom:'0.7rem',minHeight:'32px'}}>
            {channels.map(ch=>(
              <div key={ch} style={{display:'flex',alignItems:'center',gap:'0.3rem',padding:'0.2rem 0.6rem',
                borderRadius:'20px',background:'rgba(56,189,248,0.12)',border:'1px solid rgba(56,189,248,0.3)'}}>
                <span style={{fontSize:'0.8rem',color:'#38bdf8',fontWeight:600}}>{ch}</span>
                <button onClick={()=>setChannels(p=>p.filter(c=>c!==ch))}
                  style={{background:'none',border:'none',color:'rgba(255,255,255,0.5)',
                    cursor:'pointer',padding:'0 2px',fontSize:'1rem',lineHeight:1}}>×</button>
              </div>
            ))}
            {channels.length===0 && <span style={{fontSize:'0.78rem',color:'var(--text-secondary)'}}>등록된 채널 없음</span>}
          </div>
          <div style={{display:'flex',gap:'0.5rem'}}>
            <input value={newChannel} onChange={e=>setNewChannel(e.target.value)}
              onKeyDown={e=>e.key==='Enter'&&addChannel()}
              placeholder="@채널명 입력 후 Enter 또는 추가 버튼"
              style={{flex:1,padding:'0.4rem 0.8rem',borderRadius:'8px',background:'rgba(255,255,255,0.06)',
                border:'1px solid var(--glass-border)',color:'#fff',fontSize:'0.85rem'}}/>
            <button onClick={addChannel} style={{padding:'0.4rem 1rem',borderRadius:'8px',
              background:'rgba(56,189,248,0.15)',border:'1px solid rgba(56,189,248,0.4)',
              color:'#38bdf8',cursor:'pointer',fontWeight:600,fontSize:'0.82rem',whiteSpace:'nowrap'}}>+ 추가</button>
          </div>
        </div>

        {/* API 키 */}
        <div style={{marginBottom:'1.2rem'}}>
          <p style={{fontSize:'0.8rem',fontWeight:600,marginBottom:'0.7rem',color:'var(--accent-mint)'}}>🔑 API 키 설정</p>
          <div style={{display:'flex',flexDirection:'column',gap:'0.6rem'}}>
            {[
              {key:'openai_key', label:'OpenAI API Key',      placeholder:'sk-proj-...'},
              {key:'bot_token',  label:'텔레그램 봇 토큰',    placeholder:'1234567890:AAF...'},
              {key:'chat_id',    label:'결과 전송 채널 ID',   placeholder:'-1001234567890'},
            ].map(({key,label,placeholder})=>(
              <div key={key} style={{display:'grid',gridTemplateColumns:'160px 1fr',alignItems:'center',gap:'0.75rem'}}>
                <span style={{fontSize:'0.78rem',color:'var(--text-secondary)'}}>{label}</span>
                <input type="password" value={apiKeys[key]}
                  onChange={e=>setApiKeys(p=>({...p,[key]:e.target.value}))}
                  placeholder={placeholder}
                  style={{padding:'0.35rem 0.7rem',borderRadius:'6px',background:'rgba(255,255,255,0.06)',
                    border:'1px solid var(--glass-border)',color:'#fff',fontSize:'0.82rem',width:'100%'}}/>
              </div>
            ))}
          </div>
        </div>

        {/* 저장 버튼 */}
        <div style={{display:'flex',justifyContent:'flex-end',alignItems:'center',gap:'0.75rem'}}>
          {msg && <span style={{fontSize:'0.8rem',color:msg.includes('실패')?'#ef4444':'var(--accent-mint)'}}>{msg}</span>}
          <button onClick={saveSettings} style={{padding:'0.45rem 1.2rem',borderRadius:'8px',
            background:'rgba(56,189,248,0.15)',border:'1px solid rgba(56,189,248,0.4)',
            color:'#38bdf8',cursor:'pointer',fontWeight:700,fontSize:'0.85rem'}}>
            💾 텔레그램 설정 저장
          </button>
        </div>
      </div>
    );
  };


  // ── 텔레그램 종목 언급 순위 ─────────────────────────────────
  const TelegramMentions = () => {
    const [allData,  setAllData]  = React.useState({ dates: [], stocks: [] });
    const [weekly,   setWeekly]   = React.useState([]);
    const [monthly,  setMonthly]  = React.useState([]);
    const [loading,  setLoading]  = React.useState(true);
    const [activeDay, setActiveDay] = React.useState(null); // null = 전체보기

    // 이번주 월~일 7일 고정 날짜 계산
    const getWeekDates = () => {
      const today  = new Date();
      const monday = new Date(today);
      monday.setDate(today.getDate() - today.getDay() + 1); // 이번주 월요일
      return Array.from({length: 7}, (_, i) => {
        const d = new Date(monday);
        d.setDate(monday.getDate() + i);
        return d.toISOString().slice(0, 10);
      });
    };
    const WEEK_DATES = React.useMemo(() => getWeekDates(), []);
    const DAYS_KO    = ['월','화','수','목','금','토','일'];

    React.useEffect(() => {
      const load = async () => {
        setLoading(true);
        try {
          const [d, w, m] = await Promise.all([
            fetch(API('/api/telegram/mentions/daily')).then(r => r.ok ? r.json() : { dates:[], stocks:[] }),
            fetch(API('/api/telegram/mentions/weekly')).then(r => r.ok ? r.json() : []),
            fetch(API('/api/telegram/mentions/monthly')).then(r => r.ok ? r.json() : []),
          ]);
          setAllData(d); setWeekly(w); setMonthly(m);
          setActiveDay(null);
        } catch(e) { console.error(e); }
        finally { setLoading(false); }
      };
      load();
    }, []);

    const marketColor = (m) => m === 'KOSPI' ? '#3b82f6' : m === 'KOSDAQ' ? '#22c55e' : '#94a3b8';
    const marketTag   = (m) => m === 'KOSPI' ? '🔵' : m === 'KOSDAQ' ? '🟢' : '⚪';

    // 선택된 요일 또는 전체 기준으로 TOP 20 계산
    const displayStocks = React.useMemo(() => {
      if (!allData.stocks.length) return [];
      if (activeDay === null) {
        // 전체: 7일 합계 기준 정렬
        return [...allData.stocks]
          .sort((a, b) => b.total - a.total)
          .slice(0, 20);
      } else {
        // 특정 날짜: 해당 날 언급 횟수 기준 정렬
        return [...allData.stocks]
          .map(s => ({...s, dayCount: s.daily[activeDay] || 0}))
          .filter(s => s.dayCount > 0)
          .sort((a, b) => b.dayCount - a.dayCount)
          .slice(0, 20);
      }
    }, [allData, activeDay]);

    const maxCnt = displayStocks.length
      ? Math.max(...displayStocks.map(s => activeDay ? s.dayCount : s.total), 1)
      : 1;

    if (loading) return (
      <div style={{display:'flex',alignItems:'center',justifyContent:'center',height:'300px',color:'var(--accent-mint)'}}>
        데이터 로딩 중...
      </div>
    );

    const today = new Date().toISOString().slice(0,10);

    return (
      <div className="fade-in" style={{display:'flex',flexDirection:'column',gap:'1.5rem'}}>

        {/* ── 일별 TOP 20 테이블 (요일 탭 고정) ── */}
        <div className="glass-panel" style={{overflow:'auto'}}>
          {/* 헤더 */}
          <div style={{padding:'0.8rem 1rem',borderBottom:'1px solid var(--glass-border)',display:'flex',alignItems:'center',gap:'0.5rem'}}>
            <Globe size={16} color="#38bdf8"/>
            <span style={{fontWeight:700,color:'#38bdf8'}}>일별 언급 종목 TOP 20</span>
            <span style={{fontSize:'0.72rem',color:'var(--text-secondary)',marginLeft:'auto'}}>이번주 · 오전/오후 합산</span>
          </div>

          {/* 가로 7컬럼 테이블 */}
          {allData.stocks.length === 0 ? (
            <div style={{padding:'3rem',textAlign:'center',color:'var(--text-secondary)'}}>
              아직 수집된 데이터가 없습니다.<br/>
              <span style={{fontSize:'0.8rem'}}>telegram_monitor.py를 실행하면 데이터가 쌓입니다.</span>
            </div>
          ) : (
            <div style={{overflowX:'auto'}}>
              <table className="premium-table" style={{width:'100%',minWidth:'700px'}}>
                <thead>
                  <tr>
                    <th style={{minWidth:'28px',position:'sticky',left:0,background:'var(--bg-dark)'}}>#</th>
                    <th style={{minWidth:'90px',position:'sticky',left:'28px',background:'var(--bg-dark)'}}>종목명</th>
                    <th style={{minWidth:'55px'}}>시장</th>
                    {WEEK_DATES.map((date, i) => {
                      const isToday   = date === today;
                      const hasFuture = date > today;
                      return (
                        <th key={date} style={{
                          textAlign:'center', minWidth:'52px',
                          color: hasFuture ? 'rgba(255,255,255,0.2)' : isToday ? '#38bdf8' : 'var(--text-secondary)',
                          fontSize:'0.7rem',
                        }}>
                          {DAYS_KO[i]}
                          {isToday && <span style={{display:'block',fontSize:'0.55rem',color:'#38bdf8'}}>오늘</span>}
                        </th>
                      );
                    })}
                    <th style={{textAlign:'right',minWidth:'55px',color:'#38bdf8'}}>합계</th>
                  </tr>
                </thead>
                <tbody>
                  {allData.stocks.slice(0,20).map((s, i) => {
                    const maxDay = Math.max(...WEEK_DATES.map(d => s.daily[d] || 0), 1);
                    return (
                      <tr key={s.stock_name}>
                        <td style={{color:'var(--text-secondary)',fontSize:'0.78rem',position:'sticky',left:0,background:'var(--bg-dark)'}}>{i+1}</td>
                        <td style={{position:'sticky',left:'28px',background:'var(--bg-dark)'}}>
                          <span style={{fontWeight:600,cursor:'pointer',fontSize:'0.85rem'}}
                            onClick={()=>{changeStock(s.stock_name);changeTab('analysis');}}>
                            {s.stock_name}
                          </span>
                        </td>
                        <td>
                          <span style={{fontSize:'0.7rem',color:marketColor(s.market)}}>
                            {marketTag(s.market)} {s.market}
                          </span>
                        </td>
                        {WEEK_DATES.map(date => {
                          const cnt       = s.daily[date] || 0;
                          const hasFuture = date > today;
                          return (
                            <td key={date} style={{textAlign:'center',padding:'0.6rem 0.3rem'}}>
                              {hasFuture ? (
                                <span style={{color:'rgba(255,255,255,0.1)'}}>-</span>
                              ) : cnt > 0 ? (
                                <div style={{display:'flex',flexDirection:'column',alignItems:'center',gap:'2px'}}>
                                  <span style={{
                                    fontSize:'0.82rem', fontWeight:700,
                                    color: cnt>=5?'#f59e0b':cnt>=3?'#38bdf8':'var(--text-primary)',
                                  }}>{cnt}</span>
                                  <div style={{width:'28px',height:'3px',borderRadius:'2px',background:'rgba(255,255,255,0.07)'}}>
                                    <div style={{
                                      width:`${(cnt/maxDay)*100}%`, height:'100%', borderRadius:'2px',
                                      background: cnt>=5?'#f59e0b':cnt>=3?'#38bdf8':'rgba(45,212,191,0.6)',
                                    }}/>
                                  </div>
                                </div>
                              ) : (
                                <span style={{color:'rgba(255,255,255,0.15)',fontSize:'0.75rem'}}>-</span>
                              )}
                            </td>
                          );
                        })}
                        <td style={{textAlign:'right',fontWeight:700,color:'#38bdf8',fontSize:'0.88rem'}}>{s.total}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* ── 주간 / 월간 테이블 ── */}
        <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:'1rem'}}>

          {/* 주간 TOP 20 */}
          <div className="glass-panel" style={{overflow:'auto'}}>
            <div style={{padding:'0.8rem 1rem',borderBottom:'1px solid var(--glass-border)',display:'flex',alignItems:'center',gap:'0.5rem'}}>
              <TrendingUp size={15} color="#a78bfa"/>
              <span style={{fontWeight:700,color:'#a78bfa',fontSize:'0.9rem'}}>최근 6일 TOP 20</span>
              <span style={{fontSize:'0.68rem',color:'var(--text-secondary)',marginLeft:'auto'}}>최근 6일 rolling</span>
            </div>
            {weekly.length === 0 ? (
              <div style={{padding:'2rem',textAlign:'center',color:'var(--text-secondary)',fontSize:'0.8rem'}}>데이터 없음</div>
            ) : (
              <table className="premium-table" style={{width:'100%'}}>
                <thead><tr>
                  <th style={{width:'28px'}}>#</th>
                  <th>종목명</th>
                  <th style={{minWidth:'55px'}}>시장</th>
                  <th style={{textAlign:'right'}}>언급</th>
                </tr></thead>
                <tbody>
                  {weekly.map((s, i) => (
                    <tr key={s.stock_name}>
                      <td style={{color:'var(--text-secondary)',fontSize:'0.8rem'}}>{i+1}</td>
                      <td style={{fontWeight:600,cursor:'pointer'}}
                        onClick={()=>{changeStock(s.stock_name);changeTab('analysis');}}>
                        {s.stock_name}
                      </td>
                      <td style={{fontSize:'0.72rem',color:marketColor(s.market)}}>{marketTag(s.market)} {s.market}</td>
                      <td style={{textAlign:'right',fontWeight:700,color:'#a78bfa'}}>{s.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* 월간 TOP 20 */}
          <div className="glass-panel" style={{overflow:'auto'}}>
            <div style={{padding:'0.8rem 1rem',borderBottom:'1px solid var(--glass-border)',display:'flex',alignItems:'center',gap:'0.5rem'}}>
              <BarChart3 size={15} color="#f59e0b"/>
              <span style={{fontWeight:700,color:'#f59e0b',fontSize:'0.9rem'}}>이번달 TOP 20</span>
              <span style={{fontSize:'0.68rem',color:'var(--text-secondary)',marginLeft:'auto'}}>1일~오늘</span>
            </div>
            {monthly.length === 0 ? (
              <div style={{padding:'2rem',textAlign:'center',color:'var(--text-secondary)',fontSize:'0.8rem'}}>데이터 없음</div>
            ) : (
              <table className="premium-table" style={{width:'100%'}}>
                <thead><tr>
                  <th style={{width:'28px'}}>#</th>
                  <th>종목명</th>
                  <th style={{minWidth:'55px'}}>시장</th>
                  <th style={{textAlign:'right'}}>언급</th>
                </tr></thead>
                <tbody>
                  {monthly.map((s, i) => (
                    <tr key={s.stock_name}>
                      <td style={{color:'var(--text-secondary)',fontSize:'0.8rem'}}>{i+1}</td>
                      <td style={{fontWeight:600,cursor:'pointer'}}
                        onClick={()=>{changeStock(s.stock_name);changeTab('analysis');}}>
                        {s.stock_name}
                      </td>
                      <td style={{fontSize:'0.72rem',color:marketColor(s.market)}}>{marketTag(s.market)} {s.market}</td>
                      <td style={{textAlign:'right',fontWeight:700,color:'#f59e0b'}}>{s.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>
    );
  };

  // ── 시스템 상태 ──────────────────────────────────────────────
  const SystemStatus = () => (
    <div className="fade-in glass-panel" style={{ padding: '2rem' }}>
      <div className="section-title"><Database size={20} color="var(--accent-purple)" /><h2>시스템 및 데이터베이스 상태</h2></div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem', marginTop: '1rem' }}>
        <div style={{ padding: '1.5rem', background: 'rgba(255,255,255,0.02)', borderRadius: '12px', border: '1px solid var(--glass-border)' }}>
          <p style={{ color: 'var(--text-secondary)', marginBottom: '0.5rem' }}>데이터베이스 파일 경로</p>
          <code style={{ fontSize: '1.1rem', color: 'var(--accent-mint)' }}>{sysStats?.db_path || "로딩 중..."}</code>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '1rem' }}>
          <div className="glass-panel" style={{ padding: '1.5rem' }}>
            <p style={{ color: 'var(--text-secondary)' }}>수집된 기업 수</p>
            <h3 style={{ fontSize: '2rem' }}>{sysStats?.stock_count ?? 0} 개</h3>
          </div>
          <div className="glass-panel" style={{ padding: '1.5rem' }}>
            <p style={{ color: 'var(--text-secondary)' }}>총 주가 데이터</p>
            <h3 style={{ fontSize: '2rem' }}>{sysStats?.price_records?.toLocaleString() ?? 0} 건</h3>
          </div>
          <div className="glass-panel" style={{ padding: '1.5rem' }}>
            <p style={{ color: 'var(--text-secondary)' }}>마지막 업데이트</p>
            <h3 style={{ fontSize: '1rem', marginTop: '0.5rem' }}>{sysStats?.last_update ?? '-'}</h3>
          </div>
        </div>
      </div>
    </div>
  );

  // ── 메인 렌더 ────────────────────────────────────────────────
  const [sidebarOpen, setSidebarOpen] = React.useState(false);

  const NAV_ITEMS = [
    // ── 상단 섹션 ──────────────────────────────────
    { key: 'macro',            icon: <LayoutDashboard size={17} />,                            label: '종합현황' },
    { key: 'market_indicators',icon: <Globe size={17} style={{color:'#fbbf24'}} />,           label: '시장 지표' },
    { key: 'analysis',         icon: <BarChart3 size={17} />,                                 label: '개별 종목' },
    { key: 'semiconductor_sector', icon: <Cpu size={17} style={{color:'#60a5fa'}} />,         label: '반도체 섹터' },
    { key: 'screener',         icon: <Cpu size={17} style={{color:'#2dd4bf'}} />,              label: 'AI 종목' },
    { key: 'trend',            icon: <TrendingUp size={17} style={{color:'#a78bfa'}} />,       label: '가상 매매' },
    { key: 'reports',          icon: <Newspaper size={17} style={{color:'#34d399'}} />,        label: '섹터 보고서' },
    { key: 'telegram',  icon: <Send size={17} style={{color:'#38bdf8'}} />,            label: '텔레그램 종목' },
    { key: 'backtest',  icon: <FlaskConical size={17} style={{color:'#f59e0b'}} />,    label: '📊 백테스트' },
    { key: 'hs_trade2', icon: <Ship size={17} style={{color:'#93c5fd'}} />,            label: '📦 수출입분석' },
    null,
    // ── 하단 섹션 ──────────────────────────────────
    { key: 'buy_candidates', icon: <Target size={17} style={{color:'#f59e0b'}} />,    label: '매수후보' },
    { key: 'watchlist', icon: <Star size={17} style={{color:'#facc15'}} />,            label: '관심종목' },
    { key: 'portfolio', icon: <Wallet size={17} style={{color:'#c084fc'}} />,          label: '계좌현황 🔒' },
    null,
    { key: 'settings',  icon: <Settings size={17} style={{color:'#94a3b8'}} />,        label: '⚙ 설정' },
    { key: 'system',    icon: <Server size={17} style={{color:'#64748b'}} />,          label: '시스템 상태' },
  ];

  return (
    <div style={{ display: 'flex', height: '100vh', width: '100vw', overflow: 'hidden' }}>
      {isMobile && sidebarOpen && (
        <div onClick={()=>setSidebarOpen(false)}
          style={{position:'fixed',inset:0,background:'rgba(0,0,0,0.5)',zIndex:19}}/>
      )}
      {isMobile && (
        <button onClick={()=>setSidebarOpen(v=>!v)} style={{
          position:'fixed',top:'0.75rem',left:'0.75rem',zIndex:25,
          background:'var(--bg-dark)',border:'1px solid var(--glass-border)',
          borderRadius:'8px',padding:'0.4rem 0.6rem',cursor:'pointer',
          color:'var(--text-primary)',fontSize:'1.2rem',lineHeight:1,
        }}>☰</button>
      )}
      <aside
        onMouseEnter={()=>!isMobile&&setSidebarOpen(true)}
        onMouseLeave={()=>!isMobile&&setSidebarOpen(false)}
        style={{
          width: sidebarOpen?'210px':(isMobile?'0':'50px'),
          minWidth: sidebarOpen?'210px':(isMobile?'0':'50px'),
          background:'var(--bg-dark)',
          borderRight: sidebarOpen?'1px solid var(--glass-border)':'none',
          display:'flex',flexDirection:'column',
          padding: sidebarOpen?'1.2rem 0.5rem':'0',
          transition:'width 0.25s ease,min-width 0.25s ease,padding 0.25s ease',
          overflowX:'hidden', overflowY:'auto', flexShrink:0, zIndex:20,
          ...(isMobile?{position:'fixed',top:0,left:0,height:'100vh'}:{}),
        }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '1.8rem', paddingLeft: '0.3rem', whiteSpace: 'nowrap' }}>
          <Activity color="var(--accent-mint)" size={22} style={{ flexShrink: 0 }} />
          {sidebarOpen && <h1 className="neon-text" style={{ fontSize: '1rem', fontWeight: 800 }}>주식분석</h1>}
        </div>
        <nav style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: '0.2rem', minHeight: 0, overflowY: 'auto', paddingRight: sidebarOpen ? '0.15rem' : 0 }}>
          {NAV_ITEMS.map((item, i) =>
            item === null ? (
              <div key={`div-${i}`} style={{ margin: '0.5rem 0', borderTop: '1px solid var(--glass-border)' }} />
            ) : (
              <button key={item.key} onClick={() => {
                if (item.key === 'portfolio' && !portfolioAuth) {
                  const pw = window.prompt('계좌현황 비밀번호를 입력하세요:');
                  if (pw === '5133') { setPortfolioAuth(true); changeTab(item.key); }
                  else if (pw !== null) window.alert('비밀번호가 틀렸습니다.');
                } else { changeTab(item.key); }
              }}
                className={`nav-item ${activeTab === item.key ? 'active' : ''}`}
                title={item.label}
                style={{ justifyContent: 'flex-start', padding: '0.5rem 0.6rem', whiteSpace: 'nowrap', overflow: 'hidden' }}>
                <span style={{ flexShrink: 0, display: 'flex' }}>{item.icon}</span>
                {sidebarOpen && <span style={{ marginLeft: '0.6rem' }}>{item.label}</span>}
              </button>
            )
          )}
        </nav>
        {sidebarOpen && (
          <div style={{ padding: '0.6rem', borderRadius: '8px', background: 'rgba(255,255,255,0.03)', fontSize: '0.72rem', marginTop: '0.5rem', whiteSpace: 'nowrap' }}>
            <p style={{ color: 'var(--text-secondary)' }}>서버 상태</p>
            <p style={{ color: 'var(--accent-mint)', fontWeight: 700 }}>● Operational</p>
          </div>
        )}
      </aside>

      {/* Main */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <header style={{height:'52px',borderBottom:'1px solid var(--glass-border)',
          display:'flex',alignItems:'center',justifyContent:'space-between',
          padding:isMobile?'0 0.75rem 0 3.5rem':'0 2rem',flexShrink:0}}>
          <h2 style={{fontSize:isMobile?'0.9rem':'1rem',fontWeight:600}}>{TAB_TITLES[activeTab]}</h2>
          <form onSubmit={handleSearch} style={{position:'relative',width:isMobile?'160px':'300px'}}>
            <input type="text" placeholder={isMobile?'검색...':'종목명/코드 검색...'}
              value={searchQuery}
              onChange={e=>setSearchQuery(e.target.value)}
              onBlur={()=>setTimeout(()=>setShowSearchDrop(false),150)}
              onFocus={()=>{ if(searchResults.length>0) setShowSearchDrop(true); }}
              style={{width:'100%',padding:'0.4rem 0.8rem 0.4rem 2rem',borderRadius:'8px',
                background:'rgba(255,255,255,0.05)',border:'1px solid var(--glass-border)',
                color:'#fff',fontSize:isMobile?'0.85rem':'0.9rem'}}/>
            <Search size={14} style={{position:'absolute',left:'0.6rem',top:'50%',
              transform:'translateY(-50%)',color:'var(--text-secondary)'}}/>
            {showSearchDrop && searchResults.length > 0 && (
              <div style={{position:'absolute',top:'calc(100% + 4px)',left:0,right:0,
                background:'rgba(15,15,25,0.97)',backdropFilter:'blur(10px)',
                border:'1px solid var(--glass-border)',borderRadius:'8px',
                zIndex:100,boxShadow:'0 8px 24px rgba(0,0,0,0.5)',overflow:'hidden'}}>
                {searchResults.map((item, idx) => (
                  <div key={idx}
                    onMouseDown={()=>{ setSearchQuery(item.name); handleSearch(null, item.code); }}
                    style={{padding:'0.55rem 0.9rem',cursor:'pointer',display:'flex',
                      justifyContent:'space-between',alignItems:'center',
                      borderBottom: idx<searchResults.length-1?'1px solid rgba(255,255,255,0.05)':'none',
                      transition:'background 0.1s'}}
                    onMouseOver={e=>e.currentTarget.style.background='rgba(45,212,191,0.1)'}
                    onMouseOut={e=>e.currentTarget.style.background='transparent'}>
                    <span style={{fontWeight:600,fontSize:'0.85rem'}}>{item.name}</span>
                    <span style={{fontSize:'0.75rem',color:'var(--text-secondary)',
                      fontFamily:'monospace'}}>{item.code}</span>
                  </div>
                ))}
              </div>
            )}
          </form>
        </header>

        <div style={{flex:1,padding:isMobile?'1rem 0.75rem':'1.5rem',overflowY:'auto'}}>
          {/* [버그 ② 수정] screener / insight 탭 렌더링 연결 */}
          {activeTab === 'macro'             && <MacroDashboard />}
          <div style={{display: activeTab === 'market_indicators' ? 'block' : 'none'}}><MarketIndicatorsView onChangeStock={changeStock} onChangeTab={changeTab} /></div>
          {activeTab === 'analysis'          && <StockAnalysis />}
          {activeTab === 'semiconductor_sector' && (
            <div className="glass-panel" style={{padding:'0.9rem', height:'calc(100vh - 110px)'}}>
              <div style={{
                display:'flex',
                alignItems:'center',
                justifyContent:'space-between',
                gap:'1rem',
                padding:'0 0.2rem 0.8rem',
                borderBottom:'1px solid var(--glass-border)',
                marginBottom:'0.8rem'
              }}>
                <div>
                  <h3 style={{margin:0,fontSize:'1rem',fontWeight:700}}>반도체 섹터</h3>
                  <p style={{margin:'0.3rem 0 0',fontSize:'0.82rem',color:'var(--text-secondary)'}}>
                    `hs_trade_lab`의 독립 반도체 밸류체인 분석 페이지입니다.
                  </p>
                </div>
                <a
                  href="/hs/semiconductor-lab/"
                  target="_blank"
                  rel="noreferrer"
                  style={{
                    color:'var(--accent-mint)',
                    fontSize:'0.82rem',
                    textDecoration:'none',
                    fontWeight:600
                  }}
                >
                  새 창으로 열기
                </a>
              </div>
              <iframe
                src="/hs/semiconductor-lab/"
                title="반도체 섹터"
                style={{
                  width:'100%',
                  height:'calc(100% - 52px)',
                  border:'none',
                  borderRadius:'14px',
                  background:'transparent',
                  display:'block'
                }}
              />
            </div>
          )}
          {activeTab === 'buy_candidates' && <BuyCandidateView />}
          {activeTab === 'watchlist' && <WatchlistView />}
          {activeTab === 'portfolio' && portfolioAuth && <PortfolioView />}
          {activeTab === 'screener'  && <Screener />}
          {activeTab === 'trend'     && <PeakView />}
          {activeTab === 'reports'   && <SectorReports />}
          {activeTab === 'insight'   && <AIInsight />}
          {activeTab === 'system'    && <SystemStatus />}
          {activeTab === 'telegram'  && <TelegramMentions />}
          {activeTab === 'settings'  && <SettingsView />}
          {activeTab === 'backtest'  && <BacktestView />}
          {activeTab === 'hs_trade2' && <TradeAnalysis2 />}
        </div>
      </div>

      {/* 로딩 오버레이 */}
      {loading && (
        <div className="loading-overlay">
          <div style={{ textAlign: 'center' }}>
            <div style={{ width: '40px', height: '40px', border: '3px solid rgba(45,212,191,0.2)', borderTop: '3px solid var(--accent-mint)', borderRadius: '50%', animation: 'spin 0.8s linear infinite', margin: '0 auto 1rem' }} />
            <p style={{ color: 'var(--accent-mint)' }}>데이터 로딩 중...</p>
          </div>
        </div>
      )}

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  );
};

export default App;
