import numpy as np
import random


def _resolve_sl_arr(sl_model, cfg, side, entry_px, o, h, l, c, i, sl_override=None):
    if sl_model == "fixed":
        pts = sl_override if sl_override is not None else cfg.sl_fixed_pts
        return entry_px - pts if side == "LONG" else entry_px + pts
    if sl_model == "entry_bar":
        return float(l[i]) if side == "LONG" else float(h[i])
    if sl_model == "swing":
        s = max(0, i - cfg.swing_lookback + 1)
        return float(l[s:i+1].min()) if side == "LONG" else float(h[s:i+1].max())
    if sl_model == "time":
        return None
    raise ValueError(sl_model)


def simulate(ctx, signals, cfg, sl_model="entry_bar", K=2, seed=0,
             sl_override=None, lite=False):
    """
    Bounded simulator. Iterates only from first signal index to last+K_max.
    `lite=True` skips trade dict storage — returns aggregate metrics only.
                Much faster + zero allocation churn for the optimizer sweep.
    """
    o, h, l, c = ctx.o, ctx.h, ctx.l, ctx.c
    n = ctx.n
    if not signals:
        return ([], [(0, 0.0)]) if not lite else _empty_lite()

    # bounded scan range
    first_i = min(s[0] for s in signals)
    last_i  = min(n, max(s[0] for s in signals) + K + 2)

    sig_map = {}
    for i, side in signals:
        sig_map.setdefault(i, []).append(side)

    if lite:
        return _simulate_lite(ctx, sig_map, cfg, sl_model, K, sl_override,
                              first_i, last_i)

    trades = []
    open_trade = None
    eq_pts = 0.0
    eq_curve = [(first_i, 0.0)]

    for i in range(first_i, last_i):
        bar_h = h[i]; bar_l = l[i]; bar_c = c[i]

        if open_trade is not None:
            ot = open_trade
            exit_px, reason = None, None

            if ot["sl"] is not None:
                if ot["side"] == "LONG"  and bar_l <= ot["sl"]:
                    exit_px = ot["sl"]; reason = "SL"
                elif ot["side"] == "SHORT" and bar_h >= ot["sl"]:
                    exit_px = ot["sl"]; reason = "SL"

            if exit_px is None and ot["tp"] is not None:
                if ot["side"] == "LONG"  and bar_h >= ot["tp"]:
                    exit_px = ot["tp"]; reason = "TP"
                elif ot["side"] == "SHORT" and bar_l <= ot["tp"]:
                    exit_px = ot["tp"]; reason = "TP"

            if exit_px is None and i - ot["entry_idx"] >= ot["K"]:
                exit_px = bar_c; reason = "TIME"

            if ot["side"] == "LONG":
                ot["mae"] = min(ot["mae"], bar_l - ot["entry_px"])
                ot["mfe"] = max(ot["mfe"], bar_h - ot["entry_px"])
            else:
                ot["mae"] = min(ot["mae"], ot["entry_px"] - bar_h)
                ot["mfe"] = max(ot["mfe"], ot["entry_px"] - bar_l)

            if exit_px is not None:
                slip = cfg.slippage_pts
                if ot["side"] == "LONG":
                    fill_exit = exit_px - slip
                    gross_pts = fill_exit - ot["fill_entry"]
                else:
                    fill_exit = exit_px + slip
                    gross_pts = ot["fill_entry"] - fill_exit
                net_usd = gross_pts * cfg.dollar_per_pt - cfg.commission_usd
                ot.update({
                    "exit_idx": i, "exit_px": float(exit_px),
                    "fill_exit": float(fill_exit), "reason": reason,
                    "gross_pts": float(gross_pts), "net_usd": float(net_usd),
                })
                trades.append(ot)
                eq_pts += gross_pts
                eq_curve.append((i, eq_pts))
                open_trade = None

        if (open_trade is None or cfg.allow_overlap) and i in sig_map:
            for side in sig_map[i]:
                if open_trade is not None and not cfg.allow_overlap: break
                entry_px = float(bar_c)
                fill_entry = (entry_px + cfg.slippage_pts) if side == "LONG" \
                             else (entry_px - cfg.slippage_pts)
                sl = _resolve_sl_arr(sl_model, cfg, side, entry_px,
                                     o, h, l, c, i, sl_override)
                tp = None
                if cfg.take_profit_pts:
                    tp = (entry_px + cfg.take_profit_pts) if side == "LONG" \
                         else (entry_px - cfg.take_profit_pts)
                open_trade = {
                    "entry_idx": i, "side": side,
                    "entry_px": entry_px, "fill_entry": fill_entry,
                    "sl": sl, "tp": tp, "K": K,
                    "mae": 0.0, "mfe": 0.0,
                }

    return trades, eq_curve


def _empty_lite():
    return {"trades": 0, "wins": 0, "losses": 0, "net_usd": 0.0,
            "net_pts": 0.0, "gross_win": 0.0, "gross_loss": 0.0,
            "sum_sq": 0.0, "max_dd_pts": 0.0,
            "exits_sl": 0, "exits_tp": 0, "exits_time": 0}


