import json
import os
from collections import deque
from json import JSONDecodeError

# ── CONFIG ──────────────────────────────────────────────────────────
INPUT_FILE = os.path.join("data", "2pt.json")

CHUNK_SIZE = 1024 * 1024       # bytes to read at a time from JSON file
PATTERNS = (2, 3, 4, 5)        # streak lengths to test

# How many LAST bars to analyze:
#   None  -> analyze full file
#   50000 -> analyze only last 50,000 bars
LOOKBACK_BARS = None

# Print progress every N scanned/analyzed bars
STATUS_EVERY = 100000

# Optional small preview of current progress message
PRINT_STARTUP_INFO = True


# ── STREAM JSON ARRAY WITHOUT LOADING WHOLE FILE ────────────────────
def iter_json_array(path, chunk_size=CHUNK_SIZE):
    """
    Streams objects from a large JSON array file like:
    [
      {...},
      {...},
      ...
    ]
    without loading the full file into RAM.
    """
    decoder = json.JSONDecoder()
    buf = ""
    started = False
    eof = False

    with open(path, "r", encoding="utf-8") as f:
        while True:
            # keep buffer topped up
            if not eof and len(buf) < chunk_size // 2:
                more = f.read(chunk_size)
                if more:
                    buf += more
                else:
                    eof = True

            if not started:
                buf = buf.lstrip()
                if not buf:
                    if eof:
                        return
                    continue

                if buf[0] != "[":
                    raise ValueError("Expected JSON array starting with '['")

                buf = buf[1:]
                started = True

            buf = buf.lstrip()

            if not buf:
                if eof:
                    raise ValueError("Unexpected EOF while parsing JSON array")
                continue

            if buf[0] == "]":
                return

            if buf[0] == ",":
                buf = buf[1:]
                continue

            try:
                obj, idx = decoder.raw_decode(buf)
            except JSONDecodeError:
                if eof:
                    raise
                more = f.read(chunk_size)
                if more:
                    buf += more
                else:
                    eof = True
                continue

            yield obj
            buf = buf[idx:]


# ── HELPERS ──────────────────────────────────────────────────────────
def bar_dir(bar):
    """
    Returns:
        1  = bullish
       -1  = bearish
        0  = neutral/doji
    """
    o = float(bar["open"])
    c = float(bar["close"])

    if c > o:
        return 1
    elif c < o:
        return -1
    return 0


def make_stats():
    return {
        "occurrences": 0,
        "next_same": 0,
        "next_opposite": 0,
        "next_neutral": 0,
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "flats": 0,
        "total_pnl_points": 0.0,
        "avg_pnl_points": 0.0,
        "win_rate": 0.0,
        "same_probability": 0.0,
        "opposite_probability": 0.0,
    }


def finalize_stats(s):
    if s["occurrences"] > 0:
        s["same_probability"] = (s["next_same"] / s["occurrences"]) * 100.0
        s["opposite_probability"] = (s["next_opposite"] / s["occurrences"]) * 100.0

    if s["trades"] > 0:
        s["avg_pnl_points"] = s["total_pnl_points"] / s["trades"]
        s["win_rate"] = (s["wins"] / s["trades"]) * 100.0

    return s


def combine_stats(a, b):
    c = make_stats()
    for k in c.keys():
        if k in ("avg_pnl_points", "win_rate", "same_probability", "opposite_probability"):
            continue
        c[k] = a[k] + b[k]
    return finalize_stats(c)


def init_results(patterns):
    return {
        n: {
            1: make_stats(),    # bullish streak stats
            -1: make_stats(),   # bearish streak stats
        }
        for n in patterns
    }


def finalize_results(results, patterns):
    for n in patterns:
        finalize_stats(results[n][1])
        finalize_stats(results[n][-1])
        results[n]["combined"] = combine_stats(results[n][1], results[n][-1])
    return results


