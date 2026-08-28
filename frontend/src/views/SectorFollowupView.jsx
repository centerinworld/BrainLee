import React from 'react';

const SectorFollowupView = React.memo(() => {
  const [posts, setPosts] = React.useState([]);
  const [activePostId, setActivePostId] = React.useState(null);
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState('');
  const [showForm, setShowForm] = React.useState(false);
  // 입력 폼 상태
  const [form, setForm] = React.useState({
    title: '', blog_url: '', post_date: new Date().toISOString().slice(0,10), ai_summary: '',
    stocksRaw: '', // "섹터명:종목코드,종목코드\n섹터명:종목코드" 형식
  });
  const [formMsg, setFormMsg] = React.useState('');
  const [searchResults, setSearchResults] = React.useState([]);
  const [searching, setSearching] = React.useState(false);

  const loadPosts = React.useCallback(async () => {
    try {
      const r = await fetch('/api/sector-define/posts');
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      setPosts(d);
      if (d.length > 0 && !activePostId) setActivePostId(d[0].id);
    } catch (e) { setError('포스트 목록 로드 실패: ' + e.message); }
  }, [activePostId]);

  const loadDetail = React.useCallback(async (postId) => {
    if (!postId) return;
    setLoading(true); setError('');
    try {
      const r = await fetch(`/api/sector-define/post/${postId}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
    } catch (e) { setError('상세 데이터 로드 실패: ' + e.message); }
    finally { setLoading(false); }
  }, []);

  React.useEffect(() => { loadPosts(); }, []);
  React.useEffect(() => { loadDetail(activePostId); }, [activePostId]);

  // 종목 코드 검색
  const searchStock = React.useCallback(async (q) => {
    if (!q || q.length < 1) { setSearchResults([]); return; }
    setSearching(true);
    try {
      const r = await fetch(`/api/search?q=${encodeURIComponent(q)}&limit=5`);
      const d = await r.json();
      setSearchResults(d.results || d || []);
    } catch { setSearchResults([]); }
    finally { setSearching(false); }
  }, []);

  // 폼 제출 (수동 입력)
  const submitForm = async () => {
    if (!form.title) { setFormMsg('제목을 입력하세요'); return; }
    setFormMsg('저장 중...');
    // stocksRaw 파싱: "섹터명:종목코드1,종목코드2\n섹터명2:종목코드3"
    const stocks = [];
    (form.stocksRaw || '').split('\n').forEach(line => {
      const [cat, codesStr] = line.split(':');
      if (!cat || !codesStr) return;
      codesStr.split(',').forEach(code => {
        const c = code.trim();
        if (c) stocks.push({ category: cat.trim(), stock_code: c });
      });
    });
    try {
      const r = await fetch('/api/sector-define/post', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ ...form, stocks }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const d = await r.json();
      setFormMsg(`저장 완료 (ID: ${d.id}, 종목 ${stocks.length}개)`);
      setForm({ title:'', blog_url:'', post_date: new Date().toISOString().slice(0,10), ai_summary:'', stocksRaw:'' });
      setShowForm(false);
      await loadPosts();
    } catch(e) { setFormMsg('저장 실패: ' + e.message); }
  };

  const fmtPct = (v) => {
    if (v == null) return <span style={{color:'var(--text-secondary)'}}>-</span>;
    const sign = v > 0 ? '+' : '';
    const color = v > 0 ? '#ef4444' : v < 0 ? '#3b82f6' : 'var(--text-secondary)';
    return <span style={{color, fontWeight:600}}>{sign}{v.toFixed(1)}%</span>;
  };
  const fmtPrice = (v) => v == null ? '-' : Number(v).toLocaleString('ko-KR');
  const fmtMktCap2 = (v) => {
    if (!v) return '-';
    const n = Number(v);
    if (n >= 1e12) return `${(n/1e12).toFixed(1)}조`;
    if (n >= 1e8)  return `${Math.round(n/1e8).toLocaleString()}억`;
    return Math.round(n/1e4).toLocaleString() + '만';
  };

  const thSt = { padding:'0.45rem 0.6rem', fontSize:'0.72rem', color:'var(--text-secondary)', fontWeight:600, whiteSpace:'nowrap', background:'rgba(0,0,0,0.25)', borderBottom:'1px solid var(--glass-border)' };
  const tdSt = { padding:'0.4rem 0.6rem', fontSize:'0.8rem', borderBottom:'1px solid rgba(255,255,255,0.04)' };
  const inputSt = { background:'rgba(255,255,255,0.05)', border:'1px solid var(--glass-border)', color:'var(--text-primary)', borderRadius:'6px', padding:'0.4rem 0.6rem', fontSize:'0.82rem', width:'100%' };

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
        <h2 style={{margin:0, fontSize:'1.05rem', fontWeight:700}}>🎯 Hot 섹터 팔로우업</h2>
        <span style={{fontSize:'0.78rem', color:'var(--text-secondary)'}}>핫한 섹터 및 관련 종목 추적</span>
        <div style={{marginLeft:'auto', display:'flex', gap:'0.4rem'}}>
          <button onClick={() => setShowForm(v => !v)}
            style={{padding:'0.25rem 0.7rem', fontSize:'0.75rem', borderRadius:'5px', cursor:'pointer',
              background: showForm ? 'rgba(45,212,191,0.2)' : 'rgba(255,255,255,0.05)',
              border:'1px solid var(--accent-mint)', color:'var(--accent-mint)'}}>
            {showForm ? '✕ 닫기' : '+ 직접 입력'}
          </button>
          <button onClick={() => fetch('/api/sector-define/parse', {method:'POST'}).then(()=>loadPosts())}
            style={{padding:'0.25rem 0.7rem', fontSize:'0.75rem', borderRadius:'5px', cursor:'pointer', background:'rgba(255,255,255,0.05)', border:'1px solid var(--glass-border)', color:'var(--text-secondary)'}}>
            블로그 자동파싱
          </button>
        </div>
      </div>

      {/* 수동 입력 폼 */}
      {showForm && (
        <div className="glass-panel" style={{padding:'1.2rem', marginBottom:'1rem'}}>
          <div style={{fontWeight:700, fontSize:'0.88rem', marginBottom:'0.8rem', color:'var(--accent-mint)'}}>📝 섹터 분석 직접 입력</div>
          <div style={{display:'grid', gridTemplateColumns:'1fr 1fr', gap:'0.6rem', marginBottom:'0.6rem'}}>
            <div>
              <label style={{fontSize:'0.72rem', color:'var(--text-secondary)'}}>제목 *</label>
              <input style={inputSt} value={form.title} onChange={e=>setForm(f=>({...f, title:e.target.value}))} placeholder="예: 2025년 주목할 반도체 섹터" />
            </div>
            <div>
              <label style={{fontSize:'0.72rem', color:'var(--text-secondary)'}}>날짜</label>
              <input style={inputSt} type="date" value={form.post_date} onChange={e=>setForm(f=>({...f, post_date:e.target.value}))} />
            </div>
          </div>
          <div style={{marginBottom:'0.6rem'}}>
            <label style={{fontSize:'0.72rem', color:'var(--text-secondary)'}}>블로그 URL</label>
            <input style={inputSt} value={form.blog_url} onChange={e=>setForm(f=>({...f, blog_url:e.target.value}))} placeholder="https://blog.naver.com/..." />
          </div>
          <div style={{marginBottom:'0.6rem'}}>
            <label style={{fontSize:'0.72rem', color:'var(--text-secondary)'}}>분석 요약</label>
            <textarea style={{...inputSt, minHeight:'60px', resize:'vertical'}} value={form.ai_summary} onChange={e=>setForm(f=>({...f, ai_summary:e.target.value}))} placeholder="섹터 분석 내용을 간략히 입력하세요" />
          </div>
          <div style={{marginBottom:'0.8rem'}}>
            <label style={{fontSize:'0.72rem', color:'var(--text-secondary)'}}>종목 입력 (줄바꿈으로 섹터 구분)</label>
            <div style={{fontSize:'0.68rem', color:'rgba(255,255,255,0.35)', marginBottom:'0.3rem'}}>형식: <code style={{color:'#34d399'}}>섹터명:종목코드1,종목코드2</code> — 예: <code style={{color:'#34d399'}}>메모리:005930,000660</code></div>
            <textarea style={{...inputSt, minHeight:'100px', resize:'vertical', fontFamily:'monospace', fontSize:'0.78rem'}}
              value={form.stocksRaw}
              onChange={e=>setForm(f=>({...f, stocksRaw:e.target.value}))}
              placeholder={'메모리:005930,000660\n파운드리/IDM:000990\n설계:080220,389020'} />
          </div>
          <div style={{display:'flex', gap:'0.5rem', alignItems:'center'}}>
            <button onClick={submitForm}
              style={{padding:'0.4rem 1.2rem', borderRadius:'6px', cursor:'pointer', fontWeight:700, fontSize:'0.82rem', background:'var(--accent-mint)', border:'none', color:'#000'}}>
              저장
            </button>
            {formMsg && <span style={{fontSize:'0.78rem', color: formMsg.includes('완료') ? '#34d399' : '#f87171'}}>{formMsg}</span>}
          </div>
        </div>
      )}

      {posts.length === 0 && !error && !showForm && (
        <div className="glass-panel" style={{padding:'1.5rem', textAlign:'center', color:'var(--text-secondary)'}}>
          <p style={{fontWeight:700, color:'var(--text-primary)', marginBottom:'0.5rem'}}>📭 등록된 섹터 분석이 없습니다</p>
          <p style={{fontSize:'0.82rem', marginBottom:'1rem'}}>"+ 직접 입력"으로 섹터와 종목을 등록하세요.</p>
          <button onClick={() => setShowForm(true)}
            style={{padding:'0.4rem 1rem', borderRadius:'6px', cursor:'pointer', fontWeight:600, fontSize:'0.82rem', background:'rgba(45,212,191,0.15)', border:'1px solid var(--accent-mint)', color:'var(--accent-mint)'}}>
            + 직접 입력 시작
          </button>
        </div>
      )}
      {posts.length > 0 && (
        <div style={{display:'flex', gap:'0.4rem', flexWrap:'wrap', marginBottom:'1.2rem'}}>
          {posts.map(p => {
            /* 핵심 키워드 추출: 조사/괄호 제거 후 14자 이내 */
            const extractKey = (title) => {
              let t = title
                .replace(/\s*\([^)]*\)/g, '')
                .replace(/\s*\[[^\]]*\]/g, '')
                .replace(/\s*—\s*로컬.*$/g, '')
                .replace(/\s*—.*$/g, '')
                .trim();
              const m = t.match(/^(.+?)\s+(?:의\s|은\s|는\s|이\s)/);
              if (m) t = m[1].trim();
              return t.length > 14 ? t.slice(0, 14) + '…' : t;
            };
            const shortTitle = extractKey(p.title);
            return (
              <button key={p.id} onClick={() => setActivePostId(p.id)} title={`${p.post_date?.slice(0,10)} ${p.title}`} style={{
                padding:'0.35rem 0.8rem', borderRadius:'20px', fontSize:'0.78rem', fontWeight:600,
                cursor:'pointer', whiteSpace:'nowrap',
                border: activePostId === p.id ? '1px solid var(--accent-mint)' : '1px solid var(--glass-border)',
                background: activePostId === p.id ? 'rgba(45,212,191,0.15)' : 'transparent',
                color: activePostId === p.id ? 'var(--accent-mint)' : 'var(--text-secondary)',
              }}>{shortTitle}</button>
            );
          })}
        </div>
      )}
      {error && <div style={{color:'#f87171', padding:'1rem'}}>{error}</div>}
      {loading && <div style={{color:'var(--text-secondary)', padding:'2rem', textAlign:'center'}}>로딩 중...</div>}
      {!loading && data && (
        <div className="fade-in">
          <div className="glass-panel" style={{marginBottom:'1.5rem', padding:'1rem', borderLeft:'4px solid var(--accent-mint)'}}>
            <div style={{fontWeight:700, fontSize:'0.9rem', marginBottom:'0.5rem', color:'var(--accent-mint)'}}>🤖 AI 분석 요약</div>
            <div style={{fontSize:'0.85rem', lineHeight:1.6, whiteSpace:'pre-wrap'}}>{data.ai_summary || '요약 없음'}</div>
            <div style={{marginTop:'0.8rem', fontSize:'0.75rem', color:'var(--text-secondary)'}}>
              원문: <a href={data.blog_url} target="_blank" rel="noreferrer" style={{color:'var(--accent-mint)'}}>{data.blog_url}</a>
            </div>
          </div>
          {stocks.length === 0 ? (
            <div className="glass-panel" style={{padding:'1.5rem', textAlign:'center', color:'var(--text-secondary)'}}>
              <p style={{fontWeight:700, color:'rgba(255,255,255,0.6)', marginBottom:'0.5rem'}}>📋 등록된 종목이 없습니다</p>
              <p style={{fontSize:'0.8rem', marginBottom:'0.8rem'}}>
                "블로그 자동파싱" 버튼으로 AI가 종목을 자동 추출하거나,<br/>
                "+ 직접 입력"으로 수동 등록할 수 있습니다.
              </p>
              <p style={{fontSize:'0.72rem', color:'rgba(255,200,100,0.7)'}}>
                ⚠️ 자동파싱은 OpenAI API Key 설정이 필요합니다 (.env → OPENAI_API_KEY)
              </p>
            </div>
          ) : (
          <div className="glass-panel" style={{overflow:'clip', padding:'0'}}>
            <table style={{width:'100%', borderCollapse:'collapse'}}>
              <thead>
                <tr>
                  <th style={{...thSt, textAlign:'left'}}>섹터</th>
                  <th style={{...thSt, textAlign:'left'}}>종목명</th>
                  <th style={{...thSt, textAlign:'right'}}>현재가</th>
                  <th style={{...thSt, textAlign:'right'}}>시가총액</th>
                  <th style={{...thSt, textAlign:'right'}}>PBR</th>
                  <th style={{...thSt, textAlign:'right'}}>PER</th>
                  <th style={{...thSt, textAlign:'right', background:'rgba(45,212,191,0.05)'}}>기준가</th>
                  <th style={{...thSt, textAlign:'right', background:'rgba(45,212,191,0.05)'}}>기준대비</th>
                </tr>
              </thead>
              <tbody>
                {stocks.map((s, i) => {
                  const rowSpan = catSpans[i];
                  return (
                    <tr key={i} onMouseOver={e=>e.currentTarget.style.background='rgba(255,255,255,0.03)'}
                        onMouseOut={e=>e.currentTarget.style.background='transparent'}>
                      {rowSpan != null && (
                        <td rowSpan={rowSpan} style={{...tdSt, fontWeight:700, color:'var(--accent-mint)', verticalAlign:'middle', background:'rgba(45,212,191,0.02)', borderRight:'1px solid rgba(255,255,255,0.06)'}}>
                          {s.category}
                        </td>
                      )}
                      <td style={{...tdSt, fontWeight:600}}>{s.stock_name} {s.stock_code && <span style={{fontSize:'0.7rem', color:'var(--text-secondary)', fontWeight:400}}>({s.stock_code})</span>}</td>
                      <td style={{...tdSt, textAlign:'right', fontWeight:700}}>{fmtPrice(s.price)}</td>
                      <td style={{...tdSt, textAlign:'right', fontSize:'0.75rem', color:'var(--text-secondary)'}}>{fmtMktCap2(s.market_cap)}</td>
                      <td style={{...tdSt, textAlign:'right', fontSize:'0.75rem', color:'var(--text-secondary)'}}>{s.pbr?.toFixed(2) || '-'}</td>
                      <td style={{...tdSt, textAlign:'right', fontSize:'0.75rem', color:'var(--text-secondary)'}}>{s.per?.toFixed(2) || '-'}</td>
                      <td style={{...tdSt, textAlign:'right', fontSize:'0.8rem', background:'rgba(255,255,255,0.01)'}}>{fmtPrice(s.ref_price)}</td>
                      <td style={{...tdSt, textAlign:'right', background:'rgba(255,255,255,0.01)'}}>{fmtPct(s.ref_chg_pct)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          )}
        </div>
      )}
    </div>
  );
});

export default SectorFollowupView;
