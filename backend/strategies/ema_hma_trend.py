# backend/strategies/ema_hma_trend.py
from __future__ import annotations

from backend.strategies.base import BaseStrategy


class EmaHmaTrendStrategy(BaseStrategy):
    strategy_id = "ema_hma_trend"
    name = "EMA + HMA Trend Pullback"
    description = (
        "Trend-following strategy. Uses a fast EMA trigger with a slower HMA trend filter. "
        "Entries happen when price reclaims/loses the fast EMA in the direction of the HMA trend."
    )

    parameters = {
        "_group_core": {
            "type": "group",
            "label": "Core Filters",
        },
        "fast_ema": {
            "type": "number",
            "label": "Fast EMA",
            "default": 21,
            "min": 2,
            "max": 500,
            "step": 1,
            "integer": True,
            "group": "Core Filters",
        },
        "trend_hma": {
            "type": "number",
            "label": "Trend HMA",
            "default": 55,
            "min": 2,
            "max": 500,
            "step": 1,
            "integer": True,
            "group": "Core Filters",
        },
        "confirm_close": {
            "type": "boolean",
            "label": "Require close beyond trigger EMA",
            "default": True,
            "group": "Core Filters",
        },
        "_group_risk": {
            "type": "group",
            "label": "Risk",
        },
        "swing_lookback": {
            "type": "number",
            "label": "Swing Lookback",
            "default": 6,
            "min": 2,
            "max": 50,
            "step": 1,
            "integer": True,
            "group": "Risk",
        },
        "stop_buffer_points": {
            "type": "number",
            "label": "Stop Buffer (points)",
            "default": 2.0,
            "min": 0.0,
            "max": 100.0,
            "step": 0.25,
            "group": "Risk",
        },
        "reward_r": {
            "type": "number",
            "label": "Reward R",
            "default": 2.0,
            "min": 0.5,
            "max": 20.0,
            "step": 0.25,
            "group": "Risk",
        },
        "exit_mode": {
            "type": "enum",
            "label": "Exit Mode",
            "default": "ema_cross",
            "options": ["ema_cross", "hma_cross", "tp_or_sl_only"],
            "group": "Risk",
        },
    }

    indicators_required = [
        {"key": "fast_ema", "kind": "EMA", "source": "close", "period_param": "fast_ema"},
        {"key": "trend_hma", "kind": "HMA", "source": "close", "period_param": "trend_hma"},
    ]

    def _lowest_low(self, bars, i, lookback):
        start = max(0, i - lookback + 1)
        return min(b["low"] for b in bars[start:i + 1])

    def _highest_high(self, bars, i, lookback):
        start = max(0, i - lookback + 1)
        return max(b["high"] for b in bars[start:i + 1])

    def on_bar(self, i, bar, indicators, state, position, bars, params):
        if i < 2:
            return {}

        fast = indicators["current"].get("fast_ema")
        trend = indicators["current"].get("trend_hma")
        prev_fast = indicators["series"]["fast_ema"][i - 1]
        prev_trend = indicators["series"]["trend_hma"][i - 1]

        if fast is None or trend is None or prev_fast is None or prev_trend is None:
            return {}

        close_ = bar["close"]
        prev_close = bars[i - 1]["close"]

        # Exit logic for open positions
        if position:
            exit_mode = params["exit_mode"]

            if exit_mode == "ema_cross":
                if position["direction"] == "long" and close_ < fast:
                    return {"exit": True, "exit_reason": "ema_cross"}
                if position["direction"] == "short" and close_ > fast:
                    return {"exit": True, "exit_reason": "ema_cross"}

            elif exit_mode == "hma_cross":
                if position["direction"] == "long" and close_ < trend:
                    return {"exit": True, "exit_reason": "hma_cross"}
                if position["direction"] == "short" and close_ > trend:
                    return {"exit": True, "exit_reason": "hma_cross"}

            return {}

        # Entry logic
        bullish_trend = close_ > trend and fast > trend
        bearish_trend = close_ < trend and fast < trend

        crossed_up = prev_close <= prev_fast and close_ > fast
        crossed_down = prev_close >= prev_fast and close_ < fast

        if params["confirm_close"]:
            enter_long = bullish_trend and crossed_up
            enter_short = bearish_trend and crossed_down
        else:
            enter_long = bullish_trend and bar["high"] > fast
            enter_short = bearish_trend and bar["low"] < fast

        if enter_long:
            swing_low = self._lowest_low(bars, i, int(params["swing_lookback"]))
            stop = swing_low - float(params["stop_buffer_points"])
            entry = close_
            risk = max(0.01, entry - stop)
            tp = entry + (risk * float(params["reward_r"]))
            return {
                "enter": "long",
                "entry_price": entry,
                "stop_loss": stop,
                "take_profit": tp,
                "entry_reason": "ema_reclaim_with_hma_trend",
            }

        if enter_short:
            swing_high = self._highest_high(bars, i, int(params["swing_lookback"]))
            stop = swing_high + float(params["stop_buffer_points"])
            entry = close_
            risk = max(0.01, stop - entry)
            tp = entry - (risk * float(params["reward_r"]))
            return {
                "enter": "short",
                "entry_price": entry,
                "stop_loss": stop,
                "take_profit": tp,
                "entry_reason": "ema_loss_with_hma_trend",
            }

        return {}