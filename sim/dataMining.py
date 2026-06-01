import os, json, csv, time, math
from collections import defaultdict
import numpy as np
import pandas as pd

# ============================================================ CONFIG
FILE    = "data/NQ/8pt.json"
OUT_DIR = "out/reversal_first_passage"
COL_TS, COL_OPEN, COL_HIGH, COL_LOW, COL_CLOSE = "timestamp","open","high","low","close"

STARTING_CAPITAL    = 1000.0
RISK_PER_TRADE      = 10.0
POINT_VALUE_PER_LOT = 1.0
SLIPPAGE_POINTS     = 0.3        # per execution (entry + exit = 0.6 total)
COMMISSION_PER_LOT  = 0.80       # per completed trade (one charge)

SLIPPAGE_TOTAL = 2 * SLIPPAGE_POINTS    # 0.6 pts

TP_LIST  = [8, 16, 24]
SL_LIST  = [8, 16, "DYNAMIC"]    # DYNAMIC = reversal candle low/high

FP_MAX_FORWARD = 200
TRAIN_FRAC     = 0.70

os.makedirs(OUT_DIR, exist_ok=True)
T0 = time.time()
def tprint(m): print(f"[{time.time()-T0:7.1f}s] {m}", flush=True)


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
direction = np.where(cl > opn, 1, np.where(cl < opn, -1, 0)).astype(np.int8)
neutral_count = int((direction == 0).sum())
tprint(f"bars: {N_BARS:,}   train_end: {TRAIN_END:,}   neutral: {neutral_count:,}")


# ============================================================ STREAK + REVERSAL EVENTS
tprint("detecting first reversal candles (no prior-run filter)…")
streak_len = np.zeros(N_BARS, np.int32)
prev_dir = 0; cnt = 0
for i in range(N_BARS):
    d = int(direction[i])
    if d == 0: cnt = 0; prev_dir = 0
    elif d == prev_dir: cnt += 1
    else: cnt = 1; prev_dir = d
    streak_len[i] = cnt

# First reversal candle = streak_len[t]==1 AND direction[t-1] == -direction[t]
events = []
for t in range(1, N_BARS):
    if direction[t] == 0: continue
    if streak_len[t] != 1: continue
    if direction[t-1] != -direction[t]: continue
    events.append({
        "idx": t,
        "side": int(direction[t]),
        "entry": float(cl[t]),
        "rev_low": float(lo[t]),
        "rev_high": float(hi[t]),
    })

n_long  = sum(1 for e in events if e["side"] == 1)
n_short = sum(1 for e in events if e["side"] == -1)
tprint(f"  events: {len(events):,}  (long rev: {n_long:,}, short rev: {n_short:,})")


# ============================================================ FIRST-PASSAGE SIMULATION
def simulate(event, tp_pts, sl_kind):
    """First-passage on actual HIGH/LOW path.
       Returns dict with outcome, gross_pts, sl_pts_used, hold_bars."""
    side  = event["side"]
    entry = event["entry"]
    eidx  = event["idx"]

    # Resolve SL distance for this trade
    if sl_kind == "DYNAMIC":
        if side == 1:    sl_pts = entry - event["rev_low"]
        else:            sl_pts = event["rev_high"] - entry
    else:
        sl_pts = float(sl_kind)
    if sl_pts <= 0:
        return None    # skip degenerate stop

    if side == 1:
        tp_lvl = entry + tp_pts;  sl_lvl = entry - sl_pts
    else:
        tp_lvl = entry - tp_pts;  sl_lvl = entry + sl_pts

    end = min(eidx + FP_MAX_FORWARD, N_BARS - 1)
    outcome = "unresolved"; exit_idx = end

    for j in range(eidx + 1, end + 1):
        h, l = hi[j], lo[j]
        if side == 1:
            tp_hit = h >= tp_lvl
            sl_hit = l <= sl_lvl
        else:
            tp_hit = l <= tp_lvl
            sl_hit = h >= sl_lvl
        if tp_hit and sl_hit:                # same-bar tie -> loss
            outcome = "loss"; exit_idx = j; break
        if sl_hit:
            outcome = "loss"; exit_idx = j; break
        if tp_hit:
            outcome = "win"; exit_idx = j; break

    return {
        "event": event,
        "outcome": outcome,
        "exit_idx": exit_idx,
        "hold_bars": exit_idx - eidx,
        "tp_pts": tp_pts,
        "sl_pts": sl_pts,
    }


