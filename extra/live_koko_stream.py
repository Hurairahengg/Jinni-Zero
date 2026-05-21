"""
live_koko_stream.py ─ Koko Candles Live Playback Server
────────────────────────────────────────────────────────

Runs a local HTTP/SSE server and streams Koko Candles into Lightweight Charts.

Files needed:
  1. live_koko_stream.py
  2. live_koko_chart.html

Usage:
  python live_koko_stream.py

Then open:
  http://127.0.0.1:8787

What it does:
  - asks for one CSV tick file
  - asks one range size
  - asks reversal bricks
  - asks clean mode
  - generates Koko Candles from ticks
  - streams bars to browser
  - Play/Pause controls happen from HTML
  - playback delay uses actual elapsed time between bar emission timestamps

No Python external dependencies required.
"""

import json
import os
import time
import queue
import threading
from datetime import datetime
from decimal import Decimal, InvalidOperation
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse


# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────

HOST = "127.0.0.1"
PORT = 8787

HTML_FILE = "live_koko_chart.html"

CHUNK_ROWS = 50000
ASSUME_INPUT_SORTED = True
INCLUDE_PARTIAL_BAR = True

# 1.0 = exact real speed.
# Example:
#   10.0 = 10x faster
#   100.0 = 100x faster
# Default stays exact because you asked exact speed.
PLAYBACK_SPEED = 1.0


# ─────────────────────────────────────────────────────────────
# GLOBAL RUNTIME STATE
# ─────────────────────────────────────────────────────────────

class RuntimeState:
    def __init__(self):
        self.playing = False
        self.reset_requested = False
        self.finished = False
        self.error = None

        self.input_file = None
        self.range_size = None
        self.price_decimals = 0
        self.rev_bricks = 2.0
        self.clean_mode = False
        self.max_bars = None

        self.total_ticks = 0
        self.total_bars = 0

        self.clients = []
        self.lock = threading.Lock()

    def snapshot(self):
        with self.lock:
            return {
                "playing": self.playing,
                "reset_requested": self.reset_requested,
                "finished": self.finished,
                "error": self.error,
                "input_file": self.input_file,
                "range_size": self.range_size,
                "price_decimals": self.price_decimals,
                "rev_bricks": self.rev_bricks,
                "clean_mode": self.clean_mode,
                "max_bars": self.max_bars,
                "total_ticks": self.total_ticks,
                "total_bars": self.total_bars,
                "playback_speed": PLAYBACK_SPEED,
            }


STATE = RuntimeState()


# ─────────────────────────────────────────────────────────────
# INPUT HELPERS
# ─────────────────────────────────────────────────────────────

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


def ask_existing_file():
    while True:
        raw = input("\nCSV tick file path: ").strip().strip('"').strip("'")
        if not raw:
            print("  ✗ Required.")
            continue
        if not os.path.isfile(raw):
            print(f"  ✗ File not found: {raw}")
            continue
        return raw


def ask_range_size():
    while True:
        raw = input("\nRange size / brick size: ").strip()
        val = _parse_decimal(raw)
        if val is None:
            print("  ✗ Enter a valid decimal number.")
            continue
        if val <= 0:
            print("  ✗ Range must be greater than 0.")
            continue
        return val, _decimal_places(raw)


def ask_reversal_bricks():
    while True:
        raw = input("\nReversal bricks, e.g. 2, 3, 5.5: ").strip()
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
        return val


def ask_clean_mode():
    while True:
        raw = input("\nClean mode? (y/n, empty = n): ").strip().lower()
        if raw in ("", "n", "no"):
            return False
        if raw in ("y", "yes"):
            return True
        print("  ✗ Enter y or n.")


def ask_bar_limit():
    while True:
        raw = input("\nMax bars to stream? empty = full dataset: ").strip()
        if raw == "":
            return None
        try:
            n = int(raw)
        except ValueError:
            print("  ✗ Enter valid integer or leave empty.")
            continue
        if n <= 0:
            print("  ✗ Must be greater than 0.")
            continue
        return n


