# Repository Snapshot - Part 3 of 5

- Root folder: `/home/hurairahengg/Documents/Jinni Zero`
- You know my wholle Jinjnibacktester simulator thign whre ther is a UI bascially and then i can see  charst and stuff when i need to run simulatiosn liek i send simulatio nto my flask backend server it runs sims and then shows stast and stuff and i can load strategy and shit for now take a look we will be doing bug fixes and some validation and shit. udnerrtsnad each code and its role how it works and keep in ir conetxt i will ask u exactly wha tto do later code later duinerstood
- Total files indexed: `24`
- Files in this chunk: `6`
## Full Project Tree

```text
.gitignore
backend/__init__.py
backend/dollar_math.py
backend/engine_core.py
backend/shared.py
backend/stats_engine.py
backend/strategies/__init__.py
backend/strategies/base.py
backend/strategies/idk.py
backend/strategies/JinniContiniumV2.py
backend/strategies/JinniScalperXzero.py
backend/strategies/legacyReplicator.py
backend/strategy_api.py
backend/strategy_loader.py
backtest_server.py
bars/range_bars.py
index.html
js/backtest.js
js/chart.js
js/currency.js
js/strategy_loader.js
STRATEGY_GUIDE.txt
styles.css
test.py
```

## Files In This Chunk - Part 3

```text
backend/dollar_math.py
backend/strategies/idk.py
backend/strategies/JinniScalperXzero.py
index.html
js/currency.js
styles.css
```

## File Contents


---

## FILE: `backend/dollar_math.py`

```python
"""
JINNI ZERO — Centralized Dollar Conversion
===========================================
This is the ONE AND ONLY place where points → dollars conversion happens.

backend/dollar_math.py

Used by:
  - backtest_server.py (legacy mode)
  - engine_core.py (strategy mode)
  - live engine (future)

Formula:
  dollars = points × point_value × dollar_per_point × lot_size

Where:
  points          = price movement (exit - entry, direction-adjusted)
  point_value     = instrument tick multiplier (default 1.0)
  dollar_per_point = dollar value per 1 point per 1 lot (default 1.0)
  lot_size        = number of contracts/lots (default 1.0)

Examples:
  NQ Futures:  point_value=1, dollar_per_point=20  → 1pt × 1 lot = $20
  ES Futures:  point_value=1, dollar_per_point=50  → 1pt × 1 lot = $50
  USDJPY:      point_value=1, dollar_per_point=6.67 → 1pt × 1 lot = $6.67
  Forex std:   point_value=1, dollar_per_point=100000, lot=0.01 → 1pt = $1000
  Custom:      point_value=1, dollar_per_point=1   → 1pt × 1 lot = $1 (default)

R-multiples are computed BEFORE dollar conversion and are
independent of lot_size / point_value / dollar_per_point.
"""
from __future__ import annotations
import math


def points_to_dollars(points, lot_size=1.0, point_value=1.0, dollar_per_point=1.0):
    """Convert point-distance to dollar P&L.
    Formula: points × point_value × dollar_per_point × lot_size
    """
    return points * point_value * dollar_per_point * lot_size


def finalize_trade_pnl(
    closed: dict,
    lot_size: float = 1.0,
    point_value: float = 1.0,
    dollar_per_point: float = 1.0,
    commission: float = 0.0,
) -> None:
    """
    Compute ALL dollar + R fields on a closed trade dict (in-place).

    Order of operations:
      1. Points PnL (direction-aware)
      2. Risk in points (from SL)
      3. R-multiple (PURE — no dollars, no lot_size, no point_value)
      4. Dollar PnL (centralized conversion)
      5. Commission
      6. Net PnL
      7. MAE/MFE dollars

    This function is called by BOTH legacy and strategy engines.
    """
    d  = closed["direction"]
    ep = closed["entry_price"]
    xp = closed["exit_price"]

    # ── 1. Points PnL ────────────────────────────────────────────
    dir_sign = 1 if d == "long" else -1
    points_pnl = (xp - ep) * dir_sign

    # ── 2. Risk in points (from INITIAL SL, not trailed SL) ─────
    rp = closed.get("initial_risk_pts") or closed.get("risk_pts")
    if rp is None or rp <= 0:
        # Fallback: compute from initial_sl or sl_level
        sl = closed.get("initial_sl") or closed.get("sl_level")
        if sl is not None:
            rp = abs(ep - sl)
    if rp is None or rp <= 0:
        rp = None

    # ── 3. R-multiple (PURE — independent of dollar settings) ────
    r_mult = None
    if rp is not None and rp > 0:
        r_mult = points_pnl / rp

    # ── 4. Dollar PnL (centralized) ──────────────────────────────
    gross_dollar = points_to_dollars(points_pnl, lot_size, point_value, dollar_per_point)

    # ── 5. Commission ────────────────────────────────────────────
    net_dollar = gross_dollar - commission

    # ── 6. Risk / MAE / MFE in dollars (same conversion) ────────
    risk_dollar = points_to_dollars(rp, lot_size, point_value, dollar_per_point) if rp and rp > 0 else None
    mae_dollar  = points_to_dollars(closed.get("mae", 0), lot_size, point_value, dollar_per_point)
    mfe_dollar  = points_to_dollars(closed.get("mfe", 0), lot_size, point_value, dollar_per_point)

    # ── Write all fields ─────────────────────────────────────────
    closed.update(
        points_pnl  = round(points_pnl, 4),
        gross_pnl   = round(gross_dollar, 2),
        commission  = round(commission, 2),
        net_pnl     = round(net_dollar, 2),
        net_pnl_r   = round(r_mult, 3) if r_mult is not None else None,
        risk_pts    = round(rp, 4) if rp is not None else None,
        risk_dollar = round(risk_dollar, 2) if risk_dollar else None,
        mae_dollar  = round(mae_dollar, 2),
        mfe_dollar  = round(mfe_dollar, 2),
    )


def validate_conversion(
    points: float,
    lot_size: float,
    point_value: float,
    expected_dollars: float,
    dollar_per_point: float = 1.0,
) -> bool:
    """
    Validation helper. Use in tests to verify consistency.

    Example:
        validate_conversion(10.0, 1.0, 1.0, 200.0, 20.0)  # True (NQ)
        validate_conversion(10.0, 2.0, 1.0, 1000.0, 50.0)  # True (ES)
    """
    actual = points_to_dollars(points, lot_size, point_value, dollar_per_point)
    return abs(actual - expected_dollars) < 0.01


# ════════════════════════════════════════════════════════════════════
#  POSITION SIZING — Single source of truth
# ════════════════════════════════════════════════════════════════════

def compute_position_size(
    risk_dollars,
    stop_points,
    point_value=1.0,
    min_lot=0.01,
    max_lot=1000.0,
    lot_step=0.01,
    commission_per_lot=0.0,
    dollar_per_point=1.0,
):
    """
    Compute lot size from risk amount and stop distance.

    Formula (commission-aware):
        (stop_points × point_value × dollar_per_point × lot_size) + (commission_per_lot × lot_size) = risk_dollars
        lot_size = risk_dollars / (stop_points × point_value × dollar_per_point + commission_per_lot)

    Returns (lot_size, log_string, valid_bool).
    Rounds DOWN to nearest lot_step so we never risk more than intended.
    """
    if risk_dollars is None or risk_dollars <= 0:
        return None, f"[SIZING] SKIP risk_dollars={risk_dollars}", False
    if stop_points is None or stop_points <= 0:
        return None, f"[SIZING] SKIP stop_points={stop_points}", False
    if point_value is None or point_value <= 0:
        return None, f"[SIZING] SKIP point_value={point_value}", False
    if dollar_per_point is None or dollar_per_point <= 0:
        return None, f"[SIZING] SKIP dollar_per_point={dollar_per_point}", False

    cost_per_lot = stop_points * point_value * dollar_per_point + max(0.0, commission_per_lot)
    raw = risk_dollars / cost_per_lot

    # Round DOWN to nearest lot_step
    if lot_step > 0:
        stepped = math.floor(raw / lot_step) * lot_step
        stepped = round(stepped, 10)          # kill float-drift
    else:
        stepped = raw

    # Clamp
    final = max(min_lot, min(max_lot, stepped))

    # Verify: total cost (loss + commission) must not exceed intended risk
    actual_loss = stop_points * point_value * dollar_per_point * final
    actual_comm = max(0.0, commission_per_lot) * final
    actual_total = actual_loss + actual_comm

    if actual_total > risk_dollars * 1.1:
        log = (
            f"[SIZING] REJECT — total_cost=${actual_total:.2f} "
            f"(loss=${actual_loss:.2f} + comm=${actual_comm:.2f}) exceeds "
            f"intended=${risk_dollars:.2f} by {actual_total/risk_dollars:.1f}x  "
            f"(raw={raw:.6f} step={stepped:.4f} clamped={final:.4f})  "
            f"[min={min_lot} max={max_lot} step={lot_step}]"
        )
        return None, log, False

    log = (
        f"[SIZING] ${risk_dollars:.2f} / ({stop_points:.4f}pts × "
        f"${point_value:.2f}pv × ${dollar_per_point:.2f}dpp "
        f"+ ${max(0.0, commission_per_lot):.2f}comm) "
        f"= raw {raw:.6f} → step {stepped:.4f} → lot={final:.4f}  "
        f"actual_loss=${actual_loss:.2f} comm=${actual_comm:.2f} "
        f"total=${actual_total:.2f}  "
        f"[min={min_lot} max={max_lot} step={lot_step}]"
    )
    return final, log, True


def compute_scaling_risk(
    balance,
    scaling_per,
    scaling_risk,
    max_risk_pct=10.0,
):
    """
    Compute risk amount for scaling / compound mode.

    Formula:  risk = (balance / scaling_per) × scaling_risk
    Capped at max_risk_pct% of balance to prevent blowup.

    Returns (risk_amount, log_string).
    """
    if balance <= 0 or scaling_per <= 0 or scaling_risk <= 0:
        return 0.0, (
            f"[SCALING] SKIP bal={balance} per={scaling_per} risk={scaling_risk}"
        )

    raw = (balance / scaling_per) * scaling_risk
    cap = balance * (max_risk_pct / 100.0)
    final = round(min(raw, cap), 2)
    final = max(0.0, final)
    capped = raw > cap

    log = (
        f"[SCALING] ${balance:.2f} / ${scaling_per:.0f} × ${scaling_risk:.2f} "
        f"= ${raw:.2f}{' → CAPPED' if capped else ''} → risk=${final:.2f}  "
        f"(max {max_risk_pct}% = ${cap:.2f})"
    )
    return final, log
```

