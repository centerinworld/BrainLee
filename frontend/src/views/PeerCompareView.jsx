/**
 * PeerCompareView — 동종기업(유사기업) 비교
 * 2~6개 종목을 골라 자산/매출/밸류에이션/매출구성비를 나란히 비교하고
 * peer 대비 상대적 저평가/고평가를 간단히 판정한다.
 *
 * API: GET /api/peer-compare/compare?codes=290550,094970,054040
 */
import React, { useState, useCallback, useMemo } from 'react';
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend, BarChart, Bar, XAxis, YAxis, CartesianGrid } from 'recharts';
import { API } from '../utils';

const PRESET_GROUPS = [
  { label: 'PCB/전자부품 3사', codes: [
    { code: '290550', name: '디케이티' },
    { code: '094970', name: '제이엠티' },
    { code: '054040', name: '한국컴퓨터' },
  ] },
];

const PALETTE = ['#6366f1', '#22c55e', '#f59e0b', '#ef4444', '#06b6d4', '#a78bfa', '#f472b6', '#84cc16', '#94a3b8'];

const card = (style = {}) => ({
  background: 'rgba(30,41,59,0.8)',
  border: '1px solid rgba(51,65,85,0.6)',
  borderRadius: '0.75rem',
  padding: '1rem',
  ...style,
});

const fmtEok = (v) => (v === null || v === undefined) ? '-' : `${Math.round(Number(v)).toLocaleString()}억`;
const fmtNum = (v, digits = 2) => (v === null || v === undefined) ? '-' : Number(v).toFixed(digits);
const fmtPrice = (v) => (v === null || v === undefined) ? '-' : `${Math.round(Number(v)).toLocaleString()}원`;

function verdictColor(verdict) {
  if (!verdict) return '#64748b';
  if (verdict.includes('저평가')) return '#4ade80';
  if (verdict.includes('고평가')) return '#f87171';
  return '#fbbf24';
}

