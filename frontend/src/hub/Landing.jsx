import React, { useEffect, useState } from 'react';
import { Activity, ArrowUpRight, Bot, FlaskConical, Gauge, Lock, ShieldCheck } from 'lucide-react';
import { KEY_INDICATOR_URL, MODULES } from './modules';
import { onLinkClick } from './router';

const CARDS = [
  {
    key: 'info', tone: 'blue', title: 'Stock Info', href: MODULES.info.path, spa: true, Icon: Activity,
    desc: '시황·종목·섹터·수급을 한 곳에서 조회하는 주식 정보 대시보드',
    points: ['주요 지표 · 수급 현황', '국내/미국 종목 분석', '섹터 · 수출입 · ETF'],
  },
  {
    key: 'key', tone: 'green', title: 'Key Indicator', href: KEY_INDICATOR_URL, external: true, Icon: Gauge,
    desc: '핵심 경제 지표와 뉴스 브리핑 (newsinfo.cloud)',
    points: ['주요 경제지표 캘린더', 'AI 뉴스 요약 브리핑', '지표 알림'],
  },
  {
    key: 'lab', tone: 'violet', title: 'Stock Lab', href: MODULES.lab.path, spa: true, Icon: FlaskConical,
    desc: '전략을 검증하고 종목을 발굴하는 퀀트 연구실',
    points: ['전략 센터 · 백테스트', '텐버거 발굴 · 조건 필터', '가상 매매 · 실험 로드맵'],
  },
  {
    // 2026-09-27: 다른 모듈과 같은 AppBar+왼쪽 사이드바 셸로 진입(hub/StockLlmView.jsx의 iframe). 실제 화면 접근은 여전히 관리자 로그인 필요.
    key: 'llm', tone: 'amber', title: 'Stock LLM', href: MODULES['stock-llm'].path, spa: true, Icon: Bot, locked: true,
    desc: '수집한 리포트·공시를 근거로 답하는 하이브리드 RAG 에이전트',
    points: ['자연어 종목·기업 질의', 'SQL + 벡터 하이브리드 검색', '일간 마켓 브리핑'],
  },
];

/** `/` — 4개 메인 페이지 선택 + 관리자 진입 (2026-09-27 전면 개편) */
export default function Landing() {
  const [status, setStatus] = useState(null);
  useEffect(() => {
    let alive = true;
    fetch('/api/hub/status').then((r) => (r.ok ? r.json() : null)).then((d) => { if (alive && d) setStatus(d.modules); }).catch(() => {});
    return () => { alive = false; };
  }, []);
  const stateOf = (key) => (status == null ? 'unknown' : status[key]?.up ? 'up' : 'down');
  const label = { up: '정상', down: '점검 중', unknown: '확인 중' };

  return (
    <div className="hub">
      <header className="hub-top">
        <a className="appbar-brand" href="/" onClick={(e) => onLinkClick(e, '/')}>
          <span className="appbar-logo">S</span><span className="appbar-brand-text">Stock Hub</span>
        </a>
        <a className="hub-admin-link" href={MODULES.admin.path} onClick={(e) => onLinkClick(e, MODULES.admin.path)}>
          <ShieldCheck size={15} /> 관리자 모드
        </a>
      </header>

      <main className="hub-main">
        <section className="hub-hero">
          <h1>어떤 화면으로 들어갈까요?</h1>
          <p>주식 정보 · 핵심 지표 · 전략 연구 · AI 리서치를 한 곳에서 선택하세요.</p>
        </section>

        <div className="hub-grid">
          {CARDS.map(({ key, tone, title, href, spa, external, locked, Icon, desc, points }) => (
            <a key={key} href={href} className={`hub-card tone-${tone}`} onClick={spa ? (e) => onLinkClick(e, href) : undefined}>
              <div className="hub-card-head">
                <span className="hub-icon"><Icon size={22} /></span>
                <span className={`hub-status ${stateOf(key)}`}><i />{label[stateOf(key)]}</span>
              </div>
              <h2>{title}{external && <ArrowUpRight size={16} className="hub-ext" />}</h2>
              <p className="hub-desc">{desc}</p>
              <ul>{points.map((p) => <li key={p}>{p}</li>)}</ul>
              <div className="hub-open">
                {locked ? <><Lock size={13} /> 관리자 로그인 후 사용</> : <>열기 <ArrowUpRight size={14} /></>}
              </div>
            </a>
          ))}
        </div>

        <a className="hub-admin-card" href={MODULES.admin.path} onClick={(e) => onLinkClick(e, MODULES.admin.path)}>
          <span className="hub-icon slate"><ShieldCheck size={20} /></span>
          <span className="hub-admin-text"><b>관리자 모드</b><small>시스템 상태 · 리스크게이트 · 설정 — 비밀번호가 필요합니다</small></span>
          <Lock size={16} />
        </a>
      </main>

      <footer className="hub-foot">stock.leanguy.cloud</footer>
    </div>
  );
}
