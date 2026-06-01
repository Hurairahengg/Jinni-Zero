import os, json, csv, time, math
import numpy as np
import pandas as pd
from math import sqrt
from collections import defaultdict

# ============================================================ CONFIG
FILE = "data/NQ/8pt.json"            # change to 16pt.json etc. if needed
OUT_DIR = "out/hma55_break"
COL_TS, COL_OPEN, COL_HIGH, COL_LOW, COL_CLOSE = "timestamp","open","high","low","close"

HMA_PERIOD          = 55
STARTING_CAPITAL    = 1000.0
RISK_PER_TRADE      = 10.0
POINT_VALUE_PER_LOT = 1.0
SLIPPAGE_POINTS     = 0.3            # per side; total 0.6
COMMISSION_RT       = 0.80           # per lot per trade
SLIPPAGE_TOTAL      = 2 * SLIPPAGE_POINTS

TRAIN_FRAC          = 0.70
MIN_SL_DISTANCE     = 0.1            # skip degenerate trades w/ stop < this many points

os.makedirs(OUT_DIR, exist_ok=True)
T0 = time.time()
def tprint(m): print(f"[{time.time()-T0:6.1f}s] {m}", flush=True)


# ============================================================ LOAD
tprint("loading…")
with open(FILE) as f: data = json.load(f)
df = pd.DataFrame(data)
ts_col = next((c for c in [COL_TS,"time","date","datetime","ts"] if c in df.columns), None)
for c in (COL_OPEN, COL_HIGH, COL_LOW, COL_CLOSE):
    if c not in df.columns: raise KeyError(f"missing {c}")
HAS_TS = ts_col is not None
if HAS_TS:
    df[ts_col] = pd.to_datetime(df[ts_col], errors="coerce")
    timestamps = df[ts_col].values
else:
    timestamps = np.arange(len(df))

opn = df[COL_OPEN].astype(np.float64).values
hi  = df[COL_HIGH].astype(np.float64).values
lo  = df[COL_LOW].astype(np.float64).values
cl  = df[COL_CLOSE].astype(np.float64).values
N_BARS = len(df)
TRAIN_END = int(N_BARS * TRAIN_FRAC)
tprint(f"bars: {N_BARS:,}   train_end: {TRAIN_END:,}")


# ============================================================ HMA55
def wma(arr, p):
    w = np.arange(1, p+1, dtype=np.float64); ws = w.sum()
    out = np.full(len(arr), np.nan)
    if len(arr) < p: return out
    out[p-1:] = np.convolve(arr, w[::-1], "valid") / ws
    return out

def hma(arr, p):
    half = p // 2; sp = int(sqrt(p))
    raw = 2*wma(arr, half) - wma(arr, p)
    valid = ~np.isnan(raw)
    rc = np.where(valid, raw, 0.0)
    sm = wma(rc, sp)
    # propagate NaN where sp-window touched any NaN
    inv = (~valid).astype(np.int32); cs = np.cumsum(inv)
    bad = np.zeros(len(arr), bool)
    for i in range(sp-1, len(arr)):
        s = cs[i] - (cs[i-sp] if i-sp >= 0 else 0)
        if s > 0: bad[i] = True
    bad[:sp-1] = True
    sm[bad] = np.nan
    return sm

tprint(f"HMA{HMA_PERIOD}…")
H = hma(cl, HMA_PERIOD)


# ============================================================ STRATEGY WALK
# Rules:
#  - LONG entry when close crosses ABOVE HMA  (prev close <= prev HMA, cur close > cur HMA)
#  - SHORT entry when close crosses BELOW HMA (prev close >= prev HMA, cur close < cur HMA)
#  - Only ONE trade per side per crossover (armed/disarmed gating)
#  - SL = snapshot of HMA at entry bar (frozen price level)
#  - TP = price closes back across HMA in the opposite direction
#  - SL checked intrabar (using low/high), TP checked on close only

tprint("walking strategy…")
trades = []
warmup = HMA_PERIOD + int(sqrt(HMA_PERIOD)) + 2

armed_long  = True
armed_short = True

