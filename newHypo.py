#!/usr/bin/env python3
"""
Bull -> Bull Continuation TRADING SYSTEM (NQ 7pt)
=================================================
Setup:
    A bull, B is next candle, B.time - A.time >= 4s
    Entry: B.open  (LONG)
    Stop : A.low
    TP   : B.close (one candle worth)

Execution model:
    - $1 / point / lot
    - Per-trade risk cap: $10
    - Lot sizing: lots = floor( risk_cap / stop_points / lot_step ) * lot_step
                  clamped to [MIN_LOT, MAX_LOT]; if even MIN_LOT risks > cap -> skip
    - Slippage: 0.3 pt per fill, applied adversely (entry +slip, exit -slip)
    - Commission: $0.8 per LOT per TRADE (total, not per side)

Two modes:
    MODE 1: B can serve as next A (overlap)
    MODE 2: After A->B, skip B (non-overlap)
"""

import json, csv, math
from pathlib import Path
from datetime import datetime
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# ---------- CONFIG ----------
DATA_PATH       = Path("data/NQ/18pt.json")
OUT_DIR         = Path("results")
MIN_TIME_GAP    = 4         # seconds A->B
DOLLAR_PER_PT   = 1.0       # per lot
RISK_CAP_USD    = 10.0      # max $ loss per trade
MIN_LOT         = 0.01
MAX_LOT         = 1000.0
LOT_STEP        = 0.01
SLIPPAGE_PTS    = 0.3       # adverse, per fill
COMM_PER_LOT    = 0.8       # $ per lot per trade (round-turn)
STARTING_EQUITY = 10_000.0  # for curve + dd %
ANN_TRADING_SEC = 252 * 6.5 * 3600   # crude annualization base for sharpe scaling

OUT_DIR.mkdir(parents=True, exist_ok=True)


# ---------- IO ----------
def load_candles(p):
    with p.open("r") as f:
        c = json.load(f)
    c.sort(key=lambda x: x["time"])
    return c

def is_bull(c): return c["close"] > c["open"]


# ---------- Sizing ----------
def size_lots(stop_pts):
    if stop_pts <= 0:
        return 0.0
    raw = RISK_CAP_USD / (stop_pts * DOLLAR_PER_PT)
    lots = math.floor(raw / LOT_STEP) * LOT_STEP
    lots = round(lots, 2)
    if lots < MIN_LOT:
        return 0.0
    return min(lots, MAX_LOT)


# ---------- Sim ----------
def simulate(candles, allow_overlap):
    trades = []
    n = len(candles)
    i = 0
    equity = STARTING_EQUITY

    while i < n - 1:
        A, B = candles[i], candles[i+1]

        if B["time"] - A["time"] < MIN_TIME_GAP or not is_bull(A):
            i += 1
            continue

        raw_entry = B["open"]
        raw_stop  = A["low"]
        raw_tp    = B["close"]

        # adverse slippage on entry (buying long -> pay more)
        entry = raw_entry + SLIPPAGE_PTS
        stop_pts_for_size = entry - raw_stop      # effective risk in points
        if stop_pts_for_size <= 0:
            i += 1 if allow_overlap else 2
            continue

        lots = size_lots(stop_pts_for_size)
        if lots == 0.0:
            # cannot size within risk cap -> skip
            i += 1 if allow_overlap else 2
            continue

        # outcome detection (priority: SL if low tagged stop, else exit at close)
        if B["low"] <= raw_stop:
            raw_exit = raw_stop
            outcome  = "LOSS_SL"
        else:
            raw_exit = raw_tp
            if   raw_tp > raw_entry: outcome = "WIN"
            elif raw_tp < raw_entry: outcome = "LOSS_CLOSE"
            else:                    outcome = "BE"

        # adverse slippage on exit (selling long -> receive less)
        exit_px = raw_exit - SLIPPAGE_PTS

        gross_pts = exit_px - entry
        gross_usd = gross_pts * DOLLAR_PER_PT * lots
        comm_usd  = COMM_PER_LOT * lots
        net_usd   = gross_usd - comm_usd
        equity   += net_usd

        # R multiple (risk = lots * stop_pts in $)
        risk_usd = lots * stop_pts_for_size * DOLLAR_PER_PT
        r_mult   = net_usd / risk_usd if risk_usd else 0.0

        trades.append({
            "A_time":    A["time"],
            "B_time":    B["time"],
            "entry":     round(entry, 4),
            "stop":      raw_stop,
            "tp":        raw_tp,
            "exit":      round(exit_px, 4),
            "lots":      lots,
            "stop_pts":  round(stop_pts_for_size, 4),
            "gross_pts": round(gross_pts, 4),
            "gross_usd": round(gross_usd, 4),
            "commission":round(comm_usd, 4),
            "net_usd":   round(net_usd, 4),
            "R":         round(r_mult, 4),
            "equity":    round(equity, 2),
            "outcome":   outcome,
            "B_bull":    is_bull(B),
        })

        i += 1 if allow_overlap else 2

    return trades


