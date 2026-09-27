/* light-theme-codemod-2026-09-27 */
/* light-theme-codemod-pass2-2026-09-27 */
/* light-theme-codemod-pass3-2026-09-27 */
/* light-theme-codemod-pass4-2026-09-27 */
/* light-theme-codemod-pass5-2026-09-27 */
/* light-theme-codemod-pass6-2026-09-27 */
/* light-theme-codemod-pass7-2026-09-27 */
import { useState, useEffect, useCallback, Fragment } from 'react';
import { API } from '../utils';

const PHASE_COLOR = {
  Leading:   { bg: 'rgba(22,163,74,0.15)',  border: '#15803d', text: '#15803d',  label: '🚀 주도' },
  Improving: { bg: 'rgba(217,119,6,0.15)', border: '#b45309', text: '#b45309',  label: '📈 개선' },
  Weakening: { bg: 'rgba(220,38,38,0.12)',  border: '#dc2626', text: '#dc2626',  label: '⚠ 약화' },
  Lagging:   { bg: 'rgba(100,116,139,0.1)', border: '#1e293b', text: '#1e293b',  label: '😴 부진' },
};
const SIGNAL_COLOR = {
  BUY:     { bg: 'rgba(22,163,74,0.2)',  border: '#15803d', text: '#15803d',  label: '매수' },
  WATCH:   { bg: 'rgba(217,119,6,0.2)', border: '#b45309', text: '#b45309',  label: '관심' },
  NEUTRAL: { bg: 'rgba(100,116,139,0.1)',border: '#1e293b', text: '#334155',  label: '관망' },
};
const STAGE_COLOR = {
  ENTRY_NOW:   { bg: 'rgba(22,163,74,0.18)',  border: '#15803d', text: '#15803d', label: '진입' },
  EARLY_WATCH: { bg: 'rgba(217,119,6,0.18)', border: '#b45309', text: '#b45309', label: '초기 관찰' },
  HOLD_LEADER: { bg: 'rgba(37,99,235,0.16)', border: '#2563eb', text: '#2563eb', label: '보유/추세' },
  WAIT:        { bg: 'rgba(100,116,139,0.10)',border: '#1e293b', text: '#334155', label: '대기' },
  AVOID:       { bg: 'rgba(220,38,38,0.12)',  border: '#dc2626', text: '#dc2626', label: '회피' },
};

// 2026-09-27(사용자 지시): "국내 섹터 로테이션과 같은 방식으로 나스닥/S&P500 주도섹터·주도주도 판정해줘".
// 미국은 수급(외국인/기관)·수출YoY·DART실적 데이터가 없어 가격·거래량 기반 RS 로테이션만 사용(routes/us_sector_rotation.py 참조).
// 국내와 미국을 이 화면 하나에서 상단 토글로 전환 — "섹터 로테이션=주도섹터/주도주 판정 엔진", "섹터 분류=시세 비교/탐색"으로
// 두 화면의 역할을 분리했다(섹터 분류 쪽은 점수·단계·리더종목 산출 로직이 없어 판정 엔진으로 부적합하다고 판단).
const MARKETS = [
  { key: 'kr',     label: '🇰🇷 국내' },
  { key: 'sp500',  label: '🇺🇸 S&P500' },
  { key: 'nasdaq', label: '🇺🇸 나스닥' },
];

