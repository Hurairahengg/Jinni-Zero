# Repository Snapshot - Part 4 of 4

- Root folder: `/home/hurairahengg/Documents/Jinni Zero`
- you knwo my whole jinni grid systeM/ basically it is thereliek a kubernetes server setup what it does is basically a mother server with ui and bunch of lank state VMs. the vms run a speacial typa of renko style bars not normal timeframe u will get more context in the codes but yeha and we can uipload strategy codes though mother ui and it wiill run strategy mt5 report and ecetra ecetra. currently im done coding the strategy system but its not tested yet an have confrimed bugs. so firm i wil ldrop u my whole project codebases from my readme. understand each code its role and keep in ur context i will give u big promtps to update code later duinerstood
- Total files indexed: `23`
- Files in this chunk: `4`
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

## Files In This Chunk - Part 4

```text
backend/engine_core.py
index.html
js/strategy_loader.js
styles.css
```

## File Contents


---

## FILE: `backend/engine_core.py`

- Relative path: `backend/engine_core.py`
- Absolute path at snapshot time: `/home/hurairahengg/Documents/Jinni Zero/backend/engine_core.py`
- Size bytes: `33123`
- SHA256: `af4b424efa54db25e7517c4f430700ea9e9122d321892fcbf40808dade5e5097`
- Guessed MIME type: `text/x-python`
- Guessed encoding: `unknown`

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
from backend.dollar_math import points_to_dollars, finalize_trade_pnl
from backend.strategies.base import VALID_SIGNALS
from backend.shared import (
    precompute_ma,
    get_or_compute_ma,
    SpreadGenerator,
    calc_comm,
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
    mae:             float           = 0.0
    mfe:             float           = 0.0


def _build_position_state(open_t, bar_index, close_price, lot_size, point_value=1.0):
    if open_t is None:
        return PositionState(has_position=False)
    d = open_t["direction"]
    ep = open_t["entry_price"]
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
        self.starting_cap   = float(self.payload.get("starting_capital", 10000.0))
        self.comm_cfg       = self.payload.get("commission", {})
        self.ambiguous_mode = self.payload.get("ambiguous_bar_mode", "conservative")

        self.spread_gen = SpreadGenerator(self.payload.get("spread", {}))
        # ── Position sizing ──────────────────────────────────────
        self.sizing_mode = str(self.payload.get("sizing_mode", "fixed")).lower()
        self.risk_pct = float(self.payload.get("risk_pct", 1.0))

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
            f"{sizing_str} | pv={self.point_value} | cap={self.starting_cap} | "
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
            open_t, i, float(bar["close"]), trade_lot, self.point_value
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
        commission = calc_comm(self.comm_cfg)
        trade_lot = closed.get("lot_size", self.lot_size)
        finalize_trade_pnl(
            closed, lot_size=trade_lot,
            point_value=self.point_value, commission=commission,
        )

    def _close_trade(self, open_t, bar, bi, exit_price, reason, cum):
        d = open_t["direction"]; ep = open_t["entry_price"]
        hh = bar["high"]; ll = bar["low"]
        open_t["bars_held"] = bi - open_t["entry_bar"]
        open_t["mae"] = max(open_t.get("mae", 0), (ep - ll) if d == "long" else (hh - ep))
        open_t["mfe"] = max(open_t.get("mfe", 0), (hh - ep) if d == "long" else (ep - ll))

        closed = self._make_exit(open_t, bar, bi, exit_price, reason)
        trade_spread = closed.get("spread", 0.0)
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
            just_entered = False

            # ══ STEP 1: Process pending entry ═══════════════════
            if pending_signal is not None and state == "flat":
                direction = pending_signal["direction"]
                ep = o

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

                # ── Dynamic position sizing ──────────────────────
                if valid and self.sizing_mode == "risk_pct":
                    if risk_pts is None or risk_pts <= 0:
                        valid = False  # can't compute lot without SL distance
                    else:
                        balance_now = self.starting_cap + cum
                        risk_amount = balance_now * (self.risk_pct / 100.0)
                        trade_lot = risk_amount / (risk_pts * self.point_value)
                        trade_lot = max(0.01, round(trade_lot, 2))
                else:
                    trade_lot = self.lot_size

                if valid:
                    open_t = dict(
                        id=len(self.trades) + 1, direction=direction,
                        entry_bar=i, entry_time=bar["time"],
                        entry_price=round(ep, 4),
                        sl_level=sl_level, tp_level=tp_level,
                        risk_pts=risk_pts, mae=0.0, mfe=0.0, bars_held=0,
                        spread=round(trade_spread, 4),
                        lot_size=trade_lot,
                        _engine_sl_ma_key=pending_signal.get("engine_sl_ma_key"),
                        _engine_tp_ma_key=pending_signal.get("engine_tp_ma_key"),
                    )
                    state = direction; just_entered = True
                pending_signal = None

            # ══ STEP 2: Check exits ═════════════════════════════
            if state != "flat" and open_t is not None and not just_entered:
                closed = self._check_exit(open_t, bar, i)
                if closed:
                    cum = self._close_trade(
                        open_t, bar, i, closed["exit_price"], closed["exit_reason"], cum
                    )
                    last_closed_pnl = self.trades[-1]["net_pnl"]
                    open_t = None; state = "flat"

            # ── MAE/MFE on entry bar ─────────────────────────────
            if state != "flat" and open_t is not None and just_entered:
                d = open_t["direction"]; ep2 = open_t["entry_price"]
                open_t["mae"] = max(open_t.get("mae", 0), (ep2 - l) if d == "long" else (h - ep2))
                open_t["mfe"] = max(open_t.get("mfe", 0), (h - ep2) if d == "long" else (ep2 - l))

            # ══ STEP 3: Get strategy signal ═════════════════════
            action = {"signal": "HOLD"}
            if i >= self.lookback:
                equity_now = self.starting_cap + cum
                if state != "flat" and open_t:
                    pts = (c - open_t["entry_price"]) if state == "long" \
                          else (open_t["entry_price"] - c)
                    t_lot = open_t.get("lot_size", self.lot_size)
                    equity_now += points_to_dollars(pts, t_lot, self.point_value)
                ctx = self._build_ctx(i, bar, open_t, cum, equity_now, prev_indicators)
                raw_signal = self.strategy.on_bar(ctx)
                try:
                    action = validate_signal(raw_signal, i)
                except ValueError as e:
                    logger.error(str(e)); action = {"signal": "HOLD"}
            sig = action["signal"]

            # ══ STEP 4: Process CLOSE ═══════════════════════════
            if action.get("close") and state != "flat" and open_t is not None:
                reason = action.get("close_reason", "strategy_close")
                cum = self._close_trade(open_t, bar, i, c, reason, cum)
                last_closed_pnl = self.trades[-1]["net_pnl"]
                open_t = None; state = "flat"
                if i >= self.lookback:
                    eq_now = self.starting_cap + cum
                    ctx2 = self._build_ctx(i, bar, None, cum, eq_now, prev_indicators)
                    raw2 = self.strategy.on_bar(ctx2)
                    try: action = validate_signal(raw2, i)
                    except ValueError: action = {"signal": "HOLD"}
                    sig = action["signal"]

            # ══ STEP 5: Dynamic SL/TP updates ═══════════════════
            if state != "flat" and open_t is not None:
                if "update_sl" in action and action["update_sl"] is not None:
                    open_t["sl_level"] = float(action["update_sl"])
                if "update_tp" in action and action["update_tp"] is not None:
                    open_t["tp_level"] = float(action["update_tp"])

            # ══ STEP 6: BUY/SELL → set pending ═════════════════
            if state == "flat" and sig in ("BUY", "SELL"):
                direction = "long" if sig == "BUY" else "short"
                pending_signal = {
                    "direction": direction,
                    "sl": action.get("sl"), "tp": action.get("tp"),
                    "sl_mode": action.get("sl_mode"), "sl_pts": action.get("sl_pts"),
                    "sl_ma_key": action.get("sl_ma_key"), "sl_ma_val": action.get("sl_ma_val"),
                    "tp_mode": action.get("tp_mode"), "tp_r": action.get("tp_r"),
                    "engine_sl_ma_key": action.get("engine_sl_ma_key"),
                    "engine_tp_ma_key": action.get("engine_tp_ma_key"),
                }

            # ══ STEP 7: Equity / drawdown ═══════════════════════
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
                sizing_mode=self.sizing_mode,
                risk_pct=self.risk_pct,
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

## FILE: `index.html`

- Relative path: `index.html`
- Absolute path at snapshot time: `/home/hurairahengg/Documents/Jinni Zero/index.html`
- Size bytes: `29871`
- SHA256: `60bf7368c6f4f94990792a5dcadc9d0ebd3c4a0edd2006a0d3190f63c3d24e0a`
- Guessed MIME type: `text/html`
- Guessed encoding: `unknown`

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>JINNI TERMINAL</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;600;700;800&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="styles.css"/>
  <script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
</head>
<body>
  <div class="scanline"></div>

  <!-- ═══ HEADER ══════════════════════════════════════════════════════ -->
  <header class="header">
    <div class="header-left">
      <div class="logo">
        <span class="logo-bracket">[</span>
        <span class="logo-text">JINNI<span class="logo-accent">ZERO</span></span>
        <span class="logo-bracket">]</span>
      </div>
      <nav class="tab-nav">
        <button class="tab-btn active" data-tab="chart"><span class="tab-icon">▦</span>CHART</button>
        <button class="tab-btn" data-tab="backtest"><span class="tab-icon">◈</span>BACKTEST</button>
      </nav>
    </div>

    <div class="header-right" id="chartHeaderRight">
      <div class="ticker-info">
        <span class="ticker-symbol" id="tickerSymbol">NQ</span>
        <span class="ticker-price" id="tickerPrice">—</span>
        <span class="ticker-change" id="tickerChange">—</span>
      </div>

      <div class="interval-group">
        <button class="interval-btn active">2PT</button>
        <button class="interval-btn">4PT</button>
        <button class="interval-btn">6PT</button>
        <button class="interval-btn">8PT</button>
        <button class="interval-btn">10PT</button>
      </div>

      <div class="status-dot-wrap">
        <span class="status-dot"></span>
        <span class="status-label">LIVE</span>
      </div>
    </div>

    <div class="header-right" id="backtestHeaderRight" style="display:none;">
      <span class="bt-header-label">STRATEGY BACKTESTER</span>
      <div style="display:flex; gap:6px; align-items:center; margin-left:16px;">
        <span style="font-size:0.55rem; letter-spacing:0.12em; color:var(--text-dim); font-family:var(--mono); font-weight:700;">GRAPH STEP</span>
        <select id="bt_graphStep" class="bt-select bt-select-sm" style="width:60px; padding:4px 6px; font-size:0.65rem;">
          <option value="1" selected>1</option>
          <option value="2">2</option>
          <option value="4">4</option>
          <option value="8">8</option>
          <option value="16">16</option>
          <option value="32">32</option>
        </select>
      </div>
    </div>
  </header>

  <!-- ═══ CHART TAB ════════════════════════════════════════════════════ -->
  <main class="layout tab-panel active" id="tabChart">
    <aside class="sidebar">
      <div class="sidebar-block"><div class="sidebar-label">OPEN</div><div class="sidebar-value" id="statOpen">—</div></div>
      <div class="sidebar-block"><div class="sidebar-label">HIGH</div><div class="sidebar-value bull" id="statHigh">—</div></div>
      <div class="sidebar-block"><div class="sidebar-label">LOW</div><div class="sidebar-value bear" id="statLow">—</div></div>
      <div class="sidebar-block"><div class="sidebar-label">CLOSE</div><div class="sidebar-value" id="statClose">—</div></div>
      <div class="sidebar-divider"></div>
      <div class="sidebar-block"><div class="sidebar-label">VOLUME</div><div class="sidebar-value" id="statVolume">—</div></div>
      <div class="sidebar-block"><div class="sidebar-label">CHANGE</div><div class="sidebar-value" id="statChange">—</div></div>
      <div class="sidebar-divider"></div>
      <div class="sidebar-block"><div class="sidebar-label">CANDLES</div><div class="sidebar-value" id="statCandles">—</div></div>
    </aside>

    <section class="chart-area">
      <div class="chart-container" id="chartContainer"></div>
      <div class="chart-footer">
        <span class="chart-hint">↕ scroll to zoom · drag to pan · hover for OHLC</span>
        <span class="chart-powered">powered by Lightweight Charts™</span>
      </div>
    </section>
  </main>

  <!-- ═══ BACKTEST TAB ═════════════════════════════════════════════════ -->
  <main class="layout tab-panel" id="tabBacktest" style="display:none;">
    <div class="bt-root">
      <!-- ── LEFT CONFIG ──────────────────────────────────────────── -->
      <aside class="bt-config-panel">

        <!-- MODE -->
        <div class="bt-section">
          <div class="bt-section-title">MODE</div>
          <div class="bt-field">
            <label class="bt-label">Backtest Mode</label>
            <select class="bt-select" id="bt_mode">
              <option value="manual" selected>Manual Mode (Legacy)</option>
              <option value="strategy">Load Strategy Mode</option>
            </select>
          </div>
          <div class="bt-field">
            <label class="bt-label">Point Value ($/pt/lot)</label>
            <input class="bt-input" type="number" id="bt_pointValue"
                  value="1" min="0.01" max="10000" step="0.01"/>
            <span class="bt-hint" id="bt_pvHint">1 pt × 1 lot = $1.00</span>
          </div>
          <div class="bt-toggle-label">
            Manual Mode keeps your existing legacy controls. Load Strategy Mode auto-loads Python strategy schemas + engine settings.
          </div>
        </div>

        <!-- shared -->
        <div class="bt-section">
          <div class="bt-section-title">DATA SOURCE</div>

          <div class="bt-field">
            <label class="bt-label">Range Bar Size</label>
            <select class="bt-select" id="bt_range">
              <option value="2" selected>2 pt</option><option value="4">4 pt</option>
              <option value="6">6 pt</option><option value="8">8 pt</option>
              <option value="10">10 pt</option><option value="15">15 pt</option>
              <option value="20">20 pt</option><option value="25">25 pt</option>
              <option value="30">30 pt</option><option value="35">35 pt</option>
              <option value="40">40 pt</option><option value="45">45 pt</option>
              <option value="50">50 pt</option>
            </select>
          </div>

          <div class="bt-field">
            <label class="bt-label">Data Slicing</label>
            <select class="bt-select" id="bt_sliceMode">
              <option value="bar_count" selected>Bar Count</option>
              <option value="date_range">Date Range</option>
            </select>
          </div>

          <div id="bt_barRangeWrap">
            <div class="bt-field">
              <label class="bt-label">Backtest Range</label>
              <select class="bt-select" id="bt_barRange">
                <option value="500">Last 500 bars</option>
                <option value="1000" selected>Last 1000 bars</option>
                <option value="2500">Last 2500 bars</option>
                <option value="5000">Last 5000 bars</option>
                <option value="0">Full dataset</option>
              </select>
            </div>
          </div>

          <div id="bt_dateRangeWrap" style="display:none;">
            <div class="bt-field">
              <label class="bt-label">Start Date</label>
              <input type="datetime-local" class="bt-input" id="bt_startDate"/>
            </div>
            <div class="bt-field">
              <label class="bt-label">End Date</label>
              <input type="datetime-local" class="bt-input" id="bt_endDate"/>
            </div>
          </div>

          <div class="bt-field">
            <label class="bt-label">Starting Capital ($)</label>
            <input class="bt-input" type="number" id="bt_startingCapital" value="10000" min="0" step="500"/>
          </div>
          
          <div class="bt-field">
            <label class="bt-label">Position Sizing</label>
            <select class="bt-select" id="bt_sizingMode">
              <option value="fixed" selected>Fixed Lot</option>
              <option value="risk_pct">Risk %</option>
            </select>
          </div>

          <div id="bt_fixedLotWrap">
            <div class="bt-field">
              <label class="bt-label">Lot Size</label>
              <input class="bt-input" type="number" id="bt_lotSize" value="1.0" min="0.01" step="0.01"/>
              <div class="bt-toggle-label" id="bt_lotHint">1 pt = $1.00</div>
            </div>
          </div>

          <div id="bt_riskPctWrap" style="display:none;">
            <div class="bt-field">
              <label class="bt-label">Risk Per Trade (%)</label>
              <input class="bt-input" type="number" id="bt_riskPct" value="1.0" min="0.01" max="100" step="0.1"/>
              <div class="bt-toggle-label" id="bt_riskHint">
                $1,000 balance × 1% = $10 risk per trade
              </div>
            </div>
          </div>
        </div>

        <!-- MANUAL MODE ONLY -->
        <div class="bt-section bt-manual-only">
          <div class="bt-section-title">MOVING AVERAGES</div>
          <div class="bt-ma-row" id="bt_ma_0">
            <select class="bt-select bt-select-sm" data-ma-type>
              <option value="EMA">EMA</option><option value="HMA" selected>HMA</option>
              <option value="SMA">SMA</option><option value="WMA">WMA</option>
            </select>
            <input class="bt-input bt-input-sm" type="number" data-ma-period value="21" min="2" max="500"/>
            <button class="bt-icon-btn bt-remove-ma" title="Remove">✕</button>
          </div>
          <button class="bt-add-btn" id="bt_addMa">+ ADD MA</button>
        </div>

        <div class="bt-section bt-manual-only">
          <div class="bt-section-title">ENTRY LOGIC</div>
          <div class="bt-field">
            <label class="bt-label">Entry Condition</label>
            <select class="bt-select" id="bt_entry">
              <option value="above_all_mas">Price above ALL MAs (2-bar confirm)</option>
              <option value="ma_cross">MA Cross (fast × slow)</option>
              <option value="trend_filter">Trend Filter (close vs longest MA)</option>
            </select>
          </div>

          <div class="bt-field">
            <label class="bt-label">Require Candle Direction Confirmation</label>
            <div class="bt-toggle-row">
              <label class="bt-toggle"><input type="checkbox" id="bt_candleConfirm" checked/><span class="bt-toggle-slider"></span></label>
              <span class="bt-toggle-label">Entry candle must match trade direction</span>
            </div>
          </div>
        </div>

        <div class="bt-section bt-manual-only">
          <div class="bt-section-title">TRADE GATING</div>
          <div class="bt-field">
            <label class="bt-label">One Trade Per MA</label>
            <div class="bt-toggle-row">
              <label class="bt-toggle"><input type="checkbox" id="bt_gatingEnabled"/><span class="bt-toggle-slider"></span></label>
              <span class="bt-toggle-label">Lock direction until price crosses gating MA</span>
            </div>
          </div>

          <div id="bt_gating_wrap" style="display:none;">
            <div class="bt-field">
              <label class="bt-label">Gating MA Type</label>
              <select class="bt-select" id="bt_gating_ma_type">
                <option value="HMA" selected>HMA</option>
                <option value="EMA">EMA</option>
                <option value="SMA">SMA</option>
              </select>
            </div>

            <div class="bt-field">
              <label class="bt-label">Gating MA Length</label>
              <input class="bt-input" type="number" id="bt_gating_ma_length" value="21" min="2" max="500"/>
            </div>
          </div>
        </div>

        <div class="bt-section bt-manual-only">
          <div class="bt-section-title">STOP LOSS</div>
          <div class="bt-field">
            <label class="bt-label">SL Mode</label>
            <div class="bt-radio-group">
              <label class="bt-radio"><input type="radio" name="sl_mode" value="fixed" checked/><span>Fixed pts</span></label>
              <label class="bt-radio"><input type="radio" name="sl_mode" value="ma_cross"/><span>MA Cross</span></label>
              <label class="bt-radio"><input type="radio" name="sl_mode" value="ma_snapshot"/><span>MA Snapshot</span></label>
            </div>
          </div>

          <div class="bt-field" id="bt_sl_fixed_wrap">
            <label class="bt-label">SL Size (points)</label>
            <input class="bt-input" type="number" id="bt_sl_fixed" value="8" min="0.25" step="0.25"/>
          </div>

          <div id="bt_sl_ma_wrap" style="display:none;">
            <div class="bt-field">
              <label class="bt-label">SL MA Type</label>
              <select class="bt-select" id="bt_sl_ma_type">
                <option value="EMA" selected>EMA</option>
                <option value="HMA">HMA</option>
                <option value="SMA">SMA</option>
              </select>
            </div>

            <div class="bt-field">
              <label class="bt-label">SL MA Length</label>
              <input class="bt-input" type="number" id="bt_sl_ma_length" value="50" min="2" max="500"/>
            </div>
          </div>

          <div class="bt-field">
            <label class="bt-label">Ambiguous Bar (SL+TP hit)</label>
            <select class="bt-select" id="bt_ambiguousMode">
              <option value="conservative" selected>Conservative (SL first)</option>
              <option value="optimistic">Optimistic (TP first)</option>
              <option value="nearest_to_open">Nearest to Open</option>
            </select>
          </div>
        </div>

        <div class="bt-section bt-manual-only">
          <div class="bt-section-title">TAKE PROFIT</div>
          <div class="bt-field">
            <label class="bt-label">TP Mode</label>
            <div class="bt-radio-group">
              <label class="bt-radio"><input type="radio" name="tp_mode" value="r_multiple" checked/><span>R Multiple</span></label>
              <label class="bt-radio"><input type="radio" name="tp_mode" value="ma_cross"/><span>MA Cross</span></label>
            </div>
          </div>

          <div class="bt-field" id="bt_tp_r_wrap">
            <label class="bt-label">R Multiple</label>
            <div class="bt-r-group">
              <button class="bt-r-btn active" data-r="2">2R</button>
              <button class="bt-r-btn" data-r="3">3R</button>
              <button class="bt-r-btn" data-r="4">4R</button>
              <button class="bt-r-btn" data-r="5">5R</button>
            </div>
          </div>

          <div id="bt_tp_ma_wrap" style="display:none;">
            <div class="bt-field">
              <label class="bt-label">TP MA Type</label>
              <select class="bt-select" id="bt_tp_ma_type">
                <option value="EMA" selected>EMA</option>
                <option value="HMA">HMA</option>
                <option value="SMA">SMA</option>
              </select>
            </div>

            <div class="bt-field">
              <label class="bt-label">TP MA Length</label>
              <input class="bt-input" type="number" id="bt_tp_ma_length" value="9" min="2" max="500"/>
            </div>
          </div>
        </div>

        <!-- shared -->
        <div class="bt-section">
          <div class="bt-section-title">COMMISSION</div>
          <div class="bt-field">
            <label class="bt-label">Commission Type</label>
            <div class="bt-radio-group">
              <label class="bt-radio"><input type="radio" name="comm_type" value="flat" checked/><span>Flat/trade</span></label>
              <label class="bt-radio"><input type="radio" name="comm_type" value="per_contract"/><span>Per contract</span></label>
              <label class="bt-radio"><input type="radio" name="comm_type" value="per_side"/><span>Per side</span></label>
            </div>
          </div>

          <div class="bt-field">
            <label class="bt-label">Amount ($)</label>
            <input class="bt-input" type="number" id="bt_commission" value="4.00" min="0" step="0.5"/>
          </div>
        </div>
        <!-- SPREAD SIMULATION -->
        <div class="bt-section">
          <div class="bt-section-title">SPREAD SIMULATION</div>
          <div class="bt-field">
            <label class="bt-label">Random Spread</label>
            <div class="bt-toggle-row">
              <label class="bt-toggle"><input type="checkbox" id="bt_spreadEnabled"/><span class="bt-toggle-slider"></span></label>
              <span class="bt-toggle-label">Apply random spread per trade (realistic slippage)</span>
            </div>
          </div>
          <!-- ═══ CURRENCY DISPLAY (display-only) ═══ -->
        <div class="bt-section" id="cc_section_manual">
          <div class="bt-section-title">CURRENCY DISPLAY</div>

          <div class="bt-field" style="flex-direction:row;align-items:center;gap:8px;">
            <input type="checkbox" id="cc_enabled"
                   style="accent-color:var(--accent,#00e5ff);"/>
            <label class="bt-label" for="cc_enabled"
                   style="margin:0;cursor:pointer;">
              Enable Currency Conversion
            </label>
          </div>

          <div id="cc_panel" style="display:none;flex-direction:column;gap:8px;">
            <div class="bt-field">
              <label class="bt-label">Symbol</label>
              <select id="cc_symbol" class="bt-select">
                <option value="$" selected>$</option>
                <option value="¥">¥</option>
                <option value="€">€</option>
                <option value="£">£</option>
                <option value="₹">₹</option>
                <option value="৳">৳</option>
              </select>
            </div>

            <div class="bt-field">
              <label class="bt-label">Multiplier (1 USD = ?)</label>
              <input type="number" id="cc_multiplier" class="bt-input"
                     value="1" min="0.0001" step="0.01" />
            </div>

            <div class="bt-toggle-label"
                 style="font-size:0.54rem;color:#586f7f;">
              Display-only — does NOT affect backtest calculations.
            </div>
          </div>
        </div>

          <div id="bt_spreadWrap" style="display:none;">
            <div class="bt-field">
              <label class="bt-label">Min Spread ($)</label>
              <input class="bt-input" type="number" id="bt_spreadMin" value="0.10" min="0" step="0.01"/>
            </div>

            <div class="bt-field">
              <label class="bt-label">Max Spread ($)</label>
              <input class="bt-input" type="number" id="bt_spreadMax" value="0.50" min="0" step="0.01"/>
            </div>

            <div class="bt-field">
              <label class="bt-label">Seed (0 = random)</label>
              <input class="bt-input" type="number" id="bt_spreadSeed" value="0" min="0" step="1"/>
            </div>
          </div>
        </div>
        <!-- STRATEGY MODE ONLY -->
        <div id="bt_strategyPanel" style="display:none;">
          <div class="bt-section">
            <div class="bt-section-title">LOAD STRATEGY</div>

            <div class="bt-field">
              <label class="bt-label">Available Strategy</label>
              <select class="bt-select" id="bt_strategySelect"></select>
            </div>

            <div class="bt-field">
              <label class="bt-label">Description</label>
              <div class="bt-toggle-label" id="bt_strategyDescription">Loading strategies…</div>
            </div>
          </div>

          <div class="bt-section">
            <div class="bt-section-title">STRATEGY PARAMETERS</div>
            <div id="bt_strategyParams"></div>
          </div>

          <div class="bt-section">
            <div class="bt-section-title">SIMULATION</div>

            <div class="bt-field">
              <label class="bt-label">Monte Carlo Runs</label>
              <input class="bt-input" type="number" id="bt_mcRuns" value="1000" min="0" step="100"/>
            </div>
          </div>

          <div class="bt-run-wrap">
            <button class="bt-run-btn" id="bt_strategyRunBtn">
              <span class="bt-run-icon">▶</span> RUN BACKTEST
            </button>
          </div>
        </div>

        <!-- MANUAL RUN -->
        <div class="bt-run-wrap bt-manual-only" id="bt_manualRunWrap">
          <button class="bt-run-btn" id="bt_runBtn">
            <span class="bt-run-icon">▶</span> START BACKTEST
          </button>
        </div>
      </aside>

      <!-- ── RIGHT RESULTS ─────────────────────────────────────────── -->
      <div class="bt-results-panel" id="bt_resultsPanel">
        <div class="bt-progress-wrap" id="bt_progressWrap" style="display:none;">
          <div class="bt-progress-header">
            <span class="bt-progress-label" id="bt_progressLabel">Initialising…</span>
            <span class="bt-progress-pct" id="bt_progressPct">0%</span>
          </div>
          <div class="bt-progress-track"><div class="bt-progress-bar" id="bt_progressBar" style="width:0%"></div></div>
          <div class="bt-progress-steps">
            <span class="bt-step" id="step_load">LOAD DATA</span><span class="bt-step-arrow">→</span>
            <span class="bt-step" id="step_run">RUN LOGIC</span><span class="bt-step-arrow">→</span>
            <span class="bt-step" id="step_stats">COMPUTE STATS</span><span class="bt-step-arrow">→</span>
            <span class="bt-step" id="step_charts">BUILD CHARTS</span><span class="bt-step-arrow">→</span>
            <span class="bt-step" id="step_done">DONE</span>
          </div>
          <div class="bt-live-stats" id="bt_liveStats" style="display:none;">
            <div class="bt-live-stat"><span class="bt-live-label">EQUITY</span><span class="bt-live-value" id="bt_liveEquity">—</span></div>
            <div class="bt-live-stat"><span class="bt-live-label">DRAWDOWN</span><span class="bt-live-value" id="bt_liveDD">—</span></div>
            <div class="bt-live-stat"><span class="bt-live-label">OPEN TRADE</span><span class="bt-live-value" id="bt_liveOpen">—</span></div>
            <div class="bt-live-stat"><span class="bt-live-label">LAST P&L</span><span class="bt-live-value" id="bt_lastPnl">—</span></div>
          </div>
        </div>

        <div class="bt-empty" id="bt_empty">
          <div class="bt-empty-icon">◈</div>
          <div class="bt-empty-title">No results yet</div>
          <div class="bt-empty-sub">Configure your strategy and press START BACKTEST</div>
        </div>

        <div class="bt-dashboard" id="bt_dashboard" style="display:none;">
          <div class="bt-dash-header">
            <div class="bt-dash-strategy" id="bd_strategy">—</div>
            <div class="bt-dash-meta" id="bd_meta">—</div>
          </div>

          <div class="bt-dash-section"><div class="bt-dash-section-title">CORE STATISTICS</div><div class="bt-kpi-grid" id="bd_coreKpis"></div></div>
          <div class="bt-dash-section"><div class="bt-dash-section-title">RISK METRICS</div><div class="bt-kpi-grid" id="bd_riskKpis"></div></div>
          <div class="bt-dash-section"><div class="bt-dash-section-title">PERFORMANCE RATIOS</div><div class="bt-kpi-grid" id="bd_ratioKpis"></div></div>

          <!-- ── NEW PRO STAT SECTIONS ─────────────────────────── -->
          <div class="bt-dash-section"><div class="bt-dash-section-title">TRADE ANALYSIS</div><div class="bt-kpi-grid" id="bd_tradeKpis"></div></div>
          <div class="bt-dash-section"><div class="bt-dash-section-title">TIME &amp; EXPOSURE</div><div class="bt-kpi-grid" id="bd_timeExpKpis"></div></div>
          <div class="bt-dash-section"><div class="bt-dash-section-title">PERIOD PERFORMANCE</div><div class="bt-kpi-grid" id="bd_periodKpis"></div></div>
          <div class="bt-dash-section"><div class="bt-dash-section-title">MAE / MFE</div><div class="bt-kpi-grid" id="bd_maeMfeKpis"></div></div>

          <div class="bt-dash-section"><div class="bt-dash-section-title">EQUITY CURVE</div><div class="bt-chart-card"><canvas id="cv_equity" class="bt-canvas"></canvas></div></div>
          <div class="bt-dash-section"><div class="bt-dash-section-title">DRAWDOWN CURVE</div><div class="bt-chart-card"><canvas id="cv_drawdown" class="bt-canvas"></canvas></div></div>

          <div class="bt-dash-section">
            <div class="bt-dash-section-title">ROLLING METRICS <span class="bt-dash-sub">(20-trade window)</span></div>
            <div class="bt-chart-2col">
              <div class="bt-chart-card"><div class="bt-chart-label">WIN RATE %</div><canvas id="cv_rollWr" class="bt-canvas bt-canvas-sm"></canvas></div>
              <div class="bt-chart-card"><div class="bt-chart-label">EXPECTANCY $</div><canvas id="cv_rollExp" class="bt-canvas bt-canvas-sm"></canvas></div>
              <div class="bt-chart-card"><div class="bt-chart-label">PROFIT FACTOR</div><canvas id="cv_rollPf" class="bt-canvas bt-canvas-sm"></canvas></div>
              <div class="bt-chart-card"><div class="bt-chart-label">SHARPE RATIO</div><canvas id="cv_rollSharpe" class="bt-canvas bt-canvas-sm"></canvas></div>
            </div>
          </div>

          <div class="bt-dash-section">
            <div class="bt-dash-section-title">DISTRIBUTIONS</div>
            <div class="bt-chart-2col">
              <div class="bt-chart-card"><div class="bt-chart-label">R-MULTIPLE HISTOGRAM</div><canvas id="cv_rHist" class="bt-canvas bt-canvas-sm"></canvas></div>
              <div class="bt-chart-card"><div class="bt-chart-label">TRADE DURATION (BARS)</div><canvas id="cv_durHist" class="bt-canvas bt-canvas-sm"></canvas></div>
            </div>
          </div>

          <div class="bt-dash-section">
            <div class="bt-dash-section-title">SCATTER ANALYSIS</div>
            <div class="bt-chart-2col">
              <div class="bt-chart-card"><div class="bt-chart-label">MAE vs MFE</div><canvas id="cv_maemfe" class="bt-canvas bt-canvas-sm"></canvas></div>
              <div class="bt-chart-card"><div class="bt-chart-label">TRADE # vs R-MULTIPLE</div><canvas id="cv_retScatter" class="bt-canvas bt-canvas-sm"></canvas></div>
            </div>
          </div>

          <div class="bt-dash-section">
            <div class="bt-dash-section-title">TIME BREAKDOWN</div>
            <div class="bt-chart-3col">
              <div class="bt-chart-card"><div class="bt-chart-label">BY HOUR OF DAY</div><canvas id="cv_hour" class="bt-canvas bt-canvas-sm"></canvas></div>
              <div class="bt-chart-card"><div class="bt-chart-label">BY DAY OF WEEK</div><canvas id="cv_dow" class="bt-canvas bt-canvas-sm"></canvas></div>
              <div class="bt-chart-card"><div class="bt-chart-label">BY MONTH</div><canvas id="cv_month" class="bt-canvas bt-canvas-sm"></canvas></div>
            </div>
          </div>

          <div class="bt-dash-section"><div class="bt-dash-section-title">REGIME BREAKDOWN</div><div class="bt-kpi-grid" id="bd_regimeKpis"></div></div>

          <div class="bt-dash-section">
            <div class="bt-dash-section-title">MONTE CARLO <span class="bt-dash-sub">(configurable runs)</span></div>
            <div class="bt-kpi-grid" id="bd_mcKpis"></div>

            <div class="bt-chart-2col" style="margin-top:12px;">
              <div class="bt-chart-card"><div class="bt-chart-label">SIMULATED EQUITY PATHS</div><canvas id="cv_mcPaths" class="bt-canvas bt-canvas-sm"></canvas></div>
              <div class="bt-chart-card"><div class="bt-chart-label">FINAL EQUITY DISTRIBUTION</div><canvas id="cv_mcFinal" class="bt-canvas bt-canvas-sm"></canvas></div>
            </div>

            <div class="bt-chart-2col" style="margin-top:10px;">
              <div class="bt-chart-card"><div class="bt-chart-label">MAX DRAWDOWN DISTRIBUTION</div><canvas id="cv_mcDd" class="bt-canvas bt-canvas-sm"></canvas></div>
              <div class="bt-chart-card" style="padding:16px 18px;">
                <div class="bt-chart-label" style="margin-bottom:12px;">PROBABILITY TABLE</div>
                <div id="bd_mcProb"></div>
              </div>
            </div>
          </div>

          <div class="bt-dash-section"><div class="bt-dash-section-title">COMMISSION SUMMARY</div><div class="bt-kpi-grid" id="bd_commKpis"></div></div>

          <div class="bt-dash-section" style="margin-bottom:24px;">
            <div class="bt-dash-section-title">TRADE LOG</div>
            <div class="bt-chart-card" style="padding:0; overflow:hidden;">
              <div class="bt-trade-log" id="bt_tradeLog"></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </main>

  <script src="js/chart.js"></script>
  <script src="js/currency.js"></script>
  <script src="js/backtest.js"></script>
  <script src="js/strategy_loader.js"></script>
</body>
</html>
```

---

## FILE: `js/strategy_loader.js`

- Relative path: `js/strategy_loader.js`
- Absolute path at snapshot time: `/home/hurairahengg/Documents/Jinni Zero/js/strategy_loader.js`
- Size bytes: `16646`
- SHA256: `cb1e23336d7f26920f9a27e930c85380c0c5b7a0e9943d3582de1a4923c29085`
- Guessed MIME type: `text/javascript`
- Guessed encoding: `unknown`

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
      range: parseInt(($('bt_range') || {}).value || '10', 10),
      bar_range: parseInt(($('bt_barRange') || {}).value || '1000', 10),
      starting_capital: parseFloat(($('bt_startingCapital') || {}).value || '10000'),
      lot_size: parseFloat(($('bt_lotSize') || {}).value || '1.0'),
      point_value: parseFloat((document.getElementById('bt_pointValue') || {}).value || '1') || 1.0,
      sizing_mode: ($('bt_sizingMode') || {}).value || 'fixed',
      risk_pct: parseFloat(($('bt_riskPct') || {}).value || '1.0') || 1.0,
      commission: {
        type: (document.querySelector('input[name="comm_type"]:checked') || {}).value || 'flat',
        amount: parseFloat(($('bt_commission') || {}).value || '0'),
      },
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

## FILE: `styles.css`

- Relative path: `styles.css`
- Absolute path at snapshot time: `/home/hurairahengg/Documents/Jinni Zero/styles.css`
- Size bytes: `25189`
- SHA256: `f88d4428c684735daca88314b59048d58d7003f4fc790e1fb057379ec8c1373c`
- Guessed MIME type: `text/css`
- Guessed encoding: `unknown`

```css
/* ─── RESET & ROOT ─────────────────────────────────────────────────── */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg:        #080b0f;
  --bg2:       #0d1117;
  --bg3:       #111820;
  --border:    #1e2a38;
  --border2:   #243040;
  --text:      #c8d8e8;
  --text-dim:  #4a6070;
  --text-mute: #2a3a4a;
  --accent:    #00e5ff;
  --accent2:   #0095a8;
  --bull:      #00e676;
  --bull2:     #00a352;
  --bear:      #ff3d5a;
  --bear2:     #b02040;
  --mono:      'Space Mono', monospace;
  --syne:      'Syne', sans-serif;
}