---

## FILE: `backend/strategies/idk.py`

```python
"""
JINNI ZERO — Jinni Continuum
=============================
Pure price-action momentum continuation strategy.

ENTRY
  BUY:  N consecutive bull candles (close > open) → enter next bar open
  SELL: N consecutive bear candles (close < open) → enter next bar open

STOP LOSS
  BUY:  Last (Nth) bull candle's BODY LOW (min(open, close))
  SELL: Last (Nth) bear candle's BODY HIGH (max(open, close))

TAKE PROFIT
  R-multiple of risk (configurable, default 1R)
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
        "entry at next bar close, SL at last candle's body, "
        "TP at configurable R-multiple. Optional candle reuse prevention."
    )
    version = "1.2.0"
    min_lookback = 0

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
        s["last_used_bar"] = -1
        s["_last_trade_count"] = 0

    # ==========================================================
    # ON BAR — signal generation only
    # ==========================================================
    def on_bar(self, ctx: Any) -> Optional[Dict[str, Any]]:
        s = ctx.state
        p = ctx.params
        bar = ctx.bar
        i = ctx.index

        c = float(bar["close"])
        o = float(bar["open"])

        bull = c > o
        bear = c < o

        confirm_bars = int(p.get("confirm_bars", 2))
        r_multiple = float(p.get("r_multiple", 1.0))
        no_reuse = bool(p.get("no_reuse", True))

        # ══════════════════════════════════════════════════════
        # UPDATE LAST USED BAR FROM CLOSED TRADES
        # ══════════════════════════════════════════════════════
        if no_reuse:
            trades = ctx.trades
            last_count = s.get("_last_trade_count", 0)
            if len(trades) > last_count:
                last_trade = trades[-1]
                exit_bar = last_trade.get("exit_bar", i)
                s["last_used_bar"] = exit_bar
                s["bull_count"] = 0
                s["bear_count"] = 0
            s["_last_trade_count"] = len(trades)

        # ══════════════════════════════════════════════════════
        # IN POSITION → HOLD
        # ══════════════════════════════════════════════════════
        if ctx.position.has_position:
            return {"signal": "HOLD"}

        # ══════════════════════════════════════════════════════
        # CANDLE REUSE CHECK
        # ══════════════════════════════════════════════════════
        if no_reuse and i <= s.get("last_used_bar", -1):
            s["bull_count"] = 0
            s["bear_count"] = 0
            return None

        # ══════════════════════════════════════════════════════
        # COUNT CONSECUTIVE CANDLES
        # ══════════════════════════════════════════════════════
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

        # ══════════════════════════════════════════════════════
        # CHECK SIGNAL
        # ══════════════════════════════════════════════════════
        sig = None
        sl_price = None

        if s["bull_count"] >= confirm_bars:
            sig = "BUY"
            sl_price = min(o, c)   # BODY LOW

            s["bull_count"] = 0
            s["bear_count"] = 0

            if no_reuse:
                s["last_used_bar"] = i

        elif s["bear_count"] >= confirm_bars:
            sig = "SELL"
            sl_price = max(o, c)   # BODY HIGH

            s["bull_count"] = 0
            s["bear_count"] = 0

            if no_reuse:
                s["last_used_bar"] = i

        if sig is None:
            return None

        # ══════════════════════════════════════════════════════
        # BUILD SIGNAL
        # ══════════════════════════════════════════════════════
        return {
            "signal": sig,
            "sl": sl_price,
            "tp_mode": "r_multiple",
            "tp_r": r_multiple,
        }
```

---

## FILE: `backend/strategies/JinniScalperXzero.py`

```python
"""
JINNI ZERO — JinniScalper X Zero
================================
Legacy-matched scalping strategy using:

ENTRY
- Above / below ALL MAs
- 2-bar confirmation
- Candle direction confirmation

TREND + GATING
- HMA 200

STOP LOSS
- Snapshot SL using HMA 200 (signal bar value)

TAKE PROFIT
- MA-cross exit using HMA 21

Execution is 100% engine-driven (legacy-exact):
- Entry at next bar OPEN
- SL/TP computed at FILL TIME
- MA cross exits handled by engine
"""

from __future__ import annotations
from typing import Optional, Dict, Any, List
from backend.strategies.base import BaseStrategy


class JinniScalperXZero(BaseStrategy):
    # ── Metadata ───────────────────────────────────────────────
    strategy_id = "jinni_scalper_x_zero"
    name = "JinniScalper X Zero"
    description = (
        "200 HMA trend + gating, 2-bar confirmation entry, "
        "snapshot SL on HMA 200, TP via HMA 21 cross. "
        "Legacy-matched execution for Renko / range bars."
    )
    version = "1.0.0"
    min_lookback = 210  # safely covers HMA 200

    # ==========================================================
    # PARAMETERS (kept minimal on purpose)
    # ==========================================================
    parameters = {}  # Fixed strategy — no knobs, just logic

    # ==========================================================
    # INDICATOR PLAN (engine-precomputed)
    # ==========================================================
    def build_indicators(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            # Trend + SL snapshot + gating MA
            dict(key="hma_200", kind="HMA", period=200, source="close"),
            # TP MA (cross exit)
            dict(key="hma_21", kind="HMA", period=21, source="close"),
        ]

    # ==========================================================
    # INIT
    # ==========================================================
    def on_init(self, ctx: Any) -> None:
        s = ctx.state
        s["bc_long"] = 0
        s["bc_short"] = 0
        s["regime"] = "neutral"   # neutral | above | below
        s["long_locked"] = False
        s["short_locked"] = False
        s["_gating_last_trade_count"] = 0

    # ==========================================================
    # ON BAR
    # ==========================================================
    def on_bar(self, ctx: Any) -> Optional[Dict[str, Any]]:
        s = ctx.state
        bar = ctx.bar
        ind = ctx.indicators
        bars = ctx.bars
        i = ctx.index

        c = float(bar["close"])
        o = float(bar["open"])
        bull = c > o
        bear = c < o

        hma_200 = ind.get("hma_200")
        hma_21 = ind.get("hma_21")

        # ── Safety: indicators not ready ───────────────────────
        if hma_200 is None or hma_21 is None:
            s["bc_long"] = 0
            s["bc_short"] = 0
            return None

        # ======================================================
        # GATING UNLOCK (legacy-exact)
        # ======================================================
        if s["long_locked"] and c < hma_200:
            s["long_locked"] = False
        if s["short_locked"] and c > hma_200:
            s["short_locked"] = False

        # ======================================================
        # IN POSITION → HOLD
        #
        # SL/TP hits + MA cross exits are handled by ENGINE
        # ======================================================
        if ctx.position.has_position:
            return {"signal": "HOLD"}

        # ======================================================
        # ENTRY LOGIC — ABOVE/BELOW ALL MA (only HMA 200 here)
        # ======================================================
        above = c > hma_200
        below = c < hma_200

        # ── Regime reset (legacy behavior) ────────────────────
        if s["regime"] == "above" and not above:
            s["regime"] = "neutral"
            s["bc_long"] = 0
        elif s["regime"] == "below" and not below:
            s["regime"] = "neutral"
            s["bc_short"] = 0

        sig = None

        # ── LONG side ─────────────────────────────────────────
        if s["regime"] != "below":
            if above and bull:
                s["bc_long"] += 1
            else:
                s["bc_long"] = 0

            if s["bc_long"] >= 2:
                sig = "BUY"
                s["regime"] = "above"
                s["bc_long"] = 0

        # ── SHORT side ────────────────────────────────────────
        if sig is None and s["regime"] != "above":
            if below and bear:
                s["bc_short"] += 1
            else:
                s["bc_short"] = 0

            if s["bc_short"] >= 2:
                sig = "SELL"
                s["regime"] = "below"
                s["bc_short"] = 0

        # ── Gating filter ─────────────────────────────────────
        if sig == "BUY" and s["long_locked"]:
            return None
        if sig == "SELL" and s["short_locked"]:
            return None

        if sig is None:
            return None

        # ======================================================
        # BUILD SIGNAL — ENGINE COMPUTED SL / TP
        # ======================================================
        out = {
            "signal": sig,
            # ── Snapshot SL on HMA 200 (signal bar value) ──
            "sl_mode": "ma_snapshot",
            "sl_ma_val": hma_200,
            # ── TP via MA cross (HMA 21) ──
            "engine_tp_ma_key": "hma_21",
        }

        # Tell engine to also check MA cross for SL if needed
        out["engine_sl_ma_key"] = None  # snapshot SL only (not cross)

        # ======================================================
        # GATING LOCK AFTER TRADE CLOSE (legacy behavior)
        # ======================================================
        self._update_gating_locks(ctx)

        return out

    # ==========================================================
    # GATING LOCK MANAGEMENT (legacy-exact)
    # ==========================================================
    def _update_gating_locks(self, ctx: Any) -> None:
        s = ctx.state
        trades = ctx.trades
        last = s.get("_gating_last_trade_count", 0)

        if len(trades) > last:
            for t in trades[last:]:
                if t["direction"] == "long":
                    s["long_locked"] = True
                elif t["direction"] == "short":
                    s["short_locked"] = True

        s["_gating_last_trade_count"] = len(trades)
```

---

## FILE: `index.html`

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>JINNI TERMINAL</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;600;700;800&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="styles.css"/>
  <script src="https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js"></script>
