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
  dollars = points × lot_size × point_value

Where:
  points      = price movement (exit - entry, direction-adjusted)
  lot_size    = number of contracts/lots (default 1.0)
  point_value = dollar value per 1.0 point move per lot (default 1.0)

Examples:
  NQ Futures:  point_value = 20  → 1 point × 1 lot = $20
  ES Futures:  point_value = 50  → 1 point × 1 lot = $50
  Custom:      point_value = 1   → 1 point × 1 lot = $1 (default)

R-multiples are computed BEFORE dollar conversion and are
independent of lot_size / point_value.
"""
from __future__ import annotations
import math


def points_to_dollars(
    points: float,
    lot_size: float = 1.0,
    point_value: float = 1.0,
) -> float:
    """
    THE single conversion function.
    Every dollar calculation in the system MUST call this.
    """
    return points * lot_size * point_value


def finalize_trade_pnl(
    closed: dict,
    lot_size: float = 1.0,
    point_value: float = 1.0,
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

    # ── 2. Risk in points (from SL) ──────────────────────────────
    sl = closed.get("sl_level")
    rp = closed.get("risk_pts")
    if sl is not None:
        rp = abs(ep - sl)
    if rp is None or rp <= 0:
        rp = None

    # ── 3. R-multiple (PURE — independent of dollar settings) ────
    r_mult = None
    if rp is not None and rp > 0:
        r_mult = points_pnl / rp

    # ── 4. Dollar PnL (centralized) ──────────────────────────────
    gross_dollar = points_to_dollars(points_pnl, lot_size, point_value)

    # ── 5. Commission ────────────────────────────────────────────
    net_dollar = gross_dollar - commission

    # ── 6. Risk / MAE / MFE in dollars (same conversion) ────────
    risk_dollar = points_to_dollars(rp, lot_size, point_value) if rp and rp > 0 else None
    mae_dollar  = points_to_dollars(closed.get("mae", 0), lot_size, point_value)
    mfe_dollar  = points_to_dollars(closed.get("mfe", 0), lot_size, point_value)

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
) -> bool:
    """
    Validation helper. Use in tests to verify consistency.

    Example:
        validate_conversion(10.0, 1.0, 5.0, 50.0)  # True
        validate_conversion(10.0, 2.0, 20.0, 400.0) # True
    """
    actual = points_to_dollars(points, lot_size, point_value)
    return abs(actual - expected_dollars) < 0.01