html, body {
  height: 100%;
  width: 100%;
  overflow: hidden;
  background: var(--bg);
  color: var(--text);
  font-family: var(--mono);
}

/* ─── SCANLINE OVERLAY ──────────────────────────────────────────────── */
.scanline {
  pointer-events: none;
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: repeating-linear-gradient(
    to bottom,
    transparent 0px,
    transparent 2px,
    rgba(0,0,0,0.08) 2px,
    rgba(0,0,0,0.08) 4px
  );
}

/* ─── HEADER ────────────────────────────────────────────────────────── */
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 56px;
  padding: 0 20px;
  border-bottom: 1px solid var(--border);
  background: var(--bg2);
  flex-shrink: 0;
  position: relative;
  z-index: 10;
}
.header::after {
  content: '';
  position: absolute;
  bottom: 0; left: 0; right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--accent2), transparent);
  opacity: 0.5;
}
.header-left { display: flex; align-items: center; gap: 24px; }
.header-right { display: flex; align-items: center; gap: 20px; }

/* Logo */
.logo {
  font-family: var(--syne);
  font-weight: 800;
  font-size: 1.1rem;
  letter-spacing: 0.05em;
  user-select: none;
}
.logo-bracket { color: var(--accent2); }
.logo-text { color: var(--text); }
.logo-accent { color: var(--accent); }

