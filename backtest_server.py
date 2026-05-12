"""
backtest_server.py — NQ Range Bar Backtest (Bar-Close Execution)
POST /api/backtest        → full results JSON
POST /api/backtest/stream → NDJSON streaming
GET  /api/health          → ok
"""
import json, math, os, time as _time
from flask import Flask, jsonify, request, Response, stream_with_context
from flask_cors import CORS
from backend.dollar_math import points_to_dollars, finalize_trade_pnl, apply_spread_entry
from backend.strategy_api import strategy_api
from backend.stats_engine import compute_all_stats, downsample_curve as ds_curve
from backend.shared import (
    precompute_ma, get_or_compute_ma,
    compute_analytics, clean_for_json, cap_analytics_arrays,
)
app = Flask(__name__)
CORS(app, supports_credentials=True)
PROGRESS_EMIT_INTERVAL = 0.15   # ← ADD THIS LINE
DATA_DIR = "data"
app.register_blueprint(strategy_api, url_prefix="/api")


# ════════════════════════════════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════════════════════════════════
def _parse_datetime_param(val):
    if not val or not isinstance(val, str) or not val.strip():
        return None
    val = val.strip()
    from datetime import datetime, timezone
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(val, fmt).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue
    return None


def load_bars(range_pt, bar_range, start_date=None, end_date=None):
    path = os.path.join(DATA_DIR, f"{range_pt}pt.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Dataset not found: {path}")
    with open(path) as f:
        bars = json.load(f)
    total = len(bars)
    start_ts = _parse_datetime_param(start_date)
    end_ts   = _parse_datetime_param(end_date)
    if start_ts: bars = [b for b in bars if b["time"] >= start_ts]
    if end_ts:   bars = [b for b in bars if b["time"] <= end_ts]
    if bar_range and bar_range > 0:
        bars = bars[-bar_range:]
    print(f"  [DATA] {path} — {total} total, using {len(bars)}")
    return bars


# ════════════════════════════════════════════════════════════════════
#  INDICATOR ENGINE (unchanged precompute logic)
# ════════════════════════════════════════════════════════════════════
class IndicatorEngine:
    def __init__(self, ma_type, period):
        self.ma_type = ma_type.upper()
        self.period  = int(period)
        self._precomputed = None
        self._idx = -1

    def precompute(self, closes, dataset_id=None):
        self._precomputed = get_or_compute_ma(closes, self.ma_type, self.period, dataset_id)
        self._idx = -1

    def advance(self):
        self._idx += 1
        if self._precomputed and self._idx < len(self._precomputed):
            return self._precomputed[self._idx]
        return None

    @property
    def value(self):
        if self._precomputed and 0 <= self._idx < len(self._precomputed):
            return self._precomputed[self._idx]
        return None


class MultiIndicatorEngine:
    def __init__(self, ma_defs):
        self.engines = [IndicatorEngine(d["type"], d["period"]) for d in ma_defs]

    def precompute(self, closes, dataset_id=None):
        for eng in self.engines:
            eng.precompute(closes, dataset_id)

    def advance(self):
        return [eng.advance() for eng in self.engines]


