# Repository Snapshot - Part 5 of 5

- Root folder: `/home/hurairahengg/Documents/Jinni Zero`
- You know my wholle Jinjnibacktester simulator thign whre ther is a UI bascially and then i can see  charst and stuff when i need to run simulatiosn liek i send simulatio nto my flask backend server it runs sims and then shows stast and stuff and i can load strategy and shit for now take a look we will be doing bug fixes and some validation and shit. udnerrtsnad each code and its role how it works and keep in ir conetxt i will ask u exactly wha tto do later code later duinerstood
- Total files indexed: `24`
- Files in this chunk: `5`
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
backend/strategies/idk.py
backend/strategies/JinniContiniumV2.py
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

## Files In This Chunk - Part 5

```text
backend/shared.py
backend/strategy_api.py
bars/range_bars.py
js/strategy_loader.js
test.py
```

## File Contents


---

## FILE: `backend/shared.py`

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

## FILE: `backend/strategy_api.py`

```python
"""
JINNI ZERO — Strategy API Routes
"""
from __future__ import annotations

import json
import os
import time as _time
from datetime import datetime, timezone

from flask import Blueprint, Response, jsonify, request, stream_with_context

from backend.engine_core import BacktestEngine
from backend.shared import clean_for_json
from backend.strategy_loader import get_strategy, list_strategy_metadata, validate_lookback

strategy_api = Blueprint("strategy_api", __name__)
DATA_DIR = "data"


def _parse_datetime_param(val):
    if not val or not isinstance(val, str) or not val.strip():
        return None
    val = val.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(val, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue
    return None


def load_bars(range_pt, bar_range, symbol="NQ", start_date=None, end_date=None):
    path = os.path.join(DATA_DIR, symbol, f"{int(range_pt)}pt.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Dataset not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        bars = json.load(f)

    start_ts = _parse_datetime_param(start_date)
    end_ts = _parse_datetime_param(end_date)
    if start_ts:
        bars = [b for b in bars if int(b["time"]) >= start_ts]
    if end_ts:
        bars = [b for b in bars if int(b["time"]) <= end_ts]
    if bar_range and int(bar_range) > 0:
        bars = bars[-int(bar_range):]

    normalized = []
    last_time = None
    for b in bars:
        t = int(b["time"])
        if last_time is not None and t <= last_time:
            t = last_time + 1
        last_time = t
        normalized.append({
            "time": t,
            "open": float(b["open"]),
            "high": float(b["high"]),
            "low": float(b["low"]),
            "close": float(b["close"]),
            "volume": float(b.get("volume", 0) or 0),
        })
    return normalized


def _setup_engine(payload):
    """Shared setup for both streaming and non-streaming endpoints."""
    strategy_id = payload.get("strategy_id")
    if not strategy_id:
        raise ValueError("Missing strategy_id")

    strategy = get_strategy(strategy_id)

    bars = load_bars(
        range_pt=int(payload.get("range", 10)),
        bar_range=int(payload.get("bar_range", 1000)),
        symbol=payload.get("symbol", "NQ"),
        start_date=payload.get("start_date"),
        end_date=payload.get("end_date"),
    )

    lookback_override = int(payload.get("lookback_override", 0) or 0)
    validate_lookback(strategy, len(bars), lookback_override)

    if len(bars) < 5:
        raise ValueError("Insufficient data")

    return BacktestEngine(bars=bars, strategy=strategy, payload=payload)


@strategy_api.get("/strategies")
def strategies_list():
    return jsonify(list_strategy_metadata()), 200


@strategy_api.get("/strategy/<strategy_id>")
def strategy_detail(strategy_id):
    try:
        strategy = get_strategy(strategy_id)
        return jsonify(strategy.get_metadata()), 200
    except KeyError as e:
        return jsonify({"error": str(e)}), 404


@strategy_api.post("/backtest/run")
def strategy_backtest_run():
    """Non-streaming: single JSON response."""
    try:
        route_t0 = _time.perf_counter()
        payload = request.get_json(force=True) or {}

        engine = _setup_engine(payload)
        result = engine.run()

        json_t0 = _time.perf_counter()
        response_body = json.dumps(result)
        json_t1 = _time.perf_counter()

        payload_kb = len(response_body) / 1024
        total_ms = (json_t1 - route_t0) * 1000

        print(f"  [ROUTE TIMING] json={((json_t1-json_t0)*1000):.1f}ms "
              f"payload={payload_kb:.1f}KB route_total={total_ms:.1f}ms")

        return Response(response_body, mimetype="application/json"), 200

    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except KeyError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@strategy_api.post("/backtest/run/stream")
def strategy_backtest_run_stream():
    """Streaming: NDJSON progress + result. Matches legacy /api/backtest/stream."""
    try:
        payload = request.get_json(force=True) or {}
        engine = _setup_engine(payload)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except KeyError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    def generate():
        try:
            for msg in engine.run_streaming():
                yield json.dumps(clean_for_json(msg)) + "\n"
        except Exception as e:
            import traceback; traceback.print_exc()
            yield json.dumps({"type": "error", "error": str(e)}) + "\n"

    return Response(
        stream_with_context(generate()),
        mimetype='application/x-ndjson',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Access-Control-Allow-Origin': '*',
        },
    )
```

---

## FILE: `bars/range_bars.py`