/* ─── TAB NAV ────────────────────────────────────────────────────────── */
.tab-nav {
  display: flex;
  gap: 2px;
}
.tab-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  background: transparent;
  border: 1px solid transparent;
  color: var(--text-dim);
  font-family: var(--mono);
  font-size: 0.67rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  padding: 5px 13px;
  border-radius: 3px;
  cursor: pointer;
  transition: all 0.15s;
}
.tab-btn:hover {
  color: var(--text);
  border-color: var(--border);
}
.tab-btn.active {
  color: var(--accent);
  border-color: var(--accent);
  background: rgba(0,229,255,0.06);
  box-shadow: 0 0 8px rgba(0,229,255,0.1);
}
.tab-icon {
  font-size: 0.75rem;
  opacity: 0.7;
}

/* Backtest header label */
.bt-header-label {
  font-family: var(--syne);
  font-size: 0.75rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  color: var(--text-dim);
}

/* Ticker */
.ticker-info { display: flex; align-items: baseline; gap: 12px; }
.ticker-symbol {
  font-family: var(--syne);
  font-size: 0.85rem;
  font-weight: 700;
  color: var(--text-dim);
  letter-spacing: 0.1em;
}
.ticker-price {
  font-size: 1.1rem;
  font-weight: 700;
  color: var(--text);
  letter-spacing: 0.04em;
}
.ticker-change {
  font-size: 0.75rem;
  padding: 2px 7px;
  border-radius: 3px;
  font-weight: 700;
  letter-spacing: 0.05em;
  transition: all 0.3s;
}
.ticker-change.bull { background: rgba(0,230,118,0.12); color: var(--bull); }
.ticker-change.bear { background: rgba(255,61,90,0.12); color: var(--bear); }

