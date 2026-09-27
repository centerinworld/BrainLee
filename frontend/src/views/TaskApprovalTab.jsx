/* light-theme-codemod-2026-09-27 */
/* light-theme-codemod-pass2-2026-09-27 */
/* light-theme-codemod-pass3-2026-09-27 */
/* light-theme-codemod-pass4-2026-09-27 */
/* light-theme-codemod-pass5-2026-09-27 */
/* light-theme-codemod-pass6-2026-09-27 */
/* light-theme-codemod-pass7-2026-09-27 */
// TaskApprovalTab.jsx — 리스크게이트 화면의 "작업 승인 대기" 탭 (2026-09-24 신규)
//   백엔드: routes/task_approvals.py (prefix /api/task-approvals)
//   - GET  /pending  미승인 작업 + 참고 테이블 현황 + 기록된 승인
//   - POST /approve  사람 승인 기록 (confirm=true 필수, 자동승인 차단)
//   - POST /revoke   사람 철회 기록
//   - GET  /ledger   감사 원장 read-back
// ⚠️ 이 탭의 승인은 "작업 착수 결재 기록"일 뿐이며 LIVE 주문 승인과 연결되지 않습니다.
import React, { useCallback, useEffect, useState } from 'react';

const API = (path) => path;
const MIN_NOTE = 10;

const STATUS_META = {
  QUEUED_FOR_FRONTIER: { label: '프론티어 대기', color: '#b45309' },
  QUEUED_FOR_APPROVAL: { label: '승인 대기',     color: '#b45309' },
  QUEUED:              { label: '큐 대기',       color: '#b45309' },
  PENDING:             { label: '대기',          color: '#b45309' },
  WAITING_APPROVAL:    { label: '승인 대기',     color: '#b45309' },
};

function StatusBadge({ status }) {
  const meta = STATUS_META[status] || { label: status || '-', color: 'var(--text-secondary)' };
  return (
    <span style={{
      padding: '0.18rem 0.55rem', borderRadius: '12px', fontSize: '0.7rem', fontWeight: 700,
      color: meta.color, background: `${meta.color}18`, border: `1px solid ${meta.color}45`, whiteSpace: 'nowrap',
    }}>{meta.label}</span>
  );
}

