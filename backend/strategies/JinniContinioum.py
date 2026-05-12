"""
JINNI ZERO — Jinni Continuum (Bar-Close Execution)
====================================================
Pure price-action momentum continuation strategy.

LOGIC:
  Count consecutive same-direction closed bars as they come in.
  When the count reaches N → fire signal on THAT bar's close.

  Example (confirm_bars=2):
    Bar 5: bullish → bull_count=1
    Bar 6: bullish → bull_count=2 → BUY fires, entry at bar 6 close
    Bar 7: bearish → bull_count=0, bear_count=1

  This is a RUNNING counter, not a lookback.

STOP LOSS:
  BUY:  current bar's low (the bar that triggered the signal)
  SELL: current bar's high

TAKE PROFIT:
  R-multiple of risk (configurable, default 1R)

NO CANDLE REUSE:
  After trade closes, reset counter to 0 so bars already used
  for the previous signal/trade cannot contribute to the next
  one.  The very next bar after exit is free to start a fresh
  count — no artificial cooldown.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
from backend.strategies.base import BaseStrategy


class JinniContinuum(BaseStrategy):
    strategy_id = "jinni_continuum"
    name = "Jinni Continuum"
    description = (
        "Momentum continuation: N consecutive bull/bear bars → "
        "entry at bar close, SL at signal bar's low/high, "
        "TP at configurable R-multiple."
    )
    version = "2.1.0"
    min_lookback = 0

    parameters = {
        "confirm_bars": {
            "type": "number",
            "label": "Confirmation Bars",
            "default": 2,
            "min": 1,
            "max": 10,
            "step": 1,
            "help": "Consecutive same-direction bars needed. e.g. 2 = current + previous both bullish → BUY.",
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
            "help": "After trade closes, reset counter so signal-bars can't feed the next entry.",
        },
    }

    def build_indicators(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        return []

    def on_init(self, ctx: Any) -> None:
        s = ctx.state
        s["bull_count"] = 0
        s["bear_count"] = 0
        s["_last_trade_count"] = 0

    def on_bar(self, ctx: Any) -> Optional[Dict[str, Any]]:
        s = ctx.state
        p = ctx.params
        bar = ctx.bar

        c = float(bar["close"])
        o = float(bar["open"])
        h = float(bar["high"])
        l = float(bar["low"])

        bull = c > o
        bear = c < o

        confirm_bars = int(p.get("confirm_bars", 2))
        r_multiple   = float(p.get("r_multiple", 1.0))
        no_reuse     = bool(p.get("no_reuse", True))

        # ══════════════════════════════════════════════════════
        # TRADE CLOSED → just reset counters, no cooldown
        # ══════════════════════════════════════════════════════
        if no_reuse:
            trades = ctx.trades
            last_count = s.get("_last_trade_count", 0)
            if len(trades) > last_count:
                s["bull_count"] = 0
                s["bear_count"] = 0
            s["_last_trade_count"] = len(trades)

        # ══════════════════════════════════════════════════════
        # IN POSITION → HOLD
        # ══════════════════════════════════════════════════════
        if ctx.position.has_position:
            return {"signal": "HOLD"}

        # ══════════════════════════════════════════════════════
        # COUNT CONSECUTIVE BARS
        #
        # Each closed bar: if bullish → bull_count++, reset bear
        #                  if bearish → bear_count++, reset bull
        #                  if doji    → reset both
        # ══════════════════════════════════════════════════════
        if bull:
            s["bull_count"] += 1
            s["bear_count"] = 0
        elif bear:
            s["bear_count"] += 1
            s["bull_count"] = 0
        else:
            # doji — breaks streak
            s["bull_count"] = 0
            s["bear_count"] = 0
            return None

        # ══════════════════════════════════════════════════════
        # CHECK IF COUNT REACHED → FIRE SIGNAL
        # ══════════════════════════════════════════════════════
        if s["bull_count"] >= confirm_bars:
            s["bull_count"] = 0
            s["bear_count"] = 0
            return {
                "signal": "BUY",
                "sl": l,                    # this bar's low
                "tp_mode": "r_multiple",
                "tp_r": r_multiple,
            }

        if s["bear_count"] >= confirm_bars:
            s["bull_count"] = 0
            s["bear_count"] = 0
            return {
                "signal": "SELL",
                "sl": h,                    # this bar's high
                "tp_mode": "r_multiple",
                "tp_r": r_multiple,
            }

        return None