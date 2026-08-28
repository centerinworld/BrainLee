import { useState, useEffect, useCallback } from 'react';
import { API } from '../utils';

const PHASE_COLOR = {
  Leading:   { bg: 'rgba(34,197,94,0.15)',  border: '#22c55e', text: '#4ade80',  label: '🚀 주도' },
  Improving: { bg: 'rgba(251,191,36,0.15)', border: '#fbbf24', text: '#fbbf24',  label: '📈 개선' },
  Weakening: { bg: 'rgba(239,68,68,0.12)',  border: '#ef4444', text: '#f87171',  label: '⚠ 약화' },
  Lagging:   { bg: 'rgba(100,116,139,0.1)', border: '#475569', text: '#64748b',  label: '😴 부진' },
};
const SIGNAL_COLOR = {
  BUY:     { bg: 'rgba(34,197,94,0.2)',  border: '#22c55e', text: '#4ade80',  label: '매수' },
  WATCH:   { bg: 'rgba(251,191,36,0.2)', border: '#fbbf24', text: '#fbbf24',  label: '관심' },
  NEUTRAL: { bg: 'rgba(100,116,139,0.1)',border: '#475569', text: '#94a3b8',  label: '관망' },
};
const STAGE_COLOR = {
  ENTRY_NOW:   { bg: 'rgba(34,197,94,0.18)',  border: '#22c55e', text: '#4ade80', label: '진입' },
  EARLY_WATCH: { bg: 'rgba(251,191,36,0.18)', border: '#fbbf24', text: '#fbbf24', label: '초기 관찰' },
  HOLD_LEADER: { bg: 'rgba(59,130,246,0.16)', border: '#3b82f6', text: '#93c5fd', label: '보유/추세' },
  WAIT:        { bg: 'rgba(100,116,139,0.10)',border: '#475569', text: '#94a3b8', label: '대기' },
  AVOID:       { bg: 'rgba(239,68,68,0.12)',  border: '#ef4444', text: '#f87171', label: '회피' },
};

