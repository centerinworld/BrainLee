"""
verification/generate_report.py
— Before / After JSON을 비교하여 OpenAI 검토용 HTML 리포트 생성

실행: python3 verification/generate_report.py
출력: verification/financial_verification_report_YYYYMMDD.html
"""
import json, os, glob, datetime, sqlite3

VERIFY_DIR = os.path.dirname(__file__)
DB_PATH    = "/Applications/stock_dashboard/stock.db"
TIMESTAMP  = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
OUT_HTML   = f"{VERIFY_DIR}/financial_verification_report_{TIMESTAMP}.html"


def load_json(pattern):
    files = sorted(glob.glob(f"{VERIFY_DIR}/{pattern}"))
    if not files:
        return {}
    with open(files[-1]) as f:
        return json.load(f)


def sample_records(conn, n=20):
    """fnguide 마킹된 레코드 샘플"""
    rows = conn.execute("""
        SELECT f.stock_code, su.stock_name, f.year, f.quarter, f.is_annual,
               f.report_type, f.data_source,
               f.revenue, f.operating_profit, f.net_income,
               f.total_assets, f.total_equity
        FROM financial_data f
        LEFT JOIN stock_universe su ON su.stock_code = f.stock_code
        WHERE f.data_source = 'fnguide' AND f.is_annual = 1
        ORDER BY f.year DESC, f.stock_code
        LIMIT ?
    """, (n,)).fetchall()
    cols = ['stock_code','stock_name','year','quarter','is_annual',
            'report_type','data_source','revenue','operating_profit',
            'net_income','total_assets','total_equity']
    return [dict(zip(cols, r)) for r in rows]


def null_by_source(conn):
    return conn.execute("""
        SELECT COALESCE(data_source,'unknown') src,
               COUNT(*) total,
               SUM(CASE WHEN revenue IS NULL THEN 1 ELSE 0 END) rev_null,
               SUM(CASE WHEN operating_profit IS NULL THEN 1 ELSE 0 END) op_null,
               SUM(CASE WHEN net_income IS NULL THEN 1 ELSE 0 END) ni_null,
               ROUND(SUM(CASE WHEN revenue IS NULL THEN 1.0 ELSE 0 END)*100/COUNT(*),1) rev_null_pct
        FROM financial_data GROUP BY 1 ORDER BY total DESC
    """).fetchall()


def top_profiles(n=30):
    p = "/Applications/stock_dashboard/config/financial_profiles.json"
    if not os.path.exists(p):
        return []
    with open(p) as f:
        data = json.load(f)
    items = [(k, v) for k, v in data.items() if k != '_meta']
    return items[:n]


def fmt(v):
    if v is None: return '<span style="color:#aaa">NULL</span>'
    if isinstance(v, float):
        if abs(v) >= 1e8: return f'{v/1e8:,.0f}억원'
        return f'{v:,.0f}'
    return str(v)


def generate():
    before = load_json("before_migration.json")
    after  = load_json("after_fnguide_resume_*.json")
    mig    = load_json("migration_datasource_*.json")

    conn = sqlite3.connect(DB_PATH)
    samples  = sample_records(conn, 30)
    null_src = null_by_source(conn)
    profiles = top_profiles(30)
    conn.close()

    bf = before.get("financial_data", {})
    af = after.get("financial_data_by_source", {})

    html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>재무제표 데이터 검증 리포트</title>
