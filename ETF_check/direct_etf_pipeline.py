"""ETF-first KIS collection and reverse holding analytics.

KIS currently returns at most 30 components. Coverage is stored on every row;
partial snapshots must not be described as complete exchange PDFs.
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import re
import sqlite3
import time
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import requests

DB_PATH = Path(__file__).with_name("etf_check.db")
MASTER_URL = "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip"
COMPONENT_PATH = "/uapi/etfetn/v1/quotations/inquire-component-stock-price"
PRICE_PATH = "/uapi/etfetn/v1/quotations/inquire-price"
INVESTOR_PATH = "/uapi/domestic-stock/v1/quotations/inquire-investor"
WIDTHS = [2,1,4,4,4,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,9,5,5,1,1,1,2,1,1,1,2,2,2,3,1,3,12,12,8,15,21,2,7,1,1,1,1,1,9,9,9,5,9,8,9,3,1,1,1]
NAMES = ["group","cap_class","large","mid","small","manufacturing","low_liquidity","governance","k200_sector","k100","k50","krx","etp","elw","krx100","auto","semiconductor","bio","bank","spac","energy","steel","overheat","media","construction","unused","security","ship","insurance","transport","sri","reference_price","lot","after_lot","halted","liquidation","managed","warning","warning_notice","unfaithful","backdoor","lock","par_change","capital_change","margin","credit","credit_days","previous_volume","par_value","listed_date","listed_shares_thousand","capital","fiscal_month","ipo_price","preferred","short_overheat","surge","krx300","kospi","revenue","operating_profit","ordinary_profit","net_income","roe","reference_ym","market_cap_100m","group_company","credit_limit","secured_loan","stock_loan"]
LOG = logging.getLogger("etf_direct")


def num(value: Any) -> float | None:
    text = str(value or "").replace(",", "").strip()
    try:
        return float(text) if text else None
    except ValueError:
        return None


def trading_date(value: str | date | None = None) -> str:
    day = value if isinstance(value, date) else (datetime.strptime(str(value).replace("-", ""), "%Y%m%d").date() if value else date.today())
    try:
        from trading_calendar import is_kr_trading_day
        while not is_kr_trading_day(day):
            day -= timedelta(days=1)
    except Exception:
        while day.weekday() >= 5:
            day -= timedelta(days=1)
    return day.strftime("%Y%m%d")


@dataclass(frozen=True)
class ETFMeta:
    ticker: str
    name: str
    market: str = "KOSPI"
    isin: str = ""
    listed_date: str = ""
    listed_shares: float | None = None


@dataclass(frozen=True)
class Snapshot:
    meta: ETFMeta
    base_date: str
    rows: list[dict[str, Any]]
    close: float | None
    nav: float | None
    cu: float | None
    expected: int | None
    source: str = "KIS_OFFICIAL"

    @property
    def coverage(self) -> float | None:
        return min(len(self.rows) / self.expected, 1.0) if self.expected else None

    @property
    def quality(self) -> str:
        if not self.rows:
            return "empty"
        return "partial" if self.expected and len(self.rows) < self.expected else "complete"


class DatabaseManager:
    def __init__(self, path: str | Path = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=60)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=60000")
        return conn

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS etf_meta(etf_ticker TEXT PRIMARY KEY,etf_name TEXT NOT NULL,market TEXT NOT NULL,isin TEXT NOT NULL DEFAULT '',listed_date TEXT NOT NULL DEFAULT '',listed_shares REAL,universe_source TEXT NOT NULL,is_active INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS etf_pdf_daily(base_date TEXT NOT NULL,etf_ticker TEXT NOT NULL,stock_ticker TEXT NOT NULL,stock_name TEXT NOT NULL DEFAULT '',shares_per_cu REAL,estimated_shares REAL,amount_per_cu REAL,estimated_amount REAL,weight REAL,stock_price REAL,cu_quantity REAL,expected_component_count INTEGER,observed_component_count INTEGER,coverage_ratio REAL,quality_status TEXT NOT NULL,source TEXT NOT NULL,collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(base_date,etf_ticker,stock_ticker),FOREIGN KEY(etf_ticker) REFERENCES etf_meta(etf_ticker));
            CREATE INDEX IF NOT EXISTS idx_pdf_date_stock ON etf_pdf_daily(base_date,stock_ticker);
            CREATE INDEX IF NOT EXISTS idx_pdf_etf_date ON etf_pdf_daily(etf_ticker,base_date);
            CREATE TABLE IF NOT EXISTS etf_trading_daily(base_date TEXT NOT NULL,etf_ticker TEXT NOT NULL,close_price REAL,nav REAL,volume REAL,trading_value REAL,net_individual REAL,net_foreigner REAL,net_institution REAL,source TEXT NOT NULL,collected_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,PRIMARY KEY(base_date,etf_ticker),FOREIGN KEY(etf_ticker) REFERENCES etf_meta(etf_ticker));
            CREATE INDEX IF NOT EXISTS idx_trading_date_etf ON etf_trading_daily(base_date,etf_ticker);
            CREATE TABLE IF NOT EXISTS etf_direct_collection_run(run_id INTEGER PRIMARY KEY AUTOINCREMENT,base_date TEXT NOT NULL,stage TEXT NOT NULL,status TEXT NOT NULL,attempted INTEGER NOT NULL,succeeded INTEGER NOT NULL,failed INTEGER NOT NULL,partial INTEGER NOT NULL,details_json TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT NOT NULL);
            """)

    def upsert_meta(self, rows: Iterable[ETFMeta]) -> int:
        data = [(r.ticker,r.name,r.market,r.isin,r.listed_date,r.listed_shares,"KIS_MASTER") for r in rows]
        with self.connect() as conn:
            conn.executemany("""INSERT INTO etf_meta(etf_ticker,etf_name,market,isin,listed_date,listed_shares,universe_source) VALUES(?,?,?,?,?,?,?) ON CONFLICT(etf_ticker) DO UPDATE SET etf_name=excluded.etf_name,market=excluded.market,isin=excluded.isin,listed_date=excluded.listed_date,listed_shares=excluded.listed_shares,universe_source=excluded.universe_source,is_active=1,updated_at=CURRENT_TIMESTAMP""", data)
        return len(data)

    def replace_snapshot(self, snap: Snapshot) -> int:
        scale = snap.meta.listed_shares / snap.cu if snap.meta.listed_shares and snap.cu else None
        data = []
        for row in snap.rows:
            shares, price, amount = num(row.get("shares_per_cu")), num(row.get("stock_price")), num(row.get("amount_per_cu"))
            estimated_shares = shares * scale if shares is not None and scale else None
            estimated_amount = estimated_shares * price if estimated_shares is not None and price is not None else (amount * scale if amount is not None and scale else None)
            data.append((snap.base_date,snap.meta.ticker,row["stock_ticker"],row.get("stock_name", ""),shares,estimated_shares,amount,estimated_amount,num(row.get("weight")),price,snap.cu,snap.expected,len(snap.rows),snap.coverage,snap.quality,snap.source))
        with self.connect() as conn:
            conn.execute("DELETE FROM etf_pdf_daily WHERE base_date=? AND etf_ticker=?", (snap.base_date,snap.meta.ticker))
            conn.executemany("INSERT INTO etf_pdf_daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)", data)
            conn.execute("""INSERT INTO etf_trading_daily(base_date,etf_ticker,close_price,nav,source) VALUES(?,?,?,?,?) ON CONFLICT(base_date,etf_ticker) DO UPDATE SET close_price=COALESCE(excluded.close_price,close_price),nav=COALESCE(excluded.nav,nav),source=excluded.source,collected_at=CURRENT_TIMESTAMP""", (snap.base_date,snap.meta.ticker,snap.close,snap.nav,snap.source))
        return len(data)

    def upsert_trading(self, rows: Iterable[dict[str, Any]]) -> int:
        data = list(rows)
        with self.connect() as conn:
            conn.executemany("""INSERT INTO etf_trading_daily(base_date,etf_ticker,close_price,nav,volume,trading_value,net_individual,net_foreigner,net_institution,source) VALUES(:base_date,:etf_ticker,:close_price,:nav,:volume,:trading_value,:net_individual,:net_foreigner,:net_institution,:source) ON CONFLICT(base_date,etf_ticker) DO UPDATE SET close_price=COALESCE(excluded.close_price,close_price),nav=COALESCE(excluded.nav,nav),volume=COALESCE(excluded.volume,volume),trading_value=COALESCE(excluded.trading_value,trading_value),net_individual=excluded.net_individual,net_foreigner=excluded.net_foreigner,net_institution=excluded.net_institution,source=excluded.source,collected_at=CURRENT_TIMESTAMP""", data)
        return len(data)

    def record(self, day: str, stage: str, result: dict[str, Any], started: str) -> None:
        with self.connect() as conn:
            conn.execute("INSERT INTO etf_direct_collection_run(base_date,stage,status,attempted,succeeded,failed,partial,details_json,started_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (day,stage,"done" if not result["failed"] else "partial",result["attempted"],result["succeeded"],result["failed"],result.get("partial",0),json.dumps(result,ensure_ascii=False),started,datetime.now().isoformat(timespec="seconds")))


class KISETFSource:
    def __init__(self, delay: float = .3, retries: int = 3):
        from kis_client import KISClient
        self.client, self.delay, self.retries = KISClient(), max(delay,0), max(retries,1)
        self.session, self.last_call = requests.Session(), 0.0

    def get(self, path: str, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
        error = None
        for attempt in range(self.retries):
            wait = self.delay - (time.monotonic() - self.last_call)
            if wait > 0:
                time.sleep(wait)
            try:
                token = self.client.get_token()
                if not token:
                    raise RuntimeError("KIS token unavailable")
                response = self.session.get(f"{self.client.base_url}{path}", headers={"Content-Type":"application/json","authorization":f"Bearer {token}","appkey":self.client.app_key,"appsecret":self.client.app_secret,"tr_id":tr_id}, params=params, timeout=20)
                self.last_call = time.monotonic()
                response.raise_for_status()
                payload = response.json()
                if payload.get("rt_cd") == "0":
                    return payload
                raise RuntimeError(payload.get("msg1") or f"rt_cd={payload.get('rt_cd')}")
            except Exception as exc:
                error = exc
                if attempt + 1 < self.retries:
                    time.sleep(min(2 ** attempt, 4))
        raise RuntimeError(f"KIS failed after {self.retries} attempts: {error}")

    @staticmethod
    def parse_master(content: bytes) -> list[ETFMeta]:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            raw = archive.read(archive.namelist()[0])
        result, tail_size = [], sum(WIDTHS)
        for line in raw.splitlines():
            if len(line) <= tail_size + 21:
                continue
            head, tail, offset, fields = line[:-tail_size], line[-tail_size:], 0, {}
            for name, width in zip(NAMES, WIDTHS):
                fields[name] = tail[offset:offset+width].decode("cp949", errors="ignore").strip()
                offset += width
            ticker = head[:9].decode("cp949", errors="ignore").strip()[-6:]
            if fields["group"] != "EF" or not re.fullmatch(r"\d{6}", ticker):
                continue
            listed = num(fields["listed_shares_thousand"])
            result.append(ETFMeta(ticker,head[21:].decode("cp949",errors="ignore").strip(),"KOSPI",head[9:21].decode("cp949",errors="ignore").strip(),fields["listed_date"],listed*1000 if listed is not None else None))
        return result

    def universe(self) -> list[ETFMeta]:
        response = self.session.get(MASTER_URL, timeout=30)
        response.raise_for_status()
        rows = self.parse_master(response.content)
        if len(rows) < 100:
            raise RuntimeError(f"ETF master unexpectedly small: {len(rows)}")
        return rows

    def composition(self, meta: ETFMeta, day: str) -> Snapshot:
        payload = self.get(COMPONENT_PATH,"FHKST121600C0",{"FID_COND_MRKT_DIV_CODE":"J","FID_INPUT_ISCD":meta.ticker,"FID_COND_SCR_DIV_CODE":"11216"})
        info, rows = payload.get("output1") or {}, []
        for item in payload.get("output2") or []:
            ticker = str(item.get("stck_shrn_iscd") or "").strip()
            if re.fullmatch(r"\d{6}", ticker):
                rows.append({"stock_ticker":ticker,"stock_name":str(item.get("hts_kor_isnm") or "").strip(),"shares_per_cu":num(item.get("etf_cu_unit_scrt_cnt")),"amount_per_cu":num(item.get("etf_cnfg_issu_avls")),"weight":num(item.get("etf_cnfg_issu_rlim")),"stock_price":num(item.get("stck_prpr"))})
        expected = num(info.get("etf_cnfg_issu_cnt"))
        return Snapshot(meta,day,rows,num(info.get("stck_prpr")),num(info.get("nav")),num(info.get("etf_cu_unit_scrt_cnt")),int(expected) if expected is not None else None)

    def trading(self, meta: ETFMeta, start: str, end: str) -> list[dict[str, Any]]:
        price = self.get(PRICE_PATH,"FHPST02400000",{"FID_COND_MRKT_DIV_CODE":"J","FID_INPUT_ISCD":meta.ticker}).get("output") or {}
        flows = self.get(INVESTOR_PATH,"FHKST01010900",{"FID_COND_MRKT_DIV_CODE":"J","FID_INPUT_ISCD":meta.ticker}).get("output") or []
        rows = []
        for item in flows:
            day = str(item.get("stck_bsop_date") or "")
            if start <= day <= end:
                rows.append({"base_date":day,"etf_ticker":meta.ticker,"close_price":num(price.get("stck_prpr")) if day==end else None,"nav":num(price.get("nav")) if day==end else None,"volume":num(price.get("acml_vol")) if day==end else None,"trading_value":num(price.get("acml_tr_pbmn")) if day==end else None,"net_individual":num(item.get("prsn_ntby_tr_pbmn")),"net_foreigner":num(item.get("frgn_ntby_tr_pbmn")),"net_institution":num(item.get("orgn_ntby_tr_pbmn")),"source":"KIS_OFFICIAL"})
        return rows


class ETFCollector:
    def __init__(self, db: DatabaseManager, source: KISETFSource):
        self.db, self.source = db, source

    def collect_etf_universe(self, base_date: str | None = None) -> list[ETFMeta]:
        del base_date
        rows = self.source.universe()
        self.db.upsert_meta(rows)
        return rows

    def metas(self, limit: int | None = None) -> list[ETFMeta]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT etf_ticker,etf_name,market,isin,listed_date,listed_shares FROM etf_meta WHERE is_active=1 ORDER BY etf_ticker").fetchall()
        result = [ETFMeta(*row) for row in rows]
        return result[:limit] if limit else result

    def collect_daily_pdf(self, base_date: str | None = None, limit: int | None = None) -> dict[str, Any]:
        day, metas, started = trading_date(base_date), self.metas(limit), datetime.now().isoformat(timespec="seconds")
        result = {"base_date":day,"attempted":len(metas),"succeeded":0,"failed":0,"partial":0,"rows":0,"errors":[]}
        for meta in metas:
            try:
                snap = self.source.composition(meta, day)
                result["rows"] += self.db.replace_snapshot(snap)
                result["succeeded"] += 1
                result["partial"] += snap.quality == "partial"
            except Exception as exc:
                result["failed"] += 1
                if len(result["errors"]) < 30:
                    result["errors"].append({"ticker":meta.ticker,"error":str(exc)})
                LOG.warning("composition %s: %s", meta.ticker, exc)
        self.db.record(day,"pdf",result,started)
        return result

    def collect_daily_trading(self, base_date: str | None = None, lookback_days: int = 30, limit: int | None = None) -> dict[str, Any]:
        end = trading_date(base_date)
        start = (datetime.strptime(end,"%Y%m%d").date()-timedelta(days=lookback_days*2)).strftime("%Y%m%d")
        metas, started = self.metas(limit), datetime.now().isoformat(timespec="seconds")
        result = {"base_date":end,"attempted":len(metas),"succeeded":0,"failed":0,"partial":0,"rows":0,"errors":[]}
        for meta in metas:
            try:
                result["rows"] += self.db.upsert_trading(self.source.trading(meta,start,end))
                result["succeeded"] += 1
            except Exception as exc:
                result["failed"] += 1
                if len(result["errors"]) < 30:
                    result["errors"].append({"ticker":meta.ticker,"error":str(exc)})
        self.db.record(end,"trading",result,started)
        return result


class ETFAnalytics:
    def __init__(self, db: DatabaseManager):
        self.db = db

    def find_etfs_holding_stock(self, stock_ticker: str, base_date: str | None = None) -> pd.DataFrame:
        with self.db.connect() as conn:
            day = base_date or conn.execute("SELECT MAX(base_date) FROM etf_pdf_daily").fetchone()[0]
            if not day:
                return pd.DataFrame()
            return pd.read_sql_query("""SELECT p.base_date,p.etf_ticker,m.etf_name,p.stock_ticker,p.stock_name,p.shares_per_cu,p.estimated_shares,p.amount_per_cu,p.estimated_amount,p.weight,p.quality_status,p.coverage_ratio,p.source FROM etf_pdf_daily p JOIN etf_meta m USING(etf_ticker) WHERE p.stock_ticker=? AND p.base_date=? ORDER BY p.estimated_amount DESC NULLS LAST,p.weight DESC""", conn, params=(stock_ticker,day))

    def get_etf_investor_flow(self, etf_ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
        with self.db.connect() as conn:
            return pd.read_sql_query("SELECT * FROM etf_trading_daily WHERE etf_ticker=? AND base_date BETWEEN ? AND ? ORDER BY base_date", conn, params=(etf_ticker,start_date.replace("-",""),end_date.replace("-","")))

    def get_stock_estimated_flow(self, stock_ticker: str, base_date: str | None = None) -> dict[str, Any]:
        with self.db.connect() as conn:
            current = base_date or conn.execute("SELECT MAX(base_date) FROM etf_pdf_daily").fetchone()[0]
            previous = conn.execute("SELECT MAX(base_date) FROM etf_pdf_daily WHERE base_date<?",(current,)).fetchone()[0] if current else None
            if not current or not previous:
                return {"stock_ticker":stock_ticker,"base_date":current,"compare_date":previous,"rows":[],"summary":None}
            rows = [dict(row) for row in conn.execute("""SELECT c.etf_ticker,m.etf_name,c.weight,c.quality_status,c.coverage_ratio,c.estimated_shares current_shares,p.estimated_shares previous_shares,c.estimated_amount current_amount,p.estimated_amount previous_amount,c.estimated_shares-p.estimated_shares share_change,c.estimated_amount-p.estimated_amount amount_change FROM etf_pdf_daily c JOIN etf_pdf_daily p ON p.etf_ticker=c.etf_ticker AND p.stock_ticker=c.stock_ticker AND p.base_date=? JOIN etf_meta m ON m.etf_ticker=c.etf_ticker WHERE c.base_date=? AND c.stock_ticker=? ORDER BY ABS(c.estimated_amount-p.estimated_amount) DESC""",(previous,current,stock_ticker)).fetchall()]
        changes = [float(row["amount_change"]) for row in rows if row["amount_change"] is not None]
        buys, sells = sum(v for v in changes if v>0), -sum(v for v in changes if v<0)
        gross = buys+sells
        return {"stock_ticker":stock_ticker,"base_date":current,"compare_date":previous,"rows":rows,"summary":{"estimated_buy_amount":buys,"estimated_sell_amount":sells,"estimated_net_amount":buys-sells,"buy_ratio":buys/gross*100 if gross else None,"sell_ratio":sells/gross*100 if gross else None,"etf_count":len(rows),"partial_etf_count":sum(row["quality_status"]!="complete" for row in rows),"interpretation":"PDF estimated holdings change; not exchange-confirmed ETF executions"}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db",default=str(DB_PATH)); parser.add_argument("--date")
    parser.add_argument("--stage",choices=("all","universe","pdf","trading","query"),default="all")
    parser.add_argument("--limit",type=int); parser.add_argument("--delay",type=float,default=.3); parser.add_argument("--stock",default="005930")
    args = parser.parse_args(); logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
    db, collector = DatabaseManager(args.db), ETFCollector(DatabaseManager(args.db),KISETFSource(args.delay))
    if args.stage in {"all","universe"}: print(json.dumps({"universe":len(collector.collect_etf_universe(args.date))},ensure_ascii=False))
    if args.stage in {"all","pdf"}: print(json.dumps(collector.collect_daily_pdf(args.date,args.limit),ensure_ascii=False,indent=2))
    if args.stage in {"all","trading"}: print(json.dumps(collector.collect_daily_trading(args.date,limit=args.limit),ensure_ascii=False,indent=2))
    if args.stage in {"all","query"}:
        analytics=ETFAnalytics(db); print(analytics.find_etfs_holding_stock(args.stock,trading_date(args.date)).head(10).to_string(index=False)); print(json.dumps(analytics.get_stock_estimated_flow(args.stock,trading_date(args.date)),ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
