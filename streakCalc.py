# streak_sim.py
import json
from pathlib import Path

PATH    = Path("data/NQ/18pt.json")
STREAKS = [2, 12, 14, 16, 18, 20]

def direction(b):
    if b["close"] > b["open"]: return 1
    if b["close"] < b["open"]: return -1
    return 0

def load_bars(p):
    with open(p) as f: raw = json.load(f)
    return [{"open":float(b["open"]),"high":float(b["high"]),
             "low":float(b["low"]),"close":float(b["close"])} for b in raw]

def sl_hit(bar, sl, d):
    if d == 1:  return bar["low"]  <= sl
    else:       return bar["high"] >= sl

def simulate(bars):
    dirs = [direction(b) for b in bars]
    n = len(bars)

    def empty():
        return {"trades": 0, "wins": 0, "total_pts": 0.0,
                "best": float("-inf"), "worst": float("inf")}
    R = {S: {1: empty(), 2: empty()} for S in STREAKS}

    i = 0
    while i < n:
        if dirs[i] == 0:
            i += 1; continue
        d = dirs[i]
        j = i
        while j < n and dirs[j] == d:
            j += 1
        run_len = j - i

        for S in STREAKS:
            if run_len < S: continue
            for end_idx in range(i + S - 1, j):
                entry_bar = bars[end_idx]
                entry_px  = entry_bar["close"]
                sl_px     = entry_bar["low"] if d == 1 else entry_bar["high"]

                for K in (1, 2):
                    if end_idx + K >= n: continue

                    exit_px = None
                    for step in range(1, K + 1):
                        nb = bars[end_idx + step]
                        if sl_hit(nb, sl_px, d):
                            exit_px = sl_px
                            break
                    if exit_px is None:
                        exit_px = bars[end_idx + K]["close"]

                    pts = (exit_px - entry_px) if d == 1 else (entry_px - exit_px)

                    r = R[S][K]
                    r["trades"]    += 1
                    r["total_pts"] += pts
                    if pts > 0: r["wins"] += 1
                    if pts > r["best"]:  r["best"]  = pts
                    if pts < r["worst"]: r["worst"] = pts
        i = j
    return R, n

def fmt_pts(x):
    sign = "+" if x >= 0 else ""
    return f"{sign}{x:,.2f}"

def fmt_pct(num, den):
    return f"{(num/den*100):5.2f}%" if den else "  —  "

def render(R):
    print("  ╔══════════════════════════════════════════════════════════════════════════════════════╗")
    print("  ║  Next │  Trades   │    WR    │  Total Points  │  Avg/Trade  │   Best    │   Worst    ║")
    print("  ╠══════════════════════════════════════════════════════════════════════════════════════╣")

    for idx, S in enumerate(STREAKS):
        print(f"  ║                              ▸  STREAK  =  {S}                                        ║")
        print("  ║  ─────┼───────────┼──────────┼────────────────┼─────────────┼───────────┼─────────── ║")
        for K in (1, 2):
            r = R[S][K]
            t = r["trades"]
            total = r["total_pts"]
            avg   = (total / t) if t else 0
            best  = r["best"]  if t else 0
            worst = r["worst"] if t else 0
            wr    = fmt_pct(r["wins"], t)

            # WR bar visualization (20 chars wide)
            wr_pct = (r["wins"] / t) if t else 0
            wr_bar_len = int(wr_pct * 20)
            wr_bar = "█" * wr_bar_len + "░" * (20 - wr_bar_len)

            polarity = "🟢" if total >= 0 else "🔴"

            print(f"  ║   {K}   │ {t:>9,} │ {wr:>8} │ {fmt_pts(total):>14} │"
                  f" {fmt_pts(avg):>11} │ {fmt_pts(best):>9} │ {fmt_pts(worst):>9} ║")
            print(f"  ║       │           │  {wr_bar}  │  {polarity} pts                                            ║")
        if idx < len(STREAKS) - 1:
            print("  ╠══════════════════════════════════════════════════════════════════════════════════════╣")
    print("  ╚══════════════════════════════════════════════════════════════════════════════════════╝")

def render_grand_total(R):
    print()
    print("  ╭─ GRAND TOTAL (all streaks combined) ──────────────────────────────────╮")
    for K in (1, 2):
        t   = sum(R[S][K]["trades"]    for S in STREAKS)
        w   = sum(R[S][K]["wins"]      for S in STREAKS)
        p   = sum(R[S][K]["total_pts"] for S in STREAKS)
        avg = (p / t) if t else 0
        wr  = fmt_pct(w, t)
        polarity = "🟢" if p >= 0 else "🔴"
        print(f"  │  Next {K}  →  {t:>8,} trades  │  WR {wr}  │  {polarity} {fmt_pts(p):>12} pts  "
              f"(avg {fmt_pts(avg)})  │")
    print("  ╰───────────────────────────────────────────────────────────────────────╯")

def main():
    bars = load_bars(PATH)
    R, n = simulate(bars)

    print()
    print("  ╭───────────────────────────────────────────────────────────────╮")
    print("  │   📊  STREAK SIMULATOR  —  Points + Win Rate                   │")
    print("  ╰───────────────────────────────────────────────────────────────╯")
    print(f"     File  : {PATH}")
    print(f"     Bars  : {n:,}")
    print()
    print("  📋  Rules")
    print("     • Entry : close of last streak bar")
    print("     • SL    : entry bar LOW (bull) / HIGH (bear), intrabar check")
    print("     • Exit  : SL price if hit, else close of Next-K bar")
    print("     • Win   : trade closed with positive points (>0)")
    print()
    render(R)
    render_grand_total(R)
    print()

if __name__ == "__main__":
    main()