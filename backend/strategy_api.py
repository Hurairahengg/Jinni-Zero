"""
JINNI ZERO — Strategy API Routes
"""
from __future__ import annotations

import json
import os
import time as _time
from datetime import datetime, timezone

from flask import Blueprint, Response, jsonify, request, stream_with_context

from backend.engine_core import BacktestEngine
from backend.shared import clean_for_json
from backend.strategy_loader import get_strategy, list_strategy_metadata, validate_lookback

strategy_api = Blueprint("strategy_api", __name__)
DATA_DIR = "data"


def _parse_datetime_param(val):
    if not val or not isinstance(val, str) or not val.strip():
        return None
    val = val.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(val, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue
    return None


def load_bars(range_pt, bar_range, start_date=None, end_date=None):
    path = os.path.join(DATA_DIR, f"{int(range_pt)}pt.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Dataset not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        bars = json.load(f)

    start_ts = _parse_datetime_param(start_date)
    end_ts = _parse_datetime_param(end_date)
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


def _setup_engine(payload):
    """Shared setup for both streaming and non-streaming endpoints."""
    strategy_id = payload.get("strategy_id")
    if not strategy_id:
        raise ValueError("Missing strategy_id")

    strategy = get_strategy(strategy_id)

    bars = load_bars(
        range_pt=int(payload.get("range", 10)),
        bar_range=int(payload.get("bar_range", 1000)),
        start_date=payload.get("start_date"),
        end_date=payload.get("end_date"),
    )

    lookback_override = int(payload.get("lookback_override", 0) or 0)
    validate_lookback(strategy, len(bars), lookback_override)

    if len(bars) < 5:
        raise ValueError("Insufficient data")

    return BacktestEngine(bars=bars, strategy=strategy, payload=payload)


@strategy_api.get("/strategies")
def strategies_list():
    return jsonify(list_strategy_metadata()), 200


@strategy_api.get("/strategy/<strategy_id>")
def strategy_detail(strategy_id):
    try:
        strategy = get_strategy(strategy_id)
        return jsonify(strategy.get_metadata()), 200
    except KeyError as e:
        return jsonify({"error": str(e)}), 404


@strategy_api.post("/backtest/run")
def strategy_backtest_run():
    """Non-streaming: single JSON response."""
    try:
        route_t0 = _time.perf_counter()
        payload = request.get_json(force=True) or {}

        engine = _setup_engine(payload)
        result = engine.run()

        json_t0 = _time.perf_counter()
        response_body = json.dumps(result)
        json_t1 = _time.perf_counter()

        payload_kb = len(response_body) / 1024
        total_ms = (json_t1 - route_t0) * 1000

        print(f"  [ROUTE TIMING] json={((json_t1-json_t0)*1000):.1f}ms "
              f"payload={payload_kb:.1f}KB route_total={total_ms:.1f}ms")

        return Response(response_body, mimetype="application/json"), 200

    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except KeyError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@strategy_api.post("/backtest/run/stream")
def strategy_backtest_run_stream():
    """Streaming: NDJSON progress + result. Matches legacy /api/backtest/stream."""
    try:
        payload = request.get_json(force=True) or {}
        engine = _setup_engine(payload)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except KeyError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
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

    return Response(
        stream_with_context(generate()),
        mimetype='application/x-ndjson',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Access-Control-Allow-Origin': '*',
        },
    )