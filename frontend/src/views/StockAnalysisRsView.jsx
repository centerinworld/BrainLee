import React, { useEffect, useMemo, useState } from 'react';

const API = (path) => path;

const PAGE_SIZE = 25;
const PERIODS = ['12M', '1M', '3M', '6M'];

const fmtInt = (v) => (Number.isFinite(v) ? Number(v).toLocaleString('ko-KR') : '-');
const fmtPrice = (v) => (Number.isFinite(v) ? Number(v).toLocaleString('ko-KR') : '-');
const fmtPct = (v) => (Number.isFinite(v) ? `${v > 0 ? '+' : ''}${Number(v).toFixed(2)}%` : '-');
const clamp = (v, min, max) => Math.min(max, Math.max(min, v));

function percentileMap(values) {
  const sorted = [...values].filter(Number.isFinite).sort((a, b) => a - b);
  if (!sorted.length) return new Map();
  const map = new Map();
  sorted.forEach((val, idx) => {
    const pct = Math.round((idx / Math.max(sorted.length - 1, 1)) * 100);
    const arr = map.get(val) || [];
    arr.push(pct);
    map.set(val, arr);
  });
  const cursors = new Map();
  return {
    get(val) {
      const arr = map.get(val);
      if (!arr || !arr.length) return 50;
      const cur = cursors.get(val) || 0;
      const next = Math.min(cur, arr.length - 1);
      cursors.set(val, next + 1);
      return arr[next];
    },
  };
}