/* Interval buttons */
.interval-group { display: flex; gap: 4px; }
.interval-btn {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-dim);
  font-family: var(--mono);
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  padding: 4px 9px;
  border-radius: 3px;
  cursor: pointer;
  transition: all 0.15s;
}
.interval-btn:hover { border-color: var(--accent2); color: var(--accent); }
.interval-btn.active {
  border-color: var(--accent);
  color: var(--accent);
  background: rgba(0,229,255,0.07);
  box-shadow: 0 0 8px rgba(0,229,255,0.15);
}

/* Status dot */
.status-dot-wrap { display: flex; align-items: center; gap: 6px; }
.status-dot {
  width: 7px; height: 7px;
  border-radius: 50%;
  background: var(--bull);
  box-shadow: 0 0 6px var(--bull);
  animation: pulse 2s ease-in-out infinite;
}
.status-label {
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.12em;
  color: var(--bull);
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.4; }
}

/* ─── LAYOUT (chart tab) ────────────────────────────────────────────── */
.layout {
  display: flex;
  height: calc(100vh - 56px);
  overflow: hidden;
}
.tab-panel { display: none; }
.tab-panel.active { display: flex; }

/* ─── SIDEBAR ───────────────────────────────────────────────────────── */
.sidebar {
  width: 130px;
  flex-shrink: 0;
  background: var(--bg2);
  border-right: 1px solid var(--border);
  padding: 20px 0;
  display: flex;
  flex-direction: column;
  gap: 0;
  overflow-y: auto;
}
.sidebar-block {
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
  transition: background 0.15s;
}
.sidebar-block:hover { background: var(--bg3); }
.sidebar-label {
  font-size: 0.58rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  color: var(--text-dim);
  margin-bottom: 5px;
}
.sidebar-value {
  font-size: 0.8rem;
  font-weight: 700;
  color: var(--text);
  letter-spacing: 0.03em;
  transition: color 0.3s;
}
.sidebar-value.bull { color: var(--bull); }
.sidebar-value.bear { color: var(--bear); }
.sidebar-divider { height: 1px; background: var(--border2); margin: 6px 0; }

