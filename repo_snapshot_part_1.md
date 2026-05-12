# Repository Snapshot - Part 1 of 4

- Root folder: `/home/hurairahengg/Documents/Jinni Zero`
- You know my wholle Jinjnibacktester simulator thign whre ther is a UI bascially and then i can see  charst and stuff when i need to run simulatiosn liek i send simulatio nto my flask backend server it runs sims and then shows stast and stuff and i can load strategy and shit for now take a look we will be doing a whole recode. udnerrtsnad each code and its role how it works and keep in ir conetxt i will ask u exactly wha tto do later code later duinerstood
- Total files indexed: `23`
- Files in this chunk: `8`
## Full Project Tree

```text
.gitignore
backend/__init__.py
backend/dollar_math.py
backend/engine_core.py
backend/shared.py
backend/stats_engine.py
backend/strategies/__init__.py
backend/strategies/base.py
backend/strategies/JinniContinioum.py
backend/strategies/JinniScalperXzero.py
backend/strategies/legacyReplicator.py
backend/strategy_api.py
backend/strategy_loader.py
backtest_server.py
bars/range_bars.py
index.html
js/backtest.js
js/chart.js
js/currency.js
js/strategy_loader.js
STRATEGY_GUIDE.txt
styles.css
test.py
```

## Files In This Chunk - Part 1

```text
.gitignore
backend/shared.py
backend/strategies/JinniScalperXzero.py
backend/strategy_loader.py
backtest_server.py
js/currency.js
STRATEGY_GUIDE.txt
test.py
```

## File Contents


---

## FILE: `.gitignore`

- Relative path: `.gitignore`
- Absolute path at snapshot time: `/home/hurairahengg/Documents/Jinni Zero/.gitignore`
- Size bytes: `20`
- SHA256: `06a05bf02e7f7784314414bd7fbbeec0e650ef092ba7b8f0f79ace361bbc7ff4`
- Guessed MIME type: `unknown`
- Guessed encoding: `unknown`

```text
data/
data/2pt.json
```

---

## FILE: `backend/shared.py`

- Relative path: `backend/shared.py`
- Absolute path at snapshot time: `/home/hurairahengg/Documents/Jinni Zero/backend/shared.py`
- Size bytes: `17882`
- SHA256: `1db785b80127b22631681afcecb3d23e6bb02f8f8c8d140d03850f6ca20e9094`
- Guessed MIME type: `text/x-python`
- Guessed encoding: `unknown`

```python
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
# ════════════════════════════════════════════════════════════════════
#  ANALYTICS ARRAY CAPPING (prevents massive payloads)
# ════════════════════════════════════════════════════════════════════

def cap_analytics_arrays(analytics, max_points=1500):
    """Downsample analytics arrays to prevent huge JSON payloads.
    Called by both legacy and strategy engines before sending response."""
    if not analytics:
        return analytics

    # Rolling arrays (one entry per trade — can be 100k+)
    roll = analytics.get("rolling")
    if roll:
        for key in ("win_rate", "expectancy", "profit_factor", "sharpe"):
            arr = roll.get(key, [])
            if len(arr) > max_points:
                # Simple stride downsample (these are sequential, not min-max)
                step = max(1, len(arr) // max_points)
                roll[key] = arr[::step][:max_points]

    # MAE/MFE scatter (one per trade)
    mm = analytics.get("mae_mfe", [])
    if len(mm) > max_points:
        step = max(1, len(mm) // max_points)
        analytics["mae_mfe"] = mm[::step][:max_points]

    # Return scatter (one per trade)
    rs = analytics.get("return_scatter", [])
    if len(rs) > max_points:
        step = max(1, len(rs) // max_points)
        analytics["return_scatter"] = rs[::step][:max_points]

    return analytics
```

---

## FILE: `backend/strategies/JinniScalperXzero.py`

- Relative path: `backend/strategies/JinniScalperXzero.py`
- Absolute path at snapshot time: `/home/hurairahengg/Documents/Jinni Zero/backend/strategies/JinniScalperXzero.py`
- Size bytes: `7031`
- SHA256: `00b7ffc850df1bce48233dbf040b41e2590ecafe99d4098ce365ecf4d3c960e1`
- Guessed MIME type: `text/x-python`
- Guessed encoding: `unknown`

```python
"""
JINNI ZERO — JinniScalper X Zero
================================
Legacy-matched scalping strategy using:

ENTRY
- Above / below ALL MAs
- 2-bar confirmation
- Candle direction confirmation

TREND + GATING
- HMA 200

STOP LOSS
- Snapshot SL using HMA 200 (signal bar value)

TAKE PROFIT
- MA-cross exit using HMA 21

Execution is 100% engine-driven (legacy-exact):
- Entry at next bar OPEN
- SL/TP computed at FILL TIME
- MA cross exits handled by engine
"""

from __future__ import annotations
from typing import Optional, Dict, Any, List
from backend.strategies.base import BaseStrategy


class JinniScalperXZero(BaseStrategy):
    # ── Metadata ───────────────────────────────────────────────
    strategy_id = "jinni_scalper_x_zero"
    name = "JinniScalper X Zero"
    description = (
        "200 HMA trend + gating, 2-bar confirmation entry, "
        "snapshot SL on HMA 200, TP via HMA 21 cross. "
        "Legacy-matched execution for Renko / range bars."
    )
    version = "1.0.0"
    min_lookback = 210  # safely covers HMA 200

    # ==========================================================
    # PARAMETERS (kept minimal on purpose)
    # ==========================================================
    parameters = {}  # Fixed strategy — no knobs, just logic

    # ==========================================================
    # INDICATOR PLAN (engine-precomputed)
    # ==========================================================
    def build_indicators(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            # Trend + SL snapshot + gating MA
            dict(key="hma_200", kind="HMA", period=200, source="close"),
            # TP MA (cross exit)
            dict(key="hma_21", kind="HMA", period=21, source="close"),
        ]

    # ==========================================================
    # INIT
    # ==========================================================
    def on_init(self, ctx: Any) -> None:
        s = ctx.state
        s["bc_long"] = 0
        s["bc_short"] = 0
        s["regime"] = "neutral"   # neutral | above | below
        s["long_locked"] = False
        s["short_locked"] = False
        s["_gating_last_trade_count"] = 0

    # ==========================================================
    # ON BAR
    # ==========================================================
    def on_bar(self, ctx: Any) -> Optional[Dict[str, Any]]:
        s = ctx.state
        bar = ctx.bar
        ind = ctx.indicators
        bars = ctx.bars
        i = ctx.index

        c = float(bar["close"])
        o = float(bar["open"])
        bull = c > o
        bear = c < o

        hma_200 = ind.get("hma_200")
        hma_21 = ind.get("hma_21")

        # ── Safety: indicators not ready ───────────────────────
        if hma_200 is None or hma_21 is None:
            s["bc_long"] = 0
            s["bc_short"] = 0
            return None

        # ======================================================
        # GATING UNLOCK (legacy-exact)
        # ======================================================
        if s["long_locked"] and c < hma_200:
            s["long_locked"] = False
        if s["short_locked"] and c > hma_200:
            s["short_locked"] = False

        # ======================================================
        # IN POSITION → HOLD
        #
        # SL/TP hits + MA cross exits are handled by ENGINE
        # ======================================================
        if ctx.position.has_position:
            return {"signal": "HOLD"}

        # ======================================================
        # ENTRY LOGIC — ABOVE/BELOW ALL MA (only HMA 200 here)
        # ======================================================
        above = c > hma_200
        below = c < hma_200

        # ── Regime reset (legacy behavior) ────────────────────
        if s["regime"] == "above" and not above:
            s["regime"] = "neutral"
            s["bc_long"] = 0
        elif s["regime"] == "below" and not below:
            s["regime"] = "neutral"
            s["bc_short"] = 0

        sig = None

        # ── LONG side ─────────────────────────────────────────
        if s["regime"] != "below":
            if above and bull:
                s["bc_long"] += 1
            else:
                s["bc_long"] = 0

            if s["bc_long"] >= 2:
                sig = "BUY"
                s["regime"] = "above"
                s["bc_long"] = 0

        # ── SHORT side ────────────────────────────────────────
        if sig is None and s["regime"] != "above":
            if below and bear:
                s["bc_short"] += 1
            else:
                s["bc_short"] = 0

            if s["bc_short"] >= 2:
                sig = "SELL"
                s["regime"] = "below"
                s["bc_short"] = 0

        # ── Gating filter ─────────────────────────────────────
        if sig == "BUY" and s["long_locked"]:
            return None
        if sig == "SELL" and s["short_locked"]:
            return None

        if sig is None:
            return None

        # ======================================================
        # BUILD SIGNAL — ENGINE COMPUTED SL / TP
        # ======================================================
        out = {
            "signal": sig,
            # ── Snapshot SL on HMA 200 (signal bar value) ──
            "sl_mode": "ma_snapshot",
            "sl_ma_val": hma_200,
            # ── TP via MA cross (HMA 21) ──
            "engine_tp_ma_key": "hma_21",
        }

        # Tell engine to also check MA cross for SL if needed
        out["engine_sl_ma_key"] = None  # snapshot SL only (not cross)

        # ======================================================
        # GATING LOCK AFTER TRADE CLOSE (legacy behavior)
        # ======================================================
        self._update_gating_locks(ctx)

        return out

    # ==========================================================
    # GATING LOCK MANAGEMENT (legacy-exact)
    # ==========================================================
    def _update_gating_locks(self, ctx: Any) -> None:
        s = ctx.state
        trades = ctx.trades
        last = s.get("_gating_last_trade_count", 0)

        if len(trades) > last:
            for t in trades[last:]:
                if t["direction"] == "long":
                    s["long_locked"] = True
                elif t["direction"] == "short":
                    s["short_locked"] = True

        s["_gating_last_trade_count"] = len(trades)
```

---

## FILE: `backend/strategy_loader.py`

- Relative path: `backend/strategy_loader.py`
- Absolute path at snapshot time: `/home/hurairahengg/Documents/Jinni Zero/backend/strategy_loader.py`
- Size bytes: `2275`
- SHA256: `0d1c0999c2e19e99d6cf55b1636e698702bd106dbf72be185e40467bda4956a1`
- Guessed MIME type: `text/x-python`
- Guessed encoding: `unknown`