i = warmup
while i < N_BARS - 1:
    h_prev, h_cur = H[i-1], H[i]
    if math.isnan(h_prev) or math.isnan(h_cur):
        i += 1; continue
    c_prev, c_cur = cl[i-1], cl[i]

    # Rearm gating: if current close is on opposite side of HMA, rearm
    if c_cur < h_cur: armed_long  = True   # we've come back below, can take next long break
    if c_cur > h_cur: armed_short = True   # we've come back above, can take next short break

    entry_side = None
    if c_prev <= h_prev and c_cur > h_cur and armed_long:
        entry_side = 1
    elif c_prev >= h_prev and c_cur < h_cur and armed_short:
        entry_side = -1

    if entry_side is None:
        i += 1; continue

    # ----- ENTRY
    entry_idx   = i
    entry_price = c_cur
    sl_price    = H[i]                       # snapshot of HMA at entry

    if entry_side == 1:
        sl_distance = entry_price - sl_price
    else:
        sl_distance = sl_price - entry_price

    if sl_distance < MIN_SL_DISTANCE:
        # degenerate (HMA right at close) → disarm to avoid spam, skip
        if entry_side == 1: armed_long = False
        else: armed_short = False
        i += 1; continue

    # disarm same-side until we cross back
    if entry_side == 1: armed_long = False
    else: armed_short = False

    # ----- FORWARD WALK TO EXIT
    outcome = "unresolved"; exit_idx = N_BARS - 1; exit_price = cl[-1]
    for j in range(i + 1, N_BARS):
        # SL: intrabar
        if entry_side == 1:
            if lo[j] <= sl_price:
                outcome = "sl"; exit_idx = j; exit_price = sl_price; break
        else:
            if hi[j] >= sl_price:
                outcome = "sl"; exit_idx = j; exit_price = sl_price; break
        # TP: close crosses HMA back the other way
        h_j = H[j]
        if math.isnan(h_j): continue
        if entry_side == 1 and cl[j] < h_j:
            outcome = "tp"; exit_idx = j; exit_price = cl[j]; break
        if entry_side == -1 and cl[j] > h_j:
            outcome = "tp"; exit_idx = j; exit_price = cl[j]; break

    # ----- PNL
    if entry_side == 1: gross_pts = exit_price - entry_price
    else:               gross_pts = entry_price - exit_price

    lot         = RISK_PER_TRADE / sl_distance
    commission  = lot * COMMISSION_RT
    slip_cost_d = SLIPPAGE_TOTAL * lot * POINT_VALUE_PER_LOT

    if outcome in ("tp","sl"):
        net_pts = gross_pts - SLIPPAGE_TOTAL
        net_dol = net_pts * lot * POINT_VALUE_PER_LOT - commission
    else:
        net_pts = 0.0; net_dol = 0.0   # unresolved → exclude from PnL math

    trades.append({
        "entry_idx": entry_idx, "exit_idx": exit_idx,
        "side": entry_side,
        "entry_price": entry_price, "sl_price": sl_price,
        "sl_distance_pts": sl_distance,
        "exit_price": exit_price, "outcome": outcome,
        "gross_pts": gross_pts, "net_pts": net_pts,
        "lot_size": lot, "commission_dol": commission,
        "slippage_dol": slip_cost_d,
        "net_dol": net_dol,
        "hold_bars": exit_idx - entry_idx,
    })

    # advance: we don't auto-rearm here. Rearm happens at top of loop when we see opposite side.
    i = exit_idx + 1

tprint(f"generated {len(trades):,} trades")


# ============================================================ METRICS
def safe_div(a, b):
    if b > 0: return a/b
    if a > 0: return float("inf")
    return 0.0