/* ─── CHART AREA ────────────────────────────────────────────────────── */
.chart-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--bg);
  min-width: 0;
  background-image:
    linear-gradient(var(--border) 1px, transparent 1px),
    linear-gradient(90deg, var(--border) 1px, transparent 1px);
  background-size: 48px 48px;
  background-position: -1px -1px;
}
.chart-container { flex: 1; position: relative; }
.chart-container > * { background: transparent !important; }
.chart-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 7px 16px;
  border-top: 1px solid var(--border);
  background: var(--bg2);
  flex-shrink: 0;
}
.chart-hint, .chart-powered {
  font-size: 0.6rem;
  color: var(--text-mute);
  letter-spacing: 0.08em;
}
.chart-powered { color: var(--accent2); opacity: 0.6; }

/* ─── SCROLLBAR ─────────────────────────────────────────────────────── */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 2px; }

/* ─── INTERACTIVE CANVAS CURSORS ────────────────────────────────────── */
.bt-canvas-interactive { cursor: grab; }
.bt-canvas-interactive:active { cursor: grabbing; }

/* ─── INTRO ANIMATION ───────────────────────────────────────────────── */
.header, .sidebar, .chart-area { animation: fadeUp 0.5s ease both; }
.sidebar    { animation-delay: 0.1s; }
.chart-area { animation-delay: 0.2s; }
@keyframes fadeUp {
  from { opacity: 0; transform: translateY(8px); }
  to   { opacity: 1; transform: translateY(0); }
}