```python
"""
JINNI ZERO — Strategy Loader (with lookback validation)
"""
from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil

from backend.strategies.base import BaseStrategy
import backend.strategies as strategies_pkg

logger = logging.getLogger("jinni.loader")


def _iter_strategy_modules():
    for mod in pkgutil.iter_modules(strategies_pkg.__path__):
        if mod.name in {"base"} or mod.name.startswith("_"):
            continue
        yield mod.name


def discover_strategies():
    registry = {}
    for module_name in _iter_strategy_modules():
        try:
            module = importlib.import_module(f"backend.strategies.{module_name}")
        except Exception as e:
            logger.error(f"Failed to import strategy module '{module_name}': {e}")
            continue

        for _, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, BaseStrategy) or obj is BaseStrategy:
                continue
            try:
                instance = obj()
                strategy_id = instance.strategy_id or module_name
                instance.strategy_id = strategy_id
                registry[strategy_id] = instance
                logger.info(f"Loaded strategy: {strategy_id} (lookback={instance.min_lookback})")
            except Exception as e:
                logger.error(f"Failed to instantiate strategy from {module_name}: {e}")

    return registry


def get_strategy(strategy_id: str):
    registry = discover_strategies()
    if strategy_id not in registry:
        raise KeyError(f"Unknown strategy '{strategy_id}'")
    return registry[strategy_id]


def list_strategy_metadata():
    registry = discover_strategies()
    return [registry[k].get_metadata() for k in sorted(registry.keys())]


def validate_lookback(strategy, bar_count: int, lookback_override: int = 0):
    """Check if enough bars exist for strategy's lookback requirement."""
    required = max(
        getattr(strategy, "min_lookback", 0) or 0,
        lookback_override,
    )
    if bar_count < required:
        raise ValueError(
            f"Strategy '{strategy.strategy_id}' requires {required} bars lookback, "
            f"but only {bar_count} bars available."
        )
    return required
```

---

## FILE: `backtest_server.py`

- Relative path: `backtest_server.py`
- Absolute path at snapshot time: `/home/hurairahengg/Documents/Jinni Zero/backtest_server.py`
- Size bytes: `32592`
- SHA256: `5182f3c0a91274b647a8e01062ec94eb7d95d048b174fb5a0d0f0726944f3202`
- Guessed MIME type: `text/x-python`
- Guessed encoding: `unknown`