def _simulate_lite(ctx, sig_map, cfg, sl_model, K, sl_override, first_i, last_i):
    """Sim that returns ONLY aggregate metrics. No trade dicts kept."""
    o, h, l, c = ctx.o, ctx.h, ctx.l, ctx.c
    n = ctx.n
    allow_overlap = cfg.allow_overlap

    trades_n = wins = losses = 0
    net_usd = net_pts = gross_win = gross_loss = sum_sq = 0.0
    exits_sl = exits_tp = exits_time = 0
    eq_pts = 0.0; peak = 0.0; max_dd = 0.0

    # open-trade state (flat ints/floats — no dict allocation)
    has_open = False
    o_side = 0          # 1 long, -1 short
    o_entry_idx = 0
    o_fill_entry = 0.0
    o_sl = 0.0; o_has_sl = False
    o_tp = 0.0; o_has_tp = False
    o_K = K

    slip = cfg.slippage_pts
    comm = cfg.commission_usd
    dpt  = cfg.dollar_per_pt
    use_tp = bool(cfg.take_profit_pts)
    tp_pts = cfg.take_profit_pts or 0.0

    for i in range(first_i, last_i):
        bar_h = h[i]; bar_l = l[i]; bar_c = c[i]

        if has_open:
            exit_px = None; reason = 0   # 1=SL 2=TP 3=TIME
            if o_has_sl:
                if o_side == 1 and bar_l <= o_sl:
                    exit_px = o_sl; reason = 1
                elif o_side == -1 and bar_h >= o_sl:
                    exit_px = o_sl; reason = 1
            if exit_px is None and o_has_tp:
                if o_side == 1 and bar_h >= o_tp:
                    exit_px = o_tp; reason = 2
                elif o_side == -1 and bar_l <= o_tp:
                    exit_px = o_tp; reason = 2
            if exit_px is None and i - o_entry_idx >= o_K:
                exit_px = bar_c; reason = 3

            if exit_px is not None:
                if o_side == 1:
                    fill_exit = exit_px - slip
                    gpts = fill_exit - o_fill_entry
                else:
                    fill_exit = exit_px + slip
                    gpts = o_fill_entry - fill_exit
                nusd = gpts * dpt - comm

                trades_n += 1
                net_usd  += nusd
                net_pts  += gpts
                sum_sq   += nusd * nusd
                if nusd > 0: wins += 1; gross_win  += nusd
                else:        losses += 1; gross_loss += nusd
                if reason == 1: exits_sl   += 1
                elif reason == 2: exits_tp += 1
                else: exits_time += 1

                eq_pts += gpts
                if eq_pts > peak: peak = eq_pts
                dd = peak - eq_pts
                if dd > max_dd: max_dd = dd

                has_open = False

        if (not has_open or allow_overlap) and i in sig_map:
            for side_s in sig_map[i]:
                if has_open and not allow_overlap: break
                side_i = 1 if side_s == "LONG" else -1
                entry_px = float(bar_c)
                fill_entry = entry_px + slip if side_i == 1 else entry_px - slip
                sl_val = _resolve_sl_arr(sl_model, cfg, side_s, entry_px,
                                         o, h, l, c, i, sl_override)
                has_open    = True
                o_side      = side_i
                o_entry_idx = i
                o_fill_entry = float(fill_entry)
                o_has_sl    = sl_val is not None
                o_sl        = float(sl_val) if o_has_sl else 0.0
                o_has_tp    = use_tp
                if use_tp:
                    o_tp = entry_px + tp_pts if side_i == 1 else entry_px - tp_pts

    pf = (gross_win / abs(gross_loss)) if gross_loss < 0 else (float("inf") if gross_win > 0 else 0.0)
    mean_usd = net_usd / trades_n if trades_n else 0.0
    if trades_n > 1:
        var = max(sum_sq / trades_n - mean_usd * mean_usd, 0.0)
        std = var ** 0.5
    else:
        std = 0.0
    sharpe = mean_usd / std if std > 0 else 0.0

    return {
        "trades": trades_n,
        "wins": wins, "losses": losses,
        "win_rate": (wins / trades_n * 100) if trades_n else 0.0,
        "net_usd": net_usd, "net_pts": net_pts,
        "expectancy": mean_usd,
        "expectancy_pts": net_pts / trades_n if trades_n else 0.0,
        "profit_factor": pf,
        "sharpe": sharpe,
        "max_dd_pts": max_dd,
        "exits_sl": exits_sl, "exits_tp": exits_tp, "exits_time": exits_time,
    }


def metrics(trades, eq_curve):
    n = len(trades)
    if n == 0:
        return {"trades": 0, "net_usd": 0.0, "net_pts": 0.0,
                "win_rate": 0.0, "expectancy": 0.0, "expectancy_pts": 0.0,
                "profit_factor": 0.0, "sharpe": 0.0, "max_dd_pts": 0.0,
                "avg_mae": 0.0, "avg_mfe": 0.0,
                "exits_sl": 0, "exits_tp": 0, "exits_time": 0}
    pts = np.array([t["gross_pts"] for t in trades])
    usd = np.array([t["net_usd"]   for t in trades])
    wins = usd > 0
    gw = float(usd[wins].sum())  if wins.any()    else 0.0
    gl = float(usd[~wins].sum()) if (~wins).any() else 0.0
    pf = (gw / abs(gl)) if gl < 0 else float("inf")
    ys = np.array([y for _, y in eq_curve])
    peak = np.maximum.accumulate(ys)
    max_dd = float((peak - ys).max()) if len(ys) else 0.0
    sharpe = float(usd.mean() / usd.std()) if usd.std() > 0 else 0.0
    return {
        "trades": n,
        "net_usd": float(usd.sum()),
        "net_pts": float(pts.sum()),
        "win_rate": float(wins.mean() * 100),
        "expectancy": float(usd.mean()),
        "expectancy_pts": float(pts.mean()),
        "profit_factor": float(pf),
        "sharpe": sharpe,
        "max_dd_pts": max_dd,
        "avg_mae": float(np.mean([t["mae"] for t in trades])),
        "avg_mfe": float(np.mean([t["mfe"] for t in trades])),
        "exits_sl":   sum(1 for t in trades if t["reason"] == "SL"),
        "exits_tp":   sum(1 for t in trades if t["reason"] == "TP"),
        "exits_time": sum(1 for t in trades if t["reason"] == "TIME"),
    }