/* ═══════════════════════════════════════════════════════════════════════
   BACKTEST TAB STYLES
   ═══════════════════════════════════════════════════════════════════════ */

#tabBacktest { overflow: hidden; }
#tabBacktest.active { display: flex; }

/* ─── Root layout ────────────────────────────────────────────────────── */
.bt-root {
  display: flex;
  width: 100%;
  height: 100%;
  overflow: hidden;
}

/* ─── Config panel (left) ────────────────────────────────────────────── */
.bt-config-panel {
  width: 300px;
  flex-shrink: 0;
  background: var(--bg2);
  border-right: 1px solid var(--border);
  overflow-y: auto;
  padding-bottom: 24px;
  display: flex;
  flex-direction: column;
}

/* Section */
.bt-section {
  padding: 16px 18px 14px;
  border-bottom: 1px solid var(--border);
}
.bt-section-title {
  font-size: 0.56rem;
  font-weight: 700;
  letter-spacing: 0.18em;
  color: var(--text-mute);
  margin-bottom: 14px;
}

/* Fields */
.bt-field { margin-bottom: 12px; }
.bt-field:last-child { margin-bottom: 0; }
.bt-label {
  display: block;
  font-size: 0.6rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: var(--text-dim);
  margin-bottom: 6px;
}

/* Inputs */
.bt-select, .bt-input {
  width: 100%;
  background: var(--bg3);
  border: 1px solid var(--border);
  color: var(--text);
  font-family: var(--mono);
  font-size: 0.72rem;
  font-weight: 700;
  padding: 6px 10px;
  border-radius: 3px;
  outline: none;
  transition: border-color 0.15s;
  appearance: none;
  -webkit-appearance: none;
}
.bt-select:focus, .bt-input:focus { border-color: var(--accent2); }
.bt-select { cursor: pointer; background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%234a6070'/%3E%3C/svg%3E"); background-repeat: no-repeat; background-position: right 10px center; padding-right: 28px; }

.bt-input-sm, .bt-select-sm {
  width: auto;
  flex: 1;
}

/* MA rows */
.bt-ma-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
}
.bt-icon-btn {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-mute);
  font-size: 0.65rem;
  width: 26px;
  height: 26px;
  border-radius: 2px;
  cursor: pointer;
  flex-shrink: 0;
  transition: all 0.15s;
  display: flex;
  align-items: center;
  justify-content: center;
}
.bt-icon-btn:hover { border-color: var(--bear2); color: var(--bear); }
.bt-add-btn {
  display: flex;
  align-items: center;
  gap: 5px;
  background: transparent;
  border: 1px dashed var(--border2);
  color: var(--text-dim);
  font-family: var(--mono);
  font-size: 0.62rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  padding: 5px 10px;
  border-radius: 3px;
  cursor: pointer;
  width: 100%;
  justify-content: center;
  transition: all 0.15s;
  margin-top: 4px;
}
.bt-add-btn:hover { border-color: var(--accent2); color: var(--accent); }

