"""스탁이지 매도 로직 연구용 캐시 빌더: 스냅샷 페어별 보유종목 전체 점수/특징(상한 없음) + 정답(이탈) 저장."""
import json, sys, time
sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
import stockeasy_logic_validator as slv

UNCAPPED = {"max_daily_sell_ratio": 1.0, "max_daily_sell_min": 1, "max_daily_sell_max": 999}

def build(strategy):
    snaps = slv._load_strategy_snapshots(strategy, limit=999)  # 최신순
    rows = []
    for i in range(len(snaps) - 1):
        cur, prev = snaps[i], snaps[i + 1]
        as_of = str(cur.get("analyzed_at", "")).split(" ")[0]
        prev_date = str(prev.get("analyzed_at", "")).split(" ")[0]
        prev_h = []
        for x in prev.get("holdings") or []:
            nm = x.get("name") or x.get("stock_name")
            if not nm:
                continue
            prev_h.append({"name": nm, "stock_code": x.get("stock_code"), "sector": x.get("sector"),
                           "hold_days": x.get("hold_days") or x.get("holding_days") or 0,
                           "profit_pct": x.get("profit_pct") if x.get("profit_pct") is not None else x.get("return_rate", 0)})
        cur_names = {(x.get("name") or x.get("stock_name")) for x in (cur.get("holdings") or [])}
        prev_names = {h["name"] for h in prev_h}
        exits = {(x.get("name") or x.get("stock_name")) for x in (cur.get("exits") or [])}
        truth = sorted({n for n in ((prev_names - cur_names) | exits) if n})
        allo = []
        slv._get_our_sell_candidates(strategy, prev_h, as_of=as_of, sell_cfg=UNCAPPED, all_out=allo)
        rows.append({"as_of": as_of, "prev": prev_date, "n_hold": len(prev_h), "truth": truth,
                     "holdings": allo, "prev_names": sorted(prev_names)})
    return rows

if __name__ == "__main__":
    for s in sys.argv[1:] or ["peak", "momentum", "value"]:
        t0 = time.time()
        r = build(s)
        json.dump(r, open(f"scratch/stockeasy_sell_cache_{s}.json", "w"), ensure_ascii=False)
        print(s, len(r), "pairs", round(time.time() - t0, 1), "s")
