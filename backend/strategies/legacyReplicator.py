"""
JINNI ZERO — Legacy Replicator Strategy
========================================
Produces IDENTICAL results to Legacy Mode (backtest_server.py).

This is a VERIFICATION TOOL:
  1. Configure Legacy mode with your desired settings
  2. Run Legacy backtest, note results
  3. Switch to Strategy mode, select LegacyReplicator
  4. Set same parameters
  5. Results MUST match exactly

If they don't match → there's a bug in the engine.

Uses engine-computed SL/TP (Phase 3) so the engine computes
SL/TP at fill time from next bar's open — exactly matching Legacy.

Uses engine-level MA cross exits (Phase 4) so the engine checks
MA crosses on each bar — exactly matching Legacy timing.

Signal logic replicates Legacy's:
  - above_all_mas: 2-bar confirmation + regime tracking
  - ma_cross: fast/slow crossover
  - trend_filter: close vs longest MA crossover
  - candle direction confirmation
  - trade gating (one-per-direction lock)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.strategies.base import BaseStrategy


class LegacyReplicator(BaseStrategy):
    strategy_id = "legacy_replicator"
    name = "Legacy Replicator (Verification)"
    description = (
        "Replicates Legacy Mode (backtest_server.py) exactly. "
        "Use to verify Strategy Loader produces identical results. "
        "Set the same MA, SL, TP, gating, and candle confirm "
        "parameters as Legacy, then compare trade logs."
    )
    version = "1.0"
    min_lookback = 0

    parameters = {
        "entry_mode": {
            "type": "enum",
            "label": "Entry Mode",
            "options": ["above_all_mas", "ma_cross", "trend_filter"],
            "default": "above_all_mas",
            "help": "Must match Legacy Entry Condition dropdown.",
        },
        "ma1_type": {
            "type": "enum",
            "label": "MA 1 Type",
            "options": ["HMA", "EMA", "SMA", "WMA"],
            "default": "HMA",
        },
        "ma1_period": {
            "type": "number",
            "label": "MA 1 Period",
            "default": 21,
            "min": 2,
            "max": 500,
            "step": 1,
        },
        "ma2_enabled": {
            "type": "boolean",
            "label": "Enable MA 2",
            "default": False,
            "help": "Enable second MA (required for ma_cross entry mode).",
        },
        "ma2_type": {
            "type": "enum",
            "label": "MA 2 Type",
            "options": ["HMA", "EMA", "SMA", "WMA"],
            "default": "EMA",
        },
        "ma2_period": {
            "type": "number",
            "label": "MA 2 Period",
            "default": 55,
            "min": 2,
            "max": 500,
            "step": 1,
        },
        "require_candle_confirm": {
            "type": "boolean",
            "label": "Require Candle Direction",
            "default": True,
            "help": "Entry candle must match trade direction (Legacy default: ON).",
        },
        "sl_mode": {
            "type": "enum",
            "label": "SL Mode",
            "options": ["fixed", "ma_snapshot", "ma_cross"],
            "default": "fixed",
        },
        "sl_fixed_pts": {
            "type": "number",
            "label": "SL Fixed Points",
            "default": 8,
            "min": 0.25,
            "step": 0.25,
            "help": "Only used when SL Mode = fixed.",
        },
        "sl_ma_type": {
            "type": "enum",
            "label": "SL MA Type",
            "options": ["EMA", "HMA", "SMA", "WMA"],
            "default": "EMA",
            "help": "Only used when SL Mode = ma_snapshot or ma_cross.",
        },
        "sl_ma_period": {
            "type": "number",
            "label": "SL MA Period",
            "default": 50,
            "min": 2,
            "max": 500,
            "step": 1,
        },
        "tp_mode": {
            "type": "enum",
            "label": "TP Mode",
            "options": ["r_multiple", "ma_cross"],
            "default": "r_multiple",
        },
        "tp_r": {
            "type": "number",
            "label": "R Multiple",
            "default": 2,
            "min": 0.5,
            "max": 20,
            "step": 0.5,
            "help": "Only used when TP Mode = r_multiple.",
        },
        "tp_ma_type": {
            "type": "enum",
            "label": "TP MA Type",
            "options": ["EMA", "HMA", "SMA", "WMA"],
            "default": "EMA",
            "help": "Only used when TP Mode = ma_cross.",
        },
        "tp_ma_period": {
            "type": "number",
            "label": "TP MA Period",
            "default": 9,
            "min": 2,
            "max": 500,
            "step": 1,
        },
        "gating_enabled": {
            "type": "boolean",
            "label": "Trade Gating",
            "default": False,
            "help": "Lock direction until price crosses gating MA.",
        },
        "gating_ma_type": {
            "type": "enum",
            "label": "Gating MA Type",
            "options": ["HMA", "EMA", "SMA", "WMA"],
            "default": "HMA",
        },
        "gating_ma_period": {
            "type": "number",
            "label": "Gating MA Period",
            "default": 21,
            "min": 2,
            "max": 500,
            "step": 1,
        },
    }

    # ==========================================================
    # INDICATORS — tell engine what to precompute
    # ==========================================================
    def build_indicators(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        specs = []

        # Entry MA 1 (always)
        specs.append({
            "key": "ma1",
            "kind": params["ma1_type"],
            "period": int(params["ma1_period"]),
            "source": "close",
        })

        # Entry MA 2 (optional)
        if params.get("ma2_enabled", False):
            specs.append({
                "key": "ma2",
                "kind": params["ma2_type"],
                "period": int(params["ma2_period"]),
                "source": "close",
            })

        # SL MA (for ma_snapshot and ma_cross modes)
        if params["sl_mode"] in ("ma_snapshot", "ma_cross"):
            specs.append({
                "key": "sl_ma",
                "kind": params["sl_ma_type"],
                "period": int(params["sl_ma_period"]),
                "source": "close",
            })

        # TP MA (for ma_cross mode)
        if params["tp_mode"] == "ma_cross":
            specs.append({
                "key": "tp_ma",
                "kind": params["tp_ma_type"],
                "period": int(params["tp_ma_period"]),
                "source": "close",
            })

        # Gating MA
        if params.get("gating_enabled", False):
            specs.append({
                "key": "gating_ma",
                "kind": params["gating_ma_type"],
                "period": int(params["gating_ma_period"]),
                "source": "close",
            })

        return specs

    # ==========================================================
    # INIT — set up legacy state tracking
    # ==========================================================
    def on_init(self, ctx: Any) -> None:
        s = ctx.state
        # 2-bar confirmation counters (above_all_mas mode)
        s["bc"] = 0
        s["bc2"] = 0
        # Regime tracking: "neutral" | "above" | "below"
        s["regime"] = "neutral"
        # Gating locks
        s["long_locked"] = False
        s["short_locked"] = False

    # ==========================================================
    # MAIN SIGNAL LOGIC — legacy-exact replication
    # ==========================================================
    def on_bar(self, ctx: Any) -> Optional[Dict[str, Any]]:
        p = ctx.params
        s = ctx.state
        ind = ctx.indicators
        bar = ctx.bar
        i = ctx.index

        c = float(bar["close"])
        o = float(bar["open"])
        bull = c > o
        bear = c < o

        # ── Gather entry MA values ───────────────────────────
        ma_vals = []
        ma1 = ind.get("ma1")
        if ma1 is not None:
            ma_vals.append(ma1)

        ma2 = None
        if p.get("ma2_enabled", False):
            ma2 = ind.get("ma2")
            if ma2 is not None:
                ma_vals.append(ma2)

        sl_ma_val = ind.get("sl_ma")
        tp_ma_val = ind.get("tp_ma")
        gating_val = ind.get("gating_ma")

        # ── Gating unlock (legacy: checked every bar) ────────
        if p.get("gating_enabled", False) and gating_val is not None:
            if s["long_locked"] and c < gating_val:
                s["long_locked"] = False
            if s["short_locked"] and c > gating_val:
                s["short_locked"] = False

        # ══════════════════════════════════════════════════════
        # IN POSITION: check strategy-level exits
        #
        # SL/TP hit checking is done by the ENGINE (_check_exit).
        # We only handle MA CROSS exits here, because Legacy's
        # engine checks MA crosses in _check_exit and the
        # strategy engine also checks them via engine_sl_ma_key /
        # engine_tp_ma_key stored on the trade.
        #
        # HOWEVER: for MA cross SL/TP, we ALSO set engine keys
        # at entry time, so the engine handles it automatically.
        # This means we can just HOLD here — the engine does
        # the MA cross exit check for us.
        #
        # The only case we need strategy-level CLOSE is if we
        # want to exit for a reason the engine doesn't know about.
        # For Legacy replication, the engine handles everything.
        # ══════════════════════════════════════════════════════
        # ══════════════════════════════════════════════════════
        # IN POSITION: engine handles SL/TP
        # Bar-close model: TP/SL checked on FUTURE bars only.
        # Engine handles all exit logic.
        # ══════════════════════════════════════════════════════
        if ctx.position.has_position:
            return {"signal": "HOLD"}

        # ══════════════════════════════════════════════════════
        # FLAT: signal generation (legacy-exact)
        # ══════════════════════════════════════════════════════

        # Skip if any entry MA not ready
        if not ma_vals or any(v is None for v in ma_vals):
            s["bc"] = 0
            s["bc2"] = 0
            return None

        ab = all(c > v for v in ma_vals)  # close above ALL MAs
        bl = all(c < v for v in ma_vals)  # close below ALL MAs

        # ── Regime tracking (legacy-exact) ────────────────────
        if s["regime"] == "above" and not ab:
            s["regime"] = "neutral"
            s["bc"] = 0
        elif s["regime"] == "below" and not bl:
            s["regime"] = "neutral"
            s["bc2"] = 0

        sig = None
        entry_mode = p["entry_mode"]

        # ── above_all_mas: 2-bar confirmation ─────────────────
        if entry_mode == "above_all_mas":
            # Long signal
            if s["regime"] != "below":
                if ab and bull:
                    s["bc"] += 1
                else:
                    s["bc"] = 0
                if s["bc"] >= 2:
                    sig = "BUY"
                    s["regime"] = "above"
                    s["bc"] = 0

            # Short signal (only if no long signal fired)
            if sig is None and s["regime"] != "above":
                if bl and bear:
                    s["bc2"] += 1
                else:
                    s["bc2"] = 0
                if s["bc2"] >= 2:
                    sig = "SELL"
                    s["regime"] = "below"
                    s["bc2"] = 0

        # ── ma_cross: fast/slow crossover ─────────────────────
        elif entry_mode == "ma_cross" and len(ma_vals) >= 2 and i > 0:
            prev_ma1 = None
            prev_ma2 = None
            ma1_series = ctx.ind_series.get("ma1")
            ma2_series = ctx.ind_series.get("ma2")
            if ma1_series and i - 1 < len(ma1_series):
                prev_ma1 = ma1_series[i - 1]
            if ma2_series and i - 1 < len(ma2_series):
                prev_ma2 = ma2_series[i - 1]

            if None not in (ma1, ma2, prev_ma1, prev_ma2):
                if prev_ma1 <= prev_ma2 and ma1 > ma2:
                    sig = "BUY"
                elif prev_ma1 >= prev_ma2 and ma1 < ma2:
                    sig = "SELL"

        # ── trend_filter: close vs longest MA crossover ───────
        elif entry_mode == "trend_filter" and i > 0:
            # Longest MA = last in ma_vals list
            # Legacy uses mv[-1] which is the last MA
            longest_key = "ma2" if p.get("ma2_enabled", False) else "ma1"
            lm = ma_vals[-1]  # current bar's longest MA

            longest_series = ctx.ind_series.get(longest_key)
            prev_lm = None
            if longest_series and i - 1 < len(longest_series):
                prev_lm = longest_series[i - 1]

            prev_c = float(ctx.bars[i - 1]["close"]) if i > 0 else c

            if lm is not None and prev_lm is not None:
                if prev_c <= prev_lm and c > lm and bull:
                    sig = "BUY"
                elif prev_c >= prev_lm and c < lm and bear:
                    sig = "SELL"

        # ── Candle direction confirmation (legacy-exact) ──────
        if p.get("require_candle_confirm", True):
            if sig == "BUY" and not bull:
                sig = None
            if sig == "SELL" and not bear:
                sig = None

        # ── Gating filter (legacy-exact) ──────────────────────
        if sig == "BUY" and p.get("gating_enabled", False) and s["long_locked"]:
            sig = None
        if sig == "SELL" and p.get("gating_enabled", False) and s["short_locked"]:
            sig = None

        # ── No signal ────────────────────────────────────────
        if sig is None:
            return None

        # ══════════════════════════════════════════════════════
        # BUILD SIGNAL with engine-computed SL/TP
        #
        # The strategy tells the ENGINE how to compute SL/TP
        # at fill time (next bar's open). This matches Legacy
        # exactly because Legacy also computes SL/TP at fill.
        # ══════════════════════════════════════════════════════
        result = {"signal": sig}

        # ── SL ────────────────────────────────────────────────
        sl_mode = p["sl_mode"]

        if sl_mode == "fixed":
            result["sl_mode"] = "fixed"
            result["sl_pts"] = float(p.get("sl_fixed_pts", 8))

        elif sl_mode == "ma_snapshot":
            # Legacy uses prev_sl_ma_val which = signal bar's SL MA
            # That's the current bar's SL MA value (we're on the signal bar)
            if sl_ma_val is not None:
                result["sl_mode"] = "ma_snapshot"
                result["sl_ma_val"] = sl_ma_val
            else:
                # No SL MA available — skip this trade
                # (Legacy would also skip: risk=None → valid_entry=False)
                return None

        elif sl_mode == "ma_cross":
            # MA cross SL: use MA snapshot for initial risk calculation,
            # AND tell engine to check MA cross on each bar for exit
            if sl_ma_val is not None:
                result["sl_mode"] = "ma_snapshot"
                result["sl_ma_val"] = sl_ma_val
                # Engine will check this MA series each bar for cross exit
                result["engine_sl_ma_key"] = "sl_ma"
            else:
                return None

        # ── TP ────────────────────────────────────────────────
        tp_mode = p["tp_mode"]

        if tp_mode == "r_multiple":
            result["tp_mode"] = "r_multiple"
            result["tp_r"] = float(p.get("tp_r", 2))

        elif tp_mode == "ma_cross":
            # No fixed TP level — engine checks MA cross for exit
            result["engine_tp_ma_key"] = "tp_ma"

        # ── Gating: set lock after trade opens ────────────────
        # Legacy locks direction AFTER trade closes, not opens.
        # The engine doesn't have gating — we handle it in on_bar.
        # We just need to set the lock when we know a trade closed.
        #
        # But wait — we can't set the lock here because the trade
        # hasn't happened yet (it's a pending signal). We need to
        # check in the NEXT on_bar call whether our last trade
        # was a win/loss and set the lock.
        #
        # Actually, Legacy sets the lock unconditionally after ANY
        # trade close in the direction. Let's check ctx.trades to
        # see if the last trade closed and lock accordingly.
        self._update_gating_locks(ctx)

        return result

    # ==========================================================
    # GATING LOCK MANAGEMENT
    # ==========================================================
    def _update_gating_locks(self, ctx: Any) -> None:
        """
        Legacy sets gating lock after EVERY trade close.
        We check if a new trade appeared in ctx.trades since last check.
        """
        if not ctx.params.get("gating_enabled", False):
            return

        s = ctx.state
        trades = ctx.trades
        last_checked = s.get("_gating_last_trade_count", 0)

        if len(trades) > last_checked:
            # New trades closed since last check
            for t in trades[last_checked:]:
                if t["direction"] == "long":
                    s["long_locked"] = True
                elif t["direction"] == "short":
                    s["short_locked"] = True

        s["_gating_last_trade_count"] = len(trades)