</head>
<body>
  <div class="scanline"></div>

  <!-- ═══ HEADER ══════════════════════════════════════════════════════ -->
  <header class="header">
    <div class="header-left">
      <div class="logo">
        <span class="logo-bracket">[</span>
        <span class="logo-text">JINNI<span class="logo-accent">ZERO</span></span>
        <span class="logo-bracket">]</span>
      </div>
      <nav class="tab-nav">
        <button class="tab-btn active" data-tab="chart"><span class="tab-icon">▦</span>CHART</button>
        <button class="tab-btn" data-tab="backtest"><span class="tab-icon">◈</span>BACKTEST</button>
      </nav>
    </div>

    <div class="header-right" id="chartHeaderRight">
      <div class="ticker-info">
        <select class="symbol-select" id="chartSymbol">
          <option value="NQ" selected>NQ</option>
          <option value="SPX">SPX</option>
          <option value="XAUUSD">XAUUSD</option>
          <option value="EURUSD">EURUSD</option>
          <option value="USDJPY">USDJPY</option>
          <option value="GBPUSD">GBPUSD</option>
          <option value="AUDUSD">AUDUSD</option>
          <option value="USDCAD">USDCAD</option>
        </select>
        <span class="ticker-price" id="tickerPrice">—</span>
        <span class="ticker-change" id="tickerChange">—</span>
      </div>

      <div class="interval-group" id="intervalGroup"></div>

      <div class="status-dot-wrap">
        <span class="status-dot"></span>
        <span class="status-label">LIVE</span>
      </div>
    </div>

    <div class="header-right" id="backtestHeaderRight" style="display:none;">
      <span class="bt-header-label">STRATEGY BACKTESTER</span>
      <div style="display:flex; gap:6px; align-items:center; margin-left:16px;">
        <span style="font-size:0.55rem; letter-spacing:0.12em; color:var(--text-dim); font-family:var(--mono); font-weight:700;">GRAPH STEP</span>
        <select id="bt_graphStep" class="bt-select bt-select-sm" style="width:60px; padding:4px 6px; font-size:0.65rem;">
          <option value="1" selected>1</option>
          <option value="2">2</option>
          <option value="4">4</option>
          <option value="8">8</option>
          <option value="16">16</option>
          <option value="32">32</option>
        </select>
      </div>
    </div>
  </header>

  <!-- ═══ CHART TAB ════════════════════════════════════════════════════ -->
  <main class="layout tab-panel active" id="tabChart">
    <aside class="sidebar">
      <div class="sidebar-block"><div class="sidebar-label">OPEN</div><div class="sidebar-value" id="statOpen">—</div></div>
      <div class="sidebar-block"><div class="sidebar-label">HIGH</div><div class="sidebar-value bull" id="statHigh">—</div></div>
      <div class="sidebar-block"><div class="sidebar-label">LOW</div><div class="sidebar-value bear" id="statLow">—</div></div>
      <div class="sidebar-block"><div class="sidebar-label">CLOSE</div><div class="sidebar-value" id="statClose">—</div></div>
      <div class="sidebar-divider"></div>
      <div class="sidebar-block"><div class="sidebar-label">VOLUME</div><div class="sidebar-value" id="statVolume">—</div></div>
      <div class="sidebar-block"><div class="sidebar-label">CHANGE</div><div class="sidebar-value" id="statChange">—</div></div>
      <div class="sidebar-divider"></div>
      <div class="sidebar-block"><div class="sidebar-label">CANDLES</div><div class="sidebar-value" id="statCandles">—</div></div>
    </aside>

    <section class="chart-area">
      <div class="chart-container" id="chartContainer"></div>
      <div class="chart-footer">
        <span class="chart-hint">↕ scroll to zoom · drag to pan · hover for OHLC</span>
        <span class="chart-powered">powered by Lightweight Charts™</span>
      </div>
    </section>
  </main>

  <!-- ═══ BACKTEST TAB ═════════════════════════════════════════════════ -->
  <main class="layout tab-panel" id="tabBacktest" style="display:none;">
    <div class="bt-root">
      <!-- ── LEFT CONFIG ──────────────────────────────────────────── -->
      <aside class="bt-config-panel">

        <!-- MODE -->
        <div class="bt-section">
          <div class="bt-section-title">MODE</div>
          <div class="bt-field">
            <label class="bt-label">Backtest Mode</label>
            <select class="bt-select" id="bt_mode">
              <option value="manual" selected>Manual Mode (Legacy)</option>
              <option value="strategy">Load Strategy Mode</option>
            </select>
          </div>
          <div class="bt-field">
            <label class="bt-label">Point Value</label>
            <input class="bt-input" type="number" id="bt_pointValue"
                  value="1" min="0.01" max="10000" step="0.01"/>
            <span class="bt-hint" id="bt_pvHint">Instrument tick multiplier (usually 1)</span>
          </div>
          <div class="bt-field">
            <label class="bt-label">Dollar Per Point (per lot)</label>
            <input class="bt-input" type="number" id="bt_dollarPerPoint"
                  value="1" min="0.0001" max="1000000" step="0.01"/>
            <div class="bt-toggle-label" id="bt_dppHint">1pt move × 1.00 lot = $1.00 P&L</div>
          </div>
          <div class="bt-toggle-label">
            Manual Mode keeps your existing legacy controls. Load Strategy Mode auto-loads Python strategy schemas + engine settings.
          </div>
        </div>

        <!-- shared -->
        <div class="bt-section">
          <div class="bt-section-title">DATA SOURCE</div>

          <div class="bt-field">
            <label class="bt-label">Symbol</label>
            <select class="bt-select" id="bt_symbol">
              <option value="NQ" selected>NQ</option>
              <option value="SPX">SPX</option>
              <option value="XAUUSD">XAUUSD</option>
              <option value="EURUSD">EURUSD</option>
              <option value="USDJPY">USDJPY</option>
              <option value="GBPUSD">GBPUSD</option>
              <option value="AUDUSD">AUDUSD</option>
              <option value="USDCAD">USDCAD</option>
            </select>
          </div>

          <div class="bt-field">
            <label class="bt-label">Range Bar Size</label>
            <select class="bt-select" id="bt_range"></select>
          </div>

          <div class="bt-field">
            <label class="bt-label">Data Slicing</label>
            <select class="bt-select" id="bt_sliceMode">
              <option value="bar_count" selected>Bar Count</option>
              <option value="date_range">Date Range</option>
            </select>
          </div>

          <div id="bt_barRangeWrap">
            <div class="bt-field">
              <label class="bt-label">Backtest Range</label>
              <select class="bt-select" id="bt_barRange">
                <option value="500">Last 500 bars</option>
                <option value="1000" selected>Last 1000 bars</option>
                <option value="2500">Last 2500 bars</option>
                <option value="5000">Last 5000 bars</option>
                <option value="0">Full dataset</option>
              </select>
            </div>
          </div>

          <div id="bt_dateRangeWrap" style="display:none;">
            <div class="bt-field">
              <label class="bt-label">Start Date</label>
              <input type="datetime-local" class="bt-input" id="bt_startDate"/>
            </div>
            <div class="bt-field">
              <label class="bt-label">End Date</label>
              <input type="datetime-local" class="bt-input" id="bt_endDate"/>
            </div>
          </div>

          <div class="bt-field">
            <label class="bt-label">Starting Capital ($)</label>
            <input class="bt-input" type="number" id="bt_startingCapital" value="10000" min="0" step="500"/>
          </div>
          
          <div class="bt-field">
            <label class="bt-label">Position Sizing</label>
            <select class="bt-select" id="bt_sizingMode">
              <option value="fixed" selected>Fixed Lot</option>
              <option value="risk_pct">Risk %</option>
              <option value="risk_per_trade">Risk Per Trade</option>
            </select>
          </div>

          <div id="bt_fixedLotWrap">
            <div class="bt-field">
              <label class="bt-label">Lot Size</label>
              <input class="bt-input" type="number" id="bt_lotSize" value="1.0" min="0.01" step="0.01"/>
              <div class="bt-toggle-label" id="bt_lotHint">1 pt = $1.00</div>
            </div>
          </div>

          <div id="bt_riskPctWrap" style="display:none;">
            <div class="bt-field">
              <label class="bt-label">Risk Per Trade (%)</label>
              <input class="bt-input" type="number" id="bt_riskPct" value="1.0" min="0.01" max="100" step="0.1"/>
              <div class="bt-toggle-label" id="bt_riskHint">
                $1,000 balance × 1% = $10 risk per trade
              </div>
            </div>
          </div>
          <div id="bt_riskPerTradeWrap" style="display:none;">
            <div class="bt-field">
              <label class="bt-label">Base Risk ($)</label>
              <input class="bt-input" type="number" id="bt_fixedRisk" value="10" min="0.01" step="0.5"/>
              <div class="bt-toggle-label" id="bt_fixedRiskHint">
                Every trade risks exactly $10.00 (requires SL)
              </div>
            </div>

            <div class="bt-field">
              <label class="bt-label">Scaling Mode</label>
              <div class="bt-toggle-row">
                <label class="bt-toggle"><input type="checkbox" id="bt_scalingEnabled"/><span class="bt-toggle-slider"></span></label>
                <span class="bt-toggle-label">Scale risk with equity growth</span>
              </div>
            </div>

            <div id="bt_scalingWrap" style="display:none;">
              <div class="bt-field">
                <label class="bt-label">For Every ($)</label>
                <input class="bt-input" type="number" id="bt_scalingPer" value="100" min="1" step="10"/>
              </div>
              <div class="bt-field">
                <label class="bt-label">Risk ($)</label>
                <input class="bt-input" type="number" id="bt_scalingRisk" value="1" min="0.01" step="0.25"/>
              </div>
              <div class="bt-toggle-label" id="bt_scalingHint">
                $10,000 bal ÷ $100 × $1.00 = $100.00 risk/trade
              </div>
            </div>
          </div>
        </div>

        <!-- MANUAL MODE ONLY -->
        <div class="bt-section bt-manual-only">
          <div class="bt-section-title">MOVING AVERAGES</div>
          <div class="bt-ma-row" id="bt_ma_0">
            <select class="bt-select bt-select-sm" data-ma-type>
              <option value="EMA">EMA</option><option value="HMA" selected>HMA</option>
              <option value="SMA">SMA</option><option value="WMA">WMA</option>
            </select>
            <input class="bt-input bt-input-sm" type="number" data-ma-period value="21" min="2" max="500"/>
            <button class="bt-icon-btn bt-remove-ma" title="Remove">✕</button>
          </div>
          <button class="bt-add-btn" id="bt_addMa">+ ADD MA</button>
        </div>

        <div class="bt-section bt-manual-only">
          <div class="bt-section-title">ENTRY LOGIC</div>
          <div class="bt-field">
            <label class="bt-label">Entry Condition</label>
            <select class="bt-select" id="bt_entry">
              <option value="above_all_mas">Price above ALL MAs (2-bar confirm)</option>
              <option value="ma_cross">MA Cross (fast × slow)</option>
              <option value="trend_filter">Trend Filter (close vs longest MA)</option>
            </select>
          </div>

          <div class="bt-field">
            <label class="bt-label">Require Candle Direction Confirmation</label>
            <div class="bt-toggle-row">
              <label class="bt-toggle"><input type="checkbox" id="bt_candleConfirm" checked/><span class="bt-toggle-slider"></span></label>
              <span class="bt-toggle-label">Entry candle must match trade direction</span>
            </div>
          </div>
        </div>

        <div class="bt-section bt-manual-only">
          <div class="bt-section-title">TRADE GATING</div>
          <div class="bt-field">
            <label class="bt-label">One Trade Per MA</label>
            <div class="bt-toggle-row">
              <label class="bt-toggle"><input type="checkbox" id="bt_gatingEnabled"/><span class="bt-toggle-slider"></span></label>
              <span class="bt-toggle-label">Lock direction until price crosses gating MA</span>
            </div>
          </div>

          <div id="bt_gating_wrap" style="display:none;">
            <div class="bt-field">
              <label class="bt-label">Gating MA Type</label>
              <select class="bt-select" id="bt_gating_ma_type">
                <option value="HMA" selected>HMA</option>
                <option value="EMA">EMA</option>
                <option value="SMA">SMA</option>
              </select>
            </div>

            <div class="bt-field">
              <label class="bt-label">Gating MA Length</label>
              <input class="bt-input" type="number" id="bt_gating_ma_length" value="21" min="2" max="500"/>
            </div>
          </div>
        </div>

        <div class="bt-section bt-manual-only">
          <div class="bt-section-title">STOP LOSS</div>
          <div class="bt-field">
            <label class="bt-label">SL Mode</label>
            <div class="bt-radio-group">
              <label class="bt-radio"><input type="radio" name="sl_mode" value="fixed" checked/><span>Fixed pts</span></label>
              <label class="bt-radio"><input type="radio" name="sl_mode" value="ma_cross"/><span>MA Cross</span></label>
              <label class="bt-radio"><input type="radio" name="sl_mode" value="ma_snapshot"/><span>MA Snapshot</span></label>
            </div>
          </div>

          <div class="bt-field" id="bt_sl_fixed_wrap">
            <label class="bt-label">SL Size (points)</label>
            <input class="bt-input" type="number" id="bt_sl_fixed" value="8" min="0.25" step="0.25"/>
          </div>

          <div id="bt_sl_ma_wrap" style="display:none;">
            <div class="bt-field">
              <label class="bt-label">SL MA Type</label>
              <select class="bt-select" id="bt_sl_ma_type">
                <option value="EMA" selected>EMA</option>
                <option value="HMA">HMA</option>
                <option value="SMA">SMA</option>
              </select>
            </div>

            <div class="bt-field">
              <label class="bt-label">SL MA Length</label>
              <input class="bt-input" type="number" id="bt_sl_ma_length" value="50" min="2" max="500"/>
            </div>
          </div>

          <div class="bt-field">
            <label class="bt-label">Ambiguous Bar (SL+TP hit)</label>
            <select class="bt-select" id="bt_ambiguousMode">
              <option value="conservative" selected>Conservative (SL first)</option>
              <option value="optimistic">Optimistic (TP first)</option>
              <option value="nearest_to_open">Nearest to Open</option>
            </select>
          </div>
        </div>

        <div class="bt-section bt-manual-only">
          <div class="bt-section-title">TAKE PROFIT</div>
          <div class="bt-field">
            <label class="bt-label">TP Mode</label>
            <div class="bt-radio-group">
              <label class="bt-radio"><input type="radio" name="tp_mode" value="r_multiple" checked/><span>R Multiple</span></label>
              <label class="bt-radio"><input type="radio" name="tp_mode" value="ma_cross"/><span>MA Cross</span></label>
            </div>
          </div>

          <div class="bt-field" id="bt_tp_r_wrap">
            <label class="bt-label">R Multiple</label>
            <div class="bt-r-group">
              <button class="bt-r-btn active" data-r="2">2R</button>
              <button class="bt-r-btn" data-r="3">3R</button>
              <button class="bt-r-btn" data-r="4">4R</button>
              <button class="bt-r-btn" data-r="5">5R</button>
            </div>
          </div>

          <div id="bt_tp_ma_wrap" style="display:none;">
            <div class="bt-field">
              <label class="bt-label">TP MA Type</label>
              <select class="bt-select" id="bt_tp_ma_type">
                <option value="EMA" selected>EMA</option>
                <option value="HMA">HMA</option>
                <option value="SMA">SMA</option>
              </select>
            </div>

            <div class="bt-field">
              <label class="bt-label">TP MA Length</label>
              <input class="bt-input" type="number" id="bt_tp_ma_length" value="9" min="2" max="500"/>
            </div>
          </div>
        </div>

        <!-- shared -->
        <div class="bt-section">
          <div class="bt-section-title">COMMISSION</div>
          <div class="bt-field">
            <label class="bt-label">Commission Per Lot ($)</label>
            <input class="bt-input" type="number" id="bt_commPerLot" value="1.25" min="0" step="0.25"/>
            <div class="bt-toggle-label" id="bt_commHint">
              1.00 lot × $1.25 = $1.25 commission/trade
            </div>
          </div>
        </div>
        <!-- SPREAD SIMULATION -->
        <div class="bt-section">
          <div class="bt-section-title">SPREAD SIMULATION</div>
          <div class="bt-field">
            <label class="bt-label">Random Spread</label>
            <div class="bt-toggle-row">
              <label class="bt-toggle"><input type="checkbox" id="bt_spreadEnabled"/><span class="bt-toggle-slider"></span></label>
              <span class="bt-toggle-label">Apply random spread per trade (realistic slippage)</span>
            </div>
          </div>

          <div id="bt_spreadWrap" style="display:none;">
            <div class="bt-field">
              <label class="bt-label">Min Spread ($)</label>
              <input class="bt-input" type="number" id="bt_spreadMin" value="0.10" min="0" step="0.01"/>
            </div>

            <div class="bt-field">
              <label class="bt-label">Max Spread ($)</label>
              <input class="bt-input" type="number" id="bt_spreadMax" value="0.50" min="0" step="0.01"/>
            </div>

            <div class="bt-field">
              <label class="bt-label">Seed (0 = random)</label>
              <input class="bt-input" type="number" id="bt_spreadSeed" value="0" min="0" step="1"/>
            </div>
          </div>
        </div>

        <!-- ═══ CURRENCY DISPLAY (display-only) ═══ -->
        <div class="bt-section" id="cc_section_manual">
          <div class="bt-section-title">CURRENCY DISPLAY</div>

          <div class="bt-field" style="flex-direction:row;align-items:center;gap:8px;">
            <input type="checkbox" id="cc_enabled"
                   style="accent-color:var(--accent,#00e5ff);"/>
            <label class="bt-label" for="cc_enabled"
                   style="margin:0;cursor:pointer;">
              Enable Currency Conversion
            </label>
          </div>

          <div id="cc_panel" style="display:none;flex-direction:column;gap:8px;">
            <div class="bt-field">
              <label class="bt-label">Currency Symbol</label>
              <select id="cc_symbol" class="bt-select">
                <option value="$" selected>$</option>
                <option value="¥">¥</option>
                <option value="€">€</option>
                <option value="£">£</option>
                <option value="₹">₹</option>
                <option value="৳">৳</option>
              </select>
            </div>

            <div class="bt-field">
              <label class="bt-label">Multiplier (1 USD = ?)</label>
              <input type="number" id="cc_multiplier" class="bt-input"
                     value="1" min="0.0001" step="0.01" />
            </div>

            <div class="bt-toggle-label"
                 style="font-size:0.54rem;color:#586f7f;">
              Display-only — does NOT affect backtest calculations.
            </div>
          </div>
        </div>

        <!-- STRATEGY MODE ONLY -->
        <div id="bt_strategyPanel" style="display:none;">
          <div class="bt-section">
            <div class="bt-section-title">LOAD STRATEGY</div>

            <div class="bt-field">
              <label class="bt-label">Available Strategy</label>
              <select class="bt-select" id="bt_strategySelect"></select>
            </div>

            <div class="bt-field">
              <label class="bt-label">Description</label>
              <div class="bt-toggle-label" id="bt_strategyDescription">Loading strategies…</div>
            </div>
          </div>

          <div class="bt-section">
            <div class="bt-section-title">STRATEGY PARAMETERS</div>
            <div id="bt_strategyParams"></div>
          </div>

          <div class="bt-section">
            <div class="bt-section-title">SIMULATION</div>

            <div class="bt-field">
              <label class="bt-label">Monte Carlo Runs</label>
              <input class="bt-input" type="number" id="bt_mcRuns" value="1000" min="0" step="100"/>
            </div>
          </div>

          <div class="bt-run-wrap">
            <button class="bt-run-btn" id="bt_strategyRunBtn">
              <span class="bt-run-icon">▶</span> RUN BACKTEST
            </button>
          </div>
        </div>

        <!-- MANUAL RUN -->
        <div class="bt-run-wrap bt-manual-only" id="bt_manualRunWrap">
          <button class="bt-run-btn" id="bt_runBtn">
            <span class="bt-run-icon">▶</span> START BACKTEST
          </button>
        </div>
      </aside>

      <!-- ── RIGHT RESULTS ─────────────────────────────────────────── -->
      <div class="bt-results-panel" id="bt_resultsPanel">
        <div class="bt-progress-wrap" id="bt_progressWrap" style="display:none;">
          <div class="bt-progress-header">
            <span class="bt-progress-label" id="bt_progressLabel">Initialising…</span>
            <span class="bt-progress-pct" id="bt_progressPct">0%</span>
          </div>
          <div class="bt-progress-track"><div class="bt-progress-bar" id="bt_progressBar" style="width:0%"></div></div>
          <div class="bt-progress-steps">
            <span class="bt-step" id="step_load">LOAD DATA</span><span class="bt-step-arrow">→</span>
            <span class="bt-step" id="step_run">RUN LOGIC</span><span class="bt-step-arrow">→</span>
            <span class="bt-step" id="step_stats">COMPUTE STATS</span><span class="bt-step-arrow">→</span>
            <span class="bt-step" id="step_charts">BUILD CHARTS</span><span class="bt-step-arrow">→</span>
            <span class="bt-step" id="step_done">DONE</span>
          </div>
          <div class="bt-live-stats" id="bt_liveStats" style="display:none;">
            <div class="bt-live-stat"><span class="bt-live-label">EQUITY</span><span class="bt-live-value" id="bt_liveEquity">—</span></div>
            <div class="bt-live-stat"><span class="bt-live-label">DRAWDOWN</span><span class="bt-live-value" id="bt_liveDD">—</span></div>
            <div class="bt-live-stat"><span class="bt-live-label">OPEN TRADE</span><span class="bt-live-value" id="bt_liveOpen">—</span></div>
            <div class="bt-live-stat"><span class="bt-live-label">LAST P&L</span><span class="bt-live-value" id="bt_lastPnl">—</span></div>
          </div>
        </div>

        <div class="bt-empty" id="bt_empty">
          <div class="bt-empty-icon">◈</div>
          <div class="bt-empty-title">No results yet</div>
          <div class="bt-empty-sub">Configure your strategy and press START BACKTEST</div>
        </div>

        <div class="bt-dashboard" id="bt_dashboard" style="display:none;">
          <div class="bt-dash-header">
            <div class="bt-dash-strategy" id="bd_strategy">—</div>
            <div class="bt-dash-meta" id="bd_meta">—</div>
          </div>

          <div class="bt-dash-section"><div class="bt-dash-section-title">CORE STATISTICS</div><div class="bt-kpi-grid" id="bd_coreKpis"></div></div>
          <div class="bt-dash-section"><div class="bt-dash-section-title">RISK METRICS</div><div class="bt-kpi-grid" id="bd_riskKpis"></div></div>
          <div class="bt-dash-section"><div class="bt-dash-section-title">PERFORMANCE RATIOS</div><div class="bt-kpi-grid" id="bd_ratioKpis"></div></div>

          <!-- ── NEW PRO STAT SECTIONS ─────────────────────────── -->
          <div class="bt-dash-section"><div class="bt-dash-section-title">TRADE ANALYSIS</div><div class="bt-kpi-grid" id="bd_tradeKpis"></div></div>
          <div class="bt-dash-section"><div class="bt-dash-section-title">TIME &amp; EXPOSURE</div><div class="bt-kpi-grid" id="bd_timeExpKpis"></div></div>
          <div class="bt-dash-section"><div class="bt-dash-section-title">PERIOD PERFORMANCE</div><div class="bt-kpi-grid" id="bd_periodKpis"></div></div>
          <div class="bt-dash-section"><div class="bt-dash-section-title">MAE / MFE</div><div class="bt-kpi-grid" id="bd_maeMfeKpis"></div></div>

          <div class="bt-dash-section"><div class="bt-dash-section-title">EQUITY CURVE</div><div class="bt-chart-card"><canvas id="cv_equity" class="bt-canvas"></canvas></div></div>
          <div class="bt-dash-section"><div class="bt-dash-section-title">DRAWDOWN CURVE</div><div class="bt-chart-card"><canvas id="cv_drawdown" class="bt-canvas"></canvas></div></div>

          <div class="bt-dash-section">
            <div class="bt-dash-section-title">ROLLING METRICS <span class="bt-dash-sub">(20-trade window)</span></div>
            <div class="bt-chart-2col">
              <div class="bt-chart-card"><div class="bt-chart-label">WIN RATE %</div><canvas id="cv_rollWr" class="bt-canvas bt-canvas-sm"></canvas></div>
              <div class="bt-chart-card"><div class="bt-chart-label">EXPECTANCY $</div><canvas id="cv_rollExp" class="bt-canvas bt-canvas-sm"></canvas></div>
              <div class="bt-chart-card"><div class="bt-chart-label">PROFIT FACTOR</div><canvas id="cv_rollPf" class="bt-canvas bt-canvas-sm"></canvas></div>
              <div class="bt-chart-card"><div class="bt-chart-label">SHARPE RATIO</div><canvas id="cv_rollSharpe" class="bt-canvas bt-canvas-sm"></canvas></div>
            </div>
          </div>

          <div class="bt-dash-section">
            <div class="bt-dash-section-title">DISTRIBUTIONS</div>
            <div class="bt-chart-2col">
              <div class="bt-chart-card"><div class="bt-chart-label">R-MULTIPLE HISTOGRAM</div><canvas id="cv_rHist" class="bt-canvas bt-canvas-sm"></canvas></div>
              <div class="bt-chart-card"><div class="bt-chart-label">TRADE DURATION (BARS)</div><canvas id="cv_durHist" class="bt-canvas bt-canvas-sm"></canvas></div>
            </div>
          </div>

          <div class="bt-dash-section">
            <div class="bt-dash-section-title">SCATTER ANALYSIS</div>
            <div class="bt-chart-2col">
              <div class="bt-chart-card"><div class="bt-chart-label">MAE vs MFE</div><canvas id="cv_maemfe" class="bt-canvas bt-canvas-sm"></canvas></div>
              <div class="bt-chart-card"><div class="bt-chart-label">TRADE # vs R-MULTIPLE</div><canvas id="cv_retScatter" class="bt-canvas bt-canvas-sm"></canvas></div>
            </div>
          </div>

          <div class="bt-dash-section">
            <div class="bt-dash-section-title">TIME BREAKDOWN</div>
            <div class="bt-chart-3col">
              <div class="bt-chart-card"><div class="bt-chart-label">BY HOUR OF DAY</div><canvas id="cv_hour" class="bt-canvas bt-canvas-sm"></canvas></div>
              <div class="bt-chart-card"><div class="bt-chart-label">BY DAY OF WEEK</div><canvas id="cv_dow" class="bt-canvas bt-canvas-sm"></canvas></div>
              <div class="bt-chart-card"><div class="bt-chart-label">BY MONTH</div><canvas id="cv_month" class="bt-canvas bt-canvas-sm"></canvas></div>
            </div>
          </div>

          <div class="bt-dash-section"><div class="bt-dash-section-title">REGIME BREAKDOWN</div><div class="bt-kpi-grid" id="bd_regimeKpis"></div></div>

          <div class="bt-dash-section">
            <div class="bt-dash-section-title">MONTE CARLO <span class="bt-dash-sub">(configurable runs)</span></div>
            <div class="bt-kpi-grid" id="bd_mcKpis"></div>

            <div class="bt-chart-2col" style="margin-top:12px;">
              <div class="bt-chart-card"><div class="bt-chart-label">SIMULATED EQUITY PATHS</div><canvas id="cv_mcPaths" class="bt-canvas bt-canvas-sm"></canvas></div>
              <div class="bt-chart-card"><div class="bt-chart-label">FINAL EQUITY DISTRIBUTION</div><canvas id="cv_mcFinal" class="bt-canvas bt-canvas-sm"></canvas></div>
            </div>

            <div class="bt-chart-2col" style="margin-top:10px;">
              <div class="bt-chart-card"><div class="bt-chart-label">MAX DRAWDOWN DISTRIBUTION</div><canvas id="cv_mcDd" class="bt-canvas bt-canvas-sm"></canvas></div>
              <div class="bt-chart-card" style="padding:16px 18px;">
                <div class="bt-chart-label" style="margin-bottom:12px;">PROBABILITY TABLE</div>
                <div id="bd_mcProb"></div>
              </div>
            </div>
          </div>

          <div class="bt-dash-section"><div class="bt-dash-section-title">COMMISSION SUMMARY</div><div class="bt-kpi-grid" id="bd_commKpis"></div></div>

          <div class="bt-dash-section" style="margin-bottom:24px;">
            <div class="bt-dash-section-title">TRADE LOG</div>
            <div class="bt-chart-card" style="padding:0; overflow:hidden;">
              <div class="bt-trade-log" id="bt_tradeLog"></div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </main>

  <script src="js/chart.js"></script>
  <script src="js/currency.js"></script>
  <script src="js/backtest.js"></script>
  <script src="js/strategy_loader.js"></script>
