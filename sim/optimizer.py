import os, json, csv, time, math, gc, pickle
import numpy as np
import pandas as pd
from math import sqrt
from collections import defaultdict

# ============================================================ CONFIG
FILE = "data/NQ/8pt.json"          # change as needed
OUT_DIR = "out/super_ma_optimizer"
CKPT_FILE = os.path.join(OUT_DIR, "_results_chunks")
COL_TS, COL_OPEN, COL_HIGH, COL_LOW, COL_CLOSE = "timestamp","open","high","low","close"

STARTING_CAPITAL    = 1000.0
RISK_PER_TRADE      = 10.0
POINT_VALUE_PER_LOT = 1.0
SLIPPAGE_POINTS     = 0.3            # per execution
COMMISSION_RT       = 0.80           # per lot per trade
SLIPPAGE_TOTAL      = 2 * SLIPPAGE_POINTS

MA_TYPES   = ["SMA", "EMA", "HMA"]
MA_VALUES  = [9, 21, 34, 55, 84, 144, 200]
ENTRY_MODES = ["one_break", "two_confirm"]
STOP_MODELS = ["fixed_6", "fixed_8", "fixed_10", "candle"]
RR_VALUES   = [2, 3, 4, 5, 6]

MIN_SL_DISTANCE = 0.1
KEEP_TOP_K_TRADES = 10               # keep arrays for top 10 by net profit for equity PNG
CHUNK_SIZE = 200                     # flush results to disk every N candidates

os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(CKPT_FILE, exist_ok=True)
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
del df, data; gc.collect()
tprint(f"bars: {N_BARS:,}")


# ============================================================ INDICATORS
def sma_vec(arr, p):
    out = np.full(len(arr), np.nan, np.float32)
    if len(arr) < p: return out
    cs = np.cumsum(arr, dtype=np.float64)
    out[p-1:] = ((cs[p-1:] - np.concatenate(([0], cs[:-p]))) / p).astype(np.float32)
    return out

def ema_vec(arr, p):
    a = 2.0/(p+1.0); out = np.empty(len(arr), np.float32); out[0] = arr[0]
    for i in range(1, len(arr)): out[i] = a*arr[i] + (1-a)*out[i-1]
    return out

def wma_vec(arr, p):
    w = np.arange(1, p+1, dtype=np.float64); ws = w.sum()
    out = np.full(len(arr), np.nan, np.float32)
    if len(arr) < p: return out
    out[p-1:] = (np.convolve(arr, w[::-1], "valid") / ws).astype(np.float32)
    return out

def hma_vec(arr, p):
    half = p // 2; sp = int(sqrt(p))
    raw = 2.0 * wma_vec(arr, half).astype(np.float64) - wma_vec(arr, p).astype(np.float64)
    valid = ~np.isnan(raw)
    rc = np.where(valid, raw, 0.0)
    sm = wma_vec(rc, sp)
    inv = (~valid).astype(np.int32); cs = np.cumsum(inv)
    bad = np.zeros(len(arr), bool)
    for i in range(sp-1, len(arr)):
        s = cs[i] - (cs[i-sp] if i-sp >= 0 else 0)
        if s > 0: bad[i] = True
    bad[:sp-1] = True
    sm[bad] = np.nan
    return sm

tprint("precomputing all MAs (3 types × 7 periods = 21 arrays)…")
MA = {}
for t in MA_TYPES:
    for p in MA_VALUES:
        if   t == "SMA": MA[(t,p)] = sma_vec(cl, p)
        elif t == "EMA": MA[(t,p)] = ema_vec(cl, p)
        else:            MA[(t,p)] = hma_vec(cl, p)
tprint(f"  MA memory ≈ {21 * N_BARS * 4 / 1e6:.1f} MB")


# ============================================================ SIGNAL PRECOMPUTE
# For each (ma_type, ma_period, entry_mode), compute arrays:
#   long_entry_idx[]  long_signal_low[]  (low of the SIGNAL candle for candle stop)
#   short_entry_idx[] short_signal_high[]
# Signal candle = breakout bar for one_break, 2nd confirm bar for two_confirm.
# Entry price is always close of the entry bar.

tprint("precomputing entry signals for all (ma_type, period, mode)…")
SIGNALS = {}   # (ma_type, period, mode) -> dict(long_idx, long_sl, short_idx, short_sl)

for mt in MA_TYPES:
    for p in MA_VALUES:
        ma = MA[(mt, p)]
        valid = ~np.isnan(ma)
        # one_break
        above = (cl > ma) & valid
        below = (cl < ma) & valid
        prev_above = np.concatenate(([False], above[:-1]))
        prev_below = np.concatenate(([False], below[:-1]))
        long_break  = (~prev_above) & above
        short_break = (~prev_below) & below
        # need prev MA valid too
        prev_valid = np.concatenate(([False], valid[:-1]))
        long_break  &= prev_valid
        short_break &= prev_valid

        long_idx_1  = np.where(long_break)[0].astype(np.int32)
        short_idx_1 = np.where(short_break)[0].astype(np.int32)
        # signal candle = the break bar itself
        SIGNALS[(mt, p, "one_break")] = {
            "long_idx":   long_idx_1,
            "long_sig_low":  lo[long_idx_1],
            "short_idx":  short_idx_1,
            "short_sig_high": hi[short_idx_1],
        }

        # two_confirm: break at t, then close[t+1] above ma[t+1] for long (close[t+1] below for short)
        # confirm bar = t+1; that's the SIGNAL candle (and entry bar)
        # also require ma[t+1] valid
        valid_next = np.concatenate((valid[1:], [False]))   # valid at t+1
        ma_next = np.concatenate((ma[1:], [np.nan]))
        cl_next = np.concatenate((cl[1:], [np.nan]))
        lo_next = np.concatenate((lo[1:], [np.nan]))
        hi_next = np.concatenate((hi[1:], [np.nan]))

        long_confirm  = long_break  & valid_next & (cl_next > ma_next)
        short_confirm = short_break & valid_next & (cl_next < ma_next)

        # Entry bar is t+1
        entry_idx_long_2  = np.where(long_confirm)[0].astype(np.int32) + 1
        entry_idx_short_2 = np.where(short_confirm)[0].astype(np.int32) + 1
        # signal candle = entry bar (the 2nd confirmation candle)
        # filter idx within bounds
        m_l = entry_idx_long_2  < N_BARS
        m_s = entry_idx_short_2 < N_BARS
        entry_idx_long_2  = entry_idx_long_2[m_l]
        entry_idx_short_2 = entry_idx_short_2[m_s]

        SIGNALS[(mt, p, "two_confirm")] = {
            "long_idx":   entry_idx_long_2,
            "long_sig_low":  lo[entry_idx_long_2],
            "short_idx":  entry_idx_short_2,
            "short_sig_high": hi[entry_idx_short_2],
        }