# ── CORE ANALYSIS LOGIC ──────────────────────────────────────────────
def process_bar_stream(bar_iterable, patterns, status_every=STATUS_EVERY, phase_name="analysis"):
    """
    Core streak simulation over any iterable of bars.
    """
    results = init_results(patterns)

    history_dirs = deque(maxlen=max(patterns))
    prev_bar = None

    total_bars = 0
    bullish_bars = 0
    bearish_bars = 0
    neutral_bars = 0

    for bar in bar_iterable:
        total_bars += 1
        cur_dir = bar_dir(bar)

        if cur_dir == 1:
            bullish_bars += 1
        elif cur_dir == -1:
            bearish_bars += 1
        else:
            neutral_bars += 1

        # current bar is the "next bar" after a streak ending on prev_bar
        if prev_bar is not None:
            hist_list = list(history_dirs)

            for n in patterns:
                if len(hist_list) < n:
                    continue

                streak = hist_list[-n:]
                first = streak[0]

                if first == 0:
                    continue

                if any(x != first for x in streak):
                    continue

                streak_dir = first
                s = results[n][streak_dir]

                s["occurrences"] += 1

                # what did next bar do?
                if cur_dir == streak_dir:
                    s["next_same"] += 1
                elif cur_dir == -streak_dir:
                    s["next_opposite"] += 1
                else:
                    s["next_neutral"] += 1

                # simulation:
                # enter at close of prev_bar (Nth streak bar)
                # exit at close of current bar (next bar)
                entry = float(prev_bar["close"])
                exit_ = float(bar["close"])

                pnl = (exit_ - entry) * streak_dir
                # bullish streak => streak_dir = +1 => long
                # bearish streak => streak_dir = -1 => short

                s["trades"] += 1
                s["total_pnl_points"] += pnl

                if pnl > 0:
                    s["wins"] += 1
                elif pnl < 0:
                    s["losses"] += 1
                else:
                    s["flats"] += 1

        history_dirs.append(cur_dir)
        prev_bar = bar

        if status_every and total_bars % status_every == 0:
            print(
                f"[{phase_name}] processed bars: {total_bars:,} | "
                f"bull: {bullish_bars:,} | bear: {bearish_bars:,} | neutral: {neutral_bars:,}"
            )

    finalize_results(results, patterns)

    return {
        "total_bars": total_bars,
        "bullish_bars": bullish_bars,
        "bearish_bars": bearish_bars,
        "neutral_bars": neutral_bars,
        "results": results,
    }


# ── FULL FILE ANALYSIS (ONE PASS, STREAMED) ──────────────────────────
def analyze_full_file(path, patterns, status_every=STATUS_EVERY):
    if PRINT_STARTUP_INFO:
        print(f"[startup] mode = FULL FILE")
        print(f"[startup] input = {path}")
        print(f"[startup] patterns = {patterns}")
        print(f"[startup] status_every = {status_every:,}")
        print()

    return process_bar_stream(
        bar_iterable=iter_json_array(path),
        patterns=patterns,
        status_every=status_every,
        phase_name="full-scan",
    )


# ── LAST X BARS ANALYSIS (STREAM TO TAIL DEQUE, THEN ANALYZE) ────────
def analyze_last_n_bars(path, patterns, lookback_bars, status_every=STATUS_EVERY):
    if PRINT_STARTUP_INFO:
        print(f"[startup] mode = LAST N BARS")
        print(f"[startup] input = {path}")
        print(f"[startup] patterns = {patterns}")
        print(f"[startup] lookback_bars = {lookback_bars:,}")
        print(f"[startup] status_every = {status_every:,}")
        print()

    tail = deque(maxlen=lookback_bars)
    scanned = 0

    print(f"[tail-scan] building last {lookback_bars:,} bars buffer...")

    for bar in iter_json_array(path):
        tail.append(bar)
        scanned += 1

        if status_every and scanned % status_every == 0:
            print(
                f"[tail-scan] scanned bars: {scanned:,} | "
                f"currently kept in memory: {len(tail):,}"
            )

    print(
        f"[tail-scan] done. total scanned: {scanned:,} | "
        f"bars kept for analysis: {len(tail):,}"
    )
    print()

    return process_bar_stream(
        bar_iterable=tail,
        patterns=patterns,
        status_every=status_every,
        phase_name="last-n-analysis",
    )


