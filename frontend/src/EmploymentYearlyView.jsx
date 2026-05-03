import React, { useState, useEffect } from 'react';

const EmploymentYearlyView = () => {
  const [activeTab, setActiveTab] = useState(1);
  const [rows, setRows] = useState([]);
  const [date, setDate] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    const sortParam = activeTab === 2 ? 'increase' : activeTab === 3 ? 'increase_pct' : 'count';
    // 임시로 독립된 API 엔드포인트 호출 (app.js에서 proxy 연결 필요할 수 있음)
    fetch(`/api/employment-v2/yearly?limit=200&sort_by=${sortParam}`)
      .then(r => r.json())
      .then(d => {
        setRows(d.rows || []);
        setDate(d.date || '2026-04-30');
        setLoading(false);
      })
      .catch(e => {
        console.error("고용 데이터 로드 실패", e);
        setLoading(false);
      });
  }, [activeTab]);

  const formatNum = (num) => num != null ? num.toLocaleString() : '-';
  
  const renderCell = (count, pct, isBold=false) => {
    const isPos = pct > 0;
    const isNeg = pct < 0;
    const color = isPos ? '#ff4d4f' : isNeg ? '#60a5fa' : 'rgba(255,255,255,0.5)';
    const symbol = isPos ? '+' : isNeg ? '▽' : '';
    const val = pct != null ? Math.abs(pct).toFixed(1) : '-';
    
    return (
      <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: '8px' }}>
        <span style={{ fontWeight: isBold ? 600 : 500, width: '60px', textAlign: 'right' }}>
          {formatNum(count)}
        </span>
        {pct != null ? (
          <span style={{ color, fontSize: '0.75rem', fontWeight: 500, width: '45px', textAlign: 'right' }}>
            {symbol}{val}%
          </span>
        ) : (
          <span style={{ width: '45px' }}></span>
        )}
      </div>
    );
  };

  const containerStyle = {
    padding: '0',
    background: 'rgba(255, 255, 255, 0.02)',
    borderRadius: '16px',
    border: '1px solid rgba(255, 255, 255, 0.08)',
    color: '#fff',
    fontFamily: 'inherit',
    overflow: 'hidden'
  };

  const tableStyle = {
    width: '100%',
    borderCollapse: 'collapse',
    fontSize: '0.85rem'
  };

  const thStyle = {
    padding: '0.7rem 0.8rem',
    textAlign: 'center',
    color: '#e2e8f0',
    borderBottom: '2px solid rgba(59,130,246,0.5)',
    fontWeight: 600,
    background: 'rgba(30, 58, 138, 0.4)',
    whiteSpace: 'nowrap'
  };

  const tdStyle = {
    padding: '0.6rem 0.8rem',
    borderBottom: '1px solid rgba(255,255,255,0.04)',
    color: 'rgba(255,255,255,0.85)',
    verticalAlign: 'middle'
  };

  const badgeStyle = (market) => {
    const isKospi = market === 'KOSPI' || market === '유가증권';
    return {
      display: 'inline-block',
      fontSize: '0.65rem',
      padding: '0.15rem 0.4rem',
      borderRadius: '4px',
      marginRight: '0.5rem',
      background: isKospi ? 'rgba(59, 130, 246, 0.2)' : 'rgba(16, 185, 129, 0.2)',
      color: isKospi ? '#93c5fd' : '#6ee7b7',
      border: `1px solid ${isKospi ? 'rgba(59, 130, 246, 0.3)' : 'rgba(16, 185, 129, 0.3)'}`
    };
  };

  const tdStyleNum = {
    ...tdStyle,
    fontVariantNumeric: 'tabular-nums',
    letterSpacing: '0.5px'
  };

  const tabContainerStyle = {
    display: 'flex',
    gap: '0.5rem',
    padding: '1rem',
    borderBottom: '1px solid rgba(255, 255, 255, 0.08)'
  };

  const tabStyle = (isActive) => ({
    padding: '0.4rem 1rem',
    borderRadius: '8px',
    fontSize: '0.85rem',
    cursor: 'pointer',
    transition: 'all 0.2s',
    fontWeight: isActive ? 600 : 400,
    background: isActive ? 'rgba(45, 212, 191, 0.15)' : 'transparent',
    color: isActive ? '#2dd4bf' : 'rgba(255, 255, 255, 0.6)',
    border: isActive ? '1px solid #2dd4bf' : '1px solid transparent'
  });

  return (
    <div className="fade-in" style={containerStyle}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '1rem', borderBottom: '1px solid rgba(255, 255, 255, 0.08)' }}>
        <h2 style={{ margin: 0, fontSize: '1.2rem', fontWeight: 700, color: '#fff' }}>📊 연도별 순증가 (국민연금)</h2>
        <span style={{ fontSize: '0.8rem', color: '#60a5fa', fontWeight: 500 }}>
          업데이트: {date}
        </span>
      </div>
      <div style={tabContainerStyle}>
        <div style={tabStyle(activeTab === 1)} onClick={() => setActiveTab(1)}>순증가 많은순</div>
        <div style={tabStyle(activeTab === 2)} onClick={() => setActiveTab(2)}>전년대비 증가순</div>
      </div>
      
      {loading ? (
        <div style={{ padding: '3rem', textAlign: 'center', color: 'rgba(255,255,255,0.5)' }}>데이터 로딩 중...</div>
      ) : (
      <div style={{ overflowX: 'auto' }}>
        <table style={tableStyle}>
          <thead>
            <tr>
              <th style={{...thStyle, textAlign: 'center', width: '50px'}}>순위</th>
              <th style={{...thStyle, textAlign: 'left', borderRight: '1px solid rgba(59,130,246,0.3)'}}>종목명</th>
              <th style={{...thStyle, textAlign: 'left'}}>섹터</th>
              {activeTab === 2 && (
                <th style={{...thStyle, textAlign: 'right'}}>전년대비 증가(명)</th>
              )}
              <th style={{...thStyle, textAlign: 'right'}}>26년 4월 순증가</th>
              <th style={{...thStyle, textAlign: 'right'}}>25년 누적 순증가</th>
              <th style={{...thStyle, textAlign: 'right'}}>24년 누적 순증가</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => {
              const diff = row.count_26 - row.count_25;
              const diffColor = diff > 0 ? '#ff4d4f' : diff < 0 ? '#60a5fa' : 'rgba(255,255,255,0.5)';
              return (
              <tr 
                key={row.stock_code} 
                style={{ cursor: 'pointer', transition: 'background 0.2s' }}
                onMouseOver={e => e.currentTarget.style.background='rgba(255,255,255,0.04)'}
                onMouseOut={e => e.currentTarget.style.background='transparent'}
              >
                <td style={{...tdStyleNum, textAlign: 'center', color: 'rgba(255,255,255,0.5)'}}>{i + 1}</td>
                <td style={{...tdStyle, textAlign: 'left', fontWeight: 600, borderRight: '1px solid rgba(59,130,246,0.2)'}}>
                  <div style={{ display: 'flex', alignItems: 'center' }}>
                    {row.market && <span style={badgeStyle(row.market)}>{row.market}</span>}
                    {row.stock_name}
                  </div>
                </td>
                <td style={{...tdStyle, textAlign: 'left', color: 'rgba(255,255,255,0.6)', fontSize: '0.8rem'}}>
                  {row.sector || '-'}
                </td>
                {activeTab === 2 && (
                  <td style={{...tdStyleNum, textAlign: 'right', color: diffColor, fontWeight: 600}}>
                    {diff > 0 ? '+' : ''}{diff}
                  </td>
                )}
                <td style={{...tdStyleNum, textAlign: 'right'}}>
                  {renderCell(row.count_26, row.yoy_26, true)}
                </td>
                <td style={{...tdStyleNum, textAlign: 'right'}}>
                  {renderCell(row.count_25, row.yoy_25, false)}
                </td>
                <td style={{...tdStyleNum, textAlign: 'right'}}>
                  {renderCell(row.count_24, row.yoy_24, false)}
                </td>
              </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      )}
    </div>
  );
};

export default EmploymentYearlyView;