</body>
</html>
```

---

## FILE: `js/currency.js`

```javascript
/* ═══════════════════════════════════════════════════════════════════
   JINNI ZERO — Frontend Currency Conversion (Display-Only)
   
   DOES NOT affect backend, trade logic, equity curves, or stored values.
   All conversion is purely visual — raw USD values are preserved
   as data-raw-usd attributes on DOM elements.
   
   Auto-hooks into btRenderAnyResult to tag dollar elements after
   every backtest render. When user changes conversion settings,
   ALL tagged elements auto-update.
   
   Load AFTER backtest.js.
═══════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  // ════════════════════════════════════════════════════════════════
  //  STATE
  // ════════════════════════════════════════════════════════════════
  var state = {
    enabled: false,
    multiplier: 1,
    symbol: '$',
    decimals: 2,
  };

  var SYMBOL_DECIMALS = {
    '$': 2, '¥': 0, '€': 2, '£': 2, '₹': 2,
    '৳': 2, 'kr': 2, 'R$': 2, '₩': 0, 'CHF': 2,
  };

  var SYMBOL_LIST = Object.keys(SYMBOL_DECIMALS);

  // ════════════════════════════════════════════════════════════════
  //  FORMATTING
  // ════════════════════════════════════════════════════════════════

  function _addCommas(str) {
    var parts = str.split('.');
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    return parts.join('.');
  }

  function format(rawUsd, opts) {
    if (rawUsd == null || rawUsd === '' || rawUsd === '—') return '—';
    opts = opts || {};

    var num = typeof rawUsd === 'string' ? parseFloat(rawUsd) : rawUsd;
    if (!Number.isFinite(num)) return '—';

    var converted = state.enabled ? num * state.multiplier : num;
    var sym = opts.noSymbol ? '' : state.symbol;
    var dec = opts.decimals != null ? opts.decimals : state.decimals;
    var isNeg = converted < 0;
    var abs = Math.abs(converted);

    // Compact mode for large numbers
    if (opts.compact && abs >= 100000) {
      if (abs >= 1000000) {
        var mStr = _addCommas((abs / 1000000).toFixed(Math.min(dec, 2)));
        return (isNeg ? '-' : (opts.forceSign && num > 0 ? '+' : '')) + sym + mStr + 'M';
      }
      var kStr = _addCommas((abs / 1000).toFixed(Math.min(dec, 1)));
      return (isNeg ? '-' : (opts.forceSign && num > 0 ? '+' : '')) + sym + kStr + 'K';
    }

    var formatted = _addCommas(abs.toFixed(dec));
    var sign = isNeg ? '-' : (opts.forceSign && num > 0 ? '+' : '');
    return sign + sym + formatted;
  }

  function formatPct(val, dec) {
    if (val == null || val === '') return '—';
    var num = typeof val === 'string' ? parseFloat(val) : val;
    if (!Number.isFinite(num)) return '—';
    return (num > 0 ? '+' : '') + num.toFixed(dec != null ? dec : 2) + '%';
  }

  function formatR(val, dec) {
    if (val == null || val === '') return '—';
    var num = typeof val === 'string' ? parseFloat(val) : val;
    if (!Number.isFinite(num)) return '—';
    return (num > 0 ? '+' : '') + num.toFixed(dec != null ? dec : 2) + 'R';
  }

  function formatNum(val, dec) {
    if (val == null || val === '') return '—';
    var num = typeof val === 'string' ? parseFloat(val) : val;
    if (!Number.isFinite(num)) return '—';
    return (num < 0 ? '-' : '') + _addCommas(Math.abs(num).toFixed(dec != null ? dec : 2));
  }

  // ════════════════════════════════════════════════════════════════
  //  DOM TAGGING (stores raw USD, auto-refreshes on change)
  // ════════════════════════════════════════════════════════════════
  var DATA_ATTR = 'data-raw-usd';
  var OPTS_ATTR = 'data-currency-opts';

  function tag(el, rawUsd, opts) {
    if (!el) return;
    if (rawUsd == null || rawUsd === '') {
      el.textContent = '—';
      el.removeAttribute(DATA_ATTR);
      el.removeAttribute(OPTS_ATTR);
      return;
    }
    el.setAttribute(DATA_ATTR, String(rawUsd));
    if (opts) {
      el.setAttribute(OPTS_ATTR, JSON.stringify(opts));
    } else {
      el.removeAttribute(OPTS_ATTR);
    }
    el.textContent = format(rawUsd, opts);
  }

  function refreshAll() {
    var els = document.querySelectorAll('[' + DATA_ATTR + ']');
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      var raw = parseFloat(el.getAttribute(DATA_ATTR));
      var optsStr = el.getAttribute(OPTS_ATTR);
      var opts = null;
      if (optsStr) { try { opts = JSON.parse(optsStr); } catch (e) {} }
      el.textContent = format(raw, opts);
    }
  }

  // ════════════════════════════════════════════════════════════════
  //  SETTINGS
  // ════════════════════════════════════════════════════════════════

  function setEnabled(on) {
    state.enabled = !!on;
    if (!state.enabled) {
      state.multiplier = 1;
      state.symbol = '$';
      state.decimals = 2;
    }
    _syncUi();
    refreshAll();
  }

  function setMultiplier(val) {
    var n = parseFloat(val);
    state.multiplier = Number.isFinite(n) && n > 0 ? n : 1;
    refreshAll();
  }

  function setSymbol(sym) {
    state.symbol = sym || '$';
    state.decimals = SYMBOL_DECIMALS[state.symbol] != null
      ? SYMBOL_DECIMALS[state.symbol] : 2;
    refreshAll();
  }

  function _syncUi() {
    var toggle = document.getElementById('cc_enabled');
    var panel  = document.getElementById('cc_panel');
    var mult   = document.getElementById('cc_multiplier');
    var sym    = document.getElementById('cc_symbol');
    if (toggle) toggle.checked = state.enabled;
    if (panel)  panel.style.display = state.enabled ? 'flex' : 'none';
    if (mult)   mult.value = state.multiplier;
    if (sym)    sym.value = state.symbol;
  }

  // ════════════════════════════════════════════════════════════════
  //  STAT ID → FORMAT OPTIONS MAP
  //
  //  Every dashboard element that shows a dollar value.
  //  true = default opts, object = custom opts.
  // ════════════════════════════════════════════════════════════════
  

  // ════════════════════════════════════════════════════════════════
  //  AUTO-TAG AFTER BACKTEST RENDER
  // ════════════════════════════════════════════════════════════════

  

  // ════════════════════════════════════════════════════════════════
  //  HOOK INTO btRenderAnyResult
  // ════════════════════════════════════════════════════════════════

  function installHook() {
    var check = setInterval(function () {
      if (typeof window.btRenderAnyResult !== 'function') return;
      clearInterval(check);

      var original = window.btRenderAnyResult;
      window.btRenderAnyResult = function (data, cfg) {
        original(data, cfg);
        // backtest.js already formats via fmt() → CurrencyDisplay.format()
        // and stamps data-raw-usd on elements. This just ensures a clean
        // re-tag pass in case anything was missed.
        requestAnimationFrame(function () { refreshAll(); });
      };
    }, 200);

    setTimeout(function () { clearInterval(check); }, 10000);
  }

  // ════════════════════════════════════════════════════════════════
  //  UI WIRING
  // ════════════════════════════════════════════════════════════════

  function wireControls() {
    var toggle = document.getElementById('cc_enabled');
    var mult   = document.getElementById('cc_multiplier');
    var sym    = document.getElementById('cc_symbol');

    if (toggle) {
      toggle.addEventListener('change', function () { setEnabled(this.checked); });
    }
    if (mult) {
      mult.addEventListener('input', function () { setMultiplier(this.value); });
      mult.addEventListener('change', function () { setMultiplier(this.value); });
    }
    if (sym) {
      sym.addEventListener('change', function () { setSymbol(this.value); });
    }
    _syncUi();
  }

  // ════════════════════════════════════════════════════════════════
  //  BOOT
  // ════════════════════════════════════════════════════════════════

  function boot() {
    wireControls();
    installHook();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

  // ════════════════════════════════════════════════════════════════
  //  PUBLIC API
  // ════════════════════════════════════════════════════════════════

  window.CurrencyDisplay = {
    format: format,
    formatPct: formatPct,
    formatR: formatR,
    formatNum: formatNum,
    tag: tag,
    refreshAll: refreshAll,
    setEnabled: setEnabled,
    setMultiplier: setMultiplier,
    setSymbol: setSymbol,
    getState: function () { return state; },
    SYMBOLS: SYMBOL_LIST,
  };

})();
```

---

## FILE: `styles.css`

```css
/* ─── RESET & ROOT ─────────────────────────────────────────────────── */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg:        #080b0f;
  --bg2:       #0d1117;
  --bg3:       #111820;
  --border:    #1e2a38;
  --border2:   #243040;
  --text:      #c8d8e8;
  --text-dim:  #4a6070;
  --text-mute: #2a3a4a;
  --accent:    #00e5ff;
  --accent2:   #0095a8;
  --bull:      #00e676;
  --bull2:     #00a352;
  --bear:      #ff3d5a;
  --bear2:     #b02040;
  --mono:      'Space Mono', monospace;
  --syne:      'Syne', sans-serif;
}