# ── PUBLIC RUNNER ────────────────────────────────────────────────────
def run_streak_sim(path=INPUT_FILE, patterns=PATTERNS, lookback_bars=LOOKBACK_BARS, status_every=STATUS_EVERY):
    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")

    if not patterns:
        raise ValueError("PATTERNS cannot be empty")

    patterns = tuple(sorted(set(int(x) for x in patterns if int(x) > 0)))
    if not patterns:
        raise ValueError("PATTERNS must contain positive integers")

    if lookback_bars is None:
        analysis = analyze_full_file(path, patterns, status_every=status_every)
        analyzed_scope = "FULL FILE"
    else:
        lookback_bars = int(lookback_bars)
        if lookback_bars <= 0:
            raise ValueError("LOOKBACK_BARS must be None or a positive integer")
        analysis = analyze_last_n_bars(path, patterns, lookback_bars, status_every=status_every)
        analyzed_scope = f"LAST {lookback_bars:,} BARS"

    return {
        "file": path,
        "scope": analyzed_scope,
        "patterns": patterns,
        "total_bars": analysis["total_bars"],
        "bullish_bars": analysis["bullish_bars"],
        "bearish_bars": analysis["bearish_bars"],
        "neutral_bars": analysis["neutral_bars"],
        "results": analysis["results"],
    }


# ── PRINT REPORT ─────────────────────────────────────────────────────
def print_report(report):
    print()
    print("=" * 90)
    print("2PT RANGE BAR STREAK SIMULATION")
    print("=" * 90)
    print(f"File         : {report['file']}")
    print(f"Scope        : {report['scope']}")
    print(f"Patterns     : {report['patterns']}")
    print(f"Total bars   : {report['total_bars']:,}")
    print(f"Bullish bars : {report['bullish_bars']:,}")
    print(f"Bearish bars : {report['bearish_bars']:,}")
    print(f"Neutral bars : {report['neutral_bars']:,}")
    print()
    print("Logic:")
    print("  - After N consecutive bullish bars -> test whether next bar is bullish again")
    print("  - After N consecutive bearish bars -> test whether next bar is bearish again")
    print("  - Simulation:")
    print("      bullish streak -> LONG on close of Nth bar, exit on next close")
    print("      bearish streak -> SHORT on close of Nth bar, exit on next close")
    print()

    for n in report["patterns"]:
        print("-" * 90)
        print(f"AFTER {n} CONSECUTIVE BARS")
        print("-" * 90)

        for label, key in (
            ("Bullish streak", 1),
            ("Bearish streak", -1),
            ("Combined", "combined"),
        ):
            s = report["results"][n][key]

            print(f"{label}:")
            print(f"  occurrences         : {s['occurrences']:,}")
            print(f"  next same           : {s['next_same']:,} ({s['same_probability']:.2f}%)")
            print(f"  next opposite       : {s['next_opposite']:,} ({s['opposite_probability']:.2f}%)")
            print(f"  next neutral        : {s['next_neutral']:,}")
            print(f"  trades simulated    : {s['trades']:,}")
            print(f"  wins/losses/flats   : {s['wins']:,} / {s['losses']:,} / {s['flats']:,}")
            print(f"  win rate            : {s['win_rate']:.2f}%")
            print(f"  total pnl (points)  : {s['total_pnl_points']:.2f}")
            print(f"  avg pnl per trade   : {s['avg_pnl_points']:.4f}")
            print()

    print("=" * 90)
    print("DONE")
    print("=" * 90)


# ── MAIN ─────────────────────────────────────────────────────────────
def main():
    report = run_streak_sim(
        path=INPUT_FILE,
        patterns=PATTERNS,
        lookback_bars=LOOKBACK_BARS,
        status_every=STATUS_EVERY,
    )
    print_report(report)


if __name__ == "__main__":
    main()
