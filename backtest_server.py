"""
backtest_server.py  —  NQ Range Bar Backtest Engine + Analytics
Run:  python backtest_server.py
POST /api/backtest        →  full results JSON
POST /api/backtest/stream →  NDJSON streaming progress + result
GET  /api/health          →  ok
"""
import json, math, os, time as _time
from flask import Flask, jsonify, request, Response, stream_with_context
from flask_cors import CORS
from backend.dollar_math import (
    points_to_dollars,
    finalize_trade_pnl,
    compute_position_size,
    compute_scaling_risk,
)
from backend.strategy_api import strategy_api
from backend.stats_engine import compute_all_stats, downsample_curve as ds_curve
from backend.shared import (
    precompute_ma,
    get_or_compute_ma,
    SpreadGenerator,
    calc_comm,
    compute_analytics,
    clean_for_json,
    cap_analytics_arrays,
)

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
    from datetime import datetime, timezone
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
def load_bars(range_pt, bar_range, symbol="NQ", start_date=None, end_date=None):
    range_val = float(range_pt)
    range_str = str(int(range_val)) if range_val == int(range_val) else str(range_val)
    path = os.path.join(DATA_DIR, symbol, f"{range_str}pt.json")
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
#  INDICATOR ENGINE  (Legacy-specific — uses shared MA functions)
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
        self._precomputed = get_or_compute_ma(closes, self.ma_type, self.period, dataset_id)
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
#  BACKTEST ENGINE  (LEGACY — UNTOUCHED LOGIC + risk% sizing)
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
        self.commission_per_lot = float(config.get("commission_per_lot", 0))
        self.gating_cfg    = config.get("gating", {})
        self.lot_size      = float(config.get("lot_size", 1.0))
        self.point_value   = float(config.get("point_value", 1.0))
        self.dollar_per_point = float(config.get("dollar_per_point", 1.0))
        self.starting_cap  = float(config.get("starting_capital", 10000.0))
        self.ambiguous_mode = config.get("ambiguous_bar_mode", "conservative")
        self.require_candle_confirm = config.get("require_candle_confirm", True)

        self.sl_mode = self.sl_cfg.get("mode", "fixed")
        self.tp_mode = self.tp_cfg.get("mode", "r_multiple")
        self.gating_enabled = bool(self.gating_cfg.get("enabled", False))

        self.spread_gen = SpreadGenerator(config.get("spread", {}))

        # ── Position sizing ──────────────────────────────────────
        self.sizing_mode = str(config.get("sizing_mode", "fixed")).strip().lower()
        self.risk_pct = float(config.get("risk_pct", 1.0))
        self.fixed_risk = float(config.get("fixed_risk", 10.0))
        self.scaling_enabled = bool(config.get("scaling_enabled", False))
        self.scaling_per = float(config.get("scaling_per", 100.0))
        self.scaling_risk = float(config.get("scaling_risk", 1.0))
        self.min_lot = float(config.get("min_lot", 0.01))
        self.max_lot = float(config.get("max_lot", 1000.0))
        self.lot_step = float(config.get("lot_step", 0.01))

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

        closes = [b["close"] for b in self.bars]
        dataset_id = id(self.bars)
        t0 = _time.perf_counter()
        self.entry_ind.precompute(closes, dataset_id)
        if self.sl_ma_eng:  self.sl_ma_eng.precompute(closes, dataset_id)
        if self.tp_ma_eng:  self.tp_ma_eng.precompute(closes, dataset_id)
        if self.gating_eng: self.gating_eng.precompute(closes, dataset_id)
        dt = _time.perf_counter() - t0

        sizing_str = f"lot={self.lot_size}" if self.sizing_mode == "fixed" \
                     else f"risk={self.risk_pct}%"
        print(f"  [ENGINE] {self.n} bars | entry={self.entry_mode} | "
              f"SL={self.sl_mode} | TP={self.tp_mode} | "
              f"gating={self.gating_enabled} | "
              f"candle_confirm={self.require_candle_confirm} | "
              f"{sizing_str} | spread={self.spread_gen.enabled} | "
              f"precompute={dt*1000:.1f}ms")

    def run(self):
        for _ in self._run_generator(): pass
        return self._build_result()

    def run_streaming(self):
        yield from self._run_generator()
        yield {"type": "result", "data": self._build_result()}

    # ── Core loop (LEGACY — UNTOUCHED + risk% sizing) ───────────
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

            mv = self.entry_ind.update(c)
            ma_hist.append(mv)
            sl_ma_val = self.sl_ma_eng.update(c) if self.sl_ma_eng else None
            tp_ma_val = self.tp_ma_eng.update(c) if self.tp_ma_eng else None
            gating_val = self.gating_eng.update(c) if self.gating_eng else None

            just_entered = False
            if pending_signal is not None and state == "flat":
                direction = pending_signal["direction"]
                ep = pending_signal.get("entry_price", o)

                sl_level, risk_pts = self._compute_sl(direction, ep, prev_sl_ma_val)
                tp_level = self._compute_tp(direction, ep, risk_pts)

                trade_spread = self.spread_gen.generate()
                ep = self.spread_gen.apply_entry(ep, direction, trade_spread)
                sl_level = self.spread_gen.apply_sl(sl_level, direction, trade_spread)
                tp_level = self.spread_gen.apply_tp(tp_level, direction, trade_spread)

                if sl_level is not None:
                    risk_pts = abs(ep - sl_level)

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

                # ── Dynamic position sizing (centralized) ────────
                trade_lot = self.lot_size
                if valid_entry and self.sizing_mode in ("risk_pct", "risk_per_trade"):
                    if risk_pts is None or risk_pts <= 0:
                        valid_entry = False
                    else:
                        balance_now = self.starting_cap + cum

                        # ── Determine risk amount ────────────────
                        risk_amount = 0.0
                        if self.sizing_mode == "risk_pct":
                            risk_amount = balance_now * (self.risk_pct / 100.0)
                        elif self.scaling_enabled:
                            risk_amount, sc_log = compute_scaling_risk(
                                balance_now, self.scaling_per, self.scaling_risk)
                            if len(self.trades) < 5:
                                print(f"  {sc_log}")
                        else:
                            risk_amount = self.fixed_risk

                        # ── Compute lot size ─────────────────────
                        if risk_amount <= 0:
                            valid_entry = False
                        else:
                            trade_lot, sz_log, sz_ok = compute_position_size(
                                risk_amount, risk_pts, self.point_value,
                                self.min_lot, self.max_lot, self.lot_step,
                                self.commission_per_lot, self.dollar_per_point,
                            )
                            if len(self.trades) < 5:
                                print(f"  {sz_log}")
                            if not sz_ok or trade_lot is None:
                                valid_entry = False
                else:
                    trade_lot = self.lot_size

                if valid_entry:
                    open_t = dict(
                        id=len(self.trades)+1, direction=direction,
                        entry_bar=pending_signal.get("signal_bar", i),
                        entry_time=pending_signal.get("signal_time", bar["time"]),
                        entry_price=round(ep, 4),
                        sl_level=sl_level, tp_level=tp_level,
                        initial_sl=sl_level, initial_tp=tp_level,
                        risk_pts=risk_pts, initial_risk_pts=risk_pts,
                        mae=0.0, mfe=0.0, bars_held=0,
                        spread=round(trade_spread, 4),
                        lot_size=trade_lot,
                    )
                    state = direction
                    just_entered = False

            pending_signal = None

            if state != "flat" and open_t is not None and not just_entered:
                closed = self._check_exit(open_t, bar, i, sl_ma_val, tp_ma_val)
                if closed:
                    trade_spread = closed.get("spread", 0.0)
                    raw_exit = closed["exit_price"]
                    # Only apply exit spread for market-price exits.
                    # SL_HIT / TP_R exit prices already include spread from entry setup.
                    exit_reason = closed.get("exit_reason", "")
                    if exit_reason not in ("SL_HIT", "TP_R"):
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
                              f"lot={t.get('lot_size')} "
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

            if state != "flat" and open_t is not None and just_entered:
                d = open_t["direction"]; ep2 = open_t["entry_price"]
                hh = bar["high"]; ll = bar["low"]
                open_t["mae"] = max(open_t.get("mae",0), (ep2-ll) if d=="long" else (hh-ep2))
                open_t["mfe"] = max(open_t.get("mfe",0), (hh-ep2) if d=="long" else (ep2-ll))

            if self.gating_enabled and gating_val is not None:
                if long_locked  and c < gating_val: long_locked  = False
                if short_locked and c > gating_val: short_locked = False

            if state != "flat" and open_t:
                pts = (c - open_t["entry_price"]) if state == "long" \
                      else (open_t["entry_price"] - c)
                t_lot = open_t.get("lot_size", self.lot_size)
                unrealised = points_to_dollars(pts, t_lot, self.point_value, self.dollar_per_point)
            else:
                unrealised = 0.0
            eq = self.starting_cap + cum + unrealised
            self.equity_curve.append(round(eq, 2))
            peak = max(peak, eq)
            dd = eq - peak
            self.dd_curve.append(round(dd, 2))

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
                pending_signal = {
                    "direction": sig,
                    "entry_price": c,
                    "signal_bar": i,
                    "signal_time": bar["time"],
                }

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
            trade_spread = closed.get("spread", 0.0)
            # end_of_data is a market-price exit, spread applies here
            closed["exit_price"] = round(
                self.spread_gen.apply_exit(closed["exit_price"], d, trade_spread), 4)
            self._finalize_trade(closed)
            cum += closed["net_pnl"]
            closed["cumulative_pnl"] = round(cum,2)
            self.trades.append(closed)

    # ── SL computation (LEGACY — UNTOUCHED) ─────────────────────
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

    # ── Exit evaluation (LEGACY — UNTOUCHED) ────────────────────
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
        commission = round(closed.get("lot_size", self.lot_size) * self.commission_per_lot, 5)
        trade_lot = closed.get("lot_size", self.lot_size)
        finalize_trade_pnl(
            closed,
            lot_size=trade_lot,
            point_value=self.point_value,
            dollar_per_point=self.dollar_per_point,
            commission=commission,
        )

    def _build_result(self):
        MAX_TRADES = 2000
        perf = {}
        build_t0 = _time.perf_counter()

        stats_t0 = _time.perf_counter()
        stats = compute_all_stats(
            trades=self.trades,
            equity_curve=self.equity_curve,
            bars=self.bars,
            starting_capital=self.starting_cap,
            lot_size=self.lot_size,
        )
        stats_t1 = _time.perf_counter()
        perf["stats_seconds"] = round(stats_t1 - stats_t0, 4)

        analytics_t0 = _time.perf_counter()
        analytics = compute_analytics(self.trades, self.bars,
                                      self.equity_curve, self.dd_curve,
                                      self.starting_cap)
        analytics_t1 = _time.perf_counter()
        perf["analytics_seconds"] = round(analytics_t1 - analytics_t0, 4)

        # Cap analytics arrays
        analytics = cap_analytics_arrays(analytics, 1500)

        # Cap trades in response
        total_count = len(self.trades)
        if total_count > MAX_TRADES:
            trades_out = self.trades[-MAX_TRADES:]
            print(f"  [TRADE CAP] Sending last {MAX_TRADES} of {total_count} trades")
        else:
            trades_out = self.trades

        result = dict(
            stats=stats,
            trades=trades_out,
            total_trade_count=total_count,
            trades_truncated=total_count > MAX_TRADES,
            equity_curve=ds_curve(self.equity_curve, 1500),
            drawdown_curve=ds_curve(self.dd_curve, 1500),
            analytics=analytics,
        )

        build_t1 = _time.perf_counter()
        perf["response_build_seconds"] = round(build_t1 - build_t0, 4)
        perf["trade_count"] = total_count
        result["performance"] = perf

        print(f"  [BUILD TIMING] stats={perf['stats_seconds']:.3f}s "
              f"analytics={perf['analytics_seconds']:.3f}s "
              f"response_build={perf['response_build_seconds']:.3f}s "
              f"trades={total_count}")

        return result


