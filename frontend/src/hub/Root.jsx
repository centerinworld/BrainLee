import React, { useEffect } from 'react';
import App from '../App.jsx';
import AdminGate, { useAdminSession } from './AdminGate.jsx';
import Landing from './Landing.jsx';
import { LOCKED_TABS, MODULES, moduleOfTab, tabPath } from './modules';
import { navigate, parsePath, useLocation } from './router';

const LAST_KEY = (m) => `sd_last_tab_${m}`;
const lastTab = (m) => { try { const v = localStorage.getItem(LAST_KEY(m)); return LOCKED_TABS.includes(v) ? null : v; } catch { return null; } };   // 잠긴 탭(내 투자)은 접속 시 복원하지 않는다

/** 최상위 라우터 (2026-09-27 전면 개편): `/` 허브 · `/info|lab|admin[/탭]` 모듈 화면 */
/** 배포 후에도 옛 화면 코드가 탭에 남지 않도록: 화면을 옮길 때 서버의 최신 진입 스크립트 이름을 확인해 달라졌으면 한 번 새로고침한다. */
function useVersionWatch(loc) {
  useEffect(() => {
    const current = [...document.scripts].map((s) => s.src).find((x) => /\/assets\/index-[^/]+\.js/.test(x));
    if (!current) return undefined;               // 개발 서버(vite dev)에서는 해시 파일이 없다
    let dead = false;
    fetch('/index.html', { cache: 'no-store' }).then((r) => (r.ok ? r.text() : '')).then((html) => {
      const m = html.match(/\/assets\/index-[^"']+\.js/);
      if (dead || !m || current.endsWith(m[0])) return;
      try {
        const last = Number(sessionStorage.getItem('sd_ver_reload') || 0);
        if (Date.now() - last < 60000) return;
        sessionStorage.setItem('sd_ver_reload', String(Date.now()));
      } catch { /* 저장소 차단 */ }
      window.location.reload();
    }).catch(() => {});
    return () => { dead = true; };
  }, [loc]);
}

export default function Root() {
  const loc = useLocation();
  useVersionWatch(loc);
  const { module, tab } = parsePath(loc);
  const session = useAdminSession();
  const mod = MODULES[module];

  // 정규화: 알 수 없는 경로 → 허브, 탭 생략 → 마지막 탭(없으면 기본 탭), 다른 모듈의 탭 → 그 모듈 경로
  useEffect(() => {
    if (module && !mod) { navigate('/', { replace: true }); return; }
    if (!mod) return;
    if (!tab) {
      const last = lastTab(module);
      navigate(tabPath(module, last && moduleOfTab(last) === module ? last : mod.defaultTab), { replace: true });
      return;
    }
    const owner = moduleOfTab(tab);
    if (owner && owner !== module) navigate(tabPath(owner, tab), { replace: true });
    else if (!owner) navigate(tabPath(module, mod.defaultTab), { replace: true });
    else if (!LOCKED_TABS.includes(tab)) { try { localStorage.setItem(LAST_KEY(module), tab); } catch { /* storage blocked */ } }
  }, [module, tab, mod]);

  if (!mod) return module ? null : <Landing />;
  // 정규화 이동 중(예: Lab → Stock Info 로 `/info` 만 열린 순간)에도 화면을 없애지 않고 기본 탭으로 그대로 그린다.
  // 예전에는 여기서 null 을 돌려줘 App 이 통째로 사라졌다 다시 만들어졌고, 그 바람에 「내 투자」 잠금 해제 상태 등 화면 상태가 초기화됐다.
  const shownTab = tab && moduleOfTab(tab) === module ? tab : (lastTab(module) || mod.defaultTab);
  const app = <App module={module} tab={shownTab} isAdmin={session.authenticated} onLogout={session.logout} onLogin={session.refresh} />;
  return module === 'admin' ? <AdminGate session={session}>{app}</AdminGate> : app;
}