# ─────────────────────────────────────────────────────────────
# TICK FORMAT DETECTION + PARSERS
# ─────────────────────────────────────────────────────────────

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

    return {
        "ts": int(dt.timestamp()),
        "price": price,
        "volume": volume,
    }


def _parse_tick_whitespace(fields):
    if len(fields) < 4:
        return None

    date_raw = fields[0].strip()
    time_raw = fields[1].strip()
    bid_raw = fields[2].strip()
    ask_raw = fields[3].strip()

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

    return {
        "ts": int(dt.timestamp()),
        "price": price,
        "volume": volume,
    }


def _parse_metatrader_header(header_line):
    cols = [c.strip().strip("<>").upper() for c in header_line.split("\t")]
    return {name: idx for idx, name in enumerate(cols) if name}


def _parse_tick_metatrader(raw_line, col_map):
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

    bid = None
    ask = None
    last = None

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

    volume = 0.0
    vol_raw = _get("VOLUME")
    if vol_raw:
        try:
            v = float(vol_raw)
            if v > 0:
                volume = v
        except ValueError:
            pass

    return {
        "ts": int(dt.timestamp()),
        "price": price,
        "volume": volume,
    }


def iter_ticks_in_chunks(path, chunk_rows=50000):
    fmt = _detect_tick_format(path)

    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        col_map = None
        chunk = []

        for raw_line in f:
            stripped = raw_line.strip()
            if not stripped:
                continue

            if stripped.startswith("<DATE>"):
                if fmt == "metatrader":
                    col_map = _parse_metatrader_header(stripped)
                continue

            tick = None

            if fmt == "metatrader" and col_map is not None:
                tick = _parse_tick_metatrader(raw_line.rstrip("\n\r"), col_map)
            elif fmt == "comma":
                tick = _parse_tick_comma(stripped.split(","))
            else:
                tick = _parse_tick_whitespace(stripped.split())

            if tick is None:
                continue

            chunk.append(tick)

            if len(chunk) >= chunk_rows:
                yield chunk
                chunk = []

        if chunk:
            yield chunk


# ─────────────────────────────────────────────────────────────
# BAR HELPER
# ─────────────────────────────────────────────────────────────

def make_bar(time_, open_, high_, low_, close_, volume_, price_decimals):
    return {
        "time": int(time_),
        "open": round(open_, price_decimals),
        "high": round(high_, price_decimals),
        "low": round(low_, price_decimals),
        "close": round(close_, price_decimals),
        "volume": round(volume_, 2),
    }


# ─────────────────────────────────────────────────────────────
# KOKO CANDLE STREAMER - IN MEMORY / YIELD STYLE
# ─────────────────────────────────────────────────────────────

