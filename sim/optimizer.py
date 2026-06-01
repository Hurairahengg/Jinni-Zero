import os, json, csv, time, math, gc, pickle, itertools
import numpy as np
import pandas as pd
from math import sqrt
from collections import defaultdict

# ============================================================ CONFIG
FILE = "data/NQ/8pt.json"
OUT_DIR = "out/full_horizon_optimizer"
CKPT_DIR = os.path.join(OUT_DIR, "_chunks")
COL_TS, COL_OPEN, COL_HIGH, COL_LOW, COL_CLOSE = "timestamp","open","high","low","close"
COL_VOL = "volume"   # optional, enables VWMA + VWAP if present

STARTING_CAPITAL    = 1000.0
RISK_PER_TRADE      = 10.0
POINT_VALUE_PER_LOT = 1.0
SLIPPAGE_POINTS     = 0.3
COMMISSION_RT       = 0.80
SLIPPAGE_TOTAL      = 2 * SLIPPAGE_POINTS
MIN_SL_DISTANCE     = 0.1

# ---------- FAMILY TOGGLES (turn off what you don't want this run) ----------
ENABLE_MA_BREAK            = True
ENABLE_MA_TWO_CONFIRM      = True
ENABLE_MA_SLOPE            = True
ENABLE_MA_STACK            = True
ENABLE_MA_PULLBACK         = True
ENABLE_DIST_FROM_MA        = True
ENABLE_BOLLINGER           = True
ENABLE_DONCHIAN            = True
ENABLE_RSI_TREND           = True
ENABLE_RSI_MEANREV         = True
ENABLE_MACD                = True
ENABLE_ADX                 = True

ENABLE_MEAN_REVERSION_FLIP = True    # for each candidate, also test flipped direction
ENABLE_OPPOSITE_EXIT       = True
ENABLE_TRAILING_ATR        = True
ENABLE_TRAILING_CANDLE     = True
ENABLE_TIME_EXIT           = True
ENABLE_MA_EXIT             = True
ENABLE_FIXED_RR_EXIT       = True

ENABLE_BREAKEVEN_TM        = True
ENABLE_TRAIL_AFTER_R       = True

ENABLE_MA_TREND_FILTER     = True
ENABLE_ADX_FILTER          = True
ENABLE_ATR_PCT_FILTER      = False   # creates 4x explosion — leave off by default

# ---------- SAMPLED PARAMETER SETS (reduce/expand to control candidate count) ----------
MA_TYPES_FULL      = ["SMA","EMA","HMA","WMA","RMA","DEMA","TEMA","KAMA","ALMA"]
MA_TYPES_DEFAULT   = ["EMA","HMA","SMA"]                   # sample subset
MA_LENGTHS_FULL    = [5,8,9,13,21,34,55,84,100,144,200,233,300]
MA_LENGTHS_DEFAULT = [9,21,55,89,144,200]                  # representative

CONFIRM_MODES      = ["none","close_+1pt","close_+2pt"]    # plus inherent "two_confirm" entry mode
SLOPE_LOOKBACKS    = [3, 5]

DIST_PTS_THRESHOLDS = [16, 24, 32]
DIST_Z_THRESHOLDS   = [1.5, 2.0, 2.5]

BB_LENGTHS         = [20, 55]
BB_STDS            = [2.0, 2.5]
BB_MODES           = ["breakout","meanrev"]

DONCHIAN_LENGTHS   = [10, 20, 55]

RSI_LENGTHS        = [14]
RSI_OVERSOLD       = [25, 30]
RSI_OVERBOUGHT     = [70, 75]

MACD_CFGS          = [(12,26,9),(8,21,5)]

ADX_LENGTHS        = [14]
ADX_THRESHOLDS     = [20, 25]

STACK_FAST         = [9, 21]
STACK_MID          = [55, 84]
STACK_SLOW         = [144, 200]

FIXED_STOPS        = [6, 8, 10, 16]
SWING_LOOKBACKS    = [3, 5]
ATR_STOPS_LEN      = [14]
ATR_STOPS_MULT     = [1.5, 2.0, 3.0]

RR_VALUES          = [1.5, 2, 3, 4, 6]
TIME_EXITS         = [5, 13, 21]
TRAIL_ATR_MULT     = [2.0, 3.0]
TRAIL_CANDLE_N     = [2, 5]

MA_EXIT_LENGTHS    = [9, 21, 55]    # subset; constrained ≤ entry MA at runtime

TM_BREAKEVEN_AT_R  = [None, 1.0, 2.0]   # None = no BE
TM_TRAIL_AFTER_R   = [None, 1.0]

TREND_FILTER_MAS   = [None, 89, 200]
ADX_FILTER_THRESH  = [None, 20]
ATR_PCT_BUCKETS    = [None]             # set [None,(0,25),(25,75),(75,100)] when ATR filter on

# ---------- PERFORMANCE ----------
CANDIDATE_HARD_CAP = 30000               # safety cap, abort cleanly if exceeded
CHUNK_SIZE         = 1000
KEEP_TOP_K         = 10
PROGRESS_EVERY_S   = 3.0
FP_MAX_FORWARD     = 500                 # forward walk cap for first-passage

os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(CKPT_DIR, exist_ok=True)

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
HAS_VOL = COL_VOL in df.columns
if HAS_TS:
    df[ts_col] = pd.to_datetime(df[ts_col], errors="coerce")
    timestamps = df[ts_col].values
else:
    timestamps = np.arange(len(df))

opn = df[COL_OPEN].astype(np.float64).values
hi  = df[COL_HIGH].astype(np.float64).values
lo  = df[COL_LOW].astype(np.float64).values
cl  = df[COL_CLOSE].astype(np.float64).values
vol = df[COL_VOL].astype(np.float64).values if HAS_VOL else None
N_BARS = len(df)
del df, data; gc.collect()
tprint(f"bars: {N_BARS:,}   HAS_TS={HAS_TS}   HAS_VOL={HAS_VOL}")


# ============================================================ INDICATORS
def sma(arr, p):
    out = np.full(len(arr), np.nan, np.float32)
    if len(arr) < p: return out
    cs = np.cumsum(arr, dtype=np.float64)
    out[p-1:] = ((cs[p-1:] - np.concatenate(([0], cs[:-p]))) / p).astype(np.float32)
    return out
def ema(arr, p):
    a = 2.0/(p+1.0); out = np.empty(len(arr), np.float32); out[0]=arr[0]
    for i in range(1,len(arr)): out[i] = a*arr[i] + (1-a)*out[i-1]
    return out
def wma(arr, p):
    w = np.arange(1,p+1,dtype=np.float64); ws=w.sum()
    out = np.full(len(arr), np.nan, np.float32)
    if len(arr) < p: return out
    out[p-1:] = (np.convolve(arr, w[::-1], "valid") / ws).astype(np.float32)
    return out
def hma(arr, p):
    half=p//2; sp=int(sqrt(p))
    raw = 2.0*wma(arr,half).astype(np.float64) - wma(arr,p).astype(np.float64)
    valid = ~np.isnan(raw); rc = np.where(valid, raw, 0.0)
    sm = wma(rc, sp)
    inv = (~valid).astype(np.int32); cs = np.cumsum(inv)
    bad = np.zeros(len(arr), bool)
    for i in range(sp-1, len(arr)):
        s = cs[i] - (cs[i-sp] if i-sp>=0 else 0)
        if s > 0: bad[i] = True
    bad[:sp-1] = True; sm[bad] = np.nan
    return sm
def rma(arr, p):  # Wilder
    a = 1.0/p; out = np.empty(len(arr), np.float32); out[0]=arr[0]
    for i in range(1,len(arr)): out[i] = a*arr[i] + (1-a)*out[i-1]
    return out