def compute_metrics(ts_):
    n = len(ts_)
    resolved = [t for t in ts_ if t["outcome"] in ("tp","sl")]
    unresolved = n - len(resolved)
    nr = len(resolved)
    if nr == 0:
        return dict(trades=n, resolved=0, wins=0, losses=0, unresolved=unresolved,
                    wr=0.0, pf=0.0, net_dol=0.0, ev_dol=0.0, ev_R=0.0,
                    avg_win_pts=0.0, avg_loss_pts=0.0,
                    avg_win_dol=0.0, avg_loss_dol=0.0,
                    gross_profit_pts=0.0, gross_loss_pts=0.0,
                    commission_total=0.0, slippage_total=0.0,
                    max_dd_dol=0.0, max_dd_pct=0.0, mls=0,
                    final_balance=STARTING_CAPITAL, avg_hold=0.0, med_hold=0.0,
                    sharpe=0.0, sortino=0.0, avg_sl_pts=0.0)
    wins   = [t for t in resolved if t["outcome"]=="tp" and t["net_dol"] > 0]
    losses = [t for t in resolved if t not in wins]   # SL hits + any rare TP-net-negative
    # cleaner: classify by dollar outcome (since "tp" with tiny gain net of costs could be ≤0)
    wins   = [t for t in resolved if t["net_dol"] > 0]
    losses = [t for t in resolved if t["net_dol"] <= 0]
    nw, nl = len(wins), len(losses)
    wr = nw/nr*100

    gross_profit_pts = sum(t["gross_pts"] for t in wins)
    gross_loss_pts   = sum(-t["gross_pts"] for t in losses)
    gp_d = sum(t["net_dol"] for t in wins)
    gl_d = sum(-t["net_dol"] for t in losses)
    pf = safe_div(gp_d, gl_d)
    net_d = sum(t["net_dol"] for t in resolved)
    ev_d  = net_d / nr
    ev_R  = ev_d / RISK_PER_TRADE
    avg_win_pts  = (sum(t["gross_pts"] for t in wins)/nw) if nw else 0
    avg_loss_pts = (sum(t["gross_pts"] for t in losses)/nl) if nl else 0
    avg_win_dol  = (gp_d/nw) if nw else 0
    avg_loss_dol = (-gl_d/nl) if nl else 0
    comm_t = sum(t["commission_dol"] for t in resolved)
    slip_t = sum(t["slippage_dol"]   for t in resolved)
    avg_sl = sum(t["sl_distance_pts"] for t in resolved)/nr

    # equity curve / DD
    eq = peak = STARTING_CAPITAL
    max_dd_d = max_dd_p = 0.0
    cls_ = mls = 0
    for t in resolved:
        eq += t["net_dol"]
        if eq > peak: peak = eq
        dd = peak - eq
        ddp = (dd/peak*100) if peak>0 else 0
        if dd > max_dd_d: max_dd_d = dd
        if ddp > max_dd_p: max_dd_p = ddp
        if t["net_dol"] <= 0:
            cls_ += 1
            if cls_ > mls: mls = cls_
        else: cls_ = 0
    holds = [t["hold_bars"] for t in resolved]
    avg_h = (sum(holds)/len(holds)) if holds else 0
    med_h = float(np.median(holds)) if holds else 0

    rets = np.array([t["net_dol"] for t in resolved], dtype=np.float64)
    if nr >= 2:
        m_, s_ = float(rets.mean()), float(rets.std())
        downs = rets[rets < 0]
        ds = float(downs.std()) if len(downs)>1 else (float(abs(downs[0])) if len(downs)==1 else 0.0)
        sharpe = (m_/s_*sqrt(nr)) if s_>0 else 0.0
        sortino = (m_/ds*sqrt(nr)) if ds>0 else 0.0
    else: sharpe = sortino = 0.0

    return dict(trades=n, resolved=nr, wins=nw, losses=nl, unresolved=unresolved,
                wr=wr, pf=pf, net_dol=net_d, ev_dol=ev_d, ev_R=ev_R,
                avg_win_pts=avg_win_pts, avg_loss_pts=avg_loss_pts,
                avg_win_dol=avg_win_dol, avg_loss_dol=avg_loss_dol,
                gross_profit_pts=gross_profit_pts, gross_loss_pts=gross_loss_pts,
                commission_total=comm_t, slippage_total=slip_t,
                max_dd_dol=max_dd_d, max_dd_pct=max_dd_p, mls=mls,
                final_balance=eq, avg_hold=avg_h, med_hold=med_h,
                sharpe=sharpe, sortino=sortino, avg_sl_pts=avg_sl)


m_full  = compute_metrics(trades)
m_train = compute_metrics([t for t in trades if t["entry_idx"] <  TRAIN_END])
m_oos   = compute_metrics([t for t in trades if t["entry_idx"] >= TRAIN_END])

# yearly
by_year = defaultdict(list)
if HAS_TS:
    for t in trades:
        ts_ = pd.Timestamp(timestamps[t["entry_idx"]])
        if pd.notna(ts_): by_year[ts_.year].append(t)
yearly = {y: compute_metrics(v) for y, v in sorted(by_year.items())}


