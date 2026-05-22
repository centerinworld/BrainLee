import React from 'react';
import {
  ResponsiveContainer,
  ComposedChart,
  Bar,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts';

const API = (path) => path;

const SECTOR_ORDER = [
  '종합', '제조', '설계', '공정설계', '클린룸', '전공정 장비', '후공정 장비',
  '전공정 부품', '전공정 원료/소재', '후공정 소재/부품', 'EDS Test 부품',
  '테스트소켓', '기판', 'OSAT', '반도체 유동', 'EMS', '반도체+로봇(이송)', '기타', '확인중',
];

const DEFAULT_DATES = ['2026-01-02', '2025-08-01', '2025-04-02'];

const fmtNum = (v, digits = 0) => {
  if (v == null || Number.isNaN(Number(v))) return '';
  return Number(v).toLocaleString('ko-KR', {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
};

const fmtPct = (v, digits = 1) => {
  if (v == null || Number.isNaN(Number(v))) return '';
  const n = Number(v);
  return `${n > 0 ? '+' : ''}${n.toFixed(digits)}%`;
};

const pctColor = (v) => {
  if (v == null || Number.isNaN(Number(v))) return '#94a3b8';
  if (Number(v) > 0) return '#ef4444';
  if (Number(v) < 0) return '#60a5fa';
  return '#94a3b8';
};

const pctBg = (v) => {
  if (v == null || Number.isNaN(Number(v))) return 'transparent';
  const n = Math.min(Math.abs(Number(v)), 80) / 80;
  if (Number(v) > 0) return `rgba(239,68,68,${0.08 + n * 0.2})`;
  if (Number(v) < 0) return `rgba(37,99,235,${0.08 + n * 0.2})`;
  return 'rgba(148,163,184,0.06)';
};

const Indicator = ({ value, inverse = false }) => {
  if (value == null || Number.isNaN(Number(value))) return <span className="semi-ind muted">-</span>;
  const n = Number(value);
  const up = inverse ? n < 0 : n > 0;
  const down = inverse ? n > 0 : n < 0;
  return <span className={`semi-ind ${up ? 'up' : down ? 'down' : 'flat'}`}>{up ? '▲' : down ? '▼' : '■'}</span>;
};

const cellBase = {
  padding: '0.22rem 0.34rem',
  border: '1px solid rgba(34,197,94,0.5)',
  whiteSpace: 'nowrap',
};

const thBase = {
  ...cellBase,
  background: '#2563eb',
  color: '#fff',
  fontWeight: 800,
  textAlign: 'center',
};

const darkTh = {
  ...thBase,
  background: '#064e3b',
};

const orangeTh = {
  ...thBase,
  background: '#f59e0b',
};

const blueCorner = {
  position: 'absolute',
  left: 0,
  top: 0,
  width: 0,
  height: 0,
  borderTop: '9px solid #2563eb',
  borderRight: '9px solid transparent',
};

const MetricCell = ({ value, suffix = '', digits = 0, color, bg, children }) => (
  <td style={{ ...cellBase, textAlign: 'right', color: color || '#0f172a', background: bg || '#f8fafc', position: 'relative' }}>
    <span style={blueCorner} />
    {children ?? (value == null || Number.isNaN(Number(value)) ? '' : `${fmtNum(value, digits)}${suffix}`)}
  </td>
);

const PctCell = ({ value }) => (
  <td style={{ ...cellBase, textAlign: 'right', color: pctColor(value), background: pctBg(value), fontWeight: 800, position: 'relative' }}>
    <span style={blueCorner} />
    {fmtPct(value, 0)}
  </td>
);

const SemiconductorSectorView = () => {
  const [activeTab, setActiveTab] = React.useState('overview');
  const [dates, setDates] = React.useState(DEFAULT_DATES);
  const [pendingDates, setPendingDates] = React.useState(DEFAULT_DATES);
  const [stocks, setStocks] = React.useState([]);
  const [labStocks, setLabStocks] = React.useState([]);
  const [annualRows, setAnnualRows] = React.useState([]);
  const [quarterRows, setQuarterRows] = React.useState([]);
  const [benchmarks, setBenchmarks] = React.useState([]);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState('');

  const refParam = React.useMemo(() => dates.filter(Boolean).join(','), [dates]);

  React.useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError('');
    Promise.allSettled([
      fetch(API(`/api/semiconductor/stocks?ref_dates=${encodeURIComponent(refParam)}`)).then(r => r.ok ? r.json() : []).catch(() => []),
      fetch(API('/semiconductor-lab/api/stocks')).then(r => r.ok ? r.json() : { items: [] }).catch(() => ({ items: [] })),
      fetch(API('/api/semiconductor/financials?type=annual')).then(r => r.ok ? r.json() : []).catch(() => []),
      fetch(API('/api/semiconductor/financials?type=quarterly')).then(r => r.ok ? r.json() : []).catch(() => []),
      fetch(API(`/api/semiconductor/benchmarks?ref_dates=${encodeURIComponent(refParam)}`)).then(r => r.ok ? r.json() : []).catch(() => []),
    ]).then((results) => {
      if (cancelled) return;
      const [stockRows, lab, annual, quarter, benchmarkRows] = results.map(r => r.value ?? r.reason ?? null);
      setStocks(Array.isArray(stockRows) ? stockRows : []);
      setLabStocks(Array.isArray(lab?.items) ? lab.items : []);
      setAnnualRows(Array.isArray(annual) ? annual : []);
      setQuarterRows(Array.isArray(quarter) ? quarter : []);
      setBenchmarks(Array.isArray(benchmarkRows) ? benchmarkRows : []);
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [refParam]);

  const labMap = React.useMemo(() => {
    const m = new Map();
    labStocks.forEach(s => m.set(s.stock_code, s));
    return m;
  }, [labStocks]);

  const mergedStocks = React.useMemo(() => stocks.map(s => {
    const lab = labMap.get(s.code) || {};
    return {
      ...s,
      lv2: lab.lv2 || '',
      customer: lab.customer || '',
      main_biz: s.main_biz || lab.main_business || '',
      pbr: s.pbr ?? lab.current_pbr ?? lab.pbr,
      per: s.per ?? lab.current_per ?? lab.per,
      psr: s.psr ?? lab.psr ?? lab.workbook_psr,
      mktcap: s.mktcap ?? (lab.current_market_cap ? Math.round(lab.current_market_cap / 1e8) : null),
      close: s.close ?? lab.current_price,
    };
  }), [stocks, labMap]);

  const sectorStats = React.useMemo(() => {
    const by = new Map();
    mergedStocks.forEach(s => {
      const key = s.lv1 || '';
      if (!by.has(key)) by.set(key, []);
      by.get(key).push(s);
    });
    return SECTOR_ORDER.filter(k => by.has(k)).map((lv1, idx) => {
      const rows = by.get(lv1);
      const total = rows.length;
      const rising = rows.filter(r => Number(r.chg_pct) > 0).length;
      const avgPsrRows = rows.filter(r => r.psr != null && Number.isFinite(Number(r.psr)));
      const avgPsr = avgPsrRows.length ? avgPsrRows.reduce((a, r) => a + Number(r.psr), 0) / avgPsrRows.length : null;
      const avgRefs = dates.map((_, i) => {
        const vals = rows.map(r => r.refs?.[i]?.chg_pct).filter(v => v != null && Number.isFinite(Number(v)));
        return vals.length ? vals.reduce((a, v) => a + Number(v), 0) / vals.length : null;
      });
      const dayVals = rows.map(r => r.chg_pct).filter(v => v != null && Number.isFinite(Number(v)));
      const avgDay = dayVals.length ? dayVals.reduce((a, v) => a + Number(v), 0) / dayVals.length : null;
      return { no: idx + 1, lv1, total, rising, risingPct: total ? rising / total * 100 : 0, avgPsr, avgDay, avgRefs };
    });
  }, [mergedStocks, dates]);

  const totalSummary = React.useMemo(() => {
    const total = mergedStocks.length;
    const rising = mergedStocks.filter(r => Number(r.chg_pct) > 0).length;
    const avgPsrRows = mergedStocks.filter(r => r.psr != null && Number.isFinite(Number(r.psr)));
    const avgPsr = avgPsrRows.length ? avgPsrRows.reduce((a, r) => a + Number(r.psr), 0) / avgPsrRows.length : null;
    return { total, rising, risingPct: total ? rising / total * 100 : 0, avgPsr };
  }, [mergedStocks]);

  const groupedStocks = React.useMemo(() => {
    const by = new Map();
    mergedStocks.forEach(s => {
      const key = s.lv1 || '기타';
      if (!by.has(key)) by.set(key, []);
      by.get(key).push(s);
    });
    return SECTOR_ORDER.filter(k => by.has(k)).map(k => [k, by.get(k)]);
  }, [mergedStocks]);

  const applyDates = () => setDates(pendingDates.map(d => d.trim()).filter(Boolean).slice(0, 3));

  const tabs = [
    ['overview', '반도체 종합현황'],
    ['all', '전종목'],
    ['yoy', 'YoY 실적'],
    ['qoq', 'QoQ 실적'],
    ['stock', '종목 실적'],
  ];

  const categoryOptions = React.useMemo(() => {
    return groupedStocks.map(([k]) => k);
  }, [groupedStocks]);
  const [pickedCategory, setPickedCategory] = React.useState('');
  const [pickedCode, setPickedCode] = React.useState('');

  React.useEffect(() => {
    if (!categoryOptions.length) return;
    if (!pickedCategory || !categoryOptions.includes(pickedCategory)) {
      setPickedCategory(categoryOptions[0]);
    }
  }, [categoryOptions, pickedCategory]);

  const categoryStocks = React.useMemo(() => {
    const hit = groupedStocks.find(([k]) => k === pickedCategory);
    return hit ? hit[1] : [];
  }, [groupedStocks, pickedCategory]);

  React.useEffect(() => {
    if (!categoryStocks.length) return;
    const exists = categoryStocks.some(s => s.code === pickedCode);
    if (!pickedCode || !exists) setPickedCode(categoryStocks[0].code);
  }, [categoryStocks, pickedCode]);

  const pickedStock = React.useMemo(
    () => categoryStocks.find(s => s.code === pickedCode) || null,
    [categoryStocks, pickedCode],
  );

  return (
    <div className="semi-page">
      <style>{`
        .semi-page { display:flex; flex-direction:column; gap:0.75rem; color:var(--text-primary); }
        .semi-tabs { display:flex; gap:0.35rem; flex-wrap:wrap; }
        .semi-tab { border:0; border-radius:8px; padding:0.42rem 0.85rem; cursor:pointer; font-weight:800; color:var(--text-primary); background:rgba(255,255,255,0.05); }
        .semi-tab.active { background:var(--accent-mint); color:#00110f; }
        .semi-card { background:var(--bg-card); border:1px solid var(--glass-border); border-radius:8px; padding:0.8rem; }
        .semi-title { display:flex; align-items:center; justify-content:space-between; gap:0.75rem; flex-wrap:wrap; }
        .semi-table-wrap { overflow:auto; -webkit-overflow-scrolling:touch; border-radius:6px; }
        .semi-excel-table { border-collapse:collapse; min-width:920px; color:#0f172a; background:#f8fafc; font-size:0.75rem; width:100%; }
        .semi-excel-table td, .semi-excel-table th { line-height:1.15; }
        .semi-excel-table tbody tr:hover td { filter:brightness(0.95); }
        .semi-name { color:#064e3b; font-weight:800; cursor:help; }
        .semi-ind { font-size:0.72rem; font-weight:900; margin-left:0.25rem; }
        .semi-ind.up { color:#ef4444; }
        .semi-ind.down { color:#2563eb; }
        .semi-ind.flat,.semi-ind.muted { color:#94a3b8; }
        .semi-input { background:rgba(255,255,255,0.06); color:#fff; border:1px solid var(--glass-border); border-radius:6px; padding:0.38rem 0.5rem; width:126px; font-size:0.8rem; }
        .semi-button { background:var(--accent-mint); color:#00110f; border:0; border-radius:6px; padding:0.4rem 0.75rem; font-weight:800; cursor:pointer; }
      `}</style>

      <div className="semi-card semi-title">
        <div>
          <h2 style={{ margin:0, fontSize:'1.1rem' }}>반도체 섹터</h2>
          <p style={{ margin:'0.25rem 0 0', color:'var(--text-secondary)', fontSize:'0.82rem' }}>실적 개선과 우상향 기업을 조기 탐지하기 위한 밸류체인 대시보드</p>
        </div>
        <div className="semi-tabs">
          {tabs.map(([key, label]) => <button key={key} className={`semi-tab ${activeTab === key ? 'active' : ''}`} onClick={() => setActiveTab(key)}>{label}</button>)}
        </div>
      </div>

      <div className="semi-card semi-title">
        <div style={{display:'flex',alignItems:'center',gap:'0.45rem',flexWrap:'wrap'}}>
          <span style={{fontSize:'0.78rem',color:'var(--text-secondary)',fontWeight:700}}>기준일</span>
          {[0, 1, 2].map(i => (
            <input key={i} type="date" className="semi-input" value={pendingDates[i] || ''} onChange={e => {
              const next = [...pendingDates];
              next[i] = e.target.value;
              setPendingDates(next);
            }} />
          ))}
          <button className="semi-button" onClick={applyDates}>적용</button>
        </div>
        <div style={{fontSize:'0.78rem',color:'var(--text-secondary)'}}>현재가 기준일: {mergedStocks[0]?.latest_date || '-'}</div>
      </div>

      {error && <div className="semi-card" style={{color:'#f87171'}}>{error}</div>}
      {loading && <div className="semi-card" style={{textAlign:'center',color:'var(--text-secondary)'}}>반도체 데이터 로딩 중...</div>}
      {!loading && activeTab === 'overview' && <OverviewTable stats={sectorStats} total={totalSummary} dates={dates} benchmarks={benchmarks} />}
      {!loading && activeTab === 'all' && <AllStocksTables groupedStocks={groupedStocks} dates={dates} />}
      {!loading && activeTab === 'yoy' && <FinancialTable rows={annualRows} mode="yoy" />}
      {!loading && activeTab === 'qoq' && <FinancialTable rows={quarterRows} mode="qoq" />}
      {!loading && activeTab === 'stock' && (
        <StockPerformancePanel
          categoryOptions={categoryOptions}
          pickedCategory={pickedCategory}
          setPickedCategory={setPickedCategory}
          categoryStocks={categoryStocks}
          pickedCode={pickedCode}
          setPickedCode={setPickedCode}
          pickedStock={pickedStock}
          annualRows={annualRows}
          quarterRows={quarterRows}
        />
      )}
    </div>
  );
};

const _periodOrder = (label, quarterly = false) => {
  if (!label) return -1;
  if (!quarterly) {
    const y = Number(String(label).replace(/[^\d]/g, ''));
    return Number.isFinite(y) ? y : -1;
  }
  const m = String(label).match(/(\d+)\.(\d)Q/i);
  if (!m) return -1;
  return Number(m[1]) * 10 + Number(m[2]);
};

const _buildSeries = (rows, name, quarterly = false) => {
  const rev = rows.find(r => r.name === name && r.type === 'revenue');
  const prof = rows.find(r => r.name === name && r.type === 'profit');
  const keys = Array.from(new Set([
    ...Object.keys(rev?.data || {}),
    ...Object.keys(prof?.data || {}),
  ])).sort((a, b) => _periodOrder(a, quarterly) - _periodOrder(b, quarterly));
  return keys.map(k => ({
    period: k,
    revenue: rev?.data?.[k] ?? null,
    profit: prof?.data?.[k] ?? null,
  })).filter(r => r.revenue != null || r.profit != null);
};

const StockPerformancePanel = ({
  categoryOptions,
  pickedCategory,
  setPickedCategory,
  categoryStocks,
  pickedCode,
  setPickedCode,
  pickedStock,
  annualRows,
  quarterRows,
}) => {
  const annualSeries = React.useMemo(
    () => (pickedStock ? _buildSeries(annualRows, pickedStock.name, false) : []),
    [annualRows, pickedStock],
  );
  const quarterSeries = React.useMemo(
    () => (pickedStock ? _buildSeries(quarterRows, pickedStock.name, true) : []),
    [quarterRows, pickedStock],
  );

  const latestAnnual = annualSeries.length ? annualSeries[annualSeries.length - 1] : null;
  const latestQuarter = quarterSeries.length ? quarterSeries[quarterSeries.length - 1] : null;

  return (
    <div className="semi-card" style={{display:'flex',flexDirection:'column',gap:'0.8rem'}}>
      <div style={{display:'flex',flexWrap:'wrap',gap:'0.6rem',alignItems:'center',justifyContent:'space-between'}}>
        <div style={{display:'flex',alignItems:'center',gap:'0.5rem',flexWrap:'wrap'}}>
          <b style={{fontSize:'0.92rem'}}>카테고리</b>
          {categoryOptions.map(c => (
            <button key={c}
              onClick={() => setPickedCategory(c)}
              style={{
                padding:'0.25rem 0.55rem', borderRadius:'999px', cursor:'pointer',
                border: pickedCategory === c ? '1px solid #22d3ee' : '1px solid rgba(148,163,184,0.35)',
                background: pickedCategory === c ? 'rgba(34,211,238,0.15)' : 'rgba(255,255,255,0.04)',
                color: pickedCategory === c ? '#67e8f9' : '#cbd5e1', fontWeight:700, fontSize:'0.74rem',
              }}>
              {c}
            </button>
          ))}
        </div>

        <div style={{display:'flex',alignItems:'center',gap:'0.5rem'}}>
          <b style={{fontSize:'0.86rem'}}>기업</b>
          <select value={pickedCode} onChange={e => setPickedCode(e.target.value)}
            style={{padding:'0.34rem 0.5rem', borderRadius:'8px', background:'rgba(15,23,42,0.9)', color:'#e2e8f0', border:'1px solid rgba(148,163,184,0.4)'}}>
            {categoryStocks.map(s => <option key={s.code} value={s.code}>{s.name} ({s.code})</option>)}
          </select>
        </div>
      </div>

      <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(360px,1fr))',gap:'0.8rem'}}>
        <div style={{background:'rgba(15,23,42,0.85)',border:'1px solid rgba(148,163,184,0.25)',borderRadius:'10px',padding:'0.7rem'}}>
          <h4 style={{margin:'0 0 0.45rem',fontSize:'0.85rem'}}>연간 실적 (매출: 막대 / 이익: 선)</h4>
          <div style={{height:'260px'}}>
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={annualSeries}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.2)" />
                <XAxis dataKey="period" tick={{fill:'#cbd5e1', fontSize:11}} />
                <YAxis yAxisId="l" tick={{fill:'#94a3b8', fontSize:11}} />
                <YAxis yAxisId="r" orientation="right" tick={{fill:'#94a3b8', fontSize:11}} />
                <Tooltip />
                <Legend />
                <Bar yAxisId="l" dataKey="revenue" name="매출(억)" fill="#22d3ee" radius={[4,4,0,0]} />
                <Line yAxisId="r" type="monotone" dataKey="profit" name="이익(억)" stroke="#f97316" strokeWidth={2} dot={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </div>

        <div style={{background:'rgba(15,23,42,0.85)',border:'1px solid rgba(148,163,184,0.25)',borderRadius:'10px',padding:'0.7rem'}}>
          <h4 style={{margin:'0 0 0.45rem',fontSize:'0.85rem'}}>분기 실적 (매출: 막대 / 이익: 선)</h4>
          <div style={{height:'260px'}}>
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={quarterSeries}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.2)" />
                <XAxis dataKey="period" tick={{fill:'#cbd5e1', fontSize:11}} />
                <YAxis yAxisId="l" tick={{fill:'#94a3b8', fontSize:11}} />
                <YAxis yAxisId="r" orientation="right" tick={{fill:'#94a3b8', fontSize:11}} />
                <Tooltip />
                <Legend />
                <Bar yAxisId="l" dataKey="revenue" name="매출(억)" fill="#38bdf8" radius={[4,4,0,0]} />
                <Line yAxisId="r" type="monotone" dataKey="profit" name="이익(억)" stroke="#fb7185" strokeWidth={2} dot={false} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </div>
      </div>

      <div className="semi-table-wrap">
        <table className="semi-excel-table" style={{minWidth:'860px'}}>
          <thead>
            <tr>
              <th style={thBase} colSpan={2}>종목 종합현황</th>
              <th style={thBase} colSpan={2}>실적 스냅샷</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td style={{...cellBase, background:'#eef2ff', fontWeight:800, color:'#1e293b'}}>기업명</td>
              <td style={{...cellBase, background:'#fff', textAlign:'right', fontWeight:700}}>{pickedStock?.name || '-'}</td>
              <td style={{...cellBase, background:'#eef2ff', fontWeight:800, color:'#1e293b'}}>연간 최신(매출/이익)</td>
              <td style={{...cellBase, background:'#fff', textAlign:'right', fontWeight:700}}>{latestAnnual ? `${fmtNum(latestAnnual.revenue)} / ${fmtNum(latestAnnual.profit)}` : '-'}</td>
            </tr>
            <tr>
              <td style={{...cellBase, background:'#eef2ff', fontWeight:800, color:'#1e293b'}}>카테고리</td>
              <td style={{...cellBase, background:'#fff', textAlign:'right', fontWeight:700}}>{`${pickedStock?.lv1 || '-'} / ${pickedStock?.lv2 || '-'}`}</td>
              <td style={{...cellBase, background:'#eef2ff', fontWeight:800, color:'#1e293b'}}>분기 최신(매출/이익)</td>
              <td style={{...cellBase, background:'#fff', textAlign:'right', fontWeight:700}}>{latestQuarter ? `${fmtNum(latestQuarter.revenue)} / ${fmtNum(latestQuarter.profit)}` : '-'}</td>
            </tr>
            <tr>
              <td style={{...cellBase, background:'#eef2ff', fontWeight:800, color:'#1e293b'}}>현재가(등락률)</td>
              <td style={{...cellBase, background:'#fff', textAlign:'right', fontWeight:700}}>{pickedStock ? `${fmtNum(pickedStock.close)} (${fmtPct(pickedStock.chg_pct, 1)})` : '-'}</td>
              <td style={{...cellBase, background:'#eef2ff', fontWeight:800, color:'#1e293b'}}>PBR / PER / PSR</td>
              <td style={{...cellBase, background:'#fff', textAlign:'right', fontWeight:700}}>{pickedStock ? `${pickedStock.pbr ?? '-'} / ${pickedStock.per ?? '-'} / ${pickedStock.psr ?? '-'}` : '-'}</td>
            </tr>
            <tr>
              <td style={{...cellBase, background:'#eef2ff', fontWeight:800, color:'#1e293b'}}>시가총액(억)</td>
              <td style={{...cellBase, background:'#fff', textAlign:'right', fontWeight:700}}>{pickedStock?.mktcap != null ? fmtNum(pickedStock.mktcap) : '-'}</td>
              <td style={{...cellBase, background:'#eef2ff', fontWeight:800, color:'#1e293b'}}>TTM 매출(억)</td>
              <td style={{...cellBase, background:'#fff', textAlign:'right', fontWeight:700}}>{pickedStock?.ttm_rev != null ? fmtNum(pickedStock.ttm_rev) : '-'}</td>
            </tr>
            <tr>
              <td style={{...cellBase, background:'#eef2ff', fontWeight:800, color:'#1e293b'}}>주요 고객</td>
              <td style={{...cellBase, background:'#fff', textAlign:'right', fontWeight:700}}>{pickedStock?.customer || '-'}</td>
              <td style={{...cellBase, background:'#eef2ff', fontWeight:800, color:'#1e293b'}}>주요 사업</td>
              <td style={{...cellBase, background:'#fff', textAlign:'right', fontWeight:700}}>{pickedStock?.main_biz || '-'}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
};

const perfLabel = (sectorPct, benchmarkPct) => {
  if (sectorPct == null || benchmarkPct == null || Number.isNaN(Number(sectorPct)) || Number.isNaN(Number(benchmarkPct))) return '';
  return Number(sectorPct) >= Number(benchmarkPct) ? '아웃퍼폼' : '언더퍼폼';
};

const perfStyle = (label) => ({
  ...cellBase,
  textAlign: 'center',
  background: label === '아웃퍼폼' ? 'rgba(239,68,68,0.16)' : label === '언더퍼폼' ? 'rgba(37,99,235,0.16)' : '#fff',
  color: label === '아웃퍼폼' ? '#b91c1c' : label === '언더퍼폼' ? '#1d4ed8' : '#0f172a',
  fontWeight: 900,
});

const OverviewTable = ({ stats, total, dates, benchmarks }) => {
  const kospi = benchmarks.find(b => b.key === 'kospi');
  const nasdaq = benchmarks.find(b => b.key === 'nasdaq');
  return (
  <div className="semi-card">
    <div className="semi-table-wrap">
      <table className="semi-excel-table">
        <thead>
          <tr>
            <th style={thBase}>No</th>
            <th style={thBase}>섹터</th>
            <th style={thBase}>주가 변동률(%)</th>
            <th style={thBase}>종목 수</th>
            <th style={thBase}>상승 종목 수</th>
            <th style={thBase}>%</th>
            <th style={thBase}>PSR 평균</th>
            {dates.map((d, i) => (
              <React.Fragment key={d}>
                <th style={thBase}>{d.slice(5)} 대비</th>
                <th style={thBase}>코스피 대비</th>
                <th style={thBase}>나스닥 대비</th>
              </React.Fragment>
            ))}
          </tr>
        </thead>
        <tbody>
          {stats.map(s => (
            <tr key={s.lv1}>
              <td style={{...cellBase,textAlign:'center',background:'#fff',fontWeight:700}}>{s.no}</td>
              <td style={{...cellBase,background:'#fff',fontWeight:900}}>{s.lv1}</td>
              <PctCell value={s.avgDay} />
              <MetricCell value={s.total} />
              <MetricCell value={s.rising} />
              <MetricCell value={s.risingPct} suffix="%" digits={0} />
              <MetricCell value={s.avgPsr} digits={2} />
              {s.avgRefs.map((v, i) => {
                const kLabel = perfLabel(v, kospi?.refs?.[i]?.chg_pct);
                const nLabel = perfLabel(v, nasdaq?.refs?.[i]?.chg_pct);
                return (
                  <React.Fragment key={i}>
                    <PctCell value={v} />
                    <td style={perfStyle(kLabel)}>{kLabel}</td>
                    <td style={perfStyle(nLabel)}>{nLabel}</td>
                  </React.Fragment>
                );
              })}
            </tr>
          ))}
          <tr>
            <td style={{...thBase}} colSpan={2}>평균</td>
            <td style={{...cellBase,background:'#fff'}} />
            <MetricCell value={total.total} />
            <MetricCell value={total.rising} />
            <MetricCell value={total.risingPct} suffix="%" digits={0} />
            <MetricCell value={total.avgPsr} digits={2} />
            {dates.map(d => (
              <React.Fragment key={d}>
                <td style={{...cellBase,background:'#fff'}} />
                <td style={{...cellBase,background:'#fff'}} />
                <td style={{...cellBase,background:'#fff'}} />
              </React.Fragment>
            ))}
          </tr>
        </tbody>
      </table>
    </div>
  </div>
  );
};

const AllStocksTables = ({ groupedStocks, dates }) => (
  <div style={{display:'flex',flexDirection:'column',gap:'0.9rem'}}>
    {groupedStocks.map(([lv1, rows]) => (
      <div key={lv1} className="semi-card">
        <h3 style={{margin:'0 0 0.55rem',fontSize:'0.95rem'}}>{lv1}</h3>
        <div className="semi-table-wrap">
          <table className="semi-excel-table" style={{minWidth:'1500px'}}>
            <thead>
              <tr>
                {['No','기업명','Lv1','Lv2','고객','주요업','주가','변동(%)','시총(억)','PBR','PER','완성','TTM(억)','PSR'].map(h => (
                  <th key={h} style={h === '주요업' ? {...thBase, minWidth:220} : thBase}>{h}</th>
                ))}
                {dates.map(d => <React.Fragment key={d}><th style={orangeTh}>{d.slice(5)}</th><th style={orangeTh}>%</th></React.Fragment>)}
              </tr>
            </thead>
            <tbody>
              {rows.map(s => (
                <tr key={s.code}>
                  <td style={{...cellBase,textAlign:'center',background:'#fff',fontWeight:700}}>{s.no}</td>
                  <td style={{...cellBase,background:'#fff'}}><span className="semi-name" title={s.main_biz}>{s.name}</span></td>
                  <td style={{...cellBase,background:'#fff'}}>{s.lv1 || ''}</td>
                  <td style={{...cellBase,background:'#fff'}}>{s.lv2 || ''}</td>
                  <td style={{...cellBase,background:'#fff'}}>{s.customer || ''}</td>
                  <td style={{...cellBase,background:'#fff',maxWidth:280,overflow:'hidden',textOverflow:'ellipsis'}} title={s.main_biz}>{s.main_biz || ''}</td>
                  <MetricCell value={s.close} />
                  <PctCell value={s.chg_pct} />
                  <MetricCell value={s.mktcap} />
                  <MetricCell value={s.pbr} digits={2} />
                  <MetricCell value={s.per} digits={2} />
                  <td style={{...cellBase,textAlign:'center',background:'#fff'}}>{s.close ? 'Y' : 'N'}</td>
                  <MetricCell value={s.ttm_rev} />
                  <td style={{...cellBase,textAlign:'right',background:'#fff',position:'relative',fontWeight:800}}>
                    <span style={blueCorner} />
                    {s.psr == null ? '' : Number(s.psr).toFixed(2)}
                    <Indicator value={s.psr} inverse />
                  </td>
                  {(s.refs || []).slice(0, 3).map((r, idx) => (
                    <React.Fragment key={idx}>
                      <MetricCell value={r.close} />
                      <PctCell value={r.chg_pct} />
                    </React.Fragment>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    ))}
  </div>
);

const FinancialTable = ({ rows, mode }) => {
  const sample = rows[0]?.data || {};
  const headers = Object.keys(sample);
  const title = mode === 'yoy' ? 'YoY 기준 실적' : 'QoQ 기준 실적';
  const metricKey = mode === 'yoy' ? 'yoy' : 'qoq';
  return (
    <div className="semi-card">
      <h3 style={{margin:'0 0 0.55rem',fontSize:'0.95rem'}}>{title}</h3>
      <div className="semi-table-wrap">
        <table className="semi-excel-table" style={{minWidth: mode === 'yoy' ? '980px' : '1700px'}}>
          <thead>
            <tr>
              <th style={darkTh}>No</th>
              <th style={darkTh}>기업명</th>
              <th style={darkTh}>업종</th>
              <th style={darkTh}>구분</th>
              {headers.map((h, i) => <th key={h} style={i >= headers.length - 4 ? orangeTh : thBase}>{h}</th>)}
              <th style={mode === 'yoy' ? thBase : orangeTh}>{mode === 'yoy' ? 'YoY(최신)' : 'QoQ(최신)'}</th>
              <th style={orangeTh}>Data 완성</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, idx) => {
              const metric = r[metricKey] == null ? null : Number(r[metricKey]) * 100;
              return (
                <tr key={`${r.name}-${r.type}-${idx}`}>
                  <td style={{...cellBase,textAlign:'center',background:'#fff',fontWeight:700}}>{idx + 1}</td>
                  <td style={{...cellBase,background:'#fff',fontWeight:900}}>{r.name}</td>
                  <td style={{...cellBase,background:'#fff'}}>{r.lv1}</td>
                  <td style={{...cellBase,background:'#fff'}}>{r.type === 'revenue' ? '매출' : '이익'}</td>
                  {headers.map(h => <MetricCell key={h} value={r.data?.[h]} />)}
                  <PctCell value={metric} />
                  <td style={{...cellBase,textAlign:'center',background:'#fff'}}>{Object.values(r.data || {}).filter(v => v != null).length >= headers.length - 1 ? 'Y' : 'N'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default SemiconductorSectorView;
