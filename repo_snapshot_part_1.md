# Repository Snapshot - Part 1 of 5

- Root folder: `/home/hurairahengg/Documents/Jinni Zero`
- You know my wholle Jinjnibacktester simulator thign whre ther is a UI bascially and then i can see  charst and stuff when i need to run simulatiosn liek i send simulatio nto my flask backend server it runs sims and then shows stast and stuff and i can load strategy and shit for now take a look we will be doing bug fixes and some validation and shit. udnerrtsnad each code and its role how it works and keep in ir conetxt i will ask u exactly wha tto do later code later duinerstood
- Total files indexed: `24`
- Files in this chunk: `5`
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

## Files In This Chunk - Part 1

```text
.gitignore
backend/stats_engine.py
backend/strategy_loader.py
backtest_server.py
STRATEGY_GUIDE.txt
```

## File Contents


---

## FILE: `.gitignore`

```text
data/
data/2pt.json
```

---

## FILE: `backend/stats_engine.py`

```python
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
```

---

## FILE: `backend/strategy_loader.py`

```python
"""
JINNI ZERO — Strategy Loader (with lookback validation)
"""
from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil

from backend.strategies.base import BaseStrategy
import backend.strategies as strategies_pkg

logger = logging.getLogger("jinni.loader")


def _iter_strategy_modules():
    for mod in pkgutil.iter_modules(strategies_pkg.__path__):
        if mod.name in {"base"} or mod.name.startswith("_"):
            continue
        yield mod.name


def discover_strategies():
    registry = {}
    for module_name in _iter_strategy_modules():
        try:
            module = importlib.import_module(f"backend.strategies.{module_name}")
        except Exception as e:
            logger.error(f"Failed to import strategy module '{module_name}': {e}")
            continue

        for _, obj in inspect.getmembers(module, inspect.isclass):
            if not issubclass(obj, BaseStrategy) or obj is BaseStrategy:
                continue
            try:
                instance = obj()
                strategy_id = instance.strategy_id or module_name
                instance.strategy_id = strategy_id
                registry[strategy_id] = instance
                logger.info(f"Loaded strategy: {strategy_id} (lookback={instance.min_lookback})")
            except Exception as e:
                logger.error(f"Failed to instantiate strategy from {module_name}: {e}")

    return registry


def get_strategy(strategy_id: str):
    registry = discover_strategies()
    if strategy_id not in registry:
        raise KeyError(f"Unknown strategy '{strategy_id}'")
    return registry[strategy_id]


def list_strategy_metadata():
    registry = discover_strategies()
    return [registry[k].get_metadata() for k in sorted(registry.keys())]


def validate_lookback(strategy, bar_count: int, lookback_override: int = 0):
    """Check if enough bars exist for strategy's lookback requirement."""
    required = max(
        getattr(strategy, "min_lookback", 0) or 0,
        lookback_override,
    )
    if bar_count < required:
        raise ValueError(
            f"Strategy '{strategy.strategy_id}' requires {required} bars lookback, "
            f"but only {bar_count} bars available."
        )
    return required
```

---

## FILE: `backtest_server.py`

