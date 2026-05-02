# backend/strategy_api.py
from __future__ import annotations

import json
import math
import os
import time as _time
from datetime import datetime, timezone

from flask import Blueprint, Response, jsonify, request

from backend.engine_core import BacktestEngine, _clean
from backend.strategy_loader import get_strategy, list_strategy_metadata


strategy_api = Blueprint("strategy_api", __name__)
DATA_DIR = "data"


# ============================================================
# Datetime helper
# ============================================================
def _parse_datetime_param(val):
    if not val or not isinstance(val, str) or not val.strip():
        return None
    val = val.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(val, fmt)
            dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue
    return None


# ============================================================
# Data loading
# ============================================================
def load_bars(range_pt: int, bar_range: int, start_date=None, end_date=None):
    path = os.path.join(DATA_DIR, f"{int(range_pt)}pt.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Dataset not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        bars = json.load(f)

    start_ts = _parse_datetime_param(start_date)
    end_ts = _parse_datetime_param(end_date)
    if start_ts or end_ts:
        if start_ts:
            bars = [b for b in bars if int(b["time"]) >= start_ts]
        if end_ts:
            bars = [b for b in bars if int(b["time"]) <= end_ts]

    if bar_range and int(bar_range) > 0:
        bars = bars[-int(bar_range):]

    normalized = []
    last_time = None
    for b in bars:
        t = int(b["time"])
        if last_time is not None and t <= last_time:
            t = last_time + 1
        last_time = t
        normalized.append({
            "time": t,
            "open": float(b["open"]),
            "high": float(b["high"]),
            "low": float(b["low"]),
            "close": float(b["close"]),
            "volume": float(b.get("volume", 0) or 0),
        })

    return normalized


# ============================================================
# Strategy metadata
# ============================================================
def strategy_detail_payload(strategy):
    return strategy.get_metadata()


# ============================================================
# Routes
# ============================================================
@strategy_api.get("/strategies")
def strategies_list():
    return jsonify(list_strategy_metadata()), 200


@strategy_api.get("/strategy/<strategy_id>")
def strategy_detail(strategy_id):
    try:
        strategy = get_strategy(strategy_id)
        return jsonify(strategy_detail_payload(strategy)), 200
    except KeyError as e:
        return jsonify({"error": str(e)}), 404


@strategy_api.post("/backtest/run")
def strategy_backtest_run():
    try:
        route_t0 = _time.perf_counter()

        payload = request.get_json(force=True) or {}

        strategy_id = payload.get("strategy_id")
        if not strategy_id:
            return jsonify({"error": "Missing strategy_id"}), 400

        strategy = get_strategy(strategy_id)

        bars = load_bars(
            range_pt=int(payload.get("range", 10)),
            bar_range=int(payload.get("bar_range", 1000)),
            start_date=payload.get("start_date"),
            end_date=payload.get("end_date"),
        )

        if len(bars) < 5:
            return jsonify({"error": "Insufficient data"}), 400

        engine = BacktestEngine(
            bars=bars,
            strategy=strategy,
            payload=payload,
        )

        result = engine.run()

        # ── JSON serialization timing ─────────────
        json_t0 = _time.perf_counter()
        response_body = json.dumps(result)
        json_t1 = _time.perf_counter()

        payload_kb = len(response_body) / 1024
        json_ms = (json_t1 - json_t0) * 1000
        total_ms = (json_t1 - route_t0) * 1000

        print(f"  [ROUTE TIMING] json={json_ms:.1f}ms "
              f"payload={payload_kb:.1f}KB "
              f"route_total={total_ms:.1f}ms")

        return Response(response_body, mimetype="application/json"), 200

    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except KeyError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500