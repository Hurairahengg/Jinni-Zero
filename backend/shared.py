"""
JINNI ZERO — Shared Execution Primitives
=========================================
Single source of truth for code used by BOTH:
  - backtest_server.py  (Legacy mode)
  - engine_core.py      (Strategy mode)

NEVER duplicate these functions. Always import from here.

Contains:
  - MA precomputation (SMA, EMA, WMA, HMA) — O(n) implementations
  - Cross-run MA cache
  - SpreadGenerator (realistic per-trade spread simulation)
  - calc_comm (commission calculation)
  - compute_analytics (rolling metrics, distributions, regime, time breakdown)
  - clean_for_json (NaN/Inf removal for JSON serialization)
"""
from __future__ import annotations

import math
import random
import time as _time
from collections import defaultdict
from datetime import datetime, timezone


# ════════════════════════════════════════════════════════════════════
#  O(n) MA PRECOMPUTATION FUNCTIONS
# ════════════════════════════════════════════════════════════════════

def precompute_sma(closes, period):
    n = len(closes)
    out = [None] * n
    if period < 1 or n < period:
        return out
    s = 0.0
    for i in range(period):
        s += closes[i]
    out[period - 1] = s / period
    for i in range(period, n):
        s += closes[i] - closes[i - period]
        out[i] = s / period
    return out


def precompute_ema(closes, period):
    n = len(closes)
    out = [None] * n
    if period < 1 or n < period:
        return out
    k = 2.0 / (period + 1)
    s = 0.0
    for i in range(period):
        s += closes[i]
    ema = s / period
    out[period - 1] = ema
    for i in range(period, n):
        ema = closes[i] * k + ema * (1 - k)
        out[i] = ema
    return out


def precompute_wma(closes, period):
    n = len(closes)
    p = period
    out = [None] * n
    if p < 1 or n < p:
        return out
    denom = p * (p + 1) / 2.0
    ws = 0.0
    s = 0.0
    for j in range(p):
        s += closes[j]
        ws += closes[j] * (j + 1)
    out[p - 1] = ws / denom
    for i in range(p, n):
        ws = ws + p * closes[i] - s
        s = s + closes[i] - closes[i - p]
        out[i] = ws / denom
    return out


def precompute_hma(closes, period):
    n = len(closes)
    p = period
    half = p // 2
    sq = int(math.floor(math.sqrt(p)))
    out = [None] * n
    if half < 1 or sq < 1:
        return out
    wma_full = precompute_wma(closes, p)
    wma_half = precompute_wma(closes, half)
    diff = [None] * n
    diff_start = None
    for i in range(n):
        if wma_full[i] is not None and wma_half[i] is not None:
            diff[i] = 2.0 * wma_half[i] - wma_full[i]
            if diff_start is None:
                diff_start = i
    if diff_start is None:
        return out
    valid_diff = []
    valid_map = []
    for i in range(diff_start, n):
        if diff[i] is not None:
            valid_diff.append(diff[i])
            valid_map.append(i)
        else:
            break
    if len(valid_diff) < sq:
        return out
    wma_final = precompute_wma(valid_diff, sq)
    for j in range(len(wma_final)):
        if wma_final[j] is not None:
            out[valid_map[j]] = wma_final[j]
    return out


def precompute_ma(closes, ma_type, period):
    t = str(ma_type).upper()
    if t == "SMA": return precompute_sma(closes, period)
    if t == "EMA": return precompute_ema(closes, period)
    if t == "WMA": return precompute_wma(closes, period)
    if t == "HMA": return precompute_hma(closes, period)
    return [None] * len(closes)


# ════════════════════════════════════════════════════════════════════
#  CROSS-RUN MA CACHE
# ════════════════════════════════════════════════════════════════════
_ma_cache = {}
_ma_cache_dataset_id = None


def get_or_compute_ma(closes, ma_type, period, dataset_id=None):
    global _ma_cache, _ma_cache_dataset_id
    if dataset_id is not None and dataset_id != _ma_cache_dataset_id:
        _ma_cache = {}
        _ma_cache_dataset_id = dataset_id
    key = (ma_type.upper(), period)
    if key not in _ma_cache:
        t0 = _time.perf_counter()
        _ma_cache[key] = precompute_ma(closes, ma_type, period)
        dt = _time.perf_counter() - t0
        print(f"  [CACHE] Computed {ma_type.upper()}({period}) over {len(closes)} bars in {dt*1000:.1f}ms")
    return _ma_cache[key]


# ════════════════════════════════════════════════════════════════════
#  COMMISSION
# ════════════════════════════════════════════════════════════════════

def calc_comm(cfg, contracts=1):
    t = cfg.get("type", "flat")
    a = float(cfg.get("amount", 0))
    if t == "flat":         return a
    if t == "per_contract": return a * contracts * 2
    if t == "per_side":     return a * 2
    return a


