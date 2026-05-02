# backend/strategies/hma_200_flip.py
from __future__ import annotations

from backend.strategies.base import BaseStrategy


class HMA200FlipStrategy(BaseStrategy):
    """
    HMA 200 Flip Strategy
    --------------------
    - Buy when price crosses ABOVE 200 HMA
    - Sell when price crosses BELOW 200 HMA
    - Only one trade per direction until flip
    """

    strategy_id = "hma_200_flip"
    name = "HMA 200 Flip"
    description = "Trades price flips around the 200 HMA. One trade per direction."
    version = "1.0"

    # ==========================================================
    # PARAMETERS
    # ==========================================================
    def get_parameter_schema(self):
        return {
            "hma_length": {
                "type": "number",
                "label": "HMA Length",
                "default": 200,
                "min": 10,
                "max": 500,
                "step": 1,
            },
            "stop_buffer": {
                "type": "number",
                "label": "Stop Buffer (points)",
                "default": 5.0,
                "min": 0.0,
                "max": 100.0,
                "step": 0.25,
            },
        }

    def get_default_parameters(self):
        return {
            "hma_length": 200,
            "stop_buffer": 5.0,
        }

    # ==========================================================
    # INDICATORS
    # ==========================================================
    def build_indicators(self, params):
        return [
            {
                "key": "hma",
                "kind": "HMA",
                "period": int(params["hma_length"]),
                "source": "close",
            }
        ]

    # ==========================================================
    # STRATEGY
    # ==========================================================
    def on_bar(self, ctx):
        i = ctx.index
        if i < 2:
            return None

        hma = ctx.indicators.get("hma")
        prev_hma = ctx.ind_series["hma"][i - 1]

        if hma is None or prev_hma is None:
            return None

        price = ctx.bar["close"]
        prev_price = ctx.bars[i - 1]["close"]

        crossed_up = prev_price <= prev_hma and price > hma
        crossed_down = prev_price >= prev_hma and price < hma

        # ── EXIT ON OPPOSITE CROSS ──────────────────
        if ctx.position:
            if ctx.position.direction == "long" and crossed_down:
                return {
                    "exit": True,
                    "reason": "hma_flip_down",
                }
            if ctx.position.direction == "short" and crossed_up:
                return {
                    "exit": True,
                    "reason": "hma_flip_up",
                }
            return None

        # ── ENTRY (ONE PER DIRECTION) ───────────────
        stop_buffer = ctx.params["stop_buffer"]

        if crossed_up:
            sl = ctx.bar["low"] - stop_buffer
            return {
                "enter": "long",
                "stop_loss": sl,
                "reason": "hma_cross_up",
            }

        if crossed_down:
            sl = ctx.bar["high"] + stop_buffer
            return {
                "enter": "short",
                "stop_loss": sl,
                "reason": "hma_cross_down",
            }

        return None
