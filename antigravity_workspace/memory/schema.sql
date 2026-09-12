-- Project Antigravity L3 Data & Memory Infrastructure DDL
-- Database: PostgreSQL 16 + pgvector

CREATE EXTENSION IF NOT EXISTS vector;

-- 1. 정량 재무제표 (Fnguide / DART / Consensus)
CREATE TABLE IF NOT EXISTS financial_fundamentals (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(10) NOT NULL,
    stock_name VARCHAR(100) NOT NULL,
    year INT NOT NULL,
    quarter INT NOT NULL,
    revenue NUMERIC(20, 2),
    operating_profit NUMERIC(20, 2),
    net_income NUMERIC(20, 2),
    total_assets NUMERIC(20, 2),
    total_liabilities NUMERIC(20, 2),
    total_equity NUMERIC(20, 2),
    eps NUMERIC(15, 2),
    bps NUMERIC(15, 2),
    roe NUMERIC(10, 2),
    is_annual BOOLEAN DEFAULT FALSE,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_financial_record UNIQUE (stock_code, year, quarter, is_annual)
);
CREATE INDEX IF NOT EXISTS idx_financial_stock_code ON financial_fundamentals(stock_code);

-- 2. 시계열 주가 (OHLCV + 수급)
CREATE TABLE IF NOT EXISTS stock_price_history (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(10) NOT NULL,
    trade_date TIMESTAMP WITH TIME ZONE NOT NULL,
    open NUMERIC(15, 2) NOT NULL,
    high NUMERIC(15, 2) NOT NULL,
    low NUMERIC(15, 2) NOT NULL,
    close NUMERIC(15, 2) NOT NULL,
    volume NUMERIC(20, 2) NOT NULL,
    inst_net_buy NUMERIC(20, 2) DEFAULT 0,
    frn_net_buy NUMERIC(20, 2) DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_price_record UNIQUE (stock_code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_price_stock_date ON stock_price_history(stock_code, trade_date DESC);

-- 3. 거시 경제 지표 (ECOS / 환율 / 원자재)
CREATE TABLE IF NOT EXISTS macro_indicators (
    id SERIAL PRIMARY KEY,
    indicator_code VARCHAR(50) NOT NULL,
    indicator_name VARCHAR(100) NOT NULL,
    record_date DATE NOT NULL,
    val NUMERIC(15, 4) NOT NULL,
    unit VARCHAR(20),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_macro_record UNIQUE (indicator_code, record_date)
);
CREATE INDEX IF NOT EXISTS idx_macro_code_date ON macro_indicators(indicator_code, record_date DESC);

-- 4. KAI 및 항공/방산 인텔리전스 (1536차원 pgvector + HNSW)
CREATE TABLE IF NOT EXISTS defense_intelligence (
    id SERIAL PRIMARY KEY,
    source VARCHAR(50) NOT NULL, -- DAPA, MND, DART, DefenseNews, etc.
    title VARCHAR(500) NOT NULL,
    raw_content TEXT NOT NULL,
    fact_summary TEXT NOT NULL,          -- [주요 팩트]
    impact_summary TEXT NOT NULL,        -- [경쟁 환경 및 산업 영향]
    strategy_summary TEXT NOT NULL,      -- [전사 사업 전략 관점의 시사점]
    sentiment_score NUMERIC(5, 2),       -- -1.0 ~ +1.0
    embedding vector(1536),              -- 1536차원 뉴스 임베딩
    is_notified_slack BOOLEAN DEFAULT FALSE,
    is_synced_notion BOOLEAN DEFAULT FALSE,
    published_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
-- HNSW 코사인 유사도 인덱스 생성
CREATE INDEX IF NOT EXISTS idx_defense_intelligence_hnsw 
ON defense_intelligence USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- 5. 주문 체결 및 포트폴리오 원장
CREATE TABLE IF NOT EXISTS portfolio_ledger (
    id SERIAL PRIMARY KEY,
    order_id VARCHAR(50) UNIQUE NOT NULL,
    stock_code VARCHAR(10) NOT NULL,
    stock_name VARCHAR(100) NOT NULL,
    order_type VARCHAR(10) NOT NULL, -- BUY, SELL
    order_price NUMERIC(15, 2) NOT NULL,
    quantity INT NOT NULL,
    status VARCHAR(20) NOT NULL,    -- PENDING, FILLED, REJECTED
    strategy_name VARCHAR(50) NOT NULL,
    filled_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 6. 자가 고도화(Self-Healing) 및 버그 자동 패치 로그
CREATE TABLE IF NOT EXISTS self_healing_logs (
    id SERIAL PRIMARY KEY,
    error_type VARCHAR(100) NOT NULL,
    error_message TEXT NOT NULL,
    stack_trace TEXT,
    target_file VARCHAR(255),
    patch_branch VARCHAR(100),
    patch_diff TEXT,
    claude_review_status VARCHAR(20), -- APPROVED, REJECTED
    test_result_log TEXT,
    is_auto_merged BOOLEAN DEFAULT FALSE,
    resolved_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 7. L1 마스터 DAG 태스크 상태 관리
CREATE TABLE IF NOT EXISTS agent_tasks (
    task_id VARCHAR(50) PRIMARY KEY,
    domain VARCHAR(50) NOT NULL,      -- dev, trading, content, intelligence, qa
    title VARCHAR(255) NOT NULL,
    payload JSONB,
    status VARCHAR(20) NOT NULL,     -- PENDING, RUNNING, COMPLETED, FAILED, RETRY
    assigned_orchestrator VARCHAR(50),
    result JSONB,
    qa_status VARCHAR(20),           -- PASSED, FAILED
    qa_notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
