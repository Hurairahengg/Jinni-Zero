import numpy as np

def directions(o, c):
    d = np.zeros(len(c), dtype=np.int8)
    d[c > o] =  1
    d[c < o] = -1
    return d

def run_lengths(dirs):
    """Streak length ending at i (same direction)."""
    rl = np.zeros(len(dirs), dtype=np.int32)
    cur = 0; prev = 0
    for i, d in enumerate(dirs):
        if d == 0: cur = 0
        elif d == prev: cur += 1
        else: cur = 1
        rl[i] = cur
        prev = d
    return rl

def flips(dirs, w):
    """Number of direction changes in last w bricks."""
    out = np.zeros(len(dirs), dtype=np.int32)
    for i in range(len(dirs)):
        s = max(0, i - w + 1)
        seg = dirs[s:i+1]
        out[i] = int(np.sum(np.diff(seg) != 0))
    return out

def path_efficiency(c, w):
    """abs(net move)/total move over last w bricks. 1=trend, 0=chop."""
    out = np.zeros(len(c))
    for i in range(len(c)):
        s = max(0, i - w + 1)
        seg = c[s:i+1]
        if len(seg) < 2: continue
        net   = abs(seg[-1] - seg[0])
        total = np.sum(np.abs(np.diff(seg)))
        out[i] = net / total if total > 0 else 0
    return out

def entropy(dirs, w):
    """Shannon entropy of direction distribution in last w bricks (normalized 0-1)."""
    out = np.zeros(len(dirs))
    for i in range(len(dirs)):
        s = max(0, i - w + 1)
        seg = dirs[s:i+1]
        n = len(seg)
        if n < 2: continue
        p_up = np.sum(seg ==  1) / n
        p_dn = np.sum(seg == -1) / n
        h = 0.0
        for p in (p_up, p_dn):
            if p > 0: h -= p * np.log2(p)
        out[i] = h  # max ≈ 1
    return out

def brick_speed(timestamps, w):
    """Avg seconds per brick over last w bricks. None if no timestamps."""
    if timestamps is None or any(t is None for t in timestamps): return None
    ts = np.array(timestamps, dtype=np.float64)
    out = np.zeros(len(ts))
    for i in range(len(ts)):
        s = max(1, i - w + 1)
        diffs = np.diff(ts[s-1:i+1]) if i >= 1 else np.array([0.0])
        out[i] = np.mean(diffs) if len(diffs) else 0
    return out

def build_features(bars, cfg):
    # accepts a list-of-dicts (legacy) or BarCtx
    if hasattr(bars, "o"):           # BarCtx duck-type
        o, h, l, c = bars.o, bars.h, bars.l, bars.c
    else:
        from data_loader import to_arrays
        o, h, l, c = to_arrays(bars)
    dirs = directions(o, c)
    F = {"dirs": dirs}
    if cfg.use_run_len:    F["run_len"]    = run_lengths(dirs)
    if cfg.use_flips:      F["flips"]      = flips(dirs, cfg.feat_window)
    if cfg.use_efficiency: F["efficiency"] = path_efficiency(c, cfg.feat_window)
    if cfg.use_entropy:    F["entropy"]    = entropy(dirs, cfg.feat_window)
    if cfg.use_brick_speed and cfg.has_timestamps:
        ts = [b["ts"] for b in bars]
        bs = brick_speed(ts, cfg.feat_window)
        if bs is not None: F["brick_speed"] = bs
    return F, (o, h, l, c)