```python
"""
backtest_server.py  —  NQ Range Bar Backtest Engine + Analytics
Run:  python backtest_server.py
POST /api/backtest        →  full results JSON
POST /api/backtest/stream →  NDJSON streaming progress + result
GET  /api/health          →  ok
"""
import json, math, os, time as _time
from flask import Flask, jsonify, request, Response, stream_with_context
from flask_cors import CORS
from backend.dollar_math import points_to_dollars, finalize_trade_pnl
from backend.strategy_api import strategy_api
from backend.stats_engine import compute_all_stats, downsample_curve as ds_curve
from backend.shared import (
    precompute_ma,
    get_or_compute_ma,
    SpreadGenerator,
    calc_comm,
    compute_analytics,
    clean_for_json,
    cap_analytics_arrays,
)

app = Flask(__name__)
CORS(app, supports_credentials=True)
DATA_DIR = "data"

app.register_blueprint(strategy_api, url_prefix="/api")

# ════════════════════════════════════════════════════════════════════
#  DATETIME HELPER
# ════════════════════════════════════════════════════════════════════
def _parse_datetime_param(val):
    if not val or not isinstance(val, str) or not val.strip():
        return None
    val = val.strip()
    from datetime import datetime, timezone
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(val, fmt)
            dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue
    return None

# ════════════════════════════════════════════════════════════════════
#  DATA
# ════════════════════════════════════════════════════════════════════
def load_bars(range_pt, bar_range, start_date=None, end_date=None):
    path = os.path.join(DATA_DIR, f"{range_pt}pt.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Dataset not found: {path}")
    with open(path) as f:
        bars = json.load(f)
    total = len(bars)

    start_ts = _parse_datetime_param(start_date)
    end_ts   = _parse_datetime_param(end_date)
    if start_ts is not None or end_ts is not None:
        before = len(bars)
        if start_ts is not None:
            bars = [b for b in bars if b["time"] >= start_ts]
        if end_ts is not None:
            bars = [b for b in bars if b["time"] <= end_ts]
        print(f"  [DATA] Date filter: {before} → {len(bars)} bars"
              f"  (start={start_date}, end={end_date})")

    if bar_range and bar_range > 0:
        bars = bars[-bar_range:]
    print(f"  [DATA] Loaded {path} — {total} total bars, using last {len(bars)}")
    return bars

# ════════════════════════════════════════════════════════════════════
#  INDICATOR ENGINE  (Legacy-specific — uses shared MA functions)
# ════════════════════════════════════════════════════════════════════
class IndicatorEngine:
    def __init__(self, ma_type, period):
        self.ma_type = ma_type.upper()
        self.period  = int(period)
        self._precomputed = None
        self._idx = -1
        self.closes  = []
        self._ema_val = None

    def precompute(self, closes, dataset_id=None):
        self._precomputed = get_or_compute_ma(closes, self.ma_type, self.period, dataset_id)
        self._idx = -1

    def update(self, close):
        if self._precomputed is not None:
            self._idx += 1
            if self._idx < len(self._precomputed):
                return self._precomputed[self._idx]
            return None
        self.closes.append(close)
        n = len(self.closes)
        if self.ma_type == "EMA":  return self._ema(close, n)
        if self.ma_type == "SMA":  return self._sma(n)
        if self.ma_type == "WMA":  return self._wma_at(self.closes, self.period, n-1)
        if self.ma_type == "HMA":  return self._hma(n)
        return None

    def _ema(self, close, n):
        p = self.period; k = 2/(p+1)
        if self._ema_val is None:
            if n < p: return None
            self._ema_val = sum(self.closes[-p:])/p
            return self._ema_val
        self._ema_val = close*k + self._ema_val*(1-k)
        return self._ema_val

    def _sma(self, n):
        p = self.period
        if n < p: return None
        return sum(self.closes[-p:])/p

    @staticmethod
    def _wma_at(closes, p, ei):
        if ei < p-1: return None
        d = p*(p+1)/2
        return sum(closes[ei-j]*(p-j) for j in range(p))/d

    def _hma(self, n):
        p = self.period; h = p//2; sq = int(math.floor(math.sqrt(p)))
        if n < p+sq-1: return None
        ei = n-1; ds = []
        for k in range(sq):
            wf = self._wma_at(self.closes, p, ei-k)
            wh = self._wma_at(self.closes, h, ei-k)
            if wf is None or wh is None: return None
            ds.append(2*wh - wf)
        ds.reverse()
        d = sq*(sq+1)/2
        return sum(ds[sq-1-j]*(sq-j) for j in range(sq))/d

    @property
    def last_value(self):
        if self._precomputed is not None:
            if 0 <= self._idx < len(self._precomputed):
                return self._precomputed[self._idx]
            return None
        if not self.closes: return None
        n = len(self.closes)
        if self.ma_type == "EMA":  return self._ema_val
        if self.ma_type == "SMA":  return self._sma(n) if n >= self.period else None
        if self.ma_type == "WMA":  return self._wma_at(self.closes, self.period, n-1)
        if self.ma_type == "HMA":  return self._hma(n)
        return None


class MultiIndicatorEngine:
    def __init__(self, ma_defs):
        self.engines = [IndicatorEngine(d["type"], d["period"]) for d in ma_defs]
    def precompute(self, closes, dataset_id=None):
        for eng in self.engines:
            eng.precompute(closes, dataset_id)
    def update(self, close):
        return [eng.update(close) for eng in self.engines]

# ════════════════════════════════════════════════════════════════════
#  BACKTEST ENGINE  (LEGACY — UNTOUCHED LOGIC + risk% sizing)
# ════════════════════════════════════════════════════════════════════
class BacktestEngine:
    def __init__(self, bars, config):
        self.bars   = bars
        self.config = config
        self.n      = len(bars)

        self.ma_defs       = config.get("mas", [])
        self.entry_mode    = config.get("entry", "above_all_mas")
        self.sl_cfg        = config.get("sl", {})
        self.tp_cfg        = config.get("tp", {})
        self.comm_cfg      = config.get("commission", {})
        self.gating_cfg    = config.get("gating", {})
        self.lot_size      = float(config.get("lot_size", 1.0))
        self.point_value   = float(config.get("point_value", 1.0))
        self.starting_cap  = float(config.get("starting_capital", 10000.0))
        self.ambiguous_mode = config.get("ambiguous_bar_mode", "conservative")
        self.require_candle_confirm = config.get("require_candle_confirm", True)

        self.sl_mode = self.sl_cfg.get("mode", "fixed")
        self.tp_mode = self.tp_cfg.get("mode", "r_multiple")
        self.gating_enabled = bool(self.gating_cfg.get("enabled", False))

        self.spread_gen = SpreadGenerator(config.get("spread", {}))

        # ── Position sizing ──────────────────────────────────────
        self.sizing_mode = str(config.get("sizing_mode", "fixed")).lower()
        self.risk_pct = float(config.get("risk_pct", 1.0))

        self.entry_ind = MultiIndicatorEngine(self.ma_defs)

        self.sl_ma_eng = None
        if self.sl_mode in ("ma_cross", "ma_snapshot"):
            self.sl_ma_eng = IndicatorEngine(
                self.sl_cfg.get("ma_type", "EMA"),
                self.sl_cfg.get("ma_length", 50))

        self.tp_ma_eng = None
        if self.tp_mode == "ma_cross":
            self.tp_ma_eng = IndicatorEngine(
                self.tp_cfg.get("ma_type", "EMA"),
                self.tp_cfg.get("ma_length", 9))

        self.gating_eng = None
        if self.gating_enabled:
            self.gating_eng = IndicatorEngine(
                self.gating_cfg.get("ma_type", "HMA"),
                self.gating_cfg.get("ma_length", 21))

        self.trades       = []
        self.equity_curve = []
        self.dd_curve     = []

        closes = [b["close"] for b in self.bars]
        dataset_id = id(self.bars)
        t0 = _time.perf_counter()
        self.entry_ind.precompute(closes, dataset_id)
        if self.sl_ma_eng:  self.sl_ma_eng.precompute(closes, dataset_id)
        if self.tp_ma_eng:  self.tp_ma_eng.precompute(closes, dataset_id)
        if self.gating_eng: self.gating_eng.precompute(closes, dataset_id)
        dt = _time.perf_counter() - t0

        sizing_str = f"lot={self.lot_size}" if self.sizing_mode == "fixed" \
                     else f"risk={self.risk_pct}%"
        print(f"  [ENGINE] {self.n} bars | entry={self.entry_mode} | "
              f"SL={self.sl_mode} | TP={self.tp_mode} | "
              f"gating={self.gating_enabled} | "
              f"candle_confirm={self.require_candle_confirm} | "
              f"{sizing_str} | spread={self.spread_gen.enabled} | "
              f"precompute={dt*1000:.1f}ms")

    def run(self):
        for _ in self._run_generator(): pass
        return self._build_result()

    def run_streaming(self):
        yield from self._run_generator()
        yield {"type": "result", "data": self._build_result()}

    # ── Core loop (LEGACY — UNTOUCHED + risk% sizing) ───────────
    def _run_generator(self):
        state = "flat"
        open_t = None
        pending_signal = None

        ma_hist = []
        bc = bc2 = 0
        regime = "neutral"

        long_locked  = False
        short_locked = False

        cum  = 0.0
        peak = self.starting_cap
        last_closed_pnl = None

        progress_interval = max(1, self.n // 100)
        last_emit_time = _time.perf_counter()
        EMIT_MIN_INTERVAL = 0.15

        prev_sl_ma_val = None

        for i, bar in enumerate(self.bars):
            c = bar["close"]; o = bar["open"]; h = bar["high"]; l = bar["low"]

            mv = self.entry_ind.update(c)
            ma_hist.append(mv)
            sl_ma_val = self.sl_ma_eng.update(c) if self.sl_ma_eng else None
            tp_ma_val = self.tp_ma_eng.update(c) if self.tp_ma_eng else None
            gating_val = self.gating_eng.update(c) if self.gating_eng else None

            just_entered = False
            if pending_signal is not None and state == "flat":
                direction = pending_signal
                ep = o

                sl_level, risk_pts = self._compute_sl(direction, ep, prev_sl_ma_val)
                tp_level = self._compute_tp(direction, ep, risk_pts)

                trade_spread = self.spread_gen.generate()
                ep = self.spread_gen.apply_entry(ep, direction, trade_spread)
                sl_level = self.spread_gen.apply_sl(sl_level, direction, trade_spread)
                tp_level = self.spread_gen.apply_tp(tp_level, direction, trade_spread)

                if sl_level is not None:
                    risk_pts = abs(ep - sl_level)

                valid_entry = True
                if risk_pts is None or risk_pts <= 0:
                    valid_entry = False
                if sl_level is not None:
                    if direction == "long" and sl_level >= ep:
                        valid_entry = False
                    if direction == "short" and sl_level <= ep:
                        valid_entry = False
                if tp_level is not None:
                    if direction == "long" and tp_level <= ep:
                        valid_entry = False
                    if direction == "short" and tp_level >= ep:
                        valid_entry = False

                # ── Dynamic position sizing ──────────────────────
                if valid_entry and self.sizing_mode == "risk_pct":
                    if risk_pts is None or risk_pts <= 0:
                        valid_entry = False
                    else:
                        balance_now = self.starting_cap + cum
                        risk_amount = balance_now * (self.risk_pct / 100.0)
                        trade_lot = risk_amount / (risk_pts * self.point_value)
                        trade_lot = max(0.01, round(trade_lot, 2))
                else:
                    trade_lot = self.lot_size

                if valid_entry:
                    open_t = dict(
                        id=len(self.trades)+1, direction=direction,
                        entry_bar=i, entry_time=bar["time"], entry_price=round(ep, 4),
                        sl_level=sl_level, tp_level=tp_level,
                        risk_pts=risk_pts, mae=0.0, mfe=0.0, bars_held=0,
                        spread=round(trade_spread, 4),
                        lot_size=trade_lot,
                    )
                    state = direction
                    just_entered = True

            pending_signal = None

            if state != "flat" and open_t is not None and not just_entered:
                closed = self._check_exit(open_t, bar, i, sl_ma_val, tp_ma_val)
                if closed:
                    trade_spread = closed.get("spread", 0.0)
                    raw_exit = closed["exit_price"]
                    closed["exit_price"] = round(
                        self.spread_gen.apply_exit(raw_exit, closed["direction"], trade_spread), 4)

                    self._finalize_trade(closed)
                    cum += closed["net_pnl"]
                    closed["cumulative_pnl"] = round(cum, 2)
                    self.trades.append(closed)
                    last_closed_pnl = closed["net_pnl"]

                    if len(self.trades) <= 5:
                        t = closed
                        print(f"  [TRADE #{t['id']}] {t['direction'].upper()} "
                              f"entry={t['entry_price']} exit={t['exit_price']} "
                              f"SL={t.get('sl_level')} TP={t.get('tp_level')} "
                              f"risk={t.get('risk_pts')}pts "
                              f"lot={t.get('lot_size')} "
                              f"spread={t.get('spread',0):.4f} "
                              f"points={t.get('points_pnl')} "
                              f"R={t.get('net_pnl_r')} "
                              f"gross=${t.get('gross_pnl')} "
                              f"comm=${t.get('commission')} "
                              f"net=${t.get('net_pnl')} "
                              f"reason={t.get('exit_reason')}")

                    if self.gating_enabled:
                        if closed["direction"] == "long":  long_locked = True
                        if closed["direction"] == "short": short_locked = True

                    open_t = None
                    state = "flat"

            if state != "flat" and open_t is not None and just_entered:
                d = open_t["direction"]; ep2 = open_t["entry_price"]
                hh = bar["high"]; ll = bar["low"]
                open_t["mae"] = max(open_t.get("mae",0), (ep2-ll) if d=="long" else (hh-ep2))
                open_t["mfe"] = max(open_t.get("mfe",0), (hh-ep2) if d=="long" else (ep2-ll))

            if self.gating_enabled and gating_val is not None:
                if long_locked  and c < gating_val: long_locked  = False
                if short_locked and c > gating_val: short_locked = False

            if state != "flat" and open_t:
                pts = (c - open_t["entry_price"]) if state == "long" \
                      else (open_t["entry_price"] - c)
                t_lot = open_t.get("lot_size", self.lot_size)
                unrealised = points_to_dollars(pts, t_lot, self.point_value)
            else:
                unrealised = 0.0
            eq = self.starting_cap + cum + unrealised
            self.equity_curve.append(round(eq, 2))
            peak = max(peak, eq)
            dd = eq - peak
            self.dd_curve.append(round(dd, 2))

            now = _time.perf_counter()
            should_emit = (
                i == 0 or
                i == self.n - 1 or
                (i % progress_interval == 0 and (now - last_emit_time) >= EMIT_MIN_INTERVAL)
            )
            if should_emit:
                last_emit_time = now
                oi = None
                if open_t:
                    oi = dict(direction=open_t["direction"],
                              entry_price=round(open_t["entry_price"],2),
                              sl=round(open_t["sl_level"],2) if open_t.get("sl_level") else None,
                              tp=round(open_t["tp_level"],2) if open_t.get("tp_level") else None)
                yield dict(type="progress", bar=i, total=self.n,
                           pct=round(i/max(self.n-1,1)*100,1),
                           equity=round(eq,2), drawdown=round(dd,2),
                           open_trade=oi, last_closed_pnl=last_closed_pnl)

            prev_sl_ma_val = sl_ma_val

            if state != "flat" or pending_signal is not None:
                continue
            if any(v is None for v in mv):
                continue

            ab = all(c > v for v in mv)
            bl = all(c < v for v in mv)
            bull = c > o
            bear = c < o

            if regime == "above" and not ab:
                regime = "neutral"; bc = 0
            elif regime == "below" and not bl:
                regime = "neutral"; bc2 = 0

            sig = None

            if self.entry_mode == "above_all_mas":
                if regime != "below":
                    if ab and bull: bc += 1
                    else: bc = 0
                    if bc >= 2:
                        sig = "long"; regime = "above"; bc = 0
                if sig is None and regime != "above":
                    if bl and bear: bc2 += 1
                    else: bc2 = 0
                    if bc2 >= 2:
                        sig = "short"; regime = "below"; bc2 = 0

            elif self.entry_mode == "ma_cross" and len(self.ma_defs) >= 2 and i > 0:
                pm = ma_hist[i-1]
                if None not in (mv[0], mv[1], pm[0], pm[1]):
                    if pm[0] <= pm[1] and mv[0] > mv[1]: sig = "long"
                    elif pm[0] >= pm[1] and mv[0] < mv[1]: sig = "short"

            elif self.entry_mode == "trend_filter" and i > 0:
                lm = mv[-1]; plm = ma_hist[i-1][-1]; pc = self.bars[i-1]["close"]
                if lm and plm:
                    if pc <= plm and c > lm and bull:
                        sig = "long"
                    elif pc >= plm and c < lm and bear:
                        sig = "short"

            if self.require_candle_confirm:
                if sig == "long" and not bull:
                    sig = None
                if sig == "short" and not bear:
                    sig = None

            if sig == "long"  and self.gating_enabled and long_locked:  sig = None
            if sig == "short" and self.gating_enabled and short_locked: sig = None

            if sig:
                pending_signal = sig

        if state != "flat" and open_t:
            lb = self.bars[-1]; cp = lb["close"]
            d = open_t["direction"]
            open_t["mae"] = max(open_t.get("mae",0),
                                (open_t["entry_price"]-lb["low"]) if d=="long" else (lb["high"]-open_t["entry_price"]))
            open_t["mfe"] = max(open_t.get("mfe",0),
                                (lb["high"]-open_t["entry_price"]) if d=="long" else (open_t["entry_price"]-lb["low"]))
            closed = {**open_t,
                      "exit_bar": self.n-1, "exit_time": lb["time"],
                      "exit_price": round(cp,2), "exit_reason": "end_of_data",
                      "holding_seconds": abs(lb["time"]-open_t["entry_time"]),
                      "bars_held": self.n-1-open_t["entry_bar"]}
            trade_spread = closed.get("spread", 0.0)
            closed["exit_price"] = round(
                self.spread_gen.apply_exit(closed["exit_price"], d, trade_spread), 4)
            self._finalize_trade(closed)
            cum += closed["net_pnl"]
            closed["cumulative_pnl"] = round(cum,2)
            self.trades.append(closed)

    # ── SL computation (LEGACY — UNTOUCHED) ─────────────────────
    def _compute_sl(self, direction, entry_price, sl_ma_val):
        mode = self.sl_mode
        if mode == "fixed":
            pts = float(self.sl_cfg.get("fixed_pts", 8))
            if pts <= 0: return None, None
            sl = (entry_price - pts) if direction == "long" else (entry_price + pts)
            return round(sl, 4), pts
        elif mode == "ma_snapshot":
            if sl_ma_val is None: return None, None
            if direction == "long" and sl_ma_val >= entry_price:
                return None, None
            if direction == "short" and sl_ma_val <= entry_price:
                return None, None
            risk = abs(entry_price - sl_ma_val)
            if risk <= 0: return None, None
            return round(sl_ma_val, 4), round(risk, 4)
        elif mode == "ma_cross":
            if sl_ma_val is None: return None, None
            if direction == "long" and sl_ma_val >= entry_price:
                return None, None
            if direction == "short" and sl_ma_val <= entry_price:
                return None, None
            risk = abs(entry_price - sl_ma_val)
            if risk <= 0: return None, None
            return None, round(risk, 4)
        return None, None

    def _compute_tp(self, direction, entry_price, risk_pts):
        mode = self.tp_mode
        if mode == "r_multiple":
            r = float(self.tp_cfg.get("r_multiple", 2))
            if risk_pts and risk_pts > 0:
                tp = (entry_price + risk_pts * r) if direction == "long" \
                     else (entry_price - risk_pts * r)
                return round(tp, 4)
        return None

    # ── Exit evaluation (LEGACY — UNTOUCHED) ────────────────────
    def _check_exit(self, t, bar, bi, sl_ma_val, tp_ma_val):
        d  = t["direction"]; ep = t["entry_price"]
        sl = t.get("sl_level")
        tp = t.get("tp_level")
        hh = bar["high"]; ll = bar["low"]; c = bar["close"]

        t["bars_held"] = bi - t["entry_bar"]
        t["mae"] = max(t.get("mae",0), (ep-ll) if d=="long" else (hh-ep))
        t["mfe"] = max(t.get("mfe",0), (hh-ep) if d=="long" else (ep-ll))

        sl_hit = False
        if sl is not None:
            if d == "long"  and ll <= sl: sl_hit = True
            if d == "short" and hh >= sl: sl_hit = True

        tp_hit = False
        if tp is not None:
            if d == "long"  and hh >= tp: tp_hit = True
            if d == "short" and ll <= tp: tp_hit = True

        if sl_hit and tp_hit:
            m = self.ambiguous_mode
            if m == "optimistic":
                return self._make_exit(t, bar, bi, tp, "TP_R")
            elif m == "nearest_to_open":
                if abs(bar["open"]-sl) <= abs(bar["open"]-tp):
                    return self._make_exit(t, bar, bi, sl, "SL_HIT")
                else:
                    return self._make_exit(t, bar, bi, tp, "TP_R")
            else:
                return self._make_exit(t, bar, bi, sl, "SL_HIT")

        if sl_hit: return self._make_exit(t, bar, bi, sl, "SL_HIT")
        if tp_hit: return self._make_exit(t, bar, bi, tp, "TP_R")

        if self.sl_mode == "ma_cross" and sl_ma_val is not None:
            if d == "long"  and c < sl_ma_val:
                return self._make_exit(t, bar, bi, c, "MA_SL_EXIT")
            if d == "short" and c > sl_ma_val:
                return self._make_exit(t, bar, bi, c, "MA_SL_EXIT")

        if self.tp_mode == "ma_cross" and tp_ma_val is not None:
            if d == "long"  and c < tp_ma_val:
                return self._make_exit(t, bar, bi, c, "MA_TP_EXIT")
            if d == "short" and c > tp_ma_val:
                return self._make_exit(t, bar, bi, c, "MA_TP_EXIT")

        return None

    def _make_exit(self, t, bar, bi, exit_price, reason):
        return {**t,
                "exit_bar": bi, "exit_time": bar["time"],
                "exit_price": round(exit_price, 4),
                "exit_reason": reason,
                "holding_seconds": abs(bar["time"] - t["entry_time"]),
                "bars_held": bi - t["entry_bar"]}

    def _finalize_trade(self, closed):
        commission = calc_comm(self.comm_cfg)
        trade_lot = closed.get("lot_size", self.lot_size)
        finalize_trade_pnl(
            closed,
            lot_size=trade_lot,
            point_value=self.point_value,
            commission=commission,
        )

    def _build_result(self):
        MAX_TRADES = 2000
        perf = {}
        build_t0 = _time.perf_counter()

        stats_t0 = _time.perf_counter()
        stats = compute_all_stats(
            trades=self.trades,
            equity_curve=self.equity_curve,
            bars=self.bars,
            starting_capital=self.starting_cap,
            lot_size=self.lot_size,
        )
        stats_t1 = _time.perf_counter()
        perf["stats_seconds"] = round(stats_t1 - stats_t0, 4)

        analytics_t0 = _time.perf_counter()
        analytics = compute_analytics(self.trades, self.bars,
                                      self.equity_curve, self.dd_curve,
                                      self.starting_cap)
        analytics_t1 = _time.perf_counter()
        perf["analytics_seconds"] = round(analytics_t1 - analytics_t0, 4)

        # Cap analytics arrays
        analytics = cap_analytics_arrays(analytics, 1500)

        # Cap trades in response
        total_count = len(self.trades)
        if total_count > MAX_TRADES:
            trades_out = self.trades[-MAX_TRADES:]
            print(f"  [TRADE CAP] Sending last {MAX_TRADES} of {total_count} trades")
        else:
            trades_out = self.trades

        result = dict(
            stats=stats,
            trades=trades_out,
            total_trade_count=total_count,
            trades_truncated=total_count > MAX_TRADES,
            equity_curve=ds_curve(self.equity_curve, 1500),
            drawdown_curve=ds_curve(self.dd_curve, 1500),
            analytics=analytics,
        )

        build_t1 = _time.perf_counter()
        perf["response_build_seconds"] = round(build_t1 - build_t0, 4)
        perf["trade_count"] = total_count
        result["performance"] = perf

        print(f"  [BUILD TIMING] stats={perf['stats_seconds']:.3f}s "
              f"analytics={perf['analytics_seconds']:.3f}s "
              f"response_build={perf['response_build_seconds']:.3f}s "
              f"trades={total_count}")

        return result


# ════════════════════════════════════════════════════════════════════
#  FLASK ROUTES (WITH TIMING)
# ════════════════════════════════════════════════════════════════════
def _validate_and_load(cfg):
    if not cfg: raise ValueError("Empty body")
    mas = cfg.get("mas",[])
    if not mas: raise ValueError("Need at least one MA")

    bars = load_bars(
        int(cfg.get("range",10)),
        int(cfg.get("bar_range",1000)),
        start_date=cfg.get("start_date"),
        end_date=cfg.get("end_date"),
    )

    max_period = 0
    for m in mas:
        p = int(m.get("period", 0))
        if p > max_period:
            max_period = p

    sl_cfg = cfg.get("sl", {})
    if sl_cfg.get("mode") in ("ma_cross", "ma_snapshot"):
        sp = int(sl_cfg.get("ma_length", 50))
        if sp > max_period:
            max_period = sp

    tp_cfg = cfg.get("tp", {})
    if tp_cfg.get("mode") == "ma_cross":
        tp = int(tp_cfg.get("ma_length", 9))
        if tp > max_period:
            max_period = tp

    gating_cfg = cfg.get("gating", {})
    if gating_cfg.get("enabled"):
        gp = int(gating_cfg.get("ma_length", 21))
        if gp > max_period:
            max_period = gp

    min_bars = max_period + int(math.sqrt(max(max_period, 1))) + 2
    min_bars = max(min_bars, 5)

    if len(bars) < min_bars:
        raise ValueError(
            f"Need at least {min_bars} bars for these indicators "
            f"(longest MA period = {max_period}), but only {len(bars)} bars available. "
            f"Try a larger date range or lower bar count."
        )

    return bars, cfg


@app.route("/api/backtest", methods=["POST"])
def run_backtest():
    try:
        route_t0 = _time.perf_counter()

        cfg=request.get_json(force=True); bars,cfg=_validate_and_load(cfg)

        sim_t0 = _time.perf_counter()
        engine=BacktestEngine(bars,cfg); result=engine.run()
        sim_t1 = _time.perf_counter()

        result = clean_for_json(result)

        json_t0 = _time.perf_counter()
        response_body = json.dumps(result)
        json_t1 = _time.perf_counter()

        payload_kb = len(response_body) / 1024
        route_t1 = _time.perf_counter()

        perf = result.get("performance", {})
        perf["simulation_seconds"] = round(sim_t1 - sim_t0, 4)
        perf["json_seconds"] = round(json_t1 - json_t0, 4)
        perf["total_seconds"] = round(route_t1 - route_t0, 4)
        perf["payload_size_kb"] = round(payload_kb, 1)

        print(f"  [BACKTEST TIMING] simulation={perf.get('simulation_seconds',0):.3f}s "
              f"stats={perf.get('stats_seconds',0):.3f}s "
              f"analytics={perf.get('analytics_seconds',0):.3f}s "
              f"json={perf.get('json_seconds',0):.3f}s "
              f"payload={payload_kb:.1f}KB "
              f"total={perf.get('total_seconds',0):.3f}s "
              f"trades={perf.get('trade_count',0)}")

        return Response(response_body, mimetype="application/json"), 200
    except ValueError as e: return jsonify(error=str(e)),400
    except FileNotFoundError as e: return jsonify(error=str(e)),404
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify(error=str(e)),500


@app.route("/api/backtest/stream", methods=["POST"])
def run_backtest_stream():
    try:
        cfg=request.get_json(force=True); bars,cfg=_validate_and_load(cfg)
    except ValueError as e: return jsonify(error=str(e)),400
    except FileNotFoundError as e: return jsonify(error=str(e)),404
    except Exception as e: return jsonify(error=str(e)),500
    engine=BacktestEngine(bars,cfg)
    def generate():
        try:
            for msg in engine.run_streaming():
                yield json.dumps(clean_for_json(msg))+"\n"
        except Exception as e:
            import traceback; traceback.print_exc()
            yield json.dumps({"type": "error", "error": str(e)}) + "\n"
    return Response(stream_with_context(generate()), mimetype='application/x-ndjson',
        headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no','Access-Control-Allow-Origin':'*'})


@app.route("/api/health", methods=["GET"])
def health(): return jsonify(status="ok"),200


if __name__ == "__main__":
    print("="*52+"\n  NQ Backtest Server  http://localhost:5000\n"+"="*52)
    app.run(host="0.0.0.0", port=5000, debug=False)
```

