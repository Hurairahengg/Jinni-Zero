"""
range_bars.py
─────────────────────────────────────────────────────────────────────
Reads tick data from data/nq.csv (tab-separated or comma-separated)
Builds GoCharting-ish reversal range bars:

- Continuation bars require 1x range size
- Reversal bars require 2x range size
- Example for 2pt:
    bullish continuation = +2
    bearish reversal     = -4 from active bullish bar open

Saves to data/ as:
    2pt.json  4pt.json  6pt.json  8pt.json  10pt.json

Usage:
    python range_bars.py
─────────────────────────────────────────────────────────────────────
"""

import csv
import json
import os
from datetime import datetime

# ── CONFIG ──────────────────────────────────────────────────────────
INPUT_FILE = os.path.join("data", "nq.csv")
OUTPUT_DIR = "data"
RANGE_SIZES = [2, 4, 6, 8, 10]
INCLUDE_PARTIAL_BAR = True   # keep last unfinished candle for chart display


# ── LOAD TICKS ───────────────────────────────────────────────────────
def load_ticks(path):
    ticks = []

    with open(path, newline="", encoding="utf-8") as f:
        sample = f.read(4096)
        f.seek(0)

        delim = "\t" if "\t" in sample else ","
        reader = csv.DictReader(f, delimiter=delim)

        for row in reader:
            clean_row = {}
            for k, v in row.items():
                key = (k or "").strip().strip("<>")
                val = (v or "").strip()
                clean_row[key] = val

            date_str = clean_row.get("DATE", "")
            time_str = clean_row.get("TIME", "")

            price_raw = (
                clean_row.get("LAST")
                or clean_row.get("BID")
                or clean_row.get("ASK")
                or ""
            ).strip()

            if not price_raw:
                continue

            try:
                price = float(price_raw)
            except ValueError:
                continue

            vol_raw = clean_row.get("VOLUME", "").strip()
            try:
                volume = float(vol_raw) if vol_raw else 0.0
            except ValueError:
                volume = 0.0

            # Try to preserve milliseconds if present
            ts = 0
            if date_str and time_str:
                date_clean = date_str.replace(".", "-")

                dt = None
                for fmt in (
                    "%Y-%m-%d %H:%M:%S.%f",
                    "%Y-%m-%d %H:%M:%S",
                ):
                    try:
                        dt = datetime.strptime(f"{date_clean} {time_str}", fmt)
                        break
                    except ValueError:
                        pass

                if dt is not None:
                    ts = int(dt.timestamp())

            ticks.append({
                "ts": ts,
                "price": price,
                "volume": volume
            })

    ticks.sort(key=lambda x: x["ts"])
    return ticks


# ── HELPERS ──────────────────────────────────────────────────────────
def make_bar(time_, open_, high_, low_, close_, volume_):
    return {
        "time": time_,
        "open": round(open_, 2),
        "high": round(high_, 2),
        "low": round(low_, 2),
        "close": round(close_, 2),
        "volume": round(volume_, 2),
    }


