from pathlib import Path

from ETF_check.direct_etf_pipeline import DatabaseManager, ETFAnalytics, ETFMeta, Snapshot


def snap(meta: ETFMeta, day: str, shares: float, price: float) -> Snapshot:
    return Snapshot(meta,day,[{"stock_ticker":"005930","stock_name":"삼성전자","shares_per_cu":shares,"amount_per_cu":shares*price,"weight":30,"stock_price":price}],10_000,10_010,50_000,2,"TEST")


def test_reverse_index_flow_and_idempotency(tmp_path: Path) -> None:
    db=DatabaseManager(tmp_path/"etf.db"); meta=ETFMeta("069500","KODEX 200",listed_shares=100_000); db.upsert_meta([meta])
    db.replace_snapshot(snap(meta,"20260827",100,70_000)); latest=snap(meta,"20260828",120,71_000); db.replace_snapshot(latest); db.replace_snapshot(latest)
    holdings=ETFAnalytics(db).find_etfs_holding_stock("005930","20260828")
    assert len(holdings)==1 and holdings.iloc[0]["estimated_shares"]==240 and holdings.iloc[0]["quality_status"]=="partial"
    result=ETFAnalytics(db).get_stock_estimated_flow("005930","20260828")
    assert result["summary"]["estimated_buy_amount"]==3_040_000 and result["summary"]["buy_ratio"]==100