# diagnostic
for mt in MA_TYPES:
    for p in MA_VALUES:
        for mode in ENTRY_MODES:
            s = SIGNALS[(mt, p, mode)]
            tprint(f"  {mt}{p:>3} {mode:<12} : L={len(s['long_idx']):>6,}  S={len(s['short_idx']):>6,}")


# ============================================================ FIRST-PASSAGE WITH FIXED SL + RR EXIT
# For fixed-SL trades, exit is FIRST of: TP hit, SL hit, end of data.
# Same-bar TP+SL → SL (loss) per spec.
# Returns parallel arrays: outcome(int8), exit_offset(int32)
# 1=win, 0=loss, -1=unresolved
def simulate_fixed_rr(entries, side, sl_pts, rr):
    """Use precomputed forward path on demand. Vectorized cmax."""
    if len(entries) == 0:
        return (np.zeros(0, np.int8), np.zeros(0, np.int32),
                np.zeros(0, np.float32), np.zeros(0, np.float32))
    tp_pts = sl_pts * rr
    outcome = np.full(len(entries), -1, np.int8)
    exit_off = np.zeros(len(entries), np.int32)
    sl_dist_out = np.full(len(entries), sl_pts, np.float32)
    tp_pts_out  = np.full(len(entries), tp_pts, np.float32)
    for i, e in enumerate(entries):
        entry = cl[e]
        if side == 1:
            tp_lvl = entry + tp_pts; sl_lvl = entry - sl_pts
        else:
            tp_lvl = entry - tp_pts; sl_lvl = entry + sl_pts
        # forward walk
        for j in range(e + 1, N_BARS):
            h, l = hi[j], lo[j]
            if side == 1:
                tp_hit = h >= tp_lvl
                sl_hit = l <= sl_lvl
            else:
                tp_hit = l <= tp_lvl
                sl_hit = h >= sl_lvl
            if tp_hit and sl_hit:
                outcome[i] = 0; exit_off[i] = j - e; break
            if sl_hit:
                outcome[i] = 0; exit_off[i] = j - e; break
            if tp_hit:
                outcome[i] = 1; exit_off[i] = j - e; break
        else:
            exit_off[i] = N_BARS - 1 - e
    return outcome, exit_off, sl_dist_out, tp_pts_out


def simulate_candle_rr(entries, sig_levels, side, rr):
    """Candle SL: long uses signal_low; short uses signal_high.
       Returns outcome, exit_off, sl_dist_per_trade, tp_pts_per_trade."""
    n = len(entries)
    outcome = np.full(n, -1, np.int8)
    exit_off = np.zeros(n, np.int32)
    sl_dist_arr = np.zeros(n, np.float32)
    tp_pts_arr  = np.zeros(n, np.float32)
    valid_mask = np.zeros(n, bool)
    for i, e in enumerate(entries):
        entry = cl[e]
        if side == 1:
            sl_dist = entry - sig_levels[i]
        else:
            sl_dist = sig_levels[i] - entry
        if sl_dist < MIN_SL_DISTANCE:
            continue  # skip degenerate
        valid_mask[i] = True
        sl_dist_arr[i] = sl_dist
        tp_pts = sl_dist * rr
        tp_pts_arr[i] = tp_pts
        if side == 1:
            tp_lvl = entry + tp_pts; sl_lvl = entry - sl_dist
        else:
            tp_lvl = entry - tp_pts; sl_lvl = entry + sl_dist
        for j in range(e + 1, N_BARS):
            h, l = hi[j], lo[j]
            if side == 1:
                tp_hit = h >= tp_lvl; sl_hit = l <= sl_lvl
            else:
                tp_hit = l <= tp_lvl; sl_hit = h >= sl_lvl
            if tp_hit and sl_hit:
                outcome[i] = 0; exit_off[i] = j - e; break
            if sl_hit:
                outcome[i] = 0; exit_off[i] = j - e; break
            if tp_hit:
                outcome[i] = 1; exit_off[i] = j - e; break
        else:
            exit_off[i] = N_BARS - 1 - e
    return outcome, exit_off, sl_dist_arr, tp_pts_arr, valid_mask


