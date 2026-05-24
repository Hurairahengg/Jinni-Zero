"""
JINNI ZERO — Bollinger Band Reversal
====================================

BUY:
- Lower BB touch/rejection
- Optional next-candle confirmation
- Optional same-candle rejection
- SL below selected candle low
- TP configurable: Middle BB / Opposite BB / Fixed R / None

SELL:
- Upper BB touch/rejection
- Mirror logic
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.strategies.base import BaseStrategy


class BollingerBandReversalZero(BaseStrategy):
    strategy_id = "bb_reversal_zero"
    name = "BB Reversal Zero"
    description = (
        "Bollinger Band reversal strategy with dropdown-configurable "
        "entry mode, touch mode, SL candle, TP mode, and filters."
    )
    version = "1.2.0"
    min_lookback = 50

    # ==========================================================
    # PARAMETERS
    # ==========================================================
    parameters = {
        "bb_period": {
            "type": "number",
            "label": "BB Period",
            "default": 20,
            "min": 5,
            "max": 500,
            "step": 1,
            "help": "Bollinger Band period.",
        },
        "bb_std": {
            "type": "number",
            "label": "BB StdDev",
            "default": 2.0,
            "min": 0.5,
            "max": 5.0,
            "step": 0.1,
            "help": "Bollinger Band standard deviation multiplier.",
        },

        # ── DROPDOWNS ─────────────────────────────────────────
        "entry_mode": {
            "type": "enum",
            "label": "Entry Mode",
            "options": [
                "Next Candle Confirm",
                "Same Candle Rejection",
                "Either",
            ],
            "default": "Either",
            "help": (
                "Next Candle Confirm = previous candle touches BB, current candle confirms. "
                "Same Candle Rejection = current candle touches BB and reverses. "
                "Either = allow both."
            ),
        },
        "touch_mode": {
            "type": "enum",
            "label": "Band Touch Mode",
            "options": [
                "Wick Touch",
                "Open Touch",
                "Close Touch",
                "Body Touch",
            ],
            "default": "Wick Touch",
            "help": (
                "Wick Touch = low/high touches band. "
                "Open Touch = candle open touches band. "
                "Close Touch = candle close touches band. "
                "Body Touch = candle body touches band."
            ),
        },
        "sl_candle": {
            "type": "enum",
            "label": "SL Candle Source",
            "options": [
                "Signal Candle",
                "Setup Candle",
                "Worst Of Two",
            ],
            "default": "Signal Candle",
            "help": (
                "Signal Candle = SL from current signal candle. "
                "Setup Candle = SL from previous setup candle. "
                "Worst Of Two = BUY uses lowest low, SELL uses highest high."
            ),
        },
        "tp_mode": {
            "type": "enum",
            "label": "TP Mode",
            "options": [
                "Middle Band",
                "Opposite Band",
                "Fixed R",
                "No TP",
            ],
            "default": "Middle Band",
            "help": (
                "Middle Band = TP at BB middle. "
                "Opposite Band = TP at opposite BB. "
                "Fixed R = TP by risk multiple. "
                "No TP = SL only."
            ),
        },

        # ── BOOLEANS ──────────────────────────────────────────
        "require_setup_candle_color": {
            "type": "boolean",
            "label": "Require Setup Candle Color",
            "default": True,
            "help": (
                "For next-candle mode: BUY setup must be bearish, "
                "SELL setup must be bullish."
            ),
        },
        "require_signal_candle_color": {
            "type": "boolean",
            "label": "Require Signal Candle Color",
            "default": True,
            "help": (
                "BUY signal candle must be bullish, "
                "SELL signal candle must be bearish."
            ),
        },
        "no_reuse": {
            "type": "boolean",
            "label": "No Candle Reuse",
            "default": True,
            "help": "Prevents same signal candle from being reused immediately.",
        },
        "no_consecutive_same_side": {
            "type": "boolean",
            "label": "Block Same Direction Repeat",
            "default": False,
            "help": "Prevents BUY after BUY or SELL after SELL.",
        },

        # ── NUMBERS ───────────────────────────────────────────
        "sl_offset": {
            "type": "number",
            "label": "SL Offset Points",
            "default": 0.0,
            "min": 0.0,
            "max": 10000.0,
            "step": 0.25,
            "help": "Extra buffer below low for BUY or above high for SELL.",
        },
        "risk_reward": {
            "type": "number",
            "label": "Risk Reward",
            "default": 1.5,
            "min": 0.1,
            "max": 50.0,
            "step": 0.1,
            "help": "Only used when TP Mode = Fixed R.",
        },
    }

    # ==========================================================
    # INDICATORS
    # ==========================================================
    def build_indicators(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {
                "key": "bb",
                "kind": "BB",
                "period": int(params.get("bb_period", 20)),
                "std": float(params.get("bb_std", 2.0)),
                "source": "close",
            }
        ]

    # ==========================================================
    # INIT
    # ==========================================================
    def on_init(self, ctx: Any) -> None:
        s = ctx.state
        s["last_used_bar"] = -1
        s["_last_trade_count"] = 0
        s["last_trade_dir"] = None

    # ==========================================================
    # DROPDOWN NORMALIZERS
    # ==========================================================
    def _entry_mode(self, value: str) -> str:
        if value == "Next Candle Confirm":
            return "next"
        if value == "Same Candle Rejection":
            return "same"
        return "either"

    def _touch_mode(self, value: str) -> str:
        if value == "Open Touch":
            return "open"
        if value == "Close Touch":
            return "close"
        if value == "Body Touch":
            return "body"
        return "wick"

    def _sl_candle(self, value: str) -> str:
        if value == "Setup Candle":
            return "setup"
        if value == "Worst Of Two":
            return "worst"
        return "signal"

    def _tp_mode(self, value: str) -> str:
        if value == "Opposite Band":
            return "opposite"
        if value == "Fixed R":
            return "fixed_r"
        if value == "No TP":
            return "none"
        return "middle"

    # ==========================================================
    # BB HELPERS
    # ==========================================================
    def _extract_bb(self, bb: Any):
        """
        Returns:
            upper, middle, lower
        """

        if bb is None:
            return None, None, None

        if isinstance(bb, dict):
            upper = bb.get("upper")
            middle = bb.get("middle")
            lower = bb.get("lower")

            if middle is None:
                middle = bb.get("mid")
            if middle is None:
                middle = bb.get("basis")

            if upper is None:
                upper = bb.get("bb_upper")
            if middle is None:
                middle = bb.get("bb_middle")
            if middle is None:
                middle = bb.get("bb_mid")
            if lower is None:
                lower = bb.get("bb_lower")

            if upper is None or middle is None or lower is None:
                return None, None, None

            return float(upper), float(middle), float(lower)

        if isinstance(bb, (list, tuple)) and len(bb) >= 3:
            return float(bb[0]), float(bb[1]), float(bb[2])

        return None, None, None

    def _bb_at(self, ctx: Any, key: str, index: int):
        series = getattr(ctx, "ind_series", {}).get(key)

        if series is not None and 0 <= index < len(series):
            return self._extract_bb(series[index])

        if index == ctx.index:
            return self._extract_bb(ctx.indicators.get(key))

        return None, None, None

    def _touches_lower(
        self,
        o: float,
        h: float,
        l: float,
        c: float,
        lower: float,
        mode: str,
    ) -> bool:
        if mode == "open":
            return o <= lower
        if mode == "close":
            return c <= lower
        if mode == "body":
            return min(o, c) <= lower
        return l <= lower

    def _touches_upper(
        self,
        o: float,
        h: float,
        l: float,
        c: float,
        upper: float,
        mode: str,
    ) -> bool:
        if mode == "open":
            return o >= upper
        if mode == "close":
            return c >= upper
        if mode == "body":
            return max(o, c) >= upper
        return h >= upper

    # ==========================================================
    # TRADE STATE
    # ==========================================================
    def _update_closed_trade_state(self, ctx: Any) -> None:
        s = ctx.state
        p = ctx.params
        trades = ctx.trades

        last_count = s.get("_last_trade_count", 0)

        if len(trades) > last_count:
            for t in trades[last_count:]:
                exit_bar = t.get("exit_bar", ctx.index)

                if bool(p.get("no_reuse", True)):
                    s["last_used_bar"] = exit_bar

                tdir = t.get("direction") or t.get("side")

                if tdir in ("long", "LONG", "BUY"):
                    s["last_trade_dir"] = "BUY"
                elif tdir in ("short", "SHORT", "SELL"):
                    s["last_trade_dir"] = "SELL"

        s["_last_trade_count"] = len(trades)

    # ==========================================================
    # SL / TP
    # ==========================================================
    def _calc_sl(
        self,
        side: str,
        sl_candle: str,
        sl_offset: float,
        signal_low: float,
        signal_high: float,
        setup_low: float,
        setup_high: float,
    ) -> float:
        if side == "BUY":
            if sl_candle == "setup":
                return setup_low - sl_offset
            if sl_candle == "worst":
                return min(signal_low, setup_low) - sl_offset
            return signal_low - sl_offset

        if sl_candle == "setup":
            return setup_high + sl_offset
        if sl_candle == "worst":
            return max(signal_high, setup_high) + sl_offset
        return signal_high + sl_offset

    def _calc_tp(
        self,
        side: str,
        tp_mode: str,
        risk_reward: float,
        signal_close: float,
        sl_price: float,
        bb_upper: float,
        bb_middle: float,
        bb_lower: float,
    ) -> Optional[float]:
        if tp_mode == "none":
            return None

        if side == "BUY":
            if tp_mode == "middle":
                return bb_middle
            if tp_mode == "opposite":
                return bb_upper
            if tp_mode == "fixed_r":
                risk = signal_close - sl_price
                if risk <= 0:
                    return None
                return signal_close + (risk * risk_reward)

        if side == "SELL":
            if tp_mode == "middle":
                return bb_middle
            if tp_mode == "opposite":
                return bb_lower
            if tp_mode == "fixed_r":
                risk = sl_price - signal_close
                if risk <= 0:
                    return None
                return signal_close - (risk * risk_reward)

        return None

    # ==========================================================
    # ON BAR
    # ==========================================================
    def on_bar(self, ctx: Any) -> Optional[Dict[str, Any]]:
        s = ctx.state
        p = ctx.params
        bar = ctx.bar
        bars = ctx.bars
        i = ctx.index

        if i <= 0:
            return None

        self._update_closed_trade_state(ctx)

        if ctx.position.has_position:
            return {"signal": "HOLD"}

        if bool(p.get("no_reuse", True)) and i <= s.get("last_used_bar", -1):
            return None

        # ── Current candle ────────────────────────────────────
        o = float(bar["open"])
        h = float(bar["high"])
        l = float(bar["low"])
        c = float(bar["close"])

        bull = c > o
        bear = c < o

        # ── Previous candle ───────────────────────────────────
        prev = bars[i - 1]

        po = float(prev["open"])
        ph = float(prev["high"])
        pl = float(prev["low"])
        pc = float(prev["close"])

        prev_bull = pc > po
        prev_bear = pc < po

        # ── Params ────────────────────────────────────────────
        entry_mode = self._entry_mode(str(p.get("entry_mode", "Either")))
        touch_mode = self._touch_mode(str(p.get("touch_mode", "Wick Touch")))
        sl_candle = self._sl_candle(str(p.get("sl_candle", "Signal Candle")))
        tp_mode = self._tp_mode(str(p.get("tp_mode", "Middle Band")))

        require_setup_candle_color = bool(p.get("require_setup_candle_color", True))
        require_signal_candle_color = bool(p.get("require_signal_candle_color", True))
        no_consecutive_same_side = bool(p.get("no_consecutive_same_side", False))

        sl_offset = float(p.get("sl_offset", 0.0))
        risk_reward = float(p.get("risk_reward", 1.5))

        # ── BB values ─────────────────────────────────────────
        cur_upper, cur_middle, cur_lower = self._bb_at(ctx, "bb", i)
        prev_upper, prev_middle, prev_lower = self._bb_at(ctx, "bb", i - 1)

        if cur_upper is None or cur_middle is None or cur_lower is None:
            return None

        if prev_upper is None or prev_middle is None or prev_lower is None:
            return None

        # ── Touch detection ───────────────────────────────────
        prev_lower_touch = self._touches_lower(
            po, ph, pl, pc, prev_lower, touch_mode
        )
        prev_upper_touch = self._touches_upper(
            po, ph, pl, pc, prev_upper, touch_mode
        )

        cur_lower_touch = self._touches_lower(
            o, h, l, c, cur_lower, touch_mode
        )
        cur_upper_touch = self._touches_upper(
            o, h, l, c, cur_upper, touch_mode
        )

        # ======================================================
        # ENTRY CONDITIONS
        # ======================================================
        buy_next = False
        sell_next = False
        buy_same = False
        sell_same = False

        # Previous candle touches BB, current candle confirms
        if entry_mode in ("next", "either"):
            buy_next = prev_lower_touch
            sell_next = prev_upper_touch

            if require_setup_candle_color:
                buy_next = buy_next and prev_bear
                sell_next = sell_next and prev_bull

            if require_signal_candle_color:
                buy_next = buy_next and bull
                sell_next = sell_next and bear

        # Current candle touches BB and rejects
        if entry_mode in ("same", "either"):
            buy_same = cur_lower_touch
            sell_same = cur_upper_touch

            if require_signal_candle_color:
                buy_same = buy_same and bull
                sell_same = sell_same and bear

        sig = None

        if buy_next or buy_same:
            if not no_consecutive_same_side or s.get("last_trade_dir") != "BUY":
                sig = "BUY"

        elif sell_next or sell_same:
            if not no_consecutive_same_side or s.get("last_trade_dir") != "SELL":
                sig = "SELL"

        if sig is None:
            return None

        # ======================================================
        # SL / TP
        # ======================================================
        sl_price = self._calc_sl(
            side=sig,
            sl_candle=sl_candle,
            sl_offset=sl_offset,
            signal_low=l,
            signal_high=h,
            setup_low=pl,
            setup_high=ph,
        )

        tp_price = self._calc_tp(
            side=sig,
            tp_mode=tp_mode,
            risk_reward=risk_reward,
            signal_close=c,
            sl_price=sl_price,
            bb_upper=cur_upper,
            bb_middle=cur_middle,
            bb_lower=cur_lower,
        )

        if tp_mode != "none" and tp_price is None:
            return None

        if bool(p.get("no_reuse", True)):
            s["last_used_bar"] = i

        result = {
            "signal": sig,
            "sl": sl_price,
        }

        if tp_mode != "none":
            result["tp"] = tp_price

        return result