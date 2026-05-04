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