function ActorKind({ actor }) {
  const norm = (actor || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  const automated = /(bot|agent|ai|gpt|llm|codex|claude|deepseek|gemini|qwen|system|auto)$/.test(norm)
    || ['ai', 'agent', 'bot', 'system', 'auto', 'hermes', 'checker', 'planner', 'codedoer'].includes(norm);
  return (
    <span style={{
      fontSize: '0.68rem', fontWeight: 700, padding: '0.12rem 0.45rem', borderRadius: '10px',
      color: automated ? '#dc2626' : '#15803d',
      background: automated ? 'rgba(220,38,38,0.12)' : 'rgba(22,163,74,0.12)',
      border: `1px solid ${automated ? 'rgba(220,38,38,0.35)' : 'rgba(22,163,74,0.35)'}`,
    }}>{automated ? '자동/에이전트(승인 불가)' : '사람'}</span>
  );
}

function ApprovalForm({ task, onDone }) {
  const [actor, setActor] = useState('');
  const [note, setNote] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  const norm = actor.toLowerCase().replace(/[^a-z0-9]/g, '');
  const actorBlocked = !norm
    || ['ai', 'agent', 'bot', 'system', 'auto', 'hermes', 'checker', 'planner', 'codedoer'].includes(norm)
    || /(bot|agent|ai|gpt|llm|codex|claude|deepseek|gemini|qwen|system|auto)$/.test(norm);
  const ready = !actorBlocked && note.trim().length >= MIN_NOTE && confirmed && !busy;

  const submit = async (action) => {
    setBusy(true); setMsg(null);
    try {
      const r = await fetch(API(`/api/task-approvals/${action}`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          task_key: task.task_key,
          approved_by: actor.trim(),
          audit_note: note.trim(),
          confirm: confirmed,
        }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        const d = data.detail || {};
        setMsg({ ok: false, text: `${r.status} ${d.reason || ''} — ${d.message || data.detail || '거부되었습니다.'}` });
      } else {
        setMsg({
          ok: true,
          text: data.result === 'idempotent_replay'
            ? '이미 같은 결재 기록이 있습니다(중복 저장 안 함).'
            : `기록 완료 (${data.approval?.status} / ${data.approval?.approved_by} / ${data.approval?.approved_at})`,
        });
        if (typeof onDone === 'function') onDone();
      }
    } catch (e) {
      setMsg({ ok: false, text: String(e) });
    } finally { setBusy(false); }
  };

  const inputStyle = {
    padding: '0.4rem 0.6rem', borderRadius: '6px', background: 'rgba(15,23,42,0.05)',
    border: '1px solid var(--glass-border)', color: 'inherit', width: '100%',
  };

  return (
    <div style={{ marginTop: '0.7rem', padding: '0.8rem', borderRadius: '8px', background: 'rgba(15,23,42,0.02)', border: '1px solid var(--glass-border)' }}>
      <div style={{ fontSize: '0.75rem', fontWeight: 700, marginBottom: '0.5rem' }}>사람 승인 기록</div>
      <div style={{ display: 'grid', gridTemplateColumns: '180px 1fr', gap: '0.5rem', alignItems: 'start' }}>
        <div>
          <label style={{ fontSize: '0.68rem', color: 'var(--text-secondary)', display: 'block', marginBottom: '0.2rem' }}>승인자(사람 이름)</label>
          <input value={actor} onChange={e => setActor(e.target.value)} placeholder="예: brainlee" style={inputStyle} />
          {actorBlocked && actor && (
            <div style={{ fontSize: '0.66rem', color: '#dc2626', marginTop: '0.2rem' }}>에이전트/자동화 신원은 승인할 수 없습니다.</div>
          )}
        </div>
        <div>
          <label style={{ fontSize: '0.68rem', color: 'var(--text-secondary)', display: 'block', marginBottom: '0.2rem' }}>
            승인 근거 ({MIN_NOTE}자 이상)
          </label>
          <textarea value={note} onChange={e => setNote(e.target.value)} rows={2}
            placeholder="왜 이 작업을 승인하는지, 무엇을 확인했는지 적어주세요."
            style={{ ...inputStyle, resize: 'vertical', fontFamily: 'inherit', fontSize: '0.78rem' }} />
        </div>
      </div>
      <label style={{ display: 'flex', gap: '0.4rem', alignItems: 'center', marginTop: '0.5rem', fontSize: '0.72rem', color: 'var(--text-secondary)' }}>
        <input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />
        이 승인은 작업 착수 결재 기록이며 LIVE 주문 승인이 아님을 확인했습니다.
      </label>
      <div style={{ display: 'flex', gap: '0.4rem', marginTop: '0.6rem', flexWrap: 'wrap' }}>
        <button onClick={() => submit('approve')} disabled={!ready} style={{
          padding: '0.4rem 0.9rem', borderRadius: '6px', fontWeight: 700, fontSize: '0.78rem',
          cursor: ready ? 'pointer' : 'not-allowed', opacity: ready ? 1 : 0.45,
          background: 'rgba(22,163,74,0.15)', border: '1px solid rgba(22,163,74,0.45)', color: '#15803d',
        }}>승인 기록</button>
        {task.approval && (
          <button onClick={() => submit('revoke')} disabled={!ready} style={{
            padding: '0.4rem 0.9rem', borderRadius: '6px', fontWeight: 700, fontSize: '0.78rem',
            cursor: ready ? 'pointer' : 'not-allowed', opacity: ready ? 1 : 0.45,
            background: 'rgba(220,38,38,0.12)', border: '1px solid rgba(220,38,38,0.4)', color: '#dc2626',
          }}>철회 기록</button>
        )}
        <span style={{ fontSize: '0.68rem', color: 'var(--text-secondary)', alignSelf: 'center' }}>
          근거·승인자·확인 체크가 모두 충족돼야 버튼이 열립니다(서버가 다시 검사).
        </span>
      </div>
      {msg && (
        <div style={{ marginTop: '0.5rem', fontSize: '0.75rem', color: msg.ok ? '#15803d' : '#dc2626' }}>
          {msg.ok ? '✅ ' : '⛔ '}{msg.text}
        </div>
      )}
    </div>
  );
}

function LedgerPanel() {
  const [data, setData] = useState(null);
  const load = useCallback(async () => {
    try {
      const r = await fetch(API('/api/task-approvals/ledger?limit=100'));
      setData(r.ok ? await r.json() : null);
    } catch (e) { console.error(e); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const events = data?.events || [];
  return (
    <div className="glass-panel" style={{ padding: '1rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.5rem' }}>
        <div style={{ fontWeight: 700, fontSize: '0.85rem' }}>감사 원장 (task_approval_events)</div>
        <button onClick={load} style={{
          padding: '0.3rem 0.7rem', borderRadius: '6px', fontSize: '0.75rem', cursor: 'pointer',
          background: 'rgba(15,23,42,0.05)', border: '1px solid var(--glass-border)', color: 'var(--text-secondary)',
        }}>read-back</button>
      </div>
      {events.length === 0 ? (
        <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>기록된 승인 이력이 없습니다(승인 0건 = DB 쓰기 0건).</div>
      ) : (
        <table className="premium-table">
          <thead><tr><th>시각</th><th>대상</th><th>행위</th><th>승인자</th><th>사유</th><th>LIVE 연결</th></tr></thead>
          <tbody>
            {events.map(e => (
              <tr key={e.id}>
                <td style={{ fontSize: '0.72rem', whiteSpace: 'nowrap' }}>{e.created_at}</td>
                <td style={{ fontSize: '0.75rem' }}>{e.task_key}</td>
                <td style={{ fontWeight: 700, color: e.action === 'approve' ? '#15803d' : '#dc2626' }}>{e.action}</td>
                <td><ActorKind actor={e.approved_by} /> <span style={{ fontSize: '0.72rem' }}>{e.approved_by}</span></td>
                <td style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>{e.audit_note}</td>
                <td style={{ fontSize: '0.72rem', color: e.live_order_linked ? '#dc2626' : 'var(--text-secondary)' }}>
                  {e.live_order_linked ? '연결됨(비정상)' : '미연결'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function TaskApprovalTab() {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [expanded, setExpanded] = useState(null);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const r = await fetch(API('/api/task-approvals/pending'));
      if (!r.ok) setError(`요청 실패 (${r.status})`);
      else setData(await r.json());
    } catch (e) { setError(String(e)); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { load(); }, [load]);

  const tasks = data?.tasks || [];
  const approvals = data?.approvals || [];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      <div style={{ padding: '0.6rem 0.9rem', borderRadius: '8px', fontSize: '0.74rem', lineHeight: 1.6,
        background: 'rgba(217,119,6,0.07)', border: '1px solid rgba(217,119,6,0.25)', color: 'rgba(15,23,42,0.88)' }}>
        ⏳ <strong>승인 대기 작업</strong>입니다. 승인은 <strong>작업 착수 결재 기록</strong>일 뿐이고
        <strong> LIVE 주문·실계좌와는 어떤 경우에도 연결되지 않습니다</strong>
        (이 기능이 쓰는 테이블: <code>task_approvals</code>, <code>task_approval_events</code> 2개뿐).
        아래 참고 테이블은 읽기 전용입니다.
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: '0.75rem' }}>
        {[
          { label: '미승인 작업', val: data?.queue?.pending_total ?? 0, color: '#b45309' },
          { label: '기록된 승인', val: approvals.filter(a => a.status === 'approved').length, color: '#15803d' },
          { label: '철회', val: approvals.filter(a => a.status === 'revoked').length, color: '#dc2626' },
          { label: '읽은 시각', val: (data?.read_at || '-').slice(11), color: 'inherit' },
        ].map(({ label, val, color }) => (
          <div key={label} className="glass-panel" style={{ padding: '0.8rem 1rem' }}>
            <p style={{ fontSize: '0.7rem', color: 'var(--text-secondary)', marginBottom: '0.3rem' }}>{label}</p>
            <p style={{ fontSize: '0.95rem', fontWeight: 700, color }}>{val}</p>
          </div>
        ))}
      </div>

      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span style={{ fontSize: '0.75rem', color: 'var(--text-secondary)' }}>
          승인 대상 소스: <code>{data?.queue?.path?.split('/').slice(-1)[0] || 'frontier_handoff_queue.json'}</code>
          {data?.queue?.error ? ` (오류: ${data.queue.error})` : ''}
        </span>
        <button onClick={load} style={{
          padding: '0.3rem 0.7rem', borderRadius: '6px', fontSize: '0.75rem', cursor: 'pointer',
          background: 'rgba(15,23,42,0.05)', border: '1px solid var(--glass-border)', color: 'var(--text-secondary)',
        }}>새로고침</button>
      </div>

      {error && <div className="glass-panel" style={{ padding: '0.8rem 1rem', color: '#dc2626', fontSize: '0.8rem' }}>⚠️ {error}</div>}
      {loading && <div style={{ color: 'var(--text-secondary)', fontSize: '0.8rem' }}>로딩 중...</div>}

      {!loading && tasks.length === 0 && (
        <div className="glass-panel" style={{ padding: '2rem', textAlign: 'center', color: 'var(--text-secondary)' }}>
          승인 대기 작업이 없습니다.
        </div>
      )}

      {tasks.map(task => (
        <div key={task.task_key} className="glass-panel" style={{ padding: '1rem' }}>
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
            <strong style={{ fontSize: '0.9rem' }}>{task.title}</strong>
            <code style={{ fontSize: '0.72rem', color: 'var(--text-secondary)' }}>{task.task_key}</code>
            <StatusBadge status={task.status} />
            {!task.approvable && (
              <span style={{ fontSize: '0.7rem', color: '#dc2626' }}>{task.not_approvable_reason}</span>
            )}
            {task.approval && (
              <span style={{ fontSize: '0.7rem', fontWeight: 700, color: task.approval.status === 'approved' ? '#15803d' : '#dc2626' }}>
                {task.approval.status === 'approved' ? '✅ 승인됨' : '⛔ 철회'} · {task.approval.approved_by} · {task.approval.approved_at}
              </span>
            )}
          </div>

          <div style={{ fontSize: '0.74rem', color: 'var(--text-secondary)', marginTop: '0.35rem', lineHeight: 1.7 }}>
            <div>출처: {task.source_type} · {task.source_ref} · 생성 {task.created_at || '-'}
              {task.target_model ? ` · 대상모델 ${task.target_model}` : ''}
              {task.pipeline ? ` · ${task.pipeline}` : ''}</div>
            {task.target_files?.length > 0 && (
              <div>대상 파일: {task.target_files.map(f => (
                <code key={f} style={{ marginRight: '0.35rem', fontSize: '0.7rem' }}>{f}</code>
              ))}</div>
            )}
          </div>

          {task.cross_store_note && (
            <div style={{ marginTop: '0.4rem', fontSize: '0.72rem', color: '#b45309' }}>⚠️ {task.cross_store_note}</div>
          )}

          {task.approval?.audit_note && (
            <div style={{ marginTop: '0.4rem', fontSize: '0.74rem' }}>승인 근거: {task.approval.audit_note}</div>
          )}

          <div onClick={() => setExpanded(expanded === task.task_key ? null : task.task_key)}
            style={{ marginTop: '0.45rem', fontSize: '0.7rem', color: 'var(--accent-purple)', cursor: 'pointer' }}>
            {expanded === task.task_key ? '작업 지시 원문 접기 ▲' : '작업 지시 원문 보기 ▼'}
          </div>
          {expanded === task.task_key && (
            <pre style={{ marginTop: '0.4rem', whiteSpace: 'pre-wrap', fontSize: '0.7rem', lineHeight: 1.6,
              color: 'var(--text-secondary)', background: 'rgba(0,0,0,0.18)', padding: '0.6rem', borderRadius: '6px', maxHeight: '260px', overflow: 'auto' }}>
              {task.prompt_preview || '(원문 없음)'}
            </pre>
          )}

          {task.approvable && <ApprovalForm task={task} onDone={load} />}
        </div>
      ))}

      {data?.reference && (
        <div className="glass-panel" style={{ padding: '1rem' }}>
          <div style={{ fontWeight: 700, fontSize: '0.85rem', marginBottom: '0.5rem' }}>참고: 다른 승인/작업 테이블 (읽기 전용)</div>
          <table className="premium-table">
            <thead><tr><th>테이블</th><th>행수</th><th>이 탭에서 변경</th><th>설명</th></tr></thead>
            <tbody>
              {Object.entries(data.reference).map(([key, ref]) => (
                <tr key={key}>
                  <td><code style={{ fontSize: '0.72rem' }}>{ref.table || key}</code></td>
                  <td style={{ fontWeight: 700 }}>{ref.count == null ? '-' : ref.count}</td>
                  <td style={{ color: '#15803d', fontWeight: 700 }}>불가(읽기전용)</td>
                  <td style={{ fontSize: '0.74rem', color: 'var(--text-secondary)' }}>{ref.note}{ref.error ? ` (${ref.error})` : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <LedgerPanel />
    </div>
  );
}
