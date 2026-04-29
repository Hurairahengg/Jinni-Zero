# backend/engine_core.py
from __future__ import annotations

import math
import random
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.stats_engine import compute_all_stats, downsample_curve as ds_curve


# ============================================================
# Utility
# ============================================================
def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def safe_float(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _parse_datetime_param(val):
    """Convert ISO datetime string (from HTML datetime-local input) to Unix
    timestamp.  Treats naive datetimes as UTC.  Returns None if invalid."""
    if not val or not isinstance(val, str) or not val.strip():
        return None
    val = val.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(val, fmt)
            dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue
    return None


def downsample_curve(values: List[float], max_points: int = 500) -> List:
    """Local wrapper — delegates to stats_engine.downsample_curve."""
    return ds_curve(values, max_points)


# ============================================================
# Indicator Precompute
# ============================================================
def _precompute_sma(values, period):
    n = len(values)
    out = [None] * n
    if period < 1 or n < period:
        return out

    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= period:
            s -= values[i - period]
        if i >= period - 1:
            out[i] = s / period
    return out


def _precompute_ema(values, period):
    n = len(values)
    out = [None] * n
    if period < 1 or n < period:
        return out

    seed = sum(values[:period]) / period
    out[period - 1] = seed
    ema = seed
    k = 2.0 / (period + 1)

    for i in range(period, n):
        ema = values[i] * k + ema * (1 - k)
        out[i] = ema
    return out


def _precompute_wma(values, period):
    n = len(values)
    out = [None] * n
    if period < 1 or n < period:
        return out

    p = period
    denom = p * (p + 1) / 2.0
    s = 0.0
    ws = 0.0

    for j in range(p):
        s += values[j]
        ws += values[j] * (j + 1)
    out[p - 1] = ws / denom

    for i in range(p, n):
        ws = ws + p * values[i] - s
        s = s + values[i] - values[i - p]
        out[i] = ws / denom

    return out


def _precompute_hma(values, period):
    n = len(values)
    out = [None] * n
    p = int(period)
    if p < 1:
        return out

    half = max(1, p // 2)
    sq = max(1, int(math.floor(math.sqrt(p))))

    full = _precompute_wma(values, p)
    half_wma = _precompute_wma(values, half)

    diff = [None] * n
    start = None
    for i in range(n):
        if full[i] is not None and half_wma[i] is not None:
            diff[i] = 2.0 * half_wma[i] - full[i]
            if start is None:
                start = i

    if start is None:
        return out

    compact = []
    mapping = []
    for i in range(start, n):
        if diff[i] is None:
            break
        compact.append(diff[i])
        mapping.append(i)

    final = _precompute_wma(compact, sq)
    for idx, val in enumerate(final):
        if val is not None:
            out[mapping[idx]] = val

    return out


def _precompute_highest(values, period):
    n = len(values)
    out = [None] * n
    if period < 1:
        return out
    for i in range(n):
        if i < period - 1:
            continue
        start = i - period + 1
        out[i] = max(values[start:i + 1])
    return out


def _precompute_lowest(values, period):
    n = len(values)
    out = [None] * n
    if period < 1:
        return out
    for i in range(n):
        if i < period - 1:
            continue
        start = i - period + 1
        out[i] = min(values[start:i + 1])
    return out


def _precompute_vwap(bars):
    out = [None] * len(bars)
    pv_sum = 0.0
    vol_sum = 0.0
    for i, b in enumerate(bars):
        typical = (safe_float(b["high"]) + safe_float(b["low"]) + safe_float(b["close"])) / 3.0
        vol = max(0.0, safe_float(b.get("volume", 0.0), 0.0))
        pv_sum += typical * vol
        vol_sum += vol
        out[i] = (pv_sum / vol_sum) if vol_sum > 0 else typical
    return out


def _precompute_true_range(bars):
    out = [None] * len(bars)
    prev_close = None
    for i, b in enumerate(bars):
        high_ = safe_float(b["high"])
        low_ = safe_float(b["low"])
        if prev_close is None:
            tr = high_ - low_
        else:
            tr = max(high_ - low_, abs(high_ - prev_close), abs(low_ - prev_close))
        out[i] = tr
        prev_close = safe_float(b["close"])
    return out


def _precompute_choppiness(bars, period):
    n = len(bars)
    out = [None] * n
    if period < 2 or n < period:
        return out

    trs = _precompute_true_range(bars)
    highs = [safe_float(b["high"]) for b in bars]
    lows = [safe_float(b["low"]) for b in bars]

    for i in range(n):
        if i < period - 1:
            continue
        start = i - period + 1
        tr_sum = sum(trs[start:i + 1])
        hh = max(highs[start:i + 1])
        ll = min(lows[start:i + 1])
        denom = hh - ll
        if denom <= 0 or tr_sum <= 0:
            out[i] = None
        else:
            out[i] = 100.0 * math.log10(tr_sum / denom) / math.log10(period)
    return out


def _source_values(bars, source):
    if source == "open":
        return [safe_float(b["open"]) for b in bars]
    if source == "high":
        return [safe_float(b["high"]) for b in bars]
    if source == "low":
        return [safe_float(b["low"]) for b in bars]
    return [safe_float(b["close"]) for b in bars]


def precompute_indicator_series(bars: List[dict], item: dict):
    kind = str(item["kind"]).upper()
    source = item.get("source", "close")
    period = int(item.get("period", 1))
    values = _source_values(bars, source)

    if kind == "SMA":
        return _precompute_sma(values, period)
    if kind == "EMA":
        return _precompute_ema(values, period)
    if kind == "WMA":
        return _precompute_wma(values, period)
    if kind == "HMA":
        return _precompute_hma(values, period)
    if kind == "HIGHEST_HIGH":
        return _precompute_highest(values, period)
    if kind == "LOWEST_LOW":
        return _precompute_lowest(values, period)
    if kind == "VWAP":
        return _precompute_vwap(bars)
    if kind == "CHOPPINESS":
        return _precompute_choppiness(bars, period)

    raise ValueError(f"Unsupported indicator kind '{kind}'")


def build_indicator_store(bars: List[dict], plan: List[dict]) -> Dict[str, List[Optional[float]]]:
    store = {}
    for item in plan:
        key = item["key"]
        store[key] = precompute_indicator_series(bars, item)
    return store


# ============================================================
# Config Defaults
# ============================================================
ENGINE_DEFAULTS = {
    "position_sizing": {
        "mode": "fixed_lot",
        "fixed_lot": 1.0,
        "risk_percent": 1.0,
        "min_lot": 0.01,
        "max_lot": 100.0,
    },
    "stacking": {
        "allow_stacking": False,
    },
    "exits": {
        "stop_loss": {
            "mode": "strategy",
            "value": 0.0,
            "buffer_points": 0.0,
        },
        "take_profit": {
            "mode": "strategy",
            "value": 0.0,
        },
        "trailing": {
            "enabled": False,
            "mode": "points",
            "value": 0.0,
            "ma_key": "",
            "activate_after_r": 0.0,
        },
        "break_even": {
            "enabled": False,
            "trigger_r": 1.0,
            "offset_points": 0.0,
        },
        "partial_take_profit": {
            "enabled": False,
            "levels": []
        },
    },
    "scaling_in": {
        "enabled": False,
        "mode": "bars",
        "every_bars": 0,
        "every_r": 0.0,
        "size_fraction": 1.0,
        "max_adds": 0,
    },
    "scaling_out": {
        "enabled": False,
        "levels": []
    },
}


def deep_merge(base, updates):
    result = deepcopy(base)
    for k, v in (updates or {}).items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result


# ============================================================
# Data Models
# ============================================================
@dataclass
class Position:
    position_id: int
    direction: str
    entry_time: int
    entry_index: int
    avg_entry_price: float
    size: float
    initial_size: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    entry_reason: Optional[str]
    risk_points: float
    adds_used: int = 0
    realized_pnl: float = 0.0
    break_even_moved: bool = False
    scale_out_hits: set = field(default_factory=set)
    partial_tp_hits: set = field(default_factory=set)
    highest_seen: Optional[float] = None
    lowest_seen: Optional[float] = None
    peak_r: float = 0.0
    last_add_bar: Optional[int] = None
    last_add_r: float = 0.0

    def update_extremes(self, bar):
        high_ = safe_float(bar["high"])
        low_ = safe_float(bar["low"])
        self.highest_seen = high_ if self.highest_seen is None else max(self.highest_seen, high_)
        self.lowest_seen = low_ if self.lowest_seen is None else min(self.lowest_seen, low_)

    def current_r_from_price(self, price):
        if self.risk_points <= 0:
            return 0.0
        if self.direction == "long":
            return (safe_float(price) - self.avg_entry_price) / self.risk_points
        return (self.avg_entry_price - safe_float(price)) / self.risk_points


# ============================================================
# Commission / Sizing
# ============================================================
def calc_commission(commission_cfg, size):
    commission_cfg = commission_cfg or {}
    mode = commission_cfg.get("type", "flat")
    amount = safe_float(commission_cfg.get("amount", 0.0), 0.0)
    size = safe_float(size, 0.0)

    if mode == "flat":
        return amount
    if mode == "per_side":
        return amount * 2.0
    if mode == "per_contract":
        return amount * size
    return amount


def compute_position_size(balance, sizing_cfg, stop_distance_points):
    cfg = sizing_cfg or {}
    mode = cfg.get("mode", "fixed_lot")

    if mode == "percent_risk":
        risk_percent = safe_float(cfg.get("risk_percent", 1.0), 1.0)
        capital_risk = balance * (risk_percent / 100.0)
        if stop_distance_points <= 0:
            raw_size = safe_float(cfg.get("fixed_lot", 1.0), 1.0)
        else:
            raw_size = capital_risk / stop_distance_points

        raw_size = max(safe_float(cfg.get("min_lot", 0.01), 0.01), raw_size)
        raw_size = min(safe_float(cfg.get("max_lot", 100.0), 100.0), raw_size)
        return raw_size

    return max(0.0, safe_float(cfg.get("fixed_lot", 1.0), 1.0))


# ============================================================
# Exit Manager
# ============================================================
def build_engine_managed_levels(direction, entry_price, entry_bar, action, exits_cfg, indicators_current):
    sl = action.get("stop_loss")
    tp = action.get("take_profit")

    stop_cfg = (exits_cfg or {}).get("stop_loss", {})
    tp_cfg = (exits_cfg or {}).get("take_profit", {})

    stop_mode = stop_cfg.get("mode", "strategy")
    stop_value = safe_float(stop_cfg.get("value", 0.0), 0.0)
    stop_buffer = safe_float(stop_cfg.get("buffer_points", 0.0), 0.0)

    if stop_mode == "fixed_points":
        if direction == "long":
            sl = entry_price - stop_value
        else:
            sl = entry_price + stop_value

    elif stop_mode == "last_bar_extreme":
        if direction == "long":
            sl = safe_float(entry_bar["low"]) - stop_buffer
        else:
            sl = safe_float(entry_bar["high"]) + stop_buffer

    elif stop_mode == "none":
        sl = None

    tp_mode = tp_cfg.get("mode", "strategy")
    tp_value = safe_float(tp_cfg.get("value", 0.0), 0.0)

    if tp_mode == "fixed_points":
        if direction == "long":
            tp = entry_price + tp_value
        else:
            tp = entry_price - tp_value

    elif tp_mode == "fixed_r":
        if sl is not None:
            risk = abs(entry_price - safe_float(sl))
            if direction == "long":
                tp = entry_price + (risk * tp_value)
            else:
                tp = entry_price - (risk * tp_value)

    elif tp_mode == "none":
        tp = None

    return sl, tp


def apply_break_even(position: Position, break_even_cfg: dict):
    if not break_even_cfg.get("enabled", False):
        return

    if position.break_even_moved:
        return

    trigger_r = safe_float(break_even_cfg.get("trigger_r", 1.0), 1.0)
    offset_points = safe_float(break_even_cfg.get("offset_points", 0.0), 0.0)

    if position.peak_r < trigger_r:
        return

    if position.direction == "long":
        new_sl = position.avg_entry_price + offset_points
        position.stop_loss = max(position.stop_loss or -10**18, new_sl)
    else:
        new_sl = position.avg_entry_price - offset_points
        position.stop_loss = min(position.stop_loss or 10**18, new_sl)

    position.break_even_moved = True


def apply_trailing_stop(position: Position, trailing_cfg: dict, indicators_current: dict):
    if not trailing_cfg.get("enabled", False):
        return

    activate_after_r = safe_float(trailing_cfg.get("activate_after_r", 0.0), 0.0)
    if position.peak_r < activate_after_r:
        return

    mode = trailing_cfg.get("mode", "points")

    if mode == "points":
        trail_points = safe_float(trailing_cfg.get("value", 0.0), 0.0)
        if trail_points <= 0:
            return

        if position.direction == "long" and position.highest_seen is not None:
            new_sl = position.highest_seen - trail_points
            position.stop_loss = max(position.stop_loss or -10**18, new_sl)

        elif position.direction == "short" and position.lowest_seen is not None:
            new_sl = position.lowest_seen + trail_points
            position.stop_loss = min(position.stop_loss or 10**18, new_sl)

    elif mode == "ma":
        ma_key = trailing_cfg.get("ma_key", "")
        ma_val = indicators_current.get(ma_key)
        if ma_val is None:
            return

        if position.direction == "long":
            position.stop_loss = max(position.stop_loss or -10**18, safe_float(ma_val))
        else:
            position.stop_loss = min(position.stop_loss or 10**18, safe_float(ma_val))


def evaluate_sl_tp_hit(position: Position, bar: dict):
    high_ = safe_float(bar["high"])
    low_ = safe_float(bar["low"])

    sl_hit = False
    tp_hit = False

    if position.direction == "long":
        sl_hit = position.stop_loss is not None and low_ <= position.stop_loss
        tp_hit = position.take_profit is not None and high_ >= position.take_profit
    else:
        sl_hit = position.stop_loss is not None and high_ >= position.stop_loss
        tp_hit = position.take_profit is not None and low_ <= position.take_profit

    return sl_hit, tp_hit


# ============================================================
# Trade Recording
# ============================================================
def _mae_mfe_for_slice(position: Position, size_closed: float):
    entry = safe_float(position.avg_entry_price)
    if position.direction == "long":
        mae_points = max(0.0, entry - safe_float(position.lowest_seen, entry))
        mfe_points = max(0.0, safe_float(position.highest_seen, entry) - entry)
    else:
        mae_points = max(0.0, safe_float(position.highest_seen, entry) - entry)
        mfe_points = max(0.0, entry - safe_float(position.lowest_seen, entry))

    return {
        "mae_points": mae_points,
        "mfe_points": mfe_points,
        "mae_dollar": mae_points * safe_float(size_closed, 0.0),
        "mfe_dollar": mfe_points * safe_float(size_closed, 0.0),
    }


def record_trade_slice(position: Position, bar: dict, exit_price: float, exit_reason: str, size_closed: float, commission_cfg: dict):
    direction = position.direction
    entry = safe_float(position.avg_entry_price)
    exit_ = safe_float(exit_price)
    size_closed = safe_float(size_closed)

    if direction == "long":
        points = exit_ - entry
    else:
        points = entry - exit_

    gross_pnl = points * size_closed
    commission = calc_commission(commission_cfg, size_closed)
    net_pnl = gross_pnl - commission

    pnl_r = None
    if position.risk_points > 0:
        pnl_r = points / position.risk_points

    mfe_mae = _mae_mfe_for_slice(position, size_closed)

    return {
        "position_id": position.position_id,
        "direction": direction,
        "entry_time": position.entry_time,
        "exit_time": bar["time"],
        "entry_price": entry,
        "exit_price": exit_,
        "size": size_closed,
        "entry_reason": position.entry_reason,
        "exit_reason": exit_reason,
        "stop_loss": position.stop_loss,
        "take_profit": position.take_profit,
        "bars_held": max(0, int(bar["index"]) - position.entry_index),
        "holding_seconds": abs(int(bar["time"]) - int(position.entry_time)),
        "gross_pnl": gross_pnl,
        "commission": commission,
        "net_pnl": net_pnl,
        "net_pnl_r": pnl_r,
        "mae_points": mfe_mae["mae_points"],
        "mfe_points": mfe_mae["mfe_points"],
        "mae_dollar": mfe_mae["mae_dollar"],
        "mfe_dollar": mfe_mae["mfe_dollar"],
    }


# ============================================================
# Monte Carlo (kept local — only strategy engine uses it inline)
# ============================================================
def monte_carlo_summary(trades: List[dict], starting_capital: float, runs: int):
    runs = int(max(0, runs or 0))
    if runs <= 0 or not trades:
        return {
            "final_equity": {},
            "max_drawdown": {},
            "paths_sample": [],
            "final_dist": {"edges": [], "counts": []},
            "dd_dist": {"edges": [], "counts": []},
            "prob_profitable": None,
            "prob_dd_10": None,
            "prob_dd_20": None,
            "prob_dd_30": None,
        }

    pnl_series = [safe_float(t.get("net_pnl"), 0.0) for t in trades]
    finals = []
    dds = []
    paths_sample = []

    def percentile(arr, p):
        if not arr:
            return None
        arr = sorted(arr)
        idx = (p / 100.0) * (len(arr) - 1)
        lo = int(math.floor(idx))
        hi = int(math.ceil(idx))
        if lo == hi:
            return arr[lo]
        w = idx - lo
        return arr[lo] * (1 - w) + arr[hi] * w

    for _ in range(runs):
        eq = starting_capital
        peak = starting_capital
        max_dd = 0.0
        path = [starting_capital]

        for _i in range(len(pnl_series)):
            pick = random.choice(pnl_series)
            eq += pick
            peak = max(peak, eq)
            max_dd = max(max_dd, peak - eq)
            path.append(eq)

        finals.append(eq)
        dds.append(max_dd)
        if len(paths_sample) < 100:
            paths_sample.append(ds_curve(path, 500))

    def make_hist(data, bins=24):
        if not data:
            return {"edges": [], "counts": []}
        mn = min(data)
        mx = max(data)
        if mn == mx:
            return {"edges": [mn, mx], "counts": [len(data)]}
        w = (mx - mn) / bins
        counts = [0] * bins
        edges = [round(mn + i * w, 4) for i in range(bins + 1)]
        for x in data:
            idx = int((x - mn) / w)
            idx = max(0, min(bins - 1, idx))
            counts[idx] += 1
        return {"edges": edges, "counts": counts}

    profitable = len([x for x in finals if x > starting_capital])
    dd10 = len([x for x in dds if x > (starting_capital * 0.10)])
    dd20 = len([x for x in dds if x > (starting_capital * 0.20)])
    dd30 = len([x for x in dds if x > (starting_capital * 0.30)])

    return {
        "final_equity": {
            "p5": percentile(finals, 5),
            "p25": percentile(finals, 25),
            "p50": percentile(finals, 50),
            "p95": percentile(finals, 95),
        },
        "max_drawdown": {
            "p50": percentile(dds, 50),
            "p95": percentile(dds, 95),
        },
        "paths_sample": paths_sample,
        "final_dist": make_hist(finals, 24),
        "dd_dist": make_hist(dds, 24),
        "prob_profitable": (profitable / runs) * 100 if runs else None,
        "prob_dd_10": (dd10 / runs) * 100 if runs else None,
        "prob_dd_20": (dd20 / runs) * 100 if runs else None,
        "prob_dd_30": (dd30 / runs) * 100 if runs else None,
    }


# ============================================================
# Engine
# ============================================================
class BacktestEngine:
    def __init__(self, bars: List[dict], strategy, payload: dict):
        self.bars = [dict(b, index=i) for i, b in enumerate(bars)]
        self.strategy = strategy
        self.payload = payload or {}

        self.strategy_params = strategy.validate_parameters(self.payload.get("parameters", {}))
        self.engine_cfg = deep_merge(ENGINE_DEFAULTS, self.payload.get("engine", {}))
        self.starting_capital = safe_float(self.payload.get("starting_capital", 10000.0), 10000.0)
        self.balance = self.starting_capital

        self.indicator_plan = strategy.build_indicators_required(self.strategy_params)
        self.indicator_store = build_indicator_store(self.bars, self.indicator_plan)

        self.position: Optional[Position] = None
        self.position_counter = 0
        self.trades: List[dict] = []
        self.balance_curve: List[float] = []
        self.equity_curve: List[float] = []
        self.drawdown_curve: List[float] = []
        self.peak_equity = self.starting_capital

        self.state = {
            "engine": {
                "balance": self.balance,
                "equity": self.starting_capital,
                "position": None,
            }
        }

    def _current_indicators(self, i):
        return {k: v[i] if i < len(v) else None for k, v in self.indicator_store.items()}

    def _mark_to_market_equity(self, close_price):
        if not self.position:
            return self.balance

        if self.position.direction == "long":
            unrealized = (safe_float(close_price) - self.position.avg_entry_price) * self.position.size
        else:
            unrealized = (self.position.avg_entry_price - safe_float(close_price)) * self.position.size

        return self.balance + unrealized

    def _position_view(self):
        if not self.position:
            return None
        pos_view = deepcopy(self.position.__dict__)
        pos_view["entry_price"] = pos_view.get("avg_entry_price")
        pos_view["entry_index"] = pos_view.get("entry_index")
        return pos_view

    def _open_new_position(self, action, bar, indicators_current):
        direction = action["enter"]
        entry_price = safe_float(action.get("entry_price", bar["close"]), safe_float(bar["close"]))

        sl, tp = build_engine_managed_levels(
            direction=direction,
            entry_price=entry_price,
            entry_bar=bar,
            action=action,
            exits_cfg=self.engine_cfg["exits"],
            indicators_current=indicators_current,
        )

        if sl is None:
            stop_distance = 1.0
        else:
            stop_distance = abs(entry_price - safe_float(sl))

        size = compute_position_size(
            balance=self.balance,
            sizing_cfg=self.engine_cfg["position_sizing"],
            stop_distance_points=max(0.0000001, stop_distance),
        )

        self.position_counter += 1
        risk_points = max(0.0000001, stop_distance)

        self.position = Position(
            position_id=self.position_counter,
            direction=direction,
            entry_time=bar["time"],
            entry_index=int(bar["index"]),
            avg_entry_price=entry_price,
            size=size,
            initial_size=size,
            stop_loss=sl,
            take_profit=tp,
            entry_reason=action.get("entry_reason"),
            risk_points=risk_points,
            highest_seen=safe_float(bar["high"]),
            lowest_seen=safe_float(bar["low"]),
            last_add_bar=int(bar["index"]),
        )

    def _scale_in_if_needed(self, bar, indicators_current):
        if not self.position:
            return

        cfg = self.engine_cfg.get("scaling_in", {})
        if not cfg.get("enabled", False):
            return
        if not getattr(self.strategy, "allow_stacking", False):
            return
        if self.position.adds_used >= int(cfg.get("max_adds", 0)):
            return

        mode = cfg.get("mode", "bars")
        trigger = False

        if mode == "bars":
            every_bars = int(cfg.get("every_bars", 0))
            if every_bars > 0 and self.position.last_add_bar is not None:
                if (int(bar["index"]) - self.position.last_add_bar) >= every_bars:
                    trigger = True

        elif mode == "r_multiple":
            every_r = safe_float(cfg.get("every_r", 0.0), 0.0)
            current_r = self.position.current_r_from_price(bar["close"])
            if every_r > 0 and (current_r - self.position.last_add_r) >= every_r:
                trigger = True

        if not trigger:
            return

        fraction = max(0.0, safe_float(cfg.get("size_fraction", 1.0), 1.0))
        add_size = self.position.initial_size * fraction
        if add_size <= 0:
            return

        add_price = safe_float(bar["close"])
        total_cost = (self.position.avg_entry_price * self.position.size) + (add_price * add_size)
        new_size = self.position.size + add_size
        if new_size <= 0:
            return

        self.position.avg_entry_price = total_cost / new_size
        self.position.size = new_size
        self.position.adds_used += 1
        self.position.last_add_bar = int(bar["index"])
        self.position.last_add_r = self.position.current_r_from_price(bar["close"])

    def _partial_close(self, bar, size_to_close, exit_price, exit_reason):
        if not self.position or size_to_close <= 0:
            return

        size_to_close = min(size_to_close, self.position.size)

        trade_slice = record_trade_slice(
            position=self.position,
            bar=bar,
            exit_price=exit_price,
            exit_reason=exit_reason,
            size_closed=size_to_close,
            commission_cfg=self.payload.get("commission", {}),
        )
        self.trades.append(trade_slice)
        self.balance += safe_float(trade_slice["net_pnl"])
        self.position.realized_pnl += safe_float(trade_slice["net_pnl"])
        self.position.size -= size_to_close

        if self.position.size <= 0.0000001:
            self.position = None

    def _handle_partial_exits(self, bar):
        if not self.position:
            return

        scaling_out = self.engine_cfg.get("scaling_out", {})
        if scaling_out.get("enabled", False):
            current_r = self.position.current_r_from_price(bar["close"])
            for idx, lvl in enumerate(scaling_out.get("levels", [])):
                if idx in self.position.scale_out_hits:
                    continue
                target_r = safe_float(lvl.get("r", 0.0), 0.0)
                frac = clamp(safe_float(lvl.get("close_frac", 0.0), 0.0), 0.0, 1.0)
                if frac <= 0:
                    continue
                if current_r >= target_r:
                    size_to_close = self.position.size * frac
                    self._partial_close(bar, size_to_close, safe_float(bar["close"]), f"scale_out_{target_r}R")
                    if self.position is None:
                        return
                    self.position.scale_out_hits.add(idx)

        ptp = self.engine_cfg.get("exits", {}).get("partial_take_profit", {})
        if ptp.get("enabled", False) and self.position:
            current_r = self.position.current_r_from_price(bar["close"])
            for idx, lvl in enumerate(ptp.get("levels", [])):
                if idx in self.position.partial_tp_hits:
                    continue
                target_r = safe_float(lvl.get("r", 0.0), 0.0)
                frac = clamp(safe_float(lvl.get("close_frac", 0.0), 0.0), 0.0, 1.0)
                if frac <= 0:
                    continue
                if current_r >= target_r:
                    size_to_close = self.position.size * frac
                    self._partial_close(bar, size_to_close, safe_float(bar["close"]), f"partial_tp_{target_r}R")
                    if self.position is None:
                        return
                    self.position.partial_tp_hits.add(idx)

    def _close_full_position(self, bar, exit_price, exit_reason):
        if not self.position:
            return
        self._partial_close(bar, self.position.size, exit_price, exit_reason)

    def _build_analytics_payload(self):
        trades = self.trades
        r_vals = [safe_float(t.get("net_pnl_r"), 0.0) for t in trades]
        dur_vals = [safe_float(t.get("bars_held"), 0.0) for t in trades]

        def hist(vals, bins=20):
            if not vals:
                return {"edges": [], "counts": []}
            mn = min(vals)
            mx = max(vals)
            if mn == mx:
                return {"edges": [mn, mx], "counts": [len(vals)]}
            w = (mx - mn) / bins
            edges = [round(mn + i * w, 4) for i in range(bins + 1)]
            counts = [0] * bins
            for x in vals:
                idx = int((x - mn) / w)
                idx = max(0, min(bins - 1, idx))
                counts[idx] += 1
            return {"edges": edges, "counts": counts}

        return {
            "rolling": {
                "win_rate": [],
                "expectancy": [],
                "profit_factor": [],
                "sharpe": []
            },
            "r_histogram": hist(r_vals, 20),
            "duration_histogram": hist(dur_vals, 20),
            "mae_mfe": [
                {
                    "mae": safe_float(t.get("mae_dollar"), 0.0),
                    "mfe": safe_float(t.get("mfe_dollar"), 0.0),
                    "win": safe_float(t.get("net_pnl"), 0.0) > 0
                }
                for t in trades
                if t.get("mae_dollar") is not None and t.get("mfe_dollar") is not None
            ],
            "return_scatter": [
                {
                    "x": i + 1,
                    "y": safe_float(t.get("net_pnl_r"), safe_float(t.get("net_pnl"), 0.0)),
                    "win": safe_float(t.get("net_pnl"), 0.0) > 0
                }
                for i, t in enumerate(trades)
            ],
            "time_of_day": {},
            "day_of_week": {},
            "by_month": {},
            "regime": {"volatility": {}, "choppiness": {}},
            "monte_carlo": monte_carlo_summary(
                trades=trades,
                starting_capital=self.starting_capital,
                runs=int(self.payload.get("monte_carlo_runs", 0) or 0)
            ),
            "commission": {
                "total": sum(safe_float(t.get("commission"), 0.0) for t in trades),
                "per_trade": (sum(safe_float(t.get("commission"), 0.0) for t in trades) / len(trades)) if trades else 0.0,
                "net_without_comm": sum(safe_float(t.get("gross_pnl"), 0.0) for t in trades),
                "net_with_comm": sum(safe_float(t.get("net_pnl"), 0.0) for t in trades),
                "pct_of_gross": (
                    (sum(safe_float(t.get("commission"), 0.0) for t in trades) / abs(sum(safe_float(t.get("gross_pnl"), 0.0) for t in trades))) * 100.0
                ) if trades and sum(safe_float(t.get("gross_pnl"), 0.0) for t in trades) != 0 else 0.0
            }
        }

    def run(self):
        for i, bar in enumerate(self.bars):
            indicators_current = self._current_indicators(i)
            indicators_ctx = {
                "current": indicators_current,
                "series": self.indicator_store,
            }

            self.state["engine"]["balance"] = self.balance
            self.state["engine"]["equity"] = self._mark_to_market_equity(bar["close"])
            self.state["engine"]["position"] = self._position_view()

            action = self.strategy.on_bar(
                i=i,
                bar=bar,
                indicators=indicators_ctx,
                state=self.state,
                position=self._position_view(),
                bars=self.bars,
                params=self.strategy_params,
            ) or {}

            if self.position:
                self.position.update_extremes(bar)
                current_r = self.position.current_r_from_price(bar["close"])
                self.position.peak_r = max(self.position.peak_r, current_r)

                apply_break_even(self.position, self.engine_cfg["exits"]["break_even"])
                apply_trailing_stop(self.position, self.engine_cfg["exits"]["trailing"], indicators_current)
                self._handle_partial_exits(bar)

                if self.position:
                    sl_hit, tp_hit = evaluate_sl_tp_hit(self.position, bar)

                    if sl_hit and tp_hit:
                        self._close_full_position(bar, self.position.stop_loss, "sl_hit")
                    elif sl_hit:
                        self._close_full_position(bar, self.position.stop_loss, "sl_hit")
                    elif tp_hit:
                        self._close_full_position(bar, self.position.take_profit, "tp_hit")
                    elif action.get("exit"):
                        exit_price = safe_float(action.get("exit_price", bar["close"]), safe_float(bar["close"]))
                        self._close_full_position(bar, exit_price, action.get("exit_reason", "signal_exit"))

                if self.position:
                    self._scale_in_if_needed(bar, indicators_current)

            if not self.position and action.get("enter") in {"long", "short"}:
                self._open_new_position(action, bar, indicators_current)

            equity = self._mark_to_market_equity(bar["close"])
            self.peak_equity = max(self.peak_equity, equity)
            drawdown = self.peak_equity - equity

            self.balance_curve.append(self.balance)
            self.equity_curve.append(equity)
            self.drawdown_curve.append(drawdown)

        if self.position:
            last_bar = self.bars[-1]
            self._close_full_position(last_bar, safe_float(last_bar["close"]), "end_of_data")

            if self.balance_curve:
                self.balance_curve[-1] = self.balance
            if self.equity_curve:
                self.equity_curve[-1] = self.balance
            if self.drawdown_curve:
                self.peak_equity = max(self.peak_equity, self.balance)
                self.drawdown_curve[-1] = self.peak_equity - self.balance

        # ── Use centralized stats engine ───────────────────────
        lot_size_val = safe_float(
            self.payload.get("lot_size",
                self.engine_cfg.get("position_sizing", {}).get("fixed_lot", 1.0)),
            1.0
        )

        metrics = compute_all_stats(
            trades=self.trades,
            equity_curve=self.equity_curve,
            bars=self.bars,
            starting_capital=self.starting_capital,
            lot_size=lot_size_val,
        )

        analytics = self._build_analytics_payload()

        return {
            "strategy": self.strategy.get_metadata(),
            "config": {
                "strategy_id": self.strategy.strategy_id,
                "parameters": deepcopy(self.strategy_params),
                "engine": deepcopy(self.engine_cfg),
                "range": int(self.payload.get("range", 10)),
                "bar_range": int(self.payload.get("bar_range", 1000)),
                "starting_capital": self.starting_capital,
                "commission": deepcopy(self.payload.get("commission", {})),
                "monte_carlo_runs": int(self.payload.get("monte_carlo_runs", 0) or 0),
            },
            "trades": self.trades,
            "curves": {
                "balance_full": self.balance_curve,
                "balance_downsampled": ds_curve(self.balance_curve, 1500),
                "equity_full": self.equity_curve,
                "equity_downsampled": ds_curve(self.equity_curve, 1500),
                "drawdown_full": self.drawdown_curve,
                "drawdown_downsampled": ds_curve(self.drawdown_curve, 1500),
            },
            "metrics": metrics,
            "analytics": analytics,
            "equity_curve": ds_curve(self.equity_curve, 1500),
            "drawdown_curve": ds_curve(self.drawdown_curve, 1500),
            "stats": metrics,
        }