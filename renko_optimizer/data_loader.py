import json
from pathlib import Path
import numpy as np

def load_bars(path):
    raw = json.loads(Path(path).read_text())
    bars = []
    for b in raw:
        bars.append({
            "open":  float(b["open"]),
            "high":  float(b["high"]),
            "low":   float(b["low"]),
            "close": float(b["close"]),
            "ts":    b.get("ts") or b.get("time") or b.get("timestamp"),
        })
    return bars

def to_arrays(bars):
    """Vectorize for speed."""
    o = np.array([b["open"]  for b in bars], dtype=np.float64)
    h = np.array([b["high"]  for b in bars], dtype=np.float64)
    l = np.array([b["low"]   for b in bars], dtype=np.float64)
    c = np.array([b["close"] for b in bars], dtype=np.float64)
    return o, h, l, c