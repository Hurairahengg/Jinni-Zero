"""
JINNI ZERO — Jinni Continuum
=============================
Pure price-action momentum continuation strategy.

ENTRY
  BUY:  N consecutive bull candles (close > open) → enter next bar open
  SELL: N consecutive bear candles (close < open) → enter next bar open

STOP LOSS
  BUY:  Last (Nth) bull candle's LOW
  SELL: Last (Nth) bear candle's HIGH
  (absolute SL — engine computes risk from fill price)

TAKE PROFIT
  R-multiple of risk (configurable, default 1R)
  (engine computes at fill time)

NO CANDLE REUSE (toggleable)
  When ON: candles used for signal confirmation AND candles during
  the trade CANNOT be reused for the next signal. After a trade
  closes, counting starts completely fresh.

  Example (confirm_bars=2):
    Bar 1: bull (count=1)
    Bar 2: bull (count=2) → BUY signal fires
    Bar 3: trade opens at open, TP hit → trade closes
    Bars 1, 2, 3 are ALL used → next count starts from bar 4

  When OFF: only the signal resets the counter. The exit bar
  itself CAN be counted toward the next signal.

No indicators required — pure candle structure.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.strategies.base import BaseStrategy


class JinniContinuum(BaseStrategy):
    # ── Metadata ───────────────────────────────────────────────
    strategy_id = "jinni_continuum"
    name = "Jinni Continuum"
    description = (
        "Momentum continuation: N consecutive bull/bear candles → "
        "entry at next bar open, SL at last candle's low/high, "
        "TP at configurable R-multiple. Optional candle reuse prevention."
    )
    version = "1.0.0"
    min_lookback = 0  # no indicators, pure price action

    # ==========================================================
    # PARAMETERS
    # ==========================================================
    parameters = {
        "confirm_bars": {
            "type": "number",
            "label": "Confirmation Bars",
            "default": 2,
            "min": 1,
            "max": 10,
            "step": 1,
            "help": "Consecutive same-direction candles needed before entry.",
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
            "help": (
                "When ON, all candles used for signal + trade are consumed. "
                "Must wait for completely fresh candles after trade closes."
            ),
        },
    }

    # ==========================================================
    # INDICATORS — none needed
    # ==========================================================
    def build_indicators(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        return []

    # ==========================================================
    # INIT
    # ==========================================================
    def on_init(self, ctx: Any) -> None:
        s = ctx.state
        s["bull_count"] = 0
        s["bear_count"] = 0
        s["last_used_bar"] = -1        # last bar index consumed by signal/trade
        s["_last_trade_count"] = 0     # for detecting new trade closes

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
        r_multiple = float(p.get("r_multiple", 1.0))
        no_reuse = bool(p.get("no_reuse", True))

        # ══════════════════════════════════════════════════════
        # UPDATE LAST USED BAR FROM CLOSED TRADES
        #
        # If a trade closed since last check, mark the exit bar
        # as the last used bar (prevents reuse of exit bar and
        # all bars before it that were part of the trade).
        # ══════════════════════════════════════════════════════
        if no_reuse:
            trades = ctx.trades
            last_count = s.get("_last_trade_count", 0)
            if len(trades) > last_count:
                last_trade = trades[-1]
                exit_bar = last_trade.get("exit_bar", i)
                s["last_used_bar"] = exit_bar
                # Reset counters — fresh start after trade
                s["bull_count"] = 0
                s["bear_count"] = 0
            s["_last_trade_count"] = len(trades)

        # ══════════════════════════════════════════════════════
        # IN POSITION → HOLD
        # Engine handles SL/TP exits.
        # ══════════════════════════════════════════════════════
        if ctx.position.has_position:
            return {"signal": "HOLD"}

        # ══════════════════════════════════════════════════════
        # CANDLE REUSE CHECK
        #
        # If no_reuse is ON and this bar is at or before the
        # last used bar, skip it entirely.
        # ══════════════════════════════════════════════════════
        if no_reuse and i <= s.get("last_used_bar", -1):
            s["bull_count"] = 0
            s["bear_count"] = 0
            return None

        # ══════════════════════════════════════════════════════
        # COUNT CONSECUTIVE CANDLES
        #
        # Bull: close > open → increment bull, reset bear
        # Bear: close < open → increment bear, reset bull
        # Doji: close == open → reset both (not directional)
        # ══════════════════════════════════════════════════════
        if bull:
            s["bull_count"] = s.get("bull_count", 0) + 1
            s["bear_count"] = 0
        elif bear:
            s["bear_count"] = s.get("bear_count", 0) + 1
            s["bull_count"] = 0
        else:
            # Doji — breaks the streak
            s["bull_count"] = 0
            s["bear_count"] = 0
            return None

        # ══════════════════════════════════════════════════════
        # CHECK SIGNAL
        # ══════════════════════════════════════════════════════
        sig = None
        sl_price = None

        # ── BUY: N consecutive bull candles ───────────────────
        if s["bull_count"] >= confirm_bars:
            sig = "BUY"
            sl_price = l  # last bull candle's low

            # Reset counters
            s["bull_count"] = 0
            s["bear_count"] = 0

            # Mark signal bar as used
            if no_reuse:
                s["last_used_bar"] = i

        # ── SELL: N consecutive bear candles ──────────────────
        elif s["bear_count"] >= confirm_bars:
            sig = "SELL"
            sl_price = h  # last bear candle's high

            # Reset counters
            s["bull_count"] = 0
            s["bear_count"] = 0

            # Mark signal bar as used
            if no_reuse:
                s["last_used_bar"] = i

        if sig is None:
            return None

        # ══════════════════════════════════════════════════════
        # BUILD SIGNAL
        #
        # SL: absolute price (candle low/high)
        # TP: engine-computed R-multiple at fill time
        #
        # Engine flow at next bar open:
        #   1. entry_price = next bar open
        #   2. risk = abs(entry_price - sl_price)
        #   3. tp = entry_price + risk * R  (long)
        #        or entry_price - risk * R  (short)
        #   4. spread applied to all three
        # ══════════════════════════════════════════════════════
        return {
            "signal": sig,
            "sl": sl_price,
            "tp_mode": "r_multiple",
            "tp_r": r_multiple,
        }