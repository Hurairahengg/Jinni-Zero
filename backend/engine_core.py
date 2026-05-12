"""
JINNI ZERO — Strategy Backtest Engine (Bar-Close Execution)
============================================================
on_bar_close flow per bar:
  1. Store bar (pre-loaded) + indicators (pre-computed)
  2. If position open AND not entry bar → check TP/SL via wicks, MA cross via close
  3. Call strategy.on_bar(ctx) → BUY / SELL / HOLD / CLOSE
  4. If flat + BUY/SELL → open trade at bar CLOSE price (spread applied)
  5. Track equity / drawdown

Rule: a trade opened at bar close CANNOT close on the same bar.
      TP/SL only evaluated on future closed bars.
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
    apply_spread_entry,
)
from backend.strategies.base import VALID_SIGNALS
from backend.shared import (
    precompute_ma,
    get_or_compute_ma,
    compute_analytics,
    clean_for_json,
    cap_analytics_arrays,
)

logger = logging.getLogger("jinni.engine")

MAX_TRADES_IN_RESPONSE = 2000
MAX_ANALYTICS_POINTS   = 1500
PROGRESS_EMIT_INTERVAL = 0.15


# ════════════════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════════════════
def safe_float(v, default=0.0):
    try:
        if v is None: return default
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


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
        raise ValueError(f"Bar {bar_index}: expected dict, got {type(raw).__name__}")

    sig = raw.get("signal")
    if sig is not None:
        sig = str(sig).upper()
    if sig not in VALID_SIGNALS:
        raise ValueError(f"Bar {bar_index}: invalid signal '{sig}'")

    out = {"signal": sig or "HOLD"}

    # Direct SL/TP
    if raw.get("sl") is not None:  out["sl"] = float(raw["sl"])
    if raw.get("tp") is not None:  out["tp"] = float(raw["tp"])

    # Engine-computed SL/TP
    for key in ("sl_mode", "sl_pts", "sl_ma_key", "sl_ma_val", "tp_mode", "tp_r"):
        if raw.get(key) is not None:
            out[key] = raw[key] if key in ("sl_mode", "sl_ma_key", "tp_mode") else float(raw[key])

    # MA cross exit keys
    if raw.get("engine_sl_ma_key") is not None:
        out["engine_sl_ma_key"] = str(raw["engine_sl_ma_key"])
    if raw.get("engine_tp_ma_key") is not None:
        out["engine_tp_ma_key"] = str(raw["engine_tp_ma_key"])

    # CLOSE handling
    if out["signal"] == "CLOSE" or raw.get("close"):
        out["close"] = True
        out["close_reason"] = str(raw.get("close_reason", "strategy_close"))

    # Dynamic updates
    if raw.get("update_sl") is not None: out["update_sl"] = float(raw["update_sl"])
    if raw.get("update_tp") is not None: out["update_tp"] = float(raw["update_tp"])

    return out


# ════════════════════════════════════════════════════════════════════
#  POSITION STATE (frozen — read-only for strategy)
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
    mae:             float           = 0.0
    mfe:             float           = 0.0


def _build_position_state(open_t, bar_index, close_price, lot_size, point_value):
    if open_t is None:
        return PositionState(has_position=False)
    d = open_t["direction"]; ep = open_t["entry_price"]
    pts = (close_price - ep) if d == "long" else (ep - close_price)
    return PositionState(
        has_position=True, direction=d, entry_price=ep,
        entry_time=open_t.get("entry_time"), entry_bar=open_t.get("entry_bar"),
        bars_held=bar_index - open_t.get("entry_bar", 0),
        sl_level=open_t.get("sl_level"), tp_level=open_t.get("tp_level"),
        unrealized_pts=round(pts, 4),
        unrealized_pnl=round(points_to_dollars(pts, lot_size, point_value), 2),
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
    )

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


# ════════════════════════════════════════════════════════════════════
#  BACKTEST ENGINE (BAR-CLOSE MODEL)
# ════════════════════════════════════════════════════════════════════
class BacktestEngine:

    def __init__(self, bars, strategy, payload):
        self.bars     = bars
        self.strategy = strategy
        self.payload  = payload or {}
        self.n        = len(bars)

        self.params = strategy.validate_parameters(self.payload.get("parameters", {}))

        self.lot_size     = float(self.payload.get("lot_size", 1.0))
        self.point_value  = float(self.payload.get("point_value", 1.0))
        self.starting_cap = float(self.payload.get("starting_capital", 10000.0))

        # ── Commission (accept old and new format) ───────────
        comm = self.payload.get("commission", {})
        if isinstance(comm, dict):
            self.commission_per_1_lot = float(comm.get("amount", 0))
        else:
            self.commission_per_1_lot = float(self.payload.get("commission_per_1_lot", 0))

        # ── Spread (accept old and new format) ───────────────
        spread = self.payload.get("spread", {})
        if isinstance(spread, dict) and spread.get("enabled"):
            self.spread_points = (float(spread.get("min", 0)) + float(spread.get("max", 0))) / 2.0
        else:
            self.spread_points = float(self.payload.get("spread_points", 0))

        self.ambiguous_mode = self.payload.get("ambiguous_bar_mode", "conservative")

        strategy_min   = getattr(strategy, "min_lookback", 0) or 0
        user_override  = int(self.payload.get("lookback_override", 0) or 0)
        self.lookback  = max(strategy_min, user_override)

        # ── Indicators ───────────────────────────────────────
        self.indicator_plan  = strategy.build_indicators(self.params)
        self.indicator_store = {}
        for spec in self.indicator_plan:
            self.indicator_store[spec["key"]] = precompute_indicator_series(self.bars, spec)

        self.trades       = []
        self.equity_curve = []
        self.dd_curve     = []
        self._perf        = {}

        print(f"  [ENGINE] {self.n} bars | strategy={strategy.strategy_id} | "
              f"lot={self.lot_size} pv={self.point_value} cap={self.starting_cap} | "
              f"comm/lot={self.commission_per_1_lot} spread={self.spread_points}pts | "
              f"lookback={self.lookback} ambiguous={self.ambiguous_mode}")

    # ── Indicator value at bar i ─────────────────────────────
    def _ind_at(self, i):
        return {k: (v[i] if i < len(v) else None) for k, v in self.indicator_store.items()}

    # ── Equity helper ────────────────────────────────────────
    def _calc_equity(self, cum, open_t, close_price):
        if open_t is None:
            return self.starting_cap + cum
        d  = open_t["direction"]; ep = open_t["entry_price"]
        pts = (close_price - ep) if d == "long" else (ep - close_price)
        return self.starting_cap + cum + points_to_dollars(
            pts, open_t.get("lot_size", self.lot_size), self.point_value)

    # ── Build context for strategy ───────────────────────────
    def _build_ctx(self, i, bar, open_t, cum, equity):
        lot = open_t.get("lot_size", self.lot_size) if open_t else self.lot_size
        return StrategyContext(
            index=i, bar=bar, bars=self.bars,
            indicators=self._ind_at(i), ind_series=self.indicator_store,
            position=_build_position_state(open_t, i, float(bar["close"]), lot, self.point_value),
            balance=round(self.starting_cap + cum, 2),
            equity=round(equity, 2),
            trades=self.trades, params=self.params,
            state=self._strategy_state,
        )

    # ════════════════════════════════════════════════════════════
    #  STEP 3a: TP/SL CHECK (wick logic)
    # ════════════════════════════════════════════════════════════
    def _check_tp_sl(self, t, bar, bi):
        d = t["direction"]; sl = t.get("sl_level"); tp = t.get("tp_level")
        h = float(bar["high"]); l = float(bar["low"])

        sl_hit = False
        if sl is not None:
            if d == "long"  and l <= sl: sl_hit = True
            if d == "short" and h >= sl: sl_hit = True

        tp_hit = False
        if tp is not None:
            if d == "long"  and h >= tp: tp_hit = True
            if d == "short" and l <= tp: tp_hit = True

        if sl_hit and tp_hit:
            m = self.ambiguous_mode
            if m == "optimistic":
                return self._make_exit(t, bar, bi, tp, "TP_HIT")
            elif m == "nearest_to_open":
                o = float(bar["open"])
                if abs(o - sl) <= abs(o - tp):
                    return self._make_exit(t, bar, bi, sl, "SL_HIT")
                return self._make_exit(t, bar, bi, tp, "TP_HIT")
            else:
                return self._make_exit(t, bar, bi, sl, "SL_HIT")

        if sl_hit: return self._make_exit(t, bar, bi, sl, "SL_HIT")
        if tp_hit: return self._make_exit(t, bar, bi, tp, "TP_HIT")
        return None

    # ════════════════════════════════════════════════════════════
    #  STEP 3b: MA CROSS EXITS (close price logic)
    # ════════════════════════════════════════════════════════════
    def _check_ma_exits(self, t, bar, bi):
        c = float(bar["close"]); d = t["direction"]

        for label, key_name, reason in [
            ("SL", "_engine_sl_ma_key", "MA_SL_EXIT"),
            ("TP", "_engine_tp_ma_key", "MA_TP_EXIT"),
        ]:
            ma_key = t.get(key_name)
            if not ma_key or ma_key not in self.indicator_store:
                continue
            series = self.indicator_store[ma_key]
            val = series[bi] if bi < len(series) else None
            if val is None:
                continue
            if d == "long"  and c < val: return self._make_exit(t, bar, bi, c, reason)
            if d == "short" and c > val: return self._make_exit(t, bar, bi, c, reason)
        return None

    # ── MAE / MFE update (points, from wicks) ────────────────
    def _update_mae_mfe(self, t, bar):
        d = t["direction"]; ep = t["entry_price"]
        h = float(bar["high"]); l = float(bar["low"])
        t["mae"] = max(t.get("mae", 0), (ep - l) if d == "long" else (h - ep))
        t["mfe"] = max(t.get("mfe", 0), (h - ep) if d == "long" else (ep - l))
        t["bars_held"] = 0  # updated properly in _make_exit

    # ── Exit builder ─────────────────────────────────────────
    def _make_exit(self, t, bar, bi, exit_price, reason):
        return {**t,
                "exit_bar": bi, "exit_time": bar["time"],
                "exit_price": round(float(exit_price), 4),
                "exit_reason": reason,
                "holding_seconds": abs(bar["time"] - t["entry_time"]),
                "bars_held": bi - t["entry_bar"]}

    # ── Finalize + record trade ──────────────────────────────
    def _close_trade(self, open_t, bar, bi, exit_price, reason, cum):
        closed = self._make_exit(open_t, bar, bi, exit_price, reason)
        closed.pop("_engine_sl_ma_key", None)
        closed.pop("_engine_tp_ma_key", None)

        finalize_trade_pnl(
            closed, lot_size=closed.get("lot_size", self.lot_size),
            point_value=self.point_value,
            commission_per_1_lot=self.commission_per_1_lot,
        )
        cum += closed["net_pnl"]
        closed["cumulative_pnl"] = round(cum, 2)
        self.trades.append(closed)

        if len(self.trades) <= 5:
            t = closed
            print(f"  [TRADE #{t['id']}] {t['direction'].upper()} "
                  f"entry={t['entry_price']} exit={t['exit_price']} "
                  f"SL={t.get('sl_level')} TP={t.get('tp_level')} "
                  f"risk={t.get('risk_pts')}pts lot={t.get('lot_size')} "
                  f"spread={t.get('spread_points',0)}pts | "
                  f"pts={t.get('points_pnl')} R={t.get('net_pnl_r')} "
                  f"gross=${t.get('gross_pnl')} comm=${t.get('commission')} "
                  f"net=${t.get('net_pnl')} | {t.get('exit_reason')}")
        return cum

    # ── SL computation from signal ───────────────────────────
    def _compute_sl(self, action, direction, entry_price):
        if action.get("sl") is not None:
            sl = float(action["sl"])
            rp = abs(entry_price - sl)
            return (round(sl, 4), round(rp, 4)) if rp > 0 else (None, None)

        mode = action.get("sl_mode")
        if mode == "fixed":
            pts = float(action.get("sl_pts", 0))
            if pts > 0:
                sl = (entry_price - pts) if direction == "long" else (entry_price + pts)
                return round(sl, 4), pts

        if mode == "ma_snapshot":
            mv = action.get("sl_ma_val")
            if mv is not None:
                ok = (direction == "long" and mv < entry_price) or \
                     (direction == "short" and mv > entry_price)
                if ok:
                    rp = abs(entry_price - mv)
                    if rp > 0:
                        return round(mv, 4), round(rp, 4)

        return None, None

    # ── TP computation from signal ───────────────────────────
    def _compute_tp(self, action, direction, entry_price, risk_pts):
        if action.get("tp") is not None:
            return round(float(action["tp"]), 4)

        if action.get("tp_mode") == "r_multiple":
            r = float(action.get("tp_r", 2))
            if risk_pts and risk_pts > 0:
                tp = (entry_price + risk_pts * r) if direction == "long" \
                     else (entry_price - risk_pts * r)
                return round(tp, 4)
        return None

    # ════════════════════════════════════════════════════════════
    #  MAIN LOOP — BAR-CLOSE EXECUTION
    # ════════════════════════════════════════════════════════════
    def _run_generator(self):
        sim_t0 = _time.perf_counter()

        self._strategy_state = {}
        init_ctx = self._build_ctx(0, self.bars[0], None, 0.0, self.starting_cap)
        self.strategy.on_init(init_ctx)

        open_t = None
        cum    = 0.0
        peak   = self.starting_cap
        last_closed_pnl = None

        progress_interval = max(1, self.n // 100)
        last_emit_time    = _time.perf_counter()

        for i, bar in enumerate(self.bars):
            c = float(bar["close"])

            # ═══ STEP 3: Manage open position ═══════════════
            if open_t is not None:
                if open_t["entry_bar"] != i:
                    # Future bar — check TP/SL + MA exits
                    self._update_mae_mfe(open_t, bar)
                    closed = self._check_tp_sl(open_t, bar, i)
                    if closed is None:
                        closed = self._check_ma_exits(open_t, bar, i)
                    if closed:
                        cum = self._close_trade(open_t, bar, i,
                                                closed["exit_price"], closed["exit_reason"], cum)
                        last_closed_pnl = self.trades[-1]["net_pnl"]
                        open_t = None
                # Entry bar — no TP/SL check, no MAE/MFE
                # (trade opens at close, wicks already happened)

            # ═══ STEP 4: Strategy evaluation ════════════════
            action = {"signal": "HOLD"}
            if i >= self.lookback:
                eq_now = self._calc_equity(cum, open_t, c)
                ctx    = self._build_ctx(i, bar, open_t, cum, eq_now)
                raw    = self.strategy.on_bar(ctx)
                try:
                    action = validate_signal(raw, i)
                except ValueError as e:
                    logger.error(str(e))

            sig = action["signal"]

            # ── Handle CLOSE ─────────────────────────────────
            if action.get("close") and open_t is not None:
                reason = action.get("close_reason", "strategy_close")
                cum = self._close_trade(open_t, bar, i, c, reason, cum)
                last_closed_pnl = self.trades[-1]["net_pnl"]
                open_t = None
                # Re-evaluate for flip
                if i >= self.lookback:
                    ctx2 = self._build_ctx(i, bar, None, cum, self._calc_equity(cum, None, c))
                    raw2 = self.strategy.on_bar(ctx2)
                    try:
                        action = validate_signal(raw2, i)
                        sig = action["signal"]
                    except ValueError:
                        sig = "HOLD"

            # ── Dynamic SL/TP updates ────────────────────────
            if open_t is not None:
                if action.get("update_sl") is not None:
                    open_t["sl_level"] = float(action["update_sl"])
                if action.get("update_tp") is not None:
                    open_t["tp_level"] = float(action["update_tp"])

            # ═══ STEP 5: Execute entry at bar close ═════════
            if open_t is None and sig in ("BUY", "SELL"):
                direction = "long" if sig == "BUY" else "short"
                ep = apply_spread_entry(c, direction, self.spread_points)

                sl_level, risk_pts = self._compute_sl(action, direction, ep)
                tp_level = self._compute_tp(action, direction, ep, risk_pts)

                # Validate
                valid = True
                if sl_level is not None:
                    if direction == "long"  and sl_level >= ep: valid = False
                    if direction == "short" and sl_level <= ep: valid = False
                if tp_level is not None:
                    if direction == "long"  and tp_level <= ep: valid = False
                    if direction == "short" and tp_level >= ep: valid = False
                if risk_pts is not None and risk_pts <= 0:
                    valid = False

                if valid:
                    open_t = dict(
                        id=len(self.trades) + 1, direction=direction,
                        entry_bar=i, entry_time=bar["time"],
                        entry_price=round(ep, 4),
                        sl_level=sl_level, tp_level=tp_level,
                        risk_pts=risk_pts, mae=0.0, mfe=0.0, bars_held=0,
                        spread_points=self.spread_points,
                        lot_size=self.lot_size,
                        _engine_sl_ma_key=action.get("engine_sl_ma_key"),
                        _engine_tp_ma_key=action.get("engine_tp_ma_key"),
                    )

            # ═══ Equity / drawdown ══════════════════════════
            eq = self._calc_equity(cum, open_t, c)
            self.equity_curve.append(round(eq, 2))
            peak = max(peak, eq)
            self.dd_curve.append(round(eq - peak, 2))

            # ═══ Progress yield ═════════════════════════════
            now = _time.perf_counter()
            if i == 0 or i == self.n - 1 or \
               (i % progress_interval == 0 and (now - last_emit_time) >= PROGRESS_EMIT_INTERVAL):
                last_emit_time = now
                oi = None
                if open_t:
                    oi = dict(direction=open_t["direction"],
                              entry_price=round(open_t["entry_price"], 2),
                              sl=round(open_t["sl_level"], 2) if open_t.get("sl_level") else None,
                              tp=round(open_t["tp_level"], 2) if open_t.get("tp_level") else None)
                yield dict(type="progress", bar=i, total=self.n,
                           pct=round(i / max(self.n - 1, 1) * 100, 1),
                           equity=round(eq, 2), drawdown=round(eq - peak, 2),
                           open_trade=oi, last_closed_pnl=last_closed_pnl)

        # ═══ End of data — close open trade ═════════════════
        if open_t is not None:
            lb = self.bars[-1]
            cum = self._close_trade(open_t, lb, self.n - 1,
                                    float(lb["close"]), "end_of_data", cum)

        end_ctx = self._build_ctx(
            self.n - 1, self.bars[-1] if self.bars else {},
            None, cum, self.starting_cap + cum)
        self.strategy.on_end(end_ctx)

        self._perf["simulation_seconds"] = round(_time.perf_counter() - sim_t0, 4)

    # ════════════════════════════════════════════════════════════
    #  BUILD RESULT
    # ════════════════════════════════════════════════════════════
    def _build_result(self):
        perf = self._perf
        t0 = _time.perf_counter()

        stats = compute_all_stats(
            trades=self.trades, equity_curve=self.equity_curve,
            bars=self.bars, starting_capital=self.starting_cap,
            lot_size=self.lot_size)
        perf["stats_seconds"] = round(_time.perf_counter() - t0, 4)

        t1 = _time.perf_counter()
        analytics = compute_analytics(
            self.trades, self.bars,
            self.equity_curve, self.dd_curve, self.starting_cap)
        perf["analytics_seconds"] = round(_time.perf_counter() - t1, 4)

        analytics = cap_analytics_arrays(analytics, MAX_ANALYTICS_POINTS)

        total_count = len(self.trades)
        trades_out = self.trades[-MAX_TRADES_IN_RESPONSE:] if total_count > MAX_TRADES_IN_RESPONSE else self.trades

        result = clean_for_json(dict(
            stats=stats, trades=trades_out,
            total_trade_count=total_count,
            trades_truncated=total_count > MAX_TRADES_IN_RESPONSE,
            equity_curve=downsample_curve(self.equity_curve, 1500),
            drawdown_curve=downsample_curve(self.dd_curve, 1500),
            analytics=analytics, performance=perf,
            config=dict(
                strategy_id=self.strategy.strategy_id, parameters=self.params,
                starting_capital=self.starting_cap, range=int(self.payload.get("range", 10)),
                lot_size=self.lot_size, point_value=self.point_value),
        ))

        perf["trade_count"] = total_count
        print(f"  [TIMING] sim={perf.get('simulation_seconds',0):.3f}s "
              f"stats={perf.get('stats_seconds',0):.3f}s "
              f"analytics={perf.get('analytics_seconds',0):.3f}s trades={total_count}")
        return result

    # ════════════════════════════════════════════════════════════
    #  PUBLIC API
    # ════════════════════════════════════════════════════════════
    def run(self):
        for _ in self._run_generator(): pass
        return self._build_result()

    def run_streaming(self):
        yield from self._run_generator()
        yield {"type": "result", "data": self._build_result()}