class KokoCandleStreamer:
    """
    Grid-anchored Koko Candles.

    Difference from your file-output version:
      - no file writing
      - process_tick returns completed bars
      - each bar has private _emit_ts for playback timing
      - chart receives normal OHLC only
    """

    def __init__(
        self,
        range_size,
        price_decimals,
        include_partial=True,
        max_bars=None,
        rev_bricks=2.0,
        clean_mode=False,
    ):
        self.rs = float(range_size)
        self.pd = price_decimals
        self.include_partial = include_partial
        self.max_bars = max_bars
        self.rev_bricks = rev_bricks
        self.clean_mode = clean_mode

        self.trend = 0
        self.level = None
        self.bar = None
        self.bar_count = 0
        self._last_bar_time = None

    @property
    def limit_reached(self):
        if self.max_bars is None:
            return False
        return self.bar_count >= self.max_bars

    def _snap(self, price):
        rs = self.rs
        return round(round(price / rs) * rs, self.pd)

    def _reset_bar(self, tick):
        self.bar = {
            "time": tick["ts"],
            "open": self.level,
            "high": self.level,
            "low": self.level,
            "close": self.level,
            "volume": 0.0,
        }

    def _bar_dict(self, open_, high_, low_, close_, emit_ts):
        bar = make_bar(
            self.bar["time"],
            open_,
            high_,
            low_,
            close_,
            self.bar["volume"],
            self.pd,
        )

        # Keep chart time strictly increasing.
        if self._last_bar_time is not None and bar["time"] <= self._last_bar_time:
            bar["time"] = self._last_bar_time + 1

        self._last_bar_time = bar["time"]

        # Internal only: actual moment this completed.
        # Used for realistic live playback delay.
        bar["_emit_ts"] = int(emit_ts)

        self.bar_count += 1
        return bar

    def process_tick(self, tick):
        emitted = []

        if self.limit_reached:
            return emitted

        p = tick["price"]
        v = tick["volume"]
        rs = self.rs

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
            return emitted

        self.bar["volume"] += v
        self.bar["high"] = max(self.bar["high"], p)
        self.bar["low"] = min(self.bar["low"], p)

        while True:
            if self.limit_reached:
                break

            lvl = self.level

            if self.trend == 0:
                up_target = round(lvl + rs, self.pd)
                down_target = round(lvl - rs, self.pd)

                if p >= up_target:
                    bar_open = lvl
                    bar_close = up_target
                    bar_high = max(self.bar["high"], bar_close)
                    bar_low = self.bar["low"]

                    emitted.append(
                        self._bar_dict(
                            bar_open,
                            bar_high,
                            bar_low,
                            bar_close,
                            tick["ts"],
                        )
                    )

                    self.trend = 1
                    self.level = up_target
                    self._reset_bar(tick)
                    continue

                if p <= down_target:
                    bar_open = lvl
                    bar_close = down_target
                    bar_high = self.bar["high"]
                    bar_low = min(self.bar["low"], bar_close)

                    emitted.append(
                        self._bar_dict(
                            bar_open,
                            bar_high,
                            bar_low,
                            bar_close,
                            tick["ts"],
                        )
                    )

                    self.trend = -1
                    self.level = down_target
                    self._reset_bar(tick)
                    continue

                break

            elif self.trend == 1:
                cont_target = round(lvl + rs, self.pd)
                rev_target = round(lvl - self.rev_bricks * rs, self.pd)

                if p >= cont_target:
                    bar_open = lvl
                    bar_close = cont_target
                    bar_high = max(self.bar["high"], bar_close)
                    bar_low = self.bar["low"]

                    emitted.append(
                        self._bar_dict(
                            bar_open,
                            bar_high,
                            bar_low,
                            bar_close,
                            tick["ts"],
                        )
                    )

                    self.level = cont_target
                    self._reset_bar(tick)
                    continue

                if p <= rev_target:
                    if self.clean_mode:
                        bar_close = rev_target
                        bar_open = round(rev_target + rs, self.pd)
                        bar_high = max(self.bar["high"], lvl)
                        bar_low = min(self.bar["low"], bar_close)
                    else:
                        bar_open = lvl
                        bar_close = rev_target
                        bar_high = self.bar["high"]
                        bar_low = min(self.bar["low"], bar_close)

                    emitted.append(
                        self._bar_dict(
                            bar_open,
                            bar_high,
                            bar_low,
                            bar_close,
                            tick["ts"],
                        )
                    )

                    self.trend = -1
                    self.level = rev_target
                    self._reset_bar(tick)
                    continue

                break

            elif self.trend == -1:
                cont_target = round(lvl - rs, self.pd)
                rev_target = round(lvl + self.rev_bricks * rs, self.pd)

                if p <= cont_target:
                    bar_open = lvl
                    bar_close = cont_target
                    bar_high = self.bar["high"]
                    bar_low = min(self.bar["low"], bar_close)

                    emitted.append(
                        self._bar_dict(
                            bar_open,
                            bar_high,
                            bar_low,
                            bar_close,
                            tick["ts"],
                        )
                    )

                    self.level = cont_target
                    self._reset_bar(tick)
                    continue

                if p >= rev_target:
                    if self.clean_mode:
                        bar_close = rev_target
                        bar_open = round(rev_target - rs, self.pd)
                        bar_high = max(self.bar["high"], bar_close)
                        bar_low = min(self.bar["low"], lvl)
                    else:
                        bar_open = lvl
                        bar_close = rev_target
                        bar_high = max(self.bar["high"], bar_close)
                        bar_low = self.bar["low"]

                    emitted.append(
                        self._bar_dict(
                            bar_open,
                            bar_high,
                            bar_low,
                            bar_close,
                            tick["ts"],
                        )
                    )

                    self.trend = 1
                    self.level = rev_target
                    self._reset_bar(tick)
                    continue

                break

        return emitted

    def finish(self):
        if self.limit_reached:
            return None

        if not self.include_partial or self.bar is None:
            return None

        if (self.bar["high"] == self.bar["low"]) and (self.bar["close"] == self.bar["open"]):
            return None

        emit_ts = int(self.bar["time"])

        bar = make_bar(
            self.bar["time"],
            self.bar["open"],
            self.bar["high"],
            self.bar["low"],
            self.bar["close"],
            self.bar["volume"],
            self.pd,
        )

        if self._last_bar_time is not None and bar["time"] <= self._last_bar_time:
            bar["time"] = self._last_bar_time + 1

        self._last_bar_time = bar["time"]
        bar["_emit_ts"] = emit_ts
        self.bar_count += 1

        return bar


