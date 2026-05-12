"""
JINNI ZERO — Strategy Base Class (Bar-Close Execution Model)
=============================================================
Strategies are SIGNAL PROVIDERS ONLY.

Execution model:
  on_bar(ctx) is called ONCE per CLOSED bar.
  Entry executes immediately at bar close price.
  TP/SL checks happen on FUTURE bars only (no same-bar close).

They output:  BUY / SELL / HOLD / CLOSE + optional SL/TP
They do NOT:  sizing, PnL, equity, commission, spread, stats
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


SIGNAL_BUY   = "BUY"
SIGNAL_SELL  = "SELL"
SIGNAL_HOLD  = "HOLD"
SIGNAL_CLOSE = "CLOSE"

VALID_SIGNALS = {SIGNAL_BUY, SIGNAL_SELL, SIGNAL_HOLD, SIGNAL_CLOSE, None}


class BaseStrategy(ABC):
    """
    All strategies MUST subclass this.

    Lifecycle (bar-close model):
        1. Engine precomputes indicators via build_indicators(params)
        2. Engine calls on_init(ctx)
        3. For each CLOSED bar: engine calls on_bar(ctx)
           → strategy returns signal
           → engine executes entry at bar close if BUY/SELL
           → TP/SL only evaluated on future bars
        4. Engine calls on_end(ctx)
    """

    strategy_id:   str = ""
    name:          str = ""
    description:   str = ""
    version:       str = "1.0"
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
    # INDICATORS
    # ==========================================================
    def build_indicators(self, params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Return indicator specs for engine precomputation.
        Each: {"key": "hma_200", "kind": "HMA", "period": 200, "source": "close"}
        """
        return []

    # ==========================================================
    # LIFECYCLE
    # ==========================================================
    def on_init(self, ctx: Any) -> None:
        pass

    def on_end(self, ctx: Any) -> None:
        pass

    # ==========================================================
    # SIGNAL GENERATION — called once per closed bar
    # ==========================================================
    @abstractmethod
    def on_bar(self, ctx: Any) -> Optional[Dict[str, Any]]:
        """
        Called once when a bar closes.

        ctx provides:
            ctx.index, ctx.bar, ctx.bars, ctx.indicators, ctx.ind_series,
            ctx.position, ctx.params, ctx.state, ctx.trades,
            ctx.equity, ctx.balance

        Return one of:

        ENTRY:
            {"signal": "BUY",  "sl": float, "tp": float}
            {"signal": "SELL", "sl": float, "tp": float}
            {"signal": "BUY",  "sl_mode": "fixed", "sl_pts": 8, "tp_mode": "r_multiple", "tp_r": 2}
            {"signal": "BUY",  "sl_mode": "ma_snapshot", "sl_ma_val": float, "engine_tp_ma_key": "hma_21"}

        HOLD:
            {"signal": "HOLD"} or None

        CLOSE:
            {"signal": "CLOSE", "close_reason": "my_reason"}

        FLIP:
            {"signal": "SELL", "close": True, "close_reason": "flip_short"}

        UPDATE SL/TP:
            {"signal": "HOLD", "update_sl": float, "update_tp": float}

        ❌ NEVER return: size, entry_price, PnL, balance, equity, commission.
        """
        raise NotImplementedError