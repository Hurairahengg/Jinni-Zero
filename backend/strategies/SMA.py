from __future__ import annotations

from backend.strategies.base import BaseStrategy


class Sma200TwoCandleConfirmationStrategy(BaseStrategy):
    strategy_id = "sma_200_two_candle_confirmation"
    name = "200 SMA Two-Candle Confirmation"
    description = (
        "Uses a 200 SMA break with next-candle confirmation. "
        "Long: price breaks above SMA, then the next candle is bullish "
        "(and optionally closes above the SMA). "
        "Short: mirrored below the SMA with a bearish confirmation candle. "
        "Stop is the entry candle low/high and TP is fixed R multiple."
    )

    parameters = {
        "_group_core": {
            "type": "group",
            "label": "Core Filters",
        },
        "sma_period": {
            "type": "number",
            "label": "SMA Period",
            "default": 200,
            "min": 2,
            "max": 500,
            "step": 1,
            "integer": True,
            "group": "Core Filters",
        },
        "confirm_close": {
            "type": "boolean",
            "label": "Require confirmation candle close beyond SMA",
            "default": True,
            "group": "Core Filters",
        },
        "_group_risk": {
            "type": "group",
            "label": "Risk",
        },
        "reward_r": {
            "type": "number",
            "label": "Reward R",
            "default": 3.0,
            "min": 0.5,
            "max": 20.0,
            "step": 0.25,
            "group": "Risk",
        },
        "stop_buffer_points": {
            "type": "number",
            "label": "Stop Buffer (points)",
            "default": 0.0,
            "min": 0.0,
            "max": 100.0,
            "step": 0.25,
            "group": "Risk",
        },
    }

    indicators_required = [
        {"key": "trend_sma", "kind": "SMA", "source": "close", "period_param": "sma_period"},
    ]

    def _is_bullish(self, bar):
        return bar["close"] > bar["open"]

    def _is_bearish(self, bar):
        return bar["close"] < bar["open"]

    def on_bar(self, i, bar, indicators, state, position, bars, params):
        if i < 2:
            return {}

        sma = indicators["current"].get("trend_sma")
        sma_series = indicators["series"]["trend_sma"]

        prev_sma = sma_series[i - 1]
        prev2_sma = sma_series[i - 2]

        if sma is None or prev_sma is None or prev2_sma is None:
            return {}

        close_ = bar["close"]
        prev_close = bars[i - 1]["close"]
        prev2_close = bars[i - 2]["close"]

        reward_r = float(params["reward_r"])
        stop_buffer = float(params["stop_buffer_points"])
        confirm_close = bool(params["confirm_close"])

        # ------------------------------------------------------------
        # EXIT LOGIC
        # ------------------------------------------------------------
        # No custom exit logic here.
        # The engine will handle stop loss / take profit from entry.
        if position:
            return {}

        # ------------------------------------------------------------
        # ENTRY LOGIC
        # ------------------------------------------------------------
        # Long:
        # 1) two bars ago close was at/below SMA
        # 2) previous bar closed above SMA  -> break candle
        # 3) current bar is bullish         -> confirmation candle
        long_break = prev2_close <= prev2_sma and prev_close > prev_sma
        long_confirm = self._is_bullish(bar)

        if confirm_close:
            long_confirm = long_confirm and close_ > sma

        if long_break and long_confirm:
            entry = close_
            stop = bar["low"] - stop_buffer
            risk = max(0.01, entry - stop)
            tp = entry + (risk * reward_r)

            return {
                "enter": "long",
                "entry_price": entry,
                "stop_loss": stop,
                "take_profit": tp,
                "entry_reason": "sma_break_above_plus_bullish_confirmation",
            }

        # Short:
        # 1) two bars ago close was at/above SMA
        # 2) previous bar closed below SMA  -> break candle
        # 3) current bar is bearish         -> confirmation candle
        short_break = prev2_close >= prev2_sma and prev_close < prev_sma
        short_confirm = self._is_bearish(bar)

        if confirm_close:
            short_confirm = short_confirm and close_ < sma

        if short_break and short_confirm:
            entry = close_
            stop = bar["high"] + stop_buffer
            risk = max(0.01, stop - entry)
            tp = entry - (risk * reward_r)

            return {
                "enter": "short",
                "entry_price": entry,
                "stop_loss": stop,
                "take_profit": tp,
                "entry_reason": "sma_break_below_plus_bearish_confirmation",
            }

        return {}