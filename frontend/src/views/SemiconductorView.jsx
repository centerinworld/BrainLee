import React from 'react';
import {
  ResponsiveContainer,
  ComposedChart,
  Bar,
  Line,
  LabelList,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
} from 'recharts';

const SemiconductorView = React.memo(() => {
  const [data, setData]       = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [sortKey, setSortKey] = React.useState('sort_order');
  const [sortDir, setSortDir] = React.useState('asc');
  const [filterLv1, setFilterLv1] = React.useState('ALL');
  const [search, setSearch]   = React.useState('');
  const [ref1, setRef1] = React.useState('2026-01-02');
  const [ref2, setRef2] = React.useState('2025-08-01');
  const [ref3, setRef3] = React.useState('2025-04-02');
  const [tab, setTab] = React.useState('details'); // details | summary | performance
  const [sumStart, setSumStart] = React.useState('2025-04-02');
  const [sumEnd, setSumEnd] = React.useState('');
  const [summary, setSummary] = React.useState(null);
  const [summaryError, setSummaryError] = React.useState('');
  const [annualRows, setAnnualRows] = React.useState([]);
  const [quarterRows, setQuarterRows] = React.useState([]);
  const [finLoading, setFinLoading] = React.useState(false);
  const [finDetail, setFinDetail] = React.useState(null);
  const [pickedCategory, setPickedCategory] = React.useState('');
  const [pickedCode, setPickedCode] = React.useState('');

  const loadData = React.useCallback(() => {
    setLoading(true);
    const params = new URLSearchParams({ ref1, ref2, ref3 });
    fetch(`/api/market-radar/semiconductor/valuestream?${params.toString()}`)
      .then(r => r.json())
      .then(d => {
        setData(d);
        if (Array.isArray(d?.ref_dates) && d.ref_dates.length === 3) {
          setRef1(d.ref_dates[0] || ref1);
          setRef2(d.ref_dates[1] || ref2);
          setRef3(d.ref_dates[2] || ref3);
        }
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [ref1, ref2, ref3]);

  React.useEffect(() => {
    loadData();
  }, [loadData]);

  const loadSummary = React.useCallback(() => {
    setSummaryError('');
    const params = new URLSearchParams();
    if (sumStart) params.set('start_date', sumStart);
    if (sumEnd) params.set('end_date', sumEnd);
    fetch(`/api/market-radar/semiconductor/summary?${params.toString()}`)
      .then(r => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(d => {
        setSummary(d);
        if (!sumEnd && d?.end_date) setSumEnd(d.end_date);
      })
      .catch(() => {
        setSummaryError('종합현황 데이터를 불러오지 못했습니다. 잠시 후 다시 시도해주세요.');
        setSummary({ rows: [], summary: null });
      });
  }, [sumStart, sumEnd]);

  React.useEffect(() => {
    if (tab === 'summary') loadSummary();
  }, [tab, loadSummary]);

  React.useEffect(() => {
    if (tab !== 'performance') return;
    if (annualRows.length || quarterRows.length) return;
    setFinLoading(true);
    Promise.allSettled([
      fetch('/api/market-radar/semiconductor/financials?type=annual').then(r => r.ok ? r.json() : []),
      fetch('/api/market-radar/semiconductor/financials?type=quarterly').then(r => r.ok ? r.json() : []),
    ]).then(([a, q]) => {
      setAnnualRows(Array.isArray(a.value) ? a.value : []);
      setQuarterRows(Array.isArray(q.value) ? q.value : []);
    }).finally(() => setFinLoading(false));
  }, [tab, annualRows.length, quarterRows.length]);

  const refDates = data?.ref_dates || [];
  const rows     = data?.rows || [];

  const categoryList = React.useMemo(() => {
    const s = new Set(rows.map(r => r.lv1).filter(Boolean));
    return Array.from(s);
  }, [rows]);
  React.useEffect(() => {
    if (!categoryList.length) return;
    if (!pickedCategory || !categoryList.includes(pickedCategory)) {
      setPickedCategory(categoryList[0]);
    }
  }, [categoryList, pickedCategory]);

  const categoryStocks = React.useMemo(
    () => rows.filter(r => (r.lv1 || '') === pickedCategory),
    [rows, pickedCategory],
  );
  React.useEffect(() => {
    if (!categoryStocks.length) return;
    const exists = categoryStocks.some(r => r.stock_code === pickedCode);
    if (!pickedCode || !exists) setPickedCode(categoryStocks[0].stock_code);
  }, [categoryStocks, pickedCode]);
  const pickedRow = React.useMemo(
    () => categoryStocks.find(r => r.stock_code === pickedCode) || null,
    [categoryStocks, pickedCode],
  );

  React.useEffect(() => {
    if (!pickedCode) return;
    fetch(`/api/market-radar/semiconductor/financial-detail?stock_code=${encodeURIComponent(pickedCode)}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => setFinDetail(d?.ok ? d : null))
      .catch(() => setFinDetail(null));
  }, [pickedCode]);

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
    if (search.trim())       filtered = filtered.filter(r =>
      (r.company_name||'').includes(search) || (r.lv1||'').includes(search) ||
      (r.main_business||'').includes(search)
    );
    return [...filtered].sort((a, b) => {
      let va = a[sortKey], vb = b[sortKey];
      if (sortKey.startsWith('__ref_')) {
        const rd = sortKey.replace('__ref_', '');
        va = a?.ref_chgs?.[rd];
        vb = b?.ref_chgs?.[rd];
      }
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      const res = typeof va === 'string' ? va.localeCompare(vb) : va - vb;
      return sortDir === 'asc' ? res : -res;
    });
  }, [rows, filterLv1, search, sortKey, sortDir]);

  // 색상 헬퍼
  const chgColor = (v) => v == null ? '#888' : v > 0 ? '#f87171' : v < 0 ? '#60a5fa' : '#888';
  const fmtPct = (v) => v == null ? '-' : (v > 0 ? '+' : '') + Math.round(v).toLocaleString('ko-KR') + '%';
  const fmtPrc = (v) => v == null ? '-' : Math.round(v).toLocaleString('ko-KR');
  const fmtMkt = (v) => v == null ? '-' : (v >= 10000 ? Math.round(v/10000).toLocaleString('ko-KR')+'조' : Math.round(v).toLocaleString('ko-KR')+'억');
  const fmtEok = (v) => v == null ? '-' : Math.round(v).toLocaleString('ko-KR') + '억';
  const fmt1 = (v) => v == null ? '-' : Number(v).toLocaleString('ko-KR', {minimumFractionDigits:1, maximumFractionDigits:1});
  const perfBadge = (v) => {
    const out = v === 'outperform';
    return (
      <span style={{
        display:'inline-block', padding:'2px 8px', borderRadius:'999px',
        border:`1px solid ${out ? 'rgba(16,185,129,0.5)' : 'rgba(239,68,68,0.5)'}`,
        background: out ? 'rgba(16,185,129,0.15)' : 'rgba(239,68,68,0.15)',
        color: out ? '#34d399' : '#f87171', fontWeight:700, fontSize:'0.72rem'
      }}>
        {out ? '🟢 아웃퍼폼' : '🔴 언더퍼폼'}
      </span>
    );
  };

  const formatRefLabel = (d) => {
    const [y, m, day] = (d || '').split('-');
    if (!y || !m || !day) return `${d || ''} 대비`;
    return `'${y.slice(2)}.${m}.${day}일 대비`;
  };

  const groupMeta = React.useMemo(() => {
    const meta = [];
    let i = 0;
    while (i < sortedRows.length) {
      const lv1 = sortedRows[i].lv1 || '-';
      let j = i + 1;
      while (j < sortedRows.length && (sortedRows[j].lv1 || '-') === lv1) j++;
      meta.push({ start: i, end: j - 1, rowSpan: j - i, lv1 });
      i = j;
    }
    const firstIndexMap = new Map();
    meta.forEach(g => firstIndexMap.set(g.start, g));
    return { firstIndexMap };
  }, [sortedRows]);

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

  const parsePeriodOrder = (label, quarterly = false) => {
    if (!label) return -1;
    if (!quarterly) {
      const y = Number(String(label).replace(/[^\d]/g, ''));
      return Number.isFinite(y) ? y : -1;
    }
    const m = String(label).match(/(\d+)\.(\d)Q/i);
    if (!m) return -1;
    return Number(m[1]) * 10 + Number(m[2]);
  };
  const buildSeries = (finRows, stockName, quarterly = false) => {
    const rev = finRows.find(r => r.name === stockName && r.type === 'revenue');
    const prof = finRows.find(r => r.name === stockName && r.type === 'profit');
    const keys = Array.from(new Set([
      ...Object.keys(rev?.data || {}),
      ...Object.keys(prof?.data || {}),
    ])).sort((a, b) => parsePeriodOrder(a, quarterly) - parsePeriodOrder(b, quarterly));
    return keys.map(k => ({
      period: k,
      revenue: rev?.data?.[k] ?? null,
      profit: prof?.data?.[k] ?? null,
    })).filter(r => r.revenue != null || r.profit != null);
  };
  const annualSeries = React.useMemo(() => {
    const base = pickedRow ? buildSeries(annualRows, pickedRow.company_name, false) : [];
    return base.map(r => ({
      ...r,
      margin_pct: (r.revenue && r.profit != null) ? Number((r.profit / r.revenue * 100).toFixed(1)) : null,
    }));
  }, [annualRows, pickedRow]);
  const quarterSeries = React.useMemo(() => {
    const base = pickedRow ? buildSeries(quarterRows, pickedRow.company_name, true) : [];
    return base.map(r => ({
      ...r,
      margin_pct: (r.revenue && r.profit != null) ? Number((r.profit / r.revenue * 100).toFixed(1)) : null,
    }));
  }, [quarterRows, pickedRow]);
  const latestAnnual = annualSeries.length ? annualSeries[annualSeries.length - 1] : null;
  const quarterSeriesSimple = React.useMemo(() => quarterSeries.slice(-12), [quarterSeries]);
  const latestQuarter = quarterSeriesSimple.length ? quarterSeriesSimple[quarterSeriesSimple.length - 1] : null;
  const summaryView = React.useMemo(() => {
    if (summary?.rows?.length || summary?.summary) return summary;
    if (!rows.length) return { rows: [], summary: null };
    const startKey = refDates.find(d => d === sumStart) || refDates[0];
    const by = new Map();
    rows.forEach(r => {
      const key = r.lv1 || '기타';
      if (!by.has(key)) by.set(key, { sector:key, stock_count:0, up_count:0, psr_sum:0, psr_n:0, chg_sum:0, chg_n:0 });
      const g = by.get(key);
      g.stock_count += 1;
      if ((r.day_change_pct || 0) > 0) g.up_count += 1;
      if (r.psr != null) { g.psr_sum += Number(r.psr); g.psr_n += 1; }
      const c = r.ref_chgs?.[startKey];
      if (c != null) { g.chg_sum += Number(c); g.chg_n += 1; }
    });
    const outRows = Array.from(by.values()).map((g, i) => ({
      no: i + 1,
      sector: g.sector,
      avg_change_pct: g.chg_n ? Math.round((g.chg_sum / g.chg_n) * 100) / 100 : null,
      stock_count: g.stock_count,
      up_count: g.up_count,
      up_ratio_pct: g.stock_count ? Math.round((g.up_count / g.stock_count) * 1000) / 10 : 0,
      avg_psr: g.psr_n ? Math.round((g.psr_sum / g.psr_n) * 100) / 100 : null,
      vs_kospi: 'underperform',
      vs_nasdaq: 'underperform',
    }));
    const total = outRows.reduce((a, r) => ({
      stock_count: a.stock_count + (r.stock_count || 0),
      up_count: a.up_count + (r.up_count || 0),
      avg_change_sum: a.avg_change_sum + ((r.avg_change_pct || 0) * (r.stock_count || 0)),
      avg_psr_sum: a.avg_psr_sum + ((r.avg_psr || 0) * (r.stock_count || 0)),
    }), { stock_count:0, up_count:0, avg_change_sum:0, avg_psr_sum:0 });
    return {
      summary: {
        avg_change_pct: total.stock_count ? Math.round((total.avg_change_sum / total.stock_count) * 100) / 100 : null,
        stock_count: total.stock_count,
        up_count: total.up_count,
        up_ratio_pct: total.stock_count ? Math.round((total.up_count / total.stock_count) * 1000) / 10 : 0,
        avg_psr: total.stock_count ? Math.round((total.avg_psr_sum / total.stock_count) * 100) / 100 : null,
        vs_kospi: 'underperform',
        vs_nasdaq: 'underperform',
      },
      rows: outRows,
      kospi_change_pct: null,
      nasdaq_change_pct: null,
    };
  }, [summary, rows, refDates, sumStart]);

  // 종합현황 표시값: 오늘 기준(전일 대비) 섹터 평균 변동률/상승종목수
  const summaryDisplay = React.useMemo(() => {
    const by = new Map();
    rows.forEach(r => {
      const key = r.lv1 || '기타';
      if (!by.has(key)) by.set(key, {
        sector: key, stock_count: 0, up_count: 0, day_sum: 0, day_n: 0,
        psr_sum: 0, psr_n: 0, pbr_sum: 0, pbr_n: 0, per_sum: 0, per_n: 0,
      });
      const g = by.get(key);
      g.stock_count += 1;
      const day = r.day_change_pct;
      if (day != null) {
        g.day_sum += Number(day);
        g.day_n += 1;
        if (Number(day) > 0) g.up_count += 1;
      }
      if (r.psr != null) {
        g.psr_sum += Number(r.psr);
        g.psr_n += 1;
      }
      if (r.pbr != null) {
        g.pbr_sum += Number(r.pbr);
        g.pbr_n += 1;
      }
      if (r.per != null) {
        g.per_sum += Number(r.per);
        g.per_n += 1;
      }
    });
    const rowsToday = Array.from(by.values()).map((g, i) => {
      const apiRow = (summaryView?.rows || []).find(x => x.sector === g.sector);
      return {
        no: i + 2,
        sector: g.sector,
        avg_change_pct: g.day_n ? Number((g.day_sum / g.day_n).toFixed(2)) : null,
        stock_count: g.stock_count,
        up_count: g.up_count,
        up_ratio_pct: g.stock_count ? Number(((g.up_count / g.stock_count) * 100).toFixed(1)) : 0,
        avg_psr: g.psr_n ? Number((g.psr_sum / g.psr_n).toFixed(1)) : null,
        avg_pbr: g.pbr_n ? Number((g.pbr_sum / g.pbr_n).toFixed(1)) : null,
        avg_per: g.per_n ? Number((g.per_sum / g.per_n).toFixed(1)) : null,
        vs_kospi: apiRow?.vs_kospi || 'underperform',
        vs_nasdaq: apiRow?.vs_nasdaq || 'underperform',
      };
    }).sort((a,b)=>a.sector.localeCompare(b.sector,'ko'));

    const total = rowsToday.reduce((a, r) => ({
      stock_count: a.stock_count + r.stock_count,
      up_count: a.up_count + r.up_count,
      day_sum: a.day_sum + ((r.avg_change_pct || 0) * r.stock_count),
      psr_sum: a.psr_sum + ((r.avg_psr || 0) * r.stock_count),
      pbr_sum: a.pbr_sum + ((r.avg_pbr || 0) * r.stock_count),
      per_sum: a.per_sum + ((r.avg_per || 0) * r.stock_count),
    }), {stock_count:0, up_count:0, day_sum:0, psr_sum:0, pbr_sum:0, per_sum:0});

    return {
      summary: {
        avg_change_pct: total.stock_count ? Number((total.day_sum / total.stock_count).toFixed(2)) : null,
        stock_count: total.stock_count,
        up_count: total.up_count,
        up_ratio_pct: total.stock_count ? Number(((total.up_count / total.stock_count) * 100).toFixed(1)) : 0,
        avg_psr: total.stock_count ? Number((total.psr_sum / total.stock_count).toFixed(1)) : null,
        avg_pbr: total.stock_count ? Number((total.pbr_sum / total.stock_count).toFixed(1)) : null,
        avg_per: total.stock_count ? Number((total.per_sum / total.stock_count).toFixed(1)) : null,
        vs_kospi: summaryView?.summary?.vs_kospi || 'underperform',
        vs_nasdaq: summaryView?.summary?.vs_nasdaq || 'underperform',
      },
      rows: rowsToday,
      kospi_change_pct: summaryView?.kospi_change_pct ?? null,
      nasdaq_change_pct: summaryView?.nasdaq_change_pct ?? null,
    };
  }, [rows, summaryView]);

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
          <button onClick={() => setTab('details')} style={{padding:'4px 10px', borderRadius:'8px', border: tab==='details'?'1px solid #22d3ee':'1px solid rgba(148,163,184,0.35)', background: tab==='details'?'rgba(34,211,238,0.14)':'rgba(255,255,255,0.04)', color: tab==='details'?'#67e8f9':'#cbd5e1', fontWeight:700, cursor:'pointer'}}>전종목</button>
          <button onClick={() => setTab('summary')} style={{padding:'4px 10px', borderRadius:'8px', border: tab==='summary'?'1px solid #22d3ee':'1px solid rgba(148,163,184,0.35)', background: tab==='summary'?'rgba(34,211,238,0.14)':'rgba(255,255,255,0.04)', color: tab==='summary'?'#67e8f9':'#cbd5e1', fontWeight:700, cursor:'pointer'}}>종합현황</button>
          <button onClick={() => setTab('performance')} style={{padding:'4px 10px', borderRadius:'8px', border: tab==='performance'?'1px solid #22d3ee':'1px solid rgba(148,163,184,0.35)', background: tab==='performance'?'rgba(34,211,238,0.14)':'rgba(255,255,255,0.04)', color: tab==='performance'?'#67e8f9':'#cbd5e1', fontWeight:700, cursor:'pointer'}}>종목실적</button>
          {data && tab==='details' && (
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
      ) : tab === 'details' ? (
        <div style={{overflowX:'auto', overflowY:'clip'}}>
          <table style={{width:'100%', borderCollapse:'collapse', fontSize:'0.78rem'}}>
            <thead>
              <tr>
                <th style={{...thSt, textAlign:'center', width:'32px'}} onClick={() => handleSort('sort_order')}>#</th>
                <th style={{...thSt, textAlign:'left', minWidth:'90px'}}>카테고리</th>
                <th style={{...thSt, textAlign:'left', minWidth:'100px'}} onClick={() => handleSort('company_name')}>기업명{sortInd('company_name')}</th>
                <th style={{...thSt, textAlign:'left', maxWidth:'200px', borderRight:'1px solid rgba(148,163,184,0.35)'}}>주요사업</th>
                <th style={{...thSt, textAlign:'right'}} onClick={() => handleSort('price')}>현재가{sortInd('price')}</th>
                <th style={{...thSt, textAlign:'right', borderRight:'1px solid rgba(148,163,184,0.35)'}} onClick={() => handleSort('market_cap')}>시총{sortInd('market_cap')}</th>
                <th style={{...thSt, textAlign:'right'}} onClick={() => handleSort('etf_amount')}>ETF편입금액(시총비중){sortInd('etf_amount')}</th>
                <th style={{...thSt, textAlign:'right', borderLeft:'1px solid rgba(148,163,184,0.35)'}} onClick={() => handleSort('pbr')}>PBR{sortInd('pbr')}</th>
                <th style={{...thSt, textAlign:'right'}} onClick={() => handleSort('per')}>PER{sortInd('per')}</th>
                <th style={{...thSt, textAlign:'right', borderRight:'1px solid rgba(148,163,184,0.35)'}} onClick={() => handleSort('psr')}>PSR{sortInd('psr')}</th>
                {refDates.map(rd => (
                  <th key={rd} style={{...thSt, textAlign:'right'}} onClick={() => handleSort(`__ref_${rd}`)}>
                    {formatRefLabel(rd)}{sortInd(`__ref_${rd}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sortedRows.map((r, idx) => {
                return (
                  <tr key={r.stock_code || idx}
                      onMouseOver={e => e.currentTarget.style.background='rgba(255,255,255,0.04)'}
                      onMouseOut={e  => e.currentTarget.style.background='transparent'}>
                    <td style={{...tdSt, textAlign:'center', color:'rgba(255,255,255,0.35)', fontSize:'0.7rem'}}>
                      {r.sort_order}
                    </td>
                    {/* LV1 배지 */}
                    {groupMeta.firstIndexMap.get(idx) && (
                      <td rowSpan={groupMeta.firstIndexMap.get(idx).rowSpan} style={{...tdSt, textAlign:'left', verticalAlign:'top'}}>
                        <span style={{
                          display:'inline-block', padding:'0.1rem 0.4rem', borderRadius:'4px',
                          fontSize:'0.68rem', fontWeight:600, whiteSpace:'nowrap',
                          color: LV1_COLORS[r.lv1] || '#94a3b8',
                          background: `${LV1_COLORS[r.lv1] || '#94a3b8'}18`,
                          border: `1px solid ${LV1_COLORS[r.lv1] || '#94a3b8'}40`,
                        }}>
                          {r.lv1 || '-'}
                        </span>
                      </td>
                    )}
                    {/* 기업명 (tooltip: 고객·주요업) */}
                    <td style={{...tdSt, textAlign:'left', fontWeight:600}}
                        title={[r.customers && `고객: ${r.customers}`, r.main_business].filter(Boolean).join('\n')}>
                      <div style={{display:'flex', alignItems:'center', gap:'4px'}}>
                        <span style={{cursor: r.main_business ? 'help' : 'default'}}>{r.company_name}</span>
                      </div>
                      {r.main_business && (
                        <div style={{fontSize:'0.65rem', color:'rgba(255,255,255,0.4)', marginTop:'1px',
                          maxWidth:'130px', overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap'}}>
                          {r.main_business}
                        </div>
                      )}
                    </td>
                    {/* 주요사업 (짧게) */}
                    <td style={{...tdSt, textAlign:'left', color:'rgba(255,255,255,0.45)', maxWidth:'180px', borderRight:'1px solid rgba(148,163,184,0.25)',
                      overflow:'hidden', textOverflow:'ellipsis'}}>
                      {r.customers || '-'}
                    </td>
                    {/* 가격/시총/밸류 */}
                    <td style={{...tdSt, textAlign:'right', fontFamily:'monospace', fontWeight:600}}>
                      {fmtPrc(r.price)} <span style={{color: chgColor(r.day_change_pct)}}>({fmtPct(r.day_change_pct)})</span>
                    </td>
                    <td style={{...tdSt, textAlign:'right', color:'#94a3b8', borderRight:'1px solid rgba(148,163,184,0.25)'}}>
                      {fmtMkt(r.market_cap)}
                    </td>
                    <td style={{...tdSt, textAlign:'right'}}>
                      {r.etf_amount ? `${fmtEok(r.etf_amount)} (${fmtPct(r.etf_ratio_pct)})` : '-'}
                    </td>
                    <td style={{...tdSt, textAlign:'right'}}>
                      {fmt1(r.pbr)}
                    </td>
                    <td style={{...tdSt, textAlign:'right'}}>
                      {fmt1(r.per)}
                    </td>
                    <td style={{...tdSt, textAlign:'right', borderRight:'1px solid rgba(148,163,184,0.25)'}}>
                      {fmt1(r.psr)}
                    </td>
                    {/* 기준일 가격 + 변동률 */}
                    {refDates.map(rd => (
                      <td key={rd} style={{...tdSt, textAlign:'right', fontFamily:'monospace', fontWeight:600,
                        color: chgColor(r.ref_chgs?.[rd])}}>
                        {fmtPrc(r.ref_prices?.[rd])} ({fmtPct(r.ref_chgs?.[rd])})
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>

          {/* 기준일 수정 — 테이블 최하단 */}
          <div style={{
            marginTop: '1rem', padding: '0.7rem 1rem',
            background: 'rgba(30,41,59,0.6)', borderRadius: '8px',
            border: '1px solid rgba(255,255,255,0.08)',
            fontSize: '0.74rem', color: 'rgba(255,255,255,0.5)',
            display: 'flex', gap: '1.5rem', flexWrap: 'wrap',
          }}>
            <span style={{fontWeight:600, color:'rgba(255,255,255,0.7)'}}>📅 기준일</span>
            <label style={{display:'flex', alignItems:'center', gap:'6px'}}>기준1:
              <input type="date" value={ref1} onChange={e => setRef1(e.target.value)} style={{background:'#0f172a', color:'#e2e8f0', border:'1px solid rgba(148,163,184,0.3)', borderRadius:'6px', padding:'2px 6px'}} />
            </label>
            <label style={{display:'flex', alignItems:'center', gap:'6px'}}>기준2:
              <input type="date" value={ref2} onChange={e => setRef2(e.target.value)} style={{background:'#0f172a', color:'#e2e8f0', border:'1px solid rgba(148,163,184,0.3)', borderRadius:'6px', padding:'2px 6px'}} />
            </label>
            <label style={{display:'flex', alignItems:'center', gap:'6px'}}>기준3:
              <input type="date" value={ref3} onChange={e => setRef3(e.target.value)} style={{background:'#0f172a', color:'#e2e8f0', border:'1px solid rgba(148,163,184,0.3)', borderRadius:'6px', padding:'2px 6px'}} />
            </label>
            <button onClick={loadData} style={{padding:'4px 10px', borderRadius:'6px', border:'1px solid rgba(96,165,250,0.45)', background:'rgba(37,99,235,0.18)', color:'#bfdbfe', cursor:'pointer'}}>적용</button>
            <span style={{color:'rgba(255,255,255,0.3)'}}>
              변동률 = (현재가 − 기준일가) / 기준일가 × 100
            </span>
          </div>
        </div>
      ) : tab === 'summary' ? (
        <div style={{padding:'0.5rem 0.1rem'}}>
          {summaryError && (
            <div style={{marginBottom:'0.55rem', color:'#f87171', fontSize:'0.78rem'}}>{summaryError}</div>
          )}
          <div style={{fontSize:'0.75rem', color:'#93c5fd', marginBottom:'10px'}}>
            코스피 {summaryDisplay.kospi_change_pct != null ? `${summaryDisplay.kospi_change_pct}%` : '-'} · 나스닥 {summaryDisplay.nasdaq_change_pct != null ? `${summaryDisplay.nasdaq_change_pct}%` : '-'}
          </div>
          <div style={{overflowX:'auto'}}>
            <table style={{width:'100%', borderCollapse:'collapse', fontSize:'0.8rem', minWidth:'1180px'}}>
              <thead>
                <tr>
                  {['No','섹터','주가 변동률(%)','종목 수','상승 종목 수','PBR평균','PER평균','PSR평균','코스피 대비','나스닥 대비'].map((h)=>(
                    <th key={h} style={{padding:'8px', border:'1px solid rgba(148,163,184,0.35)', background:'rgba(15,23,42,0.95)', color:'#e2e8f0', fontWeight:700, textAlign:'center'}}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {summaryDisplay?.summary && (
                  <tr style={{background:'rgba(34,197,94,0.08)', cursor:'pointer'}}
                      onClick={() => { setTab('details'); setFilterLv1('ALL'); }}>
                    <td style={{padding:'8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'center', fontWeight:700}}>1</td>
                    <td style={{padding:'8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'center', fontWeight:700}}>전체</td>
                    <td style={{padding:'8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'right', color:chgColor(summaryDisplay.summary.avg_change_pct), fontWeight:700}}>{summaryDisplay.summary.avg_change_pct != null ? `${summaryDisplay.summary.avg_change_pct}%` : '-'}</td>
                    <td style={{padding:'8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'right'}}>{summaryDisplay.summary.stock_count?.toLocaleString('ko-KR') || '-'}</td>
                    <td style={{padding:'8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'right'}}>{summaryDisplay.summary.up_count?.toLocaleString('ko-KR') || 0} ({summaryDisplay.summary.up_ratio_pct || 0}%)</td>
                    <td style={{padding:'8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'right'}}>{summaryDisplay.summary.avg_pbr != null ? summaryDisplay.summary.avg_pbr.toLocaleString('ko-KR') : '-'}</td>
                    <td style={{padding:'8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'right'}}>{summaryDisplay.summary.avg_per != null ? summaryDisplay.summary.avg_per.toLocaleString('ko-KR') : '-'}</td>
                    <td style={{padding:'8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'right'}}>{summaryDisplay.summary.avg_psr != null ? summaryDisplay.summary.avg_psr.toLocaleString('ko-KR') : '-'}</td>
                    <td style={{padding:'8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'center'}}>{perfBadge(summaryDisplay.summary.vs_kospi)}</td>
                    <td style={{padding:'8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'center'}}>{perfBadge(summaryDisplay.summary.vs_nasdaq)}</td>
                  </tr>
                )}
                {(summaryDisplay?.rows || []).map((r, i) => (
                  <tr key={r.sector} style={{cursor:'pointer'}}
                      onClick={() => { setTab('details'); setFilterLv1(r.sector || 'ALL'); }}>
                    <td style={{padding:'8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'center'}}>{i+2}</td>
                    <td style={{padding:'8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'center', fontWeight:600}}>{r.sector}</td>
                    <td style={{padding:'8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'right', color:chgColor(r.avg_change_pct), fontWeight:700}}>{r.avg_change_pct != null ? `${r.avg_change_pct}%` : '-'}</td>
                    <td style={{padding:'8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'right'}}>{r.stock_count?.toLocaleString('ko-KR') || '-'}</td>
                    <td style={{padding:'8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'right'}}>{r.up_count?.toLocaleString('ko-KR') || 0} ({r.up_ratio_pct || 0}%)</td>
                    <td style={{padding:'8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'right'}}>{r.avg_pbr != null ? r.avg_pbr.toLocaleString('ko-KR') : '-'}</td>
                    <td style={{padding:'8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'right'}}>{r.avg_per != null ? r.avg_per.toLocaleString('ko-KR') : '-'}</td>
                    <td style={{padding:'8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'right'}}>{r.avg_psr != null ? r.avg_psr.toLocaleString('ko-KR') : '-'}</td>
                    <td style={{padding:'8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'center'}}>{perfBadge(r.vs_kospi)}</td>
                    <td style={{padding:'8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'center'}}>{perfBadge(r.vs_nasdaq)}</td>
                  </tr>
                ))}
                {(!summaryDisplay?.summary && (!summaryDisplay?.rows || summaryDisplay.rows.length === 0)) && (
                  <tr>
                    <td colSpan={10} style={{padding:'14px', textAlign:'center', color:'rgba(255,255,255,0.5)', border:'1px solid rgba(148,163,184,0.25)'}}>
                      표시할 종합현황 데이터가 없습니다.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <div style={{display:'flex', alignItems:'center', gap:'10px', flexWrap:'wrap', marginTop:'16px'}}>
            <label style={{color:'#cbd5e1', fontSize:'0.8rem'}}>시작일
              <input type="date" value={sumStart} onChange={e=>setSumStart(e.target.value)} style={{marginLeft:'6px', background:'#0f172a', color:'#e2e8f0', border:'1px solid rgba(148,163,184,0.35)', borderRadius:'6px', padding:'2px 6px'}} />
            </label>
            <label style={{color:'#cbd5e1', fontSize:'0.8rem'}}>종료일
              <input type="date" value={sumEnd} onChange={e=>setSumEnd(e.target.value)} style={{marginLeft:'6px', background:'#0f172a', color:'#e2e8f0', border:'1px solid rgba(148,163,184,0.35)', borderRadius:'6px', padding:'2px 6px'}} />
            </label>
            <button onClick={loadSummary} style={{padding:'4px 10px', borderRadius:'6px', border:'1px solid rgba(96,165,250,0.45)', background:'rgba(37,99,235,0.18)', color:'#bfdbfe', cursor:'pointer'}}>적용</button>
            <span style={{fontSize:'0.74rem', color:'rgba(148,163,184,0.9)'}}>시작일/종료일은 코스피·나스닥 대비(언더/아웃퍼폼) 판정 기준입니다.</span>
          </div>
        </div>
      ) : (
        <div style={{padding:'0.5rem 0.1rem', display:'flex', flexDirection:'column', gap:'0.8rem'}}>
          <div style={{display:'flex', alignItems:'center', justifyContent:'flex-start', gap:'0.7rem', flexWrap:'wrap'}}>
            <div style={{display:'flex', gap:'0.4rem', alignItems:'center', flexWrap:'wrap'}}>
              <span style={{fontSize:'0.8rem', color:'#cbd5e1', fontWeight:700}}>카테고리</span>
              {categoryList.map(cat => (
                <button key={cat} onClick={() => setPickedCategory(cat)}
                  style={{
                    padding:'3px 9px', borderRadius:'999px', cursor:'pointer',
                    border: pickedCategory===cat?'1px solid #22d3ee':'1px solid rgba(148,163,184,0.35)',
                    background: pickedCategory===cat?'rgba(34,211,238,0.15)':'rgba(255,255,255,0.04)',
                    color: pickedCategory===cat?'#67e8f9':'#cbd5e1', fontWeight:700, fontSize:'0.72rem',
                  }}>{cat}</button>
              ))}
            </div>
            <div style={{display:'flex', alignItems:'center', gap:'0.5rem'}}>
              <span style={{fontSize:'0.8rem', color:'#cbd5e1', fontWeight:700}}>기업</span>
              <select value={pickedCode} onChange={e => setPickedCode(e.target.value)}
                style={{padding:'0.35rem 0.55rem', borderRadius:'8px', background:'rgba(15,23,42,0.9)', border:'1px solid rgba(148,163,184,0.35)', color:'#e2e8f0'}}>
                {categoryStocks.map(r => (
                  <option key={r.stock_code} value={r.stock_code}>{r.company_name} ({r.stock_code})</option>
                ))}
              </select>
            </div>
          </div>

          {finLoading ? (
            <div style={{padding:'1.6rem', textAlign:'center', color:'rgba(255,255,255,0.45)'}}>실적 데이터 로딩 중...</div>
          ) : (
            <>
              <div style={{display:'grid', gridTemplateColumns:'repeat(auto-fit,minmax(360px,1fr))', gap:'0.8rem'}}>
                <div style={{border:'1px solid rgba(148,163,184,0.2)', borderRadius:'10px', background:'rgba(15,23,42,0.8)', padding:'0.6rem'}}>
                  <h4 style={{margin:'0 0 0.4rem', fontSize:'0.84rem'}}>연간 실적 (매출: 막대 / 이익: 선)</h4>
                  <div style={{height:'260px'}}>
                    <ResponsiveContainer width="100%" height="100%">
                      <ComposedChart data={annualSeries}>
                        <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.2)" />
                        <XAxis dataKey="period" tick={{fill:'#cbd5e1', fontSize:11}} />
                        <YAxis yAxisId="l" tick={{fill:'#94a3b8', fontSize:11}} />
                        <YAxis yAxisId="r" orientation="right" tick={{fill:'#94a3b8', fontSize:11}} tickFormatter={(v)=>`${v}%`} />
                        <Tooltip formatter={(v, n)=> n==='이익률(%)' ? `${v}%` : Number(v).toLocaleString('ko-KR')} />
                        <Legend />
                        <Bar yAxisId="l" dataKey="revenue" fill="#22d3ee" radius={[4,4,0,0]}>
                          <LabelList dataKey="revenue" position="top" formatter={(v)=>v==null?'':Number(v).toLocaleString('ko-KR')} style={{fill:'#7dd3fc', fontSize:10}} />
                        </Bar>
                        <Line yAxisId="r" type="monotone" dataKey="margin_pct" name="이익률(%)" stroke="#fb7185" strokeWidth={2}>
                          <LabelList dataKey="margin_pct" position="top" formatter={(v)=>v==null?'':`${v}%`} style={{fill:'#fda4af', fontSize:10}} />
                        </Line>
                      </ComposedChart>
                    </ResponsiveContainer>
                  </div>
                </div>
                <div style={{border:'1px solid rgba(148,163,184,0.2)', borderRadius:'10px', background:'rgba(15,23,42,0.8)', padding:'0.6rem'}}>
                  <h4 style={{margin:'0 0 0.4rem', fontSize:'0.84rem'}}>분기 실적 (매출: 막대 / 이익: 선)</h4>
                  <div style={{height:'260px'}}>
                    <ResponsiveContainer width="100%" height="100%">
                      <ComposedChart data={quarterSeriesSimple}>
                        <CartesianGrid strokeDasharray="3 3" stroke="rgba(148,163,184,0.2)" />
                        <XAxis dataKey="period" tick={{fill:'#cbd5e1', fontSize:11}} />
                        <YAxis yAxisId="l" tick={{fill:'#94a3b8', fontSize:11}} />
                        <YAxis yAxisId="r" orientation="right" tick={{fill:'#94a3b8', fontSize:11}} tickFormatter={(v)=>`${v}%`} />
                        <Tooltip formatter={(v, n)=> n==='이익률(%)' ? `${v}%` : Number(v).toLocaleString('ko-KR')} />
                        <Legend />
                        <Bar yAxisId="l" dataKey="revenue" fill="#38bdf8" radius={[4,4,0,0]}>
                          <LabelList dataKey="revenue" position="top" formatter={(v)=>v==null?'':Number(v).toLocaleString('ko-KR')} style={{fill:'#7dd3fc', fontSize:9}} />
                        </Bar>
                        <Line yAxisId="r" type="monotone" dataKey="margin_pct" name="이익률(%)" stroke="#f97316" strokeWidth={2} dot={false} />
                      </ComposedChart>
                    </ResponsiveContainer>
                  </div>
                  <div style={{fontSize:'0.72rem', color:'rgba(148,163,184,0.9)', marginTop:'0.35rem'}}>최근 12개 분기만 표시</div>
                </div>
              </div>

              <div style={{overflowX:'auto'}}>
                <table style={{width:'100%', borderCollapse:'collapse', fontSize:'0.8rem', minWidth:'760px'}}>
                  <thead>
                    <tr>
                      <th style={{padding:'8px', border:'1px solid rgba(148,163,184,0.35)', background:'rgba(15,23,42,0.95)', color:'#e2e8f0'}}>항목</th>
                      <th style={{padding:'8px', border:'1px solid rgba(148,163,184,0.35)', background:'rgba(15,23,42,0.95)', color:'#e2e8f0'}}>값</th>
                      <th style={{padding:'8px', border:'1px solid rgba(148,163,184,0.35)', background:'rgba(15,23,42,0.95)', color:'#e2e8f0'}}>항목</th>
                      <th style={{padding:'8px', border:'1px solid rgba(148,163,184,0.35)', background:'rgba(15,23,42,0.95)', color:'#e2e8f0'}}>값</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      ['기업명', pickedRow?.company_name || '-', '카테고리', `${pickedRow?.lv1 || '-'} / ${pickedRow?.lv2 || '-'}`],
                      ['현재가(등락)', pickedRow ? `${fmtPrc(pickedRow.price)} (${fmtPct(pickedRow.day_change_pct)})` : '-', '시가총액', pickedRow ? fmtMkt(pickedRow.market_cap) : '-'],
                      ['PBR / PER / PSR', pickedRow ? `${fmt1(pickedRow.pbr)} / ${fmt1(pickedRow.per)} / ${fmt1(pickedRow.psr)}` : '-', 'ETF편입금액', pickedRow?.etf_amount ? `${fmtEok(pickedRow.etf_amount)} (${fmtPct(pickedRow.etf_ratio_pct)})` : '-'],
                      ['연간 최신(매출/이익)', latestAnnual ? `${fmtEok(latestAnnual.revenue)} / ${fmtEok(latestAnnual.profit)}` : '-', '분기 최신(매출/이익)', latestQuarter ? `${fmtEok(latestQuarter.revenue)} / ${fmtEok(latestQuarter.profit)}` : '-'],
                      ['주요 고객', pickedRow?.customers || '-', '주요 사업', pickedRow?.main_business || '-'],
                    ].map((r, i) => (
                      <tr key={i}>
                        <td style={{padding:'7px 8px', border:'1px solid rgba(148,163,184,0.25)', background:'rgba(99,102,241,0.12)', color:'#c7d2fe', fontWeight:700}}>{r[0]}</td>
                        <td style={{padding:'7px 8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'right', fontWeight:600}}>{r[1]}</td>
                        <td style={{padding:'7px 8px', border:'1px solid rgba(148,163,184,0.25)', background:'rgba(99,102,241,0.12)', color:'#c7d2fe', fontWeight:700}}>{r[2]}</td>
                        <td style={{padding:'7px 8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'right', fontWeight:600}}>{r[3]}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div style={{overflowX:'auto'}}>
                <table style={{width:'100%', borderCollapse:'collapse', fontSize:'0.78rem', minWidth:'980px'}}>
                  <thead>
                    <tr>
                      <th style={{padding:'8px', border:'1px solid rgba(148,163,184,0.35)', background:'rgba(15,23,42,0.95)', color:'#e2e8f0'}}>구분</th>
                      <th style={{padding:'8px', border:'1px solid rgba(148,163,184,0.35)', background:'rgba(15,23,42,0.95)', color:'#e2e8f0'}}>기준연도</th>
                      <th style={{padding:'8px', border:'1px solid rgba(148,163,184,0.35)', background:'rgba(15,23,42,0.95)', color:'#e2e8f0'}}>매출</th>
                      <th style={{padding:'8px', border:'1px solid rgba(148,163,184,0.35)', background:'rgba(15,23,42,0.95)', color:'#e2e8f0'}}>영업이익</th>
                      <th style={{padding:'8px', border:'1px solid rgba(148,163,184,0.35)', background:'rgba(15,23,42,0.95)', color:'#e2e8f0'}}>순이익</th>
                      <th style={{padding:'8px', border:'1px solid rgba(148,163,184,0.35)', background:'rgba(15,23,42,0.95)', color:'#e2e8f0'}}>자산</th>
                      <th style={{padding:'8px', border:'1px solid rgba(148,163,184,0.35)', background:'rgba(15,23,42,0.95)', color:'#e2e8f0'}}>부채</th>
                      <th style={{padding:'8px', border:'1px solid rgba(148,163,184,0.35)', background:'rgba(15,23,42,0.95)', color:'#e2e8f0'}}>자본</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      ['연결(CFS)', finDetail?.financial?.cfs],
                      ['별도(OFS)', finDetail?.financial?.ofs],
                    ].map(([lbl, d]) => (
                      <tr key={lbl}>
                        <td style={{padding:'7px 8px', border:'1px solid rgba(148,163,184,0.25)', background:'rgba(99,102,241,0.12)', color:'#c7d2fe', fontWeight:700}}>{lbl}</td>
                        <td style={{padding:'7px 8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'right'}}>{d?.year ?? '-'}</td>
                        <td style={{padding:'7px 8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'right'}}>{d?.revenue != null ? fmtEok(d.revenue) : '-'}</td>
                        <td style={{padding:'7px 8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'right'}}>{d?.operating_profit != null ? fmtEok(d.operating_profit) : '-'}</td>
                        <td style={{padding:'7px 8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'right'}}>{d?.net_income != null ? fmtEok(d.net_income) : '-'}</td>
                        <td style={{padding:'7px 8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'right'}}>{d?.total_assets != null ? fmtEok(d.total_assets) : '-'}</td>
                        <td style={{padding:'7px 8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'right'}}>{d?.total_liabilities != null ? fmtEok(d.total_liabilities) : '-'}</td>
                        <td style={{padding:'7px 8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'right'}}>{d?.total_equity != null ? fmtEok(d.total_equity) : '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div style={{overflowX:'auto'}}>
                <table style={{width:'100%', borderCollapse:'collapse', fontSize:'0.78rem', minWidth:'980px'}}>
                  <thead>
                    <tr>
                      <th style={{padding:'8px', border:'1px solid rgba(148,163,184,0.35)', background:'rgba(15,23,42,0.95)', color:'#e2e8f0'}}>현금흐름 구분</th>
                      <th style={{padding:'8px', border:'1px solid rgba(148,163,184,0.35)', background:'rgba(15,23,42,0.95)', color:'#e2e8f0'}}>기준연도</th>
                      <th style={{padding:'8px', border:'1px solid rgba(148,163,184,0.35)', background:'rgba(15,23,42,0.95)', color:'#e2e8f0'}}>영업CF</th>
                      <th style={{padding:'8px', border:'1px solid rgba(148,163,184,0.35)', background:'rgba(15,23,42,0.95)', color:'#e2e8f0'}}>투자CF</th>
                      <th style={{padding:'8px', border:'1px solid rgba(148,163,184,0.35)', background:'rgba(15,23,42,0.95)', color:'#e2e8f0'}}>재무CF</th>
                      <th style={{padding:'8px', border:'1px solid rgba(148,163,184,0.35)', background:'rgba(15,23,42,0.95)', color:'#e2e8f0'}}>CAPEX</th>
                      <th style={{padding:'8px', border:'1px solid rgba(148,163,184,0.35)', background:'rgba(15,23,42,0.95)', color:'#e2e8f0'}}>FCF(영업-CAPEX)</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[
                      ['연결(CFS)', finDetail?.cashflow?.cfs],
                      ['별도(OFS)', finDetail?.cashflow?.ofs],
                    ].map(([lbl, d]) => (
                      <tr key={lbl}>
                        <td style={{padding:'7px 8px', border:'1px solid rgba(148,163,184,0.25)', background:'rgba(16,185,129,0.12)', color:'#a7f3d0', fontWeight:700}}>{lbl}</td>
                        <td style={{padding:'7px 8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'right'}}>{d?.year ?? '-'}</td>
                        <td style={{padding:'7px 8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'right'}}>{d?.operating_cf != null ? fmtEok(d.operating_cf) : '-'}</td>
                        <td style={{padding:'7px 8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'right'}}>{d?.investing_cf != null ? fmtEok(d.investing_cf) : '-'}</td>
                        <td style={{padding:'7px 8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'right'}}>{d?.financing_cf != null ? fmtEok(d.financing_cf) : '-'}</td>
                        <td style={{padding:'7px 8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'right'}}>{d?.capex != null ? fmtEok(d.capex) : '-'}</td>
                        <td style={{padding:'7px 8px', border:'1px solid rgba(148,163,184,0.25)', textAlign:'right'}}>{d?.free_cf != null ? fmtEok(d.free_cf) : '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
});

export default SemiconductorView;
