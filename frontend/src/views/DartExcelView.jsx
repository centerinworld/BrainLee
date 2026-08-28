/**
 * DartV22Builder 재현 — DART 재무데이터 기반 v22 형식 엑셀 자동 생성 페이지
 */
import React, { useState, useRef } from 'react';
import { API } from '../utils';

const PRESET_STOCKS = [
  { code: '005930', name: '삼성전자' },
  { code: '000660', name: 'SK하이닉스' },
  { code: '035420', name: 'NAVER' },
  { code: '005380', name: '현대차' },
  { code: '051910', name: 'LG화학' },
  { code: '028260', name: '삼성물산' },
  { code: '047810', name: '한국항공우주' },
  { code: '006360', name: 'GS건설' },
  { code: '011170', name: '롯데케미칼' },
  { code: '207940', name: '삼성바이오로직스' },
];

const SHEET_GUIDE = [
  { num: '1', name: '개요_스크리닝판정', desc: '기업개요 + 매출증가/이익증가/흑전 3대 판정' },
  { num: '2', name: '연간손익추이',      desc: '연간 P&L + 이익률 + YoY (억원)' },
  { num: '3', name: '분기손익',          desc: '최근 12분기 단독분기 손익 (누적차감)' },
  { num: '4', name: '재무상태_현금흐름', desc: '연간 BS + 부채비율/ROE/FCF' },
  { num: '5', name: '밸류에이션',        desc: 'TTM 배수 계산기 (노란칸 직접 입력)' },
  { num: '6', name: '장기분기손익',      desc: '전체 수집분기 장기 시계열' },
  { num: '7', name: '장기분기재무',      desc: '분기별 BS 전체 시계열' },
  { num: '8', name: '원문메모',          desc: '데이터 처리 기록 · account_id 매핑 명세' },
];

