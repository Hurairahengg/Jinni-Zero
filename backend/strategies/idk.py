"""
JINNI ZERO — Jinni Continuum
=============================
Pure price-action momentum continuation strategy.

ENTRY
  BUY:  N consecutive bull candles (close > open) → enter next bar open
  SELL: N consecutive bear candles (close < open) → enter next bar open

STOP LOSS
  BUY:  Last (Nth) bull candle's BODY LOW (min(open, close))
  SELL: Last (Nth) bear candle's BODY HIGH (max(open, close))

TAKE PROFIT
  R-multiple of risk (configurable, default 1R)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.strategies.base import BaseStrategy


class JinniContinuum(BaseStrategy):
    # ── Metadata ───────────────────────────────────────────────
    strategy_id = "jinni_continuum"
    name = "Jinni Continuum"
    description = (
        "Momentum continuation: N consecutive bull/bear candles → "
        "entry at next bar close, SL at last candle's body, "
        "TP at configurable R-multiple. Optional candle reuse prevention."
    )
    version = "1.2.0"
    min_lookback = 0

    # ==========================================================
    # PARAMETERS
    # ==========================================================
    parameters = {
        "confirm_bars": {
            "type": "number",
            "label": "Confirmation Bars",
            "default": 2,
            "min": 1,
            "max": 10,
            "step": 1,
            "help": "Consecutive same-direction candles needed before entry.",
        },
        "r_multiple": {
            "type": "number",
            "label": "R Multiple (TP)",
            "default": 1.0,
            "min": 0.5,
            "max": 20,
            "step": 0.5,
            "help": "Take profit as multiple of risk distance.",
        },
        "no_reuse": {
            "type": "boolean",
            "label": "No Candle Reuse",
            "default": True,
            "help": (
                "When ON, all candles used for signal + trade are consumed. "
                "Must wait for completely fresh candles after trade closes."
            ),
        },
    }

    # ==========================================================
    # INDICATORS — none needed
    # ==========================================================
    def build_indicators(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        return []

    # ==========================================================
    # INIT
    # ==========================================================
    def on_init(self, ctx: Any) -> None:
        s = ctx.state
        s["bull_count"] = 0
        s["bear_count"] = 0
        s["last_used_bar"] = -1
        s["_last_trade_count"] = 0

    # ==========================================================
    # ON BAR — signal generation only
    # ==========================================================
    def on_bar(self, ctx: Any) -> Optional[Dict[str, Any]]:
        s = ctx.state
        p = ctx.params
        bar = ctx.bar
        i = ctx.index

        c = float(bar["close"])
        o = float(bar["open"])

        bull = c > o
        bear = c < o

        confirm_bars = int(p.get("confirm_bars", 2))
        r_multiple = float(p.get("r_multiple", 1.0))
        no_reuse = bool(p.get("no_reuse", True))

        # ══════════════════════════════════════════════════════
        # UPDATE LAST USED BAR FROM CLOSED TRADES
        # ══════════════════════════════════════════════════════
        if no_reuse:
            trades = ctx.trades
            last_count = s.get("_last_trade_count", 0)
            if len(trades) > last_count:
                last_trade = trades[-1]
                exit_bar = last_trade.get("exit_bar", i)
                s["last_used_bar"] = exit_bar
                s["bull_count"] = 0
                s["bear_count"] = 0
            s["_last_trade_count"] = len(trades)

        # ══════════════════════════════════════════════════════
        # IN POSITION → HOLD
        # ══════════════════════════════════════════════════════
        if ctx.position.has_position:
            return {"signal": "HOLD"}

        # ══════════════════════════════════════════════════════
        # CANDLE REUSE CHECK
        # ══════════════════════════════════════════════════════
        if no_reuse and i <= s.get("last_used_bar", -1):
            s["bull_count"] = 0
            s["bear_count"] = 0
            return None

        # ══════════════════════════════════════════════════════
        # COUNT CONSECUTIVE CANDLES
        # ══════════════════════════════════════════════════════
        if bull:
            s["bull_count"] = s.get("bull_count", 0) + 1
            s["bear_count"] = 0
        elif bear:
            s["bear_count"] = s.get("bear_count", 0) + 1
            s["bull_count"] = 0
        else:
            s["bull_count"] = 0
            s["bear_count"] = 0
            return None

        # ══════════════════════════════════════════════════════
        # CHECK SIGNAL
        # ══════════════════════════════════════════════════════
        sig = None
        sl_price = None

        if s["bull_count"] >= confirm_bars:
            sig = "BUY"
            sl_price = min(o, c)   # BODY LOW

            s["bull_count"] = 0
            s["bear_count"] = 0

            if no_reuse:
                s["last_used_bar"] = i

        elif s["bear_count"] >= confirm_bars:
            sig = "SELL"
            sl_price = max(o, c)   # BODY HIGH

            s["bull_count"] = 0
            s["bear_count"] = 0

            if no_reuse:
                s["last_used_bar"] = i

        if sig is None:
            return None

        # ══════════════════════════════════════════════════════
        # BUILD SIGNAL
        # ══════════════════════════════════════════════════════
        return {
            "signal": sig,
            "sl": sl_price,
            "tp_mode": "r_multiple",
            "tp_r": r_multiple,
        }