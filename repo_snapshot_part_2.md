# Repository Snapshot - Part 2 of 5

- Root folder: `/home/hurairahengg/Documents/Jinni Zero`
- You know my wholle Jinjnibacktester simulator thign whre ther is a UI bascially and then i can see  charst and stuff when i need to run simulatiosn liek i send simulatio nto my flask backend server it runs sims and then shows stast and stuff and i can load strategy and shit for now take a look we will be doing bug fixes and some validation and shit. udnerrtsnad each code and its role how it works and keep in ir conetxt i will ask u exactly wha tto do later code later duinerstood
- Total files indexed: `24`
- Files in this chunk: `6`
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
backend/strategies/idk.py
backend/strategies/JinniContiniumV2.py
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

## Files In This Chunk - Part 2

```text
backend/__init__.py
backend/strategies/__init__.py
backend/strategies/base.py
backend/strategies/JinniContiniumV2.py
backend/strategies/legacyReplicator.py
js/backtest.js
```

## File Contents


---

## FILE: `backend/__init__.py`

```python
# backend/__init__.py
```

---

## FILE: `backend/strategies/__init__.py`

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
        3. For each bar:
           a. Engine calls on_bar(ctx)           → strategy returns signal
           b. If in position: engine calls on_manage(ctx) → trade management
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

    # ==========================================================
    # TRADE MANAGEMENT (optional — called every bar while in position)
    # ==========================================================
    def on_manage(self, ctx: Any) -> Optional[Dict[str, Any]]:
        """
        Called every bar when a position is OPEN, after on_bar.
        Override to implement trade management (trailing stop, breakeven, etc.)

        ctx.position fields available:
          .has_position    bool
          .direction       'long' | 'short'
          .entry_price     float
          .entry_bar       int
          .bars_held       int
          .sl_level        float | None
          .tp_level        float | None
          .unrealized_pts  float
          .unrealized_pnl  float (dollars)
          .unrealized_r    float | None (R-multiple of floating PnL)
          .mae             float (max adverse excursion, points)
          .mfe             float (max favorable excursion, points)

        Return None or {} for no action, or a dict with any of:
          {"update_sl": new_sl_price}
          {"update_tp": new_tp_price}
          {"close": True, "close_reason": "reason_string"}

        on_bar signals take priority over on_manage if both return updates.
        Strategies that don't override this get no trade management (default).
        """
        return None
```

---

## FILE: `backend/strategies/JinniContiniumV2.py`

```python
"""
JINNI ZERO — Jinni Renko Rider
================================
Pure renko/range-bar trend-following strategy with trailing stop.
"""
from __future__ import annotations
from typing import Any, Dict, List, Optional
from backend.strategies.base import BaseStrategy