def pnl_from_trade(t, sizing="standard"):
    """Apply cost model + lot sizing. Returns gross/net pts and dollars."""
    side    = t["event"]["side"]
    tp_pts  = t["tp_pts"]
    sl_pts  = t["sl_pts"]
    outcome = t["outcome"]

    if sizing == "standard":
        lot = RISK_PER_TRADE / sl_pts
    else:
        # cost-adjusted: add slippage points + commission equivalent
        # commission_equivalent_pts = COMMISSION_PER_LOT / (POINT_VALUE_PER_LOT * 1 lot) = $0.80 / $1 = 0.8 pts
        cost_pts = SLIPPAGE_TOTAL + (COMMISSION_PER_LOT / POINT_VALUE_PER_LOT)
        lot = RISK_PER_TRADE / (sl_pts + cost_pts)

    commission = lot * COMMISSION_PER_LOT
    slip_cost_pts  = SLIPPAGE_TOTAL
    slip_cost_dol  = slip_cost_pts * lot * POINT_VALUE_PER_LOT

    if outcome == "win":
        gross_pts = tp_pts
        net_pts   = tp_pts - SLIPPAGE_TOTAL
        net_dol   = net_pts * lot * POINT_VALUE_PER_LOT - commission
    elif outcome == "loss":
        gross_pts = -sl_pts
        net_pts   = -(sl_pts + SLIPPAGE_TOTAL)
        net_dol   = net_pts * lot * POINT_VALUE_PER_LOT - commission
    else:
        gross_pts = 0.0
        net_pts   = 0.0
        net_dol   = 0.0
        commission = 0.0
        slip_cost_dol = 0.0

    return {
        "lot": lot, "commission": commission,
        "slippage_dol": slip_cost_dol,
        "gross_pts": gross_pts, "net_pts": net_pts, "net_dol": net_dol,
    }


# ============================================================ METRICS
def safe_div(a, b):
    if b > 0: return a / b
    if a > 0: return float("inf")
    return 0.0

