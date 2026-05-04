"""
LEGACY HMA200 Gate + SL Snapshot + TP Cross HMA21
Uses NEW signal-only interface (BUY/SELL/HOLD/CLOSE).

✅ FIX: NO internal pending mechanism.
Strategy fires BUY/SELL immediately when 2-bar confirm completes.
Engine handles the "enter next bar open" delay.
SL = current bar's HMA200 (which becomes "prev bar" at entry time).
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


class JinniScalper(BaseStrategy):
    strategy_id  = "JinniScalper"
    name         = "Jinni Scalper (legacy) HMA200 gate + SL snapshot + TP cross HMA21"
    description  = "2-bar confirm, enter next open, gating HMA200, SL snapshot prev HMA200, TP exit cross HMA21."
    version      = "1.0"
    min_lookback = 250

    def get_parameter_schema(self):
        return {}

    def build_indicators(self, params):
        return []

    def on_init(self, ctx):
        closes = [float(b["close"]) for b in ctx.bars]
        ctx.state["hma200"] = _precompute_hma(closes, 200)
        ctx.state["hma21"]  = _precompute_hma(closes, 21)

        # Legacy loop state (NO pending_signal — engine handles that)
        ctx.state["regime"]   = "neutral"
        ctx.state["bc"]       = 0
        ctx.state["bc2"]      = 0
        ctx.state["long_locked"]     = False
        ctx.state["short_locked"]    = False
        ctx.state["last_trade_count"] = 0

    def on_bar(self, ctx):
        i = ctx.index
        if i is None or i < 2:
            return None

        st  = ctx.state
        bar = ctx.bar
        c = float(bar["close"]); o = float(bar["open"])
        bull = c > o; bear = c < o

        hma200 = st["hma200"][i] if i < len(st["hma200"]) else None
        hma21  = st["hma21"][i]  if i < len(st["hma21"])  else None
        if hma200 is None or hma21 is None:
            return None

        # ── Detect trade close → gating locks (legacy) ───────────
        last_ct = int(st.get("last_trade_count", 0))
        cur_ct = len(ctx.trades) if isinstance(ctx.trades, list) else 0
        if cur_ct > last_ct:
            d = str(ctx.trades[-1].get("direction", "")).lower()
            if d == "long":  st["long_locked"] = True
            if d == "short": st["short_locked"] = True
            st["last_trade_count"] = cur_ct

        # ── Gating unlock (legacy) ───────────────────────────────
        if st.get("long_locked")  and c < hma200: st["long_locked"]  = False
        if st.get("short_locked") and c > hma200: st["short_locked"] = False

        # ── TP MA cross exit (legacy MA_TP_EXIT) ─────────────────
        if ctx.position.has_position:
            d = ctx.position.direction
            if d == "long" and c < hma21:
                return {"signal": "CLOSE", "close_reason": "MA_TP_EXIT"}
            if d == "short" and c > hma21:
                return {"signal": "CLOSE", "close_reason": "MA_TP_EXIT"}
            return None  # hold position, let engine check SL

        # ── Signal generation (only when flat) ───────────────────
        # NO internal pending — engine handles "enter next bar open"
        if ctx.position.has_position:
            return None

        ab = c > hma200; bl = c < hma200
        regime = st.get("regime", "neutral")
        bc  = int(st.get("bc", 0))
        bc2 = int(st.get("bc2", 0))

        # Legacy regime reset
        if regime == "above" and not ab: regime = "neutral"; bc = 0
        elif regime == "below" and not bl: regime = "neutral"; bc2 = 0

        sig = None

        # 2-bar confirm (legacy above_all_mas)
        if regime != "below":
            if ab and bull: bc += 1
            else: bc = 0
            if bc >= 2:
                sig = "long"; regime = "above"; bc = 0

        if sig is None and regime != "above":
            if bl and bear: bc2 += 1
            else: bc2 = 0
            if bc2 >= 2:
                sig = "short"; regime = "below"; bc2 = 0

        # Gating filter (legacy)
        if sig == "long"  and st.get("long_locked"):  sig = None
        if sig == "short" and st.get("short_locked"): sig = None

        st["regime"] = regime; st["bc"] = bc; st["bc2"] = bc2

        # ✅ Fire BUY/SELL immediately. Engine will:
        #    1. Store as pending_signal
        #    2. Enter at NEXT bar open
        #    3. Use hma200[i] as SL (which is "prev bar" relative to entry bar)
        if sig == "long":
            # SL = current bar HMA200 (becomes prev_sl_ma_val at entry)
            sl = float(hma200)
            return {"signal": "BUY", "sl": sl}

        if sig == "short":
            sl = float(hma200)
            return {"signal": "SELL", "sl": sl}

        return None