"""
Shared data context — built once at startup, passed around.
Holds numpy arrays + a pre-computed pattern index for O(1) signal lookup.
"""
from pathlib import Path
import json, hashlib
import numpy as np
from collections import defaultdict

class BarCtx:
    __slots__ = ("o","h","l","c","ts","dirs","n","pattern_index","features")

    def __init__(self, o, h, l, c, ts=None):
        self.o, self.h, self.l, self.c = o, h, l, c
        self.ts = ts
        self.n  = len(c)
        self.dirs = None
        self.pattern_index = None  # dict[L] -> dict[pattern_tuple] -> np.ndarray of indices
        self.features = None

    def __len__(self): return self.n


def _cache_path(data_path):
    p = Path(data_path)
    h = hashlib.md5(str(p.resolve()).encode()).hexdigest()[:10]
    return p.parent / f".{p.stem}_{h}.npz"

def load_or_cache(data_path):
    """Load bars from JSON, cache to .npz on first run. Subsequent runs are instant."""
    data_path = Path(data_path)
    cache = _cache_path(data_path)
    if cache.exists() and cache.stat().st_mtime >= data_path.stat().st_mtime:
        z = np.load(cache, allow_pickle=False)
        return BarCtx(z["o"], z["h"], z["l"], z["c"],
                      z["ts"] if "ts" in z.files else None)

    raw = json.loads(data_path.read_text())
    n = len(raw)
    o = np.empty(n, dtype=np.float64)
    h = np.empty(n, dtype=np.float64)
    l = np.empty(n, dtype=np.float64)
    c = np.empty(n, dtype=np.float64)
    ts = None
    has_ts = bool(raw and (raw[0].get("ts") or raw[0].get("time") or raw[0].get("timestamp")))
    if has_ts:
        ts = np.empty(n, dtype=np.float64)
    for i, b in enumerate(raw):
        o[i] = float(b["open"]); h[i] = float(b["high"])
        l[i] = float(b["low"]);  c[i] = float(b["close"])
        if has_ts:
            t = b.get("ts") or b.get("time") or b.get("timestamp")
            ts[i] = float(t) if t is not None else 0.0
    # free raw immediately
    raw = None

    save = {"o": o, "h": h, "l": l, "c": c}
    if ts is not None: save["ts"] = ts
    np.savez(cache, **save)
    return BarCtx(o, h, l, c, ts)


def compute_directions(o, c):
    d = np.zeros(len(c), dtype=np.int8)
    d[c > o] =  1
    d[c < o] = -1
    return d


def build_pattern_index(dirs, L_min, L_max, K_max):
    """
    Returns: dict[L] -> dict[pattern_tuple] -> np.ndarray of indices i
    where the pattern of length L ENDS at i (so signal fires at i).
    Skips patterns containing 0 (neutral bars) and indices too close to the end.
    """
    n = len(dirs)
    index = {}
    end_cap = n - K_max - 1   # need K_max bars ahead for forward outcome
    for L in range(L_min, L_max + 1):
        buckets = defaultdict(list)
        # build patterns by sliding window
        for i in range(L - 1, end_cap):
            seg = dirs[i - L + 1 : i + 1]
            # skip if any neutral
            if (seg == 0).any(): continue
            key = tuple(int(x) for x in seg)
            buckets[key].append(i)
        # freeze lists → arrays (smaller + faster slicing)
        index[L] = {k: np.asarray(v, dtype=np.int32) for k, v in buckets.items()}
    return index