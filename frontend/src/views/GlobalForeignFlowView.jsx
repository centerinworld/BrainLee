import { useState, useEffect, useCallback } from 'react';
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ReferenceLine } from 'recharts';
import { API } from '../utils';

const CONF_COLOR = {
  HIGH:    { bg: 'rgba(34,197,94,0.15)',  border: '#22c55e', text: '#4ade80', label: '실측' },
  PENDING: { bg: 'rgba(251,191,36,0.15)', border: '#fbbf24', text: '#fbbf24', label: '설정/구현 대기' },
  BLOCKED: { bg: 'rgba(100,116,139,0.12)',border: '#64748b', text: '#94a3b8', label: '접속 차단' },
};

const COUNTRY_LINE_COLOR = { KR: '#4ade80', TW: '#94a3b8', JP: '#f87171', CN: '#fbbf24', IN: '#38bdf8' };
const TIC_LINE_COLOR = { ALL: '#e2e8f0', ASIA_TOTAL: '#fbbf24', EUROPE_TOTAL: '#38bdf8' };
const REGION_COLOR = { ASIA: '#fbbf24', EUROPE: '#38bdf8', AMERICAS: '#4ade80', MEA: '#f87171', ASIA_PAC: '#a78bfa' };

const card = (style = {}) => ({
  background: 'rgba(30,41,59,0.8)',
  border: '1px solid rgba(51,65,85,0.6)',
  borderRadius: '0.75rem',
  padding: '1rem',
  ...style,
});

function fmtUsdM(v) {
  if (v == null) return '-';
  const sign = v >= 0 ? '+' : '';
  if (Math.abs(v) >= 1000) return `${sign}${(v / 1000).toFixed(2)}십억$`;
  return `${sign}${v.toLocaleString('en-US', { maximumFractionDigits: 0 })}백만$`;
}

