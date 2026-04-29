# backend/strategy_api.py
from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from backend.engine_core import BacktestEngine, ENGINE_DEFAULTS, deep_merge
from backend.strategy_loader import get_strategy, list_strategy_metadata


strategy_api = Blueprint("strategy_api", __name__)
DATA_DIR = "data"


# ============================================================
# Datetime helper
# ============================================================
def _parse_datetime_param(val):
    """Convert ISO datetime string (from HTML datetime-local input) to Unix
    timestamp.  Treats naive datetimes as UTC.  Returns None if invalid."""
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


# ============================================================
# Data loading
# ============================================================
def load_bars(range_pt: int, bar_range: int, start_date=None, end_date=None):
    path = os.path.join(DATA_DIR, f"{int(range_pt)}pt.json")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Dataset not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        bars = json.load(f)

    # ── Date-range filtering (BEFORE normalization) ────────────
    start_ts = _parse_datetime_param(start_date)
    end_ts = _parse_datetime_param(end_date)
    if start_ts is not None or end_ts is not None:
        before_filter = len(bars)
        if start_ts is not None:
            bars = [b for b in bars if int(b["time"]) >= start_ts]
        if end_ts is not None:
            bars = [b for b in bars if int(b["time"]) <= end_ts]
        print(f"  [DATA] Date filter: {before_filter} → {len(bars)} bars"
              f"  (start={start_date}, end={end_date})")

    # ── Bar-count limit (applied AFTER date filter) ────────────
    if bar_range and int(bar_range) > 0:
        bars = bars[-int(bar_range):]

    normalized = []
    last_time = None

    for b in bars:
        t = int(b["time"])
        if last_time is not None and t <= last_time:
            t = last_time + 1
        last_time = t

        normalized.append(
            {
                "time": t,
                "open": float(b["open"]),
                "high": float(b["high"]),
                "low": float(b["low"]),
                "close": float(b["close"]),
                "volume": float(b.get("volume", 0) or 0),
            }
        )

    return normalized