html, body {
  height: 100%;
  width: 100%;
  overflow: hidden;
  background: var(--bg);
  color: var(--text);
  font-family: var(--mono);
}

/* ─── SCANLINE OVERLAY ──────────────────────────────────────────────── */
.scanline {
  pointer-events: none;
  position: fixed;
  inset: 0;
  z-index: 1000;
  background: repeating-linear-gradient(
    to bottom,
    transparent 0px,
    transparent 2px,
    rgba(0,0,0,0.08) 2px,
    rgba(0,0,0,0.08) 4px
  );
}

/* ─── HEADER ────────────────────────────────────────────────────────── */
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 56px;
  padding: 0 20px;
  border-bottom: 1px solid var(--border);
  background: var(--bg2);
  flex-shrink: 0;
  position: relative;
  z-index: 10;
}
.header::after {
  content: '';
  position: absolute;
  bottom: 0; left: 0; right: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--accent2), transparent);
  opacity: 0.5;
}
.header-left { display: flex; align-items: center; gap: 24px; }
.header-right { display: flex; align-items: center; gap: 20px; }

/* Logo */
.logo {
  font-family: var(--syne);
  font-weight: 800;
  font-size: 1.1rem;
  letter-spacing: 0.05em;
  user-select: none;
}
.logo-bracket { color: var(--accent2); }
.logo-text { color: var(--text); }
.logo-accent { color: var(--accent); }