# ============================================================ PRINT
def sect(t): print(); print("="*90); print(f" {t}"); print("="*90)

sect("STRATEGY DEFINITION")
print(f"  data           : {FILE}")
print(f"  bars           : {N_BARS:,}   train_end: {TRAIN_END:,}")
print(f"  HMA period     : {HMA_PERIOD}")
print(f"  entry          : close crosses above HMA -> long  /  below -> short")
print(f"  one per side   : armed/disarmed until close re-crosses HMA")
print(f"  SL             : snapshot of HMA at entry (frozen price, intrabar)")
print(f"  TP             : close back across HMA in opposite direction")
print(f"  costs          : ${COMMISSION_RT}/lot RT comm, {SLIPPAGE_POINTS}pt slip each side ({SLIPPAGE_TOTAL}pt total)")
print(f"  capital / risk : ${STARTING_CAPITAL:,.0f}  /  ${RISK_PER_TRADE}/trade  (no compounding)")

def print_block(label, m):
    print(f"\n[{label}]")
    print(f"  trades / resolved / unresolved : {m['trades']:,} / {m['resolved']:,} / {m['unresolved']:,}")
    print(f"  wins / losses                  : {m['wins']:,} / {m['losses']:,}")
    print(f"  win rate                       : {m['wr']:.2f}%")
    print(f"  profit factor (dollars)        : {m['pf']:.3f}")
    print(f"  net profit                     : ${m['net_dol']:+,.2f}")
    print(f"  expectancy per trade           : ${m['ev_dol']:+.2f}   ({m['ev_R']:+.3f} R)")
    print(f"  avg win  / avg loss   (pts)    : {m['avg_win_pts']:+.2f} / {m['avg_loss_pts']:+.2f}")
    print(f"  avg win  / avg loss   ($)      : {m['avg_win_dol']:+.2f} / {m['avg_loss_dol']:+.2f}")
    print(f"  gross profit / loss   (pts)    : {m['gross_profit_pts']:+,.1f} / {m['gross_loss_pts']:,.1f}")
    print(f"  commission paid                : ${m['commission_total']:,.2f}")
    print(f"  slippage cost                  : ${m['slippage_total']:,.2f}")
    print(f"  max drawdown $ / %             : ${m['max_dd_dol']:,.2f} / {m['max_dd_pct']:.2f}%")
    print(f"  max losing streak              : {m['mls']}")
    print(f"  hold bars avg / median         : {m['avg_hold']:.2f} / {m['med_hold']:.1f}")
    print(f"  avg SL distance (pts)          : {m['avg_sl_pts']:.2f}")
    print(f"  Sharpe / Sortino (per trade)   : {m['sharpe']:.3f} / {m['sortino']:.3f}")
    print(f"  final balance                  : ${m['final_balance']:,.2f}")

sect("FULL SAMPLE RESULTS")
print_block("FULL", m_full)

sect("TRAIN / OOS")
print_block("TRAIN", m_train)
print_block("OOS",   m_oos)

sect("YEARLY")
if yearly:
    print(f"  {'year':>5} {'trd':>5} {'res':>5} {'WR%':>6} {'PF$':>6} "
          f"{'net$':>10} {'EV$':>7} {'DD%':>6} {'mls':>4} {'avgSL':>6}")
    print("-"*82)
    for y, m in yearly.items():
        print(f"  {y:>5} {m['trades']:>5,} {m['resolved']:>5,} {m['wr']:>5.2f}% "
              f"{m['pf']:>6.3f} {m['net_dol']:>+10,.1f} {m['ev_dol']:>+7.2f} "
              f"{m['max_dd_pct']:>5.2f}% {m['mls']:>4} {m['avg_sl_pts']:>6.2f}")
else:
    print("  (no timestamps available)")

sect("VERDICT")
o = m_oos
if o["pf"] >= 1.30 and o["net_dol"] > 0 and o["max_dd_pct"] <= 30 and o["resolved"] >= 50:
    v = "STRONG ✓"
elif o["pf"] >= 1.10 and o["net_dol"] > 0:
    v = "WEAK"
else:
    v = "FAIL"
print(f"  OOS  trades={o['resolved']:,}  PF$={o['pf']:.3f}  "
      f"net$={o['net_dol']:+,.2f}  DD%={o['max_dd_pct']:.2f}  ->  {v}")