# ─────────────────────────────────────────────────────────────
# SSE CLIENT BROADCAST
# ─────────────────────────────────────────────────────────────

def broadcast(event_type, payload):
    msg = {
        "type": event_type,
        "payload": payload,
    }

    dead = []

    with STATE.lock:
        clients = list(STATE.clients)

    for q in clients:
        try:
            q.put_nowait(msg)
        except Exception:
            dead.append(q)

    if dead:
        with STATE.lock:
            STATE.clients = [c for c in STATE.clients if c not in dead]


def chart_bar_payload(bar):
    """
    Remove private _emit_ts before sending to chart.
    Lightweight Charts wants:
      time, open, high, low, close
    We also send volume separately for stats.
    """
    return {
        "time": int(bar["time"]),
        "open": float(bar["open"]),
        "high": float(bar["high"]),
        "low": float(bar["low"]),
        "close": float(bar["close"]),
        "volume": float(bar.get("volume", 0.0)),
        "emit_ts": int(bar.get("_emit_ts", bar["time"])),
    }


# ─────────────────────────────────────────────────────────────
# PLAYBACK TIMING
# ─────────────────────────────────────────────────────────────

def wait_until_playing():
    while True:
        with STATE.lock:
            if STATE.reset_requested:
                return False
            if STATE.playing:
                return True
            if STATE.finished:
                return False
        time.sleep(0.05)


def interruptible_real_delay(seconds):
    """
    Waits for exact real delay, but pause freezes the countdown.
    Reset cancels it.
    """
    if seconds <= 0:
        return True

    remaining = seconds
    last = time.monotonic()

    while remaining > 0:
        with STATE.lock:
            if STATE.reset_requested:
                return False
            playing = STATE.playing

        now = time.monotonic()

        if playing:
            elapsed = now - last
            remaining -= elapsed
        else:
            # paused: don't consume remaining time
            pass

        last = now
        time.sleep(0.02)

    return True


# ─────────────────────────────────────────────────────────────
# STREAMING WORKER
# ─────────────────────────────────────────────────────────────

