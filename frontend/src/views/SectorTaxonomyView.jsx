import React, { useCallback, useEffect, useMemo, useState } from 'react';

const API = (path) => path;
const SOURCE_LABELS = {
  universe_legacy: '기존 업종',
  stockeasy: '스탁이지',
  kiwoom: '키움',
  kiwoom_theme: '키움 테마',
  kiwoom_industry: '키움 업종',
  internal_taxonomy: '내부 정밀분류',
};
const DIMENSION_LABELS = {
  industry_large: '대분류', industry_major: '대분류', industry_middle: '중분류',
  industry_small: '소분류', industry: '업종', theme: '테마', value_chain: '밸류체인',
  market_segment: '시장구분', process: '공정', material: '소재', peer_group: '실질 경쟁군',
};

const fmtCap = (value) => {
  const number = Number(value);
  if (!Number.isFinite(number)) return '-';
  return `${Math.round(number).toLocaleString()}억`;
};

const badgeStyle = (status) => ({
  display: 'inline-flex', alignItems: 'center', padding: '2px 7px', borderRadius: 999,
  fontSize: '0.66rem', fontWeight: 700,
  color: status === 'verified' ? '#86efac' : status === 'observed' ? '#93c5fd' : '#fcd34d',
  background: status === 'verified' ? 'rgba(34,197,94,.13)' : status === 'observed' ? 'rgba(59,130,246,.13)' : 'rgba(245,158,11,.13)',
  border: `1px solid ${status === 'verified' ? 'rgba(34,197,94,.3)' : status === 'observed' ? 'rgba(59,130,246,.3)' : 'rgba(245,158,11,.3)'}`,
});

