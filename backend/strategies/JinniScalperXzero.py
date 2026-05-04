"""
╔══════════════════════════════════════════════════════════════════╗
║  JINNI SCALPER X-ZERO                                          ║
║  ─────────────────────────────────────────────────────────────  ║
║  Entry MA:     HMA 200 (2-bar confirm above/below)             ║
║  Candle:       Must match direction (bull=long, bear=short)    ║
║  Gating:       HMA 200 (lock after close, unlock on cross)    ║
║  SL:           MA Snapshot (HMA 200, signal bar value)         ║
║  TP:           MA Cross exit (HMA 21)                          ║
║  Ambiguous:    Conservative (SL first) — handled by engine     ║
║  Entry:        Next bar OPEN — handled by engine               ║
║  Commission:   Handled by engine                               ║
║  Spread:       Handled by engine                               ║
║                                                                ║
║  Strategy outputs ONLY: BUY / SELL / HOLD / CLOSE + SL        ║
║  Engine handles EVERYTHING else.                               ║
╚══════════════════════════════════════════════════════════════════╝
"""
from __future__ import annotations
import math
from backend.strategies.base import BaseStrategy


# ==========================================================
# LEGACY HMA PRECOMPUTE (1:1 from backtest_server.py)
# ==========================================================
def _wma(closes, period):
    n = len(closes); p = period; out = [None] * n
    if p < 1 or n < p: return out
    denom = p * (p + 1) / 2.0; ws = 0.0; s = 0.0
    for j in range(p):
        s += closes[j]; ws += closes[j] * (j + 1)
    out[p - 1] = ws / denom
    for i in range(p, n):
        ws = ws + p * closes[i] - s
        s = s + closes[i] - closes[i - p]
        out[i] = ws / denom
    return out


def _hma(closes, period):
    n = len(closes); p = period
    half = p // 2; sq = int(math.floor(math.sqrt(p)))
    out = [None] * n
    if half < 1 or sq < 1: return out
    wf = _wma(closes, p); wh = _wma(closes, half)
    diff = [None] * n; ds = None
    for i in range(n):
        if wf[i] is not None and wh[i] is not None:
            diff[i] = 2.0 * wh[i] - wf[i]
            if ds is None: ds = i
    if ds is None: return out
    vd = []; vm = []
    for i in range(ds, n):
        if diff[i] is not None: vd.append(diff[i]); vm.append(i)
        else: break
    if len(vd) < sq: return out
    final = _wma(vd, sq)
    for j in range(len(final)):
        if final[j] is not None: out[vm[j]] = final[j]
    return out


# ==========================================================
# STRATEGY
# ==========================================================
class JinniScalperXZero(BaseStrategy):
    strategy_id  = "jinni_scalper_xzero"
    name         = "Jinni Scalper X-Zero"
    description  = (
        "HMA200 2-bar confirm + candle direction | "
        "Gating HMA200 | SL Snapshot HMA200 | TP Cross HMA21"
    )
    version      = "1.0"
    min_lookback = 0  # start as soon as HMA is ready (same as legacy)

    def get_parameter_schema(self):
        return {}  # hardcoded — no knobs

    def build_indicators(self, params):
        return []  # we precompute ourselves using legacy HMA math

    def on_init(self, ctx):
        closes = [float(b["close"]) for b in ctx.bars]
        ctx.state["hma200"] = _hma(closes, 200)
        ctx.state["hma21"]  = _hma(closes, 21)
        ctx.state["regime"]           = "neutral"
        ctx.state["bc"]               = 0
        ctx.state["bc2"]              = 0
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

        # candle direction
        bull = c > o
        bear = c < o

        # MA values at this bar
        hma200 = st["hma200"][i] if i < len(st["hma200"]) else None
        hma21  = st["hma21"][i]  if i < len(st["hma21"])  else None

        # wait for MAs to be ready (same as legacy)
        if hma200 is None or hma21 is None:
            return None

        # ─────────────────────────────────────────────────────
        # 1) GATING: detect trade close → lock direction
        # ─────────────────────────────────────────────────────
        last_ct = int(st.get("last_trade_count", 0))
        cur_ct  = len(ctx.trades) if isinstance(ctx.trades, list) else 0
        if cur_ct > last_ct:
            d = str(ctx.trades[-1].get("direction", "")).lower()
            if d == "long":  st["long_locked"] = True
            if d == "short": st["short_locked"] = True
            st["last_trade_count"] = cur_ct

        # ─────────────────────────────────────────────────────
        # 2) GATING: unlock when price crosses HMA200
        # ─────────────────────────────────────────────────────
        if st.get("long_locked")  and c < hma200: st["long_locked"]  = False
        if st.get("short_locked") and c > hma200: st["short_locked"] = False

        # ─────────────────────────────────────────────────────
        # 3) TP EXIT: MA Cross vs HMA21
        #    long exits when close < hma21
        #    short exits when close > hma21
        # ─────────────────────────────────────────────────────
        if ctx.position.has_position:
            d = ctx.position.direction
            if d == "long" and c < hma21:
                return {"signal": "CLOSE", "close_reason": "MA_TP_EXIT"}
            if d == "short" and c > hma21:
                return {"signal": "CLOSE", "close_reason": "MA_TP_EXIT"}
            return None  # hold — engine checks SL hit via wicks

        # ─────────────────────────────────────────────────────
        # 4) SIGNAL: 2-bar confirm above/below HMA200
        #    + candle direction must match trade direction
        #    (buys only on bull candles, sells only on bear)
        # ─────────────────────────────────────────────────────
        above = c > hma200
        below = c < hma200

        regime = st.get("regime", "neutral")
        bc     = int(st.get("bc", 0))
        bc2    = int(st.get("bc2", 0))

        # regime reset (legacy)
        if regime == "above" and not above:
            regime = "neutral"; bc = 0
        elif regime == "below" and not below:
            regime = "neutral"; bc2 = 0

        sig = None

        # LONG: 2 consecutive bars above HMA200 + bull candle
        if regime != "below":
            if above and bull:
                bc += 1
            else:
                bc = 0
            if bc >= 2:
                sig = "long"
                regime = "above"
                bc = 0

        # SHORT: 2 consecutive bars below HMA200 + bear candle
        if sig is None and regime != "above":
            if below and bear:
                bc2 += 1
            else:
                bc2 = 0
            if bc2 >= 2:
                sig = "short"
                regime = "below"
                bc2 = 0

        # candle direction post-filter (legacy require_candle_confirm)
        if sig == "long"  and not bull: sig = None
        if sig == "short" and not bear: sig = None

        # gating filter
        if sig == "long"  and st.get("long_locked"):  sig = None
        if sig == "short" and st.get("short_locked"): sig = None

        # save state
        st["regime"] = regime
        st["bc"]     = bc
        st["bc2"]    = bc2

        # ─────────────────────────────────────────────────────
        # 5) FIRE SIGNAL
        #    SL = HMA200 on THIS bar (signal bar)
        #    Engine enters NEXT bar open
        #    → at entry time this SL = "prev bar HMA200"
        #    → matches legacy prev_sl_ma_val behavior
        # ─────────────────────────────────────────────────────
        if sig == "long":
            return {"signal": "BUY", "sl": float(hma200)}

        if sig == "short":
            return {"signal": "SELL", "sl": float(hma200)}

        return None