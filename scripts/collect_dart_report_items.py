"""
Collect additional DART business/quarterly report line items.

The existing DART collectors cover core financials plus a few CH-sheet fields.
This collector intentionally keeps a wider, auditable quarterly item table for
signals that often appear before price re-rating: working capital, inventory,
contract liabilities, capex, R&D, depreciation, provisions, and debt.

Examples:
  python3 scripts/collect_dart_report_items.py --years 2020 2021 2022 2023 2024 2025 2026 --limit 10000
  python3 scripts/collect_dart_report_items.py --codes 200470,005930 --years 2024 2025 2026
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sqlite3
import sys
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable
import xml.etree.ElementTree as ET

import requests

ROOT = Path("/Applications/stock_dashboard")
DB_PATH = ROOT / "stock.db"
PROGRESS_PATH = ROOT / "run" / "dart_report_items_progress.json"
sys.path.insert(0, str(ROOT))

from dart_key_manager import get_dart_api_keys  # noqa: E402


REPORT_CODES: dict[str, int] = {
    "11013": 1,  # 1분기보고서
    "11012": 2,  # 반기보고서
    "11014": 3,  # 3분기보고서
    "11011": 4,  # 사업보고서
}


@dataclass(frozen=True)
class ItemRule:
    metric: str
    statements: tuple[str, ...]
    account_ids: tuple[str, ...] = ()
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    abs_value: bool = False
    bs_compat: bool = False


ITEM_RULES: tuple[ItemRule, ...] = (
    ItemRule(
        "trade_receivable",
        ("BS",),
        (
            "ifrs-full_CurrentTradeReceivables",
            "ifrs-full_TradeAndOtherCurrentReceivables",
            "dart_ShortTermTradeReceivable",
            "dart_TradeReceivable",
            "dart_AccountsReceivableTrade",
        ),
        ("매출채권", "외상매출금"),
        ("대손", "충당금"),
        bs_compat=True,
    ),
    ItemRule(
        "inventory_assets",
        ("BS",),
        ("ifrs-full_Inventories", "dart_Inventories"),
        ("재고자산", "상품", "제품", "재공품", "원재료"),
        ("평가손실", "충당금", "매출원가", "변동", "금융상품", "파생상품", "지분상품", "금융자산", "금융부채", "투자"),
        bs_compat=True,
    ),
    ItemRule(
        "trade_payable",
        ("BS",),
        (
            "ifrs-full_TradeAndOtherCurrentPayables",
            "ifrs-full_CurrentTradePayables",
            "dart_ShortTermTradePayables",
        ),
        ("매입채무", "외상매입금"),
        ("미지급", "기타"),
        bs_compat=True,
    ),
    ItemRule(
        "contract_assets",
        ("BS",),
        ("ifrs-full_ContractAssets", "ifrs-full_CurrentContractAssets"),
        ("계약자산", "미청구공사"),
        (),
        bs_compat=True,
    ),
    ItemRule(
        "contract_liabilities",
        ("BS",),
        ("ifrs-full_ContractLiabilities", "ifrs-full_CurrentContractLiabilities"),
        ("계약부채", "초과청구공사"),
        (),
        bs_compat=True,
    ),
    ItemRule(
        "advances_received",
        ("BS",),
        (),
        ("선수금", "선수수익"),
        ("계약부채",),
        bs_compat=True,
    ),
    ItemRule(
        "short_term_borrowings",
        ("BS",),
        ("ifrs-full_ShorttermBorrowings", "dart_ShortTermBorrowings"),
        ("단기차입금", "유동성장기차입금", "유동성사채", "유동성장기부채"),
        (),
        bs_compat=True,
    ),
    ItemRule(
        "long_term_borrowings",
        ("BS",),
        ("ifrs-full_LongtermBorrowings", "dart_LongTermBorrowings"),
        ("장기차입금", "비유동차입금", "비유동성차입금", "사채및장기차입금", "사채 및 장기차입금", "장기사채"),
        ("유동성장기", "유동사채", "단기"),
        bs_compat=True,
    ),
    ItemRule(
        "property_plant_equipment",
        ("BS",),
        ("ifrs-full_PropertyPlantAndEquipment",),
        ("유형자산",),
        ("감가상각", "취득", "처분", "사용권"),
        bs_compat=True,
    ),
    ItemRule(
        "construction_in_progress",
        ("BS",),
        (),
        ("건설중인자산", "건설중인 자산"),
        (),
        bs_compat=True,
    ),
    ItemRule(
        "intangible_assets",
        ("BS",),
        ("ifrs-full_IntangibleAssetsOtherThanGoodwill", "ifrs-full_IntangibleAssets"),
        ("무형자산", "개발비"),
        ("상각", "손상", "취득"),
        bs_compat=True,
    ),
    ItemRule(
        "right_of_use_assets",
        ("BS",),
        ("ifrs-full_RightofuseAssets",),
        ("사용권자산", "사용권 자산"),
        ("감가상각", "유형자산 및 사용권자산", "유형자산및사용권자산"),
        bs_compat=True,
    ),
    ItemRule(
        "provisions",
        ("BS",),
        ("ifrs-full_Provisions", "ifrs-full_CurrentProvisions", "ifrs-full_NoncurrentProvisions"),
        ("충당부채", "판매보증충당부채", "복구충당부채"),
        (),
        bs_compat=True,
    ),
    ItemRule(
        "research_development_expense",
        ("IS", "IS1"),
        (),
        ("연구개발비", "경상연구개발비", "연구비", "개발비"),
        ("자산", "무형자산", "개발비상각"),
        abs_value=True,
    ),
    ItemRule(
        "sga_expense",
        ("IS", "IS1"),
        (
            "ifrs-full_SellingGeneralAndAdministrativeExpense",
            "dart_TotalSellingGeneralAdministrativeEx",
            "dart_SellingExpenses",
            "dart_GeneralAndAdministrativeExpense",
        ),
        ("판매비와관리비", "판매관리비", "판관비"),
        (),
        abs_value=True,
    ),
    ItemRule(
        "advertising_expense",
        ("IS", "IS1"),
        (),
        ("광고선전비", "광고비", "판매촉진비", "마케팅비"),
        (),
        abs_value=True,
    ),
    ItemRule(
        "depreciation_amortization_expense",
        ("IS", "IS1", "CF"),
        (
            "ifrs-full_DepreciationAndAmortisationExpense",
            "ifrs-full_AdjustmentsForDepreciationAndAmortisationExpense",
            "dart_AdjustmentsForDepreciationExpense",
            "dart_DepreciationAndAmortisation",
        ),
        ("감가상각비", "상각비", "감가상각"),
        ("누계액", "대손", "대손상각", "기타의대손", "대손충당"),
        abs_value=True,
    ),
    ItemRule(
        "capex_ppe_purchase",
        ("CF",),
        (
            "ifrs-full_PurchaseOfPropertyPlantAndEquipment",
            "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
            "dart_PurchaseOfOtherPropertyPlantAndEquipment",
        ),
        ("유형자산의 취득", "유형자산 취득", "설비투자"),
        ("처분", "매각"),
        abs_value=True,
    ),
    ItemRule(
        "capex_intangible_purchase",
        ("CF",),
        (
            "ifrs-full_PurchaseOfIntangibleAssets",
            "ifrs-full_PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities",
        ),
        ("무형자산의 취득", "무형자산 취득"),
        ("처분", "매각"),
        abs_value=True,
    ),
    ItemRule(
        "inventory_write_down",
        ("IS", "IS1"),
        (),
        ("재고자산평가손실", "재고자산 평가손실", "재고자산감모손실"),
        (),
        abs_value=True,
    ),
)


class DartClient:
    def __init__(self) -> None:
        self.keys = [k for k in get_dart_api_keys() if k]
        if not self.keys:
            raise RuntimeError("DART API key not found")
        self.key_idx = 0
        self.exhausted = False

    @property
    def key(self) -> str:
        return self.keys[self.key_idx % len(self.keys)]

    def next_key(self) -> None:
        self.key_idx = (self.key_idx + 1) % len(self.keys)

    def get_json(self, endpoint: str, params: dict[str, str], timeout: int = 20) -> dict | None:
        for _ in range(len(self.keys)):
            req_params = dict(params)
            req_params["crtfc_key"] = self.key
            try:
                resp = requests.get(
                    f"https://opendart.fss.or.kr/api/{endpoint}",
                    params=req_params,
                    timeout=timeout,
                )
                data = resp.json()
            except Exception as exc:
                print(f"[WARN] {endpoint} request failed: {exc}", flush=True)
                self.next_key()
                time.sleep(0.5)
                continue

            status = str(data.get("status", ""))
            if status == "000":
                return data
            if status == "020":
                print("[WARN] DART quota exceeded; rotating key", flush=True)
                self.next_key()
                time.sleep(1.0)
                continue
            return None

        self.exhausted = True
        return None


def init_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dart_report_items_quarterly (
            stock_code TEXT NOT NULL,
            corp_code TEXT,
            fiscal_year INTEGER NOT NULL,
            fiscal_quarter INTEGER NOT NULL,
            reprt_code TEXT NOT NULL,
            fs_div TEXT NOT NULL,
            metric_name TEXT NOT NULL,
            account_id TEXT,
            account_nm TEXT NOT NULL,
            sj_div TEXT,
            sj_nm TEXT,
            value REAL,
            rcept_no TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (
                stock_code, fiscal_year, fiscal_quarter, fs_div,
                metric_name, account_id, account_nm
            )
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_driq_metric_year
        ON dart_report_items_quarterly(metric_name, fiscal_year, fiscal_quarter)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_driq_stock_year
        ON dart_report_items_quarterly(stock_code, fiscal_year, fiscal_quarter)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dart_bs_items (
            stock_code TEXT,
            year INTEGER,
            quarter INTEGER DEFAULT 4,
            item_key TEXT,
            value REAL,
            report_type TEXT DEFAULT 'CFS',
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (stock_code, year, quarter, item_key, report_type)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dart_report_item_collection_log (
            stock_code TEXT NOT NULL,
            fiscal_year INTEGER NOT NULL,
            fiscal_quarter INTEGER NOT NULL,
            reprt_code TEXT NOT NULL,
            fs_div TEXT NOT NULL,
            corp_code TEXT,
            status TEXT NOT NULL,
            rows_saved INTEGER DEFAULT 0,
            collected_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (stock_code, fiscal_year, fiscal_quarter, fs_div)
        )
        """
    )
    conn.commit()


