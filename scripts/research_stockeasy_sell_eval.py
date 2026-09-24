"""스탁이지 매도 로직 연구: C(창 허용 채점) → B(매수조건 이탈) → A(섹터 전파). 시간분할 검증."""
import json, sys, pickle, os
sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
import sqlite3
import stockeasy_logic_validator as slv
from db_compat import connect_primary_db

def load(s):
    rows = json.load(open(f"scratch/stockeasy_sell_cache_{s}.json"))
    return sorted(rows, key=lambda r: r["as_of"])

def base_flag(strategy, h, cfg):
    if strategy == "value":
        return h["primary"] and h["score"] >= int(cfg.get("score_cut", 8))
    return (h["primary"] or h["hard_loss"] or h["parabolic_tp"]) and h["score"] >= int(cfg.get("score_cut", 2))

def prf(pred, exits, W):
    """pred/exits: set of (name, k). k=페어 인덱스. 예측이 실제이탈 W 이전~당일이면 적중."""
    exit_by = {}
    for n, k in exits: exit_by.setdefault(n, []).append(k)
    pred_by = {}
    for n, k in pred: pred_by.setdefault(n, []).append(k)
    tp_p = sum(1 for n, k in pred if any(k <= e <= k + W for e in exit_by.get(n, [])))
    tp_r = sum(1 for n, k in exits if any(k - W <= p <= k for p in pred_by.get(n, [])))
    P = tp_p / len(pred) if pred else 0; R = tp_r / len(exits) if exits else 0
    F = 2 * P * R / (P + R) if P + R else 0
    return round(P * 100, 1), round(R * 100, 1), round(F * 100, 1)

def run(strategy):
    rows = load(strategy)
    cfg = slv._get_sell_cfg(strategy)
    exits = {(n, k) for k, r in enumerate(rows) for n in r["truth"]}
    base = {(h["name"], k) for k, r in enumerate(rows) for h in r["holdings"] if base_flag(strategy, h, cfg)}
    out = {"strategy": strategy, "pairs": len(rows), "exits": len(exits), "base_flags": len(base)}
    out["C_base"] = {W: prf(base, exits, W) for W in (0, 3, 5, 10)}
    # ── B: 매수조건 이탈 ──
    cache_p = f"scratch/stockeasy_entry_ok_{strategy}.pkl"
    if os.path.exists(cache_p): ok = pickle.load(open(cache_p, "rb"))
    else:
        conn = connect_primary_db(); conn.row_factory = sqlite3.Row; ok = {}
        for k, r in enumerate(rows):
            for h in r["holdings"]:
                v, _ = slv._entry_signal_ok(conn, strategy, h["code"], r["as_of"])
                ok[(h["name"], k)] = v
        pickle.dump(ok, open(cache_p, "wb"))
    offB = {key for key, v in ok.items() if v is False}
    B1 = offB
    B2 = {(n, k) for (n, k) in offB if ok.get((n, k - 1)) is False}  # 2일 연속 이탈
    out["B_off1"] = {W: prf(B1, exits, W) for W in (0, 5)}; out["B_off1_n"] = len(B1)
    out["B_off2"] = {W: prf(B2, exits, W) for W in (0, 5)}; out["B_off2_n"] = len(B2)
    out["B_or_base"] = {W: prf(B2 | base, exits, W) for W in (0, 5)}
    out["B_and_base"] = {W: prf(B2 & base, exits, W) for W in (0, 5)}
    # ── A: 섹터 전파 (기저 신호 셋 = base ∪ trend_break ) ──
    split = int(len(rows) * 0.6)
    def sector_prop(sig_fn, theta, minn, ks):
        pred = set()
        for k in ks:
            r = rows[k]; bysec = {}
            for h in r["holdings"]: bysec.setdefault(h.get("sector") or "?", []).append(h)
            for sec, hs in bysec.items():
                flagged = [h for h in hs if sig_fn(h, k)]
                if len(hs) >= minn and len(flagged) / len(hs) >= theta:
                    for h in hs: pred.add((h["name"], k))
        return pred
    sigs = {"base": lambda h, k: (h["name"], k) in base,
            "trend_break": lambda h, k: h["trend_break"],
            "offB": lambda h, k: (h["name"], k) in offB}
    res = {}
    for sname, sf in sigs.items():
        for theta in (0.3, 0.5, 0.7):
            for minn in (2, 3):
                trk, tek = range(0, split), range(split, len(rows))
                ptr = sector_prop(sf, theta, minn, trk) | {p for p in base if p[1] < split}
                pte = sector_prop(sf, theta, minn, tek) | {p for p in base if p[1] >= split}
                etr = {e for e in exits if e[1] < split}; ete = {e for e in exits if e[1] >= split}
                res[f"{sname}|th{theta}|n{minn}"] = {"train": prf(ptr, etr, 5), "test": prf(pte, ete, 5), "npred": len(ptr | pte)}
    out["A"] = res
    out["base_split"] = {"train": prf({p for p in base if p[1] < split}, {e for e in exits if e[1] < split}, 5),
                         "test": prf({p for p in base if p[1] >= split}, {e for e in exits if e[1] >= split}, 5)}
    return out

if __name__ == "__main__":
    for s in sys.argv[1:] or ["peak", "momentum", "value"]:
        o = run(s)
        json.dump(o, open(f"scratch/stockeasy_sell_eval_{s}.json", "w"), ensure_ascii=False, indent=1)
        print("=====", s, {k: o[k] for k in ("pairs", "exits", "base_flags")})
        print("C 창허용(P,R,F1) base:", o["C_base"])
        print("B off1", o["B_off1"], o["B_off1_n"], "| off2", o["B_off2"], o["B_off2_n"])
        print("B or base", o["B_or_base"], "| B and base", o["B_and_base"])
        print("base split", o["base_split"])
        best = sorted(o["A"].items(), key=lambda kv: -kv[1]["train"][2])[:5]
        for k, v in best: print("A", k, v)
