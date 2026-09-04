// RiskGateMonitorView.jsx
// 실전 자동매매 리스크게이트 + 주문 생애주기 모니터 (2026-07-23 신규)
//   백엔드: routes/kis_trading.py (prefix /api/kis-trading)
//   - 사전점검: GET risk-gates/check (실제 주문 없이 9개 게이트 미리 확인)
//   - 판정 이력: GET risk-gates/recent (BUY_ALLOWED, BLOCKED류, WAIT_CONFIRM, SIZE_REDUCED 전량)
//   - 주문 생애주기: GET orders/lifecycle, GET orders/{id}
//   - 현금원장: GET cash-ledger
//   - 개요: GET status, paper/positions, paper/pnl
import React, { useEffect, useState, useCallback } from 'react';

const API = (path) => path;

const fmtKrw = (v) => v == null ? '-' : Math.round(v).toLocaleString('ko-KR') + '원';
const fmtPct = (v) => v == null ? '-' : (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%';
const pnlColor = (v) => v > 0 ? '#ef4444' : v < 0 ? '#3b82f6' : 'var(--text-secondary)';

const DECISION_META = {
  BUY_ALLOWED:       { label: '매수 허용',       color: '#22c55e' },
  SELL_OK:           { label: '매도 허용',       color: '#22c55e' },
  WAIT_CONFIRM:      { label: '확인 대기(409)',  color: '#f59e0b' },
  SIZE_REDUCED:      { label: '수량 축소',       color: '#38bdf8' },
  BLOCKED_RISK:      { label: '리스크 차단',     color: '#ef4444' },
  BLOCKED_STALE_DATA:{ label: '데이터 정체 차단', color: '#ef4444' },
};

const GATE_LABELS = {
  data_freshness:      '데이터 신선도',
  gap_risk:            '갭 리스크',
  liquidity:           '유동성',
  dilution_risk:       '희석 위험',
  flow_reversal:       '수급 역풍',
  market_regime:       '장세 위험(패닉)',
  credit_surge:        '신용잔고 급증',
  volatility_sizing:   '변동성 사이징',
  sector_concentration:'섹터 집중한도',
};

function DecisionBadge({ decision }) {
  const meta = DECISION_META[decision] || { label: decision || '-', color: 'var(--text-secondary)' };
  return (
    <span style={{
      padding: '0.2rem 0.6rem', borderRadius: '12px', fontSize: '0.72rem', fontWeight: 700,
      color: meta.color, background: `${meta.color}18`, border: `1px solid ${meta.color}45`,
      whiteSpace: 'nowrap',
    }}>{meta.label}</span>
  );
}

function GateTable({ gates }) {
  if (!gates) return null;
  const entries = Object.entries(gates);
  return (
    <table className="premium-table" style={{ marginTop: '0.6rem' }}>
      <thead><tr>
        <th>게이트</th><th>판정</th><th>사유</th>
      </tr></thead>
      <tbody>
        {entries.map(([key, g]) => (
          <tr key={key}>
            <td style={{ whiteSpace: 'nowrap' }}>{GATE_LABELS[key] || key}</td>
            <td>
              <span style={{ color: g.ok ? '#22c55e' : '#ef4444', fontWeight: 700 }}>
                {g.ok ? '통과' : '차단'}
              </span>
              {g.data_available === false && (
                <span style={{ marginLeft: '0.4rem', fontSize: '0.68rem', color: 'rgba(245,158,11,0.8)' }}>(데이터없음)</span>
              )}
            </td>
            <td style={{ color: 'var(--text-secondary)', fontSize: '0.78rem' }}>{g.reason}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ── 개요 탭 ──────────────────────────────────────────────
function OverviewTab() {
  const [status, setStatus] = useState(null);
  const [positions, setPositions] = useState(null);
  const [pnl, setPnl] = useState(null);
  const [ledger, setLedger] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [s, p, n, l] = await Promise.all([
        fetch(API('/api/kis-trading/status')).then(r => r.ok ? r.json() : null),
        fetch(API('/api/kis-trading/paper/positions')).then(r => r.ok ? r.json() : null),
        fetch(API('/api/kis-trading/paper/pnl')).then(r => r.ok ? r.json() : null),
        fetch(API('/api/kis-trading/cash-ledger?limit=1')).then(r => r.ok ? r.json() : null),
      ]);
      setStatus(s); setPositions(p); setPnl(n); setLedger(l);
    } catch (e) { console.error(e); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const cards = [
    { label: '거래 모드', val: status?.mode || '-', color: status?.mode === 'LIVE' ? '#ef4444' : '#22c55e' },
    { label: '현금 잔고', val: fmtKrw(ledger?.current_balance), color: 'inherit' },
    { label: '보유 포지션', val: `${positions?.summary?.position_count ?? 0}종목`, color: 'inherit' },
    { label: '평가 손익', val: fmtKrw(positions?.summary?.total_unrealized_pnl), color: pnlColor(positions?.summary?.total_unrealized_pnl || 0) },
    { label: '금일 실현손익', val: fmtKrw(pnl?.daily_realized_pnl), color: pnlColor(pnl?.daily_realized_pnl || 0) },
    { label: '누적 실현손익', val: fmtKrw(pnl?.total_realized_pnl), color: pnlColor(pnl?.total_realized_pnl || 0) },
  ];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ fontSize: '0.72rem', color: 'var(--text-secondary)' }}>
          {status?.live_order_enabled === false && 'LIVE 주문은 명시적 승인 전까지 403 차단 상태입니다.'}
        </div>
        <button onClick={load} style={{
          padding: '0.3rem 0.7rem', borderRadius: '6px', fontSize: '0.75rem', cursor: 'pointer',
          background: 'rgba(255,255,255,0.05)', border: '1px solid var(--glass-border)', color: 'var(--text-secondary)',
        }}>새로고침</button>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))', gap: '0.75rem' }}>
        {cards.map(({ label, val, color }) => (
          <div key={label} className="glass-panel" style={{ padding: '0.9rem 1rem' }}>
            <p style={{ fontSize: '0.7rem', color: 'var(--text-secondary)', marginBottom: '0.3rem' }}>{label}</p>
            <p style={{ fontSize: '0.95rem', fontWeight: 700, color }}>{val}</p>
          </div>
        ))}
      </div>
      {loading && <div style={{ color: 'var(--text-secondary)', fontSize: '0.8rem' }}>로딩 중...</div>}
      {positions?.positions?.length > 0 && (
        <div className="glass-panel" style={{ overflow: 'clip' }}>
          <div style={{ padding: '0.6rem 1rem', borderBottom: '1px solid var(--glass-border)', fontWeight: 700, fontSize: '0.85rem' }}>
            페이퍼 보유 포지션 (kis_paper_positions)
          </div>
          <table className="premium-table">
            <thead><tr><th>종목코드</th><th>수량</th><th>평균단가</th><th>현재가</th><th>평가손익</th><th>수익률</th></tr></thead>
            <tbody>
              {positions.positions.map(p => (
                <tr key={p.stock_code}>
                  <td>{p.stock_code}</td>
                  <td>{p.qty}</td>
                  <td>{fmtKrw(p.avg_price)}</td>
                  <td>{fmtKrw(p.current_price)}</td>
                  <td style={{ color: pnlColor(p.unrealized_pnl) }}>{fmtKrw(p.unrealized_pnl)}</td>
                  <td style={{ color: pnlColor(p.unrealized_pct) }}>{fmtPct(p.unrealized_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── 사전점검 탭 ──────────────────────────────────────────
function CheckTab() {
  const [stockCode, setStockCode] = useState('');
  const [side, setSide] = useState('buy');
  const [qty, setQty] = useState('10');
  const [strategyKey, setStrategyKey] = useState('');
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const runCheck = async () => {
    if (!/^\d{6}$/.test(stockCode)) { setError('종목코드 6자리를 입력하세요'); return; }
    setLoading(true); setError(null); setResult(null);
    try {
      const params = new URLSearchParams({ stock_code: stockCode, side, qty: String(qty || 1) });
      if (strategyKey) params.set('strategy_key', strategyKey);
      const r = await fetch(API(`/api/kis-trading/risk-gates/check?${params.toString()}`));
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        setError(d.detail || `요청 실패 (${r.status})`);
      } else {
        setResult(await r.json());
      }
    } catch (e) { setError(String(e)); }
    finally { setLoading(false); }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <div className="glass-panel" style={{ padding: '1rem', display: 'flex', gap: '0.6rem', flexWrap: 'wrap', alignItems: 'flex-end' }}>
        <div>
          <label style={{ fontSize: '0.7rem', color: 'var(--text-secondary)', display: 'block', marginBottom: '0.25rem' }}>종목코드</label>
          <input value={stockCode} onChange={e => setStockCode(e.target.value.trim())} placeholder="005930" maxLength={6}
            style={{ padding: '0.4rem 0.6rem', borderRadius: '6px', background: 'rgba(255,255,255,0.05)', border: '1px solid var(--glass-border)', color: 'inherit', width: '110px' }} />
        </div>
        <div>
          <label style={{ fontSize: '0.7rem', color: 'var(--text-secondary)', display: 'block', marginBottom: '0.25rem' }}>매매구분</label>
          <select value={side} onChange={e => setSide(e.target.value)}
            style={{ padding: '0.4rem 0.6rem', borderRadius: '6px', background: 'rgba(255,255,255,0.05)', border: '1px solid var(--glass-border)', color: 'inherit' }}>
            <option value="buy">매수</option>
            <option value="sell">매도</option>
          </select>
        </div>
        <div>
          <label style={{ fontSize: '0.7rem', color: 'var(--text-secondary)', display: 'block', marginBottom: '0.25rem' }}>수량</label>
          <input type="number" min="1" value={qty} onChange={e => setQty(e.target.value)}
            style={{ padding: '0.4rem 0.6rem', borderRadius: '6px', background: 'rgba(255,255,255,0.05)', border: '1px solid var(--glass-border)', color: 'inherit', width: '90px' }} />
        </div>
        <div>
          <label style={{ fontSize: '0.7rem', color: 'var(--text-secondary)', display: 'block', marginBottom: '0.25rem' }}>전략키(선택)</label>
          <input value={strategyKey} onChange={e => setStrategyKey(e.target.value.trim())} placeholder="combo_605"
            style={{ padding: '0.4rem 0.6rem', borderRadius: '6px', background: 'rgba(255,255,255,0.05)', border: '1px solid var(--glass-border)', color: 'inherit', width: '130px' }} />
        </div>
        <button onClick={runCheck} disabled={loading} style={{
          padding: '0.45rem 1rem', borderRadius: '6px', cursor: loading ? 'wait' : 'pointer',
          background: 'rgba(167,139,250,0.15)', border: '1px solid rgba(167,139,250,0.35)', color: 'var(--accent-purple)', fontWeight: 700,
        }}>{loading ? '점검 중...' : '게이트 점검'}</button>
      </div>

      {error && (
        <div className="glass-panel" style={{ padding: '0.8rem 1rem', color: '#ef4444', fontSize: '0.8rem' }}>⚠️ {error}</div>
      )}

      {result && (
        <div className="glass-panel" style={{ padding: '1rem' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', marginBottom: '0.4rem' }}>
            <DecisionBadge decision={result.decision} />
            <span style={{ fontSize: '0.72rem', color: 'var(--text-secondary)' }}>gate_decision_id: {result.gate_decision_id}</span>
          </div>
          {result.reasons?.length > 0 && (
            <div style={{ fontSize: '0.78rem', color: '#f59e0b', marginBottom: '0.4rem' }}>
              {result.reasons.map((r, i) => <div key={i}>· {r}</div>)}
            </div>
          )}
          <GateTable gates={result.gates} />
        </div>
      )}
    </div>
  );
}

// ── 판정 이력 탭 ─────────────────────────────────────────
function HistoryTab() {
  const [rows, setRows] = useState([]);
  const [decision, setDecision] = useState('');
  const [expanded, setExpanded] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ limit: '150' });
      if (decision) params.set('decision', decision);
      const r = await fetch(API(`/api/kis-trading/risk-gates/recent?${params.toString()}`));
      setRows(r.ok ? await r.json() : []);
    } catch (e) { console.error(e); }
    finally { setLoading(false); }
  }, [decision]);

  useEffect(() => { load(); }, [load]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>
      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
        <select value={decision} onChange={e => setDecision(e.target.value)} style={{
          padding: '0.35rem 0.6rem', borderRadius: '6px', background: 'rgba(255,255,255,0.05)', border: '1px solid var(--glass-border)', color: 'inherit',
        }}>
          <option value="">전체 판정</option>
          {Object.keys(DECISION_META).map(k => <option key={k} value={k}>{DECISION_META[k].label}</option>)}
        </select>
        <button onClick={load} style={{
          padding: '0.35rem 0.7rem', borderRadius: '6px', fontSize: '0.75rem', cursor: 'pointer',
          background: 'rgba(255,255,255,0.05)', border: '1px solid var(--glass-border)', color: 'var(--text-secondary)',
        }}>새로고침</button>
        <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>{rows.length}건</span>
      </div>
      {loading ? (
        <div style={{ color: 'var(--text-secondary)' }}>로딩 중...</div>
      ) : rows.length === 0 ? (
        <div className="glass-panel" style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}>판정 이력이 없습니다.</div>
      ) : (
        <div className="glass-panel" style={{ overflow: 'clip' }}>
          <table className="premium-table">
            <thead><tr><th>시각</th><th>종목</th><th>구분</th><th>전략</th><th>판정</th><th>상세</th></tr></thead>
            <tbody>
              {rows.map(r => (
                <React.Fragment key={r.id}>
                  <tr onClick={() => setExpanded(expanded === r.id ? null : r.id)} style={{ cursor: 'pointer' }}>
                    <td style={{ whiteSpace: 'nowrap', fontSize: '0.75rem' }}>{r.ts}</td>
                    <td>{r.stock_code}</td>
                    <td>{r.side === 'buy' ? '매수' : '매도'}</td>
                    <td style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>{r.strategy_key || '-'}</td>
                    <td><DecisionBadge decision={r.decision} /></td>
                    <td style={{ fontSize: '0.7rem', color: 'var(--accent-purple)' }}>{expanded === r.id ? '접기 ▲' : '펼치기 ▼'}</td>
                  </tr>
                  {expanded === r.id && (
                    <tr>
                      <td colSpan={6} style={{ background: 'rgba(255,255,255,0.02)' }}>
                        {Array.isArray(r.reasons) && r.reasons.length > 0 && (
                          <div style={{ fontSize: '0.76rem', color: '#f59e0b', margin: '0.4rem 0' }}>
                            {r.reasons.map((x, i) => <div key={i}>· {x}</div>)}
                          </div>
                        )}
                        <GateTable gates={r.gate_snapshot?.gates} />
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── 주문 생애주기 탭 ─────────────────────────────────────
function OrdersTab() {
  const [rows, setRows] = useState([]);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(API('/api/kis-trading/orders/lifecycle?limit=150'));
      setRows(r.ok ? await r.json() : []);
    } catch (e) { console.error(e); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const openDetail = async (orderId) => {
    setDetailLoading(true); setDetail(null);
    try {
      const r = await fetch(API(`/api/kis-trading/orders/${orderId}`));
      setDetail(r.ok ? await r.json() : null);
    } catch (e) { console.error(e); }
    finally { setDetailLoading(false); }
  };

  return (
    <div style={{ display: 'grid', gridTemplateColumns: detail ? '1.4fr 1fr' : '1fr', gap: '1rem' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>{rows.length}건</span>
          <button onClick={load} style={{
            padding: '0.3rem 0.7rem', borderRadius: '6px', fontSize: '0.75rem', cursor: 'pointer',
            background: 'rgba(255,255,255,0.05)', border: '1px solid var(--glass-border)', color: 'var(--text-secondary)',
          }}>새로고침</button>
        </div>
        {loading ? (
          <div style={{ color: 'var(--text-secondary)' }}>로딩 중...</div>
        ) : rows.length === 0 ? (
          <div className="glass-panel" style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}>주문 이력이 없습니다.</div>
        ) : (
          <div className="glass-panel" style={{ overflow: 'clip' }}>
            <table className="premium-table">
              <thead><tr><th>ID</th><th>종목</th><th>구분</th><th>수량</th><th>체결가</th><th>상태</th><th>전략</th></tr></thead>
              <tbody>
                {rows.map(o => (
                  <tr key={o.order_id} onClick={() => openDetail(o.order_id)}
                    style={{ cursor: 'pointer', background: detail?.order?.order_id === o.order_id ? 'rgba(167,139,250,0.08)' : undefined }}>
                    <td>{o.order_id}</td>
                    <td>{o.stock_code}</td>
                    <td>{o.side === 'buy' ? '매수' : '매도'}</td>
                    <td>{o.filled_qty}/{o.qty}</td>
                    <td>{fmtKrw(o.avg_fill_price)}</td>
                    <td style={{ color: o.status === 'FILLED' ? '#22c55e' : 'var(--text-secondary)' }}>{o.status}</td>
                    <td style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>{o.strategy_key || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      {detail && (
        <div className="glass-panel" style={{ padding: '1rem', alignSelf: 'flex-start' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <div style={{ fontWeight: 700, fontSize: '0.85rem' }}>주문 #{detail.order?.order_id} 상세</div>
            <button onClick={() => setDetail(null)} style={{ background: 'none', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer' }}>✕</button>
          </div>
          {detailLoading ? (
            <div style={{ color: 'var(--text-secondary)', marginTop: '0.5rem' }}>로딩 중...</div>
          ) : (
            <>
              <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', marginTop: '0.5rem', lineHeight: 1.7 }}>
                <div>사유: {detail.order?.decision_reason || '-'}</div>
                <div>생성: {detail.order?.created_at} · 갱신: {detail.order?.updated_at}</div>
              </div>
              <div style={{ fontWeight: 700, fontSize: '0.78rem', marginTop: '0.7rem', marginBottom: '0.3rem' }}>이벤트</div>
              <table className="premium-table">
                <thead><tr><th>시각</th><th>유형</th><th>수량</th><th>가격</th></tr></thead>
                <tbody>
                  {(detail.events || []).map(e => (
                    <tr key={e.id}><td style={{ fontSize: '0.72rem' }}>{e.event_ts}</td><td>{e.event_type}</td><td>{e.qty_delta ?? '-'}</td><td>{fmtKrw(e.price)}</td></tr>
                  ))}
                </tbody>
              </table>
              <div style={{ fontWeight: 700, fontSize: '0.78rem', marginTop: '0.7rem', marginBottom: '0.3rem' }}>체결</div>
              <table className="premium-table">
                <thead><tr><th>시각</th><th>체결수량</th><th>체결가</th><th>누적수량</th></tr></thead>
                <tbody>
                  {(detail.fills || []).map(f => (
                    <tr key={f.id}><td style={{ fontSize: '0.72rem' }}>{f.fill_ts}</td><td>{f.fill_qty}</td><td>{fmtKrw(f.fill_price)}</td><td>{f.cumulative_qty}</td></tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ── 현금원장 탭 ──────────────────────────────────────────
function LedgerTab() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(API('/api/kis-trading/cash-ledger?limit=200'));
      setData(r.ok ? await r.json() : null);
    } catch (e) { console.error(e); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div style={{ fontSize: '0.85rem' }}>현재 잔고: <strong style={{ color: 'var(--accent-purple)' }}>{fmtKrw(data?.current_balance)}</strong></div>
        <button onClick={load} style={{
          padding: '0.3rem 0.7rem', borderRadius: '6px', fontSize: '0.75rem', cursor: 'pointer',
          background: 'rgba(255,255,255,0.05)', border: '1px solid var(--glass-border)', color: 'var(--text-secondary)',
        }}>새로고침</button>
      </div>
      {loading ? (
        <div style={{ color: 'var(--text-secondary)' }}>로딩 중...</div>
      ) : !data?.entries?.length ? (
        <div className="glass-panel" style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}>원장 내역이 없습니다.</div>
      ) : (
        <div className="glass-panel" style={{ overflow: 'clip' }}>
          <table className="premium-table">
            <thead><tr><th>시각</th><th>모드</th><th>증감</th><th>잔고</th><th>사유</th><th>주문ID</th></tr></thead>
            <tbody>
              {data.entries.map(e => (
                <tr key={e.id}>
                  <td style={{ fontSize: '0.75rem' }}>{e.ts}</td>
                  <td>{e.mode}</td>
                  <td style={{ color: pnlColor(-e.delta_krw) }}>{e.delta_krw >= 0 ? '+' : ''}{fmtKrw(e.delta_krw)}</td>
                  <td>{fmtKrw(e.balance_after)}</td>
                  <td style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>{e.reason || '-'}</td>
                  <td>{e.ref_order_id ?? '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const TABS = [
  { key: 'overview', label: '개요' },
  { key: 'check',    label: '사전 점검' },
  { key: 'history',  label: '판정 이력' },
  { key: 'orders',   label: '주문 생애주기' },
  { key: 'ledger',   label: '현금 원장' },
];

export default function RiskGateMonitorView() {
  const [tab, setTab] = useState('overview');
  return (
    <div className="fade-in" style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <div style={{ padding: '0.5rem 0.9rem', background: 'rgba(167,139,250,0.06)', border: '1px solid rgba(167,139,250,0.2)',
        borderRadius: '8px', fontSize: '0.72rem', color: 'rgba(255,255,255,0.6)', lineHeight: 1.5 }}>
        🛡️ PAPER 모드 리스크게이트/주문 모니터입니다. LIVE 주문은 명시적 승인 전까지 항상 403 차단됩니다.
      </div>
      <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
        {TABS.map(t => (
          <button key={t.key} onClick={() => setTab(t.key)} style={{
            padding: '0.4rem 0.9rem', borderRadius: '7px', fontSize: '0.82rem', cursor: 'pointer',
            fontWeight: tab === t.key ? 700 : 500,
            border: `1px solid ${tab === t.key ? 'var(--accent-purple)' : 'var(--glass-border)'}`,
            background: tab === t.key ? 'rgba(167,139,250,0.15)' : 'transparent',
            color: tab === t.key ? 'var(--accent-purple)' : 'var(--text-secondary)',
          }}>{t.label}</button>
        ))}
      </div>
      {tab === 'overview' && <OverviewTab />}
      {tab === 'check' && <CheckTab />}
      {tab === 'history' && <HistoryTab />}
      {tab === 'orders' && <OrdersTab />}
      {tab === 'ledger' && <LedgerTab />}
    </div>
  );
}
