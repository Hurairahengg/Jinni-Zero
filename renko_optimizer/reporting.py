import json, csv
from pathlib import Path
import numpy as np

def ensure_dir(p):
    Path(p).mkdir(parents=True, exist_ok=True)
    return Path(p)

def export_leaderboard(leaderboard, out_dir):
    d = ensure_dir(out_dir)
    rows = []
    for r in leaderboard:
        rows.append({
            "L": r["L"], "K": r["K"], "side": r["side"],
            "sl_model": r["sl_model"],
            "sl_param": r.get("sl_param") if r.get("sl_param") is not None else "",
            "pattern": "".join("+" if x==1 else "-" for x in r["pattern"]),
            "trades": r["trades"],
            "expectancy": round(r["expectancy"], 4),
            "profit_factor": round(r["profit_factor"], 3),
            "sharpe": round(r["sharpe"], 3),
            "net_usd": round(r["net_usd"], 2),
            "stability": round(r["stability"], 3),
            "score": round(r["score"], 4),
            "folds_active": r["folds_active"],
        })
    fp = d / "leaderboard.csv"
    with open(fp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["empty"])
        w.writeheader(); w.writerows(rows)
    print(f"  ✓ leaderboard → {fp}")

def export_rule_json(rule, out_dir):
    d = ensure_dir(out_dir) / "rules"
    d.mkdir(exist_ok=True)
    sl_tag = (f"{rule['sl_model']}{rule['sl_param']:g}"
              if rule.get("sl_param") is not None else rule["sl_model"])
    name = f"L{rule['L']}_K{rule['K']}_{rule['side']}_{sl_tag}_" \
           + "".join("U" if x==1 else "D" for x in rule["pattern"])
    payload = {
        "version": "1.0", "name": name,
        "trigger": {"L": rule["L"], "pattern": rule["pattern"], "side": rule["side"]},
        "exit": {"K": rule["K"], "sl_model": rule["sl_model"]},
        "metrics": {k: rule[k] for k in
                    ["trades","expectancy","profit_factor","sharpe","net_usd",
                     "stability","score","folds_active"]},
    }
    fp = d / f"{name}.json"
    fp.write_text(json.dumps(payload, indent=2))

def export_trades(rule, out_dir):
    d = ensure_dir(out_dir) / "trades"
    d.mkdir(exist_ok=True)
    sl_tag = (f"{rule['sl_model']}{rule['sl_param']:g}"
              if rule.get("sl_param") is not None else rule["sl_model"])
    name = f"L{rule['L']}_K{rule['K']}_{rule['side']}_{sl_tag}"
    all_trades = []
    for f in rule["all_folds"]:
        for t in f["trades"]:
            all_trades.append({
                "fold": f["fold"], "entry_idx": t["entry_idx"], "exit_idx": t["exit_idx"],
                "side": t["side"], "entry_px": t["entry_px"], "exit_px": t["exit_px"],
                "fill_entry": t["fill_entry"], "fill_exit": t["fill_exit"],
                "gross_pts": t["gross_pts"], "net_usd": t["net_usd"],
                "reason": t["reason"], "mae": t["mae"], "mfe": t["mfe"],
            })
    if not all_trades: return
    fp = d / f"{name}.csv"
    with open(fp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_trades[0].keys()))
        w.writeheader(); w.writerows(all_trades)

def export_heatmap(table_summary, out_dir):
    """expectancy vs (L, K) heatmap CSV."""
    d = ensure_dir(out_dir)
    grid = {}
    for r in table_summary:
        key = (r["L"], r["K"])
        grid.setdefault(key, []).append(r["expectancy"])
    Ls = sorted({k[0] for k in grid})
    Ks = sorted({k[1] for k in grid})
    fp = d / "heatmap_L_vs_K.csv"
    with open(fp, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["L\\K"] + Ks)
        for L in Ls:
            row = [L]
            for K in Ks:
                vals = grid.get((L, K), [])
                row.append(round(float(np.mean(vals)), 4) if vals else "")
            w.writerow(row)
    print(f"  ✓ heatmap → {fp}")

def plot_equity(rule, out_dir):
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        return
    d = ensure_dir(out_dir) / "plots"; d.mkdir(exist_ok=True)
    name = f"L{rule['L']}_K{rule['K']}_{rule['side']}_{rule['sl_model']}"
    all_pts = []
    for f in rule["all_folds"]:
        all_pts.extend(t["gross_pts"] for t in f["trades"])
    if not all_pts: return
    eq = np.cumsum(all_pts)

    fig, (a1, a2) = plt.subplots(2, 1, figsize=(9, 6),
                                 gridspec_kw={"height_ratios":[2,1]})
    a1.plot(eq, color="#58a6ff"); a1.set_title(f"{name}  equity (pts)")
    a1.grid(alpha=0.3)
    a2.hist(all_pts, bins=40, color="#3fb950", alpha=0.7)
    a2.axvline(0, color="k", linewidth=0.5); a2.set_title("Trade return distribution")
    a2.grid(alpha=0.3)
    fig.tight_layout(); fig.savefig(d / f"{name}.png", dpi=110); plt.close(fig)