/* ─── TAB NAV ────────────────────────────────────────────────────────── */
.tab-nav {
  display: flex;
  gap: 2px;
}
.tab-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  background: transparent;
  border: 1px solid transparent;
  color: var(--text-dim);
  font-family: var(--mono);
  font-size: 0.67rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  padding: 5px 13px;
  border-radius: 3px;
  cursor: pointer;
  transition: all 0.15s;
}
.tab-btn:hover {
  color: var(--text);
  border-color: var(--border);
}
.tab-btn.active {
  color: var(--accent);
  border-color: var(--accent);
  background: rgba(0,229,255,0.06);
  box-shadow: 0 0 8px rgba(0,229,255,0.1);
}
.tab-icon {
  font-size: 0.75rem;
  opacity: 0.7;
}

/* Backtest header label */
.bt-header-label {
  font-family: var(--syne);
  font-size: 0.75rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  color: var(--text-dim);
}

/* Ticker */
.ticker-info { display: flex; align-items: baseline; gap: 12px; }
.ticker-symbol {
  font-family: var(--syne);
  font-size: 0.85rem;
  font-weight: 700;
  color: var(--text-dim);
  letter-spacing: 0.1em;
}
.ticker-price {
  font-size: 1.1rem;
  font-weight: 700;
  color: var(--text);
  letter-spacing: 0.04em;
}
.ticker-change {
  font-size: 0.75rem;
  padding: 2px 7px;
  border-radius: 3px;
  font-weight: 700;
  letter-spacing: 0.05em;
  transition: all 0.3s;
}
.ticker-change.bull { background: rgba(0,230,118,0.12); color: var(--bull); }
.ticker-change.bear { background: rgba(255,61,90,0.12); color: var(--bear); }