def simulate_fixed_ma_exit(entries, side, sl_pts, exit_ma_arr):
    """Fixed SL with MA close exit.
       Exit = SL hit (intrabar) OR close-side flip vs exit MA, whichever first."""
    n = len(entries)
    outcome = np.full(n, -1, np.int8)   # 1=win (any positive net), 0=loss, -1=unresolved
    exit_off = np.zeros(n, np.int32)
    sl_dist_arr = np.full(n, sl_pts, np.float32)
    exit_price_arr = np.zeros(n, np.float32)
    for i, e in enumerate(entries):
        entry = cl[e]
        if side == 1:
            sl_lvl = entry - sl_pts
        else:
            sl_lvl = entry + sl_pts
        for j in range(e + 1, N_BARS):
            h, l = hi[j], lo[j]
            # SL intrabar
            if side == 1 and l <= sl_lvl:
                outcome[i] = 0; exit_off[i] = j - e
                exit_price_arr[i] = sl_lvl; break
            if side == -1 and h >= sl_lvl:
                outcome[i] = 0; exit_off[i] = j - e
                exit_price_arr[i] = sl_lvl; break
            # MA close exit
            ema_j = exit_ma_arr[j]
            if math.isnan(ema_j): continue
            c_j = cl[j]
            if side == 1 and c_j < ema_j:
                gross = c_j - entry
                outcome[i] = 1 if gross > 0 else 0
                exit_off[i] = j - e; exit_price_arr[i] = c_j; break
            if side == -1 and c_j > ema_j:
                gross = entry - c_j
                outcome[i] = 1 if gross > 0 else 0
                exit_off[i] = j - e; exit_price_arr[i] = c_j; break
        else:
            exit_off[i] = N_BARS - 1 - e
            exit_price_arr[i] = cl[N_BARS - 1]
    return outcome, exit_off, sl_dist_arr, exit_price_arr


def simulate_candle_ma_exit(entries, sig_levels, side, exit_ma_arr):
    n = len(entries)
    outcome = np.full(n, -1, np.int8)
    exit_off = np.zeros(n, np.int32)
    sl_dist_arr = np.zeros(n, np.float32)
    exit_price_arr = np.zeros(n, np.float32)
    valid_mask = np.zeros(n, bool)
    for i, e in enumerate(entries):
        entry = cl[e]
        if side == 1:
            sl_dist = entry - sig_levels[i]
        else:
            sl_dist = sig_levels[i] - entry
        if sl_dist < MIN_SL_DISTANCE:
            continue
        valid_mask[i] = True
        sl_dist_arr[i] = sl_dist
        if side == 1:
            sl_lvl = entry - sl_dist
        else:
            sl_lvl = entry + sl_dist
        for j in range(e + 1, N_BARS):
            h, l = hi[j], lo[j]
            if side == 1 and l <= sl_lvl:
                outcome[i] = 0; exit_off[i] = j - e
                exit_price_arr[i] = sl_lvl; break
            if side == -1 and h >= sl_lvl:
                outcome[i] = 0; exit_off[i] = j - e
                exit_price_arr[i] = sl_lvl; break
            ema_j = exit_ma_arr[j]
            if math.isnan(ema_j): continue
            c_j = cl[j]
            if side == 1 and c_j < ema_j:
                gross = c_j - entry
                outcome[i] = 1 if gross > 0 else 0
                exit_off[i] = j - e; exit_price_arr[i] = c_j; break
            if side == -1 and c_j > ema_j:
                gross = entry - c_j
                outcome[i] = 1 if gross > 0 else 0
                exit_off[i] = j - e; exit_price_arr[i] = c_j; break
        else:
            exit_off[i] = N_BARS - 1 - e
            exit_price_arr[i] = cl[N_BARS - 1]
    return outcome, exit_off, sl_dist_arr, exit_price_arr, valid_mask


# ============================================================ POSITION FILTER (one active per setup)
def apply_one_active(entries_sorted, exit_off):
    """Keep trades that don't overlap. entries_sorted must be chronologically sorted."""
    n = len(entries_sorted)
    if n == 0: return np.zeros(0, bool)
    keep = np.zeros(n, bool)
    free_at = -1
    for i in range(n):
        if int(entries_sorted[i]) >= free_at:
            keep[i] = True
            free_at = int(entries_sorted[i]) + int(exit_off[i]) + 1
    return keep


