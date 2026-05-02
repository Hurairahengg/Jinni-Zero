# backend/engine_core.py
"""
JINNI ZERO — Slim Broker-Simulator Engine
Strategy is king. Engine executes orders, tracks equity, records trades.
"""
from __future__ import annotations

import math
import random
import time as _time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.stats_engine import compute_all_stats, downsample_curve


# ============================================================
# ENGINE DEFAULTS (MINIMAL)
# ============================================================
ENGINE_DEFAULTS = {
    "default_size": 1.0,
    "commission": {
        "type": "flat",
        "amount": 0.0,
    },
}


# ============================================================
# HELPERS
# ============================================================
def safe_float(v, default=0.0):
    try:
        if v is None:
            return default
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _clean(obj):
    """Remove NaN/Inf from JSON-serialisable tree. Fast-paths for common types."""
    if obj is None or isinstance(obj, (str, int, bool)):
        return obj
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, list):
        # Fast path: homogeneous numeric list (equity curves, MC paths, etc.)
        if obj and isinstance(obj[0], (int, float)):
            return [
                (None if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else v)
                for v in obj
            ]
        return [_clean(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    return obj


# ============================================================
# COMMISSION
# ============================================================
def calc_commission(cfg: dict, size: float) -> float:
    if not cfg:
        return 0.0
    t = cfg.get("type", "flat")
    amt = safe_float(cfg.get("amount", 0.0))
    if t == "flat":
        return amt
    if t == "per_side":
        return amt * 2.0
    if t == "per_contract":
        return amt * size
    return amt


# ============================================================
# INDICATOR PRECOMPUTE FUNCTIONS
# ============================================================
def _precompute_sma(values, period):
    n = len(values)
    out = [None] * n
    if period < 1 or n < period:
        return out
    s = 0.0
    for i in range(n):
        s += values[i]
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

    if len(compact) < sq:
        return out

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
        out[i] = max(values[i - period + 1: i + 1])
    return out


def _precompute_lowest(values, period):
    n = len(values)
    out = [None] * n
    if period < 1:
        return out
    for i in range(n):
        if i < period - 1:
            continue
        out[i] = min(values[i - period + 1: i + 1])
    return out


def _precompute_true_range(bars):
    out = [None] * len(bars)
    prev_close = None
    for i, b in enumerate(bars):
        h = safe_float(b["high"])
        l = safe_float(b["low"])
        if prev_close is None:
            tr = h - l
        else:
            tr = max(h - l, abs(h - prev_close), abs(l - prev_close))
        out[i] = tr
        prev_close = safe_float(b["close"])
    return out


def _precompute_vwap(bars):
    out = [None] * len(bars)
    pv_sum = 0.0
    vol_sum = 0.0
    for i, b in enumerate(bars):
        typical = (safe_float(b["high"]) + safe_float(b["low"]) + safe_float(b["close"])) / 3.0
        vol = max(0.0, safe_float(b.get("volume", 0.0)))
        pv_sum += typical * vol
        vol_sum += vol
        out[i] = (pv_sum / vol_sum) if vol_sum > 0 else typical
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


def precompute_indicator_series(bars, spec):
    kind = spec["kind"].upper()
    source = spec.get("source", "close")
    period = int(spec.get("period", 1))
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

    raise ValueError(f"Unsupported indicator kind: {kind}")


# ============================================================
# POSITION MODEL
# ============================================================
@dataclass
class Position:
    position_id: int
    direction: str
    entry_time: int
    entry_index: int
    entry_price: float
    size: float
    stop_loss: Optional[float]
    take_profit: Optional[float]
    entry_reason: Optional[str]
    risk_points: float
    highest_seen: float
    lowest_seen: float


# ============================================================
# POSITION VIEW (READ-ONLY FOR STRATEGY)
# ============================================================
class PositionView:
    __slots__ = (
        'direction', 'entry_price', 'size', 'stop_loss', 'take_profit',
        'entry_time', 'bars_held', 'highest_seen', 'lowest_seen',
        'unrealized_pnl', 'current_r', 'risk_points',
    )

    def __init__(self, pos: Position, current_price: float, bar_index: int):
        self.direction = pos.direction
        self.entry_price = pos.entry_price
        self.size = pos.size
        self.stop_loss = pos.stop_loss
        self.take_profit = pos.take_profit
        self.entry_time = pos.entry_time
        self.bars_held = bar_index - pos.entry_index
        self.highest_seen = pos.highest_seen
        self.lowest_seen = pos.lowest_seen
        self.risk_points = pos.risk_points

        if pos.direction == "long":
            self.unrealized_pnl = (current_price - pos.entry_price) * pos.size
        else:
            self.unrealized_pnl = (pos.entry_price - current_price) * pos.size

        if pos.risk_points > 0:
            if pos.direction == "long":
                self.current_r = (current_price - pos.entry_price) / pos.risk_points
            else:
                self.current_r = (pos.entry_price - current_price) / pos.risk_points
        else:
            self.current_r = 0.0


# ============================================================
# ANALYTICS BUILDER
# ============================================================
def build_analytics(trades, bars, equity_curve, dd_curve, starting_capital):
    t0 = _time.perf_counter()

    if not trades:
        return {}

    nets = [safe_float(t.get("net_pnl")) for t in trades]
    r_vals = [safe_float(t.get("net_pnl_r")) for t in trades]

    def hist(data, bins=20):
        if not data:
            return {"edges": [], "counts": []}
        mn, mx = min(data), max(data)
        if mn == mx:
            return {"edges": [mn, mx], "counts": [len(data)]}
        w = (mx - mn) / bins
        edges = [round(mn + i * w, 3) for i in range(bins + 1)]
        counts = [0] * bins
        for x in data:
            counts[min(int((x - mn) / w), bins - 1)] += 1
        return {"edges": edges, "counts": counts}

    # ── Rolling (20-trade window) ─────────────────
    W = 20
    roll_wr = []
    roll_exp = []
    roll_pf = []
    roll_sharpe = []
    for i in range(len(trades)):
        s = max(0, i - W + 1)
        chunk = [safe_float(t["net_pnl"]) for t in trades[s:i + 1]]
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

    dur_vals = [safe_float(t.get("bars_held")) for t in trades]

    mae_mfe = [
        {"mae": round(safe_float(t.get("mae_dollar")), 2),
         "mfe": round(safe_float(t.get("mfe_dollar")), 2),
         "win": safe_float(t.get("net_pnl")) > 0}
        for t in trades
        if t.get("mae_dollar") is not None and t.get("mfe_dollar") is not None
    ]

    ret_scatter = [
        {"x": i + 1, "y": round(r, 3), "win": r > 0}
        for i, r in enumerate(r_vals)
    ]

    # ── Time breakdowns ──────────────────────────
    def _ts_dt(ts):
        try:
            t = float(ts)
            if t > 1e12:
                t /= 1000
            return datetime.fromtimestamp(t, tz=timezone.utc)
        except Exception:
            return None

    DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    by_hour = defaultdict(list)
    by_dow = defaultdict(list)
    by_mon = defaultdict(list)
    for t in trades:
        dt = _ts_dt(t.get("entry_time"))
        if not dt:
            continue
        pnl = safe_float(t.get("net_pnl"))
        by_hour[dt.hour].append(pnl)
        by_dow[dt.weekday()].append(pnl)
        by_mon[dt.month - 1].append(pnl)

    def _perf(d):
        return {
            "trades": len(d), "net": round(sum(d), 2),
            "wr": round(len([x for x in d if x > 0]) / len(d) * 100, 1) if d else 0,
        }

    hour_perf = {str(h): _perf(v) for h, v in by_hour.items()}
    dow_perf = {DOW[d]: _perf(v) for d, v in by_dow.items()}
    mon_perf = {MONTHS[m]: _perf(v) for m, v in by_mon.items()} 

    # ── Regime ────────────────────────────────────
    bar_ranges = [safe_float(b["high"]) - safe_float(b["low"]) for b in bars]
    vol_regime = {}
    if bar_ranges:
        med_rng = sorted(bar_ranges)[len(bar_ranges) // 2]
        by_vol = {"low_vol": [], "high_vol": []}
        for t in trades:
            bi = int(safe_float(t.get("entry_index", t.get("entry_bar", 0))))
            if bi < len(bar_ranges):
                k = "low_vol" if bar_ranges[bi] < med_rng else "high_vol"
                by_vol[k].append(safe_float(t.get("net_pnl")))
        vol_regime = {k: _perf(v) for k, v in by_vol.items() if v}

    chop_by = {"trending": [], "choppy": []}
    for t in trades:
        bi = int(safe_float(t.get("entry_index", t.get("entry_bar", 0))))
        if bi >= 3:
            last3 = [safe_float(bars[bi - j]["close"]) - safe_float(bars[bi - j]["open"])
                      for j in range(1, 4)]
            same = all(x > 0 for x in last3) or all(x < 0 for x in last3)
            chop_by["trending" if same else "choppy"].append(safe_float(t.get("net_pnl")))
    chop_regime = {k: _perf(v) for k, v in chop_by.items() if v}

    # ── Commission ────────────────────────────────
    total_comm = sum(safe_float(t.get("commission")) for t in trades)
    gross_sum = sum(safe_float(t.get("gross_pnl")) for t in trades)
    net_sum = sum(safe_float(t.get("net_pnl")) for t in trades)
    gross_abs = sum(abs(safe_float(t.get("gross_pnl"))) for t in trades)

    return {
        "rolling": {
            "win_rate": roll_wr, "expectancy": roll_exp,
            "profit_factor": roll_pf, "sharpe": roll_sharpe,
        },
        "r_histogram": hist(r_vals, 20),
        "duration_histogram": hist(dur_vals, 15),
        "mae_mfe": mae_mfe,
        "return_scatter": ret_scatter,
        "time_of_day": hour_perf,
        "day_of_week": dow_perf,
        "by_month": mon_perf,
        "regime": {"volatility": vol_regime, "choppiness": chop_regime},
        "monte_carlo": {},
        "commission": {
            "total": round(total_comm, 2),
            "per_trade": round(total_comm / len(trades), 2) if trades else 0,
            "net_without_comm": round(gross_sum, 2),
            "net_with_comm": round(net_sum, 2),
            "pct_of_gross": round(total_comm / gross_abs * 100, 2) if gross_abs else 0,
        },
    }


# ============================================================
# BACKTEST ENGINE (SLIM BROKER SIMULATOR)
# ============================================================
class BacktestEngine:
    def __init__(self, bars: List[dict], strategy, payload: dict):
        self.bars = [dict(b, index=i) for i, b in enumerate(bars)]
        self.strategy = strategy
        self.payload = payload or {}

        self.params = strategy.validate_parameters(
            self.payload.get("parameters", {})
        )

        self.engine_cfg = {**ENGINE_DEFAULTS}

        self.starting_capital = safe_float(
            self.payload.get("starting_capital", 10000.0), 10000.0
        )
        self.balance = self.starting_capital
        self.equity = self.starting_capital

        self.position: Optional[Position] = None
        self.position_counter = 0

        self.trades: List[dict] = []
        self.equity_curve: List[float] = []
        self.drawdown_curve: List[float] = []
        self.peak_equity = self.starting_capital

        self.state: Dict[str, Any] = {}

        self.indicator_plan = strategy.build_indicators(self.params)
        self.indicator_store = {}
        for spec in self.indicator_plan:
            self.indicator_store[spec["key"]] = precompute_indicator_series(self.bars, spec)

    def _indicators_at(self, i):
        return {k: (v[i] if i < len(v) else None) for k, v in self.indicator_store.items()}

    def _mtm(self, price):
        if not self.position:
            return self.balance
        if self.position.direction == "long":
            return self.balance + (price - self.position.entry_price) * self.position.size
        return self.balance + (self.position.entry_price - price) * self.position.size

    def _pos_view(self, bar):
        if not self.position or not bar:
            return None
        return PositionView(self.position, safe_float(bar["close"]), bar["index"])

    def _record_trade(self, bar, exit_price, reason):
        pos = self.position
        if not pos:
            return

        ep = pos.entry_price
        xp = exit_price
        d = pos.direction

        points = (xp - ep) if d == "long" else (ep - xp)
        gross = points * pos.size
        comm = calc_commission(self.payload.get("commission", {}), pos.size)
        net = gross - comm

        r_mult = None
        if pos.risk_points > 0:
            r_mult = round(points / pos.risk_points, 3)

        if d == "long":
            mae_pts = max(0.0, ep - pos.lowest_seen)
            mfe_pts = max(0.0, pos.highest_seen - ep)
        else:
            mae_pts = max(0.0, pos.highest_seen - ep)
            mfe_pts = max(0.0, ep - pos.lowest_seen)

        self.trades.append({
            "position_id": pos.position_id,
            "direction": d,
            "entry_time": pos.entry_time,
            "exit_time": bar["time"],
            "entry_price": round(ep, 4),
            "exit_price": round(xp, 4),
            "size": pos.size,
            "stop_loss": pos.stop_loss,
            "take_profit": pos.take_profit,
            "entry_reason": pos.entry_reason,
            "exit_reason": reason,
            "bars_held": bar["index"] - pos.entry_index,
            "holding_seconds": abs(bar["time"] - pos.entry_time),
            "points_pnl": round(points, 4),
            "gross_pnl": round(gross, 2),
            "commission": round(comm, 2),
            "net_pnl": round(net, 2),
            "net_pnl_r": r_mult,
            "risk_pts": round(pos.risk_points, 4) if pos.risk_points > 0 else None,
            "mae": round(mae_pts, 4),
            "mfe": round(mfe_pts, 4),
            "mae_dollar": round(mae_pts * pos.size, 2),
            "mfe_dollar": round(mfe_pts * pos.size, 2),
            "entry_bar": pos.entry_index,
            "entry_index": pos.entry_index,
        })

        self.balance += net
        self.position = None

    def _ctx(self, bar):
        return type("Ctx", (), {
            "index": bar["index"] if bar else None,
            "bar": bar,
            "bars": self.bars,
            "indicators": self._indicators_at(bar["index"]) if bar else {},
            "ind_series": self.indicator_store,
            "position": self._pos_view(bar),
            "balance": self.balance,
            "equity": self.equity,
            "trades": self.trades,
            "params": self.params,
            "state": self.state,
        })()

    def run(self):
        perf = {}
        total_t0 = _time.perf_counter()

        # ── Simulation ────────────────────────────
        sim_t0 = _time.perf_counter()

        init_ctx = self._ctx(self.bars[0] if self.bars else None)
        self.strategy.on_init(init_ctx)

        for i, bar in enumerate(self.bars):
            close = safe_float(bar["close"])

            if self.position:
                self.position.highest_seen = max(
                    self.position.highest_seen, safe_float(bar["high"])
                )
                self.position.lowest_seen = min(
                    self.position.lowest_seen, safe_float(bar["low"])
                )

            self.equity = self._mtm(close)
            self.peak_equity = max(self.peak_equity, self.equity)
            self.equity_curve.append(round(self.equity, 2))
            self.drawdown_curve.append(round(self.peak_equity - self.equity, 2))

            ctx = self._ctx(bar)
            action = self.strategy.on_bar(ctx) or {}

            if self.position and action.get("exit"):
                xp = safe_float(action.get("exit_price", close), close)
                self._record_trade(bar, xp, action.get("reason", "strategy_exit"))
                self.equity = self._mtm(close)
                self.equity_curve[-1] = round(self.equity, 2)
                self.drawdown_curve[-1] = round(self.peak_equity - self.equity, 2)
                continue

            if self.position:
                if "update_sl" in action:
                    self.position.stop_loss = action["update_sl"]
                if "update_tp" in action:
                    self.position.take_profit = action["update_tp"]

            if not self.position and action.get("enter") in ("long", "short"):
                self.position_counter += 1
                direction = action["enter"]
                ep = safe_float(action.get("entry_price", close), close)
                size = safe_float(action.get("size"), self.engine_cfg["default_size"])

                sl = action.get("stop_loss")
                tp = action.get("take_profit")
                risk = abs(ep - sl) if sl is not None else 0.0

                self.position = Position(
                    position_id=self.position_counter,
                    direction=direction,
                    entry_time=bar["time"],
                    entry_index=i,
                    entry_price=ep,
                    size=size,
                    stop_loss=sl,
                    take_profit=tp,
                    entry_reason=action.get("reason"),
                    risk_points=risk,
                    highest_seen=safe_float(bar["high"]),
                    lowest_seen=safe_float(bar["low"]),
                )
                continue

            if self.position:
                pos = self.position
                sl = pos.stop_loss
                tp = pos.take_profit
                hi = safe_float(bar["high"])
                lo = safe_float(bar["low"])

                sl_hit = False
                tp_hit = False

                if pos.direction == "long":
                    if sl is not None and lo <= sl:
                        sl_hit = True
                    if tp is not None and hi >= tp:
                        tp_hit = True
                else:
                    if sl is not None and hi >= sl:
                        sl_hit = True
                    if tp is not None and lo <= tp:
                        tp_hit = True

                if sl_hit:
                    self._record_trade(bar, sl, "stop_loss")
                    self.equity = self._mtm(close)
                    self.equity_curve[-1] = round(self.equity, 2)
                    self.drawdown_curve[-1] = round(self.peak_equity - self.equity, 2)
                elif tp_hit:
                    self._record_trade(bar, tp, "take_profit")
                    self.equity = self._mtm(close)
                    self.equity_curve[-1] = round(self.equity, 2)
                    self.drawdown_curve[-1] = round(self.peak_equity - self.equity, 2)

        if self.position and self.bars:
            last = self.bars[-1]
            self._record_trade(last, safe_float(last["close"]), "end_of_data")
            self.equity = self._mtm(safe_float(last["close"]))
            if self.equity_curve:
                self.equity_curve[-1] = round(self.equity, 2)
            if self.drawdown_curve:
                self.peak_equity = max(self.peak_equity, self.equity)
                self.drawdown_curve[-1] = round(self.peak_equity - self.equity, 2)

        end_ctx = self._ctx(self.bars[-1] if self.bars else None)
        self.strategy.on_end(end_ctx)

        sim_t1 = _time.perf_counter()
        perf["simulation_seconds"] = round(sim_t1 - sim_t0, 4)

        # ── Stats ─────────────────────────────────
        stats_t0 = _time.perf_counter()
        lot_size = safe_float(self.payload.get("lot_size", self.engine_cfg["default_size"]))

        stats = compute_all_stats(
            trades=self.trades,
            equity_curve=self.equity_curve,
            bars=self.bars,
            starting_capital=self.starting_capital,
            lot_size=lot_size,
        )
        stats_t1 = _time.perf_counter()
        perf["stats_seconds"] = round(stats_t1 - stats_t0, 4)

        # ── Analytics ─────────────────────────────
        analytics_t0 = _time.perf_counter()
        analytics = build_analytics(
            trades=self.trades,
            bars=self.bars,
            equity_curve=self.equity_curve,
            dd_curve=self.drawdown_curve,
            starting_capital=self.starting_capital,
        )
        analytics_t1 = _time.perf_counter()
        perf["analytics_seconds"] = round(analytics_t1 - analytics_t0, 4)

        # ── Response build ────────────────────────
        build_t0 = _time.perf_counter()
        result = {
            "stats": stats,
            "metrics": stats,
            "trades": self.trades,
            "equity_curve": downsample_curve(self.equity_curve, 1500),
            "drawdown_curve": downsample_curve(self.drawdown_curve, 1500),
            "analytics": analytics,
            "config": {
                "strategy_id": self.strategy.strategy_id,
                "parameters": self.params,
                "starting_capital": self.starting_capital,
                "range": int(self.payload.get("range", 10)),
                "lot_size": lot_size,
            },
        }

        result = _clean(result)

        build_t1 = _time.perf_counter()
        perf["response_build_seconds"] = round(build_t1 - build_t0, 4)
        perf["total_seconds"] = round(build_t1 - total_t0, 4)
        perf["trade_count"] = len(self.trades)

        result["performance"] = perf

        print(f"  [BACKTEST TIMING] simulation={perf['simulation_seconds']:.3f}s "
              f"stats={perf['stats_seconds']:.3f}s "
              f"analytics={perf['analytics_seconds']:.3f}s "
              f"response_build={perf['response_build_seconds']:.3f}s "
              f"total={perf['total_seconds']:.3f}s "
              f"trades={perf['trade_count']}")

        return result