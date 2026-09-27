/* light-theme-codemod-2026-09-27 */
/* light-theme-codemod-pass2-2026-09-27 */
/* light-theme-codemod-pass3-2026-09-27 */
/* light-theme-codemod-pass4-2026-09-27 */
/* light-theme-codemod-pass5-2026-09-27 */
/* light-theme-codemod-pass6-2026-09-27 */
/* light-theme-codemod-pass7-2026-09-27 */
/**
 * FactorValidationPanel — 전략센터 "🔬 팩터 검증" 탭 (2026-09-25)
 * Alphalens 팩터 IC(학습/검증 분할)와 공시 이벤트 스터디를 읽기 전용으로 보여준다(GET /api/research/factor-validation).
 * 산출물은 research_outputs 파일이며 이 화면은 DB를 쓰지 않는다.
 */
import React from 'react';
import { API } from '../utils';

const FACTOR_LABEL = {
  mom_20d: '모멘텀 20일', mom_60d: '모멘텀 60일', mom_120d: '모멘텀 120일', mom_252d_ex_1m: '모멘텀 12-1개월',
  low_vol_60d: '저변동성 60일', earn_yield: '이익수익률(1/PER, TTM)', book_yield: '장부수익률(1/PBR)', small_size: '소형주',
  supply_20d_억: '수급 20일', dist_high_252: '52주 고점 근접도', turnover_surge_20_120: '거래대금 급증',
  heuristic_score: '휴리스틱 점수', model_score_6m: '모델 점수 6M', model_score_12m: '모델 점수 12M',
};
const EVENT_LABEL = {
  buyback_acquire_decision: '자사주 취득결정', buyback_trust: '자사주 신탁', buyback_cancel: '자사주 소각', buyback_disposal: '자사주 처분',
  contract_ratio_ge10: '수주(매출 10%↑)', dilution_BW: 'BW 발행', dilution_CB: 'CB 발행', dilution_EB: 'EB 발행',
  rd_license: '기술 라이선스', rd_patent: '특허', rd_rd_contract: '연구개발 계약', rd_tech_transfer: '기술이전',
};
const KIND_LABEL = { raw: '원시', sector_neutral: '섹터중립' };

const fmt = (v, d = 3) => (v == null || Number.isNaN(Number(v)) ? '-' : Number(v).toFixed(d));
const tColor = (t) => (t == null ? 'inherit' : Math.abs(t) >= 2 ? (t > 0 ? '#047857' : '#dc2626') : 'rgba(15,23,42,0.55)');

const th = { textAlign: 'right', padding: '0.35rem 0.5rem', fontWeight: 600, fontSize: '0.7rem', color: 'rgba(15,23,42,0.88)', whiteSpace: 'nowrap' };
const td = { textAlign: 'right', padding: '0.3rem 0.5rem', fontSize: '0.75rem', whiteSpace: 'nowrap' };