export default function StockAnalysisRsView() {
  const [activeTab, setActiveTab] = useState('rs');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [data, setData] = useState({ rs_list: [], sector_rs: [], sector_rs_mid: [], metadata: {} });
  const [high52Data, setHigh52Data] = useState({ high52_list: [], metadata: {} });

  const [period, setPeriod] = useState('12M');
  const [sectorMode, setSectorMode] = useState('major');
  const [capMin, setCapMin] = useState(0);
  const [query, setQuery] = useState('');
  const [sectorFilter, setSectorFilter] = useState('전체');
  const [page, setPage] = useState(1);

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    Promise.all([
      fetch(API('/api/stock-analysis-rs/dashboard-data')).then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))),
      fetch(API('/api/stock-analysis-rs/high52-data')).then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))),
    ])
      .then(([rsJson, highJson]) => {
        if (!mounted) return;
        if (!rsJson?.success) throw new Error(rsJson?.reason || 'RS 데이터 로드 실패');
        if (!highJson?.success) throw new Error(highJson?.reason || '52주 신고가 데이터 로드 실패');
        setData(rsJson.data || { rs_list: [], sector_rs: [], sector_rs_mid: [], metadata: {} });
        setHigh52Data(highJson.data || { high52_list: [], metadata: {} });
        setError('');
      })
      .catch((e) => {
        if (!mounted) return;
        setError(String(e?.message || e));
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });
    return () => {
      mounted = false;
    };
  }, []);

  const rsRows = data.rs_list || [];
  const majorSectors = data.sector_rs || [];
  const middleSectors = data.sector_rs_mid || [];

  const rsPercentile = useMemo(() => {
    const mapper = percentileMap(rsRows.map((r) => r.rs));
    return rsRows.map((r) => ({ ...r, rs_score: clamp(mapper.get(r.rs), 0, 100) }));
  }, [rsRows]);

  const sectorScores = useMemo(() => {
    const source = sectorMode === 'major' ? majorSectors : middleSectors;
    const mapper = percentileMap(source.map((s) => s.avg_rs));
    return source
      .map((s) => ({ ...s, score: clamp(mapper.get(s.avg_rs), 0, 100) }))
      .sort((a, b) => b.score - a.score);
  }, [sectorMode, majorSectors, middleSectors]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    const rows = rsPercentile.filter((r) => {
      const capOk = (r.market_cap || 0) >= capMin;
      if (!capOk) return false;
      const sectorList = sectorMode === 'major' ? (r.major_names || [r.major_name]) : (r.mid_names || [r.mid_name]);
      const secOk = sectorFilter === '전체' || sectorList.includes(sectorFilter);
      if (!secOk) return false;
      if (!q) return true;
      return String(r.stock_name || '').toLowerCase().includes(q) || String(r.stock_code || '').includes(q);
    });

    const sortKey = period === '1M' ? 'rs_1m' : period === '3M' ? 'rs_3m' : period === '6M' ? 'rs_6m' : 'rs';
    return rows.sort((a, b) => (b[sortKey] || 0) - (a[sortKey] || 0));
  }, [rsPercentile, sectorMode, sectorFilter, query, capMin, period]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const paged = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  useEffect(() => {
    setPage(1);
  }, [query, sectorFilter, sectorMode, capMin, period]);

  const strongSector = sectorScores[0];
  const weakSector = sectorScores[sectorScores.length - 1];
  const high52Filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (high52Data.high52_list || [])
      .filter((r) => {
        if ((r.market_cap || 0) < capMin) return false;
        const sectorList = sectorMode === 'major' ? (r.major_names || []) : (r.mid_names || []);
        if (sectorFilter !== '전체' && !sectorList.includes(sectorFilter)) return false;
        if (!q) return true;
        return String(r.stock_name || '').toLowerCase().includes(q) || String(r.stock_code || '').includes(q);
      })
      .sort((a, b) => {
        if (a.is_new_high !== b.is_new_high) return (b.is_new_high ? 1 : 0) - (a.is_new_high ? 1 : 0);
        if (a.is_near_high !== b.is_near_high) return (b.is_near_high ? 1 : 0) - (a.is_near_high ? 1 : 0);
        return (b.breakout_score || 0) - (a.breakout_score || 0);
      });
  }, [high52Data, query, capMin, sectorMode, sectorFilter]);

  return (
    <div style={{ padding: '18px 18px 26px', color: '#e5e7eb', background: '#080c14', minHeight: '100%' }}>
      <div style={{ display: 'flex', gap: 26, borderBottom: '1px solid #2a2f3a', paddingBottom: 10, marginBottom: 18, fontWeight: 700, fontSize: 27 }}>
        <button onClick={() => setActiveTab('rs')} style={{ border: 'none', background: 'transparent', borderBottom: activeTab === 'rs' ? '3px solid #d1d5db' : 'none', paddingBottom: 8, color: activeTab === 'rs' ? '#f9fafb' : '#7f8793', fontWeight: 700, fontSize: 27, cursor: 'pointer' }}>종합 RS</button>
        <button onClick={() => setActiveTab('high52')} style={{ border: 'none', background: 'transparent', borderBottom: activeTab === 'high52' ? '3px solid #d1d5db' : 'none', paddingBottom: 8, color: activeTab === 'high52' ? '#f9fafb' : '#7f8793', fontWeight: 700, fontSize: 27, cursor: 'pointer' }}>52주 신고가</button>
      </div>

      {activeTab === 'rs' && (
      <>
      <section style={{ background: '#111827', border: '1px solid #2b3240', borderRadius: 10, padding: 16, marginBottom: 14 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 14, fontSize: 13, color: '#a7b0bf' }}>
          <span>한눈에 보이는 섹터RS</span>
          <span>{sectorScores.length}개 섹터</span>
        </div>
        <div style={{ position: 'relative', height: 78 }}>
          <div style={{ position: 'absolute', left: 0, right: 0, top: 30, height: 10, borderRadius: 99, background: 'linear-gradient(90deg,#60a5fa,#f59e0b,#ef4444)' }} />
          {sectorScores.slice(0, 15).map((s, idx) => (
            <div
              key={s.sector}
              title={`${s.sector} ${s.score}`}
              style={{
                position: 'absolute',
                left: `${clamp(s.score, 0, 100)}%`,
                top: idx % 2 ? 44 : 12,
                transform: 'translateX(-50%)',
                fontSize: 11,
                color: '#e5e7eb',
                background: 'rgba(17,24,39,0.95)',
                border: '1px solid #3c4659',
                borderRadius: 5,
                padding: '2px 6px',
                whiteSpace: 'nowrap',
              }}
            >
              {s.sector}
            </div>
          ))}
          <div style={{ position: 'absolute', top: 64, left: 0, fontSize: 12, color: '#9ca3af' }}>약함 {weakSector?.score ?? 0}</div>
          <div style={{ position: 'absolute', top: 64, right: 0, fontSize: 12, color: '#9ca3af' }}>{strongSector?.score ?? 100} 강함</div>
        </div>
      </section>

      <section style={{ background: '#111827', border: '1px solid #2b3240', borderRadius: 10, padding: 12, marginBottom: 14 }}>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <button
            onClick={() => setSectorFilter('전체')}
            style={{
              border: '1px solid #45506b',
              background: sectorFilter === '전체' ? '#1f2937' : '#0d1320',
              color: '#e5e7eb',
              padding: '6px 10px',
              borderRadius: 8,
              cursor: 'pointer',
            }}
          >
            전체
          </button>
          {sectorScores.map((s) => (
            <button
              key={s.sector}
              onClick={() => setSectorFilter(s.sector)}
              style={{
                border: '1px solid #374151',
                background: sectorFilter === s.sector ? '#1f2937' : 'transparent',
                color: sectorFilter === s.sector ? '#f9fafb' : '#c7ceda',
                padding: '4px 8px',
                borderRadius: 999,
                cursor: 'pointer',
                fontSize: 13,
              }}
            >
              {s.sector} <span style={{ color: '#f87171', fontWeight: 700 }}>{s.score}</span>
            </button>
          ))}
        </div>
      </section>

      <section style={{ background: '#111827', border: '1px solid #2b3240', borderRadius: 10, padding: 12, marginBottom: 14 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <div style={{ display: 'flex', border: '1px solid #3a4457', borderRadius: 8, overflow: 'hidden' }}>
              {PERIODS.map((p) => (
                <button
                  key={p}
                  onClick={() => setPeriod(p)}
                  style={{
                    border: 'none',
                    borderRight: p === PERIODS[PERIODS.length - 1] ? 'none' : '1px solid #3a4457',
                    background: period === p ? '#1f2937' : '#0d1320',
                    color: period === p ? '#f9fafb' : '#94a3b8',
                    padding: '7px 12px',
                    cursor: 'pointer',
                    fontWeight: 700,
                  }}
                >
                  {p}
                </button>
              ))}
            </div>
            <div style={{ display: 'flex', border: '1px solid #3a4457', borderRadius: 8, overflow: 'hidden' }}>
              <button onClick={() => setSectorMode('major')} style={{ border: 'none', background: sectorMode === 'major' ? '#1f2937' : '#0d1320', color: '#e5e7eb', padding: '7px 10px', cursor: 'pointer' }}>대분류</button>
              <button onClick={() => setSectorMode('middle')} style={{ border: 'none', borderLeft: '1px solid #3a4457', background: sectorMode === 'middle' ? '#1f2937' : '#0d1320', color: '#e5e7eb', padding: '7px 10px', cursor: 'pointer' }}>중분류</button>
            </div>
            <select value={capMin} onChange={(e) => setCapMin(Number(e.target.value))} style={{ background: '#0d1320', color: '#e5e7eb', border: '1px solid #3a4457', borderRadius: 8, padding: '7px 10px' }}>
              <option value={0}>전체 시총</option>
              <option value={5000}>5,000억+</option>
              <option value={10000}>1조+</option>
              <option value={20000}>2조+</option>
            </select>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="종목명/종목코드"
              style={{ background: '#0d1320', color: '#e5e7eb', border: '1px solid #3a4457', borderRadius: 8, padding: '7px 10px', width: 180 }}
            />
          </div>
          <div style={{ color: '#93c5fd', fontWeight: 700 }}>{fmtInt(filtered.length)}개 종목</div>
        </div>
      </section>

      <section style={{ background: '#111827', border: '1px solid #2b3240', borderRadius: 10, overflow: 'hidden' }}>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1220 }}>
            <thead>
              <tr style={{ background: '#0f172a', color: '#94a3b8', fontSize: 13 }}>
                <th style={{ textAlign: 'left', padding: '10px 12px' }}>섹터</th>
                <th style={{ textAlign: 'left', padding: '10px 12px' }}>종목명</th>
                <th style={{ textAlign: 'right', padding: '10px 12px' }}>현재가</th>
                <th style={{ textAlign: 'right', padding: '10px 12px' }}>등락률</th>
                <th style={{ textAlign: 'right', padding: '10px 12px' }}>RS</th>
                <th style={{ textAlign: 'right', padding: '10px 12px' }}>RS(1M)</th>
                <th style={{ textAlign: 'right', padding: '10px 12px' }}>RS(3M)</th>
                <th style={{ textAlign: 'right', padding: '10px 12px' }}>RS(6M)</th>
                <th style={{ textAlign: 'right', padding: '10px 12px' }}>MMT</th>
                <th style={{ textAlign: 'right', padding: '10px 12px' }}>시가총액</th>
              </tr>
            </thead>
            <tbody>
              {paged.map((r) => {
                const sectorName = sectorMode === 'major' ? (r.major_names?.join(', ') || r.major_name || '-') : (r.mid_names?.join(', ') || r.mid_name || '-');
                return (
                  <tr key={r.stock_code} style={{ borderTop: '1px solid #1f2937' }}>
                    <td style={{ padding: '9px 12px', color: '#cbd5e1', fontSize: 13 }}>{sectorName}</td>
                    <td style={{ padding: '9px 12px', color: '#f3f4f6' }}>
                      <div style={{ fontWeight: 700 }}>{r.stock_name}</div>
                      <div style={{ color: '#64748b', fontSize: 12 }}>{r.stock_code}</div>
                    </td>
                    <td style={{ padding: '9px 12px', textAlign: 'right' }}>{fmtPrice(r.current_price)}</td>
                    <td style={{ padding: '9px 12px', textAlign: 'right', color: (r.change_rate || 0) >= 0 ? '#22c55e' : '#ef4444', fontWeight: 700 }}>{fmtPct(r.change_rate)}</td>
                    <td style={{ padding: '9px 12px', textAlign: 'right', fontWeight: 800 }}>{r.rs_score}</td>
                    <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number(r.rs_1m || 0).toFixed(2)}</td>
                    <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number(r.rs_3m || 0).toFixed(2)}</td>
                    <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number(r.rs_6m || 0).toFixed(2)}</td>
                    <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number(r.mmt || 0).toFixed(2)}</td>
                    <td style={{ padding: '9px 12px', textAlign: 'right' }}>{fmtInt(r.market_cap)}억</td>
                  </tr>
                );
              })}
              {!loading && !paged.length && (
                <tr>
                  <td colSpan={10} style={{ padding: 28, textAlign: 'center', color: '#94a3b8' }}>조건에 맞는 종목이 없습니다.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: 12, borderTop: '1px solid #1f2937' }}>
          <div style={{ color: '#64748b', fontSize: 13 }}>
            기준일: {data.metadata?.target_date || '-'} / 갱신: {data.metadata?.updated_at || '-'} / 종목수: {fmtInt(data.metadata?.count || 0)}
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1} style={{ border: '1px solid #374151', background: '#0d1320', color: '#e5e7eb', borderRadius: 6, padding: '5px 10px', cursor: 'pointer' }}>이전</button>
            <span style={{ color: '#cbd5e1', fontSize: 13 }}>{page} / {totalPages}</span>
            <button onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page >= totalPages} style={{ border: '1px solid #374151', background: '#0d1320', color: '#e5e7eb', borderRadius: 6, padding: '5px 10px', cursor: 'pointer' }}>다음</button>
          </div>
        </div>
      </section>
      </>
      )}

      {activeTab === 'high52' && (
        <section style={{ background: '#111827', border: '1px solid #2b3240', borderRadius: 10, overflow: 'hidden', marginTop: 14 }}>
          <div style={{ padding: 12, borderBottom: '1px solid #1f2937', color: '#cbd5e1', display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            <span>신고가: <strong style={{ color: '#22c55e' }}>{fmtInt(high52Data.metadata?.new_high_count || 0)}</strong></span>
            <span>근접(2% 이내): <strong style={{ color: '#93c5fd' }}>{fmtInt(high52Data.metadata?.near_high_count || 0)}</strong></span>
            <span>전체: <strong>{fmtInt(high52Data.metadata?.count || 0)}</strong></span>
          </div>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1180 }}>
              <thead>
                <tr style={{ background: '#0f172a', color: '#94a3b8', fontSize: 13 }}>
                  <th style={{ textAlign: 'left', padding: '10px 12px' }}>종목명</th>
                  <th style={{ textAlign: 'left', padding: '10px 12px' }}>섹터</th>
                  <th style={{ textAlign: 'right', padding: '10px 12px' }}>현재가</th>
                  <th style={{ textAlign: 'right', padding: '10px 12px' }}>52주 최고가</th>
                  <th style={{ textAlign: 'right', padding: '10px 12px' }}>최고가 대비</th>
                  <th style={{ textAlign: 'right', padding: '10px 12px' }}>등락률</th>
                  <th style={{ textAlign: 'right', padding: '10px 12px' }}>거래량 배수(20D)</th>
                  <th style={{ textAlign: 'right', padding: '10px 12px' }}>브레이크아웃 점수</th>
                </tr>
              </thead>
              <tbody>
                {high52Filtered.slice(0, 300).map((r) => (
                  <tr key={`h-${r.stock_code}`} style={{ borderTop: '1px solid #1f2937' }}>
                    <td style={{ padding: '9px 12px', color: '#f3f4f6' }}>
                      <div style={{ fontWeight: 700 }}>{r.stock_name}</div>
                      <div style={{ color: '#64748b', fontSize: 12 }}>{r.stock_code}</div>
                    </td>
                    <td style={{ padding: '9px 12px', color: '#cbd5e1' }}>{sectorMode === 'major' ? (r.major_names || []).join(', ') : (r.mid_names || []).join(', ')}</td>
                    <td style={{ padding: '9px 12px', textAlign: 'right' }}>{fmtPrice(r.current_price)}</td>
                    <td style={{ padding: '9px 12px', textAlign: 'right' }}>{fmtPrice(r.high52_price)}</td>
                    <td style={{ padding: '9px 12px', textAlign: 'right', color: (r.high_gap_pct || 0) >= -2 ? '#22c55e' : '#94a3b8', fontWeight: 700 }}>{fmtPct(r.high_gap_pct)}</td>
                    <td style={{ padding: '9px 12px', textAlign: 'right', color: (r.change_rate || 0) >= 0 ? '#22c55e' : '#ef4444', fontWeight: 700 }}>{fmtPct(r.change_rate)}</td>
                    <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number(r.vol_ratio || 0).toFixed(2)}x</td>
                    <td style={{ padding: '9px 12px', textAlign: 'right' }}>
                      <span style={{ color: r.is_new_high ? '#22c55e' : (r.is_near_high ? '#93c5fd' : '#cbd5e1'), fontWeight: 700 }}>
                        {Number(r.breakout_score || 0).toFixed(2)}
                      </span>
                    </td>
                  </tr>
                ))}
                {!loading && !high52Filtered.length && (
                  <tr>
                    <td colSpan={8} style={{ padding: 28, textAlign: 'center', color: '#94a3b8' }}>조건에 맞는 종목이 없습니다.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {loading && <div style={{ marginTop: 10, color: '#93c5fd' }}>데이터 로딩 중...</div>}
      {!!error && <div style={{ marginTop: 10, color: '#f87171' }}>오류: {error}</div>}
    </div>
  );
}