# ════════════════════════════════════════════════════════════════════
#  BACKTEST ENGINE (LEGACY — BAR-CLOSE EXECUTION)
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
        self.lot_size      = float(config.get("lot_size", 1.0))
        self.point_value   = float(config.get("point_value", 1.0))
        self.starting_cap  = float(config.get("starting_capital", 10000.0))
        self.ambiguous_mode = config.get("ambiguous_bar_mode", "conservative")
        self.require_candle_confirm = config.get("require_candle_confirm", True)

        self.sl_mode = self.sl_cfg.get("mode", "fixed")
        self.tp_mode = self.tp_cfg.get("mode", "r_multiple")

        self.gating_cfg     = config.get("gating", {})
        self.gating_enabled = bool(self.gating_cfg.get("enabled", False))

        # ── Commission (simple model) ────────────────────────
        comm = config.get("commission", {})
        self.commission_per_1_lot = float(comm.get("amount", 0)) if isinstance(comm, dict) \
            else float(config.get("commission_per_1_lot", 0))

        # ── Spread (simple fixed points) ─────────────────────
        spread = config.get("spread", {})
        if isinstance(spread, dict) and spread.get("enabled"):
            self.spread_points = (float(spread.get("min", 0)) + float(spread.get("max", 0))) / 2.0
        else:
            self.spread_points = float(config.get("spread_points", 0))

        # ── Indicator engines ────────────────────────────────
        self.entry_ind = MultiIndicatorEngine(self.ma_defs)

        self.sl_ma_eng = None
        if self.sl_mode in ("ma_cross", "ma_snapshot"):
            self.sl_ma_eng = IndicatorEngine(
                self.sl_cfg.get("ma_type", "EMA"), self.sl_cfg.get("ma_length", 50))

        self.tp_ma_eng = None
        if self.tp_mode == "ma_cross":
            self.tp_ma_eng = IndicatorEngine(
                self.tp_cfg.get("ma_type", "EMA"), self.tp_cfg.get("ma_length", 9))

        self.gating_eng = None
        if self.gating_enabled:
            self.gating_eng = IndicatorEngine(
                self.gating_cfg.get("ma_type", "HMA"), self.gating_cfg.get("ma_length", 21))

        self.trades       = []
        self.equity_curve = []
        self.dd_curve     = []

        # Precompute all
        closes = [b["close"] for b in self.bars]
        did    = id(self.bars)
        self.entry_ind.precompute(closes, did)
        if self.sl_ma_eng:  self.sl_ma_eng.precompute(closes, did)
        if self.tp_ma_eng:  self.tp_ma_eng.precompute(closes, did)
        if self.gating_eng: self.gating_eng.precompute(closes, did)

        print(f"  [ENGINE] {self.n} bars | entry={self.entry_mode} | "
              f"SL={self.sl_mode} TP={self.tp_mode} | gating={self.gating_enabled} | "
              f"lot={self.lot_size} pv={self.point_value} comm/lot={self.commission_per_1_lot} "
              f"spread={self.spread_points}pts")

    def run(self):
        for _ in self._run_generator(): pass
        return self._build_result()

    def run_streaming(self):
        yield from self._run_generator()
        yield {"type": "result", "data": self._build_result()}

    # ── SL/TP computation ────────────────────────────────────
    def _compute_sl(self, direction, entry_price, sl_ma_val):
        mode = self.sl_mode
        if mode == "fixed":
            pts = float(self.sl_cfg.get("fixed_pts", 8))
            if pts <= 0: return None, None
            sl = (entry_price - pts) if direction == "long" else (entry_price + pts)
            return round(sl, 4), pts
        elif mode in ("ma_snapshot", "ma_cross"):
            if sl_ma_val is None: return None, None
            if direction == "long"  and sl_ma_val >= entry_price: return None, None
            if direction == "short" and sl_ma_val <= entry_price: return None, None
            risk = abs(entry_price - sl_ma_val)
            if risk <= 0: return None, None
            return round(sl_ma_val, 4), round(risk, 4)
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

    # ── Exit check (TP/SL wicks + MA cross close) ────────────
    def _check_exit(self, t, bar, bi, sl_ma_val, tp_ma_val):
        d = t["direction"]; sl = t.get("sl_level"); tp = t.get("tp_level")
        h = bar["high"]; l = bar["low"]; c = bar["close"]

        t["bars_held"] = bi - t["entry_bar"]
        ep = t["entry_price"]
        t["mae"] = max(t.get("mae", 0), (ep - l) if d == "long" else (h - ep))
        t["mfe"] = max(t.get("mfe", 0), (h - ep) if d == "long" else (ep - l))

        sl_hit = tp_hit = False
        if sl is not None:
            if d == "long"  and l <= sl: sl_hit = True
            if d == "short" and h >= sl: sl_hit = True
        if tp is not None:
            if d == "long"  and h >= tp: tp_hit = True
            if d == "short" and l <= tp: tp_hit = True

        if sl_hit and tp_hit:
            m = self.ambiguous_mode
            if m == "optimistic":      return self._make_exit(t, bar, bi, tp, "TP_HIT")
            elif m == "nearest_to_open":
                if abs(bar["open"] - sl) <= abs(bar["open"] - tp):
                    return self._make_exit(t, bar, bi, sl, "SL_HIT")
                return self._make_exit(t, bar, bi, tp, "TP_HIT")
            else: return self._make_exit(t, bar, bi, sl, "SL_HIT")
        if sl_hit: return self._make_exit(t, bar, bi, sl, "SL_HIT")
        if tp_hit: return self._make_exit(t, bar, bi, tp, "TP_HIT")

        # MA cross exits
        if self.sl_mode == "ma_cross" and sl_ma_val is not None:
            if d == "long"  and c < sl_ma_val: return self._make_exit(t, bar, bi, c, "MA_SL_EXIT")
            if d == "short" and c > sl_ma_val: return self._make_exit(t, bar, bi, c, "MA_SL_EXIT")
        if self.tp_mode == "ma_cross" and tp_ma_val is not None:
            if d == "long"  and c < tp_ma_val: return self._make_exit(t, bar, bi, c, "MA_TP_EXIT")
            if d == "short" and c > tp_ma_val: return self._make_exit(t, bar, bi, c, "MA_TP_EXIT")
        return None

    def _make_exit(self, t, bar, bi, exit_price, reason):
        return {**t, "exit_bar": bi, "exit_time": bar["time"],
                "exit_price": round(exit_price, 4), "exit_reason": reason,
                "holding_seconds": abs(bar["time"] - t["entry_time"]),
                "bars_held": bi - t["entry_bar"]}

    def _finalize_trade(self, closed):
        finalize_trade_pnl(closed, lot_size=self.lot_size,
                           point_value=self.point_value,
                           commission_per_1_lot=self.commission_per_1_lot)