# ════════════════════════════════════════════════════════════════════
#  SPREAD GENERATOR
# ════════════════════════════════════════════════════════════════════

class SpreadGenerator:
    """Generates realistic random spread per trade."""

    def __init__(self, config):
        self.enabled = bool(config.get("enabled", False))
        self.min_spread = float(config.get("min", 0.0))
        self.max_spread = float(config.get("max", 0.0))

        seed = config.get("seed")
        if seed is not None and seed != "" and seed != 0:
            self._rng = random.Random(int(seed))
        else:
            self._rng = random.Random()

        if self.min_spread > self.max_spread:
            self.min_spread, self.max_spread = self.max_spread, self.min_spread

        if self.min_spread < 0:
            self.min_spread = 0
        if self.max_spread < 0:
            self.max_spread = 0

        if self.enabled:
            print(f"  [SPREAD] Enabled: ${self.min_spread:.4f} - ${self.max_spread:.4f}"
                  f"  seed={'deterministic' if seed else 'random'}")

    def generate(self):
        if not self.enabled or self.max_spread <= 0:
            return 0.0
        return self._rng.uniform(self.min_spread, self.max_spread)

    def apply_entry(self, price, direction, spread):
        if spread <= 0:
            return price
        half = spread / 2.0
        if direction == "long":
            return price + half
        else:
            return price - half

    def apply_exit(self, price, direction, spread):
        if spread <= 0:
            return price
        half = spread / 2.0
        if direction == "long":
            return price - half
        else:
            return price + half

    def apply_sl(self, sl_level, direction, spread):
        if sl_level is None or spread <= 0:
            return sl_level
        half = spread / 2.0
        if direction == "long":
            return sl_level + half
        else:
            return sl_level - half

    def apply_tp(self, tp_level, direction, spread):
        if tp_level is None or spread <= 0:
            return tp_level
        half = spread / 2.0
        if direction == "long":
            return tp_level + half
        else:
            return tp_level - half


# ════════════════════════════════════════════════════════════════════
#  ANALYTICS + HELPERS
# ════════════════════════════════════════════════════════════════════

def _histogram(data, bins=20):
    if not data:
        return dict(edges=[], counts=[])
    mn = min(data)
    mx = max(data)
    if mn == mx:
        return dict(edges=[mn, mx], counts=[len(data)])
    w = (mx - mn) / bins
    counts = [0] * bins
    edges = [round(mn + i * w, 3) for i in range(bins + 1)]
    for x in data:
        counts[min(int((x - mn) / w), bins - 1)] += 1
    return dict(edges=edges, counts=counts)