# ============================================================ PNL + METRICS
def compute_pnl_and_metrics(entries, side_arr, outcome, exit_off, sl_dist,
                            tp_pts=None, exit_price=None, is_rr=True):
    """Returns metrics dict and per-trade dollar series for equity/DD reconstruction.
       For RR exits: tp_pts provides win-exit points. For MA exits: exit_price provides realized price."""
    n = len(entries)
    if n == 0: return _empty(0), None
    # gross points per trade
    gross_pts = np.zeros(n, np.float32)
    for i in range(n):
        o = outcome[i]
        if o == -1: continue
        if is_rr:
            if o == 1: gross_pts[i] = tp_pts[i]
            else:      gross_pts[i] = -sl_dist[i]
        else:
            entry = cl[entries[i]]
            if side_arr[i] == 1: gross_pts[i] = exit_price[i] - entry
            else:                gross_pts[i] = entry - exit_price[i]

    lot = (RISK_PER_TRADE / sl_dist).astype(np.float32)
    comm = (lot * COMMISSION_RT).astype(np.float32)
    slip_dol = (SLIPPAGE_TOTAL * lot * POINT_VALUE_PER_LOT).astype(np.float32)
    net_pts = gross_pts - SLIPPAGE_TOTAL
    # set unresolved net_pts/dol to 0
    res_mask = outcome != -1
    net_pts = np.where(res_mask, net_pts, 0.0).astype(np.float32)
    net_dol = np.where(res_mask, net_pts * lot * POINT_VALUE_PER_LOT - comm, 0.0).astype(np.float32)
    # commission only counted for resolved
    comm_eff = np.where(res_mask, comm, 0.0)
    slip_eff = np.where(res_mask, slip_dol, 0.0)

    nr = int(res_mask.sum())
    if nr == 0: return _empty(n), None

    # win = positive net_dol; loss = non-positive
    wins_mask = res_mask & (net_dol > 0)
    losses_mask = res_mask & (net_dol <= 0)
    nw, nl = int(wins_mask.sum()), int(losses_mask.sum())
    wr = nw / nr * 100
    gross_profit_pts = float(gross_pts[wins_mask].sum())
    gross_loss_pts   = float(-gross_pts[losses_mask].sum())
    gp_d = float(net_dol[wins_mask].sum())
    gl_d = float(-net_dol[losses_mask].sum())
    pf_d = (gp_d / gl_d) if gl_d > 0 else (float("inf") if gp_d > 0 else 0.0)
    net_profit = float(net_dol[res_mask].sum())
    ev_dol = net_profit / nr
    ev_R   = ev_dol / RISK_PER_TRADE
    avg_win_pts  = float(gross_pts[wins_mask].mean()) if nw else 0.0
    avg_loss_pts = float(gross_pts[losses_mask].mean()) if nl else 0.0
    avg_win_d    = (gp_d / nw) if nw else 0.0
    avg_loss_d   = -(gl_d / nl) if nl else 0.0
    comm_total = float(comm_eff.sum())
    slip_total = float(slip_eff.sum())

    # equity / DD
    cum = np.cumsum(net_dol[res_mask]) + STARTING_CAPITAL
    peak = np.maximum.accumulate(cum)
    dd = peak - cum
    max_dd_d = float(dd.max())
    dd_pct = np.where(peak > 0, dd / peak * 100, 0.0)
    max_dd_p = float(dd_pct.max())
    # max losing streak
    loss_seq = (outcome[res_mask] != 1) | (net_dol[res_mask] <= 0)
    # actually use net_dol sign for streak (consistent with WR classification)
    loss_seq = (net_dol[res_mask] <= 0)
    mls = 0; cnt = 0
    for v in loss_seq:
        if v: cnt += 1
        else: cnt = 0
        if cnt > mls: mls = cnt

    holds = exit_off[res_mask]
    avg_h = float(holds.mean()) if len(holds) else 0.0
    med_h = float(np.median(holds)) if len(holds) else 0.0

    # sharpe/sortino per trade dollar
    rets = net_dol[res_mask]
    if nr >= 2:
        m_ = float(rets.mean()); s_ = float(rets.std())
        downs = rets[rets < 0]
        ds = float(downs.std()) if len(downs) > 1 else (float(abs(downs[0])) if len(downs) == 1 else 0.0)
        sharpe = (m_/s_*sqrt(nr)) if s_ > 0 else 0.0
        sortino = (m_/ds*sqrt(nr)) if ds > 0 else 0.0
    else: sharpe = sortino = 0.0

    # long / short blocks
    def sblk(mask_side):
        mm = mask_side & res_mask
        nn = int(mm.sum())
        if nn == 0: return dict(trades=0, wr=0.0, pf=0.0, net=0.0, ev=0.0)
        wm = mm & wins_mask; lm = mm & losses_mask
        ww = int(wm.sum())
        gw_d = float(net_dol[wm].sum()); gl_d_ = float(-net_dol[lm].sum())
        return dict(trades=nn, wr=ww/nn*100,
                    pf=(gw_d/gl_d_) if gl_d_ > 0 else (float("inf") if gw_d > 0 else 0.0),
                    net=float(net_dol[mm].sum()),
                    ev=float(net_dol[mm].sum())/nn)

    long_blk  = sblk(side_arr == 1)
    short_blk = sblk(side_arr == -1)

    return {
        "trades": n, "resolved": nr, "wins": nw, "losses": nl, "unresolved": n - nr,
        "wr": wr,
        "gross_profit_pts": gross_profit_pts, "gross_loss_pts": gross_loss_pts,
        "gross_profit_dol": gp_d, "gross_loss_dol": gl_d,
        "commission_total": comm_total, "slippage_total": slip_total,
        "net_profit": net_profit,
        "pf_dol": pf_d,
        "expectancy_dol": ev_dol, "expectancy_R": ev_R,
        "avg_win_pts": avg_win_pts, "avg_loss_pts": avg_loss_pts,
        "avg_win_dol": avg_win_d, "avg_loss_dol": avg_loss_d,
        "max_dd_dol": max_dd_d, "max_dd_pct": max_dd_p, "mls": mls,
        "final_balance": float(cum[-1]),
        "avg_hold": avg_h, "med_hold": med_h,
        "sharpe": sharpe, "sortino": sortino,
        "long": long_blk, "short": short_blk,
    }, (entries, side_arr, outcome, net_dol, exit_off)


def _empty(n):
    z = dict(trades=0,wr=0.0,pf=0.0,net=0.0,ev=0.0)
    return {"trades":n,"resolved":0,"wins":0,"losses":0,"unresolved":n,
            "wr":0.0,"gross_profit_pts":0.0,"gross_loss_pts":0.0,
            "gross_profit_dol":0.0,"gross_loss_dol":0.0,
            "commission_total":0.0,"slippage_total":0.0,
            "net_profit":0.0,"pf_dol":0.0,"expectancy_dol":0.0,"expectancy_R":0.0,
            "avg_win_pts":0.0,"avg_loss_pts":0.0,"avg_win_dol":0.0,"avg_loss_dol":0.0,
            "max_dd_dol":0.0,"max_dd_pct":0.0,"mls":0,"final_balance":STARTING_CAPITAL,
            "avg_hold":0.0,"med_hold":0.0,"sharpe":0.0,"sortino":0.0,
            "long":z,"short":z}