export default function SectorTaxonomyView() {
  const [overview, setOverview] = useState(null);
  const [nodes, setNodes] = useState([]);
  const [stocks, setStocks] = useState([]);
  const [detail, setDetail] = useState(null);
  const [peerGroups, setPeerGroups] = useState([]);
  const [taxonomy, setTaxonomy] = useState('internal_taxonomy');
  const [dimension, setDimension] = useState('');
  const [selectedNode, setSelectedNode] = useState(null);
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState('taxonomy');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    Promise.all([
      fetch(API('/api/sector-taxonomy/overview')).then(r => r.ok ? r.json() : Promise.reject(new Error('개요 조회 실패'))),
      fetch(API('/api/sector-taxonomy/peer-groups')).then(r => r.ok ? r.json() : Promise.reject(new Error('경쟁군 조회 실패'))),
    ]).then(([o, p]) => { setOverview(o); setPeerGroups(p.groups || []); })
      .catch(e => setError(e.message));
  }, []);

  useEffect(() => {
    setLoading(true);
    const params = new URLSearchParams();
    if (taxonomy) params.set('taxonomy', taxonomy);
    if (dimension) params.set('dimension', dimension);
    fetch(API(`/api/sector-taxonomy/tree?${params}`))
      .then(r => r.ok ? r.json() : Promise.reject(new Error('분류 트리 조회 실패')))
      .then(data => { setNodes(data.nodes || []); setSelectedNode(null); setStocks([]); })
      .catch(e => setError(e.message)).finally(() => setLoading(false));
  }, [taxonomy, dimension]);

  const loadStocks = useCallback((node) => {
    setSelectedNode(node);
    setDetail(null);
    setLoading(true);
    fetch(API(`/api/sector-taxonomy/stocks?node_key=${encodeURIComponent(node.node_key)}&limit=500`))
      .then(r => r.ok ? r.json() : Promise.reject(new Error('구성종목 조회 실패')))
      .then(data => setStocks(data.stocks || []))
      .catch(e => setError(e.message)).finally(() => setLoading(false));
  }, []);

  const searchStocks = useCallback((event) => {
    event.preventDefault();
    if (!query.trim()) return;
    setSelectedNode(null); setDetail(null); setLoading(true);
    fetch(API(`/api/sector-taxonomy/stocks?q=${encodeURIComponent(query.trim())}&limit=100`))
      .then(r => r.ok ? r.json() : Promise.reject(new Error('종목 검색 실패')))
      .then(data => setStocks(data.stocks || []))
      .catch(e => setError(e.message)).finally(() => setLoading(false));
  }, [query]);

  const loadDetail = useCallback((code) => {
    fetch(API(`/api/sector-taxonomy/stock/${code}`))
      .then(r => r.ok ? r.json() : Promise.reject(new Error('종목 분류 조회 실패')))
      .then(setDetail).catch(e => setError(e.message));
  }, []);

  const dimensions = useMemo(() => {
    if (!overview) return [];
    return [...new Set(overview.dimensions.filter(x => x.taxonomy === taxonomy).map(x => x.dimension))];
  }, [overview, taxonomy]);

  const taxonomies = useMemo(() => {
    if (!overview) return [];
    return [...new Set(overview.dimensions.map(x => x.taxonomy))];
  }, [overview]);

  const inputStyle = { background:'rgba(15,23,42,.65)', color:'var(--text-primary)', border:'1px solid rgba(148,163,184,.22)', borderRadius:8, padding:'8px 10px' };

  return <div className="fade-in" style={{display:'flex', flexDirection:'column', gap:'0.85rem'}}>
    <div className="glass-panel" style={{padding:'1rem 1.2rem'}}>
      <div style={{display:'flex', justifyContent:'space-between', gap:'1rem', flexWrap:'wrap', alignItems:'center'}}>
        <div>
          <h2 style={{margin:0, fontSize:'1.25rem'}}>한국 주식 다중 분류</h2>
          <div style={{fontSize:'0.75rem', color:'var(--text-secondary)', marginTop:4}}>업종·테마·밸류체인·공정·소재·실질 경쟁군을 출처와 근거별로 조회합니다.</div>
        </div>
        {overview && <div style={{display:'flex', gap:8, flexWrap:'wrap'}}>
          <Stat label="분류 커버리지" value={`${overview.covered_stock_count.toLocaleString()} / ${overview.universe_count.toLocaleString()}`} />
          <Stat label="커버리지" value={`${overview.coverage_pct}%`} />
          <Stat label="분류 축" value={`${overview.dimensions.length}개`} />
        </div>}
      </div>
      {overview && <div style={{display:'flex', gap:6, flexWrap:'wrap', marginTop:10}}>
        {overview.sources.map(source => <span key={source.source_system} title={source.source_url || ''} style={{fontSize:'0.67rem', padding:'4px 8px', borderRadius:8, background:'rgba(148,163,184,.1)', color: source.days_old > 30 ? '#fbbf24' : 'var(--text-secondary)'}}>
          {SOURCE_LABELS[source.source_system] || source.source_system} · {source.snapshot_date} · {source.membership_count.toLocaleString()}건{source.days_old > 30 ? ` · ${source.days_old}일 경과` : ''}
        </span>)}
      </div>}
    </div>

    {error && <div style={{padding:'0.7rem 1rem', color:'#fca5a5', background:'rgba(239,68,68,.1)', borderRadius:8}}>{error}</div>}

    <div style={{display:'flex', gap:8, flexWrap:'wrap', alignItems:'center'}}>
      <button onClick={() => setMode('taxonomy')} style={{...inputStyle, cursor:'pointer', color:mode==='taxonomy'?'#67e8f9':'var(--text-secondary)'}}>분류 탐색</button>
      <button onClick={() => setMode('peers')} style={{...inputStyle, cursor:'pointer', color:mode==='peers'?'#67e8f9':'var(--text-secondary)'}}>실질 경쟁군</button>
      <form onSubmit={searchStocks} style={{display:'flex', gap:6, marginLeft:'auto'}}>
        <input value={query} onChange={e => setQuery(e.target.value)} placeholder="종목명·코드 검색" style={{...inputStyle, width:180}} />
        <button style={{...inputStyle, cursor:'pointer'}}>검색</button>
      </form>
    </div>

    {mode === 'peers' ? <div style={{display:'grid', gridTemplateColumns:'repeat(auto-fit,minmax(320px,1fr))', gap:'0.8rem'}}>
      {peerGroups.map(group => <div className="glass-panel" key={group.node_key} style={{padding:'1rem'}}>
        <div style={{fontWeight:800, color:'#c4b5fd'}}>{group.name}</div>
        <div style={{fontSize:'0.7rem', color:'var(--text-secondary)', margin:'4px 0 10px'}}>{group.description}</div>
        {group.members.map(member => <button key={member.stock_code} onClick={() => { setMode('taxonomy'); loadDetail(member.stock_code); }} style={{display:'flex', width:'100%', justifyContent:'space-between', alignItems:'center', background:'transparent', border:0, borderTop:'1px solid rgba(148,163,184,.1)', padding:'8px 2px', color:'var(--text-primary)', cursor:'pointer'}}>
          <span>{member.stock_name} <small style={{color:'var(--text-secondary)'}}>{member.stock_code}</small></span>
          <span style={badgeStyle(member.status)}>{member.status === 'verified' ? '검증' : member.status}</span>
        </button>)}
      </div>)}
    </div> : <>
      <div style={{display:'flex', gap:8, flexWrap:'wrap'}}>
        <select value={taxonomy} onChange={e => setTaxonomy(e.target.value)} style={inputStyle}>
          {taxonomies.map(item => <option key={item} value={item}>{SOURCE_LABELS[item] || item}</option>)}
        </select>
        <select value={dimension} onChange={e => setDimension(e.target.value)} style={inputStyle}>
          <option value="">전체 축</option>
          {dimensions.map(item => <option key={item} value={item}>{DIMENSION_LABELS[item] || item}</option>)}
        </select>
        <span style={{fontSize:'0.7rem', color:'var(--text-secondary)', alignSelf:'center'}}>분류를 누르면 구성종목을 봅니다. 종목을 누르면 모든 출처와 근거를 봅니다.</span>
      </div>

      <div style={{display:'grid', gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,320px),1fr))', gap:'0.8rem', alignItems:'start'}}>
        <div className="glass-panel" style={{padding:'0.65rem', maxHeight:'68vh', overflowY:'auto'}}>
          <div style={{fontSize:'0.72rem', color:'var(--text-secondary)', padding:'4px 6px 8px'}}>분류 {nodes.length.toLocaleString()}개</div>
          {nodes.map(node => <button key={node.node_key} onClick={() => loadStocks(node)} style={{display:'flex', width:'100%', justifyContent:'space-between', gap:8, padding:'8px', borderRadius:7, border:0, background:selectedNode?.node_key===node.node_key?'rgba(59,130,246,.18)':'transparent', color:'var(--text-primary)', cursor:'pointer', textAlign:'left'}}>
            <span><small style={{display:'block', color:'#94a3b8'}}>{DIMENSION_LABELS[node.dimension] || node.dimension}</small>{node.name}</span>
            <b style={{color:'#67e8f9'}}>{Number(node.stock_count).toLocaleString()}</b>
          </button>)}
        </div>

        <div className="glass-panel" style={{padding:'0.65rem', maxHeight:'68vh', overflowY:'auto'}}>
          <div style={{fontSize:'0.72rem', color:'var(--text-secondary)', padding:'4px 6px 8px'}}>{selectedNode?.name || (query ? `'${query}' 검색` : '분류 또는 종목을 선택하세요')} · {stocks.length.toLocaleString()}종목</div>
          {loading && <div style={{padding:'1rem', color:'var(--text-secondary)'}}>불러오는 중…</div>}
          {stocks.map(stock => <button key={stock.stock_code} onClick={() => loadDetail(stock.stock_code)} style={{display:'grid', gridTemplateColumns:'1fr auto', width:'100%', gap:8, padding:'9px 7px', border:0, borderTop:'1px solid rgba(148,163,184,.1)', background:detail?.stock?.stock_code===stock.stock_code?'rgba(34,211,238,.08)':'transparent', color:'var(--text-primary)', cursor:'pointer', textAlign:'left'}}>
            <span><b>{stock.stock_name}</b> <small style={{color:'#94a3b8'}}>{stock.stock_code} · {stock.market}</small><small style={{display:'block', color:'var(--text-secondary)', marginTop:3}}>{stock.sector_large || '-'} › {stock.sector_mid || '-'}</small></span>
            <span style={{textAlign:'right'}}><small style={{display:'block'}}>{fmtCap(stock.market_cap)}</small><small style={{color:'#67e8f9'}}>{stock.tag_count} 태그</small></span>
          </button>)}
        </div>

        <DetailPanel detail={detail} />
      </div>
    </>}
  </div>;
}

