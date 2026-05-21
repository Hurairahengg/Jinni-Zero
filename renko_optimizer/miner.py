import numpy as np
from collections import defaultdict

def _pattern_key(dirs, i, L):
    """Tuple key of last L non-zero directions ending at i. None if not enough."""
    if i + 1 < L: return None
    seg = tuple(int(x) for x in dirs[i-L+1:i+1])
    if 0 in seg: return None
    return seg

def mine_patterns(features_or_ctx, c, L_range, K_max, idx_range=None,
                  pattern_index=None):
    """
    Indexed + vectorized mining within an index range.
    Accepts either:
      - a `ctx` with `.pattern_index` (preferred)
      - a `features` dict (legacy) + `c` array — slower fallback
    """
    n = len(c)
    if idx_range is None:
        idx_range = (0, n - K_max - 1)
    lo, hi = idx_range
    end_cap = min(hi, n - K_max - 1)

    table = defaultdict(lambda: defaultdict(lambda: {
        "n": 0, "sum": 0.0, "sum2": 0.0,
        "wins": 0, "losses": 0,
        "mae_sum": 0.0, "mfe_sum": 0.0,
        "max_loss": 0.0, "max_gain": 0.0,
    }))

    # prefer the pre-built index
    pat_idx = None
    if pattern_index is not None:
        pat_idx = pattern_index
    elif hasattr(features_or_ctx, "pattern_index"):
        pat_idx = features_or_ctx.pattern_index

    if pat_idx is not None:
        for L in L_range:
            for key, idx_arr in pat_idx.get(L, {}).items():
                # restrict to [lo, end_cap)
                mask = (idx_arr >= max(lo, L - 1)) & (idx_arr < end_cap)
                chunk = idx_arr[mask]
                if chunk.size == 0: continue
                entries = c[chunk]
                for K in range(1, K_max + 1):
                    fwd = c[chunk + K] - entries
                    fwd_window = np.stack([c[chunk + s] for s in range(1, K + 1)])
                    mae = fwd_window.min(axis=0) - entries
                    mfe = fwd_window.max(axis=0) - entries
                    s = table[(L, key)][K]
                    s["n"]       += int(chunk.size)
                    s["sum"]     += float(fwd.sum())
                    s["sum2"]    += float((fwd * fwd).sum())
                    s["wins"]    += int((fwd  > 0).sum())
                    s["losses"]  += int((fwd <= 0).sum())
                    s["mae_sum"] += float(mae.sum())
                    s["mfe_sum"] += float(mfe.sum())
                    s["max_loss"] = min(s["max_loss"], float(mae.min()))
                    s["max_gain"] = max(s["max_gain"], float(mfe.max()))
        return table

    # legacy slow path (kept for safety)
    dirs = features_or_ctx["dirs"]
    for L in L_range:
        for i in range(max(lo, L - 1), end_cap):
            key = _pattern_key(dirs, i, L)
            if key is None: continue
            entry = c[i]
            for K in range(1, K_max + 1):
                if i + K >= n: break
                seg = c[i+1:i+K+1]
                fwd = seg[-1] - entry
                mae = float(np.min(seg) - entry)
                mfe = float(np.max(seg) - entry)
                s = table[(L, key)][K]
                s["n"]       += 1
                s["sum"]     += fwd
                s["sum2"]    += fwd*fwd
                s["wins"]    += int(fwd > 0)
                s["losses"]  += int(fwd <= 0)
                s["mae_sum"] += mae
                s["mfe_sum"] += mfe
                s["max_loss"] = min(s["max_loss"], mae)
                s["max_gain"] = max(s["max_gain"], mfe)
    return table

def summarize_table(table, min_n=30, min_edge=0.0):
    """Flatten to a list of records suitable for ranking."""
    out = []
    for (L, key), per_K in table.items():
        for K, s in per_K.items():
            n = s["n"]
            if n < min_n: continue
            mean = s["sum"] / n
            var  = max(s["sum2"]/n - mean*mean, 0.0)
            std  = var ** 0.5

            # both directions
            for side in ("LONG", "SHORT"):
                exp = mean if side == "LONG" else -mean
                if abs(exp) < min_edge: continue
                wins   = s["wins"]   if side == "LONG" else s["losses"]
                losses = s["losses"] if side == "LONG" else s["wins"]
                wr     = wins / n if n else 0
                # crude PF using mean win/loss magnitude
                pf = (exp + 1e-9) / (std + 1e-9) if std > 0 else float("inf")
                out.append({
                    "L": L, "K": K, "pattern": list(key), "side": side,
                    "n": n, "expectancy": exp, "std": std,
                    "win_rate": wr, "pseudo_pf": pf,
                    "avg_mae": s["mae_sum"]/n * (1 if side=="LONG" else -1),
                    "avg_mfe": s["mfe_sum"]/n * (1 if side=="LONG" else -1),
                })
    out.sort(key=lambda r: r["expectancy"], reverse=True)
    return out

def mine_patterns_indexed(ctx, L_range, K_max, chunk_bars=200_000):
    """
    Faster mining using the pre-computed pattern index.
    Processes data in chunks so peak RAM stays bounded even for huge datasets.
    """
    c = ctx.c
    n = ctx.n
    table = defaultdict(lambda: defaultdict(lambda: {
        "n": 0, "sum": 0.0, "sum2": 0.0,
        "wins": 0, "losses": 0,
        "mae_sum": 0.0, "mfe_sum": 0.0,
        "max_loss": 0.0, "max_gain": 0.0,
    }))

    end_cap = n - K_max - 1

    for L in L_range:
        per_L = ctx.pattern_index.get(L, {})
        for key, idx_arr in per_L.items():
            # process in chunks of indices (not bars) to bound peak slice memory
            for chunk_start in range(0, len(idx_arr), chunk_bars):
                chunk = idx_arr[chunk_start:chunk_start + chunk_bars]
                chunk = chunk[chunk < end_cap]
                if chunk.size == 0: continue
                entries = c[chunk]
                for K in range(1, K_max + 1):
                    # vectorized forward outcomes for this K
                    fwd = c[chunk + K] - entries        # final close − entry
                    # MAE/MFE need a min/max scan over K bars — vectorize via stacking
                    # build a (K, len(chunk)) view of forward closes
                    fwd_window = np.stack([c[chunk + s] for s in range(1, K + 1)])
                    mae = fwd_window.min(axis=0) - entries
                    mfe = fwd_window.max(axis=0) - entries

                    s = table[(L, key)][K]
                    s["n"]       += int(chunk.size)
                    s["sum"]     += float(fwd.sum())
                    s["sum2"]    += float((fwd * fwd).sum())
                    s["wins"]    += int((fwd  > 0).sum())
                    s["losses"]  += int((fwd <= 0).sum())
                    s["mae_sum"] += float(mae.sum())
                    s["mfe_sum"] += float(mfe.sum())
                    s["max_loss"] = min(s["max_loss"], float(mae.min()))
                    s["max_gain"] = max(s["max_gain"], float(mfe.max()))
    return table