export default function DartExcelView() {
  const [inputCode, setInputCode]   = useState('');
  const [inputName, setInputName]   = useState('');
  const [yearsBack, setYearsBack]   = useState(5);
  const [jobs, setJobs]             = useState([]);  // [{job_id, stock_code, stock_name, status, step, ...}]
  const [searchResults, setSearchResults] = useState([]);
  const [searching, setSearching]   = useState(false);
  const pollRefs = useRef({});

  // ── 종목 검색 ─────────────────────────────────────────────────
  const handleSearch = async (q) => {
    setInputName(q);
    if (q.length < 2) { setSearchResults([]); return; }
    setSearching(true);
    try {
      const r = await fetch(API(`/api/search?q=${encodeURIComponent(q)}&limit=8`));
      if (r.ok) {
        const d = await r.json();
        setSearchResults(d.results || d || []);
      }
    } catch (e) {}
    setSearching(false);
  };

  const selectStock = (code, name) => {
    setInputCode(code);
    setInputName(name);
    setSearchResults([]);
  };

  // ── 작업 시작 ─────────────────────────────────────────────────
  const startBuild = async (code, name) => {
    if (!code) return;
    try {
      const r = await fetch(
        API(`/api/dart-excel/build?stock_code=${code}&years_back=${yearsBack}`),
        { method: 'POST' }
      );
      const d = await r.json();
      if (!d.job_id) { alert('작업 시작 실패'); return; }

      const job = { job_id: d.job_id, stock_code: code, stock_name: name || code,
                    status: 'queued', step: '대기 중...', years: d.years };
      setJobs(prev => [job, ...prev]);
      pollStatus(d.job_id);
    } catch (e) { alert('오류: ' + e.message); }
  };

  // ── 폴링 ──────────────────────────────────────────────────────
  const pollStatus = (job_id) => {
    const poll = async () => {
      try {
        const r = await fetch(API(`/api/dart-excel/status/${job_id}`));
        const d = await r.json();
        setJobs(prev => prev.map(j => j.job_id === job_id ? { ...j, ...d } : j));
        if (d.status === 'running' || d.status === 'queued') {
          pollRefs.current[job_id] = setTimeout(poll, 1500);
        }
      } catch (e) {}
    };
    pollRefs.current[job_id] = setTimeout(poll, 800);
  };

  const downloadExcel = (job_id, filename) => {
    const a = document.createElement('a');
    a.href = API(`/api/dart-excel/download/${job_id}`);
    a.download = filename || 'dart_v22.xlsx';
    a.click();
  };

  const statusColor = (s) => {
    if (s === 'done')    return '#34d399';
    if (s === 'error')   return '#f87171';
    if (s === 'running') return '#fbbf24';
    return '#94a3b8';
  };
  const statusLabel = (s) => {
    if (s === 'done')    return '✅ 완료';
    if (s === 'error')   return '❌ 오류';
    if (s === 'running') return '⏳ 수집중';
    if (s === 'queued')  return '🕐 대기';
    return s;
  };

  return (
    <div style={{ padding: '1.5rem', maxWidth: '1000px', margin: '0 auto' }}>

      {/* ── 헤더 ─────────────────────────────────────────────── */}
      <div style={{ marginBottom: '1.5rem' }}>
        <h2 style={{ color: '#e2e8f0', fontSize: '1.25rem', fontWeight: 700, marginBottom: '0.4rem' }}>
          📊 DART v22 엑셀 빌더
        </h2>
        <p style={{ color: '#94a3b8', fontSize: '0.85rem', lineHeight: 1.6 }}>
          DART OpenAPI에서 재무데이터를 직접 수집해 <strong style={{color:'#60a5fa'}}>v22 형식</strong> 엑셀을 자동 생성합니다.
          <br/>단독분기 자동계산 · 연간/분기 8시트 구성 · 파생수식 포함
        </p>
      </div>

      {/* ── 시트 구성 안내 ────────────────────────────────────── */}
      <div style={{ background: 'rgba(30,41,59,0.6)', border: '1px solid rgba(71,85,105,0.5)',
          borderRadius: '12px', padding: '1rem 1.2rem', marginBottom: '1.5rem' }}>
        <div style={{ fontWeight: 700, color: '#cbd5e1', marginBottom: '0.7rem', fontSize: '0.9rem' }}>
          📋 생성 시트 구성 (8개)
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))', gap: '0.4rem' }}>
          {SHEET_GUIDE.map(s => (
            <div key={s.num} style={{ display: 'flex', gap: '0.5rem', fontSize: '0.8rem', color: '#94a3b8' }}>
              <span style={{ color: '#60a5fa', fontWeight: 700, minWidth: '14px' }}>{s.num}.</span>
              <span><span style={{ color: '#e2e8f0' }}>{s.name}</span> — {s.desc}</span>
            </div>
          ))}
        </div>
      </div>

      {/* ── 입력 폼 ───────────────────────────────────────────── */}
      <div style={{ background: 'rgba(30,41,59,0.8)', border: '1px solid rgba(71,85,105,0.5)',
          borderRadius: '14px', padding: '1.4rem', marginBottom: '1.5rem' }}>
        <div style={{ fontWeight: 700, color: '#cbd5e1', marginBottom: '1rem' }}>종목 선택</div>

        <div style={{ display: 'flex', gap: '0.8rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
          {/* 종목명 검색 */}
          <div style={{ position: 'relative', flex: '1', minWidth: '200px' }}>
            <input
              type="text"
              placeholder="종목명 검색 (예: 삼성전자)"
              value={inputName}
              onChange={e => handleSearch(e.target.value)}
              style={{ width: '100%', padding: '0.6rem 0.9rem', borderRadius: '8px',
                background: 'rgba(15,23,42,0.8)', border: '1px solid rgba(71,85,105,0.6)',
                color: '#e2e8f0', fontSize: '0.88rem', outline: 'none' }}
            />
            {searchResults.length > 0 && (
              <div style={{ position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 99,
                  background: '#1e293b', border: '1px solid #334155', borderRadius: '8px',
                  boxShadow: '0 8px 24px rgba(0,0,0,0.5)', marginTop: '4px' }}>
                {searchResults.map(s => (
                  <div key={s.stock_code || s.code}
                    onClick={() => selectStock(s.stock_code || s.code, s.stock_name || s.name)}
                    style={{ padding: '0.6rem 1rem', cursor: 'pointer', borderBottom: '1px solid #334155',
                      fontSize: '0.85rem', color: '#e2e8f0', transition: 'background 0.15s' }}
                    onMouseEnter={e => e.target.style.background='rgba(99,102,241,0.2)'}
                    onMouseLeave={e => e.target.style.background='transparent'}>
                    <strong>{s.stock_name || s.name}</strong>
                    <span style={{ color: '#64748b', marginLeft: '0.5rem' }}>{s.stock_code || s.code}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* 종목코드 직접 입력 */}
          <input
            type="text"
            placeholder="코드 직접입력 (6자리)"
            value={inputCode}
            onChange={e => setInputCode(e.target.value.trim())}
            maxLength={6}
            style={{ width: '150px', padding: '0.6rem 0.9rem', borderRadius: '8px',
              background: 'rgba(15,23,42,0.8)', border: '1px solid rgba(71,85,105,0.6)',
              color: '#e2e8f0', fontSize: '0.88rem', outline: 'none' }}
          />

          {/* 수집기간 */}
          <select value={yearsBack} onChange={e => setYearsBack(Number(e.target.value))}
            style={{ padding: '0.6rem 0.9rem', borderRadius: '8px', border: '1px solid rgba(71,85,105,0.6)',
              background: 'rgba(15,23,42,0.8)', color: '#e2e8f0', fontSize: '0.85rem' }}>
            {[3,4,5,7,10].map(n => <option key={n} value={n}>{n}년치</option>)}
          </select>

          <button
            onClick={() => startBuild(inputCode, inputName)}
            disabled={!inputCode || inputCode.length !== 6}
            style={{ padding: '0.6rem 1.6rem', borderRadius: '8px', border: 'none',
              background: inputCode.length === 6 ? '#4f46e5' : '#334155',
              color: '#fff', fontWeight: 700, cursor: inputCode.length === 6 ? 'pointer' : 'not-allowed',
              fontSize: '0.9rem', transition: 'background 0.2s' }}>
            📥 엑셀 생성
          </button>
        </div>

        {/* 빠른 선택 프리셋 */}
        <div style={{ display: 'flex', gap: '0.4rem', flexWrap: 'wrap' }}>
          <span style={{ color: '#64748b', fontSize: '0.78rem', alignSelf: 'center' }}>빠른 선택:</span>
          {PRESET_STOCKS.map(s => (
            <button key={s.code} onClick={() => { selectStock(s.code, s.name); }}
              style={{ padding: '0.25rem 0.6rem', borderRadius: '6px', border: '1px solid rgba(71,85,105,0.5)',
                background: inputCode === s.code ? 'rgba(79,70,229,0.3)' : 'transparent',
                color: '#94a3b8', cursor: 'pointer', fontSize: '0.75rem', transition: 'all 0.15s' }}>
              {s.name}
            </button>
          ))}
        </div>
      </div>

      {/* ── 작업 목록 ──────────────────────────────────────────── */}
      {jobs.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>
          <div style={{ fontWeight: 700, color: '#cbd5e1', fontSize: '0.9rem' }}>생성 이력</div>
          {jobs.map(job => (
            <div key={job.job_id} style={{
                background: 'rgba(30,41,59,0.8)', border: `1px solid ${
                  job.status === 'done' ? 'rgba(52,211,153,0.3)'
                  : job.status === 'error' ? 'rgba(248,113,113,0.3)'
                  : 'rgba(71,85,105,0.4)'}`,
                borderRadius: '12px', padding: '1rem 1.2rem' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ display: 'flex', gap: '0.8rem', alignItems: 'center' }}>
                  <span style={{ fontWeight: 700, color: '#e2e8f0' }}>{job.stock_name}</span>
                  <span style={{ color: '#64748b', fontSize: '0.8rem' }}>{job.stock_code}</span>
                  {job.years && (
                    <span style={{ color: '#64748b', fontSize: '0.75rem' }}>
                      {job.years[0]}~{job.years[job.years.length-1]}
                    </span>
                  )}
                  <span style={{ color: statusColor(job.status), fontWeight: 700, fontSize: '0.82rem' }}>
                    {statusLabel(job.status)}
                  </span>
                </div>
                {job.status === 'done' && (
                  <button
                    onClick={() => downloadExcel(job.job_id, job.filename)}
                    style={{ padding: '0.4rem 1.1rem', borderRadius: '8px', border: 'none',
                      background: '#059669', color: '#fff', fontWeight: 700, cursor: 'pointer',
                      fontSize: '0.85rem' }}>
                    ⬇ xlsx 다운로드
                  </button>
                )}
              </div>

              {/* 진행 단계 */}
              {(job.status === 'running' || job.status === 'queued') && (
                <div style={{ marginTop: '0.5rem' }}>
                  <div style={{ color: '#fbbf24', fontSize: '0.82rem', marginBottom: '0.3rem' }}>
                    {job.step || '처리 중...'}
                  </div>
                  <div style={{ height: '4px', background: '#1e293b', borderRadius: '2px', overflow: 'hidden' }}>
                    <div style={{ height: '100%', background: 'linear-gradient(90deg,#4f46e5,#7c3aed)',
                        width: '100%', animation: 'pulse 1.5s ease-in-out infinite' }} />
                  </div>
                </div>
              )}

              {/* 완료 요약 */}
              {job.status === 'done' && (
                <div style={{ marginTop: '0.5rem', display: 'flex', gap: '1rem', fontSize: '0.8rem', color: '#94a3b8' }}>
                  <span>📅 수집연도: {(job.annual_years || []).join(', ')}</span>
                  <span>📊 분기수: {job.quarterly_count}개</span>
                  <span>📄 파일: {job.filename}</span>
                </div>
              )}

              {/* 오류 */}
              {job.status === 'error' && (
                <div style={{ marginTop: '0.4rem', color: '#f87171', fontSize: '0.82rem' }}>
                  {job.msg}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* ── 데이터 규칙 안내 ─────────────────────────────────── */}
      <div style={{ marginTop: '2rem', background: 'rgba(30,41,59,0.5)', border: '1px solid rgba(71,85,105,0.3)',
          borderRadius: '12px', padding: '1rem 1.2rem', fontSize: '0.8rem', color: '#94a3b8', lineHeight: 1.8 }}>
        <div style={{ fontWeight: 700, color: '#cbd5e1', marginBottom: '0.5rem' }}>📌 데이터 처리 규칙 (DartV22Builder 원칙)</div>
        <ul style={{ margin: 0, paddingLeft: '1.2rem' }}>
          <li>분기 단독값 = 누적 차감 (2Q=반기-1Q, 4Q=연간-3Q누적)</li>
          <li>DART fnlttSinglAcntAll API — CFS(연결) 우선, OFS(별도) fallback</li>
          <li>CAPEX 부호: 절댓값(ABS) 정규화 — 지출 양수 표기</li>
          <li>단위: 원 → 억원 자동 변환 (/1e8). 수주잔고는 억원 그대로.</li>
          <li>정정공시: DART API 최신 접수 기준 자동 반영</li>
          <li>공시된 사실만 기재. 추정·전망·투자의견 없음.</li>
        </ul>
      </div>

      <style>{`
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }
      `}</style>
    </div>
  );
}
