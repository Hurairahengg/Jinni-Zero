"""
LEGACY HMA200 Gate + SL Snapshot + TP Cross HMA21
Signal-only interface. Engine handles all execution.

Rules (matching legacy exactly):
  - 2-bar confirm: price above/below HMA200
  - BOTH confirm bars must be BULL candles (for long) or BEAR candles (for short)
  - Signal fires on bar close → engine enters NEXT bar open
  - SL snapshot = HMA200 value on SIGNAL bar
  - TP exit = close crosses HMA21 against position
  - Gating: lock direction after trade close, unlock when price crosses HMA200
  - NO lookback gap — strategy starts as soon as HMA is ready (same as legacy)
"""
from __future__ import annotations
import math
from backend.strategies.base import BaseStrategy


def _precompute_wma(closes, period):
    n = len(closes); p = period; out = [None] * n
    if p < 1 or n < p: return out
    denom = p * (p + 1) / 2.0; ws = 0.0; s = 0.0
    for j in range(p): s += closes[j]; ws += closes[j] * (j + 1)
    out[p - 1] = ws / denom
    for i in range(p, n):
        ws = ws + p * closes[i] - s; s = s + closes[i] - closes[i - p]
        out[i] = ws / denom
    return out


def _precompute_hma(closes, period):
    n = len(closes); p = period
    half = p // 2; sq = int(math.floor(math.sqrt(p)))
    out = [None] * n
    if half < 1 or sq < 1: return out
    wma_full = _precompute_wma(closes, p)
    wma_half = _precompute_wma(closes, half)
    diff = [None] * n; diff_start = None
    for i in range(n):
        if wma_full[i] is not None and wma_half[i] is not None:
            diff[i] = 2.0 * wma_half[i] - wma_full[i]
            if diff_start is None: diff_start = i
    if diff_start is None: return out
    valid_diff = []; valid_map = []
    for i in range(diff_start, n):
        if diff[i] is not None: valid_diff.append(diff[i]); valid_map.append(i)
        else: break
    if len(valid_diff) < sq: return out
    wma_final = _precompute_wma(valid_diff, sq)
    for j in range(len(wma_final)):
        if wma_final[j] is not None: out[valid_map[j]] = wma_final[j]
    return out