```python
"""
range_bars.py  ─  Koko Candles Generator [STREAMING]
─────────────────────────────────────────────────────────────────────
Lists folders inside data/
You select which folder to process.
Auto-detects CSV file inside selected folder.

Builds Koko Candles (grid-anchored range bars):

- All opens/closes snap to a fixed price grid (multiples of brick size).
- Continuation: price reaches next grid level in trend direction → 1-brick bar.
- Reversal: price must move rev_bricks * brick_size against trend to trigger.
  ONE bar is emitted for the reversal (no cascading).

Clean mode OFF (default):
    Reversal bar body = rev_bricks * brick_size (e.g. 2x body).
    Example (brick=5, bull, close=1010):
        price ≤ 1000 → bar open=1010, close=1000 (2x body)

Clean mode ON:
    Reversal bar body = 1 brick (same as continuation).
    The excess is pushed into the wick so the open sits 1 brick from close.
    Example (brick=5, bull, close=1010):
        price ≤ 1000 → bar open=1005, close=1000, high=1010 (wick shows origin)

Dynamic range setup:
    Ask start range, interval, end range.

Reversal bricks:
    How many bricks against trend before a reversal triggers.
    Default is 2.

Bar limit:
    Max bars to generate per range. Empty = full dataset.

Supported tick formats (auto-detected):

    Whitespace-separated:
        2026.01.01  23:05:00.018  4328.401  4328.438  ...  6

    Comma-separated:
        2023.01.02 23:00:00.374,11047.869,11051.301

Usage:
    python range_bars.py
─────────────────────────────────────────────────────────────────────
"""

import json
import os
from datetime import datetime
from decimal import Decimal, InvalidOperation


# ── CONFIG ──────────────────────────────────────────────────────────
DATA_DIR = "data"
INCLUDE_PARTIAL_BAR = True
CHUNK_ROWS = 50000
ASSUME_INPUT_SORTED = True


# ── INPUT / SELECTION HELPERS ────────────────────────────────────────
def _format_decimal(value):
    d = Decimal(str(value))
    s = format(d.normalize(), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _decimal_places(raw):
    raw = raw.strip()
    if "." not in raw:
        return 0
    return len(raw.split(".", 1)[1])


def _parse_decimal(raw):
    raw = raw.strip()
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _ask_int_choice(prompt, min_value, max_value):
    while True:
        raw = input(prompt).strip()
        try:
            choice = int(raw)
        except ValueError:
            print(f"  ✗ Invalid number. Choose {min_value}-{max_value}.")
            continue
        if min_value <= choice <= max_value:
            return choice
        print(f"  ✗ Out of range. Choose {min_value}-{max_value}.")


def ask_bar_limit():
    while True:
        raw = input("\n  Max bars to generate (empty = full dataset): ").strip()
        if raw == "":
            return None
        try:
            n = int(raw)
        except ValueError:
            print("  ✗ Enter a valid integer or leave empty.")
            continue
        if n <= 0:
            print("  ✗ Must be greater than 0.")
            continue
        return n


def ask_reversal_bricks():
    """
    How many bricks against the trend before reversal triggers.
    No default — must be entered explicitly.
    """
    while True:
        raw = input("\n  Reversal bricks (e.g. 2, 3, 5.5): ").strip()
        if raw == "":
            print("  ✗ Required. Enter a number.")
            continue
        if raw.lower().endswith("x"):
            raw = raw[:-1].strip()
        try:
            val = float(raw)
        except ValueError:
            print("  ✗ Enter a valid number.")
            continue
        if val <= 0:
            print("  ✗ Must be greater than 0.")
            continue
        print(f"  → Reversal bricks: {val}")
        return val


def ask_clean_mode():
    """
    Clean mode: reversal bars have 1-brick body (same as continuation).
    The excess distance becomes a wick showing where price came from.
    """
    while True:
        raw = input("\n  Clean mode? (y/n, empty = n): ").strip().lower()
        if raw in ("", "n", "no"):
            return False
        if raw in ("y", "yes"):
            return True
        print("  ✗ Enter y or n.")


def ask_range_settings():
    while True:
        print("\n  Enter range settings:")
        start_raw = input("    Start range:    ").strip()
        interval_raw = input("    Interval:       ").strip()
        end_raw = input("    End range:      ").strip()

        start = _parse_decimal(start_raw)
        interval = _parse_decimal(interval_raw)
        end = _parse_decimal(end_raw)

        if start is None or interval is None or end is None:
            print("\n  ✗ Invalid decimal input. Enter all three again.")
            continue

        start_dp = _decimal_places(start_raw)
        interval_dp = _decimal_places(interval_raw)
        end_dp = _decimal_places(end_raw)

        if not (start_dp == interval_dp == end_dp):
            print("\n  ✗ Decimal places are not consistent.")
            print(f"    Start decimal places:    {start_dp}")
            print(f"    Interval decimal places: {interval_dp}")
            print(f"    End decimal places:      {end_dp}")
            print("    Enter all three again with matching decimal places.")
            print("    Example valid: 5.0, 2.5, 10.0")
            continue

        if start <= 0:
            print("\n  ✗ Start range must be greater than 0. Enter all three again.")
            continue

        if interval <= 0:
            print("\n  ✗ Interval must be greater than 0. Enter all three again.")
            continue

        if end < start:
            print("\n  ✗ End range must be greater than or equal to start range. Enter all three again.")
            continue

        diff = end - start

        if diff % interval != 0:
            print("\n  ✗ Interval does not land exactly on end range.")
            print(f"    Start:    {_format_decimal(start)}")
            print(f"    Interval: {_format_decimal(interval)}")
            print(f"    End:      {_format_decimal(end)}")
            print("    Enter all three again.")
            continue

        ranges = []
        current = start
        while current <= end:
            ranges.append(current)
            current += interval

        return ranges, start_dp


def select_data_folder():
    if not os.path.exists(DATA_DIR):
        print(f"\n  ✗ Data folder not found: {DATA_DIR}")
        print("    Create data/ and put your folders inside it.")
        return None

    folders = [
        name for name in os.listdir(DATA_DIR)
        if os.path.isdir(os.path.join(DATA_DIR, name))
    ]
    folders.sort()

    if not folders:
        print(f"\n  ✗ No folders found inside: {DATA_DIR}")
        print("    Example:")
        print("      data/NQ/nq.csv")
        print("      data/ES/es.csv")
        return None

    print("\n  Folders found inside data/:")
    for idx, folder in enumerate(folders, start=1):
        print(f"    {idx}. {folder}")

    choice = _ask_int_choice("\n  Select folder number: ", 1, len(folders))
    return os.path.join(DATA_DIR, folders[choice - 1])


def select_csv_file(folder_path):
    csv_files = [
        name for name in os.listdir(folder_path)
        if os.path.isfile(os.path.join(folder_path, name))
        and name.lower().endswith(".csv")
    ]
    csv_files.sort()

    if not csv_files:
        print(f"\n  ✗ No CSV file found inside: {folder_path}")
        return None

    if len(csv_files) == 1:
        return os.path.join(folder_path, csv_files[0])

    print(f"\n  Multiple CSV files found inside {folder_path}:")
    for idx, file_name in enumerate(csv_files, start=1):
        print(f"    {idx}. {file_name}")

    choice = _ask_int_choice("\n  Select CSV number: ", 1, len(csv_files))
    return os.path.join(folder_path, csv_files[choice - 1])


# ── BAR HELPER ───────────────────────────────────────────────────────
def make_bar(time_, open_, high_, low_, close_, volume_, price_decimals):
    return {
        "time": int(time_),
        "open": round(open_, price_decimals),
        "high": round(high_, price_decimals),
        "low": round(low_, price_decimals),
        "close": round(close_, price_decimals),
        "volume": round(volume_, 2),
    }


# ── TICK FORMAT DETECTION + PARSERS ──────────────────────────────────
def _detect_tick_format(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("<DATE>"):
                return "metatrader"
            if "," in line:
                return "comma"
            return "whitespace"
    return "whitespace"

def _parse_datetime(raw):
    for fmt in ("%Y.%m.%d %H:%M:%S.%f", "%Y.%m.%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _parse_tick_comma(fields):
    if len(fields) < 3:
        return None

    dt_raw = fields[0].strip()
    bid_raw = fields[1].strip()
    ask_raw = fields[2].strip()

    if not dt_raw or not bid_raw or not ask_raw:
        return None

    dt = _parse_datetime(dt_raw)
    if dt is None:
        return None

    try:
        bid = float(bid_raw)
        ask = float(ask_raw)
    except ValueError:
        return None

    price = (bid + ask) / 2.0

    volume = 0.0
    for i in (3, 4, 5):
        if i < len(fields):
            vol_raw = fields[i].strip()
            if vol_raw:
                try:
                    v = float(vol_raw)
                    if v > 0:
                        volume = v
                        break
                except ValueError:
                    continue

    return {"ts": int(dt.timestamp()), "price": price, "volume": volume}


def _parse_tick_whitespace(fields):
    if len(fields) < 4:
        return None

    date_raw = fields[0].strip()
    time_raw = fields[1].strip()
    bid_raw  = fields[2].strip()
    ask_raw  = fields[3].strip()

    if not date_raw or not time_raw or not bid_raw or not ask_raw:
        return None

    dt = _parse_datetime(f"{date_raw} {time_raw}")
    if dt is None:
        return None

    try:
        bid = float(bid_raw)
        ask = float(ask_raw)
    except ValueError:
        return None

    price = (bid + ask) / 2.0

    volume = 0.0
    if len(fields) >= 6:
        vol_raw = fields[5].strip()
        if vol_raw:
            try:
                volume = float(vol_raw)
            except ValueError:
                volume = 0.0

    return {"ts": int(dt.timestamp()), "price": price, "volume": volume}

def _parse_metatrader_header(header_line):
    """
    Parse MT5 tab-delimited header into column name → index map.
    Header example: <DATE>\t<TIME>\t<BID>\t<ASK>\t<LAST>\t<VOLUME>\t<FLAGS>
    """
    cols = [c.strip().strip("<>").upper() for c in header_line.split("\t")]
    return {name: idx for idx, name in enumerate(cols) if name}


def _parse_tick_metatrader(raw_line, col_map):
    """
    Parse a single MT5 tick line using TAB split + column mapping.
    Handles empty LAST, empty VOLUME, etc. correctly.
    Price = mid(bid, ask) if both present, else LAST, else whichever exists.
    """
    fields = raw_line.split("\t")

    def _get(col_name):
        idx = col_map.get(col_name)
        if idx is not None and idx < len(fields):
            return fields[idx].strip()
        return ""

    date_raw = _get("DATE")
    time_raw = _get("TIME")
    if not date_raw or not time_raw:
        return None

    dt = _parse_datetime(f"{date_raw} {time_raw}")
    if dt is None:
        return None

    # parse all price fields
    bid = ask = last = None
    bid_raw = _get("BID")
    ask_raw = _get("ASK")
    last_raw = _get("LAST")

    if bid_raw:
        try:
            bid = float(bid_raw)
        except ValueError:
            pass
    if ask_raw:
        try:
            ask = float(ask_raw)
        except ValueError:
            pass
    if last_raw:
        try:
            last = float(last_raw)
        except ValueError:
            pass

    # determine price: mid(bid,ask) → last → bid → ask
    if bid is not None and ask is not None:
        price = (bid + ask) / 2.0
    elif last is not None:
        price = last
    elif bid is not None:
        price = bid
    elif ask is not None:
        price = ask
    else:
        return None

    # volume — only from VOLUME column, never from FLAGS
    volume = 0.0
    vol_raw = _get("VOLUME")
    if vol_raw:
        try:
            v = float(vol_raw)
            if v > 0:
                volume = v
        except ValueError:
            pass

    return {"ts": int(dt.timestamp()), "price": price, "volume": volume}

def iter_ticks_in_chunks(path, chunk_rows=50000):
    fmt = _detect_tick_format(path)

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        col_map = None
        chunk = []

        for raw_line in f:
            stripped = raw_line.strip()
            if not stripped:
                continue

            # header line — parse column map for metatrader, skip otherwise
            if stripped.startswith("<DATE>"):
                if fmt == "metatrader":
                    col_map = _parse_metatrader_header(stripped)
                continue

            # parse tick based on format
            tick = None

            if fmt == "metatrader" and col_map is not None:
                tick = _parse_tick_metatrader(raw_line.rstrip("\n\r"), col_map)
            elif fmt == "comma":
                fields = stripped.split(",")
                tick = _parse_tick_comma(fields)
            else:
                fields = stripped.split()
                tick = _parse_tick_whitespace(fields)

            if tick is None:
                continue

            chunk.append(tick)
            if len(chunk) >= chunk_rows:
                yield chunk
                chunk = []

        if chunk:
            yield chunk

# ── KOKO CANDLE STREAMER ─────────────────────────────────────────────
class KokoCandleStreamer:
    """
    Koko Candles — grid-anchored range bars with single-bar reversals.

    Grid = all multiples of brick_size.

    CONTINUATION:
        Price reaches next grid level in trend direction (1 brick).
        → Emit 1-brick bar.

    REVERSAL:
        Price moves rev_bricks * brick_size against trend.
        → Emit ONE bar for the full reversal.

    CLEAN MODE OFF:
        Reversal bar body = rev_bricks * brick_size.
        open = previous level, close = reversal target.
        Example (brick=5, bull, level=1010, rev=2):
            → open=1010, close=1000 (body=10, 2x brick)

    CLEAN MODE ON:
        Reversal bar body = 1 brick (same visual size as continuation).
        open is pushed to 1 brick from close.
        The real origin becomes a wick.
        Example (brick=5, bull, level=1010, rev=2):
            → open=1005, close=1000, high=1010 (wick shows origin)
        Example (brick=5, bear, level=1000, rev=2):
            → open=1005, close=1010, low=1000 (wick shows origin)
    """

    def __init__(self, range_size, out_path, price_decimals,
                 include_partial=True, max_bars=None, rev_bricks=2.0,
                 clean_mode=False):
        self.rs = float(range_size)
        self.pd = price_decimals
        self.include_partial = include_partial
        self.max_bars = max_bars
        self.rev_bricks = rev_bricks
        self.clean_mode = clean_mode

        self.trend = 0       # 0 = startup, 1 = bull, -1 = bear
        self.level = None    # current grid level (= last confirmed close)
        self.bar = None      # working bar accumulating wicks + volume
        self.bar_count = 0

        self.out_path = out_path
        self._f = open(out_path, "w", encoding="utf-8")
        self._f.write("[")
        self._wrote_any = False
        self._last_written_ts = None

    @property
    def limit_reached(self):
        if self.max_bars is None:
            return False
        return self.bar_count >= self.max_bars

    def close(self):
        if self._f:
            self._f.close()
            self._f = None

    def _snap(self, price):
        """Snap price to nearest grid level (multiple of brick size)."""
        rs = self.rs
        return round(round(price / rs) * rs, self.pd)

    def _emit(self, bar_dict):
        if self.limit_reached:
            return

        ts = int(bar_dict["time"])
        if self._last_written_ts is not None and ts <= self._last_written_ts:
            ts = self._last_written_ts + 1
        bar_dict["time"] = ts
        self._last_written_ts = ts

        if self._wrote_any:
            self._f.write(",")
        else:
            self._wrote_any = True

        self._f.write(json.dumps(bar_dict, separators=(",", ":")))
        self.bar_count += 1

    def _bar_dict(self, open_, high_, low_, close_):
        return make_bar(
            self.bar["time"],
            open_,
            high_,
            low_,
            close_,
            self.bar["volume"],
            self.pd,
        )

    def _reset_bar(self, tick):
        """Start a fresh working bar at current grid level."""
        self.bar = {
            "time": tick["ts"],
            "open": self.level,
            "high": self.level,
            "low": self.level,
            "close": self.level,
            "volume": 0.0,
        }

    def _finalize_output(self):
        self._f.write("]")
        self._f.flush()
        self.close()

    def process_tick(self, tick):
        if self.limit_reached:
            return

        p = tick["price"]
        v = tick["volume"]
        rs = self.rs

        # ── FIRST TICK: snap to grid, start working bar ──────────
        if self.bar is None:
            self.level = self._snap(p)
            self.bar = {
                "time": tick["ts"],
                "open": self.level,
                "high": self.level,
                "low": self.level,
                "close": self.level,
                "volume": v,
            }
            return

        self.bar["volume"] += v

        # track wicks on the working bar
        self.bar["high"] = max(self.bar["high"], p)
        self.bar["low"] = min(self.bar["low"], p)

        # ── process completed bricks ─────────────────────────────
        while True:
            if self.limit_reached:
                break

            lvl = self.level

            # ── STARTUP: no trend yet ────────────────────────────
            if self.trend == 0:
                up_target = round(lvl + rs, self.pd)
                down_target = round(lvl - rs, self.pd)

                if p >= up_target:
                    bar_open = lvl
                    bar_close = up_target
                    bar_high = max(self.bar["high"], bar_close)
                    bar_low = self.bar["low"]

                    self._emit(self._bar_dict(bar_open, bar_high, bar_low, bar_close))

                    self.trend = 1
                    self.level = up_target
                    self._reset_bar(tick)
                    continue

                elif p <= down_target:
                    bar_open = lvl
                    bar_close = down_target
                    bar_high = self.bar["high"]
                    bar_low = min(self.bar["low"], bar_close)

                    self._emit(self._bar_dict(bar_open, bar_high, bar_low, bar_close))

                    self.trend = -1
                    self.level = down_target
                    self._reset_bar(tick)
                    continue

                else:
                    break

            # ── BULL TREND ───────────────────────────────────────
            elif self.trend == 1:
                cont_target = round(lvl + rs, self.pd)
                rev_target = round(lvl - self.rev_bricks * rs, self.pd)

                if p >= cont_target:
                    # CONTINUATION: 1 brick up
                    bar_open = lvl
                    bar_close = cont_target
                    bar_high = max(self.bar["high"], bar_close)
                    bar_low = self.bar["low"]

                    self._emit(self._bar_dict(bar_open, bar_high, bar_low, bar_close))

                    self.level = cont_target
                    self._reset_bar(tick)
                    continue

                elif p <= rev_target:
                    # REVERSAL: bull → bear
                    if self.clean_mode:
                        # body = 1 brick, origin becomes wick
                        # close = rev_target
                        # open  = 1 brick above close
                        # high  = lvl (real origin, becomes wick)
                        bar_close = rev_target
                        bar_open = round(rev_target + rs, self.pd)
                        bar_high = max(self.bar["high"], lvl)
                        bar_low = min(self.bar["low"], bar_close)
                    else:
                        # full body = rev_bricks * brick_size
                        bar_open = lvl
                        bar_close = rev_target
                        bar_high = self.bar["high"]
                        bar_low = min(self.bar["low"], bar_close)

                    self._emit(self._bar_dict(bar_open, bar_high, bar_low, bar_close))

                    self.trend = -1
                    self.level = rev_target
                    self._reset_bar(tick)
                    continue

                else:
                    break

            # ── BEAR TREND ───────────────────────────────────────
            elif self.trend == -1:
                cont_target = round(lvl - rs, self.pd)
                rev_target = round(lvl + self.rev_bricks * rs, self.pd)

                if p <= cont_target:
                    # CONTINUATION: 1 brick down
                    bar_open = lvl
                    bar_close = cont_target
                    bar_high = self.bar["high"]
                    bar_low = min(self.bar["low"], bar_close)

                    self._emit(self._bar_dict(bar_open, bar_high, bar_low, bar_close))

                    self.level = cont_target
                    self._reset_bar(tick)
                    continue

                elif p >= rev_target:
                    # REVERSAL: bear → bull
                    if self.clean_mode:
                        # body = 1 brick, origin becomes wick
                        # close = rev_target
                        # open  = 1 brick below close
                        # low   = lvl (real origin, becomes wick)
                        bar_close = rev_target
                        bar_open = round(rev_target - rs, self.pd)
                        bar_high = max(self.bar["high"], bar_close)
                        bar_low = min(self.bar["low"], lvl)
                    else:
                        # full body = rev_bricks * brick_size
                        bar_open = lvl
                        bar_close = rev_target
                        bar_high = max(self.bar["high"], bar_close)
                        bar_low = self.bar["low"]

                    self._emit(self._bar_dict(bar_open, bar_high, bar_low, bar_close))

                    self.trend = 1
                    self.level = rev_target
                    self._reset_bar(tick)
                    continue

                else:
                    break

    def finish(self):
        if not self.limit_reached:
            if self.include_partial and self.bar is not None:
                if (self.bar["high"] != self.bar["low"]) or \
                   (self.bar["close"] != self.bar["open"]):
                    bar_dict = make_bar(
                        self.bar["time"],
                        self.bar["open"],
                        self.bar["high"],
                        self.bar["low"],
                        self.bar["close"],
                        self.bar["volume"],
                        self.pd,
                    )
                    self._emit(bar_dict)

        self._finalize_output()

# ── DEBUG: 1-MINUTE BARS FROM TICKS ─────────────────────────────────
def debug_generate_1min_bars(input_file, output_dir, max_bars=500):
    """
    Aggregates raw ticks into 1-minute OHLCV bars.
    Writes first max_bars bars as JSON for visual inspection.
    """
    fmt = _detect_tick_format(input_file)
    print(f"\n  [DEBUG] Generating {max_bars} x 1-minute bars from ticks...")
    print(f"  [DEBUG] Source: {input_file}")
    print(f"  [DEBUG] Detected format: {fmt}")

    # for metatrader, show the column mapping
    if fmt == "metatrader":
        with open(input_file, "r", encoding="utf-8", errors="ignore") as hf:
            for hline in hf:
                hline = hline.strip()
                if hline.startswith("<DATE>"):
                    cmap = _parse_metatrader_header(hline)
                    print(f"  [DEBUG] Column map: {cmap}")
                    break

    # show first 3 raw lines (unparsed) for visual verification
    print(f"  [DEBUG] First 3 raw data lines:")
    line_count = 0
    with open(input_file, "r", encoding="utf-8", errors="ignore") as rf:
        for rline in rf:
            rline = rline.rstrip("\n\r")
            if not rline.strip() or rline.strip().startswith("<DATE>"):
                continue
            print(f"    RAW: {repr(rline)}")
            line_count += 1
            if line_count >= 3:
                break

    bars = []
    current_minute = None
    bar = None

    for chunk in iter_ticks_in_chunks(input_file, chunk_rows=CHUNK_ROWS):
        for tick in chunk:
            ts = tick["ts"]
            p = tick["price"]
            v = tick["volume"]

            # floor to minute
            minute_ts = ts - (ts % 300)

            if current_minute is None or minute_ts != current_minute:
                # new minute — finalize previous bar
                if bar is not None:
                    bars.append(bar)
                    if len(bars) >= max_bars:
                        break

                current_minute = minute_ts
                bar = {
                    "time": minute_ts,
                    "open": round(p, 6),
                    "high": round(p, 6),
                    "low": round(p, 6),
                    "close": round(p, 6),
                    "volume": round(v, 2),
                }
            else:
                bar["high"] = round(max(bar["high"], p), 6)
                bar["low"] = round(min(bar["low"], p), 6)
                bar["close"] = round(p, 6)
                bar["volume"] = round(bar["volume"] + v, 2)

        if len(bars) >= max_bars:
            break

    # don't forget last bar
    if bar is not None and len(bars) < max_bars:
        bars.append(bar)

    out_path = os.path.join(output_dir, "debug_1min.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(bars, f, separators=(",", ":"))

    print(f"  [DEBUG] Wrote {len(bars)} bars → {out_path}")

    # also print first 10 bars + last 10 bars for quick sanity check
    print(f"\n  [DEBUG] First 10 bars:")
    for b in bars[:10]:
        dt = datetime.utcfromtimestamp(b["time"]).strftime("%Y-%m-%d %H:%M")
        rng = round(b["high"] - b["low"], 6)
        print(f"    {dt} | O={b['open']} H={b['high']} L={b['low']} C={b['close']} | range={rng} | vol={b['volume']}")

    print(f"\n  [DEBUG] Last 10 bars:")
    for b in bars[-10:]:
        dt = datetime.utcfromtimestamp(b["time"]).strftime("%Y-%m-%d %H:%M")
        rng = round(b["high"] - b["low"], 6)
        print(f"    {dt} | O={b['open']} H={b['high']} L={b['low']} C={b['close']} | range={rng} | vol={b['volume']}")

    # price range summary
    all_highs = [b["high"] for b in bars]
    all_lows = [b["low"] for b in bars]
    total_range = round(max(all_highs) - min(all_lows), 6)
    print(f"\n  [DEBUG] Price range across {len(bars)} bars:")
    print(f"    High: {max(all_highs)}")
    print(f"    Low:  {min(all_lows)}")
    print(f"    Total range: {total_range}")
    print(f"    Avg 1min range: {round(sum(b['high'] - b['low'] for b in bars) / len(bars), 6)}")

    # also dump first 5 raw ticks for format verification
    print(f"\n  [DEBUG] First 5 raw ticks (parsed):")
    count = 0
    for chunk in iter_ticks_in_chunks(input_file, chunk_rows=100):
        for tick in chunk:
            dt = datetime.utcfromtimestamp(tick["ts"]).strftime("%Y-%m-%d %H:%M:%S")
            print(f"    {dt} | price={tick['price']} | vol={tick['volume']}")
            count += 1
            if count >= 5:
                break
        break

    return out_path

def main():
    import sys

    debug_mode = "--debug" in sys.argv

    print("=" * 58)
    if debug_mode:
        print("  Koko Candle Generator [DEBUG MODE]")
    else:
        print("  Koko Candle Generator [STREAMING]")
    print("=" * 58)

    selected_folder = select_data_folder()
    if selected_folder is None:
        return

    input_file = select_csv_file(selected_folder)
    if input_file is None:
        return

    # ── DEBUG: just dump 1-min bars and exit ─────────────────────
    if debug_mode:
        tick_format = _detect_tick_format(input_file)
        print(f"\n  [DEBUG] Tick format detected: {tick_format}")
        debug_generate_1min_bars(input_file, selected_folder, max_bars=500)
        print("\n  [DEBUG] Done. Load debug_1min.json in your chart to inspect.")
        print("=" * 58)
        return

    # ── NORMAL MODE ──────────────────────────────────────────────
    range_sizes, price_decimals = ask_range_settings()
    rev_bricks = ask_reversal_bricks()
    clean_mode = ask_clean_mode()
    bar_limit = ask_bar_limit()

    tick_format = _detect_tick_format(input_file)

    output_dir = selected_folder
    os.makedirs(output_dir, exist_ok=True)

    rev_label = _format_decimal(Decimal(str(rev_bricks)))

    print("\n" + "=" * 58)
    print(f"  Selected folder:      {selected_folder}")
    print(f"  Input CSV:            {input_file}")
    print(f"  Tick format:          {tick_format}")
    print(f"  Chunk rows:           {CHUNK_ROWS:,}")
    print(f"  Reversal bricks:      {rev_label}")
    print(f"  Clean mode:           {'ON' if clean_mode else 'OFF'}")
    print(f"  Bar limit:            {bar_limit if bar_limit else 'unlimited (full dataset)'}")
    print("  Ranges:")
    for rng in range_sizes:
        rev_dist = _format_decimal(rng * Decimal(str(rev_bricks)))
        print(f"    {_format_decimal(rng)}pt  (reversal triggers at {rev_dist}pt move)")
    print("=" * 58)

    streamers = {}
    out_paths = {}

    try:
        for rng in range_sizes:
            label = _format_decimal(rng)
            fname = f"{label}pt.json"
            out_path = os.path.join(output_dir, fname)
            out_paths[rng] = out_path

            print(f"  Opening output stream: {out_path}")

            streamers[rng] = KokoCandleStreamer(
                range_size=rng,
                out_path=out_path,
                price_decimals=price_decimals,
                include_partial=INCLUDE_PARTIAL_BAR,
                max_bars=bar_limit,
                rev_bricks=rev_bricks,
                clean_mode=clean_mode,
            )

        print(f"\n  Streaming ticks from {input_file} ...")

        total_ticks = 0
        last_ts_seen = None
        chunk_idx = 0
        all_done = False

        for chunk in iter_ticks_in_chunks(input_file, chunk_rows=CHUNK_ROWS):
            chunk_idx += 1

            if not ASSUME_INPUT_SORTED:
                chunk.sort(key=lambda x: x["ts"])

            all_done = all(s.limit_reached for s in streamers.values())
            if all_done:
                print("  ✓ All ranges hit bar limit. Stopping early.")
                break

            for tick in chunk:
                ts = tick["ts"]

                if ts == 0:
                    ts = (last_ts_seen + 1) if last_ts_seen is not None else 1
                    tick["ts"] = ts

                if last_ts_seen is not None and ts < last_ts_seen:
                    tick["ts"] = last_ts_seen
                    ts = last_ts_seen

                last_ts_seen = ts

                for rng in range_sizes:
                    streamers[rng].process_tick(tick)

                total_ticks += 1

                all_done = all(s.limit_reached for s in streamers.values())
                if all_done:
                    break

            print(f"  ✓ chunk {chunk_idx} processed  (ticks so far: {total_ticks:,})")

            if all_done:
                print("  ✓ All ranges hit bar limit. Stopping early.")
                break

        print(f"\n  ✓ Total ticks processed: {total_ticks:,}")
        print("  Finalizing bars + closing files...")

        for rng in range_sizes:
            streamers[rng].finish()

        print("\n" + "=" * 58)
        print(f"  Done. Files saved in: {output_dir}")
        for rng in range_sizes:
            count = streamers[rng].bar_count
            print(f"    {out_paths[rng]}  ({count:,} bars)")
        print("=" * 58)

    finally:
        for s in streamers.values():
            try:
                if s and s._f is not None:
                    try:
                        s._f.write("]")
                    except Exception:
                        pass
                    try:
                        s.close()
                    except Exception:
                        pass
            except Exception:
                pass

if __name__ == "__main__":
    main()
```