def dema(arr, p):
    e1 = ema(arr, p); e2 = ema(e1.astype(np.float64), p)
    return (2.0*e1 - e2).astype(np.float32)
def tema(arr, p):
    e1 = ema(arr, p); e2 = ema(e1.astype(np.float64), p); e3 = ema(e2.astype(np.float64), p)
    return (3.0*e1 - 3.0*e2 + e3).astype(np.float32)
def kama(arr, p, fast=2, slow=30):
    n = len(arr); out = np.full(n, np.nan, np.float32)
    if n <= p: return out
    fast_a = 2.0/(fast+1); slow_a = 2.0/(slow+1)
    change = np.abs(arr[p:] - arr[:-p])
    volatility = np.array([np.sum(np.abs(np.diff(arr[i-p:i+1]))) for i in range(p, n)])
    er = np.where(volatility > 0, change/volatility, 0.0)
    sc = (er*(fast_a - slow_a) + slow_a)**2
    out[p] = arr[p]
    for i in range(p+1, n):
        out[i] = out[i-1] + sc[i-p]*(arr[i] - out[i-1])
    return out
def alma(arr, p, offset=0.85, sigma=6):
    n = len(arr); out = np.full(n, np.nan, np.float32)
    if n < p: return out
    m = offset*(p-1); s = p/sigma
    w = np.array([np.exp(-((i-m)**2)/(2*s*s)) for i in range(p)])
    w = w / w.sum()
    for i in range(p-1, n):
        out[i] = float(np.dot(arr[i-p+1:i+1], w))
    return out
def vwma(arr, vol_arr, p):
    if vol_arr is None: return None
    out = np.full(len(arr), np.nan, np.float32)
    pv = arr * vol_arr
    csv_ = np.cumsum(vol_arr, dtype=np.float64)
    cspv = np.cumsum(pv, dtype=np.float64)
    for i in range(p-1, len(arr)):
        v = csv_[i] - (csv_[i-p] if i-p>=0 else 0)
        if v > 0: out[i] = (cspv[i] - (cspv[i-p] if i-p>=0 else 0)) / v
    return out

def calc_ma(kind, arr, p):
    if kind == "SMA": return sma(arr, p)
    if kind == "EMA": return ema(arr, p)
    if kind == "HMA": return hma(arr, p)
    if kind == "WMA": return wma(arr, p)
    if kind == "RMA": return rma(arr, p)
    if kind == "DEMA": return dema(arr, p)
    if kind == "TEMA": return tema(arr, p)
    if kind == "KAMA": return kama(arr, p)
    if kind == "ALMA": return alma(arr, p)
    if kind == "VWMA": return vwma(arr, vol, p)
    raise ValueError(kind)

# ATR / RSI / MACD / ADX / Bollinger / Donchian / Z-score
def atr(p):
    tr = np.maximum(hi - lo, np.maximum(np.abs(hi - np.concatenate(([cl[0]], cl[:-1]))),
                                         np.abs(lo - np.concatenate(([cl[0]], cl[:-1])))))
    return rma(tr, p)

def rsi(p):
    delta = np.diff(cl, prepend=cl[0])
    up = np.where(delta > 0, delta, 0.0); dn = np.where(delta < 0, -delta, 0.0)
    avg_up = rma(up, p); avg_dn = rma(dn, p)
    rs = np.where(avg_dn > 0, avg_up/avg_dn, 0.0)
    return (100 - 100/(1+rs)).astype(np.float32)

def macd_hist(fast, slow, signal):
    ef = ema(cl, fast); es = ema(cl, slow)
    line = (ef - es)
    sig = ema(line.astype(np.float64), signal)
    return (line - sig).astype(np.float32)

