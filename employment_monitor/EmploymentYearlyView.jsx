import React, { useState, useEffect } from 'react';

const EmploymentYearlyView = () => {
  const [activeTab, setActiveTab] = useState(1);
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    const sortParam = activeTab === 2 ? 'increase' : 'count';
    // 임시로 독립된 API 엔드포인트 호출 (app.js에서 proxy 연결 필요할 수 있음)
    fetch(`/api/employment-v2/yearly?limit=200&sort_by=${sortParam}`)
      .then(r => r.json())
      .then(d => {
        setRows(d.rows || []);
        setLoading(false);
      })
      .catch(e => {
        console.error("고용 데이터 로드 실패", e);
        setLoading(false);
      });
  }, [activeTab]);

  const formatNum = (num) => num != null ? num.toLocaleString() : '-';
  
  const renderYoY = (pct) => {
    if (pct == null) return null;
    const isPos = pct > 0;
    const isNeg = pct < 0;
    // 한국 주식 스타일: 빨강(상승), 파랑(하락)
    const color = isPos ? '#ff4d4f' : isNeg ? '#60a5fa' : 'rgba(255,255,255,0.5)';
    const symbol = isPos ? '+' : isNeg ? '▽' : '';
    const val = Math.abs(pct).toFixed(1);
    
    return (
      <span style={{ color, fontSize: '0.75rem', marginLeft: '0.5rem', fontWeight: 500 }}>
        ({symbol}{val}%)
      </span>
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
    color: 'var(--text-secondary, rgba(255,255,255,0.6))',
    borderBottom: '1px solid rgba(255,255,255,0.1)',
    fontWeight: 600,
    background: 'rgba(255,255,255,0.04)',
    whiteSpace: 'nowrap'
  };

  const tdStyle = {
    padding: '0.6rem 0.8rem',
    borderBottom: '1px solid rgba(255,255,255,0.04)',
    color: 'rgba(255,255,255,0.85)',
    verticalAlign: 'middle'
  };

  const badgeStyle = (market) => ({
    display: 'inline-block',
    fontSize: '0.65rem',
    padding: '0.15rem 0.4rem',
    borderRadius: '4px',
    marginLeft: '0.5rem',
    background: market === 'KOSPI' ? 'rgba(59, 130, 246, 0.2)' : 'rgba(16, 185, 129, 0.2)',
    color: market === 'KOSPI' ? '#93c5fd' : '#6ee7b7',
    border: `1px solid ${market === 'KOSPI' ? 'rgba(59, 130, 246, 0.3)' : 'rgba(16, 185, 129, 0.3)'}`
  });

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
      <div style={tabContainerStyle}>
        <div style={tabStyle(activeTab === 1)} onClick={() => setActiveTab(1)}>고용 많은순</div>
        <div style={tabStyle(activeTab === 2)} onClick={() => setActiveTab(2)}>고용 증가순</div>
      </div>
      
      {loading ? (
        <div style={{ padding: '3rem', textAlign: 'center', color: 'rgba(255,255,255,0.5)' }}>데이터 로딩 중...</div>
      ) : (
      <div style={{ overflowX: 'auto' }}>
        <table style={tableStyle}>
          <thead>
            <tr>
              <th style={{...thStyle, textAlign: 'center', width: '50px'}}>순위</th>
              <th style={{...thStyle, textAlign: 'left'}}>종목명</th>
              <th style={{...thStyle, textAlign: 'left'}}>섹터</th>
              <th style={{...thStyle, textAlign: 'right'}}>25년 고용인원</th>
              <th style={{...thStyle, textAlign: 'right'}}>24년 고용인원</th>
              <th style={{...thStyle, textAlign: 'right'}}>23년 고용인원</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr 
                key={row.stock_code} 
                style={{ cursor: 'pointer', transition: 'background 0.2s' }}
                onMouseOver={e => e.currentTarget.style.background='rgba(255,255,255,0.04)'}
                onMouseOut={e => e.currentTarget.style.background='transparent'}
              >
                <td style={{...tdStyleNum, textAlign: 'center', color: 'rgba(255,255,255,0.5)'}}>{i + 1}</td>
                <td style={{...tdStyle, textAlign: 'left', fontWeight: 600}}>
                  <div style={{ display: 'flex', alignItems: 'center' }}>
                    {row.stock_name}
                    {row.market && <span style={badgeStyle(row.market)}>{row.market}</span>}
                  </div>
                </td>
                <td style={{...tdStyle, textAlign: 'left', color: 'rgba(255,255,255,0.6)', fontSize: '0.8rem'}}>
                  {row.sector || '-'}
                </td>
                <td style={{...tdStyleNum, textAlign: 'right'}}>
                  <span style={{ fontWeight: 600 }}>{formatNum(row.count_25)}</span>
                  {renderYoY(row.yoy_25)}
                </td>
                <td style={{...tdStyleNum, textAlign: 'right'}}>
                  <span style={{ fontWeight: 500 }}>{formatNum(row.count_24)}</span>
                  {renderYoY(row.yoy_24)}
                </td>
                <td style={{...tdStyleNum, textAlign: 'right', color: 'rgba(255,255,255,0.7)'}}>
                  {formatNum(row.count_23)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      )}
    </div>
  );
};

export default EmploymentYearlyView;
