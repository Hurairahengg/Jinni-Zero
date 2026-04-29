"""
backtest_server.py  —  NQ Range Bar Backtest Engine + Analytics
Run:  python backtest_server.py
POST /api/backtest        →  full results JSON
POST /api/backtest/stream →  NDJSON streaming progress + result
GET  /api/health          →  ok
"""
import json, math, os, random, time as _time
from collections import defaultdict
from datetime import datetime, timezone
from flask import Flask, jsonify, request, Response, stream_with_context
from flask_cors import CORS
from backend.strategy_api import strategy_api
from backend.stats_engine import compute_all_stats, downsample_curve as ds_curve

app = Flask(__name__)
CORS(app, supports_credentials=True)
DATA_DIR = "data"

app.register_blueprint(strategy_api, url_prefix="/api")

# ════════════════════════════════════════════════════════════════════
#  DATETIME HELPER
# ════════════════════════════════════════════════════════════════════
def _parse_datetime_param(val):
    if not val or not isinstance(val, str) or not val.strip():
        return None
    val = val.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(val, fmt)
            dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue
    return None

# ════════════════════════════════════════════════════════════════════
#  DATA
# ════════════════════════════════════════════════════════════════════
def load_bars(range_pt, bar_range, start_date=None, end_date=None):
    path = os.path.join(DATA_DIR, f"{range_pt}pt.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Dataset not found: {path}")
    with open(path) as f:
        bars = json.load(f)
    total = len(bars)

    start_ts = _parse_datetime_param(start_date)
    end_ts   = _parse_datetime_param(end_date)
    if start_ts is not None or end_ts is not None:
        before = len(bars)
        if start_ts is not None:
            bars = [b for b in bars if b["time"] >= start_ts]
        if end_ts is not None:
            bars = [b for b in bars if b["time"] <= end_ts]
        print(f"  [DATA] Date filter: {before} → {len(bars)} bars"
              f"  (start={start_date}, end={end_date})")

    if bar_range and bar_range > 0:
        bars = bars[-bar_range:]
    print(f"  [DATA] Loaded {path} — {total} total bars, using last {len(bars)}")
    return bars

# ════════════════════════════════════════════════════════════════════
#  O(n) PRECOMPUTATION FUNCTIONS
# ════════════════════════════════════════════════════════════════════

def _precompute_sma(closes, period):
    n = len(closes)
    out = [None] * n
    if period < 1 or n < period:
        return out
    s = 0.0
    for i in range(period):
        s += closes[i]
    out[period - 1] = s / period
    for i in range(period, n):
        s += closes[i] - closes[i - period]
        out[i] = s / period
    return out

def _precompute_ema(closes, period):
    n = len(closes)
    out = [None] * n
    if period < 1 or n < period:
        return out
    k = 2.0 / (period + 1)
    s = 0.0
    for i in range(period):
        s += closes[i]
    ema = s / period
    out[period - 1] = ema
    for i in range(period, n):
        ema = closes[i] * k + ema * (1 - k)
        out[i] = ema
    return out

def _precompute_wma(closes, period):
    n = len(closes)
    p = period
    out = [None] * n
    if p < 1 or n < p:
        return out
    denom = p * (p + 1) / 2.0
    ws = 0.0
    s = 0.0
    for j in range(p):
        s += closes[j]
        ws += closes[j] * (j + 1)
    out[p - 1] = ws / denom
    for i in range(p, n):
        ws = ws + p * closes[i] - s
        s = s + closes[i] - closes[i - p]
        out[i] = ws / denom
    return out

def _precompute_hma(closes, period):
    n = len(closes)
    p = period
    half = p // 2
    sq = int(math.floor(math.sqrt(p)))
    out = [None] * n
    if half < 1 or sq < 1:
        return out
    wma_full = _precompute_wma(closes, p)
    wma_half = _precompute_wma(closes, half)
    diff = [None] * n
    diff_start = None
    for i in range(n):
        if wma_full[i] is not None and wma_half[i] is not None:
            diff[i] = 2.0 * wma_half[i] - wma_full[i]
            if diff_start is None:
                diff_start = i
    if diff_start is None:
        return out
    valid_diff = []
    valid_map = []
    for i in range(diff_start, n):
        if diff[i] is not None:
            valid_diff.append(diff[i])
            valid_map.append(i)
        else:
            break
    if len(valid_diff) < sq:
        return out
    wma_final = _precompute_wma(valid_diff, sq)
    for j in range(len(wma_final)):
        if wma_final[j] is not None:
            out[valid_map[j]] = wma_final[j]
    return out

def _precompute_ma(closes, ma_type, period):
    t = ma_type.upper()
    if t == "SMA": return _precompute_sma(closes, period)
    if t == "EMA": return _precompute_ema(closes, period)
    if t == "WMA": return _precompute_wma(closes, period)
    if t == "HMA": return _precompute_hma(closes, period)
    return [None] * len(closes)

# ── Cross-run cache ───────────────────────────────────────────────
_ma_cache = {}
_ma_cache_dataset_id = None