class JinniRenkoRider(BaseStrategy):
    strategy_id = "jinni_renko_rider"
    name = "Jinni Renko Rider"
    description = (
        "Trend rider: N consecutive same-direction bars → entry, "
        "SL at last bar's extreme, trails every new favorable bar. "
        "No TP — rides until reversal hits trailing SL."
    )
    version = "1.2.0"
    min_lookback = 0

    parameters = {
        "confirm_bars": {
            "type": "number", "label": "Confirmation Bars",
            "default": 2, "min": 1, "max": 10, "step": 1,
            "help": "Consecutive same-direction bars needed before entry.",
        },
        "sl_offset": {
            "type": "number", "label": "SL Offset (pts)",
            "default": 0, "min": 0, "max": 50, "step": 0.25,
            "help": "Buffer beyond bar low/high for SL. Prevents exact-wick stops.",
        },
        "no_reuse": {
            "type": "boolean", "label": "No Candle Reuse",
            "default": True,
            "help": "Bars used for signal + trade can't count toward next signal.",
        },
    }

    def build_indicators(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        return []

    def on_init(self, ctx: Any) -> None:
        s = ctx.state
        s["bull_count"] = 0
        s["bear_count"] = 0
        s["last_used_bar"] = -1
        s["_last_trade_count"] = 0
        s["_trail_count"] = 0

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
        sl_offset = float(p.get("sl_offset", 0))
        no_reuse = bool(p.get("no_reuse", True))

        # ── Update last used bar from closed trades ───────────
        if no_reuse:
            trades = ctx.trades
            last_count = s.get("_last_trade_count", 0)
            if len(trades) > last_count:
                last_trade = trades[-1]
                exit_bar = last_trade.get("exit_bar", i)
                s["last_used_bar"] = exit_bar
                s["bull_count"] = 0
                s["bear_count"] = 0
            s["_last_trade_count"] = len(trades)

        # ══════════════════════════════════════════════════════
        # IN POSITION → TRAIL SL via on_bar
        #
        # Trailing is done here (not on_manage) because Renko
        # Rider's trail IS the core signal logic — every
        # favorable bar tightens the stop. on_manage is for
        # secondary management (breakeven etc).
        #
        # The engine applies update_sl AFTER exit check, so
        # the new SL takes effect on the NEXT bar. This
        # prevents same-bar trail→stop-hit.
        # ══════════════════════════════════════════════════════
        if ctx.position.has_position:
            pos = ctx.position

            if pos.direction == "long" and bull:
                new_sl = l - sl_offset
                current_sl = pos.sl_level
                if current_sl is None or new_sl > current_sl:
                    s["_trail_count"] = s.get("_trail_count", 0) + 1
                    return {"signal": "HOLD", "update_sl": new_sl}

            elif pos.direction == "short" and bear:
                new_sl = h + sl_offset
                current_sl = pos.sl_level
                if current_sl is None or new_sl < current_sl:
                    s["_trail_count"] = s.get("_trail_count", 0) + 1
                    return {"signal": "HOLD", "update_sl": new_sl}

            return {"signal": "HOLD"}

        # ══════════════════════════════════════════════════════
        # FLAT — look for entry signals
        # ══════════════════════════════════════════════════════
        if no_reuse and i <= s.get("last_used_bar", -1):
            s["bull_count"] = 0
            s["bear_count"] = 0
            return None

        if bull:
            s["bull_count"] = s.get("bull_count", 0) + 1
            s["bear_count"] = 0
        elif bear:
            s["bear_count"] = s.get("bear_count", 0) + 1
            s["bull_count"] = 0
        else:
            s["bull_count"] = 0
            s["bear_count"] = 0
            return None

        sig = None
        sl_price = None

        if s["bull_count"] >= confirm_bars:
            sig = "BUY"
            sl_price = l - sl_offset
            s["bull_count"] = 0
            s["bear_count"] = 0
            if no_reuse:
                s["last_used_bar"] = i

        elif s["bear_count"] >= confirm_bars:
            sig = "SELL"
            sl_price = h + sl_offset
            s["bull_count"] = 0
            s["bear_count"] = 0
            if no_reuse:
                s["last_used_bar"] = i

        if sig is None:
            return None

        s["_trail_count"] = 0

        return {
            "signal": sig,
            "sl": sl_price,
        }
```

---

## FILE: `backend/strategies/legacyReplicator.py`

```python
"""
JINNI ZERO — Legacy Replicator Strategy
========================================
Produces IDENTICAL results to Legacy Mode (backtest_server.py).

This is a VERIFICATION TOOL:
  1. Configure Legacy mode with your desired settings
  2. Run Legacy backtest, note results
  3. Switch to Strategy mode, select LegacyReplicator
  4. Set same parameters
  5. Results MUST match exactly

If they don't match → there's a bug in the engine.

Uses engine-computed SL/TP (Phase 3) so the engine computes
SL/TP at fill time from next bar's open — exactly matching Legacy.

Uses engine-level MA cross exits (Phase 4) so the engine checks
MA crosses on each bar — exactly matching Legacy timing.

Signal logic replicates Legacy's:
  - above_all_mas: 2-bar confirmation + regime tracking
  - ma_cross: fast/slow crossover
  - trend_filter: close vs longest MA crossover
  - candle direction confirmation
  - trade gating (one-per-direction lock)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.strategies.base import BaseStrategy


class LegacyReplicator(BaseStrategy):
    strategy_id = "legacy_replicator"
    name = "Legacy Replicator (Verification)"
    description = (
        "Replicates Legacy Mode (backtest_server.py) exactly. "
        "Use to verify Strategy Loader produces identical results. "
        "Set the same MA, SL, TP, gating, and candle confirm "
        "parameters as Legacy, then compare trade logs."
    )
    version = "1.0"
    min_lookback = 0

    parameters = {
        "entry_mode": {
            "type": "enum",
            "label": "Entry Mode",
            "options": ["above_all_mas", "ma_cross", "trend_filter"],
            "default": "above_all_mas",
            "help": "Must match Legacy Entry Condition dropdown.",
        },
        "ma1_type": {
            "type": "enum",
            "label": "MA 1 Type",
            "options": ["HMA", "EMA", "SMA", "WMA"],
            "default": "HMA",
        },
        "ma1_period": {
            "type": "number",
            "label": "MA 1 Period",
            "default": 21,
            "min": 2,
            "max": 500,
            "step": 1,
        },
        "ma2_enabled": {
            "type": "boolean",
            "label": "Enable MA 2",
            "default": False,
            "help": "Enable second MA (required for ma_cross entry mode).",
        },
        "ma2_type": {
            "type": "enum",
            "label": "MA 2 Type",
            "options": ["HMA", "EMA", "SMA", "WMA"],
            "default": "EMA",
        },
        "ma2_period": {
            "type": "number",
            "label": "MA 2 Period",
            "default": 55,
            "min": 2,
            "max": 500,
            "step": 1,
        },
        "require_candle_confirm": {
            "type": "boolean",
            "label": "Require Candle Direction",
            "default": True,
            "help": "Entry candle must match trade direction (Legacy default: ON).",
        },
        "sl_mode": {
            "type": "enum",
            "label": "SL Mode",
            "options": ["fixed", "ma_snapshot", "ma_cross"],
            "default": "fixed",
        },
        "sl_fixed_pts": {
            "type": "number",
            "label": "SL Fixed Points",
            "default": 8,
            "min": 0.25,
            "step": 0.25,
            "help": "Only used when SL Mode = fixed.",
        },
        "sl_ma_type": {
            "type": "enum",
            "label": "SL MA Type",
            "options": ["EMA", "HMA", "SMA", "WMA"],
            "default": "EMA",
            "help": "Only used when SL Mode = ma_snapshot or ma_cross.",
        },
        "sl_ma_period": {
            "type": "number",
            "label": "SL MA Period",
            "default": 50,
            "min": 2,
            "max": 500,
            "step": 1,
        },
        "tp_mode": {
            "type": "enum",
            "label": "TP Mode",
            "options": ["r_multiple", "ma_cross"],
            "default": "r_multiple",
        },
        "tp_r": {
            "type": "number",
            "label": "R Multiple",
            "default": 2,
            "min": 0.5,
            "max": 20,
            "step": 0.5,
            "help": "Only used when TP Mode = r_multiple.",
        },
        "tp_ma_type": {
            "type": "enum",
            "label": "TP MA Type",
            "options": ["EMA", "HMA", "SMA", "WMA"],
            "default": "EMA",
            "help": "Only used when TP Mode = ma_cross.",
        },
        "tp_ma_period": {
            "type": "number",
            "label": "TP MA Period",
            "default": 9,
            "min": 2,
            "max": 500,
            "step": 1,
        },
        "gating_enabled": {
            "type": "boolean",
            "label": "Trade Gating",
            "default": False,
            "help": "Lock direction until price crosses gating MA.",
        },
        "gating_ma_type": {
            "type": "enum",
            "label": "Gating MA Type",
            "options": ["HMA", "EMA", "SMA", "WMA"],
            "default": "HMA",
        },
        "gating_ma_period": {
            "type": "number",
            "label": "Gating MA Period",
            "default": 21,
            "min": 2,
            "max": 500,
            "step": 1,
        },
    }

    # ==========================================================
    # INDICATORS — tell engine what to precompute
    # ==========================================================
    def build_indicators(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        specs = []

        # Entry MA 1 (always)
        specs.append({
            "key": "ma1",
            "kind": params["ma1_type"],
            "period": int(params["ma1_period"]),
            "source": "close",
        })

        # Entry MA 2 (optional)
        if params.get("ma2_enabled", False):
            specs.append({
                "key": "ma2",
                "kind": params["ma2_type"],
                "period": int(params["ma2_period"]),
                "source": "close",
            })

        # SL MA (for ma_snapshot and ma_cross modes)
        if params["sl_mode"] in ("ma_snapshot", "ma_cross"):
            specs.append({
                "key": "sl_ma",
                "kind": params["sl_ma_type"],
                "period": int(params["sl_ma_period"]),
                "source": "close",
            })

        # TP MA (for ma_cross mode)
        if params["tp_mode"] == "ma_cross":
            specs.append({
                "key": "tp_ma",
                "kind": params["tp_ma_type"],
                "period": int(params["tp_ma_period"]),
                "source": "close",
            })

        # Gating MA
        if params.get("gating_enabled", False):
            specs.append({
                "key": "gating_ma",
                "kind": params["gating_ma_type"],
                "period": int(params["gating_ma_period"]),
                "source": "close",
            })

        return specs

    # ==========================================================
    # INIT — set up legacy state tracking
    # ==========================================================
    def on_init(self, ctx: Any) -> None:
        s = ctx.state
        # 2-bar confirmation counters (above_all_mas mode)
        s["bc"] = 0
        s["bc2"] = 0
        # Regime tracking: "neutral" | "above" | "below"
        s["regime"] = "neutral"
        # Gating locks
        s["long_locked"] = False
        s["short_locked"] = False

    # ==========================================================
    # MAIN SIGNAL LOGIC — legacy-exact replication
    # ==========================================================
    def on_bar(self, ctx: Any) -> Optional[Dict[str, Any]]:
        p = ctx.params
        s = ctx.state
        ind = ctx.indicators
        bar = ctx.bar
        i = ctx.index

        c = float(bar["close"])
        o = float(bar["open"])
        bull = c > o
        bear = c < o

        # ── Gather entry MA values ───────────────────────────
        ma_vals = []
        ma1 = ind.get("ma1")
        if ma1 is not None:
            ma_vals.append(ma1)

        ma2 = None
        if p.get("ma2_enabled", False):
            ma2 = ind.get("ma2")
            if ma2 is not None:
                ma_vals.append(ma2)

        sl_ma_val = ind.get("sl_ma")
        tp_ma_val = ind.get("tp_ma")
        gating_val = ind.get("gating_ma")

        # ── Gating unlock (legacy: checked every bar) ────────
        if p.get("gating_enabled", False) and gating_val is not None:
            if s["long_locked"] and c < gating_val:
                s["long_locked"] = False
            if s["short_locked"] and c > gating_val:
                s["short_locked"] = False

        # ══════════════════════════════════════════════════════
        # IN POSITION: check strategy-level exits
        #
        # SL/TP hit checking is done by the ENGINE (_check_exit).
        # We only handle MA CROSS exits here, because Legacy's
        # engine checks MA crosses in _check_exit and the
        # strategy engine also checks them via engine_sl_ma_key /
        # engine_tp_ma_key stored on the trade.
        #
        # HOWEVER: for MA cross SL/TP, we ALSO set engine keys
        # at entry time, so the engine handles it automatically.
        # This means we can just HOLD here — the engine does
        # the MA cross exit check for us.
        #
        # The only case we need strategy-level CLOSE is if we
        # want to exit for a reason the engine doesn't know about.
        # For Legacy replication, the engine handles everything.
        # ══════════════════════════════════════════════════════
        if ctx.position.has_position:
            # Nothing to do — engine handles SL/TP hit + MA cross exits
            return {"signal": "HOLD"}

        # ══════════════════════════════════════════════════════
        # FLAT: signal generation (legacy-exact)
        # ══════════════════════════════════════════════════════

        # Skip if any entry MA not ready
        if not ma_vals or any(v is None for v in ma_vals):
            s["bc"] = 0
            s["bc2"] = 0
            return None

        ab = all(c > v for v in ma_vals)  # close above ALL MAs
        bl = all(c < v for v in ma_vals)  # close below ALL MAs

        # ── Regime tracking (legacy-exact) ────────────────────
        if s["regime"] == "above" and not ab:
            s["regime"] = "neutral"
            s["bc"] = 0
        elif s["regime"] == "below" and not bl:
            s["regime"] = "neutral"
            s["bc2"] = 0

        sig = None
        entry_mode = p["entry_mode"]

        # ── above_all_mas: 2-bar confirmation ─────────────────
        if entry_mode == "above_all_mas":
            # Long signal
            if s["regime"] != "below":
                if ab and bull:
                    s["bc"] += 1
                else:
                    s["bc"] = 0
                if s["bc"] >= 2:
                    sig = "BUY"
                    s["regime"] = "above"
                    s["bc"] = 0

            # Short signal (only if no long signal fired)
            if sig is None and s["regime"] != "above":
                if bl and bear:
                    s["bc2"] += 1
                else:
                    s["bc2"] = 0
                if s["bc2"] >= 2:
                    sig = "SELL"
                    s["regime"] = "below"
                    s["bc2"] = 0

        # ── ma_cross: fast/slow crossover ─────────────────────
        elif entry_mode == "ma_cross" and len(ma_vals) >= 2 and i > 0:
            prev_ma1 = None
            prev_ma2 = None
            ma1_series = ctx.ind_series.get("ma1")
            ma2_series = ctx.ind_series.get("ma2")
            if ma1_series and i - 1 < len(ma1_series):
                prev_ma1 = ma1_series[i - 1]
            if ma2_series and i - 1 < len(ma2_series):
                prev_ma2 = ma2_series[i - 1]

            if None not in (ma1, ma2, prev_ma1, prev_ma2):
                if prev_ma1 <= prev_ma2 and ma1 > ma2:
                    sig = "BUY"
                elif prev_ma1 >= prev_ma2 and ma1 < ma2:
                    sig = "SELL"

        # ── trend_filter: close vs longest MA crossover ───────
        elif entry_mode == "trend_filter" and i > 0:
            # Longest MA = last in ma_vals list
            # Legacy uses mv[-1] which is the last MA
            longest_key = "ma2" if p.get("ma2_enabled", False) else "ma1"
            lm = ma_vals[-1]  # current bar's longest MA

            longest_series = ctx.ind_series.get(longest_key)
            prev_lm = None
            if longest_series and i - 1 < len(longest_series):
                prev_lm = longest_series[i - 1]

            prev_c = float(ctx.bars[i - 1]["close"]) if i > 0 else c

            if lm is not None and prev_lm is not None:
                if prev_c <= prev_lm and c > lm and bull:
                    sig = "BUY"
                elif prev_c >= prev_lm and c < lm and bear:
                    sig = "SELL"

        # ── Candle direction confirmation (legacy-exact) ──────
        if p.get("require_candle_confirm", True):
            if sig == "BUY" and not bull:
                sig = None
            if sig == "SELL" and not bear:
                sig = None

        # ── Gating filter (legacy-exact) ──────────────────────
        if sig == "BUY" and p.get("gating_enabled", False) and s["long_locked"]:
            sig = None
        if sig == "SELL" and p.get("gating_enabled", False) and s["short_locked"]:
            sig = None

        # ── No signal ────────────────────────────────────────
        if sig is None:
            return None

        # ══════════════════════════════════════════════════════
        # BUILD SIGNAL with engine-computed SL/TP
        #
        # The strategy tells the ENGINE how to compute SL/TP
        # at fill time (next bar's open). This matches Legacy
        # exactly because Legacy also computes SL/TP at fill.
        # ══════════════════════════════════════════════════════
        result = {"signal": sig}

        # ── SL ────────────────────────────────────────────────
        sl_mode = p["sl_mode"]

        if sl_mode == "fixed":
            result["sl_mode"] = "fixed"
            result["sl_pts"] = float(p.get("sl_fixed_pts", 8))

        elif sl_mode == "ma_snapshot":
            # Legacy uses prev_sl_ma_val which = signal bar's SL MA
            # That's the current bar's SL MA value (we're on the signal bar)
            if sl_ma_val is not None:
                result["sl_mode"] = "ma_snapshot"
                result["sl_ma_val"] = sl_ma_val
            else:
                # No SL MA available — skip this trade
                # (Legacy would also skip: risk=None → valid_entry=False)
                return None

        elif sl_mode == "ma_cross":
            # MA cross SL: use MA snapshot for initial risk calculation,
            # AND tell engine to check MA cross on each bar for exit
            if sl_ma_val is not None:
                result["sl_mode"] = "ma_snapshot"
                result["sl_ma_val"] = sl_ma_val
                # Engine will check this MA series each bar for cross exit
                result["engine_sl_ma_key"] = "sl_ma"
            else:
                return None

        # ── TP ────────────────────────────────────────────────
        tp_mode = p["tp_mode"]

        if tp_mode == "r_multiple":
            result["tp_mode"] = "r_multiple"
            result["tp_r"] = float(p.get("tp_r", 2))

        elif tp_mode == "ma_cross":
            # No fixed TP level — engine checks MA cross for exit
            result["engine_tp_ma_key"] = "tp_ma"

        # ── Gating: set lock after trade opens ────────────────
        # Legacy locks direction AFTER trade closes, not opens.
        # The engine doesn't have gating — we handle it in on_bar.
        # We just need to set the lock when we know a trade closed.
        #
        # But wait — we can't set the lock here because the trade
        # hasn't happened yet (it's a pending signal). We need to
        # check in the NEXT on_bar call whether our last trade
        # was a win/loss and set the lock.
        #
        # Actually, Legacy sets the lock unconditionally after ANY
        # trade close in the direction. Let's check ctx.trades to
        # see if the last trade closed and lock accordingly.
        self._update_gating_locks(ctx)

        return result

    # ==========================================================
    # GATING LOCK MANAGEMENT
    # ==========================================================
    def _update_gating_locks(self, ctx: Any) -> None:
        """
        Legacy sets gating lock after EVERY trade close.
        We check if a new trade appeared in ctx.trades since last check.
        """
        if not ctx.params.get("gating_enabled", False):
            return

        s = ctx.state
        trades = ctx.trades
        last_checked = s.get("_gating_last_trade_count", 0)

        if len(trades) > last_checked:
            # New trades closed since last check
            for t in trades[last_checked:]:
                if t["direction"] == "long":
                    s["long_locked"] = True
                elif t["direction"] == "short":
                    s["short_locked"] = True

        s["_gating_last_trade_count"] = len(trades)
```

---

## FILE: `js/backtest.js`

```javascript
/* ═══════════════════════════════════════════════════════════════════
 backtest.js — Full visualization dashboard
 Pure Canvas 2D — zero external chart dependencies

 v3 — Stats come 100% from Python backend (stats_engine.py).
      JS only DISPLAYS stats, never recomputes KPI values.
      Fallback analytics kept ONLY for chart data (rolling, scatter, etc.)
      when backend analytics are incomplete.
      Throttled rendering, lower point caps, reduced DOM churn.

 v4 — Currency conversion via CurrencyDisplay.format() at render time.
      Trade cap handling (total_trade_count, trades_truncated).
      All dollar values tagged with data-raw-usd for live refresh.
 ═══════════════════════════════════════════════════════════════════ */
(function () {
  var API = 'http://localhost:5000/api/backtest';
  var API_STREAM = 'http://localhost:5000/api/backtest/stream';

  // ── Design tokens ─────────────────────────────────────────────────
  var T = {
    bg: '#080b0f',
    bg2: '#0d1117',
    bg3: '#111820',
    border: '#1e2a38',
    border2: '#243040',
    text: '#c8d8e8',
    dim: '#4a6070',
    mute: '#2a3a4a',
    accent: '#00e5ff',
    accent2: '#0095a8',
    bull: '#00e676',
    bull2: '#00a352',
    bear: '#ff3d5a',
    bear2: '#b02040',
    mono: "'Space Mono', monospace",
  };

  var _lastRenderData = null;
  var _lastConfig = null;

  // ── DOM helpers ───────────────────────────────────────────────────
  function $(id) { return document.getElementById(id); }
  function setDisplay(el, val) { if (el) el.style.display = val; }

  // ✅ FIX: once results have rendered, do NOT allow late progress updates
  // to re-show progressWrap and hide the dashboard.
  var __btHasRenderedResult = false;

  // ── Performance caps (lowered for speed) ──────────────────────────
  var MAX_LINE_POINTS = 800;
  var MAX_BAR_POINTS = 200;
  var MAX_SCATTER_POINTS = 600;
  var MAX_MC_PATHS_VISUAL = 40;
  var MAX_MC_PATH_POINTS = 400;

  // ════════════════════════════════════════════════════════════════════
  // GRAPH STEP (downsample for visualization only)
  // ════════════════════════════════════════════════════════════════════
  var GRAPH_STEP = 1;
  var graphStepEl = document.getElementById('bt_graphStep');
  if (graphStepEl) {
    graphStepEl.addEventListener('change', function () {
      GRAPH_STEP = parseInt(this.value, 10) || 1;
      if (_lastRenderData) renderAllCharts(_lastRenderData.data, _lastRenderData.analytics);
    });
  }

  function sampleData(arr) {
    if (!Array.isArray(arr) || GRAPH_STEP <= 1) return arr || [];
    return arr.filter(function (_, i) { return i % GRAPH_STEP === 0; });
  }

  // ════════════════════════════════════════════════════════════════════
  // PER-CANVAS VIEWPORT SYSTEM (zoom + pan)
  // ════════════════════════════════════════════════════════════════════
  var _viewports = {};
  var _chartCache = {};
  var _interactionAttached = {};

  function getVP(id) {
    if (!_viewports[id]) _viewports[id] = { s: 0, e: 1 };
    return _viewports[id];
  }

  function resetAllVPs() {
    _viewports = {};
    _chartCache = {};
  }

  function sliceByVP(data, id) {
    if (!data || !data.length) return data || [];
    var vp = getVP(id);
    var len = data.length;
    var si = Math.floor(vp.s * len);
    var ei = Math.ceil(vp.e * len);
    si = Math.max(0, si);
    ei = Math.min(len, ei);
    if (ei <= si) ei = si + 1;
    return data.slice(si, ei);
  }

  function reRenderSingle(id) {
    var cached = _chartCache[id];
    if (!cached) return;
    var c = cached;
    if (c.type === 'line') drawLineChart(id, c.rawData, c.opts, true);
    else if (c.type === 'bar') drawBarChart(id, c.labels, c.values, c.opts, true);
    else if (c.type === 'hist') drawHistogram(id, c.hist, c.opts, true);
    else if (c.type === 'scatter') drawScatter(id, c.points, c.xKey, c.yKey, c.opts, true);
    else if (c.type === 'mc') drawMcPaths(id, c.paths, true);
  }

  function attachInteraction(id) {
    if (_interactionAttached[id]) return;
    var cv = document.getElementById(id);
    if (!cv) return;

    _interactionAttached[id] = true;
    cv.classList.add('bt-canvas-interactive');

    cv.addEventListener('wheel', function (e) {
      e.preventDefault();
      var vp = getVP(id);
      var rect = cv.getBoundingClientRect();
      var mx = (e.clientX - rect.left) / rect.width;
      var span = vp.e - vp.s;
      var factor = e.deltaY < 0 ? 0.85 : 1.18;
      var newSpan = Math.min(1, Math.max(0.005, span * factor));
      var center = vp.s + mx * span;
      var ns = center - mx * newSpan;
      var ne = center + (1 - mx) * newSpan;
      if (ns < 0) { ne -= ns; ns = 0; }
      if (ne > 1) { ns -= (ne - 1); ne = 1; }
      vp.s = Math.max(0, ns);
      vp.e = Math.min(1, ne);
      reRenderSingle(id);
    }, { passive: false });

    var dragging = false, lastX = 0;
    cv.addEventListener('mousedown', function (e) {
      dragging = true;
      lastX = e.clientX;
      e.preventDefault();
    });

    window.addEventListener('mousemove', function (e) {
      if (!dragging) return;
      var vp = getVP(id);
      var rect = cv.getBoundingClientRect();
      var dx = (e.clientX - lastX) / rect.width;
      var span = vp.e - vp.s;
      var shift = -dx * span;
      var ns = vp.s + shift;
      var ne = vp.e + shift;
      if (ns < 0) { ne -= ns; ns = 0; }
      if (ne > 1) { ns -= (ne - 1); ne = 1; }
      vp.s = Math.max(0, ns);
      vp.e = Math.min(1, ne);
      lastX = e.clientX;
      reRenderSingle(id);
    });

    window.addEventListener('mouseup', function () { dragging = false; });

    cv.addEventListener('dblclick', function () {
      _viewports[id] = { s: 0, e: 1 };
      reRenderSingle(id);
    });

    var touchStartDist = 0, touchStartSpan = 0, touchStartCenter = 0;
    var singleTouchX = 0, isTouching = false;

    cv.addEventListener('touchstart', function (e) {
      if (e.touches.length === 2) {
        e.preventDefault();
        var t0 = e.touches[0], t1 = e.touches[1];
        touchStartDist = Math.abs(t0.clientX - t1.clientX);
        var vp = getVP(id);
        touchStartSpan = vp.e - vp.s;
        touchStartCenter = vp.s + touchStartSpan / 2;
      } else if (e.touches.length === 1) {
        isTouching = true;
        singleTouchX = e.touches[0].clientX;
      }
    }, { passive: false });

    cv.addEventListener('touchmove', function (e) {
      if (e.touches.length === 2) {
        e.preventDefault();
        var t0 = e.touches[0], t1 = e.touches[1];
        var dist = Math.abs(t0.clientX - t1.clientX);
        if (touchStartDist === 0) return;
        var ratio = touchStartDist / dist;
        var newSpan = Math.min(1, Math.max(0.005, touchStartSpan * ratio));
        var vp = getVP(id);
        vp.s = Math.max(0, touchStartCenter - newSpan / 2);
        vp.e = Math.min(1, vp.s + newSpan);
        reRenderSingle(id);
      } else if (e.touches.length === 1 && isTouching) {
        e.preventDefault();
        var vp = getVP(id);
        var rect = cv.getBoundingClientRect();
        var dx = (e.touches[0].clientX - singleTouchX) / rect.width;
        var span = vp.e - vp.s;
        var shift = -dx * span;
        var ns = vp.s + shift;
        var ne = vp.e + shift;
        if (ns < 0) { ne -= ns; ns = 0; }
        if (ne > 1) { ns -= (ne - 1); ne = 1; }
        vp.s = Math.max(0, ns);
        vp.e = Math.min(1, ne);
        singleTouchX = e.touches[0].clientX;
        reRenderSingle(id);
      }
    }, { passive: false });

    cv.addEventListener('touchend', function () { isTouching = false; });
  }

  // ════════════════════════════════════════════════════════════════════
  // TAB SWITCHING
  // ════════════════════════════════════════════════════════════════════
  document.querySelectorAll('.tab-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var t = btn.dataset.tab;
      document.querySelectorAll('.tab-btn').forEach(function (b) { b.classList.remove('active'); });
      btn.classList.add('active');

      var isChart = t === 'chart';
      document.getElementById('tabChart').style.display = isChart ? '' : 'none';
      document.getElementById('tabChart').classList.toggle('active', isChart);
      document.getElementById('tabBacktest').style.display = isChart ? 'none' : '';
      document.getElementById('tabBacktest').classList.toggle('active', !isChart);
      document.getElementById('chartHeaderRight').style.display = isChart ? '' : 'none';
      document.getElementById('backtestHeaderRight').style.display = isChart ? 'none' : '';
    });
  });

  // ════════════════════════════════════════════════════════════════════
  // MA ROW MANAGEMENT
  // ════════════════════════════════════════════════════════════════════
  var firstRow = document.getElementById('bt_ma_0');
  var maSection = firstRow ? firstRow.parentElement : null;

  function wireRemove(row) {
    var btn = row.querySelector('.bt-remove-ma');
    if (!btn) return;
    btn.addEventListener('click', function () {
      if (document.querySelectorAll('.bt-ma-row').length > 1) row.remove();
    });
  }

  if (firstRow) wireRemove(firstRow);

  var addMaBtn = document.getElementById('bt_addMa');
  if (addMaBtn && maSection) {
    addMaBtn.addEventListener('click', function () {
      var row = document.createElement('div');
      row.className = 'bt-ma-row';
      row.innerHTML =
        '<select class="bt-select bt-select-sm" data-ma-type>' +
        '<option value="EMA" selected>EMA</option><option value="HMA">HMA</option>' +
        '<option value="SMA">SMA</option><option value="WMA">WMA</option>' +
        '</select>' +
        '<input class="bt-input bt-input-sm" type="number" data-ma-period value="200" min="2" max="500"/>' +
        '<button class="bt-icon-btn bt-remove-ma" title="Remove">✕</button>';
      wireRemove(row);
      maSection.insertBefore(row, addMaBtn);
    });
  }

  // ── SL toggle ─────────────────────────────────────────────────────
  document.querySelectorAll('input[name="sl_mode"]').forEach(function (r) {
    r.addEventListener('change', function () {
      var checked = document.querySelector('input[name="sl_mode"]:checked');
      var v = checked ? checked.value : 'fixed';
      var slFixed = document.getElementById('bt_sl_fixed_wrap');
      var slMa = document.getElementById('bt_sl_ma_wrap');
      if (slFixed) slFixed.style.display = v === 'fixed' ? '' : 'none';
      if (slMa) slMa.style.display = (v === 'ma_cross' || v === 'ma_snapshot') ? '' : 'none';
    });
  });

  // ── TP toggle ─────────────────────────────────────────────────────
  document.querySelectorAll('input[name="tp_mode"]').forEach(function (r) {
    r.addEventListener('change', function () {
      var checked = document.querySelector('input[name="tp_mode"]:checked');
      var v = checked ? checked.value : 'r_multiple';
      var tpR = document.getElementById('bt_tp_r_wrap');
      var tpMa = document.getElementById('bt_tp_ma_wrap');
      if (tpR) tpR.style.display = v === 'r_multiple' ? '' : 'none';
      if (tpMa) tpMa.style.display = v === 'ma_cross' ? '' : 'none';
    });
  });

  // ── Gating toggle ─────────────────────────────────────────────────
  var gatingEnabled = document.getElementById('bt_gatingEnabled');
  if (gatingEnabled) {
    gatingEnabled.addEventListener('change', function () {
      var wrap = document.getElementById('bt_gating_wrap');
      if (wrap) wrap.style.display = this.checked ? '' : 'none';
    });
  }
  // ── Spread toggle ─────────────────────────────────────────────────
  var spreadEnabled = document.getElementById('bt_spreadEnabled');
  if (spreadEnabled) {
    spreadEnabled.addEventListener('change', function () {
      var wrap = document.getElementById('bt_spreadWrap');
      if (wrap) wrap.style.display = this.checked ? '' : 'none';
    });
  }
  // ── Sizing mode toggle ────────────────────────────────────────────
  var sizingModeEl = document.getElementById('bt_sizingMode');
  if (sizingModeEl) {
    sizingModeEl.addEventListener('change', function () {
      var mode = this.value;
      setDisplay($('bt_fixedLotWrap'), mode === 'fixed' ? '' : 'none');
      setDisplay($('bt_riskPctWrap'), mode === 'risk_pct' ? '' : 'none');
      setDisplay($('bt_riskPerTradeWrap'), mode === 'risk_per_trade' ? '' : 'none');
      updateRiskHint();
    });
  }

  function updateRiskHint() {
    var hint = document.getElementById('bt_riskHint');
    if (!hint) return;
    var cap = parseFloat((document.getElementById('bt_startingCapital') || {}).value || '10000') || 10000;
    var pct = parseFloat((document.getElementById('bt_riskPct') || {}).value || '1') || 1;
    var riskAmt = cap * (pct / 100);
    hint.textContent = '$' + cap.toLocaleString() + ' balance × ' + pct + '% = $' + riskAmt.toFixed(2) + ' risk per trade';
  }

  var riskPctEl = document.getElementById('bt_riskPct');
  if (riskPctEl) riskPctEl.addEventListener('input', updateRiskHint);
  var startCapEl = document.getElementById('bt_startingCapital');
  if (startCapEl) startCapEl.addEventListener('input', function () { updateRiskHint(); updateLotHint(); });
  updateRiskHint();

  // ── Risk Per Trade: scaling toggle + hint updates ──────────
  (function () {
    var scalingCheck = $('bt_scalingEnabled');
    var scalingWrap = $('bt_scalingWrap');
    var fixedRiskInput = $('bt_fixedRisk');
    var scalingPerInput = $('bt_scalingPer');
    var scalingRiskInput = $('bt_scalingRisk');
    var fixedRiskHint = $('bt_fixedRiskHint');
    var scalingHint = $('bt_scalingHint');
    var capInput = $('bt_startingCapital');

    function updateRptHints() {
      var cap = parseFloat((capInput || {}).value) || 10000;
      var baseRisk = parseFloat((fixedRiskInput || {}).value) || 10;
      var scalingOn = scalingCheck && scalingCheck.checked;

      if (fixedRiskHint) {
        if (scalingOn) {
          fixedRiskHint.textContent = 'Base risk ignored when scaling is ON';
        } else {
          fixedRiskHint.textContent = 'Every trade risks exactly $' + baseRisk.toFixed(2) + ' (requires SL)';
        }
      }

      if (scalingHint && scalingOn) {
        var per = parseFloat((scalingPerInput || {}).value) || 100;
        var risk = parseFloat((scalingRiskInput || {}).value) || 1;
        var computed = (cap / per) * risk;
        scalingHint.textContent = '$' + cap.toLocaleString(undefined, {minimumFractionDigits:2, maximumFractionDigits:2})
          + ' bal \u00f7 $' + per.toFixed(0)
          + ' \u00d7 $' + risk.toFixed(2)
          + ' = $' + computed.toFixed(2) + ' risk/trade';
      }
    }

    if (scalingCheck) {
      scalingCheck.addEventListener('change', function () {
        setDisplay(scalingWrap, this.checked ? '' : 'none');
        updateRptHints();
      });
    }
    if (fixedRiskInput) fixedRiskInput.addEventListener('input', updateRptHints);
    if (scalingPerInput) scalingPerInput.addEventListener('input', updateRptHints);
    if (scalingRiskInput) scalingRiskInput.addEventListener('input', updateRptHints);
    if (capInput) capInput.addEventListener('input', updateRptHints);
  })();

  // ── Slice mode toggle ─────────────────────────────────────────────
  var sliceModeEl = document.getElementById('bt_sliceMode');
  if (sliceModeEl) {
    sliceModeEl.addEventListener('change', function () {
      var mode = this.value;
      var barWrap = document.getElementById('bt_barRangeWrap');
      var dateWrap = document.getElementById('bt_dateRangeWrap');
      if (barWrap) barWrap.style.display = mode === 'bar_count' ? '' : 'none';
      if (dateWrap) dateWrap.style.display = mode === 'date_range' ? '' : 'none';
    });
  }

  // ── Dynamic range bar sizes (from /api/ranges/<symbol>) ───────────
  function populateBtRanges(symbol) {
    var sel = document.getElementById('bt_range');
    if (!sel) return;

    fetch('http://localhost:5000/api/ranges/' + encodeURIComponent(symbol || 'NQ'))
      .then(function(r) { return r.json(); })
      .then(function(ranges) {
        var prev = sel.value;
        sel.innerHTML = '';
        if (!ranges || !ranges.length) return;

        ranges.forEach(function(pt) {
          var opt = document.createElement('option');
          opt.value = String(pt);
          opt.textContent = (pt % 1 === 0 ? pt.toFixed(0) : pt.toString()) + ' pt';
          sel.appendChild(opt);
        });

        // Restore previous selection if still available
        if (ranges.indexOf(parseFloat(prev)) >= 0) {
          sel.value = prev;
        } else {
          sel.value = String(ranges[0]);
        }
      })
      .catch(function(err) {
        console.error('[BT] Failed to fetch ranges:', err);
      });
  }

  var btSymbolEl = document.getElementById('bt_symbol');
  if (btSymbolEl) {
    btSymbolEl.addEventListener('change', function() {
      populateBtRanges(this.value);
    });
  }
  populateBtRanges((btSymbolEl || {}).value || 'NQ');

  // ── R buttons ─────────────────────────────────────────────────────
  var selectedR = 2;
  document.querySelectorAll('.bt-r-btn').forEach(function (b) {
    b.addEventListener('click', function () {
      document.querySelectorAll('.bt-r-btn').forEach(function (x) { x.classList.remove('active'); });
      b.classList.add('active');
      selectedR = parseFloat(b.dataset.r || '2');
    });
  });

  // ── Lot size hint ─────────────────────────────────────────────────
  function updateLotHint() {
    var ls = parseFloat((document.getElementById('bt_lotSize') || {}).value || '1') || 1;
    var pv = parseFloat((document.getElementById('bt_pointValue') || {}).value || '1') || 1;
    var dollarsPerPt = ls * pv;
    var hint = document.getElementById('bt_lotHint');
    if (hint) hint.textContent = '1 pt = $' + dollarsPerPt.toFixed(dollarsPerPt < 0.1 ? 3 : 2);
    var pvHint = document.getElementById('bt_pvHint');
    if (pvHint) pvHint.textContent = '1 pt × ' + ls + ' lot = $' + dollarsPerPt.toFixed(2);
  }
  var lotSizeEl = document.getElementById('bt_lotSize');
  if (lotSizeEl) lotSizeEl.addEventListener('input', updateLotHint);
  var pvEl = document.getElementById('bt_pointValue');
  if (pvEl) pvEl.addEventListener('input', function() { updateLotHint(); updateDppHint(); });

  function updateDppHint() {
    var hint = document.getElementById('bt_dppHint');
    if (!hint) return;
    var pv = parseFloat((document.getElementById('bt_pointValue') || {}).value || '1') || 1;
    var dpp = parseFloat((document.getElementById('bt_dollarPerPoint') || {}).value || '1') || 1;
    var lot = parseFloat((document.getElementById('bt_lotSize') || {}).value || '1') || 1;
    var perPt = pv * dpp * lot;
    hint.textContent = '1pt move = ' + pv + ' × $' + dpp.toFixed(2) + ' × ' + lot.toFixed(2) + ' lot = $' + perPt.toFixed(perPt < 0.01 ? 4 : 2) + ' P&L';
  }

  var dppEl = document.getElementById('bt_dollarPerPoint');
  if (dppEl) dppEl.addEventListener('input', function() { updateDppHint(); updateLotHint(); });
  updateDppHint();
  updateLotHint();

  // ── Commission per lot hint ───────────────────────────────────────
  function updateCommHint() {
    var hint = $('bt_commHint');
    if (!hint) return;
    var cpl = parseFloat(($('bt_commPerLot') || {}).value || '1.25') || 0;
    var lot = parseFloat(($('bt_lotSize') || {}).value || '1') || 1;
    var total = lot * cpl;
    hint.textContent = lot.toFixed(2) + ' lot × $' + cpl.toFixed(2) + ' = $' + total.toFixed(2) + ' commission/trade';
  }
  var commPerLotEl = $('bt_commPerLot');
  if (commPerLotEl) commPerLotEl.addEventListener('input', updateCommHint);
  if (lotSizeEl) lotSizeEl.addEventListener('input', updateCommHint);
  updateCommHint();

  // ════════════════════════════════════════════════════════════════════
  // CONFIG COLLECTION (legacy/manual mode)
  // ════════════════════════════════════════════════════════════════════
  function collectConfig() {
    var mas = [];
    document.querySelectorAll('.bt-ma-row').forEach(function (row) {
      var typeEl = row.querySelector('[data-ma-type]');
      var periodEl = row.querySelector('[data-ma-period]');
      var type = typeEl ? typeEl.value : 'EMA';
      var p = parseInt(periodEl ? periodEl.value : '0', 10);
      if (!isNaN(p) && p >= 2) mas.push({ type: type, period: p });
    });

    var slMode = document.querySelector('input[name="sl_mode"]:checked');
    var tpMode = document.querySelector('input[name="tp_mode"]:checked');

    var config = {
      symbol: (document.getElementById('bt_symbol') || {}).value || 'NQ',
      range: parseInt((document.getElementById('bt_range') || {}).value || '10', 10),
      bar_range: parseInt((document.getElementById('bt_barRange') || {}).value || '1000', 10),
      starting_capital: parseFloat((document.getElementById('bt_startingCapital') || {}).value || '10000') || 10000,
      lot_size: parseFloat((document.getElementById('bt_lotSize') || {}).value || '1.0') || 1.0,
      point_value: parseFloat((document.getElementById('bt_pointValue') || {}).value || '1') || 1.0,
      dollar_per_point: parseFloat((document.getElementById('bt_dollarPerPoint') || {}).value || '1') || 1.0,
      sizing_mode: (document.getElementById('bt_sizingMode') || {}).value || 'fixed',
      risk_pct: parseFloat((document.getElementById('bt_riskPct') || {}).value || '1.0') || 1.0,
      fixed_risk: parseFloat((document.getElementById('bt_fixedRisk') || {}).value || '10') || 10,
      scaling_enabled: (document.getElementById('bt_scalingEnabled') || {}).checked || false,
      scaling_per: parseFloat((document.getElementById('bt_scalingPer') || {}).value || '100') || 100,
      scaling_risk: parseFloat((document.getElementById('bt_scalingRisk') || {}).value || '1') || 1,
      mas: mas,
      entry: (document.getElementById('bt_entry') || {}).value || 'above_all_mas',
      require_candle_confirm: !!((document.getElementById('bt_candleConfirm') || {}).checked),
      sl: {
        mode: slMode ? slMode.value : 'fixed',
        fixed_pts: parseFloat((document.getElementById('bt_sl_fixed') || {}).value || '8') || 8,
        ma_type: (document.getElementById('bt_sl_ma_type') || {}).value || 'EMA',
        ma_length: parseInt((document.getElementById('bt_sl_ma_length') || {}).value || '50', 10) || 50,
      },
      tp: {
        mode: tpMode ? tpMode.value : 'r_multiple',
        r_multiple: selectedR,
        ma_type: (document.getElementById('bt_tp_ma_type') || {}).value || 'EMA',
        ma_length: parseInt((document.getElementById('bt_tp_ma_length') || {}).value || '9', 10) || 9,
      },
      gating: {
        enabled: !!((document.getElementById('bt_gatingEnabled') || {}).checked),
        ma_type: (document.getElementById('bt_gating_ma_type') || {}).value || 'HMA',
        ma_length: parseInt((document.getElementById('bt_gating_ma_length') || {}).value || '21', 10) || 21,
      },
      commission_per_lot: parseFloat(($('bt_commPerLot') || {}).value || '1.25') || 0,
      spread: {
        enabled: !!((document.getElementById('bt_spreadEnabled') || {}).checked),
        min: parseFloat((document.getElementById('bt_spreadMin') || {}).value || '0') || 0,
        max: parseFloat((document.getElementById('bt_spreadMax') || {}).value || '0') || 0,
        seed: parseInt((document.getElementById('bt_spreadSeed') || {}).value || '0', 10) || 0,
      },
      ambiguous_bar_mode: (document.getElementById('bt_ambiguousMode') || {}).value || 'conservative',
      monte_carlo_runs: parseInt((document.getElementById('bt_mcRuns') || {}).value || '0', 10) || 0
    };

    // ── Date range support ──────────────────────────────────────
    var sliceMode = (document.getElementById('bt_sliceMode') || {}).value || 'bar_count';
    if (sliceMode === 'date_range') {
      config.start_date = (document.getElementById('bt_startDate') || {}).value || '';
      config.end_date = (document.getElementById('bt_endDate') || {}).value || '';
      config.bar_range = 0;
    } else {
      config.start_date = '';
      config.end_date = '';
    }

    return config;
  }

  // ════════════════════════════════════════════════════════════════════
  // PROGRESS
  // ════════════════════════════════════════════════════════════════════
  var STEPS = ['step_load', 'step_run', 'step_stats', 'step_charts', 'step_done'];
  var STEP_LABELS = {
    step_load: 'Loading data…',
    step_run: 'Running backtest…',
    step_stats: 'Computing statistics…',
    step_charts: 'Building charts…',
    step_done: 'Complete ✓'
  };
  var STEP_PCTS = { step_load: 15, step_run: 45, step_stats: 65, step_charts: 85, step_done: 100 };

  function setStep(id) {
    STEPS.forEach(function (s) {
      var el = document.getElementById(s);
      if (!el) return;
      el.classList.remove('active', 'done');
      var si = STEPS.indexOf(s), ci = STEPS.indexOf(id);
      if (si < ci) el.classList.add('done');
      if (si === ci) el.classList.add('active');
    });
    var label = document.getElementById('bt_progressLabel');
    var bar = document.getElementById('bt_progressBar');
    var pct = document.getElementById('bt_progressPct');
    var p = STEP_PCTS[id] || 0;
    if (label) label.textContent = STEP_LABELS[id] || '…';
    if (bar) bar.style.width = p + '%';
    if (pct) pct.textContent = p + '%';
  }

  function showProgress() {
    var wrap = document.getElementById('bt_progressWrap');
    var empty = document.getElementById('bt_empty');
    var dash = document.getElementById('bt_dashboard');
    var ls = document.getElementById('bt_liveStats');
    if (wrap) wrap.style.display = '';
    if (empty) empty.style.display = 'none';
    if (dash) dash.style.display = 'none';
    if (ls) ls.style.display = '';
  }

  function hideProgress() {
    var wrap = document.getElementById('bt_progressWrap');
    var ls = document.getElementById('bt_liveStats');
    if (wrap) wrap.style.display = 'none';
    if (ls) ls.style.display = 'none';
  }

  function delay(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  // ── Throttled live progress update (max 8fps) ─────────────────────
  var _lastLiveUpdate = 0;
  function updateLiveProgress(msg) {
    var now = Date.now();
    if (now - _lastLiveUpdate < 125 && msg.pct < 99) return; // 8fps max
    _lastLiveUpdate = now;

    var pct = msg.pct != null ? msg.pct : 0;
    var barEl = document.getElementById('bt_progressBar');
    var pctEl = document.getElementById('bt_progressPct');
    var lbl = document.getElementById('bt_progressLabel');

    if (barEl) barEl.style.width = pct + '%';
    if (pctEl) pctEl.textContent = Math.round(pct) + '%';
    if (lbl) lbl.textContent = msg.label || ('Bar ' + (msg.bar || 0) + ' / ' + (msg.total || 0));

    var eqEl = document.getElementById('bt_liveEquity');
    var ddEl = document.getElementById('bt_liveDD');
    var otEl = document.getElementById('bt_liveOpen');
    var lpEl = document.getElementById('bt_lastPnl');

    if (eqEl) {
      var eq = Number(msg.equity || 0);
      eqEl.textContent = '$' + eq.toFixed(2);
      eqEl.className = 'bt-live-value' + (eq >= 0 ? ' bull' : ' bear');
    }
    if (ddEl) {
      var dd = Number(msg.drawdown || 0);
      ddEl.textContent = '$' + dd.toFixed(2);
      ddEl.className = 'bt-live-value bear';
    }
    if (otEl) {
      if (msg.open_trade) {
        var ot = msg.open_trade;
        otEl.textContent = String(ot.direction || '').toUpperCase() + ' @ ' + Number(ot.entry_price || ot.entry || 0).toFixed(2);
        otEl.className = 'bt-live-value ' + (ot.direction === 'long' ? 'bull' : 'bear');
      } else {
        otEl.textContent = '—'; otEl.className = 'bt-live-value';
      }
    }
    if (lpEl) {
      if (msg.last_closed_pnl != null) {
        var v = Number(msg.last_closed_pnl || 0);
        lpEl.textContent = (v >= 0 ? '+' : '') + '$' + v.toFixed(2);
        lpEl.className = 'bt-live-value' + (v >= 0 ? ' bull' : ' bear');
      } else {
        lpEl.textContent = '—'; lpEl.className = 'bt-live-value';
      }
    }
  }

  // ════════════════════════════════════════════════════════════════════
  // DATA HELPERS
  // ════════════════════════════════════════════════════════════════════
  function arrMin(arr) {
    if (!arr || !arr.length) return 0;
    var m = Infinity;
    for (var i = 0; i < arr.length; i++) {
      var v = arr[i];
      if (v != null && isFinite(v) && v < m) m = v;
    }
    return m === Infinity ? 0 : m;
  }

  function arrMax(arr) {
    if (!arr || !arr.length) return 0;
    var m = -Infinity;
    for (var i = 0; i < arr.length; i++) {
      var v = arr[i];
      if (v != null && isFinite(v) && v > m) m = v;
    }
    return m === -Infinity ? 0 : m;
  }

  function downsampleLineSeries(data, maxPts) {
    if (!data || data.length <= maxPts) return data || [];
    var bucketSize = data.length / maxPts;
    var result = [data[0]];
    for (var b = 1; b < maxPts - 1; b++) {
      var start = Math.floor(b * bucketSize);
      var end = Math.floor((b + 1) * bucketSize);
      var minV = Infinity, maxV = -Infinity, minI = start, maxI = start;
      for (var i = start; i < end && i < data.length; i++) {
        var v = data[i];
        if (v == null || !isFinite(v)) continue;
        if (v < minV) { minV = v; minI = i; }
        if (v > maxV) { maxV = v; maxI = i; }
      }
      if (minV !== Infinity) {
        if (minI < maxI) { result.push(data[minI]); result.push(data[maxI]); }
        else { result.push(data[maxI]); result.push(data[minI]); }
      }
    }
    result.push(data[data.length - 1]);
    return result;
  }

  function limitBarSeries(labels, values, maxPts) {
    labels = labels || []; values = values || [];
    if (labels.length <= maxPts) return { labels: labels, values: values };
    var step = Math.ceil(labels.length / maxPts);
    var outL = [], outV = [];
    for (var i = 0; i < labels.length; i += step) { outL.push(labels[i]); outV.push(values[i]); }
    return { labels: outL, values: outV };
  }

  function limitScatter(points, maxPts) {
    points = points || [];
    if (points.length <= maxPts) return points;
    var step = Math.ceil(points.length / maxPts);
    var out = [];
    for (var i = 0; i < points.length; i += step) out.push(points[i]);
    return out;
  }

  function getCanvas(id) {
    var cv = document.getElementById(id);
    if (!cv) return null;
    var dpr = window.devicePixelRatio || 1;
    var w = cv.parentElement ? (cv.parentElement.clientWidth - 2) : 0;
    if (w < 50) {
      var rect = cv.getBoundingClientRect();
      w = rect.width > 50 ? rect.width - 2 : 400;
    }
    w = Math.max(w, 50);
    var h = cv.clientHeight;
    if (h < 30) {
      var rect2 = cv.getBoundingClientRect();
      h = rect2.height > 30 ? rect2.height : (cv.classList.contains('bt-canvas-sm') ? 180 : 260);
    }
    h = Math.max(h, 30);
    cv.width = w * dpr; cv.height = h * dpr;
    cv.style.width = w + 'px'; cv.style.height = h + 'px';
    var ctx = cv.getContext('2d');
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.scale(dpr, dpr);
    ctx.fillStyle = T.bg2;
    ctx.fillRect(0, 0, w, h);
    return { ctx: ctx, w: w, h: h };
  }

  function labelFont(size) { return '700 ' + (size || 9) + 'px ' + T.mono; }

  // ════════════════════════════════════════════════════════════════════
  // DRAWERS
  // ════════════════════════════════════════════════════════════════════
  function drawLineChart(id, rawData, opts, isRerender) {
    rawData = Array.isArray(rawData) ? rawData.filter(function (v) { return v != null && isFinite(v); }) : [];
    if (!rawData.length) return;
    opts = opts || {};
    if (!isRerender) _chartCache[id] = { type: 'line', rawData: rawData, opts: opts };

    var sampled = sampleData(downsampleLineSeries(rawData, MAX_LINE_POINTS));
    var totalLen = sampled.length;
    var data = sliceByVP(sampled, id);
    if (!data.length) return;

    var g = getCanvas(id);
    if (!g) return;
    var ctx = g.ctx, w = g.w, h = g.h;
    var pad = { t: 20, r: 16, b: 32, l: 68 };
    var pw = w - pad.l - pad.r, ph = h - pad.t - pad.b;
    if (pw < 10 || ph < 10) return;

    var mn = arrMin(data), mx = arrMax(data);
    if (mn === mx) { mn -= 1; mx += 1; }
    var range = mx - mn;
    var lineCol = opts.color || T.bull;
    var vp = getVP(id);
    var vpStartIdx = Math.floor(vp.s * totalLen);

    if (opts.refVal != null && isFinite(opts.refVal) && opts.refVal >= mn && opts.refVal <= mx) {
      var ry = pad.t + ph * (1 - (opts.refVal - mn) / range);
      ctx.strokeStyle = T.border2; ctx.lineWidth = 1;
      ctx.setLineDash([3, 5]);
      ctx.beginPath(); ctx.moveTo(pad.l, ry); ctx.lineTo(w - pad.r, ry); ctx.stroke();
      ctx.setLineDash([]);
      ctx.font = labelFont(7); ctx.fillStyle = T.dim; ctx.textAlign = 'left';
      ctx.fillText(opts.refLabel || String(opts.refVal.toFixed(0)), pad.l + 4, ry - 3);
    }

    if (opts.zeroLine && mn < 0 && mx > 0) {
      var zy = pad.t + ph * (1 - (0 - mn) / range);
      ctx.strokeStyle = T.border2; ctx.lineWidth = 1;
      ctx.setLineDash([3, 4]);
      ctx.beginPath(); ctx.moveTo(pad.l, zy); ctx.lineTo(w - pad.r, zy); ctx.stroke();
      ctx.setLineDash([]);
    }

    ctx.strokeStyle = T.border; ctx.lineWidth = 0.5;
    for (var i = 0; i <= 4; i++) {
      var gy = pad.t + ph * (i / 4);
      ctx.beginPath(); ctx.moveTo(pad.l, gy); ctx.lineTo(w - pad.r, gy); ctx.stroke();
      var gv = mx - range * (i / 4);
      ctx.font = labelFont(8); ctx.fillStyle = T.dim; ctx.textAlign = 'right';
      ctx.fillText(Math.abs(gv) >= 1000 ? (gv / 1000).toFixed(1) + 'k' : gv.toFixed(1), pad.l - 5, gy + 3);
    }

    var gradient = ctx.createLinearGradient(0, pad.t, 0, h - pad.b);
    gradient.addColorStop(0, lineCol + '44');
    gradient.addColorStop(1, lineCol + '00');

    ctx.beginPath();
    var first = true;
    data.forEach(function (v, i) {
      var x = pad.l + (i / Math.max(1, data.length - 1)) * pw;
      var y = pad.t + ph * (1 - (v - mn) / range);
      if (first) { ctx.moveTo(x, y); first = false; } else ctx.lineTo(x, y);
    });
    ctx.lineTo(pad.l + pw, pad.t + ph);
    ctx.lineTo(pad.l, pad.t + ph);
    ctx.closePath();
    ctx.fillStyle = gradient; ctx.fill();

    ctx.beginPath(); first = true;
    data.forEach(function (v, i) {
      var x = pad.l + (i / Math.max(1, data.length - 1)) * pw;
      var y = pad.t + ph * (1 - (v - mn) / range);
      if (first) { ctx.moveTo(x, y); first = false; } else ctx.lineTo(x, y);
    });
    ctx.strokeStyle = lineCol; ctx.lineWidth = 1.5; ctx.stroke();

    ctx.font = labelFont(8); ctx.fillStyle = T.dim; ctx.textAlign = 'center';
    [0, 0.25, 0.5, 0.75, 1].forEach(function (t) {
      var idx = vpStartIdx + Math.floor(t * Math.max(0, data.length - 1));
      ctx.fillText(String(idx), pad.l + t * pw, h - pad.b + 14);
    });

    attachInteraction(id);
  }

  function drawBarChart(id, labels, values, opts, isRerender) {
    labels = Array.isArray(labels) ? labels : [];
    values = Array.isArray(values) ? values : [];
    if (!labels.length || !values.length) return;

    var limited = limitBarSeries(labels, values, MAX_BAR_POINTS);
    labels = limited.labels; values = limited.values;
    opts = opts || {};
    if (!isRerender) _chartCache[id] = { type: 'bar', labels: labels, values: values, opts: opts };

    var vp = getVP(id);
    var len = labels.length;
    var si = Math.floor(vp.s * len), ei = Math.ceil(vp.e * len);
    si = Math.max(0, si); ei = Math.min(len, ei);
    if (ei <= si) ei = si + 1;

    var vLabels = labels.slice(si, ei), vValues = values.slice(si, ei);
    if (!vLabels.length || !vValues.length) return;

    var g = getCanvas(id);
    if (!g) return;
    var ctx = g.ctx, w = g.w, h = g.h;
    var pad = { t: 16, r: 12, b: 44, l: 58 };
    var pw = w - pad.l - pad.r, ph = h - pad.t - pad.b;
    if (pw < 10 || ph < 10) return;

    var mn = 0, mx = 0;
    for (var vi = 0; vi < vValues.length; vi++) {
      var vv = Number(vValues[vi] || 0);
      if (vv < mn) mn = vv; if (vv > mx) mx = vv;
    }
    if (mn === mx) { mn -= 1; mx += 1; }
    var range = mx - mn;
    var slotW = pw / Math.max(1, vLabels.length);
    var barW = slotW * 0.7, barOff = slotW * 0.15;

    ctx.strokeStyle = T.border; ctx.lineWidth = 0.5;
    for (var i = 0; i <= 4; i++) {
      var y = pad.t + ph * (i / 4);
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke();
      var v = mx - range * (i / 4);
      ctx.font = labelFont(8); ctx.fillStyle = T.dim; ctx.textAlign = 'right';
      ctx.fillText(Math.abs(v) >= 1000 ? (v / 1000).toFixed(1) + 'k' : v.toFixed(1), pad.l - 4, y + 3);
    }

    var zy = pad.t + ph * (1 - (0 - mn) / range);
    ctx.strokeStyle = T.border2; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(pad.l, zy); ctx.lineTo(w - pad.r, zy); ctx.stroke();

    vLabels.forEach(function (lbl, i) {
      var v = Number(vValues[i] || 0);
      var x = pad.l + i * slotW + barOff;
      var barH = Math.abs(v / range) * ph;
      var y = v >= 0 ? zy - barH : zy;
      var col = opts.colorFn ? opts.colorFn(v, i + si) : (v >= 0 ? T.bull : T.bear);
      ctx.fillStyle = col + 'bb';
      ctx.fillRect(x, y, barW, Math.max(barH, 1));
      ctx.strokeStyle = col; ctx.lineWidth = 0.5;
      ctx.strokeRect(x, y, barW, Math.max(barH, 1));
      ctx.font = labelFont(7); ctx.fillStyle = T.dim; ctx.textAlign = 'center';
      ctx.fillText(String(lbl).substring(0, 8), x + barW / 2, h - pad.b + 13);
    });

    attachInteraction(id);
  }

  function drawHistogram(id, hist, opts, isRerender) {
    hist = hist || {};
    var counts = Array.isArray(hist.counts) ? hist.counts : [];
    var edges = Array.isArray(hist.edges) ? hist.edges : [];
    if (!counts.length) return;
    opts = opts || {};
    if (!isRerender) _chartCache[id] = { type: 'hist', hist: hist, opts: opts };

    var vp = getVP(id);
    var len = counts.length;
    var si = Math.floor(vp.s * len), ei = Math.ceil(vp.e * len);
    si = Math.max(0, si); ei = Math.min(len, ei);
    if (ei <= si) ei = si + 1;
    var vCounts = counts.slice(si, ei);
    var vEdges = edges.length ? edges.slice(si, ei + 1) : [];
    if (!vCounts.length) return;

    var g = getCanvas(id);
    if (!g) return;
    var ctx = g.ctx, w = g.w, h = g.h;
    var maxV = 0;
    for (var ci = 0; ci < vCounts.length; ci++) maxV = Math.max(maxV, Number(vCounts[ci] || 0));
    maxV = maxV || 1;

    var pad = { t: 16, r: 12, b: 36, l: 44 };
    var pw = w - pad.l - pad.r, ph = h - pad.t - pad.b;
    if (pw < 10 || ph < 10) return;
    var bw = pw / Math.max(1, vCounts.length);

    ctx.strokeStyle = T.border; ctx.lineWidth = 0.5;
    for (var i = 0; i <= 4; i++) {
      var y = pad.t + ph * (i / 4);
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke();
      ctx.font = labelFont(8); ctx.fillStyle = T.dim; ctx.textAlign = 'right';
      ctx.fillText(String(Math.round(maxV * (1 - i / 4))), pad.l - 4, y + 3);
    }

    vCounts.forEach(function (c, i) {
      var x = pad.l + i * bw;
      var barH = (Number(c || 0) / maxV) * ph;
      var edgeVal = vEdges.length ? Number(vEdges[i] || 0) : i;
      var col = opts.colorFn ? opts.colorFn(edgeVal) : (edgeVal >= 0 ? T.bull + '99' : T.bear + '99');
      ctx.fillStyle = col;
      ctx.fillRect(x + 1, pad.t + ph - barH, Math.max(1, bw - 2), Math.max(barH, 1));
    });

    [0, Math.floor(vCounts.length / 2), vCounts.length - 1].forEach(function (i) {
      if (i >= vCounts.length) return;
      var x = pad.l + i * bw + bw / 2;
      ctx.font = labelFont(7); ctx.fillStyle = T.dim; ctx.textAlign = 'center';
      ctx.fillText(vEdges.length ? Number(vEdges[i] || 0).toFixed(1) : String(i), x, h - pad.b + 13);
    });

    attachInteraction(id);
  }

  function drawScatter(id, points, xKey, yKey, opts, isRerender) {
    points = Array.isArray(points) ? points.filter(function (p) {
      return p && isFinite(Number(p[xKey])) && isFinite(Number(p[yKey]));
    }) : [];
    if (!points.length) return;
    points = limitScatter(points, MAX_SCATTER_POINTS);
    opts = opts || {};
    if (!isRerender) _chartCache[id] = { type: 'scatter', points: points, xKey: xKey, yKey: yKey, opts: opts };

    var sorted = points.slice().sort(function (a, b) { return Number(a[xKey]) - Number(b[xKey]); });
    var vp = getVP(id);
    var len = sorted.length;
    var si = Math.floor(vp.s * len), ei = Math.ceil(vp.e * len);
    si = Math.max(0, si); ei = Math.min(len, ei);
    if (ei <= si) ei = si + 1;
    var vPoints = sorted.slice(si, ei);
    if (!vPoints.length) return;

    var g = getCanvas(id);
    if (!g) return;
    var ctx = g.ctx, w = g.w, h = g.h;
    var pad = { t: 20, r: 20, b: 36, l: 52 };
    var pw = w - pad.l - pad.r, ph = h - pad.t - pad.b;
    if (pw < 10 || ph < 10) return;

    var xmn = Infinity, xmx = -Infinity, ymn = Infinity, ymx = -Infinity;
    for (var pi = 0; pi < vPoints.length; pi++) {
      var p = vPoints[pi];
      var x = Number(p[xKey]), y = Number(p[yKey]);
      if (x < xmn) xmn = x; if (x > xmx) xmx = x;
      if (y < ymn) ymn = y; if (y > ymx) ymx = y;
    }
    if (xmn === xmx) { xmn -= 1; xmx += 1; }
    if (ymn === ymx) { ymn -= 1; ymx += 1; }
    var xr = xmx - xmn, yr = ymx - ymn;

    ctx.strokeStyle = T.border; ctx.lineWidth = 0.5;
    for (var i = 0; i <= 4; i++) {
      var yy = pad.t + ph * (i / 4), xx = pad.l + pw * (i / 4);
      ctx.beginPath(); ctx.moveTo(pad.l, yy); ctx.lineTo(w - pad.r, yy); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(xx, pad.t); ctx.lineTo(xx, h - pad.b); ctx.stroke();
    }

    ctx.font = labelFont(7); ctx.fillStyle = T.dim;
    ctx.textAlign = 'right';
    for (var yi = 0; yi <= 4; yi++) ctx.fillText((ymx - yr * (yi / 4)).toFixed(1), pad.l - 4, pad.t + ph * (yi / 4) + 3);
    ctx.textAlign = 'center';
    for (var xi = 0; xi <= 4; xi++) ctx.fillText((xmn + xr * (xi / 4)).toFixed(1), pad.l + pw * (xi / 4), h - pad.b + 13);

    vPoints.forEach(function (p) {
      var x = pad.l + ((Number(p[xKey]) - xmn) / xr) * pw;
      var y = pad.t + (1 - ((Number(p[yKey]) - ymn) / yr)) * ph;
      var col = opts.colorFn ? opts.colorFn(p) : (p.win ? T.bull : T.bear);
      ctx.beginPath(); ctx.arc(x, y, 2.5, 0, Math.PI * 2);
      ctx.fillStyle = col + 'aa'; ctx.fill();
      ctx.strokeStyle = col; ctx.lineWidth = 0.5; ctx.stroke();
    });

    attachInteraction(id);
  }

  function drawMcPaths(id, paths, isRerender) {
    paths = Array.isArray(paths) ? paths.filter(function (p) { return Array.isArray(p) && p.length; }) : [];
    if (!paths.length) return;
    paths = paths.slice(0, MAX_MC_PATHS_VISUAL).map(function (p) {
      return downsampleLineSeries(p.filter(function (v) { return v != null && isFinite(v); }), MAX_MC_PATH_POINTS);
    }).filter(function (p) { return p.length > 1; });
    if (!paths.length) return;
    if (!isRerender) _chartCache[id] = { type: 'mc', paths: paths };

    var g = getCanvas(id);
    if (!g) return;
    var ctx = g.ctx, w = g.w, h = g.h;
    var pad = { t: 20, r: 16, b: 32, l: 68 };
    var pw = w - pad.l - pad.r, ph = h - pad.t - pad.b;
    if (pw < 10 || ph < 10) return;

    var processedPaths = [];
    var mn = Infinity, mx = -Infinity;
    for (var pi = 0; pi < paths.length; pi++) {
      var sampled = sampleData(paths[pi]);
      var sliced = sliceByVP(sampled, id);
      if (!sliced.length) continue;
      processedPaths.push(sliced);
      for (var vi = 0; vi < sliced.length; vi++) {
        if (sliced[vi] < mn) mn = sliced[vi];
        if (sliced[vi] > mx) mx = sliced[vi];
      }
    }
    if (!processedPaths.length || mn === Infinity || mx === -Infinity) return;
    if (mn === mx) { mn -= 1; mx += 1; }
    var range = mx - mn;

    ctx.strokeStyle = T.border; ctx.lineWidth = 0.5;
    for (var i = 0; i <= 4; i++) {
      var gy = pad.t + ph * (i / 4);
      ctx.beginPath(); ctx.moveTo(pad.l, gy); ctx.lineTo(w - pad.r, gy); ctx.stroke();
      var gv = mx - range * (i / 4);
      ctx.font = labelFont(8); ctx.fillStyle = T.dim; ctx.textAlign = 'right';
      ctx.fillText(Math.abs(gv) >= 1000 ? (gv / 1000).toFixed(1) + 'k' : gv.toFixed(0), pad.l - 4, gy + 3);
    }

    processedPaths.forEach(function (path) {
      if (path.length < 2) return;
      var col = Number(path[path.length - 1]) >= Number(path[0]) ? T.bull : T.bear;
      ctx.beginPath();
      path.forEach(function (v, i) {
        var x = pad.l + (i / Math.max(1, path.length - 1)) * pw;
        var y = pad.t + ph * (1 - ((Number(v) - mn) / range));
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      });
      ctx.strokeStyle = col + '55'; ctx.lineWidth = 0.8; ctx.stroke();
    });

    attachInteraction(id);
  }

  // ════════════════════════════════════════════════════════════════════
  // KPI HELPERS (v4: currency-aware)
  // ════════════════════════════════════════════════════════════════════
  function kpiCard(label, value, colorClass, subtext, rawUsd) {
    colorClass = colorClass || ''; subtext = subtext || '';
    // If rawUsd is provided, tag the value element so CurrencyDisplay.refreshAll() works
    var dataAttr = '';
    if (rawUsd != null && isFinite(Number(rawUsd))) {
      dataAttr = ' data-raw-usd="' + Number(rawUsd) + '"';
    }
    return '<div class="bt-kpi"><div class="bt-kpi-label">' + label + '</div><div class="bt-kpi-value ' + colorClass + '"' + dataAttr + '>' + value + '</div>' +
      (subtext ? '<div class="bt-kpi-sub">' + subtext + '</div>' : '') + '</div>';
  }

  function colorClass(v) {
    if (v == null || !isFinite(Number(v)) || Number(v) === 0) return '';
    return Number(v) > 0 ? 'bull' : 'bear';
  }

  function fmt(v, prefix, dec, opts) {
    if (v == null || !isFinite(Number(v))) return '—';
    // If CurrencyDisplay is loaded and prefix is $ (dollar value), use it
    if (window.CurrencyDisplay && (prefix == null || prefix === '$')) {
      var fmtOpts = { decimals: dec != null ? dec : 2 };
      if (opts) { for (var k in opts) fmtOpts[k] = opts[k]; }
      return window.CurrencyDisplay.format(Number(v), fmtOpts);
    }
    prefix = prefix == null ? '$' : prefix;
    dec = dec != null ? dec : 2;
    return prefix + Number(v).toFixed(dec);
  }

  function fmtPct(v, dec) {
    dec = dec != null ? dec : 1;
    if (v == null || !isFinite(Number(v))) return '—';
    return Number(v).toFixed(dec) + '%';
  }

  function fmtRatio(v, dec) {
    dec = dec != null ? dec : 3;
    if (v == null || !isFinite(Number(v))) return '—';
    return Number(v).toFixed(dec);
  }

  function fillKpiGrid(id, cards) {
    var el = document.getElementById(id);
    if (el) el.innerHTML = (cards || []).join('');
  }

  function toNumber(v, fallback) {
    var n = Number(v);
    return isFinite(n) ? n : (fallback != null ? fallback : 0);
  }

  // ════════════════════════════════════════════════════════════════════
  // CHART-ONLY FALLBACK ANALYTICS (no KPI recomputation)
  // ════════════════════════════════════════════════════════════════════
  function sanitizeNumericArray(arr) {
    if (!Array.isArray(arr)) return [];
    return arr.map(function (v) { return Number(v); }).filter(function (v) { return isFinite(v); });
  }

  function safeDateFromTs(ts) {
    var n = Number(ts);
    if (!isFinite(n) || n <= 0) return null;
    if (n > 1e12) n = Math.floor(n / 1000);
    var d = new Date(n * 1000);
    return isNaN(d.getTime()) ? null : d;
  }

  function histogram(data, bins) {
    data = sanitizeNumericArray(data); bins = bins || 20;
    if (!data.length) return { edges: [], counts: [] };
    var mn = Math.min.apply(null, data), mx = Math.max.apply(null, data);
    if (mn === mx) return { edges: [mn, mx], counts: [data.length] };
    var w = (mx - mn) / bins;
    var counts = new Array(bins).fill(0);
    var edges = [];
    for (var i = 0; i <= bins; i++) edges.push(Number((mn + i * w).toFixed(4)));
    data.forEach(function (x) { var idx = Math.min(bins - 1, Math.max(0, Math.floor((x - mn) / w))); counts[idx]++; });
    return { edges: edges, counts: counts };
  }

  function computeRollingAnalytics(trades) {
    var W = 20;
    var wr = [], exp = [], pf = [], sh = [];
    for (var i = 0; i < trades.length; i++) {
      var s = Math.max(0, i - W + 1);
      var chunk = [];
      for (var j = s; j <= i; j++) chunk.push(toNumber(trades[j].net_pnl, 0));
      var wins = chunk.filter(function (x) { return x > 0; });
      var losses = chunk.filter(function (x) { return x <= 0; });
      var wrc = chunk.length ? (wins.length / chunk.length) * 100 : 0;
      var awc = wins.length ? wins.reduce(function (a, b) { return a + b; }, 0) / wins.length : 0;
      var alc = losses.length ? losses.reduce(function (a, b) { return a + b; }, 0) / losses.length : 0;
      wr.push(Math.round(wrc * 10) / 10);
      exp.push(Math.round((wrc / 100 * awc + (1 - wrc / 100) * alc) * 100) / 100);
      var gw = wins.reduce(function (a, b) { return a + b; }, 0);
      var gl = Math.abs(losses.reduce(function (a, b) { return a + b; }, 0));
      pf.push(gl > 0 ? Math.round(gw / gl * 1000) / 1000 : null);
      if (chunk.length > 1) {
        var mu = chunk.reduce(function (a, b) { return a + b; }, 0) / chunk.length;
        var va = chunk.reduce(function (a, b) { return a + Math.pow(b - mu, 2); }, 0) / (chunk.length - 1);
        var sd = Math.sqrt(va);
        sh.push(sd > 0 ? Math.round(mu / sd * Math.sqrt(252) * 1000) / 1000 : null);
      } else sh.push(null);
    }
    return { win_rate: wr, expectancy: exp, profit_factor: pf, sharpe: sh };
  }

  function computeTimeBreakdown(trades) {
    var byHour = {}, byDOW = {}, byMonth = {};
    var DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
    var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    trades.forEach(function (t) {
      var dt = safeDateFromTs(t.exit_time || t.entry_time);
      if (!dt) return;
      var h = String(dt.getHours()), dow = DOW[dt.getDay()], mon = MONTHS[dt.getMonth()];
      var pnl = toNumber(t.net_pnl, 0);
      if (!byHour[h]) byHour[h] = { net: 0, trades: 0 };
      if (!byDOW[dow]) byDOW[dow] = { net: 0, trades: 0 };
      if (!byMonth[mon]) byMonth[mon] = { net: 0, trades: 0 };
      byHour[h].net += pnl; byHour[h].trades++;
      byDOW[dow].net += pnl; byDOW[dow].trades++;
      byMonth[mon].net += pnl; byMonth[mon].trades++;
    });
    return { time_of_day: byHour, day_of_week: byDOW, by_month: byMonth };
  }

  function ensureAnalyticsShape(a) {
    a = a || {};
    if (!a.rolling) a.rolling = {};
    if (!a.r_histogram) a.r_histogram = { edges: [], counts: [] };
    if (!a.duration_histogram) a.duration_histogram = { edges: [], counts: [] };
    if (!a.mae_mfe) a.mae_mfe = [];
    if (!a.return_scatter) a.return_scatter = [];
    if (!a.time_of_day) a.time_of_day = {};
    if (!a.day_of_week) a.day_of_week = {};
    if (!a.by_month) a.by_month = {};
    if (!a.regime) a.regime = { volatility: {}, choppiness: {} };
    if (!a.monte_carlo) a.monte_carlo = { final_equity: {}, max_drawdown: {}, paths_sample: [], final_dist: { edges: [], counts: [] }, dd_dist: { edges: [], counts: [] }, prob_profitable: null, prob_dd_10: null, prob_dd_20: null, prob_dd_30: null };
    if (!a.commission) a.commission = { total: 0, per_trade: 0, net_without_comm: 0, net_with_comm: 0, pct_of_gross: 0 };
    if (!Array.isArray(a.rolling.win_rate)) a.rolling.win_rate = [];
    if (!Array.isArray(a.rolling.expectancy)) a.rolling.expectancy = [];
    if (!Array.isArray(a.rolling.profit_factor)) a.rolling.profit_factor = [];
    if (!Array.isArray(a.rolling.sharpe)) a.rolling.sharpe = [];
    return a;
  }

  function buildFallbackChartAnalytics(data) {
    var trades = Array.isArray(data.trades) ? data.trades : [];
    if (!trades.length) return ensureAnalyticsShape({});
    var rolling = computeRollingAnalytics(trades);
    var rSeries = trades.map(function (t) { return t.net_pnl_r != null && isFinite(Number(t.net_pnl_r)) ? Number(t.net_pnl_r) : toNumber(t.net_pnl, 0); });
    var durSeries = trades.map(function (t) { return toNumber(t.bars_held, 0); });
    var time = computeTimeBreakdown(trades);
    var maeMfe = trades.filter(function (t) { return t.mae_dollar != null && t.mfe_dollar != null; }).map(function (t) {
      return { mae: toNumber(t.mae_dollar, 0), mfe: toNumber(t.mfe_dollar, 0), win: toNumber(t.net_pnl, 0) > 0 };
    });
    var retScatter = trades.map(function (t, i) {
      return { x: i + 1, y: t.net_pnl_r != null && isFinite(Number(t.net_pnl_r)) ? Number(t.net_pnl_r) : toNumber(t.net_pnl, 0), win: toNumber(t.net_pnl, 0) > 0 };
    });
    var commTotal = 0, grossTotal = 0, netTotal = 0;
    trades.forEach(function (t) { commTotal += toNumber(t.commission, 0); grossTotal += toNumber(t.gross_pnl, 0); netTotal += toNumber(t.net_pnl, 0); });
    return ensureAnalyticsShape({
      rolling: rolling,
      r_histogram: histogram(rSeries, 20),
      duration_histogram: histogram(durSeries, 20),
      mae_mfe: maeMfe,
      return_scatter: retScatter,
      time_of_day: time.time_of_day,
      day_of_week: time.day_of_week,
      by_month: time.by_month,
      commission: { total: commTotal, per_trade: trades.length ? commTotal / trades.length : 0, net_without_comm: grossTotal, net_with_comm: netTotal, pct_of_gross: grossTotal !== 0 ? (commTotal / Math.abs(grossTotal)) * 100 : 0 }
    });
  }

  // ════════════════════════════════════════════════════════════════════
  // TRADE LOG (v4: currency-aware + trade cap handling)
  // ════════════════════════════════════════════════════════════════════
  function buildTradeLog(trades) {
    var el = document.getElementById('bt_tradeLog');
    if (!el) return;
    if (!trades || !trades.length) { el.innerHTML = '<div class="bt-trade-log-empty">No trades</div>'; return; }

    var CD = window.CurrencyDisplay;
    function fmtDollar(v) {
      if (v == null || !isFinite(Number(v))) return '—';
      if (CD) return CD.format(Number(v));
      return '$' + Number(v).toFixed(2);
    }
    function fmtDollarSigned(v) {
      if (v == null || !isFinite(Number(v))) return '—';
      if (CD) return CD.format(Number(v), { forceSign: true });
      return (Number(v) >= 0 ? '+' : '') + '$' + Number(v).toFixed(2);
    }
    function rawAttr(v) {
      if (v == null || !isFinite(Number(v))) return '';
      return ' data-raw-usd="' + Number(v) + '"';
    }

    var cols = ['#', 'POS', 'DIR', 'ENTRY', 'EXIT', 'REASON', 'SL LVL', 'TP LVL', 'R', 'SIZE', 'GROSS', 'COMM', 'NET', 'BARS'];
    var gridCols = '30px 44px 42px 66px 66px 84px 58px 58px 50px 52px 60px 48px 60px 36px';
    var header = '<div class="bt-trade-row bt-trade-header" style="grid-template-columns:' + gridCols + ';">' +
      cols.map(function (c) { return '<div class="bt-trade-cell">' + c + '</div>'; }).join('') + '</div>';

    // Only render last 500 trades in DOM for performance
    var visibleTrades = trades.length > 500 ? trades.slice(-500) : trades;
    var startIdx = trades.length - visibleTrades.length;

    var rows = visibleTrades.map(function (t, i) {
      var rVal = t.net_pnl_r != null && isFinite(Number(t.net_pnl_r)) ? Number(t.net_pnl_r) : null;
      var r = rVal != null ? (rVal >= 0 ? '+' : '') + rVal.toFixed(2) + 'R' : '—';
      var reas = String(t.exit_reason || '').toUpperCase();
      var slLvl = t.stop_loss != null && isFinite(Number(t.stop_loss)) ? Number(t.stop_loss).toFixed(2) : '—';
      var tpLvl = t.take_profit != null && isFinite(Number(t.take_profit)) ? Number(t.take_profit).toFixed(2) : '—';
      var grossCls = toNumber(t.gross_pnl, 0) >= 0 ? 'bull' : 'bear';
      var netCls = toNumber(t.net_pnl, 0) >= 0 ? 'bull' : 'bear';
      var dirCls = t.direction === 'long' ? 'bull' : 'bear';
      var posId = t.position_id != null ? t.position_id : '—';
      var size = t.size != null && isFinite(Number(t.size)) ? Number(t.size).toFixed(2) : '—';

      return '<div class="bt-trade-row" style="grid-template-columns:' + gridCols + ';">' +
        '<div class="bt-trade-cell">' + (startIdx + i + 1) + '</div>' +
        '<div class="bt-trade-cell">' + posId + '</div>' +
        '<div class="bt-trade-cell ' + dirCls + '">' + String(t.direction || '').toUpperCase() + '</div>' +
        '<div class="bt-trade-cell">' + (t.entry_price != null ? Number(t.entry_price).toFixed(2) : '—') + '</div>' +
        '<div class="bt-trade-cell">' + (t.exit_price != null ? Number(t.exit_price).toFixed(2) : '—') + '</div>' +
        '<div class="bt-trade-cell">' + reas + '</div>' +
        '<div class="bt-trade-cell">' + slLvl + '</div>' +
        '<div class="bt-trade-cell">' + tpLvl + '</div>' +
        '<div class="bt-trade-cell ' + netCls + '">' + r + '</div>' +
        '<div class="bt-trade-cell">' + size + '</div>' +
        '<div class="bt-trade-cell ' + grossCls + '"' + rawAttr(t.gross_pnl) + '>' + fmtDollarSigned(t.gross_pnl) + '</div>' +
        '<div class="bt-trade-cell"' + rawAttr(t.commission) + '>' + fmtDollar(t.commission) + '</div>' +
        '<div class="bt-trade-cell ' + netCls + '"' + rawAttr(t.net_pnl) + '>' + fmtDollarSigned(t.net_pnl) + '</div>' +
        '<div class="bt-trade-cell">' + (t.bars_held != null ? t.bars_held : '—') + '</div>' +
        '</div>';
    }).join('');

    // ── Truncation note ─────────────────────────────────────────
    var totalCount = 0;
    if (_lastRenderData && _lastRenderData.data && _lastRenderData.data.total_trade_count) {
      totalCount = _lastRenderData.data.total_trade_count;
    } else {
      totalCount = trades.length;
    }

    var truncNote = '';
    if (totalCount > trades.length) {
      // Backend truncated trades (100k+ scenario)
      truncNote = '<div style="text-align:center;padding:8px;color:var(--accent);font-size:0.6rem;border-top:1px solid var(--border);">'
        + 'Showing last ' + trades.length.toLocaleString() + ' of ' + totalCount.toLocaleString() + ' total trades'
        + ' (stats computed from ALL trades)</div>';
    } else if (trades.length > 500) {
      // DOM cap (display only last 500)
      truncNote = '<div style="text-align:center;padding:6px;color:var(--text-dim);font-size:0.6rem;">'
        + 'Showing last 500 of ' + trades.length.toLocaleString() + ' trades</div>';
    }
    el.innerHTML = header + rows + truncNote;
  }

  // ════════════════════════════════════════════════════════════════════
  // NORMALIZATION (v4: trade cap fields)
  // ════════════════════════════════════════════════════════════════════
  function normalizeResultPayload(raw, configOverride) {
    if (!raw) return null;

    var data = {
      trades: Array.isArray(raw.trades) ? raw.trades : [],
      total_trade_count: raw.total_trade_count || (Array.isArray(raw.trades) ? raw.trades.length : 0),
      trades_truncated: !!raw.trades_truncated,
      stats: raw.stats || raw.metrics || {},
      analytics: raw.analytics || null,
      equity_curve: raw.equity_curve || (raw.curves ? (raw.curves.equity_downsampled || raw.curves.equity_full || []) : []) || [],
      drawdown_curve: raw.drawdown_curve || (raw.curves ? (raw.curves.drawdown_downsampled || raw.curves.drawdown_full || []) : []) || []
    };

    var config = configOverride || raw.config || {};
    if (raw.metrics && !raw.stats) data.stats = raw.metrics;
    if (data.stats.starting_capital == null && config.starting_capital != null) data.stats.starting_capital = config.starting_capital;
    if (data.stats.lot_size == null && config.lot_size != null) data.stats.lot_size = config.lot_size;

    data.equity_curve = sanitizeNumericArray(data.equity_curve);
    data.drawdown_curve = sanitizeNumericArray(data.drawdown_curve);

    // ── Fill chart analytics from backend or fallback ─────────
    var analytics = ensureAnalyticsShape(data.analytics || {});
    var fallback = buildFallbackChartAnalytics(data);

    if (!analytics.rolling.win_rate.length) analytics.rolling = fallback.rolling;
    if (!analytics.r_histogram.counts.length) analytics.r_histogram = fallback.r_histogram;
    if (!analytics.duration_histogram.counts.length) analytics.duration_histogram = fallback.duration_histogram;
    if (!analytics.mae_mfe.length) analytics.mae_mfe = fallback.mae_mfe;
    if (!analytics.return_scatter.length) analytics.return_scatter = fallback.return_scatter;
    if (!Object.keys(analytics.time_of_day || {}).length) analytics.time_of_day = fallback.time_of_day;
    if (!Object.keys(analytics.day_of_week || {}).length) analytics.day_of_week = fallback.day_of_week;
    if (!Object.keys(analytics.by_month || {}).length) analytics.by_month = fallback.by_month;
    if (!analytics.commission || !analytics.commission.total) analytics.commission = fallback.commission;

    data.analytics = analytics;
    return { data: data, config: config };
  }

  // ════════════════════════════════════════════════════════════════════
  // POPULATE DASHBOARD (v4: currency-aware, rawUsd tagged)
  // ════════════════════════════════════════════════════════════════════
  function populateDashboard(data, config) {
    resetAllVPs();

    var s = data.stats;
    var a = data.analytics || {};
    _lastRenderData = { data: data, analytics: a };
    _lastConfig = config;

    var maStr = (config && Array.isArray(config.mas) && config.mas.length)
      ? config.mas.map(function (m) { return m.type + m.period; }).join('+')
      : ((config && config.strategy_id) ? String(config.strategy_id).replace(/_/g, ' ').toUpperCase() : 'PLUGIN STRATEGY');

    var entryLabel = (config && config.entry)
      ? String(config.entry).replace(/_/g, ' ').toUpperCase() : 'PLUGIN MODE';

    document.getElementById('bd_strategy').textContent =
      maStr + ' · ' + ((config && config.range != null) ? config.range : '—') + 'pt · ' + entryLabel;

    var extraMeta = '';
    if (config && config.engine && config.engine.position_sizing && config.engine.position_sizing.mode)
      extraMeta += ' · sizing ' + String(config.engine.position_sizing.mode).replace(/_/g, ' ');
    if (config && config.engine && config.engine.scaling_in && config.engine.scaling_in.enabled)
      extraMeta += ' · scaling-in enabled';

    var sizingLabel = '';
    if (config && config.sizing_mode === 'risk_pct') {
      sizingLabel = 'risk ' + (config.risk_pct || 1) + '%/trade';
    } else if (config && config.sizing_mode === 'risk_per_trade') {
      sizingLabel = config.scaling_enabled ? 'scaling risk' : ('$' + (config.fixed_risk || 10) + ' risk/trade');
    } else {
      sizingLabel = 'lot ' + (s.lot_size != null ? s.lot_size : 1);
    }

    document.getElementById('bd_meta').textContent =
      (s.total_bars_used || 0) + ' bars · ' + sizingLabel +
      ' · cap ' + fmt(s.starting_capital, '$', 0) +
      ' · ' + (s.date_range || '') + extraMeta;

    // ── CORE STATISTICS ────────────────────────────────────────
    fillKpiGrid('bd_coreKpis', [
      kpiCard('STARTING CAPITAL', fmt(s.starting_capital, '$', 0), '', '', s.starting_capital),
      kpiCard('FINAL BALANCE', fmt(s.final_balance), colorClass(toNumber(s.final_balance, 0) - toNumber(s.starting_capital, 0)), '', s.final_balance),
      kpiCard('LOT SIZE', (s.lot_size != null ? s.lot_size : 1) + ' (1pt=' + fmt(s.lot_size != null ? s.lot_size : 1, '$', 2) + ')'),
      kpiCard('NET P&L', fmt(s.net_pnl), colorClass(s.net_pnl), '', s.net_pnl),
      kpiCard('NET PROFIT %', fmtPct(s.net_profit_pct), colorClass(s.net_profit_pct)),
      kpiCard('PROFIT FACTOR', fmtRatio(s.profit_factor), s.profit_factor > 1.5 ? 'bull' : s.profit_factor < 1 ? 'bear' : ''),
      kpiCard('WIN RATE', fmtPct(toNumber(s.win_rate, 0) * 100), toNumber(s.win_rate, 0) > 0.5 ? 'bull' : 'bear'),
      kpiCard('TOTAL TRADES', s.total_trades || 0),
      kpiCard('WINS / LOSSES', (s.winning_trades || 0) + ' / ' + (s.losing_trades || 0)),
      kpiCard('AVG WIN', fmt(s.avg_win), 'bull', '', s.avg_win),
      kpiCard('AVG LOSS', fmt(s.avg_loss), 'bear', '', s.avg_loss),
      kpiCard('LARGEST WIN', fmt(s.largest_win), 'bull', '', s.largest_win),
      kpiCard('LARGEST LOSS', fmt(s.largest_loss), 'bear', '', s.largest_loss),
      kpiCard('EXPECTANCY', fmt(s.expectancy), colorClass(s.expectancy), '', s.expectancy),
      kpiCard('MAX CONSEC WINS', s.max_consec_wins || 0),
      kpiCard('MAX CONSEC LOSS', s.max_consec_losses || 0),
    ]);

    // ── RISK METRICS ───────────────────────────────────────────
    fillKpiGrid('bd_riskKpis', [
      kpiCard('MAX DRAWDOWN', fmt(s.max_drawdown), 'bear', '', s.max_drawdown),
      kpiCard('MAX DRAWDOWN %', fmtPct(s.max_drawdown_pct), 'bear'),
      kpiCard('FINAL EQUITY', fmt(s.final_equity), colorClass(toNumber(s.final_equity, 0) - toNumber(s.starting_capital, 0)), '', s.final_equity),
      kpiCard('TOTAL BARS', s.total_bars_used || 0),
      kpiCard('AVG DRAWDOWN', fmt(s.avg_drawdown), 'bear', '', s.avg_drawdown),
      kpiCard('DD DURATION', (s.drawdown_duration_bars || 0) + ' bars'),
      kpiCard('RECOVERY FACTOR', fmtRatio(s.recovery_factor), colorClass(s.recovery_factor)),
      kpiCard('CALMAR', fmtRatio(s.calmar_ratio), colorClass(s.calmar_ratio)),
      kpiCard('ULCER INDEX', fmtRatio(s.ulcer_index), 'bear'),
    ]);

    // ── PERFORMANCE RATIOS ─────────────────────────────────────
    fillKpiGrid('bd_ratioKpis', [
      kpiCard('SHARPE', fmtRatio(s.sharpe), colorClass(s.sharpe)),
      kpiCard('SORTINO', fmtRatio(s.sortino), colorClass(s.sortino)),
      kpiCard('SQN', fmtRatio(s.sqn), colorClass(s.sqn)),
      kpiCard('OMEGA', fmtRatio(s.omega), s.omega > 1 ? 'bull' : s.omega < 1 ? 'bear' : ''),
      kpiCard('PROFIT FACTOR', fmtRatio(s.profit_factor), colorClass(s.profit_factor)),
    ]);

    // ── TRADE ANALYSIS ─────────────────────────────────────────
    fillKpiGrid('bd_tradeKpis', [
      kpiCard('PROFIT/TRADE', fmt(s.profit_per_trade), colorClass(s.profit_per_trade), '', s.profit_per_trade),
      kpiCard('PAYOFF RATIO', fmtRatio(s.payoff_ratio), s.payoff_ratio > 1 ? 'bull' : 'bear'),
      kpiCard('AVG WIN R', s.avg_win_r != null ? fmtRatio(s.avg_win_r) + 'R' : '—', 'bull'),
      kpiCard('AVG LOSS R', s.avg_loss_r != null ? fmtRatio(s.avg_loss_r) + 'R' : '—', 'bear'),
      kpiCard('MEDIAN WIN', fmt(s.median_win), 'bull', '', s.median_win),
      kpiCard('MEDIAN LOSS', fmt(s.median_loss), 'bear', '', s.median_loss),
    ]);

    // ── TIME & EXPOSURE ────────────────────────────────────────
    fillKpiGrid('bd_timeExpKpis', [
      kpiCard('AVG HOLD TIME', s.avg_hold_time_str || '—'),
      kpiCard('AVG HOLD BARS', s.avg_holding_bars != null ? Number(s.avg_holding_bars).toFixed(1) : '—'),
      kpiCard('MEDIAN HOLD', s.median_hold_bars != null ? Number(s.median_hold_bars).toFixed(1) + ' bars' : '—'),
      kpiCard('AVG WIN HOLD', s.avg_win_hold_bars != null ? Number(s.avg_win_hold_bars).toFixed(1) + ' bars' : '—'),
      kpiCard('AVG LOSS HOLD', s.avg_loss_hold_bars != null ? Number(s.avg_loss_hold_bars).toFixed(1) + ' bars' : '—'),
      kpiCard('EXPOSURE %', fmtPct(s.exposure_pct)),
      kpiCard('TRADES/DAY', s.avg_trades_per_day != null ? Number(s.avg_trades_per_day).toFixed(2) : '—'),
      kpiCard('TRADES/WEEK', s.avg_trades_per_week != null ? Number(s.avg_trades_per_week).toFixed(2) : '—'),
    ]);

    // ── PERIOD PERFORMANCE ─────────────────────────────────────
    fillKpiGrid('bd_periodKpis', [
      kpiCard('AVG DAILY P&L', fmt(s.avg_daily_pnl), colorClass(s.avg_daily_pnl), '', s.avg_daily_pnl),
      kpiCard('AVG WEEKLY P&L', fmt(s.avg_weekly_pnl), colorClass(s.avg_weekly_pnl), '', s.avg_weekly_pnl),
      kpiCard('AVG MONTHLY P&L', fmt(s.avg_monthly_pnl), colorClass(s.avg_monthly_pnl), '', s.avg_monthly_pnl),
      kpiCard('BEST DAY', fmt(s.best_day), 'bull', '', s.best_day),
      kpiCard('WORST DAY', fmt(s.worst_day), 'bear', '', s.worst_day),
      kpiCard('BEST WEEK', fmt(s.best_week), 'bull', '', s.best_week),
      kpiCard('WORST WEEK', fmt(s.worst_week), 'bear', '', s.worst_week),
      kpiCard('BEST MONTH', fmt(s.best_month), 'bull', '', s.best_month),
      kpiCard('WORST MONTH', fmt(s.worst_month), 'bear', '', s.worst_month),
    ]);

    // ── MAE / MFE ──────────────────────────────────────────────
    fillKpiGrid('bd_maeMfeKpis', [
      kpiCard('AVG MAE', fmt(s.avg_mae_dollar), 'bear', '', s.avg_mae_dollar),
      kpiCard('AVG MFE', fmt(s.avg_mfe_dollar), 'bull', '', s.avg_mfe_dollar),
      kpiCard('EDGE RATIO', fmtRatio(s.edge_ratio), s.edge_ratio > 1 ? 'bull' : 'bear'),
    ]);

    // ── COMMISSION ─────────────────────────────────────────────
    var comm = a.commission || {};
    fillKpiGrid('bd_commKpis', [
      kpiCard('TOTAL COMMISSION', fmt(comm.total), 'bear', '', comm.total),
      kpiCard('COMMISSION/TRADE', fmt(comm.per_trade), '', '', comm.per_trade),
      kpiCard('GROSS P&L', fmt(comm.net_without_comm), '', '', comm.net_without_comm),
      kpiCard('NET P&L (after comm)', fmt(comm.net_with_comm), colorClass(comm.net_with_comm), '', comm.net_with_comm),
      kpiCard('COMM % OF GROSS', fmtPct(comm.pct_of_gross), 'bear'),
    ]);

    // ── REGIME ─────────────────────────────────────────────────
    var reg = a.regime || {};
    var regCards = [];
    Object.entries(reg.volatility || {}).forEach(function (entry) {
      var k = entry[0], v = entry[1];
      regCards.push(kpiCard('VOL: ' + String(k).toUpperCase().replace('_', ' '), fmt(v.net) + ' (' + v.trades + 't)', colorClass(v.net), 'WR ' + v.wr + '%', v.net));
    });
    Object.entries(reg.choppiness || {}).forEach(function (entry) {
      var k = entry[0], v = entry[1];
      regCards.push(kpiCard('REGIME: ' + String(k).toUpperCase(), fmt(v.net) + ' (' + v.trades + 't)', colorClass(v.net), 'WR ' + v.wr + '%', v.net));
    });
    if (!regCards.length) regCards.push(kpiCard('REGIME', 'No regime breakdown'));
    fillKpiGrid('bd_regimeKpis', regCards);

    // ── MONTE CARLO ────────────────────────────────────────────
    var mc = a.monte_carlo || {};
    var fe = mc.final_equity || {}, dd = mc.max_drawdown || {};
    fillKpiGrid('bd_mcKpis', [
      kpiCard('FINAL EQ P5', fmt(fe.p5), colorClass(fe.p5), '', fe.p5),
      kpiCard('FINAL EQ P25', fmt(fe.p25), colorClass(fe.p25), '', fe.p25),
      kpiCard('FINAL EQ P50', fmt(fe.p50), colorClass(fe.p50), '', fe.p50),
      kpiCard('FINAL EQ P95', fmt(fe.p95), colorClass(fe.p95), '', fe.p95),
      kpiCard('DD P50', fmt(dd.p50), 'bear', '', dd.p50),
      kpiCard('DD P95 (WORST)', fmt(dd.p95), 'bear', '', dd.p95),
    ]);

    var probEl = document.getElementById('bd_mcProb');
    if (probEl) {
      probEl.innerHTML = [
        ['Prob profitable', fmtPct(mc.prob_profitable)],
        ['Prob DD > 10% of capital', fmtPct(mc.prob_dd_10)],
        ['Prob DD > 20% of capital', fmtPct(mc.prob_dd_20)],
        ['Prob DD > 30% of capital', fmtPct(mc.prob_dd_30)],
      ].map(function (row) { return '<div class="bt-prob-row"><span>' + row[0] + '</span><span>' + row[1] + '</span></div>'; }).join('');
    }

    // ── Defer chart rendering ──────────────────────────────────
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { renderAllChartsWithRetry(data, a, 0); });
    });
  }

  function renderAllChartsWithRetry(data, a, attempt) {
    var testCv = document.getElementById('cv_equity');
    if (testCv) {
      var pw = testCv.parentElement ? testCv.parentElement.clientWidth : 0;
      if (pw < 50 && attempt < 10) {
        requestAnimationFrame(function () { renderAllChartsWithRetry(data, a, attempt + 1); });
        return;
      }
    }
    renderAllCharts(data, a);
  }

  function renderAllCharts(data, a) {
    a = ensureAnalyticsShape(a || {});
    var eq = sanitizeNumericArray(data.equity_curve || []);
    var dd = sanitizeNumericArray(data.drawdown_curve || []);
    var roll = a.rolling || {};

    if (eq.length) drawLineChart('cv_equity', eq, { color: T.bull, zeroLine: false, refVal: (data.stats || {}).starting_capital, refLabel: 'Capital' });
    if (dd.length) drawLineChart('cv_drawdown', dd, { color: T.bear, zeroLine: true });
    if ((roll.win_rate || []).length) drawLineChart('cv_rollWr', roll.win_rate, { color: T.accent });
    if ((roll.expectancy || []).length) drawLineChart('cv_rollExp', roll.expectancy, { color: T.accent2, zeroLine: true });
    if ((roll.profit_factor || []).length) drawLineChart('cv_rollPf', roll.profit_factor, { color: '#ff9800' });
    if ((roll.sharpe || []).length) drawLineChart('cv_rollSharpe', roll.sharpe, { color: '#e040fb', zeroLine: true });

    if (a.r_histogram && (a.r_histogram.counts || []).length)
      drawHistogram('cv_rHist', a.r_histogram, { colorFn: function (v) { return v >= 0 ? T.bull + '99' : T.bear + '99'; } });
    if (a.duration_histogram && (a.duration_histogram.counts || []).length)
      drawHistogram('cv_durHist', a.duration_histogram, { colorFn: function () { return T.accent + '88'; } });
    if ((a.mae_mfe || []).length)
      drawScatter('cv_maemfe', a.mae_mfe, 'mae', 'mfe', { colorFn: function (p) { return p.win ? T.bull : T.bear; } });
    if ((a.return_scatter || []).length)
      drawScatter('cv_retScatter', a.return_scatter, 'x', 'y', { colorFn: function (p) { return p.win ? T.bull : T.bear; } });

    var hourKeys = [];
    for (var hi = 0; hi < 24; hi++) hourKeys.push(hi);
    var hourLabels = hourKeys.map(function (h) { return h + 'h'; });
    var hourVals = hourKeys.map(function (h) { var row = (a.time_of_day || {})[String(h)]; return row ? toNumber(row.net, 0) : 0; });
    if (hourVals.some(function (v) { return v !== 0; }))
      drawBarChart('cv_hour', hourLabels, hourVals, { colorFn: function (v) { return v >= 0 ? T.bull : T.bear; } });

    var DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    var dowVals = DOW.map(function (d) { var row = (a.day_of_week || {})[d]; return row ? toNumber(row.net, 0) : 0; });
    if (dowVals.some(function (v) { return v !== 0; }))
      drawBarChart('cv_dow', DOW, dowVals, { colorFn: function (v) { return v >= 0 ? T.bull : T.bear; } });

    var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    var monthVals = MONTHS.map(function (m) { var row = (a.by_month || {})[m]; return row ? toNumber(row.net, 0) : 0; });
    if (monthVals.some(function (v) { return v !== 0; }))
      drawBarChart('cv_month', MONTHS, monthVals, { colorFn: function (v) { return v >= 0 ? T.bull : T.bear; } });

    if (a.monte_carlo && (a.monte_carlo.paths_sample || []).length) drawMcPaths('cv_mcPaths', a.monte_carlo.paths_sample);
    if (a.monte_carlo && a.monte_carlo.final_dist && (a.monte_carlo.final_dist.counts || []).length)
      drawHistogram('cv_mcFinal', a.monte_carlo.final_dist, { colorFn: function (v) { return v >= 0 ? T.bull + '88' : T.bear + '88'; } });
    if (a.monte_carlo && a.monte_carlo.dd_dist && (a.monte_carlo.dd_dist.counts || []).length)
      drawHistogram('cv_mcDd', a.monte_carlo.dd_dist, { colorFn: function () { return T.bear + '88'; } });
  }

  // ── Debounced resize ──────────────────────────────────────────────
  (function () {
    var rt = null;
    var p = document.getElementById('bt_resultsPanel');
    if (p) {
      new ResizeObserver(function () {
        if (rt) clearTimeout(rt);
        rt = setTimeout(function () {
          if (_lastRenderData) renderAllCharts(_lastRenderData.data, _lastRenderData.analytics);
        }, 300);
      }).observe(p);
    }
  })();

  // ════════════════════════════════════════════════════════════════════
  // RUN BUTTON (legacy/manual mode)
  // ════════════════════════════════════════════════════════════════════
  var runBtn = document.getElementById('bt_runBtn');
  if (runBtn) {
    runBtn.addEventListener('click', async function () {
      var config = collectConfig();
      if (!config.mas.length) { alert('Add at least one Moving Average.'); return; }

      var btn = document.getElementById('bt_runBtn');
      btn.classList.add('running');
      btn.innerHTML = '<span class="bt-run-icon">⟳</span> RUNNING…';

      // ✅ reset result lock when starting a new run
      __btHasRenderedResult = false;

      showProgress();
      setStep('step_load');
      if (typeof window.clearBacktestMarkers === 'function') window.clearBacktestMarkers();

      var streamed = false;

      try {
        setStep('step_run');

        var resp = await fetch(API_STREAM, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(config)
        });

        if (!resp.ok) throw new Error('Stream ' + resp.status);
        var ct = (resp.headers.get('content-type') || '').toLowerCase();
        if (!ct.includes('ndjson') && !ct.includes('stream')) throw new Error('Not a stream response');

        streamed = true;

        var reader = resp.body.getReader();
        var decoder = new TextDecoder();
        var buffer = '';
        var finalData = null;

        while (true) {
          var chunk = await reader.read();
          if (chunk.done) break;

          buffer += decoder.decode(chunk.value, { stream: true });
          var lines = buffer.split('\n');
          buffer = lines.pop();

          for (var li = 0; li < lines.length; li++) {
            var trimmed = lines[li].trim();
            if (!trimmed) continue;
            var msg = null;
            try { msg = JSON.parse(trimmed); } catch (e) { msg = null; }
            if (!msg) continue;
            if (msg.type === 'progress') updateLiveProgress(msg);
            else if (msg.type === 'result') { finalData = msg.data; setStep('step_stats'); }
          }
        }

        if (buffer.trim()) {
          try { var msg2 = JSON.parse(buffer.trim()); if (msg2.type === 'result') finalData = msg2.data; } catch (e) {}
        }

        if (!finalData) throw new Error('No result from stream');

        setStep('step_charts');
        await delay(80);
        setStep('step_done');
        await delay(80);

        window.btRenderAnyResult(finalData, config);

      } catch (streamErr) {
        if (!streamed || streamErr.message === 'No result from stream') {
          try {
            setStep('step_run');
            var resp2 = await fetch(API, {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(config)
            });
            setStep('step_stats');
            await delay(120);
            if (!resp2.ok) { var eText = await resp2.text(); throw new Error('Server ' + resp2.status + ': ' + eText); }
            var data = await resp2.json();
            setStep('step_charts');
            await delay(80);
            setStep('step_done');
            await delay(80);
            window.btRenderAnyResult(data, config);
          } catch (fallbackErr) {
            console.error(fallbackErr);
            window.btShowRunnerError(fallbackErr.message);
          }
        } else {
          console.error(streamErr);
          window.btShowRunnerError(streamErr.message);
        }
      } finally {
        btn.classList.remove('running');
        btn.innerHTML = '<span class="bt-run-icon">▶</span> START BACKTEST';
      }
    });
  }

  // ════════════════════════════════════════════════════════════════════
  // EXPOSED RENDER / PROGRESS API FOR STRATEGY MODE
  // ════════════════════════════════════════════════════════════════════

  window.btShowRunnerState = function (state) {
    try {
      // ✅ FIX: Reset render lock ONLY when a genuinely new run starts
      if (state && state.stepId === 'step_load') {
        __btHasRenderedResult = false;
      }

      // ✅ FIX: once results are rendered, IGNORE late progress updates
      // that would re-show progressWrap and hide dashboard.
      if (__btHasRenderedResult) {
        var pw = document.getElementById('bt_progressWrap');
        if (pw) pw.style.display = 'none';
        var ls = document.getElementById('bt_liveStats');
        if (ls) ls.style.display = 'none';
        return;
      }

      showProgress();

      if (state && state.stepId) setStep(state.stepId);
      var pct = (state && state.pct != null) ? state.pct : 0;
      var bar = document.getElementById('bt_progressBar');
      var pctEl = document.getElementById('bt_progressPct');
      var label = document.getElementById('bt_progressLabel');
      if (bar) bar.style.width = pct + '%';
      if (pctEl) pctEl.textContent = Math.round(pct) + '%';
      if (label && state && state.label) label.textContent = state.label;
      if (state && state.live) updateLiveProgress({
        pct: pct,
        bar: state.live.bar || 0,
        total: state.live.total || 0,
        equity: state.live.equity,
        drawdown: state.live.drawdown,
        open_trade: state.live.open_trade,
        last_closed_pnl: state.live.last_closed_pnl
      });
    } catch (err) { console.error(err); }
  };

  window.btShowRunnerError = function (message) {
    // ✅ reset render lock on error
    __btHasRenderedResult = false;

    hideProgress();
    var dash = document.getElementById('bt_dashboard');
    var empty = document.getElementById('bt_empty');
    if (dash) dash.style.display = 'none';
    if (empty) {
      empty.style.display = '';
      empty.innerHTML =
        '<div class="bt-empty-icon" style="color:var(--bear)">✕</div>' +
        '<div class="bt-empty-title" style="color:var(--bear)">Error</div>' +
        '<div class="bt-empty-sub">' + String(message || 'Unknown error') + '</div>';
    }
  };

  window.btRenderAnyResult = function (raw, configOverride) {
    try {
      var normalized = normalizeResultPayload(raw, configOverride);
      if (!normalized) throw new Error('No result payload');

      // ✅ once we are rendering results, lock state so progress updates can't override UI
      __btHasRenderedResult = true;

      hideProgress();
      var empty = document.getElementById('bt_empty');
      var dash = document.getElementById('bt_dashboard');
      if (empty) empty.style.display = 'none';
      if (dash) dash.style.display = '';
      buildTradeLog(normalized.data.trades || []);
      populateDashboard(normalized.data, normalized.config || {});
      if (typeof window.plotBacktestMarkers === 'function') window.plotBacktestMarkers(normalized.data.trades || []);
    } catch (err) { window.btShowRunnerError(err.message || String(err)); }
  };
})();
```
