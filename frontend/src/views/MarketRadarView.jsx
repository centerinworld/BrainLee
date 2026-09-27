/* light-theme-codemod-2026-09-27 */
/* light-theme-codemod-pass2-2026-09-27 */
/* light-theme-codemod-pass3-2026-09-27 */
/* light-theme-codemod-pass4-2026-09-27 */
/* light-theme-codemod-pass5-2026-09-27 */
/* light-theme-codemod-pass6-2026-09-27 */
/* light-theme-codemod-pass7-2026-09-27 */
import React from 'react';
import { API } from '../utils.js';

// 2026-09-27: 반도체 외 14개 섹터도 「반도체 섹터」처럼 각자 메뉴에서 바로 열리도록 initialSector를 받는다(App.jsx 섹터별 메뉴 항목이 전달).
// 이 페이지 자체는 이전부터 15개 섹터를 모두 다루고 있었다(위쪽 섹터 탭에서 전환 가능) — 처음 여는 섹터만 메뉴에 따라 달라진다.
const MarketRadarView = React.memo(({ initialSector = 'semiconductor' } = {}) => {
  const RADAR_SECTORS = [
    { key: 'semiconductor', name: '반도체/IT',     emoji: '💾' },
    { key: 'battery',       name: '2차전지',        emoji: '🔋' },
    { key: 'power_infra',   name: '전력산업',       emoji: '⚡' },
    { key: 'nuclear',       name: '원자력',          emoji: '☢️' },
    { key: 'defense',       name: '방산',            emoji: '🚀' },
    { key: 'construction',  name: '산업재/건설',     emoji: '🏗️' },
    { key: 'shipbuilding',  name: '조선',            emoji: '🚢' },
    { key: 'shipping',      name: '해운',            emoji: '🛳️' },
    { key: 'automotive',    name: '자동차',          emoji: '🚗' },
    { key: 'pharma',        name: '바이오/헬스케어', emoji: '💊' },
    { key: 'energy',        name: '소재/화학',       emoji: '⛽' },
    { key: 'steel',         name: '철강/비철금속',   emoji: '⚙️' },
    { key: 'it_hardware',   name: 'IT/하드웨어',     emoji: '💻' },
    { key: 'telecom',       name: '통신/플랫폼',     emoji: '📡' },
    { key: 'finance',       name: '금융/지주',       emoji: '🏦' },
    // 2026-09-27(사용자 지시): "섹터 로테이션"에는 있었는데 이 화면엔 없던 3개 — 신규 추가(재정리).
    { key: 'pcb_package',   name: '기판/패키지',     emoji: '🔌' },
    { key: 'beauty',        name: '화장품/뷰티',     emoji: '🧴' },
    { key: 'medbeauty',     name: '의료기기/미용',   emoji: '🩺' },
  ];
  const [activeSector, setActiveSector] = React.useState(initialSector);
  const [data,      setData]      = React.useState(null);
  const [loading,   setLoading]   = React.useState(false);
  const [error,     setError]     = React.useState('');
  const [importing, setImporting] = React.useState(false);
  const fileInputRef = React.useRef(null);

  const load = React.useCallback(async (sector) => {
    setLoading(true); setError('');
    try {
      const r = await fetch(API(`/api/market-radar/sector/${sector}/detail`));
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setData(await r.json());
    } catch(e) { setError('데이터 로드 실패: ' + e.message); }
    finally { setLoading(false); }
  }, []);

  React.useEffect(() => { load(activeSector); }, [activeSector, load]);
  // 2026-09-27: 「주도섹터 진입 신호」 표는 섹터 로테이션 페이지(routes/sector_rotation.py leadership 탭)의 정본과 중복이라 이 페이지에서 뺐다 — 자세히 보려면 섹터 로테이션으로.

  // 2026-07-29: 미국 섹터 바스켓 오버나잇 방향성 신호 — 워크포워드 검증 완료
  // (반도체 학습62.7%/검증60.2%, 자동차/헬스케어/금융/소재/산업재도 전부 방향일치 확인,
  // signal_experiment_ledger 'discovery_tools/us_overnight_sector_leadlag_20260729' 참조).
  // 미국 장마감(한국시간 새벽) 정보가 한국 개장 전 확정되므로 룩어헤드 없음.
  const [usSignals, setUsSignals] = React.useState(null);
  React.useEffect(() => {
    let alive = true;
    fetch(API('/api/market-radar/sector-us-overnight-signals'))
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (alive && d) setUsSignals(d); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);
  // 2026-09-27(사용자 지시): 섹터 탭을 "미국 위/한국 아래" 비교 박스 그리드로 재구성하려면 전 섹터(15개)의 한국 평균등락이 필요 —
  // 기존엔 활성 섹터 1개의 상세(data)만 있었다. /all 은 15개 섹터 요약(코드에서 이미 쓰는 avg_1d)을 한 번에 준다.
  const [allSummary, setAllSummary] = React.useState(null);
  React.useEffect(() => {
    let alive = true;
    fetch(API('/api/market-radar/all'))
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (alive && d) setAllSummary(d); })
      .catch(() => {});
    return () => { alive = false; };
  }, []);
  // 미국 오버나잇 신호는 워크포워드 검증된 6개 섹터만 키 체계가 다르다(auto_ev/healthcare/financials/materials/industrials) — 섹터 탭 키로 매핑.
  // 2026-09-27 추가된 12개 참고용(validated:false) 바스켓은 API 키를 섹터 탭 키와 이미 동일하게 맞췄으므로 매핑 없이 그대로 사용.
  const US_KEY_TO_RADAR_KEY = { semiconductor: 'semiconductor', auto_ev: 'automotive', healthcare: 'pharma', financials: 'finance', materials: 'energy', industrials: 'construction' };
  const usBySectorKey = React.useMemo(() => {
    const m = {};
    (usSignals?.sectors || []).forEach(s => { const rk = US_KEY_TO_RADAR_KEY[s.key] || s.key; if (s.available) m[rk] = s; });
    return m;
  }, [usSignals]);
  const krBySectorKey = React.useMemo(() => {
    const m = {};
    (allSummary?.sectors || []).forEach(s => { m[s.key] = s; });
    return m;
  }, [allSummary]);
  // 핫한 섹터가 한눈에 보이도록 한국 평균등락(없으면 미국) 내림차순 정렬
  const sortedSectors = React.useMemo(() => {
    const heat = (s) => krBySectorKey[s.key]?.avg_1d ?? usBySectorKey[s.key]?.us_basket_ret_pct ?? -999;
    return [...RADAR_SECTORS].sort((a, b) => heat(b) - heat(a));
  }, [krBySectorKey, usBySectorKey]);
  const heatBg = (v) => {
    if (v == null) return 'transparent';
    const a = Math.min(Math.abs(v) / 2.5, 1); // ±2.5% 이상이면 최대 강도
    return v > 0 ? `rgba(220,38,38,${0.06 + a * 0.22})` : v < 0 ? `rgba(37,99,235,${0.06 + a * 0.22})` : 'transparent';
  };

  /* ── 포맷터 ──────────────────────────────────────────────────── */
  /* 시총: 국가별 통화기호 포함 (KR=조원/억원, JP=¥T/B, TW=NT$B, 기타=$T/B) */
  const fmtMktCap = (v_krw) => {
    if (v_krw == null || v_krw === 0) return '-';
    const n = Number(v_krw);
    if (isNaN(n) || n <= 0) return '-';
    if (n >= 1e12) return `${(n/1e12).toFixed(1)}조원`;
    if (n >= 1e8)  return `${Math.round(n/1e8).toLocaleString('ko-KR')}억원`;
    return Math.round(n/1e6).toLocaleString('ko-KR') + '백만원';
  };

  /* 주가: 통화기호 없이 숫자만 (국가 플래그로 통화 구별) */
  const fmtP = (price, country) => {
    if (price == null) return null;
    const n = Number(price);
    if (country === 'KR') return n.toLocaleString('ko-KR', {maximumFractionDigits:0});
    if (country === 'JP') return n.toLocaleString('en-US', {maximumFractionDigits:0});
    return n.toLocaleString('en-US', {maximumFractionDigits:2});
  };
  const fmtPct = (v, digits = 1) => v == null ? '-' : `${Number(v) > 0 ? '+' : ''}${Number(v).toFixed(digits)}%`;
  const fmtEok = (v) => v == null ? '-' : `${Number(v) > 0 ? '+' : ''}${Math.round(Number(v)).toLocaleString('ko-KR')}억`;
  const stageStyle = (stage) => ({
    ENTRY_NOW:   { bg:'rgba(22,163,74,0.16)',  border:'#15803d', color:'#15803d' },
    EARLY_WATCH: { bg:'rgba(217,119,6,0.16)', border:'#b45309', color:'#b45309' },
    HOLD_LEADER: { bg:'rgba(37,99,235,0.14)', border:'#2563eb', color:'#2563eb' },
    WAIT:        { bg:'rgba(100,116,139,0.10)',border:'#1e293b', color:'#334155' },
    AVOID:       { bg:'rgba(220,38,38,0.12)',  border:'#dc2626', color:'#dc2626' },
  }[stage] || { bg:'rgba(100,116,139,0.10)', border:'#1e293b', color:'#334155' });

  /* ── CSV 내보내기 ────────────────────────────────────────────── */
  const handleExport = () => {
    const a = document.createElement('a');
    a.href = API(`/api/market-radar/export-csv?sector=${activeSector}`);
    a.download = `${activeSector}_radar.csv`;
    a.click();
  };

  /* ── CSV 가져오기 ────────────────────────────────────────────── */
  const handleImport = async (e) => {
    const file = e.target.files?.[0]; if (!file) return;
    setImporting(true);
    const form = new FormData();
    form.append('file', file);
    form.append('sector', activeSector);
    try {
      const r   = await fetch(API('/api/market-radar/import-csv'), { method:'POST', body:form });
      const res = await r.json();
      if (r.ok) { alert(`${res.inserted||0}건 추가, ${res.updated||0}건 수정됨`); load(activeSector); }
      else alert('가져오기 실패: ' + (res.detail || r.statusText));
    } catch(e2) { alert('오류: ' + e2.message); }
    finally { setImporting(false); if (fileInputRef.current) fileInputRef.current.value=''; }
  };

  /* ── 그룹 빌더: lv2 연속 그룹 → KR / 해외 분리 ──────────────── */
  const buildGroups = (stocks) => {
    if (!stocks?.length) return [];
    const result = [];
    let i = 0;
    while (i < stocks.length) {
      const lv2 = (stocks[i].lv2 || '').trim();
      if (!lv2) {
        result.push({ lv2:'', single: stocks[i] });
        i++; continue;
      }
      let j = i;
      while (j < stocks.length && (stocks[j].lv2||'').trim() === lv2) j++;
      const grp  = stocks.slice(i, j);
      const ovs  = grp.filter(s => s.country !== 'KR');
      const kr   = grp.filter(s => s.country === 'KR');
      const lv2_view = grp.find(s => s.lv2_view)?.lv2_view || null;
      result.push({ lv2, kr, ovs, total: grp.length, lv2_view });
      i = j;
    }
    return result;
  };

  /* lv2 그룹의 신호 집계 (과반수) */
  const gSig = (stocks) => {
    if (!stocks?.length) return { sig_5d:'neutral', sig_10d:'neutral', sig_30d:'neutral' };
    const vote = (k) => {
      const u = stocks.filter(s => s[k] === 'up').length;
      const d = stocks.filter(s => s[k] === 'dn').length;
      return u > d ? 'up' : d > u ? 'dn' : 'neutral';
    };
    return { sig_5d: vote('sig_5d'), sig_10d: vote('sig_10d'), sig_30d: vote('sig_30d') };
  };

  /* 신호 점 3개 (5/10/30일) */
  const SigDots = ({ gs }) => gs ? (
    <span style={{display:'flex', gap:'3px', alignItems:'center', justifyContent:'center'}}>
      <span style={{color: gs.sig_5d  === 'up' ? '#dc2626' : gs.sig_5d  === 'dn' ? '#2563eb' : 'var(--text-secondary)', fontSize:'0.82rem'}}>●</span>
      <span style={{fontSize:'0.6rem', color:'var(--text-secondary)'}}>5</span>
      <span style={{color: gs.sig_10d === 'up' ? '#dc2626' : gs.sig_10d === 'dn' ? '#2563eb' : 'var(--text-secondary)', fontSize:'0.82rem'}}>●</span>
      <span style={{fontSize:'0.6rem', color:'var(--text-secondary)'}}>10</span>
      <span style={{color: gs.sig_30d === 'up' ? '#dc2626' : gs.sig_30d === 'dn' ? '#2563eb' : 'var(--text-secondary)', fontSize:'0.82rem'}}>●</span>
      <span style={{fontSize:'0.6rem', color:'var(--text-secondary)'}}>30</span>
    </span>
  ) : null;

  /* ── 스타일 상수 ─────────────────────────────────────────────── */
  const thSt = {
    padding:'0.4rem 0.5rem', fontSize:'0.7rem', color:'var(--text-secondary)',
    fontWeight:600, whiteSpace:'nowrap', background:'rgba(0,0,0,0.3)',
    borderBottom:'1px solid var(--glass-border)',
  };
  const tdSt = {
    padding:'0.32rem 0.5rem', fontSize:'0.78rem',
    borderBottom:'1px solid rgba(15,23,42,0.2)', verticalAlign:'middle',
  };
  /* Level2 구분선: 파란 좌측 테두리 */
  const lv2TdSt = {
    ...tdSt, fontSize:'0.7rem', color:'var(--text-secondary)', verticalAlign:'middle',
    background:'rgba(37,99,235,0.06)',
    borderLeft:'3px solid rgba(37,99,235,0.7)',
    borderRight:'1px solid rgba(37,99,235,0.15)',
    paddingLeft:'0.65rem',
  };
  const sigTdSt = {
    ...tdSt, textAlign:'center', verticalAlign:'middle',
    background:'rgba(37,99,235,0.03)',
  };
  /* Level2 그룹 간 구분선 — 굵고 뚜렷한 파란 실선 */
  const LV2_BORDER  = '2px solid rgba(37,99,235,0.85)';
  /* 해외 ↔ KR 구분선 (Level2 내부) — LV2_BORDER보다 얇지만 뚜렷한 실선 */
  const KR_OVS_BORDER = '1.5px solid rgba(80,140,255,0.75)';

  /* 주가 셀: 가격 위 / % 아래 */
  const PCell = ({ price, chg, bold, country }) => (
    <td style={{...tdSt, textAlign:'right'}}>
      {price != null ? (
        <div style={{display:'flex', flexDirection:'column', alignItems:'flex-end', gap:'1px'}}>
          <span style={{fontWeight:bold?700:500, fontSize:'0.78rem', whiteSpace:'nowrap'}}>{fmtP(price, country)}</span>
          {chg != null
            ? <span style={{fontSize:'0.67rem', fontWeight:600, color: chg>0?'#dc2626':chg<0?'#2563eb':'var(--text-secondary)'}}>
                {chg>0?'+':''}{chg.toFixed(1)}%
              </span>
            : <span style={{fontSize:'0.67rem', color:'var(--text-secondary)'}}>-</span>}
        </div>
      ) : <span style={{color:'var(--text-secondary)'}}>-</span>}
    </td>
  );

  /* PBR/PER 셀 */
  const ValCell = ({ v, isPbr }) => (
    <td style={{...tdSt, textAlign:'right', fontSize:'0.72rem'}}>
      {v != null
        ? <span style={{color: isPbr && v<1 ? '#047857' : 'var(--text-secondary)'}}>{v.toFixed(isPbr?2:1)}</span>
        : <span style={{color:'rgba(15,23,42,0.88)', fontSize:'0.68rem'}}>-</span>}
    </td>
  );

  /* 개별 종목 앞 3개 셀 (국가, 종목명, 시총) */
  const StockDataCells = ({ s }) => {
    const tip = [s.name, s.lv2 ? `[${s.lv2}]` : '', s.desc||''].filter(Boolean).join(' — ');
    return <>
      <td style={{...tdSt, textAlign:'center', fontSize:'1.1rem', lineHeight:1}}>{s.country_flag||s.country}</td>
      <td style={{...tdSt, fontWeight:600, whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis'}}
          title={tip}>{s.name}</td>
      <td style={{...tdSt, textAlign:'right', fontSize:'0.72rem', color:'var(--text-secondary)', whiteSpace:'nowrap'}}>
        {fmtMktCap(s.market_cap_krw ?? s.market_cap)}
      </td>
    </>;
  };

  const PriceCells = ({ s }) => <>
    <PCell price={s.price}     chg={s.chg_1d}  bold={true} country={s.country} />
    <PCell price={s.price_5d}  chg={s.chg_5d}  country={s.country} />
    <PCell price={s.price_10d} chg={s.chg_10d} country={s.country} />
    <PCell price={s.price_30d} chg={s.chg_30d} country={s.country} />
    <PCell price={s.price_1y}  chg={s.chg_1y}  country={s.country} />
    <ValCell v={s.pbr} isPbr={true} />
    <ValCell v={s.per} isPbr={false} />
  </>;

  /* ── 섹션 행 렌더 ─────────────────────────────────────────────── */
  const renderGroups = (groups) => groups.flatMap((g, gi) => {
    /* Level2 그룹 경계: 굵은 파란 실선 */
    const groupBorderTop = gi > 0 ? LV2_BORDER : undefined;

    /* lv2 없는 개별 종목 */
    if (g.single) {
      const s = g.single;
      return [(
        <tr key={s.symbol} style={{borderTop: groupBorderTop}}
            onMouseOver={e=>e.currentTarget.style.background='rgba(15,23,42,0.03)'}
            onMouseOut={e=>e.currentTarget.style.background='transparent'}>
          <StockDataCells s={s}/>
          <td style={{...lv2TdSt}}>-</td>
          <td style={{...sigTdSt}}><SigDots gs={gSig([s])}/></td>
          <PriceCells s={s}/>
        </tr>
      )];
    }

    const { lv2, kr, ovs, total, lv2_view } = g;
    const hasKR  = kr.length  > 0;
    const hasOvs = ovs.length > 0;
    const krSig  = hasKR  ? gSig(kr)  : null;
    const ovsSig = hasOvs ? gSig(ovs) : null;

    const rows = [];

    /* LV2 설명 행 (있을 때만) */
    if (lv2_view) {
      rows.push(
        <tr key={`${lv2}-lv2desc`} style={{borderTop: groupBorderTop}}>
          <td colSpan={12} style={{
            padding: '0.28rem 1rem 0.3rem 1.4rem',
            background: 'rgba(79,70,229,0.07)',
            borderBottom: '1px solid rgba(79,70,229,0.2)',
            fontSize: '0.72rem', color: 'rgba(29,78,216,0.9)',
            lineHeight: 1.5, fontStyle: 'italic',
          }}>
            📌 <span style={{fontWeight:600, color:'rgba(29,78,216,1)', marginRight:'0.3rem'}}>{lv2}</span>{lv2_view}
          </td>
        </tr>
      );
    }

    /* 해외 행 먼저 (lv2 rowspan 여기서 시작) */
    ovs.forEach((s, oi) => {
      const isFirst = oi === 0;
      rows.push(
        <tr key={s.symbol}
            style={{borderTop: isFirst && !lv2_view ? groupBorderTop : undefined}}
            onMouseOver={e=>e.currentTarget.style.background='rgba(15,23,42,0.03)'}
            onMouseOut={e=>e.currentTarget.style.background='transparent'}>
          <StockDataCells s={s}/>
          {/* lv2 셀: 그룹 전체 rowspan — 첫 해외 행에만 */}
          {isFirst && (
            <td rowSpan={total} style={{...lv2TdSt, verticalAlign:'middle'}}>{lv2}</td>
          )}
          {/* 해외 신호 셀 */}
          {isFirst && (
            <td rowSpan={ovs.length} style={{...sigTdSt}}><SigDots gs={ovsSig}/></td>
          )}
          <PriceCells s={s}/>
        </tr>
      );
    });

    /* KR 행 */
    kr.forEach((s, ki) => {
      const isFirst = ki === 0;
      rows.push(
        <tr key={s.symbol}
            style={{
              borderTop: isFirst && !hasOvs && !lv2_view ? groupBorderTop : undefined,
              background: 'rgba(22,163,74,0.03)',  /* KR 행 연한 녹색 배경 */
            }}
            onMouseOver={e=>e.currentTarget.style.background='rgba(22,163,74,0.08)'}
            onMouseOut={e=>e.currentTarget.style.background='rgba(22,163,74,0.03)'}>
          <StockDataCells s={s}/>
          {/* lv2 셀: 해외 없을 때만 */}
          {isFirst && !hasOvs && (
            <td rowSpan={kr.length} style={{...lv2TdSt, verticalAlign:'middle'}}>{lv2}</td>
          )}
          {/* KR 신호 셀 */}
          {isFirst && (
            <td rowSpan={kr.length} style={{...sigTdSt, background:'rgba(22,163,74,0.05)'}}><SigDots gs={krSig}/></td>
          )}
          <PriceCells s={s}/>
        </tr>
      );
    });

    return rows;
  });

  /* ── 렌더 ────────────────────────────────────────────────────── */
  /* ── sticky 헤더 높이 상수 (tabs + thead) ──────────────────────── */
  const STICKY_TABS_H  = 86;  /* px: 제목행 + 탭바 합산 */
  const STICKY_HEAD_H  = STICKY_TABS_H + 34; /* + thead */
  const STICKY_SECT_H  = STICKY_HEAD_H + 34; /* + 섹션 타이틀 */

  return (
    <div style={{padding:'0 0.5rem'}}>
      {/* ── sticky 영역: 제목행 + 탭 ─────────────────────────────── */}
      <div style={{
        position:'sticky', top:0, zIndex:40,
        background:'rgba(255,255,255,0.97)', backdropFilter:'blur(14px)',
        margin:'0 -0.5rem', padding:'0.5rem 0.5rem 0.4rem',
        borderBottom:'1px solid rgba(37,99,235,0.18)',
      }}>
        {/* 제목 + CSV 버튼 */}
        <div style={{display:'flex', alignItems:'center', justifyContent:'space-between', gap:'0.8rem', marginBottom:'0.5rem', flexWrap:'wrap'}}>
          <div style={{display:'flex', alignItems:'center', gap:'0.8rem'}}>
            <h2 style={{margin:0, fontSize:'1.05rem', fontWeight:700}}>🛰 섹터 분류</h2>
            <span style={{fontSize:'0.78rem', color:'var(--text-secondary)'}}>
              글로벌 선행지표 — 해외 대표 기업 시세로 섹터 방향성 포착
            </span>
          </div>
          <div style={{display:'flex', gap:'0.5rem', alignItems:'center'}}>
            <button onClick={handleExport} style={{
              padding:'0.28rem 0.7rem', borderRadius:'6px', fontSize:'0.74rem', cursor:'pointer',
              background:'rgba(37,99,235,0.12)', border:'1px solid rgba(37,99,235,0.35)',
              color:'var(--accent-mint)', fontWeight:600,
            }}>⬇ CSV</button>
            <label style={{
              padding:'0.28rem 0.7rem', borderRadius:'6px', fontSize:'0.74rem', cursor:'pointer',
              background:'rgba(217,119,6,0.12)', border:'1px solid rgba(217,119,6,0.35)',
              color:'#b45309', fontWeight:600,
            }}>
              {importing ? '업로드 중…' : '⬆ CSV'}
              <input ref={fileInputRef} type="file" accept=".csv" style={{display:'none'}} onChange={handleImport}/>
            </label>
          </div>
        </div>

        {/* 섹터 히트 그리드 — 박스 하나에 위: 미국(전일, 워크포워드 검증 섹터만) / 아래: 한국(당일 평균등락). 클릭하면 그 섹터로 전환.
            (2026-09-27 사용자 지시: 탭 버튼을 %와 함께 보이게, 미국/한국 한 박스 비교, 핫한 섹터가 한눈에 보이도록 재구성) */}
        <div style={{fontSize:'0.7rem', color:'var(--text-secondary)', marginBottom:'0.3rem'}}>
          🔥 섹터 히트맵 — 위:미국(전일 대표주 평균, <b>검증</b>=워크포워드 방향일치 확인 · <span style={{opacity:0.7}}>참고</span>=대표종목 평균만) · 아래:한국(당일 평균등락) · 붉을수록 상승, 푸를수록 하락 · 한국 등락 높은 순
        </div>
        <div style={{
          display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(112px, 1fr))', gap:'0.4rem',
        }}>
          {sortedSectors.map(s => {
            const us = usBySectorKey[s.key];
            const kr = krBySectorKey[s.key];
            const active = activeSector === s.key;
            const usTitle = us == null ? '미국 데이터 없음'
              : us.validated ? `워크포워드 검증기 방향일치 ${us.backtested_hit_rate.test_pct}%`
              : '대표종목 평균등락(참고용, 워크포워드 검증 없음)';
            return (
              <button key={s.key} onClick={() => setActiveSector(s.key)} title={usTitle} style={{
                textAlign:'left', cursor:'pointer', borderRadius:'10px', padding:'0.4rem 0.5rem',
                border: active ? '2px solid #1a73e8' : '1px solid var(--glass-border)',
                background: heatBg(kr?.avg_1d), transition:'all 0.15s',
              }}>
                <div style={{fontSize:'0.74rem', fontWeight:700, color: active ? '#1a73e8' : 'var(--text-primary)', whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis'}}>
                  {s.emoji} {s.name}
                </div>
                <div style={{display:'flex', justifyContent:'space-between', alignItems:'baseline', marginTop:'0.15rem'}}>
                  <span style={{fontSize:'0.62rem', color:'var(--text-secondary)'}}>미국{us != null && !us.validated ? '·참고' : ''}</span>
                  <span style={{fontSize:'0.78rem', fontWeight:700, opacity: us != null && !us.validated ? 0.75 : 1, color: us == null ? 'var(--text-secondary)' : us.us_basket_ret_pct > 0 ? '#dc2626' : us.us_basket_ret_pct < 0 ? '#2563eb' : 'var(--text-secondary)'}}>
                    {us == null ? '–' : `${us.us_basket_ret_pct >= 0 ? '+' : ''}${us.us_basket_ret_pct}%`}
                  </span>
                </div>
                <div style={{display:'flex', justifyContent:'space-between', alignItems:'baseline'}}>
                  <span style={{fontSize:'0.62rem', color:'var(--text-secondary)'}}>한국</span>
                  <span style={{fontSize:'0.78rem', fontWeight:800, color: kr == null ? 'var(--text-secondary)' : kr.avg_1d > 0 ? '#dc2626' : kr.avg_1d < 0 ? '#2563eb' : 'var(--text-secondary)'}}>
                    {kr == null ? '–' : `${kr.avg_1d >= 0 ? '+' : ''}${kr.avg_1d}%`}
                  </span>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {loading && <div style={{color:'var(--text-secondary)', padding:'2rem', textAlign:'center'}}>로딩 중...</div>}
      {error   && <div style={{color:'#dc2626', padding:'1rem'}}>{error}</div>}


      {!loading && !error && data?.sections?.length === 0 && (
        <div className="glass-panel" style={{padding:'3rem', textAlign:'center', color:'var(--text-secondary)'}}>
          <p>데이터를 불러오는 중입니다. 잠시 후 다시 시도해 주세요.</p>
        </div>
      )}

      {/* ── LV0 섹터 개요 패널 ─────────────────────────────────────── */}
      {!loading && data?.sector_overview && (
        <div style={{
          margin:'0.6rem 0', padding:'0.7rem 1rem',
          background:'rgba(37,99,235,0.07)',
          border:'1px solid rgba(37,99,235,0.25)',
          borderRadius:'8px', fontSize:'0.8rem',
          color:'rgba(29,78,216,0.9)', lineHeight:1.6,
        }}>
          <span style={{fontWeight:700, color:'#2563eb', marginRight:'0.5rem'}}>
            {data.emoji} {data.sector_name} 섹터 개요
          </span>
          {data.sector_overview}
        </div>
      )}

      {/* 단일 테이블 (섹션 경계 = shaded title row) */}
      {!loading && data?.sections?.length > 0 && (
        <>
        {data.updated_date && (
          <div style={{textAlign:'right', marginBottom:'0.3rem', fontSize:'0.7rem',
            color:'rgba(29,78,216,0.9)', paddingRight:'0.3rem'}}>
            가격 업데이트: {data.updated_date}
          </div>
        )}
        <div className="glass-panel" style={{padding:'0', overflowX:'clip'}}>
          <table style={{width:'100%', borderCollapse:'collapse', tableLayout:'fixed'}}>
            <colgroup>
              <col style={{width:'36px'}}/>   {/* 국가 */}
              <col style={{width:'118px'}}/>  {/* 종목명 */}
              <col style={{width:'62px'}}/>   {/* 시총 */}
              <col style={{width:'88px'}}/>   {/* Level2 */}
              <col style={{width:'76px'}}/>   {/* 신호 */}
              <col style={{width:'82px'}}/>   {/* 현재(1일) */}
              <col style={{width:'74px'}}/>   {/* 5일 */}
              <col style={{width:'74px'}}/>   {/* 10일 */}
              <col style={{width:'74px'}}/>   {/* 30일 */}
              <col style={{width:'74px'}}/>   {/* 1년 */}
              <col style={{width:'46px'}}/>   {/* PBR */}
              <col style={{width:'46px'}}/>   {/* PER */}
            </colgroup>
            <thead>
              <tr>
                {[
                  {label:'국가',      align:'center'},
                  {label:'종목명',    align:'left'},
                  {label:'시총',      align:'right'},
                  {label:'Level2',   align:'left', pl:'0.7rem'},
                  {label:'신호',      align:'center'},
                  {label:'현재(1일)', align:'right'},
                  {label:'5일',       align:'right'},
                  {label:'10일',      align:'right'},
                  {label:'30일',      align:'right'},
                  {label:'1년',       align:'right'},
                  {label:'PBR',       align:'right'},
                  {label:'PER',       align:'right'},
                ].map(h => (
                  <th key={h.label} style={{
                    ...thSt, textAlign:h.align,
                    paddingLeft: h.pl || undefined,
                    position:'sticky', top:`${STICKY_TABS_H}px`, zIndex:20,
                    background:'rgba(255,255,255,0.98)',
                    boxShadow:'0 1px 0 rgba(37,99,235,0.3)',
                  }}>{h.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.sections.map((section, si) => (
                <React.Fragment key={section.name}>
                  {/* 섹션 타이틀 행 — 파란 음영 + sticky */}
                  <tr>
                    <td colSpan={12} style={{
                      padding:'0.42rem 0.8rem 0.35rem',
                      background:'rgba(255,255,255,0.97)',
                      backgroundImage:'linear-gradient(rgba(37,99,235,0.13),rgba(37,99,235,0.13))',
                      borderTop: si > 0 ? '2px solid rgba(37,99,235,0.85)' : undefined,
                      borderBottom: section.desc ? '1px solid rgba(37,99,235,0.3)' : '2px solid rgba(37,99,235,0.7)',
                      position:'sticky', top:`${STICKY_HEAD_H}px`, zIndex:15,
                    }}>
                      <div style={{display:'flex', alignItems:'baseline', gap:'0.5rem', flexWrap:'wrap'}}>
                        <span style={{fontSize:'0.82rem', fontWeight:700, color:'#2563eb'}}>{section.name}</span>
                        {section.avg_1d != null && (
                          <span style={{fontSize:'0.72rem', fontWeight:600,
                            color: section.avg_1d > 0 ? '#dc2626' : '#2563eb'}}>
                            평균 {section.avg_1d > 0 ? '+' : ''}{section.avg_1d.toFixed(1)}%
                          </span>
                        )}
                      </div>
                    </td>
                  </tr>
                  {/* LV1 섹션 설명 행 */}
                  {section.desc && (
                    <tr>
                      <td colSpan={12} style={{
                        padding:'0.38rem 1rem 0.42rem 1.2rem',
                        background:'rgba(37,99,235,0.05)',
                        borderBottom:'2px solid rgba(37,99,235,0.7)',
                        fontSize:'0.74rem', color:'rgba(29,78,216,0.9)',
                        lineHeight:1.55, fontStyle:'italic',
                      }}>
                        💡 {section.desc}
                      </td>
                    </tr>
                  )}
                  {renderGroups(buildGroups(section.stocks || []))}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        </div>
        </>
      )}
    </div>
  );
});

export default MarketRadarView;
