# Repository Snapshot - Part 3 of 4

- Root folder: `/home/hurairahengg/Documents/Jinni Zero`
- you knwo my whole jinni grid systeM/ basically it is thereliek a kubernetes server setup what it does is basically a mother server with ui and bunch of lank state VMs. the vms run a speacial typa of renko style bars not normal timeframe u will get more context in the codes but yeha and we can uipload strategy codes though mother ui and it wiill run strategy mt5 report and ecetra ecetra. currently im done coding the strategy system but its not tested yet an have confrimed bugs. so firm i wil ldrop u my whole project codebases from my readme. understand each code its role and keep in ur context i will give u big promtps to update code later duinerstood
- Total files indexed: `23`
- Files in this chunk: `7`
## Full Project Tree

```text
.gitignore
backend/__init__.py
backend/dollar_math.py
backend/engine_core.py
backend/shared.py
backend/stats_engine.py
backend/strategies/__init__.py
backend/strategies/base.py
backend/strategies/JinniContinioum.py
backend/strategies/JinniScalperXzero.py
backend/strategies/legacyReplicator.py
backend/strategy_api.py
backend/strategy_loader.py
backtest_server.py
bars/range_bars.py
index.html
js/backtest.js
js/chart.js
js/currency.js
js/strategy_loader.js
STRATEGY_GUIDE.txt
styles.css
test.py
```

## Files In This Chunk - Part 3

```text
backend/dollar_math.py
backend/strategies/__init__.py
backend/strategies/base.py
backend/strategies/JinniContinioum.py
backend/strategy_api.py
bars/range_bars.py
js/chart.js
```

## File Contents


---

## FILE: `backend/dollar_math.py`

- Relative path: `backend/dollar_math.py`
- Absolute path at snapshot time: `/home/hurairahengg/Documents/Jinni Zero/backend/dollar_math.py`
- Size bytes: `4378`
- SHA256: `360d0d3010887d99a8a88cb197276999d2a0457c513ce83c873916f2994b725a`
- Guessed MIME type: `text/x-python`
- Guessed encoding: `unknown`

```python
"""
JINNI ZERO — Centralized Dollar Conversion
===========================================
This is the ONE AND ONLY place where points → dollars conversion happens.

backend/dollar_math.py

Used by:
  - backtest_server.py (legacy mode)
  - engine_core.py (strategy mode)
  - live engine (future)

Formula:
  dollars = points × lot_size × point_value

Where:
  points      = price movement (exit - entry, direction-adjusted)
  lot_size    = number of contracts/lots (default 1.0)
  point_value = dollar value per 1.0 point move per lot (default 1.0)

Examples:
  NQ Futures:  point_value = 20  → 1 point × 1 lot = $20
  ES Futures:  point_value = 50  → 1 point × 1 lot = $50
  Custom:      point_value = 1   → 1 point × 1 lot = $1 (default)

R-multiples are computed BEFORE dollar conversion and are
independent of lot_size / point_value.
"""
from __future__ import annotations
import math


def points_to_dollars(
    points: float,
    lot_size: float = 1.0,
    point_value: float = 1.0,
) -> float:
    """
    THE single conversion function.
    Every dollar calculation in the system MUST call this.
    """
    return points * lot_size * point_value


def finalize_trade_pnl(
    closed: dict,
    lot_size: float = 1.0,
    point_value: float = 1.0,
    commission: float = 0.0,
) -> None:
    """
    Compute ALL dollar + R fields on a closed trade dict (in-place).

    Order of operations:
      1. Points PnL (direction-aware)
      2. Risk in points (from SL)
      3. R-multiple (PURE — no dollars, no lot_size, no point_value)
      4. Dollar PnL (centralized conversion)
      5. Commission
      6. Net PnL
      7. MAE/MFE dollars

    This function is called by BOTH legacy and strategy engines.
    """
    d  = closed["direction"]
    ep = closed["entry_price"]
    xp = closed["exit_price"]

    # ── 1. Points PnL ────────────────────────────────────────────
    dir_sign = 1 if d == "long" else -1
    points_pnl = (xp - ep) * dir_sign

    # ── 2. Risk in points (from SL) ──────────────────────────────
    sl = closed.get("sl_level")
    rp = closed.get("risk_pts")
    if sl is not None:
        rp = abs(ep - sl)
    if rp is None or rp <= 0:
        rp = None

    # ── 3. R-multiple (PURE — independent of dollar settings) ────
    r_mult = None
    if rp is not None and rp > 0:
        r_mult = points_pnl / rp

    # ── 4. Dollar PnL (centralized) ──────────────────────────────
    gross_dollar = points_to_dollars(points_pnl, lot_size, point_value)

    # ── 5. Commission ────────────────────────────────────────────
    net_dollar = gross_dollar - commission

    # ── 6. Risk / MAE / MFE in dollars (same conversion) ────────
    risk_dollar = points_to_dollars(rp, lot_size, point_value) if rp and rp > 0 else None
    mae_dollar  = points_to_dollars(closed.get("mae", 0), lot_size, point_value)
    mfe_dollar  = points_to_dollars(closed.get("mfe", 0), lot_size, point_value)

    # ── Write all fields ─────────────────────────────────────────
    closed.update(
        points_pnl  = round(points_pnl, 4),
        gross_pnl   = round(gross_dollar, 2),
        commission  = round(commission, 2),
        net_pnl     = round(net_dollar, 2),
        net_pnl_r   = round(r_mult, 3) if r_mult is not None else None,
        risk_pts    = round(rp, 4) if rp is not None else None,
        risk_dollar = round(risk_dollar, 2) if risk_dollar else None,
        mae_dollar  = round(mae_dollar, 2),
        mfe_dollar  = round(mfe_dollar, 2),
    )


def validate_conversion(
    points: float,
    lot_size: float,
    point_value: float,
    expected_dollars: float,
) -> bool:
    """
    Validation helper. Use in tests to verify consistency.

    Example:
        validate_conversion(10.0, 1.0, 5.0, 50.0)  # True
        validate_conversion(10.0, 2.0, 20.0, 400.0) # True
    """
    actual = points_to_dollars(points, lot_size, point_value)
    return abs(actual - expected_dollars) < 0.01
```

---

## FILE: `backend/strategies/__init__.py`

- Relative path: `backend/strategies/__init__.py`
- Absolute path at snapshot time: `/home/hurairahengg/Documents/Jinni Zero/backend/strategies/__init__.py`
- Size bytes: `3687`
- SHA256: `a5f4cef0f5a9a6a204a5d47488f60dcf7bd569f68dd10e3fc85bbf37b064b649`
- Guessed MIME type: `text/x-python`
- Guessed encoding: `unknown`

```python
# backend/strategy_loader.py
from __future__ import annotations

import importlib
import inspect
import pkgutil
import sys
from typing import Dict, List

from backend.strategies.base import BaseStrategy
import backend.strategies as strategies_pkg


def _iter_strategy_module_names():
    """
    Iterate over python modules inside backend/strategies/
    while skipping base.py, __init__.py, and private files.
    """
    for mod in pkgutil.iter_modules(strategies_pkg.__path__):
        name = mod.name
        if name in {"base", "__init__"}:
            continue
        if name.startswith("_"):
            continue
        yield name


def _load_strategy_module(module_name: str):
    """
    Import (or reload) a strategy module.
    Reloading is helpful during development so edits are picked up.
    """
    full_name = f"backend.strategies.{module_name}"
    importlib.invalidate_caches()

    if full_name in sys.modules:
        return importlib.reload(sys.modules[full_name])

    return importlib.import_module(full_name)


def _extract_strategy_instances(module) -> List[BaseStrategy]:
    """
    Find all BaseStrategy subclasses inside a loaded module
    and return instantiated strategy objects.
    """
    found = []

    for _, obj in inspect.getmembers(module, inspect.isclass):
        # must be a real subclass of BaseStrategy, not BaseStrategy itself
        if not issubclass(obj, BaseStrategy):
            continue
        if obj is BaseStrategy:
            continue

        # avoid importing classes re-exported from other modules
        if obj.__module__ != module.__name__:
            continue

        instance = obj()

        if not getattr(instance, "strategy_id", ""):
            # fallback to module/class-derived id if missing
            instance.strategy_id = obj.__name__.replace("Strategy", "").lower()

        found.append(instance)

    return found


def discover_strategies() -> Dict[str, BaseStrategy]:
    """
    Discover all strategy plugins under backend/strategies/
    and return a registry: {strategy_id: strategy_instance}
    """
    registry: Dict[str, BaseStrategy] = {}

    for module_name in _iter_strategy_module_names():
        module = _load_strategy_module(module_name)
        instances = _extract_strategy_instances(module)

        for instance in instances:
            strategy_id = str(instance.strategy_id).strip()

            if not strategy_id:
                raise ValueError(
                    f"Strategy in module '{module_name}' is missing a valid strategy_id"
                )

            if strategy_id in registry:
                raise ValueError(
                    f"Duplicate strategy_id detected: '{strategy_id}' "
                    f"(module '{module_name}')"
                )

            registry[strategy_id] = instance

    return registry


def get_strategy(strategy_id: str) -> BaseStrategy:
    """
    Return one strategy instance by ID.
    """
    registry = discover_strategies()

    if strategy_id not in registry:
        available = ", ".join(sorted(registry.keys())) if registry else "(none found)"
        raise KeyError(
            f"Unknown strategy '{strategy_id}'. Available strategies: {available}"
        )

    return registry[strategy_id]


def list_strategy_metadata() -> List[dict]:
    """
    Return metadata for all discovered strategies.
    """
    registry = discover_strategies()

    out = []
    for strategy_id in sorted(registry.keys()):
        strategy = registry[strategy_id]
        meta = strategy.get_metadata()

        # ensure id is always consistent
        meta["id"] = strategy.strategy_id
        out.append(meta)

    return out
```

---

## FILE: `backend/strategies/base.py`

- Relative path: `backend/strategies/base.py`
- Absolute path at snapshot time: `/home/hurairahengg/Documents/Jinni Zero/backend/strategies/base.py`
- Size bytes: `5588`
- SHA256: `e04abfd566dbbea83ce03a86b623c88554bbe9ab208ac4a3f26a565d6c376847`
- Guessed MIME type: `text/x-python`
- Guessed encoding: `unknown`

```python
"""
JINNI ZERO — Strategy Base Class (Signal-Only Interface)
========================================================
Strategies are SIGNAL PROVIDERS ONLY.

They output:  BUY / SELL / HOLD / CLOSE + optional SL/TP
They do NOT:  sizing, PnL, equity, commission, spread, stats

The engine is the single source of truth for execution.
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


# ── Valid signal constants ────────────────────────────────────
SIGNAL_BUY   = "BUY"
SIGNAL_SELL  = "SELL"
SIGNAL_HOLD  = "HOLD"
SIGNAL_CLOSE = "CLOSE"

VALID_SIGNALS = {SIGNAL_BUY, SIGNAL_SELL, SIGNAL_HOLD, SIGNAL_CLOSE, None}


class BaseStrategy(ABC):
    """
    All strategies MUST subclass this.

    Lifecycle:
        1. Engine calls build_indicators(params) → precomputes indicator series
        2. Engine calls on_init(ctx)             → strategy initializes state
        3. For each bar: engine calls on_bar(ctx) → strategy returns signal
        4. Engine calls on_end(ctx)              → strategy cleanup
    """

    # ── Required metadata ─────────────────────────────────────
    strategy_id:   str = ""
    name:          str = ""
    description:   str = ""
    version:       str = "1.0"

    # ── Lookback: minimum bars before strategy can fire signals ─
    # Engine will pass HOLD for all bars before this index.
    # User can override to a higher value via payload.lookback_override.
    min_lookback:  int = 0

    # ==========================================================
    # METADATA
    # ==========================================================
    def get_metadata(self) -> Dict[str, Any]:
        return {
            "id":            self.strategy_id,
            "name":          self.name or self.strategy_id,
            "description":   self.description or "",
            "version":       self.version,
            "min_lookback":  self.min_lookback,
            "parameters":    self.get_parameter_schema(),
        }

    def get_parameter_schema(self) -> Dict[str, Any]:
        """Override to declare strategy-specific parameters for UI."""
        return getattr(self, "parameters", {})

    def get_default_parameters(self) -> Dict[str, Any]:
        schema = self.get_parameter_schema()
        defaults = {}
        for k, spec in schema.items():
            if isinstance(spec, dict) and "default" in spec:
                defaults[k] = spec["default"]
        return defaults

    def validate_parameters(self, raw_params: Dict[str, Any]) -> Dict[str, Any]:
        params = dict(self.get_default_parameters())
        for k, v in (raw_params or {}).items():
            params[k] = v
        return params

    # ==========================================================
    # INDICATORS (optional — engine precomputes these)
    # ==========================================================
    def build_indicators(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Return indicator specs for engine precomputation.
        Each spec: {"key": "hma_200", "kind": "HMA", "period": 200, "source": "close"}
        Strategy can also precompute its own in on_init() using ctx.bars.
        """
        return []

    # ==========================================================
    # LIFECYCLE HOOKS
    # ==========================================================
    def on_init(self, ctx: Any) -> None:
        """Called ONCE before first bar. Initialize ctx.state here."""
        pass

    def on_end(self, ctx: Any) -> None:
        """Called ONCE after last bar."""
        pass

    # ==========================================================
    # MAIN — SIGNAL GENERATION (the ONLY job of a strategy)
    # ==========================================================
    @abstractmethod
    def on_bar(self, ctx: Any) -> Optional[Dict[str, Any]]:
        """
        Called once per bar.

        ctx provides (READ-ONLY except ctx.state):
            ctx.index       — current bar index
            ctx.bar         — current OHLCV dict
            ctx.bars        — all bars (for lookback)
            ctx.indicators  — engine-precomputed indicator values at i
            ctx.ind_series  — full indicator series (for lookback)
            ctx.position    — PositionState (frozen dataclass, read-only)
            ctx.params      — strategy parameters
            ctx.state       — mutable dict, persists across bars
            ctx.trades      — closed trades list (read-only)
            ctx.equity      — current mark-to-market equity
            ctx.balance     — current realized balance

        MUST return one of:

        ENTRY:
            {"signal": "BUY",  "sl": float|None, "tp": float|None}
            {"signal": "SELL", "sl": float|None, "tp": float|None}

        HOLD (no action):
            {"signal": "HOLD"}  or  None

        CLOSE open position:
            {"signal": "CLOSE", "close_reason": "my_reason"}

        CLOSE + immediately signal new direction (flip):
            {"signal": "SELL", "close": True, "close_reason": "flip_short"}

        DYNAMIC SL/TP UPDATE (while in position):
            {"signal": "HOLD", "update_sl": float, "update_tp": float}

        ❌ NEVER return: size, entry_price, PnL, balance, equity, commission.
        """
        raise NotImplementedError("Strategy must implement on_bar()")
```

