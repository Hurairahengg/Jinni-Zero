import json
import matplotlib.pyplot as plt
import numpy as np

# =========================================================
# CONFIG
# =========================================================

FILE_PATH = "data/NQ/8pt.json"

RISK_PER_TRADE = 10.0

COMMISSION_PER_LOT = 0.8   # one-side
SLIPPAGE_POINTS = 0.3

ATR_PERIOD = 14

FIXED_SLS = [8, 10, 12, 14, 16, 18]
ATR_MULTS = [1.0, 2.0]

RANK_METRIC = "net_profit"   # used to sort leaderboard

# =========================================================
# LOAD DATA
# =========================================================

with open(FILE_PATH, "r") as f:
    data = json.load(f)

# =========================================================
# HELPERS
# =========================================================

def direction(candle):

    if candle["close"] > candle["open"]:
        return 1

    elif candle["close"] < candle["open"]:
        return -1

    return 0


directions = [direction(c) for c in data]


def compute_atr(data, period):
    """
    Standard Wilder-ish ATR. atr[i] uses bars up to and INCLUDING i,
    so for the entry decision at bar i+1 we read atr[i] (no lookahead).
    """

    trs = [0.0] * len(data)

    for k in range(len(data)):

        high = data[k]["high"]
        low = data[k]["low"]

        if k == 0:
            trs[k] = high - low
        else:
            prev_close = data[k - 1]["close"]
            trs[k] = max(
                high - low,
                abs(high - prev_close),
                abs(low - prev_close),
            )

    atr = [0.0] * len(data)

    # simple SMA seed then Wilder smoothing
    if len(data) >= period:

        seed = sum(trs[:period]) / period
        atr[period - 1] = seed

        for k in range(period, len(data)):
            atr[k] = (atr[k - 1] * (period - 1) + trs[k]) / period

    return atr


ATR = compute_atr(data, ATR_PERIOD)

# =========================================================
# CORE SIM (single SL config)
# =========================================================

