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

# Stream settings:
# - This is the number of CSV rows to read/process per chunk.
# - Set to 50 if you want super tiny chunks, but 50,000 is way faster.
CHUNK_ROWS = 50000

# If your CSV is guaranteed already sorted by time, keep this True.
# (Original script sorted in-memory; streaming can't without huge memory.)
ASSUME_INPUT_SORTED = True


# ── HELPERS ──────────────────────────────────────────────────────────
def make_bar(time_, open_, high_, low_, close_, volume_):
    return {
        "time": int(time_),
        "open": round(open_, 2),
        "high": round(high_, 2),
        "low": round(low_, 2),
        "close": round(close_, 2),
        "volume": round(volume_, 2),
    }


def _detect_delimiter(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        sample = f.read(4096)
    return "\t" if "\t" in sample else ","



def _parse_tick_row(row):
    """
    Parses a row like:
    2023.01.02 23:00:00.374,11047.869,11051.301

    Returns:
        {"ts": int, "price": float, "volume": float}
    """
    if not row or len(row) < 2:
        return None

    ts_raw = row[0].strip()
    price_raw = row[1].strip()
    vol_raw = row[2].strip() if len(row) >= 3 else "0"

    if not ts_raw or not price_raw:
        return None

    # parse datetime
    dt = None
    for fmt in ("%Y.%m.%d %H:%M:%S.%f", "%Y.%m.%d %H:%M:%S"):
        try:
            dt = datetime.strptime(ts_raw, fmt)
            break
        except ValueError:
            continue

    if dt is None:
        return None

    try:
        price = float(price_raw)
    except ValueError:
        return None

    try:
        volume = float(vol_raw) if vol_raw else 0.0
    except ValueError:
        volume = 0.0

    return {"ts": int(dt.timestamp()), "price": price, "volume": volume}


def iter_ticks_in_chunks(path, chunk_rows=50000):
    """
    Streams ticks from headerless CSV in chunks without loading full file.
    """
    delim = _detect_delimiter(path)

    with open(path, newline="", encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f, delimiter=delim)

        chunk = []
        for row in reader:
            tick = _parse_tick_row(row)
            if tick is None:
                continue

            chunk.append(tick)

            if len(chunk) >= chunk_rows:
                yield chunk
                chunk = []

        if chunk:
            yield chunk

# ── STREAMING RANGE BAR BUILDER (per range size) ─────────────────────
class RangeBarStreamer:
    """
    Builds range bars incrementally (tick-by-tick) and streams JSON output to disk,
    so we never store all ticks or all bars in memory.

    Logic matches the original build_range_bars():
    - continuation = 1x range_size
    - reversal     = 2x range_size
    """
    def __init__(self, range_size, out_path, include_partial=True):
        self.range_size = float(range_size)
        self.include_partial = include_partial

        self.trend = 0  # 0 unknown, 1 bullish, -1 bearish
        self.bar = None

        # output streaming
        self.out_path = out_path
        self._f = open(out_path, "w", encoding="utf-8")
        self._f.write("[")
        self._wrote_any = False

        # timestamp dedupe (matches fix_timestamps)
        self._last_written_ts = None

    def close(self):
        if self._f:
            self._f.close()
            self._f = None

    def _emit(self, bar_dict):
        # fix timestamps like fix_timestamps() but streaming
        ts = int(bar_dict["time"])
        if self._last_written_ts is not None and ts <= self._last_written_ts:
            ts = self._last_written_ts + 1
        bar_dict["time"] = ts
        self._last_written_ts = ts

        if self._wrote_any:
            self._f.write(",")
        else:
            self._wrote_any = True

        self._f.write(json.dumps(bar_dict, separators=(",", ":")))

    def _finalize_output(self):
        self._f.write("]")
        self._f.flush()
        self.close()

    def _start_bar(self, tick):
        p = tick["price"]
        self.bar = {
            "time": tick["ts"],
            "open": p,
            "high": p,
            "low": p,
            "close": p,
            "volume": tick["volume"],
        }

    def process_tick(self, tick):
        if self.bar is None:
            self._start_bar(tick)
            return

        p = tick["price"]
        v = tick["volume"]
        rs = self.range_size

        # add tick volume to current developing bar
        self.bar["volume"] += v

        # NOTE: keep while True because a single tick can complete multiple bars
        while True:
            o = self.bar["open"]

            # ── STARTUP / NO TREND YET ───────────────────────────────
            if self.trend == 0:
                up_target = o + rs
                down_target = o - rs

                if p >= up_target:
                    self.bar["high"] = max(self.bar["high"], up_target)
                    self.bar["low"] = min(self.bar["low"], o)
                    self.bar["close"] = up_target

                    self._emit(make_bar(
                        self.bar["time"], self.bar["open"], self.bar["high"],
                        self.bar["low"], self.bar["close"], self.bar["volume"]
                    ))

                    self.trend = 1
                    new_open = up_target
                    self.bar = {
                        "time": tick["ts"],
                        "open": new_open,
                        "high": new_open,
                        "low": new_open,
                        "close": new_open,
                        "volume": 0.0,
                    }
                    continue

                elif p <= down_target:
                    self.bar["high"] = max(self.bar["high"], o)
                    self.bar["low"] = min(self.bar["low"], down_target)
                    self.bar["close"] = down_target

                    self._emit(make_bar(
                        self.bar["time"], self.bar["open"], self.bar["high"],
                        self.bar["low"], self.bar["close"], self.bar["volume"]
                    ))

                    self.trend = -1
                    new_open = down_target
                    self.bar = {
                        "time": tick["ts"],
                        "open": new_open,
                        "high": new_open,
                        "low": new_open,
                        "close": new_open,
                        "volume": 0.0,
                    }
                    continue

                else:
                    self.bar["high"] = max(self.bar["high"], p)
                    self.bar["low"] = min(self.bar["low"], p)
                    self.bar["close"] = p
                    break

            # ── BULL TREND ───────────────────────────────────────────
            elif self.trend == 1:
                cont_target = o + rs
                rev_target = o - (2 * rs)

                # bullish continuation
                if p >= cont_target:
                    self.bar["high"] = max(self.bar["high"], cont_target)
                    self.bar["low"] = min(self.bar["low"], o)
                    self.bar["close"] = cont_target

                    self._emit(make_bar(
                        self.bar["time"], self.bar["open"], self.bar["high"],
                        self.bar["low"], self.bar["close"], self.bar["volume"]
                    ))

                    new_open = cont_target
                    self.bar = {
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
                    rev_open = o - rs
                    rev_close = o - (2 * rs)

                    high_ = max(self.bar["high"], o)
                    low_ = min(self.bar["low"], rev_close)

                    self._emit(make_bar(
                        self.bar["time"],
                        rev_open,
                        high_,
                        low_,
                        rev_close,
                        self.bar["volume"]
                    ))

                    self.trend = -1
                    new_open = rev_close
                    self.bar = {
                        "time": tick["ts"],
                        "open": new_open,
                        "high": new_open,
                        "low": new_open,
                        "close": new_open,
                        "volume": 0.0,
                    }
                    continue

                else:
                    self.bar["high"] = max(self.bar["high"], p)
                    self.bar["low"] = min(self.bar["low"], p)
                    self.bar["close"] = p
                    break

            # ── BEAR TREND ───────────────────────────────────────────
            elif self.trend == -1:
                cont_target = o - rs
                rev_target = o + (2 * rs)

                # bearish continuation
                if p <= cont_target:
                    self.bar["high"] = max(self.bar["high"], o)
                    self.bar["low"] = min(self.bar["low"], cont_target)
                    self.bar["close"] = cont_target

                    self._emit(make_bar(
                        self.bar["time"], self.bar["open"], self.bar["high"],
                        self.bar["low"], self.bar["close"], self.bar["volume"]
                    ))

                    new_open = cont_target
                    self.bar = {
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
                    rev_open = o + rs
                    rev_close = o + (2 * rs)

                    high_ = max(self.bar["high"], rev_close)
                    low_ = min(self.bar["low"], o)

                    self._emit(make_bar(
                        self.bar["time"],
                        rev_open,
                        high_,
                        low_,
                        rev_close,
                        self.bar["volume"]
                    ))

                    self.trend = 1
                    new_open = rev_close
                    self.bar = {
                        "time": tick["ts"],
                        "open": new_open,
                        "high": new_open,
                        "low": new_open,
                        "close": new_open,
                        "volume": 0.0,
                    }
                    continue

                else:
                    self.bar["high"] = max(self.bar["high"], p)
                    self.bar["low"] = min(self.bar["low"], p)
                    self.bar["close"] = p
                    break

    def finish(self):
        # append partial bar (same condition as original)
        if self.include_partial and self.bar is not None:
            if (self.bar["high"] != self.bar["low"]) or (self.bar["close"] != self.bar["open"]):
                self._emit(make_bar(
                    self.bar["time"], self.bar["open"], self.bar["high"],
                    self.bar["low"], self.bar["close"], self.bar["volume"]
                ))

        self._finalize_output()


# ── MAIN ─────────────────────────────────────────────────────────────
def main():
    print("=" * 58)
    print("  NQ Range Bar Generator (Double-Reversal Logic) [STREAMING]")
    print(f"  Ranges: {RANGE_SIZES} points")
    print(f"  Chunk rows: {CHUNK_ROWS:,}")
    print("=" * 58)

    if not os.path.exists(INPUT_FILE):
        print(f"\n  ✗ File not found: {INPUT_FILE}")
        print("    Put nq.csv inside the data/ folder and re-run.")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Prepare stream writers (one file per range size)
    streamers = {}
    out_paths = {}
    try:
        for rng in RANGE_SIZES:
            fname = f"{rng}pt.json"
            out_path = os.path.join(OUTPUT_DIR, fname)
            out_paths[rng] = out_path

            print(f"  Opening output stream: {out_path}")
            streamers[rng] = RangeBarStreamer(
                range_size=rng,
                out_path=out_path,
                include_partial=INCLUDE_PARTIAL_BAR
            )

        print(f"\n  Streaming ticks from {INPUT_FILE} ...")

        total_ticks = 0
        last_ts_seen = None
        chunk_idx = 0

        for chunk in iter_ticks_in_chunks(INPUT_FILE, chunk_rows=CHUNK_ROWS):
            chunk_idx += 1

            # If you absolutely need perfect chronological order (like original),
            # you must have sorted input. We'll optionally sort per chunk, but that
            # does NOT fully replicate global sort unless input is already sorted.
            if not ASSUME_INPUT_SORTED:
                chunk.sort(key=lambda x: x["ts"])

            for tick in chunk:
                ts = tick["ts"]

                # If timestamp parsing failed (ts=0), keep it monotonic so we don't
                # dump a pile of zeros at the start.
                if ts == 0:
                    ts = (last_ts_seen + 1) if last_ts_seen is not None else 1
                    tick["ts"] = ts

                # If the input isn't sorted, enforce monotonic timestamps to avoid
                # weird backwards-time artifacts (original code globally sorted).
                if last_ts_seen is not None and ts < last_ts_seen:
                    # keep time non-decreasing
                    tick["ts"] = last_ts_seen
                    ts = last_ts_seen

                last_ts_seen = ts

                for rng in RANGE_SIZES:
                    streamers[rng].process_tick(tick)

                total_ticks += 1

            print(f"  ✓ chunk {chunk_idx} processed  (ticks so far: {total_ticks:,})")

        print(f"\n  ✓ Total ticks processed: {total_ticks:,}")
        print("  Finalizing bars + closing files...")

        for rng in RANGE_SIZES:
            streamers[rng].finish()

        print("\n" + "=" * 58)
        print("  Done. Files saved in data/:")
        for rng in RANGE_SIZES:
            print(f"    data/{rng}pt.json")
        print("=" * 58)

    finally:
        # Ensure files close on any error
        for s in streamers.values():
            try:
                if s:
                    # if not finished yet, close cleanly
                    if s._f is not None:
                        # attempt to close JSON array properly if mid-run
                        try:
                            s._f.write("]")
                        except Exception:
                            pass
                        try:
                            s.close()
                        except Exception:
                            pass
            except Exception:
                pass


if __name__ == "__main__":
    main()