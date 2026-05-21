import sys, traceback, gc
from pathlib import Path
import numpy as np

from config import Config
from context import load_or_cache, compute_directions, build_pattern_index
from features import build_features
from miner import mine_patterns, summarize_table
from optimizer import run_optimizer
from validation import monte_carlo_equity
from reporting import (export_leaderboard, export_rule_json, export_trades,
                       export_heatmap, plot_equity)


def ask(prompt, default, cast=str):
    raw = input(f"{prompt} [{default}]: ").strip()
    if raw == "": return default
    try:
        if cast is bool: return raw.lower() in ("1","y","yes","true","on")
        return cast(raw)
    except Exception:
        print(f"  ! invalid, using {default}")
        return default


def _mem_mb():
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    except Exception:
        return 0.0


def collect_config():
    cfg = Config()
    print("\n══════ RENKO PATTERN OPTIMIZER ══════")
    cfg.data_path       = ask("Data JSON path", cfg.data_path)
    cfg.L_min           = ask("Min pattern length L_min", cfg.L_min, int)
    cfg.L_max           = ask("Max pattern length L_max", cfg.L_max, int)
    cfg.K_max           = ask("Max holding period K_max", cfg.K_max, int)
    cfg.min_occurrences = ask("Min pattern occurrences", cfg.min_occurrences, int)
    cfg.min_edge        = ask("Min expectancy (pts) to keep", cfg.min_edge, float)
    cfg.slippage_pts    = ask("Slippage per side (pts)", cfg.slippage_pts, float)
    cfg.commission_usd  = ask("Commission per trade (USD)", cfg.commission_usd, float)
    cfg.sl_fixed_min    = ask("Fixed-SL sweep MIN (pts)", cfg.sl_fixed_min, float)
    cfg.sl_fixed_max    = ask("Fixed-SL sweep MAX (pts)", cfg.sl_fixed_max, float)
    cfg.sl_iterations   = ask("Fixed-SL iterations", cfg.sl_iterations, int)
    cfg.swing_lookback  = ask("Swing lookback bars", cfg.swing_lookback, int)
    cfg.allow_overlap   = ask("Allow overlapping trades? (y/n)", cfg.allow_overlap, bool)
    cfg.split_mode      = ask("Split mode (train_test|walk_forward)", cfg.split_mode)
    if cfg.split_mode == "walk_forward":
        cfg.wf_train_bars = ask("WF train bars", cfg.wf_train_bars, int)
        cfg.wf_test_bars  = ask("WF test bars",  cfg.wf_test_bars,  int)
        cfg.wf_step       = ask("WF step bars",  cfg.wf_step,       int)
    else:
        cfg.train_frac    = ask("Train fraction", cfg.train_frac, float)
    cfg.objective         = ask("Objective", cfg.objective)
    cfg.top_n_rules       = ask("Top-N rules to keep", cfg.top_n_rules, int)
    cfg.min_equity_usd    = ask("Min net $ filter", cfg.min_equity_usd, float)
    cfg.monte_carlo_runs  = ask("Monte Carlo runs (0=skip)", cfg.monte_carlo_runs, int)
    cfg.out_dir           = ask("Output directory", cfg.out_dir)
    return cfg


def main():
    cfg = collect_config()

    print(f"\n[mem] start: {_mem_mb():.0f} MB")
    print("[load] bars …")
    ctx = load_or_cache(cfg.data_path)
    print(f"  bars: {ctx.n:,}    [mem: {_mem_mb():.0f} MB]")

    print("[index] building direction array + pattern index …")
    ctx.dirs = compute_directions(ctx.o, ctx.c)
    ctx.pattern_index = build_pattern_index(ctx.dirs,
                                            cfg.L_min, cfg.L_max, cfg.K_max)
    total_keys = sum(len(v) for v in ctx.pattern_index.values())
    print(f"  unique pattern keys: {total_keys:,}    [mem: {_mem_mb():.0f} MB]")

    print("[features] building …")
    features, _ = build_features(ctx, cfg)
    ctx.features = features

    print("[mine] global summary (for heatmap) …")
    full_table = mine_patterns(ctx, ctx.c, range(cfg.L_min, cfg.L_max + 1),
                               cfg.K_max)
    heatmap_summary = summarize_table(full_table, min_n=1, min_edge=0.0)
    pruned_summary  = summarize_table(full_table,
                                      min_n=cfg.min_occurrences,
                                      min_edge=cfg.min_edge)
    print(f"  heatmap rows           : {len(heatmap_summary):,}")
    print(f"  candidates (post-prune): {len(pruned_summary):,}")
    full_table = None; pruned_summary = None
    gc.collect()
    print(f"  [mem after mine: {_mem_mb():.0f} MB]")

    print("[optimize] running …")
    leaderboard = run_optimizer(ctx, cfg)
    print(f"  [mem after optimize: {_mem_mb():.0f} MB]")

    print(f"\n  top {len(leaderboard)} rules ↓")
    print(f"{'pattern':<14} {'L':>2} {'K':>2} {'side':<5} "
          f"{'sl':<14} {'n':>5} {'exp':>7} {'PF':>6} {'shrp':>5} {'score':>7}")
    for r in leaderboard:
        pat = "".join("+" if x==1 else "-" for x in r["pattern"])
        sl_lbl = (f"{r['sl_model']}{r['sl_param']:g}"
                  if r.get("sl_param") is not None else r["sl_model"])
        print(f"{pat:<14} {r['L']:>2} {r['K']:>2} {r['side']:<5} "
              f"{sl_lbl:<14} {r['trades']:>5} {r['expectancy']:>7.3f} "
              f"{r['profit_factor']:>6.2f} {r['sharpe']:>5.2f} {r['score']:>7.3f}")

    print("\n[report] writing …")
    export_leaderboard(leaderboard, cfg.out_dir)
    export_heatmap(heatmap_summary, cfg.out_dir)
    for r in leaderboard:
        export_rule_json(r, cfg.out_dir)
        export_trades(r, cfg.out_dir)
        plot_equity(r, cfg.out_dir)

    if cfg.monte_carlo_runs > 0 and leaderboard:
        print("\n[monte-carlo] robustness on #1 rule …")
        top = leaderboard[0]
        pts = [t["gross_pts"] for f in top["all_folds"] for t in f["trades"]]
        mc = monte_carlo_equity(pts, runs=cfg.monte_carlo_runs,
                                block=cfg.mc_block_size)
        print(f"  final pts  p05={mc.get('p05_final',0):.1f}  "
              f"p50={mc.get('p50_final',0):.1f}  p95={mc.get('p95_final',0):.1f}")
        print(f"  max DD     p05={mc.get('p05_dd',0):.1f}  "
              f"p50={mc.get('p50_dd',0):.1f}  p95={mc.get('p95_dd',0):.1f}")
        print(f"  prob_profit={mc.get('prob_profit',0)*100:.1f}%")

    print(f"\n[mem] final: {_mem_mb():.0f} MB")
    print(f"✓ done → {cfg.out_dir}/")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[abort] user cancelled.")
    except Exception:
        traceback.print_exc()
        sys.exit(1)