def run_sim(streak_size, tp_close_after, sl_mode, sl_value):
    """
    sl_mode: "fixed" or "atr"
    sl_value: points (fixed) or multiplier (atr)
    """

    equity = [0.0]
    trade_returns = []

    stats = {
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "gross_profit": 0.0,
        "gross_loss": 0.0,
        "gross_pnl_before_costs": 0.0,
        "net_profit": 0.0,
        "largest_win": 0.0,
        "largest_loss": 0.0,
        "total_commission": 0.0,
        "total_lots": 0.0,
        "sl_exits": 0,
        "tp_exits": 0,
    }

    i = streak_size

    while i < len(data) - (tp_close_after + 2):

        # ---------- STREAK ----------
        streak_dir = directions[i - streak_size]
        if streak_dir == 0:
            i += 1
            continue

        valid = True
        for j in range(i - streak_size, i):
            if directions[j] != streak_dir:
                valid = False
                break
        if not valid:
            i += 1
            continue

        # ---------- REVERSAL ----------
        reversal_dir = directions[i]
        if reversal_dir != -streak_dir:
            i += 1
            continue

        # ---------- SL SIZE (POINTS) ----------
        if sl_mode == "fixed":
            sl_points = float(sl_value)
        else:
            # ATR taken from signal bar i (no lookahead)
            atr_val = ATR[i]
            if atr_val <= 0:
                i += 1
                continue
            sl_points = atr_val * sl_value

        if sl_points <= 0:
            i += 1
            continue

        # ---------- ENTRY ----------
        entry_idx = i + 1
        raw_entry = data[entry_idx]["open"]

        if reversal_dir == 1:
            entry_price = raw_entry + SLIPPAGE_POINTS
            sl_price = entry_price - sl_points
        else:
            entry_price = raw_entry - SLIPPAGE_POINTS
            sl_price = entry_price + sl_points

        # ---------- LOT SIZE ----------
        lots = RISK_PER_TRADE / sl_points

        # ---------- WALK FORWARD: INTRABAR SL ----------
        tp_idx = entry_idx + tp_close_after
        hit_sl = False
        exit_price = None

        for k in range(entry_idx, tp_idx + 1):

            bar = data[k]
            bar_open = bar["open"]
            bar_high = bar["high"]
            bar_low = bar["low"]

            if reversal_dir == 1:

                if bar_open <= sl_price:
                    exit_price = bar_open - SLIPPAGE_POINTS
                    hit_sl = True
                    break

                if bar_low <= sl_price:
                    exit_price = sl_price - SLIPPAGE_POINTS
                    hit_sl = True
                    break

            else:

                if bar_open >= sl_price:
                    exit_price = bar_open + SLIPPAGE_POINTS
                    hit_sl = True
                    break

                if bar_high >= sl_price:
                    exit_price = sl_price + SLIPPAGE_POINTS
                    hit_sl = True
                    break

        # ---------- TIMED CLOSE ----------
        if not hit_sl:
            raw_tp = data[tp_idx]["close"]
            if reversal_dir == 1:
                exit_price = raw_tp - SLIPPAGE_POINTS
            else:
                exit_price = raw_tp + SLIPPAGE_POINTS

        # ---------- PNL ----------
        if reversal_dir == 1:
            pnl_points = exit_price - entry_price
        else:
            pnl_points = entry_price - exit_price

        gross_pnl = pnl_points * lots
        commission = lots * COMMISSION_PER_LOT
        net_pnl = gross_pnl - commission

        equity.append(equity[-1] + net_pnl)
        trade_returns.append(net_pnl)

        # ---------- STATS ----------
        stats["trades"] += 1
        stats["gross_pnl_before_costs"] += gross_pnl
        stats["total_commission"] += commission
        stats["total_lots"] += lots
        stats["net_profit"] += net_pnl

        if hit_sl:
            stats["sl_exits"] += 1
        else:
            stats["tp_exits"] += 1

        if net_pnl > 0:
            stats["wins"] += 1
            stats["gross_profit"] += net_pnl
            stats["largest_win"] = max(stats["largest_win"], net_pnl)
        else:
            stats["losses"] += 1
            stats["gross_loss"] += abs(net_pnl)
            stats["largest_loss"] = min(stats["largest_loss"], net_pnl)

        i += 1

    # ---------- METRICS ----------
    trades = stats["trades"]
    if trades > 0:
        winrate = stats["wins"] / trades * 100
        expectancy = stats["net_profit"] / trades
        avg_lot = stats["total_lots"] / trades
    else:
        winrate = 0.0
        expectancy = 0.0
        avg_lot = 0.0

    if stats["gross_loss"] > 0:
        pf = stats["gross_profit"] / stats["gross_loss"]
    else:
        pf = 0.0

    # drawdown
    peak = equity[0]
    max_dd = 0.0
    for val in equity:
        if val > peak:
            peak = val
        dd = peak - val
        if dd > max_dd:
            max_dd = dd

    # sharpe-like
    if len(trade_returns) > 1:
        m = np.mean(trade_returns)
        s = np.std(trade_returns)
        sharpe = m / s if s != 0 else 0.0
    else:
        sharpe = 0.0

    # recovery factor
    recovery = (
        stats["net_profit"] / max_dd if max_dd > 0 else 0.0
    )

    return {
        "equity": equity,
        "trade_returns": trade_returns,
        "trades": trades,
        "wins": stats["wins"],
        "losses": stats["losses"],
        "sl_exits": stats["sl_exits"],
        "tp_exits": stats["tp_exits"],
        "winrate": winrate,
        "pf": pf,
        "expectancy": expectancy,
        "net_profit": stats["net_profit"],
        "gross_pnl": stats["gross_pnl_before_costs"],
        "commission": stats["total_commission"],
        "avg_lot": avg_lot,
        "max_dd": max_dd,
        "sharpe": sharpe,
        "recovery": recovery,
        "largest_win": stats["largest_win"],
        "largest_loss": stats["largest_loss"],
    }


