import React from 'react';
const API = (path) => path;
const color = { pass:'#4ade80', watch:'#fbbf24', fail:'#f87171' };
function Review({ review }) {
  const data = review.result;
  if (review.status === 'failed') return <div style={{fontSize:'0.72rem',color:'#f87171'}}>{review.provider}: {review.error_text}</div>;
  if (!data) return null;
  return <div style={{flex:'1 1 360px',padding:'0.7rem',border:'1px solid rgba(255,255,255,.1)',borderRadius:8}}>
    <div style={{display:'flex',justifyContent:'space-between',gap:'0.5rem',marginBottom:'0.5rem'}}><b style={{fontSize:'0.78rem',color:'#e2e8f0'}}>{review.provider === 'openai' ? 'GPT' : 'Gemini'} 독립 검토</b><span style={{fontSize:'0.68rem',color:'#94a3b8'}}>{data.verdict || '판정 없음'} · {data.confidence || '신뢰도 미정'}</span></div>
    <div style={{display:'grid',gridTemplateColumns:'repeat(2,minmax(0,1fr))',gap:'0.35rem'}}>{Object.entries(data.filters || {}).map(([key, value]) => <div key={key} style={{fontSize:'0.68rem',color:'#cbd5e1'}}><span style={{color:color[value.status] || '#94a3b8',fontWeight:800}}>{key}: {value.status || '-'}</span><br />{value.reason || '-'}</div>)}</div>
    {(data.countercase || []).length > 0 && <div style={{fontSize:'0.69rem',color:'#fca5a5',marginTop:'0.55rem'}}>반대 논거: {data.countercase.map(x => x.statement).filter(Boolean).join(' / ')}</div>}
    {(data.next_checks || []).length > 0 && <div style={{fontSize:'0.69rem',color:'#93c5fd',marginTop:'0.35rem'}}>확인할 것: {data.next_checks.filter(Boolean).join(' / ')}</div>}
  </div>;
}
export default function InvestmentDecisionTaskPanel({ stockCode, active = true }) {
  const [data,setData] = React.useState(null); const [loading,setLoading] = React.useState(false);
  const load = React.useEffectEvent(async () => { if (!/^\d{6}$/.test(stockCode || '')) return setData(null); const response = await fetch(API(`/api/investment-decisions/tasks/${stockCode}/latest`)); if (response.ok) setData(await response.json()); });
  React.useEffect(() => { if (active) load().catch(() => {}); }, [active, stockCode]);
  React.useEffect(() => { if (!active || !['queued','reviewing'].includes(data?.task?.status)) return; const timer = window.setInterval(() => load().catch(() => {}), 3000); return () => window.clearInterval(timer); }, [active, data?.task?.status]);
  if (!active || !/^\d{6}$/.test(stockCode || '')) return null;
  const run = async () => { setLoading(true); try { const response=await fetch(API(`/api/investment-decisions/tasks/${stockCode}`),{method:'POST'}); if (!response.ok) throw new Error(); await load(); } finally { setLoading(false); } };
  const task=data?.task;
  return <section className="glass-panel" style={{padding:'0.85rem 1rem',marginTop:'0.75rem'}}>
    <div style={{display:'flex',justifyContent:'space-between',alignItems:'center',gap:'0.7rem',flexWrap:'wrap'}}><div><b style={{fontSize:'0.82rem',color:'#facc15'}}>투자위원회 검토</b><span style={{fontSize:'0.68rem',color:'#94a3b8',marginLeft:'0.5rem'}}>문서 RAG + GPT/Gemini 독립 심사 · 주문 미연결</span></div><button onClick={run} disabled={loading} style={{padding:'0.3rem 0.65rem',borderRadius:5,border:'1px solid rgba(250,204,21,.45)',background:'rgba(250,204,21,.1)',color:'#facc15',cursor:'pointer',fontSize:'0.72rem'}}>{loading ? '접수 중...' : '검토 작업 실행'}</button></div>
    {task && <div style={{fontSize:'0.7rem',color:task.status==='failed'?'#f87171':'#94a3b8',marginTop:'0.55rem'}}>상태: {task.status}{task.error_text ? ` · ${task.error_text}` : ''}</div>}
    {task?.status === 'waiting_offpeak' && <div style={{fontSize:'0.7rem',color:'#fbbf24',marginTop:'0.3rem'}}>평일 20:05 KST에 저가 구간으로 자동 재개됩니다.</div>}
    {data?.reviews?.length > 0 && <div style={{display:'flex',gap:'0.6rem',flexWrap:'wrap',marginTop:'0.65rem'}}>{data.reviews.map((r,i)=><Review key={`${r.provider}-${i}`} review={r} />)}</div>}
  </section>;
}
