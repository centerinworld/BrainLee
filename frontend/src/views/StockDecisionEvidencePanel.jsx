import React from 'react';

const API = (path) => path;
const strengthColor = { high:'#4ade80', medium:'#fbbf24', low:'#64748b' };
const directionColor = { positive:'#4ade80', negative:'#f87171', neutral:'#94a3b8' };

export default function StockDecisionEvidencePanel({ stockCode, active = true }) {
  const [regime,setRegime]=React.useState(null);
  const [signals,setSignals]=React.useState([]);
  const [outcomes,setOutcomes]=React.useState([]);
  React.useEffect(()=>{
    if(!active){return;}
    if(!/^\d{6}$/.test(stockCode||'')){setSignals([]);setOutcomes([]);return;}
    let alive=true;
    Promise.all([
      fetch(API('/api/dashboard/market-regime/latest')).then(r=>r.ok?r.json():null),
      fetch(API(`/api/dashboard/explainable-signals/${stockCode}`)).then(r=>r.ok?r.json():null),
      fetch(API(`/api/dashboard/live-signal-outcomes/${stockCode}`)).then(r=>r.ok?r.json():null),
    ]).then(([m,s,o])=>{if(alive){setRegime(m);setSignals(s?.signals||[]);setOutcomes(o?.outcomes||[]);}}).catch(()=>{});
    return()=>{alive=false;};
  },[stockCode, active]);
  if(!signals.length&&!outcomes.length&&!regime)return null;
  return <section className="glass-panel" style={{padding:'0.85rem 1rem'}}>
    <div style={{display:'flex',alignItems:'center',gap:'0.6rem',flexWrap:'wrap',marginBottom:'0.7rem'}}>
      <span style={{fontSize:'0.82rem',fontWeight:800,color:'#2dd4bf'}}>투자 근거와 검증</span>
      {regime?.market_regime&&<span style={{fontSize:'0.68rem',padding:'0.16rem 0.45rem',border:'1px solid rgba(255,255,255,.14)',borderRadius:'5px',color:'#cbd5e1'}}>
        시장 {regime.market_regime} · 점수 {Number(regime.regime_score||0).toFixed(1)} · {regime.available_at} 사용가능
      </span>}
    </div>
    {signals.length>0&&<div style={{overflowX:'auto'}}><table style={{width:'100%',borderCollapse:'collapse',minWidth:'720px',fontSize:'0.72rem'}}>
      <thead><tr style={{color:'#64748b',borderBottom:'1px solid rgba(255,255,255,.1)'}}>{['지표','기간','변화','사업노출','신뢰도','강도','판정 근거'].map((h,i)=><th key={h} style={{padding:'0.4rem',textAlign:i===0||i===6?'left':'right'}}>{h}</th>)}</tr></thead>
      <tbody>{signals.slice(0,12).map(s=><tr key={`${s.indicator_key}_${s.period}`} style={{borderBottom:'1px solid rgba(255,255,255,.05)'}}>
        <td style={{padding:'0.42rem',color:'#e2e8f0',fontWeight:700}}>{s.indicator_name}</td><td style={{padding:'0.42rem',textAlign:'right'}}>{s.period}</td>
        <td style={{padding:'0.42rem',textAlign:'right',color:directionColor[s.direction]}}>{s.change_pct==null?'-':`${Number(s.change_pct)>=0?'+':''}${Number(s.change_pct).toFixed(1)}%`}</td>
        <td style={{padding:'0.42rem',textAlign:'right'}}>{Math.max(Number(s.revenue_exposure_pct||0),Number(s.profit_exposure_pct||0),Number(s.cost_exposure_pct||0)).toFixed(1)}%</td>
        <td style={{padding:'0.42rem',textAlign:'right'}}>{(Number(s.mapping_confidence||0)*100).toFixed(0)}%</td><td style={{padding:'0.42rem',textAlign:'right',color:strengthColor[s.signal_strength],fontWeight:800}}>{s.signal_strength}</td>
        <td style={{padding:'0.42rem',color:'#94a3b8'}} title={s.explanation}>{s.explanation}</td></tr>)}</tbody>
    </table></div>}
    {!signals.length&&<div style={{fontSize:'0.72rem',color:'#64748b'}}>검증된 사업노출 기반 지표 신호가 없습니다.</div>}
    {outcomes.length>0&&<div style={{marginTop:'0.8rem',borderTop:'1px solid rgba(255,255,255,.08)',paddingTop:'0.6rem'}}>
      <div style={{fontSize:'0.74rem',fontWeight:700,color:'#cbd5e1',marginBottom:'0.4rem'}}>실제 발생 신호 사후성과</div>
      <div style={{display:'flex',gap:'0.35rem',flexWrap:'wrap'}}>{outcomes.filter(o=>o.status==='complete').slice(0,12).map(o=><span key={`${o.signal_id}_${o.horizon_days}`} style={{fontSize:'0.68rem',padding:'0.18rem 0.42rem',borderRadius:'4px',background:'rgba(255,255,255,.04)',color:Number(o.return_pct)>=0?'#4ade80':'#f87171'}}>{o.signal_type} {o.horizon_days}일 {Number(o.return_pct)>=0?'+':''}{Number(o.return_pct).toFixed(1)}%</span>)}</div>
    </div>}
  </section>;
}
