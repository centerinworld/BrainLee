import React from 'react';

const SemiconductorView = React.memo(() => {
  const [data, setData]       = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [sortKey, setSortKey] = React.useState('sort_order');
  const [sortDir, setSortDir] = React.useState('asc');
  const [filterLv1, setFilterLv1] = React.useState('ALL');
  const [filterEtf, setFilterEtf] = React.useState(false);
  const [search, setSearch]   = React.useState('');

  React.useEffect(() => {
    setLoading(true);
    fetch('/api/market-radar/semiconductor/valuestream')
      .then(r => r.json())
      .then(d => { setData(d); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  const refDates = data?.ref_dates || [];
  const rows     = data?.rows || [];

  // LV1 카테고리 목록
  const lv1List = React.useMemo(() => {
    const s = new Set(rows.map(r => r.lv1).filter(Boolean));
    return ['ALL', ...Array.from(s)];
  }, [rows]);

  // 정렬 핸들러
  const handleSort = (key) => {
    if (sortKey === key) setSortDir(d => d === 'asc' ? 'desc' : 'asc');
    else { setSortKey(key); setSortDir('asc'); }
  };
  const sortInd = (key) => sortKey === key ? (sortDir === 'asc' ? ' ↑' : ' ↓') : '';

  // 필터 + 정렬
  const sortedRows = React.useMemo(() => {
    let filtered = rows;
    if (filterLv1 !== 'ALL') filtered = filtered.filter(r => r.lv1 === filterLv1);
    if (filterEtf)           filtered = filtered.filter(r => r.etf_flag === 'y');
    if (search.trim())       filtered = filtered.filter(r =>
      (r.company_name||'').includes(search) || (r.lv1||'').includes(search) ||
      (r.main_business||'').includes(search)
    );
    return [...filtered].sort((a, b) => {
      let va = a[sortKey], vb = b[sortKey];
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      const res = typeof va === 'string' ? va.localeCompare(vb) : va - vb;
      return sortDir === 'asc' ? res : -res;
    });
  }, [rows, filterLv1, filterEtf, search, sortKey, sortDir]);

  // 색상 헬퍼
  const chgColor = (v) => v == null ? '#888' : v > 0 ? '#f87171' : v < 0 ? '#60a5fa' : '#888';
  const fmtPct = (v) => v == null ? '-' : (v > 0 ? '+' : '') + v.toFixed(1) + '%';
  const fmtPrc = (v) => v == null ? '-' : v.toLocaleString('ko-KR');
  const fmtMkt = (v) => v == null ? '-' : (v >= 10000 ? (v/10000).toFixed(1)+'조' : v.toLocaleString('ko-KR')+'억');
  const fmtRev = (v) => v == null ? '-' : (v >= 10000 ? (v/10000).toFixed(1)+'조' : v.toLocaleString('ko-KR')+'억');

  const thSt = {
    padding: '0.55rem 0.6rem', textAlign: 'right',
    color: '#e2e8f0', fontWeight: 600, fontSize: '0.74rem',
    background: 'rgba(15,23,42,0.97)',
    borderBottom: '2px solid rgba(59,130,246,0.4)',
    whiteSpace: 'nowrap', cursor: 'pointer', userSelect: 'none',
    position: 'sticky', top: 0, zIndex: 10,
  };
  const tdSt = {
    padding: '0.38rem 0.6rem', fontSize: '0.78rem',
    borderBottom: '1px solid rgba(255,255,255,0.04)',
    color: 'rgba(255,255,255,0.85)',
    whiteSpace: 'nowrap',
  };

  // LV1 그룹 색상
  const LV1_COLORS = {
    '종합': '#6366f1', '제조': '#8b5cf6', '설계': '#a78bfa', '공정설계': '#c084fc',
    '클린룸': '#06b6d4', '전공정 장비': '#0ea5e9', '전공정 원료/소재': '#38bdf8',
    '전공정 부품': '#7dd3fc', 'OSAT': '#f59e0b', '기판': '#fbbf24',
    '후공정 장비': '#34d399', '후공정 소재/부품': '#6ee7b7',
    'EDS Test 부품': '#f87171', '테스트소켓': '#fca5a5',
    '반도체+로봇(이송)': '#fb923c', 'EMS': '#fdba74',
    '반도체 유동': '#a3e635', '기타': '#d1d5db', '확인중': '#6b7280',
  };

  return (
    <div style={{padding: '0 0.2rem'}}>
      {/* 헤더 */}
      <div style={{
        position: 'sticky', top: 0, zIndex: 40,
        background: 'rgba(10,10,22,0.97)', backdropFilter: 'blur(14px)',
        padding: '0.6rem 0.2rem 0.5rem', marginBottom: '0',
        borderBottom: '1px solid rgba(59,130,246,0.18)',
      }}>
        <div style={{display:'flex', alignItems:'center', gap:'0.8rem', flexWrap:'wrap'}}>
          <h2 style={{margin:0, fontSize:'1rem', fontWeight:700, color:'#fff'}}>
            🔬 반도체 밸류스트림 전종목
          </h2>
          {data && (
            <span style={{fontSize:'0.75rem', color:'#60a5fa'}}>
              {sortedRows.length}/{rows.length}종목
            </span>
          )}
          {/* 검색 */}
          <input
            value={search} onChange={e => setSearch(e.target.value)}
            placeholder="기업명 / 업종 검색..."
            style={{
              padding: '0.3rem 0.7rem', borderRadius: '6px',
              background: 'rgba(255,255,255,0.07)', border: '1px solid rgba(255,255,255,0.15)',
              color: '#fff', fontSize: '0.78rem', width: '160px',
            }}
          />
          {/* ETF150 필터 */}
          <button
            onClick={() => setFilterEtf(f => !f)}
            style={{
              padding:'0.25rem 0.65rem', borderRadius:'6px', fontSize:'0.74rem', cursor:'pointer',
              background: filterEtf ? 'rgba(251,191,36,0.15)' : 'rgba(255,255,255,0.06)',
              border: filterEtf ? '1px solid rgba(251,191,36,0.4)' : '1px solid rgba(255,255,255,0.15)',
              color: filterEtf ? '#fbbf24' : 'rgba(255,255,255,0.6)', fontWeight:600,
            }}
          >ETF150</button>
          {/* LV1 필터 */}
          <select
            value={filterLv1} onChange={e => setFilterLv1(e.target.value)}
            style={{
              padding:'0.28rem 0.5rem', borderRadius:'6px', fontSize:'0.74rem', cursor:'pointer',
              background:'rgba(15,23,42,0.9)', border:'1px solid rgba(255,255,255,0.15)',
              color:'#e2e8f0', maxWidth:'160px',
            }}
          >
            {lv1List.map(l => <option key={l} value={l}>{l === 'ALL' ? '전체 카테고리' : l}</option>)}
          </select>
        </div>
      </div>

      {loading ? (
        <div style={{padding:'3rem', textAlign:'center', color:'rgba(255,255,255,0.4)'}}>
          로딩 중...
        </div>
      ) : (
        <div style={{overflowX:'auto', overflowY:'clip'}}>
          <table style={{width:'100%', borderCollapse:'collapse', fontSize:'0.78rem'}}>
            <thead>
              <tr>
                <th style={{...thSt, textAlign:'center', width:'32px'}} onClick={() => handleSort('sort_order')}>#</th>
                <th style={{...thSt, textAlign:'left', minWidth:'90px'}}>카테고리</th>
                <th style={{...thSt, textAlign:'left', minWidth:'100px'}} onClick={() => handleSort('company_name')}>기업명{sortInd('company_name')}</th>
                <th style={{...thSt, textAlign:'left', maxWidth:'200px'}}>주요사업</th>
                <th style={{...thSt, textAlign:'right'}} onClick={() => handleSort('price')}>현재가{sortInd('price')}</th>
                <th style={{...thSt, textAlign:'right'}} onClick={() => handleSort('market_cap')}>시총{sortInd('market_cap')}</th>
                <th style={{...thSt, textAlign:'right'}} onClick={() => handleSort('pbr')}>PBR{sortInd('pbr')}</th>
                <th style={{...thSt, textAlign:'right'}} onClick={() => handleSort('per')}>PER{sortInd('per')}</th>
                <th style={{...thSt, textAlign:'right'}} onClick={() => handleSort('psr')}>PSR{sortInd('psr')}</th>
                {refDates.map(rd => (
                  <React.Fragment key={rd}>
                    <th style={{...thSt, textAlign:'right', fontSize:'0.68rem', color:'#94a3b8'}}>
                      {rd.slice(2,7)}가
                    </th>
                    <th style={{...thSt, textAlign:'right'}}
                        onClick={() => handleSort(`__ref_${rd}`)}>
                      변동{sortInd(`__ref_${rd}`)}
                    </th>
                  </React.Fragment>
                ))}
              </tr>
            </thead>
            <tbody>
              {sortedRows.map((r, idx) => {
                // ref sort key patch
                const extRow = { ...r };
                refDates.forEach(rd => { extRow[`__ref_${rd}`] = r.ref_chgs?.[rd]; });

                return (
                  <tr key={r.stock_code || idx}
                      onMouseOver={e => e.currentTarget.style.background='rgba(255,255,255,0.04)'}
                      onMouseOut={e  => e.currentTarget.style.background='transparent'}>
                    <td style={{...tdSt, textAlign:'center', color:'rgba(255,255,255,0.35)', fontSize:'0.7rem'}}>
                      {r.sort_order}
                    </td>
                    {/* LV1 배지 */}
                    <td style={{...tdSt, textAlign:'left'}}>
                      <span style={{
                        display:'inline-block', padding:'0.1rem 0.4rem', borderRadius:'4px',
                        fontSize:'0.68rem', fontWeight:600, whiteSpace:'nowrap',
                        color: LV1_COLORS[r.lv1] || '#94a3b8',
                        background: `${LV1_COLORS[r.lv1] || '#94a3b8'}18`,
                        border: `1px solid ${LV1_COLORS[r.lv1] || '#94a3b8'}40`,
                      }}>
                        {r.lv1 || '-'}
                      </span>
                      {r.lv2 && <span style={{fontSize:'0.65rem', color:'#64748b', marginLeft:'4px'}}>{r.lv2}</span>}
                    </td>
                    {/* 기업명 (tooltip: 고객·주요업) */}
                    <td style={{...tdSt, textAlign:'left', fontWeight:600}}
                        title={[r.customers && `고객: ${r.customers}`, r.main_business].filter(Boolean).join('\n')}>
                      <div style={{display:'flex', alignItems:'center', gap:'4px'}}>
                        {r.etf_flag === 'y' && (
                          <span style={{fontSize:'0.6rem', color:'#fbbf24', fontWeight:700}}>ETF</span>
                        )}
                        <span style={{cursor: r.main_business ? 'help' : 'default'}}>
                          {r.company_name}
                        </span>
                      </div>
                      {r.main_business && (
                        <div style={{fontSize:'0.65rem', color:'rgba(255,255,255,0.4)', marginTop:'1px',
                          maxWidth:'130px', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>
                          {r.main_business}
                        </div>
                      )}
                    </td>
                    {/* 주요사업 (짧게) */}
                    <td style={{...tdSt, textAlign:'left', color:'rgba(255,255,255,0.45)', maxWidth:'180px',
                      overflow:'hidden', textOverflow:'ellipsis'}}>
                      {r.customers || '-'}
                    </td>
                    {/* 가격/시총/밸류 */}
                    <td style={{...tdSt, textAlign:'right', fontFamily:'monospace', fontWeight:600}}>
                      {fmtPrc(r.price)}
                    </td>
                    <td style={{...tdSt, textAlign:'right', color:'#94a3b8'}}>
                      {fmtMkt(r.market_cap)}
                    </td>
                    <td style={{...tdSt, textAlign:'right'}}>
                      {r.pbr != null ? r.pbr.toFixed(2) : '-'}
                    </td>
                    <td style={{...tdSt, textAlign:'right'}}>
                      {r.per != null ? r.per.toFixed(1) : '-'}
                    </td>
                    <td style={{...tdSt, textAlign:'right'}}>
                      {r.psr != null ? r.psr.toFixed(2) : '-'}
                    </td>
                    {/* 기준일 가격 + 변동률 */}
                    {refDates.map(rd => (
                      <React.Fragment key={rd}>
                        <td style={{...tdSt, textAlign:'right', color:'rgba(255,255,255,0.4)', fontFamily:'monospace', fontSize:'0.72rem'}}>
                          {fmtPrc(r.ref_prices?.[rd])}
                        </td>
                        <td style={{...tdSt, textAlign:'right', fontFamily:'monospace', fontWeight:600,
                          color: chgColor(r.ref_chgs?.[rd])}}>
                          {fmtPct(r.ref_chgs?.[rd])}
                        </td>
                      </React.Fragment>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>

          {/* 기준일 표시 — 테이블 최하단 */}
          <div style={{
            marginTop: '1rem', padding: '0.7rem 1rem',
            background: 'rgba(30,41,59,0.6)', borderRadius: '8px',
            border: '1px solid rgba(255,255,255,0.08)',
            fontSize: '0.74rem', color: 'rgba(255,255,255,0.5)',
            display: 'flex', gap: '1.5rem', flexWrap: 'wrap',
          }}>
            <span style={{fontWeight:600, color:'rgba(255,255,255,0.7)'}}>📅 기준일</span>
            {refDates.map((rd, i) => (
              <span key={rd}>
                <span style={{color:'#94a3b8'}}>기준{i+1}:</span>
                <span style={{color:'#e2e8f0', marginLeft:'4px', fontFamily:'monospace'}}>{rd}</span>
              </span>
            ))}
            <span style={{color:'rgba(255,255,255,0.3)'}}>
              변동률 = (현재가 − 기준일가) / 기준일가 × 100
            </span>
          </div>
        </div>
      )}
    </div>
  );
});

export default SemiconductorView;
