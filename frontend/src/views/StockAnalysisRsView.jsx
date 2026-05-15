import React, { useCallback, useEffect, useRef, useState } from 'react';

const API = (path) => path;
const PAGE_SIZE = 25;
const PERIODS = ['12M', '1M', '3M', '6M'];

const fmtInt = (v) => (Number.isFinite(v) ? Number(v).toLocaleString('ko-KR') : '-');
const fmtPrice = (v) => (Number.isFinite(v) ? Number(v).toLocaleString('ko-KR') : '-');
const fmtPct = (v) => (Number.isFinite(v) ? `${v > 0 ? '+' : ''}${Number(v).toFixed(2)}%` : '-');
const clamp = (v, min, max) => Math.min(max, Math.max(min, v));
const norm = (s) => String(s || '').trim();

function useDebouncedValue(value, delay = 300) {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
}

export default function StockAnalysisRsView() {
  const [activeTab, setActiveTab] = useState('rs');

  // 요약 데이터 (마운트 시 1회)
  const [summary, setSummary] = useState({ sector_rs: [], sector_rs_mid: [], benchmarks: {}, metadata: {} });
  const [summaryLoading, setSummaryLoading] = useState(false);
  const [summaryError, setSummaryError] = useState('');

  // RS 행 (서버 사이드 페이지네이션)
  const [rsRows, setRsRows] = useState([]);
  const [rsTotal, setRsTotal] = useState(0);
  const [rsTotalPages, setRsTotalPages] = useState(1);
  const [rsLoading, setRsLoading] = useState(false);
  const [rsError, setRsError] = useState('');

  // 52주 행 (탭 최초 진입 시 로드)
  const [high52Meta, setHigh52Meta] = useState({});
  const [high52Rows, setHigh52Rows] = useState([]);
  const [high52Total, setHigh52Total] = useState(0);
  const [high52TotalPages, setHigh52TotalPages] = useState(1);
  const [high52Loading, setHigh52Loading] = useState(false);
  const [high52Error, setHigh52Error] = useState('');
  const high52Loaded = useRef(false);

  // 필터 상태
  const [period, setPeriod] = useState('12M');
  const [sectorMode, setSectorMode] = useState('major');
  const [capMin, setCapMin] = useState(0);
  const [query, setQuery] = useState('');
  const [sectorFilter, setSectorFilter] = useState('전체');
  const [highOnly, setHighOnly] = useState('all');
  const [highSort, setHighSort] = useState('score');
  const [page, setPage] = useState(1);
  const [high52Page, setHigh52Page] = useState(1);

  // 디바운스된 검색어
  const debouncedQuery = useDebouncedValue(query, 300);

  // ── 1. 마운트 시 요약 데이터만 로드 ──────────────────────────
  useEffect(() => {
    const ctrl = new AbortController();
    setSummaryLoading(true);
    setSummaryError('');
    fetch(API('/api/stock-analysis-rs/dashboard-data'), { signal: ctrl.signal })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((json) => {
        if (json?.success) setSummary(json.data || {});
        else setSummaryError(json?.reason || 'RS 요약 로드 실패');
      })
      .catch((e) => { if (e.name !== 'AbortError') setSummaryError(String(e?.message || e)); })
      .finally(() => setSummaryLoading(false));
    return () => ctrl.abort();
  }, []);

  // ── 2. RS 행: 필터/페이지 변경 시 서버에서 가져오기 ──────────
  const fetchRsRows = useCallback(() => {
    const sortKey = period === '1M' ? 'rs_1m' : period === '3M' ? 'rs_3m' : period === '6M' ? 'rs_6m' : 'rs';
    const params = new URLSearchParams({
      page, page_size: PAGE_SIZE, sort: sortKey,
      q: debouncedQuery, sector: sectorFilter, cap_min: capMin, sector_mode: sectorMode,
    });
    const ctrl = new AbortController();
    setRsLoading(true);
    setRsError('');
    fetch(API(`/api/stock-analysis-rs/dashboard-rows?${params}`), { signal: ctrl.signal })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((json) => {
        if (json?.success) {
          setRsRows(json.data?.rows || []);
          setRsTotal(json.data?.total || 0);
          setRsTotalPages(json.data?.total_pages || 1);
        } else {
          setRsError(json?.reason || 'RS 행 로드 실패');
          setRsRows([]);
        }
      })
      .catch((e) => { if (e.name !== 'AbortError') { setRsError(String(e?.message || e)); setRsRows([]); } })
      .finally(() => setRsLoading(false));
    return () => ctrl.abort();
  }, [page, period, debouncedQuery, sectorFilter, capMin, sectorMode]);

  useEffect(() => {
    if (activeTab !== 'rs') return;
    return fetchRsRows();
  }, [fetchRsRows, activeTab]);

  // 필터 변경 시 1페이지로 리셋
  useEffect(() => { setPage(1); }, [debouncedQuery, sectorFilter, sectorMode, capMin, period]);

  // ── 3. 52주 행: 탭 최초 진입 시 로드 (지연 로딩) ─────────────
  const fetchHigh52Rows = useCallback((pg = 1) => {
    const params = new URLSearchParams({
      page: pg, page_size: PAGE_SIZE, sort: highSort,
      q: debouncedQuery, sector: sectorFilter, cap_min: capMin,
      sector_mode: sectorMode, high_filter: highOnly,
    });
    const ctrl = new AbortController();
    setHigh52Loading(true);
    setHigh52Error('');
    Promise.all([
      !high52Loaded.current
        ? fetch(API('/api/stock-analysis-rs/high52-data'), { signal: ctrl.signal })
            .then((r) => (r.ok ? r.json() : null))
            .then((j) => { if (j?.success) setHigh52Meta(j.data?.metadata || {}); })
        : Promise.resolve(),
      fetch(API(`/api/stock-analysis-rs/high52-rows?${params}`), { signal: ctrl.signal })
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
        .then((json) => {
          if (json?.success) {
            setHigh52Rows(json.data?.rows || []);
            setHigh52Total(json.data?.total || 0);
            setHigh52TotalPages(json.data?.total_pages || 1);
            high52Loaded.current = true;
          } else {
            setHigh52Error(json?.reason || '52주 데이터 로드 실패');
          }
        }),
    ])
      .catch((e) => { if (e.name !== 'AbortError') setHigh52Error(String(e?.message || e)); })
      .finally(() => setHigh52Loading(false));
    return () => ctrl.abort();
  }, [debouncedQuery, sectorFilter, capMin, sectorMode, highOnly, highSort]);

  useEffect(() => {
    if (activeTab !== 'high52') return;
    setHigh52Page(1);
    return fetchHigh52Rows(1);
  }, [activeTab, fetchHigh52Rows]);

  useEffect(() => {
    if (activeTab !== 'high52' || !high52Loaded.current) return;
    return fetchHigh52Rows(high52Page);
  }, [high52Page]);

  useEffect(() => { setHigh52Page(1); }, [debouncedQuery, sectorFilter, sectorMode, capMin, highOnly, highSort]);

  // ── 섹터 점수 계산 (클라이언트 측, 요약 데이터 기반) ──────────
  const sectorSource = sectorMode === 'major' ? (summary.sector_rs || []) : (summary.sector_rs_mid || []);
  const maxAvgRs = Math.max(...sectorSource.map((s) => s.avg_rs || 0), 1);
  const minAvgRs = Math.min(...sectorSource.map((s) => s.avg_rs || 0), -1);
  const sectorScores = sectorSource.map((s) => ({
    ...s,
    score: maxAvgRs === minAvgRs ? 50 : Math.round(clamp(((s.avg_rs - minAvgRs) / (maxAvgRs - minAvgRs)) * 100, 0, 100)),
    sector: norm(s.sector),
  })).sort((a, b) => b.score - a.score);

  const benchmarks = summary.benchmarks || {};
  const strongSector = sectorScores[0];
  const weakSector = sectorScores[sectorScores.length - 1];

  return (
    <div style={{ padding: '18px 18px 26px', color: '#e5e7eb', background: '#080c14', minHeight: '100%' }}>
      <div style={{ display: 'flex', gap: 26, borderBottom: '1px solid #2a2f3a', paddingBottom: 10, marginBottom: 18, fontWeight: 700, fontSize: 27 }}>
        <button onClick={() => setActiveTab('rs')} style={{ border: 'none', background: 'transparent', borderBottom: activeTab === 'rs' ? '3px solid #d1d5db' : 'none', paddingBottom: 8, color: activeTab === 'rs' ? '#f9fafb' : '#7f8793', fontWeight: 700, fontSize: 27, cursor: 'pointer' }}>종합 RS</button>
        <button onClick={() => setActiveTab('high52')} style={{ border: 'none', background: 'transparent', borderBottom: activeTab === 'high52' ? '3px solid #d1d5db' : 'none', paddingBottom: 8, color: activeTab === 'high52' ? '#f9fafb' : '#7f8793', fontWeight: 700, fontSize: 27, cursor: 'pointer' }}>52주 신고가</button>
      </div>

      {summaryLoading && <div style={{ color: '#93c5fd', marginBottom: 10 }}>요약 데이터 로딩 중...</div>}
      {!!summaryError && <div style={{ color: '#f87171', marginBottom: 10 }}>요약 오류: {summaryError}</div>}

      {activeTab === 'rs' && (
        <>
          {/* 섹터 RS 시각화 */}
          <section style={{ background: '#111827', border: '1px solid #2b3240', borderRadius: 10, padding: 16, marginBottom: 14 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 14, fontSize: 13, color: '#a7b0bf' }}>
              <span>한눈에 보이는 섹터RS</span>
              <span>{sectorScores.length}개 섹터</span>
            </div>
            <div style={{ position: 'relative', height: 78 }}>
              <div style={{ position: 'absolute', left: 0, right: 0, top: 30, height: 10, borderRadius: 99, background: 'linear-gradient(90deg,#60a5fa,#f59e0b,#ef4444)' }} />
              {sectorScores.slice(0, 15).map((s, idx) => (
                <div key={s.sector} style={{ position: 'absolute', left: `${clamp(s.score, 0, 100)}%`, top: idx % 2 ? 44 : 12, transform: 'translateX(-50%)', fontSize: 11, color: '#e5e7eb', background: 'rgba(17,24,39,0.95)', border: '1px solid #3c4659', borderRadius: 5, padding: '2px 6px', whiteSpace: 'nowrap' }}>
                  {s.sector}
                </div>
              ))}
              {!!benchmarks?.kospi && (
                <div style={{ position: 'absolute', left: `${clamp(benchmarks.kospi.rs || 50, 0, 100)}%`, top: 2, transform: 'translateX(-50%)', background: '#334155', color: '#cbd5e1', borderRadius: 6, padding: '2px 7px', fontSize: 11, border: '1px solid #64748b' }}>
                  KOSPI {Math.round(benchmarks.kospi.rs || 0)}
                </div>
              )}
              {!!benchmarks?.kosdaq && (
                <div style={{ position: 'absolute', left: `${clamp(benchmarks.kosdaq.rs || 50, 0, 100)}%`, top: 56, transform: 'translateX(-50%)', background: '#1e293b', color: '#93c5fd', borderRadius: 6, padding: '2px 7px', fontSize: 11, border: '1px solid #475569' }}>
                  KOSDAQ {Math.round(benchmarks.kosdaq.rs || 0)}
                </div>
              )}
              <div style={{ position: 'absolute', top: 64, left: 0, fontSize: 12, color: '#9ca3af' }}>약함 {weakSector?.score ?? 0}</div>
              <div style={{ position: 'absolute', top: 64, right: 0, fontSize: 12, color: '#9ca3af' }}>{strongSector?.score ?? 100} 강함</div>
            </div>
          </section>

          {/* 섹터 필터 버튼 */}
          <section style={{ background: '#111827', border: '1px solid #2b3240', borderRadius: 10, padding: 12, marginBottom: 14 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <button onClick={() => setSectorFilter('전체')} style={{ border: '1px solid #45506b', background: sectorFilter === '전체' ? '#1f2937' : '#0d1320', color: '#e5e7eb', padding: '6px 10px', borderRadius: 8, cursor: 'pointer' }}>전체</button>
              {sectorScores.map((s) => (
                <button key={s.sector} onClick={() => setSectorFilter(norm(s.sector))} style={{ border: '1px solid #374151', background: norm(sectorFilter) === norm(s.sector) ? '#1f2937' : 'transparent', color: norm(sectorFilter) === norm(s.sector) ? '#f9fafb' : '#c7ceda', padding: '4px 8px', borderRadius: 999, cursor: 'pointer', fontSize: 13 }}>
                  {s.sector} <span style={{ color: '#f87171', fontWeight: 700 }}>{s.score}</span>
                </button>
              ))}
            </div>
          </section>

          {/* 필터 컨트롤 */}
          <section style={{ background: '#111827', border: '1px solid #2b3240', borderRadius: 10, padding: 12, marginBottom: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <div style={{ display: 'flex', border: '1px solid #3a4457', borderRadius: 8, overflow: 'hidden' }}>
                  {PERIODS.map((p) => (
                    <button key={p} onClick={() => setPeriod(p)} style={{ border: 'none', borderRight: p === PERIODS[PERIODS.length - 1] ? 'none' : '1px solid #3a4457', background: period === p ? '#1f2937' : '#0d1320', color: period === p ? '#f9fafb' : '#94a3b8', padding: '7px 12px', cursor: 'pointer', fontWeight: 700 }}>{p}</button>
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
                <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="종목명/종목코드" style={{ background: '#0d1320', color: '#e5e7eb', border: '1px solid #3a4457', borderRadius: 8, padding: '7px 10px', width: 180 }} />
              </div>
              <div style={{ color: '#93c5fd', fontWeight: 700 }}>{fmtInt(rsTotal)}개 종목 {rsLoading && '⟳'}</div>
            </div>
          </section>

          {/* RS 테이블 */}
          <section style={{ background: '#111827', border: '1px solid #2b3240', borderRadius: 10, overflow: 'hidden' }}>
            {!!rsError && <div style={{ padding: 12, color: '#f87171' }}>오류: {rsError}</div>}
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
                  {sectorFilter === '전체' && !!benchmarks?.kospi && (
                    <tr style={{ borderTop: '1px solid #1f2937', background: 'rgba(148,163,184,0.08)' }}>
                      <td style={{ padding: '9px 12px' }}>지수</td><td style={{ padding: '9px 12px', fontWeight: 700 }}>코스피</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right' }}>-</td><td style={{ padding: '9px 12px', textAlign: 'right' }}>-</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right', fontWeight: 800 }}>{Math.round(benchmarks.kospi.rs || 0)}</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number(benchmarks.kospi.rs_1m || 0).toFixed(2)}</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number(benchmarks.kospi.rs_3m || 0).toFixed(2)}</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number(benchmarks.kospi.rs_6m || 0).toFixed(2)}</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right' }}>-</td><td style={{ padding: '9px 12px', textAlign: 'right' }}>-</td>
                    </tr>
                  )}
                  {sectorFilter === '전체' && !!benchmarks?.kosdaq && (
                    <tr style={{ borderTop: '1px solid #1f2937', background: 'rgba(96,165,250,0.08)' }}>
                      <td style={{ padding: '9px 12px' }}>지수</td><td style={{ padding: '9px 12px', fontWeight: 700 }}>코스닥</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right' }}>-</td><td style={{ padding: '9px 12px', textAlign: 'right' }}>-</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right', fontWeight: 800 }}>{Math.round(benchmarks.kosdaq.rs || 0)}</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number(benchmarks.kosdaq.rs_1m || 0).toFixed(2)}</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number(benchmarks.kosdaq.rs_3m || 0).toFixed(2)}</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number(benchmarks.kosdaq.rs_6m || 0).toFixed(2)}</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right' }}>-</td><td style={{ padding: '9px 12px', textAlign: 'right' }}>-</td>
                    </tr>
                  )}
                  {rsRows.map((r) => {
                    const sectorName = sectorMode === 'major' ? (r.major_names?.join(', ') || r.major_name || '-') : (r.mid_names?.join(', ') || r.mid_name || '-');
                    return (
                      <tr key={r.stock_code} style={{ borderTop: '1px solid #1f2937' }}>
                        <td style={{ padding: '9px 12px', color: '#cbd5e1', fontSize: 13 }}>{sectorName}</td>
                        <td style={{ padding: '9px 12px', color: '#f3f4f6' }}><div style={{ fontWeight: 700 }}>{r.stock_name}</div><div style={{ color: '#64748b', fontSize: 12 }}>{r.stock_code}</div></td>
                        <td style={{ padding: '9px 12px', textAlign: 'right' }}>{fmtPrice(r.current_price)}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', color: (r.change_rate || 0) >= 0 ? '#22c55e' : '#ef4444', fontWeight: 700 }}>{fmtPct(r.change_rate)}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontWeight: 800 }}>{Number(r.rs || 0).toFixed(1)}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number(r.rs_1m || 0).toFixed(2)}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number(r.rs_3m || 0).toFixed(2)}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number(r.rs_6m || 0).toFixed(2)}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number(r.mmt || 0).toFixed(2)}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right' }}>{fmtInt(r.market_cap)}억</td>
                      </tr>
                    );
                  })}
                  {!rsLoading && !rsRows.length && <tr><td colSpan={10} style={{ padding: 28, textAlign: 'center', color: '#94a3b8' }}>조건에 맞는 종목이 없습니다.</td></tr>}
                </tbody>
              </table>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: 12, borderTop: '1px solid #1f2937' }}>
              <div style={{ color: '#64748b', fontSize: 13 }}>기준일: {summary.metadata?.target_date || '-'} / 갱신: {summary.metadata?.updated_at || '-'} / 전체: {fmtInt(summary.metadata?.total_count || 0)}</div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1 || rsLoading} style={{ border: '1px solid #374151', background: '#0d1320', color: '#e5e7eb', borderRadius: 6, padding: '5px 10px', cursor: 'pointer' }}>이전</button>
                <span style={{ color: '#cbd5e1', fontSize: 13 }}>{page} / {rsTotalPages}</span>
                <button onClick={() => setPage((p) => Math.min(rsTotalPages, p + 1))} disabled={page >= rsTotalPages || rsLoading} style={{ border: '1px solid #374151', background: '#0d1320', color: '#e5e7eb', borderRadius: 6, padding: '5px 10px', cursor: 'pointer' }}>다음</button>
              </div>
            </div>
          </section>
          {rsLoading && <div style={{ marginTop: 8, color: '#93c5fd', fontSize: 13 }}>RS 데이터 로딩 중...</div>}
        </>
      )}

      {activeTab === 'high52' && (
        <section style={{ background: '#111827', border: '1px solid #2b3240', borderRadius: 10, overflow: 'hidden', marginTop: 14 }}>
          <div style={{ padding: 12, borderBottom: '1px solid #1f2937', color: '#cbd5e1', display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            <span>신고가: <strong style={{ color: '#22c55e' }}>{fmtInt(high52Meta?.new_high_count || 0)}</strong></span>
            <span>근접(2% 이내): <strong style={{ color: '#93c5fd' }}>{fmtInt(high52Meta?.near_high_count || 0)}</strong></span>
            <span>전체: <strong>{fmtInt(high52Total || high52Meta?.count || 0)}</strong></span>
            {high52Loading && <span style={{ color: '#93c5fd' }}>로딩 중...</span>}
          </div>
          <div style={{ padding: 12, borderBottom: '1px solid #1f2937', display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <select value={highOnly} onChange={(e) => setHighOnly(e.target.value)} style={{ background: '#0d1320', color: '#e5e7eb', border: '1px solid #3a4457', borderRadius: 8, padding: '7px 10px' }}>
              <option value="all">전체</option><option value="near">신고가 근접(2% 이내)</option><option value="new">신고가 달성만</option>
            </select>
            <select value={highSort} onChange={(e) => setHighSort(e.target.value)} style={{ background: '#0d1320', color: '#e5e7eb', border: '1px solid #3a4457', borderRadius: 8, padding: '7px 10px' }}>
              <option value="score">브레이크아웃 점수</option><option value="gap">최고가 대비(근접순)</option><option value="vol">거래량 배수</option>
            </select>
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="종목명/종목코드" style={{ background: '#0d1320', color: '#e5e7eb', border: '1px solid #3a4457', borderRadius: 8, padding: '7px 10px', width: 180 }} />
          </div>
          {!!high52Error && <div style={{ padding: 12, color: '#f87171' }}>오류: {high52Error}</div>}
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1180 }}>
              <thead>
                <tr style={{ background: '#0f172a', color: '#94a3b8', fontSize: 13 }}>
                  <th style={{ textAlign: 'left', padding: '10px 12px' }}>종목명</th><th style={{ textAlign: 'left', padding: '10px 12px' }}>섹터</th><th style={{ textAlign: 'right', padding: '10px 12px' }}>현재가</th><th style={{ textAlign: 'right', padding: '10px 12px' }}>52주 최고가</th><th style={{ textAlign: 'right', padding: '10px 12px' }}>최고가 대비</th><th style={{ textAlign: 'right', padding: '10px 12px' }}>등락률</th><th style={{ textAlign: 'right', padding: '10px 12px' }}>거래량 배수(20D)</th><th style={{ textAlign: 'right', padding: '10px 12px' }}>브레이크아웃 점수</th>
                </tr>
              </thead>
              <tbody>
                {high52Rows.map((r) => (
                  <tr key={`h-${r.stock_code}`} style={{ borderTop: '1px solid #1f2937' }}>
                    <td style={{ padding: '9px 12px', color: '#f3f4f6' }}><div style={{ fontWeight: 700 }}>{r.stock_name}</div><div style={{ color: '#64748b', fontSize: 12 }}>{r.stock_code}</div></td>
                    <td style={{ padding: '9px 12px', color: '#cbd5e1' }}>{sectorMode === 'major' ? (r.major_names || []).join(', ') : (r.mid_names || []).join(', ')}</td>
                    <td style={{ padding: '9px 12px', textAlign: 'right' }}>{fmtPrice(r.current_price)}</td>
                    <td style={{ padding: '9px 12px', textAlign: 'right' }}>{fmtPrice(r.high52_price)}</td>
                    <td style={{ padding: '9px 12px', textAlign: 'right', color: (r.high_gap_pct || 0) >= -2 ? '#22c55e' : '#94a3b8', fontWeight: 700 }}>{fmtPct(r.high_gap_pct)}</td>
                    <td style={{ padding: '9px 12px', textAlign: 'right', color: (r.change_rate || 0) >= 0 ? '#22c55e' : '#ef4444', fontWeight: 700 }}>{fmtPct(r.change_rate)}</td>
                    <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number(r.vol_ratio || 0).toFixed(2)}x</td>
                    <td style={{ padding: '9px 12px', textAlign: 'right' }}><span style={{ color: r.is_new_high ? '#22c55e' : (r.is_near_high ? '#93c5fd' : '#cbd5e1'), fontWeight: 700 }}>{Number(r.breakout_score || 0).toFixed(2)}</span></td>
                  </tr>
                ))}
                {!high52Loading && !high52Rows.length && <tr><td colSpan={8} style={{ padding: 28, textAlign: 'center', color: '#94a3b8' }}>조건에 맞는 종목이 없습니다.</td></tr>}
              </tbody>
            </table>
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, alignItems: 'center', padding: 12, borderTop: '1px solid #1f2937' }}>
            <button onClick={() => setHigh52Page((p) => Math.max(1, p - 1))} disabled={high52Page <= 1 || high52Loading} style={{ border: '1px solid #374151', background: '#0d1320', color: '#e5e7eb', borderRadius: 6, padding: '5px 10px', cursor: 'pointer' }}>이전</button>
            <span style={{ color: '#cbd5e1', fontSize: 13 }}>{high52Page} / {high52TotalPages}</span>
            <button onClick={() => setHigh52Page((p) => Math.min(high52TotalPages, p + 1))} disabled={high52Page >= high52TotalPages || high52Loading} style={{ border: '1px solid #374151', background: '#0d1320', color: '#e5e7eb', borderRadius: 6, padding: '5px 10px', cursor: 'pointer' }}>다음</button>
          </div>
        </section>
      )}
    </div>
  );
}
