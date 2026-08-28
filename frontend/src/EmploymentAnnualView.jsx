import React, { useEffect, useState } from 'react';
import {
  Area,
  CartesianGrid,
  ComposedChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

const EmploymentAnnualView = () => {
  const [annualQ, setAnnualQ] = useState('');
  const [annualResults, setAnnualResults] = useState([]);
  const [annualLoading, setAnnualLoading] = useState(false);
  const [selectedCompany, setSelectedCompany] = useState(null);
  const [years, setYears] = useState('3');
  const [topRows, setTopRows] = useState([]);
  const [topLoading, setTopLoading] = useState(true);
  const [topSort, setTopSort] = useState('latest');

  useEffect(() => {
    setTopLoading(true);
    fetch(`/api/employment-v2/annual-top?limit=200&sort_by=${topSort}`)
      .then(r => r.json())
      .then(d => {
        setTopRows(d.rows || []);
        setTopLoading(false);
      })
      .catch(() => setTopLoading(false));
  }, [topSort]);

  const searchAnnual = async () => {
    if (!annualQ.trim()) return;
    setAnnualLoading(true);
    try {
      const d = await fetch(`/api/employment-v2/annual-trend?q=${encodeURIComponent(annualQ)}`).then(r => r.json());
      setAnnualResults(d.results || []);
      if (d.results?.length === 1) setSelectedCompany(d.results[0]);
      else setSelectedCompany(null);
    } catch {
      setAnnualResults([]);
      setSelectedCompany(null);
    }
    setAnnualLoading(false);
  };

  const filterHistory = (history) => {
    if (!history) return [];
    const cutYear = years === '1' ? '2025' : years === '2' ? '2024' : '2023';
    return history.filter(h => h.ym >= cutYear);
  };

  const fmtWc = (n) => n != null ? `${n.toLocaleString('ko-KR')}명` : '-';
  const diffColor = (v) => v > 0 ? '#f87171' : v < 0 ? '#60a5fa' : 'rgba(255,255,255,0.4)';
  const fmtDiff = (v) => v != null ? `${v > 0 ? '+' : ''}${v.toLocaleString('ko-KR')}` : '-';
  const marketLabel = (m) => m === '유가증권' ? 'KOSPI' : m === '코스닥' ? 'KOSDAQ' : m;
  const marketBadge = (market) => {
    const isKospi = market === '유가증권' || market === 'KOSPI';
    return {
      fontSize: '0.6rem',
      padding: '0.08rem 0.3rem',
      borderRadius: '3px',
      marginRight: '0.3rem',
      background: isKospi ? 'rgba(59,130,246,0.18)' : 'rgba(16,185,129,0.18)',
      color: isKospi ? '#93c5fd' : '#6ee7b7',
      border: `1px solid ${isKospi ? 'rgba(59,130,246,0.3)' : 'rgba(16,185,129,0.3)'}`,
    };
  };

  return (
    <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <div className="glass-panel" style={{ padding: '0.7rem 1.2rem', display: 'flex', gap: '1.5rem', flexWrap: 'wrap', fontSize: '0.78rem', alignItems: 'center' }}>
        <span>기업별 연간 인원 추이 — <strong style={{color:'#34d399'}}>사업보고서</strong> 기준 (2023~2025년 연말)</span>
        <span style={{color:'var(--text-secondary)'}}>직접 고용인원 집계</span>
        <span style={{color:'var(--text-secondary)'}}>482개 상장기업 대상</span>
        <span style={{color:'#f59e0b', marginLeft:'auto'}}>고용보험 피보험자(WLB), 국민연금 월별 변동과 다른 기준의 별도 데이터</span>
      </div>

      <div className="glass-panel" style={{ padding: '1rem 1.4rem' }}>
        <div style={{ display: 'flex', gap: '0.6rem', alignItems: 'center', marginBottom: '1rem', flexWrap: 'wrap' }}>
          <span style={{ fontWeight: 600, color: '#fff', fontSize: '0.9rem' }}>기업별 인원 추이 조회</span>
          <input
            type="text"
            placeholder="기업명 입력 (예: 삼성전자)..."
            value={annualQ}
            onChange={e => setAnnualQ(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && searchAnnual()}
            style={{
              padding: '0.38rem 0.8rem', borderRadius: '6px',
              border: '1px solid var(--glass-border)',
              background: 'rgba(0,0,0,0.2)', color: '#fff', fontSize: '0.85rem', width: '220px'
            }}
          />
          <button onClick={searchAnnual} style={{
            padding: '0.38rem 1rem', borderRadius: '6px', background: '#3b82f6',
            color: '#fff', border: 'none', cursor: 'pointer', fontSize: '0.85rem'
          }}>검색</button>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: '0.3rem' }}>
            {[['1','1년'],['2','2년'],['3','3년']].map(([v,l]) => (
              <button key={v} onClick={() => setYears(v)} style={{
                padding: '0.28rem 0.65rem', borderRadius: '6px', fontSize: '0.78rem', cursor: 'pointer',
                fontWeight: years === v ? 700 : 400,
                background: years === v ? 'rgba(45,212,191,0.2)' : 'transparent',
                color: years === v ? '#2dd4bf' : 'rgba(255,255,255,0.5)',
                border: `1px solid ${years === v ? '#2dd4bf' : 'rgba(255,255,255,0.2)'}`,
              }}>{l}</button>
            ))}
          </div>
        </div>

        {annualLoading && <div style={{ padding: '1.5rem', textAlign: 'center', color: 'var(--text-secondary)' }}>검색 중...</div>}

        {!annualLoading && annualResults.length > 1 && (
          <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap', marginBottom: '0.8rem' }}>
            {annualResults.map(c => (
              <button key={c.stock_code}
                onClick={() => setSelectedCompany(c)}
                style={{
                  padding: '0.25rem 0.7rem', borderRadius: '6px', fontSize: '0.78rem', cursor: 'pointer',
                  background: selectedCompany?.stock_code === c.stock_code ? 'rgba(96,165,250,0.2)' : 'rgba(255,255,255,0.06)',
                  color: selectedCompany?.stock_code === c.stock_code ? '#60a5fa' : 'rgba(255,255,255,0.6)',
                  border: `1px solid ${selectedCompany?.stock_code === c.stock_code ? '#60a5fa' : 'rgba(255,255,255,0.15)'}`,
                }}>
                {c.stock_name}
              </button>
            ))}
          </div>
        )}

        {selectedCompany && (() => {
          const hist = filterHistory(selectedCompany.history);
          if (!hist.length) return <div style={{textAlign:'center',color:'#f59e0b',padding:'1rem'}}>선택 기간에 데이터가 없습니다.</div>;
          return (
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.8rem', marginBottom: '0.8rem' }}>
                <h3 style={{ margin: 0, color: '#fff', fontSize: '0.95rem' }}>
                  {selectedCompany.stock_name} — 연간 고용인원 추이 ({years}년)
                </h3>
                <span style={{ fontSize: '0.72rem', color: 'rgba(255,255,255,0.4)' }}>사업보고서 기준</span>
              </div>
              <div style={{ width: '100%', height: 280 }}>
                <ResponsiveContainer>
                  <ComposedChart data={hist} margin={{ left: 20, right: 20, top: 10 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.08)" />
                    <XAxis dataKey="ym" stroke="rgba(255,255,255,0.4)" tick={{ fontSize: 12 }} />
                    <YAxis stroke="rgba(255,255,255,0.4)" tick={{ fontSize: 11 }}
                      tickFormatter={v => `${(v/10000).toFixed(0)}만`} />
                    <Tooltip
                      contentStyle={{ background: 'rgba(15,23,42,0.92)', border: '1px solid rgba(255,255,255,0.2)', borderRadius: '8px' }}
                      formatter={(v) => [`${v.toLocaleString('ko-KR')}명`, '고용인원']}
                    />
                    <Area type="monotone" dataKey="worker_count"
                      stroke="#60a5fa" strokeWidth={2.5}
                      fill="rgba(96,165,250,0.12)"
                      dot={{ r: 6, fill: '#60a5fa', strokeWidth: 2, stroke: '#fff' }}
                      name="고용인원" />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
              <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap', marginTop: '0.8rem', padding: '0.6rem 0', borderTop: '1px solid rgba(255,255,255,0.06)' }}>
                {hist.map((h, idx) => {
                  const prev = hist[idx - 1];
                  const diff = prev ? h.worker_count - prev.worker_count : null;
                  return (
                    <div key={h.ym} style={{ textAlign: 'center' }}>
                      <div style={{ fontSize: '0.68rem', color: 'rgba(255,255,255,0.4)', marginBottom: '0.2rem' }}>{h.ym}</div>
                      <div style={{ fontSize: '0.92rem', fontWeight: 700, color: '#60a5fa' }}>
                        {h.worker_count.toLocaleString('ko-KR')}명
                      </div>
                      {diff != null && (
                        <div style={{ fontSize: '0.72rem', color: diffColor(diff), marginTop: '0.1rem' }}>
                          {fmtDiff(diff)}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })()}
      </div>

      <div className="glass-panel" style={{ overflow: 'hidden' }}>
        <div style={{ padding: '0.8rem 1.2rem', borderBottom: '1px solid rgba(255,255,255,0.07)', display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
          <span style={{ fontWeight: 600, color: '#fff', fontSize: '0.88rem' }}>전체 기업 연간 인원 현황 (사업보고서 기준, 2025-12)</span>
          <div style={{ marginLeft: 'auto', display: 'flex', gap: '0.3rem' }}>
            {[['latest','최신인원순'],['growth','1년 증가순'],['name','이름순']].map(([k,l]) => (
              <button key={k} onClick={() => setTopSort(k)} style={{
                padding: '0.22rem 0.6rem', borderRadius: '5px', fontSize: '0.75rem', cursor: 'pointer',
                fontWeight: topSort === k ? 700 : 400,
                background: topSort === k ? 'rgba(45,212,191,0.15)' : 'transparent',
                color: topSort === k ? '#2dd4bf' : 'rgba(255,255,255,0.5)',
                border: `1px solid ${topSort === k ? '#2dd4bf' : 'rgba(255,255,255,0.15)'}`,
              }}>{l}</button>
            ))}
          </div>
        </div>
        {topLoading ? (
          <div style={{ padding: '2rem', textAlign: 'center', color: 'rgba(255,255,255,0.4)' }}>로딩 중...</div>
        ) : (
          <div style={{ overflowX: 'auto', overflowY: 'clip' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.81rem' }}>
              <thead>
                <tr>
                  {['#','종목명','섹터','2025년말','2024년말','2023년말','1년 증감','2년 증감'].map((h,i) => (
                    <th key={i} style={{
                      padding: '0.55rem 0.8rem', textAlign: i <= 2 ? 'left' : 'right',
                      color: '#e2e8f0', borderBottom: '2px solid rgba(59,130,246,0.5)',
                      fontWeight: 600, background: 'rgba(30,58,138,0.4)',
                      whiteSpace: 'nowrap', position: 'sticky', top: 0, zIndex: 5,
                    }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {topRows.map((r, i) => (
                  <tr key={r.stock_code}
                    style={{ cursor: 'pointer', transition: 'background 0.12s' }}
                    onClick={() => {
                      setAnnualQ(r.stock_name);
                      setSelectedCompany({
                        stock_code: r.stock_code,
                        stock_name: r.stock_name,
                        history: [
                          { ym: '2023-12', worker_count: r.cnt_2023 },
                          { ym: '2024-12', worker_count: r.cnt_2024 },
                          { ym: '2025-12', worker_count: r.cnt_2025 },
                        ].filter(h => h.worker_count != null)
                      });
                    }}
                    onMouseOver={e => e.currentTarget.style.background = 'rgba(255,255,255,0.04)'}
                    onMouseOut={e => e.currentTarget.style.background = 'transparent'}>
                    <td style={{ padding:'0.45rem 0.8rem', borderBottom:'1px solid rgba(255,255,255,0.04)', textAlign:'center', color:'rgba(255,255,255,0.35)', fontSize:'0.73rem' }}>{i+1}</td>
                    <td style={{ padding:'0.45rem 0.8rem', borderBottom:'1px solid rgba(255,255,255,0.04)', fontWeight:600 }}>
                      {r.market && <span style={marketBadge(r.market)}>{marketLabel(r.market)}</span>}
                      {r.stock_name}
                    </td>
                    <td style={{ padding:'0.45rem 0.8rem', borderBottom:'1px solid rgba(255,255,255,0.04)', color:'rgba(255,255,255,0.4)', fontSize:'0.74rem' }}>{r.sector||'-'}</td>
                    <td style={{ padding:'0.45rem 0.8rem', borderBottom:'1px solid rgba(255,255,255,0.04)', textAlign:'right', fontWeight:700, color:'#34d399' }}>{fmtWc(r.cnt_2025)}</td>
                    <td style={{ padding:'0.45rem 0.8rem', borderBottom:'1px solid rgba(255,255,255,0.04)', textAlign:'right', color:'rgba(255,255,255,0.55)' }}>{fmtWc(r.cnt_2024)}</td>
                    <td style={{ padding:'0.45rem 0.8rem', borderBottom:'1px solid rgba(255,255,255,0.04)', textAlign:'right', color:'rgba(255,255,255,0.4)', fontSize:'0.78rem' }}>{fmtWc(r.cnt_2023)}</td>
                    <td style={{ padding:'0.45rem 0.8rem', borderBottom:'1px solid rgba(255,255,255,0.04)', textAlign:'right', fontWeight:600, color:diffColor(r.diff_1y) }}>{fmtDiff(r.diff_1y)}</td>
                    <td style={{ padding:'0.45rem 0.8rem', borderBottom:'1px solid rgba(255,255,255,0.04)', textAlign:'right', fontSize:'0.78rem', color:diffColor(r.diff_2y) }}>{fmtDiff(r.diff_2y)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div style={{ padding:'0.5rem 1rem', borderTop:'1px solid rgba(255,255,255,0.05)', fontSize:'0.67rem', color:'rgba(255,255,255,0.28)' }}>
          사업보고서 기준 직접 고용인원 · 행 클릭 시 추이 차트 표시
        </div>
      </div>
    </div>
  );
};

export default EmploymentAnnualView;