def compute_metrics(trades_with_pnl):
    n = len(trades_with_pnl)
    resolved = [t for t in trades_with_pnl if t["outcome"] in ("win","loss")]
    unresolved = n - len(resolved)
    nr = len(resolved)
    if nr == 0:
        return _empty(n, unresolved)
    wins   = [t for t in resolved if t["outcome"] == "win"]
    losses = [t for t in resolved if t["outcome"] == "loss"]
    nw, nl = len(wins), len(losses)
    wr = nw / nr * 100
    gross_profit_pts = sum(t["pnl"]["gross_pts"] for t in wins)
    gross_loss_pts   = sum(-t["pnl"]["gross_pts"] for t in losses)
    gross_profit_d   = sum(t["pnl"]["net_dol"] + t["pnl"]["commission"] + t["pnl"]["slippage_dol"]
                           for t in wins)   # add back costs to get gross
    gross_loss_d     = sum(-t["pnl"]["net_dol"] + (-t["pnl"]["commission"] - t["pnl"]["slippage_dol"])
                           for t in losses)
    commission_total = sum(t["pnl"]["commission"] for t in resolved)
    slippage_total_d = sum(t["pnl"]["slippage_dol"] for t in resolved)
    net_profit_d     = sum(t["pnl"]["net_dol"] for t in resolved)

    pf_pts = safe_div(gross_profit_pts, gross_loss_pts)
    pf_d   = safe_div(gross_profit_d, gross_loss_d)

    avg_win_pts  = (sum(t["pnl"]["gross_pts"] for t in wins) / nw) if nw else 0.0
    avg_loss_pts = (sum(t["pnl"]["gross_pts"] for t in losses) / nl) if nl else 0.0
    avg_win_d    = (sum(t["pnl"]["net_dol"] for t in wins) / nw) if nw else 0.0
    avg_loss_d   = (sum(t["pnl"]["net_dol"] for t in losses) / nl) if nl else 0.0

    ev_pts = (sum(t["pnl"]["net_pts"] for t in resolved) / nr)
    ev_dol = net_profit_d / nr
    avg_sl  = (sum(t["sl_pts"] for t in resolved) / nr)
    ev_R    = ev_dol / RISK_PER_TRADE   # since risk = $10 per trade target

    # equity / DD
    eq = peak = STARTING_CAPITAL
    max_dd_d = max_dd_p = 0.0
    cls_ = mls = 0
    eq_curve = [STARTING_CAPITAL]
    for t in resolved:
        eq += t["pnl"]["net_dol"]
        if eq > peak: peak = eq
        dd = peak - eq
        ddp = (dd / peak * 100) if peak > 0 else 0
        if dd > max_dd_d: max_dd_d = dd
        if ddp > max_dd_p: max_dd_p = ddp
        if t["outcome"] == "loss":
            cls_ += 1
            if cls_ > mls: mls = cls_
        else: cls_ = 0
        eq_curve.append(eq)
    holds = [t["hold_bars"] for t in resolved]
    avg_h = (sum(holds)/len(holds)) if holds else 0.0
    med_h = float(np.median(holds)) if holds else 0.0

    def sblk(side_val):
        ts_ = [t for t in resolved if t["event"]["side"] == side_val]
        nn = len(ts_)
        if nn == 0: return dict(trades=0, wr=0.0, pf=0.0, net=0.0, ev=0.0)
        ww = [x for x in ts_ if x["outcome"]=="win"]
        ll = [x for x in ts_ if x["outcome"]=="loss"]
        gw = sum(x["pnl"]["net_dol"] + x["pnl"]["commission"] + x["pnl"]["slippage_dol"] for x in ww)
        gl = sum(-x["pnl"]["net_dol"] - x["pnl"]["commission"] - x["pnl"]["slippage_dol"] for x in ll)
        net = sum(x["pnl"]["net_dol"] for x in ts_)
        return dict(trades=nn, wr=len(ww)/nn*100,
                    pf=safe_div(gw, gl), net=net, ev=net/nn)

    return {
        "trades": n, "resolved": nr, "wins": nw, "losses": nl, "unresolved": unresolved,
        "wr": wr,
        "gross_profit_pts": gross_profit_pts, "gross_loss_pts": gross_loss_pts,
        "gross_profit_dol": gross_profit_d, "gross_loss_dol": gross_loss_d,
        "commission_total": commission_total,
        "slippage_total_dol": slippage_total_d,
        "net_profit_dol": net_profit_d,
        "pf_pts": pf_pts, "pf_dol": pf_d,
        "avg_win_pts": avg_win_pts, "avg_loss_pts": avg_loss_pts,
        "avg_win_dol": avg_win_d, "avg_loss_dol": avg_loss_d,
        "expectancy_pts": ev_pts, "expectancy_dol": ev_dol, "expectancy_R": ev_R,
        "avg_sl_pts": avg_sl,
        "max_dd_dol": max_dd_d, "max_dd_pct": max_dd_p, "mls": mls,
        "final_balance": eq,
        "avg_hold": avg_h, "med_hold": med_h,
        "long":  sblk(1), "short": sblk(-1),
        "eq_curve": eq_curve,
    }

