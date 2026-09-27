/**
 * hub/router.js — 의존성 없는 경량 History 라우터 (2026-09-27, 사이트 전면 개편)
 * 경로 구조: `/`(허브) · `/info[/탭]` · `/lab[/탭]` · `/admin[/탭]`. (`/llm/`은 서버가 Stock LLM으로 프록시하므로 SPA 밖이다.)
 * vite preview의 SPA fallback이 알 수 없는 경로를 index.html로 돌려주므로 새로고침·직접 진입이 된다.
 */
import { useSyncExternalStore } from 'react';

const listeners = new Set();
const notify = () => listeners.forEach((l) => l());
if (typeof window !== 'undefined') window.addEventListener('popstate', notify);

const subscribe = (cb) => { listeners.add(cb); return () => listeners.delete(cb); };
const getSnapshot = () => window.location.pathname + window.location.search;

export function navigate(to, { replace = false } = {}) {
  if (to === getSnapshot()) return;
  window.history[replace ? 'replaceState' : 'pushState']({}, '', to);
  notify();
}

/** 현재 경로(pathname+search) — 바뀌면 리렌더 */
export const useLocation = () => useSyncExternalStore(subscribe, getSnapshot);

/** '/info/analysis' → { module:'info', tab:'analysis' } ; '/' → { module:null, tab:null } */
export function parsePath(pathname) {
  const seg = pathname.split('?')[0].split('/').filter(Boolean);
  return { module: seg[0] || null, tab: seg[1] || null };
}

/** 같은 출처 내부 링크 클릭을 SPA 이동으로 처리 (새 탭·수정키·외부·/llm 은 브라우저 기본 동작) */
export function onLinkClick(e, to) {
  if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
  e.preventDefault();
  navigate(to);
}
