"""
JINNI ZERO — Jinni Elastic
================================
Streak reversal fade with fixed SL + fixed TP.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
from backend.strategies.base import BaseStrategy


class JinniElastic(BaseStrategy):
    strategy_id = "jinni_elastic"
    name = "Jinni Elastic"
    description = (
        "Streak reversal fade. After N same-direction bars, the next opposite "
        "candle triggers a fade entry on the following bar's open. "
        "Fixed-point SL and fixed-point TP from fill price."
    )
    version = "1.2.0"
    min_lookback = 0

    parameters = {
        "streak_size": {
            "type": "number", "label": "Streak Size",
            "default": 2, "min": 2, "max": 10, "step": 1,
            "help": "Number of consecutive same-direction bars required "
                    "BEFORE the reversal candle.",
        },
        "sl_points": {
            "type": "number", "label": "Fixed SL (points)",
            "default": 16.0, "min": 0.25, "max": 500.0, "step": 0.25,
        },
        "tp_points": {
            "type": "number", "label": "Fixed TP (points)",
            "default": 24.0, "min": 0.25, "max": 1000.0, "step": 0.25,
        },
        "debug": {
            "type": "boolean", "label": "Debug Prints",
            "default": True,
        },
    }

    def build_indicators(self, params): return []

    def on_init(self, ctx):
        s = ctx.state
        s["dir_history"] = []
        s["_sig_count"] = 0

    def on_bar(self, ctx):
        s = ctx.state
        p = ctx.params
        bar = ctx.bar
        i = ctx.index

        o = float(bar["open"])
        c = float(bar["close"])

        streak_size = int(p.get("streak_size", 2))
        sl_points   = float(p.get("sl_points", 16.0))
        tp_points   = float(p.get("tp_points", 24.0))
        debug       = bool(p.get("debug", True))

        # direction
        if c > o:   d = 1
        elif c < o: d = -1
        else:       d = 0

        # rolling history (ALWAYS update, even in trade)
        s["dir_history"].append(d)
        if len(s["dir_history"]) > streak_size + 1:
            s["dir_history"] = s["dir_history"][-(streak_size + 1):]

        # in position → just hold
        if ctx.position.has_position:
            return {"signal": "HOLD"}

        hist = s["dir_history"]
        if len(hist) < streak_size + 1:
            return None

        reversal_dir = hist[-1]
        if reversal_dir == 0:
            return None

        streak_dir = -reversal_dir
        for j in range(-streak_size - 1, -1):
            if hist[j] != streak_dir:
                return None

        # ─── PATTERN MATCHED ──────────────────────────────────
        sig = "BUY" if reversal_dir == 1 else "SELL"
        s["_sig_count"] += 1

        if debug:
            print(f"[JinniElastic] bar#{i} hist={hist} → {sig} "
                  f"(signal #{s['_sig_count']})")

        # 🔑 use ABSOLUTE prices computed from current close as estimate.
        # most engines expect absolute sl/tp, not offsets.
        if reversal_dir == 1:   # long
            sl_price = c - sl_points
            tp_price = c + tp_points
        else:                   # short
            sl_price = c + sl_points
            tp_price = c - tp_points

        return {
            "signal": sig,
            "sl": sl_price,
            "tp": tp_price,
            # belt + suspenders — pass offsets too in case engine prefers them
            "sl_offset": sl_points,
            "tp_offset": tp_points,
            "sl_points": sl_points,
            "tp_points": tp_points,
        }