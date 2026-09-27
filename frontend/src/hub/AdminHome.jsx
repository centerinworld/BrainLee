import React, { useEffect, useState } from 'react';
import { Activity, Bot, ExternalLink, Gauge, LogOut, ServerCog, Settings, ShieldAlert } from 'lucide-react';
import { KEY_INDICATOR_URL, LLM_PATH } from './modules';

const MODS = [
  { key: 'info', name: 'Stock Info', where: 'stock.leanguy.cloud/info' },
  { key: 'lab', name: 'Stock Lab', where: 'stock.leanguy.cloud/lab' },
  { key: 'key', name: 'Key Indicator', where: 'newsinfo.cloud' },
  { key: 'llm', name: 'Stock LLM', where: 'stock.leanguy.cloud/llm' },
];

/** 관리자 모드 개요 탭(admin_home) — 서비스 상태 + 관리 바로가기 */
export default function AdminHome({ onLogout, changeTab }) {
  const [status, setStatus] = useState(null);
  useEffect(() => {
    const load = () => fetch('/api/hub/status').then((r) => (r.ok ? r.json() : null)).then((d) => d && setStatus(d.modules)).catch(() => {});
    load();
    const t = setInterval(load, 20000);
    return () => clearInterval(t);
  }, []);
  const shortcuts = [
    { Icon: Activity, title: '시스템 현황', desc: '코드·API·데이터·프로세스 자동 집계', run: () => changeTab('system_map') },
    { Icon: ServerCog, title: '수집 상태', desc: '수집 실행 기록·DB 현황', run: () => changeTab('system') },
    { Icon: ShieldAlert, title: '리스크게이트', desc: '주문 차단·통과 판정 이력', run: () => changeTab('risk_gate') },
    { Icon: Settings, title: '설정', desc: '시스템 설정', run: () => changeTab('settings') },
    { Icon: Bot, title: 'Stock LLM 콘솔', desc: '문서 적재·질의 (새 탭)', href: LLM_PATH },
    { Icon: Gauge, title: 'Key Indicator 관리', desc: '콘솔 · KAI 관제 센터(이 화면 안에서)', run: () => changeTab('admin_ki') },
    { Icon: ExternalLink, title: 'Key Indicator 공개 화면', desc: '주요 경제지표 · 뉴스정보 (누구나)', href: KEY_INDICATOR_URL },
  ];
  return (
    <div className="adm">
      <section className="adm-panel">
        <h2>서비스 상태</h2>
        <div className="adm-status">
          {MODS.map((m) => {
            const s = status == null ? 'unknown' : status[m.key]?.up ? 'up' : 'down';
            return (
              <div key={m.key} className="adm-stat">
                <span className={`hub-status ${s}`}><i />{{ up: '정상', down: '점검 중', unknown: '확인 중' }[s]}</span>
                <b>{m.name}</b><small>{m.where}</small>
              </div>
            );
          })}
        </div>
      </section>

      <section className="adm-panel">
        <h2>관리 바로가기</h2>
        <div className="adm-links">
          {shortcuts.map(({ Icon, title, desc, run, href }) => (
            <a key={title} className="adm-link" href={href || '#'} onClick={run ? (e) => { e.preventDefault(); run(); } : undefined}>
              <span className="hub-icon slate"><Icon size={18} /></span>
              <span><b>{title}</b><small>{desc}</small></span>
              {href && <ExternalLink size={14} className="adm-ext" />}
            </a>
          ))}
        </div>
      </section>

      <section className="adm-panel">
        <h2>세션</h2>
        <p className="adm-note">관리자 세션은 로그인 후 8시간 유지됩니다. 공용 기기에서는 사용 후 로그아웃하세요. 비밀번호 변경은 서버에서 <code>scripts/ops/set_admin_password.py</code> 실행 후 백엔드 재시작(변경 즉시 모든 세션 종료).</p>
        <button type="button" className="adm-btn" onClick={onLogout}><LogOut size={15} /> 로그아웃</button>
      </section>
    </div>
  );
}