/* Radio group */
.bt-radio-group { display: flex; flex-direction: column; gap: 6px; }
.bt-radio {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.67rem;
  color: var(--text-dim);
  cursor: pointer;
  font-weight: 700;
  letter-spacing: 0.04em;
}
.bt-radio input[type=radio] { accent-color: var(--accent); cursor: pointer; }
.bt-radio:has(input:checked) { color: var(--text); }

/* Toggle */
.bt-toggle-row { display: flex; align-items: center; gap: 10px; }
.bt-toggle { position: relative; display: inline-block; width: 34px; height: 18px; flex-shrink: 0; }
.bt-toggle input { opacity: 0; width: 0; height: 0; }
.bt-toggle-slider {
  position: absolute; inset: 0;
  background: var(--bg3);
  border: 1px solid var(--border2);
  border-radius: 18px;
  transition: 0.2s;
  cursor: pointer;
}
.bt-toggle-slider::before {
  content: '';
  position: absolute;
  left: 2px; top: 2px;
  width: 12px; height: 12px;
  background: var(--text-dim);
  border-radius: 50%;
  transition: 0.2s;
}
.bt-toggle input:checked + .bt-toggle-slider { background: rgba(0,229,255,0.12); border-color: var(--accent2); }
.bt-toggle input:checked + .bt-toggle-slider::before { transform: translateX(16px); background: var(--accent); }
.bt-toggle-label { font-size: 0.62rem; color: var(--text-dim); font-weight: 700; letter-spacing: 0.04em; line-height: 1.4; }

/* R multiple buttons */
.bt-r-group { display: flex; gap: 5px; }
.bt-r-btn {
  flex: 1;
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-dim);
  font-family: var(--mono);
  font-size: 0.68rem;
  font-weight: 700;
  padding: 5px 4px;
  border-radius: 3px;
  cursor: pointer;
  transition: all 0.15s;
}
.bt-r-btn:hover { border-color: var(--accent2); color: var(--accent); }
.bt-r-btn.active {
  border-color: var(--accent);
  color: var(--accent);
  background: rgba(0,229,255,0.07);
}

/* Run button */
.bt-run-wrap {
  padding: 18px 18px 0;
  margin-top: auto;
}
.bt-run-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  width: 100%;
  padding: 12px;
  background: rgba(0,229,255,0.06);
  border: 1px solid var(--accent2);
  color: var(--accent);
  font-family: var(--syne);
  font-size: 0.85rem;
  font-weight: 800;
  letter-spacing: 0.12em;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s;
}
.bt-run-btn:hover {
  background: rgba(0,229,255,0.12);
  border-color: var(--accent);
  box-shadow: 0 0 20px rgba(0,229,255,0.15);
}
.bt-run-btn:active { transform: scale(0.98); }
.bt-run-btn.running {
  border-color: var(--bull2);
  color: var(--bull);
  background: rgba(0,230,118,0.06);
  pointer-events: none;
}
.bt-run-icon { font-size: 1rem; }

/* ─── Results panel (right) ──────────────────────────────────────────── */
.bt-results-panel {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: var(--bg);
  background-image:
    linear-gradient(var(--border) 1px, transparent 1px),
    linear-gradient(90deg, var(--border) 1px, transparent 1px);
  background-size: 48px 48px;
  background-position: -1px -1px;
}

/* Progress bar */
.bt-progress-wrap {
  padding: 20px 24px 16px;
  border-bottom: 1px solid var(--border);
  background: var(--bg2);
  flex-shrink: 0;
}
.bt-progress-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 10px;
}
.bt-progress-label {
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: var(--text-dim);
}
.bt-progress-pct {
  font-size: 0.65rem;
  font-weight: 700;
  color: var(--accent);
}
.bt-progress-track {
  height: 3px;
  background: var(--border);
  border-radius: 2px;
  overflow: hidden;
  margin-bottom: 12px;
}
.bt-progress-bar {
  height: 100%;
  background: linear-gradient(90deg, var(--accent2), var(--accent));
  border-radius: 2px;
  transition: width 0.4s ease;
  box-shadow: 0 0 8px rgba(0,229,255,0.4);
}
.bt-progress-steps {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.bt-step {
  font-size: 0.55rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: var(--text-mute);
  transition: color 0.3s;
}
.bt-step.active { color: var(--accent); }
.bt-step.done   { color: var(--bull); }
.bt-step-arrow  { color: var(--text-mute); font-size: 0.6rem; }

/* Empty state */
.bt-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  opacity: 0.35;
  pointer-events: none;
}
.bt-empty-icon {
  font-size: 2.5rem;
  color: var(--text-dim);
}
.bt-empty-title {
  font-family: var(--syne);
  font-size: 0.9rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: var(--text-dim);
}
.bt-empty-sub {
  font-size: 0.62rem;
  color: var(--text-mute);
  letter-spacing: 0.06em;
}

