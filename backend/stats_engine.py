# backend/stats_engine.py
"""
JINNI Backtester — Centralized Statistics Engine
Single source of truth for ALL backtest metrics.
Both legacy and strategy engines call compute_all_stats().
Every returned value is a valid JSON number or None — never NaN/Inf.
"""
import math
from collections import defaultdict
from datetime import datetime, timezone

# ════════════════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════════════════

def _safe(val, decimals=2):
    """Round to *decimals* if finite, else None."""
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, decimals)
    except (TypeError, ValueError):
        return None


def _median(vals):
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def _fmt_duration(seconds):
    if not seconds or seconds <= 0:
        return "0m"
    d = int(seconds // 86400)
    h = int((seconds % 86400) // 3600)
    m = int((seconds % 3600) // 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m or not parts:
        parts.append(f"{m}m")
    return " ".join(parts)


def _ts_to_dt(ts):
    """Unix timestamp → UTC datetime (handles seconds & milliseconds)."""
    try:
        t = float(ts)
        if t <= 0 or not math.isfinite(t):
            return None
        if t > 1e12:
            t /= 1000
        return datetime.fromtimestamp(t, tz=timezone.utc)
    except Exception:
        return None


def _gf(d, key, default=0.0):
    """Get float from dict, safe."""
    v = d.get(key)
    if v is None:
        return default
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


# ════════════════════════════════════════════════════════════════════
#  SHARPE / SORTINO  (daily equity returns — correct annualisation)
# ════════════════════════════════════════════════════════════════════

def _compute_sharpe_sortino(equity_curve, bars):
    """
    Group equity by calendar day → daily returns → annualised Sharpe & Sortino.
    Falls back to None when data is insufficient.
    """
    if not equity_curve or not bars or len(equity_curve) < 2:
        return None, None

    daily_eq = {}
    for i, eq in enumerate(equity_curve):
        if i >= len(bars):
            break
        dt = _ts_to_dt(bars[i].get("time"))
        if dt:
            daily_eq[dt.strftime("%Y-%m-%d")] = eq

    if len(daily_eq) < 3:
        return None, None

    sorted_days = sorted(daily_eq.items())
    rets = []
    for i in range(1, len(sorted_days)):
        prev = sorted_days[i - 1][1]
        curr = sorted_days[i][1]
        if prev > 0:
            rets.append((curr - prev) / prev)

    if len(rets) < 2:
        return None, None

    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
    sd = math.sqrt(var) if var > 0 else 0

    sharpe = (mu / sd) * math.sqrt(252) if sd > 0 else None

    neg = [r for r in rets if r < 0]
    if neg:
        dsd = math.sqrt(sum(r * r for r in neg) / len(neg))
        sortino = (mu / dsd) * math.sqrt(252) if dsd > 0 else None
    else:
        sortino = None

    return sharpe, sortino


# ════════════════════════════════════════════════════════════════════
#  MAIN ENTRY POINT
# ════════════════════════════════════════════════════════════════════

def compute_all_stats(trades, equity_curve, bars,
                      starting_capital, lot_size=1.0):
    """
    Compute every backtest KPI from trades + equity curve.

    Parameters
    ----------
    trades : list[dict]
        Trade records from either engine.
    equity_curve : list[float]
        One equity value per bar (mark-to-market).
    bars : list[dict]
        Raw bar data (need ``time`` for period grouping / Sharpe).
    starting_capital : float
    lot_size : float

    Returns
    -------
    dict   — fully JSON-serialisable, no NaN / Inf.
    """
    equity_curve = [
        float(x) for x in (equity_curve or [])
        if x is not None and math.isfinite(float(x))
    ]
    n_bars = len(bars) if bars else 0
    n_trades = len(trades) if trades else 0

    if n_trades == 0:
        return _empty_stats(n_bars, starting_capital, lot_size, bars)

    # ════════════════════════════════════════════════════════════
    #  SINGLE PASS OVER TRADES
    # ════════════════════════════════════════════════════════════
    nets = []
    win_nets = []
    loss_nets = []
    r_vals = []
    win_r = []
    loss_r = []
    bars_held_all = []
    win_bh = []
    loss_bh = []
    hold_secs = []
    mae_d = []
    mfe_d = []
    gross_all = []
    comm_all = []

    daily_pnl = defaultdict(float)
    weekly_pnl = defaultdict(float)
    monthly_pnl = defaultdict(float)
    entry_days = set()
    entry_weeks = set()

    # streak state
    max_cw = max_cl = cw = cl = 0
    best_wl = best_ll = 0
    cwp = clp = lwp = llp = 0.0
    cum = 0.0

    for t in trades:
        pnl = _gf(t, "net_pnl")
        gross = _gf(t, "gross_pnl")
        comm = _gf(t, "commission")
        rv = t.get("net_pnl_r")
        bh = _gf(t, "bars_held")
        hs = _gf(t, "holding_seconds")
        mae = t.get("mae_dollar")
        mfe = t.get("mfe_dollar")

        nets.append(pnl)
        gross_all.append(gross)
        comm_all.append(comm)
        bars_held_all.append(bh)
        cum += pnl

        if hs > 0:
            hold_secs.append(hs)
        if mae is not None:
            try:
                v = float(mae)
                if math.isfinite(v):
                    mae_d.append(v)
            except Exception:
                pass
        if mfe is not None:
            try:
                v = float(mfe)
                if math.isfinite(v):
                    mfe_d.append(v)
            except Exception:
                pass

        is_win = pnl > 0
        if is_win:
            win_nets.append(pnl)
            win_bh.append(bh)
        else:
            loss_nets.append(pnl)
            loss_bh.append(bh)

        if rv is not None:
            try:
                fv = float(rv)
                if math.isfinite(fv):
                    r_vals.append(fv)
                    (win_r if is_win else loss_r).append(fv)
            except Exception:
                pass

        # period grouping (by exit time)
        ets = t.get("exit_time") or t.get("entry_time")
        dt = _ts_to_dt(ets)
        if dt:
            daily_pnl[dt.strftime("%Y-%m-%d")] += pnl
            yr, wk, _ = dt.isocalendar()
            weekly_pnl[f"{yr}-W{wk:02d}"] += pnl
            monthly_pnl[dt.strftime("%Y-%m")] += pnl

        edt = _ts_to_dt(t.get("entry_time"))
        if edt:
            entry_days.add(edt.strftime("%Y-%m-%d"))
            yr2, wk2, _ = edt.isocalendar()
            entry_weeks.add(f"{yr2}-W{wk2:02d}")

        # streaks
        if is_win:
            cw += 1; cwp += pnl
            if cl > 0:
                cl = 0; clp = 0
            max_cw = max(max_cw, cw)
            if cw > best_wl:
                best_wl = cw; lwp = cwp
        else:
            cl += 1; clp += pnl
            if cw > 0:
                cw = 0; cwp = 0
            max_cl = max(max_cl, cl)
            if cl > best_ll:
                best_ll = cl; llp = clp

    # ════════════════════════════════════════════════════════════
    #  DERIVED METRICS
    # ════════════════════════════════════════════════════════════
    nw = len(win_nets)
    nl = len(loss_nets)
    wr = nw / n_trades

    net_total = sum(nets)
    gross_total = sum(gross_all)
    total_comm = sum(comm_all)

    aw = sum(win_nets) / nw if nw else 0
    al = sum(loss_nets) / nl if nl else 0
    lw = max(win_nets) if win_nets else 0
    ll = min(loss_nets) if loss_nets else 0

    gw = sum(win_nets)
    gl = abs(sum(loss_nets))
    pf = gw / gl if gl > 0 else None

    exp = net_total / n_trades
    final_bal = starting_capital + net_total
    net_pct = (net_total / starting_capital * 100) if starting_capital > 0 else 0

    # ── Drawdown (recomputed from equity curve) ───────────────
    peak = starting_capital
    max_dd = 0.0
    max_dd_pct = 0.0
    dd_pos = []
    dd_dur = cur_dur = 0

    for eq in equity_curve:
        peak = max(peak, eq)
        dd = peak - eq
        dd_pos.append(dd)
        max_dd = max(max_dd, dd)
        if peak > 0:
            max_dd_pct = max(max_dd_pct, dd / peak * 100)
        if dd > 0:
            cur_dur += 1
            dd_dur = max(dd_dur, cur_dur)
        else:
            cur_dur = 0

    neg_dd = [x for x in dd_pos if x > 0]
    avg_dd = sum(neg_dd) / len(neg_dd) if neg_dd else 0

    ulcer = 0.0
    if dd_pos:
        ulcer = math.sqrt(sum(x * x for x in dd_pos) / len(dd_pos))

    rec_f = abs(net_total / max_dd) if max_dd > 0 else None
    calmar = abs(net_total / max_dd) * 12 if max_dd > 0 else None

    final_eq = equity_curve[-1] if equity_curve else starting_capital

    # ── Sharpe / Sortino (daily equity returns) ───────────────
    sharpe, sortino = _compute_sharpe_sortino(equity_curve, bars)

    # ── SQN ───────────────────────────────────────────────────
    sqn = None
    if len(r_vals) >= 2:
        mu_r = sum(r_vals) / len(r_vals)
        var_r = sum((x - mu_r) ** 2 for x in r_vals) / (len(r_vals) - 1)
        sd_r = math.sqrt(var_r) if var_r > 0 else 0
        if sd_r > 0:
            sqn = (mu_r / sd_r) * math.sqrt(len(r_vals))

    # ── Omega ─────────────────────────────────────────────────
    omega = None
    g_sum = sum(x for x in nets if x > 0)
    l_sum = abs(sum(x for x in nets if x < 0))
    if l_sum > 0:
        omega = g_sum / l_sum

    # ── Expectancy R ──────────────────────────────────────────
    exp_r = sum(r_vals) / len(r_vals) if r_vals else None

    # ── Hold time ─────────────────────────────────────────────
    avg_hs = sum(hold_secs) / len(hold_secs) if hold_secs else 0
    avg_hb = sum(bars_held_all) / len(bars_held_all) if bars_held_all else 0
    med_hb = _median(bars_held_all)
    avg_whb = sum(win_bh) / len(win_bh) if win_bh else 0
    avg_lhb = sum(loss_bh) / len(loss_bh) if loss_bh else 0

    # ── Exposure ──────────────────────────────────────────────
    tot_bh = sum(bars_held_all)
    exposure = (tot_bh / n_bars * 100) if n_bars > 0 else 0

    # ── Frequency ─────────────────────────────────────────────
    tpd = n_trades / len(entry_days) if entry_days else 0
    tpw = n_trades / len(entry_weeks) if entry_weeks else 0

    # ── Quality ───────────────────────────────────────────────
    payoff = abs(aw / al) if al != 0 else None
    ppt = net_total / n_trades
    mw = _median(win_nets)
    ml = _median(loss_nets)

    # ── MAE / MFE ─────────────────────────────────────────────
    a_mae = sum(mae_d) / len(mae_d) if mae_d else 0
    a_mfe = sum(mfe_d) / len(mfe_d) if mfe_d else 0
    edge = a_mfe / a_mae if a_mae > 0 else None

    # ── R averages ────────────────────────────────────────────
    a_wr = sum(win_r) / len(win_r) if win_r else None
    a_lr = sum(loss_r) / len(loss_r) if loss_r else None

    # ── Period PnL ────────────────────────────────────────────
    def _pstat(d):
        if not d:
            return 0, 0, 0
        vals = list(d.values())
        return sum(vals) / len(vals), max(vals), min(vals)

    adp, bd, wd = _pstat(daily_pnl)
    awp, bw, ww = _pstat(weekly_pnl)
    amp, bm, wm = _pstat(monthly_pnl)

    # ── Date range ────────────────────────────────────────────
    dr = ""
    if bars:
        d0 = _ts_to_dt(bars[0].get("time"))
        d1 = _ts_to_dt(bars[-1].get("time"))
        if d0 and d1:
            dr = f"{d0.strftime('%Y-%m-%d')} \u2192 {d1.strftime('%Y-%m-%d')}"

    # ════════════════════════════════════════════════════════════
    #  BUILD RESULT  (everything through _safe)
    # ════════════════════════════════════════════════════════════
    return {
        "total_trades": n_trades,
        "winning_trades": nw,
        "losing_trades": nl,
        "win_rate": _safe(wr, 4),
        "net_pnl": _safe(net_total),
        "gross_pnl": _safe(gross_total),
        "total_commission": _safe(total_comm),
        "avg_win": _safe(aw),
        "avg_loss": _safe(al),
        "largest_win": _safe(lw),
        "largest_loss": _safe(ll),
        "profit_factor": _safe(pf, 4),
        "expectancy": _safe(exp),
        "expectancy_r": _safe(exp_r, 3),
        "starting_capital": _safe(starting_capital),
        "final_balance": _safe(final_bal),
        "final_equity": _safe(final_eq),
        "net_profit_pct": _safe(net_pct),
        "lot_size": lot_size,
        "max_drawdown": _safe(max_dd),
        "max_drawdown_pct": _safe(max_dd_pct),
        "avg_drawdown": _safe(avg_dd),
        "drawdown_duration_bars": dd_dur,
        "recovery_factor": _safe(rec_f, 4),
        "calmar_ratio": _safe(calmar, 4),
        "ulcer_index": _safe(ulcer, 4),
        "sharpe": _safe(sharpe, 4),
        "sortino": _safe(sortino, 4),
        "omega": _safe(omega, 4),
        "sqn": _safe(sqn, 4),
        "profit_per_trade": _safe(ppt),
        "payoff_ratio": _safe(payoff, 4),
        "median_win": _safe(mw),
        "median_loss": _safe(ml),
        "avg_win_r": _safe(a_wr, 4),
        "avg_loss_r": _safe(a_lr, 4),
        "avg_mae_dollar": _safe(a_mae),
        "avg_mfe_dollar": _safe(a_mfe),
        "edge_ratio": _safe(edge, 4),
        "avg_holding_seconds": _safe(avg_hs, 1),
        "avg_holding_bars": _safe(avg_hb),
        "median_hold_bars": _safe(med_hb, 1),
        "avg_win_hold_bars": _safe(avg_whb),
        "avg_loss_hold_bars": _safe(avg_lhb),
        "avg_hold_time_str": _fmt_duration(avg_hs),
        "exposure_pct": _safe(exposure),
        "avg_trades_per_day": _safe(tpd),
        "avg_trades_per_week": _safe(tpw),
        "max_consec_wins": max_cw,
        "max_consec_losses": max_cl,
        "longest_win_streak_pnl": _safe(lwp),
        "longest_loss_streak_pnl": _safe(llp),
        "avg_daily_pnl": _safe(adp),
        "avg_weekly_pnl": _safe(awp),
        "avg_monthly_pnl": _safe(amp),
        "best_day": _safe(bd),
        "worst_day": _safe(wd),
        "best_week": _safe(bw),
        "worst_week": _safe(ww),
        "best_month": _safe(bm),
        "worst_month": _safe(wm),
        "total_bars_used": n_bars,
        "date_range": dr,
    }


# ════════════════════════════════════════════════════════════════════
#  EMPTY STATS (zero trades)
# ════════════════════════════════════════════════════════════════════

def _empty_stats(n_bars, starting_capital, lot_size, bars):
    dr = ""
    if bars:
        d0 = _ts_to_dt(bars[0].get("time"))
        d1 = _ts_to_dt(bars[-1].get("time"))
        if d0 and d1:
            dr = f"{d0.strftime('%Y-%m-%d')} \u2192 {d1.strftime('%Y-%m-%d')}"
    return {
        "total_trades": 0, "winning_trades": 0, "losing_trades": 0,
        "win_rate": 0, "net_pnl": 0, "gross_pnl": 0, "total_commission": 0,
        "avg_win": 0, "avg_loss": 0, "largest_win": 0, "largest_loss": 0,
        "profit_factor": None, "expectancy": 0, "expectancy_r": None,
        "starting_capital": starting_capital, "final_balance": starting_capital,
        "final_equity": starting_capital, "net_profit_pct": 0,
        "lot_size": lot_size,
        "max_drawdown": 0, "max_drawdown_pct": 0, "avg_drawdown": 0,
        "drawdown_duration_bars": 0, "recovery_factor": None,
        "calmar_ratio": None, "ulcer_index": 0,
        "sharpe": None, "sortino": None, "omega": None, "sqn": None,
        "profit_per_trade": 0, "payoff_ratio": None,
        "median_win": None, "median_loss": None,
        "avg_win_r": None, "avg_loss_r": None,
        "avg_mae_dollar": 0, "avg_mfe_dollar": 0, "edge_ratio": None,
        "avg_holding_seconds": 0, "avg_holding_bars": 0,
        "median_hold_bars": None, "avg_win_hold_bars": 0,
        "avg_loss_hold_bars": 0, "avg_hold_time_str": "0m",
        "exposure_pct": 0, "avg_trades_per_day": 0, "avg_trades_per_week": 0,
        "max_consec_wins": 0, "max_consec_losses": 0,
        "longest_win_streak_pnl": 0, "longest_loss_streak_pnl": 0,
        "avg_daily_pnl": 0, "avg_weekly_pnl": 0, "avg_monthly_pnl": 0,
        "best_day": 0, "worst_day": 0, "best_week": 0, "worst_week": 0,
        "best_month": 0, "worst_month": 0,
        "total_bars_used": n_bars, "date_range": dr,
        "message": "No trades",
    }


# ════════════════════════════════════════════════════════════════════
#  CURVE DOWNSAMPLING  (min-max preserving extremes)
# ════════════════════════════════════════════════════════════════════

def downsample_curve(values, max_points=1500):
    if not values:
        return []
    n = len(values)
    if n <= max_points:
        return values[:]
    bucket = n / max_points
    out = [values[0]]
    for b in range(1, max_points - 1):
        s = int(math.floor(b * bucket))
        e = int(math.floor((b + 1) * bucket))
        s = max(0, s)
        e = min(n, e)
        if s >= e:
            continue
        chunk = values[s:e]
        mn = min(chunk)
        mx = max(chunk)
        mi = chunk.index(mn)
        xi = chunk.index(mx)
        if mi <= xi:
            out.append(mn)
            out.append(mx)
        else:
            out.append(mx)
            out.append(mn)
    out.append(values[-1])
    return out[:max_points]