# =========================================================
# PROMPTS
# =========================================================

print("=" * 50)
print(" SL OPTIMIZER")
print("=" * 50)

while True:
    try:
        streak_size = int(input("Enter streak size (e.g. 5): ").strip())
        if streak_size >= 2:
            break
        print("streak must be >= 2")
    except ValueError:
        print("invalid number, try again")

while True:
    try:
        tp_close_after = int(input("Enter TP candle close (e.g. 2 or 3): ").strip())
        if tp_close_after >= 1:
            break
        print("tp must be >= 1")
    except ValueError:
        print("invalid number, try again")

# =========================================================
# BUILD SL CANDIDATE LIST
# =========================================================

candidates = []

for sl_pts in FIXED_SLS:
    candidates.append({
        "label": f"Fixed {sl_pts}pt",
        "mode": "fixed",
        "value": sl_pts,
    })

for m in ATR_MULTS:
    candidates.append({
        "label": f"ATR x{m}",
        "mode": "atr",
        "value": m,
    })

# =========================================================
# RUN ALL
# =========================================================

results = []

print(f"\nRunning {len(candidates)} SL configs "
      f"on streak={streak_size}, tp={tp_close_after}...\n")

for c in candidates:
    r = run_sim(streak_size, tp_close_after, c["mode"], c["value"])
    r["label"] = c["label"]
    r["mode"] = c["mode"]
    r["value"] = c["value"]
    results.append(r)
    print(
        f"  {c['label']:<12} | trades={r['trades']:>4} | "
        f"WR={r['winrate']:.1f}% | PF={r['pf']:.2f} | "
        f"Net=${r['net_profit']:.2f} | DD=${r['max_dd']:.2f}"
    )

# =========================================================
# LEADERBOARD (sorted by RANK_METRIC desc)
# =========================================================

results_sorted = sorted(
    results,
    key=lambda x: x[RANK_METRIC],
    reverse=True,
)

print("\n" + "=" * 90)
print(f" LEADERBOARD (ranked by {RANK_METRIC})")
print("=" * 90)
print(
    f"{'Rank':<5}{'SL':<14}{'Trades':<8}{'WR%':<8}"
    f"{'PF':<7}{'Net$':<12}{'DD$':<11}"
    f"{'Recov':<8}{'Sharpe':<8}{'SL/TP exits':<12}"
)
print("-" * 90)

for rank, r in enumerate(results_sorted, start=1):
    print(
        f"{rank:<5}{r['label']:<14}{r['trades']:<8}"
        f"{r['winrate']:<8.2f}{r['pf']:<7.2f}"
        f"{r['net_profit']:<12.2f}{r['max_dd']:<11.2f}"
        f"{r['recovery']:<8.2f}{r['sharpe']:<8.3f}"
        f"{r['sl_exits']}/{r['tp_exits']}"
    )

# =========================================================
# VISUALIZATION
# =========================================================

plt.style.use("dark_background")

fig = plt.figure(figsize=(18, 11))
fig.suptitle(
    f"SL OPTIMIZATION — Streak={streak_size}, TP after {tp_close_after} bars",
    fontsize=15,
    fontweight="bold",
    color="#e6e6e6",
)

gs = fig.add_gridspec(3, 3, hspace=0.45, wspace=0.32)

# ---- (1) Equity curves overlay ----
ax_eq = fig.add_subplot(gs[0, :])
cmap = plt.cm.viridis(np.linspace(0.1, 0.9, len(results)))

for idx, r in enumerate(results):
    ax_eq.plot(
        r["equity"],
        label=r["label"],
        color=cmap[idx],
        linewidth=1.6,
    )

ax_eq.set_title("Equity Curves", fontsize=12, color="#e6e6e6")
ax_eq.set_xlabel("Trade #")
ax_eq.set_ylabel("Equity ($)")
ax_eq.grid(alpha=0.2)
ax_eq.legend(loc="upper left", fontsize=8, ncol=2)
ax_eq.axhline(0, color="gray", linestyle="--", linewidth=0.7)