def load_progress(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        done = data.get("done", [])
        return {str(x) for x in done}
    except Exception:
        return set()


def save_progress(path: Path, done: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.stem}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "done_count": len(done),
                "done": sorted(done),
            },
            f,
            ensure_ascii=False,
        )
    tmp.replace(path)


def progress_key(stock_code: str, year: int, reprt_code: str, fs_div: str) -> str:
    return f"{stock_code}:{year}:{reprt_code}:{fs_div}"


def already_collected(
    conn: sqlite3.Connection,
    stock_code: str,
    year: int,
    quarter: int,
    reprt_code: str,
    fs_div: str,
) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM dart_report_item_collection_log
        WHERE stock_code=? AND fiscal_year=? AND fiscal_quarter=?
          AND reprt_code=? AND fs_div=? AND status IN ('done', 'no_data')
        LIMIT 1
        """,
        (stock_code, year, quarter, reprt_code, fs_div),
    ).fetchone()
    return row is not None


def mark_collected(
    conn: sqlite3.Connection,
    stock_code: str,
    corp_code: str,
    year: int,
    quarter: int,
    reprt_code: str,
    fs_div: str,
    status: str,
    rows_saved: int,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO dart_report_item_collection_log
        (stock_code, fiscal_year, fiscal_quarter, reprt_code, fs_div, corp_code,
         status, rows_saved, collected_at)
        VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
        """,
        (stock_code, year, quarter, reprt_code, fs_div, corp_code, status, rows_saved),
    )


