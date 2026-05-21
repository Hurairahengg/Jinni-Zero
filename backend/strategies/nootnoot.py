from __future__ import annotations

from typing import Any, Dict, List, Optional
from backend.strategies.base import BaseStrategy


class HoldUntilOpposite(BaseStrategy):
    strategy_id = "hold_until_opposite"
    name = "Hold Until Opposite"
    description = (
        "Enter after N consecutive candles in same direction. "
        "SL is set using the first X candles of the streak. "
        "Exit when first opposite candle appears."
    )
    version = "1.2.0"
    min_lookback = 0

    parameters = {
        "confirm_bars": {
            "type": "number",
            "label": "Confirmation Bars",
            "default": 3,
            "min": 1,
            "max": 50,
            "step": 1,
            "help": "How many consecutive candles required before entering.",
        },
        "sl_anchor_bars": {
            "type": "number",
            "label": "SL Anchor Bars",
            "default": 2,
            "min": 1,
            "max": 20,
            "step": 1,
            "help": "How many of the FIRST candles in the streak to use for SL calculation.",
        },
        "no_reuse": {
            "type": "boolean",
            "label": "No Candle Reuse",
            "default": True,
            "help": "Prevents using candles during an open trade for a new entry signal.",
        },
    }

    def build_indicators(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        return []

    def on_init(self, ctx: Any) -> None:
        s = ctx.state
        s["bull_count"] = 0
        s["bear_count"] = 0

        s["bull_anchor_lows"] = []
        s["bear_anchor_highs"] = []

        s["last_used_bar"] = -1
        s["_last_trade_count"] = 0

    def on_bar(self, ctx: Any) -> Optional[Dict[str, Any]]:
        s = ctx.state
        p = ctx.params
        bar = ctx.bar
        i = ctx.index

        o = float(bar["open"])
        c = float(bar["close"])
        h = float(bar["high"])
        l = float(bar["low"])

        bull = c > o
        bear = c < o

        confirm_bars = int(p.get("confirm_bars", 3))
        sl_anchor_bars = int(p.get("sl_anchor_bars", 2))
        no_reuse = bool(p.get("no_reuse", True))

        # safety: SL anchor cannot exceed confirm bars
        if sl_anchor_bars > confirm_bars:
            sl_anchor_bars = confirm_bars

        # ============================================
        # UPDATE LAST USED BAR FROM CLOSED TRADES
        # ============================================
        if no_reuse:
            trades = ctx.trades
            last_count = s.get("_last_trade_count", 0)
            if len(trades) > last_count:
                last_trade = trades[-1]
                exit_bar = last_trade.get("exit_bar", i)
                s["last_used_bar"] = exit_bar

                s["bull_count"] = 0
                s["bear_count"] = 0
                s["bull_anchor_lows"] = []
                s["bear_anchor_highs"] = []

            s["_last_trade_count"] = len(trades)

        # ============================================
        # EXIT RULE: close trade on opposite candle
        # ============================================
        if ctx.position.has_position:
            pos = ctx.position

            if pos.direction == "long" and bear:
                return {"signal": "CLOSE"}

            if pos.direction == "short" and bull:
                return {"signal": "CLOSE"}

            return {"signal": "HOLD"}

        # ============================================
        # NO REUSE CHECK
        # ============================================
        if no_reuse and i <= s.get("last_used_bar", -1):
            s["bull_count"] = 0
            s["bear_count"] = 0
            s["bull_anchor_lows"] = []
            s["bear_anchor_highs"] = []
            return None

        # ============================================
        # HANDLE COUNTS + ANCHOR COLLECTION
        # ============================================
        if bull:
            # new bull streak starting?
            if s["bull_count"] == 0:
                s["bull_anchor_lows"] = []

            s["bull_count"] += 1
            s["bear_count"] = 0
            s["bear_anchor_highs"] = []

            if len(s["bull_anchor_lows"]) < sl_anchor_bars:
                s["bull_anchor_lows"].append(l)

        elif bear:
            # new bear streak starting?
            if s["bear_count"] == 0:
                s["bear_anchor_highs"] = []

            s["bear_count"] += 1
            s["bull_count"] = 0
            s["bull_anchor_lows"] = []

            if len(s["bear_anchor_highs"]) < sl_anchor_bars:
                s["bear_anchor_highs"].append(h)

        else:
            # doji resets everything
            s["bull_count"] = 0
            s["bear_count"] = 0
            s["bull_anchor_lows"] = []
            s["bear_anchor_highs"] = []
            return None

        # ============================================
        # ENTRY SIGNALS
        # ============================================
        if s["bull_count"] >= confirm_bars:
            sl_price = min(s["bull_anchor_lows"]) if s["bull_anchor_lows"] else l

            s["bull_count"] = 0
            s["bear_count"] = 0
            s["bull_anchor_lows"] = []
            s["bear_anchor_highs"] = []

            if no_reuse:
                s["last_used_bar"] = i

            return {
                "signal": "BUY",
                "sl": sl_price,
            }

        if s["bear_count"] >= confirm_bars:
            sl_price = max(s["bear_anchor_highs"]) if s["bear_anchor_highs"] else h

            s["bull_count"] = 0
            s["bear_count"] = 0
            s["bull_anchor_lows"] = []
            s["bear_anchor_highs"] = []

            if no_reuse:
                s["last_used_bar"] = i

            return {
                "signal": "SELL",
                "sl": sl_price,
            }

        return None