def stream_worker():
    while True:
        with STATE.lock:
            STATE.finished = False
            STATE.error = None
            STATE.reset_requested = False
            STATE.total_ticks = 0
            STATE.total_bars = 0

            input_file = STATE.input_file
            range_size = STATE.range_size
            price_decimals = STATE.price_decimals
            rev_bricks = STATE.rev_bricks
            clean_mode = STATE.clean_mode
            max_bars = STATE.max_bars

        streamer = KokoCandleStreamer(
            range_size=range_size,
            price_decimals=price_decimals,
            include_partial=INCLUDE_PARTIAL_BAR,
            max_bars=max_bars,
            rev_bricks=rev_bricks,
            clean_mode=clean_mode,
        )

        fmt = _detect_tick_format(input_file)

        broadcast("status", {
            "message": "Ready. Press Play.",
            "input_file": input_file,
            "tick_format": fmt,
            "range_size": _format_decimal(range_size),
            "rev_bricks": rev_bricks,
            "clean_mode": clean_mode,
            "playback_speed": PLAYBACK_SPEED,
        })

        wait_until_playing()

        last_emit_ts = None
        last_ts_seen = None

        try:
            chunk_idx = 0

            for chunk in iter_ticks_in_chunks(input_file, chunk_rows=CHUNK_ROWS):
                with STATE.lock:
                    if STATE.reset_requested:
                        break

                chunk_idx += 1

                if not ASSUME_INPUT_SORTED:
                    chunk.sort(key=lambda x: x["ts"])

                for tick in chunk:
                    with STATE.lock:
                        if STATE.reset_requested:
                            break

                    if not wait_until_playing():
                        break

                    ts = tick["ts"]

                    if ts == 0:
                        ts = (last_ts_seen + 1) if last_ts_seen is not None else 1
                        tick["ts"] = ts

                    if last_ts_seen is not None and ts < last_ts_seen:
                        tick["ts"] = last_ts_seen
                        ts = last_ts_seen

                    last_ts_seen = ts

                    emitted_bars = streamer.process_tick(tick)

                    with STATE.lock:
                        STATE.total_ticks += 1

                    for bar in emitted_bars:
                        emit_ts = int(bar.get("_emit_ts", bar["time"]))

                        if last_emit_ts is not None:
                            raw_delay = max(0, emit_ts - last_emit_ts)
                            delay = raw_delay / max(PLAYBACK_SPEED, 0.000001)

                            ok = interruptible_real_delay(delay)
                            if not ok:
                                break

                        broadcast("bar", chart_bar_payload(bar))

                        with STATE.lock:
                            STATE.total_bars += 1

                        broadcast("stats", STATE.snapshot())

                        last_emit_ts = emit_ts

                    if streamer.limit_reached:
                        break

                broadcast("progress", {
                    "chunk": chunk_idx,
                    "ticks": STATE.snapshot()["total_ticks"],
                    "bars": STATE.snapshot()["total_bars"],
                })

                if streamer.limit_reached:
                    break

                with STATE.lock:
                    if STATE.reset_requested:
                        break

            with STATE.lock:
                reset_now = STATE.reset_requested

            if not reset_now:
                partial = streamer.finish()
                if partial is not None:
                    emit_ts = int(partial.get("_emit_ts", partial["time"]))

                    if last_emit_ts is not None:
                        raw_delay = max(0, emit_ts - last_emit_ts)
                        delay = raw_delay / max(PLAYBACK_SPEED, 0.000001)
                        interruptible_real_delay(delay)

                    broadcast("bar", chart_bar_payload(partial))

                    with STATE.lock:
                        STATE.total_bars += 1

                with STATE.lock:
                    STATE.finished = True
                    STATE.playing = False

                broadcast("done", STATE.snapshot())

            else:
                broadcast("reset", {
                    "message": "Reset done. Press Play to replay from beginning."
                })

        except Exception as e:
            with STATE.lock:
                STATE.error = str(e)
                STATE.finished = True
                STATE.playing = False
            broadcast("error", {"message": str(e)})

        with STATE.lock:
            should_restart = STATE.reset_requested
            STATE.reset_requested = False
            STATE.finished = False
            STATE.playing = False
            STATE.total_ticks = 0
            STATE.total_bars = 0

        if not should_restart:
            # Keep worker alive but do not auto-replay unless reset is requested.
            # User can use Reset button to replay from beginning.
            while True:
                time.sleep(0.1)
                with STATE.lock:
                    if STATE.reset_requested:
                        break


# ─────────────────────────────────────────────────────────────
# HTTP SERVER
# ─────────────────────────────────────────────────────────────