def _empty(n, unr):
    z = dict(trades=0,wr=0.0,pf=0.0,net=0.0,ev=0.0)
    return {"trades":n,"resolved":0,"wins":0,"losses":0,"unresolved":unr,
            "wr":0.0,"gross_profit_pts":0.0,"gross_loss_pts":0.0,
            "gross_profit_dol":0.0,"gross_loss_dol":0.0,
            "commission_total":0.0,"slippage_total_dol":0.0,
            "net_profit_dol":0.0,"pf_pts":0.0,"pf_dol":0.0,
            "avg_win_pts":0.0,"avg_loss_pts":0.0,"avg_win_dol":0.0,"avg_loss_dol":0.0,
            "expectancy_pts":0.0,"expectancy_dol":0.0,"expectancy_R":0.0,
            "avg_sl_pts":0.0,"max_dd_dol":0.0,"max_dd_pct":0.0,"mls":0,
            "final_balance":STARTING_CAPITAL,"avg_hold":0.0,"med_hold":0.0,
            "long":z,"short":z,"eq_curve":[STARTING_CAPITAL]}


# ============================================================ MAIN SWEEP
TP_SL_PAIRS = [(tp, sl) for tp in TP_LIST for sl in SL_LIST]
tprint(f"running {len(TP_SL_PAIRS)} TP/SL combos × {len(events):,} events…")

all_results = []   # one entry per (tp, sl, sizing) combo
trades_by_combo = {}

for sizing in ("standard", "cost_adjusted"):
    for (tp_pts, sl_kind) in TP_SL_PAIRS:
        sims = []
        for ev in events:
            sim = simulate(ev, tp_pts, sl_kind)
            if sim is None: continue
            sim["pnl"] = pnl_from_trade(sim, sizing=sizing)
            sims.append(sim)

        # full / train / oos
        m_full  = compute_metrics(sims)
        m_train = compute_metrics([t for t in sims if t["event"]["idx"] <  TRAIN_END])
        m_oos   = compute_metrics([t for t in sims if t["event"]["idx"] >= TRAIN_END])

        # yearly
        by_year = defaultdict(list)
        for t in sims:
            if not HAS_TS: continue
            ts_ = pd.Timestamp(timestamps[t["event"]["idx"]])
            if pd.notna(ts_): by_year[ts_.year].append(t)
        yearly = {}
        for y, ts_ in sorted(by_year.items()):
            yearly[y] = compute_metrics(ts_)

        all_results.append({
            "tp_pts": tp_pts, "sl_kind": sl_kind, "sizing": sizing,
            "full": m_full, "train": m_train, "oos": m_oos, "yearly": yearly,
        })
        trades_by_combo[(tp_pts, sl_kind, sizing)] = sims
tprint(f"sweep done: {len(all_results)} combos")


# ============================================================ PRINT
def sect(t): print(); print("="*100); print(f" {t}"); print("="*100)

sect("EXECUTIVE SUMMARY")
print(f"  data: {FILE}")
print(f"  bars: {N_BARS:,}   train_end: {TRAIN_END:,}")
print(f"  reversal events total: {len(events):,}  (long: {n_long:,}, short: {n_short:,})")
print(f"  TP set: {TP_LIST}    SL set: {SL_LIST}")
print(f"  costs: ${COMMISSION_PER_LOT}/lot commission, {SLIPPAGE_POINTS}pt slippage each side ({SLIPPAGE_TOTAL}pt total)")
print(f"  risk per trade: ${RISK_PER_TRADE}    starting balance: ${STARTING_CAPITAL:,.0f}")


def fmt(m):
    return (f"trd={m['trades']:>5,} res={m['resolved']:>5,} "
            f"WR={m['wr']:>5.2f}% PF$={m['pf_dol']:>5.3f} "
            f"net$={m['net_profit_dol']:>+9,.1f} EV$={m['expectancy_dol']:>+5.2f} "
            f"EVR={m['expectancy_R']:>+5.3f} DD%={m['max_dd_pct']:>5.2f} "
            f"avgSL={m['avg_sl_pts']:>5.2f}")

