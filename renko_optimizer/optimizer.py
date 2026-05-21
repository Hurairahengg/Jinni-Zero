import gc
import numpy as np
from miner import mine_patterns, summarize_table
from simulator import simulate, metrics
from validation import walk_forward_folds, stability_score


def _signals_from_index(ctx, L, pat_tuple, side, idx_lo, idx_hi):
    per_L = ctx.pattern_index.get(L, {})
    arr = per_L.get(pat_tuple)
    if arr is None or arr.size == 0: return []
    lo = np.searchsorted(arr, idx_lo, side="left")
    hi = np.searchsorted(arr, idx_hi, side="left")
    if hi <= lo: return []
    return [(int(i), side) for i in arr[lo:hi]]


def score_rule(m, cfg):
    if m["trades"] == 0: return -1e9
    obj = cfg.objective
    if obj == "net_profit":     return m["net_usd"]
    if obj == "profit_factor":  return m["profit_factor"] if m["profit_factor"] != float("inf") else 1e6
    if obj == "sharpe":         return m["sharpe"]
    if obj == "expectancy":     return m["expectancy"]
    if obj == "score":
        w = cfg.score_weights
        return (w.get("expectancy", 0) * m["expectancy"]
              + w.get("pf", 0)         * min(m["profit_factor"], 10)
              + w.get("sharpe", 0)     * m["sharpe"]
              + w.get("stability", 0)  * m.get("stability", 0))
    return m["expectancy"]


def run_optimizer(ctx, cfg):
    """
    Memory-efficient walk-forward optimizer.

    Phase 1 (sweep): for each fold, mine + evaluate every (rule × SL variant)
                     and store ONLY scalar metrics. No trade lists kept.
    Phase 2 (rebuild): re-simulate the final top-N rules across all folds
                       with the FULL simulator to populate trades for export.
    """
    n = ctx.n
    if cfg.split_mode == "train_test":
        folds = [(0, int(n * cfg.train_frac),
                  int(n * cfg.train_frac), n - cfg.K_max - 1)]
    else:
        folds = list(walk_forward_folds(n, cfg.wf_train_bars,
                                        cfg.wf_test_bars, cfg.wf_step))
    print(f"  [optimizer] folds: {len(folds)}")

    # rule_key -> {fold_idx: metrics_dict}
    fold_metrics = {}

    for fi, (tr_lo, tr_hi, te_lo, te_hi) in enumerate(folds):
        print(f"  [fold {fi+1}/{len(folds)}] train[{tr_lo}:{tr_hi}] "
              f"test[{te_lo}:{te_hi}]")

        # mine on train
        table = mine_patterns(ctx, ctx.c, range(cfg.L_min, cfg.L_max + 1),
                              cfg.K_max, idx_range=(tr_lo, tr_hi))
        ranked = summarize_table(table, min_n=cfg.min_occurrences,
                                 min_edge=cfg.min_edge)
        table = None  # release immediately
        gc.collect()

        candidates = ranked[: min(len(ranked), 200)]
        ranked = None
        sl_grid = cfg.sl_grid()
        print(f"    candidates={len(candidates)}  sl_grid={sl_grid}")

        evals = 0
        for r in candidates:
            L = r["L"]; pat = tuple(r["pattern"]); side = r["side"]; K = r["K"]
            sigs = _signals_from_index(ctx, L, pat, side, te_lo, te_hi)
            if not sigs: continue
            for sl_model in cfg.sl_models:
                sl_values = sl_grid if sl_model == "fixed" else [None]
                for sl_val in sl_values:
                    tag = f"{sl_model}@{sl_val}" if sl_val is not None else sl_model
                    rk = (L, pat, side, K, tag)
                    m = simulate(ctx, sigs, cfg, sl_model=sl_model, K=K,
                                 sl_override=sl_val, lite=True)
                    if m["trades"] == 0: continue
                    m["train_n"]    = r["n"]
                    m["train_exp"]  = r["expectancy"]
                    fold_metrics.setdefault(rk, {})[fi] = m
                    evals += 1

        candidates = None
        gc.collect()
        print(f"    completed evals={evals}   rules tracked so far={len(fold_metrics):,}")

    # ── aggregate ──
    print(f"  [aggregate] merging {len(fold_metrics):,} rule variants …")
    leaderboard = []
    min_folds = max(1, len(folds) // 2)

    for rk, fmap in fold_metrics.items():
        if len(fmap) < min_folds: continue
        L, pat, side, K, sl_tag = rk
        if "@" in sl_tag:
            sl_model, sl_param_str = sl_tag.split("@", 1)
            sl_param = float(sl_param_str)
        else:
            sl_model, sl_param = sl_tag, None

        exps = [m["expectancy"] for m in fmap.values() if m["trades"] > 0]
        if not exps: continue

        pfs = [m["profit_factor"] for m in fmap.values()
               if m["trades"] > 0 and m["profit_factor"] != float("inf")]
        shrps = [m["sharpe"] for m in fmap.values() if m["trades"] > 0]

        merged = {
            "rule_key": rk,
            "L": L, "pattern": list(pat), "side": side, "K": K,
            "sl_model": sl_model, "sl_param": sl_param,
            "folds_active": len(exps),
            "trades":  sum(m["trades"]  for m in fmap.values()),
            "net_usd": sum(m["net_usd"] for m in fmap.values()),
            "net_pts": sum(m["net_pts"] for m in fmap.values()),
            "expectancy":    float(np.mean(exps)),
            "profit_factor": float(np.mean(pfs))   if pfs   else 0.0,
            "sharpe":        float(np.mean(shrps)) if shrps else 0.0,
            "stability":     stability_score(exps),
            "_fold_indices_test": [(folds[fi][2], folds[fi][3]) for fi in fmap.keys()],
            "_fold_ids":          list(fmap.keys()),
        }
        merged["score"] = score_rule(merged, cfg)
        leaderboard.append(merged)

    if cfg.min_equity_usd > 0:
        leaderboard = [r for r in leaderboard if r["net_usd"] >= cfg.min_equity_usd]
    leaderboard.sort(key=lambda r: r["score"], reverse=True)
    leaderboard = leaderboard[: cfg.top_n_rules]

    # release the giant metrics map before phase 2
    fold_metrics = None
    gc.collect()

    # ── PHASE 2: re-simulate top-N to get trade logs for export ──
    print(f"  [rebuild] full-sim top {len(leaderboard)} rules for export …")
    for r in leaderboard:
        all_folds_data = []
        for fid in r["_fold_ids"]:
            tr_lo, tr_hi, te_lo, te_hi = folds[fid]
            sigs = _signals_from_index(ctx, r["L"], tuple(r["pattern"]),
                                       r["side"], te_lo, te_hi)
            trades, eq = simulate(ctx, sigs, cfg,
                                  sl_model=r["sl_model"], K=r["K"],
                                  sl_override=r["sl_param"], lite=False)
            all_folds_data.append({"fold": fid, "trades": trades,
                                   "eq": eq, "metrics": metrics(trades, eq)})
        r["all_folds"] = all_folds_data
        # drop internal-only fields
        r.pop("_fold_ids", None); r.pop("_fold_indices_test", None)

    return leaderboard