export default function GlobalForeignFlowView() {
  const [summary, setSummary] = useState(null);
  const [history, setHistory] = useState(null);
  const [usHistory, setUsHistory] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, h, uh] = await Promise.all([
        fetch(API('/api/global-foreign-flow/summary')).then(x => x.json()),
        fetch(API('/api/global-foreign-flow/history?days=180')).then(x => x.json()),
        fetch(API('/api/global-foreign-flow/us-inbound-history?months=24')).then(x => x.json()),
      ]);
      setSummary(s);
      setHistory(h);
      setUsHistory(uh);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading && !summary) {
    return <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}>불러오는 중...</div>;
  }
  if (!summary) return null;

  const countries = summary.countries || [];
  const maxAbs = Math.max(1, ...countries.map(c => Math.abs(c.cum_30d_usd_million || 0)));

  const dateSet = new Set();
  Object.values(history?.series || {}).forEach(arr => arr.forEach(p => dateSet.add(p.date)));
  const dates = [...dateSet].sort();
  const chartData = dates.map(date => {
    const row = { date };
    for (const code of Object.keys(history?.series || {})) {
      const pt = (history.series[code] || []).find(p => p.date === date);
      if (pt) row[code] = pt.value;
    }
    return row;
  });

  // TIC(대미 자금흐름) 월별 아시아 vs 유럽 라인차트
  const usDateSet = new Set();
  ['ALL', 'ASIA_TOTAL', 'EUROPE_TOTAL'].forEach(k => (usHistory?.series?.[k] || []).forEach(p => usDateSet.add(p.date)));
  const usDates = [...usDateSet].sort();
  const usChartData = usDates.map(date => {
    const row = { date };
    ['ALL', 'ASIA_TOTAL', 'EUROPE_TOTAL'].forEach(k => {
      const pt = (usHistory?.series?.[k] || []).find(p => p.date === date);
      if (pt) row[k] = pt.value;
    });
    return row;
  });

  const ticCountries = (summary.us_inbound?.by_country || []).filter(c => c.has_data);
  const ticMaxAbs = Math.max(1, ...ticCountries.map(c => Math.abs(c.sum_3m_usd_million || 0)));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <div style={card()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}>
          <div>
            <div style={{ fontSize: '1rem', fontWeight: 700, color: '#e2e8f0' }}>🌍 글로벌 외국인 자금 흐름</div>
            <div style={{ fontSize: '0.72rem', color: 'var(--text-secondary)', marginTop: '0.2rem' }}>
              기준일 {summary.as_of} · 자국시장 {summary.countries_with_data}/{summary.countries_total}개국 · 미국행 TIC {ticCountries.length}/20개국
            </div>
          </div>
          <div style={{
            padding: '0.4rem 0.8rem', borderRadius: '8px', fontSize: '0.8rem', fontWeight: 700,
            background: summary.asia_outflow ? 'rgba(239,68,68,0.15)' : 'rgba(34,197,94,0.15)',
            border: `1px solid ${summary.asia_outflow ? '#ef4444' : '#22c55e'}`,
            color: summary.asia_outflow ? '#f87171' : '#4ade80',
          }}>
            {summary.asia_total_cum_30d_usd_million == null
              ? '데이터 부족'
              : summary.asia_outflow
                ? `아시아 표본시장 30일 순유출 ${fmtUsdM(summary.asia_total_cum_30d_usd_million)}`
                : `아시아 표본시장 30일 순유입 ${fmtUsdM(summary.asia_total_cum_30d_usd_million)}`}
          </div>
        </div>
        {(summary.observations || []).map((obs, i) => (
          <div key={i} style={{
            marginTop: '0.6rem', padding: '0.6rem 0.8rem', borderRadius: '8px',
            background: 'rgba(56,189,248,0.08)', border: '1px solid rgba(56,189,248,0.25)',
            fontSize: '0.75rem', color: '#e2e8f0', lineHeight: 1.5,
          }}>
            🔎 {obs}
          </div>
        ))}
      </div>

      {/* 동일 기간으로 정렬한 목적지별 외부 주식자금 압력 */}
      <div style={card()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', flexWrap: 'wrap', gap: '0.4rem' }}>
          <div style={{ fontSize: '0.88rem', fontWeight: 700, color: '#2dd4bf' }}>지역별 외부 주식자금 유입·유출</div>
          <div style={{ fontSize: '0.65rem', color: 'rgba(255,255,255,0.45)' }}>공통기간 {summary.destination_snapshot?.common_period || '-'}</div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(210px,1fr))', gap: '0.65rem', marginTop: '0.75rem' }}>
          {(summary.destination_snapshot?.destinations || []).map(item => {
            const value = item.value_usd_million;
            return (
              <div key={item.code} style={{ padding: '0.75rem', borderRadius: '9px', background: 'rgba(15,23,42,0.55)', border: '1px solid rgba(148,163,184,0.2)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.4rem' }}>
                  <span style={{ color: '#e2e8f0', fontWeight: 700 }}>{item.flag} {item.label}</span>
                  <span style={{ color: value == null ? '#94a3b8' : value >= 0 ? '#4ade80' : '#f87171', fontWeight: 800 }}>{fmtUsdM(value)}</span>
                </div>
                <div style={{ marginTop: '0.3rem', fontSize: '0.64rem', color: item.confidence === 'PARTIAL' ? '#fbbf24' : '#4ade80' }}>
                  {item.confidence === 'PARTIAL' ? '부분 집계' : '공식 통계'} · {item.coverage}
                </div>
                <div style={{ marginTop: '0.25rem', fontSize: '0.63rem', color: 'rgba(255,255,255,0.4)', lineHeight: 1.4 }}>{item.definition}</div>
              </div>
            );
          })}
        </div>
        <div style={{ marginTop: '0.65rem', padding: '0.55rem 0.7rem', borderRadius: '7px', background: 'rgba(251,191,36,0.08)', color: '#fbbf24', fontSize: '0.67rem' }}>
          {summary.destination_snapshot?.warning}
        </div>
      </div>

      {/* 맥락: 달러/VIX/미국채10년 */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(160px,1fr))', gap: '0.6rem' }}>
        <div style={card({ padding: '0.7rem 0.9rem' })}>
          <div style={{ fontSize: '0.68rem', color: 'var(--text-secondary)' }}>달러인덱스(DXY)</div>
          <div style={{ fontSize: '1.1rem', fontWeight: 700, color: '#e2e8f0' }}>{summary.context?.dxy_value ?? '-'}</div>
          <div style={{ fontSize: '0.68rem', color: (summary.context?.dxy_chg_30d_pct || 0) >= 0 ? '#f87171' : '#4ade80' }}>
            30일 {summary.context?.dxy_chg_30d_pct != null ? `${summary.context.dxy_chg_30d_pct > 0 ? '+' : ''}${summary.context.dxy_chg_30d_pct}%` : '-'}
          </div>
        </div>
        <div style={card({ padding: '0.7rem 0.9rem' })}>
          <div style={{ fontSize: '0.68rem', color: 'var(--text-secondary)' }}>VIX</div>
          <div style={{ fontSize: '1.1rem', fontWeight: 700, color: '#e2e8f0' }}>{summary.context?.vix_value ?? '-'}</div>
        </div>
        <div style={card({ padding: '0.7rem 0.9rem' })}>
          <div style={{ fontSize: '0.68rem', color: 'var(--text-secondary)' }}>미국채 10년</div>
          <div style={{ fontSize: '1.1rem', fontWeight: 700, color: '#e2e8f0' }}>{summary.context?.ust10y_value != null ? `${summary.context.ust10y_value}%` : '-'}</div>
        </div>
        <div style={card({ padding: '0.7rem 0.9rem', fontSize: '0.68rem', color: 'var(--text-secondary)', display: 'flex', alignItems: 'center' })}>
          달러 강세·VIX 상승·금리 상승이 겹치면 위험자산 회피 + 미국 단기자산 선호 국면일 가능성
        </div>
      </div>

      {/* ── 섹션 1: 자국시장 외국인 순매수 ── */}
      <div style={card()}>
        <div style={{ fontSize: '0.82rem', fontWeight: 700, color: '#2dd4bf', marginBottom: '0.7rem' }}>① 국가별 자국시장 외국인·국경간 순매수 — 30일 누적(USD)</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
          {countries.map(c => {
            const conf = CONF_COLOR[c.confidence] || CONF_COLOR.PENDING;
            const val = c.cum_30d_usd_million;
            const widthPct = val != null ? Math.min(100, (Math.abs(val) / maxAbs) * 100) : 0;
            return (
              <div key={c.code}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', marginBottom: '0.2rem' }}>
                  <span style={{ color: '#e2e8f0' }}>{c.flag} {c.label}
                    <span style={{
                      marginLeft: '0.4rem', fontSize: '0.62rem', padding: '0.05rem 0.4rem', borderRadius: '999px',
                      background: conf.bg, border: `1px solid ${conf.border}`, color: conf.text,
                    }}>{conf.label}</span>
                  </span>
                  <span style={{ color: val == null ? 'rgba(255,255,255,0.35)' : val < 0 ? '#f87171' : '#4ade80', fontWeight: 700 }}>
                    {c.has_data ? fmtUsdM(val) : '데이터 없음'}
                  </span>
                </div>
                <div style={{ height: '8px', borderRadius: '4px', background: 'rgba(255,255,255,0.06)', overflow: 'hidden', position: 'relative' }}>
                  {val != null && (
                    <div style={{
                      position: 'absolute', top: 0, bottom: 0,
                      left: val < 0 ? `${50 - widthPct / 2}%` : '50%',
                      width: `${widthPct / 2}%`,
                      background: val < 0 ? '#ef4444' : '#22c55e',
                    }} />
                  )}
                  <div style={{ position: 'absolute', left: '50%', top: 0, bottom: 0, width: '1px', background: 'rgba(255,255,255,0.2)' }} />
                </div>
                <div style={{ fontSize: '0.65rem', color: 'rgba(255,255,255,0.4)', marginTop: '0.15rem' }}>
                  {c.note}{c.latest_date ? ` · 최신 ${c.latest_date}` : ''}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* 시계열 라인차트 (자국시장) */}
      <div style={card()}>
        <div style={{ fontSize: '0.82rem', fontWeight: 700, color: '#2dd4bf', marginBottom: '0.7rem' }}>국가별 자국시장 외국인·국경간 순매수 추이(USD 백만)</div>
        {chartData.length > 0 ? (
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
              <XAxis dataKey="date" tick={{ fontSize: 9, fill: '#94a3b8' }} tickFormatter={d => d?.slice(5)} interval="preserveStartEnd" />
              <YAxis tick={{ fontSize: 9, fill: '#94a3b8' }} />
              <ReferenceLine y={0} stroke="rgba(255,255,255,0.2)" />
              <Tooltip contentStyle={{ background: 'rgba(15,15,25,0.95)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '6px', fontSize: '0.72rem' }} />
              <Legend formatter={(v) => (COUNTRIES_LABEL[v] || v)} />
              {Object.keys(history?.series || {}).map(code => (
                (history.series[code] || []).length > 0 && (
                  <Line key={code} type="monotone" dataKey={code} stroke={COUNTRY_LINE_COLOR[code] || '#888'} strokeWidth={1.6} dot={false} connectNulls />
                )
              ))}
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)', fontSize: '0.78rem' }}>
            아직 누적된 시계열이 없습니다 — 수집이 진행되면 자동으로 채워집니다.
          </div>
        )}
      </div>

      {/* ── 섹션 2: TIC 대미 양자간 자금흐름 ── */}
      <div style={card()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', flexWrap: 'wrap', gap: '0.4rem' }}>
          <div style={{ fontSize: '0.82rem', fontWeight: 700, color: '#2dd4bf' }}>② 국가별 "미국 주식으로" 순매수 — TIC(미 재무부), 월별</div>
          <div style={{ fontSize: '0.65rem', color: 'rgba(255,255,255,0.4)' }}>{summary.us_inbound?.market_scope} · {summary.us_inbound?.note} · 최신 {summary.us_inbound?.as_of_month}</div>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(180px,1fr))', gap: '0.6rem', margin: '0.7rem 0' }}>
          <div style={{ padding: '0.6rem 0.8rem', borderRadius: '8px', background: 'rgba(226,232,240,0.08)', border: '1px solid rgba(226,232,240,0.2)' }}>
            <div style={{ fontSize: '0.65rem', color: 'var(--text-secondary)' }}>전세계→미국 (당월)</div>
            <div style={{ fontSize: '1.05rem', fontWeight: 700, color: '#e2e8f0' }}>{fmtUsdM(summary.us_inbound?.all_countries_usd_million)}</div>
          </div>
          <div style={{ padding: '0.6rem 0.8rem', borderRadius: '8px', background: 'rgba(251,191,36,0.1)', border: '1px solid rgba(251,191,36,0.3)' }}>
            <div style={{ fontSize: '0.65rem', color: 'var(--text-secondary)' }}>🌏 아시아→미국 (최근 3개월 합)</div>
            <div style={{ fontSize: '1.05rem', fontWeight: 700, color: '#fbbf24' }}>{fmtUsdM(summary.us_inbound?.asia_total?.sum_3m_usd_million)}</div>
          </div>
          <div style={{ padding: '0.6rem 0.8rem', borderRadius: '8px', background: 'rgba(56,189,248,0.1)', border: '1px solid rgba(56,189,248,0.3)' }}>
            <div style={{ fontSize: '0.65rem', color: 'var(--text-secondary)' }}>🌍 유럽→미국 (최근 3개월 합)</div>
            <div style={{ fontSize: '1.05rem', fontWeight: 700, color: '#38bdf8' }}>{fmtUsdM(summary.us_inbound?.europe_total?.sum_3m_usd_million)}</div>
          </div>
        </div>

        {/* 국가별 랭킹 바 */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.45rem', marginTop: '0.5rem' }}>
          {ticCountries.map(c => {
            const val = c.sum_3m_usd_million;
            const widthPct = val != null ? Math.min(100, (Math.abs(val) / ticMaxAbs) * 100) : 0;
            return (
              <div key={c.code}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.72rem', marginBottom: '0.15rem' }}>
                  <span style={{ color: '#e2e8f0' }}>{c.flag} {c.label}
                    <span style={{ marginLeft: '0.35rem', fontSize: '0.6rem', color: REGION_COLOR[c.region] || '#888' }}>({c.region})</span>
                  </span>
                  <span style={{ color: val < 0 ? '#f87171' : '#4ade80', fontWeight: 700 }}>{fmtUsdM(val)}</span>
                </div>
                <div style={{ height: '6px', borderRadius: '3px', background: 'rgba(255,255,255,0.06)', overflow: 'hidden', position: 'relative' }}>
                  <div style={{
                    position: 'absolute', top: 0, bottom: 0,
                    left: val < 0 ? `${50 - widthPct / 2}%` : '50%',
                    width: `${widthPct / 2}%`,
                    background: val < 0 ? '#ef4444' : '#22c55e',
                  }} />
                  <div style={{ position: 'absolute', left: '50%', top: 0, bottom: 0, width: '1px', background: 'rgba(255,255,255,0.2)' }} />
                </div>
              </div>
            );
          })}
        </div>
        <div style={{ fontSize: '0.65rem', color: 'rgba(255,255,255,0.35)', marginTop: '0.5rem' }}>
          최근 3개월 합계 기준 정렬 · 플러스 = 그 나라 투자자가 미국 주식을 순매수(미국행 자금) · {summary.us_inbound?.market_scope_note}
        </div>
      </div>

      {/* TIC 월별 아시아 vs 유럽 추이 */}
      <div style={card()}>
        <div style={{ fontSize: '0.82rem', fontWeight: 700, color: '#2dd4bf', marginBottom: '0.7rem' }}>전세계/아시아/유럽 → 미국 주식 순매수 월별 추이(USD 백만)</div>
        {usChartData.length > 0 ? (
          <ResponsiveContainer width="100%" height={240}>
            <LineChart data={usChartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
              <XAxis dataKey="date" tick={{ fontSize: 9, fill: '#94a3b8' }} tickFormatter={d => d?.slice(0, 7)} interval="preserveStartEnd" />
              <YAxis tick={{ fontSize: 9, fill: '#94a3b8' }} />
              <ReferenceLine y={0} stroke="rgba(255,255,255,0.2)" />
              <Tooltip contentStyle={{ background: 'rgba(15,15,25,0.95)', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '6px', fontSize: '0.72rem' }} />
              <Legend formatter={(v) => ({ ALL: '전세계', ASIA_TOTAL: '아시아', EUROPE_TOTAL: '유럽' }[v] || v)} />
              {['ALL', 'ASIA_TOTAL', 'EUROPE_TOTAL'].map(k => (
                <Line key={k} type="monotone" dataKey={k} stroke={TIC_LINE_COLOR[k]} strokeWidth={k === 'ALL' ? 1.2 : 1.8} dot={false} connectNulls />
              ))}
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)', fontSize: '0.78rem' }}>
            데이터 수집 중입니다.
          </div>
        )}
      </div>
    </div>
  );
}

const COUNTRIES_LABEL = { KR: '🇰🇷 한국', TW: '🇹🇼 대만', JP: '🇯🇵 일본', CN: '🇨🇳 중국', IN: '🇮🇳 인도' };
