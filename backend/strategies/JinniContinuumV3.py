"""
JINNI ZERO — V3 Jinni Continium
================================

Renko/range-bar continuation strategy.

Based on simple consecutive candle logic, updated with:

- Trailing stop removed
- Trend MA filter added
- Only no_reuse retained
- BUY:
    - X consecutive bullish candles
    - ALL X candle closes above selected MA
- SELL:
    - X consecutive bearish candles
    - ALL X candle closes below selected MA
- SL at signal candle low/high with offset
- TP configurable:
    - No TP -> explicit tp=None
    - Fixed R -> engine r_multiple TP
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.strategies.base import BaseStrategy


class V3JinniContinium(BaseStrategy):
    strategy_id = "v3_jinni_continium"
    name = "V3 Jinni Continium"
    description = (
        "Continuation strategy using X consecutive same-direction candles "
        "with MA trend filter. BUY requires all confirmation candle closes "
        "above selected MA. SELL requires all confirmation candle closes "
        "below selected MA. Trailing removed."
    )
    version = "3.0.0"
    min_lookback = 200

    parameters = {
        "confirm_bars": {
            "type": "number",
            "label": "Confirmation Bars",
            "default": 2,
            "min": 1,
            "max": 20,
            "step": 1,
            "help": "Consecutive same-direction candles required before entry.",
        },
        "trend_ma_type": {
            "type": "enum",
            "label": "Trend MA Type",
            "options": ["SMA", "EMA", "HMA"],
            "default": "EMA",
            "help": "Moving average type used as trend filter.",
        },
        "trend_ma_period": {
            "type": "number",
            "label": "Trend MA Period",
            "default": 50,
            "min": 2,
            "max": 500,
            "step": 1,
            "help": (
                "BUY requires all confirmation candle closes above this MA. "
                "SELL requires all confirmation candle closes below this MA."
            ),
        },
        "sl_offset": {
            "type": "number",
            "label": "SL Offset (pts)",
            "default": 0,
            "min": 0,
            "max": 10000,
            "step": 0.25,
            "help": "Buffer beyond signal candle low/high for SL.",
        },
        "tp_mode": {
            "type": "enum",
            "label": "TP Mode",
            "options": ["No TP", "Fixed R"],
            "default": "No TP",
            "help": "No TP = explicit tp=None. Fixed R = engine R-multiple TP.",
        },
        "tp_r": {
            "type": "number",
            "label": "TP R Multiple",
            "default": 2,
            "min": 0.25,
            "max": 50,
            "step": 0.25,
            "help": "Only used when TP Mode = Fixed R.",
        },
        "no_reuse": {
            "type": "boolean",
            "label": "No Candle Reuse",
            "default": True,
            "help": "Bars used for signal + trade cannot count toward next signal.",
        },
    }

    # ==========================================================
    # INDICATORS
    # ==========================================================
    def build_indicators(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {
                "key": "trend_ma",
                "kind": params.get("trend_ma_type", "EMA"),
                "period": int(params.get("trend_ma_period", 50)),
                "source": "close",
            }
        ]

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
    # HELPERS
    # ==========================================================
    def _update_closed_trade_state(self, ctx: Any) -> None:
        s = ctx.state
        p = ctx.params
        trades = ctx.trades
        i = ctx.index

        last_count = s.get("_last_trade_count", 0)

        if len(trades) > last_count:
            last_trade = trades[-1]
            exit_bar = last_trade.get("exit_bar", i)

            if bool(p.get("no_reuse", True)):
                s["last_used_bar"] = exit_bar
                s["bull_count"] = 0
                s["bear_count"] = 0

        s["_last_trade_count"] = len(trades)

    def _ma_at(self, ctx: Any, index: int) -> Optional[float]:
        series = getattr(ctx, "ind_series", {}).get("trend_ma")

        if series is not None and 0 <= index < len(series):
            val = series[index]
            return float(val) if val is not None else None

        if index == ctx.index:
            val = ctx.indicators.get("trend_ma")
            return float(val) if val is not None else None

        return None

    def _all_confirm_bars_close_above_ma(
        self,
        ctx: Any,
        end_index: int,
        confirm_bars: int,
    ) -> bool:
        start_index = end_index - confirm_bars + 1

        if start_index < 0:
            return False

        for idx in range(start_index, end_index + 1):
            ma_val = self._ma_at(ctx, idx)
            if ma_val is None:
                return False

            close_val = float(ctx.bars[idx]["close"])

            if close_val <= ma_val:
                return False

        return True

    def _all_confirm_bars_close_below_ma(
        self,
        ctx: Any,
        end_index: int,
        confirm_bars: int,
    ) -> bool:
        start_index = end_index - confirm_bars + 1

        if start_index < 0:
            return False

        for idx in range(start_index, end_index + 1):
            ma_val = self._ma_at(ctx, idx)
            if ma_val is None:
                return False

            close_val = float(ctx.bars[idx]["close"])

            if close_val >= ma_val:
                return False

        return True

    # ==========================================================
    # ON BAR
    # ==========================================================
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
        tp_mode = str(p.get("tp_mode", "No TP"))
        tp_r = float(p.get("tp_r", 2))
        no_reuse = bool(p.get("no_reuse", True))

        # Update no_reuse state from closed trades
        self._update_closed_trade_state(ctx)

        # In position:
        # trailing removed. Engine handles SL/TP.
        if ctx.position.has_position:
            return {"signal": "HOLD"}

        # No candle reuse
        if no_reuse and i <= s.get("last_used_bar", -1):
            s["bull_count"] = 0
            s["bear_count"] = 0
            return None

        # Count consecutive candle direction
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

        # BUY:
        # X bullish candles AND all X closes above trend MA.
        if s["bull_count"] >= confirm_bars:
            trend_ok = self._all_confirm_bars_close_above_ma(
                ctx=ctx,
                end_index=i,
                confirm_bars=confirm_bars,
            )

            if trend_ok:
                sig = "BUY"
                sl_price = l - sl_offset

            s["bull_count"] = 0
            s["bear_count"] = 0

            if no_reuse:
                s["last_used_bar"] = i

        # SELL:
        # X bearish candles AND all X closes below trend MA.
        elif s["bear_count"] >= confirm_bars:
            trend_ok = self._all_confirm_bars_close_below_ma(
                ctx=ctx,
                end_index=i,
                confirm_bars=confirm_bars,
            )

            if trend_ok:
                sig = "SELL"
                sl_price = h + sl_offset

            s["bull_count"] = 0
            s["bear_count"] = 0

            if no_reuse:
                s["last_used_bar"] = i

        if sig is None:
            return None

        result = {
            "signal": sig,
            "sl": sl_price,
            "tp": None,
        }

        if tp_mode == "Fixed R":
            result.pop("tp", None)
            result["tp_mode"] = "r_multiple"
            result["tp_r"] = tp_r

        return result