export default function SectorRotationView() {
  const [market, setMarket] = useState('kr'); // kr | sp500 | nasdaq
  const [leadership, setLeadership] = useState(null);
  const [scores, setScores] = useState(null);
  const [rotMap, setRotMap] = useState(null);
  const [history, setHistory] = useState(null);
  const [selSector, setSelSector] = useState(null);
  const [picks, setPicks] = useState({}); // sectorKey → picks array
  const [expandedSector, setExpandedSector] = useState(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState('leadership'); // leadership | scores | rotation | history
  const [flowValidation, setFlowValidation] = useState(null); // ka10051 신호 검증 현황(2026-09-06)
  const [usLeadlag, setUsLeadlag] = useState(null); // 2026-09-27: 미국 선행 → 국내 후행 신호(국내 모드 전용)
  const isUS = market !== 'kr';

  const load = useCallback(async (mkt) => {
    setLoading(true);
    try {
      if (mkt === 'kr') {
        const [l, s, r, ll] = await Promise.all([
          fetch(API('/api/sector-rotation/leadership?months=36&top_n=3')).then(x => x.json()),
          fetch(API('/api/sector-rotation/scores')).then(x => x.json()),
          fetch(API('/api/sector-rotation/rotation-map')).then(x => x.json()),
          fetch(API('/api/sector-rotation/us-leadlag?universe=sp500')).then(x => x.json()).catch(() => null),
        ]);
        setLeadership(l); setScores(s); setRotMap(r); setUsLeadlag(ll);
      } else {
        const l = await fetch(API(`/api/us-sector-rotation/leadership?universe=${mkt}`)).then(x => x.json());
        setLeadership(l);
        setScores({ meta: l.meta, sectors: [...l.sectors].sort((a, b) => b.score - a.score) });
        setRotMap({ meta: l.meta, sectors: l.sectors.map(s => ({ sector: s.sector, label: s.label, color: s.color, rs4w: s.rs4w, rs12w: s.rs12w, phase: s.phase })) });
      }
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshCache = useCallback(async () => {
    setLoading(true);
    try {
      await fetch(API(market === 'kr' ? '/api/sector-rotation/refresh-cache' : '/api/us-sector-rotation/refresh-cache'), { method: 'POST' });
      await load(market);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [load, market]);

  const loadHistory = useCallback(async (sectorKey) => {
    if (isUS) return; // 미국은 월별 히스토리 미제공(가격 데이터만으로 빠르게 응답하기 위한 범위 축소)
    try {
      const r = await fetch(API(`/api/sector-rotation/history/${encodeURIComponent(sectorKey)}?months=36`)).then(x => x.json());
      setHistory(r);
      setSelSector(sectorKey);
      setTab('history');
    } catch (e) { console.error(e); }
  }, [isUS]);

  const loadTopPicks = useCallback(async (sectorKey) => {
    if (expandedSector === sectorKey) {
      setExpandedSector(null);
      return;
    }
    try {
      const url = market === 'kr'
        ? `/api/sector-rotation/top-picks/${encodeURIComponent(sectorKey)}`
        : `/api/us-sector-rotation/top-picks/${encodeURIComponent(sectorKey)}?universe=${market}`;
      const r = await fetch(API(url)).then(x => x.json());
      setPicks(p => ({ ...p, [sectorKey]: r.picks || [] }));
      setExpandedSector(sectorKey);
    } catch (e) { console.error(e); }
  }, [expandedSector, market]);

  useEffect(() => {
    setExpandedSector(null); setPicks({}); setHistory(null);
    if (isUS && tab === 'history') setTab('leadership');
    load(market);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [market]);

  // ka10051(업종별투자자순매수) 신호가 실제로 쓸모 있는지 매일 쌓이는 스냅샷으로
  // 누적 검증 중 — 2026-09-06: "몇 주 뒤 로그 확인" 방식은 사람이 잊어버리니
  // 화면에서 항상 바로 보이게 함. 메인 load()와 분리해 실패해도 다른 탭에 영향 없음.
  useEffect(() => {
    fetch(API('/api/sector-rotation/flow-signal-validation'))
      .then(r => r.json())
      .then(setFlowValidation)
      .catch(() => {});
  }, []);

  const card = (style = {}) => ({
    background: 'rgba(248,250,252,0.8)',
    border: '1px solid rgba(51,65,85,0.6)',
    borderRadius: '0.75rem',
    padding: '1rem',
    ...style,
  });
  const fmtPct = (v, digits = 1) => v === null || v === undefined ? '-' : `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(digits)}%`;
  const fmtEok = (v) => v === null || v === undefined ? '-' : `${Number(v) > 0 ? '+' : ''}${Math.round(Number(v)).toLocaleString()}억`;
  const meta = leadership?.meta || scores?.meta || rotMap?.meta || null;
  const usLeadlagBySector = {};
  (usLeadlag?.sectors || []).forEach(s => { if (s.mapped) usLeadlagBySector[s.kr_sector] = s; });
  const TIER_COLOR = { strong: '#15803d', moderate: '#b45309', weak: '#64748b' };

  return (
    <div style={{ maxWidth: 1280, margin: '0 auto', padding: '0 1rem 2rem' }}>
      {/* 헤더 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.2rem', flexWrap: 'wrap', gap: '0.75rem' }}>
        <div>
          <h2 style={{ color: '#1e293b', margin: 0, fontSize: '1.2rem', fontWeight: 700 }}>🔄 주도섹터·주도주 판정</h2>
          <p style={{ color: '#1e293b', fontSize: '0.82rem', margin: '0.25rem 0 0' }}>
            {market === 'kr'
              ? '외국인/기관 3개월 순매수 + 영업이익YoY 기반 선행 신호 · 실증: 화장품 BUY신호 2024-01 → 급등 2024-05 (4개월 선행)'
              : '가격·거래량 기반 RS 로테이션(수급·실적 데이터 없음)'}
          </p>
          {meta && (
            <p style={{ color: '#334155', fontSize: '0.75rem', margin: '0.35rem 0 0' }}>
              {meta.market_status_label || '캐시 기준'} · 기준일 {meta.as_of || leadership?.as_of || '-'} · 마지막 계산 {meta.computed_at || '-'}
            </p>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
          <div style={{ display: 'flex', gap: '0.3rem', background: 'rgba(248,250,252,0.6)', border: '1px solid rgba(51,65,85,0.4)', borderRadius: '0.5rem', padding: '0.2rem' }}>
            {MARKETS.map(m => (
              <button key={m.key} onClick={() => setMarket(m.key)}
                style={{ background: market === m.key ? '#4f46e5' : 'transparent', border: 'none', borderRadius: '0.35rem', padding: '0.32rem 0.7rem', color: market === m.key ? '#fff' : '#1e293b', cursor: 'pointer', fontSize: '0.78rem', fontWeight: market === m.key ? 700 : 500 }}>
                {m.label}
              </button>
            ))}
          </div>
          <button onClick={refreshCache} disabled={loading}
            style={{ background: 'rgba(79,70,229,0.2)', border: '1px solid rgba(79,70,229,0.4)', borderRadius: '0.5rem', padding: '0.4rem 1rem', color: '#4f46e5', cursor: 'pointer', fontSize: '0.85rem' }}>
            {loading ? '계산 중…' : '🔄 즉시 재계산'}
          </button>
        </div>
      </div>

      {/* ⚠ 리더종목 판정 기준 차이 — 국내/미국 반대 방향. 탭·토글과 무관하게 항상 노출(가장 자주 오해하기 쉬운 지점). */}
      <div style={{ display: 'flex', gap: '0.6rem', marginBottom: '1rem', flexWrap: 'wrap' }}>
        <div style={{ flex: '1 1 260px', border: `2px solid ${!isUS ? '#dc2626' : 'rgba(51,65,85,0.25)'}`, background: !isUS ? 'rgba(220,38,38,0.06)' : 'rgba(248,250,252,0.4)', borderRadius: '0.6rem', padding: '0.55rem 0.8rem', opacity: !isUS ? 1 : 0.55 }}>
          <div style={{ fontWeight: 800, color: '#dc2626', fontSize: '0.8rem' }}>🇰🇷 국내 리더종목 = 저평가 반전 후보</div>
          <div style={{ color: '#334155', fontSize: '0.72rem', marginTop: 2 }}>52주 <b>저점</b> 근처 + 수급·실적 개선 초입에 가점 — 아직 안 오른 종목 중 곧 오를 후보</div>
        </div>
        <div style={{ flex: '1 1 260px', border: `2px solid ${isUS ? '#15803d' : 'rgba(51,65,85,0.25)'}`, background: isUS ? 'rgba(21,128,61,0.06)' : 'rgba(248,250,252,0.4)', borderRadius: '0.6rem', padding: '0.55rem 0.8rem', opacity: isUS ? 1 : 0.55 }}>
          <div style={{ fontWeight: 800, color: '#15803d', fontSize: '0.8rem' }}>🇺🇸 미국 리더종목 = 신고가 모멘텀 추종</div>
          <div style={{ color: '#334155', fontSize: '0.72rem', marginTop: 2 }}>52주 <b>고점</b>권 + 벤치마크 대비 초과수익에 가점(IBD/오닐 스타일) — 이미 오르는 종목 중 더 갈 후보</div>
        </div>
      </div>

      {/* 탭 — 미국은 월별 히스토리 미제공(가격만으로 즉시 응답하기 위해 범위 축소) */}
      <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem' }}>
        {[['leadership','🚦 주도섹터·진입'], ['scores','📊 섹터 스코어'], ['rotation','🗺 4분면 맵'], ...(isUS ? [] : [['history','📈 RS 히스토리']])].map(([k, lbl]) => (
          <button key={k} onClick={() => setTab(k)}
            style={{ background: tab === k ? 'rgba(79,70,229,0.3)' : 'rgba(248,250,252,0.6)', border: `1px solid ${tab === k ? '#4f46e5' : 'rgba(51,65,85,0.5)'}`, borderRadius: '0.5rem', padding: '0.4rem 0.9rem', color: tab === k ? '#4f46e5' : '#1e293b', cursor: 'pointer', fontSize: '0.82rem' }}>
            {lbl}
          </button>
        ))}
      </div>

      {/* 키움 ka10051 업종수급 신호 검증 현황 — 국내 전용, 매일 쌓이는 스냅샷 기반, 매주 월요일 텔레그램도 발송 */}
      {!isUS && flowValidation?.markets && (
        <div style={{ background: 'rgba(248,250,252,0.55)', border: '1px solid rgba(51,65,85,0.5)', borderRadius: '0.6rem', padding: '0.6rem 0.9rem', marginBottom: '1rem', fontSize: '0.78rem' }}>
          <span style={{ color: '#334155', fontWeight: 700 }}>🧪 키움 업종수급(ka10051) 신호 검증: </span>
          {Object.entries(flowValidation.markets).map(([mtype, m], i) => (
            <span key={mtype} style={{ color: '#1e293b' }}>
              {i > 0 && ' · '}
              {mtype === '0' ? '코스피' : mtype === '1' ? '코스닥' : mtype} 수집 {m.days_collected}일(검증쌍 {m.day_pairs_available}개)
              {m.spearman_corr_flow_vs_next_return != null && (
                <> — 상관계수 {m.spearman_corr_flow_vs_next_return > 0 ? '+' : ''}{m.spearman_corr_flow_vs_next_return}, 적중률 {m.same_direction_hit_rate_pct}%</>
              )}
            </span>
          ))}
          <div style={{ color: '#1e293b', marginTop: '0.2rem' }}>
            {Object.values(flowValidation.markets)[0]?.verdict}
          </div>
        </div>
      )}

      {/* 🇺🇸→🇰🇷 미국 선행 신호 요약 — 국내 전용, 2026-09-27 신규(사용자 지시: "미국 결과를 국내에 적용 가능하도록") */}
      {!isUS && usLeadlag && (
        <div style={{ background: 'rgba(37,99,235,0.06)', border: '1px solid rgba(37,99,235,0.3)', borderRadius: '0.6rem', padding: '0.7rem 0.9rem', marginBottom: '1rem', fontSize: '0.78rem' }}>
          <div style={{ color: '#1e40af', fontWeight: 800, marginBottom: '0.4rem' }}>🇺🇸→🇰🇷 미국 선행 신호 (국내 시장은 미국을 하루~한 주 늦게 따라가는 경향 — 과거 방향일치율로 판정)</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem' }}>
            {usLeadlag.sectors.filter(s => s.mapped).map(s => {
              const bull = s.implied_direction === '상승 시사';
              const bear = s.implied_direction === '하락 시사';
              const tc = TIER_COLOR[s.tier];
              return (
                <span key={s.kr_sector} title={`${s.us_label}(${s.us_etf}) 현재 ${s.us_phase} · 검증 적중률 ${s.test_hit_pct}% · 상관계수 ${s.corr}`}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 4, background: bull ? 'rgba(220,38,38,0.1)' : bear ? 'rgba(37,99,235,0.1)' : 'rgba(100,116,139,0.1)',
                    border: `1px solid ${bull ? '#dc2626' : bear ? '#2563eb' : '#64748b'}`, borderRadius: '0.4rem', padding: '0.2rem 0.5rem', cursor: 'default' }}>
                  <span style={{ color: bull ? '#dc2626' : bear ? '#2563eb' : '#64748b', fontWeight: 700 }}>{bull ? '▲' : bear ? '▼' : '－'} {s.kr_label}</span>
                  <span style={{ width: 6, height: 6, borderRadius: '50%', background: tc, display: 'inline-block' }} title={s.tier_label} />
                </span>
              );
            })}
          </div>
          <div style={{ color: '#334155', marginTop: '0.4rem', fontSize: '0.7rem' }}>
            {usLeadlag.methodology} · 점 색상 = 검증 신뢰도(<span style={{ color: TIER_COLOR.strong }}>●강함</span> <span style={{ color: TIER_COLOR.moderate }}>●보통</span> <span style={{ color: TIER_COLOR.weak }}>●약함</span>) · 원자력·2차전지는 미국 GICS 섹터와 깔끔히 대응되지 않아 제외
          </div>
        </div>
      )}

      {/* 탭 0: 주도섹터·진입 타이밍 */}
      {tab === 'leadership' && leadership && (
        <div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: '0.75rem', marginBottom: '1rem' }}>
            {[
              ['진입 섹터', leadership.summary?.entry_now || 0, '#15803d'],
              ['초기 관찰', leadership.summary?.watch || 0, '#b45309'],
              ['주도 국면', leadership.summary?.leading || 0, '#2563eb'],
              ['기준', meta?.market_status_label || leadership.as_of || '-', '#6d28d9'],
            ].map(([label, value, color]) => (
              <div key={label} style={card({ padding: '0.85rem 1rem' })}>
                <div style={{ color: '#1e293b', fontSize: '0.72rem', fontWeight: 700, marginBottom: '0.25rem' }}>{label}</div>
                <div style={{ color, fontSize: label === '기준' ? '0.95rem' : '1.55rem', fontWeight: 900 }}>{value}</div>
              </div>
            ))}
          </div>

          <div style={card({ padding: 0, overflow: 'hidden' })}>
            <div style={{ padding: '0.85rem 1rem', borderBottom: '1px solid rgba(51,65,85,0.55)', display: 'flex', justifyContent: 'space-between', gap: '1rem', alignItems: 'center' }}>
              <div style={{ color: '#1e293b', fontWeight: 800 }}>주도섹터 진입 테이블</div>
              <div style={{ color: '#1e293b', fontSize: '0.75rem' }}>수급·실적·수출·거래량·RS·주도주 압축 점수</div>
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', minWidth: 1180, borderCollapse: 'collapse', fontSize: '0.78rem' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid rgba(51,65,85,0.5)', background: 'rgba(255,255,255,0.35)' }}>
                    {(isUS
                      ? ['단계', '섹터', '점수/국면', '핵심 근거', '수익률(4W/12W)', '거래량비·섹터폭', 'RS', '주도주 TOP3', '벤치마크']
                      : ['단계', '섹터', '점수/국면', '핵심 근거', '수급 3M', '수출/실적', 'RS', '주도주 TOP3', '최근 강신호']
                    ).map(h => (
                      <th key={h} style={{ padding: '0.55rem 0.65rem', color: '#334155', fontWeight: 700, textAlign: 'left', whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {leadership.sectors.map((s) => {
                    const st = STAGE_COLOR[s.stage] || STAGE_COLOR.WAIT;
                    const ph = PHASE_COLOR[s.phase] || PHASE_COLOR.Lagging;
                    const d = s.detail || {};
                    const peak = s.peak_signal;
                    const latestBuy = s.latest_buy_signal;
                    return (
                      <tr key={s.sector} style={{ borderBottom: '1px solid rgba(248,250,252,0.65)', background: s.stage === 'ENTRY_NOW' ? 'rgba(22,163,74,0.04)' : 'transparent' }}>
                        <td style={{ padding: '0.65rem', verticalAlign: 'top' }}>
                          <span style={{ display: 'inline-block', minWidth: 62, textAlign: 'center', background: st.bg, border: `1px solid ${st.border}`, borderRadius: '0.35rem', padding: '0.18rem 0.45rem', color: st.text, fontWeight: 800, fontSize: '0.72rem' }}>
                            {s.stage_label || st.label}
                          </span>
                        </td>
                        <td style={{ padding: '0.65rem', color: '#1e293b', fontWeight: 800, verticalAlign: 'top', whiteSpace: 'nowrap' }}>
                          {s.label}
                          {!isUS && (
                            <div style={{ marginTop: 4 }}>
                              <button onClick={() => loadHistory(s.sector)}
                                style={{ background: 'rgba(79,70,229,0.12)', border: '1px solid rgba(79,70,229,0.28)', borderRadius: 4, padding: '0.12rem 0.45rem', color: '#4f46e5', cursor: 'pointer', fontSize: '0.68rem' }}>
                                히스토리
                              </button>
                            </div>
                          )}
                          {!isUS && usLeadlagBySector[s.sector] && (() => {
                            const ll = usLeadlagBySector[s.sector];
                            const bull = ll.implied_direction === '상승 시사';
                            const bear = ll.implied_direction === '하락 시사';
                            return (
                              <div title={`${ll.us_label} 현재 ${ll.us_phase} · 검증 적중률 ${ll.test_hit_pct}%`}
                                style={{ marginTop: 4, fontSize: '0.64rem', fontWeight: 700, color: bull ? '#dc2626' : bear ? '#2563eb' : '#64748b' }}>
                                🇺🇸{bull ? '▲' : bear ? '▼' : '－'}{ll.us_label.replace(/^[^가-힣]*/, '')}
                              </div>
                            );
                          })()}
                        </td>
                        <td style={{ padding: '0.65rem', verticalAlign: 'top' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '0.45rem', marginBottom: 5 }}>
                            <div style={{ width: 58, height: 6, background: 'rgba(51,65,85,0.55)', borderRadius: 4 }}>
                              <div style={{ width: `${Math.max(0, Math.min(100, s.score || 0))}%`, height: '100%', background: st.border, borderRadius: 4 }} />
                            </div>
                            <span style={{ color: st.text, fontWeight: 900 }}>{s.score}</span>
                          </div>
                          <span style={{ background: ph.bg, border: `1px solid ${ph.border}`, borderRadius: 4, padding: '0.12rem 0.45rem', color: ph.text, fontSize: '0.68rem', fontWeight: 700 }}>{ph.label}</span>
                        </td>
                        <td style={{ padding: '0.65rem', verticalAlign: 'top', minWidth: 190 }}>
                          {(s.entry_reasons || []).map((r) => (
                            <div key={r} style={{ color: r === '선행 신호 부족' ? '#1e293b' : '#1e293b', lineHeight: 1.55 }}>{r}</div>
                          ))}
                        </td>
                        {isUS ? (
                          <>
                            <td style={{ padding: '0.65rem', verticalAlign: 'top', whiteSpace: 'nowrap' }}>
                              <div style={{ color: (d.ret_4w_pct || 0) > 0 ? '#15803d' : '#dc2626', fontWeight: 700 }}>4W {fmtPct(d.ret_4w_pct)}</div>
                              <div style={{ color: (d.ret_12w_pct || 0) > 0 ? '#15803d' : '#dc2626', fontWeight: 700, marginTop: 4 }}>12W {fmtPct(d.ret_12w_pct)}</div>
                            </td>
                            <td style={{ padding: '0.65rem', verticalAlign: 'top', whiteSpace: 'nowrap' }}>
                              <div style={{ color: (d.vol_ratio || 1) >= 1.5 ? '#b45309' : '#334155' }}>거래량 {d.vol_ratio ? `${d.vol_ratio}x` : '-'}</div>
                              <div style={{ color: (d.breadth_pct || 0) >= 40 ? '#15803d' : '#334155', marginTop: 4 }}>섹터폭 {d.breadth_pct != null ? `${d.breadth_pct}%` : '-'}</div>
                            </td>
                          </>
                        ) : (
                          <>
                            <td style={{ padding: '0.65rem', verticalAlign: 'top', whiteSpace: 'nowrap' }}>
                              <div style={{ color: (d.frn_3m_억 || 0) > 0 ? '#15803d' : '#dc2626', fontWeight: 700 }}>외인 {fmtEok(d.frn_3m_억)}</div>
                              <div style={{ color: (d.inst_3m_억 || 0) > 0 ? '#2563eb' : '#dc2626', fontWeight: 700, marginTop: 4 }}>기관 {fmtEok(d.inst_3m_억)}</div>
                            </td>
                            <td style={{ padding: '0.65rem', verticalAlign: 'top', whiteSpace: 'nowrap' }}>
                              <div style={{ color: (d.hs_export_yoy || 0) >= 15 ? '#15803d' : (d.hs_export_yoy || 0) < -10 ? '#dc2626' : '#334155' }}>수출 {fmtPct(d.hs_export_yoy, 0)}</div>
                              <div style={{ color: (d.op_yoy || 0) >= 20 ? '#15803d' : (d.op_yoy || 0) < -20 ? '#dc2626' : '#334155', marginTop: 4 }}>OP {fmtPct(d.op_yoy, 0)}</div>
                              <div style={{ color: (d.vol_ratio || 1) >= 1.5 ? '#b45309' : '#1e293b', marginTop: 4 }}>거래량 {d.vol_ratio ? `${d.vol_ratio}x` : '-'}</div>
                            </td>
                          </>
                        )}
                        <td style={{ padding: '0.65rem', verticalAlign: 'top', whiteSpace: 'nowrap' }}>
                          <div style={{ color: (s.rs4w || 0) > 0 ? '#15803d' : '#dc2626', fontWeight: 700 }}>4W {fmtPct(s.rs4w)}</div>
                          <div style={{ color: (s.rs12w || 0) > 0 ? '#15803d' : '#dc2626', marginTop: 4 }}>12W {fmtPct(s.rs12w)}</div>
                        </td>
                        <td style={{ padding: '0.65rem', verticalAlign: 'top', minWidth: 230 }}>
                          {(s.leaders || []).map((p, i) => (
                            <div key={p.code} style={{ display: 'grid', gridTemplateColumns: '22px 1fr 42px', gap: '0.35rem', alignItems: 'start', marginBottom: i === (s.leaders.length - 1) ? 0 : 6 }}>
                              <span style={{ color: i === 0 ? '#b45309' : '#1e293b', fontWeight: 900 }}>{i + 1}</span>
                              <div>
                                <span style={{ color: '#1e293b', fontWeight: 800 }}>{p.name}</span>
                                <span style={{ color: '#1e293b', marginLeft: 5 }}>{p.code}</span>
                                <div style={{ color: '#334155', fontSize: '0.68rem', marginTop: 2 }}>{(p.reasons || []).join(' · ')}</div>
                              </div>
                              <span style={{ color: (p.surge_score || 0) >= 50 ? '#15803d' : (p.surge_score || 0) >= 30 ? '#b45309' : '#334155', fontWeight: 900, textAlign: 'right' }}>{p.surge_score}</span>
                            </div>
                          ))}
                        </td>
                        <td style={{ padding: '0.65rem', verticalAlign: 'top', minWidth: 140 }}>
                          {isUS ? (
                            <div style={{ color: '#334155' }}>{d.benchmark || '-'} 대비 초과수익<br/>(월별 히스토리 미제공)</div>
                          ) : peak ? (
                            <>
                              <div style={{ color: '#1e293b', fontWeight: 700 }}>최고 {peak.month} · {peak.score}점</div>
                              <div style={{ color: latestBuy ? '#15803d' : '#1e293b', marginTop: 4 }}>
                                최근 BUY {latestBuy ? `${latestBuy.month} · ${latestBuy.score}점` : '-'}
                              </div>
                              <div style={{ display: 'flex', gap: 2, alignItems: 'flex-end', height: 24, marginTop: 7 }}>
                                {(s.history_recent || []).map((h) => (
                                  <div key={h.month} title={`${h.month} ${h.score}점`}
                                    style={{ width: 7, height: Math.max(4, Math.min(24, h.score / 4)), borderRadius: 2, background: h.signal === 'BUY' ? '#15803d' : h.signal === 'WATCH' ? '#b45309' : '#f4f6fb' }} />
                                ))}
                              </div>
                            </>
                          ) : '-'}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* 탭 1: 섹터 스코어 */}
      {tab === 'scores' && scores && (
        <div>
          {/* 상단 요약 카드 */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: '0.75rem', marginBottom: '1.2rem' }}>
            {scores.sectors.slice(0, 3).map(s => {
              const sc = SIGNAL_COLOR[s.signal];
              const d = s.detail;
              return (
                <div key={s.sector} style={{ ...card(), background: sc.bg, border: `1px solid ${sc.border}`, cursor: 'pointer' }}
                  onClick={() => loadHistory(s.sector)}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.4rem' }}>
                    <span style={{ color: sc.text, fontWeight: 700, fontSize: '1rem' }}>{s.label}</span>
                    <span style={{ background: sc.bg, border: `1px solid ${sc.border}`, borderRadius: '0.3rem', padding: '0.15rem 0.5rem', color: sc.text, fontSize: '0.75rem', fontWeight: 700 }}>{sc.label}</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: '0.5rem' }}>
                    <span style={{ fontSize: '2rem', fontWeight: 900, color: sc.text }}>{s.score}</span>
                    <span style={{ color: '#1e293b', fontSize: '0.8rem' }}>점</span>
                  </div>
                  {d.pattern && (
                    <div style={{ marginTop: '0.3rem', fontSize: '0.75rem', color: '#b45309', fontWeight: 700 }}>{d.pattern}</div>
                  )}
                  <div style={{ display: 'flex', gap: '1rem', marginTop: '0.3rem' }}>
                    {d.frn_3m_억 !== undefined && (
                      <span style={{ fontSize: '0.72rem', color: d.frn_3m_억 > 0 ? '#15803d' : '#dc2626' }}>
                        외국인 {d.frn_3m_억 > 0 ? '+' : ''}{(d.frn_3m_억||0).toLocaleString()}억
                      </span>
                    )}
                    {d.inst_3m_억 !== undefined && (
                      <span style={{ fontSize: '0.72rem', color: d.inst_3m_억 > 0 ? '#2563eb' : '#dc2626' }}>
                        기관 {d.inst_3m_억 > 0 ? '+' : ''}{(d.inst_3m_억||0).toLocaleString()}억
                      </span>
                    )}
                  </div>
                  {d.hs_export_yoy !== null && d.hs_export_yoy !== undefined && (
                    <div style={{ marginTop: '0.2rem', fontSize: '0.72rem', color: d.hs_export_yoy > 15 ? '#15803d' : d.hs_export_yoy > 0 ? '#15803d' : '#dc2626' }}>
                      수출YoY {d.hs_export_yoy > 0 ? '+' : ''}{d.hs_export_yoy}%
                    </div>
                  )}
                  {d.op_yoy !== null && d.op_yoy !== undefined && (
                    <div style={{ marginTop: '0.2rem', fontSize: '0.72rem', color: d.op_yoy > 50 ? '#15803d' : d.op_yoy > 0 ? '#15803d' : '#dc2626' }}>
                      영업이익YoY {d.op_yoy > 0 ? '+' : ''}{d.op_yoy}%
                    </div>
                  )}
                  {isUS && (
                    <div style={{ display: 'flex', gap: '0.75rem', marginTop: '0.3rem', fontSize: '0.72rem' }}>
                      <span style={{ color: (d.ret_4w_pct||0) > 0 ? '#15803d' : '#dc2626' }}>4W {fmtPct(d.ret_4w_pct)}</span>
                      <span style={{ color: (d.ret_12w_pct||0) > 0 ? '#15803d' : '#dc2626' }}>12W {fmtPct(d.ret_12w_pct)}</span>
                      <span style={{ color: '#334155' }}>거래량 {d.vol_ratio ? `${d.vol_ratio}x` : '-'}</span>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* 전체 섹터 테이블 */}
          <div style={card()}>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid rgba(51,65,85,0.5)' }}>
                    {(isUS
                      ? ['섹터', '점수', '신호', 'RS4주', 'RS12주', '거래량비', '섹터폭', '4W수익률', '12W수익률', '리더']
                      : ['섹터', '점수', '신호', '패턴', '외국인3M', '기관3M', '수출YoY', '영업이익YoY', '거래량비', 'RS4주', '히스토리']
                    ).map(h => (
                      <th key={h} style={{ padding: '0.5rem 0.75rem', color: '#1e293b', fontWeight: 600, textAlign: 'left', whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {scores.sectors.map(s => {
                    const sc = SIGNAL_COLOR[s.signal];
                    const d = s.detail;
                    const fmt억 = v => v !== undefined ? `${v > 0 ? '+' : ''}${Math.round(v).toLocaleString()}억` : '-';
                    if (isUS) {
                      return (
                        <Fragment key={s.sector}>
                        <tr style={{ borderBottom: '1px solid rgba(248,250,252,0.6)' }}>
                          <td style={{ padding: '0.6rem 0.75rem', color: '#1e293b', fontWeight: 600 }}>{s.label}</td>
                          <td style={{ padding: '0.6rem 0.75rem' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                              <div style={{ width: 50, height: 5, background: 'rgba(51,65,85,0.5)', borderRadius: 3 }}>
                                <div style={{ width: `${Math.max(0,s.score)}%`, height: '100%', background: sc.border, borderRadius: 3 }}/>
                              </div>
                              <span style={{ color: sc.text, fontWeight: 700 }}>{s.score}</span>
                            </div>
                          </td>
                          <td style={{ padding: '0.6rem 0.75rem' }}>
                            <span style={{ background: sc.bg, border: `1px solid ${sc.border}`, borderRadius: '0.25rem', padding: '0.1rem 0.4rem', color: sc.text, fontSize: '0.72rem', fontWeight: 700 }}>{sc.label}</span>
                          </td>
                          <td style={{ padding: '0.6rem 0.75rem', color: (s.rs4w||0) > 0 ? '#15803d' : '#dc2626', fontWeight: 600 }}>{fmtPct(s.rs4w)}</td>
                          <td style={{ padding: '0.6rem 0.75rem', color: (s.rs12w||0) > 0 ? '#15803d' : '#dc2626', fontWeight: 600 }}>{fmtPct(s.rs12w)}</td>
                          <td style={{ padding: '0.6rem 0.75rem', color: (d.vol_ratio||1) > 1.5 ? '#b45309' : '#334155' }}>{d.vol_ratio ? `${d.vol_ratio}x` : '-'}</td>
                          <td style={{ padding: '0.6rem 0.75rem', color: (d.breadth_pct||0) >= 40 ? '#15803d' : '#334155' }}>{d.breadth_pct != null ? `${d.breadth_pct}%` : '-'}</td>
                          <td style={{ padding: '0.6rem 0.75rem', color: '#334155' }}>{fmtPct(d.ret_4w_pct)}</td>
                          <td style={{ padding: '0.6rem 0.75rem', color: '#334155' }}>{fmtPct(d.ret_12w_pct)}</td>
                          <td style={{ padding: '0.6rem 0.75rem' }}>
                            <button onClick={() => loadTopPicks(s.sector)}
                              style={{ background: expandedSector === s.sector ? 'rgba(22,163,74,0.2)' : 'rgba(22,163,74,0.1)', border: `1px solid ${expandedSector === s.sector ? '#15803d' : 'rgba(22,163,74,0.3)'}`, borderRadius: '0.25rem', padding: '0.15rem 0.5rem', color: '#15803d', cursor: 'pointer', fontSize: '0.72rem' }}>
                              🏆 픽
                            </button>
                          </td>
                        </tr>
                        {expandedSector === s.sector && picks[s.sector] && (
                          <tr>
                            <td colSpan={10} style={{ padding: '0 0.75rem 0.75rem', background: 'rgba(255,255,255,0.6)' }}>
                              <div style={{ borderTop: '1px solid rgba(22,163,74,0.3)', paddingTop: '0.5rem', fontSize: '0.75rem' }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.35rem' }}>
                                  <span style={{ color: '#15803d', fontWeight: 700 }}>🏆 {s.label} 리더종목</span>
                                  <span style={{ color: '#1e293b', fontSize: '0.68rem' }}>모멘텀점수 = 벤치마크대비3M초과(25) + 거래량비(20) + 52주고점권(25) + 3M수익률(15)</span>
                                </div>
                                <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                                  <thead>
                                    <tr style={{ color: '#1e293b', borderBottom: '1px solid rgba(51,65,85,0.5)' }}>
                                      {['모멘텀점수','종목','시총($)','벤치마크대비3M','거래량비','52주위치(고점=100)','3M수익률'].map(h =>
                                        <th key={h} style={{ padding: '0.25rem 0.4rem', textAlign: 'left', fontWeight: 600, fontSize: '0.68rem' }}>{h}</th>
                                      )}
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {picks[s.sector].map((p, i) => {
                                      const scoreColor = p.surge_score >= 50 ? '#15803d' : p.surge_score >= 30 ? '#b45309' : '#334155';
                                      const mcap = p.market_cap_달러;
                                      const mcapStr = mcap ? (mcap >= 1e12 ? `${(mcap/1e12).toFixed(2)}조$` : mcap >= 1e9 ? `${(mcap/1e9).toFixed(1)}B$` : `${(mcap/1e6).toFixed(0)}M$`) : '-';
                                      return (
                                        <tr key={p.code} style={{ borderTop: '1px solid rgba(248,250,252,0.4)', background: i === 0 ? 'rgba(22,163,74,0.04)' : 'transparent' }}>
                                          <td style={{ padding: '0.3rem 0.4rem', fontWeight: 700, color: scoreColor, fontSize: '0.85rem' }}>{p.surge_score}</td>
                                          <td style={{ padding: '0.3rem 0.4rem', color: '#1e293b', fontWeight: 600 }}>{p.name}
                                            <div style={{ fontSize: '0.65rem', color: '#1e293b' }}>{p.code}</div>
                                          </td>
                                          <td style={{ padding: '0.3rem 0.4rem', color: '#334155', fontSize: '0.72rem' }}>{mcapStr}</td>
                                          <td style={{ padding: '0.3rem 0.4rem', color: (p.rs_3m_excess||0) > 10 ? '#15803d' : (p.rs_3m_excess||0) < 0 ? '#dc2626' : '#334155', fontWeight: 700 }}>
                                            {p.rs_3m_excess !== null && p.rs_3m_excess !== undefined ? `${p.rs_3m_excess > 0 ? '+' : ''}${p.rs_3m_excess}%` : '-'}
                                          </td>
                                          <td style={{ padding: '0.3rem 0.4rem', color: (p.vol_ratio||1) >= 1.5 ? '#b45309' : '#334155', fontSize: '0.72rem' }}>{p.vol_ratio ? `${p.vol_ratio}x` : '-'}</td>
                                          <td style={{ padding: '0.3rem 0.4rem', fontSize: '0.72rem' }}>
                                            <div style={{ width: 60, height: 4, background: 'rgba(51,65,85,0.5)', borderRadius: 2, display: 'inline-block', verticalAlign: 'middle' }}>
                                              <div style={{ width: `${Math.min(100, p.pos_52w_pct||0)}%`, height: '100%', background: (p.pos_52w_pct||0) >= 80 ? '#15803d' : (p.pos_52w_pct||0) < 30 ? '#dc2626' : '#b45309', borderRadius: 2 }}/>
                                            </div>
                                            <span style={{ marginLeft: 4, color: (p.pos_52w_pct||0) >= 80 ? '#15803d' : (p.pos_52w_pct||0) < 30 ? '#dc2626' : '#b45309' }}>{p.pos_52w_pct ?? '-'}%</span>
                                          </td>
                                          <td style={{ padding: '0.3rem 0.4rem', color: (p.ret_3m||0) > 20 ? '#15803d' : (p.ret_3m||0) < -10 ? '#dc2626' : '#b45309', fontWeight: 700 }}>
                                            {p.ret_3m !== null && p.ret_3m !== undefined ? `${p.ret_3m > 0 ? '+' : ''}${p.ret_3m}%` : '-'}
                                          </td>
                                        </tr>
                                      );
                                    })}
                                  </tbody>
                                </table>
                              </div>
                            </td>
                          </tr>
                        )}
                        </Fragment>
                      );
                    }
                    return (
                      <>
                      <tr key={s.sector} style={{ borderBottom: '1px solid rgba(248,250,252,0.6)' }}>
                        <td style={{ padding: '0.6rem 0.75rem', color: '#1e293b', fontWeight: 600 }}>{s.label}</td>
                        <td style={{ padding: '0.6rem 0.75rem' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                            <div style={{ width: 50, height: 5, background: 'rgba(51,65,85,0.5)', borderRadius: 3 }}>
                              <div style={{ width: `${Math.max(0,s.score)}%`, height: '100%', background: sc.border, borderRadius: 3 }}/>
                            </div>
                            <span style={{ color: sc.text, fontWeight: 700 }}>{s.score}</span>
                          </div>
                        </td>
                        <td style={{ padding: '0.6rem 0.75rem' }}>
                          <span style={{ background: sc.bg, border: `1px solid ${sc.border}`, borderRadius: '0.25rem', padding: '0.1rem 0.4rem', color: sc.text, fontSize: '0.72rem', fontWeight: 700 }}>{sc.label}</span>
                        </td>
                        <td style={{ padding: '0.6rem 0.75rem', color: '#b45309', fontWeight: 700, fontSize: '0.75rem' }}>
                          {d.pattern || '-'}
                        </td>
                        <td style={{ padding: '0.6rem 0.75rem', color: (d.frn_3m_억||0) > 300 ? '#15803d' : (d.frn_3m_억||0) < -300 ? '#dc2626' : '#334155', fontWeight: 600 }}>
                          {fmt억(d.frn_3m_억)}
                        </td>
                        <td style={{ padding: '0.6rem 0.75rem', color: (d.inst_3m_억||0) > 200 ? '#2563eb' : (d.inst_3m_억||0) < -200 ? '#dc2626' : '#334155', fontWeight: 600 }}>
                          {fmt억(d.inst_3m_억)}
                        </td>
                        <td style={{ padding: '0.6rem 0.75rem', color: (d.hs_export_yoy||0) > 15 ? '#15803d' : (d.hs_export_yoy||0) < -10 ? '#dc2626' : '#334155', fontWeight: 600 }}>
                          {d.hs_export_yoy !== null && d.hs_export_yoy !== undefined ? `${d.hs_export_yoy > 0 ? '+' : ''}${d.hs_export_yoy}%` : '-'}
                        </td>
                        <td style={{ padding: '0.6rem 0.75rem', color: (d.op_yoy||0) > 50 ? '#15803d' : (d.op_yoy||0) > 0 ? '#15803d' : (d.op_yoy||0) < -30 ? '#dc2626' : '#334155', fontWeight: 600 }}>
                          {d.op_yoy !== null && d.op_yoy !== undefined ? `${d.op_yoy > 0 ? '+' : ''}${d.op_yoy}%` : '-'}
                        </td>
                        <td style={{ padding: '0.6rem 0.75rem', color: (d.vol_ratio||1) > 1.5 ? '#b45309' : '#334155' }}>
                          {d.vol_ratio ? `${d.vol_ratio}x` : '-'}
                        </td>
                        <td style={{ padding: '0.6rem 0.75rem', color: (d.rs4w_excess||0) > 5 ? '#15803d' : (d.rs4w_excess||0) < -5 ? '#dc2626' : '#334155' }}>
                          {d.rs4w_excess !== undefined ? `${d.rs4w_excess > 0 ? '+' : ''}${d.rs4w_excess}%` : '-'}
                        </td>
                        <td style={{ padding: '0.6rem 0.75rem', display: 'flex', gap: '0.4rem' }}>
                          <button onClick={() => loadHistory(s.sector)}
                            style={{ background: 'rgba(79,70,229,0.15)', border: '1px solid rgba(79,70,229,0.3)', borderRadius: '0.25rem', padding: '0.15rem 0.5rem', color: '#4f46e5', cursor: 'pointer', fontSize: '0.72rem' }}>
                            📈 RS
                          </button>
                          <button onClick={() => loadTopPicks(s.sector)}
                            style={{ background: expandedSector === s.sector ? 'rgba(22,163,74,0.2)' : 'rgba(22,163,74,0.1)', border: `1px solid ${expandedSector === s.sector ? '#15803d' : 'rgba(22,163,74,0.3)'}`, borderRadius: '0.25rem', padding: '0.15rem 0.5rem', color: '#15803d', cursor: 'pointer', fontSize: '0.72rem' }}>
                            🏆 픽
                          </button>
                        </td>
                      </tr>
                      {expandedSector === s.sector && picks[s.sector] && (
                        <tr key={`picks-${s.sector}`}>
                          <td colSpan={11} style={{ padding: '0 0.75rem 0.75rem', background: 'rgba(255,255,255,0.6)' }}>
                            <div style={{ borderTop: '1px solid rgba(22,163,74,0.3)', paddingTop: '0.5rem', fontSize: '0.75rem' }}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.35rem' }}>
                                <span style={{ color: '#15803d', fontWeight: 700 }}>🏆 {s.label} 급등 후보 종목</span>
                                <span style={{ color: '#1e293b', fontSize: '0.68rem' }}>급등점수 = 영업이익YoY(40) + 기관집중도%(30) + 52주위치(20) + 소형주(10)</span>
                              </div>
                              {/* 스코어링 설명 배너 */}
                              <div style={{ background: 'rgba(79,70,229,0.08)', border: '1px solid rgba(79,70,229,0.2)', borderRadius: '0.35rem', padding: '0.3rem 0.6rem', marginBottom: '0.4rem', fontSize: '0.68rem', color: '#334155', lineHeight: 1.7 }}>
                                실증 ① SK하이닉스 영업이익+404%→주가+290% ② 원익IPS 기관집중도0.43%→+524% ③ 급등주 70.5%는 52주 저점 근처 출발
                              </div>
                              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                                <thead>
                                  <tr style={{ color: '#1e293b', borderBottom: '1px solid rgba(51,65,85,0.5)' }}>
                                    {['급등점수','종목','시총','영업이익YoY','기관집중도%','기관3M','외인3M','52주위치','3M수익률','PBR'].map(h =>
                                      <th key={h} style={{ padding: '0.25rem 0.4rem', textAlign: 'left', fontWeight: 600, fontSize: '0.68rem' }}>{h}</th>
                                    )}
                                  </tr>
                                </thead>
                                <tbody>
                                  {picks[s.sector].map((p, i) => {
                                    const scoreColor = p.surge_score >= 50 ? '#15803d' : p.surge_score >= 30 ? '#b45309' : '#334155';
                                    return (
                                      <tr key={p.code} style={{ borderTop: '1px solid rgba(248,250,252,0.4)', background: i === 0 ? 'rgba(22,163,74,0.04)' : 'transparent' }}>
                                        <td style={{ padding: '0.3rem 0.4rem', fontWeight: 700, color: scoreColor, fontSize: '0.85rem' }}>{p.surge_score}
                                          <div style={{ fontSize: '0.6rem', color: '#1e293b', maxWidth: 120, lineHeight: 1.3 }}>{p.score_detail}</div>
                                        </td>
                                        <td style={{ padding: '0.3rem 0.4rem', color: '#1e293b', fontWeight: 600 }}>{p.name}
                                          <div style={{ fontSize: '0.65rem', color: '#1e293b' }}>{p.code}</div>
                                        </td>
                                        <td style={{ padding: '0.3rem 0.4rem', color: '#334155', fontSize: '0.72rem' }}>{(p.market_cap_억||0) > 10000 ? `${Math.round((p.market_cap_억||0)/10000)}조` : `${(p.market_cap_억||0).toLocaleString()}억`}</td>
                                        <td style={{ padding: '0.3rem 0.4rem', color: (p.op_yoy||0) > 100 ? '#15803d' : (p.op_yoy||0) > 20 ? '#b45309' : (p.op_yoy||0) < 0 ? '#dc2626' : '#334155', fontWeight: 700 }}>
                                          {p.op_yoy !== null && p.op_yoy !== undefined ? `${p.op_yoy > 0 ? '+' : ''}${p.op_yoy}%` : '-'}
                                          {p.op_latest_year && <div style={{ fontSize: '0.6rem', color: '#1e293b' }}>{p.op_latest_year}년기준</div>}
                                        </td>
                                        <td style={{ padding: '0.3rem 0.4rem', color: (p.inst_intensity_pct||0) > 0.3 ? '#2563eb' : (p.inst_intensity_pct||0) < -0.3 ? '#dc2626' : '#334155', fontWeight: (p.inst_intensity_pct||0) > 0.3 ? 700 : 400 }}>
                                          {p.inst_intensity_pct !== undefined ? `${p.inst_intensity_pct > 0 ? '+' : ''}${p.inst_intensity_pct}%` : '-'}
                                        </td>
                                        <td style={{ padding: '0.3rem 0.4rem', color: (p.inst_3m_억||0) > 0 ? '#2563eb' : '#dc2626', fontSize: '0.72rem' }}>{p.inst_3m_억 > 0 ? '+' : ''}{(p.inst_3m_억||0).toLocaleString()}억</td>
                                        <td style={{ padding: '0.3rem 0.4rem', color: (p.frn_3m_억||0) > 0 ? '#15803d' : '#dc2626', fontSize: '0.72rem' }}>{p.frn_3m_억 > 0 ? '+' : ''}{(p.frn_3m_억||0).toLocaleString()}억</td>
                                        <td style={{ padding: '0.3rem 0.4rem', fontSize: '0.72rem' }}>
                                          <div style={{ width: 60, height: 4, background: 'rgba(51,65,85,0.5)', borderRadius: 2, display: 'inline-block', verticalAlign: 'middle' }}>
                                            <div style={{ width: `${Math.min(100, p.pos_52w_pct||0)}%`, height: '100%', background: (p.pos_52w_pct||50) < 30 ? '#15803d' : (p.pos_52w_pct||50) > 80 ? '#dc2626' : '#b45309', borderRadius: 2 }}/>
                                          </div>
                                          <span style={{ marginLeft: 4, color: (p.pos_52w_pct||50) < 30 ? '#15803d' : (p.pos_52w_pct||50) > 80 ? '#dc2626' : '#b45309' }}>{p.pos_52w_pct||'-'}%</span>
                                        </td>
                                        <td style={{ padding: '0.3rem 0.4rem', color: (p.ret_3m||0) > 20 ? '#15803d' : (p.ret_3m||0) < -10 ? '#dc2626' : '#b45309', fontWeight: 700 }}>
                                          {p.ret_3m !== null && p.ret_3m !== undefined ? `${p.ret_3m > 0 ? '+' : ''}${p.ret_3m}%` : '-'}
                                        </td>
                                        <td style={{ padding: '0.3rem 0.4rem', color: '#334155', fontSize: '0.72rem' }}>{p.pbr || '-'}</td>
                                      </tr>
                                    );
                                  })}
                                </tbody>
                              </table>
                            </div>
                          </td>
                        </tr>
                      )}
                      </>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {/* 스코어 설명 */}
            <div style={{ marginTop: '1rem', padding: '0.75rem', background: 'rgba(255,255,255,0.5)', borderRadius: '0.5rem', fontSize: '0.75rem', color: '#1e293b', lineHeight: 1.8 }}>
              <span style={{ color: '#334155', fontWeight: 600 }}>신호 기준: </span>
              {isUS
                ? '4주RS 초과 +5%↑=35점 / 12주RS 초과 +8%↑=30점 / 거래량비 1.5x↑=20점 / 섹터폭(52주고점권 비율) 50%↑=15점'
                : 'RS4주 초과 +15%↑=30점 / RS12주 초과 +20%↑=25점 / 거래량비 1.8x↑=20점 / 섹터폭 40%↑=15점 / 기관수급 1000억↑=10점 / 수출YoY +30%↑=10점'}
              <br/>
              <span style={{ color: '#15803d' }}>■ BUY 65점+</span> · <span style={{ color: '#b45309' }}>■ WATCH 40~64점</span> · <span style={{ color: '#1e293b' }}>■ NEUTRAL ~39점</span>
              {!isUS && ' · 집중투자 권장: BUY 섹터 상위 3~5종목에 포트 60% 집중'}
              {isUS && ' · 수급·실적 데이터 없이 가격·거래량만으로 판정 — 참고용으로만 활용할 것'}
            </div>
          </div>
        </div>
      )}

      {/* 탭 2: 4분면 로테이션 맵 */}
      {tab === 'rotation' && rotMap && (
        <div style={card({ minHeight: 480 })}>
          <div style={{ marginBottom: '0.75rem', fontSize: '0.82rem', color: '#334155' }}>
            <b style={{ color: '#1e293b' }}>RS 4분면 맵</b> — X축: RS 12주(장기), Y축: RS 4주(단기) / {isUS ? (market === 'sp500' ? 'SPY' : 'QQQ') : 'KOSPI'} 초과수익 기준
          </div>
          <div style={{ position: 'relative', height: 400, border: '1px solid rgba(51,65,85,0.4)', borderRadius: '0.5rem', overflow: 'hidden' }}>
            {/* 배경 4분면 */}
            <div style={{ position: 'absolute', left: '50%', top: 0, width: 1, height: '100%', background: 'rgba(51,65,85,0.5)' }}/>
            <div style={{ position: 'absolute', left: 0, top: '50%', width: '100%', height: 1, background: 'rgba(51,65,85,0.5)' }}/>
            {/* 4분면 라벨 */}
            <div style={{ position: 'absolute', left: '25%', top: '15%', textAlign: 'center', fontSize: '0.72rem', color: '#1e293b', transform: 'translate(-50%,-50%)' }}>😴 부진<br/>(Lagging)</div>
            <div style={{ position: 'absolute', left: '75%', top: '15%', textAlign: 'center', fontSize: '0.72rem', color: '#b45309', transform: 'translate(-50%,-50%)' }}>📈 개선중<br/>(Improving)</div>
            <div style={{ position: 'absolute', left: '25%', top: '85%', textAlign: 'center', fontSize: '0.72rem', color: '#dc2626', transform: 'translate(-50%,-50%)' }}>⚠ 약화<br/>(Weakening)</div>
            <div style={{ position: 'absolute', left: '75%', top: '85%', textAlign: 'center', fontSize: '0.72rem', color: '#15803d', transform: 'translate(-50%,-50%)' }}>🚀 주도<br/>(Leading)</div>
            {/* 섹터 점 */}
            {rotMap.sectors.map(s => {
              const maxR = 30; // 최대 표시 범위 %
              const x = Math.max(5, Math.min(95, 50 + (s.rs12w / maxR) * 45));
              const y = Math.max(5, Math.min(95, 50 - (s.rs4w / maxR) * 45));
              const ph = PHASE_COLOR[s.phase] || PHASE_COLOR.Lagging;
              return (
                <div key={s.sector} style={{ position: 'absolute', left: `${x}%`, top: `${y}%`, transform: 'translate(-50%,-50%)', cursor: isUS ? 'default' : 'pointer', zIndex: 10 }}
                  onClick={() => !isUS && loadHistory(s.sector)}>
                  <div style={{ background: ph.bg, border: `2px solid ${ph.border}`, borderRadius: '0.4rem', padding: '0.2rem 0.5rem', whiteSpace: 'nowrap', fontSize: '0.72rem', color: ph.text, fontWeight: 700, boxShadow: '0 2px 8px rgba(0,0,0,0.4)' }}>
                    {s.label}<br/>
                    <span style={{ fontSize: '0.65rem', fontWeight: 400 }}>4w:{s.rs4w > 0 ? '+' : ''}{s.rs4w}% 12w:{s.rs12w > 0 ? '+' : ''}{s.rs12w}%</span>
                  </div>
                </div>
              );
            })}
          </div>
          <div style={{ marginTop: '0.75rem', fontSize: '0.75rem', color: '#1e293b' }}>
            ※ 투자 순서: <span style={{ color: '#15803d' }}>Improving(개선중)</span> → <span style={{ color: '#15803d' }}>Leading(주도)</span> 진입 타이밍. Weakening 구간에서 비중 축소.{!isUS && ' 클릭하면 RS 히스토리 조회.'}
          </div>
        </div>
      )}

      {/* 탭 3: RS 히스토리 */}
      {tab === 'history' && history && (
        <div style={card()}>
          <div style={{ fontWeight: 700, color: '#1e293b', marginBottom: '0.75rem' }}>
            {history.label} — 월별 RS 추이 (36개월)
          </div>
          <div style={{ overflowX: 'auto' }}>
            <div style={{ display: 'flex', gap: '3px', alignItems: 'flex-end', minWidth: 700, height: 180, padding: '0.5rem 0' }}>
              {history.history.map((m, i) => {
                const excess = m.excess;
                const maxH = 80;
                const h = Math.min(Math.abs(excess) * 2.5, maxH);
                const isPos = excess >= 0;
                return (
                  <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', minWidth: 14 }}>
                    {isPos && <div style={{ width: '100%', height: h, background: excess > 10 ? '#15803d' : '#15803d', borderRadius: '2px 2px 0 0', opacity: 0.85 }}/>}
                    <div style={{ width: '100%', height: 1, background: 'rgba(100,116,139,0.4)' }}/>
                    {!isPos && <div style={{ width: '100%', height: h, background: '#dc2626', borderRadius: '0 0 2px 2px', opacity: 0.85 }}/>}
                    {i % 6 === 0 && (
                      <div style={{ fontSize: '0.55rem', color: '#1e293b', marginTop: 3, whiteSpace: 'nowrap', writingMode: 'vertical-rl', height: 30 }}>
                        {m.month}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
          {/* 표 */}
          <div style={{ maxHeight: 300, overflowY: 'auto', marginTop: '1rem' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.78rem' }}>
              <thead style={{ position: 'sticky', top: 0, background: '#f8fafc', zIndex: 1 }}>
                <tr>
                  {['월', '섹터', 'KOSPI', '초과수익'].map(h => (
                    <th key={h} style={{ padding: '0.4rem 0.6rem', color: '#1e293b', fontWeight: 600, textAlign: 'left' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {[...history.history].reverse().map((m, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid rgba(248,250,252,0.5)', background: Math.abs(m.excess) > 10 ? 'rgba(22,163,74,0.05)' : 'transparent' }}>
                    <td style={{ padding: '0.35rem 0.6rem', color: '#334155' }}>{m.month}</td>
                    <td style={{ padding: '0.35rem 0.6rem', color: m.sect_ret >= 0 ? '#15803d' : '#dc2626', fontWeight: 600 }}>{m.sect_ret > 0 ? '+' : ''}{m.sect_ret}%</td>
                    <td style={{ padding: '0.35rem 0.6rem', color: '#1e293b' }}>{m.kospi_ret > 0 ? '+' : ''}{m.kospi_ret}%</td>
                    <td style={{ padding: '0.35rem 0.6rem', color: m.excess >= 0 ? '#15803d' : '#dc2626', fontWeight: 700 }}>
                      {m.excess > 0 ? '+' : ''}{m.excess}%
                      {Math.abs(m.excess) > 10 && <span style={{ marginLeft: '0.3rem', fontSize: '0.65rem' }}>{m.excess > 0 ? '⭐' : '❌'}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button onClick={() => setTab('scores')} style={{ marginTop: '0.75rem', background: 'none', border: '1px solid rgba(51,65,85,0.5)', borderRadius: '0.4rem', padding: '0.3rem 0.7rem', color: '#1e293b', cursor: 'pointer', fontSize: '0.78rem' }}>
            ← 스코어 목록으로
          </button>
        </div>
      )}
    </div>
  );
}