```python
"""
backtest_server.py  —  NQ Range Bar Backtest Engine + Analytics
Run:  python backtest_server.py
POST /api/backtest        →  full results JSON
POST /api/backtest/stream →  NDJSON streaming progress + result
GET  /api/health          →  ok
"""
import json, math, os, time as _time
from flask import Flask, jsonify, request, Response, stream_with_context
from flask_cors import CORS
from backend.dollar_math import (
    points_to_dollars,
    finalize_trade_pnl,
    compute_position_size,
    compute_scaling_risk,
)
from backend.strategy_api import strategy_api
from backend.stats_engine import compute_all_stats, downsample_curve as ds_curve
from backend.shared import (
    precompute_ma,
    get_or_compute_ma,
    SpreadGenerator,
    calc_comm,
    compute_analytics,
    clean_for_json,
    cap_analytics_arrays,
)

app = Flask(__name__)
CORS(app, supports_credentials=True)
DATA_DIR = "data"

app.register_blueprint(strategy_api, url_prefix="/api")

# ════════════════════════════════════════════════════════════════════
#  DATETIME HELPER
# ════════════════════════════════════════════════════════════════════
def _parse_datetime_param(val):
    if not val or not isinstance(val, str) or not val.strip():
        return None
    val = val.strip()
    from datetime import datetime, timezone
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(val, fmt)
            dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue
    return None

# ════════════════════════════════════════════════════════════════════
#  DATA
# ════════════════════════════════════════════════════════════════════
def load_bars(range_pt, bar_range, symbol="NQ", start_date=None, end_date=None):
    range_val = float(range_pt)
    range_str = str(int(range_val)) if range_val == int(range_val) else str(range_val)
    path = os.path.join(DATA_DIR, symbol, f"{range_str}pt.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Dataset not found: {path}")
    with open(path) as f:
        bars = json.load(f)
    total = len(bars)

    start_ts = _parse_datetime_param(start_date)
    end_ts   = _parse_datetime_param(end_date)
    if start_ts is not None or end_ts is not None:
        before = len(bars)
        if start_ts is not None:
            bars = [b for b in bars if b["time"] >= start_ts]
        if end_ts is not None:
            bars = [b for b in bars if b["time"] <= end_ts]
        print(f"  [DATA] Date filter: {before} → {len(bars)} bars"
              f"  (start={start_date}, end={end_date})")

    if bar_range and bar_range > 0:
        bars = bars[-bar_range:]
    print(f"  [DATA] Loaded {path} — {total} total bars, using last {len(bars)}")
    return bars

# ════════════════════════════════════════════════════════════════════
#  INDICATOR ENGINE  (Legacy-specific — uses shared MA functions)
# ════════════════════════════════════════════════════════════════════
class IndicatorEngine:
    def __init__(self, ma_type, period):
        self.ma_type = ma_type.upper()
        self.period  = int(period)
        self._precomputed = None
        self._idx = -1
        self.closes  = []
        self._ema_val = None

    def precompute(self, closes, dataset_id=None):
        self._precomputed = get_or_compute_ma(closes, self.ma_type, self.period, dataset_id)
        self._idx = -1

    def update(self, close):
        if self._precomputed is not None:
            self._idx += 1
            if self._idx < len(self._precomputed):
                return self._precomputed[self._idx]
            return None
        self.closes.append(close)
        n = len(self.closes)
        if self.ma_type == "EMA":  return self._ema(close, n)
        if self.ma_type == "SMA":  return self._sma(n)
        if self.ma_type == "WMA":  return self._wma_at(self.closes, self.period, n-1)
        if self.ma_type == "HMA":  return self._hma(n)
        return None

    def _ema(self, close, n):
        p = self.period; k = 2/(p+1)
        if self._ema_val is None:
            if n < p: return None
            self._ema_val = sum(self.closes[-p:])/p
            return self._ema_val
        self._ema_val = close*k + self._ema_val*(1-k)
        return self._ema_val

    def _sma(self, n):
        p = self.period
        if n < p: return None
        return sum(self.closes[-p:])/p

    @staticmethod
    def _wma_at(closes, p, ei):
        if ei < p-1: return None
        d = p*(p+1)/2
        return sum(closes[ei-j]*(p-j) for j in range(p))/d

    def _hma(self, n):
        p = self.period; h = p//2; sq = int(math.floor(math.sqrt(p)))
        if n < p+sq-1: return None
        ei = n-1; ds = []
        for k in range(sq):
            wf = self._wma_at(self.closes, p, ei-k)
            wh = self._wma_at(self.closes, h, ei-k)
            if wf is None or wh is None: return None
            ds.append(2*wh - wf)
        ds.reverse()
        d = sq*(sq+1)/2
        return sum(ds[sq-1-j]*(sq-j) for j in range(sq))/d

    @property
    def last_value(self):
        if self._precomputed is not None:
            if 0 <= self._idx < len(self._precomputed):
                return self._precomputed[self._idx]
            return None
        if not self.closes: return None
        n = len(self.closes)
        if self.ma_type == "EMA":  return self._ema_val
        if self.ma_type == "SMA":  return self._sma(n) if n >= self.period else None
        if self.ma_type == "WMA":  return self._wma_at(self.closes, self.period, n-1)
        if self.ma_type == "HMA":  return self._hma(n)
        return None


class MultiIndicatorEngine:
    def __init__(self, ma_defs):
        self.engines = [IndicatorEngine(d["type"], d["period"]) for d in ma_defs]
    def precompute(self, closes, dataset_id=None):
        for eng in self.engines:
            eng.precompute(closes, dataset_id)
    def update(self, close):
        return [eng.update(close) for eng in self.engines]

# ════════════════════════════════════════════════════════════════════
#  BACKTEST ENGINE  (LEGACY — UNTOUCHED LOGIC + risk% sizing)
# ════════════════════════════════════════════════════════════════════
class BacktestEngine:
    def __init__(self, bars, config):
        self.bars   = bars
        self.config = config
        self.n      = len(bars)

        self.ma_defs       = config.get("mas", [])
        self.entry_mode    = config.get("entry", "above_all_mas")
        self.sl_cfg        = config.get("sl", {})
        self.tp_cfg        = config.get("tp", {})
        self.commission_per_lot = float(config.get("commission_per_lot", 0))
        self.gating_cfg    = config.get("gating", {})
        self.lot_size      = float(config.get("lot_size", 1.0))
        self.point_value   = float(config.get("point_value", 1.0))
        self.dollar_per_point = float(config.get("dollar_per_point", 1.0))
        self.starting_cap  = float(config.get("starting_capital", 10000.0))
        self.ambiguous_mode = config.get("ambiguous_bar_mode", "conservative")
        self.require_candle_confirm = config.get("require_candle_confirm", True)

        self.sl_mode = self.sl_cfg.get("mode", "fixed")
        self.tp_mode = self.tp_cfg.get("mode", "r_multiple")
        self.gating_enabled = bool(self.gating_cfg.get("enabled", False))

        self.spread_gen = SpreadGenerator(config.get("spread", {}))

        # ── Position sizing ──────────────────────────────────────
        self.sizing_mode = str(config.get("sizing_mode", "fixed")).strip().lower()
        self.risk_pct = float(config.get("risk_pct", 1.0))
        self.fixed_risk = float(config.get("fixed_risk", 10.0))
        self.scaling_enabled = bool(config.get("scaling_enabled", False))
        self.scaling_per = float(config.get("scaling_per", 100.0))
        self.scaling_risk = float(config.get("scaling_risk", 1.0))
        self.min_lot = float(config.get("min_lot", 0.01))
        self.max_lot = float(config.get("max_lot", 1000.0))
        self.lot_step = float(config.get("lot_step", 0.01))

        self.entry_ind = MultiIndicatorEngine(self.ma_defs)

        self.sl_ma_eng = None
        if self.sl_mode in ("ma_cross", "ma_snapshot"):
            self.sl_ma_eng = IndicatorEngine(
                self.sl_cfg.get("ma_type", "EMA"),
                self.sl_cfg.get("ma_length", 50))

        self.tp_ma_eng = None
        if self.tp_mode == "ma_cross":
            self.tp_ma_eng = IndicatorEngine(
                self.tp_cfg.get("ma_type", "EMA"),
                self.tp_cfg.get("ma_length", 9))

        self.gating_eng = None
        if self.gating_enabled:
            self.gating_eng = IndicatorEngine(
                self.gating_cfg.get("ma_type", "HMA"),
                self.gating_cfg.get("ma_length", 21))

        self.trades       = []
        self.equity_curve = []
        self.dd_curve     = []

        closes = [b["close"] for b in self.bars]
        dataset_id = id(self.bars)
        t0 = _time.perf_counter()
        self.entry_ind.precompute(closes, dataset_id)
        if self.sl_ma_eng:  self.sl_ma_eng.precompute(closes, dataset_id)
        if self.tp_ma_eng:  self.tp_ma_eng.precompute(closes, dataset_id)
        if self.gating_eng: self.gating_eng.precompute(closes, dataset_id)
        dt = _time.perf_counter() - t0

        sizing_str = f"lot={self.lot_size}" if self.sizing_mode == "fixed" \
                     else f"risk={self.risk_pct}%"
        print(f"  [ENGINE] {self.n} bars | entry={self.entry_mode} | "
              f"SL={self.sl_mode} | TP={self.tp_mode} | "
              f"gating={self.gating_enabled} | "
              f"candle_confirm={self.require_candle_confirm} | "
              f"{sizing_str} | spread={self.spread_gen.enabled} | "
              f"precompute={dt*1000:.1f}ms")

    def run(self):
        for _ in self._run_generator(): pass
        return self._build_result()

    def run_streaming(self):
        yield from self._run_generator()
        yield {"type": "result", "data": self._build_result()}

    # ── Core loop (LEGACY — UNTOUCHED + risk% sizing) ───────────
    def _run_generator(self):
        state = "flat"
        open_t = None
        pending_signal = None

        ma_hist = []
        bc = bc2 = 0
        regime = "neutral"

        long_locked  = False
        short_locked = False

        cum  = 0.0
        peak = self.starting_cap
        last_closed_pnl = None

        progress_interval = max(1, self.n // 100)
        last_emit_time = _time.perf_counter()
        EMIT_MIN_INTERVAL = 0.15

        prev_sl_ma_val = None

        for i, bar in enumerate(self.bars):
            c = bar["close"]; o = bar["open"]; h = bar["high"]; l = bar["low"]

            mv = self.entry_ind.update(c)
            ma_hist.append(mv)
            sl_ma_val = self.sl_ma_eng.update(c) if self.sl_ma_eng else None
            tp_ma_val = self.tp_ma_eng.update(c) if self.tp_ma_eng else None
            gating_val = self.gating_eng.update(c) if self.gating_eng else None

            just_entered = False
            if pending_signal is not None and state == "flat":
                direction = pending_signal["direction"]
                ep = pending_signal.get("entry_price", o)

                sl_level, risk_pts = self._compute_sl(direction, ep, prev_sl_ma_val)
                tp_level = self._compute_tp(direction, ep, risk_pts)

                trade_spread = self.spread_gen.generate()
                ep = self.spread_gen.apply_entry(ep, direction, trade_spread)
                sl_level = self.spread_gen.apply_sl(sl_level, direction, trade_spread)
                tp_level = self.spread_gen.apply_tp(tp_level, direction, trade_spread)

                if sl_level is not None:
                    risk_pts = abs(ep - sl_level)

                valid_entry = True
                if risk_pts is None or risk_pts <= 0:
                    valid_entry = False
                if sl_level is not None:
                    if direction == "long" and sl_level >= ep:
                        valid_entry = False
                    if direction == "short" and sl_level <= ep:
                        valid_entry = False
                if tp_level is not None:
                    if direction == "long" and tp_level <= ep:
                        valid_entry = False
                    if direction == "short" and tp_level >= ep:
                        valid_entry = False

                # ── Dynamic position sizing (centralized) ────────
                trade_lot = self.lot_size
                if valid_entry and self.sizing_mode in ("risk_pct", "risk_per_trade"):
                    if risk_pts is None or risk_pts <= 0:
                        valid_entry = False
                    else:
                        balance_now = self.starting_cap + cum

                        # ── Determine risk amount ────────────────
                        risk_amount = 0.0
                        if self.sizing_mode == "risk_pct":
                            risk_amount = balance_now * (self.risk_pct / 100.0)
                        elif self.scaling_enabled:
                            risk_amount, sc_log = compute_scaling_risk(
                                balance_now, self.scaling_per, self.scaling_risk)
                            if len(self.trades) < 5:
                                print(f"  {sc_log}")
                        else:
                            risk_amount = self.fixed_risk

                        # ── Compute lot size ─────────────────────
                        if risk_amount <= 0:
                            valid_entry = False
                        else:
                            trade_lot, sz_log, sz_ok = compute_position_size(
                                risk_amount, risk_pts, self.point_value,
                                self.min_lot, self.max_lot, self.lot_step,
                                self.commission_per_lot, self.dollar_per_point,
                            )
                            if len(self.trades) < 5:
                                print(f"  {sz_log}")
                            if not sz_ok or trade_lot is None:
                                valid_entry = False
                else:
                    trade_lot = self.lot_size

                if valid_entry:
                    open_t = dict(
                        id=len(self.trades)+1, direction=direction,
                        entry_bar=pending_signal.get("signal_bar", i),
                        entry_time=pending_signal.get("signal_time", bar["time"]),
                        entry_price=round(ep, 4),
                        sl_level=sl_level, tp_level=tp_level,
                        initial_sl=sl_level, initial_tp=tp_level,
                        risk_pts=risk_pts, initial_risk_pts=risk_pts,
                        mae=0.0, mfe=0.0, bars_held=0,
                        spread=round(trade_spread, 4),
                        lot_size=trade_lot,
                    )
                    state = direction
                    just_entered = False

            pending_signal = None

            if state != "flat" and open_t is not None and not just_entered:
                closed = self._check_exit(open_t, bar, i, sl_ma_val, tp_ma_val)
                if closed:
                    trade_spread = closed.get("spread", 0.0)
                    raw_exit = closed["exit_price"]
                    # Only apply exit spread for market-price exits.
                    # SL_HIT / TP_R exit prices already include spread from entry setup.
                    exit_reason = closed.get("exit_reason", "")
                    if exit_reason not in ("SL_HIT", "TP_R"):
                        closed["exit_price"] = round(
                            self.spread_gen.apply_exit(raw_exit, closed["direction"], trade_spread), 4)

                    self._finalize_trade(closed)
                    cum += closed["net_pnl"]
                    closed["cumulative_pnl"] = round(cum, 2)
                    self.trades.append(closed)
                    last_closed_pnl = closed["net_pnl"]

                    if len(self.trades) <= 5:
                        t = closed
                        print(f"  [TRADE #{t['id']}] {t['direction'].upper()} "
                              f"entry={t['entry_price']} exit={t['exit_price']} "
                              f"SL={t.get('sl_level')} TP={t.get('tp_level')} "
                              f"risk={t.get('risk_pts')}pts "
                              f"lot={t.get('lot_size')} "
                              f"spread={t.get('spread',0):.4f} "
                              f"points={t.get('points_pnl')} "
                              f"R={t.get('net_pnl_r')} "
                              f"gross=${t.get('gross_pnl')} "
                              f"comm=${t.get('commission')} "
                              f"net=${t.get('net_pnl')} "
                              f"reason={t.get('exit_reason')}")

                    if self.gating_enabled:
                        if closed["direction"] == "long":  long_locked = True
                        if closed["direction"] == "short": short_locked = True

                    open_t = None
                    state = "flat"

            if state != "flat" and open_t is not None and just_entered:
                d = open_t["direction"]; ep2 = open_t["entry_price"]
                hh = bar["high"]; ll = bar["low"]
                open_t["mae"] = max(open_t.get("mae",0), (ep2-ll) if d=="long" else (hh-ep2))
                open_t["mfe"] = max(open_t.get("mfe",0), (hh-ep2) if d=="long" else (ep2-ll))

            if self.gating_enabled and gating_val is not None:
                if long_locked  and c < gating_val: long_locked  = False
                if short_locked and c > gating_val: short_locked = False

            if state != "flat" and open_t:
                pts = (c - open_t["entry_price"]) if state == "long" \
                      else (open_t["entry_price"] - c)
                t_lot = open_t.get("lot_size", self.lot_size)
                unrealised = points_to_dollars(pts, t_lot, self.point_value, self.dollar_per_point)
            else:
                unrealised = 0.0
            eq = self.starting_cap + cum + unrealised
            self.equity_curve.append(round(eq, 2))
            peak = max(peak, eq)
            dd = eq - peak
            self.dd_curve.append(round(dd, 2))

            now = _time.perf_counter()
            should_emit = (
                i == 0 or
                i == self.n - 1 or
                (i % progress_interval == 0 and (now - last_emit_time) >= EMIT_MIN_INTERVAL)
            )
            if should_emit:
                last_emit_time = now
                oi = None
                if open_t:
                    oi = dict(direction=open_t["direction"],
                              entry_price=round(open_t["entry_price"],2),
                              sl=round(open_t["sl_level"],2) if open_t.get("sl_level") else None,
                              tp=round(open_t["tp_level"],2) if open_t.get("tp_level") else None)
                yield dict(type="progress", bar=i, total=self.n,
                           pct=round(i/max(self.n-1,1)*100,1),
                           equity=round(eq,2), drawdown=round(dd,2),
                           open_trade=oi, last_closed_pnl=last_closed_pnl)

            prev_sl_ma_val = sl_ma_val

            if state != "flat" or pending_signal is not None:
                continue
            if any(v is None for v in mv):
                continue

            ab = all(c > v for v in mv)
            bl = all(c < v for v in mv)
            bull = c > o
            bear = c < o

            if regime == "above" and not ab:
                regime = "neutral"; bc = 0
            elif regime == "below" and not bl:
                regime = "neutral"; bc2 = 0

            sig = None

            if self.entry_mode == "above_all_mas":
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

            elif self.entry_mode == "ma_cross" and len(self.ma_defs) >= 2 and i > 0:
                pm = ma_hist[i-1]
                if None not in (mv[0], mv[1], pm[0], pm[1]):
                    if pm[0] <= pm[1] and mv[0] > mv[1]: sig = "long"
                    elif pm[0] >= pm[1] and mv[0] < mv[1]: sig = "short"

            elif self.entry_mode == "trend_filter" and i > 0:
                lm = mv[-1]; plm = ma_hist[i-1][-1]; pc = self.bars[i-1]["close"]
                if lm and plm:
                    if pc <= plm and c > lm and bull:
                        sig = "long"
                    elif pc >= plm and c < lm and bear:
                        sig = "short"

            if self.require_candle_confirm:
                if sig == "long" and not bull:
                    sig = None
                if sig == "short" and not bear:
                    sig = None

            if sig == "long"  and self.gating_enabled and long_locked:  sig = None
            if sig == "short" and self.gating_enabled and short_locked: sig = None

            if sig:
                pending_signal = {
                    "direction": sig,
                    "entry_price": c,
                    "signal_bar": i,
                    "signal_time": bar["time"],
                }

        if state != "flat" and open_t:
            lb = self.bars[-1]; cp = lb["close"]
            d = open_t["direction"]
            open_t["mae"] = max(open_t.get("mae",0),
                                (open_t["entry_price"]-lb["low"]) if d=="long" else (lb["high"]-open_t["entry_price"]))
            open_t["mfe"] = max(open_t.get("mfe",0),
                                (lb["high"]-open_t["entry_price"]) if d=="long" else (open_t["entry_price"]-lb["low"]))
            closed = {**open_t,
                      "exit_bar": self.n-1, "exit_time": lb["time"],
                      "exit_price": round(cp,2), "exit_reason": "end_of_data",
                      "holding_seconds": abs(lb["time"]-open_t["entry_time"]),
                      "bars_held": self.n-1-open_t["entry_bar"]}
            trade_spread = closed.get("spread", 0.0)
            # end_of_data is a market-price exit, spread applies here
            closed["exit_price"] = round(
                self.spread_gen.apply_exit(closed["exit_price"], d, trade_spread), 4)
            self._finalize_trade(closed)
            cum += closed["net_pnl"]
            closed["cumulative_pnl"] = round(cum,2)
            self.trades.append(closed)

    # ── SL computation (LEGACY — UNTOUCHED) ─────────────────────
    def _compute_sl(self, direction, entry_price, sl_ma_val):
        mode = self.sl_mode
        if mode == "fixed":
            pts = float(self.sl_cfg.get("fixed_pts", 8))
            if pts <= 0: return None, None
            sl = (entry_price - pts) if direction == "long" else (entry_price + pts)
            return round(sl, 4), pts
        elif mode == "ma_snapshot":
            if sl_ma_val is None: return None, None
            if direction == "long" and sl_ma_val >= entry_price:
                return None, None
            if direction == "short" and sl_ma_val <= entry_price:
                return None, None
            risk = abs(entry_price - sl_ma_val)
            if risk <= 0: return None, None
            return round(sl_ma_val, 4), round(risk, 4)
        elif mode == "ma_cross":
            if sl_ma_val is None: return None, None
            if direction == "long" and sl_ma_val >= entry_price:
                return None, None
            if direction == "short" and sl_ma_val <= entry_price:
                return None, None
            risk = abs(entry_price - sl_ma_val)
            if risk <= 0: return None, None
            return None, round(risk, 4)
        return None, None

    def _compute_tp(self, direction, entry_price, risk_pts):
        mode = self.tp_mode
        if mode == "r_multiple":
            r = float(self.tp_cfg.get("r_multiple", 2))
            if risk_pts and risk_pts > 0:
                tp = (entry_price + risk_pts * r) if direction == "long" \
                     else (entry_price - risk_pts * r)
                return round(tp, 4)
        return None

    # ── Exit evaluation (LEGACY — UNTOUCHED) ────────────────────
    def _check_exit(self, t, bar, bi, sl_ma_val, tp_ma_val):
        d  = t["direction"]; ep = t["entry_price"]
        sl = t.get("sl_level")
        tp = t.get("tp_level")
        hh = bar["high"]; ll = bar["low"]; c = bar["close"]

        t["bars_held"] = bi - t["entry_bar"]
        t["mae"] = max(t.get("mae",0), (ep-ll) if d=="long" else (hh-ep))
        t["mfe"] = max(t.get("mfe",0), (hh-ep) if d=="long" else (ep-ll))

        sl_hit = False
        if sl is not None:
            if d == "long"  and ll <= sl: sl_hit = True
            if d == "short" and hh >= sl: sl_hit = True

        tp_hit = False
        if tp is not None:
            if d == "long"  and hh >= tp: tp_hit = True
            if d == "short" and ll <= tp: tp_hit = True

        if sl_hit and tp_hit:
            m = self.ambiguous_mode
            if m == "optimistic":
                return self._make_exit(t, bar, bi, tp, "TP_R")
            elif m == "nearest_to_open":
                if abs(bar["open"]-sl) <= abs(bar["open"]-tp):
                    return self._make_exit(t, bar, bi, sl, "SL_HIT")
                else:
                    return self._make_exit(t, bar, bi, tp, "TP_R")
            else:
                return self._make_exit(t, bar, bi, sl, "SL_HIT")

        if sl_hit: return self._make_exit(t, bar, bi, sl, "SL_HIT")
        if tp_hit: return self._make_exit(t, bar, bi, tp, "TP_R")

        if self.sl_mode == "ma_cross" and sl_ma_val is not None:
            if d == "long"  and c < sl_ma_val:
                return self._make_exit(t, bar, bi, c, "MA_SL_EXIT")
            if d == "short" and c > sl_ma_val:
                return self._make_exit(t, bar, bi, c, "MA_SL_EXIT")

        if self.tp_mode == "ma_cross" and tp_ma_val is not None:
            if d == "long"  and c < tp_ma_val:
                return self._make_exit(t, bar, bi, c, "MA_TP_EXIT")
            if d == "short" and c > tp_ma_val:
                return self._make_exit(t, bar, bi, c, "MA_TP_EXIT")

        return None

    def _make_exit(self, t, bar, bi, exit_price, reason):
        return {**t,
                "exit_bar": bi, "exit_time": bar["time"],
                "exit_price": round(exit_price, 4),
                "exit_reason": reason,
                "holding_seconds": abs(bar["time"] - t["entry_time"]),
                "bars_held": bi - t["entry_bar"]}

    def _finalize_trade(self, closed):
        commission = round(closed.get("lot_size", self.lot_size) * self.commission_per_lot, 5)
        trade_lot = closed.get("lot_size", self.lot_size)
        finalize_trade_pnl(
            closed,
            lot_size=trade_lot,
            point_value=self.point_value,
            dollar_per_point=self.dollar_per_point,
            commission=commission,
        )

    def _build_result(self):
        MAX_TRADES = 2000
        perf = {}
        build_t0 = _time.perf_counter()

        stats_t0 = _time.perf_counter()
        stats = compute_all_stats(
            trades=self.trades,
            equity_curve=self.equity_curve,
            bars=self.bars,
            starting_capital=self.starting_cap,
            lot_size=self.lot_size,
        )
        stats_t1 = _time.perf_counter()
        perf["stats_seconds"] = round(stats_t1 - stats_t0, 4)

        analytics_t0 = _time.perf_counter()
        analytics = compute_analytics(self.trades, self.bars,
                                      self.equity_curve, self.dd_curve,
                                      self.starting_cap)
        analytics_t1 = _time.perf_counter()
        perf["analytics_seconds"] = round(analytics_t1 - analytics_t0, 4)

        # Cap analytics arrays
        analytics = cap_analytics_arrays(analytics, 1500)

        # Cap trades in response
        total_count = len(self.trades)
        if total_count > MAX_TRADES:
            trades_out = self.trades[-MAX_TRADES:]
            print(f"  [TRADE CAP] Sending last {MAX_TRADES} of {total_count} trades")
        else:
            trades_out = self.trades

        result = dict(
            stats=stats,
            trades=trades_out,
            total_trade_count=total_count,
            trades_truncated=total_count > MAX_TRADES,
            equity_curve=ds_curve(self.equity_curve, 1500),
            drawdown_curve=ds_curve(self.dd_curve, 1500),
            analytics=analytics,
        )

        build_t1 = _time.perf_counter()
        perf["response_build_seconds"] = round(build_t1 - build_t0, 4)
        perf["trade_count"] = total_count
        result["performance"] = perf

        print(f"  [BUILD TIMING] stats={perf['stats_seconds']:.3f}s "
              f"analytics={perf['analytics_seconds']:.3f}s "
              f"response_build={perf['response_build_seconds']:.3f}s "
              f"trades={total_count}")

        return result


# ════════════════════════════════════════════════════════════════════
#  FLASK ROUTES (WITH TIMING)
# ════════════════════════════════════════════════════════════════════
def _validate_and_load(cfg):
    if not cfg: raise ValueError("Empty body")
    mas = cfg.get("mas",[])
    if not mas: raise ValueError("Need at least one MA")

    bars = load_bars(
        float(cfg.get("range", 10)),
        int(cfg.get("bar_range", 1000)),
        symbol=cfg.get("symbol", "NQ"),
        start_date=cfg.get("start_date"),
        end_date=cfg.get("end_date"),
    )

    max_period = 0
    for m in mas:
        p = int(m.get("period", 0))
        if p > max_period:
            max_period = p

    sl_cfg = cfg.get("sl", {})
    if sl_cfg.get("mode") in ("ma_cross", "ma_snapshot"):
        sp = int(sl_cfg.get("ma_length", 50))
        if sp > max_period:
            max_period = sp

    tp_cfg = cfg.get("tp", {})
    if tp_cfg.get("mode") == "ma_cross":
        tp = int(tp_cfg.get("ma_length", 9))
        if tp > max_period:
            max_period = tp

    gating_cfg = cfg.get("gating", {})
    if gating_cfg.get("enabled"):
        gp = int(gating_cfg.get("ma_length", 21))
        if gp > max_period:
            max_period = gp

    min_bars = max_period + int(math.sqrt(max(max_period, 1))) + 2
    min_bars = max(min_bars, 5)

    if len(bars) < min_bars:
        raise ValueError(
            f"Need at least {min_bars} bars for these indicators "
            f"(longest MA period = {max_period}), but only {len(bars)} bars available. "
            f"Try a larger date range or lower bar count."
        )

    return bars, cfg


@app.route("/api/backtest", methods=["POST"])
def run_backtest():
    try:
        route_t0 = _time.perf_counter()

        cfg=request.get_json(force=True); bars,cfg=_validate_and_load(cfg)

        sim_t0 = _time.perf_counter()
        engine=BacktestEngine(bars,cfg); result=engine.run()
        sim_t1 = _time.perf_counter()

        result = clean_for_json(result)

        json_t0 = _time.perf_counter()
        response_body = json.dumps(result)
        json_t1 = _time.perf_counter()

        payload_kb = len(response_body) / 1024
        route_t1 = _time.perf_counter()

        perf = result.get("performance", {})
        perf["simulation_seconds"] = round(sim_t1 - sim_t0, 4)
        perf["json_seconds"] = round(json_t1 - json_t0, 4)
        perf["total_seconds"] = round(route_t1 - route_t0, 4)
        perf["payload_size_kb"] = round(payload_kb, 1)

        print(f"  [BACKTEST TIMING] simulation={perf.get('simulation_seconds',0):.3f}s "
              f"stats={perf.get('stats_seconds',0):.3f}s "
              f"analytics={perf.get('analytics_seconds',0):.3f}s "
              f"json={perf.get('json_seconds',0):.3f}s "
              f"payload={payload_kb:.1f}KB "
              f"total={perf.get('total_seconds',0):.3f}s "
              f"trades={perf.get('trade_count',0)}")

        return Response(response_body, mimetype="application/json"), 200
    except ValueError as e: return jsonify(error=str(e)),400
    except FileNotFoundError as e: return jsonify(error=str(e)),404
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify(error=str(e)),500


@app.route("/api/backtest/stream", methods=["POST"])
def run_backtest_stream():
    try:
        cfg=request.get_json(force=True); bars,cfg=_validate_and_load(cfg)
    except ValueError as e: return jsonify(error=str(e)),400
    except FileNotFoundError as e: return jsonify(error=str(e)),404
    except Exception as e: return jsonify(error=str(e)),500
    engine=BacktestEngine(bars,cfg)
    def generate():
        try:
            for msg in engine.run_streaming():
                yield json.dumps(clean_for_json(msg))+"\n"
        except Exception as e:
            import traceback; traceback.print_exc()
            yield json.dumps({"type": "error", "error": str(e)}) + "\n"
    return Response(stream_with_context(generate()), mimetype='application/x-ndjson',
        headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no','Access-Control-Allow-Origin':'*'})


@app.route("/api/ranges/<symbol>", methods=["GET"])
def get_ranges(symbol):
    folder = os.path.join(DATA_DIR, symbol)
    if not os.path.isdir(folder):
        return jsonify([]), 200
    ranges = []
    for f in os.listdir(folder):
        if f.endswith("pt.json"):
            try:
                val = float(f.replace("pt.json", ""))
                ranges.append(val)
            except ValueError:
                pass
    ranges.sort()
    return jsonify(ranges), 200


@app.route("/api/health", methods=["GET"])
def health(): return jsonify(status="ok"),200


if __name__ == "__main__":
    print("="*52+"\n  NQ Backtest Server  http://localhost:5000\n"+"="*52)
    app.run(host="0.0.0.0", port=5000, debug=False)
```

