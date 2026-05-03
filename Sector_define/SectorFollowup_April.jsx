import React from 'react';
import aprilData from './april_data.json';

const SectorFollowupAprilView = () => {
  const [activeId, setActiveId] = React.useState(aprilData[0].id);
  
  const activePost = aprilData.find(p => p.id === activeId);

  const containerStyle = {
    padding: '1rem',
    background: 'rgba(255, 255, 255, 0.02)',
    borderRadius: '16px',
    border: '1px solid rgba(255, 255, 255, 0.08)',
    color: '#fff',
    fontFamily: 'inherit'
  };

  const tabContainerStyle = {
    display: 'flex',
    gap: '0.5rem',
    overflowX: 'auto',
    paddingBottom: '1rem',
    marginBottom: '1.5rem',
    scrollbarWidth: 'thin'
  };

  const tabStyle = (isActive) => ({
    padding: '0.5rem 1rem',
    borderRadius: '20px',
    fontSize: '0.85rem',
    whiteSpace: 'nowrap',
    cursor: 'pointer',
    transition: 'all 0.2s',
    background: isActive ? 'rgba(45, 212, 191, 0.15)' : 'rgba(255, 255, 255, 0.03)',
    border: isActive ? '1px solid #2dd4bf' : '1px solid rgba(255, 255, 255, 0.1)',
    color: isActive ? '#2dd4bf' : 'rgba(255, 255, 255, 0.6)',
  });

  const contentStyle = {
    padding: '1.5rem',
    background: 'rgba(0, 0, 0, 0.2)',
    borderRadius: '12px',
    borderLeft: '4px solid #2dd4bf'
  };

  return (
    <div style={containerStyle}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.8rem', marginBottom: '1.5rem' }}>
        <h2 style={{ margin: 0, fontSize: '1.2rem', fontWeight: 700 }}>🎯 4월 섹터 팔로우업 (데이터 요약)</h2>
        <span style={{ fontSize: '0.8rem', color: 'rgba(255, 255, 255, 0.4)' }}>
          "돈의흐름 팔로잉" 4월 포스트 분석 데이터 (18건)
        </span>
      </div>

      {/* 탭 네비게이션 */}
      <div style={tabContainerStyle}>
        {aprilData.map(post => (
          <div 
            key={post.id} 
            onClick={() => setActiveId(post.id)}
            style={tabStyle(activeId === post.id)}
          >
            {post.date} | {post.title.length > 15 ? post.title.substring(0, 15) + '...' : post.title}
          </div>
        ))}
      </div>

      {/* 상세 내용 카드 */}
      {activePost && (
        <div className="fade-in" style={contentStyle}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '1rem' }}>
            <h3 style={{ margin: 0, fontSize: '1.1rem', color: '#2dd4bf' }}>{activePost.title}</h3>
            <span style={{ fontSize: '0.9rem', color: 'rgba(255, 255, 255, 0.5)' }}>{activePost.date}</span>
          </div>
          
          <div style={{ fontSize: '0.95rem', lineHeight: 1.7, marginBottom: '1.5rem', color: 'rgba(255, 255, 255, 0.9)' }}>
            {activePost.summary}
          </div>

          <div style={{ display: 'flex', gap: '1rem' }}>
            <a 
              href={activePost.url} 
              target="_blank" 
              rel="noreferrer"
              style={{
                fontSize: '0.85rem',
                color: '#2dd4bf',
                textDecoration: 'none',
                padding: '0.4rem 0.8rem',
                borderRadius: '6px',
                border: '1px solid rgba(45, 212, 191, 0.3)',
                background: 'rgba(45, 212, 191, 0.05)'
              }}
            >
              블로그 원문 보기
            </a>
          </div>
        </div>
      )}
    </div>
  );
};

export default SectorFollowupAprilView;
