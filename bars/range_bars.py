"""
range_bars.py  ─  Koko Candles Generator [STREAMING]
─────────────────────────────────────────────────────────────────────
Lists folders inside data/
You select which folder to process.
Auto-detects CSV file inside selected folder.

Builds Koko Candles (grid-anchored range bars):

- All opens/closes snap to a fixed price grid (multiples of brick size).
- Continuation: price reaches next grid level in trend direction → 1-brick bar.
- Reversal: price must move rev_bricks * brick_size against trend to trigger.
  ONE bar is emitted for the reversal (no cascading).

Clean mode OFF (default):
    Reversal bar body = rev_bricks * brick_size (e.g. 2x body).
    Example (brick=5, bull, close=1010):
        price ≤ 1000 → bar open=1010, close=1000 (2x body)

Clean mode ON:
    Reversal bar body = 1 brick (same as continuation).
    The excess is pushed into the wick so the open sits 1 brick from close.
    Example (brick=5, bull, close=1010):
        price ≤ 1000 → bar open=1005, close=1000, high=1010 (wick shows origin)

Dynamic range setup:
    Ask start range, interval, end range.

Reversal bricks:
    How many bricks against trend before a reversal triggers.
    Default is 2.

Bar limit:
    Max bars to generate per range. Empty = full dataset.

Supported tick formats (auto-detected):

    Whitespace-separated:
        2026.01.01  23:05:00.018  4328.401  4328.438  ...  6

    Comma-separated:
        2023.01.02 23:00:00.374,11047.869,11051.301

Usage:
    python range_bars.py
─────────────────────────────────────────────────────────────────────
"""

import json
import os
from datetime import datetime
from decimal import Decimal, InvalidOperation


# ── CONFIG ──────────────────────────────────────────────────────────
DATA_DIR = "data"
INCLUDE_PARTIAL_BAR = True
CHUNK_ROWS = 50000
ASSUME_INPUT_SORTED = True


