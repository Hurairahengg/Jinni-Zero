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

ALL dollar math goes through dollar_math.py:
  dollars = points × lot_size × point_value

R-multiples are computed FIRST (pure), dollars derived AFTER.

Strategies are SIGNAL PROVIDERS ONLY.
They return: BUY / SELL / HOLD / CLOSE + optional SL/TP.
They NEVER touch sizing, PnL, equity, commission, or stats.

ENGINE-COMPUTED SL/TP (Phase 3):
  Strategies can request SL/TP modes that the engine computes
  at fill time (next bar open), matching Legacy exactly:
    sl_mode="fixed"       + sl_pts=8.0
    sl_mode="ma_snapshot" + sl_ma_val=<MA value from signal bar>
    tp_mode="r_multiple"  + tp_r=2.0

MA CROSS EXITS (Phase 4):
  Strategies can request engine-level MA cross exit checking:
    engine_sl_ma_key="sl_ma"   → engine checks this MA for SL cross
    engine_tp_ma_key="tp_ma"   → engine checks this MA for TP cross
"""
from __future__ import annotations

import logging
import math
import time as _time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from backend.stats_engine import compute_all_stats, downsample_curve
from backend.dollar_math import points_to_dollars, finalize_trade_pnl
from backend.strategies.base import VALID_SIGNALS
from backend.shared import (
    precompute_ma,
    get_or_compute_ma,
    SpreadGenerator,
    calc_comm,
    compute_analytics,
    clean_for_json,
)

logger = logging.getLogger("jinni.engine")


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
    """Normalize + validate strategy output. Returns clean dict or raises."""
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

    # ── Optional absolute SL/TP on entry signals ─────────────
    if raw.get("sl") is not None:
        out["sl"] = float(raw["sl"])
    if raw.get("tp") is not None:
        out["tp"] = float(raw["tp"])

    # ── Engine-computed SL/TP fields (Phase 3) ───────────────
    if raw.get("sl_mode") is not None:
        out["sl_mode"] = str(raw["sl_mode"])
    if raw.get("sl_pts") is not None:
        out["sl_pts"] = float(raw["sl_pts"])
    if raw.get("sl_ma_key") is not None:
        out["sl_ma_key"] = str(raw["sl_ma_key"])
    if raw.get("sl_ma_val") is not None:
        out["sl_ma_val"] = float(raw["sl_ma_val"])
    if raw.get("tp_mode") is not None:
        out["tp_mode"] = str(raw["tp_mode"])
    if raw.get("tp_r") is not None:
        out["tp_r"] = float(raw["tp_r"])

    # ── Engine-level MA cross exit keys (Phase 4) ────────────
    if raw.get("engine_sl_ma_key") is not None:
        out["engine_sl_ma_key"] = str(raw["engine_sl_ma_key"])
    if raw.get("engine_tp_ma_key") is not None:
        out["engine_tp_ma_key"] = str(raw["engine_tp_ma_key"])

    # ── CLOSE signal ─────────────────────────────────────────
    if out["signal"] == "CLOSE":
        out["close"] = True
        out["close_reason"] = str(raw.get("close_reason", "strategy_close"))
    elif raw.get("close"):
        out["close"] = True
        out["close_reason"] = str(raw.get("close_reason", "strategy_close"))

    # ── Dynamic SL/TP updates ────────────────────────────────
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
    """Immutable snapshot of open trade state. Strategy can READ but not MODIFY."""
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
    mae:             float           = 0.0
    mfe:             float           = 0.0


def _build_position_state(open_t, bar_index, close_price, lot_size, point_value=1.0):
    """Build frozen PositionState from open trade dict."""
    if open_t is None:
        return PositionState(has_position=False)

    d = open_t["direction"]
    ep = open_t["entry_price"]
    pts = (close_price - ep) if d == "long" else (ep - close_price)

    return PositionState(
        has_position=True,
        direction=d,
        entry_price=ep,
        entry_time=open_t.get("entry_time"),
        entry_bar=open_t.get("entry_bar"),
        bars_held=bar_index - open_t.get("entry_bar", 0),
        sl_level=open_t.get("sl_level"),
        tp_level=open_t.get("tp_level"),
        unrealized_pts=round(pts, 4),
        unrealized_pnl=round(points_to_dollars(pts, lot_size, point_value), 2),
        mae=round(open_t.get("mae", 0), 4),
        mfe=round(open_t.get("mfe", 0), 4),
    )


# ════════════════════════════════════════════════════════════════════
#  STRATEGY CONTEXT (passed to strategy.on_bar)
# ════════════════════════════════════════════════════════════════════
class StrategyContext:
    """Read-only context (except .state) passed to strategy each bar."""
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
#  BACKTEST ENGINE (LEGACY EXECUTION + STRATEGY SIGNALS)
# ════════════════════════════════════════════════════════════════════
class BacktestEngine:
    """
    The engine is a DUMB BROKER.
    It uses LEGACY execution logic for EVERYTHING:
      - pending_signal → enter next bar OPEN
      - engine-computed SL/TP at fill time (fixed pts, MA snapshot, R-multiple)
      - spread on entry/exit/sl/tp
      - SL/TP hit detection + ambiguous bar handling
      - MA cross exit detection (engine-level)
      - PnL via centralized dollar_math (points × lot_size × point_value)
      - commission = legacy calc_comm
      - MAE/MFE tracking
      - equity/drawdown curves

    The ONLY input from strategy is: signal + optional sl/tp/close.
    """

    def __init__(self, bars, strategy, payload):
        self.bars       = bars
        self.strategy   = strategy
        self.payload    = payload or {}
        self.n          = len(bars)

        # ── Strategy params ──────────────────────────────────────
        self.params = strategy.validate_parameters(
            self.payload.get("parameters", {})
        )

        # ── Legacy engine config (from payload) ──────────────────
        self.lot_size       = float(self.payload.get("lot_size", 1.0))
        self.point_value    = float(self.payload.get("point_value", 1.0))
        self.starting_cap   = float(self.payload.get("starting_capital", 10000.0))
        self.comm_cfg       = self.payload.get("commission", {})
        self.ambiguous_mode = self.payload.get("ambiguous_bar_mode", "conservative")

        # ── Spread (legacy-exact) ────────────────────────────────
        self.spread_gen = SpreadGenerator(self.payload.get("spread", {}))

        # ── Lookback ─────────────────────────────────────────────
        strategy_min = getattr(strategy, "min_lookback", 0) or 0
        user_override = int(self.payload.get("lookback_override", 0) or 0)
        self.lookback = max(strategy_min, user_override)

        # ── Precompute strategy indicators ───────────────────────
        self.indicator_plan = strategy.build_indicators(self.params)
        self.indicator_store = {}
        for spec in self.indicator_plan:
            self.indicator_store[spec["key"]] = precompute_indicator_series(self.bars, spec)

        # ── Results ──────────────────────────────────────────────
        self.trades       = []
        self.equity_curve = []
        self.dd_curve     = []

        print(
            f"  [ENGINE] {self.n} bars | strategy={strategy.strategy_id} | "
            f"lot={self.lot_size} | pv={self.point_value} | cap={self.starting_cap} | "
            f"lookback={self.lookback} | spread={self.spread_gen.enabled} | "
            f"ambiguous={self.ambiguous_mode}"
        )

    # ── Indicator helpers ────────────────────────────────────────
    def _indicators_at(self, i):
        return {
            k: (v[i] if i < len(v) else None)
            for k, v in self.indicator_store.items()
        }

    # ── Build context for strategy ───────────────────────────────
    def _build_ctx(self, i, bar, open_t, cum, equity, prev_indicators=None):
        pos_state = _build_position_state(
            open_t, i, float(bar["close"]), self.lot_size, self.point_value
        )
        return StrategyContext(
            index=i,
            bar=bar,
            bars=self.bars,
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
    #  LEGACY EXIT CHECK (SL/TP hit + MA cross exits)
    # ════════════════════════════════════════════════════════════════
    def _check_exit(self, t, bar, bi):
        d  = t["direction"]
        ep = t["entry_price"]
        sl = t.get("sl_level")
        tp = t.get("tp_level")
        hh = bar["high"]
        ll = bar["low"]
        c  = bar["close"]

        t["bars_held"] = bi - t["entry_bar"]
        t["mae"] = max(t.get("mae", 0), (ep - ll) if d == "long" else (hh - ep))
        t["mfe"] = max(t.get("mfe", 0), (hh - ep) if d == "long" else (ep - ll))

        # ── Fixed SL/TP hit detection ────────────────────────────
        sl_hit = False
        if sl is not None:
            if d == "long"  and ll <= sl: sl_hit = True
            if d == "short" and hh >= sl: sl_hit = True

        tp_hit = False
        if tp is not None:
            if d == "long"  and hh >= tp: tp_hit = True
            if d == "short" and ll <= tp: tp_hit = True

        # ── Ambiguous bar handling (legacy-exact) ────────────────
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

        if sl_hit:
            return self._make_exit(t, bar, bi, sl, "SL_HIT")
        if tp_hit:
            return self._make_exit(t, bar, bi, tp, "TP_R")

        # ── MA cross exits (Phase 4 — legacy-exact timing) ──────
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
            "exit_bar": bi,
            "exit_time": bar["time"],
            "exit_price": round(exit_price, 4),
            "exit_reason": reason,
            "holding_seconds": abs(bar["time"] - t["entry_time"]),
            "bars_held": bi - t["entry_bar"],
        }

    # ════════════════════════════════════════════════════════════════
    #  FINALIZE TRADE (centralized via dollar_math.py)
    # ════════════════════════════════════════════════════════════════
    def _finalize_trade(self, closed):
        commission = calc_comm(self.comm_cfg)
        finalize_trade_pnl(
            closed,
            lot_size=self.lot_size,
            point_value=self.point_value,
            commission=commission,
        )

    # ── Helper: close trade cleanly ──────────────────────────────
    def _close_trade(self, open_t, bar, bi, exit_price, reason, cum):
        """Close a trade with spread, finalize, append. Returns new cum."""
        # ── Update MAE/MFE for exit bar (legacy-exact) ───────────
        d = open_t["direction"]
        ep = open_t["entry_price"]
        hh = bar["high"]
        ll = bar["low"]
        open_t["bars_held"] = bi - open_t["entry_bar"]
        open_t["mae"] = max(open_t.get("mae", 0),
                            (ep - ll) if d == "long" else (hh - ep))
        open_t["mfe"] = max(open_t.get("mfe", 0),
                            (hh - ep) if d == "long" else (ep - ll))

        closed = self._make_exit(open_t, bar, bi, exit_price, reason)

        # ── Apply spread to exit price ───────────────────────────
        trade_spread = closed.get("spread", 0.0)
        closed["exit_price"] = round(
            self.spread_gen.apply_exit(exit_price, closed["direction"], trade_spread), 4
        )

        # ── Strip engine-internal keys from trade record ─────────
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
                f"points={t.get('points_pnl')} "
                f"R={t.get('net_pnl_r')} "
                f"gross=${t.get('gross_pnl')} "
                f"comm=${t.get('commission')} "
                f"net=${t.get('net_pnl')} "
                f"reason={t.get('exit_reason')}"
            )

        return cum

    # ════════════════════════════════════════════════════════════════
    #  MAIN RUN
    # ════════════════════════════════════════════════════════════════
    def run(self):
        perf = {}
        total_t0 = _time.perf_counter()

        # ── Init strategy ────────────────────────────────────────
        self._strategy_state = {}
        init_ctx = self._build_ctx(0, self.bars[0], None, 0.0, self.starting_cap)
        self.strategy.on_init(init_ctx)

        # ── Legacy loop state ────────────────────────────────────
        state = "flat"
        open_t = None
        pending_signal = None   # dict or None
        cum = 0.0
        peak = self.starting_cap
        prev_indicators = {}

        sim_t0 = _time.perf_counter()

        for i, bar in enumerate(self.bars):
            c = float(bar["close"])
            o = float(bar["open"])
            h = float(bar["high"])
            l = float(bar["low"])

            just_entered = False

            # ══════════════════════════════════════════════════════
            # STEP 1: Process pending entry at THIS bar OPEN
            # (legacy: pending_signal → enter at next bar open)
            #
            # ENGINE-COMPUTED SL/TP (Phase 3):
            #   SL/TP are computed from the FILL PRICE (this bar's
            #   open), matching Legacy exactly. Order of operations:
            #     1. Compute SL from pre-spread entry price
            #     2. Compute TP from pre-spread entry + pre-spread risk
            #     3. Apply spread to entry, SL, TP
            #     4. Recompute risk_pts from spread-adjusted values
            # ══════════════════════════════════════════════════════
            if pending_signal is not None and state == "flat":
                direction = pending_signal["direction"]
                ep = o  # pre-spread entry price

                # ── SL computation (at pre-spread entry price) ───
                sl_level = None
                risk_pts = None
                sl_mode = pending_signal.get("sl_mode")

                if sl_mode == "fixed":
                    pts = float(pending_signal.get("sl_pts", 0))
                    if pts > 0:
                        sl_level = (ep - pts) if direction == "long" else (ep + pts)
                        sl_level = round(sl_level, 4)
                        risk_pts = pts

                elif sl_mode == "ma_snapshot":
                    ma_val = pending_signal.get("sl_ma_val")
                    if ma_val is not None:
                        if direction == "long" and ma_val < ep:
                            sl_level = round(ma_val, 4)
                            risk_pts = round(abs(ep - sl_level), 4)
                        elif direction == "short" and ma_val > ep:
                            sl_level = round(ma_val, 4)
                            risk_pts = round(abs(ep - sl_level), 4)

                elif pending_signal.get("sl") is not None:
                    # Absolute SL (existing behavior)
                    sl_level = float(pending_signal["sl"])
                    risk_pts = abs(ep - sl_level) if sl_level is not None else None

                # ── TP computation (at pre-spread entry + pre-spread risk) ──
                tp_level = None
                tp_mode = pending_signal.get("tp_mode")

                if tp_mode == "r_multiple":
                    r = float(pending_signal.get("tp_r", 2))
                    if risk_pts and risk_pts > 0:
                        tp_level = (ep + risk_pts * r) if direction == "long" \
                                   else (ep - risk_pts * r)
                        tp_level = round(tp_level, 4)

                elif pending_signal.get("tp") is not None:
                    # Absolute TP (existing behavior)
                    tp_level = float(pending_signal["tp"])

                # ── Apply spread (legacy order) ──────────────────
                trade_spread = self.spread_gen.generate()
                ep = self.spread_gen.apply_entry(ep, direction, trade_spread)
                sl_level = self.spread_gen.apply_sl(sl_level, direction, trade_spread)
                tp_level = self.spread_gen.apply_tp(tp_level, direction, trade_spread)

                # ── Recompute risk_pts after spread ──────────────
                if sl_level is not None:
                    risk_pts = abs(ep - sl_level)

                # ── Validation (legacy-exact) ────────────────────
                valid = True
                if risk_pts is not None and risk_pts <= 0:
                    valid = False
                if sl_level is not None:
                    if direction == "long"  and sl_level >= ep: valid = False
                    if direction == "short" and sl_level <= ep: valid = False
                if tp_level is not None:
                    if direction == "long"  and tp_level <= ep: valid = False
                    if direction == "short" and tp_level >= ep: valid = False

                if valid:
                    open_t = dict(
                        id=len(self.trades) + 1,
                        direction=direction,
                        entry_bar=i,
                        entry_time=bar["time"],
                        entry_price=round(ep, 4),
                        sl_level=sl_level,
                        tp_level=tp_level,
                        risk_pts=risk_pts,
                        mae=0.0,
                        mfe=0.0,
                        bars_held=0,
                        spread=round(trade_spread, 4),
                        _engine_sl_ma_key=pending_signal.get("engine_sl_ma_key"),
                        _engine_tp_ma_key=pending_signal.get("engine_tp_ma_key"),
                    )
                    state = direction
                    just_entered = True

                pending_signal = None

            # ══════════════════════════════════════════════════════
            # STEP 2: Check SL/TP + MA cross exits
            # Only on bars AFTER entry (legacy: not just_entered)
            # ══════════════════════════════════════════════════════
            if state != "flat" and open_t is not None and not just_entered:
                closed = self._check_exit(open_t, bar, i)
                if closed:
                    cum = self._close_trade(
                        open_t, bar, i,
                        closed["exit_price"],
                        closed["exit_reason"], cum
                    )
                    open_t = None
                    state = "flat"

            # ── Update MAE/MFE on entry bar (legacy) ─────────────
            if state != "flat" and open_t is not None and just_entered:
                d = open_t["direction"]
                ep2 = open_t["entry_price"]
                open_t["mae"] = max(open_t.get("mae", 0),
                                    (ep2 - l) if d == "long" else (h - ep2))
                open_t["mfe"] = max(open_t.get("mfe", 0),
                                    (h - ep2) if d == "long" else (ep2 - l))

            # ══════════════════════════════════════════════════════
            # STEP 3: Get strategy signal
            # ══════════════════════════════════════════════════════
            action = {"signal": "HOLD"}
            if i >= self.lookback:
                equity_now = self.starting_cap + cum
                if state != "flat" and open_t:
                    pts = (c - open_t["entry_price"]) if state == "long" \
                          else (open_t["entry_price"] - c)
                    equity_now += points_to_dollars(pts, self.lot_size, self.point_value)

                ctx = self._build_ctx(i, bar, open_t, cum, equity_now, prev_indicators)
                raw_signal = self.strategy.on_bar(ctx)

                try:
                    action = validate_signal(raw_signal, i)
                except ValueError as e:
                    logger.error(str(e))
                    action = {"signal": "HOLD"}

            sig = action["signal"]

            # ══════════════════════════════════════════════════════
            # STEP 4: Process CLOSE signal (strategy wants to exit)
            #
            # LEGACY MATCH: After closing, re-call strategy on the
            # SAME bar with position now flat. This allows MA exit +
            # new entry on the same bar (legacy runs signal generation
            # after exit in the same loop iteration).
            # ══════════════════════════════════════════════════════
            if action.get("close") and state != "flat" and open_t is not None:
                reason = action.get("close_reason", "strategy_close")
                cum = self._close_trade(open_t, bar, i, c, reason, cum)
                open_t = None
                state = "flat"

                # Re-call strategy with position now closed
                if i >= self.lookback:
                    eq_now = self.starting_cap + cum
                    ctx2 = self._build_ctx(i, bar, None, cum, eq_now, prev_indicators)
                    raw2 = self.strategy.on_bar(ctx2)
                    try:
                        action = validate_signal(raw2, i)
                    except ValueError:
                        action = {"signal": "HOLD"}
                    sig = action["signal"]

            # ══════════════════════════════════════════════════════
            # STEP 5: Dynamic SL/TP updates
            # ══════════════════════════════════════════════════════
            if state != "flat" and open_t is not None:
                if "update_sl" in action and action["update_sl"] is not None:
                    open_t["sl_level"] = float(action["update_sl"])
                if "update_tp" in action and action["update_tp"] is not None:
                    open_t["tp_level"] = float(action["update_tp"])

            # ══════════════════════════════════════════════════════
            # STEP 6: BUY/SELL → set pending (enter NEXT bar open)
            # Now stores full dict instead of tuple for engine-
            # computed SL/TP support.
            # ══════════════════════════════════════════════════════
            if state == "flat" and sig in ("BUY", "SELL"):
                direction = "long" if sig == "BUY" else "short"
                pending_signal = {
                    "direction": direction,
                    # Absolute SL/TP (existing)
                    "sl": action.get("sl"),
                    "tp": action.get("tp"),
                    # Engine-computed SL (Phase 3)
                    "sl_mode": action.get("sl_mode"),
                    "sl_pts": action.get("sl_pts"),
                    "sl_ma_key": action.get("sl_ma_key"),
                    "sl_ma_val": action.get("sl_ma_val"),
                    # Engine-computed TP (Phase 3)
                    "tp_mode": action.get("tp_mode"),
                    "tp_r": action.get("tp_r"),
                    # Engine-level MA cross exits (Phase 4)
                    "engine_sl_ma_key": action.get("engine_sl_ma_key"),
                    "engine_tp_ma_key": action.get("engine_tp_ma_key"),
                }

            # ══════════════════════════════════════════════════════
            # STEP 7: Equity / drawdown (centralized dollar math)
            # ══════════════════════════════════════════════════════
            if state != "flat" and open_t:
                pts = (c - open_t["entry_price"]) if state == "long" \
                      else (open_t["entry_price"] - c)
                unrealised = points_to_dollars(pts, self.lot_size, self.point_value)
            else:
                unrealised = 0.0
            eq = self.starting_cap + cum + unrealised
            self.equity_curve.append(round(eq, 2))
            peak = max(peak, eq)
            dd = eq - peak
            self.dd_curve.append(round(dd, 2))

            # ── Save current indicators for next bar's prev_indicators
            prev_indicators = self._indicators_at(i)

        # ══════════════════════════════════════════════════════════
        # END: Close open trade at end-of-data (legacy)
        # ══════════════════════════════════════════════════════════
        if state != "flat" and open_t:
            lb = self.bars[-1]
            cp = float(lb["close"])
            cum = self._close_trade(open_t, lb, self.n - 1, cp, "end_of_data", cum)

        # ── Strategy cleanup ─────────────────────────────────────
        end_ctx = self._build_ctx(
            self.n - 1, self.bars[-1] if self.bars else {},
            None, cum, self.starting_cap + cum, prev_indicators
        )
        self.strategy.on_end(end_ctx)

        sim_t1 = _time.perf_counter()
        perf["simulation_seconds"] = round(sim_t1 - sim_t0, 4)

        # ── Stats (shared stats_engine.py) ───────────────────────
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

        # ── Analytics ────────────────────────────────────────────
        analytics_t0 = _time.perf_counter()
        analytics = compute_analytics(
            self.trades, self.bars,
            self.equity_curve, self.dd_curve,
            self.starting_cap,
        )
        analytics_t1 = _time.perf_counter()
        perf["analytics_seconds"] = round(analytics_t1 - analytics_t0, 4)

        # ── Build result ─────────────────────────────────────────
        result = dict(
            stats=stats,
            trades=self.trades,
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
            ),
        )

        result = clean_for_json(result)

        build_t1 = _time.perf_counter()
        perf["response_build_seconds"] = round(build_t1 - total_t0, 4)
        perf["trade_count"] = len(self.trades)

        print(
            f"  [BACKTEST TIMING] simulation={perf['simulation_seconds']:.3f}s "
            f"stats={perf['stats_seconds']:.3f}s "
            f"analytics={perf['analytics_seconds']:.3f}s "
            f"total={perf['response_build_seconds']:.3f}s "
            f"trades={perf['trade_count']}"
        )

        return result