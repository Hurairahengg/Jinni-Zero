from __future__ import annotations
from typing import Any, Dict, List, Optional
from backend.strategies.base import BaseStrategy


class JinniHmaDualBreakEven(BaseStrategy):
    strategy_id = "jinni_hma_dual_breakeven"
    name = "Jinni HMA Dual Breakeven"
    description = (
        "2-bar confirmation above/below HMA200+HMA55 with reversal filter. "
        "SL snapshot on higher/lower HMA. SL moves to breakeven after 1 bar. "
        "Exit when close crosses HMA55."
    )
    version = "1.0.0"
    min_lookback = 210

    parameters = {
        "hma_fast": {
            "type": "number", "label": "Fast HMA",
            "default": 55, "min": 5, "max": 300, "step": 1,
        },
        "hma_slow": {
            "type": "number", "label": "Slow HMA",
            "default": 200, "min": 20, "max": 500, "step": 1,
        },
        "confirm_bars": {
            "type": "number", "label": "Confirm Bars",
            "default": 2, "min": 2, "max": 5, "step": 1,
            "help": "Number of consecutive bars required above/below both HMAs.",
        },
        "reversal_filter": {
            "type": "boolean", "label": "Reversal Filter",
            "default": True,
            "help": "Requires the candle BEFORE the 2 confirmation candles to match direction.",
        },
        "move_to_be_after_bars": {
            "type": "number", "label": "Move SL to BE After Bars",
            "default": 1, "min": 1, "max": 10, "step": 1,
        },
        "be_buffer_pts": {
            "type": "number", "label": "Breakeven Buffer (pts)",
            "default": 0.0, "min": 0.0, "max": 50.0, "step": 0.25,
            "help": "Optional buffer added to BE stop (ex: +0.5 points).",
        },
    }

    # ==========================================================
    # INDICATORS
    # ==========================================================
    def build_indicators(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        fast = int(params.get("hma_fast", 55))
        slow = int(params.get("hma_slow", 200))

        return [
            dict(key="hma_fast", kind="HMA", period=fast, source="close"),
            dict(key="hma_slow", kind="HMA", period=slow, source="close"),
        ]

    # ==========================================================
    # INIT
    # ==========================================================
    def on_init(self, ctx: Any) -> None:
        s = ctx.state
        s["bc_long"] = 0
        s["bc_short"] = 0

        # breakeven tracking
        s["_entry_index"] = None
        s["_entry_price"] = None
        s["_be_done"] = False

    # ==========================================================
    # ON BAR
    # ==========================================================
    def on_bar(self, ctx: Any) -> Optional[Dict[str, Any]]:
        s = ctx.state
        p = ctx.params
        bar = ctx.bar
        bars = ctx.bars
        i = ctx.index
        ind = ctx.indicators

        confirm_bars = int(p.get("confirm_bars", 2))
        reversal_filter = bool(p.get("reversal_filter", True))
        move_to_be_after = int(p.get("move_to_be_after_bars", 1))
        be_buffer = float(p.get("be_buffer_pts", 0.0))

        hma_fast = ind.get("hma_fast")
        hma_slow = ind.get("hma_slow")

        if hma_fast is None or hma_slow is None:
            s["bc_long"] = 0
            s["bc_short"] = 0
            return None

        c = float(bar["close"])
        o = float(bar["open"])
        bull = c > o
        bear = c < o

        # ======================================================
        # IN POSITION → HANDLE BREAKEVEN + EXIT LOGIC
        # ======================================================
        if ctx.position.has_position:
            pos = ctx.position

            # --- Move SL to breakeven after N bars ---
            if not s.get("_be_done", False):
                entry_index = s.get("_entry_index")
                entry_price = s.get("_entry_price")

                if entry_index is not None and entry_price is not None:
                    bars_in_trade = i - entry_index

                    if bars_in_trade >= move_to_be_after:
                        if pos.direction == "long":
                            new_sl = entry_price + be_buffer
                            if pos.sl_level is None or new_sl > pos.sl_level:
                                s["_be_done"] = True
                                return {"signal": "HOLD", "update_sl": new_sl}

                        elif pos.direction == "short":
                            new_sl = entry_price - be_buffer
                            if pos.sl_level is None or new_sl < pos.sl_level:
                                s["_be_done"] = True
                                return {"signal": "HOLD", "update_sl": new_sl}

            # --- TP / exit rule: close crosses HMA FAST (55) ---
            if pos.direction == "long":
                if c < hma_fast:
                    return {"signal": "CLOSE"}
            elif pos.direction == "short":
                if c > hma_fast:
                    return {"signal": "CLOSE"}

            return {"signal": "HOLD"}

        # ======================================================
        # FLAT → RESET TRADE STATE
        # ======================================================
        s["_entry_index"] = None
        s["_entry_price"] = None
        s["_be_done"] = False

        # ======================================================
        # REVERSAL FILTER CHECK BAR (bar before confirmations)
        # Needs at least confirm_bars + 1 history
        # ======================================================
        if reversal_filter:
            if i < confirm_bars:
                return None

            prev_check = bars[i - confirm_bars]
            pc = float(prev_check["close"])
            po = float(prev_check["open"])
            prev_bull = pc > po
            prev_bear = pc < po
        else:
            prev_bull = True
            prev_bear = True

        # ======================================================
        # ENTRY CONDITIONS: ABOVE/BELLOW BOTH HMAs
        # ======================================================
        above_both = c > hma_fast and c > hma_slow
        below_both = c < hma_fast and c < hma_slow

        sig = None

        # --- LONG confirmation ---
        if above_both and bull:
            s["bc_long"] += 1
        else:
            s["bc_long"] = 0

        if s["bc_long"] >= confirm_bars and prev_bull:
            sig = "BUY"
            s["bc_long"] = 0
            s["bc_short"] = 0

        # --- SHORT confirmation ---
        if sig is None:
            if below_both and bear:
                s["bc_short"] += 1
            else:
                s["bc_short"] = 0

            if s["bc_short"] >= confirm_bars and prev_bear:
                sig = "SELL"
                s["bc_long"] = 0
                s["bc_short"] = 0

        if sig is None:
            return None

        # ======================================================
        # SL SNAPSHOT
        # BUY SL = higher HMA (max)
        # SELL SL = lower HMA (min)
        # ======================================================
        if sig == "BUY":
            sl_price = max(float(hma_fast), float(hma_slow))
        else:
            sl_price = min(float(hma_fast), float(hma_slow))

        # ======================================================
        # STORE ENTRY METADATA FOR BREAKEVEN
        # NOTE: entry fills next bar open, so approximate entry price
        # using next bar open if available, else current close.
        # ======================================================
        entry_price_guess = c
        if i + 1 < len(bars):
            entry_price_guess = float(bars[i + 1]["open"])

        s["_entry_index"] = i + 1
        s["_entry_price"] = entry_price_guess
        s["_be_done"] = False

        return {
            "signal": sig,
            "sl": sl_price,
        }