# ── INPUT / SELECTION HELPERS ────────────────────────────────────────
def _format_decimal(value):
    d = Decimal(str(value))
    s = format(d.normalize(), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _decimal_places(raw):
    raw = raw.strip()
    if "." not in raw:
        return 0
    return len(raw.split(".", 1)[1])


def _parse_decimal(raw):
    raw = raw.strip()
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _ask_int_choice(prompt, min_value, max_value):
    while True:
        raw = input(prompt).strip()
        try:
            choice = int(raw)
        except ValueError:
            print(f"  ✗ Invalid number. Choose {min_value}-{max_value}.")
            continue
        if min_value <= choice <= max_value:
            return choice
        print(f"  ✗ Out of range. Choose {min_value}-{max_value}.")


def ask_bar_limit():
    while True:
        raw = input("\n  Max bars to generate (empty = full dataset): ").strip()
        if raw == "":
            return None
        try:
            n = int(raw)
        except ValueError:
            print("  ✗ Enter a valid integer or leave empty.")
            continue
        if n <= 0:
            print("  ✗ Must be greater than 0.")
            continue
        return n


def ask_reversal_bricks():
    """
    How many bricks against the trend before reversal triggers.
    No default — must be entered explicitly.
    """
    while True:
        raw = input("\n  Reversal bricks (e.g. 2, 3, 5.5): ").strip()
        if raw == "":
            print("  ✗ Required. Enter a number.")
            continue
        if raw.lower().endswith("x"):
            raw = raw[:-1].strip()
        try:
            val = float(raw)
        except ValueError:
            print("  ✗ Enter a valid number.")
            continue
        if val <= 0:
            print("  ✗ Must be greater than 0.")
            continue
        print(f"  → Reversal bricks: {val}")
        return val


def ask_clean_mode():
    """
    Clean mode: reversal bars have 1-brick body (same as continuation).
    The excess distance becomes a wick showing where price came from.
    """
    while True:
        raw = input("\n  Clean mode? (y/n, empty = n): ").strip().lower()
        if raw in ("", "n", "no"):
            return False
        if raw in ("y", "yes"):
            return True
        print("  ✗ Enter y or n.")


def ask_range_settings():
    while True:
        print("\n  Enter range settings:")
        start_raw = input("    Start range:    ").strip()
        interval_raw = input("    Interval:       ").strip()
        end_raw = input("    End range:      ").strip()

        start = _parse_decimal(start_raw)
        interval = _parse_decimal(interval_raw)
        end = _parse_decimal(end_raw)

        if start is None or interval is None or end is None:
            print("\n  ✗ Invalid decimal input. Enter all three again.")
            continue

        start_dp = _decimal_places(start_raw)
        interval_dp = _decimal_places(interval_raw)
        end_dp = _decimal_places(end_raw)

        if not (start_dp == interval_dp == end_dp):
            print("\n  ✗ Decimal places are not consistent.")
            print(f"    Start decimal places:    {start_dp}")
            print(f"    Interval decimal places: {interval_dp}")
            print(f"    End decimal places:      {end_dp}")
            print("    Enter all three again with matching decimal places.")
            print("    Example valid: 5.0, 2.5, 10.0")
            continue

        if start <= 0:
            print("\n  ✗ Start range must be greater than 0. Enter all three again.")
            continue

        if interval <= 0:
            print("\n  ✗ Interval must be greater than 0. Enter all three again.")
            continue

        if end < start:
            print("\n  ✗ End range must be greater than or equal to start range. Enter all three again.")
            continue

        diff = end - start

        if diff % interval != 0:
            print("\n  ✗ Interval does not land exactly on end range.")
            print(f"    Start:    {_format_decimal(start)}")
            print(f"    Interval: {_format_decimal(interval)}")
            print(f"    End:      {_format_decimal(end)}")
            print("    Enter all three again.")
            continue

        ranges = []
        current = start
        while current <= end:
            ranges.append(current)
            current += interval

        return ranges, start_dp


def select_data_folder():
    if not os.path.exists(DATA_DIR):
        print(f"\n  ✗ Data folder not found: {DATA_DIR}")
        print("    Create data/ and put your folders inside it.")
        return None

    folders = [
        name for name in os.listdir(DATA_DIR)
        if os.path.isdir(os.path.join(DATA_DIR, name))
    ]
    folders.sort()

    if not folders:
        print(f"\n  ✗ No folders found inside: {DATA_DIR}")
        print("    Example:")
        print("      data/NQ/nq.csv")
        print("      data/ES/es.csv")
        return None

    print("\n  Folders found inside data/:")
    for idx, folder in enumerate(folders, start=1):
        print(f"    {idx}. {folder}")

    choice = _ask_int_choice("\n  Select folder number: ", 1, len(folders))
    return os.path.join(DATA_DIR, folders[choice - 1])


def select_csv_file(folder_path):
    csv_files = [
        name for name in os.listdir(folder_path)
        if os.path.isfile(os.path.join(folder_path, name))
        and name.lower().endswith(".csv")
    ]
    csv_files.sort()

    if not csv_files:
        print(f"\n  ✗ No CSV file found inside: {folder_path}")
        return None

    if len(csv_files) == 1:
        return os.path.join(folder_path, csv_files[0])

    print(f"\n  Multiple CSV files found inside {folder_path}:")
    for idx, file_name in enumerate(csv_files, start=1):
        print(f"    {idx}. {file_name}")

    choice = _ask_int_choice("\n  Select CSV number: ", 1, len(csv_files))
    return os.path.join(folder_path, csv_files[choice - 1])


# ── BAR HELPER ───────────────────────────────────────────────────────
def make_bar(time_, open_, high_, low_, close_, volume_, price_decimals):
    return {
        "time": int(time_),
        "open": round(open_, price_decimals),
        "high": round(high_, price_decimals),
        "low": round(low_, price_decimals),
        "close": round(close_, price_decimals),
        "volume": round(volume_, 2),
    }


# ── TICK FORMAT DETECTION + PARSERS ──────────────────────────────────
def _detect_tick_format(path):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("<DATE>"):
                return "metatrader"
            if "," in line:
                return "comma"
            return "whitespace"
    return "whitespace"

def _parse_datetime(raw):
    for fmt in ("%Y.%m.%d %H:%M:%S.%f", "%Y.%m.%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _parse_tick_comma(fields):
    if len(fields) < 3:
        return None

    dt_raw = fields[0].strip()
    bid_raw = fields[1].strip()
    ask_raw = fields[2].strip()

    if not dt_raw or not bid_raw or not ask_raw:
        return None

    dt = _parse_datetime(dt_raw)
    if dt is None:
        return None

    try:
        bid = float(bid_raw)
        ask = float(ask_raw)
    except ValueError:
        return None

    price = (bid + ask) / 2.0

    volume = 0.0
    for i in (3, 4, 5):
        if i < len(fields):
            vol_raw = fields[i].strip()
            if vol_raw:
                try:
                    v = float(vol_raw)
                    if v > 0:
                        volume = v
                        break
                except ValueError:
                    continue

    return {"ts": int(dt.timestamp()), "price": price, "volume": volume}


def _parse_tick_whitespace(fields):
    if len(fields) < 4:
        return None

    date_raw = fields[0].strip()
    time_raw = fields[1].strip()
    bid_raw  = fields[2].strip()
    ask_raw  = fields[3].strip()

    if not date_raw or not time_raw or not bid_raw or not ask_raw:
        return None

    dt = _parse_datetime(f"{date_raw} {time_raw}")
    if dt is None:
        return None

    try:
        bid = float(bid_raw)
        ask = float(ask_raw)
    except ValueError:
        return None

    price = (bid + ask) / 2.0

    volume = 0.0
    if len(fields) >= 6:
        vol_raw = fields[5].strip()
        if vol_raw:
            try:
                volume = float(vol_raw)
            except ValueError:
                volume = 0.0

    return {"ts": int(dt.timestamp()), "price": price, "volume": volume}

def _parse_metatrader_header(header_line):
    """
    Parse MT5 tab-delimited header into column name → index map.
    Header example: <DATE>\t<TIME>\t<BID>\t<ASK>\t<LAST>\t<VOLUME>\t<FLAGS>
    """
    cols = [c.strip().strip("<>").upper() for c in header_line.split("\t")]
    return {name: idx for idx, name in enumerate(cols) if name}


def _parse_tick_metatrader(raw_line, col_map):
    """
    Parse a single MT5 tick line using TAB split + column mapping.
    Handles empty LAST, empty VOLUME, etc. correctly.
    Price = mid(bid, ask) if both present, else LAST, else whichever exists.
    """
    fields = raw_line.split("\t")

    def _get(col_name):
        idx = col_map.get(col_name)
        if idx is not None and idx < len(fields):
            return fields[idx].strip()
        return ""

    date_raw = _get("DATE")
    time_raw = _get("TIME")
    if not date_raw or not time_raw:
        return None

    dt = _parse_datetime(f"{date_raw} {time_raw}")
    if dt is None:
        return None

    # parse all price fields
    bid = ask = last = None
    bid_raw = _get("BID")
    ask_raw = _get("ASK")
    last_raw = _get("LAST")

    if bid_raw:
        try:
            bid = float(bid_raw)
        except ValueError:
            pass
    if ask_raw:
        try:
            ask = float(ask_raw)
        except ValueError:
            pass
    if last_raw:
        try:
            last = float(last_raw)
        except ValueError:
            pass

    # determine price: mid(bid,ask) → last → bid → ask
    if bid is not None and ask is not None:
        price = (bid + ask) / 2.0
    elif last is not None:
        price = last
    elif bid is not None:
        price = bid
    elif ask is not None:
        price = ask
    else:
        return None

    # volume — only from VOLUME column, never from FLAGS
    volume = 0.0
    vol_raw = _get("VOLUME")
    if vol_raw:
        try:
            v = float(vol_raw)
            if v > 0:
                volume = v
        except ValueError:
            pass

    return {"ts": int(dt.timestamp()), "price": price, "volume": volume}

def iter_ticks_in_chunks(path, chunk_rows=50000):
    fmt = _detect_tick_format(path)

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        col_map = None
        chunk = []

        for raw_line in f:
            stripped = raw_line.strip()
            if not stripped:
                continue

            # header line — parse column map for metatrader, skip otherwise
            if stripped.startswith("<DATE>"):
                if fmt == "metatrader":
                    col_map = _parse_metatrader_header(stripped)
                continue

            # parse tick based on format
            tick = None

            if fmt == "metatrader" and col_map is not None:
                tick = _parse_tick_metatrader(raw_line.rstrip("\n\r"), col_map)
            elif fmt == "comma":
                fields = stripped.split(",")
                tick = _parse_tick_comma(fields)
            else:
                fields = stripped.split()
                tick = _parse_tick_whitespace(fields)

            if tick is None:
                continue

            chunk.append(tick)
            if len(chunk) >= chunk_rows:
                yield chunk
                chunk = []

        if chunk:
            yield chunk

# ── KOKO CANDLE STREAMER ─────────────────────────────────────────────
class KokoCandleStreamer:
    """
    Koko Candles — grid-anchored range bars with single-bar reversals.

    Grid = all multiples of brick_size.

    CONTINUATION:
        Price reaches next grid level in trend direction (1 brick).
        → Emit 1-brick bar.

    REVERSAL:
        Price moves rev_bricks * brick_size against trend.
        → Emit ONE bar for the full reversal.

    CLEAN MODE OFF:
        Reversal bar body = rev_bricks * brick_size.
        open = previous level, close = reversal target.
        Example (brick=5, bull, level=1010, rev=2):
            → open=1010, close=1000 (body=10, 2x brick)

    CLEAN MODE ON:
        Reversal bar body = 1 brick (same visual size as continuation).
        open is pushed to 1 brick from close.
        The real origin becomes a wick.
        Example (brick=5, bull, level=1010, rev=2):
            → open=1005, close=1000, high=1010 (wick shows origin)
        Example (brick=5, bear, level=1000, rev=2):
            → open=1005, close=1010, low=1000 (wick shows origin)
    """

    def __init__(self, range_size, out_path, price_decimals,
                 include_partial=True, max_bars=None, rev_bricks=2.0,
                 clean_mode=False):
        self.rs = float(range_size)
        self.pd = price_decimals
        self.include_partial = include_partial
        self.max_bars = max_bars
        self.rev_bricks = rev_bricks
        self.clean_mode = clean_mode

        self.trend = 0       # 0 = startup, 1 = bull, -1 = bear
        self.level = None    # current grid level (= last confirmed close)
        self.bar = None      # working bar accumulating wicks + volume
        self.bar_count = 0

        self.out_path = out_path
        self._f = open(out_path, "w", encoding="utf-8")
        self._f.write("[")
        self._wrote_any = False
        self._last_written_ts = None

    @property
    def limit_reached(self):
        if self.max_bars is None:
            return False
        return self.bar_count >= self.max_bars

    def close(self):
        if self._f:
            self._f.close()
            self._f = None

    def _snap(self, price):
        """Snap price to nearest grid level (multiple of brick size)."""
        rs = self.rs
        return round(round(price / rs) * rs, self.pd)

    def _emit(self, bar_dict):
        if self.limit_reached:
            return

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
        self.bar_count += 1

    def _bar_dict(self, open_, high_, low_, close_):
        return make_bar(
            self.bar["time"],
            open_,
            high_,
            low_,
            close_,
            self.bar["volume"],
            self.pd,
        )

    def _reset_bar(self, tick):
        """Start a fresh working bar at current grid level."""
        self.bar = {
            "time": tick["ts"],
            "open": self.level,
            "high": self.level,
            "low": self.level,
            "close": self.level,
            "volume": 0.0,
        }

    def _finalize_output(self):
        self._f.write("]")
        self._f.flush()
        self.close()

    def process_tick(self, tick):
        if self.limit_reached:
            return

        p = tick["price"]
        v = tick["volume"]
        rs = self.rs

        # ── FIRST TICK: snap to grid, start working bar ──────────
        if self.bar is None:
            self.level = self._snap(p)
            self.bar = {
                "time": tick["ts"],
                "open": self.level,
                "high": self.level,
                "low": self.level,
                "close": self.level,
                "volume": v,
            }
            return

        self.bar["volume"] += v

        # track wicks on the working bar
        self.bar["high"] = max(self.bar["high"], p)
        self.bar["low"] = min(self.bar["low"], p)

        # ── process completed bricks ─────────────────────────────
        while True:
            if self.limit_reached:
                break

            lvl = self.level

            # ── STARTUP: no trend yet ────────────────────────────
            if self.trend == 0:
                up_target = round(lvl + rs, self.pd)
                down_target = round(lvl - rs, self.pd)

                if p >= up_target:
                    bar_open = lvl
                    bar_close = up_target
                    bar_high = max(self.bar["high"], bar_close)
                    bar_low = self.bar["low"]

                    self._emit(self._bar_dict(bar_open, bar_high, bar_low, bar_close))

                    self.trend = 1
                    self.level = up_target
                    self._reset_bar(tick)
                    continue

                elif p <= down_target:
                    bar_open = lvl
                    bar_close = down_target
                    bar_high = self.bar["high"]
                    bar_low = min(self.bar["low"], bar_close)

                    self._emit(self._bar_dict(bar_open, bar_high, bar_low, bar_close))

                    self.trend = -1
                    self.level = down_target
                    self._reset_bar(tick)
                    continue

                else:
                    break

            # ── BULL TREND ───────────────────────────────────────
            elif self.trend == 1:
                cont_target = round(lvl + rs, self.pd)
                rev_target = round(lvl - self.rev_bricks * rs, self.pd)

                if p >= cont_target:
                    # CONTINUATION: 1 brick up
                    bar_open = lvl
                    bar_close = cont_target
                    bar_high = max(self.bar["high"], bar_close)
                    bar_low = self.bar["low"]

                    self._emit(self._bar_dict(bar_open, bar_high, bar_low, bar_close))

                    self.level = cont_target
                    self._reset_bar(tick)
                    continue

                elif p <= rev_target:
                    # REVERSAL: bull → bear
                    if self.clean_mode:
                        # body = 1 brick, origin becomes wick
                        # close = rev_target
                        # open  = 1 brick above close
                        # high  = lvl (real origin, becomes wick)
                        bar_close = rev_target
                        bar_open = round(rev_target + rs, self.pd)
                        bar_high = max(self.bar["high"], lvl)
                        bar_low = min(self.bar["low"], bar_close)
                    else:
                        # full body = rev_bricks * brick_size
                        bar_open = lvl
                        bar_close = rev_target
                        bar_high = self.bar["high"]
                        bar_low = min(self.bar["low"], bar_close)

                    self._emit(self._bar_dict(bar_open, bar_high, bar_low, bar_close))

                    self.trend = -1
                    self.level = rev_target
                    self._reset_bar(tick)
                    continue

                else:
                    break

            # ── BEAR TREND ───────────────────────────────────────
            elif self.trend == -1:
                cont_target = round(lvl - rs, self.pd)
                rev_target = round(lvl + self.rev_bricks * rs, self.pd)

                if p <= cont_target:
                    # CONTINUATION: 1 brick down
                    bar_open = lvl
                    bar_close = cont_target
                    bar_high = self.bar["high"]
                    bar_low = min(self.bar["low"], bar_close)

                    self._emit(self._bar_dict(bar_open, bar_high, bar_low, bar_close))

                    self.level = cont_target
                    self._reset_bar(tick)
                    continue

                elif p >= rev_target:
                    # REVERSAL: bear → bull
                    if self.clean_mode:
                        # body = 1 brick, origin becomes wick
                        # close = rev_target
                        # open  = 1 brick below close
                        # low   = lvl (real origin, becomes wick)
                        bar_close = rev_target
                        bar_open = round(rev_target - rs, self.pd)
                        bar_high = max(self.bar["high"], bar_close)
                        bar_low = min(self.bar["low"], lvl)
                    else:
                        # full body = rev_bricks * brick_size
                        bar_open = lvl
                        bar_close = rev_target
                        bar_high = max(self.bar["high"], bar_close)
                        bar_low = self.bar["low"]

                    self._emit(self._bar_dict(bar_open, bar_high, bar_low, bar_close))

                    self.trend = 1
                    self.level = rev_target
                    self._reset_bar(tick)
                    continue

                else:
                    break

    def finish(self):
        if not self.limit_reached:
            if self.include_partial and self.bar is not None:
                if (self.bar["high"] != self.bar["low"]) or \
                   (self.bar["close"] != self.bar["open"]):
                    bar_dict = make_bar(
                        self.bar["time"],
                        self.bar["open"],
                        self.bar["high"],
                        self.bar["low"],
                        self.bar["close"],
                        self.bar["volume"],
                        self.pd,
                    )
                    self._emit(bar_dict)

        self._finalize_output()

# ── DEBUG: 1-MINUTE BARS FROM TICKS ─────────────────────────────────
def debug_generate_1min_bars(input_file, output_dir, max_bars=500):
    """
    Aggregates raw ticks into 1-minute OHLCV bars.
    Writes first max_bars bars as JSON for visual inspection.
    """
    fmt = _detect_tick_format(input_file)
    print(f"\n  [DEBUG] Generating {max_bars} x 1-minute bars from ticks...")
    print(f"  [DEBUG] Source: {input_file}")
    print(f"  [DEBUG] Detected format: {fmt}")

    # for metatrader, show the column mapping
    if fmt == "metatrader":
        with open(input_file, "r", encoding="utf-8", errors="ignore") as hf:
            for hline in hf:
                hline = hline.strip()
                if hline.startswith("<DATE>"):
                    cmap = _parse_metatrader_header(hline)
                    print(f"  [DEBUG] Column map: {cmap}")
                    break

    # show first 3 raw lines (unparsed) for visual verification
    print(f"  [DEBUG] First 3 raw data lines:")
    line_count = 0
    with open(input_file, "r", encoding="utf-8", errors="ignore") as rf:
        for rline in rf:
            rline = rline.rstrip("\n\r")
            if not rline.strip() or rline.strip().startswith("<DATE>"):
                continue
            print(f"    RAW: {repr(rline)}")
            line_count += 1
            if line_count >= 3:
                break

    bars = []
    current_minute = None
    bar = None

    for chunk in iter_ticks_in_chunks(input_file, chunk_rows=CHUNK_ROWS):
        for tick in chunk:
            ts = tick["ts"]
            p = tick["price"]
            v = tick["volume"]

            # floor to minute
            minute_ts = ts - (ts % 300)

            if current_minute is None or minute_ts != current_minute:
                # new minute — finalize previous bar
                if bar is not None:
                    bars.append(bar)
                    if len(bars) >= max_bars:
                        break

                current_minute = minute_ts
                bar = {
                    "time": minute_ts,
                    "open": round(p, 6),
                    "high": round(p, 6),
                    "low": round(p, 6),
                    "close": round(p, 6),
                    "volume": round(v, 2),
                }
            else:
                bar["high"] = round(max(bar["high"], p), 6)
                bar["low"] = round(min(bar["low"], p), 6)
                bar["close"] = round(p, 6)
                bar["volume"] = round(bar["volume"] + v, 2)

        if len(bars) >= max_bars:
            break

    # don't forget last bar
    if bar is not None and len(bars) < max_bars:
        bars.append(bar)

    out_path = os.path.join(output_dir, "debug_1min.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(bars, f, separators=(",", ":"))

    print(f"  [DEBUG] Wrote {len(bars)} bars → {out_path}")

    # also print first 10 bars + last 10 bars for quick sanity check
    print(f"\n  [DEBUG] First 10 bars:")
    for b in bars[:10]:
        dt = datetime.utcfromtimestamp(b["time"]).strftime("%Y-%m-%d %H:%M")
        rng = round(b["high"] - b["low"], 6)
        print(f"    {dt} | O={b['open']} H={b['high']} L={b['low']} C={b['close']} | range={rng} | vol={b['volume']}")

    print(f"\n  [DEBUG] Last 10 bars:")
    for b in bars[-10:]:
        dt = datetime.utcfromtimestamp(b["time"]).strftime("%Y-%m-%d %H:%M")
        rng = round(b["high"] - b["low"], 6)
        print(f"    {dt} | O={b['open']} H={b['high']} L={b['low']} C={b['close']} | range={rng} | vol={b['volume']}")

    # price range summary
    all_highs = [b["high"] for b in bars]
    all_lows = [b["low"] for b in bars]
    total_range = round(max(all_highs) - min(all_lows), 6)
    print(f"\n  [DEBUG] Price range across {len(bars)} bars:")
    print(f"    High: {max(all_highs)}")
    print(f"    Low:  {min(all_lows)}")
    print(f"    Total range: {total_range}")
    print(f"    Avg 1min range: {round(sum(b['high'] - b['low'] for b in bars) / len(bars), 6)}")

    # also dump first 5 raw ticks for format verification
    print(f"\n  [DEBUG] First 5 raw ticks (parsed):")
    count = 0
    for chunk in iter_ticks_in_chunks(input_file, chunk_rows=100):
        for tick in chunk:
            dt = datetime.utcfromtimestamp(tick["ts"]).strftime("%Y-%m-%d %H:%M:%S")
            print(f"    {dt} | price={tick['price']} | vol={tick['volume']}")
            count += 1
            if count >= 5:
                break
        break

    return out_path

def main():
    import sys

    debug_mode = "--debug" in sys.argv

    print("=" * 58)
    if debug_mode:
        print("  Koko Candle Generator [DEBUG MODE]")
    else:
        print("  Koko Candle Generator [STREAMING]")
    print("=" * 58)

    selected_folder = select_data_folder()
    if selected_folder is None:
        return

    input_file = select_csv_file(selected_folder)
    if input_file is None:
        return

    # ── DEBUG: just dump 1-min bars and exit ─────────────────────
    if debug_mode:
        tick_format = _detect_tick_format(input_file)
        print(f"\n  [DEBUG] Tick format detected: {tick_format}")
        debug_generate_1min_bars(input_file, selected_folder, max_bars=500)
        print("\n  [DEBUG] Done. Load debug_1min.json in your chart to inspect.")
        print("=" * 58)
        return

    # ── NORMAL MODE ──────────────────────────────────────────────
    range_sizes, price_decimals = ask_range_settings()
    rev_bricks = ask_reversal_bricks()
    clean_mode = ask_clean_mode()
    bar_limit = ask_bar_limit()

    tick_format = _detect_tick_format(input_file)

    output_dir = selected_folder
    os.makedirs(output_dir, exist_ok=True)

    rev_label = _format_decimal(Decimal(str(rev_bricks)))

    print("\n" + "=" * 58)
    print(f"  Selected folder:      {selected_folder}")
    print(f"  Input CSV:            {input_file}")
    print(f"  Tick format:          {tick_format}")
    print(f"  Chunk rows:           {CHUNK_ROWS:,}")
    print(f"  Reversal bricks:      {rev_label}")
    print(f"  Clean mode:           {'ON' if clean_mode else 'OFF'}")
    print(f"  Bar limit:            {bar_limit if bar_limit else 'unlimited (full dataset)'}")
    print("  Ranges:")
    for rng in range_sizes:
        rev_dist = _format_decimal(rng * Decimal(str(rev_bricks)))
        print(f"    {_format_decimal(rng)}pt  (reversal triggers at {rev_dist}pt move)")
    print("=" * 58)

    streamers = {}
    out_paths = {}

    try:
        for rng in range_sizes:
            label = _format_decimal(rng)
            fname = f"{label}pt.json"
            out_path = os.path.join(output_dir, fname)
            out_paths[rng] = out_path

            print(f"  Opening output stream: {out_path}")

            streamers[rng] = KokoCandleStreamer(
                range_size=rng,
                out_path=out_path,
                price_decimals=price_decimals,
                include_partial=INCLUDE_PARTIAL_BAR,
                max_bars=bar_limit,
                rev_bricks=rev_bricks,
                clean_mode=clean_mode,
            )

        print(f"\n  Streaming ticks from {input_file} ...")

        total_ticks = 0
        last_ts_seen = None
        chunk_idx = 0
        all_done = False

        for chunk in iter_ticks_in_chunks(input_file, chunk_rows=CHUNK_ROWS):
            chunk_idx += 1

            if not ASSUME_INPUT_SORTED:
                chunk.sort(key=lambda x: x["ts"])

            all_done = all(s.limit_reached for s in streamers.values())
            if all_done:
                print("  ✓ All ranges hit bar limit. Stopping early.")
                break

            for tick in chunk:
                ts = tick["ts"]

                if ts == 0:
                    ts = (last_ts_seen + 1) if last_ts_seen is not None else 1
                    tick["ts"] = ts

                if last_ts_seen is not None and ts < last_ts_seen:
                    tick["ts"] = last_ts_seen
                    ts = last_ts_seen

                last_ts_seen = ts

                for rng in range_sizes:
                    streamers[rng].process_tick(tick)

                total_ticks += 1

                all_done = all(s.limit_reached for s in streamers.values())
                if all_done:
                    break

            print(f"  ✓ chunk {chunk_idx} processed  (ticks so far: {total_ticks:,})")

            if all_done:
                print("  ✓ All ranges hit bar limit. Stopping early.")
                break

        print(f"\n  ✓ Total ticks processed: {total_ticks:,}")
        print("  Finalizing bars + closing files...")

        for rng in range_sizes:
            streamers[rng].finish()

        print("\n" + "=" * 58)
        print(f"  Done. Files saved in: {output_dir}")
        for rng in range_sizes:
            count = streamers[rng].bar_count
            print(f"    {out_paths[rng]}  ({count:,} bars)")
        print("=" * 58)

    finally:
        for s in streamers.values():
            try:
                if s and s._f is not None:
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