<style>
  body {{ font-family: 'Segoe UI', sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; background: #f5f5f5; }}
  h1 {{ color: #1a1a2e; border-bottom: 3px solid #e94560; padding-bottom: 10px; }}
  h2 {{ color: #16213e; margin-top: 30px; border-left: 4px solid #0f3460; padding-left: 10px; }}
  .card {{ background: white; border-radius: 8px; padding: 20px; margin: 15px 0; box-shadow: 0 2px 4px rgba(0,0,0,.1); }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ background: #1a1a2e; color: white; padding: 8px 10px; text-align: left; }}
  td {{ padding: 6px 10px; border-bottom: 1px solid #eee; }}
  tr:hover td {{ background: #f0f4ff; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 11px; font-weight: bold; }}
  .fnguide {{ background: #d4edda; color: #155724; }}
  .dart    {{ background: #fff3cd; color: #856404; }}
  .unknown {{ background: #f8d7da; color: #721c24; }}
  .num {{ text-align: right; font-family: monospace; }}
  .stat-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; }}
  .stat-box {{ background: #1a1a2e; color: white; border-radius: 8px; padding: 15px; text-align: center; }}
  .stat-box .val {{ font-size: 28px; font-weight: bold; color: #e94560; }}
  .stat-box .lbl {{ font-size: 12px; color: #aaa; margin-top: 5px; }}
  .green {{ color: #28a745; }} .red {{ color: #dc3545; }}
  pre {{ background: #1a1a2e; color: #e0e0e0; padding: 15px; border-radius: 6px; overflow-x: auto; font-size: 12px; }}
</style>
</head>
<body>
<h1>📊 재무제표 데이터 검증 리포트</h1>
<p style="color:#666">생성: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} |
목적: FnGuide 우선 소스 전환 후 데이터 품질 검증 (OpenAI 재확인용)</p>

<h2>1. 작업 개요</h2>
<div class="card">
<table>
<tr><th>항목</th><th>Before</th><th>After</th><th>변화</th></tr>
<tr><td>재무제표 전체 레코드</td>
    <td class="num">{bf.get('total','-'):,}</td>
    <td class="num">{sum(v['records'] for v in af.values()):,}</td>
    <td class="num">{sum(v['records'] for v in af.values())-bf.get('total',0):+,}</td></tr>
<tr><td>수집된 종목 수</td>
    <td class="num">{bf.get('stocks','-'):,}</td>
    <td class="num">{sum(v['stocks'] for v in af.values()):,}</td>
    <td>-</td></tr>
<tr><td>revenue NULL 건수</td>
    <td class="num">{bf.get('rev_null','-'):,}</td>
    <td class="num">-</td><td>-</td></tr>
<tr><td>operating_profit NULL 건수</td>
    <td class="num">{bf.get('op_null','-'):,}</td>
    <td class="num">-</td><td>-</td></tr>
<tr><td>net_income NULL 건수</td>
    <td class="num">{bf.get('ni_null','-'):,}</td>
    <td class="num">-</td><td>-</td></tr>
</table>
</div>

<h2>2. Data Source 분포</h2>
<div class="card">
<div class="stat-grid">
"""
    for src, info in af.items():
        badge_cls = src if src in ('fnguide','dart') else 'unknown'
        html += f"""
  <div class="stat-box">
    <div class="val">{info['stocks']:,}</div>
    <div class="lbl"><span class="badge {badge_cls}">{src}</span><br>종목 수</div>
    <div style="font-size:11px; color:#ccc; margin-top:5px">{info['records']:,} 레코드</div>
  </div>"""
    html += """
</div>
</div>

<h2>3. NULL 비율 (data_source별)</h2>
<div class="card">
<table>
<tr><th>data_source</th><th class="num">전체</th><th class="num">revenue NULL</th>
    <th class="num">op_profit NULL</th><th class="num">net_income NULL</th><th class="num">NULL%</th></tr>
"""
    for row in null_src:
        src = row[0]
        badge_cls = src if src in ('fnguide','dart') else 'unknown'
        null_pct = float(row[5]) if row[5] else 0
        pct_cls = 'green' if null_pct < 5 else 'red'
        html += f"""
<tr>
  <td><span class="badge {badge_cls}">{src}</span></td>
  <td class="num">{row[1]:,}</td>
  <td class="num">{row[2]:,}</td>
  <td class="num">{row[3]:,}</td>
  <td class="num">{row[4]:,}</td>
  <td class="num {pct_cls}">{null_pct:.1f}%</td>
</tr>"""
    html += "</table></div>"

    html += """
<h2>4. FnGuide 레코드 샘플 (연간, 최신순 30건)</h2>
<div class="card">
<table>
<tr><th>종목코드</th><th>종목명</th><th>연도</th><th>보고서</th><th>source</th>
    <th class="num">매출액</th><th class="num">영업이익</th><th class="num">순이익</th>
    <th class="num">자산총계</th><th class="num">자본총계</th></tr>
"""
    for r in samples:
        html += f"""
<tr>
  <td>{r['stock_code']}</td><td>{r['stock_name'] or '-'}</td>
  <td>{r['year']}</td><td>{r['report_type'] or '-'}</td>
  <td><span class="badge fnguide">{r['data_source']}</span></td>
  <td class="num">{fmt(r['revenue'])}</td>
  <td class="num">{fmt(r['operating_profit'])}</td>
  <td class="num">{fmt(r['net_income'])}</td>
  <td class="num">{fmt(r['total_assets'])}</td>
  <td class="num">{fmt(r['total_equity'])}</td>
</tr>"""
    html += "</table></div>"

    html += f"""
<h2>5. 종목별 키 매핑 (financial_profiles.json 샘플)</h2>
<div class="card">
<p>FnGuide 수집 시 사용된 계정명 — 비표준 계정명만 저장 (기본 키워드 제외)</p>
<pre>{json.dumps(dict(profiles), ensure_ascii=False, indent=2)}</pre>
</div>

<h2>6. 마이그레이션 상세 로그</h2>
<div class="card">
<pre>{json.dumps(mig, ensure_ascii=False, indent=2)}</pre>
</div>

<h2>7. 검증 체크리스트 (OpenAI 확인용)</h2>
<div class="card">
<table>
<tr><th>항목</th><th>기대값</th><th>확인</th></tr>
<tr><td>financial_data.data_source 컬럼 존재</td><td>YES</td><td>□</td></tr>
<tr><td>cash_flow_data.data_source 컬럼 존재</td><td>YES</td><td>□</td></tr>
<tr><td>FnGuide 레코드 data_source='fnguide'</td><td>전부 마킹</td><td>□</td></tr>
<tr><td>DART 수집 시 fnguide 레코드 덮어쓰기 없음</td><td>crud.py 보호</td><td>□</td></tr>
<tr><td>FnGuide 수집 후 profiles.json 키 증가</td><td>6종목 → 수백종목</td><td>□</td></tr>
<tr><td>2025Q4 기준 fnguide 종목 수</td><td>>= 2,000종목</td><td>□</td></tr>
<tr><td>fnguide revenue NULL% &lt; 5%</td><td>품질 기준</td><td>□</td></tr>
</table>
</div>

<p style="color:#aaa; font-size:11px; text-align:center; margin-top:40px">
Generated by verification/generate_report.py |
Before: {before.get('snapshot_at','-')} |
After: {after.get('snapshot_at','-')}
</p>
</body>
</html>"""

    with open(OUT_HTML, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"리포트 저장: {OUT_HTML}")
    return OUT_HTML


if __name__ == "__main__":
    generate()