# ════════════════════════════════════════════════════════════════════
#  FLASK ROUTES (WITH TIMING)
# ════════════════════════════════════════════════════════════════════
def _validate_and_load(cfg):
    if not cfg: raise ValueError("Empty body")
    mas = cfg.get("mas",[])
    if not mas: raise ValueError("Need at least one MA")

    bars = load_bars(
        float(cfg.get("range", 10)),
        int(cfg.get("bar_range", 1000)),
        symbol=cfg.get("symbol", "NQ"),
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


@app.route("/api/backtest", methods=["POST"])
def run_backtest():
    try:
        route_t0 = _time.perf_counter()

        cfg=request.get_json(force=True); bars,cfg=_validate_and_load(cfg)

        sim_t0 = _time.perf_counter()
        engine=BacktestEngine(bars,cfg); result=engine.run()
        sim_t1 = _time.perf_counter()

        result = clean_for_json(result)

        json_t0 = _time.perf_counter()
        response_body = json.dumps(result)
        json_t1 = _time.perf_counter()

        payload_kb = len(response_body) / 1024
        route_t1 = _time.perf_counter()

        perf = result.get("performance", {})
        perf["simulation_seconds"] = round(sim_t1 - sim_t0, 4)
        perf["json_seconds"] = round(json_t1 - json_t0, 4)
        perf["total_seconds"] = round(route_t1 - route_t0, 4)
        perf["payload_size_kb"] = round(payload_kb, 1)

        print(f"  [BACKTEST TIMING] simulation={perf.get('simulation_seconds',0):.3f}s "
              f"stats={perf.get('stats_seconds',0):.3f}s "
              f"analytics={perf.get('analytics_seconds',0):.3f}s "
              f"json={perf.get('json_seconds',0):.3f}s "
              f"payload={payload_kb:.1f}KB "
              f"total={perf.get('total_seconds',0):.3f}s "
              f"trades={perf.get('trade_count',0)}")

        return Response(response_body, mimetype="application/json"), 200
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
        try:
            for msg in engine.run_streaming():
                yield json.dumps(clean_for_json(msg))+"\n"
        except Exception as e:
            import traceback; traceback.print_exc()
            yield json.dumps({"type": "error", "error": str(e)}) + "\n"
    return Response(stream_with_context(generate()), mimetype='application/x-ndjson',
        headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no','Access-Control-Allow-Origin':'*'})


@app.route("/api/ranges/<symbol>", methods=["GET"])
def get_ranges(symbol):
    folder = os.path.join(DATA_DIR, symbol)
    if not os.path.isdir(folder):
        return jsonify([]), 200
    ranges = []
    for f in os.listdir(folder):
        if f.endswith("pt.json"):
            try:
                val = float(f.replace("pt.json", ""))
                ranges.append(val)
            except ValueError:
                pass
    ranges.sort()
    return jsonify(ranges), 200


@app.route("/api/health", methods=["GET"])
def health(): return jsonify(status="ok"),200


if __name__ == "__main__":
    print("="*52+"\n  NQ Backtest Server  http://localhost:5000\n"+"="*52)
    app.run(host="0.0.0.0", port=5000, debug=False)