export default function PeerCompareView() {
  const [selected, setSelected] = useState([]); // [{code, name}]
  const [query, setQuery] = useState('');
  const [searchResults, setSearchResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleSearch = useCallback(async (q) => {
    setQuery(q);
    if (q.trim().length < 1) { setSearchResults([]); return; }
    setSearching(true);
    try {
      const r = await fetch(API(`/api/search?q=${encodeURIComponent(q.trim())}`));
      if (r.ok) {
        const d = await r.json();
        setSearchResults(Array.isArray(d) ? d.slice(0, 8) : []);
      }
    } catch (e) { /* noop */ }
    setSearching(false);
  }, []);

  const addStock = (code, name) => {
    if (selected.some(s => s.code === code)) return;
    if (selected.length >= 6) return;
    setSelected(prev => [...prev, { code, name }]);
    setQuery('');
    setSearchResults([]);
  };

  const removeStock = (code) => {
    setSelected(prev => prev.filter(s => s.code !== code));
  };

  const loadPreset = (group) => {
    setSelected(group.codes);
    setData(null);
    setError(null);
  };

  const runCompare = useCallback(async () => {
    if (selected.length < 2) { setError('종목을 2개 이상 선택해주세요.'); return; }
    setLoading(true);
    setError(null);
    try {
      const codes = selected.map(s => s.code).join(',');
      const r = await fetch(API(`/api/peer-compare/compare?codes=${codes}`));
      const d = await r.json();
      if (!r.ok) {
        setError(d.detail || '비교 데이터를 불러오지 못했습니다.');
        setData(null);
      } else {
        setData(d);
      }
    } catch (e) {
      setError('요청 실패: ' + e.message);
    }
    setLoading(false);
  }, [selected]);

  const companies = data?.companies || [];

  const metricRows = useMemo(() => ([
    { key: 'market_cap_억', label: '시가총액', fmt: fmtEok },
    { key: 'revenue', label: '매출액(최근연도)', fmt: fmtEok, pick: c => c.latest_annual?.revenue_억 },
    { key: 'operating_profit', label: '영업이익(최근연도)', fmt: fmtEok, pick: c => c.latest_annual?.operating_profit_억 },
    { key: 'net_income', label: '순이익(최근연도)', fmt: fmtEok, pick: c => c.latest_annual?.net_income_억 },
    { key: 'total_assets', label: '자산총계', fmt: fmtEok, pick: c => c.latest_annual?.total_assets_억 },
    { key: 'total_equity', label: '자본총계', fmt: fmtEok, pick: c => c.latest_annual?.total_equity_억 },
    { key: 'current_price', label: '현재가', fmt: fmtPrice },
    { key: 'per', label: 'PER', fmt: v => fmtNum(v, 2) },
    { key: 'pbr', label: 'PBR', fmt: v => fmtNum(v, 2) },
    { key: 'roe', label: 'ROE(%)', fmt: v => fmtNum(v, 1) },
    { key: 'roa', label: 'ROA(%)', fmt: v => fmtNum(v, 1) },
  ]), []);

  // 최근 5개년 매출 추이를 company별 병렬 막대로 그리기 위한 데이터 병합
  const revenueTrendData = useMemo(() => {
    if (!companies.length) return [];
    const yearSet = new Set();
    companies.forEach(c => (c.financial_trend || []).forEach(t => yearSet.add(t.year)));
    const years = Array.from(yearSet).sort();
    return years.map(year => {
      const row = { year: String(year) };
      companies.forEach(c => {
        const t = (c.financial_trend || []).find(x => x.year === year);
        row[c.stock_name] = t ? t.revenue_억 : null;
      });
      return row;
    });
  }, [companies]);

  return (
    <div style={{ maxWidth: 1280, margin: '0 auto', padding: '0 1rem 2rem' }}>
      <div style={{ marginBottom: '1.2rem' }}>
        <h2 style={{ color: '#e2e8f0', margin: 0, fontSize: '1.2rem', fontWeight: 700 }}>🏢 동종기업 비교</h2>
        <p style={{ color: '#64748b', fontSize: '0.82rem', margin: '0.25rem 0 0' }}>
          비슷한 사업을 하는 기업들을 나란히 놓고 자산·매출·밸류에이션·매출구성비를 비교합니다.
        </p>
      </div>

      {/* 종목 선택 */}
      <div style={card({ marginBottom: '1rem' })}>
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '0.75rem' }}>
          {PRESET_GROUPS.map((g, i) => (
            <button key={i} onClick={() => loadPreset(g)}
              style={{ background: 'rgba(99,102,241,0.15)', border: '1px solid rgba(99,102,241,0.35)', borderRadius: '0.5rem', padding: '0.35rem 0.8rem', color: '#a5b4fc', cursor: 'pointer', fontSize: '0.78rem' }}>
              예시: {g.label}
            </button>
          ))}
        </div>

        <div style={{ position: 'relative', marginBottom: '0.75rem' }}>
          <input
            value={query}
            onChange={e => handleSearch(e.target.value)}
            placeholder="종목명 또는 코드 검색 (최대 6개)"
            style={{ width: '100%', background: 'rgba(15,23,42,0.6)', border: '1px solid rgba(51,65,85,0.6)', borderRadius: '0.5rem', padding: '0.55rem 0.8rem', color: '#e2e8f0', fontSize: '0.85rem', boxSizing: 'border-box' }}
          />
          {searchResults.length > 0 && (
            <div style={{ position: 'absolute', top: '110%', left: 0, right: 0, background: '#1e293b', border: '1px solid rgba(51,65,85,0.8)', borderRadius: '0.5rem', zIndex: 10, maxHeight: 260, overflowY: 'auto' }}>
              {searchResults.map(r => (
                <div key={r.code} onClick={() => addStock(r.code, r.name)}
                  style={{ padding: '0.5rem 0.8rem', cursor: 'pointer', color: '#e2e8f0', fontSize: '0.82rem', borderBottom: '1px solid rgba(51,65,85,0.4)' }}
                  onMouseEnter={e => e.currentTarget.style.background = 'rgba(99,102,241,0.15)'}
                  onMouseLeave={e => e.currentTarget.style.background = 'transparent'}>
                  {r.name} <span style={{ color: '#64748b' }}>({r.code})</span>
                </div>
              ))}
            </div>
          )}
          {searching && <div style={{ position: 'absolute', right: '0.8rem', top: '0.6rem', color: '#64748b', fontSize: '0.75rem' }}>검색중…</div>}
        </div>

        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
          {selected.map(s => (
            <div key={s.code} style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', background: 'rgba(51,65,85,0.5)', borderRadius: '999px', padding: '0.3rem 0.4rem 0.3rem 0.8rem', fontSize: '0.8rem', color: '#e2e8f0' }}>
              {s.name} <span style={{ color: '#64748b' }}>({s.code})</span>
              <button onClick={() => removeStock(s.code)}
                style={{ background: 'rgba(239,68,68,0.2)', border: 'none', borderRadius: '999px', width: 20, height: 20, color: '#f87171', cursor: 'pointer', fontSize: '0.75rem', lineHeight: '20px' }}>×</button>
            </div>
          ))}
          {selected.length === 0 && <span style={{ color: '#475569', fontSize: '0.8rem' }}>비교할 종목을 검색해서 추가하세요</span>}
        </div>

        <div style={{ marginTop: '0.9rem', display: 'flex', gap: '0.6rem', alignItems: 'center' }}>
          <button onClick={runCompare} disabled={loading || selected.length < 2}
            style={{ background: selected.length < 2 ? 'rgba(100,116,139,0.2)' : 'rgba(34,197,94,0.2)', border: `1px solid ${selected.length < 2 ? 'rgba(100,116,139,0.4)' : '#22c55e'}`, borderRadius: '0.5rem', padding: '0.5rem 1.2rem', color: selected.length < 2 ? '#64748b' : '#4ade80', cursor: selected.length < 2 ? 'not-allowed' : 'pointer', fontSize: '0.85rem', fontWeight: 700 }}>
            {loading ? '비교 중…' : '비교하기'}
          </button>
          {error && <span style={{ color: '#f87171', fontSize: '0.8rem' }}>{error}</span>}
        </div>
      </div>

      {companies.length > 0 && (
        <>
          {/* 요약 카드 */}
          <div style={{ display: 'grid', gridTemplateColumns: `repeat(${companies.length}, 1fr)`, gap: '0.75rem', marginBottom: '1rem' }}>
            {companies.map((c, i) => (
              <div key={c.stock_code} style={card()}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <div style={{ color: '#e2e8f0', fontWeight: 800, fontSize: '0.95rem' }}>{c.stock_name}</div>
                  <div style={{ color: '#64748b', fontSize: '0.72rem' }}>{c.stock_code}</div>
                </div>
                <div style={{ color: '#94a3b8', fontSize: '0.72rem', marginBottom: '0.5rem' }}>{c.market} · {c.sector_mid || c.sector_large}</div>
                <div style={{ color: '#e2e8f0', fontSize: '1.3rem', fontWeight: 900 }}>{fmtEok(c.market_cap_억)}</div>
                <div style={{ color: '#64748b', fontSize: '0.72rem', marginBottom: '0.5rem' }}>시가총액 · 현재가 {fmtPrice(c.current_price)}</div>
                <div style={{ display: 'flex', gap: '0.6rem', fontSize: '0.78rem', color: '#94a3b8', marginBottom: '0.5rem' }}>
                  <span>PER {fmtNum(c.per)}</span>
                  <span>PBR {fmtNum(c.pbr)}</span>
                  <span>ROE {fmtNum(c.roe, 1)}%</span>
                </div>
                <div style={{
                  display: 'inline-block', padding: '0.2rem 0.6rem', borderRadius: '999px', fontSize: '0.72rem', fontWeight: 700,
                  background: `${verdictColor(c.valuation_verdict)}22`, color: verdictColor(c.valuation_verdict),
                  border: `1px solid ${verdictColor(c.valuation_verdict)}55`,
                }}>
                  {c.valuation_verdict || '판정 불가'}
                </div>
              </div>
            ))}
          </div>

          {/* 저평가 판정 안내 */}
          <div style={{ ...card(), marginBottom: '1rem', fontSize: '0.78rem', color: '#94a3b8' }}>
            ℹ️ 저평가/고평가 판정은 <b>선택한 종목들 사이의 상대 비교</b>(PER·PBR이 낮고 ROE가 상대적으로 높을수록 저평가 쪽)일 뿐,
            절대적인 투자 판단이나 매수 추천이 아닙니다. 참고용으로만 활용하세요.
          </div>

          {/* 재무 비교 테이블 */}
          <div style={card({ padding: 0, overflow: 'hidden', marginBottom: '1rem' })}>
            <div style={{ padding: '0.85rem 1rem', borderBottom: '1px solid rgba(51,65,85,0.55)', color: '#e2e8f0', fontWeight: 800 }}>
              재무·밸류에이션 비교
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', minWidth: 480 + companies.length * 140, borderCollapse: 'collapse', fontSize: '0.82rem' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid rgba(51,65,85,0.5)', background: 'rgba(15,23,42,0.35)' }}>
                    <th style={{ padding: '0.55rem 0.8rem', color: '#94a3b8', fontWeight: 700, textAlign: 'left' }}>지표</th>
                    {companies.map(c => (
                      <th key={c.stock_code} style={{ padding: '0.55rem 0.8rem', color: '#e2e8f0', fontWeight: 700, textAlign: 'right' }}>{c.stock_name}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {metricRows.map(row => (
                    <tr key={row.key} style={{ borderBottom: '1px solid rgba(51,65,85,0.3)' }}>
                      <td style={{ padding: '0.5rem 0.8rem', color: '#94a3b8' }}>{row.label}</td>
                      {companies.map(c => {
                        const raw = row.pick ? row.pick(c) : c[row.key];
                        return (
                          <td key={c.stock_code} style={{ padding: '0.5rem 0.8rem', color: '#e2e8f0', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>
                            {row.fmt(raw)}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* 매출 추이 차트 */}
          <div style={card({ marginBottom: '1rem' })}>
            <div style={{ color: '#e2e8f0', fontWeight: 800, marginBottom: '0.75rem' }}>연간 매출 추이 (억원)</div>
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={revenueTrendData}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(51,65,85,0.3)" />
                <XAxis dataKey="year" tick={{ fill: '#94a3b8', fontSize: 12 }} />
                <YAxis tick={{ fill: '#94a3b8', fontSize: 12 }} />
                <Tooltip contentStyle={{ background: '#1e293b', border: '1px solid rgba(51,65,85,0.8)', borderRadius: '0.5rem' }}
                  formatter={(v) => v === null ? '-' : `${Math.round(v).toLocaleString()}억`} />
                <Legend wrapperStyle={{ fontSize: '0.78rem' }} />
                {companies.map((c, i) => (
                  <Bar key={c.stock_code} dataKey={c.stock_name} fill={PALETTE[i % PALETTE.length]} radius={[4, 4, 0, 0]} />
                ))}
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* 매출 구성비 */}
          <div style={card()}>
            <div style={{ color: '#e2e8f0', fontWeight: 800, marginBottom: '0.25rem' }}>매출 구성비 (품목별)</div>
            <div style={{ color: '#64748b', fontSize: '0.75rem', marginBottom: '0.75rem' }}>
              DART 사업보고서 '매출 및 수주상황' 기준 · 종목별 최신 사업연도
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: `repeat(${companies.length}, 1fr)`, gap: '1rem' }}>
              {companies.map((c) => (
                <div key={c.stock_code}>
                  <div style={{ textAlign: 'center', color: '#94a3b8', fontSize: '0.8rem', marginBottom: '0.25rem' }}>
                    {c.stock_name} {c.product_mix_year ? `(${c.product_mix_year}년)` : ''}
                  </div>
                  {c.product_mix && c.product_mix.length > 0 ? (
                    <>
                      <ResponsiveContainer width="100%" height={200}>
                        <PieChart>
                          <Pie data={c.product_mix} dataKey="revenue_pct" nameKey="product_name"
                            cx="50%" cy="50%" outerRadius={80} labelLine={false}
                            label={({ revenue_pct }) => revenue_pct >= 5 ? `${revenue_pct}%` : ''}>
                            {c.product_mix.map((_, idx) => (
                              <Cell key={idx} fill={PALETTE[idx % PALETTE.length]} />
                            ))}
                          </Pie>
                          <Tooltip contentStyle={{ background: '#1e293b', border: '1px solid rgba(51,65,85,0.8)', borderRadius: '0.5rem', fontSize: '0.78rem' }}
                            formatter={(value, name) => [`${value}%`, name]} />
                        </PieChart>
                      </ResponsiveContainer>
                      <div style={{ fontSize: '0.72rem', color: '#94a3b8' }}>
                        {c.product_mix.slice(0, 5).map((p, idx) => (
                          <div key={idx} style={{ display: 'flex', justifyContent: 'space-between', padding: '0.15rem 0' }}>
                            <span style={{ display: 'flex', alignItems: 'center', gap: '0.3rem' }}>
                              <span style={{ width: 8, height: 8, borderRadius: '50%', background: PALETTE[idx % PALETTE.length], display: 'inline-block' }} />
                              {p.product_name}
                            </span>
                            <span>{p.revenue_pct}%</span>
                          </div>
                        ))}
                      </div>
                    </>
                  ) : (
                    <div style={{ textAlign: 'center', color: '#475569', fontSize: '0.78rem', padding: '2rem 0' }}>
                      매출구성 데이터 미수집
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