# ----- 1. MAIN RESULTS TABLE — standard sizing
sect("1. RESULTS PER TP/SL  (STANDARD sizing, FULL sample)")
print(f"  {'TP':>3} {'SL':<8} | " + fmt(_empty(0,0)).split("trd=")[0] + "metrics →")
print("-"*100)
for r in all_results:
    if r["sizing"] != "standard": continue
    sl_lbl = "DYN" if r["sl_kind"]=="DYNAMIC" else str(r["sl_kind"])
    print(f"  {r['tp_pts']:>3} {sl_lbl:<8} | {fmt(r['full'])}")

# ----- 2. COST-ADJUSTED sizing
sect("2. RESULTS PER TP/SL  (COST-ADJUSTED sizing, FULL sample)")
for r in all_results:
    if r["sizing"] != "cost_adjusted": continue
    sl_lbl = "DYN" if r["sl_kind"]=="DYNAMIC" else str(r["sl_kind"])
    print(f"  {r['tp_pts']:>3} {sl_lbl:<8} | {fmt(r['full'])}")

# ----- 3. EXPANDED METRICS for standard sizing
sect("3. EXPANDED METRICS  (standard sizing, FULL sample)")
print(f"  {'TP':>3} {'SL':<8} | {'gross+pts':>10} {'gross-pts':>10} {'comm$':>9} {'slip$':>9} "
      f"{'avgW pts':>9} {'avgL pts':>9} {'avgWdol':>9} {'avgLdol':>9} {'mls':>4} {'final$':>10}")
print("-"*150)
for r in all_results:
    if r["sizing"] != "standard": continue
    m = r["full"]
    sl_lbl = "DYN" if r["sl_kind"]=="DYNAMIC" else str(r["sl_kind"])
    print(f"  {r['tp_pts']:>3} {sl_lbl:<8} | "
          f"{m['gross_profit_pts']:>10,.0f} {m['gross_loss_pts']:>10,.0f} "
          f"{m['commission_total']:>9,.1f} {m['slippage_total_dol']:>9,.1f} "
          f"{m['avg_win_pts']:>9.2f} {m['avg_loss_pts']:>9.2f} "
          f"{m['avg_win_dol']:>+9.2f} {m['avg_loss_dol']:>+9.2f} "
          f"{m['mls']:>4} {m['final_balance']:>10,.1f}")

# ----- 4. LONG / SHORT BREAKDOWN  (standard, full)
sect("4. LONG / SHORT BREAKDOWN  (standard sizing, FULL sample)")
print(f"  {'TP':>3} {'SL':<8} | "
      f"L {'trd':>5} {'WR%':>5} {'PF$':>6} {'net$':>9} {'EV$':>6} | "
      f"S {'trd':>5} {'WR%':>5} {'PF$':>6} {'net$':>9} {'EV$':>6}")
print("-"*110)
for r in all_results:
    if r["sizing"] != "standard": continue
    L = r["full"]["long"]; S = r["full"]["short"]
    sl_lbl = "DYN" if r["sl_kind"]=="DYNAMIC" else str(r["sl_kind"])
    print(f"  {r['tp_pts']:>3} {sl_lbl:<8} | "
          f"L {L['trades']:>5,} {L['wr']:>4.1f}% {L['pf']:>6.3f} {L['net']:>+9,.1f} {L['ev']:>+6.2f} | "
          f"S {S['trades']:>5,} {S['wr']:>4.1f}% {S['pf']:>6.3f} {S['net']:>+9,.1f} {S['ev']:>+6.2f}")