export default function FactorValidationPanel() {
  const [data, setData] = React.useState(null);
  const [error, setError] = React.useState('');
  const [horizon, setHorizon] = React.useState('60D');

  React.useEffect(() => {
    fetch(API('/api/research/factor-validation'))
      .then((r) => r.json())
      .then(setData)
      .catch((e) => setError(String(e)));
  }, []);

  if (error) return <div className="glass-panel" style={{ padding: '1rem', color: '#dc2626' }}>오류: {error}</div>;
  if (!data) return <div className="glass-panel" style={{ padding: '1rem', color: 'rgba(15,23,42,0.88)' }}>불러오는 중...</div>;

  const factors = (data.factors || []).filter((f) => f.horizon === horizon);
  const horizons = [...new Set((data.factors || []).map((f) => f.horizon))];
  const events = data.events || [];

  return (
    <div className="glass-panel" style={{ padding: '1rem' }}>
      <div style={{ fontWeight: 800, fontSize: '0.92rem', color: '#1e293b', marginBottom: '0.3rem' }}>🔬 팩터·이벤트 검증 (Alphalens)</div>
      <div style={{ fontSize: '0.75rem', color: 'rgba(15,23,42,0.88)', lineHeight: 1.6, marginBottom: '0.8rem' }}>
        {(data.notes || []).map((n, i) => <div key={i}>· {n}</div>)}
        <div>· |t| ≥ 2만 색으로 표시합니다. 학습과 검증의 부호가 다르면(부호 유지 ✗) 기간 특이 현상으로 봅니다.</div>
      </div>

      <div style={{ display: 'flex', gap: '0.4rem', marginBottom: '0.6rem' }}>
        {horizons.map((h) => (
          <button key={h} onClick={() => setHorizon(h)}
            style={{
              padding: '0.25rem 0.65rem', borderRadius: '7px', fontSize: '0.72rem', cursor: 'pointer',
              border: horizon === h ? '1px solid #b45309' : '1px solid var(--glass-border)',
              background: horizon === h ? 'rgba(217,119,6,0.15)' : 'transparent',
              color: horizon === h ? '#b45309' : 'rgba(15,23,42,0.88)',
            }}>{h}</button>
        ))}
      </div>

      <div style={{ overflowX: 'auto', marginBottom: '1.2rem' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={{ ...th, textAlign: 'left' }}>팩터</th><th style={th}>구분</th>
              <th style={th}>학습 IC</th><th style={th}>학습 t</th><th style={th}>검증 IC</th><th style={th}>검증 t</th>
              <th style={th}>부호 유지</th>
            </tr>
          </thead>
          <tbody>
            {factors.map((f, i) => (
              <tr key={`${f.factor}-${f.ic_kind}-${i}`} style={{ borderTop: '1px solid var(--glass-border)' }}>
                <td style={{ ...td, textAlign: 'left' }}>{FACTOR_LABEL[f.factor] || f.factor}</td>
                <td style={td}>{KIND_LABEL[f.ic_kind] || f.ic_kind}</td>
                <td style={td}>{fmt(f.train_ic)}</td>
                <td style={{ ...td, color: tColor(f.train_t) }}>{fmt(f.train_t, 2)}</td>
                <td style={td}>{fmt(f.valid_ic)}</td>
                <td style={{ ...td, color: tColor(f.valid_t) }}>{fmt(f.valid_t, 2)}</td>
                <td style={{ ...td, color: f.sign_kept ? '#047857' : '#dc2626' }}>{f.sign_kept ? '✓' : '✗'}</td>
              </tr>
            ))}
            {factors.length === 0 && <tr><td colSpan={7} style={{ ...td, textAlign: 'center' }}>데이터 없음</td></tr>}
          </tbody>
        </table>
      </div>

      <div style={{ fontWeight: 700, fontSize: '0.85rem', color: '#1e293b', marginBottom: '0.4rem' }}>📢 공시 이벤트 스터디 (시장 대비 초과수익 %p)</div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={{ ...th, textAlign: 'left' }}>이벤트</th><th style={th}>기간</th><th style={th}>건수</th>
              <th style={th}>평균</th><th style={th}>중앙값</th><th style={th}>t</th>
              <th style={th}>검증 건수</th><th style={th}>검증 초과</th><th style={th}>검증 t</th>
            </tr>
          </thead>
          <tbody>
            {events.map((e, i) => (
              <tr key={`${e.kind}-${e.horizon}-${i}`} style={{ borderTop: '1px solid var(--glass-border)' }}>
                <td style={{ ...td, textAlign: 'left' }}>{EVENT_LABEL[e.kind] || e.kind}</td>
                <td style={td}>{e.horizon}</td><td style={td}>{e.n}</td>
                <td style={td}>{fmt(e.mean_excess_pct, 2)}</td><td style={td}>{fmt(e.median_excess_pct, 2)}</td>
                <td style={{ ...td, color: tColor(e.t) }}>{fmt(e.t, 2)}</td>
                <td style={td}>{e.valid_n ?? '-'}</td><td style={td}>{fmt(e.valid_excess_pct, 2)}</td>
                <td style={{ ...td, color: tColor(e.valid_t) }}>{fmt(e.valid_t, 2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div style={{ fontSize: '0.7rem', color: 'rgba(15,23,42,0.88)', marginTop: '0.6rem' }}>
        수주·특허는 평균이 소수 종목에 좌우됩니다(중앙값≈0). 공시 익일 진입 기준이며 거래비용은 포함하지 않았습니다.
      </div>
    </div>
  );
}


/** QuantStats 전략 성과 표 — 곡선 출처(engine 정확 / 거래로그 MTM 근사) 배지 포함. */
export function QuantStatsPanel() {
  const [d, setD] = React.useState(null);
  React.useEffect(() => {
    fetch(API('/api/research/quantstats')).then((r) => r.json()).then(setD).catch(() => setD({ items: [] }));
  }, []);
  if (!d || !(d.items || []).length) return null;
  const rows = [...d.items].sort((a, b) => (b.sortino ?? -9) - (a.sortino ?? -9));
  return (
    <div className="glass-panel" style={{ padding: '1rem', marginTop: '0.75rem' }}>
      <div style={{ fontWeight: 800, fontSize: '0.92rem', color: '#1e293b', marginBottom: '0.3rem' }}>📈 전략 성과 지표 (QuantStats)</div>
      <div style={{ fontSize: '0.75rem', color: 'rgba(15,23,42,0.88)', lineHeight: 1.6, marginBottom: '0.6rem' }}>
        {(d.notes || []).map((n, i) => <div key={i}>· {n}</div>)}
        <div>· KOSPI 대비 알파·베타는 전략 자본곡선 기준입니다. 베타가 낮고 초과수익이 음수면 시장을 이기지 못한 것입니다.</div>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={{ ...th, textAlign: 'left' }}>전략</th><th style={th}>CAGR%</th><th style={th}>Sharpe</th><th style={th}>Sortino</th>
              <th style={th}>MDD%</th><th style={th}>알파%</th><th style={th}>베타</th><th style={th}>KOSPI 초과%</th><th style={th}>곡선</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.strategy} style={{ borderTop: '1px solid var(--glass-border)' }}>
                <td style={{ ...td, textAlign: 'left' }}>{r.strategy}</td>
                <td style={td}>{fmt(r.cagr_pct, 1)}</td><td style={td}>{fmt(r.sharpe, 2)}</td><td style={td}>{fmt(r.sortino, 2)}</td>
                <td style={td}>{fmt(r.max_drawdown_pct, 1)}</td><td style={td}>{fmt(r.alpha_annual_pct, 1)}</td>
                <td style={td}>{fmt(r.beta, 2)}</td>
                <td style={{ ...td, color: (r.excess_cagr_pct ?? 0) >= 0 ? '#047857' : '#dc2626' }}>{fmt(r.excess_cagr_pct, 1)}</td>
                <td style={{ ...td, color: r.engine_periods > 0 && !r.reconstructed_periods ? '#047857' : '#b45309' }}>
                  {r.engine_periods > 0 && !r.reconstructed_periods ? '엔진' : '근사'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
