"""
JINNI ZERO — Simplified Dollar Math
====================================
Single source of truth for ALL PnL calculations.

Model:
  pnl        = lot_size × point_value × points_moved
  commission = commission_per_1_lot × lot_size
  spread:  BUY entry  = close + spread_points
           SELL entry = close - spread_points
"""
from __future__ import annotations
import math


def points_to_dollars(points: float, lot_size: float = 1.0, point_value: float = 1.0) -> float:
    """THE single conversion. Every dollar calc MUST call this."""
    return points * lot_size * point_value


def calc_commission(lot_size: float, commission_per_1_lot: float = 0.0) -> float:
    """commission = commission_per_1_lot × lot_size"""
    return lot_size * commission_per_1_lot


def apply_spread_entry(close_price: float, direction: str, spread_points: float = 0.0) -> float:
    """Apply fixed spread at entry. BUY = worse ask, SELL = worse bid."""
    if spread_points <= 0:
        return close_price
    if direction == "long":
        return close_price + spread_points
    return close_price - spread_points


def finalize_trade_pnl(
    closed: dict,
    lot_size: float = 1.0,
    point_value: float = 1.0,
    commission_per_1_lot: float = 0.0,
) -> None:
    """
    Compute ALL dollar + R fields on a closed trade dict (in-place).

    Order:
      1. Points PnL (direction-aware)
      2. Risk in points (from SL)
      3. R-multiple (pure — no dollars)
      4. Gross dollar PnL
      5. Commission
      6. Net PnL
      7. MAE / MFE dollars
    """
    d  = closed["direction"]
    ep = closed["entry_price"]
    xp = closed["exit_price"]

    # 1 — points
    dir_sign   = 1 if d == "long" else -1
    points_pnl = (xp - ep) * dir_sign

    # 2 — risk
    sl = closed.get("sl_level")
    rp = closed.get("risk_pts")
    if sl is not None:
        rp = abs(ep - sl)
    if rp is None or rp <= 0:
        rp = None

    # 3 — R
    r_mult = (points_pnl / rp) if (rp is not None and rp > 0) else None

    # 4 — dollars
    trade_lot    = closed.get("lot_size", lot_size)
    gross_dollar = points_to_dollars(points_pnl, trade_lot, point_value)

    # 5 — commission
    commission = calc_commission(trade_lot, commission_per_1_lot)

    # 6 — net
    net_dollar = gross_dollar - commission

    # 7 — MAE / MFE / risk dollars
    risk_dollar = points_to_dollars(rp, trade_lot, point_value) if rp and rp > 0 else None
    mae_dollar  = points_to_dollars(closed.get("mae", 0), trade_lot, point_value)
    mfe_dollar  = points_to_dollars(closed.get("mfe", 0), trade_lot, point_value)

    closed.update(
        points_pnl  = round(points_pnl, 4),
        gross_pnl   = round(gross_dollar, 2),
        commission  = round(commission, 4),
        net_pnl     = round(net_dollar, 2),
        net_pnl_r   = round(r_mult, 3) if r_mult is not None else None,
        risk_pts    = round(rp, 4) if rp is not None else None,
        risk_dollar = round(risk_dollar, 2) if risk_dollar else None,
        mae_dollar  = round(mae_dollar, 2),
        mfe_dollar  = round(mfe_dollar, 2),
    )