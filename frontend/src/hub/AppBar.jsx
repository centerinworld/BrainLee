import React from 'react';
import { ArrowUpRight, LogOut, Menu } from 'lucide-react';
import { KEY_INDICATOR_URL, MODULES } from './modules';
import { onLinkClick } from './router';

/** 모든 모듈 화면 최상단 공통 바 — 허브 복귀 + 모듈 간 이동. (2026-09-27 전면 개편) */
export default function AppBar({ current, onMenu, showMenu, isAdmin, onLogout }) {
  const items = [
    { key: 'info', label: 'Stock Info', href: MODULES.info.path, spa: true },
    { key: 'key', label: 'Key Indicator', href: KEY_INDICATOR_URL, external: true },
    { key: 'lab', label: 'Stock Lab', href: MODULES.lab.path, spa: true },
    // 2026-09-27: Stock LLM도 다른 모듈과 같은 AppBar+왼쪽 사이드바 셸 안에서 연다(hub/StockLlmView.jsx, 실제 화면은 iframe).
    { key: 'llm', label: 'Stock LLM', href: MODULES['stock-llm'].path, spa: true },
  ];
  return (
    <header className="appbar">
      {showMenu && (
        <button type="button" className="appbar-menu" onClick={onMenu} aria-label="메뉴 열기/닫기"><Menu size={18} /></button>
      )}
      <a className="appbar-brand" href="/" onClick={(e) => onLinkClick(e, '/')}>
        <span className="appbar-logo">S</span><span className="appbar-brand-text">Stock Hub</span>
      </a>
      <nav className="appbar-tabs" aria-label="모듈">
        {items.map((it) => (
          <a key={it.key} href={it.href} className={`appbar-tab${current === it.key ? ' active' : ''}`}
             onClick={it.spa ? (e) => onLinkClick(e, it.href) : undefined}>
            {it.label}{it.external && <ArrowUpRight size={12} />}
          </a>
        ))}
      </nav>
      <span className="appbar-spacer" />
      <a className={`appbar-tab admin${current === 'admin' ? ' active' : ''}`} href={MODULES.admin.path}
         onClick={(e) => onLinkClick(e, MODULES.admin.path)}>관리자</a>
      {isAdmin && (
        <button type="button" className="appbar-logout" onClick={onLogout} title="로그아웃"><LogOut size={15} /></button>
      )}
    </header>
  );
}

