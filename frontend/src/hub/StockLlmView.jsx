import React from 'react';
import { ExternalLink } from 'lucide-react';
import { LLM_PATH } from './modules';

/**
 * Stock LLM (2026-09-27) — Brian_RAG(:8888)를 이 앱과 같은 AppBar+왼쪽 사이드바 셸 안에서 연다.
 * 실제 화면은 서버 프록시(routes/llm_proxy.py, 관리자 로그인 필요)가 그대로 서빙하는 Brian_RAG 페이지를 iframe으로 담는다 —
 * hub/AdminKeyIndicator.jsx 와 같은 패턴. 로그인이 안 돼 있으면 그 안에서 관리자 로그인 화면으로 안내된다.
 */
export default function StockLlmView() {
  return (
    <div className="ki-embed">
      <div className="sm-top">
        <span className="sm-muted">Brian_RAG 하이브리드 RAG 에이전트 — 로그인 창이 뜨면 관리자 비밀번호를 입력하세요.</span>
        <span className="sm-spacer" />
        <a className="adm-btn" href={LLM_PATH} target="_blank" rel="noopener noreferrer"><ExternalLink size={14} /> 새 탭에서 열기</a>
      </div>
      <iframe title="Stock LLM" src={LLM_PATH} className="ki-frame" />
    </div>
  );
}