# ============================================================ CSV + PNG
def save_csv(path, rows, lbl):
    if not rows: tprint(f"  [csv] {lbl}: empty"); return
    keys=[]; seen=set()
    for r in rows:
        for k in r:
            if k not in seen: seen.add(k); keys.append(k)
    with open(path,"w",newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore", restval="")
        w.writeheader()
        for r in rows: w.writerow(r)
    tprint(f"  [csv] {lbl}: {len(rows):,} rows -> {path}")

# trades CSV
trade_rows = []
eq = STARTING_CAPITAL; peak = eq
for tid, t in enumerate(trades, 1):
    eq += t["net_dol"]
    if eq > peak: peak = eq
    dd_now = (peak - eq) / peak * 100 if peak > 0 else 0
    trade_rows.append({
        "trade_id": tid,
        "split": "train" if t["entry_idx"] < TRAIN_END else "oos",
        "direction": "long" if t["side"]==1 else "short",
        "entry_time": str(timestamps[t["entry_idx"]]),
        "entry_price": t["entry_price"],
        "sl_price": t["sl_price"],
        "sl_distance_pts": t["sl_distance_pts"],
        "exit_time": str(timestamps[t["exit_idx"]]),
        "exit_price": t["exit_price"],
        "outcome": t["outcome"],
        "hold_bars": t["hold_bars"],
        "gross_pts": t["gross_pts"],
        "net_pts": t["net_pts"],
        "lot_size": t["lot_size"],
        "commission_dol": t["commission_dol"],
        "slippage_dol": t["slippage_dol"],
        "net_pnl_dol": t["net_dol"],
        "equity_after_trade": eq,
        "drawdown_pct_after_trade": dd_now,
    })
save_csv(os.path.join(OUT_DIR, "hma55_break_trades.csv"), trade_rows, "trades")

# yearly CSV
yearly_rows = []
for y, m in yearly.items():
    row = {"year": y}
    for k in ("trades","resolved","wins","losses","unresolved","wr","pf",
              "net_dol","ev_dol","ev_R","max_dd_dol","max_dd_pct","mls",
              "avg_win_pts","avg_loss_pts","avg_win_dol","avg_loss_dol",
              "commission_total","slippage_total","sharpe","sortino","avg_sl_pts",
              "final_balance"):
        row[k] = m[k]
    yearly_rows.append(row)
save_csv(os.path.join(OUT_DIR, "hma55_break_yearly.csv"), yearly_rows, "yearly")

# equity PNG
try:
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    resolved_trades = [t for t in trades if t["outcome"] in ("tp","sl")]
    if resolved_trades:
        resolved_trades.sort(key=lambda t: t["entry_idx"])
        dol = np.array([t["net_dol"] for t in resolved_trades])
        eq_arr = STARTING_CAPITAL + np.cumsum(dol)
        peak_a = np.maximum.accumulate(eq_arr); dd_a = peak_a - eq_arr
        ents = [t["entry_idx"] for t in resolved_trades]
        times = pd.to_datetime(timestamps[ents]) if HAS_TS else np.arange(len(ents))

        fig, ax = plt.subplots(2,1,figsize=(11,6),gridspec_kw={"height_ratios":[3,1]})
        ax[0].plot(times, eq_arr, lw=1.2)
        ax[0].set_title(f"HMA{HMA_PERIOD} Cross Strategy — final ${eq_arr[-1]:,.0f}  "
                        f"trades={len(resolved_trades):,}  PF$={m_full['pf']:.3f}  "
                        f"WR={m_full['wr']:.2f}%")
        ax[0].set_ylabel("Equity $")
        if HAS_TS and TRAIN_END > 0:
            st = pd.Timestamp(timestamps[TRAIN_END-1])
            for axx in ax: axx.axvline(st, ls="--", c="gray", lw=1, label="train/OOS")
        ax[1].fill_between(times, dd_a, 0, color="crimson", alpha=0.4)
        ax[1].set_ylabel("Drawdown $")
        plt.tight_layout()
        plt.savefig(os.path.join(OUT_DIR, "hma55_break_equity.png"), dpi=110); plt.close()
        tprint(f"  [png] equity curve saved")
except Exception as e:
    tprint(f"  matplotlib unavailable: {e}")

tprint("DONE 🫡")