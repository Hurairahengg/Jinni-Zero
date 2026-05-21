# Repository Snapshot - Part 4 of 5

- Root folder: `/home/hurairahengg/Documents/Jinni Zero`
- You know my wholle Jinjnibacktester simulator thign whre ther is a UI bascially and then i can see  charst and stuff when i need to run simulatiosn liek i send simulatio nto my flask backend server it runs sims and then shows stast and stuff and i can load strategy and shit for now take a look we will be doing bug fixes and some validation and shit. udnerrtsnad each code and its role how it works and keep in ir conetxt i will ask u exactly wha tto do later code later duinerstood
- Total files indexed: `24`
- Files in this chunk: `2`
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

## Files In This Chunk - Part 4

```text
backend/engine_core.py
js/chart.js
```

## File Contents


---

## FILE: `backend/engine_core.py`

```python
"""
JINNI ZERO — Strategy Backtest Engine (Legacy-Compatible Execution)
===================================================================
Uses the EXACT SAME execution logic as backtest_server.py:
  - pending_signal → enter next bar OPEN
  - SL/TP hit detection (ambiguous bar handling)
  - MA cross exit support (engine-level, matching legacy)
  - Engine-computed SL/TP (fixed pts, MA snapshot, R-multiple)
  - Spread simulation
  - Commission calculation (legacy calc_comm)
  - Equity/drawdown tracking
  - PnL via centralized dollar_math.py

Streaming support: run_streaming() yields NDJSON progress + result.
Trade cap: response limited to last MAX_TRADES_IN_RESPONSE trades.
Analytics cap: rolling/scatter arrays downsampled to MAX_ANALYTICS_POINTS.
"""
from __future__ import annotations

import logging
import math
import time as _time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from backend.stats_engine import compute_all_stats, downsample_curve
from backend.dollar_math import (
    points_to_dollars,
    finalize_trade_pnl,
    compute_position_size,
    compute_scaling_risk,
)
from backend.strategies.base import VALID_SIGNALS
from backend.shared import (
    precompute_ma,
    get_or_compute_ma,
    SpreadGenerator,
    compute_analytics,
    clean_for_json,
    cap_analytics_arrays,
)

logger = logging.getLogger("jinni.engine")

# ── Response caps ────────────────────────────────────────────────
MAX_TRADES_IN_RESPONSE = 2000
MAX_ANALYTICS_POINTS = 1500
PROGRESS_EMIT_INTERVAL = 0.15


# ════════════════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════════════════
def safe_float(v, default=0.0):
    try:
        if v is None:
            return default
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


# ════════════════════════════════════════════════════════════════════
#  ADDITIONAL INDICATOR PRECOMPUTE (for strategy build_indicators)
# ════════════════════════════════════════════════════════════════════
def _source_values(bars, source):
    if source == "open":  return [safe_float(b["open"])  for b in bars]
    if source == "high":  return [safe_float(b["high"])  for b in bars]
    if source == "low":   return [safe_float(b["low"])   for b in bars]
    return [safe_float(b["close"]) for b in bars]


def precompute_indicator_series(bars, spec):
    kind   = spec["kind"].upper()
    source = spec.get("source", "close")
    period = int(spec.get("period", 1))
    values = _source_values(bars, source)
    if kind in ("SMA", "EMA", "WMA", "HMA"):
        return precompute_ma(values, kind, period)
    raise ValueError(f"Unsupported indicator kind: {kind}")


# ════════════════════════════════════════════════════════════════════
#  SIGNAL VALIDATION
# ════════════════════════════════════════════════════════════════════
def validate_signal(raw, bar_index):
    if raw is None:
        return {"signal": "HOLD"}
    if not isinstance(raw, dict):
        raise ValueError(
            f"Bar {bar_index}: strategy returned {type(raw).__name__}, expected dict or None"
        )

    sig = raw.get("signal")
    if sig is not None:
        sig = str(sig).upper()
    if sig not in VALID_SIGNALS:
        raise ValueError(
            f"Bar {bar_index}: invalid signal '{sig}'. Must be BUY/SELL/HOLD/CLOSE/None"
        )

    out = {"signal": sig or "HOLD"}

    if raw.get("sl") is not None:
        out["sl"] = float(raw["sl"])
    if raw.get("tp") is not None:
        out["tp"] = float(raw["tp"])

    # Engine-computed SL/TP fields (Phase 3)
    for key in ("sl_mode", "sl_pts", "sl_ma_key", "sl_ma_val",
                "tp_mode", "tp_r"):
        if raw.get(key) is not None:
            out[key] = raw[key] if key in ("sl_mode", "sl_ma_key", "tp_mode") else float(raw[key])

    # Engine-level MA cross exit keys (Phase 4)
    if raw.get("engine_sl_ma_key") is not None:
        out["engine_sl_ma_key"] = str(raw["engine_sl_ma_key"])
    if raw.get("engine_tp_ma_key") is not None:
        out["engine_tp_ma_key"] = str(raw["engine_tp_ma_key"])

    if out["signal"] == "CLOSE":
        out["close"] = True
        out["close_reason"] = str(raw.get("close_reason", "strategy_close"))
    elif raw.get("close"):
        out["close"] = True
        out["close_reason"] = str(raw.get("close_reason", "strategy_close"))

    if raw.get("update_sl") is not None:
        out["update_sl"] = float(raw["update_sl"])
    if raw.get("update_tp") is not None:
        out["update_tp"] = float(raw["update_tp"])

    return out


# ════════════════════════════════════════════════════════════════════
#  POSITION STATE (frozen — read-only view passed to strategy)
# ════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class PositionState:
    has_position:    bool
    direction:       Optional[str]   = None
    entry_price:     Optional[float] = None
    entry_time:      Optional[int]   = None
    entry_bar:       Optional[int]   = None
    bars_held:       int             = 0
    sl_level:        Optional[float] = None
    tp_level:        Optional[float] = None
    unrealized_pts:  float           = 0.0
    unrealized_pnl:  float           = 0.0
    unrealized_r:    Optional[float] = None
    mae:             float           = 0.0
    mfe:             float           = 0.0


def _build_position_state(open_t, bar_index, close_price, lot_size, point_value=1.0, dollar_per_point=1.0):
    if open_t is None:
        return PositionState(has_position=False)
    d = open_t["direction"]
    ep = open_t["entry_price"]
    pts = (close_price - ep) if d == "long" else (ep - close_price)
    rp = open_t.get("risk_pts")
    ur = round(pts / rp, 3) if rp and rp > 0 else None
    return PositionState(
        has_position=True, direction=d, entry_price=ep,
        entry_time=open_t.get("entry_time"), entry_bar=open_t.get("entry_bar"),
        bars_held=bar_index - open_t.get("entry_bar", 0),
        sl_level=open_t.get("sl_level"), tp_level=open_t.get("tp_level"),
        unrealized_pts=round(pts, 4),
        unrealized_pnl=round(points_to_dollars(pts, lot_size, point_value, dollar_per_point), 2),
        unrealized_r=ur,
        mae=round(open_t.get("mae", 0), 4),
        mfe=round(open_t.get("mfe", 0), 4),
    )


# ════════════════════════════════════════════════════════════════════
#  STRATEGY CONTEXT
# ════════════════════════════════════════════════════════════════════
class StrategyContext:
    __slots__ = (
        "index", "bar", "bars", "indicators", "ind_series",
        "position", "balance", "equity", "trades", "params", "state",
        "prev_indicators",
    )

    def __init__(self, *, index, bar, bars, indicators, ind_series,
                 position, balance, equity, trades, params, state,
                 prev_indicators):
        self.index           = index
        self.bar             = bar
        self.bars            = bars
        self.indicators      = indicators
        self.ind_series      = ind_series
        self.position        = position
        self.balance         = balance
        self.equity          = equity
        self.trades          = trades
        self.params          = params
        self.state           = state
        self.prev_indicators = prev_indicators


# ════════════════════════════════════════════════════════════════════
#  BACKTEST ENGINE
# ════════════════════════════════════════════════════════════════════
class BacktestEngine:

    def __init__(self, bars, strategy, payload):
        self.bars       = bars
        self.strategy   = strategy
        self.payload    = payload or {}
        self.n          = len(bars)

        self.params = strategy.validate_parameters(
            self.payload.get("parameters", {})
        )

        self.lot_size       = float(self.payload.get("lot_size", 1.0))
        self.point_value    = float(self.payload.get("point_value", 1.0))
        self.dollar_per_point = float(self.payload.get("dollar_per_point", 1.0))
        self.starting_cap   = float(self.payload.get("starting_capital", 10000.0))
        self.commission_per_lot = float(self.payload.get("commission_per_lot", 0))
        self.ambiguous_mode = self.payload.get("ambiguous_bar_mode", "conservative")

        self.spread_gen = SpreadGenerator(self.payload.get("spread", {}))
        # ── Position sizing ──────────────────────────────────────
        self.sizing_mode = str(self.payload.get("sizing_mode", "fixed")).strip().lower()
        self.risk_pct = float(self.payload.get("risk_pct", 1.0))
        self.fixed_risk = float(self.payload.get("fixed_risk", 10.0))
        self.scaling_enabled = bool(self.payload.get("scaling_enabled", False))
        self.scaling_per = float(self.payload.get("scaling_per", 100.0))
        self.scaling_risk = float(self.payload.get("scaling_risk", 1.0))
        self.min_lot = float(self.payload.get("min_lot", 0.01))
        self.max_lot = float(self.payload.get("max_lot", 1000.0))
        self.lot_step = float(self.payload.get("lot_step", 0.01))

        strategy_min = getattr(strategy, "min_lookback", 0) or 0
        user_override = int(self.payload.get("lookback_override", 0) or 0)
        self.lookback = max(strategy_min, user_override)

        self.indicator_plan = strategy.build_indicators(self.params)
        self.indicator_store = {}
        for spec in self.indicator_plan:
            self.indicator_store[spec["key"]] = precompute_indicator_series(self.bars, spec)

        self.trades       = []
        self.equity_curve = []
        self.dd_curve     = []

        self._perf = {}
        self._total_t0 = None

        sizing_str = f"lot={self.lot_size}" if self.sizing_mode == "fixed" \
                     else f"risk={self.risk_pct}%"
        print(
            f"  [ENGINE] {self.n} bars | strategy={strategy.strategy_id} | "
            f"{sizing_str} | pv={self.point_value} | dpp={self.dollar_per_point} | cap={self.starting_cap} | "
            f"lookback={self.lookback} | spread={self.spread_gen.enabled} | "
            f"ambiguous={self.ambiguous_mode}"
        )

    # ── Indicator helpers ────────────────────────────────────────
    def _indicators_at(self, i):
        return {
            k: (v[i] if i < len(v) else None)
            for k, v in self.indicator_store.items()
        }

    def _build_ctx(self, i, bar, open_t, cum, equity, prev_indicators=None):
        trade_lot = open_t.get("lot_size", self.lot_size) if open_t else self.lot_size
        pos_state = _build_position_state(
            open_t, i, float(bar["close"]), trade_lot, self.point_value, self.dollar_per_point
        )
        return StrategyContext(
            index=i, bar=bar, bars=self.bars,
            indicators=self._indicators_at(i),
            ind_series=self.indicator_store,
            position=pos_state,
            balance=round(self.starting_cap + cum, 2),
            equity=round(equity, 2),
            trades=self.trades,
            params=self.params,
            state=self._strategy_state,
            prev_indicators=prev_indicators or {},
        )

    # ════════════════════════════════════════════════════════════════
    #  EXIT CHECK (SL/TP hit + MA cross exits)
    # ════════════════════════════════════════════════════════════════
    def _check_exit(self, t, bar, bi):
        d  = t["direction"]
        ep = t["entry_price"]
        sl = t.get("sl_level")
        tp = t.get("tp_level")
        hh = bar["high"]; ll = bar["low"]; c = bar["close"]

        t["bars_held"] = bi - t["entry_bar"]
        t["mae"] = max(t.get("mae", 0), (ep - ll) if d == "long" else (hh - ep))
        t["mfe"] = max(t.get("mfe", 0), (hh - ep) if d == "long" else (ep - ll))

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
                if abs(bar["open"] - sl) <= abs(bar["open"] - tp):
                    return self._make_exit(t, bar, bi, sl, "SL_HIT")
                else:
                    return self._make_exit(t, bar, bi, tp, "TP_R")
            else:
                return self._make_exit(t, bar, bi, sl, "SL_HIT")

        if sl_hit: return self._make_exit(t, bar, bi, sl, "SL_HIT")
        if tp_hit: return self._make_exit(t, bar, bi, tp, "TP_R")

        # MA cross exits (Phase 4)
        sl_ma_key = t.get("_engine_sl_ma_key")
        if sl_ma_key and sl_ma_key in self.indicator_store:
            sl_ma_series = self.indicator_store[sl_ma_key]
            sl_ma_val = sl_ma_series[bi] if bi < len(sl_ma_series) else None
            if sl_ma_val is not None:
                if d == "long" and c < sl_ma_val:
                    return self._make_exit(t, bar, bi, c, "MA_SL_EXIT")
                if d == "short" and c > sl_ma_val:
                    return self._make_exit(t, bar, bi, c, "MA_SL_EXIT")

        tp_ma_key = t.get("_engine_tp_ma_key")
        if tp_ma_key and tp_ma_key in self.indicator_store:
            tp_ma_series = self.indicator_store[tp_ma_key]
            tp_ma_val = tp_ma_series[bi] if bi < len(tp_ma_series) else None
            if tp_ma_val is not None:
                if d == "long" and c < tp_ma_val:
                    return self._make_exit(t, bar, bi, c, "MA_TP_EXIT")
                if d == "short" and c > tp_ma_val:
                    return self._make_exit(t, bar, bi, c, "MA_TP_EXIT")

        return None

    def _make_exit(self, t, bar, bi, exit_price, reason):
        return {
            **t,
            "exit_bar": bi, "exit_time": bar["time"],
            "exit_price": round(exit_price, 4),
            "exit_reason": reason,
            "holding_seconds": abs(bar["time"] - t["entry_time"]),
            "bars_held": bi - t["entry_bar"],
        }

    def _finalize_trade(self, closed):
        commission = round(closed.get("lot_size", self.lot_size) * self.commission_per_lot, 5)
        trade_lot = closed.get("lot_size", self.lot_size)
        finalize_trade_pnl(
            closed, lot_size=trade_lot,
            point_value=self.point_value, dollar_per_point=self.dollar_per_point,
            commission=commission,
        )

    def _close_trade(self, open_t, bar, bi, exit_price, reason, cum):
        d = open_t["direction"]; ep = open_t["entry_price"]
        hh = bar["high"]; ll = bar["low"]
        open_t["bars_held"] = bi - open_t["entry_bar"]
        open_t["mae"] = max(open_t.get("mae", 0), (ep - ll) if d == "long" else (hh - ep))
        open_t["mfe"] = max(open_t.get("mfe", 0), (hh - ep) if d == "long" else (ep - ll))

        closed = self._make_exit(open_t, bar, bi, exit_price, reason)
        trade_spread = closed.get("spread", 0.0)
        # Only apply exit spread for market-price exits.
        # SL_HIT / TP_R exit prices already include spread from entry setup.
        if reason not in ("SL_HIT", "TP_R"):
            closed["exit_price"] = round(
                self.spread_gen.apply_exit(exit_price, closed["direction"], trade_spread), 4
            )
        closed.pop("_engine_sl_ma_key", None)
        closed.pop("_engine_tp_ma_key", None)

        self._finalize_trade(closed)
        cum += closed["net_pnl"]
        closed["cumulative_pnl"] = round(cum, 2)
        self.trades.append(closed)

        if len(self.trades) <= 5:
            t = closed
            print(
                f"  [TRADE #{t['id']}] {t['direction'].upper()} "
                f"entry={t['entry_price']} exit={t['exit_price']} "
                f"SL={t.get('sl_level')} TP={t.get('tp_level')} "
                f"risk={t.get('risk_pts')}pts "
                f"points={t.get('points_pnl')} R={t.get('net_pnl_r')} "
                f"gross=${t.get('gross_pnl')} comm=${t.get('commission')} "
                f"net=${t.get('net_pnl')} reason={t.get('exit_reason')}"
            )
        return cum

    # ════════════════════════════════════════════════════════════════
    #  SIMULATION GENERATOR (yields progress, populates self.trades)
    # ════════════════════════════════════════════════════════════════
    def _run_generator(self):
        self._total_t0 = _time.perf_counter()

        # Init strategy
        self._strategy_state = {}
        init_ctx = self._build_ctx(0, self.bars[0], None, 0.0, self.starting_cap)
        self.strategy.on_init(init_ctx)

        state = "flat"
        open_t = None
        pending_signal = None
        cum = 0.0
        peak = self.starting_cap
        prev_indicators = {}
        last_closed_pnl = None

        progress_interval = max(1, self.n // 100)
        last_emit_time = _time.perf_counter()

        sim_t0 = _time.perf_counter()

        for i, bar in enumerate(self.bars):
            c = float(bar["close"]); o = float(bar["open"])
            h = float(bar["high"]);  l = float(bar["low"])

            # ══ STEP 1: Process pending entry ═══════════════════
            if pending_signal is not None and state == "flat":
                direction = pending_signal["direction"]
                ep = pending_signal.get("entry_price", o)

                sl_level = None; risk_pts = None
                sl_mode = pending_signal.get("sl_mode")

                if sl_mode == "fixed":
                    pts = float(pending_signal.get("sl_pts", 0))
                    if pts > 0:
                        sl_level = (ep - pts) if direction == "long" else (ep + pts)
                        sl_level = round(sl_level, 4); risk_pts = pts
                elif sl_mode == "ma_snapshot":
                    ma_val = pending_signal.get("sl_ma_val")
                    if ma_val is not None:
                        if direction == "long" and ma_val < ep:
                            sl_level = round(ma_val, 4); risk_pts = round(abs(ep - sl_level), 4)
                        elif direction == "short" and ma_val > ep:
                            sl_level = round(ma_val, 4); risk_pts = round(abs(ep - sl_level), 4)
                elif pending_signal.get("sl") is not None:
                    sl_level = float(pending_signal["sl"])
                    risk_pts = abs(ep - sl_level) if sl_level is not None else None

                tp_level = None
                tp_mode = pending_signal.get("tp_mode")
                if tp_mode == "r_multiple":
                    r = float(pending_signal.get("tp_r", 2))
                    if risk_pts and risk_pts > 0:
                        tp_level = (ep + risk_pts * r) if direction == "long" else (ep - risk_pts * r)
                        tp_level = round(tp_level, 4)
                elif pending_signal.get("tp") is not None:
                    tp_level = float(pending_signal["tp"])

                trade_spread = self.spread_gen.generate()
                ep = self.spread_gen.apply_entry(ep, direction, trade_spread)
                sl_level = self.spread_gen.apply_sl(sl_level, direction, trade_spread)
                tp_level = self.spread_gen.apply_tp(tp_level, direction, trade_spread)
                if sl_level is not None:
                    risk_pts = abs(ep - sl_level)

                valid = True
                if risk_pts is not None and risk_pts <= 0: valid = False
                if sl_level is not None:
                    if direction == "long"  and sl_level >= ep: valid = False
                    if direction == "short" and sl_level <= ep: valid = False
                if tp_level is not None:
                    if direction == "long"  and tp_level <= ep: valid = False
                    if direction == "short" and tp_level >= ep: valid = False

                # ── Dynamic position sizing (centralized) ────────
                trade_lot = self.lot_size
                if valid and self.sizing_mode in ("risk_pct", "risk_per_trade"):
                    if risk_pts is None or risk_pts <= 0:
                        valid = False
                    else:
                        balance_now = self.starting_cap + cum

                        risk_amount = 0.0
                        if self.sizing_mode == "risk_pct":
                            risk_amount = balance_now * (self.risk_pct / 100.0)
                        elif self.scaling_enabled:
                            risk_amount, sc_log = compute_scaling_risk(
                                balance_now, self.scaling_per, self.scaling_risk)
                            if len(self.trades) < 5:
                                print(f"  {sc_log}")
                        else:
                            risk_amount = self.fixed_risk

                        if risk_amount <= 0:
                            valid = False
                        else:
                            trade_lot, sz_log, sz_ok = compute_position_size(
                                risk_amount, risk_pts, self.point_value,
                                self.min_lot, self.max_lot, self.lot_step,
                                self.commission_per_lot, self.dollar_per_point,
                            )
                            if len(self.trades) < 5:
                                print(f"  {sz_log}")
                            if not sz_ok or trade_lot is None:
                                valid = False
                else:
                    trade_lot = self.lot_size

                if valid:
                    open_t = dict(
                        id=len(self.trades) + 1, direction=direction,
                        entry_bar=pending_signal.get("signal_bar", i),
                        entry_time=pending_signal.get("signal_time", bar["time"]),
                        entry_price=round(ep, 4),
                        sl_level=sl_level, tp_level=tp_level,
                        initial_sl=sl_level, initial_tp=tp_level,
                        risk_pts=risk_pts, initial_risk_pts=risk_pts,
                        mae=0.0, mfe=0.0, bars_held=0,
                        spread=round(trade_spread, 4),
                        lot_size=trade_lot,
                        _engine_sl_ma_key=pending_signal.get("engine_sl_ma_key"),
                        _engine_tp_ma_key=pending_signal.get("engine_tp_ma_key"),
                    )
                    state = direction
                pending_signal = None

            # ══ STEP 2: Exit check (BEFORE management — uses previous bar's SL/TP)
            if state != "flat" and open_t is not None:
                closed = self._check_exit(open_t, bar, i)
                if closed:
                    cum = self._close_trade(
                        open_t, bar, i, closed["exit_price"], closed["exit_reason"], cum
                    )
                    last_closed_pnl = self.trades[-1]["net_pnl"]
                    open_t = None; state = "flat"

            # ══ STEP 3: Strategy call ═══════════════════════════
            action = {"signal": "HOLD"}
            ctx = None
            if i >= self.lookback:
                equity_now = self.starting_cap + cum
                if state != "flat" and open_t:
                    pts = (c - open_t["entry_price"]) if state == "long" \
                          else (open_t["entry_price"] - c)
                    t_lot = open_t.get("lot_size", self.lot_size)
                    equity_now += points_to_dollars(pts, t_lot, self.point_value, self.dollar_per_point)
                ctx = self._build_ctx(i, bar, open_t, cum, equity_now, prev_indicators)
                raw_signal = self.strategy.on_bar(ctx)
                try:
                    action = validate_signal(raw_signal, i)
                except ValueError as e:
                    logger.error(str(e)); action = {"signal": "HOLD"}
            sig = action["signal"]

            # ══ STEP 4: Process CLOSE signal
            if action.get("close") and state != "flat" and open_t is not None:
                reason = action.get("close_reason", "strategy_close")
                cum = self._close_trade(open_t, bar, i, c, reason, cum)
                last_closed_pnl = self.trades[-1]["net_pnl"]
                open_t = None; state = "flat"
                # Re-call strategy flat to check for flip
                if i >= self.lookback:
                    eq_now = self.starting_cap + cum
                    ctx = self._build_ctx(i, bar, None, cum, eq_now, prev_indicators)
                    raw2 = self.strategy.on_bar(ctx)
                    try: action = validate_signal(raw2, i)
                    except ValueError: action = {"signal": "HOLD"}
                    sig = action["signal"]

            # ══ STEP 5: Trade management (on_manage + on_bar updates) ═══
            if state != "flat" and open_t is not None and ctx is not None:
                # on_manage hook
                mgmt = None
                try:
                    mgmt = self.strategy.on_manage(ctx)
                except Exception as e:
                    logger.error(f"Bar {i}: on_manage error: {e}")

                if mgmt and isinstance(mgmt, dict):
                    if mgmt.get("close") and not action.get("close"):
                        reason = str(mgmt.get("close_reason", "management_close"))
                        cum = self._close_trade(open_t, bar, i, c, reason, cum)
                        last_closed_pnl = self.trades[-1]["net_pnl"]
                        open_t = None; state = "flat"
                        mgmt = None
                    else:
                        if mgmt.get("update_sl") is not None and action.get("update_sl") is None:
                            action["update_sl"] = mgmt["update_sl"]
                        if mgmt.get("update_tp") is not None and action.get("update_tp") is None:
                            action["update_tp"] = mgmt["update_tp"]

            # ══ STEP 6: Apply & validate SL/TP updates ═════════
            #   Updates take effect on NEXT bar's exit check (Step 2).
            if state != "flat" and open_t is not None:
                d = open_t["direction"]

                if action.get("update_sl") is not None:
                    new_sl = float(action["update_sl"])
                    old_sl = open_t.get("sl_level")
                    reject = False
                    # Reject if SL is already inside the current bar
                    if d == "long" and new_sl >= h:
                        reject = True
                    elif d == "short" and new_sl <= l:
                        reject = True
                    if not reject:
                        open_t["sl_level"] = new_sl
                        if len(self.trades) < 5:
                            logger.info(
                                f"  [MANAGER] Bar {i}: SL "
                                f"{round(old_sl,4) if old_sl else 'None'}->"
                                f"{round(new_sl,4)} ({d})")

                if action.get("update_tp") is not None:
                    new_tp = float(action["update_tp"])
                    old_tp = open_t.get("tp_level")
                    reject = False
                    if d == "long" and new_tp <= open_t["entry_price"]:
                        reject = True
                    elif d == "short" and new_tp >= open_t["entry_price"]:
                        reject = True
                    if not reject:
                        open_t["tp_level"] = new_tp
                        if len(self.trades) < 5:
                            logger.info(
                                f"  [MANAGER] Bar {i}: TP "
                                f"{round(old_tp,4) if old_tp else 'None'}->"
                                f"{round(new_tp,4)} ({d})")

            # ══ STEP 7: BUY/SELL → set pending ═════════════════
            if state == "flat" and sig in ("BUY", "SELL"):
                direction = "long" if sig == "BUY" else "short"
                pending_signal = {
                    "direction": direction,
                    "entry_price": c,
                    "signal_bar": i,
                    "signal_time": bar["time"],
                    "sl": action.get("sl"), "tp": action.get("tp"),
                    "sl_mode": action.get("sl_mode"), "sl_pts": action.get("sl_pts"),
                    "sl_ma_key": action.get("sl_ma_key"), "sl_ma_val": action.get("sl_ma_val"),
                    "tp_mode": action.get("tp_mode"), "tp_r": action.get("tp_r"),
                    "engine_sl_ma_key": action.get("engine_sl_ma_key"),
                    "engine_tp_ma_key": action.get("engine_tp_ma_key"),
                }

            # ══ STEP 8: Equity / drawdown ═══════════════════════
            if state != "flat" and open_t:
                pts = (c - open_t["entry_price"]) if state == "long" \
                      else (open_t["entry_price"] - c)
                t_lot = open_t.get("lot_size", self.lot_size)
                unrealised = points_to_dollars(pts, t_lot, self.point_value, self.dollar_per_point)
            else:
                unrealised = 0.0
            eq = self.starting_cap + cum + unrealised
            self.equity_curve.append(round(eq, 2))
            peak = max(peak, eq)
            dd = eq - peak
            self.dd_curve.append(round(dd, 2))

            # ══ PROGRESS YIELD ══════════════════════════════════
            now = _time.perf_counter()
            should_emit = (
                i == 0 or i == self.n - 1 or
                (i % progress_interval == 0 and (now - last_emit_time) >= PROGRESS_EMIT_INTERVAL)
            )
            if should_emit:
                last_emit_time = now
                oi = None
                if open_t:
                    oi = dict(
                        direction=open_t["direction"],
                        entry_price=round(open_t["entry_price"], 2),
                        sl=round(open_t["sl_level"], 2) if open_t.get("sl_level") else None,
                        tp=round(open_t["tp_level"], 2) if open_t.get("tp_level") else None,
                    )
                yield dict(
                    type="progress", bar=i, total=self.n,
                    pct=round(i / max(self.n - 1, 1) * 100, 1),
                    equity=round(eq, 2), drawdown=round(dd, 2),
                    open_trade=oi, last_closed_pnl=last_closed_pnl,
                )

            prev_indicators = self._indicators_at(i)

        # ══ END: close open trade ═══════════════════════════════
        if state != "flat" and open_t:
            lb = self.bars[-1]; cp = float(lb["close"])
            cum = self._close_trade(open_t, lb, self.n - 1, cp, "end_of_data", cum)

        end_ctx = self._build_ctx(
            self.n - 1, self.bars[-1] if self.bars else {},
            None, cum, self.starting_cap + cum, prev_indicators
        )
        self.strategy.on_end(end_ctx)

        sim_t1 = _time.perf_counter()
        self._perf["simulation_seconds"] = round(sim_t1 - sim_t0, 4)

    # ════════════════════════════════════════════════════════════════
    #  BUILD RESULT (stats + analytics + capped trades)
    # ════════════════════════════════════════════════════════════════
    def _build_result(self):
        perf = self._perf

        stats_t0 = _time.perf_counter()
        stats = compute_all_stats(
            trades=self.trades, equity_curve=self.equity_curve,
            bars=self.bars, starting_capital=self.starting_cap,
            lot_size=self.lot_size,
        )
        stats_t1 = _time.perf_counter()
        perf["stats_seconds"] = round(stats_t1 - stats_t0, 4)

        analytics_t0 = _time.perf_counter()
        analytics = compute_analytics(
            self.trades, self.bars,
            self.equity_curve, self.dd_curve, self.starting_cap,
        )
        analytics_t1 = _time.perf_counter()
        perf["analytics_seconds"] = round(analytics_t1 - analytics_t0, 4)

        # Cap analytics arrays to prevent massive payloads
        analytics = cap_analytics_arrays(analytics, MAX_ANALYTICS_POINTS)

        # Cap trades in response
        total_count = len(self.trades)
        cap = MAX_TRADES_IN_RESPONSE
        if total_count > cap:
            trades_out = self.trades[-cap:]
            print(f"  [TRADE CAP] Sending last {cap} of {total_count} trades")
        else:
            trades_out = self.trades

        result = dict(
            stats=stats,
            trades=trades_out,
            total_trade_count=total_count,
            trades_truncated=total_count > cap,
            equity_curve=downsample_curve(self.equity_curve, 1500),
            drawdown_curve=downsample_curve(self.dd_curve, 1500),
            analytics=analytics,
            performance=perf,
            config=dict(
                strategy_id=self.strategy.strategy_id,
                parameters=self.params,
                starting_capital=self.starting_cap,
                range=int(self.payload.get("range", 10)),
                lot_size=self.lot_size,
                point_value=self.point_value,
                dollar_per_point=self.dollar_per_point,
                sizing_mode=self.sizing_mode,
                risk_pct=self.risk_pct,
                fixed_risk=self.fixed_risk,
                scaling_enabled=self.scaling_enabled,
                scaling_per=self.scaling_per,
                scaling_risk=self.scaling_risk,
            ),
        )

        result = clean_for_json(result)

        build_t1 = _time.perf_counter()
        perf["response_build_seconds"] = round(build_t1 - self._total_t0, 4)
        perf["trade_count"] = total_count

        print(
            f"  [BACKTEST TIMING] simulation={perf['simulation_seconds']:.3f}s "
            f"stats={perf['stats_seconds']:.3f}s "
            f"analytics={perf['analytics_seconds']:.3f}s "
            f"total={perf['response_build_seconds']:.3f}s "
            f"trades={total_count}"
        )
        return result

    # ════════════════════════════════════════════════════════════════
    #  PUBLIC RUN METHODS
    # ════════════════════════════════════════════════════════════════
    def run(self):
        """Non-streaming: run full simulation, return result dict."""
        for _ in self._run_generator():
            pass
        return self._build_result()

    def run_streaming(self):
        """Streaming: yield progress dicts, then yield result dict."""
        yield from self._run_generator()
        yield {"type": "result", "data": self._build_result()}
```

