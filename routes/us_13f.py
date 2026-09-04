"""SEC Form 13F comparison for a curated group of well-known US investors.

13F is a delayed quarterly disclosure, not a real-time trade feed.  The API keeps
the reported issuer name/CUSIP intact because a 13F information table normally does
not contain a verified ticker symbol.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
from fastapi import APIRouter, HTTPException, Query

router = APIRouter()

SEC_HEADERS = {
    "User-Agent": os.getenv("SEC_USER_AGENT", "StockDashboard research contact@stock-dashboard.local"),
    "Accept-Encoding": "gzip, deflate",
    "Accept": "application/json,text/xml,application/xml,*/*",
}
CACHE_SECONDS = 12 * 60 * 60
CACHE_PATH = Path("data/us_13f_cache.json")
CACHE_SCHEMA_VERSION = 4

# CIK values identify the reporting manager, while display names make the screen
# useful without implying that any manager is currently active or still managed by
# the named individual.
MANAGERS = {
    "berkshire": {"name": "Berkshire Hathaway", "manager": "Warren Buffett", "cik": "0001067983"},
    "bridgewater": {"name": "Bridgewater Associates", "manager": "Ray Dalio", "cik": "0001350694"},
    "pershing": {"name": "Pershing Square", "manager": "Bill Ackman", "cik": "0001336528"},
    "scion": {"name": "Scion Asset Management", "manager": "Michael Burry", "cik": "0001649339"},
    "soros": {"name": "Soros Fund Management", "manager": "Soros Fund", "cik": "0001029160"},
    "appaloosa": {"name": "Appaloosa Management", "manager": "David Tepper", "cik": "0001656456"},
    "third_point": {"name": "Third Point", "manager": "Dan Loeb", "cik": "0001040273"},
    "greenlight": {"name": "Greenlight Capital", "manager": "David Einhorn", "cik": "0001079114"},
    "duquesne": {"name": "Duquesne Family Office", "manager": "Stanley Druckenmiller", "cik": "0001536411"},
    "renaissance": {"name": "Renaissance Technologies", "manager": "Jim Simons legacy team", "cik": "0001037389"},
    "lone_pine": {"name": "Lone Pine Capital", "manager": "Stephen Mandel", "cik": "0001061165"},
    "tiger_global": {"name": "Tiger Global Management", "manager": "Chase Coleman", "cik": "0001167483"},
    "gates": {"name": "Gates Foundation Trust", "manager": "Bill Gates", "cik": "0001166559"},
    "oaktree": {"name": "Oaktree Capital Management", "manager": "Howard Marks", "cik": "0000949509"},
    "citadel": {"name": "Citadel Advisors", "manager": "Ken Griffin", "cik": "0001423053"},
    "baupost": {"name": "Baupost Group", "manager": "Seth Klarman", "cik": "0001061768"},
    "aqr": {"name": "AQR Capital Management", "manager": "Cliff Asness", "cik": "0001167557"},
    "gotham": {"name": "Gotham Asset Management", "manager": "Joel Greenblatt", "cik": "0001510387"},
    "miller": {"name": "Miller Value Partners", "manager": "Bill Miller", "cik": "0001135778"},
    "maverick": {"name": "Maverick Capital", "manager": "Lee Ainslie", "cik": "0000934639"},
    "fisher": {"name": "Fisher Asset Management", "manager": "Ken Fisher", "cik": "0000850529"},
    "ark": {"name": "ARK Investment Management", "manager": "Cathie Wood", "cik": "0001697748"},
    "viking": {"name": "Viking Global Investors", "manager": "Andreas Halvorsen", "cik": "0001103804"},
    "coatue": {"name": "Coatue Management", "manager": "Philippe Laffont", "cik": "0001135730"},
    "tudor": {"name": "Tudor Investment", "manager": "Paul Tudor Jones", "cik": "0000923093"},
    "paulson": {"name": "Paulson & Co.", "manager": "John Paulson", "cik": "0001035674"},
    "farallon": {"name": "Farallon Capital Management", "manager": "Thomas Steyer", "cik": "0000909661"},
    "balyasny": {"name": "Balyasny Asset Management", "manager": "Dmitry Balyasny", "cik": "0001218710"},
    "millennium": {"name": "Millennium Management", "manager": "Izzy Englander", "cik": "0001273087"},
    "point72": {"name": "Point72 Asset Management", "manager": "Steve Cohen", "cik": "0001603466"},
    "baker_bros": {"name": "Baker Bros. Advisors", "manager": "Julian & Felix Baker", "cik": "0001263508"},
    "perceptive": {"name": "Perceptive Advisors", "manager": "Joseph Edelman", "cik": "0001224962"},
    "ra_capital": {"name": "RA Capital Management", "manager": "Peter Kolchinsky", "cik": "0001346824"},
    "orbimed": {"name": "OrbiMed Advisors", "manager": "Sven Borho", "cik": "0001055951"},
    "deerfield": {"name": "Deerfield Management", "manager": "James Flynn", "cik": "0001009258"},
}

INVESTOR_PROFILES = {
    "berkshire": ("가치·퀄리티", "집중 장기보유, 현금흐름·해자"), "bridgewater": ("매크로·리스크패리티", "거시 국면과 분산"),
    "pershing": ("집중 가치·행동주의", "고확신 집중과 촉매"), "scion": ("역발상 가치", "비대칭·리스크 헤지"),
    "soros": ("글로벌 매크로", "반사성·국면 전환"), "appaloosa": ("이벤트드리븐", "사이클·특수상황"),
    "third_point": ("이벤트드리븐", "촉매·행동주의"), "greenlight": ("가치·공매도 병행", "기업가치와 리스크"),
    "duquesne": ("탑다운 성장·매크로", "고확신 성장과 유동성"), "renaissance": ("퀀트·통계차익", "시스템 신호"),
    "lone_pine": ("성장주", "장기 구조성장"), "tiger_global": ("성장·테크", "인터넷·소프트웨어"),
    "gates": ("장기 퀄리티", "대형 우량주·집중"), "oaktree": ("가치·크레딧", "리스크 통제·디스트레스"),
    "citadel": ("멀티전략·퀀트", "상대가치·리스크북"), "baupost": ("딥밸류", "마진오브세이프티"),
    "aqr": ("팩터 퀀트", "가치·모멘텀·품질 팩터"), "gotham": ("가치·퀀트", "마법공식·분산"),
    "miller": ("가치·역발상", "장기 복리·집중"), "maverick": ("성장 롱숏", "산업 리서치"),
    "fisher": ("성장·글로벌", "대형 성장주·시장 사이클"), "ark": ("혁신 성장·테마", "파괴적 혁신·고변동성"),
    "viking": ("성장 롱숏", "산업 리서치·집중"), "coatue": ("성장·테크", "인터넷·소프트웨어·AI"),
    "tudor": ("글로벌 매크로", "금리·통화·리스크 관리"), "paulson": ("이벤트드리븐", "합병·특수상황"),
    "farallon": ("멀티전략·이벤트", "크레딧·차익거래"), "balyasny": ("멀티전략", "상대가치·리서치"),
    "millennium": ("멀티전략·퀀트", "시장중립·리스크북"), "point72": ("멀티전략·성장", "산업 리서치·롱숏"),
    "baker_bros": ("바이오 전문·집중", "임상 개발·상업화·장기 지분"), "perceptive": ("바이오 전문", "임상 촉매·의료기술"),
    "ra_capital": ("바이오 전문", "생명과학 연구·임상·규제 촉매"), "orbimed": ("바이오 전문·글로벌", "의약품·의료기기·헬스케어"),
    "deerfield": ("바이오 전문·멀티전략", "헬스케어 혁신·임상 촉매"),
}


_refresh_lock = threading.Lock()
_refresh_running = False


def _cache_read(allow_stale: bool = False) -> dict | None:
    try:
        data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        if data.get("schema_version") == CACHE_SCHEMA_VERSION and (allow_stale or time.time() - float(data.get("cached_epoch") or 0) < CACHE_SECONDS):
            return data
    except Exception:
        return None
    return None


def _cache_write(data: dict) -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _sec_get(url: str, *, json_response: bool = False):
    # SEC recommends staying below ten requests per second.
    time.sleep(0.12)
    response = requests.get(url, headers=SEC_HEADERS, timeout=20)
    if response.status_code != 200:
        raise RuntimeError(f"SEC HTTP {response.status_code}")
    return response.json() if json_response else response.text


def _recent_13f_filings(cik: str) -> list[dict]:
    payload = _sec_get(f"https://data.sec.gov/submissions/CIK{cik}.json", json_response=True)
    recent = (payload.get("filings") or {}).get("recent") or {}
    rows = []
    for index, form in enumerate(recent.get("form") or []):
        if form not in {"13F-HR", "13F-HR/A"}:
            continue
        accession = str((recent.get("accessionNumber") or [""])[index])
        if not accession:
            continue
        rows.append({
            "form": form,
            "accession": accession,
            "filing_date": str((recent.get("filingDate") or [""])[index]),
            "report_date": str((recent.get("reportDate") or [""])[index]),
            "primary_document": str((recent.get("primaryDocument") or [""])[index]),
        })
    return rows


def _filing_holdings(cik: str, filing: dict) -> list[dict]:
    accession_no_dash = filing["accession"].replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession_no_dash}"
    index = _sec_get(f"{base}/index.json", json_response=True)
    files = [item.get("name", "") for item in ((index.get("directory") or {}).get("item") or [])]
    candidates = [name for name in files if name.lower().endswith(".xml") and "primary" not in name.lower()]
    info_name = next((name for name in candidates if "info" in name.lower()), candidates[0] if candidates else "")
    if not info_name:
        raise RuntimeError("13F information table XML not found")
    root = ET.fromstring(_sec_get(f"{base}/{info_name}"))
    aggregated: dict[str, dict] = {}
    for node in root.findall(".//{*}infoTable"):
        def text(tag: str) -> str:
            child = node.find(f".//{{*}}{tag}")
            return (child.text or "").strip() if child is not None else ""

        cusip = text("cusip")
        issuer = text("nameOfIssuer")
        shares = text("sshPrnamt")
        value = text("value")
        if not issuer or not cusip:
            continue
        try:
            shares_value = float(shares.replace(",", ""))
            value_usd = float(value.replace(",", ""))
        except ValueError:
            continue
        item = aggregated.setdefault(cusip, {"issuer": issuer, "cusip": cusip, "shares": 0.0, "value_usd": 0.0})
        item["shares"] += shares_value
        item["value_usd"] += value_usd
    return list(aggregated.values())


def _change_rows(current: list[dict], previous: list[dict]) -> list[dict]:
    before = {str(row["cusip"]): row for row in previous}
    after = {str(row["cusip"]): row for row in current}
    changes: list[dict] = []
    for cusip in set(before) | set(after):
        old, new = before.get(cusip), after.get(cusip)
        old_shares = float((old or {}).get("shares") or 0)
        new_shares = float((new or {}).get("shares") or 0)
        if old_shares == new_shares:
            continue
        delta_pct = None if old_shares <= 0 else (new_shares / old_shares - 1) * 100
        if old_shares <= 0:
            action = "new"
        elif new_shares <= 0:
            action = "exit"
        elif new_shares > old_shares:
            action = "add"
        else:
            action = "reduce"
        source = new or old or {}
        current_value = float((new or {}).get("value_usd") or 0)
        prior_value = float((old or {}).get("value_usd") or 0)
        share_delta = new_shares - old_shares
        reference_shares = new_shares if new_shares > 0 else old_shares
        reference_value = current_value if new_shares > 0 else prior_value
        change_notional = abs(share_delta) * (reference_value / reference_shares) if reference_shares else 0.0
        changes.append({
            "issuer": source.get("issuer"), "cusip": cusip, "action": action,
            "shares": new_shares, "prior_shares": old_shares,
            "shares_change_pct": delta_pct,
            "reported_value_usd": current_value or prior_value,
            "prior_reported_value_usd": prior_value,
            "change_notional_usd": change_notional,
            "transaction_date": None,
            "transaction_date_status": "13F does not disclose the actual trade date",
        })
    changes.sort(key=lambda row: float(row.get("change_notional_usd") or 0), reverse=True)
    return changes


def _load_manager(key: str, meta: dict) -> dict:
    filings = _recent_13f_filings(meta["cik"])
    originals = [row for row in filings if row["form"] == "13F-HR"]
    if len(originals) < 2:
        raise RuntimeError("at least two original 13F filings are required")
    current, prior = originals[0], originals[1]
    current["source_url"] = (
        f"https://www.sec.gov/Archives/edgar/data/{int(meta['cik'])}/"
        f"{current['accession'].replace('-', '')}/"
    )
    prior["source_url"] = (
        f"https://www.sec.gov/Archives/edgar/data/{int(meta['cik'])}/"
        f"{prior['accession'].replace('-', '')}/"
    )
    holdings = _filing_holdings(meta["cik"], current)
    changes = _change_rows(holdings, _filing_holdings(meta["cik"], prior))
    total_value = sum(float(row.get("value_usd") or 0) for row in holdings)
    for change in changes:
        change["position_weight_pct"] = round(float(change.get("reported_value_usd") or 0) / total_value * 100, 3) if total_value else None
        change["report_period_end"] = current["report_date"]
        change["filing_date"] = current["filing_date"]
    return {
        "key": key, **meta,
        "style": INVESTOR_PROFILES.get(key, ("기타", "공시 포트폴리오"))[0],
        "focus": INVESTOR_PROFILES.get(key, ("기타", "공시 포트폴리오"))[1],
        "latest_filing": current,
        "prior_filing": prior,
        "portfolio_total_value_usd": total_value,
        "holdings": sorted(holdings, key=lambda row: float(row.get("value_usd") or 0), reverse=True)[:250],
        "changes": changes[:100],
    }


def _pelosi_ptr_transactions() -> dict:
    """Fetch Nancy Pelosi's House PTRs separately from SEC 13F reports."""
    search_url = "https://disclosures-clerk.house.gov/FinancialDisclosure/ViewSearch"
    session = requests.Session()
    page = session.get(search_url, timeout=20).text
    token = re.search(r'name="__RequestVerificationToken" type="hidden" value="([^"]+)', page)
    if not token:
        raise RuntimeError("House Clerk verification token unavailable")
    html = session.post(
        "https://disclosures-clerk.house.gov/FinancialDisclosure/ViewMemberSearchResult",
        data={"LastName": "Pelosi", "FilingYear": str(datetime.now().year), "State": "CA", "District": "11", "__RequestVerificationToken": token.group(1)},
        headers={"Referer": search_url}, timeout=20,
    ).text
    paths = re.findall(r'href="(public_disc/ptr-pdfs/\d+/\d+\.pdf)"', html)[:6]
    rows = []
    for path in paths:
        source_url = f"https://disclosures-clerk.house.gov/{path}"
        pdf = session.get(source_url, timeout=30).content
        with tempfile.NamedTemporaryFile(suffix=".pdf") as source:
            source.write(pdf)
            source.flush()
            extracted = subprocess.run(["pdftotext", "-layout", source.name, "-"], capture_output=True, text=True, timeout=45).stdout
        pattern = re.compile(
            r"(?:^|\n)\s*(?:SP\s+)?(?P<asset>.+?)\s+(?P<action>P|S(?: \(partial\))?)\s+(?P<date>\d{2}/\d{2}/\d{4})\s+\d{2}/\d{2}/\d{4}\s+(?P<amount>\$[\d,]+(?:\s*-\s*\$[\d,]+)?|\$\d+\.\d+)",
            re.S,
        )
        for match in pattern.finditer(extracted):
            asset = " ".join(match.group("asset").split())
            if len(asset) > 180 or asset.startswith(("ID ", "Owner ", "Filing")):
                continue
            ticker = re.search(r"\(([A-Z.]{1,8})\)", asset)
            rows.append({
                "issuer": asset,
                "ticker": ticker.group(1) if ticker else None,
                "action": "buy" if match.group("action") == "P" else "sell",
                "transaction_date": match.group("date"),
                "amount_range": " ".join(match.group("amount").split()),
                "source_url": source_url,
            })
    deduped = {(row["issuer"], row["action"], row["transaction_date"], row["amount_range"]): row for row in rows}
    transactions = sorted(
        deduped.values(),
        key=lambda row: datetime.strptime(row["transaction_date"], "%m/%d/%Y"),
        reverse=True,
    )
    return {
        "key": "nancy_pelosi", "name": "U.S. House PTR", "manager": "Nancy Pelosi", "source_type": "House PTR", "style": "정치인 거래공시", "focus": "PTR 신고 거래 (보유 포트폴리오 아님)",
        "latest_filing": {"report_date": transactions[0]["transaction_date"] if transactions else None, "source_url": transactions[0]["source_url"] if transactions else search_url},
        "holdings": [], "changes": transactions[:80],
    }


