"""
range_bars.py
─────────────────────────────────────────────────────────────────────
Reads tick data from  data/nq.csv  (tab-separated, MetaTrader style)
Auto-generates range bars for sizes: 2,4,6,8  then 10-50 in 5 intervals
Saves to data/  as:
    2pt.json  4pt.json  6pt.json  8pt.json  10pt.json  15pt.json  …  50pt.json

Usage:
    python range_bars.py
No prompts — just run it.
─────────────────────────────────────────────────────────────────────
"""

import csv
import json
import os
from datetime import datetime

# ── CONFIG ──────────────────────────────────────────────────────────
INPUT_FILE  = os.path.join("data", "nq.csv")
OUTPUT_DIR  = "data"
RANGE_SIZES = [2, 4, 6, 8, 10, 15, 20, 25, 30, 35, 40, 45, 50]


# ── LOAD TICKS ───────────────────────────────────────────────────────
def load_ticks(path):
    ticks = []
    with open(path, newline="", encoding="utf-8") as f:
        sample = f.read(4096)
        f.seek(0)
        delim = "\t" if "\t" in sample else ","
        reader = csv.DictReader(f, delimiter=delim)
        for row in reader:
            row = {k.strip().strip("<>"): v.strip() for k, v in row.items()}

            date_str = row.get("DATE", "")
            time_str = row.get("TIME", "")

            price_raw = (row.get("LAST") or row.get("BID") or row.get("ASK") or "").strip()
            if not price_raw:
                continue
            try:
                price = float(price_raw)
            except ValueError:
                continue

            vol_raw = row.get("VOLUME", "").strip()
            try:
                volume = float(vol_raw) if vol_raw else 0.0
            except ValueError:
                volume = 0.0

            date_clean = date_str.replace(".", "-")
            time_clean = time_str.split(".")[0]
            try:
                dt = datetime.strptime(f"{date_clean} {time_clean}", "%Y-%m-%d %H:%M:%S")
                ts = int(dt.timestamp())
            except ValueError:
                ts = 0

            ticks.append({"ts": ts, "price": price, "volume": volume})

    return ticks


# ── BUILD RANGE BARS ─────────────────────────────────────────────────
def build_range_bars(ticks, range_size):
    bars = []
    if not ticks:
        return bars

    first = ticks[0]
    bar_open = first["price"]
    bar_time = first["ts"]

    bar = {
        "time":   bar_time,
        "open":   bar_open,
        "high":   bar_open,
        "low":    bar_open,
        "close":  bar_open,
        "volume": first["volume"],
    }

    for tick in ticks[1:]:
        p = tick["price"]
        v = tick["volume"]

        bar["volume"] += v

        if p > bar["high"]:
            bar["high"] = p
        if p < bar["low"]:
            bar["low"] = p

        while True:
            up_target   = bar["open"] + range_size
            down_target = bar["open"] - range_size

            if p >= up_target:
                bar["close"] = up_target
                bars.append({
                    "time":   bar["time"],
                    "open":   round(bar["open"],  2),
                    "high":   round(max(bar["high"], up_target), 2),
                    "low":    round(bar["low"],   2),
                    "close":  round(bar["close"], 2),
                    "volume": round(bar["volume"], 2),
                })

                new_open = up_target
                bar = {
                    "time":   tick["ts"],
                    "open":   new_open,
                    "high":   new_open,
                    "low":    new_open,
                    "close":  new_open,
                    "volume": 0.0,
                }
                continue

            elif p <= down_target:
                bar["close"] = down_target
                bars.append({
                    "time":   bar["time"],
                    "open":   round(bar["open"],  2),
                    "high":   round(bar["high"],  2),
                    "low":    round(min(bar["low"], down_target), 2),
                    "close":  round(bar["close"], 2),
                    "volume": round(bar["volume"], 2),
                })

                new_open = down_target
                bar = {
                    "time":   tick["ts"],
                    "open":   new_open,
                    "high":   new_open,
                    "low":    new_open,
                    "close":  new_open,
                    "volume": 0.0,
                }
                continue

            break

        bar["close"] = p

    if bar["high"] != bar["low"] or bar["close"] != bar["open"]:
        bars.append({
            "time":   bar["time"],
            "open":   round(bar["open"],   2),
            "high":   round(bar["high"],   2),
            "low":    round(bar["low"],    2),
            "close":  round(bar["close"],  2),
            "volume": round(bar["volume"], 2),
        })

    return bars


# ── DEDUPLICATE TIMESTAMPS ───────────────────────────────────────────
def fix_timestamps(bars):
    fixed = []
    last_ts = None
    for b in bars:
        ts = b["time"]
        if last_ts is not None and ts <= last_ts:
            ts = last_ts + 1
        b["time"] = ts
        last_ts = ts
        fixed.append(b)
    return fixed


# ── SAVE JSON ────────────────────────────────────────────────────────
def save_json(bars, range_size):
    fname    = f"{range_size}pt.json"
    out_path = os.path.join(OUTPUT_DIR, fname)
    with open(out_path, "w") as f:
        json.dump(bars, f, separators=(",", ":"))
    return out_path, fname


# ── MAIN ─────────────────────────────────────────────────────────────
def main():
    print("=" * 54)
    print("  NQ Range Bar Generator")
    print(f"  Ranges: {RANGE_SIZES} points")
    print("=" * 54)

    if not os.path.exists(INPUT_FILE):
        print(f"\n  ✗ File not found: {INPUT_FILE}")
        print("    Put nq.csv inside the data/ folder and re-run.")
        return

    print(f"\n  Loading ticks from {INPUT_FILE} ...")
    ticks = load_ticks(INPUT_FILE)

    if not ticks:
        print("  ✗ No valid ticks found — check CSV format.")
        return

    print(f"  ✓ Loaded {len(ticks):,} ticks\n")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for rng in RANGE_SIZES:
        print(f"  Building {rng}-point bars ...", end="  ")
        bars = build_range_bars(ticks, rng)
        bars = fix_timestamps(bars)
        out_path, fname = save_json(bars, rng)
        print(f"→ {len(bars):,} bars  saved: {out_path}")

    print("\n" + "=" * 54)
    print("  Done. Files saved in data/:")
    for rng in RANGE_SIZES:
        print(f"    data/{rng}pt.json")
    print("=" * 54)


if __name__ == "__main__":
    main()