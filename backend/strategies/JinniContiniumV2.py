"""
JINNI ZERO — Jinni Renko Rider
================================
Pure renko/range-bar trend-following strategy with trailing stop.
No consecutive same-direction trades allowed.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
from backend.strategies.base import BaseStrategy


class JinniRenkoRider(BaseStrategy):
    strategy_id = "jinni_renko_rider"
    name = "Jinni Renko Rider"
    description = (
        "Trend rider: N consecutive same-direction bars → entry, "
        "SL at last bar's extreme, trails every new favorable bar. "
        "No TP — rides until reversal hits trailing SL. "
        "Prevents consecutive same-direction trades."
    )
    version = "1.3.0"
    min_lookback = 0

    parameters = {
        "confirm_bars": {
            "type": "number", "label": "Confirmation Bars",
            "default": 2, "min": 1, "max": 10, "step": 1,
            "help": "Consecutive same-direction bars needed before entry.",
        },
        "sl_offset": {
            "type": "number", "label": "SL Offset (pts)",
            "default": 0, "min": 0, "max": 50, "step": 0.25,
            "help": "Buffer beyond bar low/high for SL. Prevents exact-wick stops.",
        },
        "no_reuse": {
            "type": "boolean", "label": "No Candle Reuse",
            "default": True,
            "help": "Bars used for signal + trade can't count toward next signal.",
        },
    }

    def build_indicators(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        return []

    def on_init(self, ctx: Any) -> None:
        s = ctx.state
        s["bull_count"] = 0
        s["bear_count"] = 0
        s["last_used_bar"] = -1
        s["_last_trade_count"] = 0
        s["_trail_count"] = 0

        # NEW: store last closed trade direction ("BUY" or "SELL")
        s["last_trade_dir"] = None

    def on_bar(self, ctx: Any) -> Optional[Dict[str, Any]]:
        s = ctx.state
        p = ctx.params
        bar = ctx.bar
        i = ctx.index

        c = float(bar["close"])
        o = float(bar["open"])
        h = float(bar["high"])
        l = float(bar["low"])

        bull = c > o
        bear = c < o

        confirm_bars = int(p.get("confirm_bars", 2))
        sl_offset = float(p.get("sl_offset", 0))
        no_reuse = bool(p.get("no_reuse", True))

        # ── Update last used bar + last trade direction from closed trades ──
        trades = ctx.trades
        last_count = s.get("_last_trade_count", 0)

        if len(trades) > last_count:
            last_trade = trades[-1]

            exit_bar = last_trade.get("exit_bar", i)
            if no_reuse:
                s["last_used_bar"] = exit_bar
                s["bull_count"] = 0
                s["bear_count"] = 0

            # direction might be "long"/"short" or "BUY"/"SELL"
            tdir = last_trade.get("direction") or last_trade.get("side")

            if tdir in ("long", "LONG", "BUY"):
                s["last_trade_dir"] = "BUY"
            elif tdir in ("short", "SHORT", "SELL"):
                s["last_trade_dir"] = "SELL"

        s["_last_trade_count"] = len(trades)

        # ══════════════════════════════════════════════════════
        # IN POSITION → TRAIL SL
        # ══════════════════════════════════════════════════════
        if ctx.position.has_position:
            pos = ctx.position

            if pos.direction == "long" and bull:
                new_sl = l - sl_offset
                current_sl = pos.sl_level
                if current_sl is None or new_sl > current_sl:
                    s["_trail_count"] = s.get("_trail_count", 0) + 1
                    return {"signal": "HOLD", "update_sl": new_sl}

            elif pos.direction == "short" and bear:
                new_sl = h + sl_offset
                current_sl = pos.sl_level
                if current_sl is None or new_sl < current_sl:
                    s["_trail_count"] = s.get("_trail_count", 0) + 1
                    return {"signal": "HOLD", "update_sl": new_sl}

            return {"signal": "HOLD"}

        # ══════════════════════════════════════════════════════
        # FLAT — look for entry signals
        # ══════════════════════════════════════════════════════
        if no_reuse and i <= s.get("last_used_bar", -1):
            s["bull_count"] = 0
            s["bear_count"] = 0
            return None

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

        sig = None
        sl_price = None

        # ══════════════════════════════════════════════════════
        # ENTRY RULES (NO SAME DIRECTION TWICE)
        # ══════════════════════════════════════════════════════
        if s["bull_count"] >= confirm_bars:
            if s.get("last_trade_dir") != "BUY":   # block BUY after BUY
                sig = "BUY"
                sl_price = l - sl_offset

            s["bull_count"] = 0
            s["bear_count"] = 0
            if no_reuse:
                s["last_used_bar"] = i

        elif s["bear_count"] >= confirm_bars:
            if s.get("last_trade_dir") != "SELL":  # block SELL after SELL
                sig = "SELL"
                sl_price = h + sl_offset

            s["bull_count"] = 0
            s["bear_count"] = 0
            if no_reuse:
                s["last_used_bar"] = i

        if sig is None:
            return None

        s["_trail_count"] = 0

        return {
            "signal": sig,
            "sl": sl_price,
        }