# ---------- Stats ----------
def stats(trades):
    n = len(trades)
    if n == 0: return {"trades": 0}

    wins = [t for t in trades if t["net_usd"] > 0]
    loss = [t for t in trades if t["net_usd"] < 0]
    sl   = [t for t in trades if t["outcome"] == "LOSS_SL"]
    bbull= sum(1 for t in trades if t["B_bull"])

    gross_win  = sum(t["net_usd"] for t in wins)
    gross_loss = sum(t["net_usd"] for t in loss)
    net        = sum(t["net_usd"] for t in trades)
    comm_total = sum(t["commission"] for t in trades)

    pf = (gross_win / abs(gross_loss)) if gross_loss else float("inf")
    expectancy = net / n
    avg_w = (gross_win / len(wins)) if wins else 0
    avg_l = (gross_loss / len(loss)) if loss else 0
    payoff = (avg_w / abs(avg_l)) if avg_l else float("inf")

    # equity curve / drawdown
    eq = [STARTING_EQUITY] + [t["equity"] for t in trades]
    peak = eq[0]; max_dd = 0.0; max_dd_pct = 0.0
    for v in eq:
        if v > peak: peak = v
        dd = peak - v
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = dd / peak * 100

    # per-trade Sharpe & Sortino
    rets = [t["net_usd"] for t in trades]
    mean = sum(rets) / n
    var  = sum((r-mean)**2 for r in rets) / n
    sd   = var ** 0.5
    downside = [r for r in rets if r < 0]
    dsd  = ((sum(r*r for r in downside) / n) ** 0.5) if downside else 0.0
    sharpe_trade  = (mean / sd) if sd else 0.0
    sortino_trade = (mean / dsd) if dsd else 0.0

    # crude annualization using time span
    span_sec = trades[-1]["B_time"] - trades[0]["B_time"]
    trades_per_year = (n / span_sec * ANN_TRADING_SEC) if span_sec > 0 else 0
    sharpe_ann  = sharpe_trade  * math.sqrt(trades_per_year) if trades_per_year else 0
    sortino_ann = sortino_trade * math.sqrt(trades_per_year) if trades_per_year else 0

    cagr = None
    if span_sec > 0:
        years = span_sec / ANN_TRADING_SEC
        end_eq = eq[-1]
        if end_eq > 0 and years > 0:
            cagr = ((end_eq / STARTING_EQUITY) ** (1/years) - 1) * 100

    calmar = (cagr / max_dd_pct) if (cagr and max_dd_pct) else None
    recovery = (net / max_dd) if max_dd else float("inf")

    # streaks
    cur_w=cur_l=mx_w=mx_l=0
    for t in trades:
        if t["net_usd"] > 0:
            cur_w += 1; cur_l = 0; mx_w = max(mx_w, cur_w)
        elif t["net_usd"] < 0:
            cur_l += 1; cur_w = 0; mx_l = max(mx_l, cur_l)

    return {
        "trades":              n,
        "B_bull_rate_%":       round(100*bbull/n, 2),
        "winrate_%":           round(100*len(wins)/n, 2),
        "wins":                len(wins),
        "losses":              len(loss),
        "stopped_out":         len(sl),
        "net_usd":             round(net, 2),
        "gross_win_usd":       round(gross_win, 2),
        "gross_loss_usd":      round(gross_loss, 2),
        "commission_usd":      round(comm_total, 2),
        "profit_factor":       round(pf,3) if pf != float("inf") else "inf",
        "expectancy_usd":      round(expectancy, 4),
        "avg_win_usd":         round(avg_w, 4),
        "avg_loss_usd":        round(avg_l, 4),
        "payoff_ratio":        round(payoff,3) if payoff != float("inf") else "inf",
        "avg_R":               round(sum(t["R"] for t in trades)/n, 4),
        "max_drawdown_usd":    round(max_dd, 2),
        "max_drawdown_%":      round(max_dd_pct, 3),
        "recovery_factor":     round(recovery,3) if recovery != float("inf") else "inf",
        "sharpe_per_trade":    round(sharpe_trade, 4),
        "sharpe_annualized":   round(sharpe_ann, 4),
        "sortino_per_trade":   round(sortino_trade, 4),
        "sortino_annualized":  round(sortino_ann, 4),
        "cagr_%":              round(cagr,3) if cagr is not None else None,
        "calmar":              round(calmar,3) if calmar is not None else None,
        "max_consec_wins":     mx_w,
        "max_consec_losses":   mx_l,
        "ending_equity":       round(eq[-1], 2),
    }


