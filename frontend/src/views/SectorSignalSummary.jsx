/* light-theme-codemod-2026-09-27 */
/* light-theme-codemod-pass2-2026-09-27 */
/* light-theme-codemod-pass3-2026-09-27 */
/* light-theme-codemod-pass4-2026-09-27 */
/* light-theme-codemod-pass5-2026-09-27 */
/* light-theme-codemod-pass6-2026-09-27 */
/* light-theme-codemod-pass7-2026-09-27 */
import { useState, useEffect, useCallback, useMemo } from 'react';
import { API } from '../utils';

/**
 * 오늘의 섹터 신호 — 전체 섹터를 한 표에 (2026-09-27 개편)
 * 열: 섹터 · 신호등 · 핵심 사유 한 줄. 자세한 내용은 행을 눌러 「섹터 로테이션」 상세로 이동한다.
 * 데이터: /api/sector-rotation/dashboard-summary(추세·반등 신호) + /scores(전체 섹터 목록·점수). 한 섹터가 여러 신호에 걸리면 우선순위가 높은 것 하나만 보인다.
 */
const LIGHT = {
  green:  { color: '#16a34a', label: '진입·집중' },
  yellow: { color: '#eab308', label: '관찰' },
  red:    { color: '#dc2626', label: '경보·진입 금지' },
  gray:   { color: '#94a3b8', label: '중립' },
};
const ORDER = { green: 0, yellow: 1, gray: 2, red: 3 };
const stripEmoji = (s) => String(s || '').replace(/[\p{Extended_Pictographic}️‍]/gu, '').trim();
const sgn = (v) => (v > 0 ? '+' : '') + v;

const Dot = ({ kind }) => (
  <span role="img" aria-label={LIGHT[kind].label} title={LIGHT[kind].label}
    style={{ display: 'inline-block', width: 16, height: 16, borderRadius: '50%', background: LIGHT[kind].color, boxShadow: `0 0 0 3px ${LIGHT[kind].color}26` }} />
);

export default function SectorSignalSummary({ onOpenDetail }) {
  const [summary, setSummary] = useState(null);
  const [scores, setScores] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const [s, sc] = await Promise.all([
        fetch(API('/api/sector-rotation/dashboard-summary')).then((x) => x.json()),
        fetch(API('/api/sector-rotation/scores')).then((x) => x.json()),
      ]);
      setSummary(s); setScores(sc);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => { load(); }, [load]);

  const rows = useMemo(() => {
    if (!scores?.sectors) return [];
    const bySector = {};
    const put = (sector, kind, reason, rank) => { if (!bySector[sector] || rank < bySector[sector].rank) bySector[sector] = { kind, reason, rank }; };
    const ts = summary?.trend_strategy || {};
    const rs = summary?.reversal_strategy || {};
    // 우선순위(작을수록 우선): 이탈 경보 > 진입 금지 > 집중 > 반등 진입 > 관찰
    (ts.exit_alerts || []).forEach((a) => put(a.sector, 'red', `최근 4주 RS ${sgn(a.rs4w)}%p로 꺾임 (12주 ${sgn(a.rs12w)}%p) — 비중 축소 고려`, 1));
    (rs.falling || []).forEach((f) => put(f.sector, 'red', `고점 대비 -${Math.round(f.detail?.drawdown_pct || 0)}%, 4주 RS ${sgn(f.detail?.rs4w_excess)}%p — 자유낙하, 진입 금지`, 2));
    if (ts.primary_focus) put(ts.primary_focus.sector, 'green', (ts.primary_focus.reasons || []).slice(0, 3).join(' · '), 3);
    (rs.entries || []).forEach((e) => put(e.sector, 'green', `낙폭과대 반등 — ${(e.reasons || []).slice(0, 2).join(' · ')}`, 4));
    (ts.secondary || []).forEach((s) => put(s.sector, 'yellow', `추세 관찰 (${s.score}점)`, 5));
    (rs.watch || []).forEach((w) => put(w.sector, 'yellow', `낙폭과대 관찰 — ${(w.reasons || []).slice(0, 2).join(' · ')}`, 6));
    return scores.sectors.map((s) => {
      const hit = bySector[s.sector];
      return {
        key: s.sector, name: stripEmoji(s.label || s.sector), score: s.score,
        kind: hit ? hit.kind : 'gray',
        reason: hit ? hit.reason : `특이 신호 없음 (점수 ${s.score})`,
      };
    }).sort((a, b) => ORDER[a.kind] - ORDER[b.kind] || b.score - a.score);
  }, [summary, scores]);

  if (loading && !summary) {
    return <div className="glass-panel" style={{ padding: '1rem', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>오늘의 섹터 신호 불러오는 중...</div>;
  }
  if (!rows.length) return null;
  const asOf = summary?.as_of || scores?.as_of;

  return (
    <div className="glass-panel" style={{ padding: '1rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap', marginBottom: '0.7rem' }}>
        <div style={{ fontSize: '0.9rem', fontWeight: 800 }}>오늘의 섹터 신호</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.7rem', flexWrap: 'wrap' }}>
          <span style={{ fontSize: '0.72rem', color: 'var(--text-secondary)' }}>
            기준일 {asOf || '-'}{summary?.meta?.market_status_label ? ` · ${summary.meta.market_status_label}` : ''}
          </span>
          {onOpenDetail && (
            <button onClick={onOpenDetail} style={{ padding: '0.25rem 0.65rem', borderRadius: '6px', fontSize: '0.72rem', cursor: 'pointer', border: '1px solid #1a73e8', background: '#e8f0fe', color: '#0f3d91', fontWeight: 700 }}>
              섹터 로테이션 자세히 보기 →
            </button>
          )}
        </div>
      </div>

      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
          <thead>
            <tr>
              <th style={{ textAlign: 'left', padding: '0.5rem 0.7rem', whiteSpace: 'nowrap' }}>섹터</th>
              <th style={{ textAlign: 'center', padding: '0.5rem 0.7rem', width: 64 }}>신호</th>
              <th style={{ textAlign: 'left', padding: '0.5rem 0.7rem' }}>핵심 사유</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} onClick={onOpenDetail} style={{ cursor: onOpenDetail ? 'pointer' : 'default' }} title="눌러서 섹터 로테이션 상세로 이동">
                <td style={{ padding: '0.55rem 0.7rem', fontWeight: 700, whiteSpace: 'nowrap' }}>{r.name}</td>
                <td style={{ padding: '0.55rem 0.7rem', textAlign: 'center' }}><Dot kind={r.kind} /></td>
                <td style={{ padding: '0.55rem 0.7rem', color: r.kind === 'gray' ? 'var(--text-secondary)' : 'var(--text-primary)' }}>{r.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', marginTop: '0.6rem', fontSize: '0.7rem', color: 'var(--text-secondary)' }}>
        {Object.entries(LIGHT).map(([k, v]) => (
          <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: '0.3rem' }}>
            <span style={{ width: 9, height: 9, borderRadius: '50%', background: v.color, display: 'inline-block' }} />{v.label}
          </span>
        ))}
      </div>
    </div>
  );
}
