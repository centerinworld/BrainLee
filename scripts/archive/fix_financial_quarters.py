"""
재무제표 분기값 수정 스크립트
문제: Q2/Q3가 누계값으로 저장됨 → 실제 분기값으로 역산
Q2 실제 = Q2누계 - Q1
Q3 실제 = Q3누계 - Q1 - Q2실제
Q4 실제 = 연간 - Q1 - Q2실제 - Q3실제
"""
import sqlite3, logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logger = logging.getLogger(__name__)

DB_PATH = '/Applications/stock_dashboard/stock.db'

def fix_quarters():
    conn = sqlite3.connect(DB_PATH)
    
    # 문제 종목-연도 조회 (연간 < Q1+Q2+Q3 합계의 50%)
    bad = conn.execute('''
        SELECT DISTINCT a.stock_code, a.year
        FROM financial_data a
        JOIN (
            SELECT stock_code, year, SUM(revenue) as sum_rev
            FROM financial_data
            WHERE is_annual=0 AND quarter IN (1,2,3) AND revenue > 0
            GROUP BY stock_code, year
        ) q ON a.stock_code=q.stock_code AND a.year=q.year
        WHERE a.is_annual=1 AND a.revenue > 0
          AND a.revenue < q.sum_rev * 0.5
        ORDER BY a.stock_code, a.year
    ''').fetchall()
    
    logger.info(f"수정 대상: {len(bad)}건")
    fixed = 0
    
    for stock_code, year in bad:
        # 각 분기 데이터 조회
        rows = {}
        for q in [1,2,3]:
            r = conn.execute('''SELECT revenue, operating_profit, net_income, eps
                FROM financial_data WHERE stock_code=? AND year=? AND quarter=? AND is_annual=0''',
                (stock_code, year, q)).fetchone()
            rows[q] = r
        
        ann = conn.execute('''SELECT revenue, operating_profit, net_income, eps
            FROM financial_data WHERE stock_code=? AND year=? AND is_annual=1''',
            (stock_code, year)).fetchone()
        
        if not all([rows.get(1), rows.get(2), rows.get(3), ann]):
            continue
        
        # Q1은 누계=분기 (그대로)
        q1_rev = rows[1][0] or 0
        q1_op  = rows[1][1] or 0
        q1_ni  = rows[1][2] or 0
        
        # Q2 실제 = Q2누계 - Q1
        q2_rev = (rows[2][0] or 0) - q1_rev
        q2_op  = (rows[2][1] or 0) - q1_op
        q2_ni  = (rows[2][2] or 0) - q1_ni
        
        # Q3 실제 = Q3누계 - Q1 - Q2실제
        q3_rev = (rows[3][0] or 0) - q1_rev - q2_rev
        q3_op  = (rows[3][1] or 0) - q1_op  - q2_op
        q3_ni  = (rows[3][2] or 0) - q1_ni  - q2_ni
        
        # Q4 실제 = 연간 - Q1 - Q2실제 - Q3실제
        q4_rev = (ann[0] or 0) - q1_rev - q2_rev - q3_rev
        q4_op  = (ann[1] or 0) - q1_op  - q2_op  - q3_op
        q4_ni  = (ann[2] or 0) - q1_ni  - q2_ni  - q3_ni
        
        # 음수 검증: Q2/Q3 역산 후 음수면 이미 분기값으로 저장된 것 → 스킵
        # 단, Q2누계가 Q1보다 작으면 이미 분기값
        q2_is_cumulative = (rows[2][0] or 0) > (rows[1][0] or 0) * 1.3
        q3_is_cumulative = (rows[3][0] or 0) > (rows[2][0] or 0) * 1.1
        if not q2_is_cumulative and not q3_is_cumulative:
            logger.debug(f"{stock_code} {year}: 이미 분기값 → 스킵")
            continue
        if q2_rev < 0 or q3_rev < 0:
            logger.warning(f"{stock_code} {year}: 역산 음수 → 강제 스킵 (Q2:{q2_rev/1e8:.0f}억 Q3:{q3_rev/1e8:.0f}억)")
            continue
        
        # Q2 업데이트
        conn.execute('''UPDATE financial_data SET revenue=?, operating_profit=?, net_income=?
            WHERE stock_code=? AND year=? AND quarter=2 AND is_annual=0''',
            (q2_rev, q2_op, q2_ni, stock_code, year))
        
        # Q3 업데이트
        conn.execute('''UPDATE financial_data SET revenue=?, operating_profit=?, net_income=?
            WHERE stock_code=? AND year=? AND quarter=3 AND is_annual=0''',
            (q3_rev, q3_op, q3_ni, stock_code, year))
        
        # Q4 저장/업데이트
        q4_exists = conn.execute('''SELECT id FROM financial_data
            WHERE stock_code=? AND year=? AND quarter=4 AND is_annual=0''',
            (stock_code, year)).fetchone()
        
        if q4_exists:
            conn.execute('''UPDATE financial_data SET revenue=?, operating_profit=?, net_income=?
                WHERE stock_code=? AND year=? AND quarter=4 AND is_annual=0''',
                (q4_rev, q4_op, q4_ni, stock_code, year))
        else:
            conn.execute('''INSERT INTO financial_data
                (stock_code, year, quarter, is_annual, revenue, operating_profit, net_income)
                VALUES (?,?,4,0,?,?,?)''',
                (stock_code, year, q4_rev, q4_op, q4_ni))
        
        fixed += 1
        if fixed % 50 == 0:
            conn.commit()
            logger.info(f"진행: {fixed}/{len(bad)}")
    
    conn.commit()
    logger.info(f"완료: {fixed}건 수정")
    
    # 검증
    remaining = conn.execute('''
        SELECT COUNT(DISTINCT a.stock_code||a.year)
        FROM financial_data a
        JOIN (
            SELECT stock_code, year, SUM(revenue) as sum_rev
            FROM financial_data WHERE is_annual=0 AND quarter IN (1,2,3) AND revenue > 0
            GROUP BY stock_code, year
        ) q ON a.stock_code=q.stock_code AND a.year=q.year
        WHERE a.is_annual=1 AND a.revenue > 0 AND a.revenue < q.sum_rev * 0.5
    ''').fetchone()[0]
    logger.info(f"남은 문제: {remaining}건")
    conn.close()

if __name__ == '__main__':
    fix_quarters()