---

## FILE: `js/chart.js`

```javascript
/* ═══════════════════════════════════════════════════════════════════
   chart.js — JINNI ZERO · NQ Range-Bar Chart + Indicators + Signals
   Lightweight Charts v4.2

   v2 — Full recode of windowing/loading system:
   - Immediate first render (no blank screen)
   - Stable scroll loading (no jumping, no loops)
   - All markers on candleSeries (guaranteed visible)
   - _isShifting lock prevents feedback loops
   - Clean module separation
═══════════════════════════════════════════════════════════════════ */
(function () {

/* ──────────────────────────────────────────────────────────────────
   DATA SOURCES
────────────────────────────────────────────────────────────────── */
let currentSymbol = 'NQ';
function buildRangeUrl(rangePt) {
  var ptStr = (rangePt % 1 === 0) ? rangePt.toFixed(0) : rangePt.toString();
  return 'data/' + currentSymbol + '/' + ptStr + 'pt.json?t=' + Date.now();
}

/* ──────────────────────────────────────────────────────────────────
   CONFIG
────────────────────────────────────────────────────────────────── */
const INITIAL_WINDOW_BARS = 2000;
const BUFFER_BARS = 800;
const TRIGGER_THRESHOLD = 200;
const SHIFT_DEBOUNCE_MS = 120;
const INDICATOR_RENDER_DEBOUNCE_MS = 35;
const OSC_PANE_HEIGHT = 142;
const PRICE_SCALE_MIN_WIDTH = 62;

const INDICATOR_CATALOG = {
  EMA:  { defaults: { length: 55, color: '#00e5ff', source: 'close' } },
  HMA:  { defaults: { length: 55, color: '#ff9800', source: 'close' } },
  SMA:  { defaults: { length: 50, color: '#00e5ff', source: 'close' } },
  WMA:  { defaults: { length: 50, color: '#8bc34a', source: 'close' } },
  BB:   { defaults: { length: 20, stddev: 2, color: '#66bbff', source: 'close' } },
  RSI:  { defaults: { length: 14, color: '#ffd166', source: 'close',
    obLevel: 70, osLevel: 30, midLevel: 50, showMid: true,
    obColor: '#ff3d5a', osColor: '#00e676', midColor: '#4a6070' } },
  'Stoch RSI': { defaults: { length: 14, smoothK: 3, smoothD: 3, color: '#8bc34a',
    source: 'close', obLevel: 80, osLevel: 20, obColor: '#ff3d5a', osColor: '#00e676' } },
};
const INDICATOR_TYPES = Object.keys(INDICATOR_CATALOG);
const PRICE_SOURCES = ['close', 'open', 'high', 'low'];

const DEFAULT_INDICATORS = [
  { type: 'HMA', length: 55, color: '#00e5ff', source: 'close', visible: true },
  { type: 'EMA', length: 55, color: '#ff9800', source: 'close', visible: true },
  { type: 'EMA', length: 200, color: '#e040fb', source: 'close', visible: true },
];

/* ──────────────────────────────────────────────────────────────────
   ROOT / LAYOUT
────────────────────────────────────────────────────────────────── */
const rootContainer = document.getElementById('chartContainer');
rootContainer.innerHTML = '';
rootContainer.style.position = 'relative';
rootContainer.style.overflow = 'hidden';
rootContainer.style.minWidth = '0';
rootContainer.style.minHeight = '0';

const chartsStack = document.createElement('div');
Object.assign(chartsStack.style, {
  position:'absolute',inset:'0',display:'flex',flexDirection:'column',
  minWidth:'0',minHeight:'0',pointerEvents:'none',
});
rootContainer.appendChild(chartsStack);

const mainChartHost = document.createElement('div');
Object.assign(mainChartHost.style, {
  position:'relative',flex:'1 1 auto',minWidth:'0',minHeight:'0',pointerEvents:'auto',
});
chartsStack.appendChild(mainChartHost);

const oscillatorWrap = document.createElement('div');
Object.assign(oscillatorWrap.style, {
  display:'none',flexDirection:'column',gap:'6px',paddingBottom:'6px',
  minHeight:'0',pointerEvents:'auto',
});
chartsStack.appendChild(oscillatorWrap);

const overlayUi = document.createElement('div');
Object.assign(overlayUi.style, {
  position:'absolute',top:'10px',left:'10px',zIndex:'30',display:'flex',
  flexDirection:'column',gap:'8px',pointerEvents:'auto',maxWidth:'620px',
});
rootContainer.appendChild(overlayUi);

const syncGuide = document.createElement('div');
Object.assign(syncGuide.style, {
  position:'absolute',top:'0',bottom:'0',width:'1px',
  background:'linear-gradient(to bottom,rgba(0,149,168,0),rgba(0,229,255,0.65),rgba(0,149,168,0))',
  pointerEvents:'none',zIndex:'12',display:'none',
  boxShadow:'0 0 10px rgba(0,229,255,0.22)',
});
rootContainer.appendChild(syncGuide);

/* ──────────────────────────────────────────────────────────────────
   MAIN CHART
────────────────────────────────────────────────────────────────── */
function createBaseChartOptions() {
  return {
    layout: { background:{type:'solid',color:'transparent'}, textColor:'#4a6070',
      fontFamily:"'Space Mono', monospace", fontSize:10 },
    grid: { vertLines:{visible:false}, horzLines:{color:'#1e2a38',style:1} },
    crosshair: { mode:0,
      vertLine:{color:'#0095a855',labelBackgroundColor:'#0095a8'},
      horzLine:{color:'#0095a855',labelBackgroundColor:'#0095a8'} },
    rightPriceScale: { borderColor:'#1e2a38',visible:true,minimumWidth:PRICE_SCALE_MIN_WIDTH,
      scaleMargins:{top:0.08,bottom:0.18} },
    timeScale: { borderColor:'#1e2a38',timeVisible:true,secondsVisible:true,
      barSpacing:6,minBarSpacing:2,fixLeftEdge:false,fixRightEdge:false,rightOffset:0 },
    handleScroll:true, handleScale:true,
  };
}

const mainChart = LightweightCharts.createChart(mainChartHost, createBaseChartOptions());

const candleSeries = mainChart.addCandlestickSeries({
  upColor:'#00e676',downColor:'#ff3d5a',borderUpColor:'#00e676',
  borderDownColor:'#ff3d5a',wickUpColor:'#00e67688',wickDownColor:'#ff3d5a88',
});

const volumeSeries = mainChart.addHistogramSeries({
  priceFormat:{type:'volume'},priceScaleId:'vol',
  scaleMargins:{top:0.88,bottom:0},
});

mainChart.priceScale('vol').applyOptions({
  borderColor:'#1e2a38',minimumWidth:PRICE_SCALE_MIN_WIDTH,
  scaleMargins:{top:0.88,bottom:0},visible:true,
});

/* ──────────────────────────────────────────────────────────────────
   STATE
────────────────────────────────────────────────────────────────── */
let currentRange = 2;
let fullData = [];
let datasetVersion = 0;

let loadedWindow = { start: 0, end: 0 };
let loadedData = [];
let lastVisibleRange = null;

let sourceCache = { close:[], open:[], high:[], low:[] };

// ── Anti-loop / shifting lock ────────────────────────────────────
let _isShifting = false;
let _shiftQueued = null;
let _shiftTimer = null;

let ignoreTimeSync = false;
let indicatorRenderTimer = null;

let nextIndicatorId = 1;
let indicators = [];
let signalEnabled = true;
let signalIndicatorId = null;

const indicatorSeriesRegistry = new Map();
const indicatorWindowCache = new Map();
let lastRenderedIndicatorRawValues = new Map();
let lastRenderedComputed = new Map();

// ── Markers (all on candleSeries) ────────────────────────────────
let _signalMarkers = [];
let _btEntryMarkers = [];
let _btExitMarkers = [];
let fullBacktestTrades = [];

const paneState = { rsi: null, stoch: null };
let selectedIndicatorId = null;

/* ──────────────────────────────────────────────────────────────────
   HELPERS
────────────────────────────────────────────────────────────────── */
function clamp(v, min, max) { return Math.max(min, Math.min(max, v)); }
function withAlpha(hex, alpha) {
  if (typeof hex !== 'string' || !hex.startsWith('#') || hex.length !== 7) return hex;
  return hex + clamp(Math.round(alpha*255),0,255).toString(16).padStart(2,'0');
}
function defaultForType(type) {
  return JSON.parse(JSON.stringify((INDICATOR_CATALOG[type]||INDICATOR_CATALOG.EMA).defaults));
}
function formatIndicatorLabel(ind) {
  if (ind.type === 'BB') return 'BB ' + ind.length + ',' + ind.stddev;
  if (ind.type === 'Stoch RSI') return 'Stoch RSI ' + ind.length + ',' + ind.smoothK + ',' + ind.smoothD;
  return ind.type + ' ' + ind.length;
}
function sourceArrayFor(source) { return sourceCache[source] || sourceCache.close || []; }
function indicatorWarmup(ind) {
  var len = Math.max(1, Number(ind.length) || 1);
  if (ind.type === 'Stoch RSI') return len * 8 + 20;
  if (ind.type === 'RSI') return len * 5 + 10;
  if (ind.type === 'BB') return len * 4 + 10;
  return len * 4 + 10;
}
function invalidateIndicatorCache() { indicatorWindowCache.clear(); }

function normalizeBars(rawBars) {
  var out = [], lastTime = null;
  for (var i = 0; i < rawBars.length; i++) {
    var b = rawBars[i];
    var time = Number(b.time);
    if (!Number.isFinite(time)) continue;
    if (lastTime != null && time <= lastTime) time = lastTime + 1;
    lastTime = time;
    out.push({ time:time, open:Number(b.open), high:Number(b.high),
      low:Number(b.low), close:Number(b.close), volume:Number(b.volume||0) });
  }
  return out;
}

function rebuildSourceCache() {
  sourceCache = {
    close: fullData.map(function(b){return b.close}),
    open:  fullData.map(function(b){return b.open}),
    high:  fullData.map(function(b){return b.high}),
    low:   fullData.map(function(b){return b.low}),
  };
}

function binarySearchAtOrAfter(time) {
  if (!fullData.length) return 0;
  var lo=0, hi=fullData.length-1, ans=fullData.length-1;
  while (lo <= hi) {
    var mid = (lo+hi)>>1;
    if (fullData[mid].time >= time) { ans=mid; hi=mid-1; }
    else lo=mid+1;
  }
  return ans;
}

function binarySearchAtOrBefore(time) {
  if (!fullData.length) return 0;
  var lo=0, hi=fullData.length-1, ans=0;
  while (lo <= hi) {
    var mid = (lo+hi)>>1;
    if (fullData[mid].time <= time) { ans=mid; lo=mid+1; }
    else hi=mid-1;
  }
  return ans;
}

function visibleIndexRange(range) {
  if (!range || range.from == null || range.to == null || !fullData.length) return null;
  return { fromIdx: binarySearchAtOrAfter(range.from), toIdx: binarySearchAtOrBefore(range.to) };
}

function volumeDataForLoadedWindow() {
  return loadedData.map(function(b) {
    return { time:b.time, value:b.volume||0, color: b.close>=b.open ? '#00e67633' : '#ff3d5a33' };
  });
}

function updateSidebar(bar) {
  if (!bar) return;
  document.getElementById('statOpen').textContent = bar.open.toFixed(2);
  document.getElementById('statHigh').textContent = bar.high.toFixed(2);
  document.getElementById('statLow').textContent = bar.low.toFixed(2);
  document.getElementById('statClose').textContent = bar.close.toFixed(2);
  document.getElementById('statVolume').textContent = bar.volume ? bar.volume.toFixed(0) : '—';
  var chg = bar.close - bar.open;
  var el = document.getElementById('statChange');
  el.textContent = (chg>=0?'+':'') + chg.toFixed(2);
  el.className = 'sidebar-value ' + (chg>=0?'bull':'bear');
}

function updateHeader(bar, prev) {
  if (!bar) return;
  document.getElementById('tickerPrice').textContent = bar.close.toFixed(2);
  var el = document.getElementById('tickerChange');
  if (prev) {
    var d = bar.close - prev.close;
    var pct = prev.close ? (d/prev.close*100) : 0;
    el.textContent = (d>=0?'+':'') + d.toFixed(2) + ' (' + pct.toFixed(2) + '%)';
    el.className = 'ticker-change ' + (d>=0?'bull':'bear');
  } else { el.textContent = '—'; el.className = 'ticker-change'; }
}

function safeSetVisibleRange(chart, range) {
  if (!range || range.from == null || range.to == null) return;
  try { chart.timeScale().setVisibleRange({from:range.from, to:range.to}); } catch(e) {}
}

function getHostSize(el, fallbackH) {
  var rect = el.getBoundingClientRect();
  return {
    width: Math.max(50, Math.floor(el.clientWidth || rect.width || rootContainer.clientWidth || 600)),
    height: Math.max(50, Math.floor(el.clientHeight || rect.height || fallbackH || 200)),
  };
}

function resizeMainChart() {
  var s = getHostSize(mainChartHost, Math.max(220, rootContainer.clientHeight - 20));
  mainChart.applyOptions({width:s.width, height:s.height});
}

function resizePaneCharts() {
  if (paneState.rsi && paneState.rsi.chart) {
    var s = getHostSize(paneState.rsi.host, OSC_PANE_HEIGHT);
    paneState.rsi.chart.applyOptions({width:s.width, height:s.height});
  }
  if (paneState.stoch && paneState.stoch.chart) {
    var s2 = getHostSize(paneState.stoch.host, OSC_PANE_HEIGHT);
    paneState.stoch.chart.applyOptions({width:s2.width, height:s2.height});
  }
}

function resizeAllCharts() { resizeMainChart(); resizePaneCharts(); }

function syncPanesFromMain(range) {
  if (!range || range.from == null || range.to == null || ignoreTimeSync) return;
  ignoreTimeSync = true;
  try {
    if (paneState.rsi && paneState.rsi.chart) safeSetVisibleRange(paneState.rsi.chart, range);
    if (paneState.stoch && paneState.stoch.chart) safeSetVisibleRange(paneState.stoch.chart, range);
  } finally {
    requestAnimationFrame(function(){ ignoreTimeSync = false; });
  }
}

/* ──────────────────────────────────────────────────────────────────
   MARKER SYSTEM (all markers on candleSeries — guaranteed visible)
────────────────────────────────────────────────────────────────── */
function refreshAllMarkers() {
  if (!loadedData.length) { candleSeries.setMarkers([]); return; }
  var fromT = loadedData[0].time;
  var toT = loadedData[loadedData.length-1].time;

  function inWindow(m) { return m.time >= fromT && m.time <= toT; }

  var all = [];
  // Signal markers
  for (var i = 0; i < _signalMarkers.length; i++) {
    if (inWindow(_signalMarkers[i])) all.push(_signalMarkers[i]);
  }
  // Backtest entry markers
  for (var j = 0; j < _btEntryMarkers.length; j++) {
    if (inWindow(_btEntryMarkers[j])) all.push(_btEntryMarkers[j]);
  }
  // Backtest exit markers
  for (var k = 0; k < _btExitMarkers.length; k++) {
    if (inWindow(_btExitMarkers[k])) all.push(_btExitMarkers[k]);
  }

  all.sort(function(a,b){ return a.time - b.time; });
  candleSeries.setMarkers(all);
}

function snapTimeToDataset(time) {
  if (!fullData.length) return time;
  var idx = binarySearchAtOrAfter(time);
  if (idx > 0) {
    var dCur = Math.abs(fullData[idx].time - time);
    var dPrev = Math.abs(fullData[idx-1].time - time);
    if (dPrev < dCur) return fullData[idx-1].time;
  }
  return fullData[idx] ? fullData[idx].time : fullData[fullData.length-1].time;
}

function exitColor(reason) {
  if (!reason) return '#ffffff';
  var r = String(reason).toUpperCase();
  if (r === 'TP_R' || r.indexOf('TP') >= 0) return '#00e676';
  if (r === 'SL_HIT' || r.indexOf('SL') >= 0) return '#ff3d5a';
  if (r.indexOf('MA') >= 0) return '#ffab00';
  if (r === 'END_OF_DATA') return '#ffffff';
  return '#aaaaaa';
}

function rebuildBacktestMarkerCache(trades) {
  fullBacktestTrades = Array.isArray(trades) ? trades.slice() : [];
  _btEntryMarkers = [];
  _btExitMarkers = [];
  if (!fullBacktestTrades.length || !fullData.length) return;

  var seenE = {}, seenX = {};
  for (var i = 0; i < fullBacktestTrades.length; i++) {
    var t = fullBacktestTrades[i];
    var isLong = t.direction === 'long';
    var entryTime = snapTimeToDataset(t.entry_time);
    var ek = 'e_' + entryTime + '_' + (t.id || t.position_id || i);
    if (!seenE[ek]) {
      seenE[ek] = true;
      var entryPrice = Number(t.entry_price);
      var priceStr = isFinite(entryPrice) ? ' @' + entryPrice.toFixed(2) : '';
      var slVal = Number(t.initial_sl != null ? t.initial_sl : t.sl_level);
      var tpVal = Number(t.initial_tp != null ? t.initial_tp : t.tp_level);
      var slStr = isFinite(slVal) ? ' SL:' + slVal.toFixed(2) : '';
      var tpStr = isFinite(tpVal) ? ' TP:' + tpVal.toFixed(2) : '';
      var riskStr = '';
      var rMult = '';
      if (isFinite(slVal) && isFinite(entryPrice)) {
        var riskPts = Math.abs(entryPrice - slVal);
        riskStr = ' risk:' + riskPts.toFixed(2) + 'pt';
        if (isFinite(tpVal) && riskPts > 0) {
          var tpDist = Math.abs(tpVal - entryPrice);
          rMult = ' (' + (tpDist / riskPts).toFixed(1) + 'R)';
        }
      }
      _btEntryMarkers.push({
        time: entryTime,
        position: isLong ? 'belowBar' : 'aboveBar',
        color: isLong ? '#00e676' : '#ff3d5a',
        shape: isLong ? 'arrowUp' : 'arrowDown',
        text: (isLong ? 'BUY' : 'SELL') + priceStr + slStr + tpStr + riskStr + rMult,
        size: 1,
      });
    }
    if (t.exit_time != null) {
      var exitTime = snapTimeToDataset(t.exit_time);
      if (exitTime === entryTime) {
        var idx = binarySearchAtOrAfter(entryTime);
        if (idx + 1 < fullData.length) exitTime = fullData[idx+1].time;
      }
      var xk = 'x_' + exitTime + '_' + (t.id || t.position_id || i);
      if (!seenX[xk]) {
        seenX[xk] = true;
        var exitPrice = Number(t.exit_price);
        var exitPriceStr = isFinite(exitPrice) ? ' @' + exitPrice.toFixed(2) : '';
        var reason = String(t.exit_reason || 'EXIT').toUpperCase();
        reason = reason.replace('_HIT', '').replace('_EXIT', '').replace('_OF_DATA', '');
        if (reason === 'TP_R') reason = 'TP';
        var netVal = Number(t.net_pnl);
        var netStr = isFinite(netVal) ? ' $' + (netVal >= 0 ? '+' : '') + netVal.toFixed(2) : '';
        var lotVal = Number(t.lot_size);
        var lotStr = isFinite(lotVal) && lotVal !== 1 ? ' lot:' + lotVal.toFixed(2) : '';
        var finalSl = Number(t.sl_level);
        var initSl = Number(t.initial_sl != null ? t.initial_sl : t.sl_level);
        var trailStr = '';
        if (isFinite(finalSl) && isFinite(initSl) && Math.abs(finalSl - initSl) > 0.001) {
          trailStr = ' trailSL:' + finalSl.toFixed(2);
        }
        _btExitMarkers.push({
          time: exitTime,
          position: isLong ? 'aboveBar' : 'belowBar',
          color: exitColor(t.exit_reason),
          shape: 'circle',
          text: reason + exitPriceStr + netStr + lotStr + trailStr,
          size: 1,
        });
      }
    }
  }
  _btEntryMarkers.sort(function(a,b){return a.time-b.time});
  _btExitMarkers.sort(function(a,b){return a.time-b.time});
}

window.plotBacktestMarkers = function(trades) {
  _signalMarkers = [];
  rebuildBacktestMarkerCache(trades);
  refreshAllMarkers();
};

window.clearBacktestMarkers = function() {
  fullBacktestTrades = [];
  _btEntryMarkers = [];
  _btExitMarkers = [];
  refreshAllMarkers();
};

/* ──────────────────────────────────────────────────────────────────
   WINDOW MANAGER (CORE FIX — anti-loop, stable viewport)
────────────────────────────────────────────────────────────────── */
function applyLoadedWindow() {
  loadedData = fullData.slice(loadedWindow.start, loadedWindow.end);
  candleSeries.setData(loadedData);
  volumeSeries.setData(volumeDataForLoadedWindow());
  refreshAllMarkers();
  scheduleIndicatorRender();

  var last = loadedData[loadedData.length-1] || fullData[fullData.length-1];
  updateSidebar(last);
  var candlesEl = document.getElementById('statCandles');
  if (candlesEl) candlesEl.textContent = fullData.length.toLocaleString();
}

function shiftWindow(newStart, newEnd, preserveRange) {
  if (_isShifting) {
    _shiftQueued = { start:newStart, end:newEnd, range:preserveRange };
    return;
  }

  if (newStart === loadedWindow.start && newEnd === loadedWindow.end) return;

  _isShifting = true;

  loadedWindow = { start: Math.max(0,newStart), end: Math.min(fullData.length, newEnd) };
  applyLoadedWindow();

  if (preserveRange) {
    safeSetVisibleRange(mainChart, preserveRange);
    syncPanesFromMain(preserveRange);
  }

  // Hold lock for 2 frames so chart settles before allowing new shifts
  requestAnimationFrame(function() {
    requestAnimationFrame(function() {
      _isShifting = false;
      if (_shiftQueued) {
        var q = _shiftQueued;
        _shiftQueued = null;
        shiftWindow(q.start, q.end, q.range);
      }
    });
  });
}

function checkWindowExpansion(range) {
  if (_isShifting || !range || !fullData.length) return;

  var vis = visibleIndexRange(range);
  if (!vis) return;

  var leftDist  = vis.fromIdx - loadedWindow.start;
  var rightDist = loadedWindow.end - vis.toIdx - 1;

  var needLeft  = leftDist < TRIGGER_THRESHOLD && loadedWindow.start > 0;
  var needRight = rightDist < TRIGGER_THRESHOLD && loadedWindow.end < fullData.length;

  if (!needLeft && !needRight) return;

  // Compute new window centered on visible range with buffer
  var newStart = Math.max(0, vis.fromIdx - BUFFER_BARS);
  var newEnd   = Math.min(fullData.length, vis.toIdx + 1 + BUFFER_BARS);

  // Ensure visible range is fully contained
  newStart = Math.min(newStart, vis.fromIdx);
  newEnd   = Math.max(newEnd, vis.toIdx + 1);

  if (newStart === loadedWindow.start && newEnd === loadedWindow.end) return;

  shiftWindow(newStart, newEnd, range);
}

function handleMainVisibleRangeChange(range) {
  if (_isShifting) return;
  if (!range || range.from == null || range.to == null || !fullData.length) return;

  lastVisibleRange = range;
  syncPanesFromMain(range);

  // Update oscillator readouts
  updateOscillatorReadoutsAtTime(range.to);

  // Debounced window expansion check
  if (_shiftTimer) clearTimeout(_shiftTimer);
  _shiftTimer = setTimeout(function() {
    _shiftTimer = null;
    checkWindowExpansion(lastVisibleRange);
  }, SHIFT_DEBOUNCE_MS);
}

mainChart.timeScale().subscribeVisibleTimeRangeChange(handleMainVisibleRangeChange);

/* ──────────────────────────────────────────────────────────────────
   INDICATOR MATH (O(n) precomputation — unchanged)
────────────────────────────────────────────────────────────────── */
function precomputeSma(values, period) {
  var n=values.length, out=new Array(n).fill(null);
  if (period<1||n<period) return out;
  var sum=0;
  for (var i=0;i<n;i++) {
    sum+=values[i];
    if (i>=period) sum-=values[i-period];
    if (i>=period-1) out[i]=sum/period;
  }
  return out;
}

function precomputeEma(values, period) {
  var n=values.length, out=new Array(n).fill(null);
  if (period<1||n<period) return out;
  var k=2/(period+1), seed=0;
  for (var i=0;i<period;i++) seed+=values[i];
  var ema=seed/period; out[period-1]=ema;
  for (var j=period;j<n;j++) { ema=values[j]*k+ema*(1-k); out[j]=ema; }
  return out;
}

function precomputeWma(values, period) {
  var n=values.length, out=new Array(n).fill(null);
  if (period<1||n<period) return out;
  var p=period, denom=p*(p+1)/2, sum=0, ws=0;
  for (var i=0;i<p;i++) { sum+=values[i]; ws+=values[i]*(i+1); }
  out[p-1]=ws/denom;
  for (var j=p;j<n;j++) { ws=ws+p*values[j]-sum; sum=sum+values[j]-values[j-p]; out[j]=ws/denom; }
  return out;
}

function precomputeHma(values, period) {
  var n=values.length, out=new Array(n).fill(null);
  var p=Math.max(2,period|0), half=Math.max(1,Math.floor(p/2)), sq=Math.max(1,Math.floor(Math.sqrt(p)));
  var full=precomputeWma(values,p), halfW=precomputeWma(values,half);
  var diff=new Array(n).fill(null), fv=-1;
  for (var i=0;i<n;i++) {
    if (full[i]!=null&&halfW[i]!=null) { diff[i]=2*halfW[i]-full[i]; if (fv===-1) fv=i; }
  }
  if (fv===-1) return out;
  var compact=[], map=[];
  for (var j=fv;j<n;j++) { if (diff[j]!=null) { compact.push(diff[j]); map.push(j); } }
  if (compact.length<sq) return out;
  var final=precomputeWma(compact,sq);
  for (var k=0;k<final.length;k++) { if (final[k]!=null) out[map[k]]=final[k]; }
  return out;
}

function precomputeMa(values, type, period) {
  var t=String(type).toUpperCase();
  if (t==='SMA') return precomputeSma(values,period);
  if (t==='EMA') return precomputeEma(values,period);
  if (t==='WMA') return precomputeWma(values,period);
  if (t==='HMA') return precomputeHma(values,period);
  return new Array(values.length).fill(null);
}

function precomputeBollinger(values, period, stddevMult) {
  var n=values.length;
  var basis=new Array(n).fill(null), upper=new Array(n).fill(null), lower=new Array(n).fill(null);
  if (period<1||n<period) return {basis:basis,upper:upper,lower:lower};
  var sum=0, sumSq=0;
  for (var i=0;i<n;i++) {
    var v=values[i]; sum+=v; sumSq+=v*v;
    if (i>=period) { var old=values[i-period]; sum-=old; sumSq-=old*old; }
    if (i>=period-1) {
      var mean=sum/period, variance=Math.max(0,(sumSq/period)-(mean*mean)), sd=Math.sqrt(variance);
      basis[i]=mean; upper[i]=mean+sd*stddevMult; lower[i]=mean-sd*stddevMult;
    }
  }
  return {basis:basis,upper:upper,lower:lower};
}

function precomputeRsi(values, period) {
  var n=values.length, out=new Array(n).fill(null);
  if (period<1||n<period+1) return out;
  var gains=0, losses=0;
  for (var i=1;i<=period;i++) { var d=values[i]-values[i-1]; if (d>=0) gains+=d; else losses-=d; }
  var ag=gains/period, al=losses/period;
  out[period]=al===0?100:(100-(100/(1+ag/al)));
  for (var j=period+1;j<n;j++) {
    var delta=values[j]-values[j-1], gain=delta>0?delta:0, loss=delta<0?-delta:0;
    ag=((ag*(period-1))+gain)/period; al=((al*(period-1))+loss)/period;
    out[j]=al===0?100:(100-(100/(1+ag/al)));
  }
  return out;
}

function rollingMin(values, period) {
  var out=new Array(values.length).fill(null), dq=[];
  for (var i=0;i<values.length;i++) {
    while (dq.length&&dq[0]<=i-period) dq.shift();
    while (dq.length) { var prev=values[dq[dq.length-1]]; if (prev==null||(values[i]!=null&&prev>=values[i])) dq.pop(); else break; }
    if (values[i]!=null) dq.push(i);
    if (i>=period-1&&dq.length) out[i]=values[dq[0]];
  }
  return out;
}

function rollingMax(values, period) {
  var out=new Array(values.length).fill(null), dq=[];
  for (var i=0;i<values.length;i++) {
    while (dq.length&&dq[0]<=i-period) dq.shift();
    while (dq.length) { var prev=values[dq[dq.length-1]]; if (prev==null||(values[i]!=null&&prev<=values[i])) dq.pop(); else break; }
    if (values[i]!=null) dq.push(i);
    if (i>=period-1&&dq.length) out[i]=values[dq[0]];
  }
  return out;
}

function precomputeStochRsi(values, rsiLength, smoothK, smoothD) {
  var rsi=precomputeRsi(values,rsiLength);
  var low=rollingMin(rsi,rsiLength), high=rollingMax(rsi,rsiLength);
  var rawK=new Array(values.length).fill(null);
  for (var i=0;i<values.length;i++) {
    if (rsi[i]==null||low[i]==null||high[i]==null) continue;
    var denom=high[i]-low[i]; rawK[i]=denom===0?0:((rsi[i]-low[i])/denom)*100;
  }
  var safeK=rawK.map(function(v){return v==null?0:v});
  var k=precomputeSma(safeK,Math.max(1,smoothK)).map(function(v,i){return rawK[i]==null?null:v});
  var safeK2=k.map(function(v){return v==null?0:v});
  var d=precomputeSma(safeK2,Math.max(1,smoothD)).map(function(v,i){return k[i]==null?null:v});
  return {rsi:rsi,k:k,d:d};
}

/* ──────────────────────────────────────────────────────────────────
   PANE MANAGEMENT
────────────────────────────────────────────────────────────────── */
function createPaneHost(titleText) {
  var host=document.createElement('div');
  Object.assign(host.style,{position:'relative',height:OSC_PANE_HEIGHT+'px',
    minHeight:OSC_PANE_HEIGHT+'px',borderTop:'1px solid #1e2a38'});
  var badge=document.createElement('div');
  Object.assign(badge.style,{position:'absolute',top:'6px',left:'10px',zIndex:'5',
    display:'flex',alignItems:'center',gap:'8px',background:'#0d1117dd',
    border:'1px solid #1e2a38',borderRadius:'4px',padding:'4px 8px',
    fontFamily:"'Space Mono', monospace",fontSize:'0.56rem',fontWeight:'700',
    letterSpacing:'0.06em',color:'#8aa4b6',pointerEvents:'none',backdropFilter:'blur(6px)'});
  var title=document.createElement('span'); title.textContent=titleText;
  var values=document.createElement('span'); values.textContent='—';
  badge.appendChild(title); badge.appendChild(values);
  host.appendChild(badge); oscillatorWrap.appendChild(host);
  return {host:host,valuesLabel:values};
}

function createPaneChart(host) {
  return LightweightCharts.createChart(host, {
    layout:{background:{type:'solid',color:'transparent'},textColor:'#4a6070',
      fontFamily:"'Space Mono', monospace",fontSize:10},
    grid:{vertLines:{visible:false},horzLines:{color:'#1e2a38',style:1}},
    crosshair:{mode:0,vertLine:{color:'#0095a855',labelBackgroundColor:'#0095a8'},
      horzLine:{color:'#0095a855',labelBackgroundColor:'#0095a8'}},
    rightPriceScale:{borderColor:'#1e2a38',visible:true,minimumWidth:PRICE_SCALE_MIN_WIDTH,
      scaleMargins:{top:0.14,bottom:0.12}},
    timeScale:{borderColor:'#1e2a38',timeVisible:true,secondsVisible:true,
      barSpacing:6,minBarSpacing:2,visible:false},
    handleScroll:true,handleScale:true,
  });
}

function updateOscillatorWrapVisibility() {
  oscillatorWrap.style.display = (paneState.rsi||paneState.stoch) ? 'flex' : 'none';
  var hasOsc = oscillatorWrap.style.display !== 'none';
  mainChart.applyOptions({rightPriceScale:{borderColor:'#1e2a38',visible:true,
    minimumWidth:PRICE_SCALE_MIN_WIDTH,scaleMargins:hasOsc?{top:0.08,bottom:0.08}:{top:0.08,bottom:0.18}}});
  mainChart.priceScale('vol').applyOptions({borderColor:'#1e2a38',visible:true,
    minimumWidth:PRICE_SCALE_MIN_WIDTH,scaleMargins:hasOsc?{top:0.90,bottom:0}:{top:0.88,bottom:0}});
  requestAnimationFrame(function(){
    resizeAllCharts();
    var range=lastVisibleRange;
    if (range) { syncPanesFromMain(range); updateOscillatorReadoutsAtTime(range.to); }
  });
}

function ensureRsiPane() {
  if (paneState.rsi) return paneState.rsi;
  var pane=createPaneHost('RSI');
  var chart=createPaneChart(pane.host);
  paneState.rsi={host:pane.host,valuesLabel:pane.valuesLabel,chart:chart,dynamicSeries:new Map()};
  chart.subscribeVisibleTimeRangeChange(function(range){
    if (ignoreTimeSync||!range||range.from==null||range.to==null) return;
    ignoreTimeSync=true;
    try { safeSetVisibleRange(mainChart,range);
      if (paneState.stoch&&paneState.stoch.chart) safeSetVisibleRange(paneState.stoch.chart,range);
    } finally { requestAnimationFrame(function(){ignoreTimeSync=false;}); }
  });
  chart.subscribeCrosshairMove(function(param){
    if (!paneState.rsi) return;
    var texts=[];
    paneState.rsi.dynamicSeries.forEach(function(entry,id){
      var dp=param.seriesData?param.seriesData.get(entry.line):null;
      if (dp&&dp.value!=null) {
        var ind=indicators.find(function(x){return x.id===id});
        if (ind) texts.push(formatIndicatorLabel(ind)+' '+dp.value.toFixed(2));
      }
    });
    paneState.rsi.valuesLabel.textContent=texts.length?texts.join(' · '):'—';
  });
  updateOscillatorWrapVisibility();
  requestAnimationFrame(function(){requestAnimationFrame(function(){
    resizePaneCharts();
    var range=lastVisibleRange;
    if (range) safeSetVisibleRange(chart,range);
  });});
  return paneState.rsi;
}

function ensureStochPane() {
  if (paneState.stoch) return paneState.stoch;
  var pane=createPaneHost('STOCH RSI');
  var chart=createPaneChart(pane.host);
  paneState.stoch={host:pane.host,valuesLabel:pane.valuesLabel,chart:chart,dynamicSeries:new Map()};
  chart.subscribeVisibleTimeRangeChange(function(range){
    if (ignoreTimeSync||!range||range.from==null||range.to==null) return;
    ignoreTimeSync=true;
    try { safeSetVisibleRange(mainChart,range);
      if (paneState.rsi&&paneState.rsi.chart) safeSetVisibleRange(paneState.rsi.chart,range);
    } finally { requestAnimationFrame(function(){ignoreTimeSync=false;}); }
  });
  chart.subscribeCrosshairMove(function(param){
    if (!paneState.stoch) return;
    var texts=[];
    paneState.stoch.dynamicSeries.forEach(function(entry,id){
      var dpK=param.seriesData?param.seriesData.get(entry.k):null;
      var dpD=param.seriesData?param.seriesData.get(entry.d):null;
      var ind=indicators.find(function(x){return x.id===id});
      if (ind) {
        if (dpK&&dpK.value!=null) texts.push(formatIndicatorLabel(ind)+' %K '+dpK.value.toFixed(2));
        if (dpD&&dpD.value!=null) texts.push('%D '+dpD.value.toFixed(2));
      }
    });
    paneState.stoch.valuesLabel.textContent=texts.length?texts.join(' · '):'—';
  });
  updateOscillatorWrapVisibility();
  requestAnimationFrame(function(){requestAnimationFrame(function(){
    resizePaneCharts();
    var range=lastVisibleRange;
    if (range) safeSetVisibleRange(chart,range);
  });});
  return paneState.stoch;
}

function destroyUnusedPanes() {
  var needRsi=indicators.some(function(i){return i.type==='RSI'});
  var needStoch=indicators.some(function(i){return i.type==='Stoch RSI'});
  if (!needRsi&&paneState.rsi) {
    paneState.rsi.dynamicSeries.forEach(function(e){Object.values(e).forEach(function(s){try{paneState.rsi.chart.removeSeries(s)}catch(err){}})});
    try{paneState.rsi.chart.remove()}catch(e){}
    try{oscillatorWrap.removeChild(paneState.rsi.host)}catch(e){}
    paneState.rsi=null;
  }
  if (!needStoch&&paneState.stoch) {
    paneState.stoch.dynamicSeries.forEach(function(e){Object.values(e).forEach(function(s){try{paneState.stoch.chart.removeSeries(s)}catch(err){}})});
    try{paneState.stoch.chart.remove()}catch(e){}
    try{oscillatorWrap.removeChild(paneState.stoch.host)}catch(e){}
    paneState.stoch=null;
  }
  updateOscillatorWrapVisibility();
}

/* ──────────────────────────────────────────────────────────────────
   SERIES REGISTRY
────────────────────────────────────────────────────────────────── */
function makeMainOverlaySeries(color, width, lineStyle) {
  return mainChart.addLineSeries({color:color,lineWidth:width||1.25,
    lineStyle:lineStyle||LightweightCharts.LineStyle.Solid,
    priceLineVisible:false,lastValueVisible:true,crosshairMarkerVisible:false,priceScaleId:'right'});
}

function makePaneLevelSeries(chart, color) {
  return chart.addLineSeries({color:color,lineWidth:1,
    lineStyle:LightweightCharts.LineStyle.Dashed,
    priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false,priceScaleId:'right'});
}

function removeIndicatorSeries(indicatorId) {
  var reg=indicatorSeriesRegistry.get(indicatorId);
  if (!reg) return;
  if (reg.kind==='main') { reg.series.forEach(function(s){try{mainChart.removeSeries(s)}catch(e){}}); }
  else if (reg.kind==='rsi'&&paneState.rsi) {
    var entry=paneState.rsi.dynamicSeries.get(indicatorId);
    if (entry) { Object.values(entry).forEach(function(s){try{paneState.rsi.chart.removeSeries(s)}catch(e){}}); paneState.rsi.dynamicSeries.delete(indicatorId); }
  } else if (reg.kind==='stoch'&&paneState.stoch) {
    var entry2=paneState.stoch.dynamicSeries.get(indicatorId);
    if (entry2) { Object.values(entry2).forEach(function(s){try{paneState.stoch.chart.removeSeries(s)}catch(e){}}); paneState.stoch.dynamicSeries.delete(indicatorId); }
  }
  indicatorSeriesRegistry.delete(indicatorId);
}

function ensureIndicatorSeries(ind) {
  var existing=indicatorSeriesRegistry.get(ind.id);
  if (existing) return existing;
  if (ind.type==='RSI') {
    var pane=ensureRsiPane();
    var line=pane.chart.addLineSeries({color:ind.color,lineWidth:1.4,priceLineVisible:true,
      lastValueVisible:true,crosshairMarkerVisible:true,priceScaleId:'right'});
    var ob=makePaneLevelSeries(pane.chart,withAlpha(ind.obColor||'#ff3d5a',0.55));
    var os=makePaneLevelSeries(pane.chart,withAlpha(ind.osColor||'#00e676',0.55));
    var mid=makePaneLevelSeries(pane.chart,withAlpha(ind.midColor||'#4a6070',0.45));
    pane.dynamicSeries.set(ind.id,{line:line,ob:ob,os:os,mid:mid});
    var reg={kind:'rsi',series:[line,ob,os,mid]};
    indicatorSeriesRegistry.set(ind.id,reg); return reg;
  }
  if (ind.type==='Stoch RSI') {
    var pane2=ensureStochPane();
    var k=pane2.chart.addLineSeries({color:ind.color,lineWidth:1.3,priceLineVisible:true,
      lastValueVisible:true,crosshairMarkerVisible:true,priceScaleId:'right'});
    var d=pane2.chart.addLineSeries({color:withAlpha(ind.color,0.55),lineWidth:1.1,priceLineVisible:true,
      lastValueVisible:true,crosshairMarkerVisible:true,priceScaleId:'right'});
    var ob2=makePaneLevelSeries(pane2.chart,withAlpha(ind.obColor||'#ff3d5a',0.55));
    var os2=makePaneLevelSeries(pane2.chart,withAlpha(ind.osColor||'#00e676',0.55));
    pane2.dynamicSeries.set(ind.id,{k:k,d:d,ob:ob2,os:os2});
    var reg2={kind:'stoch',series:[k,d,ob2,os2]};
    indicatorSeriesRegistry.set(ind.id,reg2); return reg2;
  }
  if (ind.type==='BB') {
    var upper=makeMainOverlaySeries(withAlpha(ind.color,0.95),1.1,LightweightCharts.LineStyle.Dashed);
    var basis=makeMainOverlaySeries(withAlpha(ind.color,0.65),1.15,LightweightCharts.LineStyle.Solid);
    var lower=makeMainOverlaySeries(withAlpha(ind.color,0.95),1.1,LightweightCharts.LineStyle.Dashed);
    var reg3={kind:'main',series:[upper,basis,lower]};
    indicatorSeriesRegistry.set(ind.id,reg3); return reg3;
  }
  var ma=makeMainOverlaySeries(ind.color,1.25,LightweightCharts.LineStyle.Solid);
  var reg4={kind:'main',series:[ma]};
  indicatorSeriesRegistry.set(ind.id,reg4); return reg4;
}

// ═══════════════════════════════════════════════════════════════════
// PART 2 — UI, Indicator CRUD, Signals, Data Loading, Init
// (paste directly after PART 1 inside the same IIFE)
// ═══════════════════════════════════════════════════════════════════

/* ──────────────────────────────────────────────────────────────────
   OSCILLATOR READOUTS
────────────────────────────────────────────────────────────────── */
function loadedIndexFromTime(time) {
  if (time == null || !loadedData.length) return -1;
  var t = Number(time);
  if (!Number.isFinite(t)) return -1;
  var globalIdx = binarySearchAtOrBefore(t);
  if (globalIdx < loadedWindow.start || globalIdx >= loadedWindow.end) return -1;
  return globalIdx - loadedWindow.start;
}

function updateOscillatorReadoutsAtTime(time) {
  var idx = loadedIndexFromTime(time);
  if (idx < 0) {
    if (paneState.rsi) paneState.rsi.valuesLabel.textContent = '—';
    if (paneState.stoch) paneState.stoch.valuesLabel.textContent = '—';
    return;
  }
  if (paneState.rsi) {
    var texts = [];
    paneState.rsi.dynamicSeries.forEach(function(entry, id) {
      var ind = indicators.find(function(x){return x.id===id});
      var computed = lastRenderedComputed.get(id);
      if (ind && computed && computed.raw && computed.raw[idx] != null)
        texts.push(formatIndicatorLabel(ind) + ' ' + computed.raw[idx].toFixed(2));
    });
    paneState.rsi.valuesLabel.textContent = texts.length ? texts.join(' · ') : '—';
  }
  if (paneState.stoch) {
    var texts2 = [];
    paneState.stoch.dynamicSeries.forEach(function(entry, id) {
      var ind = indicators.find(function(x){return x.id===id});
      var computed = lastRenderedComputed.get(id);
      if (ind && computed) {
        if (computed.kRaw && computed.kRaw[idx] != null)
          texts2.push(formatIndicatorLabel(ind) + ' %K ' + computed.kRaw[idx].toFixed(2));
        if (computed.dRaw && computed.dRaw[idx] != null)
          texts2.push('%D ' + computed.dRaw[idx].toFixed(2));
      }
    });
    paneState.stoch.valuesLabel.textContent = texts2.length ? texts2.join(' · ') : '—';
  }
}

/* ──────────────────────────────────────────────────────────────────
   INDICATOR CALC ON LOADED WINDOW
────────────────────────────────────────────────────────────────── */
function computeIndicatorForLoadedWindow(ind) {
  if (!loadedData.length) return null;

  var warmup = indicatorWarmup(ind);
  var calcStart = Math.max(0, loadedWindow.start - warmup);
  var calcEnd = loadedWindow.end;

  var cacheKey = [
    datasetVersion, currentRange, ind.id, ind.type, ind.length,
    ind.source, ind.stddev, ind.smoothK, ind.smoothD, calcStart, calcEnd
  ].join('|');

  if (indicatorWindowCache.has(cacheKey)) return indicatorWindowCache.get(cacheKey);

  var values = sourceArrayFor(ind.source).slice(calcStart, calcEnd);
  var times = fullData.slice(calcStart, calcEnd).map(function(b){return b.time});
  var offset = loadedWindow.start - calcStart;
  var visibleLen = loadedWindow.end - loadedWindow.start;
  var result = null;

  if (ind.type === 'BB') {
    var bb = precomputeBollinger(values, ind.length, Number(ind.stddev || 2));
    var basisRaw = bb.basis.slice(offset, offset + visibleLen);
    var upperRaw = bb.upper.slice(offset, offset + visibleLen);
    var lowerRaw = bb.lower.slice(offset, offset + visibleLen);
    var basis = [], upper = [], lower = [];
    for (var i = 0; i < visibleLen; i++) {
      var t = times[offset + i];
      if (upperRaw[i] != null) upper.push({time:t,value:upperRaw[i]});
      if (basisRaw[i] != null) basis.push({time:t,value:basisRaw[i]});
      if (lowerRaw[i] != null) lower.push({time:t,value:lowerRaw[i]});
    }
    result = {kind:'bb',basisRaw:basisRaw,upperRaw:upperRaw,lowerRaw:lowerRaw,basis:basis,upper:upper,lower:lower};
  } else if (ind.type === 'RSI') {
    var raw = precomputeRsi(values, ind.length).slice(offset, offset + visibleLen);
    var line = [];
    for (var j = 0; j < visibleLen; j++) { if (raw[j] != null) line.push({time:times[offset+j],value:raw[j]}); }
    result = {kind:'rsi',raw:raw,line:line};
  } else if (ind.type === 'Stoch RSI') {
    var stoch = precomputeStochRsi(values, ind.length, ind.smoothK||3, ind.smoothD||3);
    var kRaw = stoch.k.slice(offset, offset + visibleLen);
    var dRaw = stoch.d.slice(offset, offset + visibleLen);
    var kLine = [], dLine = [];
    for (var m = 0; m < visibleLen; m++) {
      var tt = times[offset + m];
      if (kRaw[m] != null) kLine.push({time:tt,value:kRaw[m]});
      if (dRaw[m] != null) dLine.push({time:tt,value:dRaw[m]});
    }
    result = {kind:'stoch',kRaw:kRaw,dRaw:dRaw,kLine:kLine,dLine:dLine};
  } else {
    var rawMa = precomputeMa(values, ind.type, ind.length).slice(offset, offset + visibleLen);
    var lineMa = [];
    for (var p = 0; p < visibleLen; p++) { if (rawMa[p] != null) lineMa.push({time:times[offset+p],value:rawMa[p]}); }
    result = {kind:'ma',raw:rawMa,line:lineMa};
  }

  indicatorWindowCache.set(cacheKey, result);
  return result;
}

function renderLevelLines() {
  if (!loadedData.length) return;
  var start = loadedData[0].time;
  var end = loadedData[loadedData.length-1].time;

  indicators.forEach(function(ind) {
    if (ind.type === 'RSI' && paneState.rsi) {
      var entry = paneState.rsi.dynamicSeries.get(ind.id);
      if (!entry) return;
      entry.ob.setData(ind.visible ? [{time:start,value:ind.obLevel},{time:end,value:ind.obLevel}] : []);
      entry.os.setData(ind.visible ? [{time:start,value:ind.osLevel},{time:end,value:ind.osLevel}] : []);
      entry.mid.setData(ind.visible && ind.showMid !== false ? [{time:start,value:ind.midLevel},{time:end,value:ind.midLevel}] : []);
    }
    if (ind.type === 'Stoch RSI' && paneState.stoch) {
      var entry2 = paneState.stoch.dynamicSeries.get(ind.id);
      if (!entry2) return;
      entry2.ob.setData(ind.visible ? [{time:start,value:ind.obLevel},{time:end,value:ind.obLevel}] : []);
      entry2.os.setData(ind.visible ? [{time:start,value:ind.osLevel},{time:end,value:ind.osLevel}] : []);
    }
  });
}

function renderIndicatorsNow() {
  if (!loadedData.length) return;
  lastRenderedIndicatorRawValues = new Map();
  lastRenderedComputed = new Map();

  indicators.forEach(function(ind) {
    var reg = ensureIndicatorSeries(ind);
    var computed = computeIndicatorForLoadedWindow(ind);
    if (!computed) return;
    lastRenderedComputed.set(ind.id, computed);

    if (ind.type === 'BB') {
      reg.series[0].setData(ind.visible ? computed.upper : []);
      reg.series[1].setData(ind.visible ? computed.basis : []);
      reg.series[2].setData(ind.visible ? computed.lower : []);
      reg.series.forEach(function(s){s.applyOptions({visible:ind.visible})});
      lastRenderedIndicatorRawValues.set(ind.id, computed.basisRaw);
    } else if (ind.type === 'RSI') {
      reg.series[0].setData(ind.visible ? computed.line : []);
      reg.series[0].applyOptions({visible:ind.visible});
      reg.series[1].applyOptions({visible:ind.visible});
      reg.series[2].applyOptions({visible:ind.visible});
      reg.series[3].applyOptions({visible:ind.visible && ind.showMid !== false});
      lastRenderedIndicatorRawValues.set(ind.id, computed.raw);
    } else if (ind.type === 'Stoch RSI') {
      reg.series[0].setData(ind.visible ? computed.kLine : []);
      reg.series[1].setData(ind.visible ? computed.dLine : []);
      reg.series[0].applyOptions({visible:ind.visible});
      reg.series[1].applyOptions({visible:ind.visible});
      reg.series[2].applyOptions({visible:ind.visible});
      reg.series[3].applyOptions({visible:ind.visible});
      lastRenderedIndicatorRawValues.set(ind.id, computed.kRaw);
    } else {
      reg.series[0].setData(ind.visible ? computed.line : []);
      reg.series[0].applyOptions({visible:ind.visible});
      lastRenderedIndicatorRawValues.set(ind.id, computed.raw);
    }
  });

  renderLevelLines();
  updateOscillatorReadoutsAtTime((lastVisibleRange && lastVisibleRange.to) || (loadedData.length ? loadedData[loadedData.length-1].time : null));
  refreshSignals();
}

function scheduleIndicatorRender() {
  if (indicatorRenderTimer) clearTimeout(indicatorRenderTimer);
  indicatorRenderTimer = setTimeout(function() {
    indicatorRenderTimer = null;
    renderIndicatorsNow();
  }, INDICATOR_RENDER_DEBOUNCE_MS);
}

/* ──────────────────────────────────────────────────────────────────
   SIGNALS
────────────────────────────────────────────────────────────────── */
function computeSignals(data, maValues) {
  if (!signalEnabled || !data || !data.length || !maValues || !maValues.length) return [];
  var markers = [];
  var bullCount = 0, bearCount = 0;
  for (var i = 1; i < data.length; i++) {
    var bar = data[i], mv = maValues[i];
    if (mv == null) { bullCount = 0; bearCount = 0; continue; }
    var c = bar.close, o = bar.open;
    var above = c > mv, below = c < mv;
    var bull = c >= o, bear = c < o;
    if (above && bull) bullCount++; else bullCount = 0;
    if (below && bear) bearCount++; else bearCount = 0;
    if (bullCount >= 2) {
      markers.push({time:bar.time,position:'belowBar',color:'#00e676',shape:'arrowUp',text:'BUY',size:1});
      bullCount = 0;
    }
    if (bearCount >= 2) {
      markers.push({time:bar.time,position:'aboveBar',color:'#ff3d5a',shape:'arrowDown',text:'SELL',size:1});
      bearCount = 0;
    }
  }
  return markers;
}

function refreshSignals() {
  _signalMarkers = [];
  if (!signalEnabled || !signalIndicatorId) { refreshAllMarkers(); return; }
  var ind = indicators.find(function(x){return x.id===signalIndicatorId && x.visible});
  if (!ind || ['EMA','HMA','SMA','WMA'].indexOf(ind.type) < 0) { refreshAllMarkers(); return; }
  var raw = lastRenderedIndicatorRawValues.get(ind.id);
  if (!raw || !loadedData.length) { refreshAllMarkers(); return; }
  _signalMarkers = computeSignals(loadedData, raw);
  refreshAllMarkers();
}

/* ──────────────────────────────────────────────────────────────────
   UI — OVERLAY CONTROLS
────────────────────────────────────────────────────────────────── */
var topControls = document.createElement('div');
Object.assign(topControls.style, {display:'flex',alignItems:'center',gap:'8px'});
overlayUi.appendChild(topControls);

var indicatorsButton = document.createElement('button');
indicatorsButton.textContent = 'Indicators';
Object.assign(indicatorsButton.style, {
  background:'#0d1117ee',border:'1px solid #1e2a38',color:'#c8d8e8',
  fontFamily:"'Space Mono', monospace",fontSize:'0.62rem',fontWeight:'700',
  letterSpacing:'0.08em',padding:'8px 12px',borderRadius:'6px',cursor:'pointer',
  boxShadow:'0 8px 20px rgba(0,0,0,0.24)',backdropFilter:'blur(8px)',
});
topControls.appendChild(indicatorsButton);

var signalControl = document.createElement('div');
Object.assign(signalControl.style, {
  display:'flex',alignItems:'center',gap:'6px',background:'#0d1117ee',
  border:'1px solid #1e2a38',borderRadius:'6px',padding:'6px 8px',
  boxShadow:'0 8px 20px rgba(0,0,0,0.24)',backdropFilter:'blur(8px)',
});
topControls.appendChild(signalControl);

var activeChipsWrap = document.createElement('div');
Object.assign(activeChipsWrap.style, {display:'flex',flexWrap:'wrap',gap:'6px',maxWidth:'620px'});
overlayUi.appendChild(activeChipsWrap);

var indicatorPanel = document.createElement('div');
Object.assign(indicatorPanel.style, {
  display:'none',width:'420px',maxHeight:'min(70vh, 720px)',background:'#0d1117f4',
  border:'1px solid #1e2a38',borderRadius:'8px',boxShadow:'0 18px 38px rgba(0,0,0,0.38)',
  backdropFilter:'blur(10px)',overflow:'hidden',
});
overlayUi.appendChild(indicatorPanel);

var panelScroll = document.createElement('div');
Object.assign(panelScroll.style, {maxHeight:'inherit',overflowY:'auto'});
indicatorPanel.appendChild(panelScroll);

var panelOpen = false;
function setPanelOpen(open) {
  panelOpen = !!open;
  indicatorPanel.style.display = panelOpen ? 'block' : 'none';
  indicatorsButton.style.borderColor = panelOpen ? '#00e5ff' : '#1e2a38';
  indicatorsButton.style.color = panelOpen ? '#00e5ff' : '#c8d8e8';
  indicatorsButton.style.background = panelOpen ? 'rgba(0,229,255,0.08)' : '#0d1117ee';
}
indicatorsButton.addEventListener('click', function(e) { e.stopPropagation(); setPanelOpen(!panelOpen); });
indicatorPanel.addEventListener('click', function(e) { e.stopPropagation(); });
overlayUi.addEventListener('click', function(e) { e.stopPropagation(); });
document.addEventListener('click', function() { setPanelOpen(false); });

function makeUiLabel(text) {
  var el = document.createElement('div');
  el.textContent = text;
  Object.assign(el.style, {fontFamily:"'Space Mono', monospace",fontSize:'0.54rem',
    fontWeight:'700',letterSpacing:'0.10em',color:'#6e8798',marginBottom:'4px'});
  return el;
}

function styleUiInput(el) {
  Object.assign(el.style, {width:'100%',background:'#111820',border:'1px solid #1e2a38',
    color:'#c8d8e8',fontFamily:"'Space Mono', monospace",fontSize:'0.62rem',
    fontWeight:'700',padding:'7px 8px',borderRadius:'5px',outline:'none'});
  return el;
}

// ── Panel header ────────────────────────────────────────────────
var panelHeader = document.createElement('div');
panelHeader.textContent = 'ADD / EDIT INDICATORS';
Object.assign(panelHeader.style, {padding:'10px 12px',borderBottom:'1px solid #1e2a38',
  fontFamily:"'Space Mono', monospace",fontSize:'0.62rem',fontWeight:'700',
  letterSpacing:'0.12em',color:'#00e5ff'});
panelScroll.appendChild(panelHeader);

var panelBody = document.createElement('div');
Object.assign(panelBody.style, {padding:'12px',display:'flex',flexDirection:'column',gap:'12px'});
panelScroll.appendChild(panelBody);

// ── Add section ─────────────────────────────────────────────────
var addSection = document.createElement('div');
Object.assign(addSection.style, {display:'flex',flexDirection:'column',gap:'10px',
  paddingBottom:'10px',borderBottom:'1px solid #1e2a38'});
panelBody.appendChild(addSection);

var addSectionTitle = document.createElement('div');
addSectionTitle.textContent = 'NEW INDICATOR';
Object.assign(addSectionTitle.style, {fontFamily:"'Space Mono', monospace",fontSize:'0.56rem',
  fontWeight:'700',letterSpacing:'0.12em',color:'#8aa4b6'});
addSection.appendChild(addSectionTitle);

var typeWrap = document.createElement('div');
typeWrap.appendChild(makeUiLabel('TYPE'));
var typeSelect = styleUiInput(document.createElement('select'));
INDICATOR_TYPES.forEach(function(type) {
  var o = document.createElement('option'); o.value = type; o.textContent = type; typeSelect.appendChild(o);
});
typeWrap.appendChild(typeSelect); addSection.appendChild(typeWrap);

var rowA = document.createElement('div');
Object.assign(rowA.style, {display:'grid',gridTemplateColumns:'1fr 1fr',gap:'8px'});
addSection.appendChild(rowA);

var lengthWrap = document.createElement('div');
lengthWrap.appendChild(makeUiLabel('LENGTH'));
var lengthInput = styleUiInput(document.createElement('input'));
lengthInput.type = 'number'; lengthInput.min = '1'; lengthInput.step = '1';
lengthWrap.appendChild(lengthInput); rowA.appendChild(lengthWrap);

var sourceWrap = document.createElement('div');
sourceWrap.appendChild(makeUiLabel('SOURCE'));
var sourceSelect = styleUiInput(document.createElement('select'));
PRICE_SOURCES.forEach(function(src) {
  var o = document.createElement('option'); o.value = src; o.textContent = src.toUpperCase(); sourceSelect.appendChild(o);
});
sourceWrap.appendChild(sourceSelect); rowA.appendChild(sourceWrap);

var rowB = document.createElement('div');
Object.assign(rowB.style, {display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:'8px'});
addSection.appendChild(rowB);

var colorWrap = document.createElement('div');
colorWrap.appendChild(makeUiLabel('COLOR'));
var colorInput = document.createElement('input'); colorInput.type = 'color';
Object.assign(colorInput.style, {width:'100%',height:'34px',background:'#111820',
  border:'1px solid #1e2a38',borderRadius:'5px',padding:'3px',cursor:'pointer'});
colorWrap.appendChild(colorInput); rowB.appendChild(colorWrap);

var stddevWrap = document.createElement('div');
stddevWrap.appendChild(makeUiLabel('STDDEV'));
var stddevInput = styleUiInput(document.createElement('input'));
stddevInput.type = 'number'; stddevInput.step = '0.1';
stddevWrap.appendChild(stddevInput); rowB.appendChild(stddevWrap);

var smoothKWrap = document.createElement('div');
smoothKWrap.appendChild(makeUiLabel('SMOOTH K'));
var smoothKInput = styleUiInput(document.createElement('input'));
smoothKInput.type = 'number'; smoothKInput.step = '1';
smoothKWrap.appendChild(smoothKInput); rowB.appendChild(smoothKWrap);

var rowC = document.createElement('div');
Object.assign(rowC.style, {display:'grid',gridTemplateColumns:'1fr auto',gap:'8px',alignItems:'end'});
addSection.appendChild(rowC);

var smoothDWrap = document.createElement('div');
smoothDWrap.appendChild(makeUiLabel('SMOOTH D'));
var smoothDInput = styleUiInput(document.createElement('input'));
smoothDInput.type = 'number'; smoothDInput.step = '1';
smoothDWrap.appendChild(smoothDInput); rowC.appendChild(smoothDWrap);

var addIndicatorButton = document.createElement('button');
addIndicatorButton.textContent = '+ ADD';
Object.assign(addIndicatorButton.style, {background:'rgba(0,229,255,0.08)',border:'1px solid #00e5ff',
  color:'#00e5ff',fontFamily:"'Space Mono', monospace",fontSize:'0.62rem',fontWeight:'700',
  letterSpacing:'0.10em',padding:'9px 14px',borderRadius:'5px',cursor:'pointer',minWidth:'88px'});
rowC.appendChild(addIndicatorButton);

var editorSection = document.createElement('div');
Object.assign(editorSection.style, {display:'flex',flexDirection:'column',gap:'10px'});
panelBody.appendChild(editorSection);

function applyFormDefaults(type) {
  var d = defaultForType(type);
  lengthInput.value = d.length != null ? d.length : 14;
  colorInput.value = d.color || '#00e5ff';
  sourceSelect.value = d.source || 'close';
  stddevInput.value = d.stddev != null ? d.stddev : 2;
  smoothKInput.value = d.smoothK != null ? d.smoothK : 3;
  smoothDInput.value = d.smoothD != null ? d.smoothD : 3;
  stddevWrap.style.display = type === 'BB' ? '' : 'none';
  smoothKWrap.style.display = type === 'Stoch RSI' ? '' : 'none';
  smoothDWrap.style.display = type === 'Stoch RSI' ? '' : 'none';
}
typeSelect.addEventListener('change', function() { applyFormDefaults(typeSelect.value); });
applyFormDefaults(typeSelect.value);

/* ──────────────────────────────────────────────────────────────────
   UI — SIGNAL CONTROL
────────────────────────────────────────────────────────────────── */
function renderSignalUi() {
  signalControl.innerHTML = '';
  var cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = signalEnabled;
  cb.style.accentColor = '#00e5ff';
  cb.addEventListener('change', function() { signalEnabled = cb.checked; renderSignalUi(); refreshSignals(); });
  var label = document.createElement('span'); label.textContent = 'Signals';
  Object.assign(label.style, {fontFamily:"'Space Mono', monospace",fontSize:'0.56rem',
    fontWeight:'700',letterSpacing:'0.08em',color:signalEnabled?'#00e5ff':'#6e8798'});
  signalControl.appendChild(cb); signalControl.appendChild(label);

  var candidates = indicators.filter(function(ind){return ['EMA','HMA','SMA','WMA'].indexOf(ind.type)>=0});
  if (!candidates.length) return;
  if (!signalIndicatorId || !candidates.some(function(ind){return ind.id===signalIndicatorId}))
    signalIndicatorId = candidates[0].id;

  var select = styleUiInput(document.createElement('select'));
  Object.assign(select.style, {width:'148px',padding:'5px 7px',fontSize:'0.56rem'});
  candidates.forEach(function(ind) {
    var o = document.createElement('option'); o.value = ind.id; o.textContent = formatIndicatorLabel(ind);
    if (ind.id === signalIndicatorId) o.selected = true; select.appendChild(o);
  });
  select.addEventListener('change', function() { signalIndicatorId = select.value; refreshSignals(); });
  signalControl.appendChild(select);
}

/* ──────────────────────────────────────────────────────────────────
   UI — INDICATOR CHIPS
────────────────────────────────────────────────────────────────── */
function renderIndicatorChips() {
  activeChipsWrap.innerHTML = '';
  indicators.forEach(function(ind) {
    var chip = document.createElement('div');
    Object.assign(chip.style, {display:'inline-flex',alignItems:'center',gap:'6px',
      background:selectedIndicatorId===ind.id?'rgba(0,229,255,0.08)':'#0d1117ee',
      border:selectedIndicatorId===ind.id?'1px solid #00e5ff':'1px solid #1e2a38',
      borderRadius:'999px',padding:'5px 8px',boxShadow:'0 4px 14px rgba(0,0,0,0.18)',
      backdropFilter:'blur(8px)',cursor:'pointer'});
    chip.addEventListener('click', function() {
      selectedIndicatorId = ind.id; renderIndicatorChips(); renderIndicatorEditors(); setPanelOpen(true);
    });

    var dot = document.createElement('span');
    Object.assign(dot.style, {width:'9px',height:'9px',borderRadius:'50%',background:ind.color,
      boxShadow:'0 0 8px '+withAlpha(ind.color,0.45),flexShrink:'0'});

    var text = document.createElement('span'); text.textContent = formatIndicatorLabel(ind);
    Object.assign(text.style, {fontFamily:"'Space Mono', monospace",fontSize:'0.56rem',
      fontWeight:'700',letterSpacing:'0.04em',color:'#c8d8e8',whiteSpace:'nowrap'});

    var toggleBtn = document.createElement('button');
    toggleBtn.textContent = ind.visible ? '◉' : '○';
    Object.assign(toggleBtn.style, {background:'transparent',border:'none',
      color:ind.visible?'#00e5ff':'#4a6070',fontFamily:"'Space Mono', monospace",
      fontSize:'0.68rem',cursor:'pointer',padding:'0 2px'});
    toggleBtn.addEventListener('click', function(e) { e.stopPropagation(); updateIndicator(ind.id, {visible:!ind.visible}); });

    var removeBtn = document.createElement('button'); removeBtn.textContent = '✕';
    Object.assign(removeBtn.style, {background:'transparent',border:'none',color:'#ff3d5a',
      fontFamily:"'Space Mono', monospace",fontSize:'0.62rem',fontWeight:'700',cursor:'pointer',padding:'0 2px'});
    removeBtn.addEventListener('click', function(e) {
      e.stopPropagation();
      if (selectedIndicatorId===ind.id) selectedIndicatorId=null;
      removeIndicator(ind.id);
    });

    chip.appendChild(dot); chip.appendChild(text); chip.appendChild(toggleBtn); chip.appendChild(removeBtn);
    activeChipsWrap.appendChild(chip);
  });
}

/* ──────────────────────────────────────────────────────────────────
   UI — INDICATOR EDITORS (inline in panel)
────────────────────────────────────────────────────────────────── */
function renderIndicatorEditors() {
  editorSection.innerHTML = '';

  var title = document.createElement('div'); title.textContent = 'ACTIVE INDICATORS';
  Object.assign(title.style, {fontFamily:"'Space Mono', monospace",fontSize:'0.56rem',
    fontWeight:'700',letterSpacing:'0.12em',color:'#8aa4b6'});
  editorSection.appendChild(title);

  if (!indicators.length) {
    var empty = document.createElement('div'); empty.textContent = 'No active indicators.';
    Object.assign(empty.style, {fontFamily:"'Space Mono', monospace",fontSize:'0.58rem',color:'#586f7f'});
    editorSection.appendChild(empty); return;
  }

  indicators.forEach(function(ind) {
    var card = document.createElement('div');
    Object.assign(card.style, {display:'flex',flexDirection:'column',gap:'8px',padding:'10px',
      borderRadius:'8px',border:selectedIndicatorId===ind.id?'1px solid #00e5ff':'1px solid #1e2a38',
      background:selectedIndicatorId===ind.id?'rgba(0,229,255,0.04)':'#0f151c'});

    // Header
    var head = document.createElement('div');
    Object.assign(head.style, {display:'flex',alignItems:'center',justifyContent:'space-between',gap:'8px',cursor:'pointer'});
    head.addEventListener('click', function() { selectedIndicatorId=ind.id; renderIndicatorEditors(); renderIndicatorChips(); });

    var left = document.createElement('div');
    Object.assign(left.style, {display:'flex',alignItems:'center',gap:'8px'});
    var dotE = document.createElement('span');
    Object.assign(dotE.style, {width:'10px',height:'10px',borderRadius:'50%',background:ind.color,flexShrink:'0'});
    var labelE = document.createElement('div'); labelE.textContent = formatIndicatorLabel(ind);
    Object.assign(labelE.style, {fontFamily:"'Space Mono', monospace",fontSize:'0.60rem',fontWeight:'700',
      letterSpacing:'0.05em',color:'#c8d8e8'});
    left.appendChild(dotE); left.appendChild(labelE);

    var actions = document.createElement('div');
    Object.assign(actions.style, {display:'flex',alignItems:'center',gap:'6px'});

    var vis = document.createElement('button'); vis.textContent = ind.visible ? 'VISIBLE' : 'HIDDEN';
    Object.assign(vis.style, {background:ind.visible?'rgba(0,229,255,0.08)':'transparent',
      border:'1px solid #1e2a38',color:ind.visible?'#00e5ff':'#6e8798',
      fontFamily:"'Space Mono', monospace",fontSize:'0.52rem',fontWeight:'700',
      letterSpacing:'0.08em',padding:'5px 8px',borderRadius:'4px',cursor:'pointer'});
    vis.addEventListener('click', function(e) { e.stopPropagation(); updateIndicator(ind.id, {visible:!ind.visible}); });

    var del = document.createElement('button'); del.textContent = 'REMOVE';
    Object.assign(del.style, {background:'transparent',border:'1px solid #3b2028',color:'#ff3d5a',
      fontFamily:"'Space Mono', monospace",fontSize:'0.52rem',fontWeight:'700',
      letterSpacing:'0.08em',padding:'5px 8px',borderRadius:'4px',cursor:'pointer'});
    del.addEventListener('click', function(e) {
      e.stopPropagation(); if (selectedIndicatorId===ind.id) selectedIndicatorId=null; removeIndicator(ind.id);
    });

    actions.appendChild(vis); actions.appendChild(del);
    head.appendChild(left); head.appendChild(actions);
    card.appendChild(head);

    // Fields grid
    var grid = document.createElement('div');
    Object.assign(grid.style, {display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:'8px'});
    card.appendChild(grid);

    function addField(parent, labelText, inputEl) {
      var wrap = document.createElement('div'); wrap.appendChild(makeUiLabel(labelText)); wrap.appendChild(inputEl); parent.appendChild(wrap);
    }

    var typeSel = styleUiInput(document.createElement('select'));
    INDICATOR_TYPES.forEach(function(t) {
      var o = document.createElement('option'); o.value=t; o.textContent=t; if (t===ind.type) o.selected=true; typeSel.appendChild(o);
    });
    typeSel.addEventListener('change', function() {
      var defaults = defaultForType(typeSel.value);
      updateIndicator(ind.id, {type:typeSel.value, length:defaults.length!=null?defaults.length:ind.length,
        source:defaults.source||ind.source, color:defaults.color||ind.color,
        stddev:defaults.stddev!=null?defaults.stddev:ind.stddev,
        smoothK:defaults.smoothK!=null?defaults.smoothK:ind.smoothK,
        smoothD:defaults.smoothD!=null?defaults.smoothD:ind.smoothD,
        obLevel:defaults.obLevel!=null?defaults.obLevel:ind.obLevel,
        osLevel:defaults.osLevel!=null?defaults.osLevel:ind.osLevel,
        midLevel:defaults.midLevel!=null?defaults.midLevel:ind.midLevel,
        showMid:defaults.showMid!=null?defaults.showMid:ind.showMid,
        obColor:defaults.obColor||ind.obColor, osColor:defaults.osColor||ind.osColor,
        midColor:defaults.midColor||ind.midColor});
    });
    addField(grid, 'TYPE', typeSel);

    var lenInput = styleUiInput(document.createElement('input'));
    lenInput.type='number'; lenInput.min='1'; lenInput.step='1'; lenInput.value=ind.length;
    lenInput.addEventListener('change', function() { updateIndicator(ind.id, {length:Number(lenInput.value||ind.length)}); });
    addField(grid, 'LENGTH', lenInput);

    var srcSel = styleUiInput(document.createElement('select'));
    PRICE_SOURCES.forEach(function(s) {
      var o = document.createElement('option'); o.value=s; o.textContent=s.toUpperCase();
      if (s===ind.source) o.selected=true; srcSel.appendChild(o);
    });
    srcSel.addEventListener('change', function() { updateIndicator(ind.id, {source:srcSel.value}); });
    addField(grid, 'SOURCE', srcSel);

    var grid2 = document.createElement('div');
    Object.assign(grid2.style, {display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:'8px'});
    card.appendChild(grid2);

    var colorEl = document.createElement('input'); colorEl.type='color'; colorEl.value=ind.color;
    Object.assign(colorEl.style, {width:'100%',height:'34px',background:'#111820',
      border:'1px solid #1e2a38',borderRadius:'5px',padding:'3px',cursor:'pointer'});
    colorEl.addEventListener('input', function() { updateIndicator(ind.id, {color:colorEl.value}); });
    addField(grid2, 'COLOR', colorEl);

    if (ind.type === 'BB') {
      var stdInput = styleUiInput(document.createElement('input'));
      stdInput.type='number'; stdInput.step='0.1'; stdInput.value=ind.stddev!=null?ind.stddev:2;
      stdInput.addEventListener('change', function() { updateIndicator(ind.id, {stddev:Number(stdInput.value||2)}); });
      addField(grid2, 'STDDEV', stdInput);
    }

    if (ind.type === 'Stoch RSI') {
      var kInput = styleUiInput(document.createElement('input'));
      kInput.type='number'; kInput.step='1'; kInput.value=ind.smoothK!=null?ind.smoothK:3;
      kInput.addEventListener('change', function() { updateIndicator(ind.id, {smoothK:Number(kInput.value||3)}); });
      addField(grid2, 'SMOOTH K', kInput);
      var dInput = styleUiInput(document.createElement('input'));
      dInput.type='number'; dInput.step='1'; dInput.value=ind.smoothD!=null?ind.smoothD:3;
      dInput.addEventListener('change', function() { updateIndicator(ind.id, {smoothD:Number(dInput.value||3)}); });
      addField(grid2, 'SMOOTH D', dInput);
    }

    // Level editors for RSI / Stoch RSI
    if (ind.type === 'RSI' || ind.type === 'Stoch RSI') {
      var lvlTitle = document.createElement('div'); lvlTitle.textContent = 'LEVELS';
      Object.assign(lvlTitle.style, {fontFamily:"'Space Mono', monospace",fontSize:'0.54rem',
        fontWeight:'700',letterSpacing:'0.10em',color:'#8aa4b6',marginTop:'2px'});
      card.appendChild(lvlTitle);

      var lvlGrid = document.createElement('div');
      Object.assign(lvlGrid.style, {display:'grid',
        gridTemplateColumns:ind.type==='RSI'?'1fr 1fr 1fr 1fr 1fr 1fr':'1fr 1fr 1fr 1fr',gap:'8px'});
      card.appendChild(lvlGrid);

      var obVal = styleUiInput(document.createElement('input'));
      obVal.type='number'; obVal.step='0.1'; obVal.value=ind.obLevel!=null?ind.obLevel:(ind.type==='RSI'?70:80);
      obVal.addEventListener('change', function() { updateIndicator(ind.id, {obLevel:Number(obVal.value)}); });
      addField(lvlGrid, 'OB', obVal);

      var osVal = styleUiInput(document.createElement('input'));
      osVal.type='number'; osVal.step='0.1'; osVal.value=ind.osLevel!=null?ind.osLevel:(ind.type==='RSI'?30:20);
      osVal.addEventListener('change', function() { updateIndicator(ind.id, {osLevel:Number(osVal.value)}); });
      addField(lvlGrid, 'OS', osVal);

      var obColorEl = document.createElement('input'); obColorEl.type='color'; obColorEl.value=ind.obColor||'#ff3d5a';
      Object.assign(obColorEl.style, {width:'100%',height:'34px',background:'#111820',
        border:'1px solid #1e2a38',borderRadius:'5px',padding:'3px',cursor:'pointer'});
      obColorEl.addEventListener('input', function() { updateIndicator(ind.id, {obColor:obColorEl.value}); });
      addField(lvlGrid, 'OB COLOR', obColorEl);

      var osColorEl = document.createElement('input'); osColorEl.type='color'; osColorEl.value=ind.osColor||'#00e676';
      Object.assign(osColorEl.style, {width:'100%',height:'34px',background:'#111820',
        border:'1px solid #1e2a38',borderRadius:'5px',padding:'3px',cursor:'pointer'});
      osColorEl.addEventListener('input', function() { updateIndicator(ind.id, {osColor:osColorEl.value}); });
      addField(lvlGrid, 'OS COLOR', osColorEl);

      if (ind.type === 'RSI') {
        var midVal = styleUiInput(document.createElement('input'));
        midVal.type='number'; midVal.step='0.1'; midVal.value=ind.midLevel!=null?ind.midLevel:50;
        midVal.addEventListener('change', function() { updateIndicator(ind.id, {midLevel:Number(midVal.value)}); });
        addField(lvlGrid, 'MID', midVal);

        var midColorEl = document.createElement('input'); midColorEl.type='color'; midColorEl.value=ind.midColor||'#4a6070';
        Object.assign(midColorEl.style, {width:'100%',height:'34px',background:'#111820',
          border:'1px solid #1e2a38',borderRadius:'5px',padding:'3px',cursor:'pointer'});
        midColorEl.addEventListener('input', function() { updateIndicator(ind.id, {midColor:midColorEl.value}); });
        addField(lvlGrid, 'MID COLOR', midColorEl);
      }
    }

    editorSection.appendChild(card);
  });
}

/* ──────────────────────────────────────────────────────────────────
   INDICATOR CRUD
────────────────────────────────────────────────────────────────── */
function addIndicator(def) {
  var defaults = defaultForType(def.type);
  var ind = {
    id: 'ind_' + (nextIndicatorId++),
    type: def.type,
    length: clamp(parseInt(def.length != null ? def.length : defaults.length, 10) || defaults.length || 14, 1, 2000),
    color: def.color || defaults.color || '#00e5ff',
    source: PRICE_SOURCES.indexOf(def.source) >= 0 ? def.source : (defaults.source || 'close'),
    visible: def.visible !== false,
    stddev: def.stddev != null ? Number(def.stddev) : (defaults.stddev != null ? defaults.stddev : 2),
    smoothK: def.smoothK != null ? Math.max(1,Number(def.smoothK)) : (defaults.smoothK != null ? defaults.smoothK : 3),
    smoothD: def.smoothD != null ? Math.max(1,Number(def.smoothD)) : (defaults.smoothD != null ? defaults.smoothD : 3),
    obLevel: def.obLevel != null ? Number(def.obLevel) : defaults.obLevel,
    osLevel: def.osLevel != null ? Number(def.osLevel) : defaults.osLevel,
    midLevel: def.midLevel != null ? Number(def.midLevel) : defaults.midLevel,
    showMid: def.showMid != null ? !!def.showMid : defaults.showMid,
    obColor: def.obColor || defaults.obColor,
    osColor: def.osColor || defaults.osColor,
    midColor: def.midColor || defaults.midColor,
  };
  indicators.push(ind);
  selectedIndicatorId = ind.id;
  ensureIndicatorSeries(ind);
  if (!signalIndicatorId && ['EMA','HMA','SMA','WMA'].indexOf(ind.type) >= 0) signalIndicatorId = ind.id;
  renderIndicatorChips(); renderSignalUi(); renderIndicatorEditors();
  invalidateIndicatorCache();
  requestAnimationFrame(function() {
    resizeAllCharts();
    var range = lastVisibleRange;
    if (range) syncPanesFromMain(range);
    scheduleIndicatorRender();
  });
}

function removeIndicator(indicatorId) {
  indicators = indicators.filter(function(ind){return ind.id !== indicatorId});
  removeIndicatorSeries(indicatorId);
  lastRenderedIndicatorRawValues.delete(indicatorId);
  lastRenderedComputed.delete(indicatorId);
  if (signalIndicatorId === indicatorId) {
    var next = indicators.find(function(ind){return ['EMA','HMA','SMA','WMA'].indexOf(ind.type)>=0});
    signalIndicatorId = next ? next.id : null;
  }
  if (selectedIndicatorId === indicatorId) {
    selectedIndicatorId = indicators.length ? indicators[0].id : null;
  }
  destroyUnusedPanes();
  renderIndicatorChips(); renderSignalUi(); renderIndicatorEditors();
  invalidateIndicatorCache();
  requestAnimationFrame(function() {
    resizeAllCharts();
    var range = lastVisibleRange;
    if (range) syncPanesFromMain(range);
    scheduleIndicatorRender();
  });
}

function updateIndicator(indicatorId, patch) {
  var ind = indicators.find(function(x){return x.id===indicatorId});
  if (!ind) return;
  var oldType = ind.type;
  Object.assign(ind, patch);
  var defaults = defaultForType(ind.type);
  ind.length = clamp(parseInt(ind.length,10)||defaults.length||14,1,2000);
  ind.source = PRICE_SOURCES.indexOf(ind.source)>=0 ? ind.source : 'close';
  if (ind.type==='BB') ind.stddev = Number(ind.stddev!=null?ind.stddev:2);
  if (ind.type==='Stoch RSI') {
    ind.smoothK = Math.max(1,Number(ind.smoothK!=null?ind.smoothK:3));
    ind.smoothD = Math.max(1,Number(ind.smoothD!=null?ind.smoothD:3));
  }
  ind.obLevel = ind.obLevel!=null ? Number(ind.obLevel) : defaults.obLevel;
  ind.osLevel = ind.osLevel!=null ? Number(ind.osLevel) : defaults.osLevel;
  ind.midLevel = ind.midLevel!=null ? Number(ind.midLevel) : defaults.midLevel;
  ind.showMid = ind.showMid!=null ? !!ind.showMid : defaults.showMid;
  ind.obColor = ind.obColor || defaults.obColor;
  ind.osColor = ind.osColor || defaults.osColor;
  ind.midColor = ind.midColor || defaults.midColor;

  if (oldType !== ind.type) {
    removeIndicatorSeries(indicatorId);
    destroyUnusedPanes();
    ensureIndicatorSeries(ind);
    if (signalIndicatorId===indicatorId && ['EMA','HMA','SMA','WMA'].indexOf(ind.type)<0) {
      var next = indicators.find(function(x){return ['EMA','HMA','SMA','WMA'].indexOf(x.type)>=0});
      signalIndicatorId = next ? next.id : null;
    } else if (!signalIndicatorId && ['EMA','HMA','SMA','WMA'].indexOf(ind.type)>=0) {
      signalIndicatorId = indicatorId;
    }
  }

  var reg = ensureIndicatorSeries(ind);
  if (ind.type==='BB') {
    reg.series[0].applyOptions({color:withAlpha(ind.color,0.95),visible:ind.visible});
    reg.series[1].applyOptions({color:withAlpha(ind.color,0.65),visible:ind.visible});
    reg.series[2].applyOptions({color:withAlpha(ind.color,0.95),visible:ind.visible});
  } else if (ind.type==='Stoch RSI') {
    reg.series[0].applyOptions({color:ind.color,visible:ind.visible});
    reg.series[1].applyOptions({color:withAlpha(ind.color,0.55),visible:ind.visible});
    reg.series[2].applyOptions({color:withAlpha(ind.obColor||'#ff3d5a',0.55),visible:ind.visible});
    reg.series[3].applyOptions({color:withAlpha(ind.osColor||'#00e676',0.55),visible:ind.visible});
  } else if (ind.type==='RSI') {
    reg.series[0].applyOptions({color:ind.color,visible:ind.visible});
    reg.series[1].applyOptions({color:withAlpha(ind.obColor||'#ff3d5a',0.55),visible:ind.visible});
    reg.series[2].applyOptions({color:withAlpha(ind.osColor||'#00e676',0.55),visible:ind.visible});
    reg.series[3].applyOptions({color:withAlpha(ind.midColor||'#4a6070',0.45),visible:ind.visible&&ind.showMid!==false});
  } else {
    reg.series[0].applyOptions({color:ind.color,visible:ind.visible});
  }

  renderIndicatorChips(); renderSignalUi(); renderIndicatorEditors();
  invalidateIndicatorCache();
  requestAnimationFrame(function() {
    resizeAllCharts();
    var range = lastVisibleRange;
    if (range) syncPanesFromMain(range);
    scheduleIndicatorRender();
  });
}

addIndicatorButton.addEventListener('click', function() {
  var type = typeSelect.value;
  var def = {type:type, length:Number(lengthInput.value), color:colorInput.value, source:sourceSelect.value, visible:true};
  if (type==='BB') def.stddev = Number(stddevInput.value||2);
  if (type==='Stoch RSI') { def.smoothK = Number(smoothKInput.value||3); def.smoothD = Number(smoothDInput.value||3); }
  addIndicator(def);
  setPanelOpen(false);
});

/* ──────────────────────────────────────────────────────────────────
   LOAD DATASET
────────────────────────────────────────────────────────────────── */
async function loadRange(rangePt) {
  currentRange = rangePt;
  var url = buildRangeUrl(rangePt);

  try {
    var resp = await fetch(url);
    if (!resp.ok) { console.error('[CHART] Load failed:', resp.status, url); return; }
    var rawBars = await resp.json();

    fullData = normalizeBars(rawBars);
    datasetVersion += 1;
    rebuildSourceCache();
    invalidateIndicatorCache();

    // ── Immediate first render: load tail window ────────────
    var total = fullData.length;
    var winStart = Math.max(0, total - INITIAL_WINDOW_BARS);
    var winEnd = total;

    loadedWindow = { start: winStart, end: winEnd };
    loadedData = fullData.slice(winStart, winEnd);

    // Set data immediately — no waiting
    candleSeries.setData(loadedData);
    volumeSeries.setData(volumeDataForLoadedWindow());

    var lastBar = fullData[fullData.length - 1];
    var prevBar = fullData.length > 1 ? fullData[fullData.length - 2] : null;
    updateHeader(lastBar, prevBar);
    updateSidebar(lastBar);

    var candlesEl = document.getElementById('statCandles');
    if (candlesEl) candlesEl.textContent = fullData.length.toLocaleString();

    // Fit content immediately
    resizeAllCharts();
    mainChart.timeScale().fitContent();

    // Rebuild backtest markers if any
    if (fullBacktestTrades.length) {
      rebuildBacktestMarkerCache(fullBacktestTrades);
    }
    refreshAllMarkers();

    // Render indicators + signals after 1 frame
    requestAnimationFrame(function() {
      renderIndicatorChips();
      renderSignalUi();
      renderIndicatorEditors();
      scheduleIndicatorRender();

      // Sync panes after another frame
      requestAnimationFrame(function() {
        var range = mainChart.timeScale().getVisibleRange();
        if (range) {
          lastVisibleRange = range;
          syncPanesFromMain(range);
        }
      });
    });

  } catch (err) {
    console.error('Load error:', err);
  }
}

/* ──────────────────────────────────────────────────────────────────
   MAIN CHART HOVER / CROSSHAIR
────────────────────────────────────────────────────────────────── */
mainChart.subscribeCrosshairMove(function(param) {
  var dp = param.seriesData ? param.seriesData.get(candleSeries) : null;
  if (dp && dp.open != null) updateSidebar(dp);

  // Sync guide
  if (!param || !param.point || param.point.x == null || param.point.x < 0 || param.point.x > rootContainer.clientWidth) {
    syncGuide.style.display = 'none';
  } else {
    syncGuide.style.display = 'block';
    syncGuide.style.left = Math.round(param.point.x) + 'px';
  }

  if (param && param.time != null) updateOscillatorReadoutsAtTime(param.time);
  else updateOscillatorReadoutsAtTime(null);
});

/* ──────────────────────────────────────────────────────────────────
   RANGE BUTTONS (dynamic from /api/ranges/<symbol>)
────────────────────────────────────────────────────────────────── */
function buildIntervalButtons(symbol) {
  var group = document.getElementById('intervalGroup');
  if (!group) return;

  fetch('http://localhost:5000/api/ranges/' + encodeURIComponent(symbol))
    .then(function(r) { return r.json(); })
    .then(function(ranges) {
      group.innerHTML = '';
      if (!ranges || !ranges.length) {
        console.warn('[CHART] No range files found for ' + symbol);
        return;
      }
      if (ranges.indexOf(currentRange) < 0) currentRange = ranges[0];

      ranges.forEach(function(pt) {
        var btn = document.createElement('button');
        btn.className = 'interval-btn' + (pt === currentRange ? ' active' : '');
        btn.textContent = (pt % 1 === 0 ? pt.toFixed(0) : pt.toString()) + 'PT';
        btn.addEventListener('click', function() {
          group.querySelectorAll('.interval-btn').forEach(function(b) { b.classList.remove('active'); });
          btn.classList.add('active');
          loadRange(pt);
        });
        group.appendChild(btn);
      });

      loadRange(currentRange);
    })
    .catch(function(err) {
      console.error('[CHART] Failed to fetch ranges:', err);
      loadRange(currentRange);
    });
}

var chartSymbolEl = document.getElementById('chartSymbol');
if (chartSymbolEl) {
  currentSymbol = chartSymbolEl.value || 'NQ';
  chartSymbolEl.addEventListener('change', function() {
    currentSymbol = this.value || 'NQ';
    var tickerEl = document.getElementById('tickerSymbol');
    if (tickerEl) tickerEl.textContent = currentSymbol;
    buildIntervalButtons(currentSymbol);
  });
}
/* ──────────────────────────────────────────────────────────────────
   RESIZE
────────────────────────────────────────────────────────────────── */
var _resizeTimer = null;
new ResizeObserver(function() {
  if (_resizeTimer) clearTimeout(_resizeTimer);
  _resizeTimer = setTimeout(function() {
    _resizeTimer = null;
    resizeAllCharts();
    var range = lastVisibleRange;
    if (range) syncPanesFromMain(range);
  }, 100);
}).observe(rootContainer);

/* ──────────────────────────────────────────────────────────────────
   LIVE APPEND STUB
────────────────────────────────────────────────────────────────── */
function appendNewBar(bar) {
  var normalized = normalizeBars(fullData.length ? [fullData[fullData.length-1], bar] : [bar]);
  var nextBar = normalized[normalized.length-1];
  if (!nextBar) return;

  fullData.push(nextBar);
  sourceCache.close.push(nextBar.close);
  sourceCache.open.push(nextBar.open);
  sourceCache.high.push(nextBar.high);
  sourceCache.low.push(nextBar.low);
  datasetVersion += 1;
  invalidateIndicatorCache();

  // If user is near right edge, extend window
  var vis = lastVisibleRange;
  var nearRight = vis ? binarySearchAtOrBefore(vis.to) >= fullData.length - 10 : true;
  if (nearRight) {
    loadedWindow.end = fullData.length;
    applyLoadedWindow();
    var last = fullData[fullData.length-1];
    var prev = fullData.length > 1 ? fullData[fullData.length-2] : null;
    updateHeader(last, prev);
  } else {
    var last2 = fullData[fullData.length-1];
    var prev2 = fullData.length > 1 ? fullData[fullData.length-2] : null;
    updateHeader(last2, prev2);
  }
}

/* ──────────────────────────────────────────────────────────────────
   INIT
────────────────────────────────────────────────────────────────── */
renderSignalUi();
renderIndicatorEditors();
DEFAULT_INDICATORS.forEach(function(def) { addIndicator(def); });
buildIntervalButtons(currentSymbol);

// Close IIFE
})();
```