# ----- 5. IS / OOS SPLIT  (standard)
sect("5. IS / OOS SPLIT  (standard sizing)")
print(f"  {'TP':>3} {'SL':<8} | "
      f"T {'trd':>5} {'WR%':>5} {'PF$':>6} {'net$':>9} {'EV$':>6} {'DD%':>5} | "
      f"O {'trd':>5} {'WR%':>5} {'PF$':>6} {'net$':>9} {'EV$':>6} {'DD%':>5}")
print("-"*120)
for r in all_results:
    if r["sizing"] != "standard": continue
    T = r["train"]; O = r["oos"]
    sl_lbl = "DYN" if r["sl_kind"]=="DYNAMIC" else str(r["sl_kind"])
    print(f"  {r['tp_pts']:>3} {sl_lbl:<8} | "
          f"T {T['trades']:>5,} {T['wr']:>4.1f}% {T['pf_dol']:>6.3f} {T['net_profit_dol']:>+9,.1f} "
          f"{T['expectancy_dol']:>+6.2f} {T['max_dd_pct']:>4.1f}% | "
          f"O {O['trades']:>5,} {O['wr']:>4.1f}% {O['pf_dol']:>6.3f} {O['net_profit_dol']:>+9,.1f} "
          f"{O['expectancy_dol']:>+6.2f} {O['max_dd_pct']:>4.1f}%")

# ----- 6. YEARLY BREAKDOWN (standard, only combos with reasonable activity)
sect("6. YEARLY BREAKDOWN  (standard sizing)")
for r in all_results:
    if r["sizing"] != "standard": continue
    if not r["yearly"]: continue
    sl_lbl = "DYN" if r["sl_kind"]=="DYNAMIC" else str(r["sl_kind"])
    print(f"\n  --- TP={r['tp_pts']}  SL={sl_lbl} ---")
    print(f"  {'year':>5} {'trd':>5} {'WR%':>5} {'PF$':>6} {'net$':>9} {'EV$':>6} {'DD%':>5} "
          f"{'L_net$':>9} {'S_net$':>9}")
    for y, m in r["yearly"].items():
        if m["resolved"] < 20: continue
        print(f"  {y:>5} {m['resolved']:>5,} {m['wr']:>4.1f}% {m['pf_dol']:>6.3f} "
              f"{m['net_profit_dol']:>+9,.1f} {m['expectancy_dol']:>+6.2f} {m['max_dd_pct']:>4.1f}% "
              f"{m['long']['net']:>+9,.1f} {m['short']['net']:>+9,.1f}")

# ----- 7. STANDARD vs COST-ADJUSTED COMPARISON
sect("7. STANDARD vs COST-ADJUSTED SIZING  (FULL sample)")
print(f"  {'TP':>3} {'SL':<8} | "
      f"std: {'net$':>9} {'EV$':>6} {'PF$':>6} {'DD%':>5} | "
      f"adj: {'net$':>9} {'EV$':>6} {'PF$':>6} {'DD%':>5}")
print("-"*110)
for (tp, sl) in TP_SL_PAIRS:
    std = next(r for r in all_results if r["tp_pts"]==tp and r["sl_kind"]==sl and r["sizing"]=="standard")
    adj = next(r for r in all_results if r["tp_pts"]==tp and r["sl_kind"]==sl and r["sizing"]=="cost_adjusted")
    sl_lbl = "DYN" if sl=="DYNAMIC" else str(sl)
    print(f"  {tp:>3} {sl_lbl:<8} | "
          f"std: {std['full']['net_profit_dol']:>+9,.1f} {std['full']['expectancy_dol']:>+6.2f} "
          f"{std['full']['pf_dol']:>6.3f} {std['full']['max_dd_pct']:>4.1f}% | "
          f"adj: {adj['full']['net_profit_dol']:>+9,.1f} {adj['full']['expectancy_dol']:>+6.2f} "
          f"{adj['full']['pf_dol']:>6.3f} {adj['full']['max_dd_pct']:>4.1f}%")