# helper: get values in candidate order (not sorted) for bar consistency
labels = [r["label"] for r in results]
net = [r["net_profit"] for r in results]
pfs = [r["pf"] for r in results]
wrs = [r["winrate"] for r in results]
dds = [r["max_dd"] for r in results]
shs = [r["sharpe"] for r in results]
recs = [r["recovery"] for r in results]

def bar_plot(ax, values, title, ylabel, fmt="{:.2f}"):

    colors = [
        "#4caf50" if v >= 0 else "#ef5350"
        for v in values
    ]
    bars = ax.bar(labels, values, color=colors, edgecolor="#222")
    ax.set_title(title, fontsize=11, color="#e6e6e6")
    ax.set_ylabel(ylabel)
    ax.grid(alpha=0.2, axis="y")
    ax.tick_params(axis="x", rotation=35, labelsize=8)
    for b, v in zip(bars, values):
        ax.text(
            b.get_x() + b.get_width() / 2,
            v,
            fmt.format(v),
            ha="center",
            va="bottom" if v >= 0 else "top",
            fontsize=8,
            color="#e6e6e6",
        )

# ---- (2) Net Profit ----
ax2 = fig.add_subplot(gs[1, 0])
bar_plot(ax2, net, "Net Profit ($)", "$", "{:.0f}")

# ---- (3) Profit Factor ----
ax3 = fig.add_subplot(gs[1, 1])
bar_plot(ax3, pfs, "Profit Factor", "PF", "{:.2f}")
ax3.axhline(1.0, color="orange", linestyle="--", linewidth=0.8)

# ---- (4) Winrate ----
ax4 = fig.add_subplot(gs[1, 2])
bar_plot(ax4, wrs, "Winrate (%)", "%", "{:.1f}")
ax4.axhline(50, color="orange", linestyle="--", linewidth=0.8)

# ---- (5) Max Drawdown ----
ax5 = fig.add_subplot(gs[2, 0])
neg_dd = [-d for d in dds]  # show as negative for intuition
bars = ax5.bar(
    labels, neg_dd, color="#ef5350", edgecolor="#222"
)
ax5.set_title("Max Drawdown ($)", fontsize=11, color="#e6e6e6")
ax5.set_ylabel("$")
ax5.grid(alpha=0.2, axis="y")
ax5.tick_params(axis="x", rotation=35, labelsize=8)
for b, v in zip(bars, neg_dd):
    ax5.text(
        b.get_x() + b.get_width() / 2, v,
        f"{v:.0f}", ha="center", va="top",
        fontsize=8, color="#e6e6e6",
    )

# ---- (6) Sharpe-like ----
ax6 = fig.add_subplot(gs[2, 1])
bar_plot(ax6, shs, "Sharpe-like (per trade)", "ratio", "{:.2f}")

# ---- (7) Recovery Factor ----
ax7 = fig.add_subplot(gs[2, 2])
bar_plot(ax7, recs, "Recovery Factor (Net/DD)", "ratio", "{:.2f}")

# highlight top 3 on the title of each bar chart background
top3_labels = [r["label"] for r in results_sorted[:3]]
for ax in (ax2, ax3, ax4, ax5, ax6, ax7):
    for tick in ax.get_xticklabels():
        if tick.get_text() in top3_labels:
            tick.set_color("#ffd54f")
            tick.set_fontweight("bold")

out_file = f"sl_opt_streak{streak_size}_tp{tp_close_after}.png"
plt.savefig(out_file, dpi=120, bbox_inches="tight", facecolor="#101010")
plt.show()

print(f"\nSaved dashboard: {out_file}")
print(f"🏆 Winner: {results_sorted[0]['label']} "
      f"(Net=${results_sorted[0]['net_profit']:.2f}, "
      f"PF={results_sorted[0]['pf']:.2f}, "
      f"DD=${results_sorted[0]['max_dd']:.2f})")