---

## FILE: `backend/strategies/JinniContinioum.py`

- Relative path: `backend/strategies/JinniContinioum.py`
- Absolute path at snapshot time: `/home/hurairahengg/Documents/Jinni Zero/backend/strategies/JinniContinioum.py`
- Size bytes: `9513`
- SHA256: `eff45f3a1c6c3043a39619123f98ea168f3575a3d20b7324f506413326783257`
- Guessed MIME type: `text/x-python`
- Guessed encoding: `unknown`

```python
"""
JINNI ZERO — Jinni Continuum
=============================
Pure price-action momentum continuation strategy.

ENTRY
  BUY:  N consecutive bull candles (close > open) → enter next bar open
  SELL: N consecutive bear candles (close < open) → enter next bar open

STOP LOSS
  BUY:  Last (Nth) bull candle's LOW
  SELL: Last (Nth) bear candle's HIGH
  (absolute SL — engine computes risk from fill price)

TAKE PROFIT
  R-multiple of risk (configurable, default 1R)
  (engine computes at fill time)

NO CANDLE REUSE (toggleable)
  When ON: candles used for signal confirmation AND candles during
  the trade CANNOT be reused for the next signal. After a trade
  closes, counting starts completely fresh.

  Example (confirm_bars=2):
    Bar 1: bull (count=1)
    Bar 2: bull (count=2) → BUY signal fires
    Bar 3: trade opens at open, TP hit → trade closes
    Bars 1, 2, 3 are ALL used → next count starts from bar 4

  When OFF: only the signal resets the counter. The exit bar
  itself CAN be counted toward the next signal.

No indicators required — pure candle structure.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.strategies.base import BaseStrategy


class JinniContinuum(BaseStrategy):
    # ── Metadata ───────────────────────────────────────────────
    strategy_id = "jinni_continuum"
    name = "Jinni Continuum"
    description = (
        "Momentum continuation: N consecutive bull/bear candles → "
        "entry at next bar open, SL at last candle's low/high, "
        "TP at configurable R-multiple. Optional candle reuse prevention."
    )
    version = "1.0.0"
    min_lookback = 0  # no indicators, pure price action

    # ==========================================================
    # PARAMETERS
    # ==========================================================
    parameters = {
        "confirm_bars": {
            "type": "number",
            "label": "Confirmation Bars",
            "default": 2,
            "min": 1,
            "max": 10,
            "step": 1,
            "help": "Consecutive same-direction candles needed before entry.",
        },
        "r_multiple": {
            "type": "number",
            "label": "R Multiple (TP)",
            "default": 1.0,
            "min": 0.5,
            "max": 20,
            "step": 0.5,
            "help": "Take profit as multiple of risk distance.",
        },
        "no_reuse": {
            "type": "boolean",
            "label": "No Candle Reuse",
            "default": True,
            "help": (
                "When ON, all candles used for signal + trade are consumed. "
                "Must wait for completely fresh candles after trade closes."
            ),
        },
    }

    # ==========================================================
    # INDICATORS — none needed
    # ==========================================================
    def build_indicators(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        return []

    # ==========================================================
    # INIT
    # ==========================================================
    def on_init(self, ctx: Any) -> None:
        s = ctx.state
        s["bull_count"] = 0
        s["bear_count"] = 0
        s["last_used_bar"] = -1        # last bar index consumed by signal/trade
        s["_last_trade_count"] = 0     # for detecting new trade closes

    # ==========================================================
    # ON BAR
    # ==========================================================
    def on_bar(self, ctx: Any) -> Optional[Dict[str, Any]]:
        s = ctx.state
        p = ctx.params
        bar = ctx.bar
        i = ctx.index

        c = float(bar["close"])
        o = float(bar["open"])
        h = float(bar["high"])
        l = float(bar["low"])

        bull = c > o
        bear = c < o

        confirm_bars = int(p.get("confirm_bars", 2))
        r_multiple = float(p.get("r_multiple", 1.0))
        no_reuse = bool(p.get("no_reuse", True))

        # ══════════════════════════════════════════════════════
        # UPDATE LAST USED BAR FROM CLOSED TRADES
        #
        # If a trade closed since last check, mark the exit bar
        # as the last used bar (prevents reuse of exit bar and
        # all bars before it that were part of the trade).
        # ══════════════════════════════════════════════════════
        if no_reuse:
            trades = ctx.trades
            last_count = s.get("_last_trade_count", 0)
            if len(trades) > last_count:
                last_trade = trades[-1]
                exit_bar = last_trade.get("exit_bar", i)
                s["last_used_bar"] = exit_bar
                # Reset counters — fresh start after trade
                s["bull_count"] = 0
                s["bear_count"] = 0
            s["_last_trade_count"] = len(trades)

        # ══════════════════════════════════════════════════════
        # IN POSITION → HOLD
        # Engine handles SL/TP exits.
        # ══════════════════════════════════════════════════════
        if ctx.position.has_position:
            return {"signal": "HOLD"}

        # ══════════════════════════════════════════════════════
        # CANDLE REUSE CHECK
        #
        # If no_reuse is ON and this bar is at or before the
        # last used bar, skip it entirely.
        # ══════════════════════════════════════════════════════
        if no_reuse and i <= s.get("last_used_bar", -1):
            s["bull_count"] = 0
            s["bear_count"] = 0
            return None

        # ══════════════════════════════════════════════════════
        # COUNT CONSECUTIVE CANDLES
        #
        # Bull: close > open → increment bull, reset bear
        # Bear: close < open → increment bear, reset bull
        # Doji: close == open → reset both (not directional)
        # ══════════════════════════════════════════════════════
        if bull:
            s["bull_count"] = s.get("bull_count", 0) + 1
            s["bear_count"] = 0
        elif bear:
            s["bear_count"] = s.get("bear_count", 0) + 1
            s["bull_count"] = 0
        else:
            # Doji — breaks the streak
            s["bull_count"] = 0
            s["bear_count"] = 0
            return None

        # ══════════════════════════════════════════════════════
        # CHECK SIGNAL
        # ══════════════════════════════════════════════════════
        sig = None
        sl_price = None

        # ── BUY: N consecutive bull candles ───────────────────
        if s["bull_count"] >= confirm_bars:
            sig = "BUY"
            sl_price = l  # last bull candle's low

            # Reset counters
            s["bull_count"] = 0
            s["bear_count"] = 0

            # Mark signal bar as used
            if no_reuse:
                s["last_used_bar"] = i

        # ── SELL: N consecutive bear candles ──────────────────
        elif s["bear_count"] >= confirm_bars:
            sig = "SELL"
            sl_price = h  # last bear candle's high

            # Reset counters
            s["bull_count"] = 0
            s["bear_count"] = 0

            # Mark signal bar as used
            if no_reuse:
                s["last_used_bar"] = i

        if sig is None:
            return None

        # ══════════════════════════════════════════════════════
        # BUILD SIGNAL
        #
        # SL: absolute price (candle low/high)
        # TP: engine-computed R-multiple at fill time
        #
        # Engine flow at next bar open:
        #   1. entry_price = next bar open
        #   2. risk = abs(entry_price - sl_price)
        #   3. tp = entry_price + risk * R  (long)
        #        or entry_price - risk * R  (short)
        #   4. spread applied to all three
        # ══════════════════════════════════════════════════════
        return {
            "signal": sig,
            "sl": sl_price,
            "tp_mode": "r_multiple",
            "tp_r": r_multiple,
        }
```

---

## FILE: `backend/strategy_api.py`

- Relative path: `backend/strategy_api.py`
- Absolute path at snapshot time: `/home/hurairahengg/Documents/Jinni Zero/backend/strategy_api.py`
- Size bytes: `5446`
- SHA256: `55a72f827a2079ea9493e1c8c0ead54ef1e6fc3cb56013f67b259def66dc0661`
- Guessed MIME type: `text/x-python`
- Guessed encoding: `unknown`

```python
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
```

---

## FILE: `bars/range_bars.py`

- Relative path: `bars/range_bars.py`
- Absolute path at snapshot time: `/home/hurairahengg/Documents/Jinni Zero/bars/range_bars.py`
- Size bytes: `17177`
- SHA256: `ff5d73fcc849280ea889ede621c93bad886c6a6c52e5721af8d576434f062557`
- Guessed MIME type: `text/x-python`
- Guessed encoding: `unknown`

```python
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
```

---

## FILE: `js/chart.js`

- Relative path: `js/chart.js`
- Absolute path at snapshot time: `/home/hurairahengg/Documents/Jinni Zero/js/chart.js`
- Size bytes: `86374`
- SHA256: `2827771dac829cef53a688c8a1bda0d8a4f2db31337a026381dbd1c934dd95a7`
- Guessed MIME type: `text/javascript`
- Guessed encoding: `unknown`