def adx_di(p):
    up_move = hi - np.concatenate(([hi[0]], hi[:-1]))
    dn_move = np.concatenate(([lo[0]], lo[:-1])) - lo
    plus_dm  = np.where((up_move > dn_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((dn_move > up_move) & (dn_move > 0), dn_move, 0.0)
    tr = np.maximum(hi - lo, np.maximum(np.abs(hi - np.concatenate(([cl[0]], cl[:-1]))),
                                         np.abs(lo - np.concatenate(([cl[0]], cl[:-1])))))
    atr_ = rma(tr, p)
    plus_di  = 100 * rma(plus_dm, p)  / np.where(atr_>0, atr_, np.nan)
    minus_di = 100 * rma(minus_dm, p) / np.where(atr_>0, atr_, np.nan)
    dx = 100 * np.abs(plus_di - minus_di) / np.where((plus_di+minus_di)>0, plus_di+minus_di, np.nan)
    adx = rma(np.nan_to_num(dx), p)
    return adx.astype(np.float32), plus_di.astype(np.float32), minus_di.astype(np.float32)

def bollinger(p, k):
    m = sma(cl, p)
    sd = np.full(len(cl), np.nan, np.float32)
    for i in range(p-1, len(cl)):
        sd[i] = float(np.std(cl[i-p+1:i+1]))
    upper = m + k*sd; lower = m - k*sd
    return m, upper, lower

def donchian(p):
    upper = np.full(len(cl), np.nan, np.float32)
    lower = np.full(len(cl), np.nan, np.float32)
    for i in range(p-1, len(cl)):
        upper[i] = hi[i-p+1:i+1].max()
        lower[i] = lo[i-p+1:i+1].min()
    return upper, lower


# precompute MAs needed
tprint("precomputing MAs…")
MA_SET = set(MA_TYPES_DEFAULT)
MA_CACHE = {}
for mt in MA_SET:
    if mt == "VWMA" and not HAS_VOL: continue
    for p in set(MA_LENGTHS_DEFAULT + MA_EXIT_LENGTHS + STACK_FAST + STACK_MID + STACK_SLOW
                 + [x for x in TREND_FILTER_MAS if x is not None]):
        MA_CACHE[(mt, p)] = calc_ma(mt, cl, p)

# precompute ATR, RSI, MACD, ADX, BB, Donchian
tprint("precomputing oscillators…")
ATR_CACHE = {p: atr(p) for p in ATR_STOPS_LEN}
RSI_CACHE = {p: rsi(p) for p in RSI_LENGTHS}
MACD_CACHE = {cfg: macd_hist(*cfg) for cfg in MACD_CFGS}
ADX_CACHE = {p: adx_di(p) for p in ADX_LENGTHS}
BB_CACHE = {(p,k): bollinger(p,k) for p in BB_LENGTHS for k in BB_STDS}
DC_CACHE = {p: donchian(p) for p in DONCHIAN_LENGTHS}
tprint("indicators ready")


# ============================================================ ENTRY SIGNAL GENERATORS
# Each returns (long_entry_idx, short_entry_idx, long_sig_low, short_sig_high)
def sig_ma_break(mt, p):
    ma = MA_CACHE[(mt,p)]; valid = ~np.isnan(ma)
    pa = np.concatenate(([False], (cl > ma)[:-1]))
    pb = np.concatenate(([False], (cl < ma)[:-1]))
    long_mask  = (~pa) & (cl > ma) & valid & np.concatenate(([False], valid[:-1]))
    short_mask = (~pb) & (cl < ma) & valid & np.concatenate(([False], valid[:-1]))
    li = np.where(long_mask)[0].astype(np.int32)
    si = np.where(short_mask)[0].astype(np.int32)
    return li, si, lo[li], hi[si]

def sig_ma_two_confirm(mt, p):
    ma = MA_CACHE[(mt,p)]; valid = ~np.isnan(ma)
    pa = np.concatenate(([False], (cl > ma)[:-1]))
    pb = np.concatenate(([False], (cl < ma)[:-1]))
    long_break  = (~pa) & (cl > ma) & valid
    short_break = (~pb) & (cl < ma) & valid
    next_valid = np.concatenate((valid[1:], [False]))
    ma_next = np.concatenate((ma[1:], [np.nan]))
    cl_next = np.concatenate((cl[1:], [np.nan]))
    long_conf  = long_break  & next_valid & (cl_next > ma_next)
    short_conf = short_break & next_valid & (cl_next < ma_next)
    li = (np.where(long_conf)[0]  + 1).astype(np.int32)
    si = (np.where(short_conf)[0] + 1).astype(np.int32)
    li = li[li < N_BARS]; si = si[si < N_BARS]
    return li, si, lo[li], hi[si]

def sig_ma_slope(mt, p, k):
    ma = MA_CACHE[(mt,p)]; valid = ~np.isnan(ma)
    slope = np.full(N_BARS, np.nan, np.float32)
    slope[k:] = ma[k:] - ma[:-k]
    above = (cl > ma) & valid & (slope > 0)
    below = (cl < ma) & valid & (slope < 0)
    # only when transitioning into that state (avoid every-bar spam)
    pa = np.concatenate(([False], above[:-1])); pb = np.concatenate(([False], below[:-1]))
    li = np.where(above & ~pa)[0].astype(np.int32)
    si = np.where(below & ~pb)[0].astype(np.int32)
    return li, si, lo[li], hi[si]

def sig_ma_stack(mt, fp, mp, sp):
    f_, m_, s_ = MA_CACHE[(mt,fp)], MA_CACHE[(mt,mp)], MA_CACHE[(mt,sp)]
    valid = ~(np.isnan(f_)|np.isnan(m_)|np.isnan(s_))
    bull = valid & (cl > f_) & (f_ > m_) & (m_ > s_)
    bear = valid & (cl < f_) & (f_ < m_) & (m_ < s_)
    pa = np.concatenate(([False], bull[:-1])); pb = np.concatenate(([False], bear[:-1]))
    li = np.where(bull & ~pa)[0].astype(np.int32)
    si = np.where(bear & ~pb)[0].astype(np.int32)
    return li, si, lo[li], hi[si]

def sig_ma_pullback(mt, p, slope_k=5):
    ma = MA_CACHE[(mt,p)]; valid = ~np.isnan(ma)
    slope = np.full(N_BARS, np.nan, np.float32)
    slope[slope_k:] = ma[slope_k:] - ma[:-slope_k]
    # long: trend up + touched MA + closed back above
    touched_below_long = lo <= ma
    closed_above = cl > ma
    long_mask  = valid & (slope > 0) & touched_below_long & closed_above
    touched_above_short = hi >= ma
    closed_below = cl < ma
    short_mask = valid & (slope < 0) & touched_above_short & closed_below
    pa = np.concatenate(([False], long_mask[:-1])); pb = np.concatenate(([False], short_mask[:-1]))
    li = np.where(long_mask & ~pa)[0].astype(np.int32)
    si = np.where(short_mask & ~pb)[0].astype(np.int32)
    return li, si, lo[li], hi[si]

def sig_dist_from_ma(mt, p, thresh_pts):
    ma = MA_CACHE[(mt,p)]; valid = ~np.isnan(ma)
    dist = cl - ma
    # mean-reversion: long when far below, short when far above
    li = np.where(valid & (dist <= -thresh_pts))[0].astype(np.int32)
    si = np.where(valid & (dist >=  thresh_pts))[0].astype(np.int32)
    return li, si, lo[li], hi[si]

def sig_bollinger(p, k, mode):
    m, u, l = BB_CACHE[(p,k)]
    valid = ~(np.isnan(u) | np.isnan(l))
    if mode == "breakout":
        long_mask  = valid & (cl > u)
        short_mask = valid & (cl < l)
    else:  # meanrev
        long_mask  = valid & (cl < l)
        short_mask = valid & (cl > u)
    pa = np.concatenate(([False], long_mask[:-1])); pb = np.concatenate(([False], short_mask[:-1]))
    li = np.where(long_mask & ~pa)[0].astype(np.int32)
    si = np.where(short_mask & ~pb)[0].astype(np.int32)
    return li, si, lo[li], hi[si]

def sig_donchian(p):
    u, l = DC_CACHE[p]
    pa_u = np.concatenate(([np.nan], u[:-1]))
    pa_l = np.concatenate(([np.nan], l[:-1]))
    long_mask  = ~np.isnan(pa_u) & (cl > pa_u)
    short_mask = ~np.isnan(pa_l) & (cl < pa_l)
    pl = np.concatenate(([False], long_mask[:-1])); ps = np.concatenate(([False], short_mask[:-1]))
    li = np.where(long_mask & ~pl)[0].astype(np.int32)
    si = np.where(short_mask & ~ps)[0].astype(np.int32)
    return li, si, lo[li], hi[si]

def sig_rsi_trend(p):
    r = RSI_CACHE[p]
    r_prev = np.concatenate(([np.nan], r[:-1]))
    long_mask  = (r > 50) & (r > r_prev) & ~np.isnan(r)
    short_mask = (r < 50) & (r < r_prev) & ~np.isnan(r)
    pa = np.concatenate(([False], long_mask[:-1])); pb = np.concatenate(([False], short_mask[:-1]))
    li = np.where(long_mask & ~pa)[0].astype(np.int32)
    si = np.where(short_mask & ~pb)[0].astype(np.int32)
    return li, si, lo[li], hi[si]

def sig_rsi_meanrev(p, os_, ob):
    r = RSI_CACHE[p]
    li = np.where(r <= os_)[0].astype(np.int32)
    si = np.where(r >= ob)[0].astype(np.int32)
    return li, si, lo[li], hi[si]

def sig_macd(cfg):
    h = MACD_CACHE[cfg]
    h_prev = np.concatenate(([np.nan], h[:-1]))
    long_mask  = (h > 0) & (h > h_prev) & ~np.isnan(h)
    short_mask = (h < 0) & (h < h_prev) & ~np.isnan(h)
    pa = np.concatenate(([False], long_mask[:-1])); pb = np.concatenate(([False], short_mask[:-1]))
    li = np.where(long_mask & ~pa)[0].astype(np.int32)
    si = np.where(short_mask & ~pb)[0].astype(np.int32)
    return li, si, lo[li], hi[si]

def sig_adx(p, thresh):
    adx_, pdi, mdi = ADX_CACHE[p]
    valid = ~np.isnan(adx_)
    long_mask  = valid & (pdi > mdi) & (adx_ >= thresh)
    short_mask = valid & (mdi > pdi) & (adx_ >= thresh)
    pa = np.concatenate(([False], long_mask[:-1])); pb = np.concatenate(([False], short_mask[:-1]))
    li = np.where(long_mask & ~pa)[0].astype(np.int32)
    si = np.where(short_mask & ~pb)[0].astype(np.int32)
    return li, si, lo[li], hi[si]


# ============================================================ FILTERS
def filter_ma_trend(entries, side, trend_p):
    if trend_p is None: return entries
    ma = MA_CACHE[("EMA", trend_p)]
    slope = np.full(N_BARS, np.nan, np.float32)
    slope[5:] = ma[5:] - ma[:-5]
    if side == 1:  mask = slope[entries] > 0
    else:          mask = slope[entries] < 0
    return entries[mask]

def filter_adx(entries, thresh):
    if thresh is None: return entries
    adx_, _, _ = ADX_CACHE[14]
    return entries[adx_[entries] >= thresh]


# ============================================================ STOP CALCULATORS
def stop_for(entries, side, kind, value=None):
    """Returns sl_dist (>0) per trade. Returns None for trades skipped (sl<=0)."""
    n = len(entries)
    if n == 0: return np.zeros(0, np.float32), np.zeros(0, bool)
    sd = np.zeros(n, np.float32)
    valid = np.ones(n, bool)
    e_close = cl[entries]
    if kind == "fixed":
        sd[:] = float(value)
    elif kind == "candle":
        if side == 1: sd[:] = e_close - lo[entries]
        else:         sd[:] = hi[entries] - e_close
    elif kind == "prev_candle":
        prev_idx = np.maximum(entries - 1, 0)
        if side == 1: sd[:] = e_close - lo[prev_idx]
        else:         sd[:] = hi[prev_idx] - e_close
    elif kind == "swing":
        n_lb = int(value)
        for i, e in enumerate(entries):
            lo_window = lo[max(0, e-n_lb+1):e+1]
            hi_window = hi[max(0, e-n_lb+1):e+1]
            if side == 1: sd[i] = e_close[i] - lo_window.min()
            else:         sd[i] = hi_window.max() - e_close[i]
    elif kind == "atr":
        atr_len, mult = value
        a = ATR_CACHE[atr_len]
        sd[:] = a[entries] * mult
    elif kind == "ma":
        mt, p = value
        ma_arr = MA_CACHE[(mt, p)]
        if side == 1: sd[:] = e_close - ma_arr[entries]
        else:         sd[:] = ma_arr[entries] - e_close
    valid = sd >= MIN_SL_DISTANCE
    return sd, valid


# ============================================================ TRADE SIMULATION
def simulate_trade(e_idx, side, sl_dist, exit_cfg):
    """Walk forward. Returns (outcome 1/0/-1, exit_off, exit_price).
       exit_cfg = dict with kind: 'fixed_rr'|'ma_close'|'opposite_signal'|'atr_trail'|'candle_trail'|'time' + params."""
    entry = cl[e_idx]
    if side == 1:
        sl_lvl = entry - sl_dist
    else:
        sl_lvl = entry + sl_dist
    end = min(e_idx + FP_MAX_FORWARD, N_BARS - 1)

    kind = exit_cfg["kind"]
    # TP target for fixed_rr
    if kind == "fixed_rr":
        rr = exit_cfg["rr"]
        tp_pts = sl_dist * rr
        tp_lvl = entry + tp_pts if side == 1 else entry - tp_pts
    elif kind == "ma_close":
        exit_ma = exit_cfg["ma_arr"]
    elif kind == "atr_trail":
        a = exit_cfg["atr_arr"]; mult = exit_cfg["mult"]
        peak_close = entry; trail = sl_lvl
    elif kind == "candle_trail":
        n_lb = exit_cfg["n_lb"]
        trail = sl_lvl
    elif kind == "time":
        max_hold = exit_cfg["bars"]
    # else: opposite_signal not implemented here (would need cross-strategy state) → fall back to ma_close fallback

    # TM
    be_at_R = exit_cfg.get("be_at_R")
    trail_after_R = exit_cfg.get("trail_after_R")
    trail_active = False

    for j in range(e_idx + 1, end + 1):
        h, l, c = hi[j], lo[j], cl[j]
        bars_held = j - e_idx

        # Breakeven move
        if be_at_R is not None:
            if side == 1:
                if (h - entry) >= be_at_R * sl_dist:
                    sl_lvl = max(sl_lvl, entry)
            else:
                if (entry - l) >= be_at_R * sl_dist:
                    sl_lvl = min(sl_lvl, entry)

        # Trail activation
        if trail_after_R is not None and not trail_active:
            if side == 1 and (h - entry) >= trail_after_R * sl_dist:
                trail_active = True
            elif side == -1 and (entry - l) >= trail_after_R * sl_dist:
                trail_active = True

        # Trailing logic for atr_trail / candle_trail when active (or always for those kinds)
        if kind == "atr_trail":
            if (trail_after_R is None) or trail_active:
                if side == 1:
                    if c > peak_close: peak_close = c
                    new_trail = peak_close - a[j] * mult
                    if new_trail > sl_lvl: sl_lvl = new_trail
                else:
                    if c < peak_close: peak_close = c
                    new_trail = peak_close + a[j] * mult
                    if new_trail < sl_lvl: sl_lvl = new_trail
        elif kind == "candle_trail":
            if (trail_after_R is None) or trail_active:
                lo_window = lo[max(0, j-n_lb+1):j+1]
                hi_window = hi[max(0, j-n_lb+1):j+1]
                if side == 1:
                    new_trail = float(lo_window.min())
                    if new_trail > sl_lvl: sl_lvl = new_trail
                else:
                    new_trail = float(hi_window.max())
                    if new_trail < sl_lvl: sl_lvl = new_trail

        # SL intrabar (conservative)
        if side == 1:
            if l <= sl_lvl:
                gross = sl_lvl - entry
                return (0 if gross <= 0 else 1), j - e_idx, sl_lvl
        else:
            if h >= sl_lvl:
                gross = entry - sl_lvl
                return (0 if gross <= 0 else 1), j - e_idx, sl_lvl

        # TP / exit conditions
        if kind == "fixed_rr":
            if side == 1 and h >= tp_lvl: return 1, j - e_idx, tp_lvl
            if side == -1 and l <= tp_lvl: return 1, j - e_idx, tp_lvl
        elif kind == "ma_close":
            mv = exit_ma[j]
            if not np.isnan(mv):
                if side == 1 and c < mv: return (1 if c > entry else 0), j - e_idx, c
                if side == -1 and c > mv: return (1 if c < entry else 0), j - e_idx, c
        elif kind == "time" and bars_held >= max_hold:
            return (1 if ((side == 1 and c > entry) or (side == -1 and c < entry)) else 0), bars_held, c

    return -1, end - e_idx, cl[end]


# ============================================================ METRICS (vectorized)
def safe_div(a, b):
    if b > 0: return a/b
    if a > 0: return float("inf")
    return 0.0

def compute_metrics(entries, sides, outcomes, net_pts, net_dol, sl_dist, hold_bars,
                    commission_arr, slippage_arr):
    n = len(entries)
    if n == 0: return _empty(0)
    res = outcomes != -1; nr = int(res.sum())
    if nr == 0: return _empty(n)
    wins_mask = res & (net_dol > 0)
    losses_mask = res & (net_dol <= 0)
    nw, nl = int(wins_mask.sum()), int(losses_mask.sum())
    wr = nw/nr*100
    gross_pts_pos = float(net_pts[wins_mask].sum())
    gross_pts_neg = float(-net_pts[losses_mask].sum())
    gp_d = float(net_dol[wins_mask].sum()); gl_d = float(-net_dol[losses_mask].sum())
    pf_d = safe_div(gp_d, gl_d)
    net_p = float(net_dol[res].sum())
    ev_d = net_p / nr
    ev_R = ev_d / RISK_PER_TRADE
    avg_w_pts = float(net_pts[wins_mask].mean()) if nw else 0.0
    avg_l_pts = float(net_pts[losses_mask].mean()) if nl else 0.0
    avg_w_dol = gp_d/nw if nw else 0.0
    avg_l_dol = -gl_d/nl if nl else 0.0
    wl_ratio = avg_w_dol / abs(avg_l_dol) if avg_l_dol != 0 else 0.0
    comm_t = float(commission_arr[res].sum()); slip_t = float(slippage_arr[res].sum())
    # equity/DD
    cum = np.cumsum(net_dol[res]) + STARTING_CAPITAL
    peak = np.maximum.accumulate(cum); dd = peak - cum
    max_dd_d = float(dd.max())
    max_dd_p = float(np.where(peak>0, dd/peak*100, 0.0).max())
    # streaks
    loss_seq = (net_dol[res] <= 0).astype(np.int8)
    win_seq  = (net_dol[res] >  0).astype(np.int8)
    def max_run(seq):
        m=0; c=0
        for v in seq:
            if v: c += 1
            else: c = 0
            if c > m: m = c
        return m
    mls = max_run(loss_seq); mws = max_run(win_seq)
    holds = hold_bars[res]
    avg_h = float(holds.mean()) if len(holds) else 0.0
    med_h = float(np.median(holds)) if len(holds) else 0.0
    rets = net_dol[res]
    if nr >= 2:
        m_ = float(rets.mean()); s_ = float(rets.std())
        downs = rets[rets < 0]
        ds = float(downs.std()) if len(downs) > 1 else (float(abs(downs[0])) if len(downs)==1 else 0.0)
        sharpe = (m_/s_*sqrt(nr)) if s_>0 else 0.0
        sortino = (m_/ds*sqrt(nr)) if ds>0 else 0.0
    else: sharpe = sortino = 0.0

    def sblk(mask_side):
        mm = mask_side & res
        nn = int(mm.sum())
        if nn == 0: return dict(trades=0,wr=0.0,pf=0.0,net=0.0,ev=0.0)
        wm = mm & wins_mask; lm = mm & losses_mask
        return dict(trades=nn, wr=int(wm.sum())/nn*100,
                    pf=safe_div(float(net_dol[wm].sum()), float(-net_dol[lm].sum())),
                    net=float(net_dol[mm].sum()),
                    ev=float(net_dol[mm].sum())/nn)
    return {
        "trades":n,"resolved":nr,"wins":nw,"losses":nl,"unresolved":n-nr,
        "wr":wr,"gross_profit_pts":gross_pts_pos,"gross_loss_pts":gross_pts_neg,
        "gross_profit_dol":gp_d,"gross_loss_dol":gl_d,
        "commission_total":comm_t,"slippage_total":slip_t,
        "net_profit":net_p,"pf_dol":pf_d,"expectancy_dol":ev_d,"expectancy_R":ev_R,
        "avg_win_pts":avg_w_pts,"avg_loss_pts":avg_l_pts,
        "avg_win_dol":avg_w_dol,"avg_loss_dol":avg_l_dol,"wl_ratio":wl_ratio,
        "max_dd_dol":max_dd_d,"max_dd_pct":max_dd_p,"mls":mls,"mws":mws,
        "avg_hold":avg_h,"med_hold":med_h,
        "final_balance":float(cum[-1]),"sharpe":sharpe,"sortino":sortino,
        "long":sblk(sides==1),"short":sblk(sides==-1),
    }

def _empty(n):
    z=dict(trades=0,wr=0.0,pf=0.0,net=0.0,ev=0.0)
    return {"trades":n,"resolved":0,"wins":0,"losses":0,"unresolved":n,
            "wr":0.0,"gross_profit_pts":0.0,"gross_loss_pts":0.0,
            "gross_profit_dol":0.0,"gross_loss_dol":0.0,
            "commission_total":0.0,"slippage_total":0.0,
            "net_profit":0.0,"pf_dol":0.0,"expectancy_dol":0.0,"expectancy_R":0.0,
            "avg_win_pts":0.0,"avg_loss_pts":0.0,
            "avg_win_dol":0.0,"avg_loss_dol":0.0,"wl_ratio":0.0,
            "max_dd_dol":0.0,"max_dd_pct":0.0,"mls":0,"mws":0,
            "avg_hold":0.0,"med_hold":0.0,
            "final_balance":STARTING_CAPITAL,"sharpe":0.0,"sortino":0.0,
            "long":z,"short":z}


# ============================================================ ONE-ACTIVE GATING (per side)
def one_active(entries, exit_offs):
    n = len(entries)
    keep = np.zeros(n, bool); free_at = -1
    for i in range(n):
        ei = int(entries[i]); xi = int(exit_offs[i])
        if ei >= free_at:
            keep[i] = True; free_at = ei + xi + 1
    return keep


# ============================================================ EVALUATE ONE CANDIDATE
def evaluate(long_idx, short_idx, sig_low_long, sig_high_short,
             stop_kind, stop_value, exit_cfg, filt):
    """Returns metrics dict + (arrays for top-K storage)."""
    # apply filters
    if filt.get("trend_ma") is not None:
        long_idx  = filter_ma_trend(long_idx,  1, filt["trend_ma"])
        short_idx = filter_ma_trend(short_idx,-1, filt["trend_ma"])
    if filt.get("adx_thresh") is not None:
        long_idx  = filter_adx(long_idx,  filt["adx_thresh"])
        short_idx = filter_adx(short_idx, filt["adx_thresh"])

    # remap sig levels if filters dropped some
    sig_low_long  = lo[long_idx]
    sig_high_short = hi[short_idx]

    # stops
    sd_l, val_l = stop_for(long_idx,  1, stop_kind, stop_value)
    sd_s, val_s = stop_for(short_idx, -1, stop_kind, stop_value)
    long_idx = long_idx[val_l]; sd_l = sd_l[val_l]
    short_idx = short_idx[val_s]; sd_s = sd_s[val_s]

    if len(long_idx) == 0 and len(short_idx) == 0:
        return _empty(0), None

    # simulate each trade
    def sim_side(entries, sd_arr, side):
        n = len(entries)
        out = np.zeros(n, np.int8); off = np.zeros(n, np.int32)
        xp = np.zeros(n, np.float32)
        for i in range(n):
            o, of, xprice = simulate_trade(int(entries[i]), side, float(sd_arr[i]), exit_cfg)
            out[i] = o; off[i] = of; xp[i] = xprice
        return out, off, xp

    out_l, off_l, xp_l = sim_side(long_idx,  sd_l,  1)
    out_s, off_s, xp_s = sim_side(short_idx, sd_s, -1)

    # one-active per side
    k_l = one_active(long_idx, off_l); k_s = one_active(short_idx, off_s)
    long_idx=long_idx[k_l]; sd_l=sd_l[k_l]; out_l=out_l[k_l]; off_l=off_l[k_l]; xp_l=xp_l[k_l]
    short_idx=short_idx[k_s]; sd_s=sd_s[k_s]; out_s=out_s[k_s]; off_s=off_s[k_s]; xp_s=xp_s[k_s]

    # merge + chronological
    entries = np.concatenate([long_idx, short_idx])
    sides   = np.concatenate([np.ones(len(long_idx), np.int8), -np.ones(len(short_idx), np.int8)])
    sd_all  = np.concatenate([sd_l, sd_s])
    out_all = np.concatenate([out_l, out_s])
    off_all = np.concatenate([off_l, off_s])
    xp_all  = np.concatenate([xp_l, xp_s])
    order = np.argsort(entries, kind="stable")
    entries=entries[order]; sides=sides[order]; sd_all=sd_all[order]
    out_all=out_all[order]; off_all=off_all[order]; xp_all=xp_all[order]

    # PnL
    e_close = cl[entries]
    gross_pts = np.where(sides==1, xp_all - e_close, e_close - xp_all).astype(np.float32)
    res = out_all != -1
    net_pts = np.where(res, gross_pts - SLIPPAGE_TOTAL, 0.0).astype(np.float32)
    lot = (RISK_PER_TRADE / sd_all).astype(np.float32)
    comm = (lot * COMMISSION_RT).astype(np.float32)
    slip_dol = (SLIPPAGE_TOTAL * lot).astype(np.float32)
    net_dol = np.where(res, net_pts*lot*POINT_VALUE_PER_LOT - comm, 0.0).astype(np.float32)
    comm_eff = np.where(res, comm, 0.0); slip_eff = np.where(res, slip_dol, 0.0)

    metrics = compute_metrics(entries, sides, out_all, net_pts, net_dol, sd_all, off_all,
                              comm_eff, slip_eff)
    arrays = (entries, sides, out_all, net_dol, off_all)
    return metrics, arrays


# ============================================================ CANDIDATE GENERATOR
def gen_candidates():
    """Yields dict per candidate. Sampled from each family."""
    # Entry signal source families
    entry_sources = []

    if ENABLE_MA_BREAK:
        for mt in MA_TYPES_DEFAULT:
            for p in MA_LENGTHS_DEFAULT:
                entry_sources.append(("ma_break", mt, p))
    if ENABLE_MA_TWO_CONFIRM:
        for mt in MA_TYPES_DEFAULT:
            for p in MA_LENGTHS_DEFAULT:
                entry_sources.append(("ma_two_confirm", mt, p))
    if ENABLE_MA_SLOPE:
        for mt in MA_TYPES_DEFAULT:
            for p in MA_LENGTHS_DEFAULT:
                for k in SLOPE_LOOKBACKS:
                    entry_sources.append(("ma_slope", mt, p, k))
    if ENABLE_MA_STACK:
        for mt in MA_TYPES_DEFAULT:
            for fp in STACK_FAST:
                for mp in STACK_MID:
                    for sp_ in STACK_SLOW:
                        if fp < mp < sp_:
                            entry_sources.append(("ma_stack", mt, fp, mp, sp_))
    if ENABLE_MA_PULLBACK:
        for mt in MA_TYPES_DEFAULT:
            for p in MA_LENGTHS_DEFAULT:
                entry_sources.append(("ma_pullback", mt, p))
    if ENABLE_DIST_FROM_MA:
        for mt in MA_TYPES_DEFAULT:
            for p in [21, 55]:
                for t in DIST_PTS_THRESHOLDS:
                    entry_sources.append(("dist_ma", mt, p, t))
    if ENABLE_BOLLINGER:
        for p in BB_LENGTHS:
            for k in BB_STDS:
                for mode in BB_MODES:
                    entry_sources.append(("bollinger", p, k, mode))
    if ENABLE_DONCHIAN:
        for p in DONCHIAN_LENGTHS:
            entry_sources.append(("donchian", p))
    if ENABLE_RSI_TREND:
        for p in RSI_LENGTHS:
            entry_sources.append(("rsi_trend", p))
    if ENABLE_RSI_MEANREV:
        for p in RSI_LENGTHS:
            for os_ in RSI_OVERSOLD:
                for ob in RSI_OVERBOUGHT:
                    entry_sources.append(("rsi_meanrev", p, os_, ob))
    if ENABLE_MACD:
        for cfg in MACD_CFGS:
            entry_sources.append(("macd", cfg))
    if ENABLE_ADX:
        for p in ADX_LENGTHS:
            for t in ADX_THRESHOLDS:
                entry_sources.append(("adx", p, t))

    # stop variants
    stop_variants = []
    for v in FIXED_STOPS: stop_variants.append(("fixed", v))
    stop_variants.append(("candle", None))
    stop_variants.append(("prev_candle", None))
    for n_ in SWING_LOOKBACKS: stop_variants.append(("swing", n_))
    for al in ATR_STOPS_LEN:
        for mu in ATR_STOPS_MULT:
            stop_variants.append(("atr", (al, mu)))

    # exit variants (with TM nested)
    exit_variants = []
    if ENABLE_FIXED_RR_EXIT:
        for rr in RR_VALUES:
            for be in TM_BREAKEVEN_AT_R:
                for tr in TM_TRAIL_AFTER_R:
                    exit_variants.append({"kind":"fixed_rr","rr":rr,"be_at_R":be,"trail_after_R":tr})
    if ENABLE_MA_EXIT:
        for ema_len in MA_EXIT_LENGTHS:
            for mt in MA_TYPES_DEFAULT:
                exit_variants.append({"kind":"ma_close","ma_arr":MA_CACHE[(mt, ema_len)],
                                      "label":f"MA_{mt}{ema_len}", "be_at_R":None,"trail_after_R":None})
    if ENABLE_TRAILING_ATR:
        for al in ATR_STOPS_LEN:
            for m in TRAIL_ATR_MULT:
                exit_variants.append({"kind":"atr_trail","atr_arr":ATR_CACHE[al],
                                      "mult":m,"label":f"ATRtrail{al}x{m}",
                                      "be_at_R":None,"trail_after_R":None})
    if ENABLE_TRAILING_CANDLE:
        for n_ in TRAIL_CANDLE_N:
            exit_variants.append({"kind":"candle_trail","n_lb":n_,
                                  "label":f"candleTrail{n_}",
                                  "be_at_R":None,"trail_after_R":None})
    if ENABLE_TIME_EXIT:
        for b in TIME_EXITS:
            exit_variants.append({"kind":"time","bars":b,
                                  "label":f"time{b}",
                                  "be_at_R":None,"trail_after_R":None})

    # filter combos
    filter_sets = []
    for tm in TREND_FILTER_MAS if ENABLE_MA_TREND_FILTER else [None]:
        for at in ADX_FILTER_THRESH if ENABLE_ADX_FILTER else [None]:
            filter_sets.append({"trend_ma":tm,"adx_thresh":at})

    # mean-rev flip flag
    direction_modes = ["trend"]
    if ENABLE_MEAN_REVERSION_FLIP: direction_modes.append("meanrev")

    # full cartesian
    for es in entry_sources:
        for sv in stop_variants:
            for ev in exit_variants:
                for fs in filter_sets:
                    for dm in direction_modes:
                        yield dict(entry=es, stop=sv, exit=ev, filt=fs, direction=dm)

# estimate candidate count
def estimate_count():
    return sum(1 for _ in gen_candidates())

est = estimate_count()
tprint(f"estimated candidate count: {est:,}  (hard cap: {CANDIDATE_HARD_CAP:,})")
if est > CANDIDATE_HARD_CAP:
    tprint(f"  !! exceeds cap. Trim CONFIG (toggle families off or reduce parameter lists).")
    # we'll still run but with safety break


# ============================================================ RESULT STORE
class ResultStore:
    def __init__(self):
        self.buf = []; self.n_chunks = 0; self.total = 0
    def add(self, row):
        self.buf.append(row); self.total += 1
        if len(self.buf) >= CHUNK_SIZE: self.flush()
    def flush(self):
        if not self.buf: return
        path = os.path.join(CKPT_DIR, f"chunk_{self.n_chunks:05d}.pkl")
        with open(path, "wb") as f: pickle.dump(self.buf, f, protocol=4)
        self.n_chunks += 1; self.buf = []; gc.collect()
    def iter_all(self):
        for k in range(self.n_chunks):
            path = os.path.join(CKPT_DIR, f"chunk_{k:05d}.pkl")
            with open(path, "rb") as f: rows = pickle.load(f)
            for r in rows: yield r
            del rows; gc.collect()

class TopK:
    def __init__(self, k): self.k=k; self.items=[]
    def add(self, score, key, arrays):
        if len(self.items) < self.k:
            self.items.append((score, key, arrays)); self.items.sort(key=lambda x:x[0])
        elif score > self.items[0][0]:
            self.items[0] = (score, key, arrays); self.items.sort(key=lambda x:x[0])

store = ResultStore()
topk_net = TopK(KEEP_TOP_K)
topk_pf  = TopK(KEEP_TOP_K)


# ============================================================ SIGNAL CACHE (per entry source)
SIG_CACHE = {}
def get_signals(es):
    if es in SIG_CACHE: return SIG_CACHE[es]
    kind = es[0]
    if   kind == "ma_break":         r = sig_ma_break(es[1], es[2])
    elif kind == "ma_two_confirm":   r = sig_ma_two_confirm(es[1], es[2])
    elif kind == "ma_slope":         r = sig_ma_slope(es[1], es[2], es[3])
    elif kind == "ma_stack":         r = sig_ma_stack(es[1], es[2], es[3], es[4])
    elif kind == "ma_pullback":      r = sig_ma_pullback(es[1], es[2])
    elif kind == "dist_ma":          r = sig_dist_from_ma(es[1], es[2], es[3])
    elif kind == "bollinger":        r = sig_bollinger(es[1], es[2], es[3])
    elif kind == "donchian":         r = sig_donchian(es[1])
    elif kind == "rsi_trend":        r = sig_rsi_trend(es[1])
    elif kind == "rsi_meanrev":      r = sig_rsi_meanrev(es[1], es[2], es[3])
    elif kind == "macd":             r = sig_macd(es[1])
    elif kind == "adx":              r = sig_adx(es[1], es[2])
    else: raise ValueError(kind)
    SIG_CACHE[es] = r
    return r

def es_label(es): return "_".join(str(x) for x in es)

def exit_label(ev): return ev.get("label", f"{ev['kind']}_RR{ev.get('rr')}_BE{ev.get('be_at_R')}_TR{ev.get('trail_after_R')}")


# ============================================================ SWEEP
tprint("running sweep…")
done = 0; sweep_start = time.time(); last_p = time.time()
aborted = False

for cand in gen_candidates():
    if done >= CANDIDATE_HARD_CAP:
        tprint(f"!! hit candidate hard cap ({CANDIDATE_HARD_CAP:,}). Stopping early.")
        aborted = True; break
    done += 1
    li, si, _, _ = get_signals(cand["entry"])
    # direction mode: meanrev = flip
    if cand["direction"] == "meanrev":
        li, si = si, li
    if len(li) + len(si) < 5:
        continue
    metrics, arrays = evaluate(li, si, lo[li] if len(li) else None, hi[si] if len(si) else None,
                               cand["stop"][0], cand["stop"][1], cand["exit"], cand["filt"])

    key = (f"{es_label(cand['entry'])}|stop_{cand['stop'][0]}_{cand['stop'][1]}|"
           f"exit_{exit_label(cand['exit'])}|filt_t{cand['filt']['trend_ma']}_a{cand['filt']['adx_thresh']}|"
           f"dir_{cand['direction']}")

    row = {
        "config_id": done, "key": key,
        "entry_kind": cand["entry"][0], "entry_params": "_".join(str(x) for x in cand["entry"][1:]),
        "stop_kind": cand["stop"][0], "stop_value": str(cand["stop"][1]),
        "exit_kind": cand["exit"]["kind"], "exit_label": exit_label(cand["exit"]),
        "be_at_R": cand["exit"].get("be_at_R"), "trail_after_R": cand["exit"].get("trail_after_R"),
        "trend_filter": cand["filt"]["trend_ma"], "adx_filter": cand["filt"]["adx_thresh"],
        "direction_mode": cand["direction"],
    }
    for k in ("trades","resolved","wins","losses","unresolved","wr",
              "gross_profit_pts","gross_loss_pts","gross_profit_dol","gross_loss_dol",
              "commission_total","slippage_total","net_profit",
              "pf_dol","expectancy_dol","expectancy_R",
              "avg_win_pts","avg_loss_pts","avg_win_dol","avg_loss_dol","wl_ratio",
              "max_dd_dol","max_dd_pct","mls","mws","avg_hold","med_hold",
              "final_balance","sharpe","sortino"):
        row[k] = metrics[k]
    for sd in ("long","short"):
        for k in ("trades","wr","pf","net","ev"):
            row[f"{sd}_{k}"] = metrics[sd][k]

    # safety flags
    flags = []
    if metrics["resolved"] < 30: flags.append("low_trades")
    if metrics["max_dd_dol"] > max(metrics["net_profit"], 1e-9): flags.append("dd_exceeds_profit")
    if metrics["mls"] > 15: flags.append("long_losing_streak")
    if metrics["long"]["net"] > 0 and metrics["short"]["net"] < -metrics["long"]["net"]*0.5:
        flags.append("side_asymmetry")
    if metrics["commission_total"] + metrics["slippage_total"] > metrics["gross_profit_dol"]:
        flags.append("costs_exceed_gross_profit")
    if metrics["final_balance"] <= 0: flags.append("blown_account")
    row["flags"] = ",".join(flags)
    store.add(row)

    if metrics["net_profit"] > 0 and metrics["resolved"] >= 10:
        topk_net.add(metrics["net_profit"], key, arrays)
        if math.isfinite(metrics["pf_dol"]):
            topk_pf.add(metrics["pf_dol"], key, arrays)

    if time.time() - last_p >= PROGRESS_EVERY_S:
        last_p = time.time()
        rate = done / (time.time()-sweep_start)
        eta = (est - done) / rate if rate > 0 else 0
        tprint(f"  sweep {done:,}/{est:,} ({100*done/max(est,1):5.1f}%)  "
               f"rate={rate:.1f}/s ETA={eta:5.0f}s  results={store.total:,}  chunks={store.n_chunks}")

store.flush()
tprint(f"SWEEP DONE  candidates: {store.total:,}  chunks: {store.n_chunks}  time: {time.time()-T0:.1f}s")


# ============================================================ LOAD ALL + RANK
tprint("loading results for ranking…")
all_rows = list(store.iter_all())
tprint(f"  loaded {len(all_rows):,} rows")

def finite(v):
    return v if isinstance(v,(int,float)) and math.isfinite(v) else 0.0

def rank(rows, key_fn, top=100, min_trades=10):
    valid = [r for r in rows if r["resolved"] >= min_trades]
    valid.sort(key=key_fn)
    return valid[:top]

def sect(t): print(); print("="*100); print(f" {t}"); print("="*100)
def k(r): return r["key"][:60]

def print_top(label, rows, cols, n=20):
    sect(label)
    print(f"  {'#':>3}  {'key':<60} | " + "  ".join(f"{c:>11}" for c in cols))
    print("-"*100)
    for i, r in enumerate(rows[:n], 1):
        vals=[]
        for c in cols:
            v = r.get(c, 0)
            if isinstance(v,float): vals.append(f"{v:>+11.3f}" if abs(v)<1000 else f"{v:>+11,.1f}")
            else: vals.append(f"{str(v):>11}")
        print(f"  {i:>3}  {k(r):<60} | " + "  ".join(vals))

top_net = rank(all_rows, lambda r: (-finite(r["net_profit"]),
                                    -finite(r["pf_dol"]),
                                    -finite(r["expectancy_R"])), top=100)
top_pf  = rank(all_rows, lambda r: (-finite(r["pf_dol"]),
                                    -finite(r["net_profit"])), top=100)
top_evR = rank(all_rows, lambda r: -finite(r["expectancy_R"]), top=100)
top_fb  = rank(all_rows, lambda r: -finite(r["final_balance"]), top=100)
profitable = [r for r in all_rows if r["net_profit"] > 0 and r["resolved"] >= 10]
profitable.sort(key=lambda r: (finite(r["max_dd_pct"]), -finite(r["net_profit"])))
top_pf_100trd  = rank([r for r in all_rows if r["resolved"]>=100],
                       lambda r: -finite(r["pf_dol"]), top=100)
top_evR_100trd = rank([r for r in all_rows if r["resolved"]>=100],
                       lambda r: -finite(r["expectancy_R"]), top=100)

print_top("TOP 20 BY NET PROFIT", top_net,
          ["resolved","wr","pf_dol","net_profit","expectancy_R","max_dd_pct","mls","final_balance"])
print_top("TOP 20 BY PROFIT FACTOR", top_pf,
          ["resolved","wr","pf_dol","net_profit","expectancy_R","max_dd_pct"])
print_top("TOP 20 BY EXPECTANCY R", top_evR,
          ["resolved","wr","pf_dol","net_profit","expectancy_R","max_dd_pct"])
print_top("TOP 20 BY FINAL BALANCE", top_fb,
          ["resolved","wr","pf_dol","net_profit","final_balance","max_dd_pct"])
print_top("TOP 20 LOWEST DD% (PROFITABLE)", profitable[:100],
          ["resolved","wr","pf_dol","net_profit","max_dd_pct","final_balance"])
print_top("TOP 20 PF WITH >=100 TRADES", top_pf_100trd,
          ["resolved","wr","pf_dol","net_profit","expectancy_R","max_dd_pct"])
print_top("TOP 20 EVR WITH >=100 TRADES", top_evR_100trd,
          ["resolved","wr","pf_dol","net_profit","expectancy_R","max_dd_pct"])


# ============================================================ GROUPED SUMMARIES
def group_summary(rows, key_fn):
    g = defaultdict(list)
    for r in rows: g[key_fn(r)].append(r)
    out = []
    for grp, items in g.items():
        nets = np.array([r["net_profit"] for r in items])
        pfs = np.array([r["pf_dol"] if math.isfinite(r["pf_dol"]) else 0 for r in items])
        evRs = np.array([r["expectancy_R"] for r in items])
        dds = np.array([r["max_dd_pct"] for r in items])
        out.append({"group":grp,"n_configs":len(items),
                    "profitable_count":int((nets>0).sum()),
                    "profitable_pct":float((nets>0).mean()*100),
                    "avg_net":float(nets.mean()),"median_net":float(np.median(nets)),
                    "avg_pf":float(pfs.mean()),"median_pf":float(np.median(pfs)),
                    "avg_evR":float(evRs.mean()),"median_evR":float(np.median(evRs)),
                    "avg_dd":float(dds.mean()),"median_dd":float(np.median(dds))})
    out.sort(key=lambda x:-x["avg_net"])
    return out

def print_grp(label, rows):
    sect(label)
    print(f"  {'group':<30} {'n':>5} {'%prof':>6} {'avg_net':>10} {'med_net':>10} "
          f"{'avg_PF':>7} {'med_PF':>7} {'avg_EVR':>8} {'avg_DD%':>7}")
    print("-"*100)
    for r in rows:
        print(f"  {str(r['group']):<30} {r['n_configs']:>5,} {r['profitable_pct']:>5.1f}% "
              f"{r['avg_net']:>+10,.1f} {r['median_net']:>+10,.1f} "
              f"{r['avg_pf']:>7.3f} {r['median_pf']:>7.3f} "
              f"{r['avg_evR']:>+8.3f} {r['avg_dd']:>6.2f}%")

print_grp("GROUPED BY ENTRY KIND",   group_summary(all_rows, lambda r:r["entry_kind"]))
print_grp("GROUPED BY STOP KIND",    group_summary(all_rows, lambda r:r["stop_kind"]))
print_grp("GROUPED BY EXIT KIND",    group_summary(all_rows, lambda r:r["exit_kind"]))
print_grp("GROUPED BY DIR MODE",     group_summary(all_rows, lambda r:r["direction_mode"]))
print_grp("GROUPED BY TREND FILTER", group_summary(all_rows, lambda r:f"trend={r['trend_filter']}"))
print_grp("GROUPED BY ADX FILTER",   group_summary(all_rows, lambda r:f"adx={r['adx_filter']}"))


# ============================================================ CSV
def save(path, rows, lbl):
    if not rows: tprint(f"  [csv] {lbl}: empty"); return
    keys=[]; seen=set()
    for r in rows:
        for kk in r:
            if kk not in seen: seen.add(kk); keys.append(kk)
    with open(path,"w",newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore", restval="")
        w.writeheader()
        for r in rows: w.writerow(r)
    tprint(f"  [csv] {lbl}: {len(rows):,} rows -> {path}")

save(os.path.join(OUT_DIR,"full_horizon_results.csv"),       all_rows,         "all")
save(os.path.join(OUT_DIR,"full_horizon_top_net.csv"),       top_net,          "top_net")
save(os.path.join(OUT_DIR,"full_horizon_top_pf.csv"),        top_pf,           "top_pf")
save(os.path.join(OUT_DIR,"full_horizon_top_evR.csv"),       top_evR,          "top_evR")
save(os.path.join(OUT_DIR,"full_horizon_top_fb.csv"),        top_fb,           "top_fb")
save(os.path.join(OUT_DIR,"full_horizon_lowest_dd.csv"),     profitable[:100], "lowest_dd")
save(os.path.join(OUT_DIR,"full_horizon_pf_100trades.csv"),  top_pf_100trd,    "pf_100trd")
save(os.path.join(OUT_DIR,"full_horizon_evR_100trades.csv"), top_evR_100trd,   "evR_100trd")

for lbl, fn in [("entry_kind", lambda r:r["entry_kind"]),
                ("stop_kind",  lambda r:r["stop_kind"]),
                ("exit_kind",  lambda r:r["exit_kind"]),
                ("direction",  lambda r:r["direction_mode"]),
                ("trend_filter", lambda r:f"trend={r['trend_filter']}"),
                ("adx_filter",   lambda r:f"adx={r['adx_filter']}")]:
    save(os.path.join(OUT_DIR, f"full_horizon_group_by_{lbl}.csv"),
         group_summary(all_rows, fn), f"group_{lbl}")

# flagged configs
flagged = [r for r in all_rows if r["flags"]]
save(os.path.join(OUT_DIR,"full_horizon_flagged.csv"), flagged, "flagged")


# ============================================================ TOP 10 EQUITY + TRADE CSV + PNG
sect("TOP 10 BY NET PROFIT — equity reconstruction + trade CSVs")
try:
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    has_plt = True
except Exception as e: plt=None; has_plt=False; tprint(f"matplotlib unavailable: {e}")

topk_sorted = sorted(topk_net.items, key=lambda x:-x[0])[:KEEP_TOP_K]
for rank_i, (score, key, arrays) in enumerate(topk_sorted, 1):
    entries, sides, outcome, net_dol, exit_off = arrays
    res = outcome != -1
    if not res.any(): continue
    eq = STARTING_CAPITAL + np.cumsum(net_dol[res])
    peak = np.maximum.accumulate(eq); dd = peak - eq

    # trade CSV
    rows_t = []
    cum=STARTING_CAPITAL; pk=cum
    for tid, idx in enumerate(np.where(res)[0], 1):
        ei=int(entries[idx]); sv=int(sides[idx]); o_=int(outcome[idx])
        cum += float(net_dol[idx])
        if cum > pk: pk = cum
        dd_pct = (pk-cum)/pk*100 if pk > 0 else 0
        rows_t.append({"trade_id":tid,"strategy_key":key,
                       "direction":"long" if sv==1 else "short",
                       "entry_time":str(timestamps[ei]),
                       "entry_price":float(cl[ei]),
                       "outcome":"win" if o_==1 else "loss",
                       "hold_bars":int(exit_off[idx]),
                       "net_pnl_dol":float(net_dol[idx]),
                       "equity_after":cum,
                       "drawdown_pct_after":dd_pct})
    save(os.path.join(OUT_DIR, f"trades_top{rank_i}_{key[:40].replace('|','_')}.csv"),
         rows_t, f"trades_top{rank_i}")

    # PNG
    if has_plt:
        times = pd.to_datetime(timestamps[entries[res]]) if HAS_TS else np.arange(int(res.sum()))
        fig, ax = plt.subplots(2,1,figsize=(11,6),gridspec_kw={"height_ratios":[3,1]})
        ax[0].plot(times, eq, lw=1.2)
        ax[0].set_title(f"#{rank_i}  {key}\nfinal=${eq[-1]:,.0f}  trades={len(eq):,}  net=${score:+,.1f}")
        ax[0].set_ylabel("Equity $")
        ax[1].fill_between(times, dd, 0, color="crimson", alpha=0.4)
        ax[1].set_ylabel("DD $")
        plt.tight_layout()
        plt.savefig(os.path.join(OUT_DIR, f"equity_top{rank_i}_{key[:40].replace('|','_')}.png"), dpi=110)
        plt.close()
        tprint(f"  [png] top{rank_i} saved")

tprint(f"sweep aborted early: {aborted}")
tprint("DONE 🔭")