# ============================================================
# Engine schema for frontend auto-generation later
# ============================================================
ENGINE_SCHEMA = {
    "_group_position": {
        "type": "group",
        "label": "Position Sizing",
    },
    "position_sizing.mode": {
        "type": "enum",
        "label": "Sizing Mode",
        "default": "fixed_lot",
        "options": ["fixed_lot", "percent_risk"],
        "group": "Position Sizing",
    },
    "position_sizing.fixed_lot": {
        "type": "number",
        "label": "Fixed Lot Size",
        "default": 1.0,
        "min": 0.01,
        "max": 1000,
        "step": 0.01,
        "group": "Position Sizing",
    },
    "position_sizing.risk_percent": {
        "type": "number",
        "label": "Risk % Per Trade",
        "default": 1.0,
        "min": 0.01,
        "max": 100.0,
        "step": 0.01,
        "group": "Position Sizing",
    },
    "_group_exits": {
        "type": "group",
        "label": "Universal Exits",
    },
    "exits.stop_loss.mode": {
        "type": "enum",
        "label": "Stop Loss Mode",
        "default": "strategy",
        "options": ["strategy", "fixed_points", "last_bar_extreme", "none"],
        "group": "Universal Exits",
    },
    "exits.stop_loss.value": {
        "type": "number",
        "label": "Fixed SL Points",
        "default": 0.0,
        "min": 0.0,
        "max": 1000.0,
        "step": 0.25,
        "group": "Universal Exits",
    },
    "exits.take_profit.mode": {
        "type": "enum",
        "label": "Take Profit Mode",
        "default": "strategy",
        "options": ["strategy", "fixed_points", "fixed_r", "none"],
        "group": "Universal Exits",
    },
    "exits.take_profit.value": {
        "type": "number",
        "label": "TP Value",
        "default": 0.0,
        "min": 0.0,
        "max": 1000.0,
        "step": 0.25,
        "group": "Universal Exits",
    },
    "_group_trailing": {
        "type": "group",
        "label": "Trailing / Break-even",
    },
    "exits.trailing.enabled": {
        "type": "boolean",
        "label": "Enable Trailing Stop",
        "default": False,
        "group": "Trailing / Break-even",
    },
    "exits.trailing.mode": {
        "type": "enum",
        "label": "Trailing Mode",
        "default": "points",
        "options": ["points", "ma"],
        "group": "Trailing / Break-even",
    },
    "exits.trailing.value": {
        "type": "number",
        "label": "Trail Points",
        "default": 0.0,
        "min": 0.0,
        "max": 1000.0,
        "step": 0.25,
        "group": "Trailing / Break-even",
    },
    "exits.trailing.ma_key": {
        "type": "string",
        "label": "Trailing MA Key",
        "default": "",
        "group": "Trailing / Break-even",
    },
    "exits.trailing.activate_after_r": {
        "type": "number",
        "label": "Activate Trailing After +R",
        "default": 0.0,
        "min": 0.0,
        "max": 100.0,
        "step": 0.25,
        "group": "Trailing / Break-even",
    },
    "exits.break_even.enabled": {
        "type": "boolean",
        "label": "Enable Break-even",
        "default": False,
        "group": "Trailing / Break-even",
    },
    "exits.break_even.trigger_r": {
        "type": "number",
        "label": "Break-even Trigger +R",
        "default": 1.0,
        "min": 0.0,
        "max": 100.0,
        "step": 0.25,
        "group": "Trailing / Break-even",
    },
    "_group_scaling": {
        "type": "group",
        "label": "Scaling",
    },
    "stacking.allow_stacking": {
        "type": "boolean",
        "label": "Allow Stacking / Adds",
        "default": False,
        "group": "Scaling",
    },
    "scaling_in.enabled": {
        "type": "boolean",
        "label": "Enable Scaling In",
        "default": False,
        "group": "Scaling",
    },
    "scaling_in.mode": {
        "type": "enum",
        "label": "Scale In Mode",
        "default": "bars",
        "options": ["bars", "r_multiple"],
        "group": "Scaling",
    },
    "scaling_in.every_bars": {
        "type": "number",
        "label": "Add Every X Bars",
        "default": 0,
        "min": 0,
        "max": 1000,
        "step": 1,
        "integer": True,
        "group": "Scaling",
    },
    "scaling_in.every_r": {
        "type": "number",
        "label": "Add Every +X R",
        "default": 0.0,
        "min": 0.0,
        "max": 100.0,
        "step": 0.25,
        "group": "Scaling",
    },
    "scaling_in.size_fraction": {
        "type": "number",
        "label": "Add Size Fraction",
        "default": 1.0,
        "min": 0.01,
        "max": 10.0,
        "step": 0.01,
        "group": "Scaling",
    },
    "scaling_in.max_adds": {
        "type": "number",
        "label": "Max Adds",
        "default": 0,
        "min": 0,
        "max": 20,
        "step": 1,
        "integer": True,
        "group": "Scaling",
    },
}


def strategy_detail_payload(strategy):
    strategy_meta = strategy.get_metadata()
    engine_defaults = deep_merge(ENGINE_DEFAULTS, strategy.engine_parameter_overrides(strategy.get_default_parameters()))
    return {
        **strategy_meta,
        "engine_schema": deepcopy(ENGINE_SCHEMA),
        "engine_defaults": engine_defaults,
    }


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

        if len(bars) < 10:
            return jsonify({"error": "Insufficient data"}), 400

        # Merge strategy-provided engine overrides into request engine config
        strategy_engine_overrides = strategy.engine_parameter_overrides(
            strategy.validate_parameters(payload.get("parameters", {}))
        )
        payload["engine"] = deep_merge(strategy_engine_overrides, payload.get("engine", {}))

        engine = BacktestEngine(
            bars=bars,
            strategy=strategy,
            payload=payload,
        )
        result = engine.run()

        return jsonify(result), 200

    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except KeyError as e:
        return jsonify({"error": str(e)}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500