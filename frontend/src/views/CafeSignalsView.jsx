import React from 'react';
import { RefreshCw, ExternalLink, Newspaper, Layers, BarChart3, FileText } from 'lucide-react';
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip } from 'recharts';

const API = (path) => path;

const fmt = (v) => Number(v || 0).toLocaleString('ko-KR', { maximumFractionDigits: 1 });

const fetchJson = async (path, fallback) => {
  const res = await fetch(API(path));
  if (!res.ok) return fallback;
  return await res.json();
};

const Badge = ({ children, tone = '#60a5fa' }) => (
  <span style={{
    display: 'inline-flex',
    alignItems: 'center',
    padding: '0.18rem 0.42rem',
    borderRadius: '6px',
    border: `1px solid ${tone}66`,
    background: `${tone}1f`,
    color: tone,
    fontSize: '0.72rem',
    fontWeight: 700,
  }}>
    {children}
  </span>
);

const RankingTable = ({ title, icon, rows, kind }) => (
  <div className="glass-panel" style={{ padding: '1rem', minHeight: '260px' }}>
    <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.8rem' }}>
      {icon}
      <h3 style={{ fontSize: '1rem', fontWeight: 800 }}>{title}</h3>
    </div>
    {!rows?.length ? (
      <div style={{ color: 'var(--text-secondary)', fontSize: '0.85rem', padding: '1.5rem 0' }}>
        아직 집계된 신호가 없습니다.
      </div>
    ) : (
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
          <thead>
            <tr style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--glass-border)' }}>
              <th style={{ textAlign: 'left', padding: '0.45rem' }}>순위</th>
              <th style={{ textAlign: 'left', padding: '0.45rem' }}>{kind === 'stock' ? '종목' : '항목'}</th>
              <th style={{ textAlign: 'right', padding: '0.45rem' }}>언급</th>
              <th style={{ textAlign: 'right', padding: '0.45rem' }}>긍정</th>
              <th style={{ textAlign: 'right', padding: '0.45rem' }}>부정</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 15).map((r, i) => (
              <tr key={`${kind}-${r.mention_key || r.key || i}`} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                <td style={{ padding: '0.5rem', color: 'var(--text-secondary)' }}>{i + 1}</td>
                <td style={{ padding: '0.5rem', fontWeight: 700 }}>
                  {r.mention_name || r.name}
                  {r.stock_code && <span style={{ marginLeft: '0.35rem', color: 'var(--text-secondary)', fontFamily: 'monospace', fontSize: '0.72rem' }}>{r.stock_code}</span>}
                </td>
                <td style={{ padding: '0.5rem', textAlign: 'right' }}>{fmt(r.mention_count || r.score)}</td>
                <td style={{ padding: '0.5rem', textAlign: 'right', color: '#34d399' }}>{fmt(r.positive_count)}</td>
                <td style={{ padding: '0.5rem', textAlign: 'right', color: '#f87171' }}>{fmt(r.negative_count)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )}
  </div>
);

const SummaryList = ({ title, items }) => (
  <div className="glass-panel" style={{ padding: '1rem' }}>
    <h3 style={{ fontSize: '1rem', fontWeight: 800, marginBottom: '0.8rem' }}>{title}</h3>
    {!items?.length ? (
      <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>요약 데이터가 없습니다.</p>
    ) : (
      <div style={{ display: 'grid', gap: '0.65rem' }}>
        {items.slice(0, 8).map((item, idx) => (
          <div key={`${item.key || item.name}-${idx}`} style={{
            display: 'grid',
            gridTemplateColumns: '34px 1fr auto',
            alignItems: 'center',
            gap: '0.65rem',
            borderBottom: '1px solid rgba(255,255,255,0.05)',
            paddingBottom: '0.55rem',
          }}>
            <Badge tone={idx < 3 ? '#34d399' : '#60a5fa'}>{idx + 1}</Badge>
            <div>
              <div style={{ fontWeight: 800 }}>{item.name}</div>
              {item.examples?.[0] && (
                <a href={item.examples[0].url} target="_blank" rel="noreferrer"
                  style={{ color: 'var(--text-secondary)', fontSize: '0.76rem', textDecoration: 'none' }}>
                  {item.examples[0].title}
                </a>
              )}
            </div>
            <div style={{ fontWeight: 800, color: '#fbbf24' }}>{fmt(item.score)}</div>
          </div>
        ))}
      </div>
    )}
  </div>
);

const pctText = (v) => (v == null || Number.isNaN(Number(v)) ? '-' : `${Number(v).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}%`);

const trafficMeta = {
  green: { label: '좋음', color: '#34d399', bg: 'rgba(52,211,153,0.12)' },
  red: { label: '나쁨', color: '#f87171', bg: 'rgba(248,113,113,0.12)' },
  yellow: { label: '주의', color: '#fbbf24', bg: 'rgba(251,191,36,0.12)' },
  gray: { label: '중립', color: '#94a3b8', bg: 'rgba(148,163,184,0.12)' },
};

const TrafficBadge = ({ light, label }) => {
  const meta = trafficMeta[light] || trafficMeta.gray;
  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: '0.35rem',
      padding: '0.2rem 0.5rem',
      borderRadius: '6px',
      border: `1px solid ${meta.color}77`,
      background: meta.bg,
      color: meta.color,
      fontSize: '0.74rem',
      fontWeight: 900,
      whiteSpace: 'nowrap',
    }}>
      <span style={{ width: 8, height: 8, borderRadius: '50%', background: meta.color, boxShadow: `0 0 10px ${meta.color}99` }} />
      {label || meta.label}
    </span>
  );
};

const valueText = (v, unit) => {
  if (v == null || Number.isNaN(Number(v))) return '-';
  const suffix = unit ? ` ${unit}` : '';
  return `${Number(v).toLocaleString('ko-KR', { maximumFractionDigits: 2 })}${suffix}`;
};

const mappingStatusLabel = (status) => ({
  ready_existing: '연결 완료',
  partial_existing: '부분 지표',
  source_discontinued: '원천 종료',
  new_collector_needed: '수집대기',
}[status] || status || '매핑');

const relationshipLabel = (stock) => {
  if (stock.revenue_exposure_pct != null) return `매출${Number(stock.revenue_exposure_pct).toFixed(1)}%`;
  if (stock.profit_exposure_pct != null) return `이익${Number(stock.profit_exposure_pct).toFixed(1)}%`;
  if (stock.mapping_status === 'confirmed_macro_signal') return '매크로 백테스트 통과';
  if (stock.mapping_status === 'confirmed_relationship') return '직접관계·비중미공시';
  if (stock.cost_exposure_pct != null) return `원가민감${Number(stock.cost_exposure_pct).toFixed(1)}%·검토`;
  return '문맥후보·비중미공시';
};

const PriceRiskBadge = ({ item }) => {
  if (!item?.price_risk || item.price_risk === 'OK') return null;
  const avoid = item.price_risk === 'AVOID';
  return (
    <span style={{
      display: 'inline-flex',
      alignItems: 'center',
      gap: '0.25rem',
      padding: '0.18rem 0.42rem',
      borderRadius: '999px',
      border: `1px solid ${avoid ? 'rgba(248,113,113,0.42)' : 'rgba(251,191,36,0.42)'}`,
      background: avoid ? 'rgba(248,113,113,0.12)' : 'rgba(251,191,36,0.12)',
      color: avoid ? '#fca5a5' : '#fbbf24',
      fontSize: '0.68rem',
      fontWeight: 900,
      whiteSpace: 'nowrap',
    }}>
      {item.price_risk_label}
      {item.price_return_1m != null && <span>1M {pctText(item.price_return_1m)}</span>}
      {item.price_return_3m != null && <span>3M {pctText(item.price_return_3m)}</span>}
    </span>
  );
};

