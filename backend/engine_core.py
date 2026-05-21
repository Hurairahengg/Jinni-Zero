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