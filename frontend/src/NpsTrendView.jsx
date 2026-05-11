import React, { useState, useEffect, useMemo } from 'react';

const PERIODS = [
  { key: 'workers', label: '피보험자 수', diffKey: 'total_workers', unit: '명' },
  { key: '1m',      label: '1개월 전 대비', diffKey: 'display_diff_1m', unit: '명' },
  { key: '3m',      label: '3개월 전 대비', diffKey: 'display_diff_3m', unit: '명' },
  { key: '6m',      label: '6개월 전 대비', diffKey: 'display_diff_6m', unit: '명' },
  { key: '1y',      label: '1년 전 대비',   diffKey: 'display_diff_1y', unit: '명' },
];

const NpsTrendView = () => {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [meta, setMeta] = useState({ date: '', wlb_data_ym: '', nps_data_ym: '', has_nps: false, has_wlb: false });
  const [activePeriod, setActivePeriod] = useState('workers');
  const [search, setSearch] = useState('');
  const [showNps, setShowNps] = useState(false);

  // activePeriod가 바뀌면 해당 정렬로 API 재요청
  useEffect(() => {
    setLoading(true);
    fetch(`/api/employment-v2/trend?sort_by=${activePeriod}&limit=500`)
      .then(r => r.json())
      .then(d => {
        setRows(d.rows || []);
        setMeta({
          date: d.date || '',
          wlb_data_ym: d.wlb_data_ym || '',
          nps_data_ym: d.nps_data_ym || '',
          has_nps: d.has_nps || false,
          has_wlb: d.has_wlb || false,
        });
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [activePeriod]);

  const fmtYm = (ym) => ym ? `${ym.slice(0, 4)}년 ${ym.slice(4, 6)}월` : '';
  const fmtNum = (v) => v != null ? Number(v).toLocaleString('ko-KR') : '-';
  const fmtDiff = (v) => {
    if (v == null) return '-';
    return (v > 0 ? '+' : '') + v.toLocaleString('ko-KR');
  };
  const diffColor = (v) => {
    if (v == null) return 'rgba(255,255,255,0.3)';
    if (v > 0) return '#f87171';
    if (v < 0) return '#60a5fa';
    return 'rgba(255,255,255,0.5)';
  };

  const filtered = useMemo(() => {
    if (!search) return rows;
    return rows.filter(r => r.stock_name?.includes(search) || r.stock_code?.includes(search));
  }, [rows, search]);

  const activePrd = PERIODS.find(p => p.key === activePeriod) || PERIODS[0];
  const hasDiffData = rows.some(r => r[activePrd.diffKey] != null);

  const thS = {
    padding: '0.6rem 0.8rem', textAlign: 'right', color: '#e2e8f0',
    borderBottom: '2px solid rgba(59,130,246,0.5)', fontWeight: 600,
    background: 'rgba(30,58,138,0.4)', whiteSpace: 'nowrap',
    position: 'sticky', top: 0, zIndex: 10,
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
      border: '1px solid rgba(255,255,255,0.08)', color: '#fff', overflow: 'hidden',
    }}>
      {/* 헤더 */}
      <div style={{
        padding: '0.9rem 1.2rem', borderBottom: '1px solid rgba(255,255,255,0.08)',
        display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '0.7rem',
      }}>
        <div>
          <h2 style={{ margin: 0, fontSize: '1rem', fontWeight: 700 }}>기업별 피보험자 및 월별 변동 현황</h2>
          <div style={{ fontSize: '0.7rem', color: 'rgba(255,255,255,0.4)', marginTop: '0.15rem' }}>
            근로복지공단 고용보험 · {fmtYm(meta.wlb_data_ym)} 기준 · 수집: {meta.date}
            {meta.has_nps && meta.nps_data_ym ? ` · 국민연금 ${fmtYm(meta.nps_data_ym)}` : ''}
          </div>
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
          {meta.has_nps && (
            <button onClick={() => setShowNps(d => !d)} style={{
              padding: '0.25rem 0.65rem', borderRadius: '6px', fontSize: '0.73rem', cursor: 'pointer',
              background: showNps ? 'rgba(52,211,153,0.15)' : 'rgba(255,255,255,0.06)',
              border: showNps ? '1px solid rgba(52,211,153,0.4)' : '1px solid rgba(255,255,255,0.15)',
              color: showNps ? '#34d399' : 'rgba(255,255,255,0.55)',
            }}>
              {showNps ? '국민연금 상세 숨기기' : '국민연금 상세 보기'}
            </button>
          )}
          <input
            placeholder="종목명 검색..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{
              padding: '0.28rem 0.65rem', borderRadius: '6px',
              background: 'rgba(255,255,255,0.07)', border: '1px solid rgba(255,255,255,0.12)',
              color: '#fff', fontSize: '0.78rem', width: '145px',
            }}
          />
          <span style={{ fontSize: '0.7rem', color: 'rgba(255,255,255,0.35)' }}>{filtered.length}개</span>
        </div>
      </div>

      {/* 기간 선택 탭 */}
      <div style={{
        padding: '0.5rem 1rem', borderBottom: '1px solid rgba(255,255,255,0.06)',
        display: 'flex', gap: '0.35rem', alignItems: 'center', flexWrap: 'wrap',
      }}>
        <span style={{ fontSize: '0.7rem', color: 'rgba(255,255,255,0.4)', marginRight: '0.2rem' }}>
          정렬 기준:
        </span>
        {PERIODS.map(p => {
          const active = activePeriod === p.key;
          return (
            <button key={p.key} onClick={() => setActivePeriod(p.key)} style={{
              padding: '0.28rem 0.75rem', borderRadius: '20px', fontSize: '0.78rem', cursor: 'pointer',
              fontWeight: active ? 700 : 400,
              background: active ? 'rgba(45,212,191,0.2)' : 'transparent',
              color: active ? '#2dd4bf' : 'rgba(255,255,255,0.5)',
              border: `1px solid ${active ? '#2dd4bf' : 'rgba(255,255,255,0.15)'}`,
              transition: 'all 0.15s',
            }}>
              {p.label}
            </button>
          );
        })}
        {activePeriod !== 'workers' && !hasDiffData && (
          <span style={{
            fontSize: '0.7rem', color: '#f59e0b',
            background: 'rgba(245,158,11,0.1)', padding: '0.2rem 0.6rem',
            borderRadius: '4px', border: '1px solid rgba(245,158,11,0.3)', marginLeft: '0.5rem',
          }}>
            아직 이 기간의 비교 데이터가 없습니다 (매월 수집 누적 중)
          </span>
        )}
      </div>

      {loading ? (
        <div style={{ padding: '3rem', textAlign: 'center', color: 'rgba(255,255,255,0.5)' }}>
          데이터 로딩 중...
        </div>
      ) : rows.length === 0 ? (
        <div style={{ padding: '3rem', textAlign: 'center', color: 'rgba(255,255,255,0.4)' }}>
          <div style={{ fontSize: '1.2rem', marginBottom: '0.5rem' }}>📭</div>
          <div>수집된 데이터가 없습니다.</div>
        </div>
      ) : (
        <div style={{ overflowX: 'auto', overflowY: 'clip' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
            <thead>
              <tr>
                <th style={{ ...thS, textAlign: 'center', cursor: 'default', width: '40px' }}>#</th>
                <th style={{ ...thS, textAlign: 'left' }}>종목명</th>
                <th style={{ ...thS, textAlign: 'left', fontSize: '0.74rem' }}>섹터</th>
                <th style={{ ...thS, color: activePeriod === 'workers' ? '#2dd4bf' : '#e2e8f0',
                  background: activePeriod === 'workers' ? 'rgba(45,212,191,0.12)' : 'rgba(30,58,138,0.4)' }}>
                  피보험자 (명)
                </th>
                <th style={{ ...thS }}>사업장 수</th>
                {/* 기간별 대비 컬럼 — 피보험자수 정렬이 아닐 때만 강조 */}
                {activePeriod !== 'workers' && (
                  <th style={{ ...thS, color: '#2dd4bf', background: 'rgba(45,212,191,0.12)',
                    borderLeft: '2px solid rgba(45,212,191,0.4)', minWidth: '120px' }}>
                    {activePrd.label}
                  </th>
                )}
                {/* 나머지 기간 컬럼 (참고용, 작게) */}
                {PERIODS.filter(p => p.key !== 'workers' && p.key !== activePeriod).map(p => (
                  <th key={p.key} style={{
                    ...thS, fontSize: '0.72rem', color: 'rgba(180,200,255,0.6)',
                    borderLeft: '1px solid rgba(59,130,246,0.15)', minWidth: '100px',
                  }}>
                    {p.label}
                  </th>
                ))}
                {showNps && (
                  <th style={{ ...thS, borderLeft: '2px solid rgba(245,158,11,0.3)',
                    color: '#fbbf24', background: 'rgba(245,158,11,0.06)', fontSize: '0.72rem' }}>
                    국민연금 순증가
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {filtered.map((row, i) => {
                const active_diff = row[activePrd.diffKey];
                return (
                  <tr key={row.stock_code}
                    style={{ transition: 'background 0.15s' }}
                    onMouseOver={e => e.currentTarget.style.background = 'rgba(255,255,255,0.04)'}
                    onMouseOut={e => e.currentTarget.style.background = 'transparent'}>
                    <td style={{ ...tdS, textAlign: 'center', color: 'rgba(255,255,255,0.4)', fontSize: '0.73rem' }}>{i + 1}</td>
                    <td style={{ ...tdS, fontWeight: 600 }}>
                      {row.market && (
                        <span style={badgeS(row.market)}>
                          {row.market === '유가증권' ? 'KOSPI' : row.market === '코스닥' ? 'KOSDAQ' : row.market}
                        </span>
                      )}
                      {row.stock_name}
                      <span style={{ fontSize: '0.67rem', color: 'rgba(255,255,255,0.28)', marginLeft: '0.3rem' }}>
                        {row.stock_code}
                      </span>
                    </td>
                    <td style={{ ...tdS, color: 'rgba(255,255,255,0.45)', fontSize: '0.74rem' }}>
                      {row.sector || '-'}
                    </td>
                    {/* 피보험자 수 */}
                    <td style={{
                      ...tdS, textAlign: 'right', fontWeight: 700,
                      color: activePeriod === 'workers' ? '#34d399' : 'rgba(255,255,255,0.7)',
                      background: activePeriod === 'workers' ? 'rgba(52,211,153,0.03)' : 'transparent',
                    }}>
                      {fmtNum(row.total_workers)}
                    </td>
                    {/* 사업장 수 */}
                    <td style={{ ...tdS, textAlign: 'right', color: 'rgba(255,255,255,0.45)' }}>
                      {fmtNum(row.workplace_cnt)}
                    </td>
                    {/* 선택된 기간 대비 */}
                    {activePeriod !== 'workers' && (
                      <td style={{
                        ...tdS, textAlign: 'right',
                        fontWeight: 700, fontSize: '0.9rem',
                        color: diffColor(active_diff),
                        borderLeft: '2px solid rgba(45,212,191,0.2)',
                        background: 'rgba(45,212,191,0.03)',
                      }}>
                        {fmtDiff(active_diff)}
                      </td>
                    )}
                    {/* 나머지 기간 컬럼 (참고용) */}
                    {PERIODS.filter(p => p.key !== 'workers' && p.key !== activePeriod).map(p => {
                      const v = row[p.diffKey];
                      return (
                        <td key={p.key} style={{
                          ...tdS, textAlign: 'right',
                          color: diffColor(v), fontSize: '0.8rem',
                          borderLeft: '1px solid rgba(59,130,246,0.08)',
                        }}>
                          {fmtDiff(v)}
                        </td>
                      );
                    })}
                    {showNps && (
                      <td style={{
                        ...tdS, textAlign: 'right', fontSize: '0.8rem',
                        color: diffColor(row.diff_0m),
                        borderLeft: '2px solid rgba(245,158,11,0.2)',
                        background: 'rgba(245,158,11,0.02)',
                      }}>
                        {fmtDiff(row.diff_0m)}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* 출처 */}
      <div style={{
        padding: '0.5rem 1rem', borderTop: '1px solid rgba(255,255,255,0.06)',
        fontSize: '0.67rem', color: 'rgba(255,255,255,0.28)',
        display: 'flex', flexWrap: 'wrap', gap: '1rem',
      }}>
        <span>현재 인원: 근로복지공단 고용·산재보험 현황정보 (B490001)</span>
        <span>기간별 변동: WLB 과거 월이 없으면 국민연금 월별 순증감으로 표시합니다</span>
        <span>매일 저녁 20:30 변화 감지 시 자동 갱신</span>
      </div>
    </div>
  );
};

export default NpsTrendView;
