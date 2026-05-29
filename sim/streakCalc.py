import json
import matplotlib.pyplot as plt
import numpy as np
from datetime import datetime, timezone

# =========================================================
# CONFIG
# =========================================================

FILE_PATH = "data/NQ/8pt.json"

FIXED_SL_POINTS = 16.0
COMMISSION_PER_LOT = 0.8   # one-side
SLIPPAGE_POINTS = 0.3

# Defaults (used when scaling OFF)
DEFAULT_FLAT_RISK = 10.0

# Hour-of-day timezone offset (in hours) applied to UTC bar timestamps.
# 0 = UTC, -5 = NY winter, -4 = NY summer, 9 = JST, etc.
HOD_TZ_OFFSET_HOURS = 0

# ---------- TIME FILTER ----------
# Hours (in the HOD_TZ_OFFSET_HOURS timezone) during which NO trades are taken.
# Hardcoded — edit this list directly. Example: [0, 1, 2, 22, 23]
BLOCKED_HOURS = [0]

# =========================================================
# LOAD DATA
# =========================================================

with open(FILE_PATH, "r") as f:
    data = json.load(f)

# =========================================================
# PROMPTS
# =========================================================

print("=" * 50)
print(" TRADE RESULT ANALYZER")
print("=" * 50)

def prompt_date(label, default_msg):
    print(f"\n{label} (leave year blank to {default_msg})")
    while True:
        try:
            y_raw = input("  year (e.g. 2024): ").strip()
            if y_raw == "":
                return None
            year = int(y_raw)
            m_raw = input("  month (1-12): ").strip()
            month = int(m_raw) if m_raw else 1
            d_raw = input("  day (1-31): ").strip()
            day = int(d_raw) if d_raw else 1
            dt = datetime(year, month, day, tzinfo=timezone.utc)
            return int(dt.timestamp())
        except (ValueError, OverflowError) as e:
            print(f"  invalid date ({e}), try again")

start_ts = prompt_date("START date", "use earliest data")
end_ts   = prompt_date("END date",   "use latest data")

if end_ts is not None:
    end_ts += 24 * 3600

original_len = len(data)
if start_ts is not None or end_ts is not None:
    filtered = []
    for c in data:
        t = c.get("time")
        if t is None:
            continue
        if start_ts is not None and t < start_ts:
            continue
        if end_ts is not None and t >= end_ts:
            continue
        filtered.append(c)
    data = filtered

if len(data) == 0:
    print("\n⚠️  No bars left after date filter. Check your range.")
    raise SystemExit