---

## FILE: `STRATEGY_GUIDE.txt`

```text
STRATEGY GUIDE
================

Purpose
-------
This guide explains how to create new Python strategy modules that plug into the strategy-loader backtester system.

It is written for:
- human developers
- AI coding agents
- future maintainers of the project

The goal is simple:
1. Put a strategy file in the correct folder.
2. Define the required strategy class and metadata.
3. Request the indicators you need.
4. Implement entry/exit logic in the expected format.
5. Let the engine handle execution, state management, commissions, sizing, and reporting.


1) How the Strategy Plugin System Works
---------------------------------------

The project uses a plugin architecture for strategies.

Core idea:
- strategy logic lives in separate Python modules
- the loader discovers available strategy files automatically
- the frontend requests the list of available strategies from the backend
- when the user selects a strategy in the Backtest tab, the frontend loads that strategy's metadata + parameter schema
- the frontend builds the parameter editor automatically
- the engine executes the selected strategy bar-by-bar

Main folders
------------
Expected backend layout:

backend/
  strategy_loader.py
  strategy_api.py
  engine_core.py
  strategies/
    __init__.py
    base.py
    ema_hma_trend.py
    breakout_retest.py
    your_new_strategy.py

Important locations:
- backend/strategies/ = all strategy plugins live here
- backend/strategies/base.py = shared base class / required interface
- backend/strategy_loader.py = discovers and loads strategy classes
- backend/strategy_api.py = API endpoints for listing strategies and running them
- backend/engine_core.py = reusable backtest engine

How discovery works
-------------------
The loader scans backend/strategies/ for Python modules.
It skips files such as:
- base.py
- __init__.py
- private files starting with underscore

Each valid module is imported.
The loader searches the module for classes that inherit from BaseStrategy.
Those classes are instantiated and registered by strategy_id.

How a strategy is selected from the frontend
--------------------------------------------
Typical flow:
1. Frontend calls GET /api/strategies
2. Backend returns available strategies (id, name, description, metadata)
3. User chooses a strategy in Load Strategy Mode
4. Frontend calls GET /api/strategy/<id>
5. Backend returns full metadata + parameter schema + engine schema/defaults
6. Frontend auto-renders the parameter editor
7. User clicks Run Backtest
8. Frontend sends POST /api/backtest/run with:
   - strategy_id
   - strategy parameters
   - engine settings
   - dataset config
   - capital / commission config
9. Backend loads bars, precomputes indicators, runs the engine, and returns results


2) Strategy File Structure
--------------------------

Where to put the file
---------------------
Create the strategy as a separate Python file inside:

backend/strategies/

Example:
backend/strategies/my_strategy.py

Required imports
----------------
At minimum, import the base strategy class:

from backend.strategies.base import BaseStrategy

You may also import:
- typing helpers
- math
- any safe standard-library utilities

Avoid importing heavy frameworks unless truly necessary.

Class naming convention
-----------------------
Use a clear class name that ends with Strategy.
Examples:
- MyStrategy
- EmaHmaTrendStrategy
- BreakoutRetestStrategy

The file name does not have to exactly match the class name, but keeping them aligned is strongly recommended.

Recommended file pattern
------------------------
Example:

backend/strategies/my_strategy.py

class MyStrategy(BaseStrategy):
    strategy_id = "my_strategy"
    name = "My Strategy"
    description = "Short description"
    parameters = {...}
    indicators_required = [...]

    def on_bar(self, i, bar, indicators, state, position, bars, params):
        ...
        return {}

How a strategy is registered
----------------------------
You do NOT manually register the strategy in a central list.
The loader discovers it automatically if:
- the file is in backend/strategies/
- the module imports successfully
- the class inherits from BaseStrategy
- the class defines a valid strategy_id


3) Required Strategy Interface / Methods
----------------------------------------

Every strategy must inherit from BaseStrategy.

Required class attributes
-------------------------
These are expected on the strategy class:

1. strategy_id
   Unique machine-friendly ID.
   Example:
   strategy_id = "ema_hma_trend"

2. name
   Human-friendly display name.
   Example:
   name = "EMA + HMA Trend Pullback"

3. description
   Plain English summary shown in the frontend.

4. parameters
   Parameter schema used by the frontend to auto-build the editor.

5. indicators_required
   List describing which indicators must be precomputed before the engine runs.

Main required method
--------------------
The key method is:

on_bar(self, i, bar, indicators, state, position, bars, params)

This method is called sequentially for each bar.

Expected parameters:
- i
  Current bar index (integer)

- bar
  Current bar dictionary

- indicators
  Dictionary containing:
  - current = current indicator values for this bar
  - series = full precomputed arrays for each requested indicator

- state
  Mutable strategy/engine state dictionary
  Use this carefully for tracking custom state between bars

- position
  Current open position view, or None if flat
  This is a read-only view intended for strategy logic decisions

- bars
  Full bar list (historical + current)
  Never use future bars beyond index i

- params
  Validated strategy parameters after defaults/min/max/type processing

Return value
------------
on_bar should return a dictionary describing an action, or an empty dictionary if no action is needed.

Typical return keys:
- enter
- entry_price
- stop_loss
- take_profit
- entry_reason
- exit
- exit_price
- exit_reason

Allowed action examples are documented later in this guide.

Optional methods
----------------
Depending on your BaseStrategy implementation, the following hooks may also exist:

- validate_parameters(...)
  Usually inherited from BaseStrategy
  Validates and merges defaults

- build_indicators_required(params)
  Usually inherited from BaseStrategy
  Resolves dynamic indicator periods from parameter references

- engine_parameter_overrides(params)
  Optional hook that lets a strategy suggest engine defaults
  Example uses:
  - enable trailing by MA
  - default fixed-R take profit
  - default stacking behavior

If your base class supports engine_parameter_overrides, only use it to suggest defaults.
Do not try to bypass the engine.


4) Available Data Fields Per Bar
--------------------------------

Each bar passed into the strategy is a dictionary.
Expected bar fields:

- time
  Numeric timestamp
  Usually epoch seconds (or normalized server timestamp)

- open
  Open price

- high
  High price

- low
  Low price

- close
  Close price

- volume
  Volume value if available

Possible internal field:
- index
  The engine may internally add the current bar index when running.
  Do not rely on this unless your current engine explicitly provides it.

Example bar
-----------
{
  "time": 1712345678,
  "open": 18345.25,
  "high": 18348.75,
  "low": 18344.50,
  "close": 18347.00,
  "volume": 120.0
}

Best practice
-------------
Use the current bar only through:
- bar["open"]
- bar["high"]
- bar["low"]
- bar["close"]
- bar["volume"]

When using bars history, only access:
- bars[:i+1]
- bars[i]
- bars[i-1]
- bars[max(0, i-lookback):i+1]

Never read bars after i.


5) Available Indicators + How to Request Them
---------------------------------------------

Supported indicators
--------------------
The engine may support these indicator kinds (depending on your current engine implementation):

- SMA
- EMA
- WMA
- HMA
- VWAP
- CHOPPINESS
- HIGHEST_HIGH
- LOWEST_LOW

How to request indicators
-------------------------
Use indicators_required on the strategy class.

Example:
indicators_required = [
    {"key": "fast_ema", "kind": "EMA", "source": "close", "period_param": "fast_ema"},
    {"key": "trend_hma", "kind": "HMA", "source": "close", "period_param": "trend_hma"},
]

Fields
------
- key
  Name used inside the strategy to read the indicator

- kind
  Indicator type, for example EMA or HMA

- source
  Which bar field to use, usually one of:
  - close
  - open
  - high
  - low

- period
  Fixed lookback period

- period_param
  Name of a strategy parameter whose value becomes the period

Fixed-period example
--------------------
indicators_required = [
    {"key": "vwap", "kind": "VWAP", "source": "close"},
    {"key": "slow_sma", "kind": "SMA", "source": "close", "period": 200},
]

How to read indicators in on_bar
--------------------------------
Current values:

fast = indicators["current"].get("fast_ema")
trend = indicators["current"].get("trend_hma")

Full series:

fast_series = indicators["series"]["fast_ema"]
prev_fast = fast_series[i - 1] if i > 0 else None

Best practice
-------------
Always guard against None values.
Indicator arrays often contain None until enough historical bars exist.

Example:
if fast is None or trend is None:
    return {}


6) Trade Engine Constraints (IMPORTANT)
---------------------------------------

These rules matter. If you ignore them, your strategy will produce wrong or unstable results.

Single position by default
--------------------------
The engine assumes one open position by default.
Do not assume you can open multiple independent positions unless the engine + strategy explicitly allow stacking or pyramiding.

Bar-by-bar sequential execution
-------------------------------
The engine runs in order, one bar at a time.
The strategy should behave like this:
- observe current bar and past data only
- make a decision
- engine handles the resulting order/action according to execution rules

Do not write strategy logic that depends on future candles.

Bar-close decision model
------------------------
Most strategies should treat the signal as being decided on the current bar close.
Be consistent.
If your engine is configured for bar-close logic, do not simulate intrabar hindsight.

Entry timing
------------
Use the project convention that is currently implemented.
Many systems signal on one bar and execute on the next bar open.
Some simplified versions may enter at the current close.

Important:
When writing a new strategy, check the current engine behavior and follow it consistently.
Do not mix entry-at-close and entry-at-next-open assumptions inside the same project.

Exits
-----
The engine may support universal exit management, such as:
- fixed SL points
- fixed TP points
- fixed R take profit
- last-bar low/high stop
- trailing stop by points
- trailing stop by MA
- break-even after +X R
- partial take profit
- scaling out at R levels

A strategy may:
- provide explicit stop_loss / take_profit in the returned action
- or rely on engine-level exit configuration

Commission handling
-------------------
The engine handles commission.
Typical supported models:
- flat per trade
- per side
- per contract

Do not manually subtract commission inside the strategy.

Position sizing
---------------
The engine may support:
- fixed lot size
- percent risk sizing based on stop distance and account balance

Do not calculate final PnL inside the strategy.
That is the engine's job.

R-multiple calculation
----------------------
R is usually based on the initial stop distance.
Typical formula:

For long:
R = (exit_price - entry_price) / (entry_price - stop_loss)

For short:
R = (entry_price - exit_price) / (stop_loss - entry_price)

If the engine already computes R internally, do not override it in strategy logic.

Scaling rules
-------------
If scaling in/out is enabled at the engine level:
- treat trade slices carefully
- the engine may output multiple trade records for a single position group
- do not assume one entry equals one final exit record


7) Strategy Output / Actions
----------------------------

A strategy should return a dictionary.
If nothing should happen, return:

{}

Common action keys
------------------

Enter long
----------
{
  "enter": "long",
  "entry_price": bar["close"],
  "stop_loss": 18340.0,
  "take_profit": 18360.0,
  "entry_reason": "ema_reclaim"
}

Enter short
-----------
{
  "enter": "short",
  "entry_price": bar["close"],
  "stop_loss": 18355.0,
  "take_profit": 18330.0,
  "entry_reason": "breakdown"
}

Close existing trade
--------------------
{
  "exit": True,
  "exit_price": bar["close"],
  "exit_reason": "trend_invalidated"
}

Minimal exit signal
-------------------
If the engine is allowed to use the current close by default:

{
  "exit": True,
  "exit_reason": "signal_exit"
}

Notes
-----
- enter should be either "long" or "short"
- exit should be True
- entry_price / exit_price should be numeric if supplied
- stop_loss / take_profit should be numeric or omitted
- entry_reason / exit_reason should be plain strings for auditability

Trailing / break-even / scaling from strategy
---------------------------------------------
If your engine supports custom runtime overrides, use the supported project pattern.
If not, do NOT invent custom keys.
Only return keys the engine understands.

Best practice:
- let engine settings handle trailing / break-even / partials
- keep strategy output focused on directional logic + optional SL/TP hints


8) What Is NOT Allowed / Common Mistakes
----------------------------------------

1. Repainting / using future bars
---------------------------------
Wrong:
future_close = bars[i + 1]["close"]

This invalidates backtest results.

2. Ignoring None indicator values
---------------------------------
Indicators usually return None before enough history exists.
Always guard against missing values.

3. Conflicting signals on the same bar
--------------------------------------
Do not return both:
- enter long
- enter short

Do not return both enter and exit for different intentions unless your engine explicitly supports that exact behavior.

4. Modifying global state unsafely
----------------------------------
Avoid module-level mutable variables.
Use the provided state object if you need per-run memory.

Bad:
last_signal = "long"   # global module variable

Better:
state.setdefault("my_strategy", {})
state["my_strategy"]["last_signal"] = "long"

5. Assuming position is always present
--------------------------------------
When flat, position may be None.
Always check first.

6. Assuming position is a custom object if current engine passes a dict
-----------------------------------------------------------------------
Use the current documented project format consistently.
If the engine passes a dict-like position view, read it as a dict.

7. Wrong bar index for lookbacks
--------------------------------
Be careful with ranges.
Example safe pattern:
start = max(0, i - lookback + 1)
window = bars[start:i+1]

8. Writing strategy-level PnL accounting
----------------------------------------
Do not update balance, commission, or account equity inside the strategy.
That belongs to the engine.

9. Returning unsupported keys
-----------------------------
Do not invent random action keys unless the engine explicitly supports them.
Stick to documented action fields.

10. Forgetting unique strategy_id
---------------------------------
If two strategies use the same strategy_id, loader registration will fail or become ambiguous.


9) Example Strategy Template
----------------------------

Below is a complete minimal example.
It requests indicators, defines parameters, enters trades, and exits trades.

Example file: backend/strategies/example_cross_strategy.py

from backend.strategies.base import BaseStrategy


class ExampleCrossStrategy(BaseStrategy):
    strategy_id = "example_cross_strategy"
    name = "Example EMA Trend Cross"
    description = "Simple example strategy using EMA + HMA trend confirmation."

    parameters = {
        "_group_filters": {
            "type": "group",
            "label": "Filters",
        },
        "fast_ema": {
            "type": "number",
            "label": "Fast EMA",
            "default": 20,
            "min": 2,
            "max": 500,
            "step": 1,
            "integer": True,
            "group": "Filters",
        },
        "trend_hma": {
            "type": "number",
            "label": "Trend HMA",
            "default": 55,
            "min": 2,
            "max": 500,
            "step": 1,
            "integer": True,
            "group": "Filters",
        },
        "reward_r": {
            "type": "number",
            "label": "Reward R",
            "default": 2.0,
            "min": 0.5,
            "max": 10.0,
            "step": 0.25,
            "group": "Filters",
        },
        "stop_buffer": {
            "type": "number",
            "label": "Stop Buffer",
            "default": 1.0,
            "min": 0.0,
            "max": 20.0,
            "step": 0.25,
            "group": "Filters",
        },
    }

    indicators_required = [
        {"key": "fast_ema", "kind": "EMA", "source": "close", "period_param": "fast_ema"},
        {"key": "trend_hma", "kind": "HMA", "source": "close", "period_param": "trend_hma"},
    ]

    def on_bar(self, i, bar, indicators, state, position, bars, params):
        if i < 2:
            return {}

        fast = indicators["current"].get("fast_ema")
        trend = indicators["current"].get("trend_hma")
        prev_fast = indicators["series"]["fast_ema"][i - 1]
        prev_trend = indicators["series"]["trend_hma"][i - 1]

        if fast is None or trend is None or prev_fast is None or prev_trend is None:
            return {}

        close_ = bar["close"]
        prev_close = bars[i - 1]["close"]

        # Exit logic first
        if position:
            if position["direction"] == "long" and close_ < fast:
                return {
                    "exit": True,
                    "exit_reason": "close_below_fast_ema"
                }

            if position["direction"] == "short" and close_ > fast:
                return {
                    "exit": True,
                    "exit_reason": "close_above_fast_ema"
                }

            return {}

        bullish_trend = close_ > trend and fast > trend
        bearish_trend = close_ < trend and fast < trend

        crossed_up = prev_close <= prev_fast and close_ > fast
        crossed_down = prev_close >= prev_fast and close_ < fast

        if bullish_trend and crossed_up:
            stop_loss = bar["low"] - params["stop_buffer"]
            risk = max(0.01, close_ - stop_loss)
            take_profit = close_ + risk * params["reward_r"]
            return {
                "enter": "long",
                "entry_price": close_,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "entry_reason": "ema_cross_long"
            }

        if bearish_trend and crossed_down:
            stop_loss = bar["high"] + params["stop_buffer"]
            risk = max(0.01, stop_loss - close_)
            take_profit = close_ - risk * params["reward_r"]
            return {
                "enter": "short",
                "entry_price": close_,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "entry_reason": "ema_cross_short"
            }

        return {}


Quick Checklist
---------------

Before saving a new strategy, verify all of this:

[ ] File is inside backend/strategies/
[ ] Class inherits from BaseStrategy
[ ] strategy_id is unique
[ ] name and description are filled in
[ ] parameters schema is valid
[ ] indicators_required only requests supported indicators
[ ] on_bar(...) exists
[ ] on_bar returns only supported action keys
[ ] Strategy never reads future bars
[ ] Strategy guards against None indicator values
[ ] Strategy does not modify account balance directly
[ ] Strategy does not use unsupported/global side effects
[ ] Strategy handles flat vs open-position logic clearly
[ ] Entry and exit reasons are human-readable
[ ] Lookbacks use safe index ranges

Recommended workflow
--------------------
1. Copy the example template.
2. Rename the class and strategy_id.
3. Adjust parameters.
4. Request required indicators.
5. Implement long logic.
6. Implement short logic.
7. Implement exit logic.
8. Run the backtester on a small dataset first.
9. Check the trade log and metrics.
10. Only then test larger datasets.

End of guide.
```