# ============================================================ MERGE + ONE-ACTIVE PER SETUP
def evaluate_candidate(long_entries, long_outcome, long_exoff, long_sl, long_tp_or_xp,
                       short_entries, short_outcome, short_exoff, short_sl, short_tp_or_xp,
                       long_valid=None, short_valid=None, is_rr=True):
    """Combine long+short, sort chronologically, apply one-active-per-setup gating
       (separate per side since each side is its own setup), then compute metrics."""
    # apply candle validity if provided
    if long_valid is not None:
        long_entries = long_entries[long_valid]
        long_outcome = long_outcome[long_valid]
        long_exoff   = long_exoff[long_valid]
        long_sl      = long_sl[long_valid]
        long_tp_or_xp = long_tp_or_xp[long_valid]
    if short_valid is not None:
        short_entries = short_entries[short_valid]
        short_outcome = short_outcome[short_valid]
        short_exoff   = short_exoff[short_valid]
        short_sl      = short_sl[short_valid]
        short_tp_or_xp = short_tp_or_xp[short_valid]

    # one-active gating PER SIDE (each side = its own setup per spec)
    keep_l = apply_one_active(long_entries, long_exoff)
    keep_s = apply_one_active(short_entries, short_exoff)

    long_entries  = long_entries[keep_l]
    long_outcome  = long_outcome[keep_l]
    long_exoff    = long_exoff[keep_l]
    long_sl       = long_sl[keep_l]
    long_tp_or_xp = long_tp_or_xp[keep_l]
    short_entries = short_entries[keep_s]
    short_outcome = short_outcome[keep_s]
    short_exoff   = short_exoff[keep_s]
    short_sl      = short_sl[keep_s]
    short_tp_or_xp = short_tp_or_xp[keep_s]

    # merge
    entries = np.concatenate([long_entries, short_entries])
    sides   = np.concatenate([np.ones(len(long_entries), np.int8),
                              -np.ones(len(short_entries), np.int8)])
    outcome = np.concatenate([long_outcome, short_outcome])
    exit_off = np.concatenate([long_exoff, short_exoff])
    sl_dist  = np.concatenate([long_sl, short_sl])
    tp_or_xp = np.concatenate([long_tp_or_xp, short_tp_or_xp])

    # sort chronologically for equity/DD calc
    order = np.argsort(entries, kind="stable")
    entries = entries[order]; sides = sides[order]; outcome = outcome[order]
    exit_off = exit_off[order]; sl_dist = sl_dist[order]; tp_or_xp = tp_or_xp[order]

    if is_rr:
        metrics, arrays = compute_pnl_and_metrics(entries, sides, outcome, exit_off,
                                                  sl_dist, tp_pts=tp_or_xp, is_rr=True)
    else:
        metrics, arrays = compute_pnl_and_metrics(entries, sides, outcome, exit_off,
                                                  sl_dist, exit_price=tp_or_xp, is_rr=False)
    return metrics, arrays


# ============================================================ SWEEP
TP_SL_PAIRS_FIXED = [("fixed_6", 6.0), ("fixed_8", 8.0), ("fixed_10", 10.0)]

# Results stream: spill to disk every CHUNK_SIZE
class ResultStore:
    def __init__(self, ckpt_dir):
        self.ckpt_dir = ckpt_dir
        self.buf = []
        self.n_chunks = 0
        self.total = 0
    def add(self, row): self.buf.append(row); self.total += 1
    def flush(self):
        if not self.buf: return
        path = os.path.join(self.ckpt_dir, f"chunk_{self.n_chunks:04d}.pkl")
        with open(path, "wb") as f: pickle.dump(self.buf, f, protocol=4)
        self.n_chunks += 1
        self.buf = []
        gc.collect()
    def maybe_flush(self):
        if len(self.buf) >= CHUNK_SIZE: self.flush()
    def iter_all(self):
        for k in range(self.n_chunks):
            path = os.path.join(self.ckpt_dir, f"chunk_{k:04d}.pkl")
            with open(path, "rb") as f: rows = pickle.load(f)
            for r in rows: yield r
            del rows; gc.collect()

store = ResultStore(CKPT_FILE)

# Top-K equity arrays tracker (kept tiny — only top by net_profit)
class TopK:
    def __init__(self, k): self.k = k; self.items = []  # list of (net_profit, key, arrays)
    def add(self, net_profit, key, arrays):
        if len(self.items) < self.k:
            self.items.append((net_profit, key, arrays))
            self.items.sort(key=lambda x: x[0])
        elif net_profit > self.items[0][0]:
            self.items[0] = (net_profit, key, arrays)
            self.items.sort(key=lambda x: x[0])
topk = TopK(KEEP_TOP_K_TRADES)


# Build full candidate grid
def all_candidates():
    for mt in MA_TYPES:
        for p in MA_VALUES:
            for mode in ENTRY_MODES:
                for stop_lbl, stop_pts in TP_SL_PAIRS_FIXED:
                    # fixed RR exits
                    for rr in RR_VALUES:
                        yield dict(ma_type=mt, ma=p, mode=mode,
                                   stop_type=stop_lbl, stop_pts=stop_pts,
                                   exit_kind="fixed_RR", rr=rr, exit_ma=None)
                    # MA close exits
                    for exit_ma in MA_VALUES:
                        if exit_ma > p: continue
                        yield dict(ma_type=mt, ma=p, mode=mode,
                                   stop_type=stop_lbl, stop_pts=stop_pts,
                                   exit_kind="ma_close", rr=None, exit_ma=exit_ma)
                # candle stop variant
                for rr in RR_VALUES:
                    yield dict(ma_type=mt, ma=p, mode=mode,
                               stop_type="candle", stop_pts=None,
                               exit_kind="fixed_RR", rr=rr, exit_ma=None)
                for exit_ma in MA_VALUES:
                    if exit_ma > p: continue
                    yield dict(ma_type=mt, ma=p, mode=mode,
                               stop_type="candle", stop_pts=None,
                               exit_kind="ma_close", rr=None, exit_ma=exit_ma)