/* Results content */
.bt-results { padding: 20px 24px; display: flex; flex-direction: column; gap: 20px; }

.bt-results-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--border);
}
.bt-results-strategy {
  font-family: var(--syne);
  font-size: 0.9rem;
  font-weight: 800;
  color: var(--text);
  letter-spacing: 0.08em;
}
.bt-results-meta {
  font-size: 0.6rem;
  color: var(--text-dim);
  letter-spacing: 0.08em;
}

/* KPI grid */
.bt-kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
  gap: 8px;
}
.bt-kpi {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 3px;
  padding: 11px 13px;
  transition: border-color 0.15s;
}
.bt-kpi:hover { border-color: var(--border2); }
.bt-kpi-label {
  font-size: 0.52rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  color: var(--text-mute);
  margin-bottom: 5px;
}
.bt-kpi-value {
  font-size: 1rem;
  font-weight: 700;
  color: var(--text);
  letter-spacing: 0.02em;
}
.bt-kpi-value.bull { color: var(--bull); }
.bt-kpi-value.bear { color: var(--bear); }
.bt-kpi-sub {
  font-size: 0.55rem;
  color: var(--text-mute);
  margin-top: 3px;
  letter-spacing: 0.06em;
}

/* Chart blocks */
.bt-chart-block {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 4px;
  overflow: hidden;
}
.bt-chart-title {
  font-size: 0.56rem;
  font-weight: 700;
  letter-spacing: 0.16em;
  color: var(--text-mute);
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
}
.bt-chart-placeholder {
  height: 180px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.bt-chart-ph-label {
  font-size: 0.6rem;
  color: var(--text-mute);
  letter-spacing: 0.1em;
  opacity: 0.4;
}

/* Trade log */
.bt-trade-log {
  max-height: 280px;
  overflow-y: auto;
  overflow-x: auto;
}
.bt-trade-log-empty {
  padding: 24px;
  text-align: center;
  font-size: 0.62rem;
  color: var(--text-mute);
  opacity: 0.5;
}
.bt-trade-row {
  display: grid;
  grid-template-columns: 30px 42px 66px 66px 60px 58px 58px 50px 60px 40px 60px 48px 48px 36px;
  align-items: center;
  padding: 6px 14px;
  border-bottom: 1px solid var(--border);
  font-size: 0.62rem;
  font-weight: 700;
  transition: background 0.1s;
}
.bt-trade-row:hover { background: var(--bg3); }
.bt-trade-header {
  font-size: 0.52rem;
  letter-spacing: 0.1em;
  color: var(--text-mute);
  background: var(--bg3);
  border-bottom: 1px solid var(--border2);
  position: sticky;
  top: 0;
  z-index: 2;
}
.bt-trade-cell { padding: 0 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.bt-trade-cell.bull { color: var(--bull); }
.bt-trade-cell.bear { color: var(--bear); }

/* ═══════════════════════════════════════════════════════════════════
   BACKTEST DASHBOARD — Part 3 additions
   ═══════════════════════════════════════════════════════════════════ */

/* ─── Dashboard root ─────────────────────────────────────────────── */
.bt-dashboard {
  padding: 0 0 32px;
  display: flex;
  flex-direction: column;
  gap: 0;
}

.bt-dash-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 8px;
  padding: 16px 24px 14px;
  border-bottom: 1px solid var(--border);
  background: var(--bg2);
  position: sticky;
  top: 0;
  z-index: 4;
}
.bt-dash-strategy {
  font-family: var(--syne);
  font-size: 0.88rem;
  font-weight: 800;
  color: var(--text);
  letter-spacing: 0.08em;
}
.bt-dash-meta {
  font-size: 0.6rem;
  color: var(--text-dim);
  letter-spacing: 0.08em;
}

/* ─── Section ────────────────────────────────────────────────────── */
.bt-dash-section {
  padding: 18px 24px 0;
  border-bottom: 1px solid var(--border);
  padding-bottom: 20px;
}
.bt-dash-section-title {
  font-size: 0.56rem;
  font-weight: 700;
  letter-spacing: 0.18em;
  color: var(--text-mute);
  margin-bottom: 14px;
}
.bt-dash-sub {
  font-size: 0.52rem;
  color: var(--text-mute);
  letter-spacing: 0.1em;
  font-weight: 400;
}

/* ─── Chart layout ───────────────────────────────────────────────── */
.bt-chart-card {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 3px;
  overflow: hidden;
}
.bt-chart-label {
  font-size: 0.52rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  color: var(--text-mute);
  padding: 8px 12px 6px;
  border-bottom: 1px solid var(--border);
}
.bt-chart-2col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.bt-chart-3col {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 10px;
}

/* ─── Canvases ───────────────────────────────────────────────────── */
.bt-canvas {
  display: block;
  width: 100%;
  height: 260px;
}
.bt-canvas-sm {
  height: 180px;
}

/* ─── MC probability table ───────────────────────────────────────── */
.bt-prob-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 7px 0;
  border-bottom: 1px solid var(--border);
  font-size: 0.62rem;
  font-weight: 700;
  color: var(--text-dim);
}
.bt-prob-row:last-child { border-bottom: none; }
.bt-prob-row span:last-child { color: var(--text); }

/* ─── Text color overrides for dashboard panels ────────────────── */
.bt-results-panel,
.bt-dashboard {
  --text-dim:  #ffffff;
  --text-mute: #ffffff;
}

/* ═══════════════════════════════════════════════════════════════════
   LOT SIZE ROW
   ═══════════════════════════════════════════════════════════════════ */
.bt-lotsize-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.bt-lotsize-hint {
  font-size: 0.58rem;
  color: var(--text-dim);
  white-space: nowrap;
}

/* ═══════════════════════════════════════════════════════════════════
   LIVE STATS (streaming progress)
   ═══════════════════════════════════════════════════════════════════ */
.bt-live-stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin-top: 12px;
}
.bt-live-stat {
  background: var(--bg3);
  border: 1px solid var(--border);
  border-radius: 3px;
  padding: 8px 12px;
}
.bt-live-label {
  display: block;
  font-size: 0.52rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  color: var(--text-mute);
  margin-bottom: 4px;
}
.bt-live-value {
  font-size: 0.82rem;
  font-weight: 700;
  color: var(--text);
  letter-spacing: 0.03em;
}
.bt-live-value.bull { color: var(--bull); }
.bt-live-value.bear { color: var(--bear); }

/* ─── Strategy Loader Mode ───────────────────────────────────────── */
#bt_strategyParams {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

#bt_strategyResults {
  display: none;
}

#bt_strategyDescription {
  line-height: 1.55;
}

/* ─── Strategy Loader / Replay ───────────────────────────────────── */
.bt-manual-only {
  display: block;
}

#bt_strategyParams,
#bt_engineParams {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

#bt_replayWrap .bt-icon-btn {
  width: 34px;
  height: 30px;
  font-size: 0.7rem;
}

#bt_replayMeta {
  margin-left: 4px;
}
```