/* Interval buttons */
.interval-group { display: flex; gap: 4px; }
.interval-btn {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-dim);
  font-family: var(--mono);
  font-size: 0.68rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  padding: 4px 9px;
  border-radius: 3px;
  cursor: pointer;
  transition: all 0.15s;
}
.interval-btn:hover { border-color: var(--accent2); color: var(--accent); }
.interval-btn.active {
  border-color: var(--accent);
  color: var(--accent);
  background: rgba(0,229,255,0.07);
  box-shadow: 0 0 8px rgba(0,229,255,0.15);
}

/* Status dot */
.status-dot-wrap { display: flex; align-items: center; gap: 6px; }
.status-dot {
  width: 7px; height: 7px;
  border-radius: 50%;
  background: var(--bull);
  box-shadow: 0 0 6px var(--bull);
  animation: pulse 2s ease-in-out infinite;
}
.status-label {
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.12em;
  color: var(--bull);
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50%       { opacity: 0.4; }
}

/* ─── LAYOUT (chart tab) ────────────────────────────────────────────── */
.layout {
  display: flex;
  height: calc(100vh - 56px);
  overflow: hidden;
}
.tab-panel { display: none; }
.tab-panel.active { display: flex; }

/* ─── SIDEBAR ───────────────────────────────────────────────────────── */
.sidebar {
  width: 130px;
  flex-shrink: 0;
  background: var(--bg2);
  border-right: 1px solid var(--border);
  padding: 20px 0;
  display: flex;
  flex-direction: column;
  gap: 0;
  overflow-y: auto;
}
.sidebar-block {
  padding: 12px 16px;
  border-bottom: 1px solid var(--border);
  transition: background 0.15s;
}
.sidebar-block:hover { background: var(--bg3); }
.sidebar-label {
  font-size: 0.58rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  color: var(--text-dim);
  margin-bottom: 5px;
}
.sidebar-value {
  font-size: 0.8rem;
  font-weight: 700;
  color: var(--text);
  letter-spacing: 0.03em;
  transition: color 0.3s;
}
.sidebar-value.bull { color: var(--bull); }
.sidebar-value.bear { color: var(--bear); }
.sidebar-divider { height: 1px; background: var(--border2); margin: 6px 0; }

/* ─── CHART AREA ────────────────────────────────────────────────────── */
.chart-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: var(--bg);
  min-width: 0;
  background-image:
    linear-gradient(var(--border) 1px, transparent 1px),
    linear-gradient(90deg, var(--border) 1px, transparent 1px);
  background-size: 48px 48px;
  background-position: -1px -1px;
}
.chart-container { flex: 1; position: relative; }
.chart-container > * { background: transparent !important; }
.chart-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 7px 16px;
  border-top: 1px solid var(--border);
  background: var(--bg2);
  flex-shrink: 0;
}
.chart-hint, .chart-powered {
  font-size: 0.6rem;
  color: var(--text-mute);
  letter-spacing: 0.08em;
}
.chart-powered { color: var(--accent2); opacity: 0.6; }

/* ─── SCROLLBAR ─────────────────────────────────────────────────────── */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--border2); border-radius: 2px; }

/* ─── INTERACTIVE CANVAS CURSORS ────────────────────────────────────── */
.bt-canvas-interactive { cursor: grab; }
.bt-canvas-interactive:active { cursor: grabbing; }

/* ─── INTRO ANIMATION ───────────────────────────────────────────────── */
.header, .sidebar, .chart-area { animation: fadeUp 0.5s ease both; }
.sidebar    { animation-delay: 0.1s; }
.chart-area { animation-delay: 0.2s; }
@keyframes fadeUp {
  from { opacity: 0; transform: translateY(8px); }
  to   { opacity: 1; transform: translateY(0); }
}


/* ═══════════════════════════════════════════════════════════════════════
   BACKTEST TAB STYLES
   ═══════════════════════════════════════════════════════════════════════ */

#tabBacktest { overflow: hidden; }
#tabBacktest.active { display: flex; }

/* ─── Root layout ────────────────────────────────────────────────────── */
.bt-root {
  display: flex;
  width: 100%;
  height: 100%;
  overflow: hidden;
}

/* ─── Config panel (left) ────────────────────────────────────────────── */
.bt-config-panel {
  width: 300px;
  flex-shrink: 0;
  background: var(--bg2);
  border-right: 1px solid var(--border);
  overflow-y: auto;
  padding-bottom: 24px;
  display: flex;
  flex-direction: column;
}

/* Section */
.bt-section {
  padding: 16px 18px 14px;
  border-bottom: 1px solid var(--border);
}
.bt-section-title {
  font-size: 0.56rem;
  font-weight: 700;
  letter-spacing: 0.18em;
  color: var(--text-mute);
  margin-bottom: 14px;
}

/* Fields */
.bt-field { margin-bottom: 12px; }
.bt-field:last-child { margin-bottom: 0; }
.bt-label {
  display: block;
  font-size: 0.6rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: var(--text-dim);
  margin-bottom: 6px;
}

/* Inputs */
.bt-select, .bt-input {
  width: 100%;
  background: var(--bg3);
  border: 1px solid var(--border);
  color: var(--text);
  font-family: var(--mono);
  font-size: 0.72rem;
  font-weight: 700;
  padding: 6px 10px;
  border-radius: 3px;
  outline: none;
  transition: border-color 0.15s;
  appearance: none;
  -webkit-appearance: none;
}
.bt-select:focus, .bt-input:focus { border-color: var(--accent2); }
.bt-select { cursor: pointer; background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%234a6070'/%3E%3C/svg%3E"); background-repeat: no-repeat; background-position: right 10px center; padding-right: 28px; }

.bt-input-sm, .bt-select-sm {
  width: auto;
  flex: 1;
}

/* MA rows */
.bt-ma-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 8px;
}
.bt-icon-btn {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-mute);
  font-size: 0.65rem;
  width: 26px;
  height: 26px;
  border-radius: 2px;
  cursor: pointer;
  flex-shrink: 0;
  transition: all 0.15s;
  display: flex;
  align-items: center;
  justify-content: center;
}
.bt-icon-btn:hover { border-color: var(--bear2); color: var(--bear); }
.bt-add-btn {
  display: flex;
  align-items: center;
  gap: 5px;
  background: transparent;
  border: 1px dashed var(--border2);
  color: var(--text-dim);
  font-family: var(--mono);
  font-size: 0.62rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  padding: 5px 10px;
  border-radius: 3px;
  cursor: pointer;
  width: 100%;
  justify-content: center;
  transition: all 0.15s;
  margin-top: 4px;
}
.bt-add-btn:hover { border-color: var(--accent2); color: var(--accent); }

/* Radio group */
.bt-radio-group { display: flex; flex-direction: column; gap: 6px; }
.bt-radio {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 0.67rem;
  color: var(--text-dim);
  cursor: pointer;
  font-weight: 700;
  letter-spacing: 0.04em;
}
.bt-radio input[type=radio] { accent-color: var(--accent); cursor: pointer; }
.bt-radio:has(input:checked) { color: var(--text); }

/* Toggle */
.bt-toggle-row { display: flex; align-items: center; gap: 10px; }
.bt-toggle { position: relative; display: inline-block; width: 34px; height: 18px; flex-shrink: 0; }
.bt-toggle input { opacity: 0; width: 0; height: 0; }
.bt-toggle-slider {
  position: absolute; inset: 0;
  background: var(--bg3);
  border: 1px solid var(--border2);
  border-radius: 18px;
  transition: 0.2s;
  cursor: pointer;
}
.bt-toggle-slider::before {
  content: '';
  position: absolute;
  left: 2px; top: 2px;
  width: 12px; height: 12px;
  background: var(--text-dim);
  border-radius: 50%;
  transition: 0.2s;
}
.bt-toggle input:checked + .bt-toggle-slider { background: rgba(0,229,255,0.12); border-color: var(--accent2); }
.bt-toggle input:checked + .bt-toggle-slider::before { transform: translateX(16px); background: var(--accent); }
.bt-toggle-label { font-size: 0.62rem; color: var(--text-dim); font-weight: 700; letter-spacing: 0.04em; line-height: 1.4; }

/* R multiple buttons */
.bt-r-group { display: flex; gap: 5px; }
.bt-r-btn {
  flex: 1;
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-dim);
  font-family: var(--mono);
  font-size: 0.68rem;
  font-weight: 700;
  padding: 5px 4px;
  border-radius: 3px;
  cursor: pointer;
  transition: all 0.15s;
}
.bt-r-btn:hover { border-color: var(--accent2); color: var(--accent); }
.bt-r-btn.active {
  border-color: var(--accent);
  color: var(--accent);
  background: rgba(0,229,255,0.07);
}

/* Run button */
.bt-run-wrap {
  padding: 18px 18px 0;
  margin-top: auto;
}
.bt-run-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  width: 100%;
  padding: 12px;
  background: rgba(0,229,255,0.06);
  border: 1px solid var(--accent2);
  color: var(--accent);
  font-family: var(--syne);
  font-size: 0.85rem;
  font-weight: 800;
  letter-spacing: 0.12em;
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.2s;
}
.bt-run-btn:hover {
  background: rgba(0,229,255,0.12);
  border-color: var(--accent);
  box-shadow: 0 0 20px rgba(0,229,255,0.15);
}
.bt-run-btn:active { transform: scale(0.98); }
.bt-run-btn.running {
  border-color: var(--bull2);
  color: var(--bull);
  background: rgba(0,230,118,0.06);
  pointer-events: none;
}
.bt-run-icon { font-size: 1rem; }

