import React, { useState } from 'react';
import { ExternalLink } from 'lucide-react';

/**
 * 관리자 › Key Indicator 관리 (2026-09-27)
 * newsinfo.cloud 의 관리 콘솔(캘린더·RSS·AI 설정·API 키·텔레그램)과 AI 관제 센터(KAI)를 관리자 화면 안으로 옮겼다.
 * newsinfo.cloud 첫 화면은 이제 누구나 보는 공개 화면(주요 경제지표·뉴스정보)이다. 콘솔 로그인은 이 사이트와 같은 관리자 비밀번호를 쓴다.
 */
const origin = () => (/(^|\.)leanguy\.cloud$/.test(window.location.hostname) ? 'https://newsinfo.cloud' : 'http://127.0.0.1:5500');

export default function AdminKeyIndicator() {
  const [view, setView] = useState('console');
  const views = { console: ['관리 콘솔', '/console.html'], kai: ['AI 관제 센터 (KAI)', '/kai/'] };
  const src = origin() + views[view][1];
  return (
    <div className="ki-embed">
      <div className="sm-top">
        <div className="sm-tabs" role="tablist" style={{ borderBottom: 'none' }}>
          {Object.entries(views).map(([k, [label]]) => <button key={k} role="tab" aria-selected={view === k} className={view === k ? 'on' : ''} onClick={() => setView(k)}>{label}</button>)}
        </div>
        <span className="sm-muted">로그인 창이 뜨면 <b>관리자 비밀번호</b>를 입력하세요.</span>
        <span className="sm-spacer" />
        <a className="adm-btn" href={src} target="_blank" rel="noopener noreferrer"><ExternalLink size={14} /> 새 탭에서 열기</a>
      </div>
      <iframe key={view} title={views[view][0]} src={src} className="ki-frame" />
    </div>
  );
}