class LiveKokoHandler(BaseHTTPRequestHandler):
    server_version = "LiveKokoHTTP/1.0"

    def log_message(self, fmt, *args):
        # Cleaner console.
        return

    def _send_json(self, obj, code=200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _send_text(self, text, content_type="text/plain; charset=utf-8", code=200):
        data = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path in ("/", "/index.html", "/live_koko_chart.html"):
            if not os.path.isfile(HTML_FILE):
                self._send_text(
                    f"Missing {HTML_FILE}. Put it beside live_koko_stream.py",
                    code=404,
                )
                return

            with open(HTML_FILE, "r", encoding="utf-8") as f:
                html = f.read()

            self._send_text(html, content_type="text/html; charset=utf-8")
            return

        if parsed.path == "/state":
            self._send_json(STATE.snapshot())
            return

        if parsed.path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            q = queue.Queue()

            with STATE.lock:
                STATE.clients.append(q)

            hello = {
                "type": "hello",
                "payload": STATE.snapshot(),
            }

            try:
                self.wfile.write(f"data: {json.dumps(hello)}\n\n".encode("utf-8"))
                self.wfile.flush()

                while True:
                    try:
                        msg = q.get(timeout=15)
                        self.wfile.write(f"data: {json.dumps(msg)}\n\n".encode("utf-8"))
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()

            except Exception:
                pass
            finally:
                with STATE.lock:
                    if q in STATE.clients:
                        STATE.clients.remove(q)

            return

        self._send_json({"error": "not found"}, code=404)

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path != "/control":
            self._send_json({"error": "not found"}, code=404)
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"

        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}

        action = body.get("action")

        if action == "play":
            with STATE.lock:
                if not STATE.finished:
                    STATE.playing = True
            broadcast("control", STATE.snapshot())
            self._send_json({"ok": True, "state": STATE.snapshot()})
            return

        if action == "pause":
            with STATE.lock:
                STATE.playing = False
            broadcast("control", STATE.snapshot())
            self._send_json({"ok": True, "state": STATE.snapshot()})
            return

        if action == "reset":
            with STATE.lock:
                STATE.playing = False
                STATE.reset_requested = True
                STATE.finished = False
                STATE.error = None
                STATE.total_ticks = 0
                STATE.total_bars = 0
            broadcast("reset", {"message": "Reset requested"})
            self._send_json({"ok": True, "state": STATE.snapshot()})
            return

        self._send_json({"error": "unknown action"}, code=400)


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    print("=" * 72)
    print("  Koko Candles Live Playback Server")
    print("=" * 72)

    input_file = ask_existing_file()
    range_size, price_decimals = ask_range_size()
    rev_bricks = ask_reversal_bricks()
    clean_mode = ask_clean_mode()
    max_bars = ask_bar_limit()

    tick_format = _detect_tick_format(input_file)

    with STATE.lock:
        STATE.input_file = input_file
        STATE.range_size = range_size
        STATE.price_decimals = price_decimals
        STATE.rev_bricks = rev_bricks
        STATE.clean_mode = clean_mode
        STATE.max_bars = max_bars

    print("\n" + "=" * 72)
    print("  SETTINGS")
    print("=" * 72)
    print(f"  Input file:        {input_file}")
    print(f"  Tick format:       {tick_format}")
    print(f"  Range size:        {_format_decimal(range_size)}")
    print(f"  Price decimals:    {price_decimals}")
    print(f"  Reversal bricks:   {rev_bricks}")
    print(f"  Clean mode:        {'ON' if clean_mode else 'OFF'}")
    print(f"  Max bars:          {max_bars if max_bars else 'full dataset'}")
    print(f"  Playback speed:    {PLAYBACK_SPEED}x")
    print("=" * 72)

    worker = threading.Thread(target=stream_worker, daemon=True)
    worker.start()

    server = ThreadingHTTPServer((HOST, PORT), LiveKokoHandler)

    print(f"\nOpen this in browser:")
    print(f"  http://{HOST}:{PORT}")
    print("\nPress Ctrl+C here to stop server.")
    print("=" * 72)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping server...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()