---

## FILE: `js/currency.js`

- Relative path: `js/currency.js`
- Absolute path at snapshot time: `/home/hurairahengg/Documents/Jinni Zero/js/currency.js`
- Size bytes: `11325`
- SHA256: `942f62b5b9e96dd9174cccf6f0983f833c53bbbeba886d17c2927c557a0f6ae7`
- Guessed MIME type: `text/javascript`
- Guessed encoding: `unknown`

```javascript
/* ═══════════════════════════════════════════════════════════════════
   JINNI ZERO — Frontend Currency Conversion (Display-Only)
   
   DOES NOT affect backend, trade logic, equity curves, or stored values.
   All conversion is purely visual — raw USD values are preserved
   as data-raw-usd attributes on DOM elements.
   
   Auto-hooks into btRenderAnyResult to tag dollar elements after
   every backtest render. When user changes conversion settings,
   ALL tagged elements auto-update.
   
   Load AFTER backtest.js.
═══════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  // ════════════════════════════════════════════════════════════════
  //  STATE
  // ════════════════════════════════════════════════════════════════
  var state = {
    enabled: false,
    multiplier: 1,
    symbol: '$',
    decimals: 2,
  };

  var SYMBOL_DECIMALS = {
    '$': 2, '¥': 0, '€': 2, '£': 2, '₹': 2,
    '৳': 2, 'kr': 2, 'R$': 2, '₩': 0, 'CHF': 2,
  };

  var SYMBOL_LIST = Object.keys(SYMBOL_DECIMALS);

  // ════════════════════════════════════════════════════════════════
  //  FORMATTING
  // ════════════════════════════════════════════════════════════════

  function _addCommas(str) {
    var parts = str.split('.');
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    return parts.join('.');
  }

  function format(rawUsd, opts) {
    if (rawUsd == null || rawUsd === '' || rawUsd === '—') return '—';
    opts = opts || {};

    var num = typeof rawUsd === 'string' ? parseFloat(rawUsd) : rawUsd;
    if (!Number.isFinite(num)) return '—';

    var converted = state.enabled ? num * state.multiplier : num;
    var sym = opts.noSymbol ? '' : state.symbol;
    var dec = opts.decimals != null ? opts.decimals : state.decimals;
    var isNeg = converted < 0;
    var abs = Math.abs(converted);

    // Compact mode for large numbers
    if (opts.compact && abs >= 100000) {
      if (abs >= 1000000) {
        var mStr = _addCommas((abs / 1000000).toFixed(Math.min(dec, 2)));
        return (isNeg ? '-' : (opts.forceSign && num > 0 ? '+' : '')) + sym + mStr + 'M';
      }
      var kStr = _addCommas((abs / 1000).toFixed(Math.min(dec, 1)));
      return (isNeg ? '-' : (opts.forceSign && num > 0 ? '+' : '')) + sym + kStr + 'K';
    }

    var formatted = _addCommas(abs.toFixed(dec));
    var sign = isNeg ? '-' : (opts.forceSign && num > 0 ? '+' : '');
    return sign + sym + formatted;
  }

  function formatPct(val, dec) {
    if (val == null || val === '') return '—';
    var num = typeof val === 'string' ? parseFloat(val) : val;
    if (!Number.isFinite(num)) return '—';
    return (num > 0 ? '+' : '') + num.toFixed(dec != null ? dec : 2) + '%';
  }

  function formatR(val, dec) {
    if (val == null || val === '') return '—';
    var num = typeof val === 'string' ? parseFloat(val) : val;
    if (!Number.isFinite(num)) return '—';
    return (num > 0 ? '+' : '') + num.toFixed(dec != null ? dec : 2) + 'R';
  }

  function formatNum(val, dec) {
    if (val == null || val === '') return '—';
    var num = typeof val === 'string' ? parseFloat(val) : val;
    if (!Number.isFinite(num)) return '—';
    return (num < 0 ? '-' : '') + _addCommas(Math.abs(num).toFixed(dec != null ? dec : 2));
  }

  // ════════════════════════════════════════════════════════════════
  //  DOM TAGGING (stores raw USD, auto-refreshes on change)
  // ════════════════════════════════════════════════════════════════
  var DATA_ATTR = 'data-raw-usd';
  var OPTS_ATTR = 'data-currency-opts';

  function tag(el, rawUsd, opts) {
    if (!el) return;
    if (rawUsd == null || rawUsd === '') {
      el.textContent = '—';
      el.removeAttribute(DATA_ATTR);
      el.removeAttribute(OPTS_ATTR);
      return;
    }
    el.setAttribute(DATA_ATTR, String(rawUsd));
    if (opts) {
      el.setAttribute(OPTS_ATTR, JSON.stringify(opts));
    } else {
      el.removeAttribute(OPTS_ATTR);
    }
    el.textContent = format(rawUsd, opts);
  }

  function refreshAll() {
    var els = document.querySelectorAll('[' + DATA_ATTR + ']');
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      var raw = parseFloat(el.getAttribute(DATA_ATTR));
      var optsStr = el.getAttribute(OPTS_ATTR);
      var opts = null;
      if (optsStr) { try { opts = JSON.parse(optsStr); } catch (e) {} }
      el.textContent = format(raw, opts);
    }
  }

  // ════════════════════════════════════════════════════════════════
  //  SETTINGS
  // ════════════════════════════════════════════════════════════════

  function setEnabled(on) {
    state.enabled = !!on;
    if (!state.enabled) {
      state.multiplier = 1;
      state.symbol = '$';
      state.decimals = 2;
    }
    _syncUi();
    refreshAll();
  }

  function setMultiplier(val) {
    var n = parseFloat(val);
    state.multiplier = Number.isFinite(n) && n > 0 ? n : 1;
    refreshAll();
  }

  function setSymbol(sym) {
    state.symbol = sym || '$';
    state.decimals = SYMBOL_DECIMALS[state.symbol] != null
      ? SYMBOL_DECIMALS[state.symbol] : 2;
    refreshAll();
  }

  function _syncUi() {
    var toggle = document.getElementById('cc_enabled');
    var panel  = document.getElementById('cc_panel');
    var mult   = document.getElementById('cc_multiplier');
    var sym    = document.getElementById('cc_symbol');
    if (toggle) toggle.checked = state.enabled;
    if (panel)  panel.style.display = state.enabled ? 'flex' : 'none';
    if (mult)   mult.value = state.multiplier;
    if (sym)    sym.value = state.symbol;
  }

  // ════════════════════════════════════════════════════════════════
  //  STAT ID → FORMAT OPTIONS MAP
  //
  //  Every dashboard element that shows a dollar value.
  //  true = default opts, object = custom opts.
  // ════════════════════════════════════════════════════════════════
  

  // ════════════════════════════════════════════════════════════════
  //  AUTO-TAG AFTER BACKTEST RENDER
  // ════════════════════════════════════════════════════════════════

  

  // ════════════════════════════════════════════════════════════════
  //  HOOK INTO btRenderAnyResult
  // ════════════════════════════════════════════════════════════════

  function installHook() {
    var check = setInterval(function () {
      if (typeof window.btRenderAnyResult !== 'function') return;
      clearInterval(check);

      var original = window.btRenderAnyResult;
      window.btRenderAnyResult = function (data, cfg) {
        original(data, cfg);
        // backtest.js already formats via fmt() → CurrencyDisplay.format()
        // and stamps data-raw-usd on elements. This just ensures a clean
        // re-tag pass in case anything was missed.
        requestAnimationFrame(function () { refreshAll(); });
      };
    }, 200);

    setTimeout(function () { clearInterval(check); }, 10000);
  }

  // ════════════════════════════════════════════════════════════════
  //  UI WIRING
  // ════════════════════════════════════════════════════════════════

  function wireControls() {
    var toggle = document.getElementById('cc_enabled');
    var mult   = document.getElementById('cc_multiplier');
    var sym    = document.getElementById('cc_symbol');

    if (toggle) {
      toggle.addEventListener('change', function () { setEnabled(this.checked); });
    }
    if (mult) {
      mult.addEventListener('input', function () { setMultiplier(this.value); });
      mult.addEventListener('change', function () { setMultiplier(this.value); });
    }
    if (sym) {
      sym.addEventListener('change', function () { setSymbol(this.value); });
    }
    _syncUi();
  }

  // ════════════════════════════════════════════════════════════════
  //  BOOT
  // ════════════════════════════════════════════════════════════════

  function boot() {
    wireControls();
    installHook();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  // ════════════════════════════════════════════════════════════════
  //  PUBLIC API
  // ════════════════════════════════════════════════════════════════

  window.CurrencyDisplay = {
    format: format,
    formatPct: formatPct,
    formatR: formatR,
    formatNum: formatNum,
    tag: tag,
    refreshAll: refreshAll,
    setEnabled: setEnabled,
    setMultiplier: setMultiplier,
    setSymbol: setSymbol,
    getState: function () { return state; },
    SYMBOLS: SYMBOL_LIST,
  };

})();
```

