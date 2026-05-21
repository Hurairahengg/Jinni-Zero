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