def _buffett_cash_history() -> list[dict]:
    facts = _sec_get("https://data.sec.gov/api/xbrl/companyfacts/CIK0001067983.json", json_response=True)
    gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    tags = (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "CashEquivalentsAtCarryingValue",
        "ShortTermInvestments",
    )
    by_end: dict[str, dict] = {}
    for tag in tags:
        for unit, values in ((gaap.get(tag) or {}).get("units") or {}).items():
            if unit != "USD":
                continue
            for row in values:
                if row.get("form") not in {"10-Q", "10-K"} or not row.get("end"):
                    continue
                item = by_end.setdefault(row["end"], {"date": row["end"], "cash_usd": 0.0, "short_term_investments_usd": 0.0})
                key = "short_term_investments_usd" if tag == "ShortTermInvestments" else "cash_usd"
                if row.get("filed", "") >= item.get(f"{key}_filed", ""):
                    item[key] = float(row.get("val") or 0)
                    item[f"{key}_filed"] = row.get("filed", "")
    rows = []
    for item in by_end.values():
        item["liquidity_usd"] = item["cash_usd"] + item["short_term_investments_usd"]
        rows.append({key: value for key, value in item.items() if not key.endswith("_filed")})
    return sorted(rows, key=lambda row: row["date"])[-20:]


