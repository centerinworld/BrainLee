/* light-theme-codemod-2026-09-27 */
/* light-theme-codemod-pass2-2026-09-27 */
/* light-theme-codemod-pass3-2026-09-27 */
/* light-theme-codemod-pass4-2026-09-27 */
/* light-theme-codemod-pass5-2026-09-27 */
/* light-theme-codemod-pass6-2026-09-27 */
/* light-theme-codemod-pass7-2026-09-27 */
/**
 * PriceIntegrityCard — 전략센터 "🧭 데이터 라우팅" 탭 상단 카드 (2026-09-25)
 * 가격 데이터 무결성 현황: 점프 감사 분류 집계, 최근 복구 run, 종가 공식 검증 결과(GET /api/research/price-integrity, 읽기 전용).
 */
import React from 'react';
import { API } from '../utils';

const SEVERITY = {
  unresolved_active_common: '#dc2626', mixed_basis_or_price_corruption: '#dc2626', invalid_ohlcv: '#dc2626',
  quarantined_basis: '#b45309', corporate_action_pending_confirmation: '#b45309', coverage_gap: '#b45309',
};
const box = { border: '1px solid var(--glass-border)', borderRadius: 8, padding: '0.5rem 0.65rem' };

export default function PriceIntegrityCard() {
  const [d, setD] = React.useState(null);
  const [error, setError] = React.useState('');
  const [open, setOpen] = React.useState(false);

  React.useEffect(() => {
    fetch(API('/api/research/price-integrity')).then((r) => r.json()).then(setD).catch((e) => setError(String(e)));
  }, []);

  if (error) return <div className="glass-panel" style={{ padding: '0.85rem 1rem', color: '#dc2626', fontSize: '0.75rem' }}>가격 무결성 조회 오류: {error}</div>;
  if (!d) return null;

  const lastVerify = (d.close_verify || [])[0];
  const unresolved = (d.classes || []).find((c) => c.classification === 'unresolved_active_common');

  return (
    <div className="glass-panel" style={{ padding: '0.85rem 1rem', border: '1px solid rgba(37,99,235,0.28)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', flexWrap: 'wrap', gap: '0.5rem', alignItems: 'center' }}>
        <div>
          <div style={{ fontSize: '0.9rem', fontWeight: 800, color: '#2563eb' }}>🛡️ 가격 데이터 무결성</div>
          <div style={{ fontSize: '0.7rem', color: 'var(--text-secondary)', marginTop: 3, lineHeight: 1.55 }}>
            백테스트 신뢰도의 기반입니다. 격리·검토 행은 수익률 계산에서 제외되거나 가드됩니다.
          </div>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
          <span style={{ ...box, fontSize: '0.7rem', color: unresolved && unresolved.count > 0 ? '#dc2626' : '#047857' }}>
            미해결 급변 {unresolved ? unresolved.count : 0}건
          </span>
          {lastVerify && (
            <span style={{ ...box, fontSize: '0.7rem', color: lastVerify.status === 'ok' ? '#047857' : '#dc2626' }}>
              종가 공식 검증 {lastVerify.trade_date} · {lastVerify.status === 'ok' ? '일치' : '불일치'} ({lastVerify.close_mismatch}/{lastVerify.compared})
            </span>
          )}
          <button onClick={() => setOpen(!open)}
            style={{ fontSize: '0.7rem', padding: '0.25rem 0.6rem', borderRadius: 6, cursor: 'pointer', border: '1px solid var(--glass-border)', background: 'transparent', color: 'rgba(15,23,42,0.88)' }}>
            {open ? '접기' : '자세히'}
          </button>
        </div>
      </div>

      {open && (
        <div style={{ marginTop: '0.75rem', display: 'grid', gap: '0.75rem' }}>
          <div>
            <div style={{ fontSize: '0.75rem', fontWeight: 700, marginBottom: 4 }}>점프 감사 분류</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(230px,1fr))', gap: '0.4rem' }}>
              {(d.classes || []).map((c) => (
                <div key={c.classification} style={{ ...box, display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: '0.7rem' }}>
                  <span style={{ color: 'rgba(15,23,42,0.88)' }}>{c.label}</span>
                  <b style={{ color: SEVERITY[c.classification] || '#1e293b' }}>{c.count.toLocaleString()}</b>
                </div>
              ))}
            </div>
          </div>
          <div>
            <div style={{ fontSize: '0.75rem', fontWeight: 700, marginBottom: 4 }}>최근 복구 작업</div>
            {(d.recent_fix_runs || []).map((r) => (
              <div key={r.run_id} style={{ fontSize: '0.68rem', color: 'rgba(15,23,42,0.88)', padding: '0.2rem 0', borderTop: '1px solid var(--glass-border)' }}>
                <b style={{ color: '#1e293b' }}>{r.fixed_at}</b> · {r.rows.toLocaleString()}행 · {r.reason}
              </div>
            ))}
          </div>
          <div>
            <div style={{ fontSize: '0.75rem', fontWeight: 700, marginBottom: 4 }}>종가 공식 검증 이력</div>
            {(d.close_verify || []).map((v, i) => (
              <div key={i} style={{ fontSize: '0.68rem', color: 'rgba(15,23,42,0.88)' }}>
                {v.checked_at} · 거래일 {v.trade_date} · 비교 {v.compared}건 · 불일치 {v.close_mismatch}건 ({v.mismatch_pct}%) · {v.status}
              </div>
            ))}
            {(d.close_verify || []).length === 0 && <div style={{ fontSize: '0.68rem', color: 'rgba(15,23,42,0.88)' }}>검증 이력이 없습니다.</div>}
          </div>
        </div>
      )}
    </div>
  );
}