# ---------- Reporting ----------
def write_csv(trades, path):
    if not trades: return
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=trades[0].keys())
        w.writeheader(); w.writerows(trades)

def write_report(label, s, path):
    with open(path, "w") as f:
        f.write(f"==== {label} ====\n")
        for k, v in s.items():
            f.write(f"{k:24s}: {v}\n")

def plot_equity(trades, label, path):
    if not trades: return
    eq = [STARTING_EQUITY] + [t["equity"] for t in trades]
    peak = eq[0]; dd = []
    for v in eq:
        peak = max(peak, v)
        dd.append(v - peak)

    fig, (ax1, ax2) = plt.subplots(2,1, figsize=(12,7), sharex=True,
                                   gridspec_kw={"height_ratios":[3,1]})
    ax1.plot(eq, color="#00c08b", linewidth=1.4)
    ax1.fill_between(range(len(eq)), eq, STARTING_EQUITY,
                     where=[v>=STARTING_EQUITY for v in eq],
                     color="#00c08b", alpha=0.15)
    ax1.axhline(STARTING_EQUITY, color="white", linestyle="--", alpha=0.4)
    ax1.set_title(f"Equity Curve — {label}", color="white")
    ax1.set_ylabel("Equity ($)", color="white")
    ax1.grid(alpha=0.2)

    ax2.fill_between(range(len(dd)), dd, 0, color="#ff4d6d", alpha=0.6)
    ax2.set_ylabel("Drawdown ($)", color="white")
    ax2.set_xlabel("Trade #", color="white")
    ax2.grid(alpha=0.2)

    for ax in (ax1, ax2):
        ax.set_facecolor("#111418")
        for spine in ax.spines.values(): spine.set_color("#444")
        ax.tick_params(colors="white")
    fig.patch.set_facecolor("#0b0d10")
    fig.tight_layout()
    fig.savefig(path, dpi=140, facecolor=fig.get_facecolor())
    plt.close(fig)


# ---------- Main ----------
def run(label, allow_overlap, candles):
    tag = "overlap" if allow_overlap else "nonoverlap"
    trades = simulate(candles, allow_overlap)
    s = stats(trades)

    print(f"\n===== {label} =====")
    for k, v in s.items(): print(f"  {k:24s}: {v}")

    write_csv(trades,           OUT_DIR / f"trades_{tag}.csv")
    write_report(label, s,      OUT_DIR / f"report_{tag}.txt")
    plot_equity(trades, label,  OUT_DIR / f"equity_{tag}.png")
    print(f"  -> results/ trades_{tag}.csv | report_{tag}.txt | equity_{tag}.png")


if __name__ == "__main__":
    print(f"[{datetime.now()}] Loading {DATA_PATH} ...")
    candles = load_candles(DATA_PATH)
    print(f"Loaded {len(candles):,} candles")

    run("MODE 1 — B REUSABLE (overlap)",    True,  candles)
    run("MODE 2 — B LOCKED (non-overlap)",  False, candles)
    print("\nDone. Check ./results/")