cand_list = list(all_candidates())
tprint(f"total candidates: {len(cand_list):,}")


# ============================================================ MAIN LOOP
tprint("running sweep…")
done = 0
last_progress = time.time()
sweep_start = time.time()

for cand in cand_list:
    done += 1
    sig = SIGNALS[(cand["ma_type"], cand["ma"], cand["mode"])]
    long_idx  = sig["long_idx"]
    short_idx = sig["short_idx"]
    long_sig_low  = sig["long_sig_low"]
    short_sig_high = sig["short_sig_high"]

    if len(long_idx) == 0 and len(short_idx) == 0:
        continue

    is_rr = cand["exit_kind"] == "fixed_RR"
    exit_ma_arr = MA[(cand["ma_type"], cand["exit_ma"])] if cand["exit_ma"] is not None else None

    if cand["stop_type"] != "candle":
        # fixed SL
        sl_pts = cand["stop_pts"]
        if is_rr:
            rr = cand["rr"]
            lo_out, lo_exoff, lo_sl, lo_tp = simulate_fixed_rr(long_idx, 1, sl_pts, rr)
            sh_out, sh_exoff, sh_sl, sh_tp = simulate_fixed_rr(short_idx, -1, sl_pts, rr)
            metrics, arrays = evaluate_candidate(
                long_idx, lo_out, lo_exoff, lo_sl, lo_tp,
                short_idx, sh_out, sh_exoff, sh_sl, sh_tp,
                is_rr=True)
        else:
            lo_out, lo_exoff, lo_sl, lo_xp = simulate_fixed_ma_exit(long_idx, 1, sl_pts, exit_ma_arr)
            sh_out, sh_exoff, sh_sl, sh_xp = simulate_fixed_ma_exit(short_idx, -1, sl_pts, exit_ma_arr)
            metrics, arrays = evaluate_candidate(
                long_idx, lo_out, lo_exoff, lo_sl, lo_xp,
                short_idx, sh_out, sh_exoff, sh_sl, sh_xp,
                is_rr=False)
    else:
        # candle SL
        if is_rr:
            rr = cand["rr"]
            lo_out, lo_exoff, lo_sl, lo_tp, lo_valid = simulate_candle_rr(
                long_idx, long_sig_low, 1, rr)
            sh_out, sh_exoff, sh_sl, sh_tp, sh_valid = simulate_candle_rr(
                short_idx, short_sig_high, -1, rr)
            metrics, arrays = evaluate_candidate(
                long_idx, lo_out, lo_exoff, lo_sl, lo_tp,
                short_idx, sh_out, sh_exoff, sh_sl, sh_tp,
                long_valid=lo_valid, short_valid=sh_valid, is_rr=True)
        else:
            lo_out, lo_exoff, lo_sl, lo_xp, lo_valid = simulate_candle_ma_exit(
                long_idx, long_sig_low, 1, exit_ma_arr)
            sh_out, sh_exoff, sh_sl, sh_xp, sh_valid = simulate_candle_ma_exit(
                short_idx, short_sig_high, -1, exit_ma_arr)
            metrics, arrays = evaluate_candidate(
                long_idx, lo_out, lo_exoff, lo_sl, lo_xp,
                short_idx, sh_out, sh_exoff, sh_sl, sh_xp,
                long_valid=lo_valid, short_valid=sh_valid, is_rr=False)

    # build flat row
    key = (f"{cand['ma_type']}{cand['ma']:>3}_{cand['mode']}_{cand['stop_type']}_"
           f"{'RR'+str(cand['rr']) if is_rr else 'MAexit'+str(cand['exit_ma'])}")
    row = {
        "key": key,
        "ma_type": cand["ma_type"], "entry_ma_value": cand["ma"],
        "entry_mode": cand["mode"], "stop_type": cand["stop_type"],
        "exit_type": cand["exit_kind"],
        "rr_value": cand["rr"] if cand["rr"] is not None else "",
        "exit_ma_value": cand["exit_ma"] if cand["exit_ma"] is not None else "",
    }
    for k in ("trades","resolved","wins","losses","unresolved","wr",
              "gross_profit_pts","gross_loss_pts","gross_profit_dol","gross_loss_dol",
              "commission_total","slippage_total","net_profit",
              "pf_dol","expectancy_dol","expectancy_R",
              "avg_win_pts","avg_loss_pts","avg_win_dol","avg_loss_dol",
              "max_dd_dol","max_dd_pct","mls","final_balance",
              "avg_hold","med_hold","sharpe","sortino"):
        row[k] = metrics[k]
    for sd in ("long","short"):
        for k in ("trades","wr","pf","net","ev"):
            row[f"{sd}_{k}"] = metrics[sd][k]
    store.add(row)
    store.maybe_flush()

    # track top-K by net profit (only keep arrays for true top performers)
    if metrics["net_profit"] > 0 and metrics["resolved"] >= 10:
        topk.add(metrics["net_profit"], key, arrays)

    # progress
    if time.time() - last_progress >= 3.0:
        last_progress = time.time()
        rate = done / (time.time() - sweep_start)
        eta = (len(cand_list) - done) / rate if rate > 0 else 0
        tprint(f"  sweep {done:,}/{len(cand_list):,} ({100*done/len(cand_list):5.1f}%) "
               f"rate={rate:.1f}/s ETA={eta:5.0f}s  results={store.total:,}")

