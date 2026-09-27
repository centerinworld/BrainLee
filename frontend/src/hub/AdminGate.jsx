import React, { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, Lock } from 'lucide-react';
import { navigate, onLinkClick } from './router';

const jget = (url) => fetch(url, { credentials: 'same-origin' }).then((r) => (r.ok ? r.json() : null)).catch(() => null);

/** 로그인 상태 조회/로그아웃 — AppBar 로그아웃 버튼과 AdminGate가 공유 */
export function useAdminSession() {
  const [state, setState] = useState({ loading: true, authenticated: false, configured: true, canSetup: false });
  const refresh = useCallback(async () => {
    const d = await jget('/api/admin-auth/status');
    setState({ loading: false, authenticated: !!d?.authenticated, configured: d ? !!d.configured : true, canSetup: !!d?.can_setup });
  }, []);
  useEffect(() => { refresh(); }, [refresh]);
  const logout = useCallback(async () => {
    await fetch('/api/admin-auth/logout', { method: 'POST', credentials: 'same-origin' }).catch(() => {});
    setState((s) => ({ ...s, authenticated: false }));
    navigate('/');
  }, []);
  return { ...state, refresh, logout };
}

// 로그인 뒤 이동할 곳: 같은 출처 내부 경로만 허용(오픈 리다이렉트 방지)
const safeNext = (raw) => (raw && /^\/(?!\/)[^\s\\]*$/.test(raw) ? raw : null);

const MESSAGES = {
  401: '비밀번호가 올바르지 않습니다.',
  429: '시도 횟수가 많습니다. 몇 분 뒤 다시 시도하세요.',
  503: '관리자 비밀번호가 아직 설정되지 않았습니다. 이 PC에서 직접(localhost:5173/admin) 접속해 설정하세요.',
};

/** `/admin` 진입 게이트 — 비밀번호를 서버가 확인한다(브라우저 코드에 비밀번호 없음). */
export default function AdminGate({ session, children }) {
  const { loading, authenticated, configured, canSetup, refresh } = session;
  const [pw, setPw] = useState('');
  const [pw2, setPw2] = useState('');
  const [err, setErr] = useState('');
  const [busy, setBusy] = useState(false);

  if (loading) return <div className="gate-wrap"><p className="gate-sub">확인 중…</p></div>;
  if (authenticated) return children;

  const setup = !configured && canSetup;
  const submitSetup = async (e) => {
    e.preventDefault();
    if (busy) return;
    if (pw.length < 10) { setErr('10자 이상 입력하세요.'); return; }
    if (pw !== pw2) { setErr('두 입력이 다릅니다.'); return; }
    setBusy(true); setErr('');
    try {
      const res = await fetch('/api/admin-auth/setup', { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: pw }) });
      if (res.ok) { setPw(''); setPw2(''); await refresh(); return; }
      setErr(res.status === 409 ? '이미 설정되어 있습니다. 새로고침 후 로그인하세요.' : res.status === 403 ? '이 PC에서 직접 접속한 경우에만 설정할 수 있습니다.' : '설정에 실패했습니다.');
    } catch { setErr('서버에 연결할 수 없습니다.'); }
    setBusy(false);
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!pw || busy) return;
    setBusy(true); setErr('');
    try {
      const res = await fetch('/api/admin-auth/login', {
        method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: pw }),
      });
      if (res.ok) {
        const next = safeNext(new URLSearchParams(window.location.search).get('next'));
        setPw('');
        if (next && !next.startsWith('/info') && !next.startsWith('/lab') && !next.startsWith('/admin') && next !== '/') { window.location.assign(next); return; }
        await refresh();
        if (next) navigate(next, { replace: true });
        return;
      }
      setErr(MESSAGES[res.status] || '로그인에 실패했습니다.');
    } catch {
      setErr('서버에 연결할 수 없습니다. 잠시 후 다시 시도하세요.');
    }
    setPw(''); setBusy(false);
  };

  if (setup) {
    return (
      <div className="gate-wrap">
        <form className="gate-card" onSubmit={submitSetup}>
          <span className="gate-lock"><Lock size={20} /></span>
          <h1>관리자 비밀번호 설정</h1>
          <p className="gate-sub">처음 한 번만 설정합니다. 이 PC에서 직접 접속했을 때만 보이는 화면입니다.</p>
          <input type="password" autoFocus autoComplete="new-password" value={pw} placeholder="새 비밀번호 (10자 이상)" onChange={(e) => { setPw(e.target.value); setErr(''); }} aria-label="새 비밀번호" />
          <input type="password" autoComplete="new-password" value={pw2} placeholder="한 번 더 입력" onChange={(e) => { setPw2(e.target.value); setErr(''); }} aria-label="비밀번호 확인" className={err ? 'err' : ''} />
          {err && <p className="gate-err" role="alert">{err}</p>}
          <button type="submit" disabled={!pw || !pw2 || busy}>{busy ? '저장 중…' : '설정하고 로그인'}</button>
          <a className="gate-back" href="/" onClick={(e) => onLinkClick(e, '/')}><ArrowLeft size={14} /> 허브로 돌아가기</a>
        </form>
      </div>
    );
  }

  return (
    <div className="gate-wrap">
      <form className="gate-card" onSubmit={submit}>
        <span className="gate-lock"><Lock size={20} /></span>
        <h1>관리자 모드</h1>
        <p className="gate-sub">비밀번호를 입력하세요.</p>
        <input type="password" autoFocus autoComplete="current-password" value={pw} placeholder="비밀번호"
               onChange={(e) => { setPw(e.target.value); setErr(''); }} className={err ? 'err' : ''} aria-label="관리자 비밀번호" />
        {err && <p className="gate-err" role="alert">{err}</p>}
        <button type="submit" disabled={!pw || busy}>{busy ? '확인 중…' : '로그인'}</button>
        <a className="gate-back" href="/" onClick={(e) => onLinkClick(e, '/')}><ArrowLeft size={14} /> 허브로 돌아가기</a>
      </form>
    </div>
  );
}