export default function SectorRotationView() {
  const [leadership, setLeadership] = useState(null);
  const [scores, setScores] = useState(null);
  const [rotMap, setRotMap] = useState(null);
  const [history, setHistory] = useState(null);
  const [selSector, setSelSector] = useState(null);
  const [picks, setPicks] = useState({}); // sectorKey → picks array
  const [expandedSector, setExpandedSector] = useState(null);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState('leadership'); // leadership | scores | rotation | history

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [l, s, r] = await Promise.all([
        fetch(API('/api/sector-rotation/leadership?months=36&top_n=3')).then(x => x.json()),
        fetch(API('/api/sector-rotation/scores')).then(x => x.json()),
        fetch(API('/api/sector-rotation/rotation-map')).then(x => x.json()),
      ]);
      setLeadership(l);
      setScores(s);
      setRotMap(r);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  const refreshCache = useCallback(async () => {
    setLoading(true);
    try {
      await fetch(API('/api/sector-rotation/refresh-cache'), { method: 'POST' });
      await load();
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, [load]);

  const loadHistory = useCallback(async (sectorKey) => {
    try {
      const r = await fetch(API(`/api/sector-rotation/history/${encodeURIComponent(sectorKey)}?months=36`)).then(x => x.json());
      setHistory(r);
      setSelSector(sectorKey);
      setTab('history');
    } catch (e) { console.error(e); }
  }, []);

  const loadTopPicks = useCallback(async (sectorKey) => {
    if (expandedSector === sectorKey) {
      setExpandedSector(null);
      return;
    }
    try {
      const r = await fetch(API(`/api/sector-rotation/top-picks/${encodeURIComponent(sectorKey)}`)).then(x => x.json());
      setPicks(p => ({ ...p, [sectorKey]: r.picks || [] }));
      setExpandedSector(sectorKey);
    } catch (e) { console.error(e); }
  }, [expandedSector]);

  useEffect(() => { load(); }, [load]);

  const card = (style = {}) => ({
    background: 'rgba(30,41,59,0.8)',
    border: '1px solid rgba(51,65,85,0.6)',
    borderRadius: '0.75rem',
    padding: '1rem',
    ...style,
  });
  const fmtPct = (v, digits = 1) => v === null || v === undefined ? '-' : `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(digits)}%`;
  const fmtEok = (v) => v === null || v === undefined ? '-' : `${Number(v) > 0 ? '+' : ''}${Math.round(Number(v)).toLocaleString()}억`;
  const meta = leadership?.meta || scores?.meta || rotMap?.meta || null;

  return (
    <div style={{ maxWidth: 1280, margin: '0 auto', padding: '0 1rem 2rem' }}>
      {/* 헤더 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.2rem' }}>
        <div>
          <h2 style={{ color: '#e2e8f0', margin: 0, fontSize: '1.2rem', fontWeight: 700 }}>🔄 주도섹터 로테이션 감지</h2>
          <p style={{ color: '#64748b', fontSize: '0.82rem', margin: '0.25rem 0 0' }}>
            외국인/기관 3개월 순매수 + 영업이익YoY 기반 선행 신호 · 실증: 화장품 BUY신호 2024-01 → 급등 2024-05 (4개월 선행)
          </p>
          {meta && (
            <p style={{ color: '#94a3b8', fontSize: '0.75rem', margin: '0.35rem 0 0' }}>
              {meta.market_status_label || '캐시 기준'} · 기준일 {meta.as_of || leadership?.as_of || '-'} · 마지막 계산 {meta.computed_at || '-'}
            </p>
          )}
        </div>
        <button onClick={refreshCache} disabled={loading}
          style={{ background: 'rgba(99,102,241,0.2)', border: '1px solid rgba(99,102,241,0.4)', borderRadius: '0.5rem', padding: '0.4rem 1rem', color: '#a5b4fc', cursor: 'pointer', fontSize: '0.85rem' }}>
          {loading ? '계산 중…' : '🔄 즉시 재계산'}
        </button>
      </div>

      {/* 탭 */}
      <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem' }}>
        {[['leadership','🚦 주도섹터·진입'], ['scores','📊 섹터 스코어'], ['rotation','🗺 4분면 맵'], ['history','📈 RS 히스토리']].map(([k, lbl]) => (
          <button key={k} onClick={() => setTab(k)}
            style={{ background: tab === k ? 'rgba(99,102,241,0.3)' : 'rgba(30,41,59,0.6)', border: `1px solid ${tab === k ? '#6366f1' : 'rgba(51,65,85,0.5)'}`, borderRadius: '0.5rem', padding: '0.4rem 0.9rem', color: tab === k ? '#a5b4fc' : '#64748b', cursor: 'pointer', fontSize: '0.82rem' }}>
            {lbl}
          </button>
        ))}
      </div>

      {/* 탭 0: 주도섹터·진입 타이밍 */}
      {tab === 'leadership' && leadership && (
        <div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: '0.75rem', marginBottom: '1rem' }}>
            {[
              ['진입 섹터', leadership.summary?.entry_now || 0, '#4ade80'],
              ['초기 관찰', leadership.summary?.watch || 0, '#fbbf24'],
              ['주도 국면', leadership.summary?.leading || 0, '#93c5fd'],
              ['기준', meta?.market_status_label || leadership.as_of || '-', '#c4b5fd'],
            ].map(([label, value, color]) => (
              <div key={label} style={card({ padding: '0.85rem 1rem' })}>
                <div style={{ color: '#64748b', fontSize: '0.72rem', fontWeight: 700, marginBottom: '0.25rem' }}>{label}</div>
                <div style={{ color, fontSize: label === '기준' ? '0.95rem' : '1.55rem', fontWeight: 900 }}>{value}</div>
              </div>
            ))}
          </div>

          <div style={card({ padding: 0, overflow: 'hidden' })}>
            <div style={{ padding: '0.85rem 1rem', borderBottom: '1px solid rgba(51,65,85,0.55)', display: 'flex', justifyContent: 'space-between', gap: '1rem', alignItems: 'center' }}>
              <div style={{ color: '#e2e8f0', fontWeight: 800 }}>주도섹터 진입 테이블</div>
              <div style={{ color: '#64748b', fontSize: '0.75rem' }}>수급·실적·수출·거래량·RS·주도주 압축 점수</div>
            </div>
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', minWidth: 1180, borderCollapse: 'collapse', fontSize: '0.78rem' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid rgba(51,65,85,0.5)', background: 'rgba(15,23,42,0.35)' }}>
                    {['단계', '섹터', '점수/국면', '핵심 근거', '수급 3M', '수출/실적', 'RS', '주도주 TOP3', '최근 강신호'].map(h => (
                      <th key={h} style={{ padding: '0.55rem 0.65rem', color: '#94a3b8', fontWeight: 700, textAlign: 'left', whiteSpace: 'nowrap' }}>{h}</th>
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
                      <tr key={s.sector} style={{ borderBottom: '1px solid rgba(30,41,59,0.65)', background: s.stage === 'ENTRY_NOW' ? 'rgba(34,197,94,0.04)' : 'transparent' }}>
                        <td style={{ padding: '0.65rem', verticalAlign: 'top' }}>
                          <span style={{ display: 'inline-block', minWidth: 62, textAlign: 'center', background: st.bg, border: `1px solid ${st.border}`, borderRadius: '0.35rem', padding: '0.18rem 0.45rem', color: st.text, fontWeight: 800, fontSize: '0.72rem' }}>
                            {s.stage_label || st.label}
                          </span>
                        </td>
                        <td style={{ padding: '0.65rem', color: '#e2e8f0', fontWeight: 800, verticalAlign: 'top', whiteSpace: 'nowrap' }}>
                          {s.label}
                          <div style={{ marginTop: 4 }}>
                            <button onClick={() => loadHistory(s.sector)}
                              style={{ background: 'rgba(99,102,241,0.12)', border: '1px solid rgba(99,102,241,0.28)', borderRadius: 4, padding: '0.12rem 0.45rem', color: '#a5b4fc', cursor: 'pointer', fontSize: '0.68rem' }}>
                              히스토리
                            </button>
                          </div>
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
                            <div key={r} style={{ color: r === '선행 신호 부족' ? '#64748b' : '#cbd5e1', lineHeight: 1.55 }}>{r}</div>
                          ))}
                        </td>
                        <td style={{ padding: '0.65rem', verticalAlign: 'top', whiteSpace: 'nowrap' }}>
                          <div style={{ color: (d.frn_3m_억 || 0) > 0 ? '#4ade80' : '#f87171', fontWeight: 700 }}>외인 {fmtEok(d.frn_3m_억)}</div>
                          <div style={{ color: (d.inst_3m_억 || 0) > 0 ? '#60a5fa' : '#f87171', fontWeight: 700, marginTop: 4 }}>기관 {fmtEok(d.inst_3m_억)}</div>
                        </td>
                        <td style={{ padding: '0.65rem', verticalAlign: 'top', whiteSpace: 'nowrap' }}>
                          <div style={{ color: (d.hs_export_yoy || 0) >= 15 ? '#4ade80' : (d.hs_export_yoy || 0) < -10 ? '#f87171' : '#94a3b8' }}>수출 {fmtPct(d.hs_export_yoy, 0)}</div>
                          <div style={{ color: (d.op_yoy || 0) >= 20 ? '#4ade80' : (d.op_yoy || 0) < -20 ? '#f87171' : '#94a3b8', marginTop: 4 }}>OP {fmtPct(d.op_yoy, 0)}</div>
                          <div style={{ color: (d.vol_ratio || 1) >= 1.5 ? '#fbbf24' : '#64748b', marginTop: 4 }}>거래량 {d.vol_ratio ? `${d.vol_ratio}x` : '-'}</div>
                        </td>
                        <td style={{ padding: '0.65rem', verticalAlign: 'top', whiteSpace: 'nowrap' }}>
                          <div style={{ color: (s.rs4w || 0) > 0 ? '#4ade80' : '#f87171', fontWeight: 700 }}>4W {fmtPct(s.rs4w)}</div>
                          <div style={{ color: (s.rs12w || 0) > 0 ? '#4ade80' : '#f87171', marginTop: 4 }}>12W {fmtPct(s.rs12w)}</div>
                        </td>
                        <td style={{ padding: '0.65rem', verticalAlign: 'top', minWidth: 230 }}>
                          {(s.leaders || []).map((p, i) => (
                            <div key={p.code} style={{ display: 'grid', gridTemplateColumns: '22px 1fr 42px', gap: '0.35rem', alignItems: 'start', marginBottom: i === (s.leaders.length - 1) ? 0 : 6 }}>
                              <span style={{ color: i === 0 ? '#fbbf24' : '#64748b', fontWeight: 900 }}>{i + 1}</span>
                              <div>
                                <span style={{ color: '#e2e8f0', fontWeight: 800 }}>{p.name}</span>
                                <span style={{ color: '#64748b', marginLeft: 5 }}>{p.code}</span>
                                <div style={{ color: '#94a3b8', fontSize: '0.68rem', marginTop: 2 }}>{(p.reasons || []).join(' · ')}</div>
                              </div>
                              <span style={{ color: (p.surge_score || 0) >= 50 ? '#4ade80' : (p.surge_score || 0) >= 30 ? '#fbbf24' : '#94a3b8', fontWeight: 900, textAlign: 'right' }}>{p.surge_score}</span>
                            </div>
                          ))}
                        </td>
                        <td style={{ padding: '0.65rem', verticalAlign: 'top', minWidth: 140 }}>
                          {peak ? (
                            <>
                              <div style={{ color: '#cbd5e1', fontWeight: 700 }}>최고 {peak.month} · {peak.score}점</div>
                              <div style={{ color: latestBuy ? '#4ade80' : '#64748b', marginTop: 4 }}>
                                최근 BUY {latestBuy ? `${latestBuy.month} · ${latestBuy.score}점` : '-'}
                              </div>
                              <div style={{ display: 'flex', gap: 2, alignItems: 'flex-end', height: 24, marginTop: 7 }}>
                                {(s.history_recent || []).map((h) => (
                                  <div key={h.month} title={`${h.month} ${h.score}점`}
                                    style={{ width: 7, height: Math.max(4, Math.min(24, h.score / 4)), borderRadius: 2, background: h.signal === 'BUY' ? '#22c55e' : h.signal === 'WATCH' ? '#fbbf24' : '#475569' }} />
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
                    <span style={{ color: '#64748b', fontSize: '0.8rem' }}>점</span>
                  </div>
                  {d.pattern && (
                    <div style={{ marginTop: '0.3rem', fontSize: '0.75rem', color: '#fbbf24', fontWeight: 700 }}>{d.pattern}</div>
                  )}
                  <div style={{ display: 'flex', gap: '1rem', marginTop: '0.3rem' }}>
                    {d.frn_3m_억 !== undefined && (
                      <span style={{ fontSize: '0.72rem', color: d.frn_3m_억 > 0 ? '#4ade80' : '#f87171' }}>
                        외국인 {d.frn_3m_억 > 0 ? '+' : ''}{(d.frn_3m_억||0).toLocaleString()}억
                      </span>
                    )}
                    {d.inst_3m_억 !== undefined && (
                      <span style={{ fontSize: '0.72rem', color: d.inst_3m_억 > 0 ? '#60a5fa' : '#f87171' }}>
                        기관 {d.inst_3m_억 > 0 ? '+' : ''}{(d.inst_3m_억||0).toLocaleString()}억
                      </span>
                    )}
                  </div>
                  {d.hs_export_yoy !== null && d.hs_export_yoy !== undefined && (
                    <div style={{ marginTop: '0.2rem', fontSize: '0.72rem', color: d.hs_export_yoy > 15 ? '#4ade80' : d.hs_export_yoy > 0 ? '#86efac' : '#f87171' }}>
                      수출YoY {d.hs_export_yoy > 0 ? '+' : ''}{d.hs_export_yoy}%
                    </div>
                  )}
                  {d.op_yoy !== null && d.op_yoy !== undefined && (
                    <div style={{ marginTop: '0.2rem', fontSize: '0.72rem', color: d.op_yoy > 50 ? '#4ade80' : d.op_yoy > 0 ? '#86efac' : '#f87171' }}>
                      영업이익YoY {d.op_yoy > 0 ? '+' : ''}{d.op_yoy}%
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
                    {['섹터', '점수', '신호', '패턴', '외국인3M', '기관3M', '수출YoY', '영업이익YoY', '거래량비', 'RS4주', '히스토리'].map(h => (
                      <th key={h} style={{ padding: '0.5rem 0.75rem', color: '#64748b', fontWeight: 600, textAlign: 'left', whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {scores.sectors.map(s => {
                    const sc = SIGNAL_COLOR[s.signal];
                    const d = s.detail;
                    const fmt억 = v => v !== undefined ? `${v > 0 ? '+' : ''}${Math.round(v).toLocaleString()}억` : '-';
                    return (
                      <>
                      <tr key={s.sector} style={{ borderBottom: '1px solid rgba(30,41,59,0.6)' }}>
                        <td style={{ padding: '0.6rem 0.75rem', color: '#e2e8f0', fontWeight: 600 }}>{s.label}</td>
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
                        <td style={{ padding: '0.6rem 0.75rem', color: '#fbbf24', fontWeight: 700, fontSize: '0.75rem' }}>
                          {d.pattern || '-'}
                        </td>
                        <td style={{ padding: '0.6rem 0.75rem', color: (d.frn_3m_억||0) > 300 ? '#4ade80' : (d.frn_3m_억||0) < -300 ? '#f87171' : '#94a3b8', fontWeight: 600 }}>
                          {fmt억(d.frn_3m_억)}
                        </td>
                        <td style={{ padding: '0.6rem 0.75rem', color: (d.inst_3m_억||0) > 200 ? '#60a5fa' : (d.inst_3m_억||0) < -200 ? '#f87171' : '#94a3b8', fontWeight: 600 }}>
                          {fmt억(d.inst_3m_억)}
                        </td>
                        <td style={{ padding: '0.6rem 0.75rem', color: (d.hs_export_yoy||0) > 15 ? '#4ade80' : (d.hs_export_yoy||0) < -10 ? '#f87171' : '#94a3b8', fontWeight: 600 }}>
                          {d.hs_export_yoy !== null && d.hs_export_yoy !== undefined ? `${d.hs_export_yoy > 0 ? '+' : ''}${d.hs_export_yoy}%` : '-'}
                        </td>
                        <td style={{ padding: '0.6rem 0.75rem', color: (d.op_yoy||0) > 50 ? '#4ade80' : (d.op_yoy||0) > 0 ? '#86efac' : (d.op_yoy||0) < -30 ? '#f87171' : '#94a3b8', fontWeight: 600 }}>
                          {d.op_yoy !== null && d.op_yoy !== undefined ? `${d.op_yoy > 0 ? '+' : ''}${d.op_yoy}%` : '-'}
                        </td>
                        <td style={{ padding: '0.6rem 0.75rem', color: (d.vol_ratio||1) > 1.5 ? '#fbbf24' : '#94a3b8' }}>
                          {d.vol_ratio ? `${d.vol_ratio}x` : '-'}
                        </td>
                        <td style={{ padding: '0.6rem 0.75rem', color: (d.rs4w_excess||0) > 5 ? '#4ade80' : (d.rs4w_excess||0) < -5 ? '#f87171' : '#94a3b8' }}>
                          {d.rs4w_excess !== undefined ? `${d.rs4w_excess > 0 ? '+' : ''}${d.rs4w_excess}%` : '-'}
                        </td>
                        <td style={{ padding: '0.6rem 0.75rem', display: 'flex', gap: '0.4rem' }}>
                          <button onClick={() => loadHistory(s.sector)}
                            style={{ background: 'rgba(99,102,241,0.15)', border: '1px solid rgba(99,102,241,0.3)', borderRadius: '0.25rem', padding: '0.15rem 0.5rem', color: '#a5b4fc', cursor: 'pointer', fontSize: '0.72rem' }}>
                            📈 RS
                          </button>
                          <button onClick={() => loadTopPicks(s.sector)}
                            style={{ background: expandedSector === s.sector ? 'rgba(34,197,94,0.2)' : 'rgba(34,197,94,0.1)', border: `1px solid ${expandedSector === s.sector ? '#22c55e' : 'rgba(34,197,94,0.3)'}`, borderRadius: '0.25rem', padding: '0.15rem 0.5rem', color: '#4ade80', cursor: 'pointer', fontSize: '0.72rem' }}>
                            🏆 픽
                          </button>
                        </td>
                      </tr>
                      {expandedSector === s.sector && picks[s.sector] && (
                        <tr key={`picks-${s.sector}`}>
                          <td colSpan={11} style={{ padding: '0 0.75rem 0.75rem', background: 'rgba(15,23,42,0.6)' }}>
                            <div style={{ borderTop: '1px solid rgba(34,197,94,0.3)', paddingTop: '0.5rem', fontSize: '0.75rem' }}>
                              <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.35rem' }}>
                                <span style={{ color: '#4ade80', fontWeight: 700 }}>🏆 {s.label} 급등 후보 종목</span>
                                <span style={{ color: '#64748b', fontSize: '0.68rem' }}>급등점수 = 영업이익YoY(40) + 기관집중도%(30) + 52주위치(20) + 소형주(10)</span>
                              </div>
                              {/* 스코어링 설명 배너 */}
                              <div style={{ background: 'rgba(99,102,241,0.08)', border: '1px solid rgba(99,102,241,0.2)', borderRadius: '0.35rem', padding: '0.3rem 0.6rem', marginBottom: '0.4rem', fontSize: '0.68rem', color: '#94a3b8', lineHeight: 1.7 }}>
                                실증 ① SK하이닉스 영업이익+404%→주가+290% ② 원익IPS 기관집중도0.43%→+524% ③ 급등주 70.5%는 52주 저점 근처 출발
                              </div>
                              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                                <thead>
                                  <tr style={{ color: '#64748b', borderBottom: '1px solid rgba(51,65,85,0.5)' }}>
                                    {['급등점수','종목','시총','영업이익YoY','기관집중도%','기관3M','외인3M','52주위치','3M수익률','PBR'].map(h =>
                                      <th key={h} style={{ padding: '0.25rem 0.4rem', textAlign: 'left', fontWeight: 600, fontSize: '0.68rem' }}>{h}</th>
                                    )}
                                  </tr>
                                </thead>
                                <tbody>
                                  {picks[s.sector].map((p, i) => {
                                    const scoreColor = p.surge_score >= 50 ? '#4ade80' : p.surge_score >= 30 ? '#fbbf24' : '#94a3b8';
                                    return (
                                      <tr key={p.code} style={{ borderTop: '1px solid rgba(30,41,59,0.4)', background: i === 0 ? 'rgba(34,197,94,0.04)' : 'transparent' }}>
                                        <td style={{ padding: '0.3rem 0.4rem', fontWeight: 700, color: scoreColor, fontSize: '0.85rem' }}>{p.surge_score}
                                          <div style={{ fontSize: '0.6rem', color: '#64748b', maxWidth: 120, lineHeight: 1.3 }}>{p.score_detail}</div>
                                        </td>
                                        <td style={{ padding: '0.3rem 0.4rem', color: '#e2e8f0', fontWeight: 600 }}>{p.name}
                                          <div style={{ fontSize: '0.65rem', color: '#64748b' }}>{p.code}</div>
                                        </td>
                                        <td style={{ padding: '0.3rem 0.4rem', color: '#94a3b8', fontSize: '0.72rem' }}>{(p.market_cap_억||0) > 10000 ? `${Math.round((p.market_cap_억||0)/10000)}조` : `${(p.market_cap_억||0).toLocaleString()}억`}</td>
                                        <td style={{ padding: '0.3rem 0.4rem', color: (p.op_yoy||0) > 100 ? '#4ade80' : (p.op_yoy||0) > 20 ? '#fbbf24' : (p.op_yoy||0) < 0 ? '#f87171' : '#94a3b8', fontWeight: 700 }}>
                                          {p.op_yoy !== null && p.op_yoy !== undefined ? `${p.op_yoy > 0 ? '+' : ''}${p.op_yoy}%` : '-'}
                                          {p.op_latest_year && <div style={{ fontSize: '0.6rem', color: '#64748b' }}>{p.op_latest_year}년기준</div>}
                                        </td>
                                        <td style={{ padding: '0.3rem 0.4rem', color: (p.inst_intensity_pct||0) > 0.3 ? '#60a5fa' : (p.inst_intensity_pct||0) < -0.3 ? '#f87171' : '#94a3b8', fontWeight: (p.inst_intensity_pct||0) > 0.3 ? 700 : 400 }}>
                                          {p.inst_intensity_pct !== undefined ? `${p.inst_intensity_pct > 0 ? '+' : ''}${p.inst_intensity_pct}%` : '-'}
                                        </td>
                                        <td style={{ padding: '0.3rem 0.4rem', color: (p.inst_3m_억||0) > 0 ? '#60a5fa' : '#f87171', fontSize: '0.72rem' }}>{p.inst_3m_억 > 0 ? '+' : ''}{(p.inst_3m_억||0).toLocaleString()}억</td>
                                        <td style={{ padding: '0.3rem 0.4rem', color: (p.frn_3m_억||0) > 0 ? '#4ade80' : '#f87171', fontSize: '0.72rem' }}>{p.frn_3m_억 > 0 ? '+' : ''}{(p.frn_3m_억||0).toLocaleString()}억</td>
                                        <td style={{ padding: '0.3rem 0.4rem', fontSize: '0.72rem' }}>
                                          <div style={{ width: 60, height: 4, background: 'rgba(51,65,85,0.5)', borderRadius: 2, display: 'inline-block', verticalAlign: 'middle' }}>
                                            <div style={{ width: `${Math.min(100, p.pos_52w_pct||0)}%`, height: '100%', background: (p.pos_52w_pct||50) < 30 ? '#4ade80' : (p.pos_52w_pct||50) > 80 ? '#f87171' : '#fbbf24', borderRadius: 2 }}/>
                                          </div>
                                          <span style={{ marginLeft: 4, color: (p.pos_52w_pct||50) < 30 ? '#4ade80' : (p.pos_52w_pct||50) > 80 ? '#f87171' : '#fbbf24' }}>{p.pos_52w_pct||'-'}%</span>
                                        </td>
                                        <td style={{ padding: '0.3rem 0.4rem', color: (p.ret_3m||0) > 20 ? '#4ade80' : (p.ret_3m||0) < -10 ? '#f87171' : '#fbbf24', fontWeight: 700 }}>
                                          {p.ret_3m !== null && p.ret_3m !== undefined ? `${p.ret_3m > 0 ? '+' : ''}${p.ret_3m}%` : '-'}
                                        </td>
                                        <td style={{ padding: '0.3rem 0.4rem', color: '#94a3b8', fontSize: '0.72rem' }}>{p.pbr || '-'}</td>
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
            <div style={{ marginTop: '1rem', padding: '0.75rem', background: 'rgba(15,23,42,0.5)', borderRadius: '0.5rem', fontSize: '0.75rem', color: '#64748b', lineHeight: 1.8 }}>
              <span style={{ color: '#94a3b8', fontWeight: 600 }}>신호 기준: </span>
              RS4주 초과 +15%↑=30점 / RS12주 초과 +20%↑=25점 / 거래량비 1.8x↑=20점 / 섹터폭 40%↑=15점 / 기관수급 1000억↑=10점 / 수출YoY +30%↑=10점
              <br/>
              <span style={{ color: '#4ade80' }}>■ BUY 65점+</span> · <span style={{ color: '#fbbf24' }}>■ WATCH 40~64점</span> · <span style={{ color: '#64748b' }}>■ NEUTRAL ~39점</span>
              · 집중투자 권장: BUY 섹터 상위 3~5종목에 포트 60% 집중
            </div>
          </div>
        </div>
      )}

      {/* 탭 2: 4분면 로테이션 맵 */}
      {tab === 'rotation' && rotMap && (
        <div style={card({ minHeight: 480 })}>
          <div style={{ marginBottom: '0.75rem', fontSize: '0.82rem', color: '#94a3b8' }}>
            <b style={{ color: '#e2e8f0' }}>RS 4분면 맵</b> — X축: RS 12주(장기), Y축: RS 4주(단기) / KOSPI 초과수익 기준
          </div>
          <div style={{ position: 'relative', height: 400, border: '1px solid rgba(51,65,85,0.4)', borderRadius: '0.5rem', overflow: 'hidden' }}>
            {/* 배경 4분면 */}
            <div style={{ position: 'absolute', left: '50%', top: 0, width: 1, height: '100%', background: 'rgba(51,65,85,0.5)' }}/>
            <div style={{ position: 'absolute', left: 0, top: '50%', width: '100%', height: 1, background: 'rgba(51,65,85,0.5)' }}/>
            {/* 4분면 라벨 */}
            <div style={{ position: 'absolute', left: '25%', top: '15%', textAlign: 'center', fontSize: '0.72rem', color: '#64748b', transform: 'translate(-50%,-50%)' }}>😴 부진<br/>(Lagging)</div>
            <div style={{ position: 'absolute', left: '75%', top: '15%', textAlign: 'center', fontSize: '0.72rem', color: '#fbbf24', transform: 'translate(-50%,-50%)' }}>📈 개선중<br/>(Improving)</div>
            <div style={{ position: 'absolute', left: '25%', top: '85%', textAlign: 'center', fontSize: '0.72rem', color: '#f87171', transform: 'translate(-50%,-50%)' }}>⚠ 약화<br/>(Weakening)</div>
            <div style={{ position: 'absolute', left: '75%', top: '85%', textAlign: 'center', fontSize: '0.72rem', color: '#4ade80', transform: 'translate(-50%,-50%)' }}>🚀 주도<br/>(Leading)</div>
            {/* 섹터 점 */}
            {rotMap.sectors.map(s => {
              const maxR = 30; // 최대 표시 범위 %
              const x = Math.max(5, Math.min(95, 50 + (s.rs12w / maxR) * 45));
              const y = Math.max(5, Math.min(95, 50 - (s.rs4w / maxR) * 45));
              const ph = PHASE_COLOR[s.phase] || PHASE_COLOR.Lagging;
              return (
                <div key={s.sector} style={{ position: 'absolute', left: `${x}%`, top: `${y}%`, transform: 'translate(-50%,-50%)', cursor: 'pointer', zIndex: 10 }}
                  onClick={() => loadHistory(s.sector)}>
                  <div style={{ background: ph.bg, border: `2px solid ${ph.border}`, borderRadius: '0.4rem', padding: '0.2rem 0.5rem', whiteSpace: 'nowrap', fontSize: '0.72rem', color: ph.text, fontWeight: 700, boxShadow: '0 2px 8px rgba(0,0,0,0.4)' }}>
                    {s.label}<br/>
                    <span style={{ fontSize: '0.65rem', fontWeight: 400 }}>4w:{s.rs4w > 0 ? '+' : ''}{s.rs4w}% 12w:{s.rs12w > 0 ? '+' : ''}{s.rs12w}%</span>
                  </div>
                </div>
              );
            })}
          </div>
          <div style={{ marginTop: '0.75rem', fontSize: '0.75rem', color: '#64748b' }}>
            ※ 투자 순서: <span style={{ color: '#4ade80' }}>Improving(개선중)</span> → <span style={{ color: '#4ade80' }}>Leading(주도)</span> 진입 타이밍. Weakening 구간에서 비중 축소. 클릭하면 RS 히스토리 조회.
          </div>
        </div>
      )}

      {/* 탭 3: RS 히스토리 */}
      {tab === 'history' && history && (
        <div style={card()}>
          <div style={{ fontWeight: 700, color: '#e2e8f0', marginBottom: '0.75rem' }}>
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
                    {isPos && <div style={{ width: '100%', height: h, background: excess > 10 ? '#22c55e' : '#4ade80', borderRadius: '2px 2px 0 0', opacity: 0.85 }}/>}
                    <div style={{ width: '100%', height: 1, background: 'rgba(100,116,139,0.4)' }}/>
                    {!isPos && <div style={{ width: '100%', height: h, background: '#ef4444', borderRadius: '0 0 2px 2px', opacity: 0.85 }}/>}
                    {i % 6 === 0 && (
                      <div style={{ fontSize: '0.55rem', color: '#475569', marginTop: 3, whiteSpace: 'nowrap', writingMode: 'vertical-rl', height: 30 }}>
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
              <thead style={{ position: 'sticky', top: 0, background: '#1e293b', zIndex: 1 }}>
                <tr>
                  {['월', '섹터', 'KOSPI', '초과수익'].map(h => (
                    <th key={h} style={{ padding: '0.4rem 0.6rem', color: '#64748b', fontWeight: 600, textAlign: 'left' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {[...history.history].reverse().map((m, i) => (
                  <tr key={i} style={{ borderBottom: '1px solid rgba(30,41,59,0.5)', background: Math.abs(m.excess) > 10 ? 'rgba(34,197,94,0.05)' : 'transparent' }}>
                    <td style={{ padding: '0.35rem 0.6rem', color: '#94a3b8' }}>{m.month}</td>
                    <td style={{ padding: '0.35rem 0.6rem', color: m.sect_ret >= 0 ? '#4ade80' : '#f87171', fontWeight: 600 }}>{m.sect_ret > 0 ? '+' : ''}{m.sect_ret}%</td>
                    <td style={{ padding: '0.35rem 0.6rem', color: '#64748b' }}>{m.kospi_ret > 0 ? '+' : ''}{m.kospi_ret}%</td>
                    <td style={{ padding: '0.35rem 0.6rem', color: m.excess >= 0 ? '#4ade80' : '#f87171', fontWeight: 700 }}>
                      {m.excess > 0 ? '+' : ''}{m.excess}%
                      {Math.abs(m.excess) > 10 && <span style={{ marginLeft: '0.3rem', fontSize: '0.65rem' }}>{m.excess > 0 ? '⭐' : '❌'}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <button onClick={() => setTab('scores')} style={{ marginTop: '0.75rem', background: 'none', border: '1px solid rgba(51,65,85,0.5)', borderRadius: '0.4rem', padding: '0.3rem 0.7rem', color: '#64748b', cursor: 'pointer', fontSize: '0.78rem' }}>
            ← 스코어 목록으로
          </button>
        </div>
      )}
    </div>
  );
}