store.flush()
tprint(f"SWEEP DONE  candidates: {store.total:,}  chunks: {store.n_chunks}  time: {time.time()-T0:.1f}s")


# ============================================================ LOAD ALL RESULTS BACK
tprint("loading results back from chunks for ranking…")
all_rows = list(store.iter_all())
tprint(f"  loaded {len(all_rows):,} rows")

# helper for NaN/inf
def finite(v): return v if (isinstance(v,(int,float)) and math.isfinite(v)) else 0.0

# ============================================================ RANKINGS
def rank_by(rows, sort_keys, top=50, min_trades=10):
    valid = [r for r in rows if r["resolved"] >= min_trades]
    valid.sort(key=lambda r: tuple(sort_keys(r)))
    return valid[:top]

def sect(t): print(); print("="*100); print(f" {t}"); print("="*100)
def kfmt(r): return r["key"][:46]

def print_top(label, rows, cols, n=50):
    sect(label)
    hdr = f"  {'#':>3}  {'key':<46} | " + "  ".join(f"{c:>11}" for c in cols)
    print(hdr); print("-"*len(hdr))
    for i, r in enumerate(rows[:n], 1):
        vals = []
        for c in cols:
            v = r.get(c, 0)
            if isinstance(v, float):
                vals.append(f"{v:>+10.3f}" if abs(v) < 100 else f"{v:>+10,.1f}")
            else:
                vals.append(f"{v:>11}")
        print(f"  {i:>3}  {kfmt(r):<46} | " + "  ".join(vals))

# Top 50 by net profit
top_net = rank_by(all_rows, lambda r: (-finite(r["net_profit"]),
                                       -finite(r["pf_dol"]),
                                       -finite(r["expectancy_R"]),
                                       finite(r["max_dd_pct"])))
print_top("TOP 50 BY NET PROFIT", top_net,
          ["resolved","wr","pf_dol","net_profit","expectancy_R",
           "max_dd_pct","mls","final_balance"])

# Top 50 by PF
top_pf = rank_by(all_rows, lambda r: (-finite(r["pf_dol"]),
                                      -finite(r["net_profit"]),
                                      finite(r["max_dd_pct"])))
print_top("TOP 50 BY PROFIT FACTOR", top_pf,
          ["resolved","wr","pf_dol","net_profit","expectancy_R","max_dd_pct"])

# Top 50 by expectancy in R
top_ev = rank_by(all_rows, lambda r: (-finite(r["expectancy_R"]),
                                      -finite(r["net_profit"]),
                                      finite(r["max_dd_pct"])))
print_top("TOP 50 BY EXPECTANCY (R)", top_ev,
          ["resolved","wr","pf_dol","net_profit","expectancy_R","max_dd_pct"])

# Top 50 by final balance
top_fb = rank_by(all_rows, lambda r: (-finite(r["final_balance"]),
                                      -finite(r["pf_dol"])))
print_top("TOP 50 BY FINAL BALANCE", top_fb,
          ["resolved","wr","pf_dol","net_profit","final_balance","max_dd_pct"])

# Top 50 lowest DD among profitable
profitable = [r for r in all_rows if r["net_profit"] > 0 and r["resolved"] >= 10]
profitable.sort(key=lambda r: (finite(r["max_dd_pct"]),
                                -finite(r["net_profit"])))
print_top("TOP 50 LOWEST DD% (PROFITABLE ONLY)", profitable,
          ["resolved","wr","pf_dol","net_profit","max_dd_pct","final_balance"])


# ============================================================ GROUPED SUMMARIES
def group_summary(rows, key_fn):
    groups = defaultdict(list)
    for r in rows: groups[key_fn(r)].append(r)
    out = []
    for g, items in groups.items():
        nets = np.array([r["net_profit"] for r in items])
        pfs  = np.array([r["pf_dol"] if math.isfinite(r["pf_dol"]) else 0 for r in items])
        evRs = np.array([r["expectancy_R"] for r in items])
        prof_pct = float((nets > 0).mean() * 100)
        out.append({
            "group": g, "n_configs": len(items),
            "avg_net": float(nets.mean()), "median_net": float(np.median(nets)),
            "avg_pf": float(pfs.mean()), "median_pf": float(np.median(pfs)),
            "avg_evR": float(evRs.mean()),
            "pct_profitable": prof_pct,
        })
    out.sort(key=lambda x: -x["avg_net"])
    return out

def print_group(label, rows):
    sect(label)
    print(f"  {'group':<30} {'n':>5}  {'avg_net':>10} {'med_net':>10} "
          f"{'avg_PF':>7} {'med_PF':>7} {'avg_EVR':>8} {'%prof':>7}")
    print("-"*100)
    for r in rows:
        print(f"  {str(r['group']):<30} {r['n_configs']:>5,}  "
              f"{r['avg_net']:>+10,.1f} {r['median_net']:>+10,.1f}  "
              f"{r['avg_pf']:>7.3f} {r['median_pf']:>7.3f}  "
              f"{r['avg_evR']:>+8.3f}  {r['pct_profitable']:>6.2f}%")

print_group("GROUPED BY MA TYPE",     group_summary(all_rows, lambda r: r["ma_type"]))
print_group("GROUPED BY ENTRY MA",    group_summary(all_rows, lambda r: f"MA{r['entry_ma_value']:>3}"))
print_group("GROUPED BY ENTRY MODE",  group_summary(all_rows, lambda r: r["entry_mode"]))
print_group("GROUPED BY STOP TYPE",   group_summary(all_rows, lambda r: r["stop_type"]))
print_group("GROUPED BY EXIT TYPE",   group_summary(all_rows, lambda r: r["exit_type"]))