```javascript
/* ═══════════════════════════════════════════════════════════════════
   chart.js — JINNI ZERO · NQ Range-Bar Chart + Indicators + Signals
   Lightweight Charts v4.2

   v2 — Full recode of windowing/loading system:
   - Immediate first render (no blank screen)
   - Stable scroll loading (no jumping, no loops)
   - All markers on candleSeries (guaranteed visible)
   - _isShifting lock prevents feedback loops
   - Clean module separation
═══════════════════════════════════════════════════════════════════ */
(function () {

/* ──────────────────────────────────────────────────────────────────
   DATA SOURCES
────────────────────────────────────────────────────────────────── */
const RANGE_FILES = {
  2:'data/2pt.json',4:'data/4pt.json',6:'data/6pt.json',8:'data/8pt.json',
  10:'data/10pt.json',15:'data/15pt.json',20:'data/20pt.json',25:'data/25pt.json',
  30:'data/30pt.json',35:'data/35pt.json',40:'data/40pt.json',45:'data/45pt.json',
  50:'data/50pt.json',
};

/* ──────────────────────────────────────────────────────────────────
   CONFIG
────────────────────────────────────────────────────────────────── */
const INITIAL_WINDOW_BARS = 2000;
const BUFFER_BARS = 800;
const TRIGGER_THRESHOLD = 200;
const SHIFT_DEBOUNCE_MS = 120;
const INDICATOR_RENDER_DEBOUNCE_MS = 35;
const OSC_PANE_HEIGHT = 142;
const PRICE_SCALE_MIN_WIDTH = 62;

const INDICATOR_CATALOG = {
  EMA:  { defaults: { length: 55, color: '#00e5ff', source: 'close' } },
  HMA:  { defaults: { length: 55, color: '#ff9800', source: 'close' } },
  SMA:  { defaults: { length: 50, color: '#00e5ff', source: 'close' } },
  WMA:  { defaults: { length: 50, color: '#8bc34a', source: 'close' } },
  BB:   { defaults: { length: 20, stddev: 2, color: '#66bbff', source: 'close' } },
  RSI:  { defaults: { length: 14, color: '#ffd166', source: 'close',
    obLevel: 70, osLevel: 30, midLevel: 50, showMid: true,
    obColor: '#ff3d5a', osColor: '#00e676', midColor: '#4a6070' } },
  'Stoch RSI': { defaults: { length: 14, smoothK: 3, smoothD: 3, color: '#8bc34a',
    source: 'close', obLevel: 80, osLevel: 20, obColor: '#ff3d5a', osColor: '#00e676' } },
};
const INDICATOR_TYPES = Object.keys(INDICATOR_CATALOG);
const PRICE_SOURCES = ['close', 'open', 'high', 'low'];

const DEFAULT_INDICATORS = [
  { type: 'HMA', length: 55, color: '#00e5ff', source: 'close', visible: true },
  { type: 'EMA', length: 55, color: '#ff9800', source: 'close', visible: true },
  { type: 'EMA', length: 200, color: '#e040fb', source: 'close', visible: true },
];

/* ──────────────────────────────────────────────────────────────────
   ROOT / LAYOUT
────────────────────────────────────────────────────────────────── */
const rootContainer = document.getElementById('chartContainer');
rootContainer.innerHTML = '';
rootContainer.style.position = 'relative';
rootContainer.style.overflow = 'hidden';
rootContainer.style.minWidth = '0';
rootContainer.style.minHeight = '0';

const chartsStack = document.createElement('div');
Object.assign(chartsStack.style, {
  position:'absolute',inset:'0',display:'flex',flexDirection:'column',
  minWidth:'0',minHeight:'0',pointerEvents:'none',
});
rootContainer.appendChild(chartsStack);

const mainChartHost = document.createElement('div');
Object.assign(mainChartHost.style, {
  position:'relative',flex:'1 1 auto',minWidth:'0',minHeight:'0',pointerEvents:'auto',
});
chartsStack.appendChild(mainChartHost);

const oscillatorWrap = document.createElement('div');
Object.assign(oscillatorWrap.style, {
  display:'none',flexDirection:'column',gap:'6px',paddingBottom:'6px',
  minHeight:'0',pointerEvents:'auto',
});
chartsStack.appendChild(oscillatorWrap);

const overlayUi = document.createElement('div');
Object.assign(overlayUi.style, {
  position:'absolute',top:'10px',left:'10px',zIndex:'30',display:'flex',
  flexDirection:'column',gap:'8px',pointerEvents:'auto',maxWidth:'620px',
});
rootContainer.appendChild(overlayUi);

const syncGuide = document.createElement('div');
Object.assign(syncGuide.style, {
  position:'absolute',top:'0',bottom:'0',width:'1px',
  background:'linear-gradient(to bottom,rgba(0,149,168,0),rgba(0,229,255,0.65),rgba(0,149,168,0))',
  pointerEvents:'none',zIndex:'12',display:'none',
  boxShadow:'0 0 10px rgba(0,229,255,0.22)',
});
rootContainer.appendChild(syncGuide);

/* ──────────────────────────────────────────────────────────────────
   MAIN CHART
────────────────────────────────────────────────────────────────── */
function createBaseChartOptions() {
  return {
    layout: { background:{type:'solid',color:'transparent'}, textColor:'#4a6070',
      fontFamily:"'Space Mono', monospace", fontSize:10 },
    grid: { vertLines:{visible:false}, horzLines:{color:'#1e2a38',style:1} },
    crosshair: { mode:0,
      vertLine:{color:'#0095a855',labelBackgroundColor:'#0095a8'},
      horzLine:{color:'#0095a855',labelBackgroundColor:'#0095a8'} },
    rightPriceScale: { borderColor:'#1e2a38',visible:true,minimumWidth:PRICE_SCALE_MIN_WIDTH,
      scaleMargins:{top:0.08,bottom:0.18} },
    timeScale: { borderColor:'#1e2a38',timeVisible:true,secondsVisible:true,
      barSpacing:6,minBarSpacing:2,fixLeftEdge:false,fixRightEdge:false,rightOffset:0 },
    handleScroll:true, handleScale:true,
  };
}

const mainChart = LightweightCharts.createChart(mainChartHost, createBaseChartOptions());

const candleSeries = mainChart.addCandlestickSeries({
  upColor:'#00e676',downColor:'#ff3d5a',borderUpColor:'#00e676',
  borderDownColor:'#ff3d5a',wickUpColor:'#00e67688',wickDownColor:'#ff3d5a88',
});

const volumeSeries = mainChart.addHistogramSeries({
  priceFormat:{type:'volume'},priceScaleId:'vol',
  scaleMargins:{top:0.88,bottom:0},
});

mainChart.priceScale('vol').applyOptions({
  borderColor:'#1e2a38',minimumWidth:PRICE_SCALE_MIN_WIDTH,
  scaleMargins:{top:0.88,bottom:0},visible:true,
});

/* ──────────────────────────────────────────────────────────────────
   STATE
────────────────────────────────────────────────────────────────── */
let currentRange = 2;
let fullData = [];
let datasetVersion = 0;

let loadedWindow = { start: 0, end: 0 };
let loadedData = [];
let lastVisibleRange = null;

let sourceCache = { close:[], open:[], high:[], low:[] };

// ── Anti-loop / shifting lock ────────────────────────────────────
let _isShifting = false;
let _shiftQueued = null;
let _shiftTimer = null;

let ignoreTimeSync = false;
let indicatorRenderTimer = null;

let nextIndicatorId = 1;
let indicators = [];
let signalEnabled = true;
let signalIndicatorId = null;

const indicatorSeriesRegistry = new Map();
const indicatorWindowCache = new Map();
let lastRenderedIndicatorRawValues = new Map();
let lastRenderedComputed = new Map();

// ── Markers (all on candleSeries) ────────────────────────────────
let _signalMarkers = [];
let _btEntryMarkers = [];
let _btExitMarkers = [];
let fullBacktestTrades = [];

const paneState = { rsi: null, stoch: null };
let selectedIndicatorId = null;

/* ──────────────────────────────────────────────────────────────────
   HELPERS
────────────────────────────────────────────────────────────────── */
function clamp(v, min, max) { return Math.max(min, Math.min(max, v)); }
function withAlpha(hex, alpha) {
  if (typeof hex !== 'string' || !hex.startsWith('#') || hex.length !== 7) return hex;
  return hex + clamp(Math.round(alpha*255),0,255).toString(16).padStart(2,'0');
}
function defaultForType(type) {
  return JSON.parse(JSON.stringify((INDICATOR_CATALOG[type]||INDICATOR_CATALOG.EMA).defaults));
}
function formatIndicatorLabel(ind) {
  if (ind.type === 'BB') return 'BB ' + ind.length + ',' + ind.stddev;
  if (ind.type === 'Stoch RSI') return 'Stoch RSI ' + ind.length + ',' + ind.smoothK + ',' + ind.smoothD;
  return ind.type + ' ' + ind.length;
}
function sourceArrayFor(source) { return sourceCache[source] || sourceCache.close || []; }
function indicatorWarmup(ind) {
  var len = Math.max(1, Number(ind.length) || 1);
  if (ind.type === 'Stoch RSI') return len * 8 + 20;
  if (ind.type === 'RSI') return len * 5 + 10;
  if (ind.type === 'BB') return len * 4 + 10;
  return len * 4 + 10;
}
function invalidateIndicatorCache() { indicatorWindowCache.clear(); }

function normalizeBars(rawBars) {
  var out = [], lastTime = null;
  for (var i = 0; i < rawBars.length; i++) {
    var b = rawBars[i];
    var time = Number(b.time);
    if (!Number.isFinite(time)) continue;
    if (lastTime != null && time <= lastTime) time = lastTime + 1;
    lastTime = time;
    out.push({ time:time, open:Number(b.open), high:Number(b.high),
      low:Number(b.low), close:Number(b.close), volume:Number(b.volume||0) });
  }
  return out;
}

function rebuildSourceCache() {
  sourceCache = {
    close: fullData.map(function(b){return b.close}),
    open:  fullData.map(function(b){return b.open}),
    high:  fullData.map(function(b){return b.high}),
    low:   fullData.map(function(b){return b.low}),
  };
}

function binarySearchAtOrAfter(time) {
  if (!fullData.length) return 0;
  var lo=0, hi=fullData.length-1, ans=fullData.length-1;
  while (lo <= hi) {
    var mid = (lo+hi)>>1;
    if (fullData[mid].time >= time) { ans=mid; hi=mid-1; }
    else lo=mid+1;
  }
  return ans;
}

function binarySearchAtOrBefore(time) {
  if (!fullData.length) return 0;
  var lo=0, hi=fullData.length-1, ans=0;
  while (lo <= hi) {
    var mid = (lo+hi)>>1;
    if (fullData[mid].time <= time) { ans=mid; lo=mid+1; }
    else hi=mid-1;
  }
  return ans;
}

function visibleIndexRange(range) {
  if (!range || range.from == null || range.to == null || !fullData.length) return null;
  return { fromIdx: binarySearchAtOrAfter(range.from), toIdx: binarySearchAtOrBefore(range.to) };
}

function volumeDataForLoadedWindow() {
  return loadedData.map(function(b) {
    return { time:b.time, value:b.volume||0, color: b.close>=b.open ? '#00e67633' : '#ff3d5a33' };
  });
}

function updateSidebar(bar) {
  if (!bar) return;
  document.getElementById('statOpen').textContent = bar.open.toFixed(2);
  document.getElementById('statHigh').textContent = bar.high.toFixed(2);
  document.getElementById('statLow').textContent = bar.low.toFixed(2);
  document.getElementById('statClose').textContent = bar.close.toFixed(2);
  document.getElementById('statVolume').textContent = bar.volume ? bar.volume.toFixed(0) : '—';
  var chg = bar.close - bar.open;
  var el = document.getElementById('statChange');
  el.textContent = (chg>=0?'+':'') + chg.toFixed(2);
  el.className = 'sidebar-value ' + (chg>=0?'bull':'bear');
}

function updateHeader(bar, prev) {
  if (!bar) return;
  document.getElementById('tickerPrice').textContent = bar.close.toFixed(2);
  var el = document.getElementById('tickerChange');
  if (prev) {
    var d = bar.close - prev.close;
    var pct = prev.close ? (d/prev.close*100) : 0;
    el.textContent = (d>=0?'+':'') + d.toFixed(2) + ' (' + pct.toFixed(2) + '%)';
    el.className = 'ticker-change ' + (d>=0?'bull':'bear');
  } else { el.textContent = '—'; el.className = 'ticker-change'; }
}

function safeSetVisibleRange(chart, range) {
  if (!range || range.from == null || range.to == null) return;
  try { chart.timeScale().setVisibleRange({from:range.from, to:range.to}); } catch(e) {}
}

function getHostSize(el, fallbackH) {
  var rect = el.getBoundingClientRect();
  return {
    width: Math.max(50, Math.floor(el.clientWidth || rect.width || rootContainer.clientWidth || 600)),
    height: Math.max(50, Math.floor(el.clientHeight || rect.height || fallbackH || 200)),
  };
}

function resizeMainChart() {
  var s = getHostSize(mainChartHost, Math.max(220, rootContainer.clientHeight - 20));
  mainChart.applyOptions({width:s.width, height:s.height});
}

function resizePaneCharts() {
  if (paneState.rsi && paneState.rsi.chart) {
    var s = getHostSize(paneState.rsi.host, OSC_PANE_HEIGHT);
    paneState.rsi.chart.applyOptions({width:s.width, height:s.height});
  }
  if (paneState.stoch && paneState.stoch.chart) {
    var s2 = getHostSize(paneState.stoch.host, OSC_PANE_HEIGHT);
    paneState.stoch.chart.applyOptions({width:s2.width, height:s2.height});
  }
}

function resizeAllCharts() { resizeMainChart(); resizePaneCharts(); }

function syncPanesFromMain(range) {
  if (!range || range.from == null || range.to == null || ignoreTimeSync) return;
  ignoreTimeSync = true;
  try {
    if (paneState.rsi && paneState.rsi.chart) safeSetVisibleRange(paneState.rsi.chart, range);
    if (paneState.stoch && paneState.stoch.chart) safeSetVisibleRange(paneState.stoch.chart, range);
  } finally {
    requestAnimationFrame(function(){ ignoreTimeSync = false; });
  }
}

/* ──────────────────────────────────────────────────────────────────
   MARKER SYSTEM (all markers on candleSeries — guaranteed visible)
────────────────────────────────────────────────────────────────── */
function refreshAllMarkers() {
  if (!loadedData.length) { candleSeries.setMarkers([]); return; }
  var fromT = loadedData[0].time;
  var toT = loadedData[loadedData.length-1].time;

  function inWindow(m) { return m.time >= fromT && m.time <= toT; }

  var all = [];
  // Signal markers
  for (var i = 0; i < _signalMarkers.length; i++) {
    if (inWindow(_signalMarkers[i])) all.push(_signalMarkers[i]);
  }
  // Backtest entry markers
  for (var j = 0; j < _btEntryMarkers.length; j++) {
    if (inWindow(_btEntryMarkers[j])) all.push(_btEntryMarkers[j]);
  }
  // Backtest exit markers
  for (var k = 0; k < _btExitMarkers.length; k++) {
    if (inWindow(_btExitMarkers[k])) all.push(_btExitMarkers[k]);
  }

  all.sort(function(a,b){ return a.time - b.time; });
  candleSeries.setMarkers(all);
}

function snapTimeToDataset(time) {
  if (!fullData.length) return time;
  var idx = binarySearchAtOrAfter(time);
  if (idx > 0) {
    var dCur = Math.abs(fullData[idx].time - time);
    var dPrev = Math.abs(fullData[idx-1].time - time);
    if (dPrev < dCur) return fullData[idx-1].time;
  }
  return fullData[idx] ? fullData[idx].time : fullData[fullData.length-1].time;
}

function exitColor(reason) {
  if (!reason) return '#ffffff';
  var r = String(reason).toUpperCase();
  if (r === 'TP_R' || r.indexOf('TP') >= 0) return '#00e676';
  if (r === 'SL_HIT' || r.indexOf('SL') >= 0) return '#ff3d5a';
  if (r.indexOf('MA') >= 0) return '#ffab00';
  if (r === 'END_OF_DATA') return '#ffffff';
  return '#aaaaaa';
}

function rebuildBacktestMarkerCache(trades) {
  fullBacktestTrades = Array.isArray(trades) ? trades.slice() : [];
  _btEntryMarkers = [];
  _btExitMarkers = [];
  if (!fullBacktestTrades.length || !fullData.length) return;

  var seenE = {}, seenX = {};
  for (var i = 0; i < fullBacktestTrades.length; i++) {
    var t = fullBacktestTrades[i];
    var isLong = t.direction === 'long';
    var entryTime = snapTimeToDataset(t.entry_time);
    var ek = 'e_' + entryTime + '_' + (t.id || t.position_id || i);
    if (!seenE[ek]) {
      seenE[ek] = true;
      _btEntryMarkers.push({
        time: entryTime,
        position: isLong ? 'belowBar' : 'aboveBar',
        color: isLong ? '#00e676' : '#ff3d5a',
        shape: isLong ? 'arrowUp' : 'arrowDown',
        text: isLong ? 'BUY' : 'SELL',
        size: 1,
      });
    }
    if (t.exit_time != null) {
      var exitTime = snapTimeToDataset(t.exit_time);
      if (exitTime === entryTime) {
        var idx = binarySearchAtOrAfter(entryTime);
        if (idx + 1 < fullData.length) exitTime = fullData[idx+1].time;
      }
      var xk = 'x_' + exitTime + '_' + (t.id || t.position_id || i);
      if (!seenX[xk]) {
        seenX[xk] = true;
        _btExitMarkers.push({
          time: exitTime,
          position: isLong ? 'aboveBar' : 'belowBar',
          color: exitColor(t.exit_reason),
          shape: 'circle',
          text: (t.exit_reason || 'EXIT').toUpperCase(),
          size: 1,
        });
      }
    }
  }
  _btEntryMarkers.sort(function(a,b){return a.time-b.time});
  _btExitMarkers.sort(function(a,b){return a.time-b.time});
}

window.plotBacktestMarkers = function(trades) {
  _signalMarkers = [];
  rebuildBacktestMarkerCache(trades);
  refreshAllMarkers();
};

window.clearBacktestMarkers = function() {
  fullBacktestTrades = [];
  _btEntryMarkers = [];
  _btExitMarkers = [];
  refreshAllMarkers();
};

/* ──────────────────────────────────────────────────────────────────
   WINDOW MANAGER (CORE FIX — anti-loop, stable viewport)
────────────────────────────────────────────────────────────────── */
function applyLoadedWindow() {
  loadedData = fullData.slice(loadedWindow.start, loadedWindow.end);
  candleSeries.setData(loadedData);
  volumeSeries.setData(volumeDataForLoadedWindow());
  refreshAllMarkers();
  scheduleIndicatorRender();

  var last = loadedData[loadedData.length-1] || fullData[fullData.length-1];
  updateSidebar(last);
  var candlesEl = document.getElementById('statCandles');
  if (candlesEl) candlesEl.textContent = fullData.length.toLocaleString();
}

function shiftWindow(newStart, newEnd, preserveRange) {
  if (_isShifting) {
    _shiftQueued = { start:newStart, end:newEnd, range:preserveRange };
    return;
  }

  if (newStart === loadedWindow.start && newEnd === loadedWindow.end) return;

  _isShifting = true;

  loadedWindow = { start: Math.max(0,newStart), end: Math.min(fullData.length, newEnd) };
  applyLoadedWindow();

  if (preserveRange) {
    safeSetVisibleRange(mainChart, preserveRange);
    syncPanesFromMain(preserveRange);
  }

  // Hold lock for 2 frames so chart settles before allowing new shifts
  requestAnimationFrame(function() {
    requestAnimationFrame(function() {
      _isShifting = false;
      if (_shiftQueued) {
        var q = _shiftQueued;
        _shiftQueued = null;
        shiftWindow(q.start, q.end, q.range);
      }
    });
  });
}

function checkWindowExpansion(range) {
  if (_isShifting || !range || !fullData.length) return;

  var vis = visibleIndexRange(range);
  if (!vis) return;

  var leftDist  = vis.fromIdx - loadedWindow.start;
  var rightDist = loadedWindow.end - vis.toIdx - 1;

  var needLeft  = leftDist < TRIGGER_THRESHOLD && loadedWindow.start > 0;
  var needRight = rightDist < TRIGGER_THRESHOLD && loadedWindow.end < fullData.length;

  if (!needLeft && !needRight) return;

  // Compute new window centered on visible range with buffer
  var newStart = Math.max(0, vis.fromIdx - BUFFER_BARS);
  var newEnd   = Math.min(fullData.length, vis.toIdx + 1 + BUFFER_BARS);

  // Ensure visible range is fully contained
  newStart = Math.min(newStart, vis.fromIdx);
  newEnd   = Math.max(newEnd, vis.toIdx + 1);

  if (newStart === loadedWindow.start && newEnd === loadedWindow.end) return;

  shiftWindow(newStart, newEnd, range);
}

function handleMainVisibleRangeChange(range) {
  if (_isShifting) return;
  if (!range || range.from == null || range.to == null || !fullData.length) return;

  lastVisibleRange = range;
  syncPanesFromMain(range);

  // Update oscillator readouts
  updateOscillatorReadoutsAtTime(range.to);

  // Debounced window expansion check
  if (_shiftTimer) clearTimeout(_shiftTimer);
  _shiftTimer = setTimeout(function() {
    _shiftTimer = null;
    checkWindowExpansion(lastVisibleRange);
  }, SHIFT_DEBOUNCE_MS);
}

mainChart.timeScale().subscribeVisibleTimeRangeChange(handleMainVisibleRangeChange);

/* ──────────────────────────────────────────────────────────────────
   INDICATOR MATH (O(n) precomputation — unchanged)
────────────────────────────────────────────────────────────────── */
function precomputeSma(values, period) {
  var n=values.length, out=new Array(n).fill(null);
  if (period<1||n<period) return out;
  var sum=0;
  for (var i=0;i<n;i++) {
    sum+=values[i];
    if (i>=period) sum-=values[i-period];
    if (i>=period-1) out[i]=sum/period;
  }
  return out;
}

function precomputeEma(values, period) {
  var n=values.length, out=new Array(n).fill(null);
  if (period<1||n<period) return out;
  var k=2/(period+1), seed=0;
  for (var i=0;i<period;i++) seed+=values[i];
  var ema=seed/period; out[period-1]=ema;
  for (var j=period;j<n;j++) { ema=values[j]*k+ema*(1-k); out[j]=ema; }
  return out;
}

function precomputeWma(values, period) {
  var n=values.length, out=new Array(n).fill(null);
  if (period<1||n<period) return out;
  var p=period, denom=p*(p+1)/2, sum=0, ws=0;
  for (var i=0;i<p;i++) { sum+=values[i]; ws+=values[i]*(i+1); }
  out[p-1]=ws/denom;
  for (var j=p;j<n;j++) { ws=ws+p*values[j]-sum; sum=sum+values[j]-values[j-p]; out[j]=ws/denom; }
  return out;
}

function precomputeHma(values, period) {
  var n=values.length, out=new Array(n).fill(null);
  var p=Math.max(2,period|0), half=Math.max(1,Math.floor(p/2)), sq=Math.max(1,Math.floor(Math.sqrt(p)));
  var full=precomputeWma(values,p), halfW=precomputeWma(values,half);
  var diff=new Array(n).fill(null), fv=-1;
  for (var i=0;i<n;i++) {
    if (full[i]!=null&&halfW[i]!=null) { diff[i]=2*halfW[i]-full[i]; if (fv===-1) fv=i; }
  }
  if (fv===-1) return out;
  var compact=[], map=[];
  for (var j=fv;j<n;j++) { if (diff[j]!=null) { compact.push(diff[j]); map.push(j); } }
  if (compact.length<sq) return out;
  var final=precomputeWma(compact,sq);
  for (var k=0;k<final.length;k++) { if (final[k]!=null) out[map[k]]=final[k]; }
  return out;
}

function precomputeMa(values, type, period) {
  var t=String(type).toUpperCase();
  if (t==='SMA') return precomputeSma(values,period);
  if (t==='EMA') return precomputeEma(values,period);
  if (t==='WMA') return precomputeWma(values,period);
  if (t==='HMA') return precomputeHma(values,period);
  return new Array(values.length).fill(null);
}

function precomputeBollinger(values, period, stddevMult) {
  var n=values.length;
  var basis=new Array(n).fill(null), upper=new Array(n).fill(null), lower=new Array(n).fill(null);
  if (period<1||n<period) return {basis:basis,upper:upper,lower:lower};
  var sum=0, sumSq=0;
  for (var i=0;i<n;i++) {
    var v=values[i]; sum+=v; sumSq+=v*v;
    if (i>=period) { var old=values[i-period]; sum-=old; sumSq-=old*old; }
    if (i>=period-1) {
      var mean=sum/period, variance=Math.max(0,(sumSq/period)-(mean*mean)), sd=Math.sqrt(variance);
      basis[i]=mean; upper[i]=mean+sd*stddevMult; lower[i]=mean-sd*stddevMult;
    }
  }
  return {basis:basis,upper:upper,lower:lower};
}

function precomputeRsi(values, period) {
  var n=values.length, out=new Array(n).fill(null);
  if (period<1||n<period+1) return out;
  var gains=0, losses=0;
  for (var i=1;i<=period;i++) { var d=values[i]-values[i-1]; if (d>=0) gains+=d; else losses-=d; }
  var ag=gains/period, al=losses/period;
  out[period]=al===0?100:(100-(100/(1+ag/al)));
  for (var j=period+1;j<n;j++) {
    var delta=values[j]-values[j-1], gain=delta>0?delta:0, loss=delta<0?-delta:0;
    ag=((ag*(period-1))+gain)/period; al=((al*(period-1))+loss)/period;
    out[j]=al===0?100:(100-(100/(1+ag/al)));
  }
  return out;
}

function rollingMin(values, period) {
  var out=new Array(values.length).fill(null), dq=[];
  for (var i=0;i<values.length;i++) {
    while (dq.length&&dq[0]<=i-period) dq.shift();
    while (dq.length) { var prev=values[dq[dq.length-1]]; if (prev==null||(values[i]!=null&&prev>=values[i])) dq.pop(); else break; }
    if (values[i]!=null) dq.push(i);
    if (i>=period-1&&dq.length) out[i]=values[dq[0]];
  }
  return out;
}

function rollingMax(values, period) {
  var out=new Array(values.length).fill(null), dq=[];
  for (var i=0;i<values.length;i++) {
    while (dq.length&&dq[0]<=i-period) dq.shift();
    while (dq.length) { var prev=values[dq[dq.length-1]]; if (prev==null||(values[i]!=null&&prev<=values[i])) dq.pop(); else break; }
    if (values[i]!=null) dq.push(i);
    if (i>=period-1&&dq.length) out[i]=values[dq[0]];
  }
  return out;
}

function precomputeStochRsi(values, rsiLength, smoothK, smoothD) {
  var rsi=precomputeRsi(values,rsiLength);
  var low=rollingMin(rsi,rsiLength), high=rollingMax(rsi,rsiLength);
  var rawK=new Array(values.length).fill(null);
  for (var i=0;i<values.length;i++) {
    if (rsi[i]==null||low[i]==null||high[i]==null) continue;
    var denom=high[i]-low[i]; rawK[i]=denom===0?0:((rsi[i]-low[i])/denom)*100;
  }
  var safeK=rawK.map(function(v){return v==null?0:v});
  var k=precomputeSma(safeK,Math.max(1,smoothK)).map(function(v,i){return rawK[i]==null?null:v});
  var safeK2=k.map(function(v){return v==null?0:v});
  var d=precomputeSma(safeK2,Math.max(1,smoothD)).map(function(v,i){return k[i]==null?null:v});
  return {rsi:rsi,k:k,d:d};
}

/* ──────────────────────────────────────────────────────────────────
   PANE MANAGEMENT
────────────────────────────────────────────────────────────────── */
function createPaneHost(titleText) {
  var host=document.createElement('div');
  Object.assign(host.style,{position:'relative',height:OSC_PANE_HEIGHT+'px',
    minHeight:OSC_PANE_HEIGHT+'px',borderTop:'1px solid #1e2a38'});
  var badge=document.createElement('div');
  Object.assign(badge.style,{position:'absolute',top:'6px',left:'10px',zIndex:'5',
    display:'flex',alignItems:'center',gap:'8px',background:'#0d1117dd',
    border:'1px solid #1e2a38',borderRadius:'4px',padding:'4px 8px',
    fontFamily:"'Space Mono', monospace",fontSize:'0.56rem',fontWeight:'700',
    letterSpacing:'0.06em',color:'#8aa4b6',pointerEvents:'none',backdropFilter:'blur(6px)'});
  var title=document.createElement('span'); title.textContent=titleText;
  var values=document.createElement('span'); values.textContent='—';
  badge.appendChild(title); badge.appendChild(values);
  host.appendChild(badge); oscillatorWrap.appendChild(host);
  return {host:host,valuesLabel:values};
}

function createPaneChart(host) {
  return LightweightCharts.createChart(host, {
    layout:{background:{type:'solid',color:'transparent'},textColor:'#4a6070',
      fontFamily:"'Space Mono', monospace",fontSize:10},
    grid:{vertLines:{visible:false},horzLines:{color:'#1e2a38',style:1}},
    crosshair:{mode:0,vertLine:{color:'#0095a855',labelBackgroundColor:'#0095a8'},
      horzLine:{color:'#0095a855',labelBackgroundColor:'#0095a8'}},
    rightPriceScale:{borderColor:'#1e2a38',visible:true,minimumWidth:PRICE_SCALE_MIN_WIDTH,
      scaleMargins:{top:0.14,bottom:0.12}},
    timeScale:{borderColor:'#1e2a38',timeVisible:true,secondsVisible:true,
      barSpacing:6,minBarSpacing:2,visible:false},
    handleScroll:true,handleScale:true,
  });
}

function updateOscillatorWrapVisibility() {
  oscillatorWrap.style.display = (paneState.rsi||paneState.stoch) ? 'flex' : 'none';
  var hasOsc = oscillatorWrap.style.display !== 'none';
  mainChart.applyOptions({rightPriceScale:{borderColor:'#1e2a38',visible:true,
    minimumWidth:PRICE_SCALE_MIN_WIDTH,scaleMargins:hasOsc?{top:0.08,bottom:0.08}:{top:0.08,bottom:0.18}}});
  mainChart.priceScale('vol').applyOptions({borderColor:'#1e2a38',visible:true,
    minimumWidth:PRICE_SCALE_MIN_WIDTH,scaleMargins:hasOsc?{top:0.90,bottom:0}:{top:0.88,bottom:0}});
  requestAnimationFrame(function(){
    resizeAllCharts();
    var range=lastVisibleRange;
    if (range) { syncPanesFromMain(range); updateOscillatorReadoutsAtTime(range.to); }
  });
}

function ensureRsiPane() {
  if (paneState.rsi) return paneState.rsi;
  var pane=createPaneHost('RSI');
  var chart=createPaneChart(pane.host);
  paneState.rsi={host:pane.host,valuesLabel:pane.valuesLabel,chart:chart,dynamicSeries:new Map()};
  chart.subscribeVisibleTimeRangeChange(function(range){
    if (ignoreTimeSync||!range||range.from==null||range.to==null) return;
    ignoreTimeSync=true;
    try { safeSetVisibleRange(mainChart,range);
      if (paneState.stoch&&paneState.stoch.chart) safeSetVisibleRange(paneState.stoch.chart,range);
    } finally { requestAnimationFrame(function(){ignoreTimeSync=false;}); }
  });
  chart.subscribeCrosshairMove(function(param){
    if (!paneState.rsi) return;
    var texts=[];
    paneState.rsi.dynamicSeries.forEach(function(entry,id){
      var dp=param.seriesData?param.seriesData.get(entry.line):null;
      if (dp&&dp.value!=null) {
        var ind=indicators.find(function(x){return x.id===id});
        if (ind) texts.push(formatIndicatorLabel(ind)+' '+dp.value.toFixed(2));
      }
    });
    paneState.rsi.valuesLabel.textContent=texts.length?texts.join(' · '):'—';
  });
  updateOscillatorWrapVisibility();
  requestAnimationFrame(function(){requestAnimationFrame(function(){
    resizePaneCharts();
    var range=lastVisibleRange;
    if (range) safeSetVisibleRange(chart,range);
  });});
  return paneState.rsi;
}

function ensureStochPane() {
  if (paneState.stoch) return paneState.stoch;
  var pane=createPaneHost('STOCH RSI');
  var chart=createPaneChart(pane.host);
  paneState.stoch={host:pane.host,valuesLabel:pane.valuesLabel,chart:chart,dynamicSeries:new Map()};
  chart.subscribeVisibleTimeRangeChange(function(range){
    if (ignoreTimeSync||!range||range.from==null||range.to==null) return;
    ignoreTimeSync=true;
    try { safeSetVisibleRange(mainChart,range);
      if (paneState.rsi&&paneState.rsi.chart) safeSetVisibleRange(paneState.rsi.chart,range);
    } finally { requestAnimationFrame(function(){ignoreTimeSync=false;}); }
  });
  chart.subscribeCrosshairMove(function(param){
    if (!paneState.stoch) return;
    var texts=[];
    paneState.stoch.dynamicSeries.forEach(function(entry,id){
      var dpK=param.seriesData?param.seriesData.get(entry.k):null;
      var dpD=param.seriesData?param.seriesData.get(entry.d):null;
      var ind=indicators.find(function(x){return x.id===id});
      if (ind) {
        if (dpK&&dpK.value!=null) texts.push(formatIndicatorLabel(ind)+' %K '+dpK.value.toFixed(2));
        if (dpD&&dpD.value!=null) texts.push('%D '+dpD.value.toFixed(2));
      }
    });
    paneState.stoch.valuesLabel.textContent=texts.length?texts.join(' · '):'—';
  });
  updateOscillatorWrapVisibility();
  requestAnimationFrame(function(){requestAnimationFrame(function(){
    resizePaneCharts();
    var range=lastVisibleRange;
    if (range) safeSetVisibleRange(chart,range);
  });});
  return paneState.stoch;
}

function destroyUnusedPanes() {
  var needRsi=indicators.some(function(i){return i.type==='RSI'});
  var needStoch=indicators.some(function(i){return i.type==='Stoch RSI'});
  if (!needRsi&&paneState.rsi) {
    paneState.rsi.dynamicSeries.forEach(function(e){Object.values(e).forEach(function(s){try{paneState.rsi.chart.removeSeries(s)}catch(err){}})});
    try{paneState.rsi.chart.remove()}catch(e){}
    try{oscillatorWrap.removeChild(paneState.rsi.host)}catch(e){}
    paneState.rsi=null;
  }
  if (!needStoch&&paneState.stoch) {
    paneState.stoch.dynamicSeries.forEach(function(e){Object.values(e).forEach(function(s){try{paneState.stoch.chart.removeSeries(s)}catch(err){}})});
    try{paneState.stoch.chart.remove()}catch(e){}
    try{oscillatorWrap.removeChild(paneState.stoch.host)}catch(e){}
    paneState.stoch=null;
  }
  updateOscillatorWrapVisibility();
}

/* ──────────────────────────────────────────────────────────────────
   SERIES REGISTRY
────────────────────────────────────────────────────────────────── */
function makeMainOverlaySeries(color, width, lineStyle) {
  return mainChart.addLineSeries({color:color,lineWidth:width||1.25,
    lineStyle:lineStyle||LightweightCharts.LineStyle.Solid,
    priceLineVisible:false,lastValueVisible:true,crosshairMarkerVisible:false,priceScaleId:'right'});
}

function makePaneLevelSeries(chart, color) {
  return chart.addLineSeries({color:color,lineWidth:1,
    lineStyle:LightweightCharts.LineStyle.Dashed,
    priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false,priceScaleId:'right'});
}

function removeIndicatorSeries(indicatorId) {
  var reg=indicatorSeriesRegistry.get(indicatorId);
  if (!reg) return;
  if (reg.kind==='main') { reg.series.forEach(function(s){try{mainChart.removeSeries(s)}catch(e){}}); }
  else if (reg.kind==='rsi'&&paneState.rsi) {
    var entry=paneState.rsi.dynamicSeries.get(indicatorId);
    if (entry) { Object.values(entry).forEach(function(s){try{paneState.rsi.chart.removeSeries(s)}catch(e){}}); paneState.rsi.dynamicSeries.delete(indicatorId); }
  } else if (reg.kind==='stoch'&&paneState.stoch) {
    var entry2=paneState.stoch.dynamicSeries.get(indicatorId);
    if (entry2) { Object.values(entry2).forEach(function(s){try{paneState.stoch.chart.removeSeries(s)}catch(e){}}); paneState.stoch.dynamicSeries.delete(indicatorId); }
  }
  indicatorSeriesRegistry.delete(indicatorId);
}

function ensureIndicatorSeries(ind) {
  var existing=indicatorSeriesRegistry.get(ind.id);
  if (existing) return existing;
  if (ind.type==='RSI') {
    var pane=ensureRsiPane();
    var line=pane.chart.addLineSeries({color:ind.color,lineWidth:1.4,priceLineVisible:true,
      lastValueVisible:true,crosshairMarkerVisible:true,priceScaleId:'right'});
    var ob=makePaneLevelSeries(pane.chart,withAlpha(ind.obColor||'#ff3d5a',0.55));
    var os=makePaneLevelSeries(pane.chart,withAlpha(ind.osColor||'#00e676',0.55));
    var mid=makePaneLevelSeries(pane.chart,withAlpha(ind.midColor||'#4a6070',0.45));
    pane.dynamicSeries.set(ind.id,{line:line,ob:ob,os:os,mid:mid});
    var reg={kind:'rsi',series:[line,ob,os,mid]};
    indicatorSeriesRegistry.set(ind.id,reg); return reg;
  }
  if (ind.type==='Stoch RSI') {
    var pane2=ensureStochPane();
    var k=pane2.chart.addLineSeries({color:ind.color,lineWidth:1.3,priceLineVisible:true,
      lastValueVisible:true,crosshairMarkerVisible:true,priceScaleId:'right'});
    var d=pane2.chart.addLineSeries({color:withAlpha(ind.color,0.55),lineWidth:1.1,priceLineVisible:true,
      lastValueVisible:true,crosshairMarkerVisible:true,priceScaleId:'right'});
    var ob2=makePaneLevelSeries(pane2.chart,withAlpha(ind.obColor||'#ff3d5a',0.55));
    var os2=makePaneLevelSeries(pane2.chart,withAlpha(ind.osColor||'#00e676',0.55));
    pane2.dynamicSeries.set(ind.id,{k:k,d:d,ob:ob2,os:os2});
    var reg2={kind:'stoch',series:[k,d,ob2,os2]};
    indicatorSeriesRegistry.set(ind.id,reg2); return reg2;
  }
  if (ind.type==='BB') {
    var upper=makeMainOverlaySeries(withAlpha(ind.color,0.95),1.1,LightweightCharts.LineStyle.Dashed);
    var basis=makeMainOverlaySeries(withAlpha(ind.color,0.65),1.15,LightweightCharts.LineStyle.Solid);
    var lower=makeMainOverlaySeries(withAlpha(ind.color,0.95),1.1,LightweightCharts.LineStyle.Dashed);
    var reg3={kind:'main',series:[upper,basis,lower]};
    indicatorSeriesRegistry.set(ind.id,reg3); return reg3;
  }
  var ma=makeMainOverlaySeries(ind.color,1.25,LightweightCharts.LineStyle.Solid);
  var reg4={kind:'main',series:[ma]};
  indicatorSeriesRegistry.set(ind.id,reg4); return reg4;
}

// ═══════════════════════════════════════════════════════════════════
// PART 2 — UI, Indicator CRUD, Signals, Data Loading, Init
// (paste directly after PART 1 inside the same IIFE)
// ═══════════════════════════════════════════════════════════════════

/* ──────────────────────────────────────────────────────────────────
   OSCILLATOR READOUTS
────────────────────────────────────────────────────────────────── */
function loadedIndexFromTime(time) {
  if (time == null || !loadedData.length) return -1;
  var t = Number(time);
  if (!Number.isFinite(t)) return -1;
  var globalIdx = binarySearchAtOrBefore(t);
  if (globalIdx < loadedWindow.start || globalIdx >= loadedWindow.end) return -1;
  return globalIdx - loadedWindow.start;
}

function updateOscillatorReadoutsAtTime(time) {
  var idx = loadedIndexFromTime(time);
  if (idx < 0) {
    if (paneState.rsi) paneState.rsi.valuesLabel.textContent = '—';
    if (paneState.stoch) paneState.stoch.valuesLabel.textContent = '—';
    return;
  }
  if (paneState.rsi) {
    var texts = [];
    paneState.rsi.dynamicSeries.forEach(function(entry, id) {
      var ind = indicators.find(function(x){return x.id===id});
      var computed = lastRenderedComputed.get(id);
      if (ind && computed && computed.raw && computed.raw[idx] != null)
        texts.push(formatIndicatorLabel(ind) + ' ' + computed.raw[idx].toFixed(2));
    });
    paneState.rsi.valuesLabel.textContent = texts.length ? texts.join(' · ') : '—';
  }
  if (paneState.stoch) {
    var texts2 = [];
    paneState.stoch.dynamicSeries.forEach(function(entry, id) {
      var ind = indicators.find(function(x){return x.id===id});
      var computed = lastRenderedComputed.get(id);
      if (ind && computed) {
        if (computed.kRaw && computed.kRaw[idx] != null)
          texts2.push(formatIndicatorLabel(ind) + ' %K ' + computed.kRaw[idx].toFixed(2));
        if (computed.dRaw && computed.dRaw[idx] != null)
          texts2.push('%D ' + computed.dRaw[idx].toFixed(2));
      }
    });
    paneState.stoch.valuesLabel.textContent = texts2.length ? texts2.join(' · ') : '—';
  }
}

/* ──────────────────────────────────────────────────────────────────
   INDICATOR CALC ON LOADED WINDOW
────────────────────────────────────────────────────────────────── */
function computeIndicatorForLoadedWindow(ind) {
  if (!loadedData.length) return null;

  var warmup = indicatorWarmup(ind);
  var calcStart = Math.max(0, loadedWindow.start - warmup);
  var calcEnd = loadedWindow.end;

  var cacheKey = [
    datasetVersion, currentRange, ind.id, ind.type, ind.length,
    ind.source, ind.stddev, ind.smoothK, ind.smoothD, calcStart, calcEnd
  ].join('|');

  if (indicatorWindowCache.has(cacheKey)) return indicatorWindowCache.get(cacheKey);

  var values = sourceArrayFor(ind.source).slice(calcStart, calcEnd);
  var times = fullData.slice(calcStart, calcEnd).map(function(b){return b.time});
  var offset = loadedWindow.start - calcStart;
  var visibleLen = loadedWindow.end - loadedWindow.start;
  var result = null;

  if (ind.type === 'BB') {
    var bb = precomputeBollinger(values, ind.length, Number(ind.stddev || 2));
    var basisRaw = bb.basis.slice(offset, offset + visibleLen);
    var upperRaw = bb.upper.slice(offset, offset + visibleLen);
    var lowerRaw = bb.lower.slice(offset, offset + visibleLen);
    var basis = [], upper = [], lower = [];
    for (var i = 0; i < visibleLen; i++) {
      var t = times[offset + i];
      if (upperRaw[i] != null) upper.push({time:t,value:upperRaw[i]});
      if (basisRaw[i] != null) basis.push({time:t,value:basisRaw[i]});
      if (lowerRaw[i] != null) lower.push({time:t,value:lowerRaw[i]});
    }
    result = {kind:'bb',basisRaw:basisRaw,upperRaw:upperRaw,lowerRaw:lowerRaw,basis:basis,upper:upper,lower:lower};
  } else if (ind.type === 'RSI') {
    var raw = precomputeRsi(values, ind.length).slice(offset, offset + visibleLen);
    var line = [];
    for (var j = 0; j < visibleLen; j++) { if (raw[j] != null) line.push({time:times[offset+j],value:raw[j]}); }
    result = {kind:'rsi',raw:raw,line:line};
  } else if (ind.type === 'Stoch RSI') {
    var stoch = precomputeStochRsi(values, ind.length, ind.smoothK||3, ind.smoothD||3);
    var kRaw = stoch.k.slice(offset, offset + visibleLen);
    var dRaw = stoch.d.slice(offset, offset + visibleLen);
    var kLine = [], dLine = [];
    for (var m = 0; m < visibleLen; m++) {
      var tt = times[offset + m];
      if (kRaw[m] != null) kLine.push({time:tt,value:kRaw[m]});
      if (dRaw[m] != null) dLine.push({time:tt,value:dRaw[m]});
    }
    result = {kind:'stoch',kRaw:kRaw,dRaw:dRaw,kLine:kLine,dLine:dLine};
  } else {
    var rawMa = precomputeMa(values, ind.type, ind.length).slice(offset, offset + visibleLen);
    var lineMa = [];
    for (var p = 0; p < visibleLen; p++) { if (rawMa[p] != null) lineMa.push({time:times[offset+p],value:rawMa[p]}); }
    result = {kind:'ma',raw:rawMa,line:lineMa};
  }

  indicatorWindowCache.set(cacheKey, result);
  return result;
}

function renderLevelLines() {
  if (!loadedData.length) return;
  var start = loadedData[0].time;
  var end = loadedData[loadedData.length-1].time;

  indicators.forEach(function(ind) {
    if (ind.type === 'RSI' && paneState.rsi) {
      var entry = paneState.rsi.dynamicSeries.get(ind.id);
      if (!entry) return;
      entry.ob.setData(ind.visible ? [{time:start,value:ind.obLevel},{time:end,value:ind.obLevel}] : []);
      entry.os.setData(ind.visible ? [{time:start,value:ind.osLevel},{time:end,value:ind.osLevel}] : []);
      entry.mid.setData(ind.visible && ind.showMid !== false ? [{time:start,value:ind.midLevel},{time:end,value:ind.midLevel}] : []);
    }
    if (ind.type === 'Stoch RSI' && paneState.stoch) {
      var entry2 = paneState.stoch.dynamicSeries.get(ind.id);
      if (!entry2) return;
      entry2.ob.setData(ind.visible ? [{time:start,value:ind.obLevel},{time:end,value:ind.obLevel}] : []);
      entry2.os.setData(ind.visible ? [{time:start,value:ind.osLevel},{time:end,value:ind.osLevel}] : []);
    }
  });
}

function renderIndicatorsNow() {
  if (!loadedData.length) return;
  lastRenderedIndicatorRawValues = new Map();
  lastRenderedComputed = new Map();

  indicators.forEach(function(ind) {
    var reg = ensureIndicatorSeries(ind);
    var computed = computeIndicatorForLoadedWindow(ind);
    if (!computed) return;
    lastRenderedComputed.set(ind.id, computed);

    if (ind.type === 'BB') {
      reg.series[0].setData(ind.visible ? computed.upper : []);
      reg.series[1].setData(ind.visible ? computed.basis : []);
      reg.series[2].setData(ind.visible ? computed.lower : []);
      reg.series.forEach(function(s){s.applyOptions({visible:ind.visible})});
      lastRenderedIndicatorRawValues.set(ind.id, computed.basisRaw);
    } else if (ind.type === 'RSI') {
      reg.series[0].setData(ind.visible ? computed.line : []);
      reg.series[0].applyOptions({visible:ind.visible});
      reg.series[1].applyOptions({visible:ind.visible});
      reg.series[2].applyOptions({visible:ind.visible});
      reg.series[3].applyOptions({visible:ind.visible && ind.showMid !== false});
      lastRenderedIndicatorRawValues.set(ind.id, computed.raw);
    } else if (ind.type === 'Stoch RSI') {
      reg.series[0].setData(ind.visible ? computed.kLine : []);
      reg.series[1].setData(ind.visible ? computed.dLine : []);
      reg.series[0].applyOptions({visible:ind.visible});
      reg.series[1].applyOptions({visible:ind.visible});
      reg.series[2].applyOptions({visible:ind.visible});
      reg.series[3].applyOptions({visible:ind.visible});
      lastRenderedIndicatorRawValues.set(ind.id, computed.kRaw);
    } else {
      reg.series[0].setData(ind.visible ? computed.line : []);
      reg.series[0].applyOptions({visible:ind.visible});
      lastRenderedIndicatorRawValues.set(ind.id, computed.raw);
    }
  });

  renderLevelLines();
  updateOscillatorReadoutsAtTime((lastVisibleRange && lastVisibleRange.to) || (loadedData.length ? loadedData[loadedData.length-1].time : null));
  refreshSignals();
}

function scheduleIndicatorRender() {
  if (indicatorRenderTimer) clearTimeout(indicatorRenderTimer);
  indicatorRenderTimer = setTimeout(function() {
    indicatorRenderTimer = null;
    renderIndicatorsNow();
  }, INDICATOR_RENDER_DEBOUNCE_MS);
}

/* ──────────────────────────────────────────────────────────────────
   SIGNALS
────────────────────────────────────────────────────────────────── */
function computeSignals(data, maValues) {
  if (!signalEnabled || !data || !data.length || !maValues || !maValues.length) return [];
  var markers = [];
  var bullCount = 0, bearCount = 0;
  for (var i = 1; i < data.length; i++) {
    var bar = data[i], mv = maValues[i];
    if (mv == null) { bullCount = 0; bearCount = 0; continue; }
    var c = bar.close, o = bar.open;
    var above = c > mv, below = c < mv;
    var bull = c >= o, bear = c < o;
    if (above && bull) bullCount++; else bullCount = 0;
    if (below && bear) bearCount++; else bearCount = 0;
    if (bullCount >= 2) {
      markers.push({time:bar.time,position:'belowBar',color:'#00e676',shape:'arrowUp',text:'BUY',size:1});
      bullCount = 0;
    }
    if (bearCount >= 2) {
      markers.push({time:bar.time,position:'aboveBar',color:'#ff3d5a',shape:'arrowDown',text:'SELL',size:1});
      bearCount = 0;
    }
  }
  return markers;
}

function refreshSignals() {
  _signalMarkers = [];
  if (!signalEnabled || !signalIndicatorId) { refreshAllMarkers(); return; }
  var ind = indicators.find(function(x){return x.id===signalIndicatorId && x.visible});
  if (!ind || ['EMA','HMA','SMA','WMA'].indexOf(ind.type) < 0) { refreshAllMarkers(); return; }
  var raw = lastRenderedIndicatorRawValues.get(ind.id);
  if (!raw || !loadedData.length) { refreshAllMarkers(); return; }
  _signalMarkers = computeSignals(loadedData, raw);
  refreshAllMarkers();
}

/* ──────────────────────────────────────────────────────────────────
   UI — OVERLAY CONTROLS
────────────────────────────────────────────────────────────────── */
var topControls = document.createElement('div');
Object.assign(topControls.style, {display:'flex',alignItems:'center',gap:'8px'});
overlayUi.appendChild(topControls);

var indicatorsButton = document.createElement('button');
indicatorsButton.textContent = 'Indicators';
Object.assign(indicatorsButton.style, {
  background:'#0d1117ee',border:'1px solid #1e2a38',color:'#c8d8e8',
  fontFamily:"'Space Mono', monospace",fontSize:'0.62rem',fontWeight:'700',
  letterSpacing:'0.08em',padding:'8px 12px',borderRadius:'6px',cursor:'pointer',
  boxShadow:'0 8px 20px rgba(0,0,0,0.24)',backdropFilter:'blur(8px)',
});
topControls.appendChild(indicatorsButton);

var signalControl = document.createElement('div');
Object.assign(signalControl.style, {
  display:'flex',alignItems:'center',gap:'6px',background:'#0d1117ee',
  border:'1px solid #1e2a38',borderRadius:'6px',padding:'6px 8px',
  boxShadow:'0 8px 20px rgba(0,0,0,0.24)',backdropFilter:'blur(8px)',
});
topControls.appendChild(signalControl);

var activeChipsWrap = document.createElement('div');
Object.assign(activeChipsWrap.style, {display:'flex',flexWrap:'wrap',gap:'6px',maxWidth:'620px'});
overlayUi.appendChild(activeChipsWrap);

var indicatorPanel = document.createElement('div');
Object.assign(indicatorPanel.style, {
  display:'none',width:'420px',maxHeight:'min(70vh, 720px)',background:'#0d1117f4',
  border:'1px solid #1e2a38',borderRadius:'8px',boxShadow:'0 18px 38px rgba(0,0,0,0.38)',
  backdropFilter:'blur(10px)',overflow:'hidden',
});
overlayUi.appendChild(indicatorPanel);

var panelScroll = document.createElement('div');
Object.assign(panelScroll.style, {maxHeight:'inherit',overflowY:'auto'});
indicatorPanel.appendChild(panelScroll);

var panelOpen = false;
function setPanelOpen(open) {
  panelOpen = !!open;
  indicatorPanel.style.display = panelOpen ? 'block' : 'none';
  indicatorsButton.style.borderColor = panelOpen ? '#00e5ff' : '#1e2a38';
  indicatorsButton.style.color = panelOpen ? '#00e5ff' : '#c8d8e8';
  indicatorsButton.style.background = panelOpen ? 'rgba(0,229,255,0.08)' : '#0d1117ee';
}
indicatorsButton.addEventListener('click', function(e) { e.stopPropagation(); setPanelOpen(!panelOpen); });
indicatorPanel.addEventListener('click', function(e) { e.stopPropagation(); });
overlayUi.addEventListener('click', function(e) { e.stopPropagation(); });
document.addEventListener('click', function() { setPanelOpen(false); });

function makeUiLabel(text) {
  var el = document.createElement('div');
  el.textContent = text;
  Object.assign(el.style, {fontFamily:"'Space Mono', monospace",fontSize:'0.54rem',
    fontWeight:'700',letterSpacing:'0.10em',color:'#6e8798',marginBottom:'4px'});
  return el;
}

function styleUiInput(el) {
  Object.assign(el.style, {width:'100%',background:'#111820',border:'1px solid #1e2a38',
    color:'#c8d8e8',fontFamily:"'Space Mono', monospace",fontSize:'0.62rem',
    fontWeight:'700',padding:'7px 8px',borderRadius:'5px',outline:'none'});
  return el;
}

// ── Panel header ────────────────────────────────────────────────
var panelHeader = document.createElement('div');
panelHeader.textContent = 'ADD / EDIT INDICATORS';
Object.assign(panelHeader.style, {padding:'10px 12px',borderBottom:'1px solid #1e2a38',
  fontFamily:"'Space Mono', monospace",fontSize:'0.62rem',fontWeight:'700',
  letterSpacing:'0.12em',color:'#00e5ff'});
panelScroll.appendChild(panelHeader);

var panelBody = document.createElement('div');
Object.assign(panelBody.style, {padding:'12px',display:'flex',flexDirection:'column',gap:'12px'});
panelScroll.appendChild(panelBody);

// ── Add section ─────────────────────────────────────────────────
var addSection = document.createElement('div');
Object.assign(addSection.style, {display:'flex',flexDirection:'column',gap:'10px',
  paddingBottom:'10px',borderBottom:'1px solid #1e2a38'});
panelBody.appendChild(addSection);

var addSectionTitle = document.createElement('div');
addSectionTitle.textContent = 'NEW INDICATOR';
Object.assign(addSectionTitle.style, {fontFamily:"'Space Mono', monospace",fontSize:'0.56rem',
  fontWeight:'700',letterSpacing:'0.12em',color:'#8aa4b6'});
addSection.appendChild(addSectionTitle);

var typeWrap = document.createElement('div');
typeWrap.appendChild(makeUiLabel('TYPE'));
var typeSelect = styleUiInput(document.createElement('select'));
INDICATOR_TYPES.forEach(function(type) {
  var o = document.createElement('option'); o.value = type; o.textContent = type; typeSelect.appendChild(o);
});
typeWrap.appendChild(typeSelect); addSection.appendChild(typeWrap);

var rowA = document.createElement('div');
Object.assign(rowA.style, {display:'grid',gridTemplateColumns:'1fr 1fr',gap:'8px'});
addSection.appendChild(rowA);

var lengthWrap = document.createElement('div');
lengthWrap.appendChild(makeUiLabel('LENGTH'));
var lengthInput = styleUiInput(document.createElement('input'));
lengthInput.type = 'number'; lengthInput.min = '1'; lengthInput.step = '1';
lengthWrap.appendChild(lengthInput); rowA.appendChild(lengthWrap);

var sourceWrap = document.createElement('div');
sourceWrap.appendChild(makeUiLabel('SOURCE'));
var sourceSelect = styleUiInput(document.createElement('select'));
PRICE_SOURCES.forEach(function(src) {
  var o = document.createElement('option'); o.value = src; o.textContent = src.toUpperCase(); sourceSelect.appendChild(o);
});
sourceWrap.appendChild(sourceSelect); rowA.appendChild(sourceWrap);

var rowB = document.createElement('div');
Object.assign(rowB.style, {display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:'8px'});
addSection.appendChild(rowB);

var colorWrap = document.createElement('div');
colorWrap.appendChild(makeUiLabel('COLOR'));
var colorInput = document.createElement('input'); colorInput.type = 'color';
Object.assign(colorInput.style, {width:'100%',height:'34px',background:'#111820',
  border:'1px solid #1e2a38',borderRadius:'5px',padding:'3px',cursor:'pointer'});
colorWrap.appendChild(colorInput); rowB.appendChild(colorWrap);

var stddevWrap = document.createElement('div');
stddevWrap.appendChild(makeUiLabel('STDDEV'));
var stddevInput = styleUiInput(document.createElement('input'));
stddevInput.type = 'number'; stddevInput.step = '0.1';
stddevWrap.appendChild(stddevInput); rowB.appendChild(stddevWrap);

var smoothKWrap = document.createElement('div');
smoothKWrap.appendChild(makeUiLabel('SMOOTH K'));
var smoothKInput = styleUiInput(document.createElement('input'));
smoothKInput.type = 'number'; smoothKInput.step = '1';
smoothKWrap.appendChild(smoothKInput); rowB.appendChild(smoothKWrap);

var rowC = document.createElement('div');
Object.assign(rowC.style, {display:'grid',gridTemplateColumns:'1fr auto',gap:'8px',alignItems:'end'});
addSection.appendChild(rowC);

var smoothDWrap = document.createElement('div');
smoothDWrap.appendChild(makeUiLabel('SMOOTH D'));
var smoothDInput = styleUiInput(document.createElement('input'));
smoothDInput.type = 'number'; smoothDInput.step = '1';
smoothDWrap.appendChild(smoothDInput); rowC.appendChild(smoothDWrap);

var addIndicatorButton = document.createElement('button');
addIndicatorButton.textContent = '+ ADD';
Object.assign(addIndicatorButton.style, {background:'rgba(0,229,255,0.08)',border:'1px solid #00e5ff',
  color:'#00e5ff',fontFamily:"'Space Mono', monospace",fontSize:'0.62rem',fontWeight:'700',
  letterSpacing:'0.10em',padding:'9px 14px',borderRadius:'5px',cursor:'pointer',minWidth:'88px'});
rowC.appendChild(addIndicatorButton);

var editorSection = document.createElement('div');
Object.assign(editorSection.style, {display:'flex',flexDirection:'column',gap:'10px'});
panelBody.appendChild(editorSection);

function applyFormDefaults(type) {
  var d = defaultForType(type);
  lengthInput.value = d.length != null ? d.length : 14;
  colorInput.value = d.color || '#00e5ff';
  sourceSelect.value = d.source || 'close';
  stddevInput.value = d.stddev != null ? d.stddev : 2;
  smoothKInput.value = d.smoothK != null ? d.smoothK : 3;
  smoothDInput.value = d.smoothD != null ? d.smoothD : 3;
  stddevWrap.style.display = type === 'BB' ? '' : 'none';
  smoothKWrap.style.display = type === 'Stoch RSI' ? '' : 'none';
  smoothDWrap.style.display = type === 'Stoch RSI' ? '' : 'none';
}
typeSelect.addEventListener('change', function() { applyFormDefaults(typeSelect.value); });
applyFormDefaults(typeSelect.value);

/* ──────────────────────────────────────────────────────────────────
   UI — SIGNAL CONTROL
────────────────────────────────────────────────────────────────── */
function renderSignalUi() {
  signalControl.innerHTML = '';
  var cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = signalEnabled;
  cb.style.accentColor = '#00e5ff';
  cb.addEventListener('change', function() { signalEnabled = cb.checked; renderSignalUi(); refreshSignals(); });
  var label = document.createElement('span'); label.textContent = 'Signals';
  Object.assign(label.style, {fontFamily:"'Space Mono', monospace",fontSize:'0.56rem',
    fontWeight:'700',letterSpacing:'0.08em',color:signalEnabled?'#00e5ff':'#6e8798'});
  signalControl.appendChild(cb); signalControl.appendChild(label);

  var candidates = indicators.filter(function(ind){return ['EMA','HMA','SMA','WMA'].indexOf(ind.type)>=0});
  if (!candidates.length) return;
  if (!signalIndicatorId || !candidates.some(function(ind){return ind.id===signalIndicatorId}))
    signalIndicatorId = candidates[0].id;

  var select = styleUiInput(document.createElement('select'));
  Object.assign(select.style, {width:'148px',padding:'5px 7px',fontSize:'0.56rem'});
  candidates.forEach(function(ind) {
    var o = document.createElement('option'); o.value = ind.id; o.textContent = formatIndicatorLabel(ind);
    if (ind.id === signalIndicatorId) o.selected = true; select.appendChild(o);
  });
  select.addEventListener('change', function() { signalIndicatorId = select.value; refreshSignals(); });
  signalControl.appendChild(select);
}

/* ──────────────────────────────────────────────────────────────────
   UI — INDICATOR CHIPS
────────────────────────────────────────────────────────────────── */
function renderIndicatorChips() {
  activeChipsWrap.innerHTML = '';
  indicators.forEach(function(ind) {
    var chip = document.createElement('div');
    Object.assign(chip.style, {display:'inline-flex',alignItems:'center',gap:'6px',
      background:selectedIndicatorId===ind.id?'rgba(0,229,255,0.08)':'#0d1117ee',
      border:selectedIndicatorId===ind.id?'1px solid #00e5ff':'1px solid #1e2a38',
      borderRadius:'999px',padding:'5px 8px',boxShadow:'0 4px 14px rgba(0,0,0,0.18)',
      backdropFilter:'blur(8px)',cursor:'pointer'});
    chip.addEventListener('click', function() {
      selectedIndicatorId = ind.id; renderIndicatorChips(); renderIndicatorEditors(); setPanelOpen(true);
    });

    var dot = document.createElement('span');
    Object.assign(dot.style, {width:'9px',height:'9px',borderRadius:'50%',background:ind.color,
      boxShadow:'0 0 8px '+withAlpha(ind.color,0.45),flexShrink:'0'});

    var text = document.createElement('span'); text.textContent = formatIndicatorLabel(ind);
    Object.assign(text.style, {fontFamily:"'Space Mono', monospace",fontSize:'0.56rem',
      fontWeight:'700',letterSpacing:'0.04em',color:'#c8d8e8',whiteSpace:'nowrap'});

    var toggleBtn = document.createElement('button');
    toggleBtn.textContent = ind.visible ? '◉' : '○';
    Object.assign(toggleBtn.style, {background:'transparent',border:'none',
      color:ind.visible?'#00e5ff':'#4a6070',fontFamily:"'Space Mono', monospace",
      fontSize:'0.68rem',cursor:'pointer',padding:'0 2px'});
    toggleBtn.addEventListener('click', function(e) { e.stopPropagation(); updateIndicator(ind.id, {visible:!ind.visible}); });

    var removeBtn = document.createElement('button'); removeBtn.textContent = '✕';
    Object.assign(removeBtn.style, {background:'transparent',border:'none',color:'#ff3d5a',
      fontFamily:"'Space Mono', monospace",fontSize:'0.62rem',fontWeight:'700',cursor:'pointer',padding:'0 2px'});
    removeBtn.addEventListener('click', function(e) {
      e.stopPropagation();
      if (selectedIndicatorId===ind.id) selectedIndicatorId=null;
      removeIndicator(ind.id);
    });

    chip.appendChild(dot); chip.appendChild(text); chip.appendChild(toggleBtn); chip.appendChild(removeBtn);
    activeChipsWrap.appendChild(chip);
  });
}

/* ──────────────────────────────────────────────────────────────────
   UI — INDICATOR EDITORS (inline in panel)
────────────────────────────────────────────────────────────────── */
function renderIndicatorEditors() {
  editorSection.innerHTML = '';

  var title = document.createElement('div'); title.textContent = 'ACTIVE INDICATORS';
  Object.assign(title.style, {fontFamily:"'Space Mono', monospace",fontSize:'0.56rem',
    fontWeight:'700',letterSpacing:'0.12em',color:'#8aa4b6'});
  editorSection.appendChild(title);

  if (!indicators.length) {
    var empty = document.createElement('div'); empty.textContent = 'No active indicators.';
    Object.assign(empty.style, {fontFamily:"'Space Mono', monospace",fontSize:'0.58rem',color:'#586f7f'});
    editorSection.appendChild(empty); return;
  }

  indicators.forEach(function(ind) {
    var card = document.createElement('div');
    Object.assign(card.style, {display:'flex',flexDirection:'column',gap:'8px',padding:'10px',
      borderRadius:'8px',border:selectedIndicatorId===ind.id?'1px solid #00e5ff':'1px solid #1e2a38',
      background:selectedIndicatorId===ind.id?'rgba(0,229,255,0.04)':'#0f151c'});

    // Header
    var head = document.createElement('div');
    Object.assign(head.style, {display:'flex',alignItems:'center',justifyContent:'space-between',gap:'8px',cursor:'pointer'});
    head.addEventListener('click', function() { selectedIndicatorId=ind.id; renderIndicatorEditors(); renderIndicatorChips(); });

    var left = document.createElement('div');
    Object.assign(left.style, {display:'flex',alignItems:'center',gap:'8px'});
    var dotE = document.createElement('span');
    Object.assign(dotE.style, {width:'10px',height:'10px',borderRadius:'50%',background:ind.color,flexShrink:'0'});
    var labelE = document.createElement('div'); labelE.textContent = formatIndicatorLabel(ind);
    Object.assign(labelE.style, {fontFamily:"'Space Mono', monospace",fontSize:'0.60rem',fontWeight:'700',
      letterSpacing:'0.05em',color:'#c8d8e8'});
    left.appendChild(dotE); left.appendChild(labelE);

    var actions = document.createElement('div');
    Object.assign(actions.style, {display:'flex',alignItems:'center',gap:'6px'});

    var vis = document.createElement('button'); vis.textContent = ind.visible ? 'VISIBLE' : 'HIDDEN';
    Object.assign(vis.style, {background:ind.visible?'rgba(0,229,255,0.08)':'transparent',
      border:'1px solid #1e2a38',color:ind.visible?'#00e5ff':'#6e8798',
      fontFamily:"'Space Mono', monospace",fontSize:'0.52rem',fontWeight:'700',
      letterSpacing:'0.08em',padding:'5px 8px',borderRadius:'4px',cursor:'pointer'});
    vis.addEventListener('click', function(e) { e.stopPropagation(); updateIndicator(ind.id, {visible:!ind.visible}); });

    var del = document.createElement('button'); del.textContent = 'REMOVE';
    Object.assign(del.style, {background:'transparent',border:'1px solid #3b2028',color:'#ff3d5a',
      fontFamily:"'Space Mono', monospace",fontSize:'0.52rem',fontWeight:'700',
      letterSpacing:'0.08em',padding:'5px 8px',borderRadius:'4px',cursor:'pointer'});
    del.addEventListener('click', function(e) {
      e.stopPropagation(); if (selectedIndicatorId===ind.id) selectedIndicatorId=null; removeIndicator(ind.id);
    });

    actions.appendChild(vis); actions.appendChild(del);
    head.appendChild(left); head.appendChild(actions);
    card.appendChild(head);

    // Fields grid
    var grid = document.createElement('div');
    Object.assign(grid.style, {display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:'8px'});
    card.appendChild(grid);

    function addField(parent, labelText, inputEl) {
      var wrap = document.createElement('div'); wrap.appendChild(makeUiLabel(labelText)); wrap.appendChild(inputEl); parent.appendChild(wrap);
    }

    var typeSel = styleUiInput(document.createElement('select'));
    INDICATOR_TYPES.forEach(function(t) {
      var o = document.createElement('option'); o.value=t; o.textContent=t; if (t===ind.type) o.selected=true; typeSel.appendChild(o);
    });
    typeSel.addEventListener('change', function() {
      var defaults = defaultForType(typeSel.value);
      updateIndicator(ind.id, {type:typeSel.value, length:defaults.length!=null?defaults.length:ind.length,
        source:defaults.source||ind.source, color:defaults.color||ind.color,
        stddev:defaults.stddev!=null?defaults.stddev:ind.stddev,
        smoothK:defaults.smoothK!=null?defaults.smoothK:ind.smoothK,
        smoothD:defaults.smoothD!=null?defaults.smoothD:ind.smoothD,
        obLevel:defaults.obLevel!=null?defaults.obLevel:ind.obLevel,
        osLevel:defaults.osLevel!=null?defaults.osLevel:ind.osLevel,
        midLevel:defaults.midLevel!=null?defaults.midLevel:ind.midLevel,
        showMid:defaults.showMid!=null?defaults.showMid:ind.showMid,
        obColor:defaults.obColor||ind.obColor, osColor:defaults.osColor||ind.osColor,
        midColor:defaults.midColor||ind.midColor});
    });
    addField(grid, 'TYPE', typeSel);

    var lenInput = styleUiInput(document.createElement('input'));
    lenInput.type='number'; lenInput.min='1'; lenInput.step='1'; lenInput.value=ind.length;
    lenInput.addEventListener('change', function() { updateIndicator(ind.id, {length:Number(lenInput.value||ind.length)}); });
    addField(grid, 'LENGTH', lenInput);

    var srcSel = styleUiInput(document.createElement('select'));
    PRICE_SOURCES.forEach(function(s) {
      var o = document.createElement('option'); o.value=s; o.textContent=s.toUpperCase();
      if (s===ind.source) o.selected=true; srcSel.appendChild(o);
    });
    srcSel.addEventListener('change', function() { updateIndicator(ind.id, {source:srcSel.value}); });
    addField(grid, 'SOURCE', srcSel);

    var grid2 = document.createElement('div');
    Object.assign(grid2.style, {display:'grid',gridTemplateColumns:'1fr 1fr 1fr',gap:'8px'});
    card.appendChild(grid2);

    var colorEl = document.createElement('input'); colorEl.type='color'; colorEl.value=ind.color;
    Object.assign(colorEl.style, {width:'100%',height:'34px',background:'#111820',
      border:'1px solid #1e2a38',borderRadius:'5px',padding:'3px',cursor:'pointer'});
    colorEl.addEventListener('input', function() { updateIndicator(ind.id, {color:colorEl.value}); });
    addField(grid2, 'COLOR', colorEl);

    if (ind.type === 'BB') {
      var stdInput = styleUiInput(document.createElement('input'));
      stdInput.type='number'; stdInput.step='0.1'; stdInput.value=ind.stddev!=null?ind.stddev:2;
      stdInput.addEventListener('change', function() { updateIndicator(ind.id, {stddev:Number(stdInput.value||2)}); });
      addField(grid2, 'STDDEV', stdInput);
    }

    if (ind.type === 'Stoch RSI') {
      var kInput = styleUiInput(document.createElement('input'));
      kInput.type='number'; kInput.step='1'; kInput.value=ind.smoothK!=null?ind.smoothK:3;
      kInput.addEventListener('change', function() { updateIndicator(ind.id, {smoothK:Number(kInput.value||3)}); });
      addField(grid2, 'SMOOTH K', kInput);
      var dInput = styleUiInput(document.createElement('input'));
      dInput.type='number'; dInput.step='1'; dInput.value=ind.smoothD!=null?ind.smoothD:3;
      dInput.addEventListener('change', function() { updateIndicator(ind.id, {smoothD:Number(dInput.value||3)}); });
      addField(grid2, 'SMOOTH D', dInput);
    }

    // Level editors for RSI / Stoch RSI
    if (ind.type === 'RSI' || ind.type === 'Stoch RSI') {
      var lvlTitle = document.createElement('div'); lvlTitle.textContent = 'LEVELS';
      Object.assign(lvlTitle.style, {fontFamily:"'Space Mono', monospace",fontSize:'0.54rem',
        fontWeight:'700',letterSpacing:'0.10em',color:'#8aa4b6',marginTop:'2px'});
      card.appendChild(lvlTitle);

      var lvlGrid = document.createElement('div');
      Object.assign(lvlGrid.style, {display:'grid',
        gridTemplateColumns:ind.type==='RSI'?'1fr 1fr 1fr 1fr 1fr 1fr':'1fr 1fr 1fr 1fr',gap:'8px'});
      card.appendChild(lvlGrid);

      var obVal = styleUiInput(document.createElement('input'));
      obVal.type='number'; obVal.step='0.1'; obVal.value=ind.obLevel!=null?ind.obLevel:(ind.type==='RSI'?70:80);
      obVal.addEventListener('change', function() { updateIndicator(ind.id, {obLevel:Number(obVal.value)}); });
      addField(lvlGrid, 'OB', obVal);

      var osVal = styleUiInput(document.createElement('input'));
      osVal.type='number'; osVal.step='0.1'; osVal.value=ind.osLevel!=null?ind.osLevel:(ind.type==='RSI'?30:20);
      osVal.addEventListener('change', function() { updateIndicator(ind.id, {osLevel:Number(osVal.value)}); });
      addField(lvlGrid, 'OS', osVal);

      var obColorEl = document.createElement('input'); obColorEl.type='color'; obColorEl.value=ind.obColor||'#ff3d5a';
      Object.assign(obColorEl.style, {width:'100%',height:'34px',background:'#111820',
        border:'1px solid #1e2a38',borderRadius:'5px',padding:'3px',cursor:'pointer'});
      obColorEl.addEventListener('input', function() { updateIndicator(ind.id, {obColor:obColorEl.value}); });
      addField(lvlGrid, 'OB COLOR', obColorEl);

      var osColorEl = document.createElement('input'); osColorEl.type='color'; osColorEl.value=ind.osColor||'#00e676';
      Object.assign(osColorEl.style, {width:'100%',height:'34px',background:'#111820',
        border:'1px solid #1e2a38',borderRadius:'5px',padding:'3px',cursor:'pointer'});
      osColorEl.addEventListener('input', function() { updateIndicator(ind.id, {osColor:osColorEl.value}); });
      addField(lvlGrid, 'OS COLOR', osColorEl);

      if (ind.type === 'RSI') {
        var midVal = styleUiInput(document.createElement('input'));
        midVal.type='number'; midVal.step='0.1'; midVal.value=ind.midLevel!=null?ind.midLevel:50;
        midVal.addEventListener('change', function() { updateIndicator(ind.id, {midLevel:Number(midVal.value)}); });
        addField(lvlGrid, 'MID', midVal);

        var midColorEl = document.createElement('input'); midColorEl.type='color'; midColorEl.value=ind.midColor||'#4a6070';
        Object.assign(midColorEl.style, {width:'100%',height:'34px',background:'#111820',
          border:'1px solid #1e2a38',borderRadius:'5px',padding:'3px',cursor:'pointer'});
        midColorEl.addEventListener('input', function() { updateIndicator(ind.id, {midColor:midColorEl.value}); });
        addField(lvlGrid, 'MID COLOR', midColorEl);
      }
    }

    editorSection.appendChild(card);
  });
}

/* ──────────────────────────────────────────────────────────────────
   INDICATOR CRUD
────────────────────────────────────────────────────────────────── */
function addIndicator(def) {
  var defaults = defaultForType(def.type);
  var ind = {
    id: 'ind_' + (nextIndicatorId++),
    type: def.type,
    length: clamp(parseInt(def.length != null ? def.length : defaults.length, 10) || defaults.length || 14, 1, 2000),
    color: def.color || defaults.color || '#00e5ff',
    source: PRICE_SOURCES.indexOf(def.source) >= 0 ? def.source : (defaults.source || 'close'),
    visible: def.visible !== false,
    stddev: def.stddev != null ? Number(def.stddev) : (defaults.stddev != null ? defaults.stddev : 2),
    smoothK: def.smoothK != null ? Math.max(1,Number(def.smoothK)) : (defaults.smoothK != null ? defaults.smoothK : 3),
    smoothD: def.smoothD != null ? Math.max(1,Number(def.smoothD)) : (defaults.smoothD != null ? defaults.smoothD : 3),
    obLevel: def.obLevel != null ? Number(def.obLevel) : defaults.obLevel,
    osLevel: def.osLevel != null ? Number(def.osLevel) : defaults.osLevel,
    midLevel: def.midLevel != null ? Number(def.midLevel) : defaults.midLevel,
    showMid: def.showMid != null ? !!def.showMid : defaults.showMid,
    obColor: def.obColor || defaults.obColor,
    osColor: def.osColor || defaults.osColor,
    midColor: def.midColor || defaults.midColor,
  };
  indicators.push(ind);
  selectedIndicatorId = ind.id;
  ensureIndicatorSeries(ind);
  if (!signalIndicatorId && ['EMA','HMA','SMA','WMA'].indexOf(ind.type) >= 0) signalIndicatorId = ind.id;
  renderIndicatorChips(); renderSignalUi(); renderIndicatorEditors();
  invalidateIndicatorCache();
  requestAnimationFrame(function() {
    resizeAllCharts();
    var range = lastVisibleRange;
    if (range) syncPanesFromMain(range);
    scheduleIndicatorRender();
  });
}

function removeIndicator(indicatorId) {
  indicators = indicators.filter(function(ind){return ind.id !== indicatorId});
  removeIndicatorSeries(indicatorId);
  lastRenderedIndicatorRawValues.delete(indicatorId);
  lastRenderedComputed.delete(indicatorId);
  if (signalIndicatorId === indicatorId) {
    var next = indicators.find(function(ind){return ['EMA','HMA','SMA','WMA'].indexOf(ind.type)>=0});
    signalIndicatorId = next ? next.id : null;
  }
  if (selectedIndicatorId === indicatorId) {
    selectedIndicatorId = indicators.length ? indicators[0].id : null;
  }
  destroyUnusedPanes();
  renderIndicatorChips(); renderSignalUi(); renderIndicatorEditors();
  invalidateIndicatorCache();
  requestAnimationFrame(function() {
    resizeAllCharts();
    var range = lastVisibleRange;
    if (range) syncPanesFromMain(range);
    scheduleIndicatorRender();
  });
}

function updateIndicator(indicatorId, patch) {
  var ind = indicators.find(function(x){return x.id===indicatorId});
  if (!ind) return;
  var oldType = ind.type;
  Object.assign(ind, patch);
  var defaults = defaultForType(ind.type);
  ind.length = clamp(parseInt(ind.length,10)||defaults.length||14,1,2000);
  ind.source = PRICE_SOURCES.indexOf(ind.source)>=0 ? ind.source : 'close';
  if (ind.type==='BB') ind.stddev = Number(ind.stddev!=null?ind.stddev:2);
  if (ind.type==='Stoch RSI') {
    ind.smoothK = Math.max(1,Number(ind.smoothK!=null?ind.smoothK:3));
    ind.smoothD = Math.max(1,Number(ind.smoothD!=null?ind.smoothD:3));
  }
  ind.obLevel = ind.obLevel!=null ? Number(ind.obLevel) : defaults.obLevel;
  ind.osLevel = ind.osLevel!=null ? Number(ind.osLevel) : defaults.osLevel;
  ind.midLevel = ind.midLevel!=null ? Number(ind.midLevel) : defaults.midLevel;
  ind.showMid = ind.showMid!=null ? !!ind.showMid : defaults.showMid;
  ind.obColor = ind.obColor || defaults.obColor;
  ind.osColor = ind.osColor || defaults.osColor;
  ind.midColor = ind.midColor || defaults.midColor;

  if (oldType !== ind.type) {
    removeIndicatorSeries(indicatorId);
    destroyUnusedPanes();
    ensureIndicatorSeries(ind);
    if (signalIndicatorId===indicatorId && ['EMA','HMA','SMA','WMA'].indexOf(ind.type)<0) {
      var next = indicators.find(function(x){return ['EMA','HMA','SMA','WMA'].indexOf(x.type)>=0});
      signalIndicatorId = next ? next.id : null;
    } else if (!signalIndicatorId && ['EMA','HMA','SMA','WMA'].indexOf(ind.type)>=0) {
      signalIndicatorId = indicatorId;
    }
  }

  var reg = ensureIndicatorSeries(ind);
  if (ind.type==='BB') {
    reg.series[0].applyOptions({color:withAlpha(ind.color,0.95),visible:ind.visible});
    reg.series[1].applyOptions({color:withAlpha(ind.color,0.65),visible:ind.visible});
    reg.series[2].applyOptions({color:withAlpha(ind.color,0.95),visible:ind.visible});
  } else if (ind.type==='Stoch RSI') {
    reg.series[0].applyOptions({color:ind.color,visible:ind.visible});
    reg.series[1].applyOptions({color:withAlpha(ind.color,0.55),visible:ind.visible});
    reg.series[2].applyOptions({color:withAlpha(ind.obColor||'#ff3d5a',0.55),visible:ind.visible});
    reg.series[3].applyOptions({color:withAlpha(ind.osColor||'#00e676',0.55),visible:ind.visible});
  } else if (ind.type==='RSI') {
    reg.series[0].applyOptions({color:ind.color,visible:ind.visible});
    reg.series[1].applyOptions({color:withAlpha(ind.obColor||'#ff3d5a',0.55),visible:ind.visible});
    reg.series[2].applyOptions({color:withAlpha(ind.osColor||'#00e676',0.55),visible:ind.visible});
    reg.series[3].applyOptions({color:withAlpha(ind.midColor||'#4a6070',0.45),visible:ind.visible&&ind.showMid!==false});
  } else {
    reg.series[0].applyOptions({color:ind.color,visible:ind.visible});
  }

  renderIndicatorChips(); renderSignalUi(); renderIndicatorEditors();
  invalidateIndicatorCache();
  requestAnimationFrame(function() {
    resizeAllCharts();
    var range = lastVisibleRange;
    if (range) syncPanesFromMain(range);
    scheduleIndicatorRender();
  });
}

addIndicatorButton.addEventListener('click', function() {
  var type = typeSelect.value;
  var def = {type:type, length:Number(lengthInput.value), color:colorInput.value, source:sourceSelect.value, visible:true};
  if (type==='BB') def.stddev = Number(stddevInput.value||2);
  if (type==='Stoch RSI') { def.smoothK = Number(smoothKInput.value||3); def.smoothD = Number(smoothDInput.value||3); }
  addIndicator(def);
  setPanelOpen(false);
});

/* ──────────────────────────────────────────────────────────────────
   LOAD DATASET
────────────────────────────────────────────────────────────────── */
async function loadRange(rangePt) {
  currentRange = rangePt;
  var url = RANGE_FILES[rangePt];
  if (!url) return;

  try {
    var resp = await fetch(url);
    var rawBars = await resp.json();

    fullData = normalizeBars(rawBars);
    datasetVersion += 1;
    rebuildSourceCache();
    invalidateIndicatorCache();

    // ── Immediate first render: load tail window ────────────
    var total = fullData.length;
    var winStart = Math.max(0, total - INITIAL_WINDOW_BARS);
    var winEnd = total;

    loadedWindow = { start: winStart, end: winEnd };
    loadedData = fullData.slice(winStart, winEnd);

    // Set data immediately — no waiting
    candleSeries.setData(loadedData);
    volumeSeries.setData(volumeDataForLoadedWindow());

    var lastBar = fullData[fullData.length - 1];
    var prevBar = fullData.length > 1 ? fullData[fullData.length - 2] : null;
    updateHeader(lastBar, prevBar);
    updateSidebar(lastBar);

    var candlesEl = document.getElementById('statCandles');
    if (candlesEl) candlesEl.textContent = fullData.length.toLocaleString();

    // Fit content immediately
    resizeAllCharts();
    mainChart.timeScale().fitContent();

    // Rebuild backtest markers if any
    if (fullBacktestTrades.length) {
      rebuildBacktestMarkerCache(fullBacktestTrades);
    }
    refreshAllMarkers();

    // Render indicators + signals after 1 frame
    requestAnimationFrame(function() {
      renderIndicatorChips();
      renderSignalUi();
      renderIndicatorEditors();
      scheduleIndicatorRender();

      // Sync panes after another frame
      requestAnimationFrame(function() {
        var range = mainChart.timeScale().getVisibleRange();
        if (range) {
          lastVisibleRange = range;
          syncPanesFromMain(range);
        }
      });
    });

  } catch (err) {
    console.error('Load error:', err);
  }
}

/* ──────────────────────────────────────────────────────────────────
   MAIN CHART HOVER / CROSSHAIR
────────────────────────────────────────────────────────────────── */
mainChart.subscribeCrosshairMove(function(param) {
  var dp = param.seriesData ? param.seriesData.get(candleSeries) : null;
  if (dp && dp.open != null) updateSidebar(dp);

  // Sync guide
  if (!param || !param.point || param.point.x == null || param.point.x < 0 || param.point.x > rootContainer.clientWidth) {
    syncGuide.style.display = 'none';
  } else {
    syncGuide.style.display = 'block';
    syncGuide.style.left = Math.round(param.point.x) + 'px';
  }

  if (param && param.time != null) updateOscillatorReadoutsAtTime(param.time);
  else updateOscillatorReadoutsAtTime(null);
});

/* ──────────────────────────────────────────────────────────────────
   RANGE BUTTONS
────────────────────────────────────────────────────────────────── */
document.querySelectorAll('.interval-btn').forEach(function(btn) {
  btn.addEventListener('click', function() {
    document.querySelectorAll('.interval-btn').forEach(function(b){b.classList.remove('active')});
    btn.classList.add('active');
    var pt = parseInt(btn.textContent, 10);
    loadRange(pt);
  });
});

/* ──────────────────────────────────────────────────────────────────
   RESIZE
────────────────────────────────────────────────────────────────── */
var _resizeTimer = null;
new ResizeObserver(function() {
  if (_resizeTimer) clearTimeout(_resizeTimer);
  _resizeTimer = setTimeout(function() {
    _resizeTimer = null;
    resizeAllCharts();
    var range = lastVisibleRange;
    if (range) syncPanesFromMain(range);
  }, 100);
}).observe(rootContainer);

/* ──────────────────────────────────────────────────────────────────
   LIVE APPEND STUB
────────────────────────────────────────────────────────────────── */
function appendNewBar(bar) {
  var normalized = normalizeBars(fullData.length ? [fullData[fullData.length-1], bar] : [bar]);
  var nextBar = normalized[normalized.length-1];
  if (!nextBar) return;

  fullData.push(nextBar);
  sourceCache.close.push(nextBar.close);
  sourceCache.open.push(nextBar.open);
  sourceCache.high.push(nextBar.high);
  sourceCache.low.push(nextBar.low);
  datasetVersion += 1;
  invalidateIndicatorCache();

  // If user is near right edge, extend window
  var vis = lastVisibleRange;
  var nearRight = vis ? binarySearchAtOrBefore(vis.to) >= fullData.length - 10 : true;
  if (nearRight) {
    loadedWindow.end = fullData.length;
    applyLoadedWindow();
    var last = fullData[fullData.length-1];
    var prev = fullData.length > 1 ? fullData[fullData.length-2] : null;
    updateHeader(last, prev);
  } else {
    var last2 = fullData[fullData.length-1];
    var prev2 = fullData.length > 1 ? fullData[fullData.length-2] : null;
    updateHeader(last2, prev2);
  }
}

/* ──────────────────────────────────────────────────────────────────
   INIT
────────────────────────────────────────────────────────────────── */
renderSignalUi();
renderIndicatorEditors();
DEFAULT_INDICATORS.forEach(function(def) { addIndicator(def); });
loadRange(currentRange);

// Close IIFE
})();
```
