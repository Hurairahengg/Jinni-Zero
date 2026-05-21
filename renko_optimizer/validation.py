import numpy as np
import random
from miner import mine_patterns, summarize_table

def walk_forward_folds(n_bars, train_bars, test_bars, step):
    """Yield (train_lo, train_hi, test_lo, test_hi) index ranges."""
    start = 0
    while start + train_bars + test_bars <= n_bars:
        yield (start, start + train_bars,
               start + train_bars, start + train_bars + test_bars)
        start += step

def train_test_split(n_bars, train_frac, K_max):
    cut = int(n_bars * train_frac)
    return (0, cut, cut, n_bars - K_max - 1)

def stability_score(per_fold_exp):
    """Higher = more stable. Mean / (1 + std)."""
    arr = np.array(per_fold_exp, dtype=float)
    if len(arr) == 0: return 0
    return float(arr.mean() / (1 + arr.std()))

def monte_carlo_equity(trades_pts, runs=500, block=50, seed=0):
    """Block-bootstrap trade pts; return distribution of final cumulative pts & max DDs."""
    rng = random.Random(seed)
    arr = np.array(trades_pts, dtype=float)
    n = len(arr)
    if n == 0: return {"runs": 0}
    finals, max_dds = [], []
    for _ in range(runs):
        out = []
        while len(out) < n:
            i = rng.randint(0, max(0, n - block))
            out.extend(arr[i:i+block].tolist())
        out = np.array(out[:n])
        eq  = np.cumsum(out)
        peak = np.maximum.accumulate(eq)
        finals.append(float(eq[-1]))
        max_dds.append(float((peak - eq).max()))
    finals = np.array(finals); max_dds = np.array(max_dds)
    return {
        "runs": runs,
        "p05_final": float(np.percentile(finals, 5)),
        "p50_final": float(np.percentile(finals, 50)),
        "p95_final": float(np.percentile(finals, 95)),
        "p05_dd":    float(np.percentile(max_dds, 5)),
        "p50_dd":    float(np.percentile(max_dds, 50)),
        "p95_dd":    float(np.percentile(max_dds, 95)),
        "prob_profit": float((finals > 0).mean()),
    }