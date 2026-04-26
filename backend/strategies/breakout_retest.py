# backend/strategies/breakout_retest.py
from __future__ import annotations

from backend.strategies.base import BaseStrategy


class BreakoutRetestStrategy(BaseStrategy):
    strategy_id = "breakout_retest"
    name = "Breakout + Retest"
    description = (
        "Breakout and retest strategy using rolling highs/lows. "
        "Waits for a level break and retest before entry."
    )

    parameters = {
        "_group_breakout": {
            "type": "group",
            "label": "Breakout Rules",
        },
        "lookback": {
            "type": "number",
            "label": "Breakout Lookback",
            "default": 20,
            "min": 2,
            "max": 500,
            "step": 1,
            "integer": True,
            "group": "Breakout Rules",
        },
        "retest_tolerance": {
            "type": "number",
            "label": "Retest Tolerance (points)",
            "default": 2.0,
            "min": 0.0,
            "max": 50.0,
            "step": 0.25,
            "group": "Breakout Rules",
        },
        "_group_risk": {
            "type": "group",
            "label": "Risk",
        },
        "stop_buffer_points": {
            "type": "number",
            "label": "Stop Buffer (points)",
            "default": 1.0,
            "min": 0.0,
            "max": 50.0,
            "step": 0.25,
            "group": "Risk",
        },
        "reward_r": {
            "type": "number",
            "label": "Reward R",
            "default": 2.5,
            "min": 0.5,
            "max": 20.0,
            "step": 0.25,
            "group": "Risk",
        },
        "exit_on_mid_close": {
            "type": "boolean",
            "label": "Exit on failure close",
            "default": True,
            "group": "Risk",
        },
    }

    indicators_required = [
        {"key": "breakout_high", "kind": "HIGHEST_HIGH", "source": "high", "period_param": "lookback"},
        {"key": "breakout_low", "kind": "LOWEST_LOW", "source": "low", "period_param": "lookback"},
    ]

    def on_bar(self, i, bar, indicators, state, position, bars, params):
        if i < 2:
            return {}

        hi = indicators["current"].get("breakout_high")
        lo = indicators["current"].get("breakout_low")
        prev_hi = indicators["series"]["breakout_high"][i - 1]
        prev_lo = indicators["series"]["breakout_low"][i - 1]

        if hi is None or lo is None or prev_hi is None or prev_lo is None:
            return {}

        close_ = bar["close"]
        high_ = bar["high"]
        low_ = bar["low"]
        tol = float(params["retest_tolerance"])

        # Exit
        if position and params["exit_on_mid_close"]:
            if position["direction"] == "long" and close_ < position["entry_price"]:
                return {"exit": True, "exit_reason": "failure_close"}
            if position["direction"] == "short" and close_ > position["entry_price"]:
                return {"exit": True, "exit_reason": "failure_close"}
            return {}

        if position:
            return {}

        # Long breakout + retest
        broke_above = bars[i - 1]["high"] > prev_hi
        retested_long = low_ <= (prev_hi + tol) and close_ > prev_hi

        if broke_above and retested_long:
            entry = close_
            stop = min(low_, prev_hi) - float(params["stop_buffer_points"])
            risk = max(0.01, entry - stop)
            tp = entry + (risk * float(params["reward_r"]))
            return {
                "enter": "long",
                "entry_price": entry,
                "stop_loss": stop,
                "take_profit": tp,
                "entry_reason": "breakout_retest_long",
            }

        # Short breakout + retest
        broke_below = bars[i - 1]["low"] < prev_lo
        retested_short = high_ >= (prev_lo - tol) and close_ < prev_lo

        if broke_below and retested_short:
            entry = close_
            stop = max(high_, prev_lo) + float(params["stop_buffer_points"])
            risk = max(0.01, stop - entry)
            tp = entry - (risk * float(params["reward_r"]))
            return {
                "enter": "short",
                "entry_price": entry,
                "stop_loss": stop,
                "take_profit": tp,
                "entry_reason": "breakout_retest_short",
            }

        return {}