# ════════════════════════════════════════════════════════════
    #  CORE LOOP (BAR-CLOSE EXECUTION)
    # ════════════════════════════════════════════════════════════
    def _run_generator(self):
        state  = "flat"
        open_t = None
        cum    = 0.0
        peak   = self.starting_cap

        # Signal state
        bc  = 0   # bull confirm counter
        bc2 = 0   # bear confirm counter
        regime = "neutral"  # above / below / neutral
        long_locked  = False
        short_locked = False

        last_closed_pnl = None
        progress_interval = max(1, self.n // 100)
        last_emit_time = _time.perf_counter()

        for i, bar in enumerate(self.bars):
            c = float(bar["close"]); o = float(bar["open"])
            h = float(bar["high"]);  l = float(bar["low"])
            bull = c > o; bear = c < o

            # ── Advance all indicators ───────────────────────
            mv = self.entry_ind.advance()
            sl_ma_val = self.sl_ma_eng.advance() if self.sl_ma_eng else None
            tp_ma_val = self.tp_ma_eng.advance() if self.tp_ma_eng else None
            gating_val = self.gating_eng.advance() if self.gating_eng else None

            # ── Gating unlock ────────────────────────────────
            if self.gating_enabled and gating_val is not None:
                if long_locked  and c < gating_val: long_locked = False
                if short_locked and c > gating_val: short_locked = False

            # ═══ STEP 3: Manage open position ════════════════
            if state != "flat" and open_t is not None:
                if open_t["entry_bar"] != i:
                    # Future bar — check TP/SL + MA exits
                    closed = self._check_exit(open_t, bar, i, sl_ma_val, tp_ma_val)
                    if closed:
                        self._finalize_trade(closed)
                        cum += closed["net_pnl"]
                        closed["cumulative_pnl"] = round(cum, 2)
                        self.trades.append(closed)
                        last_closed_pnl = closed["net_pnl"]

                        # Gating lock
                        if self.gating_enabled:
                            if closed["direction"] == "long":  long_locked = True
                            if closed["direction"] == "short": short_locked = True

                        if len(self.trades) <= 5:
                            t = closed
                            print(f"  [TRADE #{t['id']}] {t['direction'].upper()} "
                                  f"entry={t['entry_price']} exit={t['exit_price']} "
                                  f"pts={t.get('points_pnl')} R={t.get('net_pnl_r')} "
                                  f"gross=${t.get('gross_pnl')} comm=${t.get('commission')} "
                                  f"net=${t.get('net_pnl')} | {t.get('exit_reason')}")

                        open_t = None; state = "flat"
                # Entry bar — skip TP/SL (no same-bar close)

            # ═══ STEP 4: Signal generation ═══════════════════
            sig = None

            if state == "flat":
                ma_vals = [v for v in mv if v is not None]

                if not ma_vals:
                    bc = 0; bc2 = 0
                else:
                    ab = all(c > v for v in ma_vals)
                    bl = all(c < v for v in ma_vals)

                    # Regime tracking
                    if regime == "above" and not ab:
                        regime = "neutral"; bc = 0
                    elif regime == "below" and not bl:
                        regime = "neutral"; bc2 = 0

                    # ── above_all_mas (2-bar confirm) ────────
                    if self.entry_mode == "above_all_mas":
                        if regime != "below":
                            if ab and bull: bc += 1
                            else: bc = 0
                            if bc >= 2:
                                sig = "BUY"; regime = "above"; bc = 0

                        if sig is None and regime != "above":
                            if bl and bear: bc2 += 1
                            else: bc2 = 0
                            if bc2 >= 2:
                                sig = "SELL"; regime = "below"; bc2 = 0

                    # ── ma_cross ─────────────────────────────
                    elif self.entry_mode == "ma_cross" and len(ma_vals) >= 2 and i > 0:
                        prev_ma1 = None; prev_ma2 = None
                        for eng_idx, eng in enumerate(self.entry_ind.engines):
                            prev = eng._precomputed[i - 1] if eng._precomputed and i - 1 < len(eng._precomputed) else None
                            if eng_idx == 0: prev_ma1 = prev
                            elif eng_idx == 1: prev_ma2 = prev
                        ma1_now = mv[0] if len(mv) > 0 else None
                        ma2_now = mv[1] if len(mv) > 1 else None
                        if None not in (ma1_now, ma2_now, prev_ma1, prev_ma2):
                            if prev_ma1 <= prev_ma2 and ma1_now > ma2_now: sig = "BUY"
                            elif prev_ma1 >= prev_ma2 and ma1_now < ma2_now: sig = "SELL"

                    # ── trend_filter ─────────────────────────
                    elif self.entry_mode == "trend_filter" and i > 0:
                        lm = ma_vals[-1]
                        longest_eng = self.entry_ind.engines[-1]
                        prev_lm = longest_eng._precomputed[i - 1] \
                            if longest_eng._precomputed and i - 1 < len(longest_eng._precomputed) else None
                        prev_c = float(self.bars[i - 1]["close"])
                        if lm is not None and prev_lm is not None:
                            if prev_c <= prev_lm and c > lm and bull:  sig = "BUY"
                            elif prev_c >= prev_lm and c < lm and bear: sig = "SELL"

                    # ── Candle direction confirm ─────────────
                    if self.require_candle_confirm:
                        if sig == "BUY"  and not bull: sig = None
                        if sig == "SELL" and not bear: sig = None

                    # ── Gating filter ────────────────────────
                    if sig == "BUY"  and self.gating_enabled and long_locked:  sig = None
                    if sig == "SELL" and self.gating_enabled and short_locked: sig = None

            # ═══ STEP 5: Execute entry at bar close ══════════
            if state == "flat" and sig in ("BUY", "SELL"):
                direction = "long" if sig == "BUY" else "short"
                ep = apply_spread_entry(c, direction, self.spread_points)

                sl_level, risk_pts = self._compute_sl(direction, ep, sl_ma_val)
                tp_level = self._compute_tp(direction, ep, risk_pts)

                # Validate
                valid = True
                if risk_pts is not None and risk_pts <= 0: valid = False
                if sl_level is not None:
                    if direction == "long"  and sl_level >= ep: valid = False
                    if direction == "short" and sl_level <= ep: valid = False
                if tp_level is not None:
                    if direction == "long"  and tp_level <= ep: valid = False
                    if direction == "short" and tp_level >= ep: valid = False

                if valid:
                    open_t = dict(
                        id=len(self.trades) + 1, direction=direction,
                        entry_bar=i, entry_time=bar["time"],
                        entry_price=round(ep, 4),
                        sl_level=sl_level, tp_level=tp_level,
                        risk_pts=risk_pts, mae=0.0, mfe=0.0, bars_held=0,
                        spread_points=self.spread_points,
                        lot_size=self.lot_size,
                    )
                    state = direction

            # ═══ Equity / drawdown ═══════════════════════════
            if state != "flat" and open_t:
                pts = (c - open_t["entry_price"]) if state == "long" \
                      else (open_t["entry_price"] - c)
                unrealised = points_to_dollars(pts, self.lot_size, self.point_value)
            else:
                unrealised = 0.0
            eq = self.starting_cap + cum + unrealised
            self.equity_curve.append(round(eq, 2))
            peak = max(peak, eq)
            self.dd_curve.append(round(eq - peak, 2))

            # ═══ Progress yield ══════════════════════════════
            now = _time.perf_counter()
            if i == 0 or i == self.n - 1 or \
               (i % progress_interval == 0 and (now - last_emit_time) >= PROGRESS_EMIT_INTERVAL):
                last_emit_time = now
                oi = None
                if open_t:
                    oi = dict(direction=open_t["direction"],
                              entry_price=round(open_t["entry_price"], 2),
                              sl=round(open_t["sl_level"], 2) if open_t.get("sl_level") else None,
                              tp=round(open_t["tp_level"], 2) if open_t.get("tp_level") else None)
                yield dict(type="progress", bar=i, total=self.n,
                           pct=round(i / max(self.n - 1, 1) * 100, 1),
                           equity=round(eq, 2), drawdown=round(eq - peak, 2),
                           open_trade=oi, last_closed_pnl=last_closed_pnl)

        # ═══ End of data — close open trade ══════════════════
        if state != "flat" and open_t:
            lb = self.bars[-1]
            closed = self._make_exit(open_t, lb, self.n - 1, float(lb["close"]), "end_of_data")
            self._finalize_trade(closed)
            cum += closed["net_pnl"]
            closed["cumulative_pnl"] = round(cum, 2)
            self.trades.append(closed)

    # ════════════════════════════════════════════════════════════
    #  BUILD RESULT
    # ════════════════════════════════════════════════════════════
    def _build_result(self):
        stats = compute_all_stats(
            trades=self.trades, equity_curve=self.equity_curve,
            bars=self.bars, starting_capital=self.starting_cap,
            lot_size=self.lot_size)

        analytics = compute_analytics(
            self.trades, self.bars,
            self.equity_curve, self.dd_curve, self.starting_cap)
        analytics = cap_analytics_arrays(analytics, 1500)

        total_count = len(self.trades)
        trades_out = self.trades[-2000:] if total_count > 2000 else self.trades

        result = clean_for_json(dict(
            stats=stats, trades=trades_out,
            total_trade_count=total_count,
            trades_truncated=total_count > 2000,
            equity_curve=ds_curve(self.equity_curve, 1500),
            drawdown_curve=ds_curve(self.dd_curve, 1500),
            analytics=analytics,
            config=dict(
                starting_capital=self.starting_cap,
                range=self.config.get("range", 10),
                lot_size=self.lot_size,
                point_value=self.point_value,
                entry=self.entry_mode,
                mas=self.ma_defs),
        ))
        print(f"  [LEGACY] {total_count} trades generated")
        return result


# ════════════════════════════════════════════════════════════════════
#  ROUTES
# ════════════════════════════════════════════════════════════════════
@app.post("/api/backtest")
def backtest_run():
    try:
        config = request.get_json(force=True) or {}
        bars = load_bars(
            int(config.get("range", 10)),
            int(config.get("bar_range", 1000)),
            config.get("start_date"), config.get("end_date"))
        if len(bars) < 5:
            return jsonify({"error": "Not enough bars"}), 400
        engine = BacktestEngine(bars, config)
        result = engine.run()
        return Response(json.dumps(result), mimetype="application/json"), 200
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.post("/api/backtest/stream")
def backtest_stream():
    try:
        config = request.get_json(force=True) or {}
        bars = load_bars(
            int(config.get("range", 10)),
            int(config.get("bar_range", 1000)),
            config.get("start_date"), config.get("end_date"))
        if len(bars) < 5:
            return jsonify({"error": "Not enough bars"}), 400
        engine = BacktestEngine(bars, config)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500

    def generate():
        try:
            for msg in engine.run_streaming():
                yield json.dumps(clean_for_json(msg)) + "\n"
        except Exception as e:
            import traceback; traceback.print_exc()
            yield json.dumps({"type": "error", "error": str(e)}) + "\n"

    return Response(stream_with_context(generate()),
                    mimetype='application/x-ndjson',
                    headers={'Cache-Control': 'no-cache',
                             'X-Accel-Buffering': 'no',
                             'Access-Control-Allow-Origin': '*'})


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "engine": "bar_close_v2"})


if __name__ == "__main__":
    print("=" * 58)
    print("  JINNI ZERO — NQ Range Bar Backtester (Bar-Close v2)")
    print("=" * 58)
    app.run(host="0.0.0.0", port=5000, debug=True)