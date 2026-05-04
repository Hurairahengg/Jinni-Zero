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