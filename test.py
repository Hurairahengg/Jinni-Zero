"""
Jinni Continuum — Streaming Backtester v7
==========================================
• 2 consecutive bull/bear bars → entry next bar open (+ spread)
• SL = signal bar's low/high, TP = 1:1
• Both hit same bar → SL wins
• Risk = lot × sl_dist + lot × commission  ≤  budget  ALWAYS
• Random spread per trade
• Scaling mode + lot cap + date range
"""
import json
import random
from datetime import datetime, timezone

# ═══════════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════════
STARTING_BALANCE = 1000.0
BASE_RISK        = 10.0          # flat mode only
SCALING_MODE     = False
RISK_PER_100     = 1.0           # $1 per $100 equity
MIN_LOT          = 0.01
MAX_LOT          = 400.0
LOT_STEP         = 0.01
DATA_FILE        = "data/6pt.json"

# COMMISSION: $ per 1 lot per trade (deducted on close)
COMMISSION_PER_LOT = 1.25

# SPREAD: random points added to entry
SPREAD_MIN = 0.1
SPREAD_MAX = 0.5

# SEED for reproducibility (None = true random)
RANDOM_SEED = 42

# DATE RANGE — None = no limit
DATE_FROM = "2025-01-01"
DATE_TO   = "2025-04-25"


# ═══════════════════════════════════════════════════════════════════
#  DATE PARSING
# ═══════════════════════════════════════════════════════════════════
def parse_date(val):
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return int(val)
    try:
        return int(val)
    except ValueError:
        return int(datetime.strptime(val, "%Y-%m-%d")
                   .replace(tzinfo=timezone.utc).timestamp())


