import { useState, useEffect, useCallback } from 'react';
import { API } from '../utils';

// SectorRotationView.jsx와 동일한 팔레트 — 메인페이지/상세페이지 시각 일관성 유지
const FOCUS_COLOR = { bg: 'rgba(34,197,94,0.14)', border: '#22c55e', text: '#4ade80' };
const EXIT_COLOR = { bg: 'rgba(239,68,68,0.14)', border: '#ef4444', text: '#f87171' };
const REVERSAL_COLOR = { bg: 'rgba(251,191,36,0.14)', border: '#fbbf24', text: '#fbbf24' };
const NEUTRAL_COLOR = { bg: 'rgba(100,116,139,0.1)', border: '#475569', text: '#94a3b8' };

export default function SectorSignalSummary({ onOpenDetail, changeStock }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const r = await fetch(API('/api/sector-rotation/dashboard-summary')).then(x => x.json());
      setData(r);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  if (loading && !data) {
    return (
      <div className="glass-panel" style={{ padding: '1rem', fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
        🧭 오늘의 섹터 신호 불러오는 중...
      </div>
    );
  }
  if (!data || data.error) return null;

  const { headline, hold_cash, trend_strategy = {}, reversal_strategy = {}, as_of, meta } = data;
  const primary = trend_strategy.primary_focus;
  const secondary = trend_strategy.secondary || [];
  const exitAlerts = trend_strategy.exit_alerts || [];
  const revEntries = reversal_strategy.entries || [];
  const revWatch = reversal_strategy.watch || [];
  const revFalling = reversal_strategy.falling || [];

  const chip = (label, color) => (
    <span style={{
      display: 'inline-block', padding: '0.1rem 0.5rem', borderRadius: '999px', fontSize: '0.68rem', fontWeight: 700,
      background: color.bg, border: `1px solid ${color.border}`, color: color.text,
    }}>{label}</span>
  );

  const leaderChips = (leaders) => (leaders || []).map(l => (
    <span key={l.code}
      onClick={() => changeStock && changeStock(l.code)}
      style={{ cursor: changeStock ? 'pointer' : 'default', fontSize: '0.68rem', color: 'rgba(255,255,255,0.65)',
        border: '1px solid rgba(255,255,255,0.15)', borderRadius: '4px', padding: '0.05rem 0.4rem', marginRight: '0.3rem' }}>
      {l.name}
    </span>
  ));

  return (
    <div className="glass-panel" style={{ padding: '1rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap', marginBottom: '0.7rem' }}>
        <div style={{ fontSize: '0.82rem', fontWeight: 700, color: '#2dd4bf' }}>🧭 오늘의 섹터 신호</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
          <span style={{ fontSize: '0.65rem', color: 'var(--text-secondary)' }}>
            기준일 {as_of || '-'}{meta?.market_status_label ? ` · ${meta.market_status_label}` : ''}
          </span>
          {onOpenDetail && (
            <button onClick={onOpenDetail} style={{
              padding: '0.2rem 0.55rem', borderRadius: '6px', fontSize: '0.68rem', cursor: 'pointer',
              border: '1px solid rgba(45,212,191,0.4)', background: 'rgba(45,212,191,0.1)', color: '#2dd4bf', fontWeight: 600,
            }}>섹터 로테이션 자세히 보기 →</button>
          )}
        </div>
      </div>

      {hold_cash && !exitAlerts.length && (
        <div style={{
          padding: '0.7rem 0.9rem', borderRadius: '8px', background: NEUTRAL_COLOR.bg,
          border: `1px solid ${NEUTRAL_COLOR.border}`, color: NEUTRAL_COLOR.text, fontSize: '0.78rem', fontWeight: 600,
        }}>
          😐 {headline} — 활성도·수급·추세 조건을 동시에 만족하는 섹터가 없습니다.
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
        {primary && (
          <div style={{ padding: '0.7rem 0.9rem', borderRadius: '8px', background: FOCUS_COLOR.bg, border: `1px solid ${FOCUS_COLOR.border}` }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.3rem', flexWrap: 'wrap' }}>
              {chip('🎯 지금 집중', FOCUS_COLOR)}
              <span style={{ fontWeight: 700, fontSize: '0.85rem', color: FOCUS_COLOR.text }}>{primary.label}</span>
              <span style={{ fontSize: '0.68rem', color: 'rgba(255,255,255,0.5)' }}>점수 {primary.score}점 · 4주RS {primary.rs4w > 0 ? '+' : ''}{primary.rs4w}%p · 12주RS {primary.rs12w > 0 ? '+' : ''}{primary.rs12w}%p</span>
            </div>
            <div style={{ fontSize: '0.72rem', color: 'rgba(255,255,255,0.7)', marginBottom: '0.3rem' }}>
              {(primary.reasons || []).join(' · ')}
            </div>
            {primary.leaders?.length > 0 && <div>{leaderChips(primary.leaders)}</div>}
          </div>
        )}

        {secondary.length > 0 && (
          <div style={{ fontSize: '0.72rem', color: 'rgba(255,255,255,0.55)' }}>
            그 외 관찰 섹터: {secondary.map(s => `${s.label}(${s.score}점)`).join(' · ')}
          </div>
        )}

        {exitAlerts.map(a => (
          <div key={a.sector} style={{ padding: '0.6rem 0.9rem', borderRadius: '8px', background: EXIT_COLOR.bg, border: `1px solid ${EXIT_COLOR.border}` }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.15rem' }}>
              {chip('🚪 추세 이탈 경보', EXIT_COLOR)}
              <span style={{ fontWeight: 700, fontSize: '0.8rem', color: EXIT_COLOR.text }}>{a.label}</span>
            </div>
            <div style={{ fontSize: '0.72rem', color: 'rgba(255,255,255,0.7)' }}>{a.message}</div>
          </div>
        ))}

        {revEntries.map(r => (
          <div key={r.sector} style={{ padding: '0.6rem 0.9rem', borderRadius: '8px', background: REVERSAL_COLOR.bg, border: `1px solid ${REVERSAL_COLOR.border}` }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.15rem', flexWrap: 'wrap' }}>
              {chip('🔻 낙폭과대 반등 진입', REVERSAL_COLOR)}
              <span style={{ fontWeight: 700, fontSize: '0.8rem', color: REVERSAL_COLOR.text }}>{r.label}</span>
              <span style={{ fontSize: '0.68rem', color: 'rgba(255,255,255,0.5)' }}>점수 {r.score}점</span>
            </div>
            <div style={{ fontSize: '0.72rem', color: 'rgba(255,255,255,0.7)' }}>{(r.reasons || []).join(' · ')}</div>
          </div>
        ))}

        {revWatch.length > 0 && (
          <div style={{ fontSize: '0.72rem', color: 'rgba(255,255,255,0.5)' }}>
            낙폭과대 관찰(아직 반등 미확정): {revWatch.map(s => `${s.label}(${s.score}점)`).join(' · ')}
          </div>
        )}

        {revFalling.length > 0 && (
          <div style={{ fontSize: '0.72rem', color: 'rgba(248,113,113,0.55)' }}>
            낙폭과대이나 아직 자유낙하 중(진입 금지): {revFalling.map(s => `${s.label} -${s.detail?.drawdown_pct?.toFixed(0)}%`).join(' · ')}
          </div>
        )}
      </div>
    </div>
  );
}