---

## FILE: `STRATEGY_GUIDE.txt`

- Relative path: `STRATEGY_GUIDE.txt`
- Absolute path at snapshot time: `/home/hurairahengg/Documents/Jinni Zero/STRATEGY_GUIDE.txt`
- Size bytes: `20612`
- SHA256: `d3c24e95ee404c4d3e6fba51364bc0d7a4908257b3c793b23c14bb6883fd2ec8`
- Guessed MIME type: `text/plain`
- Guessed encoding: `unknown`

```text
STRATEGY GUIDE
================

Purpose
-------
This guide explains how to create new Python strategy modules that plug into the strategy-loader backtester system.

It is written for:
- human developers
- AI coding agents
- future maintainers of the project

The goal is simple:
1. Put a strategy file in the correct folder.
2. Define the required strategy class and metadata.
3. Request the indicators you need.
4. Implement entry/exit logic in the expected format.
5. Let the engine handle execution, state management, commissions, sizing, and reporting.


1) How the Strategy Plugin System Works
---------------------------------------

The project uses a plugin architecture for strategies.

Core idea:
- strategy logic lives in separate Python modules
- the loader discovers available strategy files automatically
- the frontend requests the list of available strategies from the backend
- when the user selects a strategy in the Backtest tab, the frontend loads that strategy's metadata + parameter schema
- the frontend builds the parameter editor automatically
- the engine executes the selected strategy bar-by-bar

Main folders
------------
Expected backend layout:

backend/
  strategy_loader.py
  strategy_api.py
  engine_core.py
  strategies/
    __init__.py
    base.py
    ema_hma_trend.py
    breakout_retest.py
    your_new_strategy.py

Important locations:
- backend/strategies/ = all strategy plugins live here
- backend/strategies/base.py = shared base class / required interface
- backend/strategy_loader.py = discovers and loads strategy classes
- backend/strategy_api.py = API endpoints for listing strategies and running them
- backend/engine_core.py = reusable backtest engine

How discovery works
-------------------
The loader scans backend/strategies/ for Python modules.
It skips files such as:
- base.py
- __init__.py
- private files starting with underscore

Each valid module is imported.
The loader searches the module for classes that inherit from BaseStrategy.
Those classes are instantiated and registered by strategy_id.

How a strategy is selected from the frontend
--------------------------------------------
Typical flow:
1. Frontend calls GET /api/strategies
2. Backend returns available strategies (id, name, description, metadata)
3. User chooses a strategy in Load Strategy Mode
4. Frontend calls GET /api/strategy/<id>
5. Backend returns full metadata + parameter schema + engine schema/defaults
6. Frontend auto-renders the parameter editor
7. User clicks Run Backtest
8. Frontend sends POST /api/backtest/run with:
   - strategy_id
   - strategy parameters
   - engine settings
   - dataset config
   - capital / commission config
9. Backend loads bars, precomputes indicators, runs the engine, and returns results


2) Strategy File Structure
--------------------------

Where to put the file
---------------------
Create the strategy as a separate Python file inside:

backend/strategies/

Example:
backend/strategies/my_strategy.py

Required imports
----------------
At minimum, import the base strategy class:

from backend.strategies.base import BaseStrategy

You may also import:
- typing helpers
- math
- any safe standard-library utilities

Avoid importing heavy frameworks unless truly necessary.

Class naming convention
-----------------------
Use a clear class name that ends with Strategy.
Examples:
- MyStrategy
- EmaHmaTrendStrategy
- BreakoutRetestStrategy

The file name does not have to exactly match the class name, but keeping them aligned is strongly recommended.

Recommended file pattern
------------------------
Example:

backend/strategies/my_strategy.py

class MyStrategy(BaseStrategy):
    strategy_id = "my_strategy"
    name = "My Strategy"
    description = "Short description"
    parameters = {...}
    indicators_required = [...]

    def on_bar(self, i, bar, indicators, state, position, bars, params):
        ...
        return {}

How a strategy is registered
----------------------------
You do NOT manually register the strategy in a central list.
The loader discovers it automatically if:
- the file is in backend/strategies/
- the module imports successfully
- the class inherits from BaseStrategy
- the class defines a valid strategy_id


3) Required Strategy Interface / Methods
----------------------------------------

Every strategy must inherit from BaseStrategy.

Required class attributes
-------------------------
These are expected on the strategy class:

1. strategy_id
   Unique machine-friendly ID.
   Example:
   strategy_id = "ema_hma_trend"

2. name
   Human-friendly display name.
   Example:
   name = "EMA + HMA Trend Pullback"

3. description
   Plain English summary shown in the frontend.

4. parameters
   Parameter schema used by the frontend to auto-build the editor.

5. indicators_required
   List describing which indicators must be precomputed before the engine runs.

Main required method
--------------------
The key method is:

on_bar(self, i, bar, indicators, state, position, bars, params)

This method is called sequentially for each bar.

Expected parameters:
- i
  Current bar index (integer)

- bar
  Current bar dictionary

- indicators
  Dictionary containing:
  - current = current indicator values for this bar
  - series = full precomputed arrays for each requested indicator

- state
  Mutable strategy/engine state dictionary
  Use this carefully for tracking custom state between bars

- position
  Current open position view, or None if flat
  This is a read-only view intended for strategy logic decisions

- bars
  Full bar list (historical + current)
  Never use future bars beyond index i

- params
  Validated strategy parameters after defaults/min/max/type processing

Return value
------------
on_bar should return a dictionary describing an action, or an empty dictionary if no action is needed.

Typical return keys:
- enter
- entry_price
- stop_loss
- take_profit
- entry_reason
- exit
- exit_price
- exit_reason

Allowed action examples are documented later in this guide.

Optional methods
----------------
Depending on your BaseStrategy implementation, the following hooks may also exist:

- validate_parameters(...)
  Usually inherited from BaseStrategy
  Validates and merges defaults

- build_indicators_required(params)
  Usually inherited from BaseStrategy
  Resolves dynamic indicator periods from parameter references

- engine_parameter_overrides(params)
  Optional hook that lets a strategy suggest engine defaults
  Example uses:
  - enable trailing by MA
  - default fixed-R take profit
  - default stacking behavior

If your base class supports engine_parameter_overrides, only use it to suggest defaults.
Do not try to bypass the engine.


4) Available Data Fields Per Bar
--------------------------------

Each bar passed into the strategy is a dictionary.
Expected bar fields:

- time
  Numeric timestamp
  Usually epoch seconds (or normalized server timestamp)

- open
  Open price

- high
  High price

- low
  Low price

- close
  Close price

- volume
  Volume value if available

Possible internal field:
- index
  The engine may internally add the current bar index when running.
  Do not rely on this unless your current engine explicitly provides it.

Example bar
-----------
{
  "time": 1712345678,
  "open": 18345.25,
  "high": 18348.75,
  "low": 18344.50,
  "close": 18347.00,
  "volume": 120.0
}

Best practice
-------------
Use the current bar only through:
- bar["open"]
- bar["high"]
- bar["low"]
- bar["close"]
- bar["volume"]

When using bars history, only access:
- bars[:i+1]
- bars[i]
- bars[i-1]
- bars[max(0, i-lookback):i+1]

Never read bars after i.


5) Available Indicators + How to Request Them
---------------------------------------------

Supported indicators
--------------------
The engine may support these indicator kinds (depending on your current engine implementation):

- SMA
- EMA
- WMA
- HMA
- VWAP
- CHOPPINESS
- HIGHEST_HIGH
- LOWEST_LOW

How to request indicators
-------------------------
Use indicators_required on the strategy class.

Example:
indicators_required = [
    {"key": "fast_ema", "kind": "EMA", "source": "close", "period_param": "fast_ema"},
    {"key": "trend_hma", "kind": "HMA", "source": "close", "period_param": "trend_hma"},
]

Fields
------
- key
  Name used inside the strategy to read the indicator

- kind
  Indicator type, for example EMA or HMA

- source
  Which bar field to use, usually one of:
  - close
  - open
  - high
  - low

- period
  Fixed lookback period

- period_param
  Name of a strategy parameter whose value becomes the period

Fixed-period example
--------------------
indicators_required = [
    {"key": "vwap", "kind": "VWAP", "source": "close"},
    {"key": "slow_sma", "kind": "SMA", "source": "close", "period": 200},
]

How to read indicators in on_bar
--------------------------------
Current values:

fast = indicators["current"].get("fast_ema")
trend = indicators["current"].get("trend_hma")

Full series:

fast_series = indicators["series"]["fast_ema"]
prev_fast = fast_series[i - 1] if i > 0 else None

Best practice
-------------
Always guard against None values.
Indicator arrays often contain None until enough historical bars exist.

Example:
if fast is None or trend is None:
    return {}


6) Trade Engine Constraints (IMPORTANT)
---------------------------------------

These rules matter. If you ignore them, your strategy will produce wrong or unstable results.

Single position by default
--------------------------
The engine assumes one open position by default.
Do not assume you can open multiple independent positions unless the engine + strategy explicitly allow stacking or pyramiding.

Bar-by-bar sequential execution
-------------------------------
The engine runs in order, one bar at a time.
The strategy should behave like this:
- observe current bar and past data only
- make a decision
- engine handles the resulting order/action according to execution rules

Do not write strategy logic that depends on future candles.

Bar-close decision model
------------------------
Most strategies should treat the signal as being decided on the current bar close.
Be consistent.
If your engine is configured for bar-close logic, do not simulate intrabar hindsight.

Entry timing
------------
Use the project convention that is currently implemented.
Many systems signal on one bar and execute on the next bar open.
Some simplified versions may enter at the current close.

Important:
When writing a new strategy, check the current engine behavior and follow it consistently.
Do not mix entry-at-close and entry-at-next-open assumptions inside the same project.

Exits
-----
The engine may support universal exit management, such as:
- fixed SL points
- fixed TP points
- fixed R take profit
- last-bar low/high stop
- trailing stop by points
- trailing stop by MA
- break-even after +X R
- partial take profit
- scaling out at R levels

A strategy may:
- provide explicit stop_loss / take_profit in the returned action
- or rely on engine-level exit configuration

Commission handling
-------------------
The engine handles commission.
Typical supported models:
- flat per trade
- per side
- per contract

Do not manually subtract commission inside the strategy.

Position sizing
---------------
The engine may support:
- fixed lot size
- percent risk sizing based on stop distance and account balance

Do not calculate final PnL inside the strategy.
That is the engine's job.

R-multiple calculation
----------------------
R is usually based on the initial stop distance.
Typical formula:

For long:
R = (exit_price - entry_price) / (entry_price - stop_loss)

For short:
R = (entry_price - exit_price) / (stop_loss - entry_price)

If the engine already computes R internally, do not override it in strategy logic.

Scaling rules
-------------
If scaling in/out is enabled at the engine level:
- treat trade slices carefully
- the engine may output multiple trade records for a single position group
- do not assume one entry equals one final exit record


7) Strategy Output / Actions
----------------------------

A strategy should return a dictionary.
If nothing should happen, return:

{}

Common action keys
------------------

Enter long
----------
{
  "enter": "long",
  "entry_price": bar["close"],
  "stop_loss": 18340.0,
  "take_profit": 18360.0,
  "entry_reason": "ema_reclaim"
}

Enter short
-----------
{
  "enter": "short",
  "entry_price": bar["close"],
  "stop_loss": 18355.0,
  "take_profit": 18330.0,
  "entry_reason": "breakdown"
}

Close existing trade
--------------------
{
  "exit": True,
  "exit_price": bar["close"],
  "exit_reason": "trend_invalidated"
}

Minimal exit signal
-------------------
If the engine is allowed to use the current close by default:

{
  "exit": True,
  "exit_reason": "signal_exit"
}

Notes
-----
- enter should be either "long" or "short"
- exit should be True
- entry_price / exit_price should be numeric if supplied
- stop_loss / take_profit should be numeric or omitted
- entry_reason / exit_reason should be plain strings for auditability

Trailing / break-even / scaling from strategy
---------------------------------------------
If your engine supports custom runtime overrides, use the supported project pattern.
If not, do NOT invent custom keys.
Only return keys the engine understands.

Best practice:
- let engine settings handle trailing / break-even / partials
- keep strategy output focused on directional logic + optional SL/TP hints


8) What Is NOT Allowed / Common Mistakes
----------------------------------------

1. Repainting / using future bars
---------------------------------
Wrong:
future_close = bars[i + 1]["close"]

This invalidates backtest results.

2. Ignoring None indicator values
---------------------------------
Indicators usually return None before enough history exists.
Always guard against missing values.

3. Conflicting signals on the same bar
--------------------------------------
Do not return both:
- enter long
- enter short

Do not return both enter and exit for different intentions unless your engine explicitly supports that exact behavior.

4. Modifying global state unsafely
----------------------------------
Avoid module-level mutable variables.
Use the provided state object if you need per-run memory.

Bad:
last_signal = "long"   # global module variable

Better:
state.setdefault("my_strategy", {})
state["my_strategy"]["last_signal"] = "long"

5. Assuming position is always present
--------------------------------------
When flat, position may be None.
Always check first.

6. Assuming position is a custom object if current engine passes a dict
-----------------------------------------------------------------------
Use the current documented project format consistently.
If the engine passes a dict-like position view, read it as a dict.

7. Wrong bar index for lookbacks
--------------------------------
Be careful with ranges.
Example safe pattern:
start = max(0, i - lookback + 1)
window = bars[start:i+1]

8. Writing strategy-level PnL accounting
----------------------------------------
Do not update balance, commission, or account equity inside the strategy.
That belongs to the engine.

9. Returning unsupported keys
-----------------------------
Do not invent random action keys unless the engine explicitly supports them.
Stick to documented action fields.

10. Forgetting unique strategy_id
---------------------------------
If two strategies use the same strategy_id, loader registration will fail or become ambiguous.


9) Example Strategy Template
----------------------------

Below is a complete minimal example.
It requests indicators, defines parameters, enters trades, and exits trades.

Example file: backend/strategies/example_cross_strategy.py

from backend.strategies.base import BaseStrategy


class ExampleCrossStrategy(BaseStrategy):
    strategy_id = "example_cross_strategy"
    name = "Example EMA Trend Cross"
    description = "Simple example strategy using EMA + HMA trend confirmation."

    parameters = {
        "_group_filters": {
            "type": "group",
            "label": "Filters",
        },
        "fast_ema": {
            "type": "number",
            "label": "Fast EMA",
            "default": 20,
            "min": 2,
            "max": 500,
            "step": 1,
            "integer": True,
            "group": "Filters",
        },
        "trend_hma": {
            "type": "number",
            "label": "Trend HMA",
            "default": 55,
            "min": 2,
            "max": 500,
            "step": 1,
            "integer": True,
            "group": "Filters",
        },
        "reward_r": {
            "type": "number",
            "label": "Reward R",
            "default": 2.0,
            "min": 0.5,
            "max": 10.0,
            "step": 0.25,
            "group": "Filters",
        },
        "stop_buffer": {
            "type": "number",
            "label": "Stop Buffer",
            "default": 1.0,
            "min": 0.0,
            "max": 20.0,
            "step": 0.25,
            "group": "Filters",
        },
    }

    indicators_required = [
        {"key": "fast_ema", "kind": "EMA", "source": "close", "period_param": "fast_ema"},
        {"key": "trend_hma", "kind": "HMA", "source": "close", "period_param": "trend_hma"},
    ]

    def on_bar(self, i, bar, indicators, state, position, bars, params):
        if i < 2:
            return {}

        fast = indicators["current"].get("fast_ema")
        trend = indicators["current"].get("trend_hma")
        prev_fast = indicators["series"]["fast_ema"][i - 1]
        prev_trend = indicators["series"]["trend_hma"][i - 1]

        if fast is None or trend is None or prev_fast is None or prev_trend is None:
            return {}

        close_ = bar["close"]
        prev_close = bars[i - 1]["close"]

        # Exit logic first
        if position:
            if position["direction"] == "long" and close_ < fast:
                return {
                    "exit": True,
                    "exit_reason": "close_below_fast_ema"
                }

            if position["direction"] == "short" and close_ > fast:
                return {
                    "exit": True,
                    "exit_reason": "close_above_fast_ema"
                }

            return {}

        bullish_trend = close_ > trend and fast > trend
        bearish_trend = close_ < trend and fast < trend

        crossed_up = prev_close <= prev_fast and close_ > fast
        crossed_down = prev_close >= prev_fast and close_ < fast

        if bullish_trend and crossed_up:
            stop_loss = bar["low"] - params["stop_buffer"]
            risk = max(0.01, close_ - stop_loss)
            take_profit = close_ + risk * params["reward_r"]
            return {
                "enter": "long",
                "entry_price": close_,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "entry_reason": "ema_cross_long"
            }

        if bearish_trend and crossed_down:
            stop_loss = bar["high"] + params["stop_buffer"]
            risk = max(0.01, stop_loss - close_)
            take_profit = close_ - risk * params["reward_r"]
            return {
                "enter": "short",
                "entry_price": close_,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "entry_reason": "ema_cross_short"
            }

        return {}


Quick Checklist
---------------

Before saving a new strategy, verify all of this:

[ ] File is inside backend/strategies/
[ ] Class inherits from BaseStrategy
[ ] strategy_id is unique
[ ] name and description are filled in
[ ] parameters schema is valid
[ ] indicators_required only requests supported indicators
[ ] on_bar(...) exists
[ ] on_bar returns only supported action keys
[ ] Strategy never reads future bars
[ ] Strategy guards against None indicator values
[ ] Strategy does not modify account balance directly
[ ] Strategy does not use unsupported/global side effects
[ ] Strategy handles flat vs open-position logic clearly
[ ] Entry and exit reasons are human-readable
[ ] Lookbacks use safe index ranges

Recommended workflow
--------------------
1. Copy the example template.
2. Rename the class and strategy_id.
3. Adjust parameters.
4. Request required indicators.
5. Implement long logic.
6. Implement short logic.
7. Implement exit logic.
8. Run the backtester on a small dataset first.
9. Check the trade log and metrics.
10. Only then test larger datasets.

End of guide.
```