/* ─── Results panel (right) ──────────────────────────────────────────── */
.bt-results-panel {
  flex: 1;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: var(--bg);
  background-image:
    linear-gradient(var(--border) 1px, transparent 1px),
    linear-gradient(90deg, var(--border) 1px, transparent 1px);
  background-size: 48px 48px;
  background-position: -1px -1px;
}

/* Progress bar */
.bt-progress-wrap {
  padding: 20px 24px 16px;
  border-bottom: 1px solid var(--border);
  background: var(--bg2);
  flex-shrink: 0;
}
.bt-progress-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 10px;
}
.bt-progress-label {
  font-size: 0.65rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: var(--text-dim);
}
.bt-progress-pct {
  font-size: 0.65rem;
  font-weight: 700;
  color: var(--accent);
}
.bt-progress-track {
  height: 3px;
  background: var(--border);
  border-radius: 2px;
  overflow: hidden;
  margin-bottom: 12px;
}
.bt-progress-bar {
  height: 100%;
  background: linear-gradient(90deg, var(--accent2), var(--accent));
  border-radius: 2px;
  transition: width 0.4s ease;
  box-shadow: 0 0 8px rgba(0,229,255,0.4);
}
.bt-progress-steps {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.bt-step {
  font-size: 0.55rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: var(--text-mute);
  transition: color 0.3s;
}
.bt-step.active { color: var(--accent); }
.bt-step.done   { color: var(--bull); }
.bt-step-arrow  { color: var(--text-mute); font-size: 0.6rem; }

/* Empty state */
.bt-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  opacity: 0.35;
  pointer-events: none;
}
.bt-empty-icon {
  font-size: 2.5rem;
  color: var(--text-dim);
}
.bt-empty-title {
  font-family: var(--syne);
  font-size: 0.9rem;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: var(--text-dim);
}
.bt-empty-sub {
  font-size: 0.62rem;
  color: var(--text-mute);
  letter-spacing: 0.06em;
}

/* Results content */
.bt-results { padding: 20px 24px; display: flex; flex-direction: column; gap: 20px; }

.bt-results-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--border);
}
.bt-results-strategy {
  font-family: var(--syne);
  font-size: 0.9rem;
  font-weight: 800;
  color: var(--text);
  letter-spacing: 0.08em;
}
.bt-results-meta {
  font-size: 0.6rem;
  color: var(--text-dim);
  letter-spacing: 0.08em;
}

/* KPI grid */
.bt-kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(130px, 1fr));
  gap: 8px;
}
.bt-kpi {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 3px;
  padding: 11px 13px;
  transition: border-color 0.15s;
}
.bt-kpi:hover { border-color: var(--border2); }
.bt-kpi-label {
  font-size: 0.52rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  color: var(--text-mute);
  margin-bottom: 5px;
}
.bt-kpi-value {
  font-size: 1rem;
  font-weight: 700;
  color: var(--text);
  letter-spacing: 0.02em;
}
.bt-kpi-value.bull { color: var(--bull); }
.bt-kpi-value.bear { color: var(--bear); }
.bt-kpi-sub {
  font-size: 0.55rem;
  color: var(--text-mute);
  margin-top: 3px;
  letter-spacing: 0.06em;
}

/* Chart blocks */
.bt-chart-block {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 4px;
  overflow: hidden;
}
.bt-chart-title {
  font-size: 0.56rem;
  font-weight: 700;
  letter-spacing: 0.16em;
  color: var(--text-mute);
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
}
.bt-chart-placeholder {
  height: 180px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.bt-chart-ph-label {
  font-size: 0.6rem;
  color: var(--text-mute);
  letter-spacing: 0.1em;
  opacity: 0.4;
}

/* Trade log */
.bt-trade-log {
  max-height: 280px;
  overflow-y: auto;
  overflow-x: auto;
}
.bt-trade-log-empty {
  padding: 24px;
  text-align: center;
  font-size: 0.62rem;
  color: var(--text-mute);
  opacity: 0.5;
}
.bt-trade-row {
  display: grid;
  grid-template-columns: 30px 42px 66px 66px 60px 58px 58px 50px 60px 40px 60px 48px 48px 36px;
  align-items: center;
  padding: 6px 14px;
  border-bottom: 1px solid var(--border);
  font-size: 0.62rem;
  font-weight: 700;
  transition: background 0.1s;
}
.bt-trade-row:hover { background: var(--bg3); }
.bt-trade-header {
  font-size: 0.52rem;
  letter-spacing: 0.1em;
  color: var(--text-mute);
  background: var(--bg3);
  border-bottom: 1px solid var(--border2);
  position: sticky;
  top: 0;
  z-index: 2;
}
.bt-trade-cell { padding: 0 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.bt-trade-cell.bull { color: var(--bull); }
.bt-trade-cell.bear { color: var(--bear); }

/* ═══════════════════════════════════════════════════════════════════
   BACKTEST DASHBOARD — Part 3 additions
   ═══════════════════════════════════════════════════════════════════ */

/* ─── Dashboard root ─────────────────────────────────────────────── */
.bt-dashboard {
  padding: 0 0 32px;
  display: flex;
  flex-direction: column;
  gap: 0;
}

.bt-dash-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 8px;
  padding: 16px 24px 14px;
  border-bottom: 1px solid var(--border);
  background: var(--bg2);
  position: sticky;
  top: 0;
  z-index: 4;
}
.bt-dash-strategy {
  font-family: var(--syne);
  font-size: 0.88rem;
  font-weight: 800;
  color: var(--text);
  letter-spacing: 0.08em;
}
.bt-dash-meta {
  font-size: 0.6rem;
  color: var(--text-dim);
  letter-spacing: 0.08em;
}

/* ─── Section ────────────────────────────────────────────────────── */
.bt-dash-section {
  padding: 18px 24px 0;
  border-bottom: 1px solid var(--border);
  padding-bottom: 20px;
}
.bt-dash-section-title {
  font-size: 0.56rem;
  font-weight: 700;
  letter-spacing: 0.18em;
  color: var(--text-mute);
  margin-bottom: 14px;
}
.bt-dash-sub {
  font-size: 0.52rem;
  color: var(--text-mute);
  letter-spacing: 0.1em;
  font-weight: 400;
}

/* ─── Chart layout ───────────────────────────────────────────────── */
.bt-chart-card {
  background: var(--bg2);
  border: 1px solid var(--border);
  border-radius: 3px;
  overflow: hidden;
}
.bt-chart-label {
  font-size: 0.52rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  color: var(--text-mute);
  padding: 8px 12px 6px;
  border-bottom: 1px solid var(--border);
}
.bt-chart-2col {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
}
.bt-chart-3col {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 10px;
}

/* ─── Canvases ───────────────────────────────────────────────────── */
.bt-canvas {
  display: block;
  width: 100%;
  height: 260px;
}
.bt-canvas-sm {
  height: 180px;
}

/* ─── MC probability table ───────────────────────────────────────── */
.bt-prob-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 7px 0;
  border-bottom: 1px solid var(--border);
  font-size: 0.62rem;
  font-weight: 700;
  color: var(--text-dim);
}
.bt-prob-row:last-child { border-bottom: none; }
.bt-prob-row span:last-child { color: var(--text); }

/* ─── Text color overrides for dashboard panels ────────────────── */
.bt-results-panel,
.bt-dashboard {
  --text-dim:  #ffffff;
  --text-mute: #ffffff;
}

/* ═══════════════════════════════════════════════════════════════════
   LOT SIZE ROW
   ═══════════════════════════════════════════════════════════════════ */
.bt-lotsize-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.bt-lotsize-hint {
  font-size: 0.58rem;
  color: var(--text-dim);
  white-space: nowrap;
}

/* ═══════════════════════════════════════════════════════════════════
   LIVE STATS (streaming progress)
   ═══════════════════════════════════════════════════════════════════ */
.bt-live-stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin-top: 12px;
}
.bt-live-stat {
  background: var(--bg3);
  border: 1px solid var(--border);
  border-radius: 3px;
  padding: 8px 12px;
}
.bt-live-label {
  display: block;
  font-size: 0.52rem;
  font-weight: 700;
  letter-spacing: 0.14em;
  color: var(--text-mute);
  margin-bottom: 4px;
}
.bt-live-value {
  font-size: 0.82rem;
  font-weight: 700;
  color: var(--text);
  letter-spacing: 0.03em;
}
.bt-live-value.bull { color: var(--bull); }
.bt-live-value.bear { color: var(--bear); }

/* ─── Strategy Loader Mode ───────────────────────────────────────── */
#bt_strategyParams {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

#bt_strategyResults {
  display: none;
}

#bt_strategyDescription {
  line-height: 1.55;
}

/* ─── Strategy Loader / Replay ───────────────────────────────────── */
.bt-manual-only {
  display: block;
}

#bt_strategyParams,
#bt_engineParams {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

#bt_replayWrap .bt-icon-btn {
  width: 34px;
  height: 30px;
  font-size: 0.7rem;
}

#bt_replayMeta {
  margin-left: 4px;
}

/* ═══════════════════════════════════════════════════════════════════
   SYMBOL SELECT (chart header + backtester)
   ═══════════════════════════════════════════════════════════════════ */
.symbol-select {
  background: var(--bg3);
  border: 1px solid var(--border);
  color: var(--accent);
  font-family: var(--syne);
  font-size: 0.82rem;
  font-weight: 700;
  letter-spacing: 0.08em;
  padding: 3px 24px 3px 8px;
  border-radius: 3px;
  cursor: pointer;
  outline: none;
  appearance: none;
  -webkit-appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%2300e5ff'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 6px center;
  transition: border-color 0.15s;
}
.symbol-select:hover { border-color: var(--accent2); }
.symbol-select:focus { border-color: var(--accent); }
.symbol-select option {
  background: var(--bg2);
  color: var(--text);
}
```
