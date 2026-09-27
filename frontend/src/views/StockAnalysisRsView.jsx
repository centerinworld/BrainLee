/* light-theme-codemod-2026-09-27 */
/* light-theme-codemod-pass2-2026-09-27 */
/* light-theme-codemod-pass3-2026-09-27 */
/* light-theme-codemod-pass4-2026-09-27 */
/* light-theme-codemod-pass5-2026-09-27 */
/* light-theme-codemod-pass6-2026-09-27 */
/* light-theme-codemod-pass7-2026-09-27 */
import React, { useCallback, useEffect, useRef, useState } from 'react';

const API = (path) => path;
const PAGE_SIZE = 25;
const PERIODS = ['12M', '1M', '3M', '6M'];

const fmtInt = (v) => (Number.isFinite(v) ? Number(v).toLocaleString('ko-KR') : '-');
const fmtPrice = (v) => (Number.isFinite(v) ? Number(v).toLocaleString('ko-KR') : '-');
const fmtPct = (v) => (Number.isFinite(v) ? `${v > 0 ? '+' : ''}${Number(v).toFixed(2)}%` : '-');
const clamp = (v, min, max) => Math.min(max, Math.max(min, v));
const norm = (s) => String(s || '').trim();

const METHOD_LABELS = {
  percentile: { label: '① 종합', title: '종합 RS — 지수 대비 초과수익을 전 종목 내 순위(0~100 퍼센타일)로 변환. 이 페이지 상단의 RS와 동일값.' },
  sector_rotation_4w: { label: '② 로테이션4주', title: '섹터 로테이션 방식 — 무보정 단순수익률(20거래일) − 코스피. routes/sector_rotation.py 와 같은 산식(%p, 코스피 고정 기준).' },
  sector_rotation_12w: { label: '② 로테이션12주', title: '섹터 로테이션 방식 — 무보정 단순수익률(60거래일) − 코스피 (%p, 코스피 고정 기준).' },
  signal_light: { label: '③ 신호등', title: '종목 분석 RS 신호등 — 3개월 초과수익 기준 green(>0%p)/yellow(>-5%p)/red(그 외). signal_engine._calc_rs 와 동일 임계값.' },
  track_r: { label: '④ TrackR', title: 'Track R(스크리너 점수) — 1M·3M·6M 초과수익이 양전환된 개수로 0~4점. 스크리너·전략 점수의 상대강도 트랙과 동일 규칙.' },
  ibd_rs: { label: '⑤ IBD RS', title: "IBD RS(윌리엄 오닐) — 0.4·C/C63+0.2·C/C126+0.2·C/C189+0.2·C/C252 를 전 종목 퍼센타일(1~99)로 변환. 시총 1,000억 미만 등은 계산 유니버스 밖이라 값이 없을 수 있음." },
};
const LIGHT_DOT = { green: '#16a34a', yellow: '#eab308', red: '#dc2626' };

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
  const [themeComposition, setThemeComposition] = useState(null);
  const [themeCompositionError, setThemeCompositionError] = useState('');

  // 필터 상태
  const [period, setPeriod] = useState('12M');
  const [sectorMode, setSectorMode] = useState('major');
  const [capMin, setCapMin] = useState(0);
  const [query, setQuery] = useState('');
  const [showMethods, setShowMethods] = useState(false);   // 2026-09-27: 5가지 RS 방식 병기 토글 (docs/RS_METHODS_REVIEW_20260927.md)
  const [sectorFilter, setSectorFilter] = useState('전체');
  const [highOnly, setHighOnly] = useState('all');
  const [highSort, setHighSort] = useState('score');
  const [page, setPage] = useState(1);
  const [high52Page, setHigh52Page] = useState(1);

  // 디바운스된 검색어
  const debouncedQuery = useDebouncedValue(query, 300);
  const REFRESH_MS = 30 * 60 * 1000;

  const fetchSummary = useCallback(() => {
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
    return ctrl;
  }, []);

  // ── 1. 마운트 시 요약 데이터 로드 + 30분 주기 갱신 ─────────────────
  useEffect(() => {
    const ctrl = fetchSummary();
    const t = setInterval(() => fetchSummary(), REFRESH_MS);
    return () => { ctrl.abort(); clearInterval(t); };
  }, [fetchSummary]);

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

  useEffect(() => {
    if (activeTab !== 'rs') return;
    const t = setInterval(() => fetchRsRows(), REFRESH_MS);
    return () => clearInterval(t);
  }, [activeTab, fetchRsRows]);

  useEffect(() => {
    if (activeTab !== 'composition') return;
    setThemeCompositionError('');
    fetch(API('/api/stock-analysis-rs/theme-composition/tags'))
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((json) => {
        if (json?.success) setThemeComposition(json.data || {});
        else setThemeCompositionError(json?.reason || '테마 구성 변화 로드 실패');
      })
      .catch((e) => setThemeCompositionError(String(e?.message || e)));
  }, [activeTab]);

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

  useEffect(() => {
    if (activeTab !== 'high52') return;
    const t = setInterval(() => fetchHigh52Rows(high52Page), REFRESH_MS);
    return () => clearInterval(t);
  }, [activeTab, high52Page, fetchHigh52Rows]);

  useEffect(() => { setHigh52Page(1); }, [debouncedQuery, sectorFilter, sectorMode, capMin, highOnly, highSort]);

  // ── 섹터 점수 계산 (클라이언트 측, 요약 데이터 기반) ──────────
  const periodKey = String(period || '12M').toLowerCase();
  const sectorSource = sectorMode === 'major'
    ? (summary.sector_rs_map?.[periodKey] || summary.sector_rs || [])
    : (summary.sector_rs_mid_map?.[periodKey] || summary.sector_rs_mid || []);
  // 2026-09-27: 예전에는 섹터 평균 RS 를 (최저~최고)로 다시 늘려 1등 섹터를 항상 100점으로 보여줬다("반도체 100"은 실제 12M 평균 RS 74.9).
  // 이제 서버가 계산한 실제 값(종목 RS 퍼센타일의 시총(log) 가중 평균, 0~100)을 그대로 쓴다.
  const sectorScores = sectorSource.map((s) => ({
    ...s,
    score: Math.round(clamp(s.avg_rs || 0, 0, 100)),
    sector: norm(s.sector),
  })).sort((a, b) => b.score - a.score);

  const benchmarks = summary.benchmarks || {};
  const strongSector = sectorScores[0];
  const weakSector = sectorScores[sectorScores.length - 1];
  const sectorSourceMode = sectorMode === 'major'
    ? (summary?.metadata?.sector_rs_source_major || 'local')
    : (summary?.metadata?.sector_rs_source_mid || 'local');
  const isLocalSectorSource = sectorSourceMode !== 'stockeasy';

  return (
    <div style={{ padding: '18px 18px 26px', color: '#374151', background: '#f4f6fb', minHeight: '100%' }}>
      <div style={{ display: 'flex', gap: 26, borderBottom: '1px solid #2a2f3a', paddingBottom: 10, marginBottom: 18, fontWeight: 700, fontSize: 27 }}>
        <button onClick={() => setActiveTab('rs')} style={{ border: 'none', background: 'transparent', borderBottom: activeTab === 'rs' ? '3px solid #d1d5db' : 'none', paddingBottom: 8, color: activeTab === 'rs' ? '#f9fafb' : '#7f8793', fontWeight: 700, fontSize: 27, cursor: 'pointer' }}>종합 RS</button>
        <button onClick={() => setActiveTab('high52')} style={{ border: 'none', background: 'transparent', borderBottom: activeTab === 'high52' ? '3px solid #d1d5db' : 'none', paddingBottom: 8, color: activeTab === 'high52' ? '#f9fafb' : '#7f8793', fontWeight: 700, fontSize: 27, cursor: 'pointer' }}>52주 신고가</button>
        <button onClick={() => setActiveTab('composition')} style={{ border: 'none', background: 'transparent', borderBottom: activeTab === 'composition' ? '3px solid #d1d5db' : 'none', paddingBottom: 8, color: activeTab === 'composition' ? '#f9fafb' : '#7f8793', fontWeight: 700, fontSize: 27, cursor: 'pointer' }}>테마 구성 변화</button>
      </div>

      {summaryLoading && <div style={{ color: '#2563eb', marginBottom: 10 }}>요약 데이터 로딩 중...</div>}
      {!!summaryError && <div style={{ color: '#dc2626', marginBottom: 10 }}>요약 오류: {summaryError}</div>}

      {activeTab === 'rs' && (
        <>
          {/* 섹터 RS 시각화 */}
          <section style={{ background: '#ffffff', border: '1px solid #e2e8f0', borderRadius: 10, padding: 16, marginBottom: 14 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 14, fontSize: 13, color: '#374151' }}>
              <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                한눈에 보이는 섹터RS
                {isLocalSectorSource && (
                  <span style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 5,
                    padding: '2px 8px',
                    borderRadius: 999,
                    border: '1px solid #1e293b',
                    color: '#1e293b',
                    background: '#ffffff',
                    fontSize: 11,
                    fontWeight: 700,
                  }}>
                    로컬 기준
                  </span>
                )}
              </span>
              <span>{sectorScores.length}개 섹터</span>
            </div>
            <div style={{ position: 'relative', height: 78 }}>
              <div style={{ position: 'absolute', left: 0, right: 0, top: 30, height: 10, borderRadius: 99, background: 'linear-gradient(90deg,#2563eb,#b45309,#dc2626)' }} />
              {sectorScores.slice(0, 15).map((s, idx) => (
                <div key={s.sector} style={{ position: 'absolute', left: `${clamp(s.score, 0, 100)}%`, top: idx % 2 ? 44 : 12, transform: 'translateX(-50%)', fontSize: 11, color: '#374151', background: 'rgba(255,255,255,0.95)', border: '1px solid #3c4659', borderRadius: 5, padding: '2px 6px', whiteSpace: 'nowrap' }}>
                  {s.sector}
                </div>
              ))}
              {!!benchmarks?.kospi && (
                <div style={{ position: 'absolute', left: `${clamp(benchmarks.kospi.rs || 50, 0, 100)}%`, top: 2, transform: 'translateX(-50%)', background: '#f1f5f9', color: '#1e293b', borderRadius: 6, padding: '2px 7px', fontSize: 11, border: '1px solid #1e293b' }}>
                  KOSPI {Math.round(benchmarks.kospi.rs || 0)} | {fmtPrice(benchmarks.kospi.current_price)} ({fmtPct(Number(benchmarks.kospi.change_rate))})
                </div>
              )}
              {!!benchmarks?.kosdaq && (
                <div style={{ position: 'absolute', left: `${clamp(benchmarks.kosdaq.rs || 50, 0, 100)}%`, top: 56, transform: 'translateX(-50%)', background: '#f8fafc', color: '#2563eb', borderRadius: 6, padding: '2px 7px', fontSize: 11, border: '1px solid #1e293b' }}>
                  KOSDAQ {Math.round(benchmarks.kosdaq.rs || 0)} | {fmtPrice(benchmarks.kosdaq.current_price)} ({fmtPct(Number(benchmarks.kosdaq.change_rate))})
                </div>
              )}
              <div style={{ position: 'absolute', top: 64, left: 0, fontSize: 12, color: '#374151' }}>약함 {weakSector?.score ?? 0}</div>
              <div style={{ position: 'absolute', top: 64, right: 0, fontSize: 12, color: '#374151' }}>{strongSector?.score ?? 100} 강함</div>
            </div>
          </section>

          {/* 섹터 필터 버튼 */}
          <section style={{ background: '#ffffff', border: '1px solid #e2e8f0', borderRadius: 10, padding: 12, marginBottom: 14 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              <button onClick={() => setSectorFilter('전체')} style={{ border: '1px solid #45506b', background: sectorFilter === '전체' ? '#f1f5f9' : '#f1f5f9', color: '#374151', padding: '6px 10px', borderRadius: 8, cursor: 'pointer' }}>전체</button>
              {sectorScores.map((s) => (
                <button key={s.sector} onClick={() => setSectorFilter(norm(s.sector))} style={{ border: '1px solid #374151', background: norm(sectorFilter) === norm(s.sector) ? '#f1f5f9' : 'transparent', color: norm(sectorFilter) === norm(s.sector) ? '#f9fafb' : '#374151', padding: '4px 8px', borderRadius: 999, cursor: 'pointer', fontSize: 13 }}>
                  {s.sector} <span style={{ color: '#dc2626', fontWeight: 700 }}>{s.score}</span>
                </button>
              ))}
            </div>
          </section>

          {/* 필터 컨트롤 */}
          <section style={{ background: '#ffffff', border: '1px solid #e2e8f0', borderRadius: 10, padding: 12, marginBottom: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <div style={{ display: 'flex', border: '1px solid #e2e8f0', borderRadius: 8, overflow: 'hidden' }}>
                  {PERIODS.map((p) => (
                    <button key={p} onClick={() => setPeriod(p)} style={{ border: 'none', borderRight: p === PERIODS[PERIODS.length - 1] ? 'none' : '1px solid #e2e8f0', background: period === p ? '#f1f5f9' : '#f1f5f9', color: period === p ? '#f9fafb' : '#334155', padding: '7px 12px', cursor: 'pointer', fontWeight: 700 }}>{p}</button>
                  ))}
                </div>
                <div style={{ display: 'flex', border: '1px solid #e2e8f0', borderRadius: 8, overflow: 'hidden' }}>
                  <button onClick={() => setSectorMode('major')} style={{ border: 'none', background: sectorMode === 'major' ? '#f1f5f9' : '#f1f5f9', color: '#374151', padding: '7px 10px', cursor: 'pointer' }}>대분류</button>
                  <button onClick={() => setSectorMode('middle')} style={{ border: 'none', borderLeft: '1px solid #e2e8f0', background: sectorMode === 'middle' ? '#f1f5f9' : '#f1f5f9', color: '#374151', padding: '7px 10px', cursor: 'pointer' }}>중분류</button>
                </div>
                <select value={capMin} onChange={(e) => setCapMin(Number(e.target.value))} style={{ background: '#ffffff', color: '#374151', border: '1px solid #e2e8f0', borderRadius: 8, padding: '7px 10px' }}>
                  <option value={0}>전체 시총</option>
                  <option value={5000}>5,000억+</option>
                  <option value={10000}>1조+</option>
                  <option value={20000}>2조+</option>
                </select>
                <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="종목명/종목코드" style={{ background: '#ffffff', color: '#374151', border: '1px solid #e2e8f0', borderRadius: 8, padding: '7px 10px', width: 180 }} />
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: '#374151', cursor: 'pointer' }} title="한 종목에 대해 서로 다른 5가지 RS 계산 방식을 나란히 비교합니다(docs/RS_METHODS_REVIEW_20260927.md). 정의·기준·기간이 각각 달라 숫자를 직접 비교하면 안 됩니다.">
                  <input type="checkbox" checked={showMethods} onChange={(e) => setShowMethods(e.target.checked)} />
                  5가지 RS 방식 비교
                </label>
                <div style={{ color: '#2563eb', fontWeight: 700 }}>{fmtInt(rsTotal)}개 종목 {rsLoading && '⟳'}</div>
              </div>
            </div>
          </section>

          {/* RS 테이블 */}
          <section style={{ background: '#ffffff', border: '1px solid #e2e8f0', borderRadius: 10, overflow: 'hidden' }}>
            {!!rsError && <div style={{ padding: 12, color: '#dc2626' }}>오류: {rsError}</div>}
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1220 }}>
                <thead>
                  <tr style={{ background: '#ffffff', color: '#334155', fontSize: 13 }}>
                    <th style={{ textAlign: 'left', padding: '10px 12px' }}>섹터</th>
                    <th style={{ textAlign: 'left', padding: '10px 12px' }}>종목명</th>
                    <th style={{ textAlign: 'right', padding: '10px 12px' }}>현재가</th>
                    <th style={{ textAlign: 'right', padding: '10px 12px' }}>등락률</th>
                    <th style={{ textAlign: 'right', padding: '10px 12px' }}>RS</th>
                    <th style={{ textAlign: 'right', padding: '10px 12px' }}>RS(1M)</th>
                    <th style={{ textAlign: 'right', padding: '10px 12px' }}>RS(3M)</th>
                    <th style={{ textAlign: 'right', padding: '10px 12px' }}>RS(6M)</th>
                    <th style={{ textAlign: 'right', padding: '10px 12px' }}>MMT</th>
                    {showMethods && Object.entries(METHOD_LABELS).map(([k, m]) => (
                      <th key={k} title={m.title} style={{ textAlign: 'right', padding: '10px 12px', background: '#eff4ff', color: '#0f3d91', cursor: 'help', whiteSpace: 'nowrap' }}>{m.label}</th>
                    ))}
                    <th style={{ textAlign: 'right', padding: '10px 12px' }}>시가총액</th>
                  </tr>
                </thead>
                <tbody>
                  {sectorFilter === '전체' && !!benchmarks?.kospi && (
                    <tr style={{ borderTop: '1px solid #e2e8f0', background: 'rgba(100,116,139,0.08)' }}>
                      <td style={{ padding: '9px 12px' }}>지수</td><td style={{ padding: '9px 12px', fontWeight: 700 }}>코스피</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right' }}>{fmtPrice(benchmarks.kospi.current_price)}</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right', color: Number(benchmarks.kospi.change_rate || 0) >= 0 ? '#15803d' : '#dc2626', fontWeight: 700 }}>{fmtPct(Number(benchmarks.kospi.change_rate || 0))}</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right', fontWeight: 800 }}>{Math.round(benchmarks.kospi.rs || 0)}</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number(benchmarks.kospi.rs_1m || 0).toFixed(2)}</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number(benchmarks.kospi.rs_3m || 0).toFixed(2)}</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number(benchmarks.kospi.rs_6m || 0).toFixed(2)}</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right' }}>-</td>
                      {showMethods && Object.keys(METHOD_LABELS).map((k) => <td key={k} style={{ padding: '9px 12px', textAlign: 'right', color: '#1e293b' }}>-</td>)}
                      <td style={{ padding: '9px 12px', textAlign: 'right' }}>-</td>
                    </tr>
                  )}
                  {sectorFilter === '전체' && !!benchmarks?.kosdaq && (
                    <tr style={{ borderTop: '1px solid #e2e8f0', background: 'rgba(37,99,235,0.08)' }}>
                      <td style={{ padding: '9px 12px' }}>지수</td><td style={{ padding: '9px 12px', fontWeight: 700 }}>코스닥</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right' }}>{fmtPrice(benchmarks.kosdaq.current_price)}</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right', color: Number(benchmarks.kosdaq.change_rate || 0) >= 0 ? '#15803d' : '#dc2626', fontWeight: 700 }}>{fmtPct(Number(benchmarks.kosdaq.change_rate || 0))}</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right', fontWeight: 800 }}>{Math.round(benchmarks.kosdaq.rs || 0)}</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number(benchmarks.kosdaq.rs_1m || 0).toFixed(2)}</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number(benchmarks.kosdaq.rs_3m || 0).toFixed(2)}</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number(benchmarks.kosdaq.rs_6m || 0).toFixed(2)}</td>
                      <td style={{ padding: '9px 12px', textAlign: 'right' }}>-</td>
                      {showMethods && Object.keys(METHOD_LABELS).map((k) => <td key={k} style={{ padding: '9px 12px', textAlign: 'right', color: '#1e293b' }}>-</td>)}
                      <td style={{ padding: '9px 12px', textAlign: 'right' }}>-</td>
                    </tr>
                  )}
                  {rsRows.map((r) => {
                    const sectorName = sectorMode === 'major' ? (r.major_names?.join(', ') || r.major_name || '-') : (r.mid_names?.join(', ') || r.mid_name || '-');
                    return (
                      <tr key={r.stock_code} style={{ borderTop: '1px solid #e2e8f0' }}>
                        <td style={{ padding: '9px 12px', color: '#1e293b', fontSize: 13 }}>{sectorName}</td>
                        <td style={{ padding: '9px 12px', color: '#374151' }}><div style={{ fontWeight: 700 }}>{r.stock_name}</div><div style={{ color: '#1e293b', fontSize: 12 }}>{r.stock_code}</div></td>
                        <td style={{ padding: '9px 12px', textAlign: 'right' }}>{fmtPrice(r.current_price)}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', color: (r.change_rate || 0) >= 0 ? '#15803d' : '#dc2626', fontWeight: 700 }}>{fmtPct(r.change_rate)}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right', fontWeight: 800 }}>{Number(r.rs || 0).toFixed(1)}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number(r.rs_1m || 0).toFixed(2)}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number(r.rs_3m || 0).toFixed(2)}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number(r.rs_6m || 0).toFixed(2)}</td>
                        <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number(r.mmt || 0).toFixed(2)}</td>
                        {showMethods && (() => {
                          const m = r.rs_methods || {};
                          return (
                            <>
                              <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number.isFinite(m.percentile) ? m.percentile.toFixed(0) : '-'}</td>
                              <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number.isFinite(m.sector_rotation_4w) ? fmtPct(m.sector_rotation_4w) : '-'}</td>
                              <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number.isFinite(m.sector_rotation_12w) ? fmtPct(m.sector_rotation_12w) : '-'}</td>
                              <td style={{ padding: '9px 12px', textAlign: 'center' }}>
                                {m.signal_light ? <span title={m.signal_light} style={{ display: 'inline-block', width: 10, height: 10, borderRadius: '50%', background: LIGHT_DOT[m.signal_light] || '#94a3b8' }} /> : '-'}
                              </td>
                              <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number.isFinite(m.track_r) ? `${m.track_r}/4` : '-'}</td>
                              <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number.isFinite(m.ibd_rs) ? m.ibd_rs : '-'}</td>
                            </>
                          );
                        })()}
                        <td style={{ padding: '9px 12px', textAlign: 'right' }}>{fmtInt(r.market_cap)}억</td>
                      </tr>
                    );
                  })}
                  {!rsLoading && !rsRows.length && <tr><td colSpan={showMethods ? 16 : 10} style={{ padding: 28, textAlign: 'center', color: '#334155' }}>조건에 맞는 종목이 없습니다.</td></tr>}
                </tbody>
              </table>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: 12, borderTop: '1px solid #e2e8f0' }}>
              <div style={{ color: '#1e293b', fontSize: 13 }}>기준일: {summary.metadata?.target_date || '-'} / 갱신: {summary.metadata?.updated_at || '-'} / 전체: {fmtInt(summary.metadata?.total_count || 0)} {showMethods && summary.metadata?.ibd_rs_as_of && `/ IBD RS 기준일: ${summary.metadata.ibd_rs_as_of}`}</div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1 || rsLoading} style={{ border: '1px solid #374151', background: '#ffffff', color: '#374151', borderRadius: 6, padding: '5px 10px', cursor: 'pointer' }}>이전</button>
                <span style={{ color: '#1e293b', fontSize: 13 }}>{page} / {rsTotalPages}</span>
                <button onClick={() => setPage((p) => Math.min(rsTotalPages, p + 1))} disabled={page >= rsTotalPages || rsLoading} style={{ border: '1px solid #374151', background: '#ffffff', color: '#374151', borderRadius: 6, padding: '5px 10px', cursor: 'pointer' }}>다음</button>
              </div>
            </div>
          </section>
          {rsLoading && <div style={{ marginTop: 8, color: '#2563eb', fontSize: 13 }}>RS 데이터 로딩 중...</div>}
        </>
      )}

      {activeTab === 'high52' && (
        <section style={{ background: '#ffffff', border: '1px solid #e2e8f0', borderRadius: 10, overflow: 'hidden', marginTop: 14 }}>
          <div style={{ padding: 12, borderBottom: '1px solid #e2e8f0', color: '#1e293b', display: 'flex', gap: 16, flexWrap: 'wrap' }}>
            <span>신고가: <strong style={{ color: '#15803d' }}>{fmtInt(high52Meta?.new_high_count || 0)}</strong></span>
            <span>근접(2% 이내): <strong style={{ color: '#2563eb' }}>{fmtInt(high52Meta?.near_high_count || 0)}</strong></span>
            <span>전체: <strong>{fmtInt(high52Total || high52Meta?.count || 0)}</strong></span>
            {high52Loading && <span style={{ color: '#2563eb' }}>로딩 중...</span>}
          </div>
          <div style={{ padding: 12, borderBottom: '1px solid #e2e8f0', display: 'flex', gap: 10, flexWrap: 'wrap' }}>
            <select value={highOnly} onChange={(e) => setHighOnly(e.target.value)} style={{ background: '#ffffff', color: '#374151', border: '1px solid #e2e8f0', borderRadius: 8, padding: '7px 10px' }}>
              <option value="all">전체</option><option value="near">신고가 근접(2% 이내)</option><option value="new">신고가 달성만</option>
            </select>
            <select value={highSort} onChange={(e) => setHighSort(e.target.value)} style={{ background: '#ffffff', color: '#374151', border: '1px solid #e2e8f0', borderRadius: 8, padding: '7px 10px' }}>
              <option value="score">브레이크아웃 점수</option><option value="gap">최고가 대비(근접순)</option><option value="vol">거래량 배수</option>
            </select>
            <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="종목명/종목코드" style={{ background: '#ffffff', color: '#374151', border: '1px solid #e2e8f0', borderRadius: 8, padding: '7px 10px', width: 180 }} />
          </div>
          {!!high52Error && <div style={{ padding: 12, color: '#dc2626' }}>오류: {high52Error}</div>}
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 1180 }}>
              <thead>
                <tr style={{ background: '#ffffff', color: '#334155', fontSize: 13 }}>
                  <th style={{ textAlign: 'left', padding: '10px 12px' }}>종목명</th><th style={{ textAlign: 'left', padding: '10px 12px' }}>섹터</th><th style={{ textAlign: 'right', padding: '10px 12px' }}>현재가</th><th style={{ textAlign: 'right', padding: '10px 12px' }}>52주 최고가</th><th style={{ textAlign: 'right', padding: '10px 12px' }}>최고가 대비</th><th style={{ textAlign: 'right', padding: '10px 12px' }}>등락률</th><th style={{ textAlign: 'right', padding: '10px 12px' }}>거래량 배수(20D)</th><th style={{ textAlign: 'right', padding: '10px 12px' }}>브레이크아웃 점수</th>
                </tr>
              </thead>
              <tbody>
                {high52Rows.map((r) => (
                  <tr key={`h-${r.stock_code}`} style={{ borderTop: '1px solid #e2e8f0' }}>
                    <td style={{ padding: '9px 12px', color: '#374151' }}><div style={{ fontWeight: 700 }}>{r.stock_name}</div><div style={{ color: '#1e293b', fontSize: 12 }}>{r.stock_code}</div></td>
                    <td style={{ padding: '9px 12px', color: '#1e293b' }}>{sectorMode === 'major' ? (r.major_names || []).join(', ') : (r.mid_names || []).join(', ')}</td>
                    <td style={{ padding: '9px 12px', textAlign: 'right' }}>{fmtPrice(r.current_price)}</td>
                    <td style={{ padding: '9px 12px', textAlign: 'right' }}>{fmtPrice(r.high52_price)}</td>
                    <td style={{ padding: '9px 12px', textAlign: 'right', color: (r.high_gap_pct || 0) >= -2 ? '#15803d' : '#334155', fontWeight: 700 }}>{fmtPct(r.high_gap_pct)}</td>
                    <td style={{ padding: '9px 12px', textAlign: 'right', color: (r.change_rate || 0) >= 0 ? '#15803d' : '#dc2626', fontWeight: 700 }}>{fmtPct(r.change_rate)}</td>
                    <td style={{ padding: '9px 12px', textAlign: 'right' }}>{Number(r.vol_ratio || 0).toFixed(2)}x</td>
                    <td style={{ padding: '9px 12px', textAlign: 'right' }}><span style={{ color: r.is_new_high ? '#15803d' : (r.is_near_high ? '#2563eb' : '#1e293b'), fontWeight: 700 }}>{Number(r.breakout_score || 0).toFixed(2)}</span></td>
                  </tr>
                ))}
                {!high52Loading && !high52Rows.length && <tr><td colSpan={8} style={{ padding: 28, textAlign: 'center', color: '#334155' }}>조건에 맞는 종목이 없습니다.</td></tr>}
              </tbody>
            </table>
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, alignItems: 'center', padding: 12, borderTop: '1px solid #e2e8f0' }}>
            <button onClick={() => setHigh52Page((p) => Math.max(1, p - 1))} disabled={high52Page <= 1 || high52Loading} style={{ border: '1px solid #374151', background: '#ffffff', color: '#374151', borderRadius: 6, padding: '5px 10px', cursor: 'pointer' }}>이전</button>
            <span style={{ color: '#1e293b', fontSize: 13 }}>{high52Page} / {high52TotalPages}</span>
            <button onClick={() => setHigh52Page((p) => Math.min(high52TotalPages, p + 1))} disabled={high52Page >= high52TotalPages || high52Loading} style={{ border: '1px solid #374151', background: '#ffffff', color: '#374151', borderRadius: 6, padding: '5px 10px', cursor: 'pointer' }}>다음</button>
          </div>
        </section>
      )}

      {activeTab === 'composition' && (
        <section style={{ background: '#ffffff', border: '1px solid #e2e8f0', borderRadius: 10, overflow: 'hidden', marginTop: 14 }}>
          <div style={{ padding: 14, borderBottom: '1px solid #e2e8f0' }}>
            <div style={{ fontWeight: 800, color: '#374151' }}>테마·업종 구성종목 변동</div>
            <div style={{ color: '#334155', fontSize: 13, marginTop: 5 }}>{themeComposition?.notice || '기준일별 종목분류 스냅샷을 비교하는 중입니다.'}</div>
            {themeComposition?.current_date && <div style={{ color: '#0891b2', fontSize: 12, marginTop: 7 }}>비교: {themeComposition.previous_date} → {themeComposition.current_date}</div>}
          </div>
          {!!themeCompositionError && <div style={{ padding: 12, color: '#dc2626' }}>오류: {themeCompositionError}</div>}
          {!themeComposition ? <div style={{ padding: 28, textAlign: 'center', color: '#334155' }}>로딩 중...</div> : <>
            <div style={{ padding: 14, overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 520 }}>
                <thead><tr style={{ color: '#334155', fontSize: 13, background: '#ffffff' }}><th style={{ textAlign: 'left', padding: '9px 12px' }}>테마/업종</th><th style={{ textAlign: 'right', padding: '9px 12px' }}>현재</th><th style={{ textAlign: 'right', padding: '9px 12px' }}>직전</th><th style={{ textAlign: 'right', padding: '9px 12px' }}>증감</th></tr></thead>
                <tbody>{(themeComposition.themes || []).slice(0, 60).map((row) => <tr key={row.theme} style={{ borderTop: '1px solid #e2e8f0' }}><td style={{ padding: '8px 12px' }}>{row.theme}</td><td style={{ padding: '8px 12px', textAlign: 'right' }}>{fmtInt(row.count)}</td><td style={{ padding: '8px 12px', textAlign: 'right', color: '#334155' }}>{fmtInt(row.previous_count)}</td><td style={{ padding: '8px 12px', textAlign: 'right', fontWeight: 800, color: row.change > 0 ? '#15803d' : row.change < 0 ? '#dc2626' : '#334155' }}>{row.change > 0 ? '+' : ''}{fmtInt(row.change)}</td></tr>)}</tbody>
              </table>
            </div>
            <div style={{ borderTop: '1px solid #e2e8f0', padding: 14 }}>
              <div style={{ fontWeight: 750, marginBottom: 8 }}>종목 분류 변경</div>
              {!themeComposition.moves?.length ? <div style={{ color: '#334155', fontSize: 13 }}>두 기준일 사이 분류 변경 종목이 없습니다.</div> : <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(240px,1fr))', gap: 8 }}>{themeComposition.moves.map((row) => <div key={row.stock_code} style={{ border: '1px solid #263043', borderRadius: 7, padding: '8px 10px', fontSize: 13 }}><b>{row.stock_name}</b> <span style={{ color: '#1e293b' }}>{row.stock_code}</span><div style={{ color: '#334155', marginTop: 4 }}>{row.previous_theme} <span style={{ color: '#0891b2' }}>→</span> {row.current_theme}</div></div>)}</div>}
            </div>
          </>}
        </section>
      )}
    </div>
  );
}