def _get_or_compute_ma(closes, ma_type, period, dataset_id=None):
    global _ma_cache, _ma_cache_dataset_id
    if dataset_id is not None and dataset_id != _ma_cache_dataset_id:
        _ma_cache = {}
        _ma_cache_dataset_id = dataset_id
    key = (ma_type.upper(), period)
    if key not in _ma_cache:
        t0 = _time.perf_counter()
        _ma_cache[key] = _precompute_ma(closes, ma_type, period)
        dt = _time.perf_counter() - t0
        print(f"  [CACHE] Computed {ma_type.upper()}({period}) over {len(closes)} bars in {dt*1000:.1f}ms")
    return _ma_cache[key]

# ════════════════════════════════════════════════════════════════════
#  INDICATOR ENGINE
# ════════════════════════════════════════════════════════════════════
class IndicatorEngine:
    def __init__(self, ma_type, period):
        self.ma_type = ma_type.upper()
        self.period  = int(period)
        self._precomputed = None
        self._idx = -1
        self.closes  = []
        self._ema_val = None

    def precompute(self, closes, dataset_id=None):
        self._precomputed = _get_or_compute_ma(closes, self.ma_type, self.period, dataset_id)
        self._idx = -1

    def update(self, close):
        if self._precomputed is not None:
            self._idx += 1
            if self._idx < len(self._precomputed):
                return self._precomputed[self._idx]
            return None
        self.closes.append(close)
        n = len(self.closes)
        if self.ma_type == "EMA":  return self._ema(close, n)
        if self.ma_type == "SMA":  return self._sma(n)
        if self.ma_type == "WMA":  return self._wma_at(self.closes, self.period, n-1)
        if self.ma_type == "HMA":  return self._hma(n)
        return None

    def _ema(self, close, n):
        p = self.period; k = 2/(p+1)
        if self._ema_val is None:
            if n < p: return None
            self._ema_val = sum(self.closes[-p:])/p
            return self._ema_val
        self._ema_val = close*k + self._ema_val*(1-k)
        return self._ema_val

    def _sma(self, n):
        p = self.period
        if n < p: return None
        return sum(self.closes[-p:])/p

    @staticmethod
    def _wma_at(closes, p, ei):
        if ei < p-1: return None
        d = p*(p+1)/2
        return sum(closes[ei-j]*(p-j) for j in range(p))/d

    def _hma(self, n):
        p = self.period; h = p//2; sq = int(math.floor(math.sqrt(p)))
        if n < p+sq-1: return None
        ei = n-1; ds = []
        for k in range(sq):
            wf = self._wma_at(self.closes, p, ei-k)
            wh = self._wma_at(self.closes, h, ei-k)
            if wf is None or wh is None: return None
            ds.append(2*wh - wf)
        ds.reverse()
        d = sq*(sq+1)/2
        return sum(ds[sq-1-j]*(sq-j) for j in range(sq))/d

    @property
    def last_value(self):
        if self._precomputed is not None:
            if 0 <= self._idx < len(self._precomputed):
                return self._precomputed[self._idx]
            return None
        if not self.closes: return None
        n = len(self.closes)
        if self.ma_type == "EMA":  return self._ema_val
        if self.ma_type == "SMA":  return self._sma(n) if n >= self.period else None
        if self.ma_type == "WMA":  return self._wma_at(self.closes, self.period, n-1)
        if self.ma_type == "HMA":  return self._hma(n)
        return None


class MultiIndicatorEngine:
    def __init__(self, ma_defs):
        self.engines = [IndicatorEngine(d["type"], d["period"]) for d in ma_defs]
    def precompute(self, closes, dataset_id=None):
        for eng in self.engines:
            eng.precompute(closes, dataset_id)
    def update(self, close):
        return [eng.update(close) for eng in self.engines]

# ════════════════════════════════════════════════════════════════════
#  COMMISSION
# ════════════════════════════════════════════════════════════════════
def calc_comm(cfg, contracts=1):
    t = cfg.get("type","flat"); a = float(cfg.get("amount",0))
    if t=="flat":         return a
    if t=="per_contract": return a*contracts*2
    if t=="per_side":     return a*2
    return a

# ════════════════════════════════════════════════════════════════════
#  SPREAD GENERATOR
#  - One spread per trade (generated at entry, reused at exit)
#  - Direction-aware: always makes performance worse
#  - Deterministic when seed is provided
# ════════════════════════════════════════════════════════════════════
class SpreadGenerator:
    """Generates realistic random spread per trade."""

    def __init__(self, config):
        self.enabled = bool(config.get("enabled", False))
        self.min_spread = float(config.get("min", 0.0))
        self.max_spread = float(config.get("max", 0.0))

        seed = config.get("seed")
        if seed is not None and seed != "" and seed != 0:
            self._rng = random.Random(int(seed))
        else:
            self._rng = random.Random()

        if self.min_spread > self.max_spread:
            self.min_spread, self.max_spread = self.max_spread, self.min_spread

        if self.min_spread < 0:
            self.min_spread = 0
        if self.max_spread < 0:
            self.max_spread = 0

        if self.enabled:
            print(f"  [SPREAD] Enabled: ${self.min_spread:.4f} - ${self.max_spread:.4f}"
                  f"  seed={'deterministic' if seed else 'random'}")

    def generate(self):
        """Generate one spread value for a trade (call once at entry)."""
        if not self.enabled or self.max_spread <= 0:
            return 0.0
        return self._rng.uniform(self.min_spread, self.max_spread)

    def apply_entry(self, price, direction, spread):
        """Worsen entry price by spread. Long=higher entry, Short=lower entry."""
        if spread <= 0:
            return price
        half = spread / 2.0
        if direction == "long":
            return price + half   # buy at worse (higher) price
        else:
            return price - half   # sell at worse (lower) price

    def apply_exit(self, price, direction, spread):
        """Worsen exit price by spread. Long=lower exit, Short=higher exit."""
        if spread <= 0:
            return price
        half = spread / 2.0
        if direction == "long":
            return price - half   # sell at worse (lower) price
        else:
            return price + half   # buy-to-cover at worse (higher) price

    def apply_sl(self, sl_level, direction, spread):
        """Worsen SL trigger. Long=SL triggers sooner (higher), Short=SL triggers sooner (lower)."""
        if sl_level is None or spread <= 0:
            return sl_level
        half = spread / 2.0
        if direction == "long":
            return sl_level + half   # effective SL is higher (closer to entry)
        else:
            return sl_level - half   # effective SL is lower (closer to entry)

    def apply_tp(self, tp_level, direction, spread):
        """Worsen TP target. Long=TP further away (higher), Short=TP further away (lower)."""
        if tp_level is None or spread <= 0:
            return tp_level
        half = spread / 2.0
        if direction == "long":
            return tp_level + half   # need price to go higher to actually fill TP
        else:
            return tp_level - half   # need price to go lower to actually fill TP

