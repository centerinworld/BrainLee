/* light-theme-codemod-2026-09-27 */
/* light-theme-codemod-pass2-2026-09-27 */
/* light-theme-codemod-pass3-2026-09-27 */
/* light-theme-codemod-pass4-2026-09-27 */
/* light-theme-codemod-pass5-2026-09-27 */
/* light-theme-codemod-pass6-2026-09-27 */
/* light-theme-codemod-pass7-2026-09-27 */
import React, { useState, useEffect } from 'react';

const EmploymentYearlyView = () => {
  const [sortBy, setSortBy] = useState('count');
  const [rows, setRows] = useState([]);
  const [meta, setMeta] = useState({ date: '', data_ym: '', total_workers: 0, total_workplaces: 0 });
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    setLoading(true);
    setShowAll(false);
    fetch(`/api/employment-v2/yearly?sort_by=${sortBy}`)
      .then(r => r.json())
      .then(d => {
        setRows(d.rows || []);
        setMeta({
          date: d.date || new Date().toISOString().slice(0, 10),
          data_ym: d.data_ym || '',
          total_workers: d.total_workers || 0,
          total_workplaces: d.total_workplaces || 0,
          source: d.source || '근로복지공단 고용보험',
        });
        setLoading(false);
      })
      .catch(e => {
        console.error('고용 데이터 로드 실패', e);
        setLoading(false);
      });
  }, [sortBy]);

  const formatNum = (n) => (n != null ? Number(n).toLocaleString() : '-');
  const formatYm = (ym) => ym ? `${ym.slice(0, 4)}년 ${ym.slice(4, 6)}월` : '';

  const filtered = search
    ? rows.filter(r => r.stock_name?.includes(search) || r.stock_code?.includes(search))
    : rows;
  const visible = showAll ? filtered : filtered.slice(0, 15);

  const thS = {
    padding: '0.6rem 0.8rem', textAlign: 'right', color: '#1e293b',
    borderBottom: '2px solid rgba(37,99,235,0.5)', fontWeight: 600,
    background: 'rgba(30,58,138,0.4)', whiteSpace: 'nowrap',
    position: 'sticky', top: 0, zIndex: 5,
  };
  const tdS = {
    padding: '0.5rem 0.8rem', borderBottom: '1px solid rgba(15,23,42,0.2)',
    color: 'rgba(15,23,42,0.88)', verticalAlign: 'middle',
  };
  const badgeS = (market) => {
    const k = market === 'KOSPI' || market === '유가증권';
    return {
      display: 'inline-block', fontSize: '0.62rem', padding: '0.1rem 0.35rem',
      borderRadius: '4px', marginRight: '0.4rem',
      background: k ? 'rgba(37,99,235,0.18)' : 'rgba(5,150,105,0.18)',
      color: k ? '#2563eb' : '#047857',
      border: `1px solid ${k ? 'rgba(37,99,235,0.3)' : 'rgba(5,150,105,0.3)'}`,
    };
  };

  return (
    <div className="fade-in" style={{
      background: 'rgba(15,23,42,0.02)', borderRadius: '16px',
      border: '1px solid rgba(15,23,42,0.2)', color: 'var(--text-primary)', overflow: 'hidden'
    }}>
      {/* 헤더 */}
      <div style={{ padding: '1rem 1.2rem', borderBottom: '1px solid rgba(15,23,42,0.2)', display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '0.8rem' }}>
        <div>
          <h2 style={{ margin: 0, fontSize: '1rem', fontWeight: 700 }}>
            🏭 고용보험 가입 인원 현황
          </h2>
          <div style={{ fontSize: '0.72rem', color: 'rgba(15,23,42,0.88)', marginTop: '0.2rem' }}>
            {meta.source} · {formatYm(meta.data_ym)} 기준 · 업데이트: {meta.date}
          </div>
        </div>
        {/* 요약 카드 */}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
          {[
            { label: '집계 기업', val: `${(rows.length).toLocaleString()}개`, color: '#2563eb' },
            { label: '총 피보험자', val: `${(meta.total_workers || 0).toLocaleString()}명`, color: '#047857' },
            { label: '총 사업장', val: `${(meta.total_workplaces || 0).toLocaleString()}개`, color: '#7c3aed' },
          ].map(c => (
            <div key={c.label} style={{ textAlign: 'right' }}>
              <div style={{ fontSize: '0.65rem', color: 'rgba(15,23,42,0.88)' }}>{c.label}</div>
              <div style={{ fontSize: '0.92rem', fontWeight: 700, color: c.color }}>{c.val}</div>
            </div>
          ))}
        </div>
      </div>

      <div style={{ padding: '0.55rem 1rem', borderBottom: '1px solid rgba(15,23,42,0.2)', fontSize: '0.72rem', color: '#b45309', background: 'rgba(217,119,6,0.08)' }}>
        모든 피보험자 수를 표시합니다. 보험 영업인력, 현장·프로젝트 인력, 다사업장 운영처럼 업종별 집계 범위가 다른 경우 종목명 옆에 특성을 표시합니다.
      </div>

      {/* 컨트롤 */}
      <div style={{ padding: '0.7rem 1rem', display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap', borderBottom: '1px solid rgba(15,23,42,0.2)' }}>
        {[
          { k: 'count', lbl: '인원수순' },
          { k: 'workplace', lbl: '사업장수순' },
        ].map(t => (
          <button key={t.k} onClick={() => setSortBy(t.k)} style={{
            padding: '0.3rem 0.8rem', borderRadius: '7px', fontSize: '0.8rem', cursor: 'pointer',
            fontWeight: sortBy === t.k ? 700 : 400,
            background: sortBy === t.k ? 'rgba(37,99,235,0.15)' : 'transparent',
            color: sortBy === t.k ? '#2563eb' : 'rgba(15,23,42,0.88)',
            border: `1px solid ${sortBy === t.k ? '#2563eb' : 'transparent'}`,
          }}>{t.lbl}</button>
        ))}
        <input
          placeholder="종목명 검색..."
          value={search}
          onChange={e => { setSearch(e.target.value); setShowAll(false); }}
          style={{
            marginLeft: 'auto', padding: '0.3rem 0.7rem', borderRadius: '6px',
            background: 'rgba(15,23,42,0.07)', border: '1px solid rgba(15,23,42,0.2)',
            color: 'var(--text-primary)', fontSize: '0.8rem', width: '160px'
          }}
        />
        <span style={{ fontSize: '0.72rem', color: 'rgba(15,23,42,0.88)' }}>{visible.length}/{filtered.length}개</span>
      </div>

      {loading ? (
        <div style={{ padding: '3rem', textAlign: 'center', color: 'rgba(15,23,42,0.88)' }}>데이터 로딩 중...</div>
      ) : rows.length === 0 ? (
        <div style={{ padding: '3rem', textAlign: 'center', color: 'rgba(15,23,42,0.88)' }}>
          <div style={{ fontSize: '1.2rem', marginBottom: '0.5rem' }}>📭</div>
          <div>아직 수집된 데이터가 없습니다.</div>
          <div style={{ fontSize: '0.75rem', marginTop: '0.3rem', color: 'rgba(15,23,42,0.88)' }}>
            서버 재시작 후 매일 저녁 20:30 자동 수집됩니다.
          </div>
        </div>
      ) : (
        <div style={{ overflowX: 'auto', overflowY: 'clip' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.83rem' }}>
            <thead>
              <tr>
                <th style={{ ...thS, textAlign: 'center', width: '45px' }}>#</th>
                <th style={{ ...thS, textAlign: 'left' }}>종목명</th>
                <th style={{ ...thS, textAlign: 'left' }}>섹터</th>
                <th style={{ ...thS }}>사업보고서 인원</th>
                <th style={{ ...thS }}>피보험자 (명)</th>
                <th style={{ ...thS }}>사업장 수</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((row, i) => (
                <tr key={row.stock_code}
                  style={{ transition: 'background 0.15s' }}
                  onMouseOver={e => e.currentTarget.style.background = 'rgba(15,23,42,0.04)'}
                  onMouseOut={e => e.currentTarget.style.background = 'transparent'}>
                  <td style={{ ...tdS, textAlign: 'center', color: 'rgba(15,23,42,0.88)', fontSize: '0.75rem' }}>{i + 1}</td>
                  <td style={{ ...tdS, fontWeight: 600 }}>
                    {row.market && <span style={badgeS(row.market)}>{row.market === '유가증권' ? 'KOSPI' : row.market === '코스닥' ? 'KOSDAQ' : row.market}</span>}
                    {row.stock_name}
                    <span style={{ fontSize: '0.68rem', color: 'rgba(15,23,42,0.88)', marginLeft: '0.3rem' }}>{row.stock_code}</span>
                    <span style={{ fontSize: '0.68rem', color: '#6d28d9', marginLeft: '0.45rem' }}>
                      (보고서 {formatNum(row.report_workers)}명)
                    </span>
                    {row.employment_scope?.type !== 'general' && (
                      <span title={row.employment_scope.note} style={{ display:'inline-block', marginLeft:'0.4rem', padding:'0.08rem 0.3rem', borderRadius:'4px', fontSize:'0.62rem', color:'#b45309', border:'1px solid rgba(217,119,6,0.35)', background:'rgba(217,119,6,0.08)' }}>
                        {row.employment_scope.label}
                      </span>
                    )}
                  </td>
                  <td style={{ ...tdS, color: 'rgba(15,23,42,0.88)', fontSize: '0.77rem' }}>{row.sector || '-'}</td>
                  <td style={{ ...tdS, textAlign: 'right', color: '#6d28d9', fontWeight: 600 }}>
                    {formatNum(row.report_workers)}
                  </td>
                  <td style={{ ...tdS, textAlign: 'right', fontWeight: 700, color: row.total_workers ? '#047857' : 'rgba(15,23,42,0.88)' }}>
                    {formatNum(row.total_workers)}
                  </td>
                  <td style={{ ...tdS, textAlign: 'right', color: 'rgba(15,23,42,0.88)' }}>
                    {formatNum(row.workplace_cnt)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!showAll && filtered.length > 15 && (
            <div style={{ padding: '0.8rem', textAlign: 'center', borderTop: '1px solid rgba(15,23,42,0.2)' }}>
              <button onClick={() => setShowAll(true)} style={{ padding: '0.35rem 1.2rem', borderRadius: '7px', fontSize: '0.8rem', cursor: 'pointer', background: 'rgba(15,23,42,0.07)', color: 'rgba(15,23,42,0.88)', border: '1px solid rgba(15,23,42,0.2)' }}>
                전체 보기 ({filtered.length - 15}개 더)
              </button>
            </div>
          )}
        </div>
      )}

      {/* 출처 안내 */}
      <div style={{ padding: '0.6rem 1rem', borderTop: '1px solid rgba(15,23,42,0.2)', fontSize: '0.68rem', color: 'rgba(15,23,42,0.88)', display: 'flex', flexWrap: 'wrap', gap: '1rem' }}>
        <span>📋 출처: 근로복지공단 고용·산재보험 현황정보 (B490001)</span>
        <span>💡 피보험자 수 = 고용보험 가입 상시근로자 합계 (사업장별 자진신고)</span>
        <span>🔄 매일 저녁 20:30 변화 감지 시 자동 갱신</span>
      </div>
    </div>
  );
};

export default EmploymentYearlyView;