# ── BUILD RANGE BARS ─────────────────────────────────────────────────
def build_range_bars(ticks, range_size, include_partial=True):
    """
    GoCharting-ish reversal range bars:
    - continuation = 1x range size
    - reversal     = 2x range size

    Trend meanings:
      0  = unknown / startup
      1  = bullish
     -1  = bearish
    """
    bars = []
    if not ticks:
        return bars

    first = ticks[0]
    trend = 0

    bar = {
        "time": first["ts"],
        "open": first["price"],
        "high": first["price"],
        "low": first["price"],
        "close": first["price"],
        "volume": first["volume"],
    }

    for tick in ticks[1:]:
        p = tick["price"]
        v = tick["volume"]

        # add tick volume to current developing bar
        bar["volume"] += v

        while True:
            o = bar["open"]

            # ── STARTUP / NO TREND YET ───────────────────────────────
            if trend == 0:
                up_target = o + range_size
                down_target = o - range_size

                if p >= up_target:
                    bar["high"] = max(bar["high"], up_target)
                    bar["low"] = min(bar["low"], o)
                    bar["close"] = up_target

                    bars.append(make_bar(
                        bar["time"], bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"]
                    ))

                    trend = 1
                    new_open = up_target
                    bar = {
                        "time": tick["ts"],
                        "open": new_open,
                        "high": new_open,
                        "low": new_open,
                        "close": new_open,
                        "volume": 0.0,
                    }
                    continue

                elif p <= down_target:
                    bar["high"] = max(bar["high"], o)
                    bar["low"] = min(bar["low"], down_target)
                    bar["close"] = down_target

                    bars.append(make_bar(
                        bar["time"], bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"]
                    ))

                    trend = -1
                    new_open = down_target
                    bar = {
                        "time": tick["ts"],
                        "open": new_open,
                        "high": new_open,
                        "low": new_open,
                        "close": new_open,
                        "volume": 0.0,
                    }
                    continue

                else:
                    bar["high"] = max(bar["high"], p)
                    bar["low"] = min(bar["low"], p)
                    bar["close"] = p
                    break

            # ── BULL TREND ───────────────────────────────────────────
            elif trend == 1:
                cont_target = o + range_size
                rev_target = o - (2 * range_size)

                # bullish continuation
                if p >= cont_target:
                    bar["high"] = max(bar["high"], cont_target)
                    bar["low"] = min(bar["low"], o)
                    bar["close"] = cont_target

                    bars.append(make_bar(
                        bar["time"], bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"]
                    ))

                    new_open = cont_target
                    bar = {
                        "time": tick["ts"],
                        "open": new_open,
                        "high": new_open,
                        "low": new_open,
                        "close": new_open,
                        "volume": 0.0,
                    }
                    continue

                # bearish reversal requires double range
                elif p <= rev_target:
                    rev_open = o - range_size
                    rev_close = o - (2 * range_size)

                    high_ = max(bar["high"], o)
                    low_ = min(bar["low"], rev_close)

                    bars.append(make_bar(
                        bar["time"],
                        rev_open,
                        high_,
                        low_,
                        rev_close,
                        bar["volume"]
                    ))

                    trend = -1
                    new_open = rev_close
                    bar = {
                        "time": tick["ts"],
                        "open": new_open,
                        "high": new_open,
                        "low": new_open,
                        "close": new_open,
                        "volume": 0.0,
                    }
                    continue

                # no completion yet -> just wick/developing candle
                else:
                    bar["high"] = max(bar["high"], p)
                    bar["low"] = min(bar["low"], p)
                    bar["close"] = p
                    break

            # ── BEAR TREND ───────────────────────────────────────────
            elif trend == -1:
                cont_target = o - range_size
                rev_target = o + (2 * range_size)

                # bearish continuation
                if p <= cont_target:
                    bar["high"] = max(bar["high"], o)
                    bar["low"] = min(bar["low"], cont_target)
                    bar["close"] = cont_target

                    bars.append(make_bar(
                        bar["time"], bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"]
                    ))

                    new_open = cont_target
                    bar = {
                        "time": tick["ts"],
                        "open": new_open,
                        "high": new_open,
                        "low": new_open,
                        "close": new_open,
                        "volume": 0.0,
                    }
                    continue

                # bullish reversal requires double range
                elif p >= rev_target:
                    rev_open = o + range_size
                    rev_close = o + (2 * range_size)

                    high_ = max(bar["high"], rev_close)
                    low_ = min(bar["low"], o)

                    bars.append(make_bar(
                        bar["time"],
                        rev_open,
                        high_,
                        low_,
                        rev_close,
                        bar["volume"]
                    ))

                    trend = 1
                    new_open = rev_close
                    bar = {
                        "time": tick["ts"],
                        "open": new_open,
                        "high": new_open,
                        "low": new_open,
                        "close": new_open,
                        "volume": 0.0,
                    }
                    continue

                # no completion yet -> just wick/developing candle
                else:
                    bar["high"] = max(bar["high"], p)
                    bar["low"] = min(bar["low"], p)
                    bar["close"] = p
                    break

    if include_partial and (
        bar["high"] != bar["low"] or bar["close"] != bar["open"]
    ):
        bars.append(make_bar(
            bar["time"], bar["open"], bar["high"], bar["low"], bar["close"], bar["volume"]
        ))

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
    fname = f"{range_size}pt.json"
    out_path = os.path.join(OUTPUT_DIR, fname)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(bars, f, separators=(",", ":"))

    return out_path, fname


# ── MAIN ─────────────────────────────────────────────────────────────
def main():
    print("=" * 58)
    print("  NQ Range Bar Generator (Double-Reversal Logic)")
    print(f"  Ranges: {RANGE_SIZES} points")
    print("=" * 58)

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
        bars = build_range_bars(ticks, rng, include_partial=INCLUDE_PARTIAL_BAR)
        bars = fix_timestamps(bars)
        out_path, _ = save_json(bars, rng)
        print(f"→ {len(bars):,} bars  saved: {out_path}")

    print("\n" + "=" * 58)
    print("  Done. Files saved in data/:")
    for rng in RANGE_SIZES:
        print(f"    data/{rng}pt.json")
    print("=" * 58)


if __name__ == "__main__":
    main()