function Stat({label, value}) {
  return <div style={{padding:'6px 9px', borderRadius:8, background:'rgba(15,23,42,.45)', minWidth:85}}><small style={{display:'block', color:'var(--text-secondary)'}}>{label}</small><b>{value}</b></div>;
}

function DetailPanel({detail}) {
  if (!detail) return <div className="glass-panel" style={{padding:'1rem', minHeight:220, color:'var(--text-secondary)'}}>종목을 선택하면 복수 분류, 출처, 신뢰도, 근거와 최신 제품 매출구성을 함께 표시합니다.</div>;
  const groups = Object.entries(detail.tags.reduce((acc, tag) => { const key = DIMENSION_LABELS[tag.dimension] || tag.dimension; (acc[key] ||= []).push(tag); return acc; }, {}));
  return <div className="glass-panel" style={{padding:'1rem', maxHeight:'68vh', overflowY:'auto'}}>
    <h3 style={{margin:'0 0 3px'}}>{detail.stock.stock_name} <small style={{color:'#94a3b8'}}>{detail.stock.stock_code}</small></h3>
    <div style={{fontSize:'0.7rem', color:'var(--text-secondary)', marginBottom:10}}>{detail.stock.market} · 시총 {fmtCap(detail.stock.market_cap)}</div>
    {detail.source_disagreements.map(item => <div key={item.dimension} style={{fontSize:'0.7rem', color:'#fcd34d', background:'rgba(245,158,11,.1)', padding:'7px', borderRadius:7, marginBottom:7}}>출처 불일치: {item.values.join(' ↔ ')}<br/><span style={{color:'var(--text-secondary)'}}>{item.notice}</span></div>)}
    {groups.map(([label, tags]) => <div key={label} style={{marginBottom:11}}>
      <div style={{fontSize:'0.68rem', fontWeight:800, color:'#94a3b8', marginBottom:4}}>{label}</div>
      {tags.map(tag => <div key={`${tag.source_system}-${tag.node_key}`} style={{padding:'7px 8px', background:'rgba(15,23,42,.38)', borderRadius:7, marginBottom:4}}>
        <div style={{display:'flex', justifyContent:'space-between', gap:7}}><b style={{fontSize:'0.82rem'}}>{tag.name}</b><span style={badgeStyle(tag.status)}>{tag.status === 'verified' ? '검증' : tag.status === 'observed' ? '원천관측' : tag.status === 'inferred' ? '규칙추론' : tag.status}</span></div>
        <div style={{fontSize:'0.65rem', color:'var(--text-secondary)', marginTop:3}}>{SOURCE_LABELS[tag.source_system] || tag.source_system} · 신뢰도 {tag.confidence} · {tag.source_snapshot_date}</div>
        {(tag.evidence?.rationale || tag.evidence?.matched) && <div style={{fontSize:'0.67rem', marginTop:4, color:'#cbd5e1'}}>{tag.evidence.rationale || `일치 근거: ${tag.evidence.matched}`}</div>}
      </div>)}
    </div>)}
    {!!detail.product_mix.length && <div>
      <div style={{fontSize:'0.68rem', fontWeight:800, color:'#94a3b8', margin:'12px 0 4px'}}>DART 제품 매출 구성 ({detail.product_mix[0].year})</div>
      {detail.product_mix.slice(0, 8).map((item, index) => <div key={`${item.product_name}-${index}`} style={{display:'flex', justifyContent:'space-between', gap:8, fontSize:'0.7rem', padding:'4px 0', borderTop:'1px solid rgba(148,163,184,.08)'}}><span>{item.product_name}</span><b>{item.revenue_pct == null ? '-' : `${Number(item.revenue_pct).toFixed(1)}%`}</b></div>)}
    </div>}
  </div>;
}