def _aggregate(managers: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for manager in managers:
        for change in manager.get("changes") or []:
            key = str(change.get("cusip") or "")
            if not key:
                continue
            item = grouped.setdefault(key, {
                "issuer": change.get("issuer"), "cusip": key,
                "new_count": 0, "add_count": 0, "reduce_count": 0, "exit_count": 0,
                "reported_value_usd": 0.0, "managers": [],
                "buy_notional_usd": 0.0, "sell_notional_usd": 0.0,
            })
            item[f"{change['action']}_count"] += 1
            item["reported_value_usd"] += float(change.get("reported_value_usd") or 0)
            notional = float(change.get("change_notional_usd") or 0)
            if change["action"] in {"new", "add"}:
                item["buy_notional_usd"] += notional
            else:
                item["sell_notional_usd"] += notional
            item["managers"].append({"name": manager["name"], "manager": manager["manager"], "action": change["action"], "change_notional_usd": notional, "position_weight_pct": change.get("position_weight_pct")})
    rows = list(grouped.values())
    for item in rows:
        item["buying_count"] = item["new_count"] + item["add_count"]
        item["selling_count"] = item["reduce_count"] + item["exit_count"]
        item["net_manager_count"] = item["buying_count"] - item["selling_count"]
    rows.sort(key=lambda item: (item["net_manager_count"], item["reported_value_usd"]), reverse=True)
    return rows[:60]


def _ai_hypothesis(change: dict) -> str:
    """Evidence-bounded interpretation, deliberately not an investment recommendation."""
    action = change.get("action")
    delta = change.get("shares_change_pct")
    value = float(change.get("reported_value_usd") or 0)
    scale = "large reported position" if value >= 1_000_000_000 else "reported position"
    if action == "new":
        return f"AI hypothesis: a new {scale}; review valuation, earnings revision, and the manager's mandate before treating it as conviction."
    if action == "add":
        size = "material" if delta is not None and delta >= 50 else "incremental"
        return f"AI hypothesis: {size} accumulation versus the prior quarter; it may reflect conviction, rebalancing, or an event-driven thesis."
    if action == "reduce":
        return "AI hypothesis: a partial reduction; distinguish profit-taking or rebalancing from a negative fundamental view using current filings and results."
    return "AI hypothesis: the reported position disappeared; the disclosure cannot tell whether it was sold for a thesis change, risk control, or portfolio rebalancing."


def _build_13f_summary() -> dict:
    """Network-heavy SEC refresh. Scheduler/background thread only."""
    managers, errors = [], []
    for key, meta in MANAGERS.items():
        try:
            manager = _load_manager(key, meta)
            for change in manager["changes"]:
                change["ai_hypothesis"] = _ai_hypothesis(change)
            managers.append(manager)
        except Exception as exc:
            errors.append({"key": key, "name": meta["name"], "error": str(exc)})
    if not managers:
        raise HTTPException(status_code=503, detail={"message": "SEC 13F data is temporarily unavailable", "errors": errors})
    politics, political_errors = [], []
    try:
        politics.append(_pelosi_ptr_transactions())
    except Exception as exc:
        political_errors.append({"key": "nancy_pelosi", "name": "Nancy Pelosi", "error": str(exc)})
    payload = {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "source": "SEC EDGAR Form 13F-HR",
        "managers": managers,
        "politicians": politics,
        "consensus": _aggregate(managers),
        "errors": errors + political_errors,
        "disclaimer": "13F is a delayed quarterly long-position disclosure. It omits many instruments and does not prove a current trade or provide a buy/sell recommendation.",
    }
    payload["cached_epoch"] = time.time()
    payload["schema_version"] = CACHE_SCHEMA_VERSION
    _cache_write(payload)
    return payload


def _refresh_async() -> None:
    global _refresh_running
    try:
        with _refresh_lock:
            _build_13f_summary()
    finally:
        _refresh_running = False


@router.get("/summary")
def get_13f_summary(force: bool = Query(False)):
    """Always return the saved snapshot; never block the page on SEC I/O."""
    global _refresh_running
    cached = _cache_read(allow_stale=True)
    if force and not _refresh_running:
        _refresh_running = True
        threading.Thread(target=_refresh_async, name="13f-refresh", daemon=True).start()
    if cached:
        cached["cache_status"] = "refreshing" if _refresh_running else "ready"
        cached["cache_age_seconds"] = max(0, round(time.time() - float(cached.get("cached_epoch") or 0)))
        return cached
    raise HTTPException(status_code=503, detail="13F 스냅샷이 아직 준비되지 않았습니다. 백그라운드 갱신 후 다시 시도하세요.")


@router.get("/buffett-cash")
def get_buffett_cash_history():
    """Berkshire cash and short-term investments from SEC company facts."""
    try:
        return {"source": "SEC companyfacts / Berkshire Hathaway 10-Q, 10-K", "series": _buffett_cash_history()}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Berkshire cash data unavailable: {exc}")