class LegacyHMA200GateSnapshotTP21(BaseStrategy):
    strategy_id  = "TheRealJinniScalper"
    name         = "Perfected Jinni Scalper (legacy) HMA200 gate + SL snapshot + TP cross HMA21"
    description  = "2-bar bull/bear confirm, next open entry, gating HMA200, SL snapshot, TP cross HMA21."
    version      = "1.0"

    # ✅ NO artificial lookback — strategy handles MA readiness internally
    # Legacy starts signal gen as soon as HMA is ready (~bar 214).
    # Setting min_lookback=250 was causing a 36-bar gap → missed early trades.
    min_lookback = 0

    def get_parameter_schema(self):
        return {}

    def build_indicators(self, params):
        return []

    def on_init(self, ctx):
        closes = [float(b["close"]) for b in ctx.bars]
        ctx.state["hma200"] = _precompute_hma(closes, 200)
        ctx.state["hma21"]  = _precompute_hma(closes, 21)

        ctx.state["regime"]   = "neutral"
        ctx.state["bc"]       = 0    # bull confirm counter (for long)
        ctx.state["bc2"]      = 0    # bear confirm counter (for short)
        ctx.state["long_locked"]      = False
        ctx.state["short_locked"]     = False
        ctx.state["last_trade_count"] = 0

    def on_bar(self, ctx):
        i = ctx.index
        if i is None or i < 1:
            return None

        st  = ctx.state
        bar = ctx.bar

        c = float(bar["close"])
        o = float(bar["open"])

        # ✅ EXPLICIT candle direction (this is the core fix)
        bull = c > o    # green candle
        bear = c < o    # red candle
        # if c == o: neither bull nor bear → no signal can fire

        hma200 = st["hma200"][i] if i < len(st["hma200"]) else None
        hma21  = st["hma21"][i]  if i < len(st["hma21"])  else None

        # ✅ Wait for MAs to be ready (same as legacy: skips bars where MA is None)
        if hma200 is None or hma21 is None:
            return None

        # ──────────────────────────────────────────────────────────
        # 1) Detect trade close → apply gating lock (legacy)
        # ──────────────────────────────────────────────────────────
        last_ct = int(st.get("last_trade_count", 0))
        cur_ct  = len(ctx.trades) if isinstance(ctx.trades, list) else 0
        if cur_ct > last_ct:
            last_d = str(ctx.trades[-1].get("direction", "")).lower()
            if last_d == "long":
                st["long_locked"] = True
            elif last_d == "short":
                st["short_locked"] = True
            st["last_trade_count"] = cur_ct

        # ──────────────────────────────────────────────────────────
        # 2) Gating unlock (legacy)
        # ──────────────────────────────────────────────────────────
        if st.get("long_locked") and c < hma200:
            st["long_locked"] = False
        if st.get("short_locked") and c > hma200:
            st["short_locked"] = False

        # ──────────────────────────────────────────────────────────
        # 3) If in position → check TP MA cross exit
        # ──────────────────────────────────────────────────────────
        if ctx.position.has_position:
            d = ctx.position.direction
            if d == "long" and c < hma21:
                return {"signal": "CLOSE", "close_reason": "MA_TP_EXIT"}
            if d == "short" and c > hma21:
                return {"signal": "CLOSE", "close_reason": "MA_TP_EXIT"}
            # Hold — let engine check SL hit via wicks
            return None

        # ──────────────────────────────────────────────────────────
        # 4) Signal generation (ONLY when flat — same as legacy)
        #
        #    Legacy skips this entire block when:
        #      state != "flat" or pending_signal is not None
        #
        #    Strategy equivalent: we only reach here when
        #      ctx.position.has_position is False
        #      (engine handles pending_signal blocking internally)
        # ──────────────────────────────────────────────────────────

        above = c > hma200
        below = c < hma200

        regime = st.get("regime", "neutral")
        bc     = int(st.get("bc", 0))
        bc2    = int(st.get("bc2", 0))

        # Legacy regime reset
        if regime == "above" and not above:
            regime = "neutral"
            bc = 0
        elif regime == "below" and not below:
            regime = "neutral"
            bc2 = 0

        sig = None

        # ── LONG: 2-bar confirm above HMA200 + BULL candles ─────
        # ✅ Both confirm bars must be:
        #    - close > hma200 (above)
        #    - close > open (bull candle)
        if regime != "below":
            if above and bull:
                bc += 1
            else:
                bc = 0
            if bc >= 2:
                sig = "long"
                regime = "above"
                bc = 0

        # ── SHORT: 2-bar confirm below HMA200 + BEAR candles ────
        # ✅ Both confirm bars must be:
        #    - close < hma200 (below)
        #    - close < open (bear candle)
        if sig is None and regime != "above":
            if below and bear:
                bc2 += 1
            else:
                bc2 = 0
            if bc2 >= 2:
                sig = "short"
                regime = "below"
                bc2 = 0

        # ✅ EXPLICIT candle direction post-filter (legacy require_candle_confirm)
        # This is redundant for above_all_mas (already inside bc logic),
        # but matches legacy exactly — legacy applies this as a separate filter.
        if sig == "long" and not bull:
            sig = None
        if sig == "short" and not bear:
            sig = None

        # ── Gating filter (legacy) ───────────────────────────────
        if sig == "long" and st.get("long_locked"):
            sig = None
        if sig == "short" and st.get("short_locked"):
            sig = None

        # Save state
        st["regime"] = regime
        st["bc"]     = bc
        st["bc2"]    = bc2

        # ── Fire signal ──────────────────────────────────────────
        # SL = HMA200 on THIS bar (signal bar).
        # Engine will enter on NEXT bar open.
        # So at entry time, this SL is effectively "prev bar HMA200"
        # — exactly matching legacy prev_sl_ma_val behavior.
        if sig == "long":
            sl = float(hma200)
            return {"signal": "BUY", "sl": sl}

        if sig == "short":
            sl = float(hma200)
            return {"signal": "SELL", "sl": sl}

        return None