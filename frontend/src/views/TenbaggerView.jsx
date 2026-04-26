import React from 'react';
import { Rocket } from 'lucide-react';

const API = (path) => path;

const TenbaggerView = () => {
  const [results,   setResults]   = React.useState([]);
  const [loading,   setLoading]   = React.useState(false);
  const [scope,     setScope]     = React.useState('watchlist');
  const [minScore,  setMinScore]  = React.useState(50);
  const [detail,    setDetail]    = React.useState(null);
  const [analyzing, setAnalyzing] = React.useState(null);

  const VERDICT_COLOR = {
    strong_buy: '#22c55e',
    buy:        '#86efac',
    watch:      '#fbbf24',
    avoid:      '#f87171',
  };
  const VERDICT_LABEL = {
    strong_buy: '강력매수',
    buy:        '매수',
    watch:      '관찰',
    avoid:      '회피',
  };

  const fmtMktcap = (v) => {
    if (!v) return '-';
    if (v >= 1e12) return `${(v/1e12).toFixed(1)}조`;
    return `${Math.round(v/1e8).toLocaleString()}억`;
  };

  const runScan = async () => {
    setLoading(true);
    setDetail(null);
    try {
      const r = await fetch(API('/api/tenbagger/scan'), {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ scope, min_score: minScore, limit: 50 }),
      });
      if (r.ok) {
        const d = await r.json();
        setResults(d.results || []);
      }
    } catch(e) {}
    setLoading(false);
  };

  const runQuick = async () => {
    setLoading(true);
    setDetail(null);
    try {
      const r = await fetch(API('/api/tenbagger/watchlist-quick'));
      if (r.ok) {
        const d = await r.json();
        setResults((d.results || []).filter(x => x.tenbagger_score >= minScore));
      }
    } catch(e) {}
    setLoading(false);
  };

  const analyzeOne = async (code) => {
    setAnalyzing(code);
    try {
      const r = await fetch(API(`/api/tenbagger/analyze/${code}`), {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({ force_refresh: true }),
      });
      if (r.ok) {
        const d = await r.json();
        setDetail(d);
      }
    } catch(e) {}
    setAnalyzing(null);
  };

  // 최근 캐시 로드
  React.useEffect(() => {
    fetch(API('/api/tenbagger/candidates'))
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.results?.length) setResults(d.results); })
      .catch(()=>{});
  }, []);

  return (
    <div className="fade-in" style={{ padding: '1.5rem', display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>

      {/* 헤더 */}
      <div className="glass-panel" style={{ padding: '1.5rem' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '1rem' }}>
          <Rocket size={22} color="#ec4899" />
          <h2 style={{ fontSize: '1.2rem', fontWeight: 700 }}>텐배거 헌터 — Logic #5</h2>
          <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', background: 'rgba(236,72,153,0.1)', border: '1px solid rgba(236,72,153,0.3)', borderRadius: '12px', padding: '0.1rem 0.6rem' }}>
            3~5년 10배 잠재 종목
          </span>
        </div>
        <p style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
          메가트렌드 부합 · 구조적 EPS 턴어라운드 · 독점 포지션 · 거래량 폭발 조건을 충족하는 종목을 탐색합니다.<br/>
          <span style={{ color: '#ec4899' }}>ANTHROPIC_API_KEY</span> 설정 시 Claude AI가 산업 특성·가짜 펌핑 여부를 분석합니다.
        </p>

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '1rem', marginTop: '1.2rem', alignItems: 'center' }}>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            {[
              { v: 'watchlist', label: '관심종목' },
              { v: 'all',      label: '관심+매수후보' },
              { v: 'universe', label: '전체종목(랜덤500)' },
            ].map(s => (
              <button key={s.v} onClick={() => setScope(s.v)}
                style={{
                  padding: '0.4rem 0.9rem', borderRadius: '20px', cursor: 'pointer', fontSize: '0.82rem',
                  background: scope === s.v ? '#ec4899' : 'rgba(255,255,255,0.05)',
                  border: `1px solid ${scope === s.v ? '#ec4899' : 'var(--glass-border)'}`,
                  color: scope === s.v ? '#fff' : 'var(--text-secondary)',
                }}>{s.label}</button>
            ))}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', fontSize: '0.82rem', color: 'var(--text-secondary)' }}>
            최소점수:
            <select value={minScore} onChange={e => setMinScore(Number(e.target.value))}
              style={{ background: 'var(--bg-dark)', border: '1px solid var(--glass-border)', color: 'var(--text-primary)', borderRadius: '6px', padding: '0.2rem 0.4rem', fontSize: '0.82rem' }}>
              {[30, 40, 50, 60, 70].map(v => <option key={v} value={v}>{v}점+</option>)}
            </select>
          </div>
          <div style={{ display: 'flex', gap: '0.5rem' }}>
            <button onClick={runQuick} disabled={loading}
              style={{ padding: '0.5rem 1.2rem', borderRadius: '8px', cursor: 'pointer', fontSize: '0.85rem', background: 'rgba(236,72,153,0.15)', border: '1px solid #ec4899', color: '#ec4899', fontWeight: 600 }}>
              {loading ? '스캔 중...' : '⚡ 빠른 스캔 (정량)'}
            </button>
            <button onClick={runScan} disabled={loading}
              style={{ padding: '0.5rem 1.2rem', borderRadius: '8px', cursor: 'pointer', fontSize: '0.85rem', background: loading ? 'rgba(255,255,255,0.05)' : 'linear-gradient(135deg,#ec4899,#a855f7)', border: 'none', color: '#fff', fontWeight: 600 }}>
              {loading ? '분석 중...' : '🤖 AI 전체 스캔'}
            </button>
          </div>
        </div>
      </div>

      {/* 스캔 결과 */}
      {results.length > 0 && (
        <div className="glass-panel" style={{ padding: '1.5rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
            <h3 style={{ fontSize: '1rem', fontWeight: 600 }}>스캔 결과 ({results.length}종목)</h3>
            <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>점수 높은 순 / 클릭하면 AI 상세분석</span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: '0.75rem' }}>
            {results.map(r => (
              <div key={r.stock_code}
                onClick={() => analyzeOne(r.stock_code)}
                style={{
                  padding: '1rem', borderRadius: '10px', cursor: 'pointer',
                  background: 'rgba(255,255,255,0.03)', border: `1px solid ${VERDICT_COLOR[r.verdict] || 'var(--glass-border)'}22`,
                  transition: 'background 0.15s',
                }}
                onMouseEnter={e => e.currentTarget.style.background = 'rgba(255,255,255,0.07)'}
                onMouseLeave={e => e.currentTarget.style.background = 'rgba(255,255,255,0.03)'}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '0.5rem' }}>
                  <div>
                    <div style={{ fontWeight: 700, fontSize: '0.95rem' }}>{r.stock_name}</div>
                    <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>{r.stock_code} · {r.sector}</div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{ fontSize: '1.4rem', fontWeight: 800, color: VERDICT_COLOR[r.verdict] || '#fff' }}>
                      {r.tenbagger_score}
                    </div>
                    <div style={{ fontSize: '0.7rem', color: VERDICT_COLOR[r.verdict], fontWeight: 600 }}>
                      {VERDICT_LABEL[r.verdict] || r.verdict}
                    </div>
                  </div>
                </div>
                <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap', marginBottom: '0.5rem' }}>
                  {r.is_megatrend && (
                    <span style={{ fontSize: '0.7rem', background: 'rgba(34,197,94,0.15)', border: '1px solid #22c55e44', color: '#22c55e', borderRadius: '10px', padding: '0.1rem 0.5rem' }}>메가트렌드</span>
                  )}
                  {r.is_fake_risk && (
                    <span style={{ fontSize: '0.7rem', background: 'rgba(248,113,113,0.15)', border: '1px solid #f8717144', color: '#f87171', borderRadius: '10px', padding: '0.1rem 0.5rem' }}>위험신호</span>
                  )}
                  {r.source === 'claude_api' && (
                    <span style={{ fontSize: '0.7rem', background: 'rgba(168,85,247,0.15)', border: '1px solid #a855f744', color: '#a855f7', borderRadius: '10px', padding: '0.1rem 0.5rem' }}>AI분석</span>
                  )}
                </div>
                <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                  {r.curr_price ? `현재가: ${r.curr_price.toLocaleString()}원` : ''}
                  {r.mktcap ? ` · 시총: ${fmtMktcap(r.mktcap)}` : ''}
                </div>
                {analyzing === r.stock_code && (
                  <div style={{ marginTop: '0.5rem', fontSize: '0.78rem', color: '#a855f7' }}>Claude AI 분석 중...</div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 상세 분석 패널 */}
      {detail && (
        <div className="glass-panel" style={{ padding: '1.5rem', borderColor: `${VERDICT_COLOR[detail.verdict] || 'var(--glass-border)'}` }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.2rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
              <h3 style={{ fontSize: '1.1rem', fontWeight: 700 }}>{detail.stock_name}</h3>
              <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>{detail.stock_code} · {detail.sector}</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '1rem' }}>
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: '2rem', fontWeight: 800, color: VERDICT_COLOR[detail.verdict] }}>{detail.tenbagger_score}</div>
                <div style={{ fontSize: '0.75rem', color: VERDICT_COLOR[detail.verdict], fontWeight: 600 }}>{VERDICT_LABEL[detail.verdict] || detail.verdict}</div>
              </div>
              <button onClick={() => setDetail(null)} style={{ background: 'none', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: '1.2rem' }}>✕</button>
            </div>
          </div>

          {detail.ai_summary && (
            <div style={{ padding: '0.75rem 1rem', background: 'rgba(168,85,247,0.08)', border: '1px solid rgba(168,85,247,0.25)', borderRadius: '8px', marginBottom: '1rem', fontSize: '0.88rem', lineHeight: 1.6 }}>
              {detail.ai_summary}
            </div>
          )}

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
            {detail.reasons?.length > 0 && (
              <div>
                <div style={{ fontSize: '0.8rem', fontWeight: 600, color: '#22c55e', marginBottom: '0.5rem' }}>✅ 긍정 요인</div>
                {detail.reasons.map((r, i) => (
                  <div key={i} style={{ fontSize: '0.82rem', color: 'var(--text-primary)', padding: '0.2rem 0', borderBottom: '1px solid rgba(255,255,255,0.05)' }}>• {r}</div>
                ))}
              </div>
            )}
            {detail.warnings?.length > 0 && (
              <div>
                <div style={{ fontSize: '0.8rem', fontWeight: 600, color: '#f87171', marginBottom: '0.5rem' }}>⚠️ 위험 요인</div>
                {detail.warnings.map((w, i) => (
                  <div key={i} style={{ fontSize: '0.82rem', color: 'var(--text-secondary)', padding: '0.2rem 0', borderBottom: '1px solid rgba(255,255,255,0.05)' }}>• {w}</div>
                ))}
              </div>
            )}
          </div>

          <div style={{ marginTop: '1rem', display: 'flex', gap: '1rem', fontSize: '0.82rem', color: 'var(--text-secondary)' }}>
            <span>{detail.curr_price ? `현재가: ${detail.curr_price.toLocaleString()}원` : ''}</span>
            <span>{detail.mktcap ? `시총: ${fmtMktcap(detail.mktcap)}` : ''}</span>
            <span style={{ color: detail.source === 'claude_api' ? '#a855f7' : 'var(--text-secondary)' }}>
              출처: {detail.source === 'claude_api' ? '🤖 Claude AI' : '📊 정량분석'}
            </span>
          </div>
        </div>
      )}

      {/* 전략 설명 */}
      <div className="glass-panel" style={{ padding: '1.5rem' }}>
        <h3 style={{ fontSize: '0.95rem', fontWeight: 700, marginBottom: '1rem', color: '#ec4899' }}>Logic #5 텐배거 헌터 — 전략 설명</h3>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: '1rem', fontSize: '0.82rem', color: 'var(--text-secondary)' }}>
          {[
            { icon: '📈', title: '추세 확립', desc: 'MA200 상방 + 52주 고가 88% 이상 (신고가 권역 진입)' },
            { icon: '💥', title: '거래량 폭발', desc: '60일 평균 거래량의 3배 이상 (세력 개입 확인)' },
            { icon: '💰', title: '실적 필터', desc: '영업이익 흑자 OR 매출 YoY +30% 이상 (내러티브만 제외)' },
            { icon: '🏢', title: '시총 제한', desc: '1.5조원 이하 소형·중형주 집중 (텐배거 가능 규모)' },
            { icon: '🛡️', title: '시장 필터', desc: 'KOSPI regime≥2(중립 이상) / KOSDAQ MA60 위' },
            { icon: '🚀', title: '수익 극대화', desc: 'Scale-out +30%, 추적손절 -20%, MA20 추적 (+15% 이후)' },
          ].map((c, i) => (
            <div key={i} style={{ padding: '0.75rem', background: 'rgba(255,255,255,0.02)', borderRadius: '8px', border: '1px solid var(--glass-border)' }}>
              <div style={{ fontSize: '1rem', marginBottom: '0.3rem' }}>{c.icon} <strong style={{ color: 'var(--text-primary)' }}>{c.title}</strong></div>
              <div>{c.desc}</div>
            </div>
          ))}
        </div>
      </div>

    </div>
  );
};


export default TenbaggerView;