# ----- 8. VERDICT
sect("8. VERDICT — Can base reversal entry reach +8/+16/+24 before stop?")
print("  Reading the tables:")
print("  - PF$ < 1.00: structural net loss after costs. Hypothesis dead for this TP/SL pair.")
print("  - PF$ 1.00-1.10: marginal, sensitive to cost assumptions. Not tradeable.")
print("  - PF$ >= 1.15 AND IS/OOS gap small AND DD% reasonable: real signal.")
print("  - Compare standard vs cost-adjusted (section 7) — if cost-adjusted collapses PF,")
print("    your stop is too tight relative to costs and the system is not robust.")
print()

# best combo
exec_ranked = [r for r in all_results if r["sizing"] == "standard"]
exec_ranked.sort(key=lambda r: (-r["oos"]["pf_dol"], -r["oos"]["expectancy_dol"]))
print("  Top 3 (TP, SL) by OOS PF$:")
for r in exec_ranked[:3]:
    sl_lbl = "DYN" if r["sl_kind"]=="DYNAMIC" else str(r["sl_kind"])
    o = r["oos"]
    print(f"    TP={r['tp_pts']:>3}  SL={sl_lbl:<8}  "
          f"OOS PF$={o['pf_dol']:.3f}  EV$={o['expectancy_dol']:+.2f}  "
          f"net$={o['net_profit_dol']:+,.1f}  WR={o['wr']:.2f}%  DD%={o['max_dd_pct']:.2f}")


# ============================================================ CSV
def save(path, rows, lbl):
    if not rows: tprint(f"  [csv] {lbl}: empty"); return
    keys = []; seen = set()
    for r in rows:
        for k in r:
            if k not in seen: seen.add(k); keys.append(k)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore", restval="")
        w.writeheader()
        for r in rows: w.writerow(r)
    tprint(f"  [csv] {lbl}: {len(rows):,} rows -> {path}")

# main summary CSV — one row per (tp, sl, sizing, split)
summary_rows = []
for r in all_results:
    sl_lbl = "DYNAMIC" if r["sl_kind"]=="DYNAMIC" else r["sl_kind"]
    for split, m in (("full", r["full"]), ("train", r["train"]), ("oos", r["oos"])):
        row = {
            "tp_pts": r["tp_pts"], "sl_kind": sl_lbl, "sizing": r["sizing"], "split": split,
        }
        for k in ("trades","resolved","wins","losses","unresolved","wr",
                  "gross_profit_pts","gross_loss_pts","gross_profit_dol","gross_loss_dol",
                  "commission_total","slippage_total_dol","net_profit_dol",
                  "pf_pts","pf_dol","avg_win_pts","avg_loss_pts","avg_win_dol","avg_loss_dol",
                  "expectancy_pts","expectancy_dol","expectancy_R","avg_sl_pts",
                  "max_dd_dol","max_dd_pct","mls","final_balance","avg_hold","med_hold"):
            row[k] = m[k]
        for sd in ("long","short"):
            for k in ("trades","wr","pf","net","ev"):
                row[f"{sd}_{k}"] = m[sd][k]
        summary_rows.append(row)
save(os.path.join(OUT_DIR, "reversal_fp_summary.csv"), summary_rows, "summary")

# yearly CSV
yearly_rows = []
for r in all_results:
    sl_lbl = "DYNAMIC" if r["sl_kind"]=="DYNAMIC" else r["sl_kind"]
    for y, m in r["yearly"].items():
        row = {"tp_pts": r["tp_pts"], "sl_kind": sl_lbl, "sizing": r["sizing"], "year": y}
        for k in ("trades","resolved","wr","pf_dol","net_profit_dol","expectancy_dol",
                  "max_dd_pct","mls","avg_sl_pts","commission_total","slippage_total_dol"):
            row[k] = m[k]
        for sd in ("long","short"):
            for k in ("trades","wr","pf","net","ev"):
                row[f"{sd}_{k}"] = m[sd][k]
        yearly_rows.append(row)