def ts_to_str(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


TS_FROM = parse_date(DATE_FROM)
TS_TO   = parse_date(DATE_TO)

if RANDOM_SEED is not None:
    random.seed(RANDOM_SEED)


# ═══════════════════════════════════════════════════════════════════
#  STREAMING JSON PARSER
# ═══════════════════════════════════════════════════════════════════
def stream_bars(filepath, read_size=1024 * 1024):
    decoder = json.JSONDecoder()
    with open(filepath, "r") as f:
        buf = ""
        while True:
            chunk = f.read(read_size)
            if not chunk and not buf.strip().rstrip("]"):
                break
            buf += chunk
            while buf:
                buf = buf.lstrip(" \t\n\r,")
                if not buf:
                    break
                if buf[0] in "[]":
                    buf = buf[1:]
                    continue
                try:
                    obj, end = decoder.raw_decode(buf)
                    yield obj
                    buf = buf[end:]
                except json.JSONDecodeError:
                    break


# ═══════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════
def direction(bar):
    c, o = float(bar["close"]), float(bar["open"])
    if c > o:
        return "bull"
    if c < o:
        return "bear"
    return "doji"


def calc_risk(bal):
    """Total $ budget for this trade (covers SL loss + commission)."""
    if SCALING_MODE:
        return max(0.0, (bal / 100.0) * RISK_PER_100)
    return BASE_RISK


def calc_lot_size(risk_dollars, sl_distance):
    """
    lot × (sl_distance + commission_per_lot) ≤ risk_dollars  ALWAYS.
    
    On SL hit:  total cost = lot × sl_dist  +  lot × commission
              = lot × (sl_dist + commission)
              ≤ risk_dollars  ✅
    """
    if sl_distance <= 0 or risk_dollars <= 0:
        return 0.0

    cost_per_lot = sl_distance + COMMISSION_PER_LOT
    if cost_per_lot <= 0:
        return 0.0

    raw = risk_dollars / cost_per_lot

    # floor to LOT_STEP (NEVER round up)
    floored = int(raw / LOT_STEP) * LOT_STEP
    floored = round(floored, 10)

    # cap
    lot = min(floored, MAX_LOT)

    # SAFETY: final verify
    if lot > 0 and (lot * cost_per_lot) > risk_dollars + 0.001:
        lot = round(lot - LOT_STEP, 10)
        lot = max(lot, 0.0)

    return lot


def get_spread():
    if SPREAD_MIN <= 0 and SPREAD_MAX <= 0:
        return 0.0
    return random.uniform(SPREAD_MIN, SPREAD_MAX)


# ═══════════════════════════════════════════════════════════════════
#  STATE
# ═══════════════════════════════════════════════════════════════════
balance = STARTING_BALANCE
prev_dir = None
prev_streak = 0
pending = None
position = None
trades = []
bar_count = 0
bars_in_range = 0
skipped = 0
lot_capped_count = 0
total_commission = 0.0
total_spread_cost = 0.0
range_first_ts = None
range_last_ts = None

# ═══════════════════════════════════════════════════════════════════
#  PRINT CONFIG
# ═══════════════════════════════════════════════════════════════════
print(f"  ┌─ CONFIG ────────────────────────────────────────")
print(f"  │ Balance      : ${STARTING_BALANCE:,.2f}")
print(f"  │ Mode         : {'SCALING' if SCALING_MODE else 'FLAT'}")
print(f"  │ Risk         : {'$'+f'{RISK_PER_100}'+' per $100 equity' if SCALING_MODE else f'${BASE_RISK:.2f} flat'}")
print(f"  │ Risk includes: SL loss + commission")
print(f"  │ Lot cap      : {MAX_LOT}")
print(f"  │ Commission   : ${COMMISSION_PER_LOT} per lot")
print(f"  │ Spread       : {SPREAD_MIN} - {SPREAD_MAX} pts (random)")
print(f"  │ Seed         : {RANDOM_SEED}")
print(f"  │ Date from    : {ts_to_str(TS_FROM) if TS_FROM else 'START'}")
print(f"  │ Date to      : {ts_to_str(TS_TO) if TS_TO else 'END'}")
print(f"  └─────────────────────────────────────────────────")
print(f"  Running...\n")

# ═══════════════════════════════════════════════════════════════════
#  MAIN LOOP
# ═══════════════════════════════════════════════════════════════════
for bar in stream_bars(DATA_FILE):
    bar_count += 1
    ts = int(bar["time"])

    # ── date filter ─────────────────────────────────────────
    if TS_FROM is not None and ts < TS_FROM:
        continue
    if TS_TO is not None and ts >= TS_TO:
        if position is not None:
            pass
        else:
            break
    else:
        bars_in_range += 1
        if range_first_ts is None:
            range_first_ts = ts
        range_last_ts = ts

    o = float(bar["open"])
    h = float(bar["high"])
    l = float(bar["low"])
    c = float(bar["close"])
    d = direction(bar)

    # ── streak ──
    if d == "doji":
        streak = 0
    elif prev_dir is None or prev_dir == "doji" or d != prev_dir:
        streak = 1
    else:
        streak = prev_streak + 1

    # ══════════════════════════════════════════════════════════
    # STEP 1: ENTER on this bar's open if pending
    # ══════════════════════════════════════════════════════════
    if pending and position is None:
        spread = get_spread()
        risk_budget = calc_risk(balance)

        entered = False
        if pending["dir"] == "bull":
            entry = o + spread                   # pay ask
            sl = pending["sl_ref"]
            sl_dist = entry - sl

            if sl_dist > 0:
                lot = calc_lot_size(risk_budget, sl_dist)
                if lot >= MIN_LOT:
                    commission = round(lot * COMMISSION_PER_LOT, 5)
                    sl_loss = round(lot * sl_dist, 5)
                    total_risk = round(sl_loss + commission, 5)
                    was_capped = lot >= MAX_LOT
                    if was_capped:
                        lot_capped_count += 1

                    position = {
                        "dir": "BUY",
                        "entry_raw": o,
                        "entry": entry,
                        "spread": round(spread, 5),
                        "sl": sl,
                        "tp": entry + (sl_dist * 1.5),       # 1:1
                        "sl_dist": round(sl_dist, 5),
                        "lot": lot,
                        "risk_budget": round(risk_budget, 5),
                        "sl_loss": sl_loss,
                        "commission": commission,
                        "total_risk": total_risk,
                        "capped": was_capped,
                        "entry_bar": bars_in_range,
                        "entry_time": ts,
                        "bal_at_entry": round(balance, 2),
                    }
                    entered = True

        else:  # SELL
            entry = o - spread                   # receive bid
            sl = pending["sl_ref"]
            sl_dist = sl - entry

            if sl_dist > 0:
                lot = calc_lot_size(risk_budget, sl_dist)
                if lot >= MIN_LOT:
                    commission = round(lot * COMMISSION_PER_LOT, 5)
                    sl_loss = round(lot * sl_dist, 5)
                    total_risk = round(sl_loss + commission, 5)
                    was_capped = lot >= MAX_LOT
                    if was_capped:
                        lot_capped_count += 1

                    position = {
                        "dir": "SELL",
                        "entry_raw": o,
                        "entry": entry,
                        "spread": round(spread, 5),
                        "sl": sl,
                        "tp": entry - (sl_dist * 1.5),       # 1:1
                        "sl_dist": round(sl_dist, 5),
                        "lot": lot,
                        "risk_budget": round(risk_budget, 5),
                        "sl_loss": sl_loss,
                        "commission": commission,
                        "total_risk": total_risk,
                        "capped": was_capped,
                        "entry_bar": bars_in_range,
                        "entry_time": ts,
                        "bal_at_entry": round(balance, 2),
                    }
                    entered = True

        if not entered:
            skipped += 1
        pending = None

    # ══════════════════════════════════════════════════════════
    # STEP 2: CHECK TP / SL
    # ══════════════════════════════════════════════════════════
    if position:
        hit_sl = hit_tp = False

        if position["dir"] == "BUY":
            if l <= position["sl"]:
                hit_sl = True
            if h >= position["tp"]:
                hit_tp = True
        else:
            if h >= position["sl"]:
                hit_sl = True
            if l <= position["tp"]:
                hit_tp = True

        if hit_sl or hit_tp:
            commission = position["commission"]

            if hit_sl:  # both hit → SL wins
                raw_pnl = -(position["lot"] * position["sl_dist"])
                exit_price = position["sl"]
                result = "SL"
            else:
                raw_pnl = position["lot"] * position["sl_dist"]  # 1:1
                exit_price = position["tp"]
                result = "TP"

            net_pnl = round(raw_pnl - commission, 5)
            balance += net_pnl

            total_commission += commission
            total_spread_cost += position["spread"] * position["lot"]

            trades.append({
                "dir": position["dir"],
                "entry_raw": position["entry_raw"],
                "entry": position["entry"],
                "exit": exit_price,
                "spread": position["spread"],
                "lot": position["lot"],
                "sl_dist": position["sl_dist"],
                "risk_budget": position["risk_budget"],
                "sl_loss": position["sl_loss"],
                "commission": commission,
                "total_risk": position["total_risk"],
                "raw_pnl": round(raw_pnl, 5),
                "pnl": net_pnl,
                "balance": round(balance, 2),
                "bal_at_entry": position["bal_at_entry"],
                "capped": position["capped"],
                "result": result,
                "entry_bar": position["entry_bar"],
                "exit_bar": bars_in_range,
                "time": ts_to_str(ts),
            })
            position = None

    # ══════════════════════════════════════════════════════════
    # STEP 3: NEW SIGNAL
    # ══════════════════════════════════════════════════════════
    past_end = TS_TO is not None and ts >= TS_TO
    if not past_end and position is None and pending is None and streak == 2:
        if d == "bull":
            pending = {"dir": "bull", "sl_ref": l}
        elif d == "bear":
            pending = {"dir": "bear", "sl_ref": h}

    prev_dir = d
    prev_streak = streak

    if bars_in_range % 500_000 == 0 and bars_in_range > 0:
        print(f"  ... {bars_in_range:,} bars  |  trades: {len(trades):,}  |  bal: ${balance:,.2f}")


# ═══════════════════════════════════════════════════════════════════
#  RESULTS
# ═══════════════════════════════════════════════════════════════════
total = len(trades)
wins = [t for t in trades if t["result"] == "TP"]
losses = [t for t in trades if t["result"] == "SL"]
buys = [t for t in trades if t["dir"] == "BUY"]
sells = [t for t in trades if t["dir"] == "SELL"]

total_pnl = sum(t["pnl"] for t in trades)
total_raw_pnl = sum(t["raw_pnl"] for t in trades)
gross_profit = sum(t["pnl"] for t in wins)
gross_loss = sum(t["pnl"] for t in losses)

peak = STARTING_BALANCE
max_dd = 0
max_dd_pct = 0
eq = STARTING_BALANCE
for t in trades:
    eq += t["pnl"]
    if eq > peak:
        peak = eq
    dd = peak - eq
    dd_pct = (dd / peak * 100) if peak > 0 else 0
    if dd > max_dd:
        max_dd = dd
    if dd_pct > max_dd_pct:
        max_dd_pct = dd_pct

max_win_streak = max_loss_streak = cur_w = cur_l = 0
for t in trades:
    if t["result"] == "TP":
        cur_w += 1; cur_l = 0
    else:
        cur_l += 1; cur_w = 0
    max_win_streak = max(max_win_streak, cur_w)
    max_loss_streak = max(max_loss_streak, cur_l)

avg_bars = (
    sum(t["exit_bar"] - t["entry_bar"] for t in trades) / total
    if total > 0 else 0
)

# risk verification: total_risk must NEVER exceed risk_budget
risk_violations = 0
worst_overshoot = 0.0
for t in trades:
    if not t["capped"] and t["total_risk"] > t["risk_budget"] + 0.02:
        risk_violations += 1
        overshoot = t["total_risk"] - t["risk_budget"]
        if overshoot > worst_overshoot:
            worst_overshoot = overshoot

capped = [t for t in trades if t["capped"]]
uncapped = [t for t in trades if not t["capped"]]

range_str = "ALL DATA"
if range_first_ts and range_last_ts:
    range_str = f"{ts_to_str(range_first_ts)} → {ts_to_str(range_last_ts)}"

SEP = "=" * 70

print(f"\n{SEP}")
print(f"  JINNI CONTINUUM BACKTEST RESULTS  v7")
print(f"  2 Consec | 1:1 R:R | Commission | Spread | Lot Cap {MAX_LOT}")
print(f"{SEP}")
print(f"  Data Range         : {range_str}")
print(f"  Mode               : {'SCALING ($'+f'{RISK_PER_100}'+' per $100 eq)' if SCALING_MODE else f'FLAT (${BASE_RISK:.2f})'}")
print(f"  Starting Balance   : ${STARTING_BALANCE:>14,.2f}")
print(f"  Starting Risk      : ${calc_risk(STARTING_BALANCE):>14,.2f}")
print(f"  Commission         : ${COMMISSION_PER_LOT:>14,.2f} /lot")
print(f"  Spread             :     {SPREAD_MIN} - {SPREAD_MAX} pts")
print(f"  Lot Cap            : {MAX_LOT:>14,.2f}")
print(f"  Total bars (file)  : {bar_count:>14,}")
print(f"  Bars in range      : {bars_in_range:>14,}")
print(f"  Total trades       : {total:>14,}")
print(f"  Skipped (tiny lot) : {skipped:>14,}")
print(f"  BUY / SELL         : {len(buys):>6,} / {len(sells):,}")
print(SEP)

print(f"\n  ── P&L (after commission + spread) ──")
print(f"  Final Balance      : ${balance:>14,.2f}")
print(f"  Net P&L            : ${total_pnl:>14,.2f}")
print(f"  Return             : {total_pnl / STARTING_BALANCE * 100:>13.2f}%")
print(f"  Gross Profit (net) : ${gross_profit:>14,.2f}")
print(f"  Gross Loss   (net) : ${gross_loss:>14,.2f}")
pf = abs(gross_profit / gross_loss) if gross_loss != 0 else float("inf")
print(f"  Profit Factor      : {pf:>14.2f}")

print(f"\n  ── COSTS ──")
print(f"  Total Commission   : ${total_commission:>14,.2f}")
print(f"  Total Spread Cost  : ${total_spread_cost:>14,.2f}")
print(f"  Total Costs        : ${total_commission + total_spread_cost:>14,.2f}")
print(f"  Raw P&L (no costs) : ${total_raw_pnl:>14,.2f}")
print(f"  Avg Commission/Trd : ${total_commission / total if total else 0:>14,.2f}")
print(f"  Avg Spread (pts)   : {sum(t['spread'] for t in trades) / total if total else 0:>14.2f}")

print(f"\n  ── WIN RATE ──")
wr = len(wins) / total * 100 if total > 0 else 0
print(f"  Wins               : {len(wins):>14,}  ({wr:.2f}%)")
print(f"  Losses             : {len(losses):>14,}  ({100 - wr:.2f}%)")
buy_wins = len([t for t in buys if t["result"] == "TP"])
sell_wins = len([t for t in sells if t["result"] == "TP"])
print(f"  BUY win rate       : {buy_wins / len(buys) * 100 if buys else 0:>13.2f}%")
print(f"  SELL win rate      : {sell_wins / len(sells) * 100 if sells else 0:>13.2f}%")

print(f"\n  ── STREAKS ──")
print(f"  Max win streak     : {max_win_streak:>14,}")
print(f"  Max loss streak    : {max_loss_streak:>14,}")

print(f"\n  ── RISK VERIFICATION ──")
avg_budget = sum(t["risk_budget"] for t in trades) / total if total else 0
avg_total_risk = sum(t["total_risk"] for t in trades) / total if total else 0
avg_sl_loss = sum(t["sl_loss"] for t in trades) / total if total else 0
avg_comm = sum(t["commission"] for t in trades) / total if total else 0
print(f"  Avg Risk Budget    : ${avg_budget:>14,.2f}  (what we WANT to risk)")
print(f"  Avg Total Risk     : ${avg_total_risk:>14,.2f}  (SL loss + commission)")
print(f"    Avg SL component : ${avg_sl_loss:>14,.2f}")
print(f"    Avg Comm compnt  : ${avg_comm:>14,.2f}")
print(f"  Risk Violations    : {risk_violations:>14,}  (budget exceeded = BAD)")
if risk_violations > 0:
    print(f"  Worst Overshoot    : ${worst_overshoot:>14,.2f}")
print(f"  Lot-capped trades  : {len(capped):>14,}  ({len(capped)/total*100:.1f}%)")
print(f"  Uncapped trades    : {len(uncapped):>14,}  ({len(uncapped)/total*100:.1f}%)")
print(f"  Max Drawdown       : ${max_dd:>14,.2f}  ({max_dd_pct:.2f}% of peak)")
print(f"  Avg Bars in Trade  : {avg_bars:>14.1f}")

# show first 5 uncapped trades
print(f"\n  ── RISK CHECK: first 5 uncapped trades ──")
print(f"  {'#':>3} {'Dir':<5} {'Bal':>9} {'Budget':>8} {'Lot':>6} {'SLdist':>7} "
      f"{'SL$':>7} {'Comm$':>7} {'Total$':>8} {'OK?':>3} {'NetPnL':>9} {'Res'}")
print(f"  {'─'*3} {'─'*5} {'─'*9} {'─'*8} {'─'*6} {'─'*7} "
      f"{'─'*7} {'─'*7} {'─'*8} {'─'*3} {'─'*9} {'─'*3}")
shown = 0
for t in trades:
    if t["capped"]:
        continue
    ok = "✅" if t["total_risk"] <= t["risk_budget"] + 0.02 else "❌"
    print(
        f"  {shown+1:>3} {t['dir']:<5} ${t['bal_at_entry']:>8,.0f} "
        f"${t['risk_budget']:>7,.2f} {t['lot']:>6.2f} {t['sl_dist']:>7.2f} "
        f"${t['sl_loss']:>6,.2f} ${t['commission']:>6,.2f} "
        f"${t['total_risk']:>7,.2f} {ok:>3} "
        f"${t['pnl']:>8,.2f} {t['result']}"
    )
    shown += 1
    if shown >= 5:
        break

# win/loss breakdown
if wins:
    avg_win_net = gross_profit / len(wins)
    avg_loss_net = gross_loss / len(losses) if losses else 0
    print(f"\n  ── WIN/LOSS BREAKDOWN ──")
    print(f"  Avg Win  (net)     : ${avg_win_net:>14,.2f}")
    print(f"  Avg Loss (net)     : ${avg_loss_net:>14,.2f}")
    print(f"  Avg Win/Loss Ratio : {abs(avg_win_net / avg_loss_net) if avg_loss_net else 0:>14.2f}")

print(f"\n  ── LAST 15 TRADES ──")
hdr = (f"  {'#':>5} {'Dir':<5} {'Lot':>6} {'Entry':>10} {'Spd':>4} "
       f"{'Exit':>10} {'Budg$':>7} {'Tot$':>7} {'P&L':>9} {'Bal':>12} {'R':>2}  {'Time'}")
print(hdr)
print(f"  {'─'*5} {'─'*5} {'─'*6} {'─'*10} {'─'*4} {'─'*10} "
      f"{'─'*7} {'─'*7} {'─'*9} {'─'*12} {'─'*2}  {'─'*16}")
for i, t in enumerate(trades[-15:], start=max(1, total - 14)):
    # Format PnL with the $ sign inside the width-constrained block
    pnl_display = f"${t['pnl']:,.2f}"
    
    print(
        f"  {i:>5} {t['dir']:<5} {t['lot']:>6.2f} {t['entry']:>10,.1f} "
        f"{t['spread']:>4.1f} {t['exit']:>10,.1f} "
        f"${t['risk_budget']:>6,.0f} ${t['total_risk']:>6,.0f} "
        f"{pnl_display:>10} " # Adjusted to 10 to fit the $ and comma safely
        f"${t['balance']:>11,.2f} {t['result'][0]:>2}  {t['time']}"
    )

print(f"\n{SEP}")
print(f"  ${STARTING_BALANCE:,.2f} → ${balance:,.2f}  "
      f"({'+' if total_pnl >= 0 else ''}{total_pnl/STARTING_BALANCE*100:.2f}%)")

cost_total = total_commission + total_spread_cost
if total_raw_pnl > 0:
    print(f"  Costs: ${cost_total:,.2f} "
          f"({cost_total / total_raw_pnl * 100:.1f}% of raw profit)")
else:
    print(f"  Costs: ${cost_total:,.2f}")
print(SEP)
