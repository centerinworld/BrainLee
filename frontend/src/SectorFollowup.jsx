import React from 'react';

const SectorFollowupView = React.memo(({ API }) => {
  const [posts, setPosts] = React.useState([]);
  const [activePostId, setActivePostId] = React.useState(null);
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState('');

  const loadPosts = React.useCallback(async () => {
    try {
      const r = await fetch(API('/api/sector-define/posts'));
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      setPosts(d);
      if (d.length > 0 && !activePostId) {
        setActivePostId(d[0].id);
      }
    } catch (e) { setError('포스트 목록 로드 실패: ' + e.message); }
  }, [API, activePostId]);

  const loadDetail = React.useCallback(async (postId) => {
    if (!postId) return;
    setLoading(true); setError('');
    try {
      const r = await fetch(API(`/api/sector-define/post/${postId}`));
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
    } catch (e) { setError('상세 데이터 로드 실패: ' + e.message); }
    finally { setLoading(false); }
  }, [API]);

  React.useEffect(() => { loadPosts(); }, [loadPosts]);
  React.useEffect(() => { loadDetail(activePostId); }, [activePostId, loadDetail]);

  const fmtPct = (v) => {
    if (v == null) return <span style={{color:'var(--text-secondary)'}}>-</span>;
    const sign = v > 0 ? '+' : '';
    const color = v > 0 ? '#ef4444' : v < 0 ? '#3b82f6' : 'var(--text-secondary)';
    return <span style={{color, fontWeight:600}}>{sign}{v.toFixed(1)}%</span>;
  };

  const fmtPrice = (v) => {
    if (v == null) return '-';
    return Number(v).toLocaleString('ko-KR');
  };

  const fmtMktCap = (v) => {
    if (v == null || v === 0) return '-';
    const n = Number(v);
    if (n >= 1e12) return `${(n/1e12).toFixed(2)}조`;
    if (n >= 1e8)  return `${Math.round(n/1e8).toLocaleString()}억`;
    return Math.round(n/1e4).toLocaleString() + '만';
  };

  const thStyle = { padding:'0.45rem 0.6rem', fontSize:'0.72rem', color:'var(--text-secondary)',
                    fontWeight:600, whiteSpace:'nowrap', background:'rgba(0,0,0,0.25)',
                    borderBottom:'1px solid var(--glass-border)' };
  const tdStyle = { padding:'0.4rem 0.6rem', fontSize:'0.8rem', borderBottom:'1px solid rgba(255,255,255,0.04)' };

  const PriceCell = ({price, chg, bold}) => (
    <td style={{...tdStyle, textAlign:'right', verticalAlign:'middle'}}>
      {price != null ? (
        <div style={{display:'flex', flexDirection:'column', alignItems:'flex-end', gap:'1px'}}>
          <span style={{fontWeight: bold ? 700 : 500, fontSize:'0.8rem'}}>{fmtPrice(price)}</span>
          {chg != null
            ? <span style={{fontSize:'0.7rem', fontWeight:600,
                color: chg > 0 ? '#ef4444' : chg < 0 ? '#3b82f6' : 'var(--text-secondary)'}}>
                {chg > 0 ? '+' : ''}{chg.toFixed(1)}%
              </span>
            : <span style={{fontSize:'0.7rem', color:'var(--text-secondary)'}}>-</span>}
        </div>
      ) : <span style={{color:'var(--text-secondary)'}}>-</span>}
    </td>
  );

  const stocks = data?.stocks || [];
  const catSpans = {};
  stocks.forEach((s, i) => {
    const cat = s.category || '';
    if (i === 0 || stocks[i-1].category !== cat) {
      let span = 1;
      for (let j = i+1; j < stocks.length && stocks[j].category === cat; j++) span++;
      catSpans[i] = span;
    }
  });

  return (
    <div style={{padding:'0 0.5rem'}}>
      <div style={{display:'flex', alignItems:'center', gap:'0.8rem', marginBottom:'1rem', flexWrap:'wrap'}}>
        <h2 style={{margin:0, fontSize:'1.05rem', fontWeight:700}}>🎯 섹터 팔로우업</h2>
        <span style={{fontSize:'0.78rem', color:'var(--text-secondary)'}}>
          블로그 "돈의흐름 팔로잉" 분석 — 핫한 섹터 및 관련 종목 추적
        </span>
        <button onClick={() => fetch(API('/api/sector-define/parse'), {method:'POST'}).then(()=>loadPosts())} 
          style={{marginLeft:'auto', padding:'0.2rem 0.6rem', fontSize:'0.7rem', borderRadius:'4px', cursor:'pointer', background:'rgba(45,212,191,0.1)', border:'1px solid var(--accent-mint)', color:'var(--accent-mint)'}}>
          즉시 업데이트
        </button>
      </div>

      <div style={{display:'flex', gap:'0.4rem', overflowX:'auto', marginBottom:'1.2rem', paddingBottom:'0.5rem', scrollbarWidth:'thin'}}>
        {posts.map(p => (
          <button key={p.id} onClick={() => setActivePostId(p.id)} style={{
            padding:'0.35rem 0.8rem', borderRadius:'20px', fontSize:'0.78rem', fontWeight:600,
            cursor:'pointer', transition:'all 0.15s', whiteSpace:'nowrap',
            border: activePostId === p.id ? '1px solid var(--accent-mint)' : '1px solid var(--glass-border)',
            background: activePostId === p.id ? 'rgba(45,212,191,0.15)' : 'transparent',
            color: activePostId === p.id ? 'var(--accent-mint)' : 'var(--text-secondary)',
          }}>
            {p.title}
          </button>
        ))}
      </div>

      {loading && <div style={{color:'var(--text-secondary)', padding:'2rem', textAlign:'center'}}>로딩 중...</div>}
      {error   && <div style={{color:'#f87171', padding:'1rem'}}>{error}</div>}

      {!loading && data && (
        <div className="fade-in">
          <div className="glass-panel" style={{marginBottom:'1.5rem', padding:'1rem', borderLeft:'4px solid var(--accent-mint)'}}>
            <div style={{fontWeight:700, fontSize:'0.9rem', marginBottom:'0.5rem', color:'var(--accent-mint)'}}>🤖 AI 분석 요약</div>
            <div style={{fontSize:'0.85rem', lineHeight:1.6, color:'var(--text-primary)', whiteSpace:'pre-wrap'}}>
              {data.ai_summary}
            </div>
            <div style={{marginTop:'0.8rem', fontSize:'0.75rem', color:'var(--text-secondary)'}}>
              원문: <a href={data.blog_url} target="_blank" rel="noreferrer" style={{color:'var(--accent-mint)'}}>{data.blog_url}</a>
            </div>
          </div>

          <div className="glass-panel" style={{overflow:'auto', padding:'0'}}>
            <table style={{width:'100%', borderCollapse:'collapse'}}>
              <thead>
                <tr>
                  <th style={{...thStyle, textAlign:'left'}}>섹터 (Level 1)</th>
                  <th style={{...thStyle, textAlign:'left'}}>종목명 (Level 2)</th>
                  <th style={{...thStyle, textAlign:'right'}}>현재가</th>
                  <th style={{...thStyle, textAlign:'right'}}>시가총액</th>
                  <th style={{...thStyle, textAlign:'right'}}>PBR</th>
                  <th style={{...thStyle, textAlign:'right'}}>PER</th>
                  <th style={{...thStyle, textAlign:'right', background:'rgba(45,212,191,0.05)'}}>기준 주가</th>
                  <th style={{...thStyle, textAlign:'right', background:'rgba(45,212,191,0.05)'}}>기준 대비 변동</th>
                </tr>
              </thead>
              <tbody>
                {stocks.map((s, i) => {
                  const rowSpan = catSpans[i];
                  return (
                    <tr key={i} style={{transition:'background 0.1s'}}
                      onMouseOver={e=>e.currentTarget.style.background='rgba(255,255,255,0.03)'}
                      onMouseOut={e=>e.currentTarget.style.background='transparent'}>
                      
                      {rowSpan != null && (
                        <td rowSpan={rowSpan} style={{...tdStyle, fontWeight:700, color:'var(--accent-mint)', verticalAlign:'middle', background:'rgba(45,212,191,0.02)', borderRight:'1px solid rgba(255,255,255,0.06)'}}>
                          {s.category}
                        </td>
                      )}
                      
                      <td style={{...tdStyle, fontWeight:600}}>{s.stock_name} {s.stock_code && <span style={{fontSize:'0.7rem', color:'var(--text-secondary)', fontWeight:400}}>({s.stock_code})</span>}</td>
                      <PriceCell price={s.price} chg={s.chg_pct} bold={true} />
                      <td style={{...tdStyle, textAlign:'right', fontSize:'0.75rem', color:'var(--text-secondary)'}}>{fmtMktCap(s.market_cap)}</td>
                      <td style={{...tdStyle, textAlign:'right', fontSize:'0.75rem', color:'var(--text-secondary)'}}>{s.pbr?.toFixed(2) || '-'}</td>
                      <td style={{...tdStyle, textAlign:'right', fontSize:'0.75rem', color:'var(--text-secondary)'}}>{s.per?.toFixed(2) || '-'}</td>
                      <td style={{...tdStyle, textAlign:'right', fontSize:'0.8rem', background:'rgba(255,255,255,0.01)'}}>{fmtPrice(s.ref_price)}</td>
                      <td style={{...tdStyle, textAlign:'right', background:'rgba(255,255,255,0.01)'}}>{fmtPct(s.ref_chg_pct)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
});

export default SectorFollowupView;