save(os.path.join(OUT_DIR, "reversal_fp_yearly.csv"), yearly_rows, "yearly")

# trades CSV — standard sizing only, all combos
trade_rows = []
for (tp, sl, sizing), trades in trades_by_combo.items():
    if sizing != "standard": continue
    sl_lbl = "DYNAMIC" if sl=="DYNAMIC" else sl
    for tid, t in enumerate(trades, 1):
        ei = t["event"]["idx"]
        trade_rows.append({
            "trade_id": tid,
            "tp_pts": tp, "sl_kind": sl_lbl,
            "split": "train" if ei < TRAIN_END else "oos",
            "direction": "long" if t["event"]["side"]==1 else "short",
            "entry_time": str(timestamps[ei]),
            "entry_price": t["event"]["entry"],
            "rev_candle_low": t["event"]["rev_low"],
            "rev_candle_high": t["event"]["rev_high"],
            "tp_points": tp,
            "sl_points_used": t["sl_pts"],
            "exit_time": str(timestamps[t["exit_idx"]]),
            "outcome": t["outcome"],
            "hold_bars": t["hold_bars"],
            "gross_pts": t["pnl"]["gross_pts"],
            "net_pts": t["pnl"]["net_pts"],
            "lot_size": t["pnl"]["lot"],
            "commission_dol": t["pnl"]["commission"],
            "slippage_dol": t["pnl"]["slippage_dol"],
            "net_pnl_dol": t["pnl"]["net_dol"],
        })
save(os.path.join(OUT_DIR, "reversal_fp_trades.csv"), trade_rows, "trades")


# ============================================================ EQUITY PNG (top 3 OOS PF$)
try:
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
except Exception as e:
    plt = None; tprint(f"  matplotlib unavailable: {e}")

if plt:
    for rank, r in enumerate(exec_ranked[:3], 1):
        sl_lbl = "DYN" if r["sl_kind"]=="DYNAMIC" else r["sl_kind"]
        tag = f"TP{r['tp_pts']}_SL{sl_lbl}"
        sims = trades_by_combo[(r["tp_pts"], r["sl_kind"], "standard")]
        sims_resolved = [t for t in sims if t["outcome"] in ("win","loss")]
        if not sims_resolved: continue
        sims_resolved.sort(key=lambda t: t["event"]["idx"])
        dol = np.array([t["pnl"]["net_dol"] for t in sims_resolved])
        eq = STARTING_CAPITAL + np.cumsum(dol)
        peak = np.maximum.accumulate(eq); dd = peak - eq
        ents = [t["event"]["idx"] for t in sims_resolved]
        times = pd.to_datetime(timestamps[ents]) if HAS_TS else np.arange(len(ents))

        fig, ax = plt.subplots(2,1,figsize=(11,6),gridspec_kw={"height_ratios":[3,1]})
        ax[0].plot(times, eq, lw=1.2)
        ax[0].set_title(f"#{rank}  {tag}  "
                        f"OOS PF$={r['oos']['pf_dol']:.3f}  "
                        f"net$={r['oos']['net_profit_dol']:+,.1f}  "
                        f"WR={r['oos']['wr']:.2f}%")
        ax[0].set_ylabel("Equity $")
        if HAS_TS and TRAIN_END > 0:
            st = pd.Timestamp(timestamps[TRAIN_END-1])
            for axx in ax: axx.axvline(st, ls="--", c="gray", lw=1)
        ax[1].fill_between(times, dd, 0, color="crimson", alpha=0.4)
        ax[1].set_ylabel("Drawdown $")
        plt.tight_layout()
        path = os.path.join(OUT_DIR, f"equity_rank{rank}_{tag}.png")
        plt.savefig(path, dpi=110); plt.close()
        tprint(f"  [png] {path}")

tprint("DONE 🔬")