# ============================================================ CSV OUTPUTS
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

save(os.path.join(OUT_DIR, "super_ma_full_results.csv"), all_rows, "full")
save(os.path.join(OUT_DIR, "super_ma_top_net.csv"),   top_net, "top_net")
save(os.path.join(OUT_DIR, "super_ma_top_pf.csv"),    top_pf, "top_pf")
save(os.path.join(OUT_DIR, "super_ma_top_evR.csv"),   top_ev, "top_evR")
save(os.path.join(OUT_DIR, "super_ma_top_fb.csv"),    top_fb, "top_fb")
save(os.path.join(OUT_DIR, "super_ma_top_lowest_dd.csv"), profitable[:50], "lowest_dd")

# group summaries
for lbl, fn in [("ma_type", lambda r: r["ma_type"]),
                ("entry_ma", lambda r: f"MA{r['entry_ma_value']}"),
                ("entry_mode", lambda r: r["entry_mode"]),
                ("stop_type", lambda r: r["stop_type"]),
                ("exit_type", lambda r: r["exit_type"])]:
    save(os.path.join(OUT_DIR, f"super_ma_group_by_{lbl}.csv"),
         group_summary(all_rows, fn), f"group_{lbl}")


# ============================================================ TOP 10 EQUITY + TRADE CSV + PNG
sect("TOP 10 EQUITY CURVES + TRADE CSVS")
topk_sorted = sorted(topk.items, key=lambda x: -x[0])[:KEEP_TOP_K_TRADES]
try:
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    has_plt = True
except Exception as e:
    plt = None; has_plt = False
    tprint(f"  matplotlib unavailable: {e}")

for rank, (net_profit, key, arrays) in enumerate(topk_sorted, 1):
    entries, sides, outcome, net_dol, exit_off = arrays
    res_mask = outcome != -1
    if not res_mask.any(): continue
    eq_arr = STARTING_CAPITAL + np.cumsum(net_dol[res_mask])
    peak_a = np.maximum.accumulate(eq_arr); dd_a = peak_a - eq_arr
    times = pd.to_datetime(timestamps[entries[res_mask]]) if HAS_TS else np.arange(int(res_mask.sum()))

    # trade CSV
    trade_rows = []
    cum_eq = STARTING_CAPITAL; peak_eq = cum_eq
    for tid, idx in enumerate(np.where(res_mask)[0], 1):
        ei = int(entries[idx]); sv = int(sides[idx]); outc = int(outcome[idx])
        cum_eq += float(net_dol[idx])
        if cum_eq > peak_eq: peak_eq = cum_eq
        dd_pct_t = (peak_eq - cum_eq) / peak_eq * 100 if peak_eq > 0 else 0
        trade_rows.append({
            "trade_id": tid, "strategy_key": key,
            "direction": "long" if sv == 1 else "short",
            "entry_time": str(timestamps[ei]),
            "entry_price": float(cl[ei]),
            "outcome": "win" if outc == 1 else "loss" if outc == 0 else "unresolved",
            "hold_bars": int(exit_off[idx]),
            "net_pnl_dol": float(net_dol[idx]),
            "equity_after_trade": cum_eq,
            "drawdown_pct_after_trade": dd_pct_t,
        })
    save(os.path.join(OUT_DIR, f"trades_top{rank}_{key[:40]}.csv"),
         trade_rows, f"trades_top{rank}")

    # PNG
    if has_plt:
        fig, ax = plt.subplots(2,1,figsize=(11,6),gridspec_kw={"height_ratios":[3,1]})
        ax[0].plot(times, eq_arr, lw=1.2)
        ax[0].set_title(f"#{rank}  {key}\nfinal=${eq_arr[-1]:,.0f}  trades={len(eq_arr):,}  net=${net_profit:+,.1f}")
        ax[0].set_ylabel("Equity $")
        ax[1].fill_between(times, dd_a, 0, color="crimson", alpha=0.4)
        ax[1].set_ylabel("DD $")
        plt.tight_layout()
        plt.savefig(os.path.join(OUT_DIR, f"equity_top{rank}_{key[:40]}.png"), dpi=110); plt.close()
        tprint(f"  [png] top{rank}: equity saved  key={key}")


# ============================================================ YEARLY (top 10)
sect("YEARLY BREAKDOWN — TOP 10 BY NET PROFIT")
if HAS_TS:
    for rank, (net_profit, key, arrays) in enumerate(topk_sorted, 1):
        entries, sides, outcome, net_dol, exit_off = arrays
        res_mask = outcome != -1
        if not res_mask.any(): continue
        years = pd.to_datetime(timestamps[entries[res_mask]]).year
        nd = net_dol[res_mask]
        by_y = defaultdict(lambda: {"n":0,"wins":0,"net":0.0})
        for y, d in zip(years, nd):
            g = by_y[int(y)]
            g["n"] += 1
            if d > 0: g["wins"] += 1
            g["net"] += float(d)
        print(f"\n  #{rank}  {key}")
        print(f"  {'year':>5} {'trades':>7} {'WR%':>6} {'net$':>10}")
        print("  "+"-"*40)
        for y in sorted(by_y):
            g = by_y[y]
            print(f"  {y:>5} {g['n']:>7,} {100*g['wins']/g['n']:>5.2f}% {g['net']:>+10,.1f}")
else:
    print("  (no timestamps available)")

tprint("DONE 🚀")