def parse_amount(raw: str | None) -> float | None:
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "")
    if not s or s in {"-", "nan", "None"}:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    try:
        val = float(s)
    except ValueError:
        return None
    return -val if neg else val


def norm_text(value: str | None) -> str:
    return re.sub(r"\s+", "", str(value or "").lower())


def matches_rule(row: sqlite3.Row | dict, rule: ItemRule) -> bool:
    sj_div = str(row.get("sj_div") or "")
    if sj_div not in rule.statements:
        return False
    account_id = str(row.get("account_id") or "").strip()
    account_nm = norm_text(row.get("account_nm"))
    if rule.metric in {"short_term_borrowings", "long_term_borrowings"} and account_nm in {
        "사채및차입금",
        "차입금및사채",
    }:
        return False
    if rule.exclude and any(norm_text(k) in account_nm for k in rule.exclude):
        return False
    if rule.metric == "short_term_borrowings" and "비유동" in account_nm:
        return False
    if rule.account_ids and account_id in rule.account_ids:
        return True
    if rule.include and not any(norm_text(k) in account_nm for k in rule.include):
        return False
    return bool(rule.include)


def load_corp_code_map(client: DartClient) -> dict[str, str]:
    cache_path = Path("/tmp/CORPCODE.xml")
    if cache_path.exists() and time.time() - cache_path.stat().st_mtime < 86400 * 7:
        root = ET.parse(cache_path).getroot()
    else:
        resp = requests.get(
            "https://opendart.fss.or.kr/api/corpCode.xml",
            params={"crtfc_key": client.key},
            timeout=40,
        )
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            data = zf.read("CORPCODE.xml")
        cache_path.write_bytes(data)
        root = ET.fromstring(data)

    corp_map: dict[str, str] = {}
    for item in root.findall(".//list"):
        stock_code = (item.findtext("stock_code") or "").strip()
        corp_code = (item.findtext("corp_code") or "").strip()
        if stock_code and corp_code:
            corp_map[stock_code.zfill(6)] = corp_code.zfill(8)
    return corp_map