first_dt = datetime.fromtimestamp(data[0]["time"],  tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
last_dt  = datetime.fromtimestamp(data[-1]["time"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
print(f"\nLoaded {len(data):,} bars (filtered from {original_len:,})")
print(f"Range: {first_dt}  →  {last_dt}")

if BLOCKED_HOURS:
    print(f"Blocked hours (no trades): {sorted(set(BLOCKED_HOURS))}  "
          f"(tz=UTC{'+' if HOD_TZ_OFFSET_HOURS>=0 else ''}{HOD_TZ_OFFSET_HOURS})")
else:
    print("Blocked hours: none")

while True:
    try:
        streak_size = int(input("\nEnter streak size (e.g. 5): ").strip())
        if streak_size >= 1:
            break
        print("streak must be >= 2")
    except ValueError:
        print("invalid number, try again")

while True:
    try:
        tp_close_after = int(input("Enter TP candle close (e.g. 2): ").strip())
        if tp_close_after >= 1:
            break
        print("tp must be >= 1")
    except ValueError:
        print("invalid number, try again")

scaling_enabled = False
starting_balance = 0.0
risk_per_100 = 0.0
flat_risk = DEFAULT_FLAT_RISK

while True:
    ans = input("Scaling enabled? (y/n): ").strip().lower()
    if ans in ("y", "n"):
        scaling_enabled = (ans == "y")
        break
    print("please enter y or n")

if scaling_enabled:
    while True:
        try:
            starting_balance = float(input("Enter starting balance (e.g. 1000): ").strip())
            if starting_balance > 0:
                break
            print("must be > 0")
        except ValueError:
            print("invalid number")
    while True:
        try:
            risk_per_100 = float(input("Enter risk per $100 (e.g. 1 = $1 per $100 = 1%): ").strip())
            if risk_per_100 > 0:
                break
            print("must be > 0")
        except ValueError:
            print("invalid number")
else:
    while True:
        try:
            raw = input(f"Enter flat risk per trade $ (default {DEFAULT_FLAT_RISK}): ").strip()
            if raw == "":
                flat_risk = DEFAULT_FLAT_RISK
                break
            flat_risk = float(raw)
            if flat_risk > 0:
                break
            print("must be > 0")
        except ValueError:
            print("invalid number")

# =========================================================
# HELPERS
# =========================================================

BLOCKED_SET = set(int(h) % 24 for h in BLOCKED_HOURS)

def direction(candle):
    if candle["close"] > candle["open"]:
        return 1
    elif candle["close"] < candle["open"]:
        return -1
    return 0

directions = [direction(c) for c in data]

def current_risk(current_balance):
    if scaling_enabled:
        return (current_balance / 100.0) * risk_per_100
    return flat_risk

def bar_hour(ts):
    """Hour-of-day bucket using configured offset."""
    return int(((ts // 3600) + HOD_TZ_OFFSET_HOURS) % 24)

# =========================================================
# MAIN SIM
# =========================================================

mode_str = (
    f"SCALING ON | start=${starting_balance:.2f} | risk={risk_per_100}/100 ({risk_per_100}%)"
    if scaling_enabled else f"FLAT RISK ${flat_risk:.2f}"
)
print(f"\nRunning streak={streak_size}, tp_after={tp_close_after}, "
      f"SL={FIXED_SL_POINTS}pt | {mode_str}\n")

if scaling_enabled:
    equity = [starting_balance]
else:
    equity = [0.0]

trade_returns = []
trade_records = []
risk_history = []
blocked_setups = 0  # count of valid setups skipped by time filter

stats = {
    "trades": 0, "wins": 0, "losses": 0,
    "gross_profit": 0.0, "gross_loss": 0.0,
    "gross_pnl_before_costs": 0.0, "net_profit": 0.0,
    "largest_win": 0.0, "largest_loss": 0.0,
    "total_commission": 0.0, "total_slippage_cost": 0.0,
    "total_lots": 0.0, "sl_exits": 0, "tp_exits": 0,
    "long_trades": 0, "short_trades": 0,
    "long_wins": 0, "short_wins": 0,
}

i = streak_size

while i < len(data) - (tp_close_after + 2):

    streak_dir = directions[i - streak_size]
    if streak_dir == 0:
        i += 1; continue

    valid = True
    for j in range(i - streak_size, i):
        if directions[j] != streak_dir:
            valid = False; break
    if not valid:
        i += 1; continue

    reversal_dir = directions[i]
    if reversal_dir != -streak_dir:
        i += 1; continue

    # ---------- TIME FILTER ----------
    entry_idx = i + 1
    entry_time = data[entry_idx]["time"]
    entry_hour = bar_hour(entry_time)
    if entry_hour in BLOCKED_SET:
        blocked_setups += 1
        i += 1
        continue

    balance_now = equity[-1]
    risk_this_trade = current_risk(balance_now)

    if risk_this_trade <= 0:
        print(f"Balance non-positive at trade attempt #{stats['trades']+1}, halting.")
        break

    MIN_LOTS = 0.01
    MAX_LOTS = 600.0
    raw_lots = risk_this_trade / FIXED_SL_POINTS
    lots = max(MIN_LOTS, min(MAX_LOTS, raw_lots))

    raw_entry = data[entry_idx]["open"]

    if reversal_dir == 1:
        entry_price = raw_entry + SLIPPAGE_POINTS
        sl_price = entry_price - FIXED_SL_POINTS
    else:
        entry_price = raw_entry - SLIPPAGE_POINTS
        sl_price = entry_price + FIXED_SL_POINTS

    tp_idx = entry_idx + tp_close_after
    hit_sl = False
    exit_price = None
    exit_idx = None
    mfe_points = 0.0
    mae_points = 0.0

    for k in range(entry_idx, tp_idx + 1):
        bar = data[k]
        bar_open = bar["open"]; bar_high = bar["high"]; bar_low = bar["low"]

        if reversal_dir == 1:
            fav = bar_high - entry_price
            adv = entry_price - bar_low
        else:
            fav = entry_price - bar_low
            adv = bar_high - entry_price

        if fav > mfe_points: mfe_points = fav
        if adv > mae_points: mae_points = adv

        if reversal_dir == 1:
            if bar_open <= sl_price:
                exit_price = bar_open - SLIPPAGE_POINTS
                hit_sl = True; exit_idx = k; break
            if bar_low <= sl_price:
                exit_price = sl_price - SLIPPAGE_POINTS
                hit_sl = True; exit_idx = k; break
        else:
            if bar_open >= sl_price:
                exit_price = bar_open + SLIPPAGE_POINTS
                hit_sl = True; exit_idx = k; break
            if bar_high >= sl_price:
                exit_price = sl_price + SLIPPAGE_POINTS
                hit_sl = True; exit_idx = k; break

    if not hit_sl:
        raw_tp = data[tp_idx]["close"]
        if reversal_dir == 1:
            exit_price = raw_tp - SLIPPAGE_POINTS
        else:
            exit_price = raw_tp + SLIPPAGE_POINTS
        exit_idx = tp_idx

    if reversal_dir == 1:
        pnl_points = exit_price - entry_price
    else:
        pnl_points = entry_price - exit_price

    gross_pnl = pnl_points * lots
    commission = lots * COMMISSION_PER_LOT
    slippage_cost = (SLIPPAGE_POINTS * 2) * lots
    net_pnl = gross_pnl - commission
    r_multiple = net_pnl / risk_this_trade if risk_this_trade > 0 else 0.0

    equity.append(equity[-1] + net_pnl)
    trade_returns.append(net_pnl)
    risk_history.append(risk_this_trade)

    trade_records.append({
        "entry_idx": entry_idx, "exit_idx": exit_idx,
        "entry_time": entry_time, "entry_hour": entry_hour,
        "dir": reversal_dir,
        "entry": entry_price, "exit": exit_price,
        "pnl_points": pnl_points, "net_pnl": net_pnl, "gross_pnl": gross_pnl,
        "commission": commission, "lots": lots,
        "bars_held": exit_idx - entry_idx + 1,
        "hit_sl": hit_sl,
        "mfe_points": mfe_points, "mae_points": mae_points,
        "mfe_dollars": mfe_points * lots, "mae_dollars": mae_points * lots,
        "r_multiple": r_multiple, "risk_used": risk_this_trade,
        "balance_after": equity[-1],
    })

    stats["trades"] += 1
    stats["gross_pnl_before_costs"] += gross_pnl
    stats["total_commission"] += commission
    stats["total_slippage_cost"] += slippage_cost
    stats["total_lots"] += lots
    stats["net_profit"] += net_pnl

    if hit_sl: stats["sl_exits"] += 1
    else: stats["tp_exits"] += 1

    if reversal_dir == 1: stats["long_trades"] += 1
    else: stats["short_trades"] += 1

    if net_pnl > 0:
        stats["wins"] += 1
        stats["gross_profit"] += net_pnl
        stats["largest_win"] = max(stats["largest_win"], net_pnl)
        if reversal_dir == 1: stats["long_wins"] += 1
        else: stats["short_wins"] += 1
    else:
        stats["losses"] += 1
        stats["gross_loss"] += abs(net_pnl)
        stats["largest_loss"] = min(stats["largest_loss"], net_pnl)

    i += 1

# =========================================================
# DERIVED QUANT STATS
# =========================================================

trades = stats["trades"]
returns_np = np.array(trade_returns) if trades > 0 else np.array([0.0])
equity_np = np.array(equity)

if trades > 0:
    winrate = stats["wins"] / trades * 100
    expectancy = stats["net_profit"] / trades
    avg_lot = stats["total_lots"] / trades

    wins_arr = returns_np[returns_np > 0]
    losses_arr = returns_np[returns_np <= 0]
    avg_win = wins_arr.mean() if len(wins_arr) > 0 else 0.0
    avg_loss = losses_arr.mean() if len(losses_arr) > 0 else 0.0
    payoff = abs(avg_win / avg_loss) if avg_loss != 0 else 0.0
    pf = stats["gross_profit"] / stats["gross_loss"] if stats["gross_loss"] > 0 else 0.0
    kelly = ((winrate / 100) - ((1 - winrate / 100) / payoff)) * 100 if payoff > 0 else 0.0

    mean_ret = returns_np.mean()
    std_ret = returns_np.std()
    sharpe = mean_ret / std_ret if std_ret != 0 else 0.0
    downside = returns_np[returns_np < 0]
    downside_std = downside.std() if len(downside) > 0 else 0.0
    sortino = mean_ret / downside_std if downside_std != 0 else 0.0

    if std_ret != 0:
        skew = ((returns_np - mean_ret) ** 3).mean() / (std_ret ** 3)
        kurt = ((returns_np - mean_ret) ** 4).mean() / (std_ret ** 4) - 3
    else:
        skew = 0.0; kurt = 0.0

    mfe_arr = np.array([t["mfe_dollars"] for t in trade_records])
    mae_arr = np.array([t["mae_dollars"] for t in trade_records])
    avg_mfe = mfe_arr.mean(); avg_mae = mae_arr.mean()
    mfe_mae_ratio = avg_mfe / avg_mae if avg_mae > 0 else 0.0
    avg_bars = np.mean([t["bars_held"] for t in trade_records])
else:
    winrate = expectancy = avg_lot = avg_win = avg_loss = 0.0
    payoff = pf = kelly = sharpe = sortino = skew = kurt = 0.0
    avg_mfe = avg_mae = mfe_mae_ratio = avg_bars = 0.0
    mfe_arr = mae_arr = np.array([])

# ---------- DRAWDOWN ----------
peak = equity_np[0]
max_dd_abs = 0.0; max_dd_pct = 0.0
dd_curve = []; dd_durations = []
cur_dd_len = 0; max_dd_len = 0

for val in equity_np:
    if val > peak:
        peak = val
        if cur_dd_len > 0: dd_durations.append(cur_dd_len)
        cur_dd_len = 0
    else:
        cur_dd_len += 1
    dd = peak - val
    dd_curve.append(-dd)
    if dd > max_dd_abs: max_dd_abs = dd
    if peak != 0:
        dd_pct = (dd / peak) * 100 if peak > 0 else 0
        if dd_pct > max_dd_pct: max_dd_pct = dd_pct
    if cur_dd_len > max_dd_len: max_dd_len = cur_dd_len

if cur_dd_len > 0: dd_durations.append(cur_dd_len)
avg_dd_len = np.mean(dd_durations) if dd_durations else 0.0
recovery = stats["net_profit"] / max_dd_abs if max_dd_abs > 0 else 0.0

# ---------- WIN/LOSS STREAKS ----------
max_wins = 0; max_losses = 0
cur_w = 0; cur_l = 0
for ret in trade_returns:
    if ret > 0: cur_w += 1; cur_l = 0
    else: cur_l += 1; cur_w = 0
    max_wins = max(max_wins, cur_w)
    max_losses = max(max_losses, cur_l)

final_balance = equity_np[-1] if scaling_enabled else (starting_balance + stats["net_profit"])
total_return_pct = ((final_balance / starting_balance) - 1) * 100 if (scaling_enabled and starting_balance > 0) else 0.0

# =========================================================
# HOUR-OF-DAY AGGREGATION
# =========================================================

hod = {
    h: {"pnl": 0.0, "n": 0, "wins": 0, "losses": 0,
        "gross_p": 0.0, "gross_l": 0.0,
        "long_n": 0, "short_n": 0, "sl": 0, "tp": 0,
        "pnls": []}
    for h in range(24)
}

for t in trade_records:
    h = t["entry_hour"]
    pnl = t["net_pnl"]
    b = hod[h]
    b["n"] += 1
    b["pnl"] += pnl
    b["pnls"].append(pnl)
    if pnl > 0:
        b["wins"] += 1
        b["gross_p"] += pnl
    else:
        b["losses"] += 1
        b["gross_l"] += abs(pnl)
    if t["dir"] == 1: b["long_n"] += 1
    else: b["short_n"] += 1
    if t["hit_sl"]: b["sl"] += 1
    else: b["tp"] += 1

hours = list(range(24))
hod_pnl   = [hod[h]["pnl"] for h in hours]
hod_n     = [hod[h]["n"] for h in hours]
hod_wr    = [(hod[h]["wins"] / hod[h]["n"] * 100) if hod[h]["n"] > 0 else 0 for h in hours]
hod_exp   = [(hod[h]["pnl"] / hod[h]["n"]) if hod[h]["n"] > 0 else 0 for h in hours]
hod_pf    = [(hod[h]["gross_p"] / hod[h]["gross_l"]) if hod[h]["gross_l"] > 0
             else (float("inf") if hod[h]["gross_p"] > 0 else 0) for h in hours]
hod_avgW  = [(np.mean([p for p in hod[h]["pnls"] if p > 0]) if hod[h]["wins"] > 0 else 0) for h in hours]
hod_avgL  = [(np.mean([p for p in hod[h]["pnls"] if p <= 0]) if hod[h]["losses"] > 0 else 0) for h in hours]

if any(n > 0 for n in hod_n):
    best_hour  = int(np.argmax(hod_pnl))
    worst_hour = int(np.argmin(hod_pnl))
else:
    best_hour = worst_hour = 0

# =========================================================
# PRINT REPORT
# =========================================================

def line(label, val):
    print(f"  {label:<28}: {val}")

print("\n" + "=" * 60)
print(f" RESULTS — Streak={streak_size} | TP={tp_close_after} | "
      f"SL={FIXED_SL_POINTS}pt | {'SCALED' if scaling_enabled else 'FLAT'}")
print(f" Date range: {first_dt}  →  {last_dt}")
print(f" Blocked hours: {sorted(BLOCKED_SET) if BLOCKED_SET else 'none'} "
      f"(skipped setups: {blocked_setups})")
print("=" * 60)

print("\n[ MODE ]")
if scaling_enabled:
    line("Starting balance", f"${starting_balance:.2f}")
    line("Risk per $100", f"${risk_per_100:.2f} ({risk_per_100}%)")
    line("Final balance", f"${final_balance:.2f}")
    line("Total return", f"{total_return_pct:.2f}%")
    if len(risk_history) > 0:
        line("First trade risk", f"${risk_history[0]:.2f}")
        line("Last trade risk", f"${risk_history[-1]:.2f}")
        line("Avg trade risk", f"${np.mean(risk_history):.2f}")
        line("Max trade risk", f"${max(risk_history):.2f}")
        line("Min trade risk", f"${min(risk_history):.2f}")
else:
    line("Flat risk per trade", f"${flat_risk:.2f}")

print("\n[ TRADE COUNTS ]")
line("Total trades", trades)
line("Skipped by time filter", blocked_setups)
line("Wins / Losses", f"{stats['wins']} / {stats['losses']}")
line("Long / Short", f"{stats['long_trades']} / {stats['short_trades']}")
line("Long winrate", f"{(stats['long_wins']/stats['long_trades']*100) if stats['long_trades'] else 0:.2f}%")
line("Short winrate", f"{(stats['short_wins']/stats['short_trades']*100) if stats['short_trades'] else 0:.2f}%")
line("SL exits / TP exits", f"{stats['sl_exits']} / {stats['tp_exits']}")

print("\n[ PROFITABILITY ]")
line("Net profit", f"${stats['net_profit']:.2f}")
line("Gross PnL (pre-costs)", f"${stats['gross_pnl_before_costs']:.2f}")
line("Gross profit", f"${stats['gross_profit']:.2f}")
line("Gross loss", f"${stats['gross_loss']:.2f}")
line("Commission paid", f"${stats['total_commission']:.2f}")
line("Slippage cost (est)", f"${stats['total_slippage_cost']:.2f}")
line("Winrate", f"{winrate:.2f}%")
line("Profit factor", f"{pf:.2f}")
line("Expectancy / trade", f"${expectancy:.2f}")
line("Payoff (avgW/avgL)", f"{payoff:.2f}")
line("Kelly %", f"{kelly:.2f}%")

print("\n[ TRADE QUALITY ]")
line("Avg win", f"${avg_win:.2f}")
line("Avg loss", f"${avg_loss:.2f}")
line("Largest win", f"${stats['largest_win']:.2f}")
line("Largest loss", f"${stats['largest_loss']:.2f}")
line("Avg bars in trade", f"{avg_bars:.2f}")
line("Avg lot size", f"{avg_lot:.4f}")

print("\n[ EXCURSION ]")
line("Avg MFE", f"${avg_mfe:.2f}")
line("Avg MAE", f"${avg_mae:.2f}")
line("MFE/MAE ratio", f"{mfe_mae_ratio:.2f}")

print("\n[ RISK ]")
line("Max drawdown ($)", f"${max_dd_abs:.2f}")
if scaling_enabled:
    line("Max drawdown (%)", f"{max_dd_pct:.2f}%")
line("Max DD duration (trades)", max_dd_len)
line("Avg DD duration (trades)", f"{avg_dd_len:.2f}")
line("Recovery factor", f"{recovery:.2f}")
line("Sharpe-like (per trade)", f"{sharpe:.3f}")
line("Sortino-like (per trade)", f"{sortino:.3f}")
line("Return skew", f"{skew:.3f}")
line("Return kurtosis (excess)", f"{kurt:.3f}")

print("\n[ STREAKS ]")
line("Max consecutive wins", max_wins)
line("Max consecutive losses", max_losses)

# ---------- HOUR-OF-DAY TABLE ----------
print("\n[ HOUR OF DAY ]  (tz offset = "
      f"{'+' if HOD_TZ_OFFSET_HOURS >= 0 else ''}{HOD_TZ_OFFSET_HOURS}h from UTC)")
print(f"  {'HR':>3} {'N':>5} {'WR%':>7} {'NET$':>11} {'EXP$':>9} {'PF':>7} {'L/S':>9} {'SL/TP':>9}  STATE")
for h in hours:
    state = "BLOCK" if h in BLOCKED_SET else ""
    if hod[h]["n"] == 0 and not state:
        continue
    pf_s = f"{hod_pf[h]:.2f}" if hod_pf[h] != float("inf") else "inf"
    print(f"  {h:>3d} {hod_n[h]:>5d} {hod_wr[h]:>6.2f}% "
          f"{hod_pnl[h]:>10.2f} {hod_exp[h]:>8.2f} {pf_s:>7} "
          f"{hod[h]['long_n']:>3d}/{hod[h]['short_n']:<3d}   "
          f"{hod[h]['sl']:>3d}/{hod[h]['tp']:<3d}  {state}")

if any(n > 0 for n in hod_n):
    print(f"  → Best hour:  {best_hour:02d}:00  (${hod_pnl[best_hour]:.2f}, "
          f"WR {hod_wr[best_hour]:.1f}%, N={hod_n[best_hour]})")
    print(f"  → Worst hour: {worst_hour:02d}:00  (${hod_pnl[worst_hour]:.2f}, "
          f"WR {hod_wr[worst_hour]:.1f}%, N={hod_n[worst_hour]})")

# =========================================================
# DASHBOARD (main)
# =========================================================

if trades == 0:
    print("\nNo trades, skipping dashboards.")
    raise SystemExit

plt.style.use("dark_background")

fig = plt.figure(figsize=(20, 14))
mode_title = (
    f"SCALED | start=${starting_balance:.0f} | risk={risk_per_100}/100"
    if scaling_enabled else f"FLAT ${flat_risk:.0f}"
)
blocked_str = f"BLOCK={sorted(BLOCKED_SET)}" if BLOCKED_SET else "BLOCK=none"
fig.suptitle(
    f"TRADE RESULT DASHBOARD  |  Streak={streak_size}  |  "
    f"TP={tp_close_after}  |  SL={FIXED_SL_POINTS}pt  |  {mode_title}  |  "
    f"{blocked_str}  |  {first_dt} → {last_dt}  |  "
    f"Net=${stats['net_profit']:.0f}  |  PF={pf:.2f}  |  WR={winrate:.1f}%",
    fontsize=12, fontweight="bold", color="#e6e6e6",
)
gs = fig.add_gridspec(4, 3, hspace=0.6, wspace=0.32)

# 1) Equity curve
ax = fig.add_subplot(gs[0, :2])
ax.plot(equity_np, color="#4caf50", linewidth=1.7, label="Equity")
base = starting_balance if scaling_enabled else 0
ax.fill_between(range(len(equity_np)), equity_np, base,
                where=(equity_np >= base), color="#4caf50", alpha=0.15)
ax.fill_between(range(len(equity_np)), equity_np, base,
                where=(equity_np < base), color="#ef5350", alpha=0.15)
ax.axhline(base, color="gray", linestyle="--", linewidth=0.7,
           label=f"Start ${base:.0f}" if scaling_enabled else "Zero")
ax.set_title("Equity Curve", fontsize=12)
ax.set_xlabel("Trade #"); ax.set_ylabel("Equity ($)")
ax.grid(alpha=0.2); ax.legend(fontsize=8)

# 2) Stats panel
ax = fig.add_subplot(gs[0, 2])
ax.axis("off")
panel_lines = [
    f"Trades:       {trades}",
    f"Skipped(blk): {blocked_setups}",
    f"Winrate:      {winrate:.2f}%",
    f"PF:           {pf:.2f}",
    f"Expectancy:   ${expectancy:.2f}",
    f"Payoff:       {payoff:.2f}",
    f"Kelly %:      {kelly:.2f}%",
    f"Sharpe:       {sharpe:.3f}",
    f"Sortino:      {sortino:.3f}",
    f"Max DD:       ${max_dd_abs:.2f}",
]
if scaling_enabled:
    panel_lines += [
        f"Max DD %:     {max_dd_pct:.2f}%",
        f"Total Ret:    {total_return_pct:.2f}%",
        f"Final Bal:    ${final_balance:.2f}",
    ]
panel_lines += [
    f"Recovery:     {recovery:.2f}",
    f"Max W streak: {max_wins}",
    f"Max L streak: {max_losses}",
    f"Avg MFE:      ${avg_mfe:.2f}",
    f"Avg MAE:      ${avg_mae:.2f}",
    f"MFE/MAE:      {mfe_mae_ratio:.2f}",
    f"Best hour:    {best_hour:02d}:00 (${hod_pnl[best_hour]:.0f})",
    f"Worst hour:   {worst_hour:02d}:00 (${hod_pnl[worst_hour]:.0f})",
    f"Blocked hrs:  {sorted(BLOCKED_SET) if BLOCKED_SET else '-'}",
]
ax.text(0.02, 0.98, "\n".join(panel_lines), family="monospace", fontsize=9.5,
        color="#e6e6e6", va="top", ha="left",
        bbox=dict(facecolor="#1a1a1a", edgecolor="#333", boxstyle="round,pad=0.6"))
ax.set_title("Key Stats", fontsize=12)

# 3) Drawdown
ax = fig.add_subplot(gs[1, :2])
ax.fill_between(range(len(dd_curve)), dd_curve, 0, color="#ef5350", alpha=0.55)
ax.plot(dd_curve, color="#ef5350", linewidth=1)
ax.set_title("Drawdown (Underwater, $)", fontsize=12)
ax.set_xlabel("Trade #"); ax.set_ylabel("DD ($)"); ax.grid(alpha=0.2)

# 4) Win/Loss pie
ax = fig.add_subplot(gs[1, 2])
ax.pie([stats["wins"], stats["losses"]],
       labels=[f"Wins\n{stats['wins']}", f"Losses\n{stats['losses']}"],
       colors=["#4caf50", "#ef5350"],
       autopct="%1.1f%%", startangle=90,
       textprops={"color": "#e6e6e6", "fontsize": 9},
       wedgeprops={"edgecolor": "#101010", "linewidth": 2})
ax.set_title("Win / Loss", fontsize=12)

# 5) PnL histogram
ax = fig.add_subplot(gs[2, 0])
ax.hist(returns_np, bins=40, color="#42a5f5", edgecolor="#101010")
ax.axvline(0, color="gray", linestyle="--", linewidth=0.7)
ax.axvline(returns_np.mean(), color="#ffd54f", linestyle="--",
           linewidth=1, label=f"mean={returns_np.mean():.2f}")
ax.set_title("Per-Trade PnL Distribution", fontsize=12)
ax.set_xlabel("$"); ax.set_ylabel("count")
ax.grid(alpha=0.2); ax.legend(fontsize=8)

# 6) R-multiple
ax = fig.add_subplot(gs[2, 1])
r_arr = np.array([t["r_multiple"] for t in trade_records])
ax.hist(r_arr, bins=40, color="#ab47bc", edgecolor="#101010")
ax.axvline(0, color="gray", linestyle="--", linewidth=0.7)
ax.axvline(r_arr.mean(), color="#ffd54f", linestyle="--",
           linewidth=1, label=f"mean={r_arr.mean():.2f}R")
ax.set_title("R-Multiple Distribution", fontsize=12)
ax.set_xlabel("R"); ax.set_ylabel("count")
ax.grid(alpha=0.2); ax.legend(fontsize=8)

# 7) MFE vs MAE
ax = fig.add_subplot(gs[2, 2])
colors = ["#4caf50" if t["net_pnl"] > 0 else "#ef5350" for t in trade_records]
ax.scatter(mae_arr, mfe_arr, c=colors, s=20, alpha=0.7, edgecolors="none")
lim = max(mfe_arr.max() if len(mfe_arr) else 1, mae_arr.max() if len(mae_arr) else 1)
ax.plot([0, lim], [0, lim], color="gray", linestyle="--", linewidth=0.7)
ax.set_title("MFE vs MAE ($)", fontsize=12)
ax.set_xlabel("MAE"); ax.set_ylabel("MFE"); ax.grid(alpha=0.2)

# 8) Per-trade PnL seq
ax = fig.add_subplot(gs[3, 0])
bar_colors = ["#4caf50" if r > 0 else "#ef5350" for r in returns_np]
ax.bar(range(len(returns_np)), returns_np, color=bar_colors, width=1.0)
ax.axhline(0, color="gray", linewidth=0.7)
ax.set_title("Per-Trade PnL (sequence)", fontsize=12)
ax.set_xlabel("Trade #"); ax.set_ylabel("$"); ax.grid(alpha=0.2, axis="y")

# 9) Rolling winrate
ax = fig.add_subplot(gs[3, 1])
window = min(30, max(5, len(returns_np) // 10))
wins_bool = (returns_np > 0).astype(float)
if len(wins_bool) >= window:
    rolling_wr = np.convolve(wins_bool, np.ones(window) / window, mode="valid") * 100
    ax.plot(rolling_wr, color="#ffb74d", linewidth=1.4)
    ax.axhline(50, color="gray", linestyle="--", linewidth=0.7)
    ax.set_title(f"Rolling Winrate (window={window})", fontsize=12)
else:
    ax.text(0.5, 0.5, "not enough trades", ha="center", va="center",
            color="#888", transform=ax.transAxes)
    ax.set_title("Rolling Winrate", fontsize=12)
ax.set_xlabel("Trade #"); ax.set_ylabel("%"); ax.grid(alpha=0.2)

# 10) Risk per trade
ax = fig.add_subplot(gs[3, 2])
if scaling_enabled:
    ax.plot(risk_history, color="#26c6da", linewidth=1.4)
    ax.fill_between(range(len(risk_history)), risk_history, color="#26c6da", alpha=0.2)
    ax.set_title("Risk per Trade ($) — compounding", fontsize=12)
else:
    ax.axhline(flat_risk, color="#26c6da", linewidth=1.6)
    ax.set_ylim(0, flat_risk * 2)
    ax.set_title(f"Risk per Trade ($) — FLAT ${flat_risk:.0f}", fontsize=12)
ax.set_xlabel("Trade #"); ax.set_ylabel("$"); ax.grid(alpha=0.2)

date_tag = ""
if start_ts is not None or end_ts is not None:
    s = datetime.fromtimestamp(data[0]["time"],  tz=timezone.utc).strftime("%Y%m%d")
    e = datetime.fromtimestamp(data[-1]["time"], tz=timezone.utc).strftime("%Y%m%d")
    date_tag = f"_{s}-{e}"

block_tag = f"_blk{'-'.join(str(h) for h in sorted(BLOCKED_SET))}" if BLOCKED_SET else ""

out_file = (
    f"trade_report_s{streak_size}_tp{tp_close_after}"
    f"{'_scaled' if scaling_enabled else '_flat'}{date_tag}{block_tag}.png"
)
plt.savefig(out_file, dpi=120, bbox_inches="tight", facecolor="#101010")

# =========================================================
# HOUR-OF-DAY DASHBOARD
# =========================================================

fig2 = plt.figure(figsize=(20, 11))
tz_label = f"UTC{'+' if HOD_TZ_OFFSET_HOURS>=0 else ''}{HOD_TZ_OFFSET_HOURS}"
fig2.suptitle(
    f"HOUR-OF-DAY ANALYSIS ({tz_label})  |  "
    f"Streak={streak_size}  TP={tp_close_after}  SL={FIXED_SL_POINTS}pt  |  "
    f"BLOCK={sorted(BLOCKED_SET) if BLOCKED_SET else 'none'}  |  "
    f"{first_dt} → {last_dt}  |  N={trades}  Net=${stats['net_profit']:.0f}",
    fontsize=12, fontweight="bold", color="#e6e6e6",
)
gs2 = fig2.add_gridspec(3, 2, hspace=0.55, wspace=0.22)

x = np.arange(24)
xticklabels = [f"{h:02d}" for h in hours]

def shade_blocked(ax):
    for h in BLOCKED_SET:
        ax.axvspan(h - 0.5, h + 0.5, color="#555", alpha=0.35, zorder=0)

# A) Total PnL per hour
ax = fig2.add_subplot(gs2[0, :])
pnl_colors = ["#4caf50" if p > 0 else "#ef5350" for p in hod_pnl]
ax.bar(x, hod_pnl, color=pnl_colors, edgecolor="#101010")
shade_blocked(ax)
ax.axhline(0, color="gray", linewidth=0.7)
ax.axvline(best_hour,  color="#4caf50", linestyle="--", linewidth=0.8, alpha=0.6,
           label=f"best {best_hour:02d}:00 (${hod_pnl[best_hour]:.0f})")
ax.axvline(worst_hour, color="#ef5350", linestyle="--", linewidth=0.8, alpha=0.6,
           label=f"worst {worst_hour:02d}:00 (${hod_pnl[worst_hour]:.0f})")
for h in hours:
    if hod_n[h] > 0:
        ax.text(h, hod_pnl[h], f"{hod_pnl[h]:.0f}",
                ha="center",
                va="bottom" if hod_pnl[h] >= 0 else "top",
                fontsize=7, color="#cccccc")
ax.set_xticks(x); ax.set_xticklabels(xticklabels)
ax.set_title(f"Total Net PnL by Hour ({tz_label})  — gray = blocked", fontsize=12)
ax.set_xlabel("Hour"); ax.set_ylabel("$"); ax.grid(alpha=0.2, axis="y")
ax.legend(fontsize=8)

# B) Win rate per hour
ax = fig2.add_subplot(gs2[1, 0])
wr_colors = ["#4caf50" if w >= 50 else "#ef5350" for w in hod_wr]
ax.bar(x, hod_wr, color=wr_colors, edgecolor="#101010")
shade_blocked(ax)
ax.axhline(50, color="gray", linestyle="--", linewidth=0.7)
ax.axhline(winrate, color="#ffd54f", linestyle="--", linewidth=0.8,
           label=f"overall {winrate:.1f}%")
for h in hours:
    if hod_n[h] > 0:
        ax.text(h, hod_wr[h], f"{hod_wr[h]:.0f}", ha="center", va="bottom",
                fontsize=7, color="#cccccc")
ax.set_xticks(x); ax.set_xticklabels(xticklabels)
ax.set_title("Win Rate by Hour", fontsize=12)
ax.set_xlabel("Hour"); ax.set_ylabel("%")
ax.set_ylim(0, max(100, max(hod_wr) + 10) if hod_wr else 100)
ax.grid(alpha=0.2, axis="y"); ax.legend(fontsize=8)

# C) Trade count per hour
ax = fig2.add_subplot(gs2[1, 1])
ax.bar(x, hod_n, color="#42a5f5", edgecolor="#101010")
shade_blocked(ax)
for h in hours:
    if hod_n[h] > 0:
        ax.text(h, hod_n[h], str(hod_n[h]), ha="center", va="bottom",
                fontsize=7, color="#cccccc")
ax.set_xticks(x); ax.set_xticklabels(xticklabels)
ax.set_title("Trade Count by Hour", fontsize=12)
ax.set_xlabel("Hour"); ax.set_ylabel("N"); ax.grid(alpha=0.2, axis="y")

# D) Expectancy per hour
ax = fig2.add_subplot(gs2[2, 0])
exp_colors = ["#4caf50" if e > 0 else "#ef5350" for e in hod_exp]
ax.bar(x, hod_exp, color=exp_colors, edgecolor="#101010")
shade_blocked(ax)
ax.axhline(0, color="gray", linewidth=0.7)
ax.axhline(expectancy, color="#ffd54f", linestyle="--", linewidth=0.8,
           label=f"overall ${expectancy:.2f}")
ax.set_xticks(x); ax.set_xticklabels(xticklabels)
ax.set_title("Expectancy by Hour ($/trade)", fontsize=12)
ax.set_xlabel("Hour"); ax.set_ylabel("$/trade")
ax.grid(alpha=0.2, axis="y"); ax.legend(fontsize=8)

# E) Profit factor per hour
ax = fig2.add_subplot(gs2[2, 1])
pf_plot = [min(p, 5.0) if p != float("inf") else 5.0 for p in hod_pf]
pf_colors = ["#4caf50" if p > 1 else "#ef5350" for p in hod_pf]
ax.bar(x, pf_plot, color=pf_colors, edgecolor="#101010")
shade_blocked(ax)
ax.axhline(1, color="gray", linestyle="--", linewidth=0.7, label="PF=1")
ax.axhline(min(pf, 5.0), color="#ffd54f", linestyle="--", linewidth=0.8,
           label=f"overall {pf:.2f}")
for h in hours:
    if hod_n[h] > 0:
        label_txt = "∞" if hod_pf[h] == float("inf") else f"{hod_pf[h]:.1f}"
        ax.text(h, pf_plot[h], label_txt, ha="center", va="bottom",
                fontsize=7, color="#cccccc")
ax.set_xticks(x); ax.set_xticklabels(xticklabels)
ax.set_title("Profit Factor by Hour (capped at 5 for display)", fontsize=12)
ax.set_xlabel("Hour"); ax.set_ylabel("PF")
ax.grid(alpha=0.2, axis="y"); ax.legend(fontsize=8)

hod_out_file = (
    f"trade_report_HOD_s{streak_size}_tp{tp_close_after}"
    f"{'_scaled' if scaling_enabled else '_flat'}{date_tag}{block_tag}.png"
)
plt.savefig(hod_out_file, dpi=120, bbox_inches="tight", facecolor="#101010")

# =========================================================
# HOUR-OF-DAY EQUITY CURVES (3rd figure)
# =========================================================

hod_series = {h: [] for h in range(24)}
for t in trade_records:
    hod_series[t["entry_hour"]].append(t["net_pnl"])

active_hours = [h for h in range(24) if len(hod_series[h]) > 0]

hod_eq_file = None
if len(active_hours) == 0:
    print("\nNo per-hour trades, skipping HOD equity figure.")
else:
    ncols = 4
    nrows = int(np.ceil(len(active_hours) / ncols))

    fig3 = plt.figure(figsize=(20, 3.2 * nrows + 1.2))
    fig3.suptitle(
        f"HOUR-OF-DAY EQUITY CURVES ({tz_label})  |  "
        f"Streak={streak_size}  TP={tp_close_after}  SL={FIXED_SL_POINTS}pt  |  "
        f"BLOCK={sorted(BLOCKED_SET) if BLOCKED_SET else 'none'}  |  "
        f"{first_dt} → {last_dt}  |  hours with trades: {len(active_hours)}/24",
        fontsize=12, fontweight="bold", color="#e6e6e6",
    )
    gs3 = fig3.add_gridspec(nrows, ncols, hspace=0.75, wspace=0.30)

    for idx, h in enumerate(active_hours):
        r = idx // ncols
        c = idx % ncols
        ax = fig3.add_subplot(gs3[r, c])

        series = np.array(hod_series[h])
        eq = np.cumsum(series)
        xx = np.arange(1, len(eq) + 1)

        final_eq = eq[-1]
        n = len(series)
        wins = int((series > 0).sum())
        wr_h = wins / n * 100 if n > 0 else 0
        color = "#4caf50" if final_eq >= 0 else "#ef5350"

        ax.plot(xx, eq, color=color, linewidth=1.5)
        ax.fill_between(xx, eq, 0, where=(eq >= 0), color="#4caf50", alpha=0.15)
        ax.fill_between(xx, eq, 0, where=(eq < 0),  color="#ef5350", alpha=0.15)
        ax.axhline(0, color="gray", linestyle="--", linewidth=0.6)

        peak_i = int(np.argmax(eq))
        trough_i = int(np.argmin(eq))
        ax.scatter([peak_i + 1], [eq[peak_i]], color="#4caf50", s=15, zorder=3)
        ax.scatter([trough_i + 1], [eq[trough_i]], color="#ef5350", s=15, zorder=3)

        running_peak = np.maximum.accumulate(eq)
        dd_h = (running_peak - eq).max() if len(eq) else 0.0

        ax.set_title(
            f"{h:02d}:00  |  N={n}  WR={wr_h:.0f}%  "
            f"Net=${final_eq:.0f}  DD=${dd_h:.0f}",
            fontsize=10, color="#e6e6e6",
        )
        ax.tick_params(labelsize=7)
        ax.grid(alpha=0.2)
        if c == 0:
            ax.set_ylabel("$", fontsize=8)
        if r == nrows - 1:
            ax.set_xlabel("trade # (within hour)", fontsize=8)

    hod_eq_file = (
        f"trade_report_HOD_EQUITY_s{streak_size}_tp{tp_close_after}"
        f"{'_scaled' if scaling_enabled else '_flat'}{date_tag}{block_tag}.png"
    )
    plt.savefig(hod_eq_file, dpi=120, bbox_inches="tight", facecolor="#101010")

plt.show()

print(f"\nSaved dashboard:        {out_file}")
print(f"Saved HOD dashboard:    {hod_out_file}")
if hod_eq_file:
    print(f"Saved HOD equity grid:  {hod_eq_file}")