const LeadershipTable = ({ title, rows, type }) => (
  <div className="glass-panel" style={{ padding: '1rem' }}>
    <h3 style={{ fontSize: '1rem', fontWeight: 800, marginBottom: '0.8rem' }}>{title}</h3>
    {!rows?.length ? (
      <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>월별 리더십 데이터가 없습니다.</p>
    ) : (
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
          <thead>
            <tr style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--glass-border)' }}>
              <th style={{ textAlign: 'left', padding: '0.45rem' }}>순위</th>
              <th style={{ textAlign: 'left', padding: '0.45rem' }}>{type === 'hs' ? 'HS code' : '섹터'}</th>
              <th style={{ textAlign: 'right', padding: '0.45rem' }}>수출액</th>
              <th style={{ textAlign: 'right', padding: '0.45rem' }}>YoY</th>
              <th style={{ textAlign: 'right', padding: '0.45rem' }}>전기비</th>
              <th style={{ textAlign: 'left', padding: '0.45rem' }}>{type === 'hs' ? '관련 종목' : '단가 YoY'}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const companies = Array.isArray(r.related_companies)
                ? r.related_companies.slice(0, 3).map((c) => c.stock_name).join(', ')
                : '';
              return (
                <tr key={`${type}-${r.rank_no}-${r.hs_code || r.indicator_key}`} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                  <td style={{ padding: '0.48rem', color: 'var(--text-secondary)' }}>{r.rank_no}</td>
                  <td style={{ padding: '0.48rem', fontWeight: 800 }}>
                    {type === 'hs' ? (
                      <>
                        <span style={{ fontFamily: 'monospace', color: '#93c5fd' }}>{r.hs_code}</span>
                        <div style={{ color: 'var(--text-secondary)', fontSize: '0.72rem', maxWidth: '320px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.hs_name}</div>
                      </>
                    ) : r.sector_name}
                  </td>
                  <td style={{ padding: '0.48rem', textAlign: 'right' }}>
                    {type === 'hs'
                      ? `${(Number(r.export_value_usd || 0) / 1_000_000).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}백만$`
                      : `${Number(r.export_value_musd || 0).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}백만$`}
                  </td>
                  <td style={{ padding: '0.48rem', textAlign: 'right', color: Number(r.export_yoy_pct) >= 0 ? '#34d399' : '#f87171' }}>{pctText(r.export_yoy_pct)}</td>
                  <td style={{ padding: '0.48rem', textAlign: 'right', color: Number(r.export_mom_pct) >= 0 ? '#34d399' : '#f87171' }}>{pctText(r.export_mom_pct)}</td>
                  <td style={{ padding: '0.48rem', color: 'var(--text-secondary)' }}>
                    {type === 'hs' ? (companies || '매핑 검토') : pctText(r.unit_price_yoy_pct)}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    )}
  </div>
);

const IndicatorTrafficLightTable = ({ rows }) => (
  <div className="glass-panel" style={{ padding: '1rem' }}>
    <h3 style={{ fontSize: '1rem', fontWeight: 900, marginBottom: '0.35rem' }}>지표 발표 신호등</h3>
    <p style={{ color: 'var(--text-secondary)', fontSize: '0.78rem', marginBottom: '0.8rem' }}>
      최신 발표값을 MoM·YoY·z-score로 비교하고, 지표 방향성에 따라 좋음/주의/나쁨을 표시합니다.
    </p>
    {!rows?.length ? (
      <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>현재 표시할 지표 신호등 데이터가 없습니다.</p>
    ) : (
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
          <thead>
            <tr style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--glass-border)' }}>
              <th style={{ textAlign: 'left', padding: '0.45rem' }}>신호</th>
              <th style={{ textAlign: 'left', padding: '0.45rem' }}>지표</th>
              <th style={{ textAlign: 'left', padding: '0.45rem' }}>기간</th>
              <th style={{ textAlign: 'right', padding: '0.45rem' }}>값</th>
              <th style={{ textAlign: 'right', padding: '0.45rem' }}>MoM</th>
              <th style={{ textAlign: 'right', padding: '0.45rem' }}>YoY</th>
              <th style={{ textAlign: 'right', padding: '0.45rem' }}>z</th>
              <th style={{ textAlign: 'left', padding: '0.45rem' }}>관련 종목</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const stocks = Array.isArray(r.related_stocks) ? r.related_stocks.slice(0, 4) : [];
              const meta = trafficMeta[r.traffic_light] || trafficMeta.gray;
              return (
                <tr key={`${r.indicator_key}-${r.series_name}-${r.period}`} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                  <td style={{ padding: '0.5rem' }}><TrafficBadge light={r.traffic_light} label={r.signal_label} /></td>
                  <td style={{ padding: '0.5rem', minWidth: '260px' }}>
                    <div style={{ fontWeight: 850, color: meta.color }}>{r.indicator_name || r.indicator_key}</div>
                    <div style={{ color: 'var(--text-secondary)', fontSize: '0.7rem' }}>{r.series_name}</div>
                    {r.quality && r.quality !== 'official' && (
                      <div style={{ color: '#fbbf24', fontSize: '0.66rem', marginTop: '0.12rem' }}>
                        {r.quality.includes('proxy') ? '대리지표' : r.quality.includes('partial') ? '부분지표' : r.quality}
                        {r.source_name ? ` · ${r.source_name}` : ''}
                      </div>
                    )}
                    {r.is_fresh === false && (
                      <div style={{ color: '#f87171', fontSize: '0.66rem', marginTop: '0.12rem' }}>갱신 지연 · 매매점수 제외</div>
                    )}
                    <div style={{ color: 'rgba(255,255,255,0.52)', fontSize: '0.68rem', marginTop: '0.15rem' }}>{r.reason}</div>
                  </td>
                  <td style={{ padding: '0.5rem', fontFamily: 'monospace', color: '#93c5fd' }}>{r.period}</td>
                  <td style={{ padding: '0.5rem', textAlign: 'right', whiteSpace: 'nowrap' }}>{valueText(r.value, r.unit)}</td>
                  <td style={{ padding: '0.5rem', textAlign: 'right', color: Number(r.mom_pct) >= 0 ? '#34d399' : '#f87171' }}>{pctText(r.mom_pct)}</td>
                  <td style={{ padding: '0.5rem', textAlign: 'right', color: Number(r.yoy_pct) >= 0 ? '#34d399' : '#f87171' }}>{pctText(r.yoy_pct)}</td>
                  <td style={{ padding: '0.5rem', textAlign: 'right' }}>{r.z_score == null ? '-' : Number(r.z_score).toFixed(2)}</td>
                  <td style={{ padding: '0.5rem', minWidth: '240px' }}>
                    <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap' }}>
                      {stocks.length ? stocks.map((s) => (
                        <Badge key={`${r.indicator_key}-${s.stock_code}`} tone="#60a5fa">
                          {s.stock_name} {relationshipLabel(s)}
                        </Badge>
                      )) : <span style={{ color: 'var(--text-secondary)' }}>매핑 없음</span>}
                    </div>
                    <div style={{ color: 'rgba(255,255,255,0.45)', fontSize: '0.68rem', marginTop: '0.28rem' }}>{r.direction_note}</div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    )}
  </div>
);

const SectorTrafficLightTable = ({ rows }) => (
  <div className="glass-panel" style={{ padding: '1rem' }}>
    <h3 style={{ fontSize: '1rem', fontWeight: 900, marginBottom: '0.35rem' }}>섹터 신호등</h3>
    <p style={{ color: 'var(--text-secondary)', fontSize: '0.78rem', marginBottom: '0.8rem' }}>
      구성 지표를 지표 단위로 압축해 섹터 방향을 계산하고 관련 종목을 연결합니다.
    </p>
    {!rows?.length ? (
      <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>현재 표시할 섹터 신호가 없습니다.</p>
    ) : (
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
          <thead>
            <tr style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--glass-border)' }}>
              <th style={{ textAlign: 'left', padding: '0.45rem' }}>신호</th>
              <th style={{ textAlign: 'left', padding: '0.45rem' }}>섹터</th>
              <th style={{ textAlign: 'right', padding: '0.45rem' }}>점수</th>
              <th style={{ textAlign: 'left', padding: '0.45rem' }}>구성</th>
              <th style={{ textAlign: 'left', padding: '0.45rem' }}>핵심 지표</th>
              <th style={{ textAlign: 'left', padding: '0.45rem' }}>연결 종목</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const meta = trafficMeta[r.traffic_light] || trafficMeta.gray;
              const signals = Array.isArray(r.top_signals) ? r.top_signals.slice(0, 3) : [];
              const stocks = Array.isArray(r.related_stocks) ? r.related_stocks.slice(0, 5) : [];
              return (
                <tr key={r.sector_name} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                  <td style={{ padding: '0.5rem' }}><TrafficBadge light={r.traffic_light} label={r.signal_label} /></td>
                  <td style={{ padding: '0.5rem', fontWeight: 900, color: meta.color, whiteSpace: 'nowrap' }}>{r.sector_name}</td>
                  <td style={{ padding: '0.5rem', textAlign: 'right', fontFamily: 'monospace', color: meta.color }}>{Number(r.sector_score || 0).toFixed(2)}</td>
                  <td style={{ padding: '0.5rem', whiteSpace: 'nowrap' }}>
                    <span style={{ color: '#34d399' }}>+{r.positive_indicators || 0}</span>
                    <span style={{ color: 'var(--text-secondary)' }}> / </span>
                    <span style={{ color: '#f87171' }}>-{r.negative_indicators || 0}</span>
                    <div style={{ color: 'var(--text-secondary)', fontSize: '0.68rem' }}>총 {r.indicator_count || 0}개 지표</div>
                  </td>
                  <td style={{ padding: '0.5rem', minWidth: '230px' }}>
                    {signals.map((s) => (
                      <div key={`${r.sector_name}-${s.indicator_key}`} style={{ color: Number(s.score) >= 0 ? '#86efac' : '#fca5a5', fontSize: '0.72rem', marginBottom: '0.2rem' }}>
                        {s.indicator_name} {Number(s.score) >= 0 ? '+' : ''}{Number(s.score || 0).toFixed(2)}
                      </div>
                    ))}
                  </td>
                  <td style={{ padding: '0.5rem', minWidth: '300px' }}>
                    <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap' }}>
                      {stocks.length ? stocks.map((s) => (
                        <Badge key={`${r.sector_name}-${s.stock_code}`} tone={s.mapping_status === 'confirmed_exposure' ? '#34d399' : '#60a5fa'}>
                          {s.stock_name} {relationshipLabel(s)}
                        </Badge>
                      )) : <span style={{ color: 'var(--text-secondary)' }}>연결 종목 없음</span>}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    )}
  </div>
);

const StockTradeSignalTable = ({ rows, counts }) => (
  <div className="glass-panel" style={{ padding: '1rem' }}>
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.75rem', marginBottom: '0.8rem', flexWrap: 'wrap' }}>
      <h3 style={{ fontSize: '1rem', fontWeight: 900 }}>종목 매수·매도 시그널</h3>
      <div style={{ display: 'flex', gap: '0.65rem', fontSize: '0.72rem' }}>
        <span style={{ color: '#34d399' }}>매수 {counts?.buy || 0}</span>
        <span style={{ color: '#f87171' }}>매도/위험 {counts?.sell_risk || 0}</span>
        <span style={{ color: '#fbbf24' }}>관찰 {counts?.watch || 0}</span>
      </div>
    </div>
    {!rows?.length ? (
      <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>현재 확인 기준을 충족한 종목 시그널이 없습니다.</p>
    ) : (
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
          <thead>
            <tr style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--glass-border)' }}>
              <th style={{ textAlign: 'left', padding: '0.45rem' }}>판정</th>
              <th style={{ textAlign: 'left', padding: '0.45rem' }}>종목</th>
              <th style={{ textAlign: 'right', padding: '0.45rem' }}>점수</th>
              <th style={{ textAlign: 'left', padding: '0.45rem' }}>가격위험</th>
              <th style={{ textAlign: 'left', padding: '0.45rem' }}>핵심 근거</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const drivers = Array.isArray(r.drivers) ? r.drivers.slice(0, 3) : [];
              return (
                <tr key={r.stock_code} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                  <td style={{ padding: '0.5rem' }}><TrafficBadge light={r.traffic_light} label={r.action} /></td>
                  <td style={{ padding: '0.5rem', minWidth: '150px' }}>
                    <div style={{ fontWeight: 900 }}>{r.stock_name}</div>
                    <div style={{ color: 'var(--text-secondary)', fontFamily: 'monospace', fontSize: '0.68rem' }}>{r.stock_code}</div>
                  </td>
                  <td style={{ padding: '0.5rem', textAlign: 'right', fontFamily: 'monospace', fontWeight: 800 }}>
                    {Number(r.risk_adjusted_score ?? r.score ?? 0).toFixed(2)}
                    {r.risk_adjusted_score != null && Number(r.risk_adjusted_score) !== Number(r.score) && (
                      <div style={{ color: 'var(--text-secondary)', fontSize: '0.66rem', fontWeight: 600 }}>
                        원 {Number(r.score || 0).toFixed(2)}
                      </div>
                    )}
                  </td>
                  <td style={{ padding: '0.5rem', minWidth: '180px' }}>
                    <PriceRiskBadge item={r} />
                    {r.price_risk_note && <div style={{ marginTop: '0.25rem', color: 'var(--text-secondary)', fontSize: '0.68rem' }}>{r.price_risk_note}</div>}
                  </td>
                  <td style={{ padding: '0.5rem', minWidth: '360px' }}>
                    {drivers.map((d) => (
                      <div key={`${r.stock_code}-${d.indicator_key}`} style={{ display: 'flex', justifyContent: 'space-between', gap: '0.75rem', marginBottom: '0.3rem', color: d.traffic_light === 'green' ? '#86efac' : '#fca5a5' }}>
                        <span>{d.indicator_name} · {d.series_name}</span>
                        <span style={{ whiteSpace: 'nowrap' }}>
                          {d.revenue_exposure_pct != null ? `매출 ${Number(d.revenue_exposure_pct).toFixed(1)}%` : d.mapping_status === 'confirmed_relationship' ? '직접관계' : '비중확인'} · {Number(d.contribution) >= 0 ? '+' : ''}{Number(d.contribution || 0).toFixed(2)}
                        </span>
                      </div>
                    ))}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    )}
  </div>
);

const QuantMappingTable = ({ rows }) => (
  <div className="glass-panel" style={{ padding: '1rem' }}>
    <h3 style={{ fontSize: '1rem', fontWeight: 800, marginBottom: '0.8rem' }}>카페 프레임 → 퀀트 지표 매핑</h3>
    {!rows?.length ? (
      <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>아직 매핑 데이터가 없습니다.</p>
    ) : (
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
          <thead>
            <tr style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--glass-border)' }}>
              <th style={{ textAlign: 'left', padding: '0.45rem' }}>섹터</th>
              <th style={{ textAlign: 'right', padding: '0.45rem' }}>언급</th>
              <th style={{ textAlign: 'left', padding: '0.45rem' }}>연결 지표</th>
              <th style={{ textAlign: 'left', padding: '0.45rem' }}>상태</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 80).map((r) => (
              <tr key={`${r.sector_name}-${r.indicator_key}`} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                <td style={{ padding: '0.48rem', fontWeight: 800 }}>{r.sector_name}</td>
                <td style={{ padding: '0.48rem', textAlign: 'right', color: '#fbbf24' }}>{fmt(r.mention_count)}</td>
                <td style={{ padding: '0.48rem' }}>
                  <span style={{ fontFamily: 'monospace', color: '#93c5fd', marginRight: '0.45rem' }}>{r.indicator_key}</span>
                  {r.indicator_name || '-'}
                </td>
                <td style={{ padding: '0.48rem', color: r.status?.includes('ready') || r.indicator_key?.startsWith('public:') ? '#34d399' : r.status === 'source_discontinued' ? '#f87171' : '#fbbf24' }}>
                  {mappingStatusLabel(r.status)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )}
  </div>
);

const IndicatorSignalTable = ({ rows }) => (
  <div className="glass-panel" style={{ padding: '1rem' }}>
    <h3 style={{ fontSize: '1rem', fontWeight: 900, marginBottom: '0.8rem' }}>지표 급변 매수 후보</h3>
    {!rows?.length ? (
      <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>현재 감지된 지표 급변 신호가 없습니다.</p>
    ) : (
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8rem' }}>
          <thead>
            <tr style={{ color: 'var(--text-secondary)', borderBottom: '1px solid var(--glass-border)' }}>
              <th style={{ textAlign: 'left', padding: '0.45rem' }}>지표</th>
              <th style={{ textAlign: 'left', padding: '0.45rem' }}>기간</th>
              <th style={{ textAlign: 'right', padding: '0.45rem' }}>MoM</th>
              <th style={{ textAlign: 'right', padding: '0.45rem' }}>YoY</th>
              <th style={{ textAlign: 'right', padding: '0.45rem' }}>z</th>
              <th style={{ textAlign: 'left', padding: '0.45rem' }}>관련 후보</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 20).map((r) => {
              const stocks = Array.isArray(r.related_stocks) ? r.related_stocks.slice(0, 4) : [];
              return (
                <tr key={`${r.indicator_key}-${r.series_name}-${r.period}-${r.signal_type}`} style={{ borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                  <td style={{ padding: '0.5rem' }}>
                    <div style={{ fontWeight: 800, color: r.signal_type === 'spike_up' ? '#34d399' : '#f87171' }}>
                      {r.signal_type === 'spike_up' ? '▲' : '▼'} {r.indicator_name || r.indicator_key}
                    </div>
                    <div style={{ color: 'var(--text-secondary)', fontSize: '0.7rem' }}>{r.series_name}</div>
                  </td>
                  <td style={{ padding: '0.5rem', fontFamily: 'monospace', color: '#93c5fd' }}>{r.period}</td>
                  <td style={{ padding: '0.5rem', textAlign: 'right', color: Number(r.mom_pct) >= 0 ? '#34d399' : '#f87171' }}>{pctText(r.mom_pct)}</td>
                  <td style={{ padding: '0.5rem', textAlign: 'right', color: Number(r.yoy_pct) >= 0 ? '#34d399' : '#f87171' }}>{pctText(r.yoy_pct)}</td>
                  <td style={{ padding: '0.5rem', textAlign: 'right' }}>{r.z_score == null ? '-' : Number(r.z_score).toFixed(2)}</td>
                  <td style={{ padding: '0.5rem' }}>
                    <div style={{ display: 'flex', gap: '0.35rem', flexWrap: 'wrap' }}>
                      {stocks.map((s) => (
                        <Badge key={`${r.id}-${s.stock_code}`} tone="#60a5fa">
                          {s.stock_name} {s.stock_code} {relationshipLabel(s)}
                        </Badge>
                      ))}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    )}
  </div>
);

export function QuantCafeSignalsPanel({ mode = 'sector' }) {
  const [sectorRows, setSectorRows] = React.useState([]);
  const [tradeRows, setTradeRows] = React.useState([]);
  const [mappingRows, setMappingRows] = React.useState([]);
  const [performanceRows, setPerformanceRows] = React.useState([]);
  const [macroBacktests, setMacroBacktests] = React.useState({ items: [], summary: {} });
  const [selectedSector, setSelectedSector] = React.useState('');
  const [selectedSectorIndicator, setSelectedSectorIndicator] = React.useState('');
  const [sectorSeries, setSectorSeries] = React.useState([]);
  const [selectedStock, setSelectedStock] = React.useState('');
  const [stockCrossContext, setStockCrossContext] = React.useState(null);
  const [stockSearch, setStockSearch] = React.useState('');
  const [stockSectorFilter, setStockSectorFilter] = React.useState('all');
  const [relationshipFilter, setRelationshipFilter] = React.useState('confirmed');
  const [signalFilter, setSignalFilter] = React.useState('all');
  const [loading, setLoading] = React.useState(true);

  React.useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      fetchJson('/api/cafe-signals/sector-traffic-lights?limit=50', { items: [] }),
      fetchJson('/api/cafe-signals/stock-trade-signals?limit=100', { items: [] }),
      fetchJson('/api/cafe-signals/stock-indicator-mappings?limit=1000', { items: [] }),
      fetchJson('/api/cafe-signals/stock-trade-signal-performance?limit=500', { items: [] }),
      fetchJson('/api/cafe-signals/macro-signal-backtests?limit=30&passed_only=true', { items: [], summary: {} }),
    ]).then(([sectorRes, tradeRes, mappingRes, performanceRes, macroBacktestRes]) => {
      if (cancelled) return;
      setSectorRows(sectorRes?.items || []);
      setTradeRows(tradeRes?.items || []);
      setMappingRows(mappingRes?.items || []);
      setPerformanceRows(performanceRes?.items || []);
      setMacroBacktests(macroBacktestRes || { items: [], summary: {} });
    }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const filteredMappings = React.useMemo(() => mappingRows.filter((row) => {
    if (relationshipFilter === 'confirmed' && !['confirmed_exposure', 'confirmed_relationship', 'confirmed_macro_signal'].includes(row.mapping_status)) return false;
    if (relationshipFilter === 'candidate' && row.mapping_status !== 'candidate_context') return false;
    if (stockSectorFilter !== 'all' && row.sector_name !== stockSectorFilter) return false;
    const signal = tradeRows.find((item) => item.stock_code === row.stock_code);
    if (signalFilter !== 'all' && signal?.action !== signalFilter) return false;
    const q = stockSearch.trim().toLowerCase();
    return !q || row.stock_name.toLowerCase().includes(q) || row.stock_code.includes(q) || String(row.indicator_name || '').toLowerCase().includes(q);
  }), [mappingRows, relationshipFilter, stockSectorFilter, signalFilter, stockSearch, tradeRows]);
  const stockOptions = React.useMemo(() => {
    const byCode = new Map();
    filteredMappings.forEach((row) => {
      if (!byCode.has(row.stock_code)) byCode.set(row.stock_code, { code: row.stock_code, name: row.stock_name, sector: row.sector_name || '기타' });
    });
    tradeRows.forEach((row) => {
      if (byCode.has(row.stock_code)) byCode.set(row.stock_code, { code: row.stock_code, name: row.stock_name, sector: row.stock_sector || byCode.get(row.stock_code)?.sector || '기타' });
    });
    return [...byCode.values()].sort((a, b) => a.sector.localeCompare(b.sector, 'ko') || a.name.localeCompare(b.name, 'ko'));
  }, [filteredMappings, tradeRows]);
  const stocksBySector = React.useMemo(() => {
    const groups = new Map();
    filteredMappings.forEach((row) => {
      const theme = row.signal_sector || row.sector_name || '기타';
      if (!groups.has(theme)) groups.set(theme, new Map());
      groups.get(theme).set(row.stock_code, {
        code: row.stock_code,
        name: row.stock_name,
        sector: row.sector_name || '기타',
      });
    });
    return [...groups.entries()]
      .map(([sector, stocks]) => ({ sector, stocks: [...stocks.values()].sort((a, b) => a.name.localeCompare(b.name, 'ko')) }))
      .sort((a, b) => a.sector.localeCompare(b.sector, 'ko'));
  }, [filteredMappings]);

  React.useEffect(() => {
    if (!selectedSector && sectorRows.length) setSelectedSector(sectorRows[0].sector_name);
  }, [sectorRows, selectedSector]);
  const activeSector = React.useMemo(
    () => sectorRows.find((item) => item.sector_name === selectedSector),
    [sectorRows, selectedSector],
  );
  const activeSectorSignals = React.useMemo(
    () => Array.isArray(activeSector?.top_signals) ? activeSector.top_signals : [],
    [activeSector],
  );
  React.useEffect(() => {
    if (!activeSectorSignals.length) { setSelectedSectorIndicator(''); return; }
    if (!activeSectorSignals.some((item) => item.indicator_key === selectedSectorIndicator)) {
      setSelectedSectorIndicator(activeSectorSignals[0].indicator_key);
    }
  }, [activeSectorSignals, selectedSectorIndicator]);
  React.useEffect(() => {
    if (!selectedSectorIndicator) { setSectorSeries([]); return; }
    let cancelled = false;
    fetchJson(`/api/quant-major-indicators/series/${encodeURIComponent(selectedSectorIndicator)}?limit=240`, { items: [] })
      .then((payload) => {
        if (cancelled) return;
        const items = payload?.items || [];
        const preferred = activeSectorSignals.find((item) => item.indicator_key === selectedSectorIndicator)?.series_name;
        const chosen = preferred && items.some((item) => item.series_name === preferred) ? preferred : items[0]?.series_name;
        setSectorSeries(items.filter((item) => item.series_name === chosen).slice().reverse().map((item) => ({ period:item.period, value:Number(item.value), unit:item.unit, source:item.source_name })));
      });
    return () => { cancelled = true; };
  }, [selectedSectorIndicator, activeSectorSignals]);
  React.useEffect(() => {
    if (stockOptions.length && !stockOptions.some((item) => item.code === selectedStock)) {
      const firstSignal = tradeRows.find((row) => row.action === '매수 후보') || tradeRows[0];
      const allowedSignal = firstSignal && stockOptions.some((item) => item.code === firstSignal.stock_code) ? firstSignal : null;
      setSelectedStock(allowedSignal?.stock_code || stockOptions[0].code);
    } else if (!stockOptions.length && selectedStock) {
      setSelectedStock('');
    }
  }, [stockOptions, tradeRows, selectedStock]);
  React.useEffect(() => {
    if (!selectedStock) { setStockCrossContext(null); return; }
    let cancelled = false;
    fetchJson(`/api/quant-major-indicators/stock-context/${selectedStock}?limit=100`, null)
      .then((payload) => { if (!cancelled) setStockCrossContext(payload); })
      .catch(() => { if (!cancelled) setStockCrossContext(null); });
    return () => { cancelled = true; };
  }, [selectedStock]);

  if (loading) return <div className="glass-panel" style={{ padding:'1rem', color:'var(--text-secondary)' }}>신호 데이터를 불러오는 중...</div>;

  if (mode === 'sector') {
    const row = activeSector;
    const signals = Array.isArray(row?.top_signals) ? row.top_signals : [];
    const stocks = Array.isArray(row?.related_stocks) ? row.related_stocks : [];
    return (
      <div style={{ display:'grid', gap:'0.85rem' }}>
        <div className="glass-panel" style={{ padding:'0.9rem 1rem', overflowX:'auto' }}>
          <div style={{ display:'flex', justifyContent:'space-between', gap:'0.7rem', alignItems:'baseline', flexWrap:'wrap', marginBottom:'0.65rem' }}>
            <div style={{ fontWeight:850 }}>매크로 백테스트 통과 조합</div>
            <span style={{ color:'var(--text-secondary)', fontSize:'0.72rem' }}>
              {macroBacktests?.run_id || '-'} · 통과 {macroBacktests?.summary?.passed || 0} / 전체 {macroBacktests?.summary?.total || 0}
            </span>
          </div>
          <table style={{ width:'100%', borderCollapse:'collapse', fontSize:'0.76rem', minWidth:760 }}>
            <thead><tr style={{ color:'var(--text-secondary)', borderBottom:'1px solid rgba(255,255,255,0.1)' }}>
              {['지표','섹터','관측','60일 평균','60일 중앙','승률','PF','60일 MDD'].map((head) => <th key={head} style={{ padding:'0.46rem', textAlign:['지표','섹터'].includes(head) ? 'left' : 'right' }}>{head}</th>)}
            </tr></thead>
            <tbody>{(macroBacktests?.items || []).slice(0, 12).map((row) => (
              <tr key={`${row.indicator_key}-${row.sector_name}`} style={{ borderBottom:'1px solid rgba(255,255,255,0.05)' }}>
                <td style={{ padding:'0.5rem', fontWeight:800 }}>{row.indicator_name || row.indicator_key}</td>
                <td style={{ padding:'0.5rem', color:'#93c5fd' }}>{row.sector_name}</td>
                <td style={{ padding:'0.5rem', textAlign:'right' }}>{row.observation_count}</td>
                <td style={{ padding:'0.5rem', textAlign:'right', color:Number(row.avg_ret_60d) >= 0 ? '#34d399' : '#f87171' }}>{pctText(row.avg_ret_60d)}</td>
                <td style={{ padding:'0.5rem', textAlign:'right' }}>{pctText(row.median_ret_60d)}</td>
                <td style={{ padding:'0.5rem', textAlign:'right' }}>{pctText(row.hit_rate_60d)}</td>
                <td style={{ padding:'0.5rem', textAlign:'right' }}>{row.profit_factor_60d == null ? '-' : Number(row.profit_factor_60d).toFixed(2)}</td>
                <td style={{ padding:'0.5rem', textAlign:'right', color:'#fbbf24' }}>{pctText(row.avg_mdd_60d)}</td>
              </tr>
            ))}</tbody>
          </table>
          {!(macroBacktests?.items || []).length && <div style={{ color:'var(--text-secondary)', padding:'0.8rem 0' }}>백테스트 통과 조합이 없습니다.</div>}
        </div>
        <div className="glass-panel" style={{ padding:'0.9rem 1rem', overflowX:'auto' }}>
          <div style={{ fontWeight:850, marginBottom:'0.65rem' }}>전체 섹터 신호 현황</div>
          <table style={{ width:'100%', borderCollapse:'collapse', fontSize:'0.78rem', minWidth:680 }}>
            <thead><tr style={{ color:'var(--text-secondary)', borderBottom:'1px solid rgba(255,255,255,0.1)' }}>
              {['섹터','신호','점수','지표','긍정','부정','연결 종목'].map((head) => <th key={head} style={{ padding:'0.5rem', textAlign:['점수','지표','긍정','부정','연결 종목'].includes(head) ? 'right' : 'left' }}>{head}</th>)}
            </tr></thead>
            <tbody>{sectorRows.map((item) => (
              <tr key={item.sector_name} onClick={() => setSelectedSector(item.sector_name)} style={{ borderBottom:'1px solid rgba(255,255,255,0.05)', cursor:'pointer', background:selectedSector === item.sector_name ? 'rgba(45,212,191,0.08)' : 'transparent' }}>
                <td style={{ padding:'0.52rem', fontWeight:850 }}>{item.sector_name}</td>
                <td style={{ padding:'0.52rem' }}><TrafficBadge light={item.traffic_light} label={item.signal_label} /></td>
                <td style={{ padding:'0.52rem', textAlign:'right' }}>{Number(item.sector_score || 0).toFixed(2)}</td>
                <td style={{ padding:'0.52rem', textAlign:'right' }}>{item.indicator_count || 0}</td>
                <td style={{ padding:'0.52rem', textAlign:'right', color:'#34d399' }}>{item.positive_indicators || 0}</td>
                <td style={{ padding:'0.52rem', textAlign:'right', color:'#f87171' }}>{item.negative_indicators || 0}</td>
                <td style={{ padding:'0.52rem', textAlign:'right' }}>{Array.isArray(item.related_stocks) ? item.related_stocks.length : 0}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
        <div className="glass-panel" style={{ padding:'1rem' }}>
          <label style={{ display:'grid', gap:'0.4rem', maxWidth:520 }}>
            <span style={{ fontSize:'0.76rem', color:'var(--text-secondary)' }}>섹터 선택</span>
            <select value={selectedSector} onChange={(e) => setSelectedSector(e.target.value)} style={{ padding:'0.62rem 0.75rem', borderRadius:8, background:'#111827', color:'#fff', border:'1px solid rgba(255,255,255,0.14)' }}>
              {sectorRows.map((item) => <option key={item.sector_name} value={item.sector_name}>{item.sector_name} · {item.signal_label}</option>)}
            </select>
          </label>
        </div>
        {row && <div className="glass-panel" style={{ padding:'1rem' }}>
          <div style={{ display:'flex', alignItems:'center', gap:'0.65rem', flexWrap:'wrap', marginBottom:'0.9rem' }}>
            <TrafficBadge light={row.traffic_light} label={row.signal_label} />
            <h3 style={{ fontSize:'1.05rem' }}>{row.sector_name}</h3>
            <span style={{ color:'var(--text-secondary)', fontSize:'0.78rem' }}>점수 {Number(row.sector_score || 0).toFixed(2)} · 지표 {row.indicator_count || 0}개</span>
          </div>
          <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fit, minmax(260px, 1fr))', gap:'0.7rem' }}>
            {signals.map((signal) => <div key={signal.indicator_key} style={{ padding:'0.75rem', border:'1px solid rgba(255,255,255,0.09)', borderRadius:8 }}>
              <div style={{ fontWeight:850, color:Number(signal.score) >= 0 ? '#86efac' : '#fca5a5' }}>{signal.indicator_name}</div>
              <div style={{ marginTop:'0.3rem', color:'var(--text-secondary)', fontSize:'0.75rem' }}>{signal.series_name || signal.indicator_key} · {Number(signal.score) >= 0 ? '+' : ''}{Number(signal.score || 0).toFixed(2)}</div>
              {signal.direction_note && <div style={{ marginTop:'0.32rem', color:'rgba(255,255,255,0.48)', fontSize:'0.7rem', lineHeight:1.45 }}>{signal.direction_note}</div>}
            </div>)}
          </div>
          {!!signals.length && <div style={{ marginTop:'0.9rem' }}>
            <label style={{ display:'grid', gap:'0.35rem', maxWidth:520, marginBottom:'0.7rem' }}>
              <span style={{ color:'var(--text-secondary)', fontSize:'0.74rem' }}>세부 지표 추세</span>
              <select value={selectedSectorIndicator} onChange={(e) => setSelectedSectorIndicator(e.target.value)} style={{ padding:'0.58rem 0.7rem', borderRadius:8, background:'#111827', color:'#fff', border:'1px solid rgba(255,255,255,0.14)' }}>
                {signals.map((signal) => <option key={signal.indicator_key} value={signal.indicator_key}>{signal.indicator_name}</option>)}
              </select>
            </label>
            <div style={{ height:280, border:'1px solid rgba(255,255,255,0.08)', borderRadius:8, padding:'0.65rem' }}>
              {sectorSeries.length ? <ResponsiveContainer width="100%" height="100%"><LineChart data={sectorSeries} margin={{ top:8, right:18, left:4, bottom:8 }}>
                <CartesianGrid stroke="rgba(255,255,255,0.08)" strokeDasharray="3 3" />
                <XAxis dataKey="period" tick={{ fill:'#94a3b8', fontSize:11 }} />
                <YAxis tick={{ fill:'#94a3b8', fontSize:11 }} />
                <Tooltip contentStyle={{ background:'#0f172a', border:'1px solid rgba(255,255,255,0.12)', borderRadius:8 }} />
                <Line type="monotone" dataKey="value" stroke="#2dd4bf" strokeWidth={2} dot={false} />
              </LineChart></ResponsiveContainer> : <div style={{ color:'var(--text-secondary)' }}>선택한 지표의 시계열이 없습니다.</div>}
            </div>
          </div>}
          <div style={{ marginTop:'0.9rem', display:'flex', gap:'0.4rem', flexWrap:'wrap' }}>
            {stocks.map((stock) => <Badge key={stock.stock_code} tone={stock.mapping_status === 'confirmed_exposure' ? '#34d399' : '#60a5fa'}>{stock.stock_name} {relationshipLabel(stock)}</Badge>)}
          </div>
        </div>}
      </div>
    );
  }

  const mappings = filteredMappings.filter((row) => row.stock_code === selectedStock);
  const trade = tradeRows.find((row) => row.stock_code === selectedStock);
  const selected = stockOptions.find((row) => row.code === selectedStock);
  const performance = performanceRows.find((row) => row.stock_code === selectedStock);
  const stockSectors = [...new Set(mappingRows.map((row) => row.sector_name).filter(Boolean))].sort((a, b) => a.localeCompare(b, 'ko'));
  return (
    <div style={{ display:'grid', gap:'0.85rem' }}>
      <div className="glass-panel" style={{ padding:'0.9rem 1rem' }}>
        <div style={{ display:'grid', gridTemplateColumns:'minmax(180px, 1fr) repeat(3, minmax(140px, 0.45fr))', gap:'0.65rem' }}>
          <input value={stockSearch} onChange={(e) => setStockSearch(e.target.value)} placeholder="종목명·코드·연관 지표 검색" style={{ padding:'0.58rem 0.7rem', borderRadius:8, background:'#111827', color:'#fff', border:'1px solid rgba(255,255,255,0.14)' }} />
          <select value={stockSectorFilter} onChange={(e) => setStockSectorFilter(e.target.value)} style={{ padding:'0.58rem 0.7rem', borderRadius:8, background:'#111827', color:'#fff', border:'1px solid rgba(255,255,255,0.14)' }}><option value="all">전체 섹터</option>{stockSectors.map((sector) => <option key={sector} value={sector}>{sector}</option>)}</select>
              <select value={relationshipFilter} onChange={(e) => setRelationshipFilter(e.target.value)} style={{ padding:'0.58rem 0.7rem', borderRadius:8, background:'#111827', color:'#fff', border:'1px solid rgba(255,255,255,0.14)' }}><option value="confirmed">확정·백테스트 통과</option><option value="candidate">문맥 후보만</option><option value="all">전체 관계</option></select>
          <select value={signalFilter} onChange={(e) => setSignalFilter(e.target.value)} style={{ padding:'0.58rem 0.7rem', borderRadius:8, background:'#111827', color:'#fff', border:'1px solid rgba(255,255,255,0.14)' }}><option value="all">전체 신호</option><option value="매수 후보">매수 후보</option><option value="매도/위험">매도·위험</option><option value="관찰">관찰</option></select>
        </div>
        <div style={{ marginTop:'0.5rem', color:'var(--text-secondary)', fontSize:'0.72rem' }}>기본값은 매출·이익 비중, 직접 사업 관계, 또는 매크로 백테스트를 통과한 종목만 표시합니다. 문맥 후보는 매매 점수에서 제외됩니다.</div>
      </div>
      <div className="glass-panel" style={{ padding:'0.9rem 1rem', overflowX:'auto' }}>
        <div style={{ fontWeight:850, marginBottom:'0.65rem' }}>투자 테마별 연결 종목 현황</div>
        <table style={{ width:'100%', borderCollapse:'collapse', fontSize:'0.78rem', minWidth:680 }}>
          <thead><tr style={{ color:'var(--text-secondary)', borderBottom:'1px solid rgba(255,255,255,0.1)' }}>
            <th style={{ padding:'0.5rem', textAlign:'left', width:150 }}>투자 테마</th><th style={{ padding:'0.5rem', textAlign:'left' }}>종목 · 시장 섹터</th><th style={{ padding:'0.5rem', textAlign:'right', width:80 }}>수</th>
          </tr></thead>
          <tbody>{stocksBySector.map((group) => (
            <tr key={group.sector} style={{ borderBottom:'1px solid rgba(255,255,255,0.05)' }}>
              <td style={{ padding:'0.58rem', fontWeight:850, color:'#93c5fd' }}>{group.sector}</td>
              <td style={{ padding:'0.58rem' }}><div style={{ display:'flex', gap:'0.38rem', flexWrap:'wrap' }}>{group.stocks.map((stock) => {
                const signal = tradeRows.find((row) => row.stock_code === stock.code);
                return <button key={stock.code} title={`${stock.name} · ${stock.sector}`} onClick={() => setSelectedStock(stock.code)} style={{ padding:'0.24rem 0.48rem', borderRadius:6, cursor:'pointer', border:selectedStock === stock.code ? '1px solid #2dd4bf' : '1px solid rgba(255,255,255,0.12)', background:selectedStock === stock.code ? 'rgba(45,212,191,0.12)' : 'rgba(255,255,255,0.035)', color:signal?.traffic_light === 'green' ? '#86efac' : signal?.traffic_light === 'red' ? '#fca5a5' : 'var(--text-primary)', fontSize:'0.72rem' }}>{stock.name} · {stock.sector}{signal ? ` · ${signal.action}` : ''}</button>;
              })}</div></td>
              <td style={{ padding:'0.58rem', textAlign:'right' }}>{group.stocks.length}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      <div className="glass-panel" style={{ padding:'1rem' }}>
        <label style={{ display:'grid', gap:'0.4rem', maxWidth:520 }}>
          <span style={{ fontSize:'0.76rem', color:'var(--text-secondary)' }}>종목 선택</span>
          <select value={selectedStock} onChange={(e) => setSelectedStock(e.target.value)} style={{ padding:'0.62rem 0.75rem', borderRadius:8, background:'#111827', color:'#fff', border:'1px solid rgba(255,255,255,0.14)' }}>
            {stockOptions.map((item) => <option key={item.code} value={item.code}>{item.name} ({item.code})</option>)}
          </select>
        </label>
      </div>
      <div className="glass-panel" style={{ padding:'1rem' }}>
        <div style={{ display:'flex', alignItems:'center', gap:'0.65rem', flexWrap:'wrap', marginBottom:'0.85rem' }}>
          {trade ? <TrafficBadge light={trade.traffic_light} label={trade.action} /> : <TrafficBadge light="gray" label="관계 검토" />}
          <h3 style={{ fontSize:'1.05rem' }}>{selected?.name || selectedStock}</h3>
          {trade && <span style={{ color:'var(--text-secondary)', fontSize:'0.78rem' }}>
            종합점수 {Number(trade.risk_adjusted_score ?? trade.score ?? 0).toFixed(2)}
            {trade.risk_adjusted_score != null && Number(trade.risk_adjusted_score) !== Number(trade.score) ? ` (원 ${Number(trade.score || 0).toFixed(2)})` : ''}
          </span>}
          <PriceRiskBadge item={trade} />
        </div>
        {trade?.price_risk_note && <div style={{ marginBottom:'0.85rem', color:'#fbbf24', fontSize:'0.76rem' }}>{trade.price_risk_note}</div>}
        {trade?.market_confirmation && (() => {
          const market = trade.market_confirmation;
          const checks = [
            ['주가>MA20', market.checks?.price_above_ma20],
            ['MA20>MA60', market.checks?.ma20_above_ma60],
            ['거래량 확대', market.checks?.volume_expansion],
            ['5일 수급+', market.checks?.positive_flow_5d],
          ];
          return <div style={{ padding:'0.72rem', border:'1px solid rgba(96,165,250,0.18)', borderRadius:8, marginBottom:'0.85rem', background:'rgba(96,165,250,0.05)' }}>
            <div style={{ display:'flex', justifyContent:'space-between', gap:'0.6rem', flexWrap:'wrap', marginBottom:'0.55rem' }}>
              <strong style={{ color:market.score >= 3 ? '#34d399' : market.score >= 2 ? '#fbbf24' : '#94a3b8' }}>시장 확인 {market.score}/4 · {market.label}</strong>
              <span style={{ color:'var(--text-secondary)', fontSize:'0.7rem' }}>{market.as_of} · 거래량 {market.volume_ratio_20d == null ? '-' : `${Number(market.volume_ratio_20d).toFixed(2)}배`} · 외인 {Number(market.foreign_5d_억 || 0).toFixed(1)}억 · 기관 {Number(market.institution_5d_억 || 0).toFixed(1)}억</span>
            </div>
            <div style={{ display:'flex', gap:'0.38rem', flexWrap:'wrap' }}>{checks.map(([label, ok]) => <span key={label} style={{ padding:'0.2rem 0.45rem', borderRadius:6, fontSize:'0.7rem', border:`1px solid ${ok ? 'rgba(52,211,153,0.35)' : 'rgba(248,113,113,0.3)'}`, color:ok ? '#86efac' : '#fca5a5', background:ok ? 'rgba(52,211,153,0.08)' : 'rgba(248,113,113,0.06)' }}>{ok ? '충족' : '미충족'} · {label}</span>)}</div>
          </div>;
        })()}
        {performance && <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fit, minmax(120px, 1fr))', gap:'0.55rem', marginBottom:'0.85rem' }}>
          <div style={{ padding:'0.65rem', border:'1px solid rgba(255,255,255,0.08)', borderRadius:8 }}><div style={{ color:'var(--text-secondary)', fontSize:'0.68rem' }}>검증 상태</div><strong>{performance.evaluation_status} · {performance.trading_days_elapsed}거래일</strong></div>
          {[5,20,60,120].map((days) => { const value = performance[`return_${days}d_pct`]; return <div key={days} style={{ padding:'0.65rem', border:'1px solid rgba(255,255,255,0.08)', borderRadius:8 }}><div style={{ color:'var(--text-secondary)', fontSize:'0.68rem' }}>{days}일 수익률</div><strong style={{ color:value == null ? '#94a3b8' : Number(value) >= 0 ? '#34d399' : '#f87171' }}>{value == null ? '평가대기' : `${Number(value).toFixed(2)}%`}</strong></div>; })}
        </div>}
        <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fit, minmax(260px, 1fr))', gap:'0.7rem' }}>
	          {mappings.map((item) => {
	            const traffic = item.traffic || {};
	            const cross = stockCrossContext?.quant_indicators?.find((row) => row.indicator_key === item.indicator_key);
	            return <div key={`${item.stock_code}-${item.indicator_key}`} style={{ padding:'0.78rem', border:'1px solid rgba(255,255,255,0.09)', borderRadius:8 }}>
	              <div style={{ display:'flex', justifyContent:'space-between', gap:'0.5rem' }}><strong>{item.indicator_name}</strong><TrafficBadge light={traffic.traffic_light || 'gray'} label={traffic.is_fresh === false ? '갱신 지연' : traffic.signal_label} /></div>
	              <div style={{ marginTop:'0.42rem', color:'#93c5fd', fontSize:'0.74rem' }}>{traffic.series_name || item.indicator_key} · {traffic.period || '-'}</div>
	              <div style={{ marginTop:'0.3rem', color:cross?.cross_validation === 'cross_confirmed' ? '#34d399' : '#fbbf24', fontSize:'0.72rem' }}>
	                {cross?.cross_validation === 'cross_confirmed' ? `HS 일치 · ${(cross.matching_hs_mappings || []).map((h) => `${h.hs_code} ${h.display_name || h.hs_name}`).join(' · ')}` : '퀀트 근거만 · 동일 HS 품목 미확인'}
	              </div>
              <div style={{ marginTop:'0.32rem', color:item.mapping_status === 'candidate_context' || item.mapping_status === 'candidate_macro_context' ? '#94a3b8' : '#fbbf24', fontSize:'0.74rem' }}>{item.mapping_status === 'candidate_context' || item.mapping_status === 'candidate_macro_context' ? '문맥 후보 · 매매점수 제외' : relationshipLabel(item)}</div>
              <div style={{ marginTop:'0.25rem', color:'rgba(255,255,255,0.55)', fontSize:'0.7rem' }}>{item.revenue_exposure_pct != null ? `매출 민감도 ${Number(item.revenue_exposure_pct).toFixed(1)}%` : item.profit_exposure_pct != null ? `이익 민감도 ${Number(item.profit_exposure_pct).toFixed(1)}%` : item.cost_exposure_pct != null ? `원가 민감도 ${Number(item.cost_exposure_pct).toFixed(1)}%` : '민감도 비중 미공시'} · {traffic.is_fresh === false ? '최신성 기준 제외' : '최신 데이터'}</div>
              <div style={{ marginTop:'0.32rem', color:'var(--text-secondary)', fontSize:'0.72rem', lineHeight:1.45 }}>{traffic.reason || item.mapping_note}</div>
            </div>;
          })}
          {!mappings.length && <div style={{ color:'var(--text-secondary)' }}>표시할 지표 매핑이 없습니다.</div>}
        </div>
      </div>
    </div>
  );
}

export default function CafeSignalsView() {
  const [runType, setRunType] = React.useState('weekly');
  const [summary, setSummary] = React.useState(null);
  const [stocks, setStocks] = React.useState([]);
  const [sectors, setSectors] = React.useState([]);
  const [indicators, setIndicators] = React.useState([]);
  const [posts, setPosts] = React.useState([]);
  const [leadership, setLeadership] = React.useState(null);
  const [quantMappings, setQuantMappings] = React.useState([]);
  const [indicatorSignals, setIndicatorSignals] = React.useState([]);
  const [trafficLights, setTrafficLights] = React.useState([]);
  const [sectorTrafficLights, setSectorTrafficLights] = React.useState([]);
  const [stockTradeSignals, setStockTradeSignals] = React.useState([]);
  const [stockTradeCounts, setStockTradeCounts] = React.useState({});
  const [loading, setLoading] = React.useState(false);
  const [message, setMessage] = React.useState('');
  const [error, setError] = React.useState('');

  const load = React.useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [sumRes, stockRes, sectorRes, indRes, postRes, leadRes, mapRes, signalRes, trafficRes, sectorTrafficRes, tradeSignalRes] = await Promise.allSettled([
        fetchJson(`/api/cafe-signals/summary?run_type=${runType}`, null),
        fetchJson('/api/cafe-signals/mentions?mention_type=stock&days=35&limit=50', { items: [] }),
        fetchJson('/api/cafe-signals/mentions?mention_type=sector&days=35&limit=50', { items: [] }),
        fetchJson('/api/cafe-signals/mentions?mention_type=indicator&days=35&limit=50', { items: [] }),
        fetchJson('/api/cafe-signals/posts?limit=30', { items: [] }),
        fetchJson('/api/cafe-signals/leadership?sector_limit=12&hs_limit=30', null),
        fetchJson('/api/cafe-signals/quant-mappings?limit=200', { items: [] }),
        fetchJson('/api/cafe-signals/quant-indicator-signals?signal_type=spike_up&limit=50', { items: [] }),
        fetchJson('/api/cafe-signals/indicator-traffic-lights?limit=300', { items: [] }),
        fetchJson('/api/cafe-signals/sector-traffic-lights?limit=30', { items: [] }),
        fetchJson('/api/cafe-signals/stock-trade-signals?limit=50', { items: [], counts: {} }),
      ]);
      setSummary(sumRes.status === 'fulfilled' ? sumRes.value : null);
      setStocks(stockRes.status === 'fulfilled' ? stockRes.value?.items || [] : []);
      setSectors(sectorRes.status === 'fulfilled' ? sectorRes.value?.items || [] : []);
      setIndicators(indRes.status === 'fulfilled' ? indRes.value?.items || [] : []);
      setPosts(postRes.status === 'fulfilled' ? postRes.value?.items || [] : []);
      setLeadership(leadRes.status === 'fulfilled' ? leadRes.value : null);
      setQuantMappings(mapRes.status === 'fulfilled' ? mapRes.value?.items || [] : []);
      setIndicatorSignals(signalRes.status === 'fulfilled' ? signalRes.value?.items || [] : []);
      setTrafficLights(trafficRes.status === 'fulfilled' ? trafficRes.value?.items || [] : []);
      setSectorTrafficLights(sectorTrafficRes.status === 'fulfilled' ? sectorTrafficRes.value?.items || [] : []);
      setStockTradeSignals(tradeSignalRes.status === 'fulfilled' ? tradeSignalRes.value?.items || [] : []);
      setStockTradeCounts(tradeSignalRes.status === 'fulfilled' ? tradeSignalRes.value?.counts || {} : {});
      if ([sumRes, stockRes, sectorRes, indRes, postRes, leadRes, mapRes, signalRes, trafficRes, sectorTrafficRes, tradeSignalRes].some((r) => r.status === 'rejected')) {
        setError('일부 카페 시그널 데이터를 시간 내에 불러오지 못했습니다.');
      }
    } catch (e) {
      setError(e?.message || '카페 시그널 데이터를 불러오지 못했습니다.');
    } finally {
      setLoading(false);
    }
  }, [runType]);

  React.useEffect(() => { load(); }, [load]);

  const triggerCollect = async () => {
    setMessage('');
    const res = await fetch(API('/api/cafe-signals/collect'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ run_type: runType, max_pages: runType === 'monthly' ? 8 : 4 }),
    });
    setMessage(res.ok ? '수집 작업을 시작했습니다. 잠시 후 새로고침하면 반영됩니다.' : '수집 시작에 실패했습니다.');
  };

  const latest = summary?.latest || {};
  const runSummary = latest.summary || {};
  const counts = summary?.counts || {};
  const leadershipReport = leadership?.report || null;

  return (
    <div className="fade-in" style={{ display: 'grid', gap: '1rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.8rem', flexWrap: 'wrap' }}>
        <div>
          <h2 style={{ fontSize: '1.25rem', fontWeight: 900 }}>카페 시그널</h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.84rem', marginTop: '0.25rem' }}>
            과거 지표상회 글의 판단 프레임을 현재 월별 수출입·HS 데이터에 적용해 주도 섹터와 종목 신호를 생성합니다.
          </p>
        </div>
        <div style={{ display: 'flex', gap: '0.45rem', alignItems: 'center', flexWrap: 'wrap' }}>
          <button onClick={() => setRunType('weekly')} className={`nav-item ${runType === 'weekly' ? 'active' : ''}`} style={{ padding: '0.5rem 0.75rem' }}>주간</button>
          <button onClick={() => setRunType('monthly')} className={`nav-item ${runType === 'monthly' ? 'active' : ''}`} style={{ padding: '0.5rem 0.75rem' }}>월간</button>
          <button onClick={load} className="nav-item" style={{ padding: '0.5rem 0.75rem' }}><RefreshCw size={15} /> 새로고침</button>
          <button onClick={triggerCollect} className="nav-item" style={{ padding: '0.5rem 0.75rem', color: '#34d399' }}>수동 수집</button>
        </div>
      </div>

      {message && <div className="glass-panel" style={{ padding: '0.75rem', color: '#fbbf24' }}>{message}</div>}
      {error && <div className="glass-panel" style={{ padding: '0.75rem', color: '#f87171' }}>{error}</div>}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: '0.75rem' }}>
        {[
          ['전체 글', counts.posts],
          ['추출 신호', counts.mentions],
          ['최근 수집', counts.latest_collected_at || '-'],
          ['현재 요약', latest.period_key || '-'],
        ].map(([label, value]) => (
          <div key={label} className="glass-panel" style={{ padding: '0.9rem' }}>
            <div style={{ color: 'var(--text-secondary)', fontSize: '0.76rem' }}>{label}</div>
            <div style={{ fontSize: '1.05rem', fontWeight: 900, marginTop: '0.25rem' }}>{typeof value === 'number' ? fmt(value) : value}</div>
          </div>
        ))}
      </div>

      {loading && <div className="glass-panel" style={{ padding: '0.8rem', color: 'var(--text-secondary)' }}>로딩 중...</div>}

      <StockTradeSignalTable rows={stockTradeSignals} counts={stockTradeCounts} />

      <SectorTrafficLightTable rows={sectorTrafficLights} />

      <IndicatorTrafficLightTable rows={trafficLights} />

      <IndicatorSignalTable rows={indicatorSignals} />

      <div className="glass-panel" style={{ padding: '1rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.75rem' }}>
          <FileText size={18} color="#fbbf24" />
          <h3 style={{ fontSize: '1rem', fontWeight: 900 }}>
            월별 주도 섹터/HS 브리핑 {leadership?.period ? `(${leadership.period})` : ''}
          </h3>
        </div>
        {leadershipReport?.summary_text ? (
          <div style={{ whiteSpace: 'pre-wrap', lineHeight: 1.65, fontSize: '0.86rem', color: 'rgba(255,255,255,0.82)' }}>
            {leadershipReport.summary_text}
          </div>
        ) : (
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>아직 생성된 월별 브리핑이 없습니다.</p>
        )}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))', gap: '1rem' }}>
        <LeadershipTable title="월별 주도 섹터" rows={leadership?.sectors || []} type="sector" />
        <LeadershipTable title="월별 주도 HS code" rows={leadership?.hs_codes || []} type="hs" />
      </div>

      <QuantMappingTable rows={quantMappings} />

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '1rem' }}>
        <SummaryList title="요약 상위 종목" items={runSummary.top_stocks || []} />
        <SummaryList title="요약 상위 섹터" items={runSummary.top_sectors || []} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: '1rem' }}>
        <RankingTable title="종목 랭킹" icon={<Newspaper size={18} color="#38bdf8" />} rows={stocks} kind="stock" />
        <RankingTable title="섹터 랭킹" icon={<Layers size={18} color="#34d399" />} rows={sectors} kind="sector" />
        <RankingTable title="지표 랭킹" icon={<BarChart3 size={18} color="#fbbf24" />} rows={indicators} kind="indicator" />
      </div>

      <div className="glass-panel" style={{ padding: '1rem' }}>
        <h3 style={{ fontSize: '1rem', fontWeight: 800, marginBottom: '0.8rem' }}>최근 수집 글</h3>
        {!posts.length ? (
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.85rem' }}>
            아직 수집된 글이 없습니다. 서버 환경변수 `NAVER_CAFE_COOKIE`가 설정되어 있어야 자동 수집됩니다.
          </p>
        ) : (
          <div style={{ display: 'grid', gap: '0.65rem' }}>
            {posts.map((p) => (
              <a key={p.id} href={p.url} target="_blank" rel="noreferrer"
                style={{ display: 'grid', gap: '0.25rem', textDecoration: 'none',
                  padding: '0.7rem', border: '1px solid var(--glass-border)', borderRadius: '8px',
                  background: 'rgba(255,255,255,0.025)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.8rem' }}>
                  <strong style={{ color: 'var(--text-primary)' }}>{p.title}</strong>
                  <ExternalLink size={14} color="var(--text-secondary)" />
                </div>
                <div style={{ color: 'var(--text-secondary)', fontSize: '0.76rem' }}>
                  {p.board_name || '-'} · {p.collected_at || '-'} · {p.mentions || '추출 신호 없음'}
                </div>
                {p.excerpt && <div style={{ color: 'rgba(255,255,255,0.62)', fontSize: '0.78rem' }}>{p.excerpt.slice(0, 160)}</div>}
              </a>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
