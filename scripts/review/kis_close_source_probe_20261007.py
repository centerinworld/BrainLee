#!/usr/bin/env python3
"""KIS 당일 봉이 장 마감 뒤 바뀌는 원인 확정용 관측(2026-10-07, REVIEW_PLAN §19-2 1번) — 읽기 전용.

같은 종목들을 KIS 일봉(FHKST03010100)으로 시장 코드 J(KRX)·NX(대체거래소)·UN(통합) 세 가지로 조회하고,
현재가(FHKST01010100) 응답의 종가 관련 필드도 함께 저장한다. 실행 시각마다 한 파일:
  research_outputs/kis_close_probe_20261007/<YYYYMMDD_HHMM>.json
`--compare` : 저장된 관측을 KRX 공식 종가(stock_price_daily)와 대조한 표를 출력·저장(compare.json / compare.md).
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import collect_kis_ohlcv as k  # noqa: E402

OUT = ROOT / "research_outputs" / "kis_close_probe_20261007"
# 대형 2 + 중소형 3(장후 거래가 잦은 종목 포함) + 10-06 실측에서 값이 바뀐 종목
CODES = ["005930", "000660", "108860", "443060", "432320", "491000", "069540", "158430"]
HEAD = lambda tr: {"Content-Type": "application/json", "authorization": f"Bearer {TOKEN}", "appkey": k.KIS_APP_KEY,
                   "appsecret": k.KIS_APP_SECRET, "tr_id": tr, "custtype": "P"}
TOKEN = None


def get(path, tr, params):
    for _ in range(3):
        k._rate_wait()
        try:
            return requests.get(f"{k.KIS_URL}{path}", headers=HEAD(tr), params=params, timeout=10).json()
        except Exception as e:  # noqa: BLE001
            err = str(e)
            time.sleep(1)
    return {"error": err}


def probe():
    global TOKEN
    TOKEN = k.get_token()
    day = datetime.now().strftime("%Y%m%d")
    res = {"at": datetime.now().isoformat(timespec="seconds"), "day": day, "codes": {}}
    for code in CODES:
        row = {}
        for mk in ("J", "NX", "UN"):
            d = get("/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", "FHKST03010100",
                    {"FID_COND_MRKT_DIV_CODE": mk, "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": day, "FID_INPUT_DATE_2": day,
                     "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"})
            bar = next((b for b in (d.get("output2") or []) if b.get("stck_bsop_date") == day), None)
            row[f"daily_{mk}"] = {"close": bar.get("stck_clpr"), "vol": bar.get("acml_vol"), "open": bar.get("stck_oprc")} if bar else \
                {"rt_cd": d.get("rt_cd"), "msg": d.get("msg1"), "error": d.get("error")}
            q = get("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                    {"FID_COND_MRKT_DIV_CODE": mk, "FID_INPUT_ISCD": code})
            o = q.get("output") or {}
            row[f"price_{mk}"] = {f: o.get(f) for f in ("stck_prpr", "stck_sdpr", "prdy_vrss", "acml_vol", "stck_oprc")} if o else \
                {"rt_cd": q.get("rt_cd"), "msg": q.get("msg1")}
        res["codes"][code] = row
    OUT.mkdir(parents=True, exist_ok=True)
    fn = OUT / f"{datetime.now():%Y%m%d_%H%M}.json"
    fn.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(fn)


def compare():
    from db_compat import connect_primary_db
    conn = connect_primary_db(readonly=True)
    lines, table = ["| 관측 시각 | 종목 | KRX 공식 종가/거래량 | J 일봉 | NX 일봉 | UN 일봉 | J 현재가 |", "|---|---|---|---|---|---|---|"], []
    for fn in sorted(OUT.glob("2*.json")):
        r = json.loads(fn.read_text())
        for code, row in r["codes"].items():
            off = conn.execute("SELECT close_price, volume FROM stock_price_daily WHERE stock_code=? AND bas_dt=?", (code, r["day"])).fetchone()
            off = tuple(off) if off else (None, None)
            cell = lambda x: f"{x.get('close')}/{x.get('vol')}" if x.get("close") else f"없음({x.get('msg') or x.get('error')})"
            ent = {"at": r["at"], "code": code, "krx": off, **{m: row.get(f"daily_{m}") for m in ("J", "NX", "UN")}, "price_J": row.get("price_J")}
            table.append(ent)
            lines.append(f"| {r['at']} | {code} | {off[0]}/{off[1]} | {cell(row['daily_J'])} | {cell(row['daily_NX'])} | {cell(row['daily_UN'])} | "
                         f"{(row.get('price_J') or {}).get('stck_prpr')}/{(row.get('price_J') or {}).get('acml_vol')} |")
    # 판정 기준(REVIEW_PLAN §21-2 3번, 관측 전에 정함): 거래일별로 J 일봉 종가 vs KRX 공식 종가
    #   ① 15:45 = 공식이고 18:10·20:30 중 하나라도 다름 → 'KRX 시간외 단일가 반영' 확정 → 수집 시각 15:45~16:00 또는 정규장 종가 필드
    #   ② 15:45도 다름 → KIS 당일 봉은 다음 날까지 잠정 → 잠정 표시 + KRX 교체 유지
    verdict = {}
    for e in table:
        hhmm = e["at"][11:16]
        slot = "15:45" if "15:40" <= hhmm < "16:00" else "18:10" if "18:05" <= hhmm < "18:30" else "20:30" if "20:25" <= hhmm < "20:50" else None
        if not slot or e["krx"][0] is None or not (e.get("J") or {}).get("close"):
            continue
        v = verdict.setdefault(e["at"][:10], {}).setdefault(slot, [0, 0])
        v[0] += 1
        v[1] += int(abs(float(e["J"]["close"]) - float(e["krx"][0])) <= 0.5)
    lines.append("")
    lines.append("판정(§21-2 3번): 날짜별 시각 = [비교 종목 수, J 종가 = KRX 공식 수]")
    for day, v in sorted(verdict.items()):
        early = v.get("15:45")
        late_diff = any(x[1] < x[0] for k2, x in v.items() if k2 != "15:45")
        if early and early[1] == early[0] and late_diff:
            res = "① 시간외 단일가 반영 확정 — 수집 시각 15:45~16:00 또는 정규장 종가 필드"
        elif early and early[1] < early[0]:
            res = "② 15:45도 다름 — 잠정 표시 + KRX 교체 유지"
        else:
            res = "판정 불가(관측 부족 또는 시각 간 차이 없음)"
        lines.append(f"- {day}: {v} → {res}")
    (OUT / "compare.json").write_text(json.dumps(table, ensure_ascii=False, indent=1, default=str))
    (OUT / "compare.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--until", default="", help="YYYYMMDD — 이 날짜 뒤에는 아무것도 하지 않음(launchd 관측 기간 제한)")
    a = ap.parse_args()
    if a.until and datetime.now().strftime("%Y%m%d") > a.until:
        sys.exit(0)
    compare() if a.compare else probe()