# ════════════════════════════════════════════════════════════════════
#  BACKTEST ENGINE
# ════════════════════════════════════════════════════════════════════
class BacktestEngine:
    def __init__(self, bars, config):
        self.bars   = bars
        self.config = config
        self.n      = len(bars)

        self.ma_defs       = config.get("mas", [])
        self.entry_mode    = config.get("entry", "above_all_mas")
        self.sl_cfg        = config.get("sl", {})
        self.tp_cfg        = config.get("tp", {})
        self.comm_cfg      = config.get("commission", {})
        self.gating_cfg    = config.get("gating", {})
        self.lot_size      = float(config.get("lot_size", 1.0))
        self.starting_cap  = float(config.get("starting_capital", 10000.0))
        self.ambiguous_mode = config.get("ambiguous_bar_mode", "conservative")
        self.require_candle_confirm = config.get("require_candle_confirm", True)

        self.sl_mode = self.sl_cfg.get("mode", "fixed")
        self.tp_mode = self.tp_cfg.get("mode", "r_multiple")
        self.gating_enabled = bool(self.gating_cfg.get("enabled", False))

        # ── Spread generator ──────────────────────────────────────
        self.spread_gen = SpreadGenerator(config.get("spread", {}))

        self.entry_ind = MultiIndicatorEngine(self.ma_defs)

        self.sl_ma_eng = None
        if self.sl_mode in ("ma_cross", "ma_snapshot"):
            self.sl_ma_eng = IndicatorEngine(
                self.sl_cfg.get("ma_type", "EMA"),
                self.sl_cfg.get("ma_length", 50))

        self.tp_ma_eng = None
        if self.tp_mode == "ma_cross":
            self.tp_ma_eng = IndicatorEngine(
                self.tp_cfg.get("ma_type", "EMA"),
                self.tp_cfg.get("ma_length", 9))

        self.gating_eng = None
        if self.gating_enabled:
            self.gating_eng = IndicatorEngine(
                self.gating_cfg.get("ma_type", "HMA"),
                self.gating_cfg.get("ma_length", 21))

        self.trades       = []
        self.equity_curve = []
        self.dd_curve     = []

        # ── Precompute ALL indicators ──────────────────────────
        closes = [b["close"] for b in self.bars]
        dataset_id = id(self.bars)
        t0 = _time.perf_counter()
        self.entry_ind.precompute(closes, dataset_id)
        if self.sl_ma_eng:  self.sl_ma_eng.precompute(closes, dataset_id)
        if self.tp_ma_eng:  self.tp_ma_eng.precompute(closes, dataset_id)
        if self.gating_eng: self.gating_eng.precompute(closes, dataset_id)
        dt = _time.perf_counter() - t0
        print(f"  [ENGINE] {self.n} bars | entry={self.entry_mode} | "
              f"SL={self.sl_mode} | TP={self.tp_mode} | "
              f"gating={self.gating_enabled} | "
              f"candle_confirm={self.require_candle_confirm} | "
              f"lot={self.lot_size} | spread={self.spread_gen.enabled} | "
              f"precompute={dt*1000:.1f}ms")

    def run(self):
        for _ in self._run_generator(): pass
        return self._build_result()

    def run_streaming(self):
        yield from self._run_generator()
        yield {"type": "result", "data": self._build_result()}

    # ── Core loop ───────────────────────────────────────────────
    def _run_generator(self):
        state = "flat"
        open_t = None
        pending_signal = None

        ma_hist = []
        bc = bc2 = 0
        regime = "neutral"

        long_locked  = False
        short_locked = False

        cum  = 0.0
        peak = self.starting_cap
        last_closed_pnl = None

        progress_interval = max(1, self.n // 100)
        last_emit_time = _time.perf_counter()
        EMIT_MIN_INTERVAL = 0.15

        prev_sl_ma_val = None

        for i, bar in enumerate(self.bars):
            c = bar["close"]; o = bar["open"]; h = bar["high"]; l = bar["low"]

            # ── Update ALL indicators ───────────────────────────
            mv = self.entry_ind.update(c)
            ma_hist.append(mv)
            sl_ma_val = self.sl_ma_eng.update(c) if self.sl_ma_eng else None
            tp_ma_val = self.tp_ma_eng.update(c) if self.tp_ma_eng else None
            gating_val = self.gating_eng.update(c) if self.gating_eng else None

            # ── Execute pending entry at this bar's open ────────
            just_entered = False
            if pending_signal is not None and state == "flat":
                direction = pending_signal
                ep = o   # enter at THIS bar's open

                sl_level, risk_pts = self._compute_sl(direction, ep, prev_sl_ma_val)
                tp_level = self._compute_tp(direction, ep, risk_pts)

                # ── APPLY SPREAD ────────────────────────────────
                trade_spread = self.spread_gen.generate()
                ep = self.spread_gen.apply_entry(ep, direction, trade_spread)
                sl_level = self.spread_gen.apply_sl(sl_level, direction, trade_spread)
                tp_level = self.spread_gen.apply_tp(tp_level, direction, trade_spread)

                # Recalculate risk after spread
                if sl_level is not None:
                    risk_pts = abs(ep - sl_level)

                # ── VALIDATE ────────────────────────────────────
                valid_entry = True
                if risk_pts is None or risk_pts <= 0:
                    valid_entry = False
                if sl_level is not None:
                    if direction == "long" and sl_level >= ep:
                        valid_entry = False
                    if direction == "short" and sl_level <= ep:
                        valid_entry = False
                if tp_level is not None:
                    if direction == "long" and tp_level <= ep:
                        valid_entry = False
                    if direction == "short" and tp_level >= ep:
                        valid_entry = False

                if valid_entry:
                    open_t = dict(
                        id=len(self.trades)+1, direction=direction,
                        entry_bar=i, entry_time=bar["time"], entry_price=round(ep, 4),
                        sl_level=sl_level, tp_level=tp_level,
                        risk_pts=risk_pts, mae=0.0, mfe=0.0, bars_held=0,
                        spread=round(trade_spread, 4),
                    )
                    state = direction
                    just_entered = True

            pending_signal = None

            # ── Manage open trade ───────────────────────────────
            if state != "flat" and open_t is not None and not just_entered:
                closed = self._check_exit(open_t, bar, i, sl_ma_val, tp_ma_val)
                if closed:
                    # Apply spread to exit price
                    trade_spread = closed.get("spread", 0.0)
                    raw_exit = closed["exit_price"]
                    closed["exit_price"] = round(
                        self.spread_gen.apply_exit(raw_exit, closed["direction"], trade_spread), 4)

                    self._finalize_trade(closed)
                    cum += closed["net_pnl"]
                    closed["cumulative_pnl"] = round(cum, 2)
                    self.trades.append(closed)
                    last_closed_pnl = closed["net_pnl"]

                    if len(self.trades) <= 5:
                        t = closed
                        print(f"  [TRADE #{t['id']}] {t['direction'].upper()} "
                              f"entry={t['entry_price']} exit={t['exit_price']} "
                              f"SL={t.get('sl_level')} TP={t.get('tp_level')} "
                              f"risk={t.get('risk_pts')}pts "
                              f"spread={t.get('spread',0):.4f} "
                              f"points={t.get('points_pnl')} "
                              f"R={t.get('net_pnl_r')} "
                              f"gross=${t.get('gross_pnl')} "
                              f"comm=${t.get('commission')} "
                              f"net=${t.get('net_pnl')} "
                              f"reason={t.get('exit_reason')}")

                    if self.gating_enabled:
                        if closed["direction"] == "long":  long_locked = True
                        if closed["direction"] == "short": short_locked = True

                    open_t = None
                    state = "flat"

            # ── Update MAE/MFE on entry bar ─────────────────────
            if state != "flat" and open_t is not None and just_entered:
                d = open_t["direction"]; ep2 = open_t["entry_price"]
                hh = bar["high"]; ll = bar["low"]
                open_t["mae"] = max(open_t.get("mae",0), (ep2-ll) if d=="long" else (hh-ep2))
                open_t["mfe"] = max(open_t.get("mfe",0), (hh-ep2) if d=="long" else (ep2-ll))

            # ── Update gating locks ─────────────────────────────
            if self.gating_enabled and gating_val is not None:
                if long_locked  and c < gating_val: long_locked  = False
                if short_locked and c > gating_val: short_locked = False

            # ── Equity tracking ─────────────────────────────────
            if state != "flat" and open_t:
                pts = (c - open_t["entry_price"]) if state == "long" \
                      else (open_t["entry_price"] - c)
                unrealised = pts * self.lot_size
            else:
                unrealised = 0.0
            eq = self.starting_cap + cum + unrealised
            self.equity_curve.append(round(eq, 2))
            peak = max(peak, eq)
            dd = eq - peak
            self.dd_curve.append(round(dd, 2))

            # ── Emit progress (throttled) ───────────────────────
            now = _time.perf_counter()
            should_emit = (
                i == 0 or
                i == self.n - 1 or
                (i % progress_interval == 0 and (now - last_emit_time) >= EMIT_MIN_INTERVAL)
            )
            if should_emit:
                last_emit_time = now
                oi = None
                if open_t:
                    oi = dict(direction=open_t["direction"],
                              entry_price=round(open_t["entry_price"],2),
                              sl=round(open_t["sl_level"],2) if open_t.get("sl_level") else None,
                              tp=round(open_t["tp_level"],2) if open_t.get("tp_level") else None)
                yield dict(type="progress", bar=i, total=self.n,
                           pct=round(i/max(self.n-1,1)*100,1),
                           equity=round(eq,2), drawdown=round(dd,2),
                           open_trade=oi, last_closed_pnl=last_closed_pnl)

            prev_sl_ma_val = sl_ma_val

            # ── Signal detection ────────────────────────────────
            if state != "flat" or pending_signal is not None:
                continue
            if any(v is None for v in mv):
                continue

            ab = all(c > v for v in mv)
            bl = all(c < v for v in mv)
            bull = c > o
            bear = c < o

            if regime == "above" and not ab:
                regime = "neutral"; bc = 0
            elif regime == "below" and not bl:
                regime = "neutral"; bc2 = 0

            sig = None

            if self.entry_mode == "above_all_mas":
                if regime != "below":
                    if ab and bull: bc += 1
                    else: bc = 0
                    if bc >= 2:
                        sig = "long"; regime = "above"; bc = 0
                if sig is None and regime != "above":
                    if bl and bear: bc2 += 1
                    else: bc2 = 0
                    if bc2 >= 2:
                        sig = "short"; regime = "below"; bc2 = 0

            elif self.entry_mode == "ma_cross" and len(self.ma_defs) >= 2 and i > 0:
                pm = ma_hist[i-1]
                if None not in (mv[0], mv[1], pm[0], pm[1]):
                    if pm[0] <= pm[1] and mv[0] > mv[1]: sig = "long"
                    elif pm[0] >= pm[1] and mv[0] < mv[1]: sig = "short"

            elif self.entry_mode == "trend_filter" and i > 0:
                lm = mv[-1]; plm = ma_hist[i-1][-1]; pc = self.bars[i-1]["close"]
                if lm and plm:
                    if pc <= plm and c > lm and bull:
                        sig = "long"
                    elif pc >= plm and c < lm and bear:
                        sig = "short"

            if self.require_candle_confirm:
                if sig == "long" and not bull:
                    sig = None
                if sig == "short" and not bear:
                    sig = None

            if sig == "long"  and self.gating_enabled and long_locked:  sig = None
            if sig == "short" and self.gating_enabled and short_locked: sig = None

            if sig:
                pending_signal = sig

        # ── Close open trade at end of data ─────────────────────
        if state != "flat" and open_t:
            lb = self.bars[-1]; cp = lb["close"]
            d = open_t["direction"]
            open_t["mae"] = max(open_t.get("mae",0),
                                (open_t["entry_price"]-lb["low"]) if d=="long" else (lb["high"]-open_t["entry_price"]))
            open_t["mfe"] = max(open_t.get("mfe",0),
                                (lb["high"]-open_t["entry_price"]) if d=="long" else (open_t["entry_price"]-lb["low"]))
            closed = {**open_t,
                      "exit_bar": self.n-1, "exit_time": lb["time"],
                      "exit_price": round(cp,2), "exit_reason": "end_of_data",
                      "holding_seconds": abs(lb["time"]-open_t["entry_time"]),
                      "bars_held": self.n-1-open_t["entry_bar"]}
            # Apply spread to end-of-data exit
            trade_spread = closed.get("spread", 0.0)
            closed["exit_price"] = round(
                self.spread_gen.apply_exit(closed["exit_price"], d, trade_spread), 4)
            self._finalize_trade(closed)
            cum += closed["net_pnl"]
            closed["cumulative_pnl"] = round(cum,2)
            self.trades.append(closed)

    # ── SL computation ──────────────────────────────────────────
    def _compute_sl(self, direction, entry_price, sl_ma_val):
        mode = self.sl_mode
        if mode == "fixed":
            pts = float(self.sl_cfg.get("fixed_pts", 8))
            if pts <= 0: return None, None
            sl = (entry_price - pts) if direction == "long" else (entry_price + pts)
            return round(sl, 4), pts
        elif mode == "ma_snapshot":
            if sl_ma_val is None: return None, None
            if direction == "long" and sl_ma_val >= entry_price:
                return None, None
            if direction == "short" and sl_ma_val <= entry_price:
                return None, None
            risk = abs(entry_price - sl_ma_val)
            if risk <= 0: return None, None
            return round(sl_ma_val, 4), round(risk, 4)
        elif mode == "ma_cross":
            if sl_ma_val is None: return None, None
            if direction == "long" and sl_ma_val >= entry_price:
                return None, None
            if direction == "short" and sl_ma_val <= entry_price:
                return None, None
            risk = abs(entry_price - sl_ma_val)
            if risk <= 0: return None, None
            return None, round(risk, 4)
        return None, None

    def _compute_tp(self, direction, entry_price, risk_pts):
        mode = self.tp_mode
        if mode == "r_multiple":
            r = float(self.tp_cfg.get("r_multiple", 2))
            if risk_pts and risk_pts > 0:
                tp = (entry_price + risk_pts * r) if direction == "long" \
                     else (entry_price - risk_pts * r)
                return round(tp, 4)
        return None

    # ── Exit evaluation ─────────────────────────────────────────
    def _check_exit(self, t, bar, bi, sl_ma_val, tp_ma_val):
        d  = t["direction"]; ep = t["entry_price"]
        sl = t.get("sl_level")
        tp = t.get("tp_level")
        hh = bar["high"]; ll = bar["low"]; c = bar["close"]

        t["bars_held"] = bi - t["entry_bar"]
        t["mae"] = max(t.get("mae",0), (ep-ll) if d=="long" else (hh-ep))
        t["mfe"] = max(t.get("mfe",0), (hh-ep) if d=="long" else (ep-ll))

        sl_hit = False
        if sl is not None:
            if d == "long"  and ll <= sl: sl_hit = True
            if d == "short" and hh >= sl: sl_hit = True

        tp_hit = False
        if tp is not None:
            if d == "long"  and hh >= tp: tp_hit = True
            if d == "short" and ll <= tp: tp_hit = True

        if sl_hit and tp_hit:
            m = self.ambiguous_mode
            if m == "optimistic":
                return self._make_exit(t, bar, bi, tp, "TP_R")
            elif m == "nearest_to_open":
                if abs(bar["open"]-sl) <= abs(bar["open"]-tp):
                    return self._make_exit(t, bar, bi, sl, "SL_HIT")
                else:
                    return self._make_exit(t, bar, bi, tp, "TP_R")
            else:
                return self._make_exit(t, bar, bi, sl, "SL_HIT")

        if sl_hit: return self._make_exit(t, bar, bi, sl, "SL_HIT")
        if tp_hit: return self._make_exit(t, bar, bi, tp, "TP_R")

        if self.sl_mode == "ma_cross" and sl_ma_val is not None:
            if d == "long"  and c < sl_ma_val:
                return self._make_exit(t, bar, bi, c, "MA_SL_EXIT")
            if d == "short" and c > sl_ma_val:
                return self._make_exit(t, bar, bi, c, "MA_SL_EXIT")

        if self.tp_mode == "ma_cross" and tp_ma_val is not None:
            if d == "long"  and c < tp_ma_val:
                return self._make_exit(t, bar, bi, c, "MA_TP_EXIT")
            if d == "short" and c > tp_ma_val:
                return self._make_exit(t, bar, bi, c, "MA_TP_EXIT")

        return None

    def _make_exit(self, t, bar, bi, exit_price, reason):
        return {**t,
                "exit_bar": bi, "exit_time": bar["time"],
                "exit_price": round(exit_price, 4),
                "exit_reason": reason,
                "holding_seconds": abs(bar["time"] - t["entry_time"]),
                "bars_held": bi - t["entry_bar"]}

    def _finalize_trade(self, closed):
        d = closed["direction"]
        ep = closed["entry_price"]
        xp = closed["exit_price"]

        dir_sign = 1 if d == "long" else -1
        points_pnl = (xp - ep) * dir_sign

        sl = closed.get("sl_level")
        rp = closed.get("risk_pts")
        if sl is not None:
            rp = abs(ep - sl)
        if rp is None or rp <= 0:
            rp = None

        gross_dollar = points_pnl * self.lot_size
        commission = calc_comm(self.comm_cfg)
        net_dollar = gross_dollar - commission

        r_mult = None
        if rp is not None and rp > 0:
            r_mult = points_pnl / rp

        risk_dollar = rp * self.lot_size if rp and rp > 0 else None

        closed.update(
            points_pnl = round(points_pnl, 4),
            gross_pnl  = round(gross_dollar, 2),
            commission = round(commission, 2),
            net_pnl    = round(net_dollar, 2),
            net_pnl_r  = round(r_mult, 3) if r_mult is not None else None,
            risk_pts   = round(rp, 4) if rp is not None else None,
            risk_dollar= round(risk_dollar, 2) if risk_dollar else None,
            mae_dollar = round(closed.get("mae", 0) * self.lot_size, 2),
            mfe_dollar = round(closed.get("mfe", 0) * self.lot_size, 2),
        )

    def _build_result(self):
        stats = compute_all_stats(
            trades=self.trades,
            equity_curve=self.equity_curve,
            bars=self.bars,
            starting_capital=self.starting_cap,
            lot_size=self.lot_size,
        )

        analytics = compute_analytics(self.trades, self.bars,
                                      self.equity_curve, self.dd_curve,
                                      self.starting_cap)

        return dict(
            stats=stats,
            trades=self.trades,
            equity_curve=ds_curve(self.equity_curve, 1500),
            drawdown_curve=ds_curve(self.dd_curve, 1500),
            analytics=analytics,
        )

# ════════════════════════════════════════════════════════════════════
#  ANALYTICS + MONTE CARLO
# ════════════════════════════════════════════════════════════════════
def compute_analytics(trades, bars, equity_curve, dd_curve, starting_cap=10000):
    if not trades: return {}
    nets = [t["net_pnl"] for t in trades]
    r_mul = [t.get("net_pnl_r") or 0 for t in trades]
    r_hist = _histogram(r_mul, bins=20)

    W = 20
    roll_wr=[]; roll_exp=[]; roll_pf=[]; roll_sharpe=[]
    for i in range(len(trades)):
        sl = max(0,i-W+1); chunk = [t["net_pnl"] for t in trades[sl:i+1]]
        wc = [x for x in chunk if x > 0]; lc = [x for x in chunk if x <= 0]
        wrc = len(wc)/len(chunk) if chunk else 0
        awc = sum(wc)/len(wc) if wc else 0
        alc = sum(lc)/len(lc) if lc else 0
        roll_wr.append(round(wrc*100,1))
        roll_exp.append(round(wrc*awc+(1-wrc)*alc,2))
        gwa=sum(wc); gla=abs(sum(lc))
        roll_pf.append(round(gwa/gla,3) if gla else None)
        if len(chunk) > 1:
            mu=sum(chunk)/len(chunk); var=sum((x-mu)**2 for x in chunk)/(len(chunk)-1)
            sd=math.sqrt(var) if var>0 else 0
            roll_sharpe.append(round((mu/sd)*math.sqrt(252),3) if sd else None)
        else: roll_sharpe.append(None)

    dur_hist = _histogram([t.get("bars_held",0) for t in trades], bins=15)
    mae_mfe = [dict(mae=round(t.get("mae",0),2), mfe=round(t.get("mfe",0),2),
                     win=t["net_pnl"]>0) for t in trades]
    ret_scatter = [dict(x=i+1, y=round(r,3), win=r>0) for i,r in enumerate(r_mul)]

    by_hour = defaultdict(list)
    for t in trades:
        try: hh=datetime.fromtimestamp(t["entry_time"],tz=timezone.utc).hour; by_hour[hh].append(t["net_pnl"])
        except: pass
    hour_perf = {str(hh): dict(trades=len(v), net=round(sum(v),2),
                 wr=round(len([x for x in v if x>0])/len(v)*100,1)) for hh,v in by_hour.items()}

    DOW=["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
    by_dow=defaultdict(list)
    for t in trades:
        try: d=datetime.fromtimestamp(t["entry_time"],tz=timezone.utc).weekday(); by_dow[d].append(t["net_pnl"])
        except: pass
    dow_perf={DOW[d]: dict(trades=len(v), net=round(sum(v),2),
              wr=round(len([x for x in v if x>0])/len(v)*100,1)) for d,v in by_dow.items()}

    MONTHS=["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    by_mon=defaultdict(list)
    for t in trades:
        try: m=datetime.fromtimestamp(t["entry_time"],tz=timezone.utc).month-1; by_mon[m].append(t["net_pnl"])
        except: pass
    mon_perf={MONTHS[m]: dict(trades=len(v), net=round(sum(v),2),
              wr=round(len([x for x in v if x>0])/len(v)*100,1)) for m,v in by_mon.items()}

    bar_ranges=[b["high"]-b["low"] for b in bars]; vol_regime={}
    if bar_ranges:
        med_rng=sorted(bar_ranges)[len(bar_ranges)//2]
        by_vol={"low_vol":[],"high_vol":[]}
        for t in trades:
            bi=t.get("entry_bar",0)
            if bi<len(bar_ranges):
                k="low_vol" if bar_ranges[bi]<med_rng else "high_vol"
                by_vol[k].append(t["net_pnl"])
        vol_regime={k: dict(trades=len(v), net=round(sum(v),2),
                    wr=round(len([x for x in v if x>0])/len(v)*100,1) if v else 0)
                    for k,v in by_vol.items()}

    chop_by={"trending":[],"choppy":[]}
    for t in trades:
        bi=t.get("entry_bar",0)
        if bi>=3:
            last3=[bars[bi-k]["close"]-bars[bi-k]["open"] for k in range(1,4)]
            same_dir=all(x>0 for x in last3) or all(x<0 for x in last3)
            chop_by["trending" if same_dir else "choppy"].append(t["net_pnl"])
    chop_regime={k: dict(trades=len(v), net=round(sum(v),2),
                 wr=round(len([x for x in v if x>0])/len(v)*100,1) if v else 0)
                 for k,v in chop_by.items()}

    N_SIM = 1000
    rng = random.Random(42)
    sim_finals=[]; sim_max_dds=[]; mc_paths_sample=[]
    cap = starting_cap
    for sim_i in range(N_SIM):
        shuffled = nets[:]
        rng.shuffle(shuffled)
        cum_mc = cap; peak_mc = cap; max_dd_s = 0; cum_path = []
        for p in shuffled:
            cum_mc += p
            cum_path.append(round(cum_mc, 2))
            peak_mc = max(peak_mc, cum_mc)
            dd_abs = cum_mc - peak_mc
            max_dd_s = min(max_dd_s, dd_abs)
        sim_finals.append(round(cum_mc, 2))
        sim_max_dds.append(round(max_dd_s, 2))
        if sim_i < 100:
            mc_paths_sample.append(ds_curve(cum_path, 500))
    sim_finals.sort(); sim_max_dds.sort()
    def pct(arr,p): idx=int(len(arr)*p/100); return arr[min(idx,len(arr)-1)]

    dd_10 = starting_cap * 0.10
    dd_20 = starting_cap * 0.20
    dd_30 = starting_cap * 0.30

    mc = dict(
        n_simulations=N_SIM,
        final_equity=dict(p5=pct(sim_finals,5), p25=pct(sim_finals,25),
                          p50=pct(sim_finals,50), p75=pct(sim_finals,75),
                          p95=pct(sim_finals,95)),
        max_drawdown=dict(p5=pct(sim_max_dds,5), p25=pct(sim_max_dds,25),
                          p50=pct(sim_max_dds,50), p75=pct(sim_max_dds,75),
                          p95=pct(sim_max_dds,95)),
        prob_profitable=round(len([x for x in sim_finals if x > cap])/N_SIM*100,1),
        prob_dd_10=round(len([x for x in sim_max_dds if abs(x) >= dd_10])/N_SIM*100,1),
        prob_dd_20=round(len([x for x in sim_max_dds if abs(x) >= dd_20])/N_SIM*100,1),
        prob_dd_30=round(len([x for x in sim_max_dds if abs(x) >= dd_30])/N_SIM*100,1),
        paths_sample=mc_paths_sample,
        final_dist=_histogram(sim_finals, bins=30),
        dd_dist=_histogram(sim_max_dds, bins=30),
    )

    total_comm=sum(t.get("commission",0) for t in trades)
    gross_abs=sum(abs(t["gross_pnl"]) for t in trades)
    comm_summary=dict(
        total=round(total_comm,2),
        per_trade=round(total_comm/len(trades),2) if trades else 0,
        pct_of_gross=round(total_comm/gross_abs*100,2) if gross_abs else 0,
        net_without_comm=round(sum(t["gross_pnl"] for t in trades),2),
        net_with_comm=round(sum(t["net_pnl"] for t in trades),2))

    return dict(
        r_histogram=r_hist,
        rolling=dict(win_rate=roll_wr, expectancy=roll_exp,
                     profit_factor=roll_pf, sharpe=roll_sharpe),
        duration_histogram=dur_hist, mae_mfe=mae_mfe,
        return_scatter=ret_scatter,
        time_of_day=hour_perf, day_of_week=dow_perf, by_month=mon_perf,
        regime=dict(volatility=vol_regime, choppiness=chop_regime),
        monte_carlo=mc, commission=comm_summary)

def _histogram(data, bins=20):
    if not data: return dict(edges=[], counts=[])
    mn=min(data); mx=max(data)
    if mn==mx: return dict(edges=[mn,mx], counts=[len(data)])
    w=(mx-mn)/bins
    counts=[0]*bins; edges=[round(mn+i*w,3) for i in range(bins+1)]
    for x in data: counts[min(int((x-mn)/w),bins-1)] += 1
    return dict(edges=edges, counts=counts)

# ════════════════════════════════════════════════════════════════════
#  FLASK ROUTES
# ════════════════════════════════════════════════════════════════════
def _validate_and_load(cfg):
    if not cfg: raise ValueError("Empty body")
    mas = cfg.get("mas",[])
    if not mas: raise ValueError("Need at least one MA")

    bars = load_bars(
        int(cfg.get("range",10)),
        int(cfg.get("bar_range",1000)),
        start_date=cfg.get("start_date"),
        end_date=cfg.get("end_date"),
    )

    max_period = 0
    for m in mas:
        p = int(m.get("period", 0))
        if p > max_period:
            max_period = p

    sl_cfg = cfg.get("sl", {})
    if sl_cfg.get("mode") in ("ma_cross", "ma_snapshot"):
        sp = int(sl_cfg.get("ma_length", 50))
        if sp > max_period:
            max_period = sp

    tp_cfg = cfg.get("tp", {})
    if tp_cfg.get("mode") == "ma_cross":
        tp = int(tp_cfg.get("ma_length", 9))
        if tp > max_period:
            max_period = tp

    gating_cfg = cfg.get("gating", {})
    if gating_cfg.get("enabled"):
        gp = int(gating_cfg.get("ma_length", 21))
        if gp > max_period:
            max_period = gp

    min_bars = max_period + int(math.sqrt(max(max_period, 1))) + 2
    min_bars = max(min_bars, 5)

    if len(bars) < min_bars:
        raise ValueError(
            f"Need at least {min_bars} bars for these indicators "
            f"(longest MA period = {max_period}), but only {len(bars)} bars available. "
            f"Try a larger date range or lower bar count."
        )

    return bars, cfg

def _clean(obj):
    if isinstance(obj,float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj,dict): return {k:_clean(v) for k,v in obj.items()}
    if isinstance(obj,list): return [_clean(v) for v in obj]
    return obj

@app.route("/api/backtest", methods=["POST"])
def run_backtest():
    try:
        cfg=request.get_json(force=True); bars,cfg=_validate_and_load(cfg)
        engine=BacktestEngine(bars,cfg); result=engine.run()
        return jsonify(_clean(result)),200
    except ValueError as e: return jsonify(error=str(e)),400
    except FileNotFoundError as e: return jsonify(error=str(e)),404
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify(error=str(e)),500

@app.route("/api/backtest/stream", methods=["POST"])
def run_backtest_stream():
    try:
        cfg=request.get_json(force=True); bars,cfg=_validate_and_load(cfg)
    except ValueError as e: return jsonify(error=str(e)),400
    except FileNotFoundError as e: return jsonify(error=str(e)),404
    except Exception as e: return jsonify(error=str(e)),500
    engine=BacktestEngine(bars,cfg)
    def generate():
        for msg in engine.run_streaming():
            yield json.dumps(_clean(msg))+"\n"
    return Response(stream_with_context(generate()), mimetype='application/x-ndjson',
        headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no','Access-Control-Allow-Origin':'*'})

@app.route("/api/health", methods=["GET"])
def health(): return jsonify(status="ok"),200

if __name__ == "__main__":
    print("="*52+"\n  NQ Backtest Server  http://localhost:5000\n"+"="*52)
    app.run(host="0.0.0.0", port=5000, debug=False)