---

## FILE: `js/strategy_loader.js`

```javascript
/* ============================================================
   JINNI ZERO — Strategy Loader
   Now with REAL streaming progress (matches legacy mode).
============================================================ */
(function () {
  var API = {
    list: 'http://localhost:5000/api/strategies',
    detail: function (id) { return 'http://localhost:5000/api/strategy/' + encodeURIComponent(id); },
    run: 'http://localhost:5000/api/backtest/run',
    runStream: 'http://localhost:5000/api/backtest/run/stream',
  };

  var STATE = {
    mode: 'manual',
    strategies: [],
    currentStrategyId: null,
    currentMeta: null,
  };

  function $(id) { return document.getElementById(id); }
  function setDisplay(node, val) { if (node) node.style.display = val; }

  // ==========================================================
  // MODE SWITCHING
  // ==========================================================
  function setMode(mode) {
    STATE.mode = mode;
    document.querySelectorAll('.bt-manual-only').forEach(function (node) {
      setDisplay(node, mode === 'manual' ? '' : 'none');
    });
    setDisplay($('bt_strategyPanel'), mode === 'strategy' ? '' : 'none');
  }

  // ==========================================================
  // SCHEMA → UI
  // ==========================================================
  function renderStrategyParams(schema, defaults) {
    var root = $('bt_strategyParams');
    if (!root) return;
    root.innerHTML = '';

    if (!schema || !Object.keys(schema).length) {
      var empty = document.createElement('div');
      empty.className = 'bt-toggle-label';
      empty.textContent = 'This strategy has no configurable parameters.';
      root.appendChild(empty);
      return;
    }

    Object.entries(schema).forEach(function (entry) {
      var key = entry[0], spec = entry[1];
      if (!spec || spec.type === 'group') return;

      var row = document.createElement('div');
      row.className = 'bt-field';

      var label = document.createElement('label');
      label.className = 'bt-label';
      label.textContent = spec.label || key;
      row.appendChild(label);

      var input;
      var val = defaults[key] != null ? defaults[key] : spec.default;

      if (spec.type === 'enum') {
        input = document.createElement('select');
        input.className = 'bt-select';
        (spec.options || []).forEach(function (opt) {
          var o = document.createElement('option');
          o.value = opt; o.textContent = opt;
          if (val === opt) o.selected = true;
          input.appendChild(o);
        });
      } else if (spec.type === 'boolean') {
        input = document.createElement('input');
        input.type = 'checkbox'; input.checked = Boolean(val);
        input.style.accentColor = 'var(--accent)';
      } else if (spec.type === 'number') {
        input = document.createElement('input');
        input.className = 'bt-input'; input.type = 'number';
        input.value = val != null ? val : '';
        if (spec.min != null) input.min = spec.min;
        if (spec.max != null) input.max = spec.max;
        if (spec.step != null) input.step = spec.step;
      } else {
        input = document.createElement('input');
        input.className = 'bt-input'; input.type = 'text';
        input.value = val != null ? val : '';
      }

      input.dataset.key = key;
      row.appendChild(input);

      if (spec.help) {
        var help = document.createElement('div');
        help.className = 'bt-toggle-label';
        help.textContent = spec.help;
        help.style.marginTop = '4px';
        row.appendChild(help);
      }

      root.appendChild(row);
    });
  }

  function collectStrategyParams() {
    var out = {};
    var root = $('bt_strategyParams');
    if (!root) return out;
    root.querySelectorAll('[data-key]').forEach(function (el) {
      var key = el.dataset.key;
      if (el.type === 'checkbox') out[key] = el.checked;
      else if (el.type === 'number') out[key] = el.value === '' ? null : Number(el.value);
      else out[key] = el.value;
    });
    return out;
  }

  // ==========================================================
  // STRATEGY LOADING
  // ==========================================================
  function renderStrategyMeta(meta) {
    STATE.currentMeta = meta || null;
    $('bt_strategyDescription').textContent =
      (meta && meta.description) ? meta.description : 'No description.';
    var schema = (meta && meta.parameters) || {};
    var defaults = {};
    Object.entries(schema).forEach(function (entry) {
      var k = entry[0], v = entry[1];
      if (v && v.default != null) defaults[k] = v.default;
    });
    renderStrategyParams(schema, defaults);
  }

  async function fetchStrategies() {
    try {
      var resp = await fetch(API.list);
      if (!resp.ok) throw new Error('Failed (' + resp.status + ')');
      var list = await resp.json();
      STATE.strategies = Array.isArray(list) ? list : [];
      var select = $('bt_strategySelect');
      if (!select) return;
      select.innerHTML = '';
      STATE.strategies.forEach(function (item) {
        var opt = document.createElement('option');
        opt.value = item.id; opt.textContent = item.name;
        select.appendChild(opt);
      });
      if (STATE.strategies.length) {
        STATE.currentStrategyId = STATE.strategies[0].id;
        select.value = STATE.currentStrategyId;
        await fetchStrategyDetail(STATE.currentStrategyId);
      } else {
        $('bt_strategyDescription').textContent = 'No strategy plugins found.';
        $('bt_strategyParams').innerHTML = '';
      }
    } catch (err) {
      $('bt_strategyDescription').textContent = 'Error loading strategies: ' + err.message;
    }
  }

  async function fetchStrategyDetail(id) {
    try {
      var resp = await fetch(API.detail(id));
      if (!resp.ok) throw new Error('Failed (' + resp.status + ')');
      var meta = await resp.json();
      STATE.currentStrategyId = id;
      renderStrategyMeta(meta);
    } catch (err) {
      $('bt_strategyDescription').textContent = 'Error: ' + err.message;
    }
  }

  // ==========================================================
  // BUILD PAYLOAD
  // ==========================================================
  function buildPayload() {
    var sliceMode = ($('bt_sliceMode') || {}).value || 'bar_count';

    var payload = {
      strategy_id: STATE.currentStrategyId,
      parameters: collectStrategyParams(),
      symbol: ($('bt_symbol') || {}).value || 'NQ',
      range: parseInt(($('bt_range') || {}).value || '10', 10),
      bar_range: parseInt(($('bt_barRange') || {}).value || '1000', 10),
      starting_capital: parseFloat(($('bt_startingCapital') || {}).value || '10000'),
      lot_size: parseFloat(($('bt_lotSize') || {}).value || '1.0'),
      point_value: parseFloat((document.getElementById('bt_pointValue') || {}).value || '1') || 1.0,
      sizing_mode: ($('bt_sizingMode') || {}).value || 'fixed',
      risk_pct: parseFloat(($('bt_riskPct') || {}).value || '1.0') || 1.0,
      fixed_risk: parseFloat(($('bt_fixedRisk') || {}).value || '10') || 10,
      scaling_enabled: ($('bt_scalingEnabled') || {}).checked || false,
      scaling_per: parseFloat(($('bt_scalingPer') || {}).value || '100') || 100,
      scaling_risk: parseFloat(($('bt_scalingRisk') || {}).value || '1') || 1,
      commission_per_lot: parseFloat(($('bt_commPerLot') || {}).value || '1.25') || 0,
      ambiguous_bar_mode: ($('bt_ambiguousMode') || {}).value || 'conservative',
      spread: {
        enabled: ($('bt_spreadEnabled') || {}).checked || false,
        min: parseFloat(($('bt_spreadMin') || {}).value || '0'),
        max: parseFloat(($('bt_spreadMax') || {}).value || '0'),
        seed: parseInt(($('bt_spreadSeed') || {}).value || '0', 10),
      },
      mc_runs: parseInt(($('bt_mcRuns') || {}).value || '1000', 10),
    };

    if (sliceMode === 'date_range') {
      payload.start_date = ($('bt_startDate') || {}).value || '';
      payload.end_date = ($('bt_endDate') || {}).value || '';
      payload.bar_range = 0;
    }
    return payload;
  }

  // ==========================================================
  // RUN — REAL STREAMING PROGRESS
  // ==========================================================
  async function runStrategyBacktest() {
    var btn = $('bt_strategyRunBtn');
    var payload = buildPayload();

    if (!payload.strategy_id) { alert('No strategy selected.'); return; }

    btn.classList.add('running');
    btn.innerHTML = '<span class="bt-run-icon">⟳</span> RUNNING…';

    if (typeof window.clearBacktestMarkers === 'function') window.clearBacktestMarkers();

    var timing = {};
    var totalT0 = performance.now();
    var streamed = false;

    try {
      // Reset progress UI
      if (typeof window.btShowRunnerState === 'function') {
        window.btShowRunnerState({ stepId: 'step_load', pct: 5, label: 'Preparing strategy…' });
      }

      // ══ TRY STREAMING FIRST ══════════════════════════════
      var fetchT0 = performance.now();
      var resp = await fetch(API.runStream, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!resp.ok) throw new Error('Stream failed: ' + resp.status);
      var ct = (resp.headers.get('content-type') || '').toLowerCase();
      if (!ct.includes('ndjson') && !ct.includes('stream')) throw new Error('Not a stream response');

      streamed = true;

      var reader = resp.body.getReader();
      var decoder = new TextDecoder();
      var buffer = '';
      var finalData = null;

      while (true) {
        var chunk = await reader.read();
        if (chunk.done) break;

        buffer += decoder.decode(chunk.value, { stream: true });
        var lines = buffer.split('\n');
        buffer = lines.pop();

        for (var li = 0; li < lines.length; li++) {
          var trimmed = lines[li].trim();
          if (!trimmed) continue;
          var msg = null;
          try { msg = JSON.parse(trimmed); } catch (e) { continue; }

          if (msg.type === 'progress') {
            // Real progress from backend
            if (typeof window.btShowRunnerState === 'function') {
              window.btShowRunnerState({
                pct: msg.pct || 0,
                label: 'Bar ' + (msg.bar || 0).toLocaleString() + ' / ' + (msg.total || 0).toLocaleString(),
                live: {
                  bar: msg.bar,
                  total: msg.total,
                  equity: msg.equity,
                  drawdown: msg.drawdown,
                  open_trade: msg.open_trade,
                  last_closed_pnl: msg.last_closed_pnl,
                },
              });
            }
          } else if (msg.type === 'result') {
            finalData = msg.data;
          } else if (msg.type === 'error') {
            throw new Error(msg.error || 'Backend error during stream');
          }
        }
      }

      // Check leftover buffer
      if (buffer.trim()) {
        try {
          var lastMsg = JSON.parse(buffer.trim());
          if (lastMsg.type === 'result') finalData = lastMsg.data;
          if (lastMsg.type === 'error') throw new Error(lastMsg.error || 'Backend error');
        } catch (e) { /* ignore parse errors on leftover */ }
      }

      if (!finalData) throw new Error('No result from stream');

      var fetchT1 = performance.now();
      timing.receive_ms = Math.round(fetchT1 - fetchT0);

      // ══ RENDER ═══════════════════════════════════════════
      if (typeof window.btShowRunnerState === 'function') {
        window.btShowRunnerState({ stepId: 'step_stats', pct: 92, label: 'Computing statistics…' });
      }

      if (typeof window.btRenderAnyResult !== 'function') {
        throw new Error('btRenderAnyResult missing (backtest.js not loaded).');
      }

      // Normalize
      if (finalData && finalData.type === 'result' && finalData.data) finalData = finalData.data;
      if (finalData && !finalData.stats && finalData.metrics) finalData.stats = finalData.metrics;
      if (finalData && !finalData.equity_curve && finalData.curves) {
        finalData.equity_curve = finalData.curves.equity_downsampled || finalData.curves.equity_full || [];
      }
      if (finalData && !finalData.drawdown_curve && finalData.curves) {
        finalData.drawdown_curve = finalData.curves.drawdown_downsampled || finalData.curves.drawdown_full || [];
      }

      if (typeof window.btShowRunnerState === 'function') {
        window.btShowRunnerState({ stepId: 'step_charts', pct: 96, label: 'Rendering dashboard…' });
      }

      var renderT0 = performance.now();
      window.btRenderAnyResult(finalData, {
        mode: 'strategy',
        strategy_id: payload.strategy_id,
        range: payload.range,
      });
      var renderT1 = performance.now();
      timing.render_ms = Math.round(renderT1 - renderT0);

      if (typeof window.btShowRunnerState === 'function') {
        window.btShowRunnerState({ stepId: 'step_done', pct: 100, label: 'Complete ✓' });
      }

    } catch (streamErr) {
      // ══ FALLBACK: non-streaming ══════════════════════════
      if (!streamed || streamErr.message.includes('No result')) {
        try {
          if (typeof window.btShowRunnerState === 'function') {
            window.btShowRunnerState({ stepId: 'step_run', pct: 30, label: 'Running (non-streaming)…' });
          }
          var resp2 = await fetch(API.run, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
          });
          if (!resp2.ok) {
            var errText = await resp2.text();
            throw new Error('Server ' + resp2.status + ': ' + errText);
          }

          if (typeof window.btShowRunnerState === 'function') {
            window.btShowRunnerState({ stepId: 'step_stats', pct: 80, label: 'Parsing response…' });
          }

          var data = await resp2.json().catch(function () { return {}; });
          if (data && data.type === 'result' && data.data) data = data.data;
          if (data && !data.stats && data.metrics) data.stats = data.metrics;

          if (typeof window.btRenderAnyResult === 'function') {
            window.btRenderAnyResult(data, {
              mode: 'strategy',
              strategy_id: payload.strategy_id,
              range: payload.range,
            });
          }

          if (typeof window.btShowRunnerState === 'function') {
            window.btShowRunnerState({ stepId: 'step_done', pct: 100, label: 'Complete ✓' });
          }

        } catch (fallbackErr) {
          console.error(fallbackErr);
          if (typeof window.btShowRunnerError === 'function') {
            window.btShowRunnerError(fallbackErr.message || String(fallbackErr));
          } else {
            alert(fallbackErr.message || String(fallbackErr));
          }
        }
      } else {
        console.error(streamErr);
        if (typeof window.btShowRunnerError === 'function') {
          window.btShowRunnerError(streamErr.message || String(streamErr));
        } else {
          alert(streamErr.message || String(streamErr));
        }
      }
    } finally {
      btn.classList.remove('running');
      btn.innerHTML = '<span class="bt-run-icon">▶</span> RUN BACKTEST';

      var totalT1 = performance.now();
      timing.total_ms = Math.round(totalT1 - totalT0);
      console.log('[STRATEGY TIMING] receive=' + (timing.receive_ms || '?') + 'ms '
        + 'render=' + (timing.render_ms || '?') + 'ms '
        + 'total=' + timing.total_ms + 'ms');
    }
  }

  // ==========================================================
  // WIRING
  // ==========================================================
  function boot() {
    setMode(($('bt_mode') || {}).value || 'manual');

    var modeSelect = $('bt_mode');
    if (modeSelect) {
      modeSelect.addEventListener('change', async function () {
        setMode(this.value);
        if (this.value === 'strategy' && !STATE.strategies.length) await fetchStrategies();
      });
    }

    var strategySelect = $('bt_strategySelect');
    if (strategySelect) {
      strategySelect.addEventListener('change', function () { fetchStrategyDetail(this.value); });
    }

    var runBtn = $('bt_strategyRunBtn');
    if (runBtn) {
      runBtn.addEventListener('click', function () { runStrategyBacktest(); });
    }

    if ((($('bt_mode') || {}).value || 'manual') === 'strategy') fetchStrategies();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
```

---

## FILE: `test.py`

```python
with open("data/USDJPY/usdjpy.csv", "r", encoding="utf-8") as f:
    for _ in range(3):
        print(f.readline().rstrip())
```