---

## FILE: `test.py`

- Relative path: `test.py`
- Absolute path at snapshot time: `/home/hurairahengg/Documents/Jinni Zero/test.py`
- Size bytes: `13204`
- SHA256: `3429a38272d0c9cf2a2222e66cdea55db12fc5a05be67213e44e8c4d56a46550`
- Guessed MIME type: `text/x-python`
- Guessed encoding: `unknown`

```python
import json
import os
from collections import deque
from json import JSONDecodeError

# ── CONFIG ──────────────────────────────────────────────────────────
INPUT_FILE = os.path.join("data", "2pt.json")

CHUNK_SIZE = 1024 * 1024       # bytes to read at a time from JSON file
PATTERNS = (2, 3, 4, 5)        # streak lengths to test

# How many LAST bars to analyze:
#   None  -> analyze full file
#   50000 -> analyze only last 50,000 bars
LOOKBACK_BARS = None

# Print progress every N scanned/analyzed bars
STATUS_EVERY = 100000

# Optional small preview of current progress message
PRINT_STARTUP_INFO = True


# ── STREAM JSON ARRAY WITHOUT LOADING WHOLE FILE ────────────────────
def iter_json_array(path, chunk_size=CHUNK_SIZE):
    """
    Streams objects from a large JSON array file like:
    [
      {...},
      {...},
      ...
    ]
    without loading the full file into RAM.
    """
    decoder = json.JSONDecoder()
    buf = ""
    started = False
    eof = False

    with open(path, "r", encoding="utf-8") as f:
        while True:
            # keep buffer topped up
            if not eof and len(buf) < chunk_size // 2:
                more = f.read(chunk_size)
                if more:
                    buf += more
                else:
                    eof = True

            if not started:
                buf = buf.lstrip()
                if not buf:
                    if eof:
                        return
                    continue

                if buf[0] != "[":
                    raise ValueError("Expected JSON array starting with '['")

                buf = buf[1:]
                started = True

            buf = buf.lstrip()

            if not buf:
                if eof:
                    raise ValueError("Unexpected EOF while parsing JSON array")
                continue

            if buf[0] == "]":
                return

            if buf[0] == ",":
                buf = buf[1:]
                continue

            try:
                obj, idx = decoder.raw_decode(buf)
            except JSONDecodeError:
                if eof:
                    raise
                more = f.read(chunk_size)
                if more:
                    buf += more
                else:
                    eof = True
                continue

            yield obj
            buf = buf[idx:]


# ── HELPERS ──────────────────────────────────────────────────────────
def bar_dir(bar):
    """
    Returns:
        1  = bullish
       -1  = bearish
        0  = neutral/doji
    """
    o = float(bar["open"])
    c = float(bar["close"])

    if c > o:
        return 1
    elif c < o:
        return -1
    return 0


def make_stats():
    return {
        "occurrences": 0,
        "next_same": 0,
        "next_opposite": 0,
        "next_neutral": 0,
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "flats": 0,
        "total_pnl_points": 0.0,
        "avg_pnl_points": 0.0,
        "win_rate": 0.0,
        "same_probability": 0.0,
        "opposite_probability": 0.0,
    }


def finalize_stats(s):
    if s["occurrences"] > 0:
        s["same_probability"] = (s["next_same"] / s["occurrences"]) * 100.0
        s["opposite_probability"] = (s["next_opposite"] / s["occurrences"]) * 100.0

    if s["trades"] > 0:
        s["avg_pnl_points"] = s["total_pnl_points"] / s["trades"]
        s["win_rate"] = (s["wins"] / s["trades"]) * 100.0

    return s


def combine_stats(a, b):
    c = make_stats()
    for k in c.keys():
        if k in ("avg_pnl_points", "win_rate", "same_probability", "opposite_probability"):
            continue
        c[k] = a[k] + b[k]
    return finalize_stats(c)


def init_results(patterns):
    return {
        n: {
            1: make_stats(),    # bullish streak stats
            -1: make_stats(),   # bearish streak stats
        }
        for n in patterns
    }


def finalize_results(results, patterns):
    for n in patterns:
        finalize_stats(results[n][1])
        finalize_stats(results[n][-1])
        results[n]["combined"] = combine_stats(results[n][1], results[n][-1])
    return results


# ── CORE ANALYSIS LOGIC ──────────────────────────────────────────────
def process_bar_stream(bar_iterable, patterns, status_every=STATUS_EVERY, phase_name="analysis"):
    """
    Core streak simulation over any iterable of bars.
    """
    results = init_results(patterns)

    history_dirs = deque(maxlen=max(patterns))
    prev_bar = None

    total_bars = 0
    bullish_bars = 0
    bearish_bars = 0
    neutral_bars = 0

    for bar in bar_iterable:
        total_bars += 1
        cur_dir = bar_dir(bar)

        if cur_dir == 1:
            bullish_bars += 1
        elif cur_dir == -1:
            bearish_bars += 1
        else:
            neutral_bars += 1

        # current bar is the "next bar" after a streak ending on prev_bar
        if prev_bar is not None:
            hist_list = list(history_dirs)

            for n in patterns:
                if len(hist_list) < n:
                    continue

                streak = hist_list[-n:]
                first = streak[0]

                if first == 0:
                    continue

                if any(x != first for x in streak):
                    continue

                streak_dir = first
                s = results[n][streak_dir]

                s["occurrences"] += 1

                # what did next bar do?
                if cur_dir == streak_dir:
                    s["next_same"] += 1
                elif cur_dir == -streak_dir:
                    s["next_opposite"] += 1
                else:
                    s["next_neutral"] += 1

                # simulation:
                # enter at close of prev_bar (Nth streak bar)
                # exit at close of current bar (next bar)
                entry = float(prev_bar["close"])
                exit_ = float(bar["close"])

                pnl = (exit_ - entry) * streak_dir
                # bullish streak => streak_dir = +1 => long
                # bearish streak => streak_dir = -1 => short

                s["trades"] += 1
                s["total_pnl_points"] += pnl

                if pnl > 0:
                    s["wins"] += 1
                elif pnl < 0:
                    s["losses"] += 1
                else:
                    s["flats"] += 1

        history_dirs.append(cur_dir)
        prev_bar = bar

        if status_every and total_bars % status_every == 0:
            print(
                f"[{phase_name}] processed bars: {total_bars:,} | "
                f"bull: {bullish_bars:,} | bear: {bearish_bars:,} | neutral: {neutral_bars:,}"
            )

    finalize_results(results, patterns)

    return {
        "total_bars": total_bars,
        "bullish_bars": bullish_bars,
        "bearish_bars": bearish_bars,
        "neutral_bars": neutral_bars,
        "results": results,
    }


# ── FULL FILE ANALYSIS (ONE PASS, STREAMED) ──────────────────────────
def analyze_full_file(path, patterns, status_every=STATUS_EVERY):
    if PRINT_STARTUP_INFO:
        print(f"[startup] mode = FULL FILE")
        print(f"[startup] input = {path}")
        print(f"[startup] patterns = {patterns}")
        print(f"[startup] status_every = {status_every:,}")
        print()

    return process_bar_stream(
        bar_iterable=iter_json_array(path),
        patterns=patterns,
        status_every=status_every,
        phase_name="full-scan",
    )


# ── LAST X BARS ANALYSIS (STREAM TO TAIL DEQUE, THEN ANALYZE) ────────
def analyze_last_n_bars(path, patterns, lookback_bars, status_every=STATUS_EVERY):
    if PRINT_STARTUP_INFO:
        print(f"[startup] mode = LAST N BARS")
        print(f"[startup] input = {path}")
        print(f"[startup] patterns = {patterns}")
        print(f"[startup] lookback_bars = {lookback_bars:,}")
        print(f"[startup] status_every = {status_every:,}")
        print()

    tail = deque(maxlen=lookback_bars)
    scanned = 0

    print(f"[tail-scan] building last {lookback_bars:,} bars buffer...")

    for bar in iter_json_array(path):
        tail.append(bar)
        scanned += 1

        if status_every and scanned % status_every == 0:
            print(
                f"[tail-scan] scanned bars: {scanned:,} | "
                f"currently kept in memory: {len(tail):,}"
            )

    print(
        f"[tail-scan] done. total scanned: {scanned:,} | "
        f"bars kept for analysis: {len(tail):,}"
    )
    print()

    return process_bar_stream(
        bar_iterable=tail,
        patterns=patterns,
        status_every=status_every,
        phase_name="last-n-analysis",
    )


# ── PUBLIC RUNNER ────────────────────────────────────────────────────
def run_streak_sim(path=INPUT_FILE, patterns=PATTERNS, lookback_bars=LOOKBACK_BARS, status_every=STATUS_EVERY):
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")

    if not patterns:
        raise ValueError("PATTERNS cannot be empty")

    patterns = tuple(sorted(set(int(x) for x in patterns if int(x) > 0)))
    if not patterns:
        raise ValueError("PATTERNS must contain positive integers")

    if lookback_bars is None:
        analysis = analyze_full_file(path, patterns, status_every=status_every)
        analyzed_scope = "FULL FILE"
    else:
        lookback_bars = int(lookback_bars)
        if lookback_bars <= 0:
            raise ValueError("LOOKBACK_BARS must be None or a positive integer")
        analysis = analyze_last_n_bars(path, patterns, lookback_bars, status_every=status_every)
        analyzed_scope = f"LAST {lookback_bars:,} BARS"

    return {
        "file": path,
        "scope": analyzed_scope,
        "patterns": patterns,
        "total_bars": analysis["total_bars"],
        "bullish_bars": analysis["bullish_bars"],
        "bearish_bars": analysis["bearish_bars"],
        "neutral_bars": analysis["neutral_bars"],
        "results": analysis["results"],
    }


# ── PRINT REPORT ─────────────────────────────────────────────────────
def print_report(report):
    print()
    print("=" * 90)
    print("2PT RANGE BAR STREAK SIMULATION")
    print("=" * 90)
    print(f"File         : {report['file']}")
    print(f"Scope        : {report['scope']}")
    print(f"Patterns     : {report['patterns']}")
    print(f"Total bars   : {report['total_bars']:,}")
    print(f"Bullish bars : {report['bullish_bars']:,}")
    print(f"Bearish bars : {report['bearish_bars']:,}")
    print(f"Neutral bars : {report['neutral_bars']:,}")
    print()
    print("Logic:")
    print("  - After N consecutive bullish bars -> test whether next bar is bullish again")
    print("  - After N consecutive bearish bars -> test whether next bar is bearish again")
    print("  - Simulation:")
    print("      bullish streak -> LONG on close of Nth bar, exit on next close")
    print("      bearish streak -> SHORT on close of Nth bar, exit on next close")
    print()

    for n in report["patterns"]:
        print("-" * 90)
        print(f"AFTER {n} CONSECUTIVE BARS")
        print("-" * 90)

        for label, key in (
            ("Bullish streak", 1),
            ("Bearish streak", -1),
            ("Combined", "combined"),
        ):
            s = report["results"][n][key]

            print(f"{label}:")
            print(f"  occurrences         : {s['occurrences']:,}")
            print(f"  next same           : {s['next_same']:,} ({s['same_probability']:.2f}%)")
            print(f"  next opposite       : {s['next_opposite']:,} ({s['opposite_probability']:.2f}%)")
            print(f"  next neutral        : {s['next_neutral']:,}")
            print(f"  trades simulated    : {s['trades']:,}")
            print(f"  wins/losses/flats   : {s['wins']:,} / {s['losses']:,} / {s['flats']:,}")
            print(f"  win rate            : {s['win_rate']:.2f}%")
            print(f"  total pnl (points)  : {s['total_pnl_points']:.2f}")
            print(f"  avg pnl per trade   : {s['avg_pnl_points']:.4f}")
            print()

    print("=" * 90)
    print("DONE")
    print("=" * 90)


# ── MAIN ─────────────────────────────────────────────────────────────
def main():
    report = run_streak_sim(
        path=INPUT_FILE,
        patterns=PATTERNS,
        lookback_bars=LOOKBACK_BARS,
        status_every=STATUS_EVERY,
    )
    print_report(report)


if __name__ == "__main__":
    main()
```