def compute_analytics(trades, bars, equity_curve, dd_curve, starting_cap=10000):
    if not trades:
        return {}

    nets = [t["net_pnl"] for t in trades]
    r_mul = [t.get("net_pnl_r") or 0 for t in trades]
    r_hist = _histogram(r_mul, bins=20)

    # ── Rolling window analytics ─────────────────────────────────
    W = 20
    roll_wr = []
    roll_exp = []
    roll_pf = []
    roll_sharpe = []
    for i in range(len(trades)):
        sl = max(0, i - W + 1)
        chunk = [t["net_pnl"] for t in trades[sl:i + 1]]
        wc = [x for x in chunk if x > 0]
        lc = [x for x in chunk if x <= 0]
        wrc = len(wc) / len(chunk) if chunk else 0
        awc = sum(wc) / len(wc) if wc else 0
        alc = sum(lc) / len(lc) if lc else 0
        roll_wr.append(round(wrc * 100, 1))
        roll_exp.append(round(wrc * awc + (1 - wrc) * alc, 2))
        gwa = sum(wc)
        gla = abs(sum(lc))
        roll_pf.append(round(gwa / gla, 3) if gla else None)
        if len(chunk) > 1:
            mu = sum(chunk) / len(chunk)
            var = sum((x - mu) ** 2 for x in chunk) / (len(chunk) - 1)
            sd = math.sqrt(var) if var > 0 else 0
            roll_sharpe.append(round((mu / sd) * math.sqrt(252), 3) if sd else None)
        else:
            roll_sharpe.append(None)

    dur_hist = _histogram([t.get("bars_held", 0) for t in trades], bins=15)
    mae_mfe = [
        dict(mae=round(t.get("mae", 0), 2), mfe=round(t.get("mfe", 0), 2),
             win=t["net_pnl"] > 0)
        for t in trades
    ]
    ret_scatter = [
        dict(x=i + 1, y=round(r, 3), win=r > 0)
        for i, r in enumerate(r_mul)
    ]

    # ── Time-of-day ──────────────────────────────────────────────
    by_hour = defaultdict(list)
    for t in trades:
        try:
            hh = datetime.fromtimestamp(t["entry_time"], tz=timezone.utc).hour
            by_hour[hh].append(t["net_pnl"])
        except Exception:
            pass
    hour_perf = {
        str(hh): dict(trades=len(v), net=round(sum(v), 2),
                       wr=round(len([x for x in v if x > 0]) / len(v) * 100, 1))
        for hh, v in by_hour.items()
    }

    # ── Day-of-week ──────────────────────────────────────────────
    DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    by_dow = defaultdict(list)
    for t in trades:
        try:
            d = datetime.fromtimestamp(t["entry_time"], tz=timezone.utc).weekday()
            by_dow[d].append(t["net_pnl"])
        except Exception:
            pass
    dow_perf = {
        DOW[d]: dict(trades=len(v), net=round(sum(v), 2),
                      wr=round(len([x for x in v if x > 0]) / len(v) * 100, 1))
        for d, v in by_dow.items()
    }

    # ── By month ─────────────────────────────────────────────────
    MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    by_mon = defaultdict(list)
    for t in trades:
        try:
            m = datetime.fromtimestamp(t["entry_time"], tz=timezone.utc).month - 1
            by_mon[m].append(t["net_pnl"])
        except Exception:
            pass
    mon_perf = {
        MONTHS[m]: dict(trades=len(v), net=round(sum(v), 2),
                         wr=round(len([x for x in v if x > 0]) / len(v) * 100, 1))
        for m, v in by_mon.items()
    }

    # ── Volatility regime ────────────────────────────────────────
    bar_ranges = [b["high"] - b["low"] for b in bars]
    vol_regime = {}
    if bar_ranges:
        med_rng = sorted(bar_ranges)[len(bar_ranges) // 2]
        by_vol = {"low_vol": [], "high_vol": []}
        for t in trades:
            bi = t.get("entry_bar", 0)
            if bi < len(bar_ranges):
                k = "low_vol" if bar_ranges[bi] < med_rng else "high_vol"
                by_vol[k].append(t["net_pnl"])
        vol_regime = {
            k: dict(trades=len(v), net=round(sum(v), 2),
                     wr=round(len([x for x in v if x > 0]) / len(v) * 100, 1) if v else 0)
            for k, v in by_vol.items()
        }

    # ── Choppiness regime ────────────────────────────────────────
    chop_by = {"trending": [], "choppy": []}
    for t in trades:
        bi = t.get("entry_bar", 0)
        if bi >= 3:
            last3 = [bars[bi - k]["close"] - bars[bi - k]["open"] for k in range(1, 4)]
            same_dir = all(x > 0 for x in last3) or all(x < 0 for x in last3)
            chop_by["trending" if same_dir else "choppy"].append(t["net_pnl"])
    chop_regime = {
        k: dict(trades=len(v), net=round(sum(v), 2),
                 wr=round(len([x for x in v if x > 0]) / len(v) * 100, 1) if v else 0)
        for k, v in chop_by.items()
    }

    # ── Commission summary ───────────────────────────────────────
    total_comm = sum(t.get("commission", 0) for t in trades)
    gross_abs = sum(abs(t["gross_pnl"]) for t in trades)
    comm_summary = dict(
        total=round(total_comm, 2),
        per_trade=round(total_comm / len(trades), 2) if trades else 0,
        pct_of_gross=round(total_comm / gross_abs * 100, 2) if gross_abs else 0,
        net_without_comm=round(sum(t["gross_pnl"] for t in trades), 2),
        net_with_comm=round(sum(t["net_pnl"] for t in trades), 2),
    )

    return dict(
        r_histogram=r_hist,
        rolling=dict(win_rate=roll_wr, expectancy=roll_exp,
                     profit_factor=roll_pf, sharpe=roll_sharpe),
        duration_histogram=dur_hist,
        mae_mfe=mae_mfe,
        return_scatter=ret_scatter,
        time_of_day=hour_perf,
        day_of_week=dow_perf,
        by_month=mon_perf,
        regime=dict(volatility=vol_regime, choppiness=chop_regime),
        monte_carlo={},
        commission=comm_summary,
    )


# ════════════════════════════════════════════════════════════════════
#  JSON CLEANING  (NaN / Inf removal)
# ════════════════════════════════════════════════════════════════════

def clean_for_json(obj):
    if obj is None or isinstance(obj, (str, int, bool)):
        return obj
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, list):
        if obj and isinstance(obj[0], (int, float)):
            return [
                (None if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else v)
                for v in obj
            ]
        return [clean_for_json(v) for v in obj]
    if isinstance(obj, dict):
        return {k: clean_for_json(v) for k, v in obj.items()}
    return obj