def load_target_codes(conn: sqlite3.Connection, args: argparse.Namespace) -> list[str]:
    if args.codes:
        return [c.strip().zfill(6) for c in args.codes.split(",") if c.strip()]
    rows = conn.execute(
        """
        SELECT stock_code
        FROM stock_universe
        WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND COALESCE(market_cap, 0) > 0
        GROUP BY stock_code
        ORDER BY MAX(market_cap) DESC
        LIMIT ?
        """,
        (args.limit,),
    ).fetchall()
    return [str(r[0]).zfill(6) for r in rows]


def upsert_item(
    conn: sqlite3.Connection,
    stock_code: str,
    corp_code: str,
    year: int,
    quarter: int,
    reprt_code: str,
    fs_div: str,
    rule: ItemRule,
    row: dict,
    value: float,
) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO dart_report_items_quarterly
        (stock_code, corp_code, fiscal_year, fiscal_quarter, reprt_code, fs_div,
         metric_name, account_id, account_nm, sj_div, sj_nm, value, rcept_no, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
        """,
        (
            stock_code,
            corp_code,
            year,
            quarter,
            reprt_code,
            fs_div,
            rule.metric,
            row.get("account_id") or "",
            row.get("account_nm") or "",
            row.get("sj_div") or "",
            row.get("sj_nm") or "",
            value,
            row.get("rcept_no") or "",
        ),
    )
    if rule.bs_compat:
        compact_name = norm_text(row.get("account_nm"))
        if rule.metric in {"trade_receivable", "trade_payable"} and (
            "장기" in compact_name or "비유동" in compact_name
        ):
            return
        conn.execute(
            """
            INSERT OR REPLACE INTO dart_bs_items
            (stock_code, year, quarter, item_key, value, report_type, created_at)
            VALUES (?,?,?,?,?,?,datetime('now'))
            """,
            (stock_code, year, quarter, rule.metric, value, fs_div),
        )


def collect_for_stock(
    conn: sqlite3.Connection,
    client: DartClient,
    stock_code: str,
    corp_code: str,
    years: Iterable[int],
    report_codes: Iterable[str],
    fs_divs: Iterable[str],
    done: set[str],
    force: bool = False,
) -> int:
    saved = 0
    for year in years:
        for reprt_code in report_codes:
            quarter = REPORT_CODES[reprt_code]
            for fs_div in fs_divs:
                pkey = progress_key(stock_code, year, reprt_code, fs_div)
                if not force and (pkey in done or already_collected(conn, stock_code, year, quarter, reprt_code, fs_div)):
                    done.add(pkey)
                    continue
                data = client.get_json(
                    "fnlttSinglAcntAll.json",
                    {
                        "corp_code": corp_code,
                        "bsns_year": str(year),
                        "reprt_code": reprt_code,
                        "fs_div": fs_div,
                    },
                )
                if client.exhausted:
                    return saved
                rows = data.get("list") if data else None
                if not rows:
                    mark_collected(conn, stock_code, corp_code, year, quarter, reprt_code, fs_div, "no_data", 0)
                    done.add(pkey)
                    conn.commit()
                    continue
                hit_for_fs = 0
                for row in rows:
                    amount = parse_amount(row.get("thstrm_amount"))
                    if amount is None or amount == 0:
                        continue
                    for rule in ITEM_RULES:
                        if not matches_rule(row, rule):
                            continue
                        value = abs(amount) if rule.abs_value else amount
                        upsert_item(conn, stock_code, corp_code, year, quarter, reprt_code, fs_div, rule, row, value)
                        saved += 1
                        hit_for_fs += 1
                mark_collected(conn, stock_code, corp_code, year, quarter, reprt_code, fs_div, "done", hit_for_fs)
                done.add(pkey)
                conn.commit()
                if fs_div == "CFS" and hit_for_fs:
                    break
    return saved


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes", help="Comma separated stock codes")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--years", nargs="+", type=int, default=list(range(2020, datetime.now().year + 1)))
    parser.add_argument(
        "--reports",
        nargs="+",
        default=["11013", "11012", "11014", "11011"],
        choices=sorted(REPORT_CODES),
    )
    parser.add_argument("--fs-divs", nargs="+", default=["CFS", "OFS"], choices=["CFS", "OFS"])
    parser.add_argument("--sleep", type=float, default=0.12)
    parser.add_argument("--progress-file", default=str(PROGRESS_PATH))
    parser.add_argument("--force", action="store_true", help="Re-fetch combinations even when collection log says done")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    client = DartClient()

    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    init_tables(conn)

    corp_map = load_corp_code_map(client)
    codes = load_target_codes(conn, args)
    progress_path = Path(args.progress_file)
    done = set() if args.force else load_progress(progress_path)

    total_saved = 0
    no_corp = 0
    started = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(
        f"[START] dart report item collection {started} codes={len(codes)} "
        f"years={args.years} resume_done={len(done)} force={args.force}",
        flush=True,
    )
    for idx, stock_code in enumerate(codes, 1):
        corp_code = corp_map.get(stock_code)
        if not corp_code:
            no_corp += 1
            continue
        saved = collect_for_stock(
            conn,
            client,
            stock_code,
            corp_code,
            args.years,
            args.reports,
            args.fs_divs,
            done,
            force=args.force,
        )
        total_saved += saved
        if idx % 20 == 0 or saved:
            print(f"[{idx}/{len(codes)}] {stock_code} saved={saved} total={total_saved}", flush=True)
            save_progress(progress_path, done)
        if client.exhausted:
            print("[STOP] DART quota exhausted across keys", flush=True)
            break
        time.sleep(args.sleep)

    conn.commit()
    save_progress(progress_path, done)
    summary = conn.execute(
        """
        SELECT metric_name, COUNT(*) rows, COUNT(DISTINCT stock_code) stocks,
               MIN(fiscal_year) min_year, MAX(fiscal_year) max_year
        FROM dart_report_items_quarterly
        GROUP BY metric_name
        ORDER BY rows DESC
        """
    ).fetchall()
    print("\n=== summary ===", flush=True)
    print(f"saved_this_run={total_saved} no_corp={no_corp}", flush=True)
    for row in summary:
        print(
            f"{row['metric_name']}: rows={row['rows']} stocks={row['stocks']} "
            f"years={row['min_year']}-{row['max_year']}",
            flush=True,
        )
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
