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
    padding: '0.6rem 0.8rem', textAlign: 'right', color: '#e2e8f0',
    borderBottom: '2px solid rgba(59,130,246,0.5)', fontWeight: 600,
    background: 'rgba(30,58,138,0.4)', whiteSpace: 'nowrap',
    position: 'sticky', top: 0, zIndex: 5,
  };
  const tdS = {
    padding: '0.5rem 0.8rem', borderBottom: '1px solid rgba(255,255,255,0.04)',
    color: 'rgba(255,255,255,0.85)', verticalAlign: 'middle',
  };
  const badgeS = (market) => {
    const k = market === 'KOSPI' || market === '유가증권';
    return {
      display: 'inline-block', fontSize: '0.62rem', padding: '0.1rem 0.35rem',
      borderRadius: '4px', marginRight: '0.4rem',
      background: k ? 'rgba(59,130,246,0.18)' : 'rgba(16,185,129,0.18)',
      color: k ? '#93c5fd' : '#6ee7b7',
      border: `1px solid ${k ? 'rgba(59,130,246,0.3)' : 'rgba(16,185,129,0.3)'}`,
    };
  };

  return (
    <div className="fade-in" style={{
      background: 'rgba(255,255,255,0.02)', borderRadius: '16px',
      border: '1px solid rgba(255,255,255,0.08)', color: '#fff', overflow: 'hidden'
    }}>
      {/* 헤더 */}
      <div style={{ padding: '1rem 1.2rem', borderBottom: '1px solid rgba(255,255,255,0.08)', display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '0.8rem' }}>
        <div>
          <h2 style={{ margin: 0, fontSize: '1rem', fontWeight: 700 }}>
            🏭 고용보험 가입 인원 현황
          </h2>
          <div style={{ fontSize: '0.72rem', color: 'rgba(255,255,255,0.45)', marginTop: '0.2rem' }}>
            {meta.source} · {formatYm(meta.data_ym)} 기준 · 업데이트: {meta.date}
          </div>
        </div>
        {/* 요약 카드 */}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
          {[
            { label: '집계 기업', val: `${(rows.length).toLocaleString()}개`, color: '#60a5fa' },
            { label: '총 피보험자', val: `${(meta.total_workers || 0).toLocaleString()}명`, color: '#34d399' },
            { label: '총 사업장', val: `${(meta.total_workplaces || 0).toLocaleString()}개`, color: '#a78bfa' },
          ].map(c => (
            <div key={c.label} style={{ textAlign: 'right' }}>
              <div style={{ fontSize: '0.65rem', color: 'rgba(255,255,255,0.45)' }}>{c.label}</div>
              <div style={{ fontSize: '0.92rem', fontWeight: 700, color: c.color }}>{c.val}</div>
            </div>
          ))}
        </div>
      </div>

      <div style={{ padding: '0.55rem 1rem', borderBottom: '1px solid rgba(255,255,255,0.06)', fontSize: '0.72rem', color: '#fbbf24', background: 'rgba(251,191,36,0.08)' }}>
        안내: 피보험자 수는 특수고용직/사업장 집계를 포함할 수 있어 사업보고서 인원보다 크게 보일 수 있습니다.
      </div>

      {/* 컨트롤 */}
      <div style={{ padding: '0.7rem 1rem', display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap', borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
        {[
          { k: 'count', lbl: '인원수순' },
          { k: 'workplace', lbl: '사업장수순' },
        ].map(t => (
          <button key={t.k} onClick={() => setSortBy(t.k)} style={{
            padding: '0.3rem 0.8rem', borderRadius: '7px', fontSize: '0.8rem', cursor: 'pointer',
            fontWeight: sortBy === t.k ? 700 : 400,
            background: sortBy === t.k ? 'rgba(45,212,191,0.15)' : 'transparent',
            color: sortBy === t.k ? '#2dd4bf' : 'rgba(255,255,255,0.5)',
            border: `1px solid ${sortBy === t.k ? '#2dd4bf' : 'transparent'}`,
          }}>{t.lbl}</button>
        ))}
        <input
          placeholder="종목명 검색..."
          value={search}
          onChange={e => { setSearch(e.target.value); setShowAll(false); }}
          style={{
            marginLeft: 'auto', padding: '0.3rem 0.7rem', borderRadius: '6px',
            background: 'rgba(255,255,255,0.07)', border: '1px solid rgba(255,255,255,0.12)',
            color: '#fff', fontSize: '0.8rem', width: '160px'
          }}
        />
        <span style={{ fontSize: '0.72rem', color: 'rgba(255,255,255,0.35)' }}>{visible.length}/{filtered.length}개</span>
      </div>

      {loading ? (
        <div style={{ padding: '3rem', textAlign: 'center', color: 'rgba(255,255,255,0.5)' }}>데이터 로딩 중...</div>
      ) : rows.length === 0 ? (
        <div style={{ padding: '3rem', textAlign: 'center', color: 'rgba(255,255,255,0.4)' }}>
          <div style={{ fontSize: '1.2rem', marginBottom: '0.5rem' }}>📭</div>
          <div>아직 수집된 데이터가 없습니다.</div>
          <div style={{ fontSize: '0.75rem', marginTop: '0.3rem', color: 'rgba(255,255,255,0.3)' }}>
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
                  onMouseOver={e => e.currentTarget.style.background = 'rgba(255,255,255,0.04)'}
                  onMouseOut={e => e.currentTarget.style.background = 'transparent'}>
                  <td style={{ ...tdS, textAlign: 'center', color: 'rgba(255,255,255,0.4)', fontSize: '0.75rem' }}>{i + 1}</td>
                  <td style={{ ...tdS, fontWeight: 600 }}>
                    {row.market && <span style={badgeS(row.market)}>{row.market === '유가증권' ? 'KOSPI' : row.market === '코스닥' ? 'KOSDAQ' : row.market}</span>}
                    {row.stock_name}
                    <span style={{ fontSize: '0.68rem', color: 'rgba(255,255,255,0.3)', marginLeft: '0.3rem' }}>{row.stock_code}</span>
                    <span style={{ fontSize: '0.68rem', color: '#c4b5fd', marginLeft: '0.45rem' }}>
                      (보고서 {formatNum(row.report_workers)}명)
                    </span>
                  </td>
                  <td style={{ ...tdS, color: 'rgba(255,255,255,0.5)', fontSize: '0.77rem' }}>{row.sector || '-'}</td>
                  <td style={{ ...tdS, textAlign: 'right', color: '#c4b5fd', fontWeight: 600 }}>
                    {formatNum(row.report_workers)}
                  </td>
                  <td style={{ ...tdS, textAlign: 'right', fontWeight: 700, color: row.total_workers ? '#34d399' : 'rgba(255,255,255,0.25)' }}>
                    {formatNum(row.total_workers)}
                  </td>
                  <td style={{ ...tdS, textAlign: 'right', color: 'rgba(255,255,255,0.55)' }}>
                    {formatNum(row.workplace_cnt)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!showAll && filtered.length > 15 && (
            <div style={{ padding: '0.8rem', textAlign: 'center', borderTop: '1px solid rgba(255,255,255,0.06)' }}>
              <button onClick={() => setShowAll(true)} style={{ padding: '0.35rem 1.2rem', borderRadius: '7px', fontSize: '0.8rem', cursor: 'pointer', background: 'rgba(255,255,255,0.07)', color: 'rgba(255,255,255,0.6)', border: '1px solid rgba(255,255,255,0.15)' }}>
                전체 보기 ({filtered.length - 15}개 더)
              </button>
            </div>
          )}
        </div>
      )}

      {/* 출처 안내 */}
      <div style={{ padding: '0.6rem 1rem', borderTop: '1px solid rgba(255,255,255,0.06)', fontSize: '0.68rem', color: 'rgba(255,255,255,0.3)', display: 'flex', flexWrap: 'wrap', gap: '1rem' }}>
        <span>📋 출처: 근로복지공단 고용·산재보험 현황정보 (B490001)</span>
        <span>💡 피보험자 수 = 고용보험 가입 상시근로자 합계 (사업장별 자진신고)</span>
        <span>🔄 매일 저녁 20:30 변화 감지 시 자동 갱신</span>
